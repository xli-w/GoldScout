import asyncio
import hashlib
import json
import selectors
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from dashboard.reader import LedgerReader, ReadError

try:
    from fastapi.testclient import TestClient
    from dashboard.app import create_app
except ModuleNotFoundError:
    TestClient = None
    create_app = None


class MemorySelector(selectors.BaseSelector):
    """ASGI scheduling only: file descriptors and network IO are forbidden."""

    def __init__(self):
        self.ready = threading.Event()

    def register(self, fileobj, events, data=None):
        raise AssertionError("ASGI tests must not register network/file IO")

    def unregister(self, fileobj):
        raise AssertionError("ASGI tests must not register network/file IO")

    def select(self, timeout=None):
        self.ready.wait(timeout)
        self.ready.clear()
        return []

    def get_map(self):
        return {}


class MemoryEventLoop(asyncio.SelectorEventLoop):
    """Replace asyncio's socket wakeup with an in-memory threading event."""

    def __init__(self):
        super().__init__(selector=MemorySelector())

    def _make_self_pipe(self):
        pass

    def _close_self_pipe(self):
        pass

    def _write_to_self(self):
        self._selector.ready.set()


class DashboardReaderTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = Path(self.directory.name) / "sample.sqlite3"
        self.now = datetime(2026, 11, 13, 15, tzinfo=timezone.utc)
        self.state = {
            "positions": {"SPY": {
                "symbol": "SPY", "strategy": "mean_reversion", "side": "long", "qty": 6,
                "entry_price": 100.25, "entry_time": "2026-11-13T14:00:00+00:00", "hard_stop": 98,
                "server_stop": 98, "protection_verified": True, "identity": "synthetic-entry",
            }},
            "daily": {"day": "2026-11-13", "last_equity": 100000, "realized": 8},
            "daily_controls": {"halted": False, "accounting_unresolved": False},
        }

    def create_database(self, state=None):
        payload = json.dumps(self.state if state is None else state)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.executescript("""
                PRAGMA user_version=1;
                CREATE TABLE state(id INTEGER PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE events(sequence INTEGER PRIMARY KEY, kind TEXT, payload TEXT, observed_at TEXT);
            """)
            connection.execute("INSERT INTO state VALUES(1, ?)", (payload,))
            connection.execute("INSERT INTO metadata VALUES('state_sequence', '1')")
            connection.execute("INSERT INTO metadata VALUES('database_id', 'synthetic-ledger-1')")
            connection.execute("INSERT INTO metadata VALUES('account_binding', ?)",
                               (json.dumps({"account_id": "synthetic-account", "environment": "paper"}),))
            connection.execute("INSERT INTO events VALUES(1, 'state_committed', ?, ?)",
                               (payload, "2026-11-13T14:59:30+00:00"))
            connection.commit()

    def test_reads_do_not_modify_database_or_create_files(self):
        self.create_database()
        before = hashlib.sha256(self.database.read_bytes()).hexdigest()
        files = set(self.database.parent.iterdir())
        reader = LedgerReader(self.database)
        snapshot = reader.snapshot(self.now)
        self.assertEqual(snapshot["overview"]["recorded_equity"], "100000")
        self.assertEqual(snapshot["positions"][0]["quantity"], "6")
        self.assertEqual(snapshot["positions"][0]["entry_price"], "100.25")
        self.assertEqual(snapshot["positions"][0]["protection"], "unknown")
        self.assertIsNone(snapshot["positions"][0]["protection_observed_at"])
        self.assertEqual(snapshot["overview"]["entries"], "unknown")
        self.assertEqual(snapshot["meta"]["environment"], "paper")
        self.assertEqual(hashlib.sha256(self.database.read_bytes()).hexdigest(), before)
        self.assertEqual(set(self.database.parent.iterdir()), files)

    def test_missing_database_is_not_created(self):
        with self.assertRaises(ReadError) as result:
            LedgerReader(self.database).snapshot(self.now)
        self.assertEqual(result.exception.code, "ledger_missing")
        self.assertFalse(self.database.exists())

    def test_empty_valid_account_is_distinct_from_missing_snapshot(self):
        self.create_database({"positions": {}})
        self.assertEqual(LedgerReader(self.database).snapshot(self.now)["positions"], [])
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("DELETE FROM state")
            connection.commit()
        with self.assertRaises(ReadError) as result:
            LedgerReader(self.database).snapshot(self.now)
        self.assertEqual(result.exception.code, "ledger_invalid")

    def test_tampered_snapshot_and_future_schema_are_rejected(self):
        self.create_database()
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("UPDATE state SET payload='{}'")
            connection.commit()
        with self.assertRaises(ReadError):
            LedgerReader(self.database).snapshot(self.now)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("PRAGMA user_version=2")
        with self.assertRaises(ReadError) as result:
            LedgerReader(self.database).snapshot(self.now)
        self.assertEqual(result.exception.code, "schema_unsupported")

    def test_invalid_numeric_values_are_rejected(self):
        self.state["positions"]["SPY"]["qty"] = float("nan")
        self.create_database()
        with self.assertRaises(ReadError):
            LedgerReader(self.database).snapshot(self.now)

    def test_accounting_block_keeps_realized_value_unknown(self):
        self.state["daily_controls"]["accounting_unresolved"] = True
        self.create_database()
        overview = LedgerReader(self.database).snapshot(self.now)["overview"]
        self.assertEqual(overview["entries"], "blocked")
        self.assertIsNone(overview["realized_pnl"])
        self.assertIn("accounting_unresolved", overview["block_reasons"])

    def test_wal_writer_uncommitted_state_is_not_published(self):
        self.create_database()
        with closing(sqlite3.connect(self.database)) as writer:
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("BEGIN IMMEDIATE")
            writer.execute("UPDATE state SET payload='{}'")
            self.assertEqual(LedgerReader(self.database).snapshot(self.now)["positions"][0]["quantity"], "6")
            writer.rollback()

    def test_client_id_only_exit_is_projected_as_pending(self):
        self.state["positions"]["SPY"]["exit_client_order_id"] = "synthetic-pending-client"
        self.create_database()
        snapshot = LedgerReader(self.database).snapshot(self.now)
        self.assertTrue(snapshot["positions"][0]["exit_pending"])
        self.assertIsNone(snapshot["positions"][0]["pending_exit_order_id"])
        self.assertIn("exit_pending", snapshot["overview"]["block_reasons"])


