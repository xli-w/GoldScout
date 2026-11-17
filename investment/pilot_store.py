"""Temporary offline journal for synthetic investment-pilot recovery tests."""

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile

from . import InvestmentError
from .core import text
from .pilot import simulate_pilot_trace

MAX_EVENTS = 256
MAX_EVENT_BYTES = 64 * 1024
MAX_EVIDENCE_BYTES = 3 * 1024 * 1024
MAX_DATABASE_BYTES = 4 * 1024 * 1024
_SCHEMA_VERSION = 2
_IDENTITY = "synthetic-pilot-recovery-v2"
_TABLES = {
    "identity": "CREATE TABLE identity (id INTEGER PRIMARY KEY CHECK(id=1), "
                "value TEXT NOT NULL)",
    "events": "CREATE TABLE events (revision INTEGER PRIMARY KEY, "
              "event_id TEXT UNIQUE NOT NULL, payload TEXT NOT NULL, "
              "previous_hash TEXT NOT NULL, hash TEXT NOT NULL)",
    "commits": "CREATE TABLE commits (revision INTEGER PRIMARY KEY, "
               "state_hash TEXT NOT NULL)",
}


def _canonical(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise InvestmentError("Invalid synthetic pilot event") from exc


def _hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _event_hash(previous, event_id, payload):
    return _hash(previous + _canonical([event_id, payload]))


def _trigger(table, operation):
    return (f"CREATE TRIGGER immutable_{table}_{operation.lower()} "
            f"BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT, "
            "'immutable pilot evidence'); END")


def _connect(path, readonly):
    path = Path(path).absolute()
    if not path.is_file():
        raise InvestmentError("Synthetic pilot journal missing; explicit init required")
    connection = None
    try:
        if path.stat().st_size > MAX_DATABASE_BYTES:
            raise InvestmentError("Synthetic pilot journal exceeds size bound")
        connection = sqlite3.connect(
            path.as_uri() + ("?mode=ro" if readonly else "?mode=rw"), uri=True, timeout=1)
        if readonly:
            connection.execute("PRAGMA query_only=ON")
        if connection.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
            raise InvestmentError("Unsupported synthetic pilot journal mode")
        return connection
    except InvestmentError:
        if connection is not None:
            connection.close()
        raise
    except (sqlite3.Error, OSError) as exc:
        if connection is not None:
            connection.close()
        raise InvestmentError("Cannot open synthetic pilot journal") from exc


def _schema(connection):
    if connection.execute("PRAGMA user_version").fetchone()[0] != _SCHEMA_VERSION:
        raise InvestmentError("Unsupported synthetic pilot journal version")
    rows = connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master").fetchall()
    expected = {("table", table, table, sql) for table, sql in _TABLES.items()}
    expected.update(("trigger", f"immutable_{table}_{operation.lower()}", table,
                     _trigger(table, operation))
                    for table in _TABLES for operation in ("UPDATE", "DELETE"))
    expected.add(("index", "sqlite_autoindex_events_1", "events", None))
    if len(rows) != len(expected) or set(rows) != expected:
        raise InvestmentError("Invalid synthetic pilot journal schema")
    if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        raise InvestmentError("Synthetic pilot journal integrity check failed")


def _read(connection):
    _schema(connection)
    identity = connection.execute("SELECT id,value FROM identity").fetchall()
    if identity != [(1, _IDENTITY)]:
        raise InvestmentError("Synthetic pilot journal identity mismatch")
    rows = connection.execute(
        "SELECT revision,event_id,payload,previous_hash,hash FROM events "
        "ORDER BY revision LIMIT ?", (MAX_EVENTS + 1,)).fetchall()
    if len(rows) > MAX_EVENTS:
        raise InvestmentError("Synthetic pilot journal exceeds event limit")
    events, identifiers, previous, evidence_bytes = [], {}, "0" * 64, 0
    for revision, event_id, payload, old_hash, new_hash in rows:
        if (type(revision) is not int or type(event_id) is not str
                or type(payload) is not str or type(old_hash) is not str
                or type(new_hash) is not str):
            raise InvestmentError("Synthetic pilot journal row has invalid storage types")
        text(event_id)
        payload_bytes = len(payload.encode("utf-8"))
        evidence_bytes += payload_bytes + len(event_id.encode("utf-8"))
        if (revision != len(events) + 1 or payload_bytes > MAX_EVENT_BYTES
                or evidence_bytes > MAX_EVIDENCE_BYTES
                or old_hash != previous or _event_hash(previous, event_id, payload) != new_hash):
            raise InvestmentError("Synthetic pilot journal chain is invalid")
        try:
            event = json.loads(payload, parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError("nonfinite JSON")))
        except (ValueError, RecursionError) as exc:
            raise InvestmentError("Synthetic pilot journal event is invalid") from exc
        if not isinstance(event, dict) or _canonical(event) != payload:
            raise InvestmentError("Synthetic pilot journal event is not canonical")
        events.append(event)
        identifiers[event_id] = (revision, payload)
        previous = new_hash
    commits = connection.execute(
        "SELECT revision,state_hash FROM commits ORDER BY revision LIMIT ?",
        (MAX_EVENTS + 2,)).fetchall()
    if ([revision for revision, _ in commits] != list(range(len(events) + 1))):
        raise InvestmentError("Synthetic pilot journal has an uncommitted revision")
    if any(type(revision) is not int or type(state_hash) is not str
           or revision < 0 or revision > len(events)
           for revision, state_hash in commits):
        raise InvestmentError("Synthetic pilot journal has invalid commit boundaries")
    for revision, state_hash in commits:
        state = ({"state": "empty"} if revision == 0 else
                 simulate_pilot_trace(events[:revision]))
        if _hash(_canonical(state)) != state_hash:
            raise InvestmentError("Synthetic pilot journal replay does not match commit")
    return events, identifiers, previous, commits[-1][1]


