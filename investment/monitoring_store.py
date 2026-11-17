"""Explicit, separately bound and purgeable observation cache, never a ledger."""

from contextlib import closing, contextmanager
import os
from pathlib import Path
import sqlite3
from datetime import timedelta

from . import InvestmentError
from .core import explicit_clock, fields, iso, text
from .monitoring import validate_observation
from .store import canonical, digest, parse_json

TABLES = {
    "monitor_identity": "CREATE TABLE monitor_identity (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)",
    "monitor_attempts": "CREATE TABLE monitor_attempts (revision INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL, payload TEXT NOT NULL)",
}
MAX_ATTEMPTS = 500
MAX_CACHE_BYTES = 32 * 1024 * 1024
MAX_ATTEMPT_BYTES = 256 * 1024
ERROR_CODES = ("provider_unavailable", "permission_denied", "invalid_response", "collection_interrupted")


def _binding(profile):
    return {"portfolio_id": profile["portfolio_id"], "profile_hash": digest(canonical(profile))}


@contextmanager
def _connection(path, write=False):
    path = Path(path).absolute()
    if not path.is_file() or path.stat().st_size > MAX_CACHE_BYTES:
        raise InvestmentError("Monitoring cache missing or exceeds size bound; explicit init required")
    try:
        with closing(sqlite3.connect(path.as_uri() + ("?mode=rw" if write else "?mode=ro"),
                                     uri=True, timeout=1)) as connection:
            if not write:
                connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            objects = connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master").fetchall()
            expected = {("table", name, name, sql) for name, sql in TABLES.items()}
            expected.add(("index", "sqlite_autoindex_monitor_attempts_1", "monitor_attempts", None))
            expected.add(("table", "sqlite_sequence", "sqlite_sequence",
                          "CREATE TABLE sqlite_sequence(name,seq)"))
            if (set(objects) != expected or len(objects) != len(expected)
                    or connection.execute("PRAGMA user_version").fetchone()[0] != 1
                    or connection.execute("PRAGMA journal_mode").fetchone()[0] != "delete"):
                raise InvestmentError("Unsupported or foreign monitoring cache schema")
            yield connection
            if write:
                connection.commit()
    except (sqlite3.Error, OSError) as exc:
        raise InvestmentError("Monitoring persistence failed; no success was published") from exc


def initialize_cache(path, profile, retention_seconds):
    if type(retention_seconds) is not int or not 1 <= retention_seconds <= 366 * 86400:
        raise InvestmentError("Explicit permitted retention must be 1 to 31622400 seconds")
    identity = {**_binding(profile), "retention_seconds": retention_seconds}
    path = Path(path)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
    except OSError as exc:
        raise InvestmentError("Cache initialization requires a new explicit path") from exc
    try:
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("PRAGMA user_version=1")
            for sql in TABLES.values():
                connection.execute(sql)
            connection.execute("INSERT INTO monitor_identity VALUES (1,?)", (canonical(identity),))
    except sqlite3.Error as exc:
        path.unlink(missing_ok=True)
        raise InvestmentError("Monitoring cache initialization failed") from exc


def _identity(connection, profile):
    rows = connection.execute("SELECT payload FROM monitor_identity WHERE id=1").fetchall()
    if len(rows) != 1:
        raise InvestmentError("Monitoring identity missing")
    identity = parse_json(rows[0][0])
    fields(identity, ("portfolio_id", "profile_hash", "retention_seconds"))
    if ({key: identity[key] for key in ("portfolio_id", "profile_hash")} != _binding(profile)
            or type(identity["retention_seconds"]) is not int
            or not 1 <= identity["retention_seconds"] <= 366 * 86400):
        raise InvestmentError("Monitoring cache profile binding or retention is invalid")
    return identity