@unittest.skipIf(TestClient is None, "Install requirements-dashboard-dev.txt for in-process API tests")
class DashboardAPITests(DashboardReaderTests):
    def client(self, database=None, scenario="healthy_recorded", process_check=None):
        return TestClient(create_app(database=database, scenario=scenario, clock=lambda: self.now, process_check=process_check, serve_frontend=False),
                          base_url="http://127.0.0.1", raise_server_exceptions=False,
                          backend_options={"loop_factory": MemoryEventLoop})

    def test_api_reads_and_revision_pairing(self):
        self.create_database()
        with self.client(self.database) as client:
            overview = client.get("/api/v1/overview")
            self.assertEqual(overview.status_code, 200)
            positions = client.get("/api/v1/positions?revision=1")
            self.assertEqual(positions.status_code, 200)
            self.assertEqual(positions.json()["meta"], overview.json()["meta"])
            self.assertEqual(client.get("/api/v1/positions?revision=2").status_code, 503)
            self.assertEqual(client.get("/api/v1/positions?revision=bad").status_code, 400)
            self.assertEqual(client.get("/api/v1/positions?revision=1&database_id=another-ledger").status_code, 503)

    def test_errors_are_sanitized_and_missing_storage_stays_missing(self):
        with self.client(self.database) as client:
            response = client.get("/api/v1/overview")
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["data"]["code"], "ledger_missing")
            self.assertNotIn(str(self.database), response.text)
            self.assertFalse(self.database.exists())

    def test_cross_origin_host_and_mutations_are_refused(self):
        with self.client() as client:
            self.assertEqual(client.get("/api/v1/overview", headers={"Origin": "https://other.invalid"}).status_code, 403)
            self.assertEqual(client.get("/api/v1/overview", headers={"Host": "other.invalid"}).status_code, 400)
            self.assertEqual(client.post("/api/v1/overview").status_code, 405)
            response = client.get("/api/v1/overview")
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertNotIn("access-control-allow-origin", response.headers)
            self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])
            self.assertEqual(client.get("/").status_code, 404)
            self.assertEqual(client.get("/assets/app.js").status_code, 404)

    def test_demo_is_explicit_in_each_data_response(self):
        with self.client(scenario="partial_exit") as client:
            self.assertEqual(client.get("/api/v1/settings").json()["mode"], "demo")
            self.assertEqual(client.get("/api/v1/overview").json()["meta"]["source_mode"], "demo")
            self.assertEqual(client.get("/api/v1/positions").json()["data"][0]["quantity"], "6")
            self.assertEqual(client.get("/api/v1/overview").json()["data"]["entries"], "blocked")

    def test_history_demo_endpoints_filters_and_continuation(self):
        with self.client() as client:
            cursor, days = None, []
            for _ in range(10):
                response = client.get("/api/v1/performance?limit=1" + ("&cursor=" + cursor if cursor else ""))
                self.assertEqual(response.status_code, 200)
                body = response.json()["data"]
                days.extend(row["day"] for row in body["daily"])
                cursor = body["next_cursor"]
                if cursor is None:
                    break
            self.assertEqual(len(days), 5)
            self.assertEqual(len(set(days)), 5)
            filtered = client.get("/api/v1/performance?symbol=QQQ").json()["data"]
            self.assertEqual(len(filtered["exits"]), 1)
            self.assertEqual(len(filtered["daily"]), 5)
            self.assertEqual(client.get("/api/v1/performance?from=invalid").status_code, 400)
            self.assertEqual(client.get("/api/v1/orders?limit=0").status_code, 400)
            self.assertEqual(client.get("/api/v1/events").status_code, 200)

    def test_health_api_degrades_bad_telemetry_without_hiding_accounting(self):
        self.create_database()
        path = self.database.with_name("bot_telemetry.json")
        path.write_text(json.dumps({
            "version": 1, "database_id": "synthetic-ledger-1", "environment": "paper", "run_id": "synthetic-run",
            "pid": 123, "process_created_at": 100.0, "observed_at": "2026-11-13T14:59:55Z",
            "last_monitor_completed_at": "2026-11-13T14:59:30Z", "runtime": "running", "persistence_fault": False,
            "accounting_unresolved": False, "daily_halted": False, "unmanaged_symbols": [], "block_reasons": [],
            "task_failures": {}, "activity_sync": {"status": "unknown", "checkpoint_at": None}, "protection": {},
        }))
        with self.client(self.database, process_check=lambda *args: True) as client:
            response = client.get("/api/v1/health")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["data"]["runtime"], "running")
            self.assertEqual(client.get("/api/v1/health?run_id=previous-run").status_code, 503)
            payload = json.loads(path.read_text())
            payload["task_failures"] = {"x" * 200: 1}
            path.write_text(json.dumps(payload))
            response = client.get("/api/v1/health")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["data"]["runtime"], "unknown")
            self.assertEqual(client.get("/api/v1/overview").status_code, 200)


if __name__ == "__main__":
    unittest.main()