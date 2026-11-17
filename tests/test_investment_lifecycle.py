"""Investment backup and restore checks using synthetic temporary stores only."""

import io
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from investment import InvestmentError
from investment.__main__ import main
from investment.service import demo_events, demo_profile, snapshot
from investment.store import import_events, initialize, trigger_sql
from test_investment_opening import event, opening, profile


NOW = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)


class InvestmentLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_backup_and_restore_preserve_v1_and_v2_replay(self):
        from investment.lifecycle import copy_verified

        for version in (1, 2):
            with self.subTest(version=version):
                source = self.root / f"source-{version}.sqlite3"
                backup = self.root / f"backup-{version}.sqlite3"
                restored = self.root / f"restored-{version}.sqlite3"
                spec = demo_profile(NOW) if version == 1 else profile()
                events = demo_events(NOW) if version == 1 else [opening()]
                initialize(source, spec, NOW)
                import_events(source, events, NOW)
                original = source.read_bytes()
                receipt = copy_verified(source, backup, NOW)
                recovered = copy_verified(backup, restored, NOW)
                self.assertEqual(receipt, recovered)
                self.assertEqual(receipt["schema_version"], version)
                self.assertEqual(receipt["capacity"]["event_count"], len(events))
                self.assertEqual(receipt["capacity"]["copy_size_bytes"], backup.stat().st_size)
                self.assertGreater(receipt["capacity"]["evidence_bytes"], 0)
                self.assertEqual(snapshot(source, NOW), snapshot(backup, NOW))
                self.assertEqual(snapshot(source, NOW), snapshot(restored, NOW))
                self.assertEqual(source.read_bytes(), original)
                self.assertEqual(backup.read_bytes(), restored.read_bytes())
                if version == 2:
                    import_events(restored, [event("after-restore", "interest", NOW, "bank",
                                                   amount_gbp="1")], NOW)
                    self.assertEqual(snapshot(restored, NOW)["revision"], receipt["revision"] + 1)
                    self.assertEqual(snapshot(source, NOW)["revision"], receipt["revision"])

    def test_refuses_invalid_source_and_existing_destination(self):
        from investment.lifecycle import copy_verified

        source = self.root / "source.sqlite3"
        target = self.root / "target.sqlite3"
        initialize(source, profile(), NOW)
        target.write_bytes(b"keep existing")
        with self.assertRaises(InvestmentError):
            copy_verified(source, target, NOW)
        self.assertEqual(target.read_bytes(), b"keep existing")
        with self.assertRaises(InvestmentError):
            copy_verified(source, source, NOW)
        invalid = self.root / "invalid.sqlite3"
        with closing(sqlite3.connect(invalid)) as connection:
            connection.execute("CREATE TABLE unrelated (value TEXT)")
        new = self.root / "new.sqlite3"
        with self.assertRaises(InvestmentError):
            copy_verified(invalid, new, NOW)
        self.assertFalse(new.exists())

    def test_copy_failure_removes_only_new_incomplete_destination(self):
        from investment.lifecycle import copy_verified

        source = self.root / "source.sqlite3"
        target = self.root / "target.sqlite3"
        initialize(source, profile(), NOW)
        original = source.read_bytes()
        with patch("investment.lifecycle._copy_database", side_effect=sqlite3.OperationalError("synthetic failure")):
            with self.assertRaises(InvestmentError):
                copy_verified(source, target, NOW)
        self.assertFalse(target.exists())
        self.assertEqual(source.read_bytes(), original)

    def test_invalid_copied_journal_is_removed_and_source_is_unchanged(self):
        from investment.lifecycle import copy_verified

        source = self.root / "source.sqlite3"
        target = self.root / "target.sqlite3"
        initialize(source, profile(), NOW)
        original = source.read_bytes()

        def copy_then_corrupt(source_connection, target_connection):
            source_connection.backup(target_connection)
            target_connection.execute("INSERT INTO commits VALUES (999, 'invalid')")
            target_connection.commit()

        with patch("investment.lifecycle._copy_database", side_effect=copy_then_corrupt):
            with self.assertRaises(InvestmentError):
                copy_verified(source, target, NOW)
        self.assertFalse(target.exists())
        self.assertEqual(source.read_bytes(), original)

    def test_publication_failure_preserves_racing_destination(self):
        from investment.lifecycle import copy_verified

        source = self.root / "source.sqlite3"
        target = self.root / "target.sqlite3"
        initialize(source, profile(), NOW)
        original_link = os.link

        def create_racing_destination(temporary, destination):
            Path(destination).write_bytes(b"other file")
            return original_link(temporary, destination)

        with patch("investment.lifecycle.os.link", side_effect=create_racing_destination):
            with self.assertRaises(InvestmentError):
                copy_verified(source, target, NOW)
        self.assertEqual(target.read_bytes(), b"other file")
        self.assertEqual(sorted(path.name for path in self.root.iterdir()),
                         ["source.sqlite3", "target.sqlite3"])

    def test_copy_checks_commit_boundaries(self):
        from investment.lifecycle import copy_verified

        source = self.root / "source.sqlite3"
        target = self.root / "target.sqlite3"
        initialize(source, profile(), NOW)
        import_events(source, [opening()], NOW)
        import_events(source, [event("interest", "interest", NOW, "bank", amount_gbp="1")], NOW)

        def copy_with_missing_commit(source_connection, target_connection):
            source_connection.backup(target_connection)
            target_connection.execute("DROP TRIGGER immutable_commits_delete")
            target_connection.execute("DELETE FROM commits WHERE revision = 1")
            target_connection.execute(trigger_sql("commits", "DELETE"))
            target_connection.commit()

        with patch("investment.lifecycle._copy_database", side_effect=copy_with_missing_commit):
            with self.assertRaisesRegex(InvestmentError, "does not match"):
                copy_verified(source, target, NOW)
        self.assertFalse(target.exists())

    def test_receipt_distinguishes_valid_commit_histories(self):
        from investment.lifecycle import copy_verified

        one_batch = self.root / "one.sqlite3"
        two_batches = self.root / "two.sqlite3"
        interest = event("interest", "interest", NOW, "bank", amount_gbp="1")
        for source in (one_batch, two_batches):
            initialize(source, profile(), NOW)
        import_events(one_batch, [opening(), interest], NOW)
        import_events(two_batches, [opening()], NOW)
        import_events(two_batches, [interest], NOW)
        first = copy_verified(one_batch, self.root / "one-copy.sqlite3", NOW)
        second = copy_verified(two_batches, self.root / "two-copy.sqlite3", NOW)
        self.assertEqual(first["event_chain_hash"], second["event_chain_hash"])
        self.assertEqual(first["state_hash"], second["state_hash"])
        self.assertNotEqual(first["commit_history_hash"], second["commit_history_hash"])

    def test_interruption_and_sidecar_destinations_are_safe(self):
        from investment.lifecycle import copy_verified

        source = self.root / "source.sqlite3"
        target = self.root / "target.sqlite3"
        initialize(source, profile(), NOW)
        with patch("investment.lifecycle._copy_database", side_effect=KeyboardInterrupt):
            with self.assertRaisesRegex(InvestmentError, "interrupted"):
                copy_verified(source, target, NOW)
        self.assertEqual([path.name for path in self.root.iterdir()], ["source.sqlite3"])
        for suffix in ("-journal", "-wal", "-shm"):
            with self.subTest(suffix=suffix), self.assertRaisesRegex(InvestmentError, "sidecar"):
                copy_verified(source, str(source) + suffix, NOW)

    def test_existing_destination_sidecars_are_preserved(self):
        from investment.lifecycle import copy_verified

        source = self.root / "source.sqlite3"
        target = self.root / "target.sqlite3"
        initialize(source, profile(), NOW)
        for suffix in ("-journal", "-wal", "-shm"):
            sidecar = Path(str(target) + suffix)
            sidecar.write_bytes(b"existing sidecar")
            with self.subTest(suffix=suffix), self.assertRaisesRegex(InvestmentError, "sidecar"):
                copy_verified(source, target, NOW)
            self.assertFalse(target.exists())
            self.assertEqual(sidecar.read_bytes(), b"existing sidecar")
            sidecar.unlink()

    def test_sidecar_appearing_during_publication_is_reported(self):
        from investment.lifecycle import copy_verified

        source = self.root / "source.sqlite3"
        target = self.root / "target.sqlite3"
        sidecar = Path(str(target) + "-journal")
        initialize(source, profile(), NOW)
        original_link = os.link

        def publish_with_sidecar(temporary, destination):
            sidecar.write_bytes(b"racing sidecar")
            return original_link(temporary, destination)

        with patch("investment.lifecycle.os.link", side_effect=publish_with_sidecar):
            with self.assertRaisesRegex(InvestmentError, "Published copy.*sidecar"):
                copy_verified(source, target, NOW)
        self.assertTrue(target.exists())
        self.assertEqual(sidecar.read_bytes(), b"racing sidecar")

    def test_sidecar_and_cleanup_failures_are_both_reported(self):
        source = self.root / "source.sqlite3"
        target = self.root / "target.sqlite3"
        sidecar = Path(str(target) + "-journal")
        initialize(source, profile(), NOW)
        original_link = os.link
        output, error = io.StringIO(), io.StringIO()

        def publish_with_sidecar(temporary, destination):
            sidecar.write_bytes(b"racing sidecar")
            return original_link(temporary, destination)

        with patch("investment.lifecycle.os.link", side_effect=publish_with_sidecar), \
                patch("investment.lifecycle.Path.unlink", side_effect=OSError("cleanup failure")):
            status = main(["backup", "--database", str(source), "--destination", str(target)],
                          stdout=output, stderr=error, now=NOW)
        self.assertNotEqual(status, 0)
        self.assertIn("cleanup failed", error.getvalue())
        self.assertIn("SQLite sidecar detected", error.getvalue())
        self.assertIn("destination was published and requires inspection", error.getvalue())
        self.assertTrue(target.exists())
        self.assertEqual(sidecar.read_bytes(), b"racing sidecar")

    def test_malformed_source_error_is_reported_by_cli(self):
        source = self.root / "source.sqlite3"
        target = self.root / "target.sqlite3"
        initialize(source, profile(), NOW)
        output, error = io.StringIO(), io.StringIO()
        with patch("investment.lifecycle._read", side_effect=AttributeError("malformed evidence")):
            status = main(["backup", "--database", str(source), "--destination", str(target)],
                          stdout=output, stderr=error, now=NOW)
        self.assertNotEqual(status, 0)
        self.assertIn("Investment copy failed", error.getvalue())
        self.assertFalse(target.exists())

    def test_cleanup_warning_reaches_cli(self):
        source = self.root / "source.sqlite3"
        target = self.root / "target.sqlite3"
        initialize(source, profile(), NOW)
        output, error = io.StringIO(), io.StringIO()
        with patch("investment.lifecycle.Path.unlink", side_effect=OSError("synthetic failure")):
            status = main(["backup", "--database", str(source), "--destination", str(target)],
                          stdout=output, stderr=error, now=NOW)
        self.assertNotEqual(status, 0)
        self.assertIn("destination was published and requires inspection", error.getvalue())
        self.assertIn("inspect temporary directory", error.getvalue())
        self.assertTrue(target.exists())

    def test_uncommitted_source_write_is_not_in_the_copy(self):
        from investment.lifecycle import copy_verified

        source = self.root / "source.sqlite3"
        target = self.root / "target.sqlite3"
        initialize(source, profile(), NOW)
        import_events(source, [opening()], NOW)
        expected = snapshot(source, NOW)
        with closing(sqlite3.connect(source, timeout=1)) as writer:
            writer.execute("BEGIN IMMEDIATE")
            writer.execute("INSERT INTO commits VALUES (999, 'uncommitted')")
            receipt = copy_verified(source, target, NOW)
            writer.rollback()
        self.assertEqual(receipt["revision"], 1)
        self.assertEqual(snapshot(target, NOW), expected)

    def test_cli_backup_and_restore_require_new_explicit_paths(self):
        source = self.root / "source.sqlite3"
        backup = self.root / "backup.sqlite3"
        restored = self.root / "restored.sqlite3"
        initialize(source, profile(), NOW)
        import_events(source, [opening()], NOW)
        for action, origin, destination in (
                ("backup", source, backup), ("restore", backup, restored)):
            output, error = io.StringIO(), io.StringIO()
            status = main([action, "--database", str(origin), "--destination", str(destination)],
                          stdout=output, stderr=error, now=NOW)
            self.assertEqual(status, 0, error.getvalue())
            self.assertEqual(json.loads(output.getvalue())["revision"], 1)
        self.assertEqual(snapshot(source, NOW), snapshot(restored, NOW))
