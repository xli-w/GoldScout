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
        self.payloads = list(complete or [encoded(aggregate()), balance(), encoded([])])
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

    def test_revocation_invalidates_queued_connection_reads(self):
        started = threading.Event()
        queued = threading.Event()
        release = threading.Event()
        paths = []
        errors = []

        class TrackingLock:
            def __init__(self):
                self.lock = threading.Lock()
                self.count_lock = threading.Lock()
                self.attempts = 0

            def __enter__(self):
                with self.count_lock:
                    self.attempts += 1
                    if self.attempts == 2:
                        queued.set()
                self.lock.acquire()

            def __exit__(self, *args):
                self.lock.release()

        def transport(path, headers):
            paths.append(path)
            if len(paths) == 1:
                started.set()
                if not release.wait(5):
                    raise AssertionError("synthetic read was not released")
            return encoded(aggregate()) if "aggregate-portfolio" in path else balance()

        manager = self.manager(transport)
        manager.collection_lock = TrackingLock()

        def begin(request_id):
            try:
                self.connect(manager, request_id=request_id)
            except etoro.EToroError as exc:
                errors.append(exc.code)

        first = threading.Thread(target=begin, args=("first",))
        second = threading.Thread(target=begin, args=("second",))
        first.start()
        try:
            self.assertTrue(started.wait(2))
            second.start()
            self.assertTrue(queued.wait(2))
            self.assertEqual(manager.revoke(NOW)["state"], "not_connected")
        finally:
            release.set()
            first.join(5)
            if second.ident is not None:
                second.join(5)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertCountEqual(
            errors, ["approval_interrupted", "connection_read_outcome_uncertain"])
        self.assertEqual(len(paths), 1)
        self.assertEqual(manager.pending, {})

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

    def test_preview_expiry_includes_collection_time_and_rejects_late_results(self):
        for delay, expected_error in (
                (timedelta(minutes=4), None),
                (timedelta(minutes=6), "connection_read_outcome_uncertain")):
            with self.subTest(delay=delay):
                current = [NOW]
                fixture_transport = FixtureTransport()

                class DelayedTransport:
                    def __call__(self, path, headers):
                        if "/balances/" in path:
                            current[0] += delay
                        return fixture_transport(path, headers)

                manager = EToroConnection(
                    self.path, lambda: current[0], transport_factory=DelayedTransport,
                    credential_accessor=lambda: KEYS)
                self.managers.append(manager)
                if expected_error is not None:
                    with self.assertRaisesRegex(etoro.EToroError, expected_error):
                        manager.begin(
                            "synthetic-session", f"delayed-{delay}", self.consent(), NOW)
                    self.assertEqual(manager.pending, {})
                else:
                    manager.begin(
                        "synthetic-session", f"delayed-{delay}", self.consent(), NOW)
                    pending = manager.pending[f"delayed-{delay}"]
                    self.assertAlmostEqual(pending["timer"].interval, 60, delta=0.1)

    def test_preview_expired_in_queue_does_not_dispatch_a_read(self):
        current = [NOW]
        transport = FixtureTransport()
        manager = EToroConnection(
            self.path, lambda: current[0], transport_factory=lambda: transport,
            credential_accessor=lambda: KEYS)
        self.managers.append(manager)
        blocked = threading.Lock()
        blocked.acquire()
        queued = threading.Event()
        errors = []

        class QueueingLock:
            def __enter__(self):
                queued.set()
                blocked.acquire()

            def __exit__(self, *args):
                blocked.release()

        manager.collection_lock = QueueingLock()

        def begin():
            try:
                self.connect(manager, request_id="queued")
            except etoro.EToroError as exc:
                errors.append(exc.code)

        worker = threading.Thread(target=begin)
        worker.start()
        try:
            self.assertTrue(queued.wait(2))
            current[0] = NOW + timedelta(minutes=5)
        finally:
            blocked.release()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, ["approval_interrupted"])
        self.assertEqual(transport.paths, [])
        self.assertEqual(manager.pending, {})

    def test_preview_expiry_between_reads_stops_remaining_collection(self):
        current = [NOW]
        paths = []

        class ExpiringTransport:
            def __call__(self, path, headers):
                paths.append(path)
                if path == etoro.AGGREGATE_PORTFOLIO_PATHS["real"]:
                    current[0] = NOW + timedelta(minutes=5)
                    return encoded(aggregate())
                return balance()

        manager = EToroConnection(
            self.path, lambda: current[0], transport_factory=ExpiringTransport,
            credential_accessor=lambda: KEYS)
        self.managers.append(manager)
        with self.assertRaisesRegex(
                etoro.EToroError, "^connection_read_outcome_uncertain$"):
            self.connect(manager, request_id="between-reads")
        self.assertEqual(paths, [etoro.AGGREGATE_PORTFOLIO_PATHS["real"]])
        self.assertEqual(manager.pending, {})

    def test_preview_clock_failure_after_dispatch_is_reported_as_uncertain(self):
        for error_type in (OSError, RuntimeError):
            with self.subTest(error_type=error_type):
                paths = []

                def clock():
                    if paths:
                        raise error_type("synthetic clock failure")
                    return NOW

                def transport(path, headers):
                    paths.append(path)
                    return encoded(aggregate())

                manager = EToroConnection(
                    self.path, clock, transport_factory=lambda: transport,
                    credential_accessor=lambda: KEYS)
                self.managers.append(manager)
                with self.assertRaisesRegex(
                        etoro.EToroError, "^connection_read_outcome_uncertain$"):
                    manager.begin(
                        "synthetic-session", f"clock-{error_type.__name__}",
                        self.consent(), NOW)
                self.assertEqual(paths, [etoro.AGGREGATE_PORTFOLIO_PATHS["real"]])
                self.assertEqual(manager.pending, {})

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

    def test_key_rotation_requires_fresh_consent_before_any_further_read(self):
        revision = [4]
        transport = FixtureTransport([
            encoded(aggregate()), balance(), encoded(aggregate()), balance(),
        ])
        manager = EToroConnection(
            self.path, lambda: NOW, transport_factory=lambda: transport,
            credential_accessor=lambda: KEYS,
            credential_revision_accessor=lambda: revision[0])
        self.managers.append(manager)
        preview = self.connect(manager)
        manager.confirm("synthetic-session", preview["preview_id"], True, True, 30, NOW)
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(saved["schema_version"], 2)
        self.assertEqual(saved["credential_revision"], 4)

        calls_before_rotation = len(transport.paths)
        revision[0] += 1
        self.assertEqual(manager.status(NOW)["state"], "reauthorization_required")
        manager.start_once()
        self.assertEqual(len(transport.paths), calls_before_rotation)

        renewed = self.connect(manager, request_id="after-rotation")
        status = manager.confirm(
            "synthetic-session", renewed["preview_id"], True, True, 30, NOW)
        self.assertEqual(status["state"], "startup_refresh_enabled")
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["credential_revision"], 5)

    def test_legacy_permission_requires_reauthorization_with_revision_binding(self):
        original = self.manager(FixtureTransport())
        preview = self.connect(original)
        original.confirm("synthetic-session", preview["preview_id"], True, True, 30, NOW)
        self.assertEqual(
            json.loads(self.path.read_text(encoding="utf-8"))["schema_version"], 1)

        transport = FixtureTransport()
        upgraded = EToroConnection(
            self.path, lambda: NOW, transport_factory=lambda: transport,
            credential_accessor=lambda: KEYS, credential_revision_accessor=lambda: 1)
        self.managers.append(upgraded)
        self.assertEqual(upgraded.status(NOW)["state"], "reauthorization_required")
        upgraded.start_once()
        self.assertEqual(transport.paths, [])

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

    def test_explicit_refresh_does_not_require_startup_permission(self):
        manager = self.manager(FixtureTransport())
        preview = self.connect(manager, self.consent(read_scope=False))
        manager.confirm("synthetic-session", preview["preview_id"], True, False, None, NOW)
        transport = FixtureTransport()
        manager.transport_factory = lambda: transport
        generation = manager.begin_generation()
        expected = (1, 12345, "real")
        result = manager.refresh_explicitly(
            NOW, manual_confirmed=True, generation=generation, expected_connection=expected,
            history_confirmed=True)
        self.assertEqual(result["state"], "refreshed")
        self.assertEqual(len(transport.paths), 3)
        self.assertEqual(manager.status(NOW)["state"], "connected")
        manager.revoke(NOW)
        with self.assertRaises(InvestmentError):
            manager.refresh_explicitly(
                NOW, manual_confirmed=True, generation=generation, expected_connection=expected,
                history_confirmed=True)
        self.assertEqual(len(transport.paths), 3)

    def test_manual_refresh_requires_separate_closed_history_confirmation(self):
        manager = self.manager(FixtureTransport())
        preview = self.connect(manager)
        manager.confirm("synthetic-session", preview["preview_id"], True, False, None, NOW)
        transport = FixtureTransport()
        manager.transport_factory = lambda: transport
        with self.assertRaisesRegex(InvestmentError, "closed-trade history read"):
            manager.refresh_explicitly(
                NOW, manual_confirmed=True, generation=manager.begin_generation(),
                expected_connection=(1, 12345, "real"))
        self.assertEqual(transport.paths, [])

    def test_expired_startup_permission_allows_only_confirmed_manual_refresh(self):
        manager = self.manager(FixtureTransport())
        preview = self.connect(manager)
        manager.confirm("synthetic-session", preview["preview_id"], True, True, 1, NOW)
        later = NOW + timedelta(days=2)
        transport = FixtureTransport()
        restarted = self.manager(transport, now=later)
        restarted.start_once()
        self.assertEqual(transport.paths, [])
        with self.assertRaises(InvestmentError):
            restarted.refresh_explicitly(later)
        self.assertEqual(transport.paths, [])
        result = restarted.refresh_explicitly(
            later, manual_confirmed=True, generation=restarted.begin_generation(),
            expected_connection=(1, 12345, "real"), history_confirmed=True)
        self.assertEqual(result["state"], "refreshed")
        self.assertEqual(restarted.status(later)["state"], "expired")

    def test_confirmed_manual_read_rejects_replaced_connection(self):
        manager = self.manager(FixtureTransport())
        first = self.connect(manager)
        manager.confirm("synthetic-session", first["preview_id"], True, False, None, NOW)
        generation = manager.begin_generation()
        second_transport = FixtureTransport()
        manager.transport_factory = lambda: second_transport
        second = self.connect(manager, request_id="replacement")
        manager.confirm("synthetic-session", second["preview_id"], True, False, None, NOW)
        with self.assertRaises(InvestmentError):
            manager.refresh_explicitly(
                NOW, manual_confirmed=True, generation=generation,
                expected_connection=(1, 12345, "real"), history_confirmed=True)
        self.assertEqual(len(second_transport.paths), 2)

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
        self.assertFalse(any("trade/history" in path for path in refreshed.paths))

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
        result = restarted.refresh_explicitly(
            NOW, manual_confirmed=True, generation=restarted.begin_generation(),
            expected_connection=(1, 12345, "real"), history_confirmed=True)
        self.assertEqual(result["state"], "refresh_failed")
        self.assertEqual(result["error_code"], "account_binding_changed")
        self.assertIsNone(restarted.snapshot)
        self.assertEqual(len(refresh_transport.paths), 1)

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

    def test_clock_failure_after_balance_marks_manual_and_startup_reads_failed(self):
        manager = self.manager(FixtureTransport())
        preview = self.connect(manager)
        manager.confirm("synthetic-session", preview["preview_id"], True, True, 30, NOW)
        for error_type in (OSError, RuntimeError):
            for startup in (False, True):
                with self.subTest(error_type=error_type, startup=startup):
                    fail = [False]
                    def clock():
                        if fail[0]:
                            fail[0] = False
                            raise error_type("synthetic clock failure")
                        return NOW
                    class FailingClockTransport(FixtureTransport):
                        def __call__(self, path, headers):
                            payload = super().__call__(path, headers)
                            if len(self.paths) == 2:
                                fail[0] = True
                            return payload
                    transport = FailingClockTransport()
                    fresh = EToroConnection(
                        self.path, clock, transport_factory=lambda: transport,
                        credential_accessor=lambda: KEYS)
                    self.managers.append(fresh)
                    if startup:
                        fresh.start_once()
                        fresh._worker.join(timeout=5)
                        self.assertFalse(fresh._worker.is_alive())
                    else:
                        result = fresh.refresh_explicitly(
                            NOW, manual_confirmed=True, generation=fresh.begin_generation(),
                            expected_connection=(1, 12345, "real"), history_confirmed=True)
                        self.assertEqual(result["error_code"], "refresh_unavailable")
                    self.assertEqual(fresh.status(NOW)["last_status"], "refresh_unavailable")
                    self.assertIsNone(fresh.snapshot)
                    self.assertEqual(len(transport.paths), 2)

    def test_clock_failure_at_final_publication_check_preserves_snapshot(self):
        manager = self.manager(FixtureTransport())
        preview = self.connect(manager)
        manager.confirm("synthetic-session", preview["preview_id"], True, True, 30, NOW)
        expected = json.loads(self.path.read_text(encoding="utf-8"))
        for error_type in (OSError, RuntimeError):
            with self.subTest(error_type=error_type):
                calls = [0]
                def clock():
                    calls[0] += 1
                    if calls[0] == 7:
                        raise error_type("synthetic clock failure")
                    return NOW
                changed = aggregate()
                changed["accountTotals"]["accountTotalValue"] = 999
                manager.clock_fn = clock
                manager.transport_factory = lambda: FixtureTransport([encoded(changed), balance()])
                result = manager._refresh(expected, NOW)
                self.assertEqual(result["error_code"], "approval_interrupted")
                self.assertEqual(manager.last_status, "approval_interrupted")
                self.assertEqual(manager.snapshot["account_totals"]["accountTotalValue"], "120")


if __name__ == "__main__":
    unittest.main()
