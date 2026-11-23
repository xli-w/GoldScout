"""Separate explicit investment SQLite store. Readers never initialize or repair."""

from contextlib import ExitStack, closing
from copy import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from . import InvestmentError
from .core import MAX_EVENTS, clock, fmt, iso, profile, replay

MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_DATABASE_BYTES = 32 * 1024 * 1024
TABLE_SQL = {
    "identity": "CREATE TABLE identity (id INTEGER PRIMARY KEY CHECK(id=1), "
                "profile TEXT NOT NULL, hash TEXT NOT NULL)",
    "events": "CREATE TABLE events (revision INTEGER PRIMARY KEY, "
              "id TEXT UNIQUE NOT NULL, payload TEXT NOT NULL, "
              "previous_hash TEXT NOT NULL, hash TEXT NOT NULL)",
    "commits": "CREATE TABLE commits (revision INTEGER PRIMARY KEY, "
               "state_hash TEXT NOT NULL)",
}


class CommittedInvestmentError(InvestmentError):
    def __init__(self, action, revision, event_id=None, problem="connection cleanup failed"):
        super().__init__(f"Investment {action} at revision {revision}; "
                         f"{problem}. Preserve the journal before retrying.")
        self.revision = revision
        self.event_id = event_id


class UncertainInvestmentError(InvestmentError):
    pass


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
    # Keep the immutable state-hash projection; cash valuation time is replay-derived.
    if ledger.has_portfolio_attestation:
        value["latest_available_at"] = iso(ledger.latest_available_at)
    value["opening"] = {
        "opened": ledger.opened,
        "baseline_at": iso(ledger.baseline_at) if ledger.baseline_at else None,
        "baseline_value": fmt(ledger.baseline_value),
        "baseline_net": fmt(ledger.baseline_net),
    }
    if ledger.baseline_scope_additions != 0:
        value["opening"]["baseline_scope_additions"] = fmt(
            ledger.baseline_scope_additions)
    if ledger.profile_revision:
        value["profile_revision"] = ledger.profile_revision
        value["profile_hash"] = digest(canonical(ledger.spec))
        value["active_accounts"] = sorted(ledger.active_accounts)
        value["active_products"] = sorted(ledger.active_products)
        value["scope_additions"] = fmt(ledger.scope_additions)
    if ledger.historical_correction:
        value["historical_correction"] = True
        value["correction_available_at"] = iso(ledger.correction_available_at)
    if ledger.cash_observations:
        value["cash_observations"] = {
            account: observation
            for account, observation in sorted(ledger.cash_observations.items())
        }
    if ledger.updated_account_terms:
        value["account_terms"] = {
            account: ledger.account_terms[account]
            for account in sorted(ledger.updated_account_terms)
        }
        value["terms_last_time"] = iso(ledger.terms_last_time)
        value["terms_latest_available_at"] = iso(ledger.terms_latest_available_at)
    if ledger.role_active:
        value["role_accounting"] = {
            "cash": [[*key, fmt(amount)] for key, amount in sorted(ledger.role_cash.items())],
            "holdings": [[*key, fmt(quantity)]
                         for key, quantity in sorted(ledger.role_holdings.items())],
            "attributed_at": iso(ledger.role_last_time),
        }
    return digest(canonical(value))


