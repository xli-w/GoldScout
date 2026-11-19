"""Synthetic local API checks for the eToro startup connection flow."""

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from dashboard.app import create_app
from test_dashboard_api import MemoryEventLoop
from test_investment_etoro_portfolio import aggregate, encoded


NOW = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)


class FixtureTransport:
    def __init__(self):
        portfolio = encoded(aggregate())
        balance = encoded({"balances": [{
            "accountId": "12345", "accountType": "Trading",
            "balance": 105, "currency": "USD", "displayBalance": 82.25,
            "displayCurrency": "GBP", "exchangeRate": 0.78,
        }]})
        self.payloads = [portfolio, balance, portfolio, balance]
        self.paths = []

    def __call__(self, path, headers):
        self.paths.append(path)
        return self.payloads.pop(0)


class EToroDashboardAPITests(unittest.TestCase):
    def test_connection_routes_enforce_origin_csrf_and_keep_only_consent(self):
        with tempfile.TemporaryDirectory() as directory:
            permission_path = Path(directory) / ".etoro" / "connection.json"
            transport = FixtureTransport()
            app = create_app(
                clock=lambda: NOW, serve_frontend=False,
                etoro_connection_path=permission_path,
                etoro_transport_factory=lambda: transport,
                etoro_credential_accessor=lambda: {
                    "x-api-key": "synthetic-public", "x-user-key": "synthetic-user",
                })
            with TestClient(
                    app, base_url="http://localhost",
                    backend_options={"loop_factory": MemoryEventLoop}) as client:
                session = client.get("/api/investment/v1/etoro/session")
                self.assertEqual(session.status_code, 200, session.text)
                csrf = session.json()["csrf"]
                connect_path = "/api/investment/v1/etoro/connect"
                connect_body = {
                    "request_id": "synthetic-request", "environment": "real",
                    "read_scope_verified": True,
                    "trade_capable_key_risk_accepted": False,
                    "eligibility_verified": True, "retention_rights_verified": True,
                }
                self.assertEqual(
                    client.post(connect_path, json=connect_body,
                                headers={"Origin": "http://localhost"}).status_code, 403)
                self.assertEqual(
                    client.post(connect_path, json=connect_body, headers={
                        "Origin": "http://remote.invalid", "X-CSRF-Token": csrf,
                    }).status_code, 403)
                self.assertEqual(transport.paths, [])

                connected = client.post(connect_path, json=connect_body, headers={
                    "Origin": "http://localhost", "X-CSRF-Token": csrf,
                })
                self.assertEqual(connected.status_code, 200, connected.text)
                preview = connected.json()
                self.assertEqual(preview["snapshot"]["account_id"], 12345)
                refused = {
                    "Origin": "http://localhost", "X-CSRF-Token": "invalid",
                }
                for path, mutation_body in (
                    ("/api/investment/v1/etoro/confirm", {
                        "preview_id": preview["preview_id"],
                        "account_binding_confirmed": True,
                        "startup_refresh": True, "refresh_days": 30,
                    }),
                    ("/api/investment/v1/etoro/discard", {
                        "preview_id": preview["preview_id"],
                    }),
                    ("/api/investment/v1/etoro/revoke", {}),
                    ("/api/investment/v1/etoro/refresh", {}),
                ):
                    with self.subTest(path=path):
                        self.assertEqual(
                            client.post(path, json=mutation_body, headers=refused).status_code, 403)
                self.assertFalse(permission_path.exists())
                self.assertEqual(len(transport.paths), 2)

                discarded = client.post("/api/investment/v1/etoro/discard", json={
                    "preview_id": preview["preview_id"],
                }, headers={"Origin": "http://localhost", "X-CSRF-Token": csrf})
                self.assertEqual(discarded.status_code, 200, discarded.text)
                self.assertEqual(discarded.json(), {"discarded": True})
                reconnected = client.post(connect_path, json={
                    **connect_body, "request_id": "synthetic-request-2",
                }, headers={"Origin": "http://localhost", "X-CSRF-Token": csrf})
                self.assertEqual(reconnected.status_code, 200, reconnected.text)
                preview = reconnected.json()

                confirmed = client.post("/api/investment/v1/etoro/confirm", json={
                    "preview_id": preview["preview_id"],
                    "account_binding_confirmed": True,
                    "startup_refresh": True, "refresh_days": 30,
                }, headers={"Origin": "http://localhost", "X-CSRF-Token": csrf})
                self.assertEqual(confirmed.status_code, 200, confirmed.text)
                self.assertEqual(confirmed.json()["state"], "startup_refresh_enabled")

                saved_before_refused_requests = permission_path.read_bytes()
                for path in ("/api/investment/v1/etoro/revoke",
                             "/api/investment/v1/etoro/refresh"):
                    with self.subTest(path=path):
                        self.assertEqual(
                            client.post(path, json={}, headers=refused).status_code, 403)
                self.assertEqual(permission_path.read_bytes(), saved_before_refused_requests)
                self.assertEqual(len(transport.paths), 4)

                portfolio = client.get("/api/investment/v1/etoro/portfolio")
                self.assertEqual(portfolio.status_code, 200, portfolio.text)
                self.assertEqual(portfolio.json()["snapshot"]["account_id"], 12345)
                self.assertEqual(len(transport.paths), 4)
                saved = permission_path.read_text(encoding="utf-8")
                self.assertIn('"account_id":12345', saved)
                self.assertNotIn("account_totals", saved)
                self.assertNotIn("direct_positions", saved)
                self.assertNotIn("synthetic-public", saved)


if __name__ == "__main__":
    unittest.main()
