"""Synthetic same-origin API checks for eToro connection activation."""

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.concurrency import run_in_threadpool
from dashboard.app import create_app
from investment import etoro
from test_dashboard_api import MemoryEventLoop
from test_investment_etoro_connection import FixtureTransport, KEYS

NOW = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)
BASE = "/api/investment/v1/etoro"


class EToroDashboardAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "connection.json"
        self.transport = FixtureTransport()
        self.credential_revision = [1]
        app = create_app(
            clock=lambda: NOW, serve_frontend=False, etoro_connection_path=self.path,
            etoro_transport_factory=lambda: self.transport,
            etoro_credential_snapshot_accessor=lambda: (self.credential_revision[0], KEYS),
            connections_path=Path(self.temp.name) / "connections.json")
        self.client = TestClient(app, base_url="http://localhost",
                                 backend_options={"loop_factory": MemoryEventLoop})
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.session = self.client.get(BASE + "/session").json()
        self.headers = {"Origin": "http://localhost", "X-CSRF-Token": self.session["csrf"]}

    def connect(self, **overrides):
        body = {"request_id": "initial", "environment": "real",
                "read_generation": self.session["read_generation"],
                "connection_revision": self.session["connection_revision"], **overrides}
        return self.client.post(BASE + "/connect", json=body, headers=self.headers)

    def test_enable_refresh_revoke_and_no_confirmation_endpoints(self):
        self.assertEqual(self.transport.paths, [])
        response = self.connect()
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["state"], "startup_refresh_enabled")
        self.assertEqual(data["snapshot"]["account_id"], 12345)
        connection = data["connection"]
        refresh = {"read_generation": data["read_generation"],
                   "revision": connection["revision"], "account_id": connection["account_id"],
                   "environment": connection["environment"]}
        self.assertEqual(self.client.post(BASE + "/confirm", json={}, headers=self.headers).status_code, 405)
        self.assertEqual(self.client.post(BASE + "/discard", json={}, headers=self.headers).status_code, 405)
        self.assertEqual(self.client.post(BASE + "/refresh", json={
            **refresh, "account_id": 99999}, headers=self.headers).status_code, 409)
        result = self.client.post(BASE + "/refresh", json=refresh, headers=self.headers)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["state"], "refreshed")
        self.assertEqual(len(self.transport.paths), 5)
        self.assertEqual(self.client.post(BASE + "/revoke", json={}, headers=self.headers).json()["state"], "revoked")
        self.assertEqual(self.client.post(BASE + "/refresh", json=refresh, headers=self.headers).status_code, 409)
        self.assertEqual(len(self.transport.paths), 5)

    def test_manual_lock_file_error_is_explicit_and_sanitized(self):
        from dashboard.etoro_connection import EToroConnection
        connected = self.connect().json()
        connection = connected["connection"]
        refresh = {"read_generation": connected["read_generation"],
                   "revision": connection["revision"], "account_id": connection["account_id"],
                   "environment": connection["environment"]}
        with patch.object(EToroConnection, "_claim_file", side_effect=OSError("private synthetic path")):
            result = self.client.post(BASE + "/refresh", json=refresh, headers=self.headers)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json(), {"state": "refresh_failed", "error_code": "startup_lock_unavailable"})
        self.assertNotIn("private synthetic path", result.text)
        self.assertEqual(self.client.get(BASE + "/session").json()["last_status"], "startup_lock_unavailable")
        self.assertEqual(len(self.transport.paths), 2)

    def test_startup_lock_file_error_reaches_status_without_provider_reads(self):
        from dashboard.etoro_connection import EToroConnection
        self.assertEqual(self.connect().status_code, 200)
        done = threading.Event()
        original_claim = EToroConnection._claim_file
        original_startup = EToroConnection._refresh_startup
        def claim(manager, name, blocking=False):
            if name == "startup.lock":
                raise OSError("private synthetic path")
            return original_claim(manager, name, blocking=blocking)
        def startup(manager, expected, now):
            try:
                return original_startup(manager, expected, now)
            finally:
                done.set()
        app = create_app(
            clock=lambda: NOW, serve_frontend=False, etoro_connection_path=self.path,
            etoro_transport_factory=lambda: self.transport,
            etoro_credential_snapshot_accessor=lambda: (self.credential_revision[0], KEYS),
            connections_path=Path(self.temp.name) / "connections.json")
        with patch.object(EToroConnection, "_claim_file", claim), \
                patch.object(EToroConnection, "_refresh_startup", startup), \
                TestClient(app, base_url="http://localhost",
                           backend_options={"loop_factory": MemoryEventLoop}) as restarted:
            self.assertTrue(done.wait(5))
            for endpoint in ("/session", "/portfolio"):
                response = restarted.get(BASE + endpoint)
                self.assertEqual(response.status_code, 200, response.text)
                status = response.json()
                self.assertEqual(status["last_status"], "startup_lock_unavailable")
                self.assertFalse(status["startup_pending"])
                self.assertNotIn("private synthetic path", response.text)
        self.assertEqual(len(self.transport.paths), 2)

    def test_origin_csrf_and_payload_guard_before_reads(self):
        payload = {"request_id": "initial", "environment": "real", "read_generation": 0,
                   "connection_revision": None}
        self.assertEqual(self.client.post(BASE + "/connect", json=payload).status_code, 403)
        self.assertEqual(self.client.post(BASE + "/connect", json=payload,
                                         headers={"Origin": "http://evil.test",
                                                  "X-CSRF-Token": self.session["csrf"]}).status_code, 403)
        self.assertEqual(self.client.post(BASE + "/connect", json=payload,
                                         headers={"Origin": "http://localhost",
                                                  "X-CSRF-Token": "bad"}).status_code, 403)
        self.assertEqual(self.connect(read_scope_verified=True).status_code, 400)
        self.assertEqual(self.transport.paths, [])
        self.assertFalse(self.path.exists())

    def test_queued_connect_is_interrupted_by_disable(self):
        queued, release = threading.Event(), threading.Event()
        results = []
        async def delayed(func, *args, **kwargs):
            queued.set()
            if not await run_in_threadpool(release.wait, 5):
                raise AssertionError("worker not released")
            return await run_in_threadpool(func, *args, **kwargs)
        def connect():
            results.append(self.connect())
        with patch("dashboard.app.run_in_threadpool", delayed):
            thread = threading.Thread(target=connect)
            thread.start()
            try:
                self.assertTrue(queued.wait(2))
                self.assertEqual(self.client.post(BASE + "/revoke", json={},
                                                  headers=self.headers).status_code, 200)
            finally:
                release.set()
                thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results[0].status_code, 409)
        self.assertEqual(results[0].json()["error_code"], "approval_interrupted")
        self.assertEqual(self.transport.paths, [])

    def test_connect_retry_does_not_repeat_reads(self):
        first = self.connect()
        second = self.connect()
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(len(self.transport.paths), 2)

    def test_retry_after_rotation_reports_commit_and_disable_remains_effective(self):
        committed = self.connect()
        self.assertEqual(committed.status_code, 200)
        self.credential_revision[0] += 1
        status = self.client.get(BASE + "/session").json()
        self.assertEqual(status["state"], "reauthorization_required")
        self.assertIsNone(status["connection"]["revoked_at"])
        retry = self.connect()
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.json()["state"], "reauthorization_required")
        self.assertEqual(retry.json()["activation_receipt"], committed.json()["activation_receipt"])
        self.assertEqual(len(self.transport.paths), 2)
        disabled = self.client.post(BASE + "/revoke", json={}, headers=self.headers)
        self.assertEqual(disabled.json()["state"], "revoked")
        retry = self.connect()
        self.assertEqual(retry.json()["state"], "revoked")
        self.assertEqual(retry.json()["activation_receipt"], committed.json()["activation_receipt"])
        self.credential_revision[0] -= 1
        self.assertEqual(self.client.get(BASE + "/session").json()["state"], "revoked")
        self.assertEqual(len(self.transport.paths), 2)

    def test_other_instance_activation_has_no_local_startup_pending(self):
        from dashboard.etoro_connection import EToroConnection
        other = EToroConnection(
            self.path, lambda: NOW, transport_factory=lambda: self.transport,
            credential_snapshot_accessor=lambda: (self.credential_revision[0], KEYS))
        other.begin("other", "enable", {"environment": "real"}, NOW)
        session = self.client.get(BASE + "/session").json()
        portfolio = self.client.get(BASE + "/portfolio").json()
        self.assertEqual(session["state"], "startup_refresh_enabled")
        self.assertEqual(session["last_status"], "not_collected")
        self.assertFalse(session["startup_pending"])
        self.assertFalse(portfolio["startup_pending"])
        connection = session["connection"]
        refresh = {"read_generation": session["read_generation"],
                   "revision": connection["revision"], "account_id": connection["account_id"],
                   "environment": connection["environment"]}
        self.assertEqual(self.client.post(BASE + "/refresh", json=refresh, headers=self.headers).json()["state"],
                         "refreshed")

    def test_committed_retry_and_receipt_survive_corrupt_current_connection(self):
        committed = self.connect().json()
        corrupt = b'{"revision":1,"revision":2}'
        self.path.write_bytes(corrupt)
        retry = self.connect()
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.json()["state"], "unavailable")
        self.assertEqual(retry.json()["activation_receipt"], committed["activation_receipt"])
        payload = {"request_id": "initial", "environment": "real",
                   "read_generation": self.session["read_generation"],
                   "connection_revision": self.session["connection_revision"]}
        receipt = self.client.post(BASE + "/activation-receipt", json=payload, headers=self.headers)
        self.assertEqual(receipt.status_code, 200)
        self.assertEqual(receipt.json()["receipt_status"], "committed")
        self.assertEqual(receipt.json()["state"], "unavailable")
        self.assertEqual(receipt.json()["activation_receipt"], committed["activation_receipt"])
        self.assertEqual(self.path.read_bytes(), corrupt)
        self.assertEqual(len(self.transport.paths), 2)

    def test_receipt_lookup_is_non_dispatching_and_requires_same_session_and_request(self):
        payload = {"request_id": "initial", "environment": "real",
                   "read_generation": self.session["read_generation"],
                   "connection_revision": self.session["connection_revision"]}
        self.assertEqual(self.client.post(BASE + "/activation-receipt", json=payload).status_code, 403)
        unknown = self.client.post(BASE + "/activation-receipt", json=payload, headers=self.headers)
        self.assertEqual(unknown.json(), {"receipt_status": "unknown"})
        self.assertEqual(self.transport.paths, [])
        committed = self.connect().json()
        changed = {**payload, "environment": "demo"}
        self.assertEqual(self.client.post(BASE + "/activation-receipt", json=changed,
                                          headers=self.headers).status_code, 409)
        for field, value in (("read_generation", 1), ("connection_revision", 1)):
            self.assertEqual(self.client.post(BASE + "/activation-receipt",
                                              json={**payload, field: value},
                                              headers=self.headers).status_code, 409)
        self.assertEqual(self.client.post(BASE + "/activation-receipt", json=payload,
                                         headers={"Origin": "http://evil.test",
                                                  "X-CSRF-Token": self.session["csrf"]}).status_code, 403)
        self.assertEqual(self.client.post(BASE + "/activation-receipt", json=payload,
                                         headers={"Origin": "http://localhost",
                                                  "X-CSRF-Token": "bad"}).status_code, 403)
        self.credential_revision[0] += 1
        receipt = self.client.post(BASE + "/activation-receipt", json=payload, headers=self.headers)
        self.assertEqual(receipt.json()["state"], "reauthorization_required")
        self.assertEqual(receipt.json()["activation_receipt"], committed["activation_receipt"])
        self.assertEqual(len(self.transport.paths), 2)

    def test_receipt_survives_permission_metadata_error_without_repair(self):
        committed = self.connect().json()
        original = self.path.read_bytes()
        payload = {"request_id": "initial", "environment": "real",
                   "read_generation": self.session["read_generation"],
                   "connection_revision": self.session["connection_revision"]}
        with patch("dashboard.etoro_connection.path_identity", side_effect=PermissionError("synthetic")):
            receipt = self.client.post(BASE + "/activation-receipt", json=payload, headers=self.headers)
        self.assertEqual(receipt.status_code, 200, receipt.text)
        self.assertEqual(receipt.json()["state"], "unavailable")
        self.assertEqual(receipt.json()["activation_receipt"], committed["activation_receipt"])
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(len(self.transport.paths), 2)

    def test_receipt_is_not_available_to_another_valid_session(self):
        self.assertEqual(self.connect().status_code, 200)
        payload = {"request_id": "initial", "environment": "real",
                   "read_generation": self.session["read_generation"],
                   "connection_revision": self.session["connection_revision"]}
        with TestClient(self.client.app, base_url="http://localhost",
                        backend_options={"loop_factory": MemoryEventLoop}) as other:
            session = other.get(BASE + "/session").json()
            response = other.post(BASE + "/activation-receipt", json=payload,
                                  headers={"Origin": "http://localhost",
                                           "X-CSRF-Token": session["csrf"]})
            self.assertEqual(response.status_code, 409)
        self.assertEqual(len(self.transport.paths), 2)

    def test_waiting_receipt_does_not_block_disable_on_event_loop(self):
        entered, release, disabled = threading.Event(), threading.Event(), threading.Event()
        results = []
        from dashboard.etoro_connection import EToroConnection
        original = EToroConnection.activation_receipt
        def delayed(manager, *args):
            entered.set()
            if not release.wait(5):
                raise AssertionError("receipt worker not released")
            return original(manager, *args)
        payload = {"request_id": "initial", "environment": "real",
                   "read_generation": self.session["read_generation"],
                   "connection_revision": self.session["connection_revision"]}
        def lookup():
            results.append(self.client.post(BASE + "/activation-receipt", json=payload, headers=self.headers))
        def disable():
            results.append(self.client.post(BASE + "/revoke", json={}, headers=self.headers))
            disabled.set()
        with patch.object(EToroConnection, "activation_receipt", delayed):
            lookup_thread = threading.Thread(target=lookup)
            lookup_thread.start()
            disable_thread = None
            try:
                self.assertTrue(entered.wait(2))
                disable_thread = threading.Thread(target=disable)
                disable_thread.start()
                self.assertTrue(disabled.wait(2), "receipt blocked the event loop")
            finally:
                release.set()
                lookup_thread.join(5)
                if disable_thread is not None:
                    disable_thread.join(5)
        self.assertEqual(sorted(result.status_code for result in results), [200, 200])
        self.assertEqual(self.transport.paths, [])

    def test_uncertain_activation_is_visible_to_a_new_status_reader_and_disable(self):
        fixture = self.transport
        def interrupted(path, headers):
            if "/balances/" in path:
                raise etoro.EToroError("cancellation_unconfirmed")
            return fixture(path, headers)
        self.transport = interrupted
        response = self.connect()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error_code"], "connection_read_outcome_uncertain")
        session = self.client.get(BASE + "/session").json()
        portfolio = self.client.get(BASE + "/portfolio").json()
        self.assertEqual(session["last_status"], "connection_read_outcome_uncertain")
        self.assertEqual(portfolio["last_status"], "connection_read_outcome_uncertain")
        self.assertEqual(session["state"], "not_connected")
        self.assertEqual(self.client.post(BASE + "/revoke", json={}, headers=self.headers).status_code, 200)
        self.assertEqual(len(fixture.paths), 1)

    def test_legacy_disable_returns_revoked_and_never_activates(self):
        legacy = {"schema_version": 2, "revision": 4, "environment": "real",
                  "account_id": 12345, "approved_at": "2026-01-01T00:00:00Z",
                  "expires_at": "2026-01-02T00:00:00Z",
                  "retention_rights_verified": True, "read_scope_verified": True,
                  "trade_capable_key_risk_accepted": False, "eligibility_verified": True,
                  "account_binding_verified": True, "revoked_at": None,
                  "credential_revision": 1}
        self.path.write_text(json.dumps(legacy), encoding="utf-8")
        self.assertEqual(self.client.get(BASE + "/session").json()["state"], "reauthorization_required")
        response = self.client.post(BASE + "/revoke", json={}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["state"], "revoked")
        self.assertEqual(self.client.get(BASE + "/portfolio").json()["state"], "revoked")
        self.assertEqual(self.transport.paths, [])

    def test_other_instance_activation_hides_old_same_account_from_both_status_endpoints(self):
        from dashboard.etoro_connection import EToroConnection
        self.assertEqual(self.connect().status_code, 200)
        other = EToroConnection(
            self.path, lambda: NOW, transport_factory=lambda: self.transport,
            credential_snapshot_accessor=lambda: (self.credential_revision[0], KEYS))
        other.begin("other", "other-request", {"environment": "real"}, NOW)
        session = self.client.get(BASE + "/session").json()
        portfolio = self.client.get(BASE + "/portfolio").json()
        self.assertEqual(session["state"], "startup_refresh_enabled")
        self.assertEqual(session["connection"]["revision"], 2)
        self.assertFalse(session["snapshot_available"])
        self.assertEqual(session["last_status"], "connection_changed")
        self.assertIsNone(portfolio["snapshot"])
        self.assertEqual(portfolio["last_status"], "connection_changed")

    def test_unconfirmed_refresh_cancellation_is_reported_as_uncertain(self):
        connected = self.connect().json()
        connection = connected["connection"]
        fixture = self.transport
        def interrupted(path, headers):
            if "/balances/" in path:
                raise etoro.EToroError("cancellation_unconfirmed")
            return fixture(path, headers)
        self.transport = interrupted
        payload = {"read_generation": connected["read_generation"],
                   "revision": connection["revision"], "account_id": connection["account_id"],
                   "environment": connection["environment"]}
        result = self.client.post(BASE + "/refresh", json=payload, headers=self.headers)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["error_code"], "connection_read_outcome_uncertain")
        session = self.client.get(BASE + "/session").json()
        self.assertEqual(session["last_status"], "connection_read_outcome_uncertain")
        self.assertEqual(session["state"], "startup_refresh_enabled")

    def test_stale_cross_instance_connect_cannot_undo_disable(self):
        from dashboard.etoro_connection import EToroConnection
        other = EToroConnection(self.path, lambda: NOW,
                                credential_snapshot_accessor=lambda: (self.credential_revision[0], KEYS))
        other.revoke(NOW)
        result = self.connect()
        self.assertEqual(result.status_code, 409)
        self.assertEqual(self.transport.paths, [])
        status = self.client.get(BASE + "/session").json()
        self.assertEqual(status["state"], "not_connected")
        self.assertEqual(status["connection_revision"], 1)
        self.assertIsNone(status["connection"])

    def test_injected_credentials_require_their_own_revision_binding(self):
        with self.assertRaises(ValueError):
            create_app(serve_frontend=False, etoro_connection_path=self.path,
                       etoro_credential_accessor=lambda: KEYS)
        connected = self.connect().json()
        self.credential_revision[0] += 1
        self.assertEqual(self.client.get(BASE + "/session").json()["state"],
                         "reauthorization_required")
        connection = connected["connection"]
        refresh = {"read_generation": connected["read_generation"],
                   "revision": connection["revision"], "account_id": connection["account_id"],
                   "environment": connection["environment"]}
        self.assertEqual(self.client.post(BASE + "/refresh", json=refresh,
                                          headers=self.headers).status_code, 409)
        self.assertEqual(len(self.transport.paths), 2)