def _connect(database, readonly):
    path = Path(database).absolute()
    if not path.is_file():
        raise InvestmentError("Investment store missing; explicit init required") from FileNotFoundError(path)
    try:
        if path.stat().st_size > MAX_DATABASE_BYTES:
            raise InvestmentError("Investment database exceeds size bound")
        with ExitStack() as stack:
            connection = sqlite3.connect(path.as_uri() + ("?mode=ro" if readonly else "?mode=rw"),
                                         uri=True, timeout=1)
            stack.callback(connection.close)
            if readonly:
                connection.execute("PRAGMA query_only=ON")
            if connection.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
                raise InvestmentError("Unsupported store journal mode")
            stack.pop_all()
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
    committed = False
    commit_attempted = False
    try:
        with closing(sqlite3.connect(path)) as connection:
            with connection:
                connection.execute(f"PRAGMA user_version={spec['schema_version']}")
                for sql in TABLE_SQL.values():
                    connection.execute(sql)
                for table in ("identity", "events", "commits"):
                    for operation in ("UPDATE", "DELETE"):
                        connection.execute(trigger_sql(table, operation))
                encoded = canonical(spec)
                connection.execute("INSERT INTO identity VALUES (1,?,?)", (encoded, digest(encoded)))
                connection.execute("INSERT INTO commits VALUES (0,?)", (state_digest(ledger),))
                commit_attempted = True
                connection.commit()
                committed = True
    except (sqlite3.Error, OSError) as exc:
        if committed:
            raise CommittedInvestmentError("initialized", 0) from exc
        if commit_attempted:
            raise UncertainInvestmentError(
                "Investment initialization commit outcome uncertain; preserve the journal and inspect before retrying"
            ) from exc
        path.unlink(missing_ok=True)
        raise InvestmentError("Investment initialization failed") from exc


def _read(connection, now):
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version != 2:
        raise InvestmentError("Only opening-evidence schema_version=2 is supported; no migrations")
    schema = connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master").fetchall()
    expected = {("table", table, table, sql) for table, sql in TABLE_SQL.items()}
    expected.update(("trigger", f"immutable_{table}_{op.lower()}", table, trigger_sql(table, op))
                    for table in TABLE_SQL for op in ("UPDATE", "DELETE"))
    expected.add(("index", "sqlite_autoindex_events_1", "events", None))
    if len(schema) != len(expected) or set(schema) != expected:
        raise InvestmentError("Not an investment store")
    if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        raise InvestmentError("Investment integrity check failed")
    identities = connection.execute("SELECT id,profile,hash FROM identity LIMIT 2").fetchall()
    if len(identities) != 1 or identities[0][0] != 1:
        raise InvestmentError("Invalid portfolio identity")
    _, encoded, previous = identities[0]
    if len(encoded) > MAX_JSON_BYTES or digest(encoded) != previous:
        raise InvestmentError("Portfolio binding hash mismatch")
    spec = profile(parse_json(encoded))
    if spec["schema_version"] != version:
        raise InvestmentError("Investment profile and store version disagree")
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
    commits = connection.execute(
        "SELECT revision,state_hash FROM commits ORDER BY revision LIMIT ?",
        (MAX_EVENTS + 2,)).fetchall()
    if not commits or commits[0][0] != 0 or commits[-1][0] != len(events):
        raise InvestmentError("Uncommitted investment revision")
    if any(type(r) is not int or r < 0 or r > len(events) for r, _ in commits):
        raise InvestmentError("Invalid commit revision")
    hashes = dict(commits)

    def verify_revision(current, timestamp):
        if current.revision not in hashes:
            return
        # Checkpoint changes only scalars; preserve the ongoing replay state.
        checkpoint = copy(current)
        if timestamp is not None:
            checkpoint.checkpoint(timestamp)
        if state_digest(checkpoint) != hashes[current.revision]:
            raise InvestmentError("Investment replay verification failed")

    ledger = replay(spec, events, now, verify_revision)
    if ledger.revision != len(events):
        raise InvestmentError("Duplicate source event in store")
    if commits[-1][1] != state_digest(ledger):
        raise InvestmentError("Investment replay verification failed")
    return spec, events, ledger, previous


def read_snapshot_with_clock(database, now=None, *, clock_source=None):
    if now is not None and clock_source is not None:
        raise InvestmentError("Supply either an observation time or a clock source")
    if now is not None:
        now = clock(now)
    try:
        with closing(_connect(database, True)) as connection:
            connection.execute("BEGIN")
            if now is None:
                # Pin the snapshot without executing an unvalidated store object.
                connection.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
                if clock_source is None:
                    now = clock()
                else:
                    observed = clock_source()
                    if observed is None:
                        raise InvestmentError("Clock source must return an observation time")
                    now = clock(observed)
            _, _, ledger, head = _read(connection, now)
            return ledger, now, head
    except (sqlite3.Error, TypeError, AttributeError) as exc:
        raise InvestmentError("Invalid or unreadable investment store") from exc


