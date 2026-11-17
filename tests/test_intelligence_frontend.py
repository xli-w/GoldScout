from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from dashboard.app import create_app
from fastapi.testclient import TestClient
from test_dashboard_api import MemoryEventLoop
from market_intelligence.store import initialize


class IntelligenceAPITests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "intelligence.sqlite3"
        initialize(self.path, datetime.now(timezone.utc))

    def client(self, demo=False, controls=True):
        return TestClient(create_app(
            serve_frontend=False, intelligence_demo=demo,
            intelligence_database=None if demo else self.path,
            analysis_controls_enabled=controls, analysis_profiles=("local",)),
            base_url="http://127.0.0.1", backend_options={"loop_factory": MemoryEventLoop})

    def test_analysis_controls_remain_disabled_without_explicit_opt_in(self):
        with self.client(controls=False) as client:
            session = client.get("/api/v1/intelligence/session").json()
            self.assertFalse(session["enabled"])
            self.assertIsNone(session["csrf"])
            response = client.post("/api/v1/intelligence/controls", json={},
                                   headers={"Origin": "http://127.0.0.1"})
            self.assertEqual(response.status_code, 405)

    def test_opt_in_origin_csrf_and_portfolio_routes_are_isolated(self):
        with self.client() as client:
            session = client.get("/api/v1/intelligence/session").json()
            snapshot = client.get("/api/v1/intelligence").json()
            self.assertNotIn("bot_observed_revision", snapshot)
            settings = {key: value for key, value in snapshot["settings"].items() if key != "revision"}
            payload = {"request_id": "request-1", "database_id": snapshot["database_id"],
                       "base_revision": 0, "settings": settings}
            headers = {"Origin": "http://127.0.0.1", "X-CSRF-Token": session["csrf"]}
            self.assertEqual(client.post("/api/v1/intelligence/controls", json=payload).status_code, 403)
            self.assertEqual(client.post("/api/v1/intelligence/controls", json=payload,
                                         headers={**headers, "Origin": "https://attacker.invalid"}).status_code, 403)
            self.assertEqual(client.post("/api/v1/intelligence/controls", json=payload,
                                         headers={"Origin": headers["Origin"]}).status_code, 403)
            response = client.post("/api/v1/intelligence/controls", json=payload, headers=headers)
            self.assertEqual(response.status_code, 202)
            self.assertEqual(response.json()["status"], "pending")
            self.assertEqual(client.post("/api/investment/v1/recording/confirm", json={}, headers=headers).status_code, 405)
            payload["settings"]["risk_per_trade_pct"] = 1
            self.assertEqual(client.post("/api/v1/intelligence/controls", json=payload, headers=headers).status_code, 409)

    def test_demo_controls_apply_only_to_temporary_synthetic_workspace(self):
        with self.client(demo=True) as client:
            session = client.get("/api/v1/intelligence/session").json()
            snapshot = client.get("/api/v1/intelligence").json()
            self.assertEqual(snapshot["source_mode"], "demo")
            settings = {key: value for key, value in snapshot["settings"].items() if key != "revision"}
            settings.update(enabled=True, ai_enabled=True, ai_profile="demo")
            payload = {"request_id": "demo-command", "database_id": snapshot["database_id"],
                       "base_revision": snapshot["settings"]["revision"], "settings": settings}
            response = client.post("/api/v1/intelligence/controls", json=payload,
                                   headers={"Origin": "http://127.0.0.1", "X-CSRF-Token": session["csrf"]})
            self.assertEqual(response.status_code, 202)
            snapshot = client.get("/api/v1/intelligence").json()
            self.assertEqual(len(snapshot["briefs"]), 1)
            self.assertEqual(snapshot["budget"]["calls"], 1)

    def test_ai_capability_is_not_exposed_for_pre_v4_store(self):
        older = Path(self.directory.name) / "version-three.sqlite3"
        initialize(older, datetime.now(timezone.utc), schema_version=3)
        with TestClient(create_app(serve_frontend=False, intelligence_database=older,
                                   analysis_controls_enabled=True),
                        base_url="http://127.0.0.1",
                        backend_options={"loop_factory": MemoryEventLoop}) as client:
            snapshot = client.get("/api/v1/intelligence").json()
            self.assertFalse(snapshot["ai_capable"])
            self.assertTrue(snapshot["news_capable"])
