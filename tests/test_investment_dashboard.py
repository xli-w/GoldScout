import copy
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from dashboard.app import create_app
from fastapi.testclient import TestClient
from investment.service import demo_events, demo_profile, demo_snapshot, snapshot
from investment.store import acknowledge_review, import_events, initialize
from test_dashboard_api import MemoryEventLoop


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
