"""Temporary-storage tests for explicit eToro connection and refresh consent."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock

from dashboard.etoro_connection import EToroConnection
from investment import InvestmentError, etoro

from test_investment_etoro_portfolio import aggregate, encoded


NOW = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)
KEYS = {"x-api-key": "synthetic-public", "x-user-key": "synthetic-user"}


def balance():
    return encoded({"balances": [{
        "accountId": "12345", "accountType": "Trading",
        "balance": 105, "currency": "USD", "displayBalance": 82.25,
        "displayCurrency": "GBP", "exchangeRate": 0.78,
    }]})


class FixtureTransport:
    def __init__(self, complete=None):
        self.payloads = list(complete or [encoded(aggregate()), balance()])
        self.paths = []

    def __call__(self, path, headers):
        self.paths.append(path)
        if not etoro._headers_valid(headers):
            raise AssertionError("invalid synthetic request headers")
        return self.payloads.pop(0)


class EToroConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / ".etoro" / "connection.json"
        self.managers = []

    def manager(self, transport, now=NOW, accessor=None):
        manager = EToroConnection(
            self.path, lambda: now, transport_factory=lambda: transport,
            credential_accessor=accessor or (lambda: KEYS))
        self.managers.append(manager)
        return manager

    def tearDown(self):
        for manager in self.managers:
            manager.stop()

    def consent(self, read_scope=True):
        return {
            "environment": "real",
            "read_scope_verified": read_scope,
            "trade_capable_key_risk_accepted": not read_scope,
            "eligibility_verified": True,
            "retention_rights_verified": True,
        }

    def connect(self, manager, consent=None, request_id="synthetic-request"):
        return manager.begin("synthetic-session", request_id,
                             self.consent() if consent is None else consent, NOW)

    def test_reads_need_explicit_request_and_status_never_loads_credentials(self):
        accessor = Mock(side_effect=AssertionError("credentials must stay untouched"))
        manager = self.manager(FixtureTransport(), accessor=accessor)
        self.assertEqual(manager.status(NOW)["state"], "not_connected")
        manager.start_once()
        self.assertFalse(self.path.parent.exists())
        accessor.assert_not_called()

    def test_discard_and_expiry_release_preview_memory_and_capacity(self):
        class RepeatingTransport:
            def __init__(self):
                self.paths = []

            def __call__(self, path, headers):
                self.paths.append(path)
                return encoded(aggregate()) if "aggregate-portfolio" in path else balance()

        current = [NOW]
        transport = RepeatingTransport()
        manager = EToroConnection(
            self.path, lambda: current[0], transport_factory=lambda: transport,
            credential_accessor=lambda: KEYS)
        self.managers.append(manager)
        consent = self.consent()
        for index in range(32):
            preview = manager.begin("synthetic-session", f"request-{index}", consent, NOW)
            self.assertTrue(manager.discard("synthetic-session", preview["preview_id"]))
        self.assertEqual(manager.pending, {})

        for index in range(32):
            manager.begin("synthetic-session", f"pending-{index}", consent, NOW)
        with self.assertRaisesRegex(InvestmentError, "Too many pending"):
            manager.begin("synthetic-session", "overflow", consent, NOW)

        current[0] = NOW + timedelta(minutes=6)
        manager.begin("synthetic-session", "after-expiry", consent, current[0])
        self.assertEqual(set(manager.pending), {"after-expiry"})

    def test_preview_is_process_local_and_confirmation_saves_only_account_consent(self):
        transport = FixtureTransport()
        manager = self.manager(transport)
        preview = self.connect(manager)
        self.assertTrue(preview["snapshot"]["direct_positions"])
        self.assertFalse(self.path.exists())
        self.assertEqual(manager.status(NOW)["state"], "not_connected")
        saved = manager.confirm(
            "synthetic-session", preview["preview_id"], True, True, 30, NOW)
        self.assertEqual(saved["state"], "startup_refresh_enabled")
        self.assertEqual(saved["snapshot"]["account_id"], 12345)
        stored = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(stored["account_id"], 12345)
        self.assertEqual(stored["revision"], 1)
        self.assertNotIn("account_totals", json.dumps(stored))
        self.assertNotIn("direct_positions", json.dumps(stored))
        self.assertNotIn("synthetic-public", json.dumps(stored))
        self.assertEqual(len(transport.paths), 2)

    def test_broader_key_acceptance_allows_one_read_but_not_startup_permission(self):
        manager = self.manager(FixtureTransport())
        preview = self.connect(manager, self.consent(read_scope=False))
        with self.assertRaisesRegex(InvestmentError, "verified Read-only key scope"):
            manager.confirm("synthetic-session", preview["preview_id"], True, True, 1, NOW)
        state = manager.confirm("synthetic-session", preview["preview_id"], True, False, None, NOW)
        self.assertEqual(state["state"], "connected")
        self.assertIsNone(state["connection"]["expires_at"])
        manager.start_once()
        self.assertEqual(state["snapshot"]["account_id"], 12345)

    def test_revoke_is_immediate_and_expiry_is_bounded(self):
        manager = self.manager(FixtureTransport())
        preview = self.connect(manager)
        with self.assertRaisesRegex(InvestmentError, "1 to 30 days"):
            manager.confirm("synthetic-session", preview["preview_id"], True, True, 31, NOW)
        manager.confirm("synthetic-session", preview["preview_id"], True, True, 7, NOW)
        revoked = manager.revoke(NOW + timedelta(minutes=1))
        self.assertEqual(revoked["state"], "revoked")
        self.assertIsNotNone(json.loads(self.path.read_text(encoding="utf-8"))["revoked_at"])

    def test_startup_refresh_is_one_read_set_and_binds_expected_account(self):
        initial = FixtureTransport()
        manager = self.manager(initial)
        preview = self.connect(manager)
        manager.confirm("synthetic-session", preview["preview_id"], True, True, 30, NOW)

        completion = threading.Event()
        refreshed = FixtureTransport()

        class SignallingTransport(FixtureTransport):
            def __call__(self, path, headers):
                result = super().__call__(path, headers)
                if len(self.paths) == 2:
                    completion.set()
                return result

        refreshed = SignallingTransport()
        restarted = self.manager(refreshed)
        restarted.start_once()
        self.assertTrue(completion.wait(2), "startup refresh did not complete")
        restarted._worker.join(timeout=2)
        self.assertFalse(restarted._worker.is_alive(), "startup refresh did not finish")
        self.assertEqual(restarted.status(NOW)["last_status"], "success")
        self.assertEqual(len(refreshed.paths), 2)

    def test_startup_refresh_does_not_publish_a_different_account(self):
        manager = self.manager(FixtureTransport())
        preview = self.connect(manager)
        manager.confirm("synthetic-session", preview["preview_id"], True, True, 30, NOW)

        changed = aggregate()
        changed["cid"] = 54321
        refresh_transport = FixtureTransport([
            encoded(changed),
            encoded({"balances": [{
                "accountId": "54321", "accountType": "Trading", "balance": 0,
                "currency": "USD", "displayBalance": 0, "displayCurrency": "GBP",
                "exchangeRate": 0,
            }]})
        ])
        restarted = self.manager(refresh_transport)
        result = restarted.refresh_explicitly(NOW)
        self.assertEqual(result["state"], "refresh_failed")
        self.assertEqual(result["error_code"], "account_binding_changed")
        self.assertIsNone(restarted.snapshot)

    def test_revocation_between_collection_and_publication_prevents_new_snapshot(self):
        manager = self.manager(FixtureTransport())
        preview = self.connect(manager)
        manager.confirm("synthetic-session", preview["preview_id"], True, True, 30, NOW)
        original = manager._read_permission
        read_complete = threading.Event()
        continue_read = threading.Event()
        first = [True]

        def delayed_permission_read(now):
            result = original(now)
            if first[0]:
                first[0] = False
                read_complete.set()
                if not continue_read.wait(2):
                    raise AssertionError("permission read was not released")
            return result

        manager._read_permission = delayed_permission_read
        changed = aggregate()
        changed["accountTotals"]["accountTotalValue"] = 999
        transport = FixtureTransport([encoded(changed), balance()])
        manager.transport_factory = lambda: transport
        permission = json.loads(self.path.read_text(encoding="utf-8"))
        result = []
        worker = threading.Thread(target=lambda: result.append(
            manager._refresh(permission, NOW)))
        worker.start()
        self.assertTrue(read_complete.wait(2), "refresh did not reach publication check")
        manager.revoke(NOW + timedelta(minutes=1))
        continue_read.set()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive(), "refresh did not finish")
        self.assertEqual(result[0]["error_code"], "approval_interrupted")
        self.assertEqual(manager.snapshot["account_totals"]["accountTotalValue"], "120")


if __name__ == "__main__":
    unittest.main()
