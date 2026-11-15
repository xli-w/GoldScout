"""Bounded local persistence for intelligence, never the trading ledger."""

from contextlib import contextmanager
from dataclasses import asdict, fields, replace
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import uuid
from typing import TypeVar

from market_intelligence.contracts import (
    CalendarEvent, CalendarSnapshot, ControlReceipt, ControlRequest,
    IntelligenceSettings, NewsEvidence, SourceCoverage, identifier, nonnegative_integer, utc_time,
)
from market_intelligence.news import ArchivedNews, NewsBatch, NewsObservation, ProviderError, parse_fed_news
from market_intelligence.analysis import AnalysisBrief, AnalysisLimits, EvidenceClaim, SYSTEM_PROMPT, analysis_input


APPLICATION_ID = 0x4D494E54
SCHEMA_VERSION = 4
MAX_PAYLOAD = 524288
MAX_RAW_NEWS = 1048576
_Record = TypeVar("_Record")


def storage_schema(version=SCHEMA_VERSION):
    if version not in (1, 2, 3, 4):
        raise StoreError("schema_unsupported")
    definitions = {
        ("table", "identity"): "CREATE TABLE identity(id INTEGER PRIMARY KEY CHECK(id=1), database_id TEXT NOT NULL CHECK(length(database_id) BETWEEN 1 AND 128))",
        ("table", "settings_history"): "CREATE TABLE settings_history(revision INTEGER PRIMARY KEY CHECK(revision>=0), payload TEXT NOT NULL, payload_hash TEXT NOT NULL CHECK(length(payload_hash)=64), observed_at TEXT NOT NULL CHECK(length(observed_at)<=32))",
        ("table", "settings_head"): "CREATE TABLE settings_head(id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL REFERENCES settings_history(revision))",
        ("table", "commands"): "CREATE TABLE commands(sequence INTEGER PRIMARY KEY AUTOINCREMENT CHECK(sequence>0), request_id TEXT UNIQUE NOT NULL CHECK(length(request_id) BETWEEN 1 AND 128), payload TEXT NOT NULL, payload_hash TEXT NOT NULL CHECK(length(payload_hash)=64), receipt TEXT NOT NULL, receipt_hash TEXT NOT NULL CHECK(length(receipt_hash)=64), status TEXT NOT NULL CHECK(status IN ('pending','applied','rejected','expired')), enqueued_at TEXT NOT NULL CHECK(length(enqueued_at)<=32), applied_revision INTEGER UNIQUE REFERENCES settings_history(revision), CHECK((status='applied' AND applied_revision IS NOT NULL) OR (status!='applied' AND applied_revision IS NULL)))",
        ("table", "command_events"): "CREATE TABLE command_events(sequence INTEGER PRIMARY KEY AUTOINCREMENT CHECK(sequence>0), request_id TEXT NOT NULL REFERENCES commands(request_id), request_hash TEXT NOT NULL CHECK(length(request_hash)=64), receipt TEXT NOT NULL, receipt_hash TEXT NOT NULL CHECK(length(receipt_hash)=64), observed_at TEXT NOT NULL CHECK(length(observed_at)<=32))",
        ("table", "calendar_history"): "CREATE TABLE calendar_history(revision INTEGER PRIMARY KEY CHECK(revision>0), settings_revision INTEGER NOT NULL REFERENCES settings_history(revision), payload TEXT NOT NULL, payload_hash TEXT NOT NULL CHECK(length(payload_hash)=64))",
        ("index", "command_status"): "CREATE INDEX command_status ON commands(status,sequence)",
        ("index", "command_event_request"): "CREATE INDEX command_event_request ON command_events(request_id,sequence)",
    }
    for table, key in (("settings_history", "revision"), ("command_events", "sequence"), ("calendar_history", "revision")):
        for operation in ("UPDATE", "DELETE"):
            name = f"immutable_{table}_{operation.lower()}"
            definitions[("trigger", name)] = (f"CREATE TRIGGER {name} BEFORE {operation} ON {table} "
                                              "BEGIN SELECT RAISE(ABORT, 'Evidence is append-only'); END")
        name = f"immutable_{table}_replace"
        definitions[("trigger", name)] = (f"CREATE TRIGGER {name} BEFORE INSERT ON {table} "
                                          f"WHEN EXISTS(SELECT 1 FROM {table} WHERE {key}=NEW.{key}) "
                                          "BEGIN SELECT RAISE(ABORT, 'Evidence is append-only'); END")
    definitions[("trigger", "immutable_command_request")] = (
        "CREATE TRIGGER immutable_command_request BEFORE UPDATE ON commands "
        "WHEN NEW.sequence!=OLD.sequence OR NEW.request_id!=OLD.request_id OR NEW.payload!=OLD.payload "
        "OR NEW.payload_hash!=OLD.payload_hash OR NEW.enqueued_at!=OLD.enqueued_at "
        "BEGIN SELECT RAISE(ABORT, 'Request evidence is immutable'); END")
    definitions[("trigger", "immutable_command_delete")] = (
        "CREATE TRIGGER immutable_command_delete BEFORE DELETE ON commands "
        "BEGIN SELECT RAISE(ABORT, 'Request evidence is immutable'); END")
    definitions[("trigger", "immutable_command_replace")] = (
        "CREATE TRIGGER immutable_command_replace BEFORE INSERT ON commands "
        "WHEN EXISTS(SELECT 1 FROM commands WHERE request_id=NEW.request_id OR sequence=NEW.sequence) "
        "BEGIN SELECT RAISE(ABORT, 'Request evidence is immutable'); END")
    if version >= 2:
        definitions.update(news_storage_schema())
    if version >= 3:
        definitions.update(news_status_schema())
    if version >= 4:
        definitions.update(analysis_storage_schema())
    return definitions


def analysis_storage_schema():
    definitions = {
        ("table", "analysis_reservations"): "CREATE TABLE analysis_reservations(sequence INTEGER PRIMARY KEY, reservation_id TEXT UNIQUE NOT NULL, observed_at TEXT NOT NULL, day TEXT NOT NULL, accepted INTEGER NOT NULL CHECK(accepted IN (0,1)), input_bytes INTEGER NOT NULL CHECK(input_bytes>=0), output_allowance INTEGER NOT NULL CHECK(output_allowance>=0), payload TEXT NOT NULL, payload_hash TEXT NOT NULL, limits_hash TEXT NOT NULL)",
        ("table", "analysis_briefs"): "CREATE TABLE analysis_briefs(brief_id TEXT PRIMARY KEY, reservation_id TEXT UNIQUE NOT NULL REFERENCES analysis_reservations(reservation_id), payload TEXT NOT NULL, payload_hash TEXT NOT NULL)",
        ("index", "analysis_day"): "CREATE INDEX analysis_day ON analysis_reservations(day,accepted)",
    }
    for table, key in (("analysis_reservations", "sequence"), ("analysis_briefs", "brief_id")):
        for action in ("UPDATE", "DELETE"):
            name = f"immutable_{table}_{action.lower()}"
            definitions[("trigger", name)] = f"CREATE TRIGGER {name} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'Analysis is append-only'); END"
        name = f"immutable_{table}_replace"
        condition = f"{key}=NEW.{key} OR reservation_id=NEW.reservation_id"
        definitions[("trigger", name)] = f"CREATE TRIGGER {name} BEFORE INSERT ON {table} WHEN EXISTS(SELECT 1 FROM {table} WHERE {condition}) BEGIN SELECT RAISE(ABORT, 'Analysis is append-only'); END"
    return definitions


