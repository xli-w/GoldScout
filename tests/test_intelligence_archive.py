from contextlib import closing
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import importlib.util
import hashlib
import io
import json
import tempfile
import unittest
from unittest.mock import patch

from dashboard.intelligence import IntelligenceWorkspace
from market_intelligence.analysis import AnalysisLimits, analysis_input
from market_intelligence.contracts import CalendarSnapshot, ControlRequest, IntelligenceSettings
from market_intelligence.store import IntelligenceStore, StoreError, digest, encode, initialize, storage_schema, upgrade
from market_intelligence.news import parse_fed_news
from market_intelligence.service import IntelligenceService
from market_intelligence.__main__ import main
from test_intelligence_news import feed
from test_intelligence_controls import IntelligenceStoreFixture


class IntelligenceArchiveMigrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "legacy.sqlite3"
        self.now = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
        self.store = initialize(self.path, self.now, schema_version=1)

    def old_records(self):
        with closing(sqlite3.connect(self.path)) as handle:
            return {table: handle.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
                    for table in ("identity", "settings_history", "settings_head", "commands", "command_events", "calendar_history")}

    def test_version_one_readers_do_not_upgrade_or_change_storage(self):
        before = {path.name: path.read_bytes() for path in self.path.parent.iterdir()}
        self.assertEqual(IntelligenceStore(self.path).settings(), IntelligenceSettings())
        self.assertEqual(self.old_records()["settings_head"], [(1, 0)])
        self.assertEqual({path.name: path.read_bytes() for path in self.path.parent.iterdir()}, before)
        with closing(sqlite3.connect(self.path)) as handle:
            self.assertEqual(handle.execute("PRAGMA user_version").fetchone()[0], 1)

    def test_legacy_schema_manifests_are_frozen(self):
        expected = {1: "0e99b0e316365c156feac990ed2db0c370327e520a89dc0d002d89b6b3137ddf",
                    2: "36425afe9032cc32262844ffcdf4290a54105da5bbb74263f0b4111dbfe1143f",
                    3: "48747f05f2794e6d30b090dfa6772fcc5faa53e8f052a0eace4df0f1814ca87e",
                    4: "f87d0146ff210fba0725a9b713630fbdcb9e1de357b5fee5a1f73dcabf0331b0"}
        for version, checksum in expected.items():
            payload = json.dumps(sorted((kind, name, sql) for (kind, name), sql in storage_schema(version).items()), separators=(",", ":"))
            self.assertEqual(hashlib.sha256(payload.encode()).hexdigest(), checksum)

    def test_upgrade_preserves_identity_settings_receipts_and_calendar(self):
        settings = IntelligenceSettings(enabled=True, revision=1)
        request = ControlRequest("enable", self.store.database_id, 0, self.now,
                                 self.now + timedelta(minutes=5), settings)
        self.store.enqueue(request, self.now)
        receipt = self.store.apply_pending(self.now)[0]
        snapshot = CalendarSnapshot(1, self.now, (), ())
        self.store.publish_calendar(snapshot, 1, self.now)
        before = self.old_records()
        upgraded = upgrade(self.path)
        self.assertEqual(self.old_records(), before)
        self.assertEqual(upgraded.database_id, self.store.database_id)
        self.assertEqual(upgraded.settings(), settings)
        self.assertEqual(upgraded.receipt("enable"), receipt)
        self.assertEqual(upgraded.calendar(), snapshot)
        self.assertEqual(upgrade(self.path).database_id, upgraded.database_id)

    def test_failed_upgrade_rolls_back_all_schema_changes(self):
        with patch("market_intelligence.store.news_storage_schema", return_value={
                ("table", "news_payloads"): "CREATE TABLE news_payloads(value)",
                ("table", "invalid"): "NOT VALID SQL"}):
            with self.assertRaises(StoreError):
                upgrade(self.path)
        self.assertEqual(self.store.settings(), IntelligenceSettings())
        with closing(sqlite3.connect(self.path)) as handle:
            self.assertEqual(handle.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertIsNone(handle.execute("SELECT name FROM sqlite_master WHERE name='news_payloads'").fetchone())

    def test_unknown_or_foreign_storage_is_not_upgraded(self):
        with closing(sqlite3.connect(self.path)) as handle:
            handle.execute("PRAGMA user_version=6")
        before = self.path.read_bytes()
        with self.assertRaises(StoreError):
            upgrade(self.path)
        self.assertEqual(self.path.read_bytes(), before)

    def test_upgrade_requires_service_ownership_and_cli_upgrade_is_explicit(self):
        with IntelligenceService(self.store):
            with self.assertRaises(StoreError):
                upgrade(self.path)
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["status", "--database", str(self.path)]), 0)
        self.assertEqual(json.loads(output.getvalue())["schema_version"], 1)
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["upgrade", "--database", str(self.path)]), 0)
        self.assertEqual(json.loads(output.getvalue())["schema_version"], 5)
        self.assertEqual(IntelligenceStore(self.path).database_id, self.store.database_id)

    def test_old_schema_news_access_refuses_without_implicit_upgrade(self):
        with self.assertRaises(StoreError) as result:
            self.store.news_acquisition("missing")
        self.assertEqual(result.exception.code, "upgrade_required")
        self.assertEqual(IntelligenceStore(self.path).schema_version, 1)


