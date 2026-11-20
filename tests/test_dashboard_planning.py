import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Barrier

from dashboard.app import create_app
from dashboard.planning import PlanningError, PlanningProfile
from investment.core import iso
from investment.service import demo_events, demo_profile
from investment.store import import_events, initialize, read
from fastapi.testclient import TestClient
from test_dashboard_api import MemoryEventLoop


NOW = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)


class PlanningProfileTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.path = root / "planning" / "goals.json"
        self.journal = root / "investment.sqlite3"

    def client(self):
        return TestClient(create_app(
            serve_frontend=False, investment_database=self.journal,
            planning_profile=self.path),
            base_url="http://127.0.0.1",
            backend_options={"loop_factory": MemoryEventLoop})

    def get_session(self, client):
        response = client.get("/api/investment/v1/planning/session")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def preview(self, client, session, goals, request_id="planning-request",
                portfolio_id=None, journal_revision=None):
        return client.post(
            "/api/investment/v1/planning/preview",
            headers={"Origin": "http://127.0.0.1", "X-CSRF-Token": session["csrf"]},
            json={
                "request_id": request_id,
                "expected_revision": session["revision"],
                "expected_hash": session["profile_hash"],
                "goals": goals,
                "expected_portfolio_id": portfolio_id,
                "expected_journal_revision": journal_revision,
            })

    def confirm(self, client, session, preview_id):
        return client.post(
            "/api/investment/v1/planning/confirm",
            headers={"Origin": "http://127.0.0.1", "X-CSRF-Token": session["csrf"]},
            json={"preview_id": preview_id})

    def initialize_journal(self):
        initialize(self.journal, demo_profile(NOW), NOW)
        import_events(self.journal, demo_events(NOW), NOW)
        return read(self.journal, NOW)

    def linked_goal(self, portfolio_id="synthetic-demo", account_id="savings"):
        return {
            "name": "Emergency reserve", "target_amount_gbp": None,
            "target_date": None, "notes": "", "purpose": "financial_resilience",
            "access_date": None, "liquidity": "immediate", "loss_tolerance": "avoid_loss",
            "current_amount_gbp": None, "monthly_contribution_gbp": None,
            "linked_portfolio_id": portfolio_id, "linked_account_ids": [account_id],
        }

    def basic_goal(self, name):
        return {
            "name": name, "target_amount_gbp": None, "target_date": None, "notes": "",
            "purpose": "not_set", "access_date": None, "liquidity": "unknown",
            "loss_tolerance": "not_set", "current_amount_gbp": None,
            "monthly_contribution_gbp": None,
            "linked_portfolio_id": None, "linked_account_ids": [],
        }

    def test_reads_do_not_create_profile_or_touch_journal_and_save_requires_confirmation(self):
        with self.client() as client:
            session = self.get_session(client)
            self.assertTrue(session["enabled"])
            self.assertEqual((session["revision"], session["goals"]), (0, []))
            self.assertFalse(self.path.exists())
            self.assertFalse(self.journal.exists())

            goals = [self.basic_goal("First home") | {
                "target_amount_gbp": "25000.00",
                "target_date": "2032-05-01",
                "notes": "Synthetic local-only note",
            }]
            preview = self.preview(client, session, goals)
            self.assertEqual(preview.status_code, 200, preview.text)
            self.assertFalse(self.path.exists())
            self.assertFalse(self.journal.exists())
            repeated = self.preview(client, session, goals)
            self.assertEqual(repeated.status_code, 200, repeated.text)
            self.assertEqual(repeated.json(), preview.json())

            unauthorized = client.post(
                "/api/investment/v1/planning/confirm",
                headers={"Origin": "http://127.0.0.1"},
                json={"preview_id": preview.json()["preview_id"]})
            self.assertEqual(unauthorized.status_code, 403)
            self.assertFalse(self.path.exists())

            saved = self.confirm(client, session, preview.json()["preview_id"])
            self.assertEqual(saved.status_code, 200, saved.text)
            self.assertEqual(saved.json(), {"status": "saved", "revision": 1})
            self.assertTrue(self.path.is_file())
            self.assertFalse(self.journal.exists())

            profile = json.loads(self.path.read_text(encoding="utf-8"))
            self.assertEqual(profile["revision"], 1)
            self.assertEqual(profile["goals"][0]["name"], "First home")
            self.assertEqual(profile["goals"][0]["target_amount_gbp"], "25000.00")
            self.assertNotIn("account_id", profile)
            self.assertEqual(client.get("/api/investment/v1/planning/session").json()["goals"],
                             profile["goals"])
            self.assertEqual(self.confirm(client, session, preview.json()["preview_id"]).json(),
                             {"status": "saved", "revision": 1})

    def test_invalid_goals_and_stale_previews_fail_closed(self):
        with self.client() as client:
            session = self.get_session(client)
            invalid = [self.basic_goal("Too much precision") | {
                "target_amount_gbp": "1.001", "target_date": "2030-02-29",
            }]
            response = self.preview(client, session, invalid)
            self.assertEqual(response.status_code, 400)
            self.assertFalse(self.path.exists())

            goals = [self.basic_goal("Emergency reserve")]
            first = self.preview(client, session, goals, "first")
            self.assertEqual(first.status_code, 200, first.text)
            changed = [{
                **goals[0], "name": "Long-term goal",
            }]
            second = self.preview(client, session, changed, "second")
            self.assertEqual(second.status_code, 200, second.text)
            self.assertEqual(self.confirm(client, session, second.json()["preview_id"]).status_code, 200)
            stale = self.confirm(client, session, first.json()["preview_id"])
            self.assertEqual(stale.status_code, 409)
            self.assertEqual(stale.json()["error"], "planning_profile_changed")
            self.assertEqual(
                client.get("/api/investment/v1/planning/session").json()["goals"][0]["name"],
                "Long-term goal")

    def test_legacy_goal_profile_is_read_without_rewrite_and_upgraded_only_after_confirmation(self):
        self.path.parent.mkdir(parents=True)
        legacy = {
            "schema_version": 1, "revision": 1,
            "saved_at": "2026-01-01T12:00:00Z",
            "goals": [{
                "id": "a" * 32, "name": "First home", "target_amount_gbp": "25000",
                "target_date": "2035-01-01", "notes": "",
            }],
        }
        self.path.write_text(json.dumps(legacy), encoding="utf-8")
        original = self.path.read_bytes()
        with self.client() as client:
            session = self.get_session(client)
            self.assertEqual(session["goals"][0]["purpose"], "not_set")
            self.assertEqual(self.path.read_bytes(), original)
            goal = {
                "name": "First home", "target_amount_gbp": "25000",
                "target_date": "2035-01-01", "notes": "",
                "purpose": "first_home", "access_date": "2030-01-01",
                "liquidity": "within_5_years", "loss_tolerance": "small_fluctuations",
                "current_amount_gbp": "5000", "monthly_contribution_gbp": "200",
                "linked_portfolio_id": None, "linked_account_ids": [],
            }
            preview = self.preview(client, session, [goal], "planning-upgrade")
            self.assertEqual(preview.status_code, 200, preview.text)
            saved = self.confirm(client, session, preview.json()["preview_id"])
            self.assertEqual(saved.status_code, 200, saved.text)
        upgraded = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(upgraded["schema_version"], 3)
        self.assertEqual(upgraded["goals"][0]["liquidity"], "within_5_years")
        self.assertEqual(upgraded["goals"][0]["current_amount_gbp"], "5000")

    def test_goal_constraints_and_zero_contribution_are_validated_explicitly(self):
        with self.client() as client:
            session = self.get_session(client)
            goal = {
                "name": "Reserve", "target_amount_gbp": "1000",
                "target_date": "2032-01-01", "notes": "",
                "purpose": "financial_resilience", "access_date": None,
                "liquidity": "immediate", "loss_tolerance": "avoid_loss",
                "current_amount_gbp": "0", "monthly_contribution_gbp": "0.00",
                "linked_portfolio_id": None, "linked_account_ids": [],
            }
            valid = self.preview(client, session, [goal], "valid-zero-inputs")
            self.assertEqual(valid.status_code, 200, valid.text)
            goal["loss_tolerance"] = "guaranteed_return"
            invalid = self.preview(client, session, [goal], "invalid-tolerance")
            self.assertEqual(invalid.status_code, 400)
            self.assertFalse(self.path.exists())

    def test_goal_account_assignments_are_unique_and_portfolio_bound(self):
        with self.client() as client:
            session = self.get_session(client)
            goal = self.linked_goal()
            duplicate = self.preview(client, session, [goal, {
                **goal, "name": "Second goal",
            }], "duplicate-link")
            self.assertEqual(duplicate.status_code, 400)
            self.assertEqual(duplicate.json()["error"], "goal_account_already_assigned")
            self.assertFalse(self.path.exists())

            ledger = self.initialize_journal()
            mismatch = self.preview(
                client, session, [self.linked_goal("another-portfolio")],
                "mismatched-portfolio", "synthetic-demo", ledger.revision)
            self.assertEqual(mismatch.status_code, 409)
            self.assertEqual(mismatch.json()["error"], "planning_portfolio_changed")
            unavailable = self.preview(
                client, session, [self.linked_goal(account_id="retired-account")],
                "unavailable-account", "synthetic-demo", ledger.revision)
            self.assertEqual(unavailable.status_code, 409)
            self.assertEqual(unavailable.json()["error"], "planning_account_unavailable")
            inconsistent = self.preview(
                client, session, [self.linked_goal() | {"current_amount_gbp": "10"}],
                "linked-manual-amount", "synthetic-demo", ledger.revision)
            self.assertEqual(inconsistent.status_code, 400)
            self.assertEqual(inconsistent.json()["error"], "invalid_goal_links")
            self.assertFalse(self.path.exists())

    def test_linked_goal_preview_and_confirmation_reject_changed_journal_revision(self):
        with self.client() as client:
            session = self.get_session(client)
            ledger = self.initialize_journal()
            goal = self.linked_goal()
            stale_preview = self.preview(
                client, session, [goal], "stale-journal-preview",
                "synthetic-demo", ledger.revision - 1)
            self.assertEqual(stale_preview.status_code, 409)
            self.assertEqual(stale_preview.json()["error"], "planning_portfolio_changed")

            preview = self.preview(
                client, session, [goal], "changed-journal-confirm",
                "synthetic-demo", ledger.revision)
            self.assertEqual(preview.status_code, 200, preview.text)
            later = NOW.replace(day=2)
            import_events(self.journal, [{
                "id": "later-contribution", "type": "contribution",
                "timestamp": iso(later), "available_at": iso(later),
                "account_id": "savings", "currency": "GBP",
                "amount_gbp": "1", "evidence": "synthetic test contribution",
            }], later)
            result = self.confirm(client, session, preview.json()["preview_id"])
            self.assertEqual(result.status_code, 409)
            self.assertEqual(result.json()["error"], "planning_portfolio_changed")
            self.assertFalse(self.path.exists())

    def test_malformed_existing_profile_is_not_repaired_or_overwritten(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text('{"schema_version":1,"revision":0}', encoding="utf-8")
        original = self.path.read_bytes()
        with self.client() as client:
            response = client.get("/api/investment/v1/planning/session")
            self.assertEqual(response.status_code, 503)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertFalse(self.journal.exists())

    def test_concurrent_dashboard_instances_cannot_overwrite_a_confirmed_goal_edit(self):
        first_client, second_client = self.client(), self.client()
        with first_client, second_client:
            sessions = [self.get_session(first_client), self.get_session(second_client)]
            names = ["Near-term reserve", "Long-term goal"]
            previews = []
            for client, session, name in zip(
                    (first_client, second_client), sessions, names):
                result = self.preview(
                    client, session, [self.basic_goal(name)], f"request-{name}")
                self.assertEqual(result.status_code, 200, result.text)
                previews.append(result.json()["preview_id"])
            start = Barrier(2)

            def save(client, session, preview_id):
                start.wait(timeout=5)
                return self.confirm(client, session, preview_id)

            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [
                    executor.submit(save, client, session, preview_id)
                    for client, session, preview_id in zip(
                        (first_client, second_client), sessions, previews)
                ]
                responses = [future.result(timeout=10) for future in futures]
            self.assertEqual(sorted(response.status_code for response in responses), [200, 409])
            self.assertEqual(
                self.get_session(first_client)["revision"], 1)
            self.assertIn(
                self.get_session(first_client)["goals"][0]["name"], names)
            self.assertFalse(self.journal.exists())

    def test_file_lock_makes_cross_instance_confirmation_conflict_deterministic(self):
        now = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)
        first, second = PlanningProfile(self.path), PlanningProfile(self.path)
        first_session, first_token = first.session_payload(None, now)
        second_session, second_token = second.session_payload(None, now)
        first_preview = first.preview(
            first_token, "first", first_session["revision"], first_session["profile_hash"],
            [self.basic_goal("First edit")], None, None, now)
        second_preview = second.preview(
            second_token, "second", second_session["revision"], second_session["profile_hash"],
            [self.basic_goal("Conflicting edit")], None, None, now)

        with first._file_lock():
            with self.assertRaises(PlanningError) as busy:
                second.confirm(second_token, second_preview["preview_id"], now)
        self.assertEqual(busy.exception.code, "planning_profile_busy")
        self.assertEqual(first.confirm(first_token, first_preview["preview_id"], now),
                         {"status": "saved", "revision": 1})
        with self.assertRaises(PlanningError) as stale:
            second.confirm(second_token, second_preview["preview_id"], now)
        self.assertEqual(stale.exception.code, "planning_profile_changed")
        current, _ = first.session_payload(first_token, now)
        self.assertEqual(current["goals"][0]["name"], "First edit")

    def test_profile_cannot_alias_the_investment_journal_or_live_under_workspace(self):
        with self.assertRaisesRegex(ValueError, "separate"):
            create_app(
                serve_frontend=False, investment_database=self.journal,
                planning_profile=self.journal)
        workspace = Path(self.directory.name) / "workspace"
        with self.assertRaisesRegex(ValueError, "outside"):
            create_app(
                serve_frontend=False, investment_database=workspace / "investment.sqlite3",
                setup_workspace=workspace, planning_profile=workspace / "goals.json")


if __name__ == "__main__":
    unittest.main()