def news_status_schema():
    definitions = {
        ("table", "news_observations"): "CREATE TABLE news_observations(revision INTEGER PRIMARY KEY CHECK(revision>0), attempt_id TEXT UNIQUE NOT NULL CHECK(length(attempt_id) BETWEEN 1 AND 128), source TEXT NOT NULL CHECK(source='fed_monetary'), settings_revision INTEGER NOT NULL REFERENCES settings_history(revision), acquisition_id TEXT REFERENCES news_acquisitions(acquisition_id), last_success_id TEXT REFERENCES news_acquisitions(acquisition_id), payload TEXT NOT NULL, payload_hash TEXT NOT NULL CHECK(length(payload_hash)=64))",
        ("index", "news_observation_source"): "CREATE INDEX news_observation_source ON news_observations(source,revision)",
    }
    for operation in ("UPDATE", "DELETE"):
        name = f"immutable_news_observations_{operation.lower()}"
        definitions[("trigger", name)] = (f"CREATE TRIGGER {name} BEFORE {operation} ON news_observations "
                                          "BEGIN SELECT RAISE(ABORT, 'Source observations are append-only'); END")
    definitions[("trigger", "immutable_news_observations_replace")] = (
        "CREATE TRIGGER immutable_news_observations_replace BEFORE INSERT ON news_observations "
        "WHEN EXISTS(SELECT 1 FROM news_observations WHERE revision=NEW.revision OR attempt_id=NEW.attempt_id) "
        "BEGIN SELECT RAISE(ABORT, 'Source observations are append-only'); END")
    return definitions


def news_storage_schema():
    definitions = {
        ("table", "news_payloads"): "CREATE TABLE news_payloads(input_hash TEXT PRIMARY KEY CHECK(length(input_hash)=64), body BLOB NOT NULL CHECK(typeof(body)='blob' AND length(body)>0 AND length(body)<=1048576))",
        ("table", "news_acquisitions"): "CREATE TABLE news_acquisitions(revision INTEGER PRIMARY KEY CHECK(revision>0), acquisition_id TEXT UNIQUE NOT NULL CHECK(length(acquisition_id) BETWEEN 1 AND 128), settings_revision INTEGER NOT NULL REFERENCES settings_history(revision), source TEXT NOT NULL CHECK(source='fed_monetary'), received_at TEXT NOT NULL CHECK(length(received_at)<=32), available_at TEXT NOT NULL CHECK(length(available_at)<=32), parser_version TEXT NOT NULL CHECK(parser_version='fed-rss-v1'), payload TEXT NOT NULL, payload_hash TEXT NOT NULL CHECK(length(payload_hash)=64), input_hash TEXT NOT NULL REFERENCES news_payloads(input_hash))",
        ("table", "news_items"): "CREATE TABLE news_items(evidence_id TEXT PRIMARY KEY CHECK(length(evidence_id) BETWEEN 1 AND 128), cluster_id TEXT NOT NULL CHECK(length(cluster_id) BETWEEN 1 AND 128), first_acquisition_id TEXT NOT NULL REFERENCES news_acquisitions(acquisition_id), payload TEXT NOT NULL, payload_hash TEXT NOT NULL CHECK(length(payload_hash)=64))",
        ("index", "news_source_revision"): "CREATE INDEX news_source_revision ON news_acquisitions(source,revision)",
        ("index", "news_cluster"): "CREATE INDEX news_cluster ON news_items(cluster_id,evidence_id)",
    }
    for table, key in (("news_payloads", "input_hash"), ("news_acquisitions", "revision"), ("news_items", "evidence_id")):
        for operation in ("UPDATE", "DELETE"):
            name = f"immutable_{table}_{operation.lower()}"
            definitions[("trigger", name)] = (f"CREATE TRIGGER {name} BEFORE {operation} ON {table} "
                                              "BEGIN SELECT RAISE(ABORT, 'News evidence is append-only'); END")
        name = f"immutable_{table}_replace"
        condition = f"{key}=NEW.{key}"
        if table == "news_acquisitions":
            condition += " OR acquisition_id=NEW.acquisition_id"
        definitions[("trigger", name)] = (f"CREATE TRIGGER {name} BEFORE INSERT ON {table} "
                                          f"WHEN EXISTS(SELECT 1 FROM {table} WHERE {condition}) "
                                          "BEGIN SELECT RAISE(ABORT, 'News evidence is append-only'); END")
    return definitions


def bounded_column(name, maximum):
    return f"CASE WHEN typeof({name})='text' AND length(CAST({name} AS BLOB))<={maximum} THEN {name} END AS {name.split('.')[-1]}"


