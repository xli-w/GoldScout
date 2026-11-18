from dataclasses import replace
from datetime import datetime, timedelta, timezone
from contextlib import closing
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from market_intelligence.contracts import (
    CalendarSnapshot, ControlReceipt, ControlRequest, IntelligenceSettings, ServiceFailure,
    SourceCoverage, service_status_state,
)
from market_intelligence.store import IntelligenceStore, StoreError, digest, encode, initialize, storage_schema
from market_intelligence.service import IntelligenceService
from market_intelligence.__main__ import main


class IntelligenceControlContractsTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
        self.settings = IntelligenceSettings(enabled=True, mode="shadow", revision=1)
        self.request = ControlRequest("request-1", "intelligence-1", 0, self.now,
                                      self.now + timedelta(minutes=5), self.settings)

    def test_request_is_revision_bound_and_limited_to_analysis_modes(self):
        self.assertEqual(self.request.settings.mode, "shadow")
        for changes in ({"base_revision": -1}, {"base_revision": True},
                        {"base_revision": 1}, {"settings": replace(self.settings, revision=2)}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(self.request, **changes)

    def test_request_expiry_is_positive_and_bounded(self):
        for expires in (self.now, self.now - timedelta(seconds=1), self.now + timedelta(seconds=301)):
            with self.subTest(expires=expires), self.assertRaises(ValueError):
                replace(self.request, expires_at=expires)

    def test_request_times_and_identity_are_validated(self):
        for changes in ({"requested_at": self.now.replace(tzinfo=None)}, {"database_id": ""},
                        {"request_id": "request\n"}, {"settings": {"enabled": True}}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(self.request, **changes)

    def test_pending_is_not_an_applied_acknowledgement(self):
        receipt = ControlReceipt("request-1", "intelligence-1", "pending", None, None, "Awaiting service")
        for changes in ({"status": "applied"}, {"applied_at": self.now}, {"reason": ""}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(receipt, **changes)
        applied = replace(receipt, status="applied", effective_revision=1, applied_at=self.now, reason="Applied")
        self.assertEqual(applied.effective_revision, 1)
        self.assertEqual(applied.applied_at, self.now)


class IntelligenceStoreFixture:
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "intelligence.sqlite3"
        self.now = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
        self.store = initialize(self.path, self.now)

    def request(self, request_id="request-1", base_revision=0, **settings):
        return ControlRequest(request_id, self.store.database_id, base_revision, self.now,
                              self.now + timedelta(minutes=5),
                              IntelligenceSettings(revision=base_revision + 1, **settings))


class IntelligenceStoreTests(IntelligenceStoreFixture, unittest.TestCase):
    def test_oversized_collection_is_refused_before_publication(self):
        sources = tuple(SourceCoverage(f"source-{index}", ("inflation",), None, None, None) for index in range(51))
        self.store.enqueue(self.request(enabled=True), self.now)
        self.store.apply_pending(self.now)
        with self.assertRaises(ValueError):
            self.store.publish_calendar(CalendarSnapshot(1, self.now, sources, ()), 1, self.now)
        self.assertIsNone(self.store.calendar())

    def test_replace_cannot_rewrite_append_only_evidence(self):
        with closing(sqlite3.connect(self.path)) as handle:
            with self.assertRaises(sqlite3.IntegrityError):
                handle.execute("INSERT OR REPLACE INTO settings_history SELECT * FROM settings_history WHERE revision=0")
        self.assertEqual(self.store.settings(), IntelligenceSettings())

    def test_missing_storage_trigger_is_not_a_supported_schema(self):
        with closing(sqlite3.connect(self.path)) as handle:
            handle.execute("DROP TRIGGER immutable_settings_history_delete")
            handle.commit()
        with self.assertRaises(StoreError):
            IntelligenceStore(self.path)

    def test_applied_receipt_requires_matching_committed_settings(self):
        request = self.request(enabled=True)
        self.store.enqueue(request, self.now)
        receipt = ControlReceipt(request.request_id, self.store.database_id, "applied", 99, self.now, "Forged applied")
        payload = encode(receipt)
        with closing(sqlite3.connect(self.path)) as handle:
            handle.execute("UPDATE commands SET status='applied',applied_revision=99,receipt=?,receipt_hash=? WHERE request_id=?",
                           (payload, digest(payload), request.request_id))
            handle.execute("INSERT INTO command_events(request_id,request_hash,receipt,receipt_hash,observed_at) VALUES(?,?,?,?,?)",
                           (request.request_id, digest(encode(request)), payload, digest(payload), "2026-11-14T12:00:00Z"))
            handle.commit()
        with self.assertRaises(StoreError):
            self.store.receipt(request.request_id)

    def test_original_request_is_immutable(self):
        request = self.request(enabled=True)
        self.store.enqueue(request, self.now)
        payload = encode(replace(request, settings=replace(request.settings, enabled=False)))
        with closing(sqlite3.connect(self.path)) as handle:
            with self.assertRaises(sqlite3.IntegrityError):
                handle.execute("UPDATE commands SET payload=?,payload_hash=? WHERE request_id=?",
                               (payload, digest(payload), request.request_id))

    def test_enqueue_observation_cannot_follow_application(self):
        request = self.request(enabled=True)
        self.store.enqueue(request, self.now + timedelta(minutes=4))
        with self.assertRaises(StoreError):
            self.store.apply_pending(self.now + timedelta(minutes=1))
        self.assertEqual(self.store.receipt(request.request_id).status, "pending")

    def test_oversized_metadata_is_rejected(self):
        with closing(sqlite3.connect(self.path)) as handle:
            handle.execute("PRAGMA ignore_check_constraints=ON")
            handle.execute("UPDATE identity SET database_id=? WHERE id=1", ("x" * 1048576,))
            handle.commit()
        with self.assertRaises(StoreError):
            IntelligenceStore(self.path)

    def test_reads_do_not_change_database_or_directory_contents(self):
        self.store.enqueue(self.request(), self.now)
        before = {path.name: path.read_bytes() for path in self.path.parent.iterdir()}
        reader = IntelligenceStore(self.path)
        reader.settings()
        reader.receipt("request-1")
        reader.calendar()
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(["status", "--database", str(self.path)]), 0)
        after = {path.name: path.read_bytes() for path in self.path.parent.iterdir()}
        self.assertEqual(after, before)

    def test_retained_expired_history_does_not_exhaust_old_request_lookup(self):
        original = self.request(enabled=True)
        self.store.enqueue(original, self.now)
        expired = replace(original, requested_at=self.now - timedelta(minutes=10),
                          expires_at=self.now - timedelta(minutes=5))
        observed_at = "2026-11-14T12:00:00Z"
        with closing(sqlite3.connect(self.path)) as handle:
            for index in range(20000):
                request = replace(expired, request_id=f"expired-{index}")
                receipt = ControlReceipt(request.request_id, self.store.database_id, "expired", None, None, "Expired")
                request_payload, receipt_payload = encode(request), encode(receipt)
                handle.execute("INSERT INTO commands(request_id,payload,payload_hash,receipt,receipt_hash,status,enqueued_at) VALUES(?,?,?,?,?,?,?)",
                               (request.request_id, request_payload, digest(request_payload), receipt_payload,
                                digest(receipt_payload), "expired", observed_at))
                handle.execute("INSERT INTO command_events(request_id,request_hash,receipt,receipt_hash,observed_at) VALUES(?,?,?,?,?)",
                               (request.request_id, digest(request_payload), receipt_payload, digest(receipt_payload), observed_at))
            handle.commit()
        self.assertEqual(self.store.receipt(original.request_id).status, "pending")
        self.assertEqual(self.store.apply_pending(self.now)[0].status, "applied")
        self.assertEqual(self.store.enqueue(original, self.now).status, "applied")

    def test_corrupt_oversized_receipt_hash_is_refused(self):
        self.store.enqueue(self.request(), self.now)
        with closing(sqlite3.connect(self.path)) as handle:
            handle.execute("PRAGMA ignore_check_constraints=ON")
            handle.execute("UPDATE commands SET receipt_hash=? WHERE request_id='request-1'", ("x" * 1048576,))
            handle.commit()
        with self.assertRaises(StoreError):
            self.store.receipt("request-1")

    def test_future_schema_is_refused_without_resetting_settings(self):
        with closing(sqlite3.connect(self.path)) as handle:
            handle.execute("PRAGMA user_version=6")
        with self.assertRaises(StoreError) as result:
            IntelligenceStore(self.path)
        self.assertEqual(result.exception.code, "schema_unsupported")

    def test_effective_settings_require_an_applied_control(self):
        payload = encode(IntelligenceSettings(enabled=True, revision=1))
        with closing(sqlite3.connect(self.path)) as handle:
            handle.execute("INSERT INTO settings_history VALUES(1,?,?,?)",
                           (payload, digest(payload), "2026-11-14T12:00:00Z"))
            handle.execute("UPDATE settings_head SET revision=1 WHERE id=1")
            handle.commit()
        with self.assertRaises(StoreError):
            IntelligenceStore(self.path)

    def test_refusing_wal_storage_does_not_create_sidecars(self):
        with closing(sqlite3.connect(self.path)) as handle:
            self.assertEqual(handle.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal")
        before = {path.name: path.read_bytes() for path in self.path.parent.iterdir()}
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(["status", "--database", str(self.path)]), 1)
        after = {path.name: path.read_bytes() for path in self.path.parent.iterdir()}
        self.assertEqual(after, before)

    def test_pending_id_byte_guard_works_without_sqlite_row_limit(self):
        request = self.request(enabled=True)
        receipt = ControlReceipt(request.request_id, self.store.database_id, "pending", None, None, "Awaiting service")
        payload, receipt_payload = encode(request), encode(receipt)
        with closing(sqlite3.connect(self.path)) as handle:
            handle.execute("PRAGMA ignore_check_constraints=ON")
            handle.execute("INSERT INTO commands(request_id,payload,payload_hash,receipt,receipt_hash,status,enqueued_at) VALUES(?,?,?,?,?,?,?)",
                           ("x" * 1048576, payload, digest(payload), receipt_payload, digest(receipt_payload),
                            "pending", "2026-11-14T12:00:00Z"))
            handle.commit()
        class NoRowLimitConnection(sqlite3.Connection):
            def __getattribute__(self, name):
                if name == "setlimit":
                    raise AttributeError(name)
                return super().__getattribute__(name)
        connect = sqlite3.connect
        with patch("market_intelligence.store.sqlite3.connect",
                   side_effect=lambda *args, **kwargs: connect(*args, **kwargs, factory=NoRowLimitConnection)):
            with self.assertRaises(StoreError) as result:
                self.store.apply_pending(self.now)
        self.assertEqual(result.exception.code, "storage_invalid")

    def test_initial_state_is_off_and_restart_preserves_identity(self):
        self.assertEqual(self.store.settings(), IntelligenceSettings())
        self.assertIsNone(self.store.calendar())
        reopened = initialize(self.path, self.now + timedelta(days=1))
        self.assertEqual(reopened.database_id, self.store.database_id)
        self.assertEqual(reopened.settings(), IntelligenceSettings())

    def test_enqueue_is_pending_until_service_commits_settings(self):
        request = self.request(enabled=True, mode="shadow")
        self.assertEqual(self.store.enqueue(request, self.now).status, "pending")
        self.assertFalse(self.store.settings().enabled)
        applied = self.store.apply_pending(self.now + timedelta(seconds=1))
        self.assertEqual(len(applied), 1)
        self.assertEqual(applied[0].status, "applied")
        self.assertEqual(applied[0].effective_revision, 1)
        reopened = IntelligenceStore(self.path)
        self.assertEqual(reopened.settings(), request.settings)
        self.assertEqual(reopened.receipt(request.request_id), applied[0])
        self.assertEqual(reopened.apply_pending(self.now + timedelta(seconds=2)), ())

    def test_identical_retry_is_idempotent_conflicting_retry_is_refused(self):
        request = self.request(enabled=True)
        pending = self.store.enqueue(request, self.now)
        self.assertEqual(self.store.enqueue(request, self.now), pending)
        with self.assertRaises(StoreError) as result:
            self.store.enqueue(replace(request, settings=replace(request.settings, enabled=False)), self.now)
        self.assertEqual(result.exception.code, "request_conflict")
        applied = self.store.apply_pending(self.now)[0]
        self.assertEqual(self.store.enqueue(request, self.now + timedelta(days=1)), applied)

    def test_expired_controls_are_not_replayed_after_restart(self):
        request = self.request(enabled=True)
        self.store.enqueue(request, self.now)
        reopened = IntelligenceStore(self.path)
        self.assertEqual(reopened.apply_pending(request.expires_at)[0].status, "expired")
        self.assertEqual(reopened.settings(), IntelligenceSettings())
        expired = self.request(request_id="expired", enabled=True)
        self.assertEqual(reopened.enqueue(expired, expired.expires_at).status, "expired")

    def test_competing_base_revisions_apply_only_once(self):
        self.store.enqueue(self.request("first", enabled=True), self.now)
        self.store.enqueue(self.request("second", mode="shadow"), self.now)
        receipts = self.store.apply_pending(self.now)
        self.assertEqual([receipt.status for receipt in receipts], ["applied", "rejected"])
        self.assertTrue(self.store.settings().enabled)
        self.assertEqual(self.store.settings().revision, 1)

    def test_foreign_database_binding_and_unregistered_profiles_are_refused(self):
        request = self.request(enabled=True)
        with self.assertRaises(StoreError) as result:
            self.store.enqueue(replace(request, database_id="other-database"), self.now)
        self.assertEqual(result.exception.code, "identity_mismatch")
        self.store.enqueue(self.request(ai_enabled=True, ai_profile="unknown"), self.now)
        self.assertEqual(self.store.apply_pending(self.now)[0].status, "rejected")
        self.assertEqual(self.store.settings().revision, 0)
        self.store.enqueue(self.request("registered", ai_enabled=True, ai_profile="local"), self.now)
        self.assertEqual(self.store.apply_pending(self.now, profiles=("local",))[0].status, "applied")

    def test_missing_and_foreign_storage_are_never_initialized_by_reader(self):
        missing = Path(self.directory.name) / "missing.sqlite3"
        with self.assertRaises(StoreError):
            IntelligenceStore(missing)
        self.assertFalse(missing.exists())
        foreign = Path(self.directory.name) / "foreign.sqlite3"
        with closing(sqlite3.connect(foreign)) as handle:
            handle.execute("CREATE TABLE foreign_data(value)")
            handle.commit()
        before = foreign.read_bytes()
        with self.assertRaises(StoreError) as result:
            initialize(foreign, self.now)
        self.assertEqual(result.exception.code, "foreign_database")
        self.assertEqual(foreign.read_bytes(), before)
        with self.assertRaises(ValueError):
            initialize(Path(self.directory.name) / "bot_ledger.sqlite3", self.now)

    def test_calendar_publish_checks_effective_settings_and_revision(self):
        snapshot = CalendarSnapshot(1, self.now, (), ())
        with self.assertRaises(StoreError) as result:
            self.store.publish_calendar(snapshot, 0, self.now)
        self.assertEqual(result.exception.code, "feature_disabled")
        self.store.enqueue(self.request(enabled=True), self.now)
        self.store.apply_pending(self.now)
        self.store.publish_calendar(snapshot, 1, self.now)
        self.assertEqual(self.store.calendar(), snapshot)
        with self.assertRaises(StoreError) as result:
            self.store.publish_calendar(snapshot, 1, self.now)
        self.assertEqual(result.exception.code, "snapshot_conflict")
        self.store.enqueue(self.request("off", base_revision=1, enabled=False), self.now)
        self.store.apply_pending(self.now)
        with self.assertRaises(StoreError) as result:
            self.store.publish_calendar(replace(snapshot, revision=2), 1, self.now)
        self.assertEqual(result.exception.code, "settings_changed")
        self.assertEqual(self.store.calendar(), snapshot)

    def test_failed_control_transaction_publishes_no_settings_or_acknowledgement(self):
        request = self.request(enabled=True)
        self.store.enqueue(request, self.now)
        with patch.object(self.store, "_event", side_effect=sqlite3.OperationalError("injected failure")):
            with self.assertRaises(StoreError):
                self.store.apply_pending(self.now)
        reopened = IntelligenceStore(self.path)
        self.assertEqual(reopened.settings(), IntelligenceSettings())
        self.assertEqual(reopened.receipt(request.request_id).status, "pending")
        self.assertEqual(reopened.apply_pending(self.now)[0].status, "applied")

    def test_history_is_append_only_and_current_payload_corruption_is_refused(self):
        self.store.enqueue(self.request(enabled=True), self.now)
        self.store.apply_pending(self.now)
        with closing(sqlite3.connect(self.path)) as handle:
            for table in ("settings_history", "command_events"):
                with self.assertRaises(sqlite3.IntegrityError):
                    handle.execute(f"DELETE FROM {table}")
                handle.rollback()
            handle.execute("UPDATE commands SET receipt='{}' WHERE request_id='request-1'")
            handle.commit()
        with self.assertRaises(StoreError) as result:
            self.store.receipt("request-1")
        self.assertEqual(result.exception.code, "storage_invalid")


class IntelligenceServiceStatusTests(IntelligenceStoreFixture, unittest.TestCase):
    def observe(self, observation_id="observation-1", now=None):
        return self.store.observe_service(observation_id, "service-1", 0, now or self.now,
                                          False, ("bls", "bea"), False, False, ())

    def test_observations_are_append_only_idempotent_and_revision_bound(self):
        original = self.observe()
        self.assertEqual(self.observe(), original)
        with self.assertRaises(StoreError) as result:
            self.observe(now=self.now + timedelta(seconds=1))
        self.assertEqual(result.exception.code, "observation_conflict")
        with closing(sqlite3.connect(self.path)) as handle:
            for statement in ("UPDATE service_observations SET payload='{}'",
                              "DELETE FROM service_observations",
                              "INSERT OR REPLACE INTO service_observations SELECT * FROM service_observations"):
                with self.assertRaises(sqlite3.IntegrityError):
                    handle.execute(statement)
                handle.rollback()
        before = self.path.read_bytes()
        self.assertEqual(IntelligenceStore(self.path).service_status(), original)
        self.assertEqual(self.path.read_bytes(), before)
        self.store.enqueue(self.request(enabled=True), self.now)
        self.store.apply_pending(self.now)
        self.assertEqual(service_status_state(original, self.store.settings(), self.now), "superseded")
        with self.assertRaises(StoreError) as result:
            self.observe("old-revision")
        self.assertEqual(result.exception.code, "settings_changed")

    def test_freshness_and_unregistered_failures_remain_explicit(self):
        status = self.observe()
        settings = self.store.settings()
        self.assertEqual(service_status_state(None, settings, self.now), "unknown")
        self.assertEqual(service_status_state(status, settings, self.now - timedelta(microseconds=1)), "future")
        self.assertEqual(service_status_state(status, settings, self.now + timedelta(seconds=600)), "recent")
        self.assertEqual(service_status_state(status, settings, self.now + timedelta(seconds=600, microseconds=1)), "stale")
        with self.assertRaises(ValueError):
            ServiceFailure("analysis", "private-debug-detail", self.now)

    def test_failure_cannot_be_recorded_for_disabled_settings(self):
        self.store.enqueue(self.request(enabled=True, calendar_enabled=False), self.now)
        self.store.apply_pending(self.now)
        failure = ServiceFailure("analysis", "provider_unavailable", self.now)
        with self.assertRaises(StoreError):
            self.store.observe_service("invalid", "service-1", 1, self.now, True, ("bls",),
                                       True, True, (failure,))
        self.assertIsNone(self.store.service_status())

    def test_control_cannot_be_revived_before_a_later_service_observation(self):
        self.store.enqueue(self.request(enabled=True), self.now)
        later = self.now + timedelta(minutes=6)
        self.observe(now=later)
        with self.assertRaises(StoreError) as result:
            self.store.apply_pending(self.now + timedelta(minutes=1))
        self.assertEqual(result.exception.code, "clock_invalid")
        self.assertEqual(self.store.receipt("request-1").status, "pending")
        self.assertFalse(self.store.settings().enabled)
        self.assertEqual(self.store.apply_pending(later)[0].status, "expired")

    def test_failed_observation_commit_is_rolled_back(self):
        original_connect = sqlite3.connect

        class FailingCommit(sqlite3.Connection):
            def commit(self):
                raise sqlite3.OperationalError("injected failure")

        with patch("market_intelligence.store.sqlite3.connect",
                   side_effect=lambda *args, **kwargs: original_connect(*args, **kwargs, factory=FailingCommit)):
            with self.assertRaises(StoreError):
                self.observe()
        self.assertIsNone(self.store.service_status())

    def test_corrupted_observation_id_is_an_error_not_an_unknown_status(self):
        self.observe()
        with closing(sqlite3.connect(self.path)) as handle:
            handle.execute("DROP TRIGGER immutable_service_observations_update")
            handle.execute("PRAGMA ignore_check_constraints=ON")
            handle.execute("UPDATE service_observations SET observation_id=?", ("x" * 129,))
            handle.execute(storage_schema()[("trigger", "immutable_service_observations_update")])
            handle.commit()
        before = self.path.read_bytes()
        with self.assertRaises(StoreError) as result:
            self.store.service_status()
        self.assertEqual(result.exception.code, "storage_invalid")
        self.assertEqual(self.path.read_bytes(), before)

    def test_status_failure_does_not_claim_committed_settings_rolled_back(self):
        self.store.enqueue(self.request(enabled=True), self.now)
        service = IntelligenceService(self.store, clock=lambda: self.now)
        with self.assertLogs("market_intelligence.service", level="ERROR") as logs:
            with patch.object(self.store, "observe_service", side_effect=StoreError("private-debug-detail")):
                with self.assertRaises(StoreError):
                    with service:
                        service.step()
        self.assertTrue(self.store.settings().enabled)
        self.assertEqual(self.store.receipt("request-1").status, "applied")
        self.assertIsNone(self.store.service_status())
        self.assertIn("may already be committed", logs.output[0])
        self.assertNotIn("private-debug-detail", logs.output[0])

    def test_status_read_failure_warns_about_previously_committed_settings(self):
        self.store.enqueue(self.request(enabled=True), self.now)
        service = IntelligenceService(self.store, clock=lambda: self.now)
        with self.assertLogs("market_intelligence.service", level="ERROR") as logs:
            with patch.object(self.store, "service_status", side_effect=StoreError("private-debug-detail")):
                with self.assertRaises(StoreError):
                    with service:
                        service.step()
        self.assertTrue(self.store.settings().enabled)
        self.assertEqual(self.store.receipt("request-1").status, "applied")
        self.assertIn("may already be committed", logs.output[0])
        self.assertNotIn("private-debug-detail", logs.output[0])
        output = io.StringIO()
        with patch.object(IntelligenceService, "step", side_effect=StoreError("private-debug-detail")):
            with redirect_stdout(output):
                self.assertEqual(main(["once", "--database", str(self.path)]), 1)
        self.assertIn("may already be committed", output.getvalue())
        self.assertNotIn("private-debug-detail", output.getvalue())

    def test_idle_service_observations_are_rate_limited(self):
        self.elapsed = 0.0
        with IntelligenceService(self.store, clock=lambda: self.now, monotonic=lambda: self.elapsed) as service:
            service.step()
            first = self.store.service_status()
            self.now += timedelta(seconds=299)
            self.elapsed = 299.0
            service.step()
            self.assertEqual(self.store.service_status(), first)
            self.now += timedelta(seconds=1)
            self.elapsed = 300.0
            service.step()
            second = self.store.service_status()
        self.assertEqual(second.revision, first.revision + 1)
        self.assertEqual(second.service_id, first.service_id)
        self.assertFalse(second.network_permitted)

    @unittest.skipIf(importlib.util.find_spec("jsonschema") is None, "Install dashboard development dependencies")
    def test_service_observation_matches_wire_schema(self):
        from jsonschema import Draft202012Validator, FormatChecker
        schema = json.loads((Path(__file__).resolve().parents[1] / "market_intelligence" / "schema.json").read_text())
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        payload = json.loads(encode(self.observe()))
        validator.validate(payload)
        payload["failures"] = [{"source": "analysis", "error_code": "provider_unavailable",
                                "observed_at": payload["observed_at"]}]
        self.assertFalse(validator.is_valid(payload))


class IntelligenceServiceTests(IntelligenceStoreFixture, unittest.TestCase):
    def test_invalid_identity_slot_is_a_sanitized_cli_failure(self):
        with closing(sqlite3.connect(self.path)) as handle:
            handle.execute("PRAGMA ignore_check_constraints=ON")
            handle.execute("UPDATE identity SET id=2 WHERE id=1")
            handle.commit()
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(["status", "--database", str(self.path)]), 1)

    def test_initialize_connect_failure_is_sanitized_by_cli(self):
        path = Path(self.directory.name) / "connect-failed.sqlite3"
        with patch("market_intelligence.store.sqlite3.connect", side_effect=sqlite3.OperationalError("private details")):
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["initialize", "--database", str(path)]), 1)
        self.assertNotIn("private details", output.getvalue())

    def test_service_steps_require_process_ownership(self):
        service = IntelligenceService(self.store, clock=lambda: self.now)
        with self.assertRaises(StoreError) as result:
            service.step()
        self.assertEqual(result.exception.code, "service_not_owned")
        self.store.enqueue(self.request(enabled=True), self.now)
        with service:
            self.assertEqual(service.step()[0].status, "applied")
        with self.assertRaises(StoreError):
            service.step()

    def test_second_service_cannot_own_the_same_database(self):
        first = IntelligenceService(self.store, clock=lambda: self.now)
        second = IntelligenceService(self.store, clock=lambda: self.now)
        with first:
            with self.assertRaises(StoreError):
                with second:
                    self.fail("Second process owner unexpectedly acquired lock")
        with second:
            self.assertEqual(second.step(), ())

    def test_service_releases_lock_when_processing_fails(self):
        service = IntelligenceService(self.store, clock=lambda: self.now)
        with patch.object(self.store, "apply_pending", side_effect=StoreError("injected")):
            with self.assertRaises(StoreError):
                with service:
                    service.step()
        with IntelligenceService(self.store, clock=lambda: self.now) as reopened:
            self.assertEqual(reopened.step(), ())

    def test_cli_status_is_honest_and_does_not_create_missing_storage(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["status", "--database", str(self.path)]), 0)
        status = json.loads(output.getvalue())
        self.assertFalse(status["settings"]["enabled"])
        self.assertFalse(status["trading_authority"])
        self.assertFalse(status["providers_connected"])
        missing = Path(self.directory.name) / "absent.sqlite3"
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(["status", "--database", str(missing)]), 1)
        self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()