def read_with_clock(database, now=None, *, clock_source=None):
    ledger, observed, _ = read_snapshot_with_clock(database, now, clock_source=clock_source)
    return ledger, observed


def read(database, now=None):
    return read_with_clock(database, now)[0]


def _check_evidence_size(spec, events):
    if len(canonical(spec).encode("utf-8")) + len(
            canonical(events).encode("utf-8")) > MAX_JSON_BYTES:
        raise InvestmentError("Combined investment evidence exceeds size bound")


def _check_database_size(connection):
    page_count = connection.execute("PRAGMA page_count").fetchone()[0]
    page_size = connection.execute("PRAGMA page_size").fetchone()[0]
    if page_count * page_size > MAX_DATABASE_BYTES:
        raise InvestmentError("Investment database exceeds size bound; transaction rolled back")


def _append_events(connection, spec, existing, old, previous, values, now):
    ids = {v["id"]: v for v in existing}
    additions = []
    for value in values:
        if not isinstance(value, dict) or not isinstance(value.get("id"), str):
            raise InvestmentError("Invalid source event")
        if value["id"] in ids:
            if canonical(value) != canonical(ids[value["id"]]):
                raise InvestmentError("Conflicting duplicate event")
        else:
            ids[value["id"]] = value
            additions.append(value)
    ledger = replay(spec, existing + additions, now)
    if ledger.revision != old.revision + len(additions):
        raise InvestmentError("Investment event and replay revisions disagree")
    _check_evidence_size(spec, existing + additions)
    for offset, value in enumerate(additions, old.revision + 1):
        payload = canonical(value)
        new_hash = digest(previous + payload)
        connection.execute("INSERT INTO events VALUES (?,?,?,?,?)",
                           (offset, value["id"], payload, previous, new_hash))
        previous = new_hash
    if additions:
        connection.execute("INSERT INTO commits VALUES (?,?)",
                           (ledger.revision, state_digest(ledger)))
        _check_database_size(connection)
    return ledger.revision


def import_events(database, values, now=None):
    """Validate and replay the complete batch before an atomic append."""
    now = clock(now)
    values = parse_json(canonical(values))
    if not isinstance(values, list) or len(values) > MAX_EVENTS:
        raise InvestmentError("Explicit bounded event array required")
    committed = False
    commit_attempted = False
    try:
        with closing(_connect(database, False)) as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                spec, existing, old, previous = _read(connection, now)
                revision = _append_events(connection, spec, existing, old, previous, values, now)
                commit_attempted = True
                connection.commit()
                committed = True
        return revision
    except (sqlite3.Error, OSError, TypeError, AttributeError, RecursionError) as exc:
        if committed:
            raise CommittedInvestmentError("import completed", revision) from exc
        if commit_attempted:
            raise UncertainInvestmentError(
                "Investment import commit outcome uncertain; preserve the journal and inspect before retrying"
            ) from exc
        raise InvestmentError("Investment import failed; transaction rolled back") from exc


def preview_record(database, value, now=None):
    """Replay one event without writing; the caller must authenticate previews."""
    from .service import opening_report

    value = parse_json(canonical(value))
    try:
        with closing(_connect(database, True)) as connection:
            connection.execute("BEGIN")
            if now is None:
                connection.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
            now = clock(now)
            spec, existing, ledger, previous = _read(connection, now)
            if not isinstance(value, dict) or not isinstance(value.get("id"), str):
                raise InvestmentError("Invalid source event")
            if value["id"] in ledger.ids:
                raise InvestmentError("Event ID already recorded")
            _check_evidence_size(spec, existing + [value])
            updated = replay(spec, existing + [value], now)
            result = {
                "profile_hash": digest(canonical(ledger.spec)),
                "revision": ledger.revision,
                "previous_hash": previous,
                "event": value,
                "event_hash": digest(canonical(value)),
                "before": opening_report(ledger, now),
                "after": opening_report(updated, now),
            }
            if len(canonical(result).encode("utf-8")) > MAX_JSON_BYTES:
                raise InvestmentError("Investment preview exceeds size bound")
            return result
    except (sqlite3.Error, TypeError, AttributeError, RecursionError) as exc:
        raise InvestmentError("Investment preview failed") from exc


