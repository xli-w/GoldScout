"""Synthetic local API checks for the eToro startup connection flow."""

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.concurrency import run_in_threadpool

from dashboard.app import create_app
from investment import InvestmentError
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
        self.payloads = [portfolio, balance, portfolio, balance, encoded([])]
        self.paths = []

    def __call__(self, path, headers):
        self.paths.append(path)
        return self.payloads.pop(0)


class EToroDashboardAPITests(unittest.TestCase):
    def test_clock_failure_after_provider_reads_is_not_a_predispatch_rejection(self):
        fail_clock = [False]
        transport = FixtureTransport()
        def clock():
            if fail_clock[0]:
                fail_clock[0] = False
                raise InvestmentError("synthetic clock failure")
            return NOW
        def collecting(path, headers):
            payload = transport(path, headers)
            if len(transport.paths) == 4:
                fail_clock[0] = True
            return payload

        with tempfile.TemporaryDirectory() as directory:
            app = create_app(
                clock=clock, serve_frontend=False,
                etoro_connection_path=Path(directory) / "connection.json",
                etoro_transport_factory=lambda: collecting,
                etoro_credential_accessor=lambda: {
                    "x-api-key": "synthetic-public", "x-user-key": "synthetic-user",
                })
            with TestClient(app, base_url="http://localhost",
                            backend_options={"loop_factory": MemoryEventLoop}) as client:
                session = client.get("/api/investment/v1/etoro/session").json()
                headers = {"Origin": "http://localhost", "X-CSRF-Token": session["csrf"]}
                preview = client.post("/api/investment/v1/etoro/connect", json={
                    "request_id": "initial", "environment": "real",
                    "read_generation": session["read_generation"],
                    "read_scope_verified": True, "trade_capable_key_risk_accepted": False,
                    "eligibility_verified": True, "retention_rights_verified": True,
                }, headers=headers).json()
                client.post("/api/investment/v1/etoro/confirm", json={
                    "preview_id": preview["preview_id"], "account_binding_confirmed": True,
                    "startup_refresh": False, "refresh_days": None,
                }, headers=headers)
                result = client.post("/api/investment/v1/etoro/refresh", json={
                    "confirm_read": True, "read_generation": session["read_generation"],
                    "confirm_closed_trade_history": True,
                    "history_retention_rights_verified": True,
                    "revision": 1, "account_id": 12345, "environment": "real",
                }, headers=headers)
                self.assertEqual(len(transport.paths), 4)
                self.assertEqual(result.status_code, 503)
                self.assertEqual(result.json()["error"], "etoro_refresh_unavailable")
                status = client.get("/api/investment/v1/etoro/portfolio").json()
                self.assertEqual(status["last_status"], "refresh_unavailable")

    def test_manual_refresh_requires_confirmation_and_not_startup_permission(self):
        transport = FixtureTransport()
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(
                clock=lambda: NOW, serve_frontend=False,
                etoro_connection_path=Path(directory) / "connection.json",
                etoro_transport_factory=lambda: transport,
                etoro_credential_accessor=lambda: {
                    "x-api-key": "synthetic-public", "x-user-key": "synthetic-user",
                })
            with TestClient(app, base_url="http://localhost",
                            backend_options={"loop_factory": MemoryEventLoop}) as client:
                session = client.get("/api/investment/v1/etoro/session").json()
                headers = {"Origin": "http://localhost", "X-CSRF-Token": session["csrf"]}
                preview = client.post("/api/investment/v1/etoro/connect", json={
                    "request_id": "initial", "environment": "real",
                    "read_generation": session["read_generation"],
                    "read_scope_verified": False, "trade_capable_key_risk_accepted": True,
                    "eligibility_verified": True, "retention_rights_verified": True,
                }, headers=headers).json()
                confirmed = client.post("/api/investment/v1/etoro/confirm", json={
                    "preview_id": preview["preview_id"], "account_binding_confirmed": True,
                    "startup_refresh": False, "refresh_days": None,
                }, headers=headers)
                self.assertEqual(confirmed.json()["state"], "connected")
                manual = {"confirm_read": True, "read_generation": session["read_generation"],
                          "confirm_closed_trade_history": True,
                          "history_retention_rights_verified": True,
                          "revision": 1, "account_id": 12345, "environment": "real"}
                path = "/api/investment/v1/etoro/refresh"
                self.assertEqual(client.post(path, json={}, headers=headers).status_code, 400)
                self.assertEqual(client.post(path, json={
                    **manual, "confirm_read": False,
                }, headers=headers).status_code, 400)
                self.assertEqual(client.post(path, json={
                    **manual, "confirm_closed_trade_history": False,
                }, headers=headers).status_code, 400)
                self.assertEqual(client.post(path, json={
                    **manual, "account_id": 54321,
                }, headers=headers).status_code, 409)
                self.assertEqual(client.post(path, json={
                    **manual, "revision": 2,
                }, headers=headers).status_code, 409)
                self.assertEqual(len(transport.paths), 2)
                result = client.post(path, json=manual, headers=headers)
                self.assertEqual(result.status_code, 200, result.text)
                self.assertEqual(result.json()["state"], "refreshed")
                self.assertEqual(len(transport.paths), 5)
                client.post("/api/investment/v1/etoro/revoke", json={}, headers=headers)
                result = client.post(path, json=manual, headers=headers)
                self.assertEqual(result.status_code, 409)
                self.assertEqual(len(transport.paths), 5)

    def test_queued_manual_refresh_is_invalidated_by_revocation(self):
        queued, release = threading.Event(), threading.Event()
        transport = FixtureTransport()
        results = {}
        async def delayed_dispatch(func, *args, **kwargs):
            queued.set()
            if not await run_in_threadpool(release.wait, 5):
                raise AssertionError("synthetic worker was not released")
            return await run_in_threadpool(func, *args, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            app = create_app(
                clock=lambda: NOW, serve_frontend=False,
                etoro_connection_path=Path(directory) / "connection.json",
                etoro_transport_factory=lambda: transport,
                etoro_credential_accessor=lambda: {
                    "x-api-key": "synthetic-public", "x-user-key": "synthetic-user",
                })
            with TestClient(app, base_url="http://localhost",
                            backend_options={"loop_factory": MemoryEventLoop}) as client:
                session = client.get("/api/investment/v1/etoro/session").json()
                headers = {"Origin": "http://localhost", "X-CSRF-Token": session["csrf"]}
                connect = {"request_id": "initial", "environment": "real",
                           "read_generation": session["read_generation"],
                           "read_scope_verified": True, "trade_capable_key_risk_accepted": False,
                           "eligibility_verified": True, "retention_rights_verified": True}
                preview = client.post("/api/investment/v1/etoro/connect", json=connect, headers=headers).json()
                client.post("/api/investment/v1/etoro/confirm", json={
                    "preview_id": preview["preview_id"], "account_binding_confirmed": True,
                    "startup_refresh": False, "refresh_days": None,
                }, headers=headers)
                manual = {"confirm_read": True, "read_generation": session["read_generation"],
                          "confirm_closed_trade_history": True,
                          "history_retention_rights_verified": True,
                          "revision": 1, "account_id": 12345, "environment": "real"}

                def refresh():
                    results["refresh"] = client.post(
                        "/api/investment/v1/etoro/refresh", json=manual, headers=headers)
                with patch("dashboard.app.run_in_threadpool", delayed_dispatch):
                    worker = threading.Thread(target=refresh)
                    worker.start()
                    try:
                        self.assertTrue(queued.wait(2))
                        client.post("/api/investment/v1/etoro/revoke", json={}, headers=headers)
                    finally:
                        release.set()
                        worker.join(5)
                self.assertFalse(worker.is_alive())
                self.assertEqual(results["refresh"].status_code, 409)
                self.assertEqual(len(transport.paths), 2)

    def test_revocation_invalidates_connection_before_worker_dispatch(self):
        queued = threading.Event()
        release = threading.Event()
        paths = []
        results = {}

        async def delayed_dispatch(func, *args, **kwargs):
            queued.set()
            if not await run_in_threadpool(release.wait, 5):
                raise AssertionError("synthetic worker was not released")
            return await run_in_threadpool(func, *args, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            app = create_app(
                clock=lambda: NOW, serve_frontend=False,
                etoro_connection_path=Path(directory) / ".etoro" / "connection.json",
                etoro_transport_factory=lambda: lambda path, headers: paths.append(path),
                etoro_credential_accessor=lambda: {
                    "x-api-key": "synthetic-public", "x-user-key": "synthetic-user",
                })
            with patch("dashboard.app.run_in_threadpool", delayed_dispatch), TestClient(
                    app, base_url="http://localhost",
                    backend_options={"loop_factory": MemoryEventLoop}) as client:
                session = client.get("/api/investment/v1/etoro/session").json()
                csrf = session["csrf"]
                headers = {"Origin": "http://localhost", "X-CSRF-Token": csrf}

                def connect():
                    results["connect"] = client.post("/api/investment/v1/etoro/connect", json={
                        "request_id": "queued", "environment": "real",
                        "read_generation": session["read_generation"],
                        "read_scope_verified": True, "trade_capable_key_risk_accepted": False,
                        "eligibility_verified": True, "retention_rights_verified": True,
                    }, headers=headers)

                worker = threading.Thread(target=connect)
                worker.start()
                try:
                    self.assertTrue(queued.wait(2), "connection did not reach worker dispatch")
                    response = client.post("/api/investment/v1/etoro/revoke", json={}, headers=headers)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()["state"], "not_connected")
                finally:
                    release.set()
                    worker.join(5)
                self.assertFalse(worker.is_alive())
                self.assertEqual(results["connect"].status_code, 409)
                self.assertEqual(results["connect"].json()["error_code"], "approval_interrupted")
                self.assertEqual(paths, [])
                late = client.post("/api/investment/v1/etoro/connect", json={
                    "request_id": "late", "environment": "real",
                    "read_generation": session["read_generation"],
                    "read_scope_verified": True, "trade_capable_key_risk_accepted": False,
                    "eligibility_verified": True, "retention_rights_verified": True,
                }, headers=headers)
                self.assertEqual(late.status_code, 409)
                self.assertEqual(late.json()["error_code"], "approval_interrupted")
                self.assertEqual(paths, [])

    def test_revocation_remains_available_during_manual_refresh(self):
        started = threading.Event()
        release = threading.Event()
        revoked = threading.Event()
        pause = threading.Event()
        paths = []
        results = {}

        class ControlledTransport:
            def __init__(self, *, before_request=None):
                self.before_request = before_request

            def __call__(self, path, headers):
                if self.before_request is not None:
                    self.before_request()
                paths.append(path)
                if pause.is_set() and "aggregate-portfolio" in path:
                    started.set()
                    if not release.wait(5):
                        raise AssertionError("synthetic read was not released")
                if "aggregate-portfolio" in path:
                    return encoded(aggregate())
                return encoded({"balances": [{
                    "accountId": "12345", "accountType": "Trading",
                    "balance": 105, "currency": "USD", "displayBalance": 82.25,
                    "displayCurrency": "GBP", "exchangeRate": 0.78,
                }]})

        with tempfile.TemporaryDirectory() as directory:
            app = create_app(
                clock=lambda: NOW, serve_frontend=False,
                etoro_connection_path=Path(directory) / ".etoro" / "connection.json",
                etoro_credential_accessor=lambda: {
                    "x-api-key": "synthetic-public", "x-user-key": "synthetic-user",
                })
            with patch("investment.etoro.EToroTransport", ControlledTransport), TestClient(
                    app, base_url="http://localhost",
                    backend_options={"loop_factory": MemoryEventLoop}) as client:
                session = client.get("/api/investment/v1/etoro/session").json()
                csrf = session["csrf"]
                headers = {"Origin": "http://localhost", "X-CSRF-Token": csrf}
                preview = client.post("/api/investment/v1/etoro/connect", json={
                    "request_id": "initial", "environment": "real",
                    "read_generation": session["read_generation"],
                    "read_scope_verified": True, "trade_capable_key_risk_accepted": False,
                    "eligibility_verified": True, "retention_rights_verified": True,
                }, headers=headers).json()
                confirmed = client.post("/api/investment/v1/etoro/confirm", json={
                    "preview_id": preview["preview_id"], "account_binding_confirmed": True,
                    "startup_refresh": True, "refresh_days": 30,
                }, headers=headers)
                self.assertEqual(confirmed.status_code, 200, confirmed.text)
                original_snapshot = client.get("/api/investment/v1/etoro/portfolio").json()["snapshot"]
                paths.clear()
                pause.set()

                def refresh():
                    results["refresh"] = client.post(
                        "/api/investment/v1/etoro/refresh", json={
                            "confirm_read": True, "read_generation": session["read_generation"],
                            "confirm_closed_trade_history": True,
                            "history_retention_rights_verified": True,
                            "revision": 1, "account_id": 12345, "environment": "real",
                        }, headers=headers)

                def revoke():
                    try:
                        results["revoke"] = client.post(
                            "/api/investment/v1/etoro/revoke", json={}, headers=headers)
                    finally:
                        revoked.set()

                refresh_thread = threading.Thread(target=refresh)
                revoke_thread = threading.Thread(target=revoke)
                refresh_thread.start()
                try:
                    self.assertTrue(started.wait(2), "synthetic refresh did not start")
                    revoke_thread.start()
                    revoked_before_release = revoked.wait(1)
                finally:
                    release.set()
                    refresh_thread.join(5)
                    if revoke_thread.ident is not None:
                        revoke_thread.join(5)
                self.assertFalse(refresh_thread.is_alive())
                self.assertFalse(revoke_thread.is_alive())
                self.assertTrue(revoked_before_release, "revocation was blocked by the provider read")
                self.assertEqual(results["revoke"].status_code, 200)
                self.assertEqual(results["revoke"].json()["state"], "revoked")
                self.assertEqual(results["refresh"].json()["error_code"], "approval_interrupted")
                self.assertEqual(len(paths), 1, "a second provider request followed revocation")
                status = client.get("/api/investment/v1/etoro/portfolio").json()
                self.assertEqual(status["state"], "revoked")
                self.assertEqual(status["snapshot"], original_snapshot)

                pause.clear()
                release.clear()
                started.clear()
                revoked.clear()
                reconnect = {
                    "request_id": "reconnect", "environment": "real",
                    "read_generation": client.get("/api/investment/v1/etoro/session").json()["read_generation"],
                    "read_scope_verified": True, "trade_capable_key_risk_accepted": False,
                    "eligibility_verified": True, "retention_rights_verified": True,
                }
                preview = client.post("/api/investment/v1/etoro/connect", json=reconnect, headers=headers).json()
                confirmed = client.post("/api/investment/v1/etoro/confirm", json={
                    "preview_id": preview["preview_id"], "account_binding_confirmed": True,
                    "startup_refresh": True, "refresh_days": 30,
                }, headers=headers)
                self.assertEqual(confirmed.status_code, 200, confirmed.text)
                paths.clear()
                pause.set()
                reconnect["request_id"] = "interrupted"

                def connect():
                    results["connect"] = client.post(
                        "/api/investment/v1/etoro/connect", json=reconnect, headers=headers)

                connect_thread = threading.Thread(target=connect)
                revoke_thread = threading.Thread(target=revoke)
                connect_thread.start()
                try:
                    self.assertTrue(started.wait(2), "synthetic connection read did not start")
                    revoke_thread.start()
                    revoked_before_release = revoked.wait(1)
                finally:
                    release.set()
                    connect_thread.join(5)
                    if revoke_thread.ident is not None:
                        revoke_thread.join(5)
                self.assertFalse(connect_thread.is_alive())
                self.assertFalse(revoke_thread.is_alive())
                self.assertTrue(revoked_before_release, "connection read blocked revocation")
                self.assertEqual(results["revoke"].status_code, 200)
                self.assertEqual(results["connect"].status_code, 409)
                self.assertEqual(results["connect"].json()["error_code"], "approval_interrupted")
                self.assertNotIn("preview_id", results["connect"].json())
                self.assertEqual(len(paths), 1, "a second provider request followed revocation")

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
                    "read_generation": session.json()["read_generation"],
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
                for generation in (None, True, -1):
                    with self.subTest(read_generation=generation):
                        invalid = {**connect_body, "read_generation": generation}
                        self.assertEqual(client.post(connect_path, json=invalid, headers={
                            "Origin": "http://localhost", "X-CSRF-Token": csrf,
                        }).status_code, 400)
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
