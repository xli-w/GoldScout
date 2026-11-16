import copy
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from dashboard.app import create_app
from fastapi.testclient import TestClient
from investment.service import demo_events, demo_profile, demo_snapshot, snapshot
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
            self.assertFalse(report["proposal"]["execution_enabled"])
            for route in ("/api/investment/v1/snapshot", "/api/v1/orders"):
                self.assertEqual(client.post(route, json={}, headers={"Origin": "http://127.0.0.1"}).status_code, 405)
            self.assertEqual(client.get("/api/investment/v1/snapshot", headers={"Origin": "https://foreign.invalid"}).status_code, 403)
        self.assertEqual(list(self.path.parent.iterdir()), [])

    def test_recorded_source_never_falls_back_to_demo_or_creates_missing_store(self):
        with self.client(investment_database=self.path) as client:
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

    def test_recorded_sell_proposal_is_read_only_and_schema_valid(self):
        spec = demo_profile(self.now)
        spec["policy"]["targets"] = {"equity": "60", "bond": "20", "cash": "20"}
        spec["products"].append(dict(spec["products"][0], id="bond", sleeve="bond"))
        initialize(self.path, spec, self.now)
        import_events(self.path, demo_events(self.now), self.now)
        original = self.path.read_bytes()
        with self.client(investment_database=self.path) as client:
            response = client.get("/api/investment/v1/snapshot")
            self.assertEqual(response.status_code, 200, response.text)
            proposal = response.json()["proposal"]
            self.assertEqual([(order["side"], order["quantity"])
                              for order in proposal["orders"]], [("sell", "20")])
            self.assertFalse(proposal["execution_enabled"])
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
        report = copy.deepcopy(demo_snapshot(self.now))
        report["equity_gbp"] = "NaN"
        with patch("investment.service.demo_snapshot", return_value=report), self.client() as client:
            response = client.get("/api/investment/v1/snapshot")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"], "investment_invalid")

    def test_explicit_sources_cannot_be_combined(self):
        with self.assertRaises(ValueError):
            create_app(serve_frontend=False, database=self.path, investment_database=self.path)


if __name__ == "__main__":
    unittest.main()
