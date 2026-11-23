from copy import deepcopy
from contextlib import closing
from datetime import timedelta
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from investment import InvestmentError
from investment.core import iso
from investment.monitoring_store import append_attempt, initialize_cache, purge_expired, read_cache
from test_investment_monitoring import monitoring_profile
from test_investment_opening import NOW, profile


def attempt():
    return {
        "id": "batch-1", "account_id": "broker", "provider": "etoro", "attempted_at": iso(NOW),
        "status": "success", "error_code": None,
        "observation": {
            "id": "source-revision-1", "account_id": "broker", "provider": "etoro",
            "observed_at": iso(NOW), "available_at": iso(NOW), "currency": "USD",
            "cash": "10", "holdings": [{"source_product": "synthetic", "quantity": "2"}],
            "coverage": "partial", "evidence": "synthetic observation",
        },
    }


class MonitoringStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "monitor.sqlite3"
        self.profile = monitoring_profile(profile())
        initialize_cache(self.path, self.profile, 86400)

    def test_initialization_preserves_known_and_uncertain_commits(self):
        from test_investment_opening import (
            CommitResultFailure, ConnectionCloseFailure, TransactionExitFailure,
        )

        connect = sqlite3.connect
        for failure in (ConnectionCloseFailure, TransactionExitFailure, CommitResultFailure):
            with self.subTest(failure=failure.__name__):
                path = Path(self.temporary.name) / f"{failure.__name__}.sqlite3"
                with patch("investment.monitoring_store.sqlite3.connect",
                           side_effect=lambda *args, **kwargs: failure(connect(*args, **kwargs))):
                    with self.assertRaises(InvestmentError) as raised:
                        initialize_cache(path, self.profile, 86400)
                expected = "uncertain" if failure is CommitResultFailure else "committed"
                self.assertIn(expected, str(raised.exception).lower())
                self.assertFalse(path.exists())
                staged = raised.exception.staged_path
                self.assertTrue(staged.exists())
                self.assertIn(str(staged), str(raised.exception))
                reopened = read_cache(staged, self.profile, NOW)
                self.assertEqual(reopened["revision"], 0)
                self.assertEqual(len(reopened["instance_id"]), 32)

    def test_initialization_removes_only_definite_precommit_failure(self):
        from test_investment_opening import ConnectionCloseFailure

        class BeforeCommitFailure(ConnectionCloseFailure):
            def execute(self, sql, *args):
                if sql.startswith("INSERT INTO monitor_identity"):
                    raise sqlite3.OperationalError("synthetic precommit failure")
                return self.connection.execute(sql, *args)

            def close(self):
                self.connection.close()

        class UnappliedCommitFailure(ConnectionCloseFailure):
            def commit(self):
                raise sqlite3.OperationalError("synthetic unknown commit outcome")

            def close(self):
                self.connection.close()

        connect = sqlite3.connect
        for failure, preserved in ((BeforeCommitFailure, False), (UnappliedCommitFailure, True)):
            with self.subTest(failure=failure.__name__):
                path = Path(self.temporary.name) / f"{failure.__name__}.sqlite3"
                before = set(path.parent.iterdir())
                with patch("investment.monitoring_store.sqlite3.connect",
                           side_effect=lambda *args, **kwargs: failure(connect(*args, **kwargs))):
                    with self.assertRaises(InvestmentError) as raised:
                        initialize_cache(path, self.profile, 86400)
                self.assertFalse(path.exists())
                if preserved:
                    self.assertIn("uncertain", str(raised.exception).lower())
                    self.assertTrue(raised.exception.staged_path.exists())
                    with self.assertRaises(InvestmentError):
                        read_cache(raised.exception.staged_path, self.profile, NOW)
                else:
                    self.assertEqual(set(path.parent.iterdir()), before)

    def test_initialization_preserves_staged_or_published_cache_on_publication_failures(self):
        from investment.monitoring_store import CommittedMonitoringError

        unlink = Path.unlink
        link = os.link

        def fail_staged_unlink(path, *args, **kwargs):
            if path.parent.name.startswith(".goldscout-monitor-"):
                raise OSError("synthetic staged link cleanup failure")
            return unlink(path, *args, **kwargs)

        def lose_publication_result(source, destination):
            link(source, destination)
            raise OSError("synthetic lost publication result")

        cases = (
            ("publication", patch("investment.monitoring_store.os.link",
                                  side_effect=OSError("synthetic publication failure")), False),
            ("uncertain-publication", patch("investment.monitoring_store.os.link",
                                             side_effect=lose_publication_result), True),
            ("cleanup", patch.object(Path, "unlink", autospec=True,
                                     side_effect=fail_staged_unlink), True),
        )
        for name, failure, published in cases:
            with self.subTest(name=name):
                path = Path(self.temporary.name) / f"{name}.sqlite3"
                with failure:
                    with self.assertRaises(CommittedMonitoringError) as raised:
                        initialize_cache(path, self.profile, 86400)
                staged = raised.exception.staged_path
                self.assertTrue(staged.exists())
                self.assertEqual(read_cache(staged, self.profile, NOW)["revision"], 0)
                self.assertEqual(path.exists(), published)
                if published:
                    self.assertEqual(read_cache(path, self.profile, NOW)["instance_id"],
                                     read_cache(staged, self.profile, NOW)["instance_id"])

    def test_initialization_does_not_change_substituted_existing_stores(self):
        from investment.store import initialize

        connect = sqlite3.connect
        for kind in ("journal", "cache"):
            with self.subTest(kind=kind):
                directory = Path(self.temporary.name) / kind
                directory.mkdir()
                target = directory / "new-cache.sqlite3"
                replacement = directory / "replacement.sqlite3"
                if kind == "journal":
                    initialize(replacement, profile(), NOW)
                else:
                    initialize_cache(replacement, self.profile, 86400)
                    append_attempt(replacement, self.profile, attempt(), NOW)
                original = replacement.read_bytes()

                def substitute(*args, **kwargs):
                    if target.exists():
                        target.unlink()
                    replacement.replace(target)
                    return connect(*args, **kwargs)

                with patch("investment.monitoring_store.sqlite3.connect", side_effect=substitute):
                    with self.assertRaises(InvestmentError):
                        initialize_cache(target, self.profile, 86400)
                self.assertTrue(target.exists())
                self.assertEqual(target.read_bytes(), original)

    def test_initialization_preserves_stage_when_published_cache_is_replaced(self):
        from investment import monitoring_store
        from investment.monitoring_store import CommittedMonitoringError

        target = Path(self.temporary.name) / "published.sqlite3"
        replacement = Path(self.temporary.name) / "replacement.sqlite3"
        initialize_cache(replacement, self.profile, 86400)
        append_attempt(replacement, self.profile, attempt(), NOW)
        original = replacement.read_bytes()
        remove = monitoring_store._remove_staging

        def substitute_before_cleanup(staged, identity, *args, **kwargs):
            target.unlink()
            replacement.replace(target)
            return remove(staged, identity, *args, **kwargs)

        with patch.object(monitoring_store, "_remove_staging",
                          side_effect=substitute_before_cleanup):
            with self.assertRaises(CommittedMonitoringError) as raised:
                initialize_cache(target, self.profile, 86400)
        staged = raised.exception.staged_path
        self.assertTrue(staged.is_file())
        self.assertEqual(read_cache(staged, self.profile, NOW)["revision"], 0)
        self.assertEqual(target.read_bytes(), original)

    def test_initialization_does_not_recreate_missing_staged_file(self):
        target = Path(self.temporary.name) / "missing-stage.sqlite3"
        connect = sqlite3.connect
        fdopen = os.fdopen
        handles = []
        before = set(target.parent.iterdir())

        def track_handle(*args, **kwargs):
            handle = fdopen(*args, **kwargs)
            handles.append(handle)
            return handle

        def remove_staged(*args, **kwargs):
            directories = [
                path for path in target.parent.iterdir()
                if path.name.startswith(".goldscout-monitor-")
            ]
            self.assertEqual(len(directories), 1)
            handles[0].close()
            (directories[0] / "cache.sqlite3").unlink()
            return connect(*args, **kwargs)

        with patch("investment.monitoring_store.os.fdopen", side_effect=track_handle), \
                patch("investment.monitoring_store.sqlite3.connect", side_effect=remove_staged):
            with self.assertRaises(InvestmentError):
                initialize_cache(target, self.profile, 86400)
        self.assertFalse(target.exists())
        self.assertEqual(set(target.parent.iterdir()), before)

    def test_initialization_preserves_substituted_staged_file(self):
        from investment.store import initialize

        target = Path(self.temporary.name) / "substituted-stage.sqlite3"
        replacement = Path(self.temporary.name) / "replacement.sqlite3"
        initialize(replacement, profile(), NOW)
        original = replacement.read_bytes()
        connect = sqlite3.connect
        fdopen = os.fdopen
        handles = []
        staged = []

        def track_handle(*args, **kwargs):
            handle = fdopen(*args, **kwargs)
            handles.append(handle)
            return handle

        def substitute(*args, **kwargs):
            directory = next(path for path in target.parent.iterdir()
                             if path.name.startswith(".goldscout-monitor-"))
            staged.append(directory / "cache.sqlite3")
            handles[0].close()
            staged[-1].unlink()
            replacement.replace(staged[-1])
            return connect(*args, **kwargs)

        with patch("investment.monitoring_store.os.fdopen", side_effect=track_handle), \
                patch("investment.monitoring_store.sqlite3.connect", side_effect=substitute):
            with self.assertRaises(InvestmentError):
                initialize_cache(target, self.profile, 86400)
        self.assertFalse(target.exists())
        preserved = replacement if replacement.exists() else staged[0]
        self.assertTrue(preserved.exists())
        self.assertEqual(preserved.read_bytes(), original)

    def test_initialization_cleanup_failure_reports_preserved_database_and_sidecar(self):
        from investment.monitoring_store import CommittedMonitoringError

        for add_sidecar in (False, True):
            with self.subTest(add_sidecar=add_sidecar):
                target = Path(self.temporary.name) / f"cleanup-{add_sidecar}.sqlite3"
                directories = []

                def fail_cleanup(path):
                    directories.append(path)
                    if add_sidecar:
                        (path / "cache.sqlite3-journal").write_bytes(b"synthetic sidecar")
                    raise OSError("synthetic directory cleanup failure")

                with patch.object(Path, "rmdir", autospec=True, side_effect=fail_cleanup):
                    with self.assertRaises(CommittedMonitoringError) as raised:
                        initialize_cache(target, self.profile, 86400)
                staged = raised.exception.staged_path
                self.assertTrue(staged.is_file())
                self.assertIn(str(staged), str(raised.exception))
                self.assertEqual(read_cache(staged, self.profile, NOW)["revision"], 0)
                self.assertEqual(read_cache(target, self.profile, NOW)["revision"], 0)
                if add_sidecar:
                    self.assertEqual((directories[0] / "cache.sqlite3-journal").read_bytes(),
                                     b"synthetic sidecar")

    def test_initialization_does_not_publish_when_staged_sidecar_remains(self):
        from investment.monitoring_store import CommittedMonitoringError
        from test_investment_opening import ConnectionCloseFailure

        target = Path(self.temporary.name) / "sidecar.sqlite3"
        connect = sqlite3.connect
        sidecars = []

        class SidecarAfterClose(ConnectionCloseFailure):
            def close(self):
                self.connection.close()
                directory = next(path for path in target.parent.iterdir()
                                 if path.name.startswith(".goldscout-monitor-"))
                sidecars.append(directory / "cache.sqlite3-journal")
                sidecars[-1].write_bytes(b"synthetic sidecar")

        with patch("investment.monitoring_store.sqlite3.connect",
                   side_effect=lambda *args, **kwargs: SidecarAfterClose(connect(*args, **kwargs))):
            with self.assertRaises(CommittedMonitoringError) as raised:
                initialize_cache(target, self.profile, 86400)
        self.assertFalse(target.exists())
        self.assertTrue(raised.exception.staged_path.is_file())
        self.assertEqual(sidecars[0].read_bytes(), b"synthetic sidecar")

    def test_cache_attempt_id_divergence_blocks_bound_append_and_reads(self):
        from investment.monitoring_store import StaleMonitoringCacheError

        append_attempt(self.path, self.profile, attempt(), NOW)
        later = attempt()
        later["id"] = "later-batch"
        later["observation"]["id"] = "later-observation"
        append_attempt(self.path, self.profile, later, NOW)
        cursor = read_cache(self.path, self.profile, NOW, include_cursor=True)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("UPDATE monitor_attempts SET id=? WHERE id=?",
                               ("different-sql-id", "batch-1"))
        original = self.path.read_bytes()
        next_attempt = deepcopy(later)
        next_attempt["id"] = "next-batch"
        next_attempt["observation"]["id"] = "next-observation"
        with self.assertRaises(StaleMonitoringCacheError):
            append_attempt(
                self.path, self.profile, next_attempt, NOW,
                expected_instance_id=cursor["instance_id"],
                expected_revision=cursor["revision"],
                expected_attempt_revision=cursor["next_revision"],
                expected_predecessor_hash=cursor["predecessor_hash"],
            )
        self.assertEqual(self.path.read_bytes(), original)
        with self.assertRaises(InvestmentError):
            read_cache(self.path, self.profile, NOW)
        with self.assertRaises(InvestmentError):
            append_attempt(self.path, self.profile, next_attempt, NOW)
        with self.assertRaises(InvestmentError):
            append_attempt(self.path, self.profile, later, NOW)
        with self.assertRaises(InvestmentError):
            purge_expired(self.path, self.profile, NOW + timedelta(days=1))
        self.assertEqual(self.path.read_bytes(), original)

    def test_restart_idempotency_conflicts_and_failed_collection(self):
        if os.name != "nt":
            self.assertEqual(self.path.stat().st_mode & 0o077, 0)
        value = attempt()
        self.assertEqual(append_attempt(self.path, self.profile, value, NOW), 1)
        self.assertEqual(append_attempt(self.path, self.profile, deepcopy(value), NOW), 1)
        changed = deepcopy(value)
        changed["observation"]["cash"] = "11"
        with self.assertRaises(InvestmentError):
            append_attempt(self.path, self.profile, changed, NOW)
        failed = {**value, "id": "failed", "status": "failed", "observation": None,
                  "error_code": "provider_unavailable"}
        append_attempt(self.path, self.profile, failed, NOW)
        reopened = read_cache(self.path, self.profile, NOW)
        self.assertEqual([a["status"] for a in reopened["attempts"]], ["success", "failed"])
        self.assertEqual(reopened["attempts"][0]["observation"]["coverage"], "partial")
        self.assertEqual(len(reopened["instance_id"]), 32)
        self.assertEqual(reopened["revision"], 2)

    def test_dashboard_publication_can_pin_cache_instance_and_revision(self):
        identity = read_cache(self.path, self.profile, NOW)
        value = attempt()
        with self.assertRaisesRegex(InvestmentError, "instance changed"):
            append_attempt(self.path, self.profile, value, NOW,
                           expected_instance_id="0" * 32, expected_revision=0)
        with self.assertRaisesRegex(InvestmentError, "revision changed"):
            append_attempt(self.path, self.profile, value, NOW,
                           expected_instance_id=identity["instance_id"], expected_revision=1)
        self.assertEqual(append_attempt(
            self.path, self.profile, value, NOW,
            expected_instance_id=identity["instance_id"], expected_revision=0), 1)
        self.assertEqual(append_attempt(
            self.path, self.profile, value, NOW,
            expected_instance_id=identity["instance_id"], expected_revision=0), 1)
        self.assertEqual(read_cache(self.path, self.profile, NOW)["revision"], 1)

    def test_reader_does_not_create_or_repair_foreign_or_missing_stores(self):
        missing = self.path.with_name("missing.sqlite3")
        with self.assertRaises(InvestmentError):
            read_cache(missing, self.profile, NOW)
        self.assertFalse(missing.exists())
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("CREATE TABLE foreign_table (value TEXT)")
        before = self.path.read_bytes()
        with self.assertRaises(InvestmentError):
            read_cache(self.path, self.profile, NOW)
        self.assertEqual(self.path.read_bytes(), before)

    def test_retention_and_binding_are_explicit_and_purge_is_separate(self):
        append_attempt(self.path, self.profile, attempt(), NOW)
        before = self.path.read_bytes()
        self.assertEqual(read_cache(self.path, self.profile, NOW + timedelta(days=1))["attempts"], [])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(purge_expired(self.path, self.profile, NOW + timedelta(days=1)), {"deleted_attempts": 1})
        after_purge = read_cache(self.path, self.profile, NOW + timedelta(days=1))
        self.assertEqual(after_purge["revision"], 1)
        self.assertEqual(after_purge["instance_id"], read_cache(self.path, self.profile, NOW)["instance_id"])
        later_attempt = attempt()
        later_attempt["id"] = "post-purge"
        later_clock = NOW + timedelta(days=2)
        later_attempt["attempted_at"] = iso(later_clock)
        later_attempt["observation"]["observed_at"] = iso(later_clock)
        later_attempt["observation"]["available_at"] = iso(later_clock)
        self.assertEqual(append_attempt(self.path, self.profile, later_attempt, later_clock), 2)
        self.assertEqual(read_cache(self.path, self.profile, later_clock)["revision"], 2)
        changed = deepcopy(self.profile)
        changed["accounts"][1]["evidence"] = "changed binding"
        with self.assertRaises(InvestmentError):
            read_cache(self.path, changed, NOW)

    def test_invalid_batch_never_publishes_success(self):
        for edit in (
            lambda v: v["observation"].update(currency="XXX"),
            lambda v: v["observation"].update(available_at=iso(NOW + timedelta(seconds=1))),
            lambda v: v["observation"].update(cash="NaN"),
            lambda v: v.update(provider="foreign"),
            lambda v: v.update(status="failed", error_code="secret exception text"),
        ):
            value = attempt()
            edit(value)
            with self.assertRaises(InvestmentError):
                append_attempt(self.path, self.profile, value, NOW)
        self.assertEqual(read_cache(self.path, self.profile, NOW)["revision"], 0)

    def test_commit_and_lock_failures_publish_no_batch(self):
        connect = sqlite3.connect

        class FailedCommit(sqlite3.Connection):
            def commit(self):
                raise sqlite3.OperationalError("synthetic commit failure")

        with patch("investment.monitoring_store.sqlite3.connect",
                   side_effect=lambda *args, **kwargs: connect(*args, **kwargs, factory=FailedCommit)):
            with self.assertRaises(InvestmentError):
                append_attempt(self.path, self.profile, attempt(), NOW)
        self.assertEqual(read_cache(self.path, self.profile, NOW)["revision"], 0)
        with closing(connect(self.path)) as blocker:
            blocker.execute("BEGIN EXCLUSIVE")
            with self.assertRaises(InvestmentError):
                append_attempt(self.path, self.profile, attempt(), NOW)
            blocker.rollback()
        self.assertEqual(read_cache(self.path, self.profile, NOW)["revision"], 0)

    def test_database_byte_limit_rolls_back_oversized_append(self):
        value = attempt()
        value["observation"]["mapping_available_at"] = iso(NOW)
        value["observation"]["product_mappings"] = [
            {"source_product": f"{index:03d}" + "s" * 197,
             "product_id": f"{index:03d}" + "t" * 197,
             "evidence": "e" * 200}
            for index in range(100)
        ]
        with closing(sqlite3.connect(self.path)) as connection:
            page_count = connection.execute("PRAGMA page_count").fetchone()[0]
            page_size = connection.execute("PRAGMA page_size").fetchone()[0]
        byte_limit = max(self.path.stat().st_size, page_count * page_size)
        with patch("investment.monitoring_store.MAX_CACHE_BYTES", byte_limit):
            with self.assertRaisesRegex(InvestmentError, "cache size limit"):
                append_attempt(self.path, self.profile, value, NOW)
        self.assertEqual(read_cache(self.path, self.profile, NOW)["revision"], 0)
        self.assertLessEqual(self.path.stat().st_size, byte_limit)