class StoreError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def encode(record):
    if isinstance(record, CalendarSnapshot) and (len(record.sources) > 50 or len(record.events) > 1000):
        raise ValueError("Calendar collections exceed storage limits")
    def timestamp(value):
        return utc_time(value).isoformat().replace("+00:00", "Z")
    payload = json.dumps(asdict(record), default=timestamp, sort_keys=True,
                         separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    if len(payload.encode("utf-8")) > MAX_PAYLOAD:
        raise ValueError("Intelligence payload exceeds storage limit")
    return payload


def parse_time(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", value):
        raise ValueError("Stored timestamps must be UTC wire timestamps")
    return utc_time(datetime.fromisoformat(value[:-1] + "+00:00"))


def construct(record_type: type[_Record], payload: object) -> _Record:
    expected = {field.name for field in fields(record_type)}
    optional = {"input_hash", "source_url"} if record_type is SourceCoverage else set()
    if record_type is AnalysisBrief:
        optional = {"reservation_id"}
    if type(payload) is not dict or set(payload) - expected or expected - set(payload) - optional:
        raise ValueError("Stored record fields do not match the contract")
    values = dict(payload)
    for name in ("requested_at", "expires_at", "applied_at", "available_at", "scheduled_at",
                 "published_at", "received_at", "started_at", "completed_at", "checked_at", "coverage_start", "coverage_end"):
        if name in values and values[name] is not None:
            values[name] = parse_time(values[name])
    if record_type is ControlRequest:
        values["settings"] = construct(IntelligenceSettings, values["settings"])
    if record_type is CalendarSnapshot:
        for name, child_type, maximum in (("sources", SourceCoverage, 50), ("events", CalendarEvent, 1000)):
            if type(values[name]) is not list or len(values[name]) > maximum:
                raise ValueError("Stored collection exceeds the contract")
            values[name] = tuple(construct(child_type, item) for item in values[name])
    if record_type is NewsBatch:
        if type(values["items"]) is not list or len(values["items"]) > 200:
            raise ValueError("Stored news items exceed the contract")
        values["items"] = tuple(construct(NewsEvidence, item) for item in values["items"])
    if record_type is AnalysisBrief:
        if type(values["claims"]) is not list or not 1 <= len(values["claims"]) <= 10 or type(values["uncertainties"]) is not list:
            raise ValueError("Invalid stored analysis lists")
        values["claims"] = tuple(construct(EvidenceClaim, item) for item in values["claims"])
        values["uncertainties"] = tuple(values["uncertainties"])
    if record_type is EvidenceClaim:
        if type(values["evidence_ids"]) is not list:
            raise ValueError("Invalid stored claim references")
        values["evidence_ids"] = tuple(values["evidence_ids"])
    for name in ("symbols", "categories"):
        if name in values:
            if type(values[name]) is not list:
                raise ValueError("Stored collection must be an array")
            values[name] = tuple(values[name])
    return record_type(**values)


def digest(payload):
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def decode(record_type: type[_Record], payload: object, expected_hash: object) -> _Record:
    if not isinstance(payload, str) or len(payload.encode("utf-8")) > MAX_PAYLOAD or digest(payload) != expected_hash:
        raise StoreError("storage_invalid")
    try:
        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("Duplicate stored JSON field")
                result[key] = value
            return result
        return construct(record_type, json.loads(payload, object_pairs_hook=unique_object))
    except (ValueError, TypeError, KeyError, RecursionError) as exc:
        raise StoreError("storage_invalid") from exc


def database_path(path):
    path = Path(path).absolute()
    if path.suffix.lower() != ".sqlite3" or path.name.lower() == "bot_ledger.sqlite3" or path.is_symlink():
        raise ValueError("Use a separate nonsymlink intelligence .sqlite3 database")
    return path


def require_rollback_header(path):
    try:
        with path.open("rb") as handle:
            header = handle.read(100)
    except OSError as exc:
        raise StoreError("storage_unavailable") from exc
    if len(header) != 100 or header[:16] != b"SQLite format 3\x00":
        raise StoreError("foreign_database")
    if header[18:20] != b"\x01\x01":
        raise StoreError("schema_unsupported")


@contextmanager
def connection(path, writable=False, expected_id=None):
    require_rollback_header(path)
    handle = None
    try:
        handle = sqlite3.connect(path.as_uri() + ("?mode=rw" if writable else "?mode=ro"),
                                 uri=True, timeout=0.25, isolation_level=None)
        handle.row_factory = sqlite3.Row
        if hasattr(handle, "setlimit"):
            handle.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, max(MAX_PAYLOAD, MAX_RAW_NEWS) + 8192)
        steps = 0
        def bounded_query():
            nonlocal steps
            steps += 1
            return int(steps > 200)
        handle.set_progress_handler(bounded_query, 1000)
        handle.execute("PRAGMA trusted_schema=OFF")
        handle.execute("PRAGMA foreign_keys=ON")
        handle.execute("PRAGMA recursive_triggers=ON")
        if not writable:
            handle.execute("PRAGMA query_only=ON")
        handle.execute("BEGIN IMMEDIATE" if writable else "BEGIN")
        if handle.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
            raise StoreError("foreign_database")
        version = handle.execute("PRAGMA user_version").fetchone()[0]
        if version not in (1, 2, 3, SCHEMA_VERSION):
            raise StoreError("schema_unsupported")
        if handle.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
            raise StoreError("schema_unsupported")
        schema = handle.execute(f"SELECT {bounded_column('type', 16)}, {bounded_column('name', 128)}, "
                                f"{bounded_column('sql', 8192)} FROM sqlite_master "
                                "WHERE name NOT LIKE 'sqlite_%' LIMIT 65").fetchall()
        actual_schema = {(row["type"], row["name"]): row["sql"] for row in schema}
        if actual_schema != storage_schema(version):
            raise StoreError("storage_invalid")
        identities = handle.execute(f"SELECT id, {bounded_column('database_id', 128)} FROM identity LIMIT 2").fetchall()
        if len(identities) != 1 or identities[0]["id"] != 1:
            raise StoreError("storage_invalid")
        identifier(identities[0]["database_id"])
        if expected_id is not None and identities[0]["database_id"] != expected_id:
            raise StoreError("identity_mismatch")
        yield handle
        handle.commit()
    except (sqlite3.Error, OSError, ValueError) as exc:
        raise StoreError("storage_unavailable") from exc
    finally:
        if handle is not None:
            handle.close()


def initialize(path, now, schema_version=SCHEMA_VERSION):
    utc_time(now)
    schema = storage_schema(schema_version)
    path = database_path(path)
    if path.exists():
        return IntelligenceStore(path)
    try:
        with path.open("xb"):
            pass
    except OSError as exc:
        raise StoreError("storage_unavailable") from exc
    handle = None
    try:
        handle = sqlite3.connect(path, isolation_level=None)
        handle.execute("PRAGMA journal_mode=DELETE")
        handle.execute("BEGIN IMMEDIATE")
        handle.execute(f"PRAGMA application_id={APPLICATION_ID}")
        handle.execute(f"PRAGMA user_version={schema_version}")
        for statement in schema.values():
            handle.execute(statement)
        payload = encode(IntelligenceSettings())
        handle.execute("INSERT INTO identity VALUES(1, ?)", ("intelligence-" + uuid.uuid4().hex,))
        handle.execute("INSERT INTO settings_history VALUES(0, ?, ?, ?)",
                       (payload, digest(payload), now.isoformat().replace("+00:00", "Z")))
        handle.execute("INSERT INTO settings_head VALUES(1, 0)")
        handle.commit()
    except (sqlite3.Error, OSError, ValueError) as exc:
        raise StoreError("storage_unavailable") from exc
    finally:
        if handle is not None:
            handle.close()
    return IntelligenceStore(path)


def upgrade(path):
    store = IntelligenceStore(path)
    from market_intelligence.service import ServiceLock
    lock = ServiceLock(store.path.with_suffix(".service.lock"))
    try:
        with connection(store.path, writable=True, expected_id=store.database_id) as handle:
            store._settings(handle)
            version = handle.execute("PRAGMA user_version").fetchone()[0]
            if version == 1:
                for statement in news_storage_schema().values():
                    handle.execute(statement)
            if version < 3:
                for statement in news_status_schema().values():
                    handle.execute(statement)
                handle.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            if version < 4:
                for statement in analysis_storage_schema().values():
                    handle.execute(statement)
                handle.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    finally:
        lock.close()
    return IntelligenceStore(path)


class IntelligenceStore:
    def __init__(self, path):
        self.path = database_path(path)
        with connection(self.path) as handle:
            self.database_id = handle.execute("SELECT database_id FROM identity WHERE id=1").fetchone()[0]
            self.schema_version = handle.execute("PRAGMA user_version").fetchone()[0]
            self._settings(handle)

    def _settings(self, handle: sqlite3.Connection) -> tuple[IntelligenceSettings, datetime]:
        rows = handle.execute(f"""SELECT history.revision,
            {bounded_column('history.payload', MAX_PAYLOAD)},
            {bounded_column('history.payload_hash', 64)}, {bounded_column('history.observed_at', 32)} FROM settings_head AS head
            JOIN settings_history AS history ON history.revision=head.revision WHERE head.id=1""").fetchall()
        if len(rows) != 1 or rows[0]["revision"] != handle.execute("SELECT MAX(revision) FROM settings_history").fetchone()[0]:
            raise StoreError("storage_invalid")
        settings = decode(IntelligenceSettings, rows[0]["payload"], rows[0]["payload_hash"])
        if settings.revision != rows[0]["revision"]:
            raise StoreError("storage_invalid")
        try:
            observed_at = parse_time(rows[0]["observed_at"])
        except ValueError as exc:
            raise StoreError("storage_invalid") from exc
        if settings.revision == 0:
            if settings != IntelligenceSettings():
                raise StoreError("storage_invalid")
        else:
            applied = handle.execute(f"SELECT {bounded_column('request_id', 128)} FROM commands WHERE applied_revision=?",
                                     (settings.revision,)).fetchone()
            if applied is None or applied[0] is None:
                raise StoreError("storage_invalid")
            record = self._command(handle, applied[0])
            if (record is None or record[0].settings != settings or record[1].status != "applied"
                    or record[1].applied_at != observed_at):
                raise StoreError("storage_invalid")
        return settings, observed_at

    def settings(self) -> IntelligenceSettings:
        with connection(self.path, expected_id=self.database_id) as handle:
            return self._settings(handle)[0]

    def _command(self, handle, request_id):
        row = handle.execute(f"""SELECT sequence, applied_revision, {bounded_column('status', 8)},
            {bounded_column('payload', MAX_PAYLOAD)}, {bounded_column('payload_hash', 64)},
            {bounded_column('receipt', MAX_PAYLOAD)}, {bounded_column('receipt_hash', 64)}, {bounded_column('enqueued_at', 32)}
            FROM commands WHERE request_id=?""", (request_id,)).fetchone()
        if row is None:
            return None
        request = decode(ControlRequest, row["payload"], row["payload_hash"])
        receipt = decode(ControlReceipt, row["receipt"], row["receipt_hash"])
        events = []
        for direction in ("ASC", "DESC"):
            event = handle.execute(f"SELECT {bounded_column('request_hash', 64)}, {bounded_column('receipt', MAX_PAYLOAD)}, "
                                   f"{bounded_column('receipt_hash', 64)}, {bounded_column('observed_at', 32)} FROM command_events "
                                   f"WHERE request_id=? ORDER BY sequence {direction} LIMIT 1", (request_id,)).fetchone()
            if event is None:
                raise StoreError("storage_invalid")
            event_receipt = decode(ControlReceipt, event["receipt"], event["receipt_hash"])
            try:
                observed_at = parse_time(event["observed_at"])
            except (ValueError, TypeError) as exc:
                raise StoreError("storage_invalid") from exc
            if (event["request_hash"] != row["payload_hash"] or event_receipt.request_id != request_id
                    or event_receipt.database_id != self.database_id):
                raise StoreError("storage_invalid")
            events.append((event_receipt, observed_at))
        try:
            enqueued_at = parse_time(row["enqueued_at"])
        except (ValueError, TypeError) as exc:
            raise StoreError("storage_invalid") from exc
        if (request.request_id != request_id or receipt.request_id != request_id or events[-1][0] != receipt
                or events[0][0].status not in ("pending", "expired") or events[0][1] != enqueued_at
                or enqueued_at < request.requested_at or events[-1][1] < enqueued_at or request.database_id != self.database_id
                or receipt.database_id != self.database_id or receipt.status != row["status"]):
            raise StoreError("storage_invalid")
        if ((receipt.status == "applied" and receipt.effective_revision != row["applied_revision"])
                or (receipt.status != "applied" and row["applied_revision"] is not None)):
            raise StoreError("storage_invalid")
        if receipt.status == "applied":
            history = handle.execute(f"SELECT {bounded_column('payload', MAX_PAYLOAD)}, {bounded_column('payload_hash', 64)}, "
                                     f"{bounded_column('observed_at', 32)} FROM settings_history WHERE revision=?",
                                     (receipt.effective_revision,)).fetchone()
            if (history is None or receipt.effective_revision != request.settings.revision
                    or decode(IntelligenceSettings, history["payload"], history["payload_hash"]) != request.settings
                    or parse_time(history["observed_at"]) != receipt.applied_at or events[-1][1] != receipt.applied_at
                    or not enqueued_at <= receipt.applied_at < request.expires_at):
                raise StoreError("storage_invalid")
        return request, receipt, row["payload_hash"], enqueued_at

    def receipt(self, request_id):
        identifier(request_id)
        with connection(self.path, expected_id=self.database_id) as handle:
            record = self._command(handle, request_id)
            return record[1] if record else None

    def enqueue(self, request, now):
        utc_time(now)
        if not isinstance(request, ControlRequest) or request.requested_at > now:
            raise ValueError("A validated, already observed control request is required")
        if request.database_id != self.database_id:
            raise StoreError("identity_mismatch")
        payload = encode(request)
        with connection(self.path, writable=True, expected_id=self.database_id) as handle:
            existing = self._command(handle, request.request_id)
            if existing:
                if existing[2] != digest(payload):
                    raise StoreError("request_conflict")
                return existing[1]
            if handle.execute("SELECT COUNT(*) FROM commands WHERE status='pending'").fetchone()[0] >= 100:
                raise StoreError("queue_full")
            status = "expired" if request.expires_at <= now else "pending"
            receipt = ControlReceipt(request.request_id, self.database_id, status, None, None,
                                     "Request expired" if status == "expired" else "Awaiting service")
            encoded_receipt = encode(receipt)
            handle.execute("INSERT INTO commands(request_id,payload,payload_hash,receipt,receipt_hash,status,enqueued_at) VALUES(?,?,?,?,?,?,?)",
                           (request.request_id, payload, digest(payload), encoded_receipt, digest(encoded_receipt), status,
                            now.isoformat().replace("+00:00", "Z")))
            self._event(handle, receipt, now)
            return receipt

    def _event(self, handle, receipt, now):
        payload = encode(receipt)
        request_hash = handle.execute(f"SELECT {bounded_column('payload_hash', 64)} FROM commands WHERE request_id=?",
                          (receipt.request_id,)).fetchone()[0]
        handle.execute("INSERT INTO command_events(request_id,request_hash,receipt,receipt_hash,observed_at) VALUES(?,?,?,?,?)",
                   (receipt.request_id, request_hash, payload, digest(payload), now.isoformat().replace("+00:00", "Z")))

    def apply_pending(self, now, profiles=(), limit=25):
        utc_time(now)
        nonnegative_integer(limit)
        if not 1 <= limit <= 100 or type(profiles) is not tuple or len(profiles) > 50:
            raise ValueError("Control batch and registered profiles must be bounded")
        for profile in profiles:
            identifier(profile)
        receipts = []
        with connection(self.path, writable=True, expected_id=self.database_id) as handle:
            settings, observed_at = self._settings(handle)
            if now < observed_at:
                raise StoreError("clock_invalid")
            pending = handle.execute(f"SELECT {bounded_column('request_id', 128)} FROM commands WHERE status='pending' "
                                     "ORDER BY sequence LIMIT ?", (limit,)).fetchall()
            for row in pending:
                try:
                    identifier(row[0])
                except ValueError as exc:
                    raise StoreError("storage_invalid") from exc
                request, old_receipt, _, enqueued_at = self._command(handle, row[0])
                if now < enqueued_at:
                    raise StoreError("clock_invalid")
                if old_receipt.status != "pending":
                    raise StoreError("storage_invalid")
                status, reason = "applied", "Settings applied"
                if request.expires_at <= now:
                    status, reason = "expired", "Request expired"
                elif request.requested_at > now:
                    status, reason = "rejected", "Request was not yet available"
                elif request.base_revision != settings.revision:
                    status, reason = "rejected", "Settings revision changed"
                elif request.settings.ai_profile is not None and request.settings.ai_profile not in profiles:
                    status, reason = "rejected", "AI profile is not registered"
                if status == "applied":
                    settings = request.settings
                    payload = encode(settings)
                    handle.execute("INSERT INTO settings_history VALUES(?,?,?,?)",
                                   (settings.revision, payload, digest(payload), now.isoformat().replace("+00:00", "Z")))
                    handle.execute("UPDATE settings_head SET revision=? WHERE id=1", (settings.revision,))
                receipt = ControlReceipt(request.request_id, self.database_id, status, settings.revision,
                                         now if status == "applied" else None, reason)
                payload = encode(receipt)
                handle.execute("UPDATE commands SET receipt=?,receipt_hash=?,status=?,applied_revision=? WHERE request_id=?",
                               (payload, digest(payload), status, settings.revision if status == "applied" else None,
                                request.request_id))
                self._event(handle, receipt, now)
                receipts.append(receipt)
        return tuple(receipts)

    def calendar(self):
        with connection(self.path, expected_id=self.database_id) as handle:
            return self._calendar(handle)

    def _calendar(self, handle):
        row = handle.execute(f"SELECT revision, {bounded_column('payload', MAX_PAYLOAD)}, "
                     f"{bounded_column('payload_hash', 64)} FROM calendar_history ORDER BY revision DESC LIMIT 1").fetchone()
        if row is None:
            return None
        snapshot = decode(CalendarSnapshot, row["payload"], row["payload_hash"])
        if snapshot.revision != row["revision"]:
            raise StoreError("storage_invalid")
        return snapshot

    def publish_calendar(self, snapshot, settings_revision, now):
        utc_time(now)
        nonnegative_integer(settings_revision)
        if not isinstance(snapshot, CalendarSnapshot) or snapshot.available_at > now:
            raise ValueError("Calendar must already be available")
        payload = encode(snapshot)
        with connection(self.path, writable=True, expected_id=self.database_id) as handle:
            settings, observed_at = self._settings(handle)
            if settings.revision != settings_revision:
                raise StoreError("settings_changed")
            if not settings.enabled or not settings.calendar_enabled:
                raise StoreError("feature_disabled")
            if now < observed_at or snapshot.available_at < observed_at:
                raise StoreError("clock_invalid")
            previous = self._calendar(handle)
            if snapshot.revision != (previous.revision + 1 if previous else 1):
                raise StoreError("snapshot_conflict")
            if previous and snapshot.available_at < previous.available_at:
                raise StoreError("clock_invalid")
            handle.execute("INSERT INTO calendar_history VALUES(?,?,?,?)",
                           (snapshot.revision, settings_revision, payload, digest(payload)))

    def _require_news_schema(self, handle):
        if handle.execute("PRAGMA user_version").fetchone()[0] < 2:
            raise StoreError("upgrade_required")

    def _news_body(self, handle, input_hash):
        row = handle.execute("SELECT CASE WHEN typeof(body)='blob' AND length(body)<=? THEN body END AS body "
                             "FROM news_payloads WHERE input_hash=?", (MAX_RAW_NEWS, input_hash)).fetchone()
        if row is None or not isinstance(row[0], bytes) or not row[0] or hashlib.sha256(row[0]).hexdigest() != input_hash:
            raise StoreError("storage_invalid")
        return row[0]

    def _news_item(self, handle, evidence_id):
        row = handle.execute(f"SELECT {bounded_column('payload', MAX_PAYLOAD)}, {bounded_column('payload_hash', 64)}, "
                             f"{bounded_column('cluster_id', 128)}, {bounded_column('first_acquisition_id', 128)} "
                             "FROM news_items WHERE evidence_id=?", (evidence_id,)).fetchone()
        if row is None:
            return None
        item = decode(NewsEvidence, row["payload"], row["payload_hash"])
        if item.evidence_id != evidence_id or item.cluster_id != row["cluster_id"] or row["first_acquisition_id"] is None:
            raise StoreError("storage_invalid")
        return item, row["first_acquisition_id"]

    def _news(self, handle, acquisition_id):
        row = handle.execute(f"SELECT revision, settings_revision, {bounded_column('source', 128)}, "
                             f"{bounded_column('received_at', 32)}, {bounded_column('available_at', 32)}, "
                             f"{bounded_column('parser_version', 32)}, {bounded_column('payload', MAX_PAYLOAD)}, "
                             f"{bounded_column('payload_hash', 64)}, {bounded_column('input_hash', 64)} "
                             "FROM news_acquisitions WHERE acquisition_id=?", (acquisition_id,)).fetchone()
        if row is None:
            return None
        batch = decode(NewsBatch, row["payload"], row["payload_hash"])
        try:
            record = ArchivedNews(acquisition_id, row["revision"], row["settings_revision"],
                                  parse_time(row["available_at"]), batch, row["parser_version"])
            if batch.received_at != parse_time(row["received_at"]) or batch.source != row["source"] or batch.input_hash != row["input_hash"]:
                raise StoreError("storage_invalid")
            history = handle.execute(f"SELECT {bounded_column('payload', MAX_PAYLOAD)}, {bounded_column('payload_hash', 64)}, "
                                     f"{bounded_column('observed_at', 32)} FROM settings_history WHERE revision=?",
                                     (record.settings_revision,)).fetchone()
            if history is None:
                raise StoreError("storage_invalid")
            settings = decode(IntelligenceSettings, history["payload"], history["payload_hash"])
            if (settings.revision != record.settings_revision or not settings.enabled or not settings.news_enabled
                    or parse_time(history["observed_at"]) > batch.received_at):
                raise StoreError("storage_invalid")
            applied = handle.execute(f"SELECT {bounded_column('request_id', 128)} FROM commands WHERE applied_revision=?",
                                     (record.settings_revision,)).fetchone()
            if applied is None or applied[0] is None:
                raise StoreError("storage_invalid")
            command = self._command(handle, applied[0])
            if command is None or command[0].settings != settings or command[1].status != "applied":
                raise StoreError("storage_invalid")
            raw = self._news_body(handle, batch.input_hash)
            if parse_fed_news(raw, batch.received_at) != batch:
                raise StoreError("storage_invalid")
        except (ValueError, TypeError, ProviderError) as exc:
            raise StoreError("storage_invalid") from exc
        for item in batch.items:
            stored = self._news_item(handle, item.evidence_id)
            if stored is None or stored[0].received_at > item.received_at or replace(stored[0], received_at=item.received_at) != item:
                raise StoreError("storage_invalid")
        return record

    def news_acquisition(self, acquisition_id):
        identifier(acquisition_id)
        with connection(self.path, expected_id=self.database_id) as handle:
            self._require_news_schema(handle)
            return self._news(handle, acquisition_id)

    def news_body(self, acquisition_id):
        identifier(acquisition_id)
        with connection(self.path, expected_id=self.database_id) as handle:
            self._require_news_schema(handle)
            record = self._news(handle, acquisition_id)
            return self._news_body(handle, record.batch.input_hash) if record else None

    def news_evidence(self, evidence_id):
        identifier(evidence_id)
        with connection(self.path, expected_id=self.database_id) as handle:
            self._require_news_schema(handle)
            stored = self._news_item(handle, evidence_id)
            if stored is None:
                return None
            original = self._news(handle, stored[1])
            if original is None or stored[0] not in original.batch.items:
                raise StoreError("storage_invalid")
            return stored[0]

    def publish_news(self, acquisition_id, batch, raw, settings_revision, now):
        identifier(acquisition_id)
        nonnegative_integer(settings_revision)
        utc_time(now)
        if not isinstance(batch, NewsBatch) or batch.received_at > now:
            raise ValueError("News must already have been received")
        if not isinstance(raw, bytes) or not raw or len(raw) > MAX_RAW_NEWS or hashlib.sha256(raw).hexdigest() != batch.input_hash:
            raise ValueError("Raw news must match its bounded source checksum")
        if parse_fed_news(raw, batch.received_at) != batch:
            raise ValueError("Normalized news must match the archived source")
        payload = encode(batch)
        with connection(self.path, writable=True, expected_id=self.database_id) as handle:
            return self._publish_news(handle, acquisition_id, batch, raw, settings_revision, now, payload)

    def _publish_news(self, handle, acquisition_id, batch, raw, settings_revision, now, payload):
        self._require_news_schema(handle)
        existing = self._news(handle, acquisition_id)
        if existing is not None:
            if existing.batch != batch or existing.settings_revision != settings_revision:
                raise StoreError("acquisition_conflict")
            return existing
        settings, observed_at = self._settings(handle)
        if settings.revision != settings_revision:
            raise StoreError("settings_changed")
        if not settings.enabled or not settings.news_enabled:
            raise StoreError("feature_disabled")
        if batch.received_at < observed_at:
            raise StoreError("clock_invalid")
        previous = handle.execute(f"SELECT {bounded_column('acquisition_id', 128)} FROM news_acquisitions ORDER BY revision DESC LIMIT 1").fetchone()
        prior = self._news(handle, previous[0]) if previous else None
        if prior and (now < prior.available_at or batch.received_at < prior.batch.received_at):
            raise StoreError("clock_invalid")
        if handle.execute("SELECT 1 FROM news_payloads WHERE input_hash=?", (batch.input_hash,)).fetchone() is None:
            handle.execute("INSERT INTO news_payloads VALUES(?,?)", (batch.input_hash, raw))
        elif self._news_body(handle, batch.input_hash) != raw:
            raise StoreError("storage_invalid")
        revision = prior.revision + 1 if prior else 1
        handle.execute("INSERT INTO news_acquisitions VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (revision, acquisition_id, settings_revision, batch.source,
                        batch.received_at.isoformat().replace("+00:00", "Z"), now.isoformat().replace("+00:00", "Z"),
                        "fed-rss-v1", payload, digest(payload), batch.input_hash))
        for item in batch.items:
            stored = self._news_item(handle, item.evidence_id)
            if stored is None:
                encoded = encode(item)
                handle.execute("INSERT INTO news_items VALUES(?,?,?,?,?)",
                               (item.evidence_id, item.cluster_id, acquisition_id, encoded, digest(encoded)))
            elif stored[0].received_at > item.received_at or replace(stored[0], received_at=item.received_at) != item:
                raise StoreError("evidence_conflict")
        return self._news(handle, acquisition_id)

    def _require_status_schema(self, handle):
        if handle.execute("PRAGMA user_version").fetchone()[0] < 3:
            raise StoreError("upgrade_required")

    def _news_observation(self, handle, attempt_id):
        row = handle.execute(f"SELECT revision, settings_revision, {bounded_column('source', 128)}, "
                             f"{bounded_column('acquisition_id', 128)}, {bounded_column('last_success_id', 128)}, "
                             f"{bounded_column('payload', MAX_PAYLOAD)}, {bounded_column('payload_hash', 64)} "
                             "FROM news_observations WHERE attempt_id=?", (attempt_id,)).fetchone()
        if row is None:
            return None
        observation = decode(NewsObservation, row["payload"], row["payload_hash"])
        if (observation.attempt_id != attempt_id or observation.revision != row["revision"]
                or observation.settings_revision != row["settings_revision"] or observation.source != row["source"]
                or observation.acquisition_id != row["acquisition_id"] or observation.last_success_id != row["last_success_id"]):
            raise StoreError("storage_invalid")
        command = handle.execute(f"SELECT {bounded_column('request_id', 128)} FROM commands WHERE applied_revision=?",
                                 (observation.settings_revision,)).fetchone()
        applied = self._command(handle, command[0]) if command and command[0] else None
        if (applied is None or not applied[0].settings.enabled or not applied[0].settings.news_enabled
                or applied[1].status != "applied" or applied[1].applied_at > observation.started_at):
            raise StoreError("storage_invalid")
        if observation.last_success_id is not None:
            last = self._news(handle, observation.last_success_id)
            if last is None or last.available_at > observation.available_at:
                raise StoreError("storage_invalid")
            if observation.status == "successful" and (last.settings_revision != observation.settings_revision
                    or last.available_at != observation.available_at
                    or not observation.started_at <= last.batch.received_at <= observation.completed_at):
                raise StoreError("storage_invalid")
        return observation

    def news_observation(self, attempt_id):
        identifier(attempt_id)
        with connection(self.path, expected_id=self.database_id) as handle:
            self._require_status_schema(handle)
            return self._news_observation(handle, attempt_id)

    def news_status(self) -> NewsObservation | None:
        with connection(self.path, expected_id=self.database_id) as handle:
            self._require_status_schema(handle)
            row = handle.execute(f"SELECT {bounded_column('attempt_id', 128)} FROM news_observations "
                                 "WHERE source='fed_monetary' ORDER BY revision DESC LIMIT 1").fetchone()
            return self._news_observation(handle, row[0]) if row else None

    def complete_news_attempt(self, attempt_id, settings_revision, started_at, completed_at, now,
                              batch=None, raw=None, error_code=None):
        identifier(attempt_id)
        nonnegative_integer(settings_revision)
        for value in (started_at, completed_at, now):
            utc_time(value)
        if not started_at <= completed_at <= now:
            raise ValueError("News attempt clock order is invalid")
        if error_code is None:
            if (not isinstance(batch, NewsBatch) or not started_at <= batch.received_at <= completed_at
                    or not isinstance(raw, bytes) or not raw or len(raw) > MAX_RAW_NEWS
                    or hashlib.sha256(raw).hexdigest() != batch.input_hash or parse_fed_news(raw, batch.received_at) != batch):
                raise ValueError("Successful news attempts require matching received evidence")
        elif batch is not None or raw is not None:
            raise ValueError("Failed news attempts cannot claim a new acquisition")
        status = "successful" if error_code is None else "invalid" if error_code in {
            "invalid_news", "article_refused", "content_refused", "encoding_refused", "response_too_large"} else "unavailable"
        NewsObservation(attempt_id, 1, settings_revision, started_at, completed_at, now,
                        status, error_code, attempt_id if batch else None, attempt_id if batch else None)
        with connection(self.path, writable=True, expected_id=self.database_id) as handle:
            self._require_status_schema(handle)
            existing = self._news_observation(handle, attempt_id)
            if existing is not None:
                if (existing.settings_revision != settings_revision or existing.started_at != started_at
                        or existing.completed_at != completed_at or existing.error_code != error_code):
                    raise StoreError("attempt_conflict")
                if batch is not None and self._news(handle, attempt_id).batch != batch:
                    raise StoreError("attempt_conflict")
                return existing
            settings, observed_at = self._settings(handle)
            if settings.revision != settings_revision:
                raise StoreError("settings_changed")
            if not settings.enabled or not settings.news_enabled:
                raise StoreError("feature_disabled")
            if started_at < observed_at:
                raise StoreError("clock_invalid")
            latest = handle.execute(f"SELECT {bounded_column('attempt_id', 128)} FROM news_observations "
                                    "WHERE source='fed_monetary' ORDER BY revision DESC LIMIT 1").fetchone()
            previous = self._news_observation(handle, latest[0]) if latest else None
            if previous and (started_at < previous.completed_at or now < previous.available_at):
                raise StoreError("clock_invalid")
            last = handle.execute(f"SELECT {bounded_column('acquisition_id', 128)} FROM news_acquisitions "
                                  "WHERE source='fed_monetary' ORDER BY revision DESC LIMIT 1").fetchone()
            last_success_id = last[0] if last else None
            if batch is not None:
                if self._news(handle, attempt_id) is not None:
                    raise StoreError("attempt_conflict")
                self._publish_news(handle, attempt_id, batch, raw, settings_revision, now, encode(batch))
                last_success_id = attempt_id
            observation = NewsObservation(attempt_id, previous.revision + 1 if previous else 1, settings_revision,
                                          started_at, completed_at, now, status, error_code,
                                          attempt_id if batch else None, last_success_id)
            payload = encode(observation)
            handle.execute("INSERT INTO news_observations VALUES(?,?,?,?,?,?,?,?)",
                           (observation.revision, attempt_id, observation.source, settings_revision,
                            observation.acquisition_id, last_success_id, payload, digest(payload)))
            return self._news_observation(handle, attempt_id)

    def reserve_analysis(self, now, input_bytes, limits, context):
        utc_time(now)
        nonnegative_integer(input_bytes)
        if not isinstance(limits, AnalysisLimits) or type(context) is not dict:
            raise ValueError("Validated analysis reservation is required")
        required = {"acquisition_id", "source_attempt_id", "settings_revision", "profile", "model", "cluster_id", "input_hash", "prompt_version"}
        if set(context) != required:
            raise ValueError("Invalid reservation context")
        for key in required - {"settings_revision", "model"}:
            identifier(context[key])
        payload = json.dumps(context, sort_keys=True, separators=(",", ":"), allow_nan=False)
        limits_hash = digest(encode(limits))
        accepted = False
        reservation_id = "reservation-" + uuid.uuid4().hex
        with connection(self.path, writable=True, expected_id=self.database_id) as handle:
            if handle.execute("PRAGMA user_version").fetchone()[0] < 4:
                raise StoreError("upgrade_required")
            settings, _ = self._settings(handle)
            if settings.revision != context["settings_revision"] or not settings.enabled or not settings.news_enabled or not settings.ai_enabled or settings.ai_profile != context["profile"]:
                raise StoreError("settings_changed")
            archive = self._news(handle, context["acquisition_id"])
            status = self._news_observation(handle, context["source_attempt_id"])
            latest_status = handle.execute(f"SELECT {bounded_column('attempt_id',128)} FROM news_observations ORDER BY revision DESC LIMIT 1").fetchone()
            if archive is None or status is None or status.status != "successful" or status.acquisition_id != archive.acquisition_id or latest_status[0] != status.attempt_id or not 0 <= (now-status.available_at).total_seconds() <= limits.max_source_age_seconds:
                raise StoreError("source_unavailable")
            _,actual_bytes,actual_hash=analysis_input(archive,context["cluster_id"])
            if input_bytes!=actual_bytes or context["input_hash"]!=actual_hash or input_bytes>limits.max_input_bytes:
                raise StoreError("reservation_invalid")
            latest = handle.execute(f"SELECT {bounded_column('observed_at',32)} FROM analysis_reservations ORDER BY sequence DESC LIMIT 1").fetchone()
            if latest and now < parse_time(latest[0]):
                raise StoreError("clock_invalid")
            day = now.date().isoformat()
            usage = handle.execute("SELECT COUNT(*), COALESCE(SUM(input_bytes),0), COALESCE(SUM(output_allowance),0) FROM analysis_reservations WHERE day=? AND accepted=1", (day,)).fetchone()
            prior_policy = handle.execute(f"SELECT {bounded_column('limits_hash',64)} FROM analysis_reservations WHERE day=? AND accepted=1 LIMIT 1", (day,)).fetchone()
            accepted = (not prior_policy or prior_policy[0] == limits_hash) and usage[0] < limits.daily_calls and usage[1]+input_bytes <= limits.daily_input_bytes and usage[2]+limits.max_output_tokens <= limits.daily_output_allowance
            handle.execute("INSERT INTO analysis_reservations VALUES(NULL,?,?,?,?,?,?,?,?,?)", (reservation_id, now.isoformat().replace("+00:00","Z"), day, int(accepted), input_bytes, limits.max_output_tokens, payload, digest(payload), limits_hash))
        if not accepted:
            raise StoreError("budget_exhausted")
        return reservation_id

    def analysis_usage(self, now):
        utc_time(now)
        with connection(self.path, expected_id=self.database_id) as handle:
            if handle.execute("PRAGMA user_version").fetchone()[0] < 4:
                return None
            row = handle.execute("SELECT COUNT(*),COALESCE(SUM(input_bytes),0),COALESCE(SUM(output_allowance),0) FROM analysis_reservations WHERE day=? AND accepted=1", (now.date().isoformat(),)).fetchone()
            return {"day": now.date().isoformat(), "calls": row[0], "input_bytes": row[1], "output_allowance": row[2]}

    def publish_brief(self, brief, now):
        if not isinstance(brief, AnalysisBrief) or brief.reservation_id is None:
            raise ValueError("Durably reserved validated brief is required")
        utc_time(now)
        with connection(self.path, writable=True, expected_id=self.database_id) as handle:
            row = handle.execute(f"SELECT accepted,{bounded_column('payload',32768)},{bounded_column('payload_hash',64)},{bounded_column('observed_at',32)} FROM analysis_reservations WHERE reservation_id=?", (brief.reservation_id,)).fetchone()
            if row is None or row[0] != 1 or not isinstance(row[1],str) or not isinstance(row[2],str) or digest(row[1]) != row[2]:
                raise StoreError("reservation_invalid")
            context = json.loads(row[1])
            settings, _ = self._settings(handle)
            if settings.revision != brief.settings_revision or not settings.ai_enabled or not settings.enabled or not settings.news_enabled or settings.ai_profile != brief.provider:
                raise StoreError("settings_changed")
            if any(context[name] != getattr(brief, name) for name in ("model", "cluster_id", "input_hash", "prompt_version", "settings_revision")) or context["profile"] != brief.provider or not parse_time(row[3]) <= brief.available_at <= now < brief.expires_at:
                raise StoreError("brief_invalid")
            archive = self._news(handle, context["acquisition_id"])
            status = self._news_observation(handle, context["source_attempt_id"])
            if archive is None or status is None or status.status != "successful":
                raise StoreError("source_unavailable")
            latest=handle.execute(f"SELECT {bounded_column('attempt_id',128)} FROM news_observations ORDER BY revision DESC LIMIT 1").fetchone()
            if not latest or latest[0]!=status.attempt_id or not 0<=(now-status.available_at).total_seconds()<=900:
                raise StoreError("source_unavailable")
            if analysis_input(archive,brief.cluster_id)[2]!=brief.input_hash:
                raise StoreError("brief_invalid")
            evidence = {item.evidence_id: item for item in archive.batch.items if item.cluster_id == brief.cluster_id}
            for claim in brief.claims:
                for reference in claim.evidence_ids:
                    item = evidence.get(reference)
                    if item is None or not (claim.text in item.title or claim.text in item.snippet):
                        raise StoreError("brief_invalid")
            old = handle.execute(f"SELECT {bounded_column('payload',MAX_PAYLOAD)},{bounded_column('payload_hash',64)} FROM analysis_briefs WHERE brief_id=?", (brief.brief_id,)).fetchone()
            if old:
                prior=decode(AnalysisBrief,old[0],old[1])
                if replace(prior,available_at=brief.available_at)!=brief:
                    raise StoreError("brief_conflict")
                return prior
            committed = replace(brief, available_at=now)
            payload = encode(committed)
            handle.execute("INSERT INTO analysis_briefs VALUES(?,?,?,?)", (brief.brief_id, brief.reservation_id, payload, digest(payload)))
            return committed

    def recent_briefs(self, limit=20):
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("Brief limit must be bounded")
        with connection(self.path, expected_id=self.database_id) as handle:
            if handle.execute("PRAGMA user_version").fetchone()[0] < 4:
                return ()
            rows = handle.execute(f"SELECT {bounded_column('brief_id',128)},{bounded_column('reservation_id',128)},{bounded_column('payload',MAX_PAYLOAD)},{bounded_column('payload_hash',64)} FROM analysis_briefs ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
            results=[]
            for row in rows:
                brief=decode(AnalysisBrief,row[2],row[3])
                if brief.brief_id!=row[0] or brief.reservation_id!=row[1]:
                    raise StoreError("storage_invalid")
                reservation=handle.execute(f"SELECT accepted,{bounded_column('payload',32768)},{bounded_column('payload_hash',64)},{bounded_column('observed_at',32)} FROM analysis_reservations WHERE reservation_id=?",(brief.reservation_id,)).fetchone()
                if not reservation or reservation[0]!=1 or not isinstance(reservation[1],str) or digest(reservation[1])!=reservation[2]:
                    raise StoreError("storage_invalid")
                context=json.loads(reservation[1])
                if any(context[name]!=getattr(brief,name) for name in ("settings_revision","model","cluster_id","input_hash","prompt_version")) or context["profile"]!=brief.provider or parse_time(reservation[3])>brief.available_at:
                    raise StoreError("storage_invalid")
                archive=self._news(handle,context["acquisition_id"])
                if archive is None or analysis_input(archive,brief.cluster_id)[2]!=brief.input_hash:
                    raise StoreError("storage_invalid")
                evidence={item.evidence_id:item for item in archive.batch.items}
                for claim in brief.claims:
                    for reference in claim.evidence_ids:
                        item=evidence.get(reference)
                        if item is None or not (claim.text in item.title or claim.text in item.snippet):
                            raise StoreError("storage_invalid")
                results.append(brief)
            return tuple(results)