@unittest.skipIf(importlib.util.find_spec("defusedxml") is None, "Install requirements-intelligence.txt for archive parser tests")
class IntelligenceNewsArchiveTests(IntelligenceStoreFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.raw = feed()
        self.batch = parse_fed_news(self.raw, self.now)
        self.store.enqueue(self.request(enabled=True), self.now)
        self.store.apply_pending(self.now)

    def test_archive_roundtrip_preserves_raw_and_normalized_evidence(self):
        record = self.store.publish_news("fetch-1", self.batch, self.raw, 1, self.now)
        self.assertEqual(record.revision, 1)
        self.assertEqual(record.available_at, self.now)
        reopened = IntelligenceStore(self.path)
        self.assertEqual(reopened.news_acquisition("fetch-1"), record)
        self.assertEqual(reopened.news_body("fetch-1"), self.raw)
        self.assertEqual(reopened.news_evidence(self.batch.items[0].evidence_id), self.batch.items[0])

    def test_populated_version_two_upgrade_preserves_raw_and_normalized_history(self):
        path = Path(self.directory.name) / "version-two.sqlite3"
        old = initialize(path, self.now, schema_version=2)
        request = ControlRequest("enable", old.database_id, 0, self.now, self.now + timedelta(minutes=5),
                                 IntelligenceSettings(enabled=True, revision=1))
        old.enqueue(request, self.now)
        old.apply_pending(self.now)
        record = old.publish_news("fetch-1", self.batch, self.raw, 1, self.now)
        with self.assertRaises(StoreError):
            old.news_status()
        with closing(sqlite3.connect(path)) as handle:
            before = {table: handle.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
                      for table in ("news_payloads", "news_items", "news_acquisitions")}
        migrated = upgrade(path)
        self.assertEqual(migrated.schema_version, 5)
        self.assertEqual(migrated.news_acquisition("fetch-1"), record)
        self.assertEqual(migrated.news_body("fetch-1"), self.raw)
        self.assertIsNone(migrated.news_status())
        before_read = path.read_bytes()
        with patch("dashboard.intelligence.datetime") as clock:
            clock.now.return_value = self.now
            snapshot = IntelligenceWorkspace(path).snapshot()
        self.assertIsNone(snapshot["source_status"])
        self.assertEqual([item["evidence_id"] for item in snapshot["news"]],
                         [item.evidence_id for item in self.batch.items])
        self.assertEqual(snapshot["news_last_success_at"], self.now.isoformat().replace("+00:00", "Z"))
        self.assertEqual(path.read_bytes(), before_read)
        with closing(sqlite3.connect(path)) as handle:
            self.assertEqual({table: handle.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall() for table in before}, before)

    def test_populated_version_three_upgrade_preserves_source_observations(self):
        path=Path(self.directory.name)/"version-three.sqlite3"
        old=initialize(path,self.now,schema_version=3)
        request=ControlRequest("enable",old.database_id,0,self.now,self.now+timedelta(minutes=5),
                               IntelligenceSettings(enabled=True,revision=1))
        old.enqueue(request,self.now)
        old.apply_pending(self.now)
        status=old.complete_news_attempt("attempt-1",1,self.now,self.now,self.now,
                                         batch=self.batch,raw=self.raw)
        with closing(sqlite3.connect(path)) as handle:
            before={table:handle.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
                    for table in ("news_payloads","news_acquisitions","news_items","news_observations")}
        upgraded=upgrade(path)
        self.assertEqual(upgraded.schema_version,5)
        self.assertEqual(upgraded.news_status(),status)
        self.assertEqual(upgraded.news_body("attempt-1"),self.raw)
        with closing(sqlite3.connect(path)) as handle:
            self.assertEqual({table:handle.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall() for table in before},before)

    def test_version_four_requires_explicit_upgrade_and_preserves_analysis_reservations(self):
        path = Path(self.directory.name) / "version-four.sqlite3"
        old = initialize(path, self.now, schema_version=4)
        settings = IntelligenceSettings(enabled=True, ai_enabled=True, ai_profile="fixture", revision=1)
        old.enqueue(ControlRequest("enable", old.database_id, 0, self.now,
                                   self.now + timedelta(minutes=5), settings), self.now)
        old.apply_pending(self.now, profiles=("fixture",))
        status = old.complete_news_attempt("attempt-v4", 1, self.now, self.now, self.now,
                                           batch=self.batch, raw=self.raw)
        archive = old.news_acquisition(status.acquisition_id)
        cluster_id = self.batch.items[0].cluster_id
        _, size, input_hash = analysis_input(archive, cluster_id)
        context = {"acquisition_id": status.acquisition_id, "source_attempt_id": status.attempt_id,
                   "settings_revision": 1, "profile": "fixture", "model": "synthetic",
                   "cluster_id": cluster_id, "input_hash": input_hash, "prompt_version": "news-extractive-v1"}
        old.reserve_analysis(self.now, size, AnalysisLimits(), context)
        before = path.read_bytes()
        with self.assertRaises(StoreError) as result:
            old.service_status()
        self.assertEqual(result.exception.code, "upgrade_required")
        with patch("dashboard.intelligence.datetime") as clock:
            clock.now.return_value = self.now
            snapshot = IntelligenceWorkspace(path, profiles=("fixture",)).snapshot()
        self.assertFalse(snapshot["service_status_capable"])
        self.assertEqual(snapshot["service_status_state"], "unsupported")
        self.assertIsNone(snapshot["service_status"])
        self.assertEqual(snapshot["news_last_success_at"], self.now.isoformat().replace("+00:00", "Z"))
        self.assertEqual(path.read_bytes(), before)

        upgraded = upgrade(path)
        self.assertEqual(upgraded.schema_version, 5)
        self.assertEqual(upgraded.database_id, old.database_id)
        self.assertEqual(upgraded.news_status(), status)
        self.assertEqual(upgraded.news_body(status.acquisition_id), self.raw)
        self.assertEqual(upgraded.analysis_usage(self.now)["calls"], 1)
        self.assertIsNone(upgraded.service_status())

    def test_identical_retry_is_idempotent_and_conflicting_id_is_refused(self):
        original = self.store.publish_news("fetch-1", self.batch, self.raw, 1, self.now)
        self.assertEqual(self.store.publish_news("fetch-1", self.batch, self.raw, 1, self.now), original)
        raw = feed(description="Updated announcement")
        with self.assertRaises(StoreError) as result:
            self.store.publish_news("fetch-1", parse_fed_news(raw, self.now), raw, 1, self.now)
        self.assertEqual(result.exception.code, "acquisition_conflict")

    def test_repeat_observation_does_not_rewrite_first_receipt(self):
        self.store.publish_news("fetch-1", self.batch, self.raw, 1, self.now)
        later = self.now + timedelta(minutes=1)
        batch = parse_fed_news(self.raw, later)
        second = self.store.publish_news("fetch-2", batch, self.raw, 1, later)
        self.assertEqual(second.batch.items[0].received_at, later)
        self.assertEqual(self.store.news_evidence(batch.items[0].evidence_id).received_at, self.now)
        with closing(sqlite3.connect(self.path)) as handle:
            self.assertEqual(handle.execute("SELECT COUNT(*) FROM news_payloads").fetchone()[0], 1)
            self.assertEqual(handle.execute("SELECT COUNT(*) FROM news_items").fetchone()[0], 1)
            self.assertEqual(handle.execute("SELECT COUNT(*) FROM news_acquisitions").fetchone()[0], 2)

    def test_changed_news_preserves_both_versions_without_guessed_supersession(self):
        self.store.publish_news("fetch-1", self.batch, self.raw, 1, self.now)
        raw = feed(description="Updated announcement")
        batch = parse_fed_news(raw, self.now)
        self.store.publish_news("fetch-2", batch, raw, 1, self.now)
        self.assertNotEqual(self.batch.items[0].evidence_id, batch.items[0].evidence_id)
        self.assertEqual(self.store.news_evidence(self.batch.items[0].evidence_id), self.batch.items[0])
        self.assertIsNone(self.store.news_evidence(batch.items[0].evidence_id).supersedes)

    def test_raw_hash_and_normalized_payload_must_match(self):
        with self.assertRaises(ValueError):
            self.store.publish_news("fetch-1", self.batch, b"different", 1, self.now)
        item = replace(self.batch.items[0], snippet="Invented text")
        with self.assertRaises(ValueError):
            self.store.publish_news("fetch-1", replace(self.batch, items=(item,)), self.raw, 1, self.now)
        self.assertIsNone(self.store.news_acquisition("fetch-1"))

    def test_disabled_or_superseded_settings_block_new_archive_writes(self):
        self.store.enqueue(self.request("off", base_revision=1, enabled=False), self.now)
        self.store.apply_pending(self.now)
        with self.assertRaises(StoreError) as result:
            self.store.publish_news("fetch-1", self.batch, self.raw, 1, self.now)
        self.assertEqual(result.exception.code, "settings_changed")
        with self.assertRaises(StoreError) as result:
            self.store.publish_news("fetch-1", self.batch, self.raw, 2, self.now)
        self.assertEqual(result.exception.code, "feature_disabled")

    def test_failure_rolls_back_raw_items_and_acquisition_together(self):
        original = self.store._news
        calls = 0
        def fail_after_insert(handle, acquisition_id):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise StoreError("injected failure")
            return original(handle, acquisition_id)
        with patch.object(self.store, "_news", side_effect=fail_after_insert):
            with self.assertRaises(StoreError):
                self.store.publish_news("fetch-1", self.batch, self.raw, 1, self.now)
        with closing(sqlite3.connect(self.path)) as handle:
            for table in ("news_payloads", "news_items", "news_acquisitions"):
                self.assertEqual(handle.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)

    def test_archive_evidence_cannot_be_updated_deleted_or_replaced(self):
        self.store.publish_news("fetch-1", self.batch, self.raw, 1, self.now)
        with closing(sqlite3.connect(self.path)) as handle:
            for table in ("news_payloads", "news_items", "news_acquisitions"):
                for statement in (f"DELETE FROM {table}", f"INSERT OR REPLACE INTO {table} SELECT * FROM {table}"):
                    with self.assertRaises(sqlite3.IntegrityError):
                        handle.execute(statement)
                    handle.rollback()
        self.assertEqual(self.store.news_body("fetch-1"), self.raw)

    def test_archive_reads_are_directory_preserving_and_missing_ids_are_unknown(self):
        self.store.publish_news("fetch-1", self.batch, self.raw, 1, self.now)
        before = {path.name: path.read_bytes() for path in self.path.parent.iterdir()}
        self.assertIsNone(self.store.news_acquisition("missing"))
        self.assertIsNone(self.store.news_evidence("missing"))
        self.assertIsNone(self.store.news_body("missing"))
        self.store.news_acquisition("fetch-1")
        self.store.news_body("fetch-1")
        self.store.news_evidence(self.batch.items[0].evidence_id)
        self.assertEqual({path.name: path.read_bytes() for path in self.path.parent.iterdir()}, before)

    def test_publication_time_cannot_precede_receipt_or_prior_commit(self):
        with self.assertRaises(ValueError):
            self.store.publish_news("fetch-1", self.batch, self.raw, 1, self.now - timedelta(seconds=1))
        self.store.publish_news("fetch-1", self.batch, self.raw, 1, self.now + timedelta(minutes=1))
        with self.assertRaises(StoreError) as result:
            self.store.publish_news("fetch-2", self.batch, self.raw, 1, self.now)
        self.assertEqual(result.exception.code, "clock_invalid")

    def test_maximum_raw_body_and_two_hundred_item_batch_roundtrip(self):
        padding = 1048576 - len(self.raw) - 7
        maximum = self.raw.replace(b"</rss>", b"<!--" + b"x" * padding + b"--></rss>")
        self.assertEqual(len(maximum), 1048576)
        batch = parse_fed_news(maximum, self.now)
        self.store.publish_news("max-body", batch, maximum, 1, self.now)
        self.assertEqual(self.store.news_body("max-body"), maximum)
        items = []
        for index in range(200):
            item = feed(link=f"https://www.federalreserve.gov/newsevents/pressreleases/monetary20261113a{index}.htm")
            items.append(b"<item>" + item.split(b"<item>", 1)[1].split(b"</item>", 1)[0] + b"</item>")
        raw = b"<rss version='2.0'><channel>" + b"".join(items) + b"</channel></rss>"
        batch = parse_fed_news(raw, self.now)
        self.store.publish_news("max-items", batch, raw, 1, self.now)
        self.assertEqual(len(self.store.news_acquisition("max-items").batch.items), 200)

    def test_commit_failure_returns_no_acknowledgement_and_retry_succeeds(self):
        fail = True
        class CommitFailureConnection(sqlite3.Connection):
            def commit(self):
                nonlocal fail
                if fail:
                    fail = False
                    raise sqlite3.OperationalError("injected commit failure")
                return super().commit()
        connect = sqlite3.connect
        with patch("market_intelligence.store.sqlite3.connect",
                   side_effect=lambda *args, **kwargs: connect(*args, **kwargs, factory=CommitFailureConnection)):
            with self.assertRaises(StoreError):
                self.store.publish_news("fetch-1", self.batch, self.raw, 1, self.now)
            self.assertIsNone(self.store.news_acquisition("fetch-1"))
            self.assertEqual(self.store.publish_news("fetch-1", self.batch, self.raw, 1, self.now).revision, 1)

    def corrupt(self, table, statement, parameters):
        name = f"immutable_{table}_update"
        with closing(sqlite3.connect(self.path)) as handle:
            handle.execute(f"DROP TRIGGER {name}")
            handle.execute(statement, parameters)
            handle.execute(storage_schema(2)[("trigger", name)])
            handle.commit()

    def test_corrupted_raw_body_is_rejected_by_all_archive_readers(self):
        self.store.publish_news("fetch-1", self.batch, self.raw, 1, self.now)
        self.corrupt("news_payloads", "UPDATE news_payloads SET body=?", (b"different raw bytes",))
        for method, argument in ((self.store.news_acquisition, "fetch-1"), (self.store.news_body, "fetch-1"),
                                 (self.store.news_evidence, self.batch.items[0].evidence_id)):
            with self.assertRaises(StoreError):
                method(argument)

    def test_recomputed_metadata_hash_does_not_replace_raw_evidence(self):
        self.store.publish_news("fetch-1", self.batch, self.raw, 1, self.now)
        item = replace(self.batch.items[0], snippet="Invented replacement")
        payload = encode(replace(self.batch, items=(item,)))
        self.corrupt("news_acquisitions", "UPDATE news_acquisitions SET payload=?,payload_hash=?",
                     (payload, digest(payload)))
        with self.assertRaises(StoreError):
            self.store.news_acquisition("fetch-1")

    def test_wrong_first_acquisition_link_is_rejected_by_evidence_reader(self):
        self.store.publish_news("fetch-1", self.batch, self.raw, 1, self.now)
        raw = feed(description="Different article observation")
        self.store.publish_news("fetch-2", parse_fed_news(raw, self.now), raw, 1, self.now)
        self.corrupt("news_items", "UPDATE news_items SET first_acquisition_id=? WHERE evidence_id=?",
                     ("fetch-2", self.batch.items[0].evidence_id))
        with self.assertRaises(StoreError):
            self.store.news_evidence(self.batch.items[0].evidence_id)


if __name__ == "__main__":
    unittest.main()