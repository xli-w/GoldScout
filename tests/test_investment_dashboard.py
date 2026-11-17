import copy
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from dashboard.app import create_app
from fastapi.testclient import TestClient
from investment import InvestmentError
from dashboard.investment_demo import demo_opening_snapshot
from investment.service import demo_events, demo_profile, snapshot
from investment import store as investment_store
from investment.store import acknowledge_review, import_events, initialize
from test_dashboard_api import MemoryEventLoop
from test_investment_opening import (NOW as OPENING_NOW, event, opening, profile,
                                     cash_observation, iso, savings_terms,
                                     savings_terms_event)


class InvestmentDashboardTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "investment.sqlite3"
        self.now = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)

    def client(self, **options):
        return TestClient(create_app(serve_frontend=False, clock=lambda: self.now, **options),
                          base_url="http://127.0.0.1", backend_options={"loop_factory": MemoryEventLoop})

    def test_synthetic_investment_is_valid_and_cannot_mutate_any_account(self):
        with self.client() as client:
            response = client.get("/api/investment/v1/snapshot")
            self.assertEqual(response.status_code, 200, response.text)
            report = response.json()
            self.assertEqual(report["source_mode"], "demo")
            self.assertEqual(report["currency"], "GBP")
            self.assert_blocked_proposal(report["proposal"])
            for route in ("/api/investment/v1/snapshot", "/api/v1/orders"):
                self.assertEqual(client.post(route, json={}, headers={"Origin": "http://127.0.0.1"}).status_code, 405)
            self.assertEqual(client.get("/api/investment/v1/snapshot", headers={"Origin": "https://foreign.invalid"}).status_code, 403)
        self.assertEqual(list(self.path.parent.iterdir()), [])

    def test_recorded_source_never_falls_back_to_demo_or_creates_missing_store(self):
        with self.client(investment_database=self.path) as client:
            self.assertEqual(client.get("/api/v1/settings").json()["workspace"], "investment")
            self.assertEqual(client.get("/api/investment/v1/snapshot").status_code, 503)
            legacy = client.get("/api/v1/overview")
            self.assertEqual(legacy.status_code, 503)
            self.assertEqual(legacy.json()["meta"]["source_mode"], "recorded")
        self.assertFalse(self.path.exists())

    def test_recorded_snapshot_matches_verified_replay_without_mutating_store(self):
        initialize(self.path, demo_profile(self.now), self.now)
        import_events(self.path, demo_events(self.now), self.now)
        expected = snapshot(self.path, self.now)
        original = self.path.read_bytes()
        with self.client(investment_database=self.path) as client:
            for _ in range(2):
                response = client.get("/api/investment/v1/snapshot")
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json(), expected)
                self.assertEqual(response.json()["source_mode"], "recorded")
                self.assert_blocked_proposal(response.json()["proposal"])
                self.assertEqual(client.get("/api/v1/overview").status_code, 503)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_v2_opening_snapshot_is_read_only_and_preserves_unknown_basis(self):
        self.now = OPENING_NOW
        initialize(self.path, profile(), self.now)
        import_events(self.path, [opening()], self.now)
        expected = snapshot(self.path, self.now)
        original = self.path.read_bytes()
        with self.client(investment_database=self.path) as client:
            response = client.get("/api/investment/v1/snapshot")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), expected)
            self.assertEqual(response.json()["schema_version"], 2)
            self.assertIsNone(response.json()["lifetime_gain_gbp"])
            self.assertEqual(client.post("/api/investment/v1/snapshot", json={},
                                         headers={"Origin": "http://127.0.0.1"}).status_code, 405)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_v2_recorded_event_history_is_schema_valid_and_read_only(self):
        self.now = OPENING_NOW
        initialize(self.path, profile(), self.now)
        import_events(self.path, [
            opening(),
            event("recorded-buy", "buy", OPENING_NOW, "broker", product_id="equity",
                  quantity="1", price_gbp="10", fee_gbp="2"),
            event("recorded-interest", "interest", OPENING_NOW, "bank", amount_gbp="5"),
            event("recorded-contribution", "contribution", OPENING_NOW, "bank",
                  amount_gbp="100"),
            event("recorded-transfer", "transfer", OPENING_NOW, "bank",
                  amount_gbp="50", destination_account_id="broker"),
            event("recorded-withdrawal", "withdrawal", OPENING_NOW, "bank",
                  amount_gbp="20"),
        ], self.now)
        original = self.path.read_bytes()

        with self.client(investment_database=self.path) as client:
            response = client.get("/api/investment/v1/snapshot")
        self.assertEqual(response.status_code, 200, response.text)
        report = response.json()
        self.assertEqual(report["recorded_fee_events"][0]["id"], "recorded-buy")
        self.assertEqual(report["recorded_fee_events"][0]["available_at"], iso(OPENING_NOW))
        self.assertEqual(report["recorded_income_events"][0]["id"], "recorded-interest")
        self.assertIsNone(report["recorded_income_events"][0]["product_id"])
        self.assertEqual(report["recorded_income_events"][0]["evidence"],
                         "synthetic event evidence")
        self.assertEqual([item["id"] for item in report["recorded_flow_events"]],
                         ["recorded-contribution", "recorded-transfer",
                          "recorded-withdrawal"])
        self.assertEqual(report["recorded_flow_events"][1]["destination_account_id"],
                         "broker")
        self.assertEqual(self.path.read_bytes(), original)

    def test_v2_unopened_store_does_not_invent_zero_balances(self):
        self.now = OPENING_NOW
        initialize(self.path, profile(), self.now)
        with self.client(investment_database=self.path) as client:
            response = client.get("/api/investment/v1/snapshot")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertFalse(response.json()["complete"])
            self.assertTrue(all(account["cash_gbp"] is None
                                for account in response.json()["accounts"]))

    def test_recording_is_disabled_by_default_and_mutation_routes_stay_closed(self):
        self.now = OPENING_NOW
        initialize(self.path, profile(), self.now)
        with self.client(investment_database=self.path) as client:
            session = client.get("/api/investment/v1/recording/session")
            self.assertEqual(session.status_code, 200)
            self.assertFalse(session.json()["enabled"])
            self.assertEqual(client.post(
                "/api/investment/v1/recording/preview", json={},
                headers={"Origin": "http://127.0.0.1"}).status_code, 405)
            self.assertEqual(client.post(
                "/api/investment/v1/recording/confirm", json={},
                headers={"Origin": "http://127.0.0.1"}).status_code, 405)

    def test_authenticated_recording_preview_commit_and_lost_response_retry(self):
        self.now = OPENING_NOW
        initialize(self.path, profile(), self.now)
        original = self.path.read_bytes()
        with self.client(
                investment_database=self.path,
                investment_recording_password="local-recording-passphrase") as client:
            session = client.get("/api/investment/v1/recording/session").json()
            self.assertTrue(session["enabled"])
            self.assertFalse(session["authenticated"])
            self.assertEqual(session["accounts"], [])
            headers = {"Origin": "http://127.0.0.1",
                       "X-CSRF-Token": session["csrf"]}
            self.assertEqual(client.post(
                "/api/investment/v1/recording/preview",
                json={"request_id": "unauthorized", "event": opening()},
                headers=headers).status_code, 403)
            self.assertEqual(client.post(
                "/api/investment/v1/recording/login", json={"password": "wrong"},
                headers=headers).status_code, 403)
            self.assertEqual(client.post(
                "/api/investment/v1/recording/login",
                json={"password": "local-recording-passphrase"},
                headers=headers).status_code, 200)
            authenticated = client.get("/api/investment/v1/recording/session").json()
            self.assertTrue(authenticated["authenticated"])
            self.assertEqual([item["id"] for item in authenticated["accounts"]],
                             ["bank", "broker"])
            headers["X-CSRF-Token"] = authenticated["csrf"]
            preview = client.post(
                "/api/investment/v1/recording/preview",
                json={"request_id": "open-portfolio", "event": opening()},
                headers=headers)
            self.assertEqual(preview.status_code, 200, preview.text)
            self.assertEqual(preview.json()["revision"], 0)
            self.assertEqual(preview.json()["after"]["equity_gbp"], "2000")
            self.assertEqual(self.path.read_bytes(), original)
            preview_id = preview.json()["preview_id"]
            confirmation = client.post(
                "/api/investment/v1/recording/confirm",
                json={"preview_id": preview_id}, headers=headers)
            self.assertEqual(confirmation.status_code, 200, confirmation.text)
            self.assertEqual(confirmation.json(), {"id": "opening", "revision": 1})
            retry = client.post(
                "/api/investment/v1/recording/confirm",
                json={"preview_id": preview_id}, headers=headers)
            self.assertEqual(retry.status_code, 200, retry.text)
            self.assertEqual(retry.json(), confirmation.json())
            snapshot_response = client.get("/api/investment/v1/snapshot")
            self.assertEqual(snapshot_response.status_code, 200, snapshot_response.text)
            self.assertEqual(snapshot_response.json()["revision"], 1)

    def test_confirmation_storage_failure_is_retryable_with_same_preview(self):
        self.now = OPENING_NOW
        initialize(self.path, profile(), self.now)
        import_events(self.path, [opening()], self.now)
        with self.client(
                investment_database=self.path,
                investment_recording_password="local-recording-passphrase") as client:
            session = client.get("/api/investment/v1/recording/session").json()
            headers = {"Origin": "http://127.0.0.1",
                       "X-CSRF-Token": session["csrf"]}
            client.post("/api/investment/v1/recording/login",
                        json={"password": "local-recording-passphrase"}, headers=headers)
            value = event("retryable-interest", "interest", OPENING_NOW, "bank",
                          amount_gbp="5")
            preview = client.post(
                "/api/investment/v1/recording/preview",
                json={"request_id": "retryable-write", "event": value},
                headers=headers)
            self.assertEqual(preview.status_code, 200, preview.text)
            append_events = investment_store._append_events
            attempts = 0

            def fail_once(*args, **kwargs):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise sqlite3.OperationalError("database is locked")
                return append_events(*args, **kwargs)

            with patch("investment.store._append_events", side_effect=fail_once):
                failed = client.post(
                    "/api/investment/v1/recording/confirm",
                    json={"preview_id": preview.json()["preview_id"]}, headers=headers)
                self.assertEqual(failed.status_code, 503, failed.text)
                self.assertEqual(failed.json(), {
                    "error": "investment_store_unavailable", "retryable": True})
                retried = client.post(
                    "/api/investment/v1/recording/confirm",
                    json={"preview_id": preview.json()["preview_id"]}, headers=headers)
            self.assertEqual(retried.status_code, 200, retried.text)
            self.assertEqual(retried.json()["revision"], 2)
            self.assertEqual(
                snapshot(self.path, OPENING_NOW)["recorded_interest_gbp"], "5")

    def test_confirmation_missing_store_race_is_retryable_with_same_preview(self):
        self.now = OPENING_NOW
        initialize(self.path, profile(), self.now)
        import_events(self.path, [opening()], self.now)
        with self.client(
                investment_database=self.path,
                investment_recording_password="local-recording-passphrase") as client:
            session = client.get("/api/investment/v1/recording/session").json()
            headers = {"Origin": "http://127.0.0.1",
                       "X-CSRF-Token": session["csrf"]}
            client.post("/api/investment/v1/recording/login",
                        json={"password": "local-recording-passphrase"}, headers=headers)
            value = event("retryable-after-disappear", "interest", OPENING_NOW, "bank",
                          amount_gbp="5")
            preview = client.post(
                "/api/investment/v1/recording/preview",
                json={"request_id": "retryable-disappear", "event": value},
                headers=headers)
            self.assertEqual(preview.status_code, 200, preview.text)
            connect = investment_store._connect
            attempts = 0

            def unavailable_during_commit(database, readonly):
                nonlocal attempts
                attempts += 1
                if attempts == 2:
                    raise InvestmentError(
                        "Investment store missing; explicit init required") from FileNotFoundError(self.path)
                return connect(database, readonly)

            with patch("investment.store._connect", side_effect=unavailable_during_commit):
                failed = client.post(
                    "/api/investment/v1/recording/confirm",
                    json={"preview_id": preview.json()["preview_id"]}, headers=headers)
                self.assertEqual(failed.status_code, 503, failed.text)
                self.assertTrue(failed.json()["retryable"])
                retried = client.post(
                    "/api/investment/v1/recording/confirm",
                    json={"preview_id": preview.json()["preview_id"]}, headers=headers)
            self.assertEqual(retried.status_code, 200, retried.text)
            self.assertEqual(retried.json()["revision"], 2)

    def test_recording_stale_preview_and_origin_checks_fail_closed(self):
        self.now = OPENING_NOW
        initialize(self.path, profile(), self.now)
        import_events(self.path, [opening()], self.now)
        with self.client(
                investment_database=self.path,
                investment_recording_password="local-recording-passphrase") as client:
            session = client.get("/api/investment/v1/recording/session").json()
            csrf = session["csrf"]
            headers = {"Origin": "http://127.0.0.1", "X-CSRF-Token": csrf}
            client.post("/api/investment/v1/recording/login",
                        json={"password": "local-recording-passphrase"}, headers=headers)
            first = event("first-interest", "interest", OPENING_NOW, "bank", amount_gbp="5")
            second = event("second-interest", "interest", OPENING_NOW, "bank", amount_gbp="7")
            previews = []
            for request_id, value in (("first", first), ("second", second)):
                response = client.post(
                    "/api/investment/v1/recording/preview",
                    json={"request_id": request_id, "event": value}, headers=headers)
                self.assertEqual(response.status_code, 200, response.text)
                previews.append(response.json()["preview_id"])
            committed = client.post(
                "/api/investment/v1/recording/confirm",
                json={"preview_id": previews[0]}, headers=headers)
            self.assertEqual(committed.status_code, 200, committed.text)
            stale = client.post(
                "/api/investment/v1/recording/confirm",
                json={"preview_id": previews[1]}, headers=headers)
            self.assertEqual(stale.status_code, 409, stale.text)
            self.assertFalse(stale.json()["retryable"])
            cross_origin = client.post(
                "/api/investment/v1/recording/preview",
                json={"request_id": "foreign", "event": first},
                headers={"Origin": "https://foreign.invalid", "X-CSRF-Token": csrf})
            self.assertEqual(cross_origin.status_code, 403)
            no_csrf = client.post(
                "/api/investment/v1/recording/preview",
                json={"request_id": "missing-csrf", "event": first},
                headers={"Origin": "http://127.0.0.1"})
            self.assertEqual(no_csrf.status_code, 403)
        self.assertEqual(snapshot(self.path, OPENING_NOW)["recorded_interest_gbp"], "5")

    def test_authenticated_correction_preview_keeps_original_event_and_reports_replacement(self):
        self.now = OPENING_NOW
        initialize(self.path, profile(), self.now)
        contribution = event("recorded-contribution", "contribution", OPENING_NOW,
                             "bank", amount_gbp="100")
        import_events(self.path, [opening(), contribution], self.now)
        corrected_at = OPENING_NOW + timedelta(days=1)
        replacement = dict(contribution, id="replacement-contribution",
                           amount_gbp="80", available_at=iso(corrected_at),
                           evidence="Synthetic corrected statement")
        correction_event = {
            "id": "correction-contribution", "type": "correction",
            "timestamp": iso(OPENING_NOW), "available_at": iso(corrected_at),
            "evidence": "Synthetic corrected statement",
            "original_event_id": contribution["id"], "replacement_event": replacement,
        }
        self.now = corrected_at
        with self.client(
                investment_database=self.path,
                investment_recording_password="local-recording-passphrase") as client:
            session = client.get("/api/investment/v1/recording/session").json()
            headers = {"Origin": "http://127.0.0.1",
                       "X-CSRF-Token": session["csrf"]}
            client.post("/api/investment/v1/recording/login",
                        json={"password": "local-recording-passphrase"}, headers=headers)
            preview = client.post(
                "/api/investment/v1/recording/preview",
                json={"request_id": "correct-contribution", "event": correction_event},
                headers=headers)
            self.assertEqual(preview.status_code, 200, preview.text)
            self.assertEqual(preview.json()["after"]["net_contributions_gbp"], "80")
            receipt = client.post(
                "/api/investment/v1/recording/confirm",
                json={"preview_id": preview.json()["preview_id"]}, headers=headers)
            self.assertEqual(receipt.status_code, 200, receipt.text)
            result = client.get("/api/investment/v1/snapshot")
            self.assertEqual(result.status_code, 200, result.text)
        report = result.json()
        self.assertEqual(report["revision"], 3)
        self.assertEqual(report["net_contributions_gbp"], "80")
        self.assertEqual([item["id"] for item in report["recorded_flow_events"]],
                         ["replacement-contribution"])
        self.assertEqual(report["recorded_corrections"][0]["original_event_id"],
                         "recorded-contribution")

    def test_recording_requires_an_explicit_existing_version_two_store(self):
        self.now = OPENING_NOW
        initialize(self.path, demo_profile(self.now), self.now)
        with self.assertRaisesRegex(ValueError, "version 2"):
            self.client(investment_database=self.path,
                        investment_recording_password="local-recording-passphrase")

    def test_v2_savings_terms_are_read_only_and_do_not_change_portfolio_value(self):
        self.now = OPENING_NOW
        spec = profile()
        spec["accounts"][0]["terms"] = savings_terms()
        initialize(self.path, spec, self.now)
        import_events(self.path, [opening()], self.now)
        expected = snapshot(self.path, self.now)
        self.assertEqual(expected["equity_gbp"], "2000")
        original = self.path.read_bytes()
        with self.client(investment_database=self.path) as client:
            response = client.get("/api/investment/v1/snapshot")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), expected)
            self.assertEqual(response.json()["accounts"][0]["terms"], savings_terms())
        self.assertEqual(self.path.read_bytes(), original)

    def test_v2_savings_terms_update_is_exposed_read_only(self):
        self.now = OPENING_NOW
        initialize(self.path, profile(), self.now)
        import_events(self.path, [opening()], self.now)
        observed_at = OPENING_NOW + timedelta(days=1)
        update = savings_terms_event(observed_at)
        self.now = observed_at
        import_events(self.path, [update], self.now)
        expected = snapshot(self.path, self.now)
        original = self.path.read_bytes()

        with self.client(investment_database=self.path) as client:
            response = client.get("/api/investment/v1/snapshot")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), expected)
            bank = next(account for account in response.json()["accounts"]
                        if account["id"] == "bank")
            self.assertEqual(bank["terms"]["annual_rate_pct"], "3.75")
            self.assertEqual(bank["cash_gbp"], "1000")
            self.assertEqual(response.json()["equity_gbp"], "2000")
        self.assertEqual(self.path.read_bytes(), original)

    def test_v2_cash_observation_is_displayed_without_a_dashboard_write(self):
        self.now = OPENING_NOW
        initialize(self.path, profile(), self.now)
        import_events(self.path, [opening(), cash_observation()], self.now)
        expected = snapshot(self.path, self.now)
        original = self.path.read_bytes()
        with self.client(investment_database=self.path) as client:
            response = client.get("/api/investment/v1/snapshot")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), expected)
            bank = next(account for account in response.json()["accounts"]
                        if account["id"] == "bank")
            self.assertEqual(bank["cash_gbp"], "1000")
            self.assertEqual(bank["cash_observation"]["difference_gbp"], "-1")
        self.assertEqual(self.path.read_bytes(), original)

    def test_v2_snapshot_normalizes_utc_offset_evidence_for_schema(self):
        self.now = OPENING_NOW
        spec = profile()
        spec["accounts"][0]["terms"] = savings_terms()
        for field in ("observed_at", "available_at"):
            spec["accounts"][0]["terms"][field] = (
                spec["accounts"][0]["terms"][field].replace("Z", "+00:00"))
        initialize(self.path, spec, self.now)
        observation = cash_observation()
        observation["timestamp"] = observation["timestamp"].replace("Z", "+00:00")
        observation["available_at"] = observation["available_at"].replace("Z", "+00:00")
        next_day = OPENING_NOW + timedelta(days=1)
        terms_update = savings_terms_event(next_day)
        terms_update["timestamp"] = terms_update["timestamp"].replace("Z", "+00:00")
        terms_update["available_at"] = terms_update["available_at"].replace("Z", "+00:00")
        self.now = next_day
        import_events(self.path, [opening(), observation, terms_update], self.now)

        with self.client(investment_database=self.path) as client:
            response = client.get("/api/investment/v1/snapshot")
        self.assertEqual(response.status_code, 200, response.text)
        report = response.json()
        self.assertEqual(report["cash_observations"][0]["timestamp"], iso(OPENING_NOW))
        self.assertEqual(report["cash_observations"][0]["available_at"], iso(OPENING_NOW))
        bank = next(account for account in report["accounts"] if account["id"] == "bank")
        self.assertEqual(bank["cash_observation"]["timestamp"], iso(OPENING_NOW))
        self.assertEqual(bank["cash_observation"]["available_at"], iso(OPENING_NOW))
        self.assertEqual(bank["terms"]["observed_at"], iso(next_day))
        self.assertEqual(bank["terms"]["available_at"], iso(next_day))
        self.assertTrue(any(f"at {iso(OPENING_NOW)}" in warning
                            for warning in report["warnings"]))
        self.assertFalse(any(OPENING_NOW.isoformat() in warning
                             for warning in report["warnings"]))

    def test_v2_invalid_lifetime_gain_is_not_a_success_response(self):
        self.now = OPENING_NOW
        initialize(self.path, profile(), self.now)
        import_events(self.path, [opening()], self.now)
        report = snapshot(self.path, self.now)
        report["lifetime_gain_gbp"] = "0"
        with patch("investment.service.snapshot", return_value=report), \
                self.client(investment_database=self.path) as client:
            response = client.get("/api/investment/v1/snapshot")
        self.assertEqual(response.status_code, 409)

    def assert_blocked_proposal(self, proposal):
        self.assertEqual(proposal["status"], "blocked")
        self.assertTrue(proposal["reason"])
        self.assertIsNone(proposal["expires_at"])
        self.assertEqual(proposal["orders"], [])
        self.assertEqual(proposal["manual_transfers"], [])
        self.assertFalse(proposal["execution_enabled"])

    def test_recorded_overweight_proposal_is_blocked_read_only_and_schema_valid(self):
        spec = demo_profile(self.now)
        spec["policy"]["targets"] = {"equity": "60", "bond": "20", "cash": "20"}
        spec["products"].append(dict(spec["products"][0], id="bond", sleeve="bond"))
        initialize(self.path, spec, self.now)
        import_events(self.path, demo_events(self.now), self.now)
        original = self.path.read_bytes()
        with self.client(investment_database=self.path) as client:
            response = client.get("/api/investment/v1/snapshot")
            self.assertEqual(response.status_code, 200, response.text)
            self.assert_blocked_proposal(response.json()["proposal"])
            self.assertEqual(client.post("/api/investment/v1/snapshot", json={},
                                         headers={"Origin": "http://127.0.0.1"}).status_code, 405)
        self.assertEqual(self.path.read_bytes(), original)

    def test_acknowledgement_is_visible_but_dashboard_remains_read_only(self):
        initialize(self.path, demo_profile(self.now), self.now)
        import_events(self.path, demo_events(self.now), self.now)
        acknowledge_review(self.path, "review", "monthly", 4, "synthetic review", self.now)
        expected = snapshot(self.path, self.now)
        original = self.path.read_bytes()
        with self.client(investment_database=self.path) as client:
            response = client.get("/api/investment/v1/snapshot")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), expected)
            self.assertEqual(response.json()["revision"], 5)
            self.assertTrue(any("not trade approvals" in item
                                for item in response.json()["limitations"]))
            self.assertEqual(client.post("/api/investment/v1/snapshot", json={},
                                         headers={"Origin": "http://127.0.0.1"}).status_code, 405)
        self.assertEqual(self.path.read_bytes(), original)

    def test_legacy_source_does_not_implicitly_supply_investment_records(self):
        with self.client(database=self.path) as client:
            self.assertEqual(client.get("/api/investment/v1/snapshot").status_code, 503)
        self.assertFalse(self.path.exists())

    def test_invalid_report_is_not_a_success_response(self):
        report = copy.deepcopy(demo_opening_snapshot(self.now))
        report["equity_gbp"] = "NaN"
        with patch("dashboard.investment_demo.demo_opening_snapshot", return_value=report), \
                self.client() as client:
            response = client.get("/api/investment/v1/snapshot")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"], "investment_invalid")

    def test_explicit_sources_cannot_be_combined(self):
        with self.assertRaises(ValueError):
            create_app(serve_frontend=False, database=self.path, investment_database=self.path)


if __name__ == "__main__":
    unittest.main()
