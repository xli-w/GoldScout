"""Synthetic, offline tests for the eToro dashboard connection."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from dashboard.etoro_connection import EToroConnection, ManualRefreshRejected
from investment import InvestmentError, etoro
from test_investment_etoro_portfolio import aggregate, encoded

NOW = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)
KEYS = {"x-api-key": "synthetic-public", "x-user-key": "synthetic-user"}


def balance():
    return encoded({"balances": [{
        "accountId": "12345", "accountType": "Trading", "balance": 105,
        "currency": "USD", "displayBalance": 82.25, "displayCurrency": "GBP",
        "exchangeRate": 0.78,
    }]})


class FixtureTransport:
    def __init__(self):
        self.paths = []
        self.account = 12345

    def __call__(self, path, headers):
        self.paths.append(path)
        if "aggregate-portfolio" in path:
            data = aggregate()
            data["cid"] = self.account
            return encoded(data)
        if "/balances/" in path:
            return balance()
        return encoded([])


class EToroConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / ".etoro" / "connection.json"
        self.managers = []
        self.transport = FixtureTransport()
        self.revision = [1]
        self.now = [NOW]

    def manager(self, transport=None, accessor=None):
        manager = EToroConnection(
            self.path, lambda: self.now[0],
            transport_factory=lambda: transport or self.transport,
            credential_snapshot_accessor=lambda: (self.revision[0], (accessor or (lambda: KEYS))()))
        self.managers.append(manager)
        return manager

    def tearDown(self):
        for manager in self.managers:
            manager.stop()

    def enable(self, manager, request="enable", generation=None):
        return manager.begin("session", request, {"environment": "real"}, self.now[0],
                             manager.begin_generation() if generation is None else generation)

    def refresh(self, manager, connection=None):
        connection = connection or manager.status(self.now[0])["connection"]
        return manager.refresh_explicitly(
            self.now[0], generation=manager.begin_generation(),
            expected_connection=(connection["revision"], connection["account_id"],
                                 connection["environment"]))

    def legacy(self, revoked=None):
        return {"schema_version": 2, "revision": 4, "environment": "real",
                "account_id": 12345, "approved_at": "2026-01-01T00:00:00.000000Z",
                "expires_at": "2026-01-02T00:00:00.000000Z",
                "retention_rights_verified": True, "read_scope_verified": True,
                "trade_capable_key_risk_accepted": False, "eligibility_verified": True,
                "account_binding_verified": True, "revoked_at": revoked,
                "credential_revision": 1}

    def write(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data), encoding="utf-8")

    def test_idle_status_and_startup_never_touch_credentials(self):
        accessor = Mock(side_effect=AssertionError("unexpected credential read"))
        manager = self.manager(accessor=accessor)
        self.assertEqual(manager.status(NOW)["state"], "not_connected")
        manager.start_once()
        self.assertFalse(self.path.exists())
        accessor.assert_not_called()

    def test_credential_revision_failure_after_dispatch_is_uncertain(self):
        manager = self.manager()
        self.enable(manager)
        original = self.transport
        def interrupted(path, headers):
            result = original(path, headers)
            manager.credential_snapshot_accessor = lambda: (_ for _ in ()).throw(ValueError())
            return result
        manager.transport_factory = lambda: interrupted
        prior = manager.snapshot
        result = self.refresh(manager)
        self.assertEqual(result["state"], "refresh_failed")
        self.assertEqual(result["error_code"], "connection_read_outcome_uncertain")
        self.assertIs(manager.snapshot, prior)
        self.assertEqual(len(original.paths), 3)

    def test_sent_credentials_remain_the_snapshot_bound_to_activation(self):
        manager = self.manager()
        current = [dict(KEYS)]
        manager.credential_snapshot_accessor = lambda: (self.revision[0], dict(current[0]))
        sent = []
        def transport(path, headers):
            sent.append({key: headers[key] for key in ("x-api-key", "x-user-key")})
            current[0] = {"x-api-key": "other-synthetic", "x-user-key": "other-synthetic"}
            result = self.transport(path, headers)
            current[0] = dict(KEYS)
            return result
        manager.transport_factory = lambda: transport
        self.assertEqual(self.enable(manager)["state"], "startup_refresh_enabled")
        self.assertEqual(sent, [KEYS, KEYS])

    def test_change_between_capture_and_dispatch_stops_real_transport_path(self):
        manager = self.manager()
        calls = [0]
        def snapshot():
            calls[0] += 1
            keys = KEYS if calls[0] != 2 else {
                "x-api-key": "other-synthetic", "x-user-key": "other-synthetic"}
            return self.revision[0], dict(keys)
        manager.credential_snapshot_accessor = snapshot
        manager.transport_factory = None
        with patch("dashboard.etoro_connection.etoro.EToroTransport") as transport:
            with self.assertRaisesRegex(etoro.EToroError, "approval_interrupted"):
                self.enable(manager)
        transport.return_value.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_transient_credential_failure_at_balance_dispatch_cannot_activate(self):
        manager = self.manager()
        calls = [0]
        def snapshot():
            calls[0] += 1
            if calls[0] == 5:
                raise ValueError("synthetic transient failure")
            return self.revision[0], dict(KEYS)
        manager.credential_snapshot_accessor = snapshot
        with self.assertRaisesRegex(etoro.EToroError, "connection_read_outcome_uncertain"):
            self.enable(manager)
        self.assertEqual(len(self.transport.paths), 1)
        self.assertFalse(self.path.exists())

    def test_mutation_lock_failure_after_dispatch_is_uncertain(self):
        manager = self.manager()
        with patch.object(manager, "_claim_mutation", side_effect=etoro.EToroError("request_in_progress")):
            with self.assertRaisesRegex(etoro.EToroError, "connection_read_outcome_uncertain"):
                self.enable(manager)
        self.assertFalse(self.path.exists())
        self.enable(manager, "second")
        previous = manager.snapshot
        with patch.object(manager, "_claim_mutation", side_effect=etoro.EToroError("request_in_progress")):
            result = self.refresh(manager)
        self.assertEqual(result["error_code"], "connection_read_outcome_uncertain")
        self.assertIs(manager.snapshot, previous)

    def test_enable_refresh_restart_without_expiry_and_disable(self):
        manager = self.manager()
        result = self.enable(manager)
        self.assertEqual(result["state"], "startup_refresh_enabled")
        self.assertEqual(result["connection"]["account_id"], 12345)
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(saved["schema_version"], 3)
        self.assertNotIn("expires_at", saved)
        self.assertNotIn("synthetic-public", self.path.read_text(encoding="utf-8"))
        self.assertEqual(self.refresh(manager)["state"], "refreshed")
        self.assertEqual(len(self.transport.paths), 5)
        self.now[0] += timedelta(days=400)
        restarted = self.manager()
        restarted.start_once()
        restarted._worker.join(5)
        self.assertEqual(len(self.transport.paths), 7)
        restarted.start_once()
        self.assertEqual(len(self.transport.paths), 7)
        self.assertFalse(any("trade/history" in p for p in self.transport.paths[5:]))
        self.assertEqual(restarted.last_status, "success")
        self.assertEqual(restarted.revoke(self.now[0])["state"], "revoked")
        with self.assertRaises(ManualRefreshRejected):
            self.refresh(restarted)
        later = self.manager()
        later.start_once()
        self.assertEqual(len(self.transport.paths), 7)

    def test_legacy_expired_and_revoked_never_start_without_enable(self):
        for revoked in (None, "2026-01-03T00:00:00.000000Z"):
            with self.subTest(revoked=revoked):
                self.write(self.legacy(revoked))
                manager = self.manager()
                before = self.path.read_bytes()
                self.assertEqual(manager.status(NOW)["state"],
                                 "revoked" if revoked else "reauthorization_required")
                manager.start_once()
                self.assertEqual(self.transport.paths, [])
                self.assertEqual(self.path.read_bytes(), before)
                self.assertEqual(self.enable(manager, str(revoked))["state"], "startup_refresh_enabled")
                self.assertEqual(json.loads(self.path.read_bytes())["revision"], 5)
                self.transport.paths.clear()

    def test_unexpired_legacy_permission_also_requires_explicit_enable(self):
        legacy = self.legacy()
        legacy["approved_at"] = "2026-11-18T00:00:00Z"
        legacy["expires_at"] = "2026-11-25T00:00:00Z"
        self.write(legacy)
        manager = self.manager()
        self.assertEqual(manager.status(NOW)["state"], "reauthorization_required")
        manager.start_once()
        self.assertEqual(self.transport.paths, [])

    def test_revoked_legacy_records_are_reported_disabled_without_credentials(self):
        for version in (1, 2):
            with self.subTest(version=version):
                record = self.legacy()
                record["schema_version"] = version
                if version == 1:
                    del record["credential_revision"]
                self.write(record)
                manager = self.manager()
                manager.credential_snapshot_accessor = lambda: (_ for _ in ()).throw(ValueError())
                self.assertEqual(manager.status(NOW)["state"], "reauthorization_required")
                self.assertEqual(manager.revoke(NOW)["state"], "revoked")
                self.assertEqual(manager.status(NOW)["state"], "revoked")
                manager.start_once()
                self.assertEqual(self.transport.paths, [])

    def test_retry_after_credential_rotation_reports_commit_and_current_state_without_reads(self):
        manager = self.manager()
        committed = self.enable(manager)
        self.assertEqual(committed["state"], "startup_refresh_enabled")
        self.revision[0] += 1
        self.assertEqual(manager.status(NOW)["state"], "reauthorization_required")
        retry = self.enable(manager)
        self.assertEqual(retry["state"], "reauthorization_required")
        self.assertEqual(retry["activation_receipt"], committed["activation_receipt"])
        self.assertTrue(retry["activation_receipt"]["committed"])
        self.assertEqual(len(self.transport.paths), 2)
        self.assertEqual(manager.status(NOW)["state"], "reauthorization_required")
        self.assertEqual(manager.revoke(NOW)["state"], "revoked")
        retry = self.enable(manager, generation=0)
        self.assertEqual(retry["state"], "revoked")
        self.assertEqual(retry["activation_receipt"], committed["activation_receipt"])
        self.revision[0] -= 1
        restarted = self.manager()
        restarted.start_once()
        self.assertIsNone(restarted._worker)
        self.assertEqual(restarted.status(NOW)["state"], "revoked")
        self.assertEqual(len(self.transport.paths), 2)

    def test_disconnected_startup_then_other_instance_activation_is_not_pending(self):
        first = self.manager()
        first.start_once()
        self.assertFalse(first.status(NOW)["startup_pending"])
        other = self.manager()
        self.enable(other)
        status = first.status(NOW)
        self.assertEqual(status["state"], "startup_refresh_enabled")
        self.assertEqual(status["last_status"], "not_collected")
        self.assertFalse(status["startup_pending"])
        self.assertEqual(self.refresh(first)["state"], "refreshed")

    def test_startup_pending_is_true_only_during_the_local_startup_read(self):
        original = self.manager()
        self.enable(original)
        entered, release = threading.Event(), threading.Event()
        fixture = self.transport
        def blocking(path, headers):
            entered.set()
            if not release.wait(5):
                raise AssertionError("startup read not released")
            return fixture(path, headers)
        restarted = self.manager(blocking)
        restarted.start_once()
        try:
            self.assertTrue(entered.wait(2))
            self.assertTrue(restarted.status(NOW)["startup_pending"])
        finally:
            release.set()
            restarted._worker.join(5)
        self.assertFalse(restarted._worker.is_alive())
        self.assertFalse(restarted.status(NOW)["startup_pending"])
        self.assertEqual(restarted.status(NOW)["last_status"], "success")

    def test_uncertain_startup_cancellation_is_exposed_after_pending_finishes(self):
        original = self.manager()
        self.enable(original)
        def interrupted(path, headers):
            if "/balances/" in path:
                raise etoro.EToroError("cancellation_unconfirmed")
            return self.transport(path, headers)
        restarted = self.manager(interrupted)
        restarted.start_once()
        restarted._worker.join(5)
        status = restarted.status(NOW)
        self.assertEqual(status["last_status"], "connection_read_outcome_uncertain")
        self.assertFalse(status["startup_pending"])

    def test_startup_lock_file_error_is_sanitized_and_does_not_read(self):
        original = self.manager()
        self.enable(original)
        paths_before = list(self.transport.paths)
        restarted = self.manager()
        with patch.object(restarted, "_claim_file", side_effect=OSError("private synthetic path")):
            restarted.start_once()
            restarted._worker.join(5)
        self.assertFalse(restarted._worker.is_alive())
        status = restarted.status(NOW)
        self.assertEqual(status["state"], "startup_refresh_enabled")
        self.assertEqual(status["last_status"], "startup_lock_unavailable")
        self.assertFalse(status["startup_pending"])
        self.assertNotIn("private synthetic path", repr(status))
        self.assertEqual(self.transport.paths, paths_before)

    def test_manual_lock_file_error_is_sanitized_and_does_not_read(self):
        manager = self.manager()
        self.enable(manager)
        paths_before = list(self.transport.paths)
        previous = manager.snapshot
        with patch.object(manager, "_claim_file", side_effect=OSError("private synthetic path")):
            result = self.refresh(manager)
        self.assertEqual(result, {"state": "refresh_failed", "error_code": "startup_lock_unavailable"})
        self.assertEqual(manager.status(NOW)["last_status"], "startup_lock_unavailable")
        self.assertIs(manager.snapshot, previous)
        self.assertNotIn("private synthetic path", repr(result))
        self.assertEqual(self.transport.paths, paths_before)

    def test_startup_reparse_point_error_is_sanitized_and_does_not_read(self):
        from dashboard.workspace import path_identity
        original = self.manager()
        self.enable(original)
        paths_before = list(self.transport.paths)
        restarted = self.manager()
        def guarded_identity(path):
            if Path(path).name == "startup.lock":
                raise InvestmentError("private synthetic reparse point")
            return path_identity(path)
        with patch("dashboard.etoro_connection.path_identity", side_effect=guarded_identity):
            restarted.start_once()
            restarted._worker.join(5)
        self.assertFalse(restarted._worker.is_alive())
        status = restarted.status(NOW)
        self.assertEqual(status["last_status"], "startup_lock_unavailable")
        self.assertFalse(status["startup_pending"])
        self.assertNotIn("private synthetic reparse point", repr(status))
        self.assertEqual(self.transport.paths, paths_before)

    def test_unconfirmed_activation_before_dispatch_remains_uncertain(self):
        manager = self.manager()
        with patch.object(manager, "_claim_startup",
                          side_effect=etoro.EToroError("cancellation_unconfirmed")):
            with self.assertRaisesRegex(etoro.EToroError, "connection_read_outcome_uncertain"):
                self.enable(manager)
        self.assertEqual(self.transport.paths, [])
        self.assertEqual(manager.status(NOW)["last_status"], "connection_read_outcome_uncertain")
        with self.assertRaisesRegex(etoro.EToroError, "connection_read_outcome_uncertain"):
            self.enable(manager)
        self.assertEqual(self.transport.paths, [])

    def test_other_instance_same_account_activation_hides_old_snapshot(self):
        first = self.manager()
        self.enable(first)
        original = first.snapshot
        other = self.manager()
        self.enable(other, "another-instance")
        status = first.status(NOW)
        self.assertEqual(status["state"], "startup_refresh_enabled")
        self.assertEqual(status["connection"]["account_id"], original["account_id"])
        self.assertEqual(status["connection"]["revision"], 2)
        self.assertIsNone(status["snapshot"])
        self.assertEqual(status["last_status"], "connection_changed")
        self.assertIs(first.snapshot, original)
        self.transport.account = 99999
        self.assertEqual(self.refresh(first)["error_code"], "account_binding_changed")
        status = first.status(NOW)
        self.assertEqual(status["last_status"], "account_binding_changed")
        self.assertIsNone(status["snapshot"])
        self.transport.account = 12345
        self.assertEqual(self.refresh(first)["state"], "refreshed")
        status = first.status(NOW)
        self.assertEqual(status["last_status"], "success")
        self.assertEqual(status["snapshot"]["account_id"], original["account_id"])

    def test_unreadable_connection_never_publishes_cached_snapshot(self):
        manager = self.manager()
        self.enable(manager)
        self.path.write_bytes(b"{invalid")
        status = manager.status(NOW)
        self.assertEqual(status["state"], "unavailable")
        self.assertIsNone(status["snapshot"])

    def test_demo_environment_persists_and_is_used_on_restart(self):
        manager = self.manager()
        result = manager.begin("session", "demo", {"environment": "demo"}, NOW, 0)
        self.assertEqual(result["connection"]["environment"], "demo")
        self.assertIn("/demo/", self.transport.paths[0])
        restarted = self.manager()
        restarted.start_once()
        restarted._worker.join(5)
        self.assertIn("/demo/", self.transport.paths[2])

    def test_corruption_never_overwritten_or_read(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b'{"schema_version":3,"revision":1,"revision":2}')
        before = self.path.read_bytes()
        manager = self.manager()
        self.assertEqual(manager.status(NOW)["state"], "unavailable")
        with self.assertRaises(InvestmentError):
            self.enable(manager)
        with self.assertRaises(InvestmentError):
            manager.revoke(NOW)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.transport.paths, [])

    def test_exact_binding_and_credential_revision_before_reads(self):
        manager = self.manager()
        connection = self.enable(manager)["connection"]
        self.transport.account = 99999
        result = self.refresh(manager)
        self.assertEqual(result["error_code"], "account_binding_changed")
        self.assertEqual(len(self.transport.paths), 3)
        self.revision[0] += 1
        self.assertEqual(manager.status(NOW)["state"], "reauthorization_required")
        with self.assertRaises(ManualRefreshRejected):
            self.refresh(manager, connection)
        self.assertEqual(manager.revoke(NOW)["state"], "revoked")

    def test_startup_wrong_account_is_not_published_or_followed_by_balance(self):
        manager = self.manager()
        self.enable(manager)
        self.transport.account = 99999
        restarted = self.manager()
        restarted.start_once()
        restarted._worker.join(5)
        self.assertEqual(restarted.last_status, "account_binding_changed")
        self.assertIsNone(restarted.snapshot)
        self.assertEqual(len(self.transport.paths), 3)

    def test_clock_failure_at_publication_preserves_previous_snapshot(self):
        manager = self.manager()
        self.enable(manager)
        previous = manager.snapshot
        claim = manager._claim_mutation
        def claim_after_clock_failure():
            manager.clock_fn = lambda: (_ for _ in ()).throw(InvestmentError("clock failure"))
            return claim()
        with patch.object(manager, "_claim_mutation", side_effect=claim_after_clock_failure):
            result = self.refresh(manager)
        self.assertEqual(result["error_code"], "connection_read_outcome_uncertain")
        self.assertIs(manager.snapshot, previous)

    def test_revocation_interrupts_reads_and_queued_cross_instance_enable(self):
        entered, release = threading.Event(), threading.Event()
        paths = []
        def blocking(path, headers):
            paths.append(path)
            entered.set()
            self.assertTrue(release.wait(5))
            return encoded(aggregate())
        manager = self.manager(blocking)
        other = self.manager()
        errors = []
        worker = threading.Thread(target=lambda: self._capture_error(
            errors, lambda: self.enable(manager)))
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            self.assertEqual(other.revoke(NOW)["state"], "not_connected")
        finally:
            release.set()
            worker.join(5)
        self.assertEqual(errors, ["connection_read_outcome_uncertain"])
        self.assertEqual(len(paths), 1)
        self.assertEqual(manager.status(NOW)["state"], "not_connected")

    @staticmethod
    def _capture_error(errors, fn):
        try:
            fn()
        except etoro.EToroError as exc:
            errors.append(exc.code)

    def test_queued_enable_observes_cross_instance_revoke(self):
        first = self.manager()
        second = self.manager()
        first.collection_lock.acquire()
        entered = threading.Event()
        initial = first.capture_connection(NOW)
        errors = []
        def work():
            entered.set()
            self._capture_error(errors, lambda: first.begin(
                "session", "queued", {"environment": "real"}, NOW, 0, initial))
        thread = threading.Thread(target=work)
        thread.start()
        try:
            self.assertTrue(entered.wait(2))
            second.revoke(NOW)
        finally:
            first.collection_lock.release()
            thread.join(5)
        self.assertEqual(errors, ["approval_interrupted"])
        self.assertEqual(self.transport.paths, [])

    def test_repeated_disable_interrupts_an_enable_queued_in_another_instance(self):
        first = self.manager()
        second = self.manager()
        second.revoke(NOW)
        initial = first.capture_connection(NOW)
        second.revoke(NOW)
        with self.assertRaisesRegex(etoro.EToroError, "approval_interrupted"):
            first.begin("session", "queued", {"environment": "real"}, NOW, 0, initial)
        self.assertEqual(self.transport.paths, [])

    def test_request_retry_and_save_uncertainty_do_not_repeat_gets(self):
        manager = self.manager()
        with patch.object(manager, "_write_permission", side_effect=InvestmentError("failed")):
            for _ in range(2):
                with self.assertRaisesRegex(etoro.EToroError, "connection_read_outcome_uncertain"):
                    self.enable(manager)
        self.assertEqual(len(self.transport.paths), 2)
        self.assertFalse(self.path.exists())

    def test_unconfirmed_balance_cancellation_never_saves_activation(self):
        def transport(path, headers):
            if "aggregate-portfolio" in path:
                return encoded(aggregate())
            raise etoro.EToroError("cancellation_unconfirmed")
        manager = self.manager(transport)
        with self.assertRaisesRegex(etoro.EToroError, "connection_read_outcome_uncertain"):
            self.enable(manager)
        self.assertFalse(self.path.exists())

    def test_collection_time_has_no_preview_deadline(self):
        fixture = self.transport
        def delayed(path, headers):
            payload = fixture(path, headers)
            self.now[0] += timedelta(minutes=10)
            return payload
        manager = self.manager(delayed)
        self.assertEqual(self.enable(manager)["state"], "startup_refresh_enabled")
        self.assertEqual(len(fixture.paths), 2)

    def test_credential_change_during_read_prevents_balance_and_activation(self):
        paths = []
        def rotate(path, headers):
            paths.append(path)
            self.revision[0] += 1
            return encoded(aggregate())
        manager = self.manager(rotate)
        with self.assertRaisesRegex(etoro.EToroError, "connection_read_outcome_uncertain"):
            self.enable(manager)
        self.assertEqual(len(paths), 1)
        self.assertFalse(self.path.exists())

    def test_clock_failure_after_dispatch_does_not_save_activation(self):
        manager = self.manager()
        fail = [False]
        def clock():
            if fail[0]:
                raise InvestmentError("synthetic clock failure")
            return NOW
        def transport(path, headers):
            fail[0] = True
            return encoded(aggregate())
        manager.clock_fn = clock
        manager.transport_factory = lambda: transport
        with self.assertRaisesRegex(etoro.EToroError, "connection_read_outcome_uncertain"):
            self.enable(manager)
        self.assertFalse(self.path.exists())

    def test_revocation_during_manual_read_stops_remaining_requests(self):
        manager = self.manager()
        self.enable(manager)
        entered, release = threading.Event(), threading.Event()
        paths = []
        def transport(path, headers):
            paths.append(path)
            entered.set()
            self.assertTrue(release.wait(5))
            return encoded(aggregate())
        manager.transport_factory = lambda: transport
        result = []
        worker = threading.Thread(target=lambda: result.append(self.refresh(manager)))
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            manager.revoke(NOW)
        finally:
            release.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result[0]["error_code"], "connection_read_outcome_uncertain")
        self.assertEqual(len(paths), 1)
        self.assertEqual(manager.status(NOW)["state"], "revoked")

    def test_revocation_after_reads_keeps_prior_snapshot(self):
        manager = self.manager()
        self.enable(manager)
        old_snapshot = manager.snapshot
        other = self.manager()
        fixture = self.transport
        def transport(path, headers):
            payload = fixture(path, headers)
            if "trade/history" in path:
                other.revoke(NOW)
            return payload
        manager.transport_factory = lambda: transport
        result = self.refresh(manager)
        self.assertEqual(result["error_code"], "connection_read_outcome_uncertain")
        self.assertEqual(manager.snapshot, old_snapshot)

    def test_explicit_reenable_in_another_instance_permits_new_refresh(self):
        manager = self.manager()
        self.enable(manager)
        manager.revoke(NOW)
        other = self.manager()
        self.enable(other, "reenable")
        self.assertEqual(self.refresh(manager)["state"], "refreshed")

    def test_unconfirmed_balance_cancellation_stops_history_refresh(self):
        manager = self.manager()
        self.enable(manager)
        old_snapshot = manager.snapshot
        paths = []
        def transport(path, headers):
            paths.append(path)
            if "aggregate-portfolio" in path:
                return encoded(aggregate())
            raise etoro.EToroError("cancellation_unconfirmed")
        manager.transport_factory = lambda: transport
        result = self.refresh(manager)
        self.assertEqual(result["error_code"], "connection_read_outcome_uncertain")
        self.assertEqual(len(paths), 2)
        self.assertEqual(manager.snapshot, old_snapshot)

    def test_future_activation_and_revision_failure_do_not_start(self):
        manager = self.manager()
        self.enable(manager)
        self.now[0] -= timedelta(seconds=1)
        before = len(self.transport.paths)
        fresh = self.manager()
        fresh.start_once()
        self.assertIsNone(fresh._worker)
        self.assertEqual(len(self.transport.paths), before)
        self.now[0] = NOW
        broken = self.manager()
        broken.credential_snapshot_accessor = lambda: (_ for _ in ()).throw(ValueError())
        broken.start_once()
        self.assertEqual(broken.last_status, "permission_unavailable")