def _create_schema(path):
    with closing(sqlite3.connect(path)) as connection:
        with connection:
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
            for sql in _TABLES.values():
                connection.execute(sql)
            for table in _TABLES:
                for operation in ("UPDATE", "DELETE"):
                    connection.execute(_trigger(table, operation))
            connection.execute("INSERT INTO identity VALUES (1,?)", (_IDENTITY,))
            initial = _hash(_canonical({"state": "empty"}))
            connection.execute("INSERT INTO commits VALUES (0,?)", (initial,))


def initialize(database):
    """Create an empty synthetic-only journal at a new path."""
    path = Path(database).absolute()
    directory = None
    temporary = None
    published = False
    try:
        directory = Path(tempfile.mkdtemp(prefix=".synthetic-pilot-", dir=path.parent))
        temporary = directory / "pilot.sqlite3"
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        os.close(fd)
        _create_schema(temporary)
        with closing(_connect(temporary, True)) as connection:
            connection.execute("BEGIN")
            if _read(connection)[0]:
                raise InvestmentError("New synthetic pilot journal was not empty")
        os.link(temporary, path)
        published = True
    except InvestmentError:
        raise
    except FileExistsError as exc:
        raise InvestmentError("Pilot journal init requires a new path") from exc
    except (sqlite3.Error, OSError, TypeError, ValueError) as exc:
        raise InvestmentError("Pilot journal initialization failed; no destination was published") from exc
    finally:
        if directory is not None:
            try:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
                directory.rmdir()
            except OSError as exc:
                status = "verified destination exists" if published else "no destination was published"
                raise InvestmentError(
                    f"Pilot journal initialization cleanup failed; {status}; "
                    f"inspect temporary directory {directory}") from exc


def read(database):
    """Read and replay an existing synthetic journal without mutation."""
    try:
        with closing(_connect(database, True)) as connection:
            connection.execute("BEGIN")
            return _read(connection)[0]
    except InvestmentError:
        raise
    except (sqlite3.Error, OSError, TypeError, ValueError) as exc:
        raise InvestmentError("Synthetic pilot journal cannot be read") from exc


def append_event(database, event_id, event):
    """Atomically append one synthetic event; identical event IDs are idempotent."""
    event_id = text(event_id)
    payload = _canonical(event)
    if len(payload.encode("utf-8")) > MAX_EVENT_BYTES:
        raise InvestmentError("Synthetic pilot event exceeds size limit")
    try:
        with closing(_connect(database, False)) as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                events, identifiers, previous, _ = _read(connection)
                existing = identifiers.get(event_id)
                if existing is not None:
                    if existing[1] != payload:
                        raise InvestmentError("Conflicting synthetic pilot event ID")
                    return existing[0]
                if len(events) >= MAX_EVENTS:
                    raise InvestmentError("Synthetic pilot journal exceeds event limit")
                evidence_bytes = sum(
                    len(existing_payload.encode("utf-8")) + len(identifier.encode("utf-8"))
                    for identifier, (revision, existing_payload) in identifiers.items())
                if evidence_bytes + len(payload.encode("utf-8")) > MAX_EVIDENCE_BYTES:
                    raise InvestmentError("Synthetic pilot journal exceeds evidence size limit")
                proposed = events + [event]
                state = simulate_pilot_trace(proposed)
                revision = len(proposed)
                new_hash = _event_hash(previous, event_id, payload)
                state_hash = _hash(_canonical(state))
                _append_rows(connection, revision, event_id, payload,
                             previous, new_hash, state_hash)
                return revision
    except InvestmentError:
        raise
    except (sqlite3.Error, OSError, TypeError, ValueError, RecursionError) as exc:
        raise InvestmentError("Synthetic pilot event append failed; transaction rolled back") from exc


def _append_rows(connection, revision, event_id, payload,
                 previous_hash, event_hash, state_hash):
    connection.execute("INSERT INTO events VALUES (?,?,?,?,?)",
                       (revision, event_id, payload, previous_hash, event_hash))
    connection.execute("INSERT INTO commits VALUES (?,?)", (revision, state_hash))
