import copy
import io
import json
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
from investment.store import import_events, initialize
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

    def monitoring_workflow_stores(self, profile_observed_at=None):
        import json
        from investment.monitoring_reviews import (
            initialize_review_store, monitoring_cache_binding,
        )
        from investment.monitoring_store import initialize_cache, read_cache
        from investment.store import canonical, digest
        from test_investment_monitoring import monitoring_profile

        self.now = OPENING_NOW + timedelta(days=1)
        initialize(self.path, profile(), OPENING_NOW)
        import_events(self.path, [opening()], OPENING_NOW)
        monitoring = monitoring_profile(profile())
        if profile_observed_at is not None:
            monitoring["observed_at"] = profile_observed_at.isoformat().replace("+00:00", "Z")
        profile_path = self.path.with_name("monitoring.json")
        cache_path = self.path.with_name("monitoring-cache.sqlite3")
        reviews_path = self.path.with_name("monitoring-reviews.sqlite3")
        profile_path.write_text(json.dumps(monitoring), encoding="utf-8")
        initialize_cache(cache_path, monitoring, 86400)
        cache = read_cache(cache_path, monitoring, self.now)
        profile_hash = digest(canonical(monitoring))
        binding = monitoring_cache_binding(cache_path, cache["instance_id"])
        initialize_review_store(
            reviews_path, profile()["portfolio_id"], profile_hash, binding)
        return profile_path, cache_path, reviews_path, monitoring

    def test_monitoring_demo_disabled_and_recorded_reads_preserve_journal(self):
        import json
        from test_investment_monitoring import monitoring_profile, assignments
        from investment.store import read
        from investment.core import iso
        from investment.monitoring import project
        with self.client() as client:
            data = client.get("/api/investment/v1/monitoring").json()
            self.assertEqual(data["source_mode"], "demo")
            self.assertFalse(data["affects_trading"])
            self.assertTrue(data["findings"])
        investment_store.initialize(self.path, profile(), OPENING_NOW)
        investment_store.import_events(self.path, [opening(), *assignments()], OPENING_NOW)
        monitoring = monitoring_profile(profile())
        path = self.path.with_name("monitoring.json")
        path.write_text(json.dumps(monitoring), encoding="utf-8")
        original = self.path.read_bytes()
        with self.client(investment_database=self.path) as client:
            self.assertEqual(client.get("/api/investment/v1/monitoring").json(), {"enabled": False})
        with self.client(investment_database=self.path, monitoring_profile=path) as client:
            response = client.get("/api/investment/v1/monitoring")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), project(read(self.path, self.now), monitoring, self.now))
            self.assertEqual(client.post("/api/investment/v1/monitoring", json={}).status_code, 405)
            path.write_text("{}", encoding="utf-8")
            self.assertEqual(client.get("/api/investment/v1/monitoring").status_code, 503)
        self.assertEqual(self.path.read_bytes(), original)

    def test_verified_backup_download_and_restore_rehearsal_are_explicit_and_non_destructive(self):
        import zipfile

        initialize(self.path, profile(), OPENING_NOW)
        import_events(self.path, [opening()], OPENING_NOW)
        original = self.path.read_bytes()
        with self.client(investment_database=self.path) as client:
            unauthorized = client.post(
                "/api/investment/v1/backup/download", json={},
                headers={"Origin": "http://127.0.0.1"})
            self.assertEqual(unauthorized.status_code, 403)

            session = client.get("/api/investment/v1/backup/session")
            self.assertEqual(session.status_code, 200, session.text)
            self.assertTrue(session.json()["enabled"])
            headers = {"Origin": "http://127.0.0.1",
                       "X-CSRF-Token": session.json()["csrf"]}
            downloaded = client.post(
                "/api/investment/v1/backup/download", json={}, headers=headers)
            self.assertEqual(downloaded.status_code, 200, downloaded.text)
            self.assertEqual(downloaded.headers["content-type"], "application/zip")
            with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
                self.assertEqual(set(archive.namelist()),
                                 {"investment.sqlite3", "backup-receipt.json"})
                receipt = json.loads(archive.read("backup-receipt.json"))
                self.assertEqual(receipt["revision"], 1)

            rehearsed = client.post(
                "/api/investment/v1/backup/rehearsal",
                content=downloaded.content,
                headers={**headers, "Content-Type": "application/zip"})
            self.assertEqual(rehearsed.status_code, 200, rehearsed.text)
            self.assertEqual(rehearsed.json()["status"], "verified")
            self.assertEqual(rehearsed.json()["revision"], 1)
            self.assertEqual(self.path.read_bytes(), original)

            malformed = client.post(
                "/api/investment/v1/backup/rehearsal", content=b"not a zip",
                headers={**headers, "Content-Type": "application/zip"})
            self.assertEqual(malformed.status_code, 400)
            self.assertEqual(self.path.read_bytes(), original)

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
            self.assertEqual(legacy.status_code, 404)
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
                self.assertEqual(client.get("/api/v1/overview").status_code, 404)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_recorded_value_comparison_evidence_matches_real_replay(self):
        later = OPENING_NOW + timedelta(days=1)
        self.now = later
        initialize(self.path, profile(), OPENING_NOW)
        import_events(self.path, [
            opening(),
            event("equity-price", "mark", later, "broker", product_id="equity", price_gbp="11"),
            event("bond-price", "mark", later, "broker", product_id="bond", price_gbp="20"),
            event("deposit", "contribution", later, "bank", amount_gbp="100"),
            event("withdrawal", "withdrawal", later, "bank", amount_gbp="20"),
            event("internal", "transfer", later, "bank", amount_gbp="5",
                  destination_account_id="broker"),
            event("interest", "interest", later, "bank", amount_gbp="2"),
            event("buy", "buy", later, "broker", product_id="equity",
                  quantity="1", price_gbp="11", fee_gbp="1"),
        ], later)
        original = self.path.read_bytes()
        with self.client(investment_database=self.path) as client:
            response = client.get("/api/investment/v1/snapshot")
            self.assertEqual(response.status_code, 200, response.text)
            report = response.json()
        start, end = report["valuation_history"]
        self.assertEqual((start["equity_gbp"], end["equity_gbp"]), ("2000", "2131"))
        self.assertEqual((start["net_contributions_gbp"], end["net_contributions_gbp"]), ("0", "80"))
        self.assertEqual((start["capital_basis_gbp"], end["capital_basis_gbp"]), ("2000", "2080"))
        self.assertEqual([(item["type"], item["amount_gbp"]) for item in report["recorded_flow_events"]],
                         [("contribution", "100"), ("withdrawal", "20"), ("transfer", "5")])
        self.assertEqual(report["recorded_income_events"][0]["amount_gbp"], "2")
        self.assertEqual(report["recorded_fee_events"][0]["fee_gbp"], "1")
        self.assertEqual(end["source_available_at"], iso(later))
        self.assertEqual(self.path.read_bytes(), original)

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

    def test_opt_in_recording_preview_commit_and_lost_response_retry(self):
        self.now = OPENING_NOW
        initialize(self.path, profile(), self.now)
        original = self.path.read_bytes()
        with self.client(
                investment_database=self.path,
                investment_recording_enabled=True) as client:
            session = client.get("/api/investment/v1/recording/session").json()
            self.assertTrue(session["enabled"])
            self.assertNotIn("authenticated", session)
            self.assertEqual([item["id"] for item in session["accounts"]],
                             ["bank", "broker"])
            headers = {"Origin": "http://127.0.0.1",
                       "X-CSRF-Token": session["csrf"]}
            self.assertEqual(client.post(
                "/api/investment/v1/recording/preview",
                json={"request_id": "unauthorized", "event": opening()},
                headers={"Origin": "http://127.0.0.1"}).status_code, 403)
            self.assertEqual(client.post(
                "/api/investment/v1/recording/login",
                json={"password": "unused"}, headers=headers).status_code, 405)
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
                investment_recording_enabled=True) as client:
            session = client.get("/api/investment/v1/recording/session").json()
            headers = {"Origin": "http://127.0.0.1",
                       "X-CSRF-Token": session["csrf"]}
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
                investment_recording_enabled=True) as client:
            session = client.get("/api/investment/v1/recording/session").json()
            headers = {"Origin": "http://127.0.0.1",
                       "X-CSRF-Token": session["csrf"]}
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
                investment_recording_enabled=True) as client:
            session = client.get("/api/investment/v1/recording/session").json()
            csrf = session["csrf"]
            headers = {"Origin": "http://127.0.0.1", "X-CSRF-Token": csrf}
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

    def test_recording_preview_exposes_flow_time_performance_loss_without_writing(self):
        initialize(self.path, profile(), OPENING_NOW)
        import_events(self.path, [opening()], OPENING_NOW)
        self.now = OPENING_NOW + timedelta(days=7)
        original = self.path.read_bytes()
        with self.client(investment_database=self.path,
                         investment_recording_enabled=True) as client:
            session = client.get("/api/investment/v1/recording/session").json()
            headers = {"Origin": "http://127.0.0.1", "X-CSRF-Token": session["csrf"]}
            flow = event("weekly-deposit", "contribution", self.now, "bank", amount_gbp="100")
            response = client.post(
                "/api/investment/v1/recording/preview",
                json={"request_id": "unpriced-flow", "event": flow}, headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            preview = response.json()
            self.assertEqual(preview["before"]["twr_pct"], "0")
            self.assertIsNone(preview["after"]["twr_pct"])
            self.assertIsNone(preview["after"]["max_drawdown_pct"])
            self.assertEqual(preview["before"]["revision"], 1)
            self.assertEqual(preview["after"]["revision"], 2)
            self.assertTrue(preview["after"]["warnings"])
            self.assertEqual(self.path.read_bytes(), original)

            # Source-supported same-clock marks precede the flow, never follow it.
            marks = [
                event("weekly-equity", "mark", self.now, "broker",
                      product_id="equity", price_gbp="10"),
                event("weekly-bond", "mark", self.now, "broker",
                      product_id="bond", price_gbp="20"),
            ]
            import_events(self.path, marks, self.now)
            marked = self.path.read_bytes()
            response = client.post(
                "/api/investment/v1/recording/preview",
                json={"request_id": "supported-flow", "event": flow}, headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            preview = response.json()
            self.assertEqual(preview["after"]["twr_pct"], "0")
            self.assertEqual(preview["after"]["max_drawdown_pct"], "0")
            self.assertEqual(self.path.read_bytes(), marked)
            receipt = client.post(
                "/api/investment/v1/recording/confirm",
                json={"preview_id": preview["preview_id"]}, headers=headers)
            self.assertEqual(receipt.status_code, 200, receipt.text)
            reloaded = client.get("/api/investment/v1/snapshot")
            self.assertEqual(reloaded.status_code, 200, reloaded.text)
            self.assertEqual(reloaded.json(), preview["after"])
            bank = next(account for account in reloaded.json()["accounts"] if account["id"] == "bank")
            self.assertEqual(bank["cash_gbp"], "1100")

    def test_opt_in_correction_preview_keeps_original_event_and_reports_replacement(self):
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
                investment_recording_enabled=True) as client:
            session = client.get("/api/investment/v1/recording/session").json()
            headers = {"Origin": "http://127.0.0.1",
                       "X-CSRF-Token": session["csrf"]}
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

    def test_profile_change_and_scope_addition_recording_previews_are_schema_valid(self):
        self.now = OPENING_NOW
        initialize(self.path, profile(), self.now)
        import_events(self.path, [opening()], self.now)
        rename_at = OPENING_NOW + timedelta(hours=1)
        self.now = rename_at
        with self.client(
                investment_database=self.path,
                investment_recording_enabled=True) as client:
            session = client.get("/api/investment/v1/recording/session").json()
            headers = {"Origin": "http://127.0.0.1",
                       "X-CSRF-Token": session["csrf"]}
            rename = {
                "id": "rename-equity", "type": "profile_change",
                "timestamp": iso(rename_at), "available_at": iso(rename_at),
                "evidence": "Synthetic product documentation", "action": "rename",
                "target": "product", "entity_id": "equity", "name": "Renamed equity",
            }
            preview = client.post(
                "/api/investment/v1/recording/preview",
                json={"request_id": "rename-equity", "event": rename}, headers=headers)
            self.assertEqual(preview.status_code, 200, preview.text)
            self.assertEqual(preview.json()["after"]["recorded_profile_changes"][0]["name"],
                             "Renamed equity")
            self.assertEqual(preview.json()["after"]["since_baseline_gain_gbp"], "0")
            receipt = client.post(
                "/api/investment/v1/recording/confirm",
                json={"preview_id": preview.json()["preview_id"]}, headers=headers)
            self.assertEqual(receipt.status_code, 200, receipt.text)

            scope_at = rename_at + timedelta(hours=1)
            self.now = scope_at
            session = client.get("/api/investment/v1/recording/session").json()
            headers["X-CSRF-Token"] = session["csrf"]
            self.assertEqual(session["products"][0]["name"], "Renamed equity")
            scope = {
                "id": "add-scope", "type": "scope_addition",
                "timestamp": iso(scope_at), "available_at": iso(scope_at),
                "evidence": "Synthetic common-clock statements",
                "account": {"id": "scope", "name": "Additional broker", "kind": "broker",
                            "binding": "scope-test", "currency": "GBP"},
                "balances_gbp": {"bank": "1000", "broker": "100", "scope": "10"},
                "holdings": [
                    {"account_id": "broker", "product_id": "equity", "quantity": "50",
                     "price_gbp": "10"},
                    {"account_id": "broker", "product_id": "bond", "quantity": "20",
                     "price_gbp": "20"},
                    {"account_id": "scope", "product_id": "equity", "quantity": "2",
                     "price_gbp": "5"},
                ],
            }
            preview = client.post(
                "/api/investment/v1/recording/preview",
                json={"request_id": "add-scope", "event": scope}, headers=headers)
            self.assertEqual(preview.status_code, 200, preview.text)
            self.assertEqual(preview.json()["after"]["recorded_scope_additions_gbp"], "20")
            self.assertEqual(preview.json()["after"]["net_contributions_gbp"], "0")
            receipt = client.post(
                "/api/investment/v1/recording/confirm",
                json={"preview_id": preview.json()["preview_id"]}, headers=headers)
            self.assertEqual(receipt.status_code, 200, receipt.text)
            result = client.get("/api/investment/v1/snapshot")
            self.assertEqual(result.status_code, 200, result.text)

        report = result.json()
        self.assertEqual(report["recorded_scope_additions_gbp"], "20")
        self.assertEqual(report["net_contributions_gbp"], "0")
        self.assertEqual(report["accounts"][-1]["id"], "scope")

    def test_opt_in_non_cash_historical_correction_invalidates_performance(self):
        self.now = OPENING_NOW
        initialize(self.path, profile(), self.now)
        marked_at = OPENING_NOW + timedelta(hours=1)
        mark = event("historical-mark", "mark", marked_at, "broker",
                     product_id="equity", price_gbp="11")
        import_events(self.path, [opening(), mark], marked_at)

        corrected_at = marked_at + timedelta(days=1)
        self.now = corrected_at
        replacement = dict(
            mark, id="replacement-mark", price_gbp="12",
            available_at=iso(corrected_at), evidence="Synthetic corrected mark evidence")
        correction = {
            "id": "correction-mark", "type": "correction",
            "timestamp": iso(corrected_at), "available_at": iso(corrected_at),
            "evidence": "Synthetic corrected mark evidence",
            "original_event_id": mark["id"], "replacement_event": replacement,
        }
        with self.client(
                investment_database=self.path,
                investment_recording_enabled=True) as client:
            session = client.get("/api/investment/v1/recording/session").json()
            headers = {"Origin": "http://127.0.0.1",
                       "X-CSRF-Token": session["csrf"]}
            preview = client.post(
                "/api/investment/v1/recording/preview",
                json={"request_id": "correct-historical-mark", "event": correction},
                headers=headers)
            self.assertEqual(preview.status_code, 200, preview.text)
            self.assertIsNone(preview.json()["after"]["since_baseline_gain_gbp"])
            receipt = client.post(
                "/api/investment/v1/recording/confirm",
                json={"preview_id": preview.json()["preview_id"]}, headers=headers)
            self.assertEqual(receipt.status_code, 200, receipt.text)
            result = client.get("/api/investment/v1/snapshot")
            self.assertEqual(result.status_code, 200, result.text)

        report = result.json()
        self.assertEqual(report["holdings"][1]["value_gbp"], "600")
        self.assertIsNone(report["since_baseline_gain_gbp"])
        self.assertEqual(report["valuation_history"][-1]["gap_reason"],
                         "Historical correction invalidated performance evidence; no new baseline was established.")

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

    def test_recorded_proposal_is_blocked_read_only_and_schema_valid(self):
        spec = demo_profile(self.now)
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

    def test_dashboard_review_inbox_is_read_only_until_explicit_opt_in_sync(self):
        from investment.monitoring_reviews import review_inbox

        profile_path, cache_path, reviews_path, _ = self.monitoring_workflow_stores()
        journal_before = self.path.read_bytes()
        review_before = reviews_path.read_bytes()
        with self.client(investment_database=self.path, monitoring_profile=profile_path,
                         monitoring_cache=cache_path, monitoring_reviews=reviews_path) as client:
            response = client.get("/api/investment/v1/monitoring/reviews")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(response.json()["enabled"])
            self.assertFalse(response.json()["sync_current"])
            self.assertEqual(response.json()["cases"], [])
            self.assertEqual(client.post(
                "/api/investment/v1/monitoring-workflow/action", json={},
                headers={"Origin": "http://127.0.0.1"}).status_code, 405)
        self.assertEqual(self.path.read_bytes(), journal_before)
        self.assertEqual(reviews_path.read_bytes(), review_before)
        self.assertEqual(review_inbox(reviews_path)["revision"], 0)

    def test_opt_in_review_actions_require_current_sync_and_are_idempotent(self):
        from investment.monitoring_reviews import review_inbox
        from investment.store import read as read_journal
        from test_investment_opening import cash_observation

        profile_path, cache_path, reviews_path, _ = self.monitoring_workflow_stores()
        with self.client(investment_database=self.path, monitoring_profile=profile_path,
                         monitoring_cache=cache_path, monitoring_reviews=reviews_path,
                         monitoring_workflow_enabled=True) as client:
            session = client.get("/api/investment/v1/monitoring-workflow/session").json()
            self.assertTrue(session["enabled"])
            self.assertNotIn("authenticated", session)
            self.assertIn({"account_id": "broker", "name": "Broker", "provider": "etoro"},
                          session["accounts"])
            self.assertEqual(session["products"], [
                {"product_id": "equity", "name": "Equity fund"},
                {"product_id": "bond", "name": "Bond fund"},
            ])
            headers = {"Origin": "http://127.0.0.1", "X-CSRF-Token": session["csrf"]}
            self.assertEqual(client.post(
                "/api/investment/v1/monitoring-workflow/action", json={},
                headers={"Origin": "http://127.0.0.1"}).status_code, 403)
            self.assertEqual(client.post(
                "/api/investment/v1/monitoring-workflow/login",
                json={"password": "unused"}, headers=headers).status_code, 405)
            no_csrf = client.post(
                "/api/investment/v1/monitoring-workflow/sync", json={},
                headers={"Origin": "http://127.0.0.1"})
            self.assertEqual(no_csrf.status_code, 403)
            foreign = client.post(
                "/api/investment/v1/monitoring-workflow/sync", json={},
                headers={"Origin": "https://foreign.invalid",
                         "X-CSRF-Token": session["csrf"]})
            self.assertEqual(foreign.status_code, 403)
            synced = client.post(
                "/api/investment/v1/monitoring-workflow/sync", json={}, headers=headers)
            self.assertEqual(synced.status_code, 200, synced.text)
            inbox = client.get("/api/investment/v1/monitoring/reviews").json()
            self.assertTrue(inbox["sync_current"])
            case = next(item for item in inbox["cases"] if item["lifecycle"] == "active")
            action_payload = {
                "case_id": case["case_id"], "request_id": "dashboard-action-1",
                "expected_revision": inbox["revision"], "action": "acknowledged",
                "note": "Checked local source date",
            }
            action = client.post(
                "/api/investment/v1/monitoring-workflow/action",
                json=action_payload, headers=headers)
            self.assertEqual(action.status_code, 200, action.text)
            duplicate = client.post(
                "/api/investment/v1/monitoring-workflow/action",
                json=action_payload, headers=headers)
            self.assertEqual(duplicate.status_code, 200, duplicate.text)
            self.assertTrue(duplicate.json()["duplicate"])
            before_revision = review_inbox(reviews_path)["revision"]
            import_events(self.path, [cash_observation(
                timestamp=OPENING_NOW, available_at=OPENING_NOW,
                identifier="later-dashboard-observation")], self.now)
            stale = client.get("/api/investment/v1/monitoring/reviews").json()
            self.assertFalse(stale["sync_current"])
            action_payload.update(request_id="dashboard-stale-action",
                                  expected_revision=stale["revision"])
            rejected = client.post(
                "/api/investment/v1/monitoring-workflow/action",
                json=action_payload, headers=headers)
            self.assertEqual(rejected.status_code, 409)
            self.assertEqual(rejected.json()["error"], "stale_review_sync")
            self.assertEqual(review_inbox(reviews_path)["revision"], before_revision)
            self.assertGreater(read_journal(self.path, self.now).revision, 1)

    def test_manual_observation_requires_preview_and_stages_only_monitoring_cache(self):
        from investment.monitoring_store import read_cache

        profile_path, cache_path, reviews_path, monitoring = self.monitoring_workflow_stores()
        original_journal = self.path.read_bytes()
        observation = {
            "id": "manual-dashboard-observation", "account_id": "broker",
            "provider": "etoro", "observed_at": (OPENING_NOW + timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
            "available_at": (OPENING_NOW + timedelta(minutes=2)).isoformat().replace("+00:00", "Z"),
            "currency": "GBP", "cash": "50",
            "holdings": [{"source_product": "local-export-fund", "quantity": "2"}],
            "coverage": "partial", "evidence": "Operator-normalized local test evidence",
        }
        request_payload = {
            "request_id": "manual-attempt-1",
            "mapping_observed_at": OPENING_NOW.isoformat().replace("+00:00", "Z"),
            "observation": observation,
        }
        with self.client(investment_database=self.path, monitoring_profile=profile_path,
                         monitoring_cache=cache_path, monitoring_reviews=reviews_path,
                         monitoring_workflow_enabled=True) as client:
            session = client.get("/api/investment/v1/monitoring-workflow/session").json()
            headers = {"Origin": "http://127.0.0.1", "X-CSRF-Token": session["csrf"]}
            preview = client.post(
                "/api/investment/v1/monitoring-workflow/observation/preview",
                json=request_payload, headers=headers)
            self.assertEqual(preview.status_code, 200, preview.text)
            preview_body = preview.json()
            self.assertEqual(preview_body["stages_only_to"], "monitoring_cache")
            self.assertFalse(preview_body["investment_journal_changed"])
            self.assertEqual(read_cache(cache_path, monitoring, self.now)["revision"], 0)
            self.assertEqual(self.path.read_bytes(), original_journal)
            confirmed = client.post(
                "/api/investment/v1/monitoring-workflow/observation/confirm",
                json={"preview_id": preview_body["preview_id"]}, headers=headers)
            self.assertEqual(confirmed.status_code, 200, confirmed.text)
            self.assertEqual(confirmed.json()["cache_revision"], 1)
            self.assertFalse(confirmed.json()["investment_journal_changed"])
            self.assertEqual(read_cache(cache_path, monitoring, self.now)["revision"], 1)
            self.assertEqual(self.path.read_bytes(), original_journal)
            retried = client.post(
                "/api/investment/v1/monitoring-workflow/observation/confirm",
                json={"preview_id": preview_body["preview_id"]}, headers=headers)
            self.assertEqual(retried.status_code, 200, retried.text)
            self.assertEqual(retried.json(), confirmed.json())
        with self.client(investment_database=self.path, monitoring_profile=profile_path,
                         monitoring_cache=cache_path, monitoring_reviews=reviews_path,
                         monitoring_workflow_enabled=True) as restarted:
            session = restarted.get("/api/investment/v1/monitoring-workflow/session").json()
            headers = {"Origin": "http://127.0.0.1", "X-CSRF-Token": session["csrf"]}
            retry_preview = restarted.post(
                "/api/investment/v1/monitoring-workflow/observation/preview",
                json=request_payload, headers=headers)
            self.assertEqual(retry_preview.status_code, 200, retry_preview.text)
            self.assertTrue(retry_preview.json()["already_staged"])
            retry_confirmation = restarted.post(
                "/api/investment/v1/monitoring-workflow/observation/confirm",
                json={"preview_id": retry_preview.json()["preview_id"]}, headers=headers)
            self.assertEqual(retry_confirmation.status_code, 200, retry_confirmation.text)
            self.assertEqual(retry_confirmation.json()["cache_revision"], 1)
            self.assertEqual(read_cache(cache_path, monitoring, self.now)["revision"], 1)
        self.assertEqual(self.path.read_bytes(), original_journal)

    def test_manual_observation_preview_does_not_backdate_profile_mapping(self):
        profile_path, cache_path, reviews_path, _ = self.monitoring_workflow_stores(
            OPENING_NOW + timedelta(minutes=10))
        with self.client(investment_database=self.path, monitoring_profile=profile_path,
                         monitoring_cache=cache_path, monitoring_reviews=reviews_path,
                         monitoring_workflow_enabled=True) as client:
            session = client.get("/api/investment/v1/monitoring-workflow/session").json()
            headers = {"Origin": "http://127.0.0.1", "X-CSRF-Token": session["csrf"]}
            observation = {
                "id": "mapping-time-observation", "account_id": "broker",
                "provider": "etoro",
                "observed_at": (OPENING_NOW + timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
                "available_at": (OPENING_NOW + timedelta(minutes=2)).isoformat().replace("+00:00", "Z"),
                "currency": "GBP", "cash": "50", "holdings": [],
                "coverage": "partial", "evidence": "Synthetic local evidence",
            }
            response = client.post(
                "/api/investment/v1/monitoring-workflow/observation/preview",
                json={"request_id": "mapping-time-attempt",
                      "mapping_observed_at": OPENING_NOW.isoformat().replace("+00:00", "Z"),
                      "observation": observation}, headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(
                response.json()["mapping_observed_at"],
                iso(OPENING_NOW + timedelta(minutes=10)))
            compared = response.json()["reconciliation"]["accounts"][0]
            self.assertEqual(
                compared["comparison_unavailable_reason"],
                "mapping_not_available_at_observation")

    def test_manual_observation_confirmation_rejects_stale_journal_revision(self):
        from investment.monitoring_store import read_cache
        from test_investment_opening import cash_observation

        profile_path, cache_path, reviews_path, monitoring = self.monitoring_workflow_stores()
        with self.client(investment_database=self.path, monitoring_profile=profile_path,
                         monitoring_cache=cache_path, monitoring_reviews=reviews_path,
                         monitoring_workflow_enabled=True) as client:
            session = client.get("/api/investment/v1/monitoring-workflow/session").json()
            headers = {"Origin": "http://127.0.0.1", "X-CSRF-Token": session["csrf"]}
            observation = {
                "id": "stale-preview-observation", "account_id": "broker",
                "provider": "etoro",
                "observed_at": (OPENING_NOW + timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
                "available_at": (OPENING_NOW + timedelta(minutes=2)).isoformat().replace("+00:00", "Z"),
                "currency": "GBP", "cash": "50", "holdings": [],
                "coverage": "partial", "evidence": "Synthetic local evidence",
            }
            preview = client.post(
                "/api/investment/v1/monitoring-workflow/observation/preview",
                json={"request_id": "stale-preview-attempt",
                      "mapping_observed_at": OPENING_NOW.isoformat().replace("+00:00", "Z"),
                      "observation": observation}, headers=headers)
            self.assertEqual(preview.status_code, 200, preview.text)
            import_events(self.path, [cash_observation(
                timestamp=OPENING_NOW, available_at=OPENING_NOW,
                identifier="journal-changed-after-preview")], self.now)
            rejected = client.post(
                "/api/investment/v1/monitoring-workflow/observation/confirm",
                json={"preview_id": preview.json()["preview_id"]}, headers=headers)
            self.assertEqual(rejected.status_code, 409)
            self.assertEqual(rejected.json()["error"], "stale_observation_preview")
            self.assertEqual(read_cache(cache_path, monitoring, self.now)["revision"], 0)

    def test_invalid_report_is_not_a_success_response(self):
        report = copy.deepcopy(demo_opening_snapshot(self.now))
        report["equity_gbp"] = "NaN"
        with patch("dashboard.investment_demo.demo_opening_snapshot", return_value=report), \
                self.client() as client:
            response = client.get("/api/investment/v1/snapshot")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"], "investment_invalid")


if __name__ == "__main__":
    unittest.main()