def _committed_record_receipt(connection, spec, existing, ledger, preview, now):
    value = preview["event"]
    expected = preview["revision"]
    if value["id"] not in ledger.ids:
        return None
    if (expected >= len(existing) or
            canonical(existing[expected]) != canonical(value) or
            connection.execute("SELECT 1 FROM commits WHERE revision=?",
                               (expected,)).fetchone() is None or
            connection.execute("SELECT 1 FROM commits WHERE revision=?",
                               (expected + 1,)).fetchone() is None or
            (digest(canonical(spec)) if expected == 0 else
             connection.execute("SELECT hash FROM events WHERE revision=?",
                                (expected,)).fetchone()[0]) != preview["previous_hash"]):
        raise InvestmentError("Conflicting duplicate event or revision")
    predecessor = replay(spec, existing[:expected], now)
    if digest(canonical(predecessor.spec)) != preview["profile_hash"]:
        raise InvestmentError("Investment profile changed")
    return {"id": value["id"], "revision": expected + 1}


def commit_record(database, preview, now=None):
    """Append a previewed event only against its exact journal predecessor.

    This is not an authentication or confirmation mechanism. A caller exposing
    it to a user must bind the entire preview to an authenticated confirmation.
    The profile hash is not a unique physical-store identity.
    """
    preview = parse_json(canonical(preview))
    if (not isinstance(preview, dict) or set(preview) != {
            "profile_hash", "revision", "previous_hash", "event", "event_hash", "before", "after"}
            or type(preview["revision"]) is not int or
            not 0 <= preview["revision"] < MAX_EVENTS or
            not isinstance(preview["event"], dict) or
            not isinstance(preview["event"].get("id"), str) or
            preview["event_hash"] != digest(canonical(preview["event"]))):
        raise InvestmentError("Invalid preview binding")
    committed = False
    commit_attempted = False
    try:
        with closing(_connect(database, False)) as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                now = clock(now)
                spec, existing, ledger, previous = _read(connection, now)
                expected = preview["revision"]
                value = preview["event"]
                receipt = _committed_record_receipt(
                    connection, spec, existing, ledger, preview, now)
                if receipt is None:
                    if digest(canonical(ledger.spec)) != preview["profile_hash"]:
                        raise InvestmentError("Investment profile changed")
                    if ledger.revision != expected or previous != preview["previous_hash"]:
                        raise InvestmentError("Investment revision changed; preview again")
                    revision = _append_events(connection, spec, existing, ledger, previous, [value], now)
                    receipt = {"id": value["id"], "revision": revision}
                commit_attempted = True
                connection.commit()
                committed = True
        return receipt
    except (sqlite3.Error, OSError, TypeError, AttributeError, RecursionError) as exc:
        if committed:
            raise CommittedInvestmentError(
                "record committed", receipt["revision"], receipt["id"]) from exc
        if commit_attempted:
            try:
                with closing(_connect(database, True)) as connection:
                    connection.execute("BEGIN")
                    spec, existing, ledger, _ = _read(connection, now)
                    receipt = _committed_record_receipt(
                        connection, spec, existing, ledger, preview, now)
            except (InvestmentError, sqlite3.Error, OSError, TypeError, AttributeError) as verification:
                raise UncertainInvestmentError(
                    "Investment commit outcome uncertain; exact verification failed; preserve the journal"
                ) from verification
            if receipt is not None:
                raise CommittedInvestmentError(
                    "record committed", receipt["revision"], receipt["id"],
                    "commit result failed but the exact record was verified") from exc
            raise UncertainInvestmentError(
                "Investment commit outcome uncertain; preserve the journal and inspect before retrying"
            ) from exc
        raise InvestmentError("Investment commit failed; transaction rolled back") from exc
