"""Temporary SQLite persistence for the synthetic-only pilot recovery trace."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from investment import InvestmentError
import investment.pilot_store as pilot_store
from investment.pilot import simulate_pilot_trace
from investment.pilot_store import append_event, initialize, read
from test_investment_pilot import acknowledgement, attempt, intent, observation


class PilotStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.database = Path(self.temporary.name) / "pilot.sqlite3"

    def test_explicit_init_and_duplicate_event_retry_are_safe(self):
        initialize(self.database)
        event = intent()
        self.assertEqual(append_event(self.database, "event-1", event), 1)
        before = self.database.read_bytes()
        self.assertEqual(append_event(self.database, "event-1", deepcopy(event)), 1)
        self.assertEqual(self.database.read_bytes(), before)
        with self.assertRaisesRegex(InvestmentError, "Conflicting"):
            append_event(self.database, "event-1", attempt())
        self.assertEqual(read(self.database), [event])
        with self.assertRaisesRegex(InvestmentError, "new path"):
            initialize(self.database)

    def test_v2_event_hash_uses_canonical_pair_encoding(self):
        initialize(self.database)
        event_id = "event-1"
        append_event(self.database, event_id, intent())
        with closing(sqlite3.connect(self.database)) as connection:
            previous, payload, actual = connection.execute(
                "SELECT previous_hash,payload,hash FROM events WHERE revision=1"
            ).fetchone()
        pair = json.dumps([event_id, payload], sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False)
        expected = hashlib.sha256((previous + pair).encode("utf-8")).hexdigest()
        legacy = hashlib.sha256((previous + event_id + payload).encode("utf-8")).hexdigest()
        self.assertEqual(actual, expected)
        self.assertNotEqual(actual, legacy)

    def test_concurrent_identical_event_retries_commit_once(self):
        initialize(self.database)
        gate = Barrier(2)
        event = intent()

        def append():
            gate.wait()
            return append_event(self.database, "same-event", event)

        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(lambda _: append(), range(2)))
        self.assertEqual(results, [1, 1])
        self.assertEqual(read(self.database), [event])

    def test_unsupported_v1_journal_is_rejected_by_version(self):
        initialize(self.database)
        with closing(sqlite3.connect(self.database)) as connection:
            with connection:
                connection.execute("PRAGMA user_version=1")
        with self.assertRaisesRegex(InvestmentError, "version"):
            read(self.database)

    def test_connection_is_closed_when_post_open_validation_fails(self):
        class ConnectionStub:
            closed = False

            def execute(self, _statement):
                raise sqlite3.OperationalError("injected pragma failure")

            def close(self):
                self.closed = True

        initialize(self.database)
        connection = ConnectionStub()
        with patch("investment.pilot_store.sqlite3.connect", return_value=connection):
            with self.assertRaisesRegex(InvestmentError, "Cannot open"):
                pilot_store._connect(self.database, False)
        self.assertTrue(connection.closed)

    def test_real_close_reopen_recovers_partial_cancel_and_repeated_observations(self):
        initialize(self.database)
        events = [intent(), attempt(), acknowledgement(),
                  observation("2026-01-01T10:02:00Z", "partial", "3"),
                  observation("2026-01-01T10:03:00Z", "partial", "3"),
                  observation("2026-01-01T10:04:00Z", "cancelled", "4")]
        for index, event in enumerate(events, 1):
            self.assertEqual(append_event(self.database, f"event-{index}", event), index)
            recovered = read(self.database)
            self.assertEqual(recovered, events[:index])
            self.assertEqual(simulate_pilot_trace(recovered)["submission_attempts"],
                             1 if index >= 2 else 0)
        result = simulate_pilot_trace(read(self.database))
        self.assertEqual(result["state"], "cancelled")
        self.assertEqual(result["observed_filled_quantity"], "4")
        self.assertEqual(result["fill_deltas"], ["3", "1"])

    def test_restart_after_uncertain_submit_is_durable_and_cannot_retry(self):
        initialize(self.database)
        for event_id, event in (
                ("intent", intent()), ("attempt", attempt()),
                ("restart", {"type": "restart", "at": "2026-01-01T10:02:00Z"})):
            append_event(self.database, event_id, event)
        self.assertEqual(simulate_pilot_trace(read(self.database))["state"], "manual_review")
        for event_id, event in (("retry", attempt()), ("late-ack", acknowledgement())):
            with self.subTest(event_id=event_id), self.assertRaises(InvestmentError):
                append_event(self.database, event_id, event)
        self.assertEqual(len(read(self.database)), 3)

    def test_append_failure_rolls_back_event_and_commit_together(self):
        initialize(self.database)
        first = intent()
        append_event(self.database, "intent", first)

        def insert_then_fail(connection, revision, event_id, payload,
                             previous_hash, event_hash, state_hash):
            connection.execute("INSERT INTO events VALUES (?,?,?,?,?)",
                               (revision, event_id, payload, previous_hash, event_hash))
            raise sqlite3.OperationalError("injected commit boundary failure")

        with patch("investment.pilot_store._append_rows", side_effect=insert_then_fail):
            with self.assertRaisesRegex(InvestmentError, "rolled back"):
                append_event(self.database, "attempt", attempt())
        self.assertEqual(read(self.database), [first])

    def test_tampered_chain_or_missing_commit_boundary_is_refused(self):
        initialize(self.database)
        append_event(self.database, "intent", intent())
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("DROP TRIGGER immutable_events_update")
            connection.execute("UPDATE events SET payload = ? WHERE revision = 1",
                               ('{"type":"corrupt"}',))
            connection.execute(
                "CREATE TRIGGER immutable_events_update BEFORE UPDATE ON events "
                "BEGIN SELECT RAISE(ABORT, 'immutable pilot evidence'); END")
            connection.commit()
        with self.assertRaises(InvestmentError):
            read(self.database)

        other = Path(self.temporary.name) / "missing-commit.sqlite3"
        initialize(other)
        append_event(other, "intent", intent())
        with closing(sqlite3.connect(other)) as connection:
            connection.execute("DROP TRIGGER immutable_commits_delete")
            connection.execute("DELETE FROM commits WHERE revision = 0")
            connection.execute(
                "CREATE TRIGGER immutable_commits_delete BEFORE DELETE ON commits "
                "BEGIN SELECT RAISE(ABORT, 'immutable pilot evidence'); END")
            connection.commit()
        with self.assertRaisesRegex(InvestmentError, "commit"):
            read(other)

    def test_missing_and_invalid_schemas_fail_closed_without_creation(self):
        with self.assertRaisesRegex(InvestmentError, "missing"):
            read(self.database)
        self.assertFalse(self.database.exists())
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("CREATE TABLE unrelated (value TEXT)")
        with self.assertRaisesRegex(InvestmentError, "(schema|version)"):
            read(self.database)

    def test_interrupted_initialization_cleans_temporary_file_and_allows_retry(self):
        create = pilot_store._create_schema

        def create_then_interrupt(path):
            create(path)
            raise sqlite3.OperationalError("injected initialization interruption")

        with patch("investment.pilot_store._create_schema", side_effect=create_then_interrupt):
            with self.assertRaisesRegex(InvestmentError, "no destination was published"):
                initialize(self.database)
        self.assertFalse(self.database.exists())
        self.assertEqual(list(Path(self.temporary.name).iterdir()), [])
        initialize(self.database)
        self.assertEqual(read(self.database), [])

    def test_non_text_event_payload_is_reported_without_mutation(self):
        initialize(self.database)
        append_event(self.database, "intent", intent())
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("DROP TRIGGER immutable_events_update")
            connection.execute("UPDATE events SET payload = ? WHERE revision = 1",
                               (sqlite3.Binary(b"\xff"),))
            connection.execute(
                "CREATE TRIGGER immutable_events_update BEFORE UPDATE ON events "
                "BEGIN SELECT RAISE(ABORT, 'immutable pilot evidence'); END")
            connection.commit()
        before = self.database.read_bytes()
        for action in (lambda: read(self.database),
                       lambda: append_event(self.database, "next", attempt())):
            with self.assertRaisesRegex(InvestmentError, "storage types"):
                action()
        self.assertEqual(self.database.read_bytes(), before)

    def test_manual_review_is_sticky_across_process_replay(self):
        initialize(self.database)
        events = [intent(), attempt(),
                  {"type": "uncertain", "at": "2026-01-01T10:01:01Z"}]
        for index, event in enumerate(events):
            append_event(self.database, f"event-{index}", event)
        recovered = read(self.database)
        self.assertEqual(simulate_pilot_trace(recovered)["state"], "manual_review")
        with self.assertRaises(InvestmentError):
            append_event(self.database, "late-order", acknowledgement())
        self.assertEqual(read(self.database), events)