def _validate_attempt(value, profile, now):
    fields(value, ("id", "account_id", "provider", "attempted_at", "status", "error_code", "observation"))
    for key in ("id", "account_id", "provider"):
        text(value[key])
    if explicit_clock(value["attempted_at"]) > now:
        raise InvestmentError("Future collection attempt")
    mapping = next((a for a in profile["accounts"] if a["account_id"] == value["account_id"]), None)
    if mapping is None or mapping["provider"] != value["provider"]:
        raise InvestmentError("Attempt is not bound to the monitoring account/provider")
    if value["status"] == "success":
        if value["error_code"] is not None or value["observation"] is None:
            raise InvestmentError("Success requires a validated observation")
        observed = validate_observation(value["observation"], now)
        if (observed["account_id"] != value["account_id"] or observed["provider"] != value["provider"]
                or explicit_clock(observed["available_at"]) > explicit_clock(value["attempted_at"])):
            raise InvestmentError("Observation does not match collection identity/clock")
    elif value["status"] == "failed":
        if value["observation"] is not None or value["error_code"] not in ERROR_CODES:
            raise InvestmentError("Failure requires a bounded diagnostic and no published batch")
    else:
        raise InvestmentError("Unknown attempt status")
    if len(canonical(value).encode("utf-8")) > MAX_ATTEMPT_BYTES:
        raise InvestmentError("Monitoring attempt exceeds size bound")


def append_attempt(path, profile, value, now):
    now = explicit_clock(now)
    _validate_attempt(value, profile, now)
    with _connection(path, write=True) as connection:
        _identity(connection, profile)
        existing = connection.execute("SELECT revision,payload FROM monitor_attempts WHERE id=?",
                                      (value["id"],)).fetchone()
        payload = canonical(value)
        if existing:
            if existing[1] != payload:
                raise InvestmentError("Conflicting monitoring attempt ID")
            return existing[0]
        rows = connection.execute("SELECT payload FROM monitor_attempts ORDER BY revision LIMIT ?",
                                  (MAX_ATTEMPTS + 1,)).fetchall()
        if len(rows) >= MAX_ATTEMPTS:
            raise InvestmentError("Monitoring cache full; explicitly purge expired evidence")
        if rows and explicit_clock(parse_json(rows[-1][0])["attempted_at"]) > explicit_clock(value["attempted_at"]):
            raise InvestmentError("Collection attempts must preserve clock order")
        for row in rows:
            earlier = parse_json(row[0])
            old, new = earlier["observation"], value["observation"]
            if (old and new and earlier["provider"] == value["provider"]
                    and earlier["account_id"] == value["account_id"] and old["id"] == new["id"]
                    and canonical(old) != canonical(new)):
                raise InvestmentError("Conflicting source observation ID; preserve revisions explicitly")
        revision = connection.execute("INSERT INTO monitor_attempts(id,payload) VALUES (?,?)",
                                      (value["id"], payload)).lastrowid
        page_count = connection.execute("PRAGMA page_count").fetchone()[0]
        page_size = connection.execute("PRAGMA page_size").fetchone()[0]
        if page_count * page_size > MAX_CACHE_BYTES:
            raise InvestmentError("Monitoring cache size limit reached; explicitly purge expired evidence")
        return revision


def read_cache(path, profile, now):
    now = explicit_clock(now)
    with _connection(path) as connection:
        identity = _identity(connection, profile)
        rows = connection.execute("SELECT revision,payload FROM monitor_attempts ORDER BY revision LIMIT ?",
                                  (MAX_ATTEMPTS + 1,)).fetchall()
        if len(rows) > MAX_ATTEMPTS:
            raise InvestmentError("Monitoring attempt limit exceeded")
        cutoff = now - timedelta(seconds=identity["retention_seconds"])
        attempts = []
        for revision, payload in rows:
            attempt = parse_json(payload)
            _validate_attempt(attempt, profile, now)
            if explicit_clock(attempt["attempted_at"]) > cutoff:
                attempts.append(attempt)
        return {"revision": rows[-1][0] if rows else 0, "attempts": attempts,
                "retention_seconds": identity["retention_seconds"]}


def purge_expired(path, profile, now):
    """Explicit cache maintenance; never reads or changes the investment journal."""
    now = explicit_clock(now)
    with _connection(path, write=True) as connection:
        identity = _identity(connection, profile)
        connection.execute("PRAGMA secure_delete=ON")
        rows = connection.execute("SELECT revision,payload FROM monitor_attempts").fetchall()
        cutoff = now - timedelta(seconds=identity["retention_seconds"])
        expired = [revision for revision, payload in rows
                   if explicit_clock(parse_json(payload)["attempted_at"]) <= cutoff]
        for revision in expired:
            connection.execute("DELETE FROM monitor_attempts WHERE revision=?", (revision,))
        return {"deleted_attempts": len(expired)}
