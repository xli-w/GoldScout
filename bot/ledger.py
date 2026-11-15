import csv
import hashlib
import json
import sqlite3
import uuid
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path


class PersistenceError(RuntimeError):
    pass


class Ledger:
    SCHEMA_VERSION = 1

    def __init__(self, path, timeout=2):
        self.path = Path(path)
        self.timeout = timeout
        with self.connect() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, self.SCHEMA_VERSION):
                raise PersistenceError(f"Unsupported ledger schema version {version}")
            connection.execute("CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS exits (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS legacy_import (id INTEGER PRIMARY KEY CHECK(id=1))")
            connection.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS events (sequence INTEGER PRIMARY KEY, event_key TEXT UNIQUE NOT NULL, kind TEXT NOT NULL, observed_at TEXT NOT NULL, payload TEXT NOT NULL, payload_version INTEGER NOT NULL DEFAULT 1)")
            connection.execute("CREATE INDEX IF NOT EXISTS events_kind ON events(kind, sequence)")
            connection.execute("CREATE TABLE IF NOT EXISTS orders (client_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS daily_accounts (day TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS broker_activities (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS corrections (id TEXT PRIMARY KEY, execution_id TEXT NOT NULL REFERENCES exits(id), activity_id TEXT NOT NULL REFERENCES broker_activities(id), payload TEXT NOT NULL)")
            connection.execute("CREATE INDEX IF NOT EXISTS corrections_execution ON corrections(execution_id)")
            connection.execute("CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT, 'Audit events are append-only'); END")
            connection.execute("CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT, 'Audit events are append-only'); END")
            row = connection.execute("SELECT payload FROM state WHERE id=1").fetchone()
            revision = connection.execute("SELECT value FROM metadata WHERE key='state_sequence'").fetchone()
            if version == 0 and row and revision is None:
                payload = self.decode(row[0])
                sequence = self._event(connection, "state_committed", payload)
                connection.execute("INSERT INTO metadata VALUES ('state_sequence', ?)", (str(sequence),))
            connection.execute("INSERT OR IGNORE INTO metadata VALUES ('database_id', ?)", (uuid.uuid4().hex,))
            connection.execute(f"PRAGMA user_version={self.SCHEMA_VERSION}")
        with closing(sqlite3.connect(self.path, timeout=self.timeout)) as connection:
            connection.execute("PRAGMA journal_mode=WAL")

    @contextmanager
    def connect(self, write=True):
        connection = sqlite3.connect(self.path, timeout=self.timeout)
        try:
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def encode(payload):
        return json.dumps(payload, allow_nan=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def decode(payload):
        def invalid_constant(value):
            raise PersistenceError(f"Nonfinite JSON value {value}")
        return json.loads(payload, parse_constant=invalid_constant)

    def load(self):
        with self.connect(write=False) as connection:
            row = connection.execute("SELECT payload FROM state WHERE id=1").fetchone()
            revision = connection.execute("SELECT value FROM metadata WHERE key='state_sequence'").fetchone()
            if bool(row) != bool(revision):
                raise PersistenceError("Snapshot revision is missing")
            if row and revision:
                latest = connection.execute("SELECT MAX(sequence) FROM events WHERE kind='state_committed'").fetchone()[0]
                if latest != int(revision[0]):
                    raise PersistenceError("Snapshot revision is not the latest committed state")
                event = connection.execute("SELECT payload FROM events WHERE sequence=? AND kind='state_committed'", (int(revision[0]),)).fetchone()
                if not event or self.decode(event[0]) != self.decode(row[0]):
                    raise PersistenceError("Snapshot does not match its journal revision")
        return self.decode(row[0]) if row else None

    def bind(self, account_id, environment):
        if not isinstance(account_id, str) or not account_id.strip():
            raise PersistenceError("Broker account identity unavailable")
        binding = self.encode({"account_id": account_id, "environment": environment})
        with self.connect() as connection:
            row = connection.execute("SELECT value FROM metadata WHERE key='account_binding'").fetchone()
            if row and row[0] != binding:
                raise PersistenceError("Ledger belongs to another broker account or environment")
            connection.execute("INSERT OR IGNORE INTO metadata VALUES ('account_binding', ?)", (binding,))

    def _event(self, connection, kind, payload, event_key=None):
        encoded = self.encode(payload)
        event_key = event_key or uuid.uuid4().hex
        previous = connection.execute("SELECT kind, payload, sequence FROM events WHERE event_key=?", (event_key,)).fetchone()
        if previous:
            if previous[:2] != (kind, encoded):
                raise PersistenceError(f"Conflicting duplicate event {event_key}")
            return previous[2]
        cursor = connection.execute("INSERT INTO events(event_key, kind, observed_at, payload) VALUES (?, ?, ?, ?)",
                                    (event_key, kind, datetime.now(timezone.utc).isoformat(), encoded))
        return cursor.lastrowid

    def record(self, kind, payload, event_key=None):
        with self.connect() as connection:
            return self._event(connection, kind, payload, event_key)

    def commit(self, state, event=None, daily_rows=(), order=None):
        encoded = self.encode(state)
        with self.connect() as connection:
            if event is not None:
                connection.execute("INSERT INTO exits VALUES (?, ?)", (event["execution_id"], self.encode(event)))
                self._event(connection, "exit_accounted", event, "exit:" + event["execution_id"])
                if not event.get("price_verified", True):
                    self._event(connection, "accounting_exception", {"execution_id": event["execution_id"], "reason": "unverified_exit_price"})
            for row in daily_rows:
                previous = connection.execute("SELECT payload FROM daily_accounts WHERE day=?", (row["date"],)).fetchone()
                if previous is None or previous[0] != self.encode(row):
                    self._event(connection, "daily_account_updated", row)
                    connection.execute("INSERT OR REPLACE INTO daily_accounts VALUES (?, ?)", (row["date"], self.encode(row)))
            if event is not None and event.get("accounting_date") != state.get("daily", {}).get("day"):
                day = event.get("accounting_date")
                previous = connection.execute("SELECT payload FROM daily_accounts WHERE day=?", (day,)).fetchone()
                if previous:
                    row = self.decode(previous[0])
                    realized = event.get("accounting", {}).get("profit_loss", event.get("profit_loss"))
                    row["realized_pnl"] = float(row.get("realized_pnl") or 0) + float(realized or 0)
                    row["trades_closed"] = int(row.get("trades_closed") or 0) + 1
                    connection.execute("UPDATE daily_accounts SET payload=? WHERE day=?", (self.encode(row), day))
                    self._event(connection, "daily_account_restated", row)
                elif day:
                    self._event(connection, "accounting_exception", {"execution_id": event["execution_id"], "reason": "missing_historical_daily_row", "day": day})
            if order is not None:
                self._event(connection, "order_intent", order, "order:" + order["client_order_id"])
                connection.execute("INSERT OR REPLACE INTO orders VALUES (?, ?)", (order["client_order_id"], self.encode(order)))
            previous = connection.execute("SELECT payload FROM state WHERE id=1").fetchone()
            revision = connection.execute("SELECT value FROM metadata WHERE key='state_sequence'").fetchone()
            if previous is None or self.decode(previous[0]) != state or revision is None:
                if previous:
                    old_state = self.decode(previous[0])
                    for section, kind in (("positions", "positions_changed"), ("entry_intents", "entry_intents_changed"),
                                          ("daily_controls", "risk_controls_changed"), ("last_bar_ts", "bars_processed")):
                        if old_state.get(section) != state.get(section):
                            self._event(connection, kind, {"before": old_state.get(section), "after": state.get(section)})
                sequence = self._event(connection, "state_committed", state)
                connection.execute("INSERT OR REPLACE INTO state VALUES (1, ?)", (encoded,))
                connection.execute("INSERT OR REPLACE INTO metadata VALUES ('state_sequence', ?)", (str(sequence),))

    def ingest_activity(self, activity):
        if not isinstance(activity, dict) or not isinstance(activity.get("id"), str) or not activity["id"]:
            raise ValueError("Activity requires a nonempty broker ID")
        with self.connect() as connection:
            encoded = self.encode(activity)
            previous = connection.execute("SELECT payload FROM broker_activities WHERE id=?", (activity["id"],)).fetchone()
            if previous and previous[0] != encoded:
                raise PersistenceError(f"Conflicting broker activity {activity['id']}")
            self._event(connection, "broker_activity", activity, "activity:" + activity["id"])
            connection.execute("INSERT OR IGNORE INTO broker_activities VALUES (?, ?)", (activity["id"], encoded))

    def observe_order(self, payload):
        with self.connect() as connection:
            identity = hashlib.sha256(self.encode(payload).encode("utf-8")).hexdigest()
            self._event(connection, "order_observed", payload, "observed:" + identity)
            client_id = payload.get("client_order_id")
            if client_id:
                previous = connection.execute("SELECT payload FROM orders WHERE client_id=?", (client_id,)).fetchone()
                projection = self.decode(previous[0]) if previous else {"client_order_id": client_id}
                projection["observation"] = payload
                connection.execute("INSERT OR REPLACE INTO orders VALUES (?, ?)", (client_id, self.encode(projection)))

    def get_metadata(self, key):
        with self.connect(write=False) as connection:
            row = connection.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set_metadata(self, key, value):
        if key != "activities_synced_at":
            raise PersistenceError("Metadata key is not externally writable")
        with self.connect() as connection:
            connection.execute("INSERT OR REPLACE INTO metadata VALUES (?, ?)", (key, value))

    def history(self, after=0, limit=100):
        if not 1 <= limit <= 1000:
            raise ValueError("History limit must be between 1 and 1000")
        with self.connect(write=False) as connection:
            rows = connection.execute("SELECT sequence, kind, observed_at, payload FROM events WHERE sequence>? ORDER BY sequence LIMIT ?", (after, limit)).fetchall()
        return [dict(sequence=row[0], kind=row[1], observed_at=row[2], payload=self.decode(row[3])) for row in rows]

    def replay(self):
        with self.connect(write=False) as connection:
            row = connection.execute("SELECT payload FROM events WHERE kind='state_committed' ORDER BY sequence DESC LIMIT 1").fetchone()
        return self.decode(row[0]) if row else None

    def summary(self):
        with self.connect(write=False) as connection:
            counts = {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                      for table in ("events", "orders", "exits", "daily_accounts", "broker_activities", "corrections")}
        return dict(schema_version=self.SCHEMA_VERSION, account_binding=self.get_metadata("account_binding"),
                    database_id=self.get_metadata("database_id"), counts=counts)

    def apply_correction(self, execution_id, correction_id, activity_id, exit_price, fees, reason):
        self.load()
        if not all(isinstance(value, str) and value.strip() for value in (execution_id, correction_id, activity_id, reason)):
            raise ValueError("Correction IDs and reason must be nonempty strings")
        price, fee_total = Decimal(str(exit_price)), Decimal(str(fees))
        if not price.is_finite() or price <= 0 or not fee_total.is_finite() or fee_total < 0 or not reason:
            raise ValueError("Correction requires a positive verified price, nonnegative total fees and reason")
        correction = dict(execution_id=execution_id, correction_id=correction_id, activity_id=activity_id,
                          exit_price=str(price), fees=str(fee_total), reason=reason)
        with self.connect() as connection:
            duplicate = connection.execute("SELECT payload FROM corrections WHERE id=?", (correction_id,)).fetchone()
            if duplicate:
                if self.decode(duplicate[0])["input"] != correction:
                    raise PersistenceError("Conflicting correction ID")
                return
            row = connection.execute("SELECT payload FROM exits WHERE id=?", (execution_id,)).fetchone()
            evidence = connection.execute("SELECT 1 FROM broker_activities WHERE id=?", (activity_id,)).fetchone()
            if row is None or evidence is None:
                raise PersistenceError("Correction requires an existing exit and broker activity evidence")
            original = self.decode(row[0])
            accounting = original.get("accounting", {})
            quantity = Decimal(str(accounting.get("qty", original["position_size"])))
            entry = Decimal(str(accounting.get("entry_price", original["entry_price"])))
            sign = Decimal(1 if original["direction"] == "long" else -1)
            gross = sign * (price - entry) * quantity
            pnl = gross - fee_total
            prior = connection.execute("SELECT payload FROM corrections WHERE execution_id=? ORDER BY rowid DESC LIMIT 1", (execution_id,)).fetchone()
            previous_pnl = self.decode(prior[0])["profit_loss"] if prior else accounting.get("profit_loss", original.get("profit_loss"))
            delta = pnl - Decimal(str(previous_pnl or 0))
            payload = dict(input=correction, profit_loss=str(pnl), gross_profit_loss=str(gross), delta=str(delta))
            connection.execute("INSERT INTO corrections VALUES (?, ?, ?, ?)", (correction_id, execution_id, activity_id, self.encode(payload)))
            self._event(connection, "accounting_corrected", payload, "correction:" + correction_id)
            day = original.get("accounting_date")
            daily = connection.execute("SELECT payload FROM daily_accounts WHERE day=?", (day,)).fetchone()
            if daily:
                projection = self.decode(daily[0])
                projection["realized_pnl"] = float(Decimal(str(projection.get("realized_pnl") or 0)) + delta)
                connection.execute("UPDATE daily_accounts SET payload=? WHERE day=?", (self.encode(projection), day))
                self._event(connection, "daily_account_restated", projection)
            else:
                self._event(connection, "accounting_exception", dict(execution_id=execution_id, reason="correction_missing_daily_row"))
            state_row = connection.execute("SELECT payload FROM state WHERE id=1").fetchone()
            if state_row:
                state = self.decode(state_row[0])
                if day and state.get("daily", {}).get("day") == day:
                    state["daily"]["realized"] = float(Decimal(str(state["daily"]["realized"])) + delta)
                    sequence = self._event(connection, "state_committed", state)
                    connection.execute("UPDATE state SET payload=? WHERE id=1", (self.encode(state),))
                    connection.execute("UPDATE metadata SET value=? WHERE key='state_sequence'", (str(sequence),))

    def resolve_accounting(self, reason):
        state = self.load()
        if not state or not isinstance(reason, str) or not reason.strip():
            raise PersistenceError("Accounting resolution requires a recovery state and review reason")
        with self.connect() as connection:
            rows = connection.execute("SELECT id, payload FROM exits WHERE id NOT IN (SELECT execution_id FROM corrections)").fetchall()
            if any(self.decode(row[1]).get("price_verified") is False for row in rows):
                raise PersistenceError("Unverified exits still require evidence-linked corrections")
            state.setdefault("daily_controls", {})["accounting_unresolved"] = False
            self._event(connection, "accounting_review_resolved", {"reason": reason})
            sequence = self._event(connection, "state_committed", state)
            connection.execute("UPDATE state SET payload=? WHERE id=1", (self.encode(state),))
            connection.execute("UPDATE metadata SET value=? WHERE key='state_sequence'", (str(sequence),))

    def import_legacy(self, path, daily_path=None):
        with self.connect() as connection:
            if not connection.execute("SELECT 1 FROM legacy_import").fetchone():
                if path.exists():
                    with path.open(newline="") as handle:
                        for index, row in enumerate(csv.DictReader(handle)):
                            connection.execute("INSERT OR IGNORE INTO exits VALUES (?, ?)", (f"legacy-{index}", self.encode(row)))
                connection.execute("INSERT INTO legacy_import VALUES (1)")
            if daily_path is not None and not connection.execute("SELECT 1 FROM metadata WHERE key='daily_import'").fetchone():
                if daily_path.exists():
                    with daily_path.open(newline="") as handle:
                        for row in csv.DictReader(handle):
                            connection.execute("INSERT OR IGNORE INTO daily_accounts VALUES (?, ?)", (row["date"], self.encode(row)))
                connection.execute("INSERT INTO metadata VALUES ('daily_import', 'complete')")

    def export(self, path, fields, daily=False):
        with self.connect(write=False) as connection:
            query = "SELECT payload FROM daily_accounts ORDER BY day" if daily else "SELECT payload FROM exits ORDER BY rowid"
            rows = connection.execute(query).fetchall()
        temporary = path.with_suffix(".tmp")
        with temporary.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(self.decode(row[0]) for row in rows)
        temporary.replace(path)

    def backup(self, destination):
        destination = Path(destination)
        if destination.resolve() == self.path.resolve() or destination.exists():
            raise PersistenceError("Backup requires a new destination")
        with closing(sqlite3.connect(self.path, timeout=self.timeout)) as source:
            with closing(sqlite3.connect(destination)) as target:
                source.backup(target)
                if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise PersistenceError("Backup integrity check failed")
        restored = Ledger(destination, self.timeout)
        restored.load()

    def export_corrections(self, path):
        with self.connect(write=False) as connection:
            rows = connection.execute("SELECT payload FROM corrections ORDER BY rowid").fetchall()
        columns = ["execution_id", "correction_id", "activity_id", "exit_price", "fees", "reason",
                   "profit_loss", "gross_profit_loss", "delta"]
        temporary = path.with_suffix(".tmp")
        with temporary.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            for row in rows:
                payload = self.decode(row[0])
                writer.writerow(dict(payload["input"], profit_loss=payload["profit_loss"],
                                     gross_profit_loss=payload["gross_profit_loss"], delta=payload["delta"]))
        temporary.replace(path)