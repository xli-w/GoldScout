"""Separate explicit v1 SQLite store. Readers never initialize or repair."""

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from . import InvestmentError
from .core import MAX_EVENTS, clock, fmt, iso, profile, replay, text

MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_DATABASE_BYTES = 32 * 1024 * 1024


def trigger_sql(table, operation):
    return (f"CREATE TRIGGER immutable_{table}_{operation.lower()} "
            f"BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT, "
            "'immutable investment evidence'); END")


def canonical(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                          allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise InvestmentError("Invalid JSON-compatible investment input") from exc


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvestmentError("Duplicate JSON field")
        result[key] = value
    return result


def parse_json(raw):
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_JSON_BYTES:
        raise InvestmentError("Input exceeds JSON size bound")
    try:
        return json.loads(raw, object_pairs_hook=_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(
                              InvestmentError("Nonfinite JSON is unsupported")))
    except (ValueError, RecursionError) as exc:
        raise InvestmentError("Invalid JSON input") from exc


def load_json(path):
    try:
        with open(path, "rb") as handle:
            raw = handle.read(MAX_JSON_BYTES + 1)
        if len(raw) > MAX_JSON_BYTES:
            raise InvestmentError("Input exceeds JSON size bound")
        return parse_json(raw.decode("utf-8"))
    except (OSError, UnicodeError) as exc:
        raise InvestmentError("Unable to read explicit local JSON") from exc


def state_digest(ledger):
    value = {
        "cash": {k: fmt(v) for k, v in ledger.cash.items()},
        "holdings": [[*key, fmt(v)] for key, v in sorted(ledger.holdings.items())],
        "marks": [[*key, fmt(v[0]), iso(v[1]), iso(v[2])]
                  for key, v in sorted(ledger.marks.items())],
        "net": fmt(ledger.net), "fees": fmt(ledger.fees), "revision": ledger.revision,
        "last_time": iso(ledger.last_time), "units": fmt(ledger.units),
        "unit_price": fmt(ledger.unit_price), "peak": fmt(ledger.peak),
        "drawdown": fmt(ledger.drawdown), "performance_valid": ledger.performance_valid,
    }
    if ledger.monthly_review_at is not None:
        value["reviews"] = {
            "monthly": iso(ledger.monthly_review_at),
            "annual": iso(ledger.annual_review_at) if ledger.annual_review_at else None,
        }
    if ledger.has_portfolio_attestation:
        value["latest_available_at"] = iso(ledger.latest_available_at)
    return digest(canonical(value))


def _connect(database, readonly):
    path = Path(database).absolute()
    if not path.is_file():
        raise InvestmentError("Investment store missing; explicit init required")
    try:
        if path.stat().st_size > MAX_DATABASE_BYTES:
            raise InvestmentError("Investment database exceeds size bound")
        connection = sqlite3.connect(path.as_uri() + ("?mode=ro" if readonly else "?mode=rw"),
                                     uri=True, timeout=1)
        if readonly:
            connection.execute("PRAGMA query_only=ON")
        if connection.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
            connection.close()
            raise InvestmentError("Unsupported store journal mode")
        return connection
    except (sqlite3.Error, OSError) as exc:
        raise InvestmentError("Cannot open investment store") from exc


def initialize(database, spec, now=None):
    """Create only a new explicit path; never overwrite an existing file."""
    spec = parse_json(canonical(spec))
    ledger = replay(spec, [], now)
    path = Path(database)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
    except OSError as exc:
        raise InvestmentError("Init requires a new explicit database path") from exc
    try:
        with closing(sqlite3.connect(path)) as connection:
            with connection:
                connection.execute("PRAGMA user_version=1")
                connection.execute("CREATE TABLE identity (id INTEGER PRIMARY KEY CHECK(id=1), "
                                   "profile TEXT NOT NULL, hash TEXT NOT NULL)")
                connection.execute("CREATE TABLE events (revision INTEGER PRIMARY KEY, "
                                   "id TEXT UNIQUE NOT NULL, payload TEXT NOT NULL, "
                                   "previous_hash TEXT NOT NULL, hash TEXT NOT NULL)")
                connection.execute("CREATE TABLE commits (revision INTEGER PRIMARY KEY, "
                                   "state_hash TEXT NOT NULL)")
                for table in ("identity", "events", "commits"):
                    for operation in ("UPDATE", "DELETE"):
                        connection.execute(trigger_sql(table, operation))
                encoded = canonical(spec)
                connection.execute("INSERT INTO identity VALUES (1,?,?)", (encoded, digest(encoded)))
                connection.execute("INSERT INTO commits VALUES (0,?)", (state_digest(ledger),))
    except (sqlite3.Error, OSError) as exc:
        path.unlink(missing_ok=True)
        raise InvestmentError("Investment initialization failed") from exc


def _read(connection, now):
    if connection.execute("PRAGMA user_version").fetchone()[0] != 1:
        raise InvestmentError("Unsupported investment schema; no migrations")
    if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        raise InvestmentError("Investment integrity check failed")
    tables = {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    triggers = {row[0]: row[1] for row in connection.execute(
        "SELECT name,sql FROM sqlite_master WHERE type='trigger'")}
    expected_triggers = {f"immutable_{table}_{op.lower()}": trigger_sql(table, op)
                         for table in ("identity", "events", "commits")
                         for op in ("UPDATE", "DELETE")}
    if tables != {"identity", "events", "commits"} or triggers != expected_triggers:
        raise InvestmentError("Not an investment v1 store")
    identities = connection.execute("SELECT id,profile,hash FROM identity LIMIT 2").fetchall()
    if len(identities) != 1 or identities[0][0] != 1:
        raise InvestmentError("Invalid portfolio identity")
    _, encoded, previous = identities[0]
    if len(encoded) > MAX_JSON_BYTES or digest(encoded) != previous:
        raise InvestmentError("Portfolio binding hash mismatch")
    spec = profile(parse_json(encoded))
    rows = connection.execute(
        "SELECT revision,id,payload,previous_hash,hash FROM events ORDER BY revision LIMIT ?",
        (MAX_EVENTS + 1,)).fetchall()
    if len(rows) > MAX_EVENTS:
        raise InvestmentError("Store event count exceeds bound")
    events = []
    total_bytes = len(encoded)
    for revision, identifier, payload, old_hash, new_hash in rows:
        total_bytes += len(payload)
        if total_bytes > MAX_JSON_BYTES or revision != len(events) + 1:
            raise InvestmentError("Invalid bounded event journal")
        value = parse_json(payload)
        if canonical(value) != payload or value.get("id") != identifier:
            raise InvestmentError("Invalid original event evidence")
        if old_hash != previous or digest(previous + payload) != new_hash:
            raise InvestmentError("Investment event chain mismatch")
        events.append(value)
        previous = new_hash
    ledger = replay(spec, events, now)
    if ledger.revision != len(events):
        raise InvestmentError("Duplicate source event in store")
    commits = connection.execute(
        "SELECT revision,state_hash FROM commits ORDER BY revision LIMIT ?",
        (MAX_EVENTS + 2,)).fetchall()
    if not commits or commits[0][0] != 0 or commits[-1][0] != ledger.revision:
        raise InvestmentError("Uncommitted investment revision")
    if any(type(r) is not int or r < 0 or r > ledger.revision for r, _ in commits):
        raise InvestmentError("Invalid commit revision")
    if commits[-1][1] != state_digest(ledger):
        raise InvestmentError("Investment replay verification failed")
    return spec, events, ledger, previous


def read(database, now=None):
    now = clock(now)
    try:
        with closing(_connect(database, True)) as connection:
            connection.execute("BEGIN")
            return _read(connection, now)[2]
    except (sqlite3.Error, TypeError, AttributeError) as exc:
        raise InvestmentError("Invalid or unreadable investment store") from exc


def import_events(database, values, now=None):
    """Validate and replay the complete batch before an atomic append."""
    now = clock(now)
    values = parse_json(canonical(values))
    if not isinstance(values, list) or len(values) > MAX_EVENTS:
        raise InvestmentError("Explicit bounded event array required")
    try:
        with closing(_connect(database, False)) as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                spec, existing, old, previous = _read(connection, now)
                ids = {v["id"]: v for v in existing}
                additions = []
                for value in values:
                    if not isinstance(value, dict) or not isinstance(value.get("id"), str):
                        raise InvestmentError("Invalid source event")
                    if value["id"] in ids:
                        if value != ids[value["id"]]:
                            raise InvestmentError("Conflicting duplicate event")
                    else:
                        ids[value["id"]] = value
                        additions.append(value)
                ledger = replay(spec, existing + additions, now)
                if len(canonical(spec).encode("utf-8")) + len(
                        canonical(existing + additions).encode("utf-8")) > MAX_JSON_BYTES:
                    raise InvestmentError("Combined investment evidence exceeds size bound")
                for offset, value in enumerate(additions, old.revision + 1):
                    payload = canonical(value)
                    new_hash = digest(previous + payload)
                    connection.execute("INSERT INTO events VALUES (?,?,?,?,?)",
                                       (offset, value["id"], payload, previous, new_hash))
                    previous = new_hash
                if additions:
                    connection.execute("INSERT INTO commits VALUES (?,?)",
                                       (ledger.revision, state_digest(ledger)))
                return ledger.revision
    except (sqlite3.Error, TypeError, AttributeError, RecursionError) as exc:
        raise InvestmentError("Investment import failed; transaction rolled back") from exc


def acknowledge_review(database, identifier, kind, reviewed_revision, evidence, now=None):
    """Append a revision-bound manual review; never execute or approve an order."""
    now = clock(now)
    text(identifier)
    text(evidence)
    if kind not in ("monthly", "annual"):
        raise InvestmentError("Review kind must be monthly or annual")
    if type(reviewed_revision) is not int or not 0 <= reviewed_revision < MAX_EVENTS:
        raise InvestmentError("Invalid reviewed revision")
    ledger = read(database, now)
    previous = ledger.ids.get(identifier)
    if previous is not None:
        if (previous["type"] == "review_acknowledgement" and
                previous["review_kind"] == kind and
                previous["reviewed_revision"] == reviewed_revision and
                previous["evidence"] == evidence):
            return previous.copy()
        raise InvestmentError("Conflicting duplicate event")
    if ledger.revision != reviewed_revision:
        raise InvestmentError("Reviewed revision changed; review the current snapshot")
    if ledger.valuation_status(now) != "fresh":
        raise InvestmentError("Review requires a complete, fresh recorded valuation")
    _, as_of = ledger.valuation()
    value = {"id": identifier, "type": "review_acknowledgement",
             "timestamp": iso(now), "available_at": iso(now), "evidence": evidence,
             "review_kind": kind, "reviewed_revision": reviewed_revision,
             "valuation_as_of": iso(as_of)}
    import_events(database, [value], now)
    return value
