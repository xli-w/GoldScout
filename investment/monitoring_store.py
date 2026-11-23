"""Explicit, separately bound and purgeable observation cache, never a ledger."""

from contextlib import closing, contextmanager
import os
from pathlib import Path
import sqlite3
import tempfile
from datetime import timedelta
from uuid import uuid4

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


class CommittedMonitoringError(InvestmentError):
    def __init__(self, message="Monitoring change committed, but connection cleanup failed",
                 staged_path=None):
        super().__init__(message)
        self.revision = None
        self.staged_path = staged_path


class UncertainMonitoringError(InvestmentError):
    def __init__(self, message, staged_path=None):
        super().__init__(message)
        self.staged_path = staged_path


class StaleMonitoringCacheError(InvestmentError):
    pass


def _binding(profile):
    return {"portfolio_id": profile["portfolio_id"], "profile_hash": digest(canonical(profile))}


def _predecessor_hash(identity, revision, rows):
    return digest(canonical({
        "identity": {key: value for key, value in identity.items() if key != "cache_revision"},
        "revision": revision, "attempts": rows,
    }))


def _same_staging_file(path, identity):
    if identity is None:
        return False
    try:
        return os.path.samestat(path.lstat(), identity)
    except OSError:
        return False


def _remove_staging(staged, identity, published_path=None):
    if published_path is not None and not _same_staging_file(published_path, identity):
        raise InvestmentError("Published monitoring cache identity changed")
    try:
        current = staged.lstat()
    except FileNotFoundError:
        pass
    else:
        if identity is None or not os.path.samestat(current, identity):
            raise InvestmentError("Monitoring staging file identity changed")
        staged.unlink()
    staged.parent.rmdir()


@contextmanager
def _connection(path, write=False):
    path = Path(path).absolute()
    if not path.is_file() or path.stat().st_size > MAX_CACHE_BYTES:
        raise InvestmentError("Monitoring cache missing or exceeds size bound; explicit init required")
    committed = False
    commit_attempted = False
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
                commit_attempted = True
                connection.commit()
                committed = True
    except (sqlite3.Error, OSError) as exc:
        if committed:
            raise CommittedMonitoringError() from exc
        if commit_attempted:
            raise UncertainMonitoringError(
                "Monitoring commit outcome uncertain; inspect the cache before retrying"
            ) from exc
        raise InvestmentError("Monitoring persistence failed; no success was published") from exc


def initialize_cache(path, profile, retention_seconds):
    if type(retention_seconds) is not int or not 1 <= retention_seconds <= 366 * 86400:
        raise InvestmentError("Explicit permitted retention must be 1 to 31622400 seconds")
    identity = {**_binding(profile), "retention_seconds": retention_seconds,
                "instance_id": uuid4().hex, "cache_revision": 0}
    path = Path(path).absolute()
    if path.exists():
        raise InvestmentError("Cache initialization requires a new explicit path")
    try:
        directory = Path(tempfile.mkdtemp(prefix=".goldscout-monitor-", dir=path.parent))
    except OSError as exc:
        raise InvestmentError("Cache initialization requires a new explicit path") from exc
    staged = directory / "cache.sqlite3"
    committed = False
    commit_attempted = False
    published = False
    stage_identity = None
    try:
        descriptor = os.open(staged, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "wb") as staged_handle:
            stage_identity = os.fstat(staged_handle.fileno())
            if not _same_staging_file(staged, stage_identity):
                raise InvestmentError("Monitoring staging file identity changed")
            with closing(sqlite3.connect(staged.as_uri() + "?mode=rw", uri=True)) as connection:
                if not _same_staging_file(staged, stage_identity):
                    raise InvestmentError("Monitoring staging file identity changed")
                with connection:
                    connection.execute("PRAGMA user_version=1")
                    for sql in TABLES.values():
                        connection.execute(sql)
                    connection.execute("INSERT INTO monitor_identity VALUES (1,?)", (canonical(identity),))
                    commit_attempted = True
                    connection.commit()
                    committed = True
            if not _same_staging_file(staged, stage_identity):
                raise InvestmentError("Monitoring staging file identity changed")
            with os.scandir(directory) as entries:
                if any(entry.name != staged.name for entry in entries):
                    raise CommittedMonitoringError(
                        f"Monitoring cache committed in {staged}; preserve it and the staging "
                        f"directory {directory} because extra files remain",
                        staged_path=staged)
            try:
                os.link(staged, path)
                published = True
            except OSError as exc:
                raise CommittedMonitoringError(
                    f"Monitoring cache initialized in {staged}, but no-overwrite publication was not "
                    "confirmed; preserve the staged cache and inspect the requested path",
                    staged_path=staged) from exc
    except CommittedMonitoringError:
        raise
    except (sqlite3.Error, OSError, InvestmentError) as exc:
        if committed:
            preserved = staged if _same_staging_file(staged, stage_identity) else None
            publication = "published" if published else "committed before publication"
            raise CommittedMonitoringError(
                f"Monitoring cache initialization {publication}, but completion failed; "
                f"inspect staging path {staged}, directory {directory}, and requested path {path}",
                staged_path=preserved) from exc
        if commit_attempted:
            preserved = staged if _same_staging_file(staged, stage_identity) else None
            raise UncertainMonitoringError(
                f"Monitoring cache initialization commit outcome uncertain; inspect staging path "
                f"{staged}, directory {directory}, and its sidecars",
                staged_path=preserved
            ) from exc
        try:
            _remove_staging(staged, stage_identity)
        except (OSError, InvestmentError) as cleanup:
            raise InvestmentError(
                f"Cache initialization failed and staging cleanup was not confirmed; inspect {directory}"
            ) from cleanup
        raise InvestmentError("Monitoring cache initialization failed") from exc
    try:
        _remove_staging(staged, stage_identity, path)
    except (OSError, InvestmentError) as exc:
        preserved = next((candidate for candidate in (staged, path)
                          if _same_staging_file(candidate, stage_identity)), None)
        recovery = (f"preserve {preserved}" if preserved is not None
                    else f"the original cache path could not be verified; inspect {path}")
        raise CommittedMonitoringError(
            f"Monitoring cache published, but staging cleanup was not confirmed; {recovery} "
            f"and inspect {directory} and its sidecars",
            staged_path=preserved) from exc


def _identity(connection, profile):
    rows = connection.execute("SELECT payload FROM monitor_identity WHERE id=1").fetchall()
    if len(rows) != 1:
        raise InvestmentError("Monitoring identity missing")
    identity = parse_json(rows[0][0])
    allowed_identity_fields = (
        {"portfolio_id", "profile_hash", "retention_seconds"},
        {"portfolio_id", "profile_hash", "retention_seconds", "instance_id"},
        {"portfolio_id", "profile_hash", "retention_seconds", "instance_id", "cache_revision"},
    )
    if type(identity) is not dict or set(identity) not in allowed_identity_fields:
        raise InvestmentError("Monitoring cache identity is invalid")
    fields(identity, ("portfolio_id", "profile_hash", "retention_seconds")
           + (("instance_id",) if "instance_id" in identity else ())
           + (("cache_revision",) if "cache_revision" in identity else ()))
    if ({key: identity[key] for key in ("portfolio_id", "profile_hash")} != _binding(profile)
            or type(identity["retention_seconds"]) is not int
            or not 1 <= identity["retention_seconds"] <= 366 * 86400):
        raise InvestmentError("Monitoring cache profile binding or retention is invalid")
    if "instance_id" in identity and (
            not isinstance(identity["instance_id"], str) or len(identity["instance_id"]) != 32
            or any(char not in "0123456789abcdef" for char in identity["instance_id"])):
        raise InvestmentError("Monitoring cache instance identity is invalid")
    if "cache_revision" in identity and (
            "instance_id" not in identity
            or type(identity["cache_revision"]) is not int or identity["cache_revision"] < 0):
        raise InvestmentError("Monitoring cache revision identity is invalid")
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


def _attempt_payload(identifier, payload):
    value = parse_json(payload)
    if type(value) is not dict or value.get("id") != identifier:
        raise InvestmentError("Monitoring attempt ID does not match retained payload")
    return value


def append_attempt(path, profile, value, now, expected_instance_id=None,
                   expected_revision=None, expected_attempt_revision=None,
                   expected_predecessor_hash=None):
    now = explicit_clock(now)
    _validate_attempt(value, profile, now)
    revision = None
    try:
        with _connection(path, write=True) as connection:
            identity = _identity(connection, profile)
            if (expected_instance_id is not None
                    and identity.get("instance_id") != expected_instance_id):
                raise StaleMonitoringCacheError("Monitoring cache instance changed; stage preview is stale")
            if expected_predecessor_hash is not None:
                if (type(expected_revision) is not int or expected_revision < 0
                        or not isinstance(expected_predecessor_hash, str)):
                    raise InvestmentError("Invalid monitoring predecessor binding")
                if (type(identity.get("cache_revision")) is not int
                        or identity["cache_revision"] < expected_revision):
                    raise StaleMonitoringCacheError("Monitoring cache predecessor changed; stage preview is stale")
                predecessor = connection.execute(
                    "SELECT revision,id,payload FROM monitor_attempts WHERE revision<=? "
                    "ORDER BY revision LIMIT ?", (expected_revision, MAX_ATTEMPTS + 1)).fetchall()
                if (len(predecessor) > MAX_ATTEMPTS
                        or _predecessor_hash(identity, expected_revision, predecessor)
                        != expected_predecessor_hash):
                    raise StaleMonitoringCacheError("Monitoring cache predecessor changed; stage preview is stale")
            rows = connection.execute("SELECT id,payload FROM monitor_attempts ORDER BY revision LIMIT ?",
                                      (MAX_ATTEMPTS + 1,)).fetchall()
            if len(rows) > MAX_ATTEMPTS:
                raise InvestmentError("Monitoring attempt limit exceeded")
            earlier_attempts = [_attempt_payload(identifier, payload)
                                for identifier, payload in rows]
            existing = connection.execute("SELECT revision,payload FROM monitor_attempts WHERE id=?",
                                          (value["id"],)).fetchone()
            payload = canonical(value)
            if existing:
                if existing[1] != payload:
                    raise InvestmentError("Conflicting monitoring attempt ID")
                revision = existing[0]
                if (expected_attempt_revision is not None
                        and revision != expected_attempt_revision):
                    raise StaleMonitoringCacheError("Monitoring attempt revision changed; stage preview is stale")
            else:
                if (expected_revision is not None
                        and identity.get("cache_revision") != expected_revision):
                    raise StaleMonitoringCacheError("Monitoring cache revision changed; stage preview is stale")
                if len(rows) >= MAX_ATTEMPTS:
                    raise InvestmentError("Monitoring cache full; explicitly purge expired evidence")
                if earlier_attempts and explicit_clock(earlier_attempts[-1]["attempted_at"]) > explicit_clock(value["attempted_at"]):
                    raise InvestmentError("Collection attempts must preserve clock order")
                if ("cache_revision" in identity and rows
                        and identity["cache_revision"] < connection.execute(
                            "SELECT MAX(revision) FROM monitor_attempts").fetchone()[0]):
                    raise InvestmentError("Monitoring cache revision precedes retained evidence")
                for earlier in earlier_attempts:
                    old, new = earlier["observation"], value["observation"]
                    if (old and new and earlier["provider"] == value["provider"]
                            and earlier["account_id"] == value["account_id"] and old["id"] == new["id"]
                            and canonical(old) != canonical(new)):
                        raise InvestmentError("Conflicting source observation ID; preserve revisions explicitly")
                revision = connection.execute("INSERT INTO monitor_attempts(id,payload) VALUES (?,?)",
                                              (value["id"], payload)).lastrowid
                if (expected_attempt_revision is not None
                        and revision != expected_attempt_revision):
                    raise StaleMonitoringCacheError("Monitoring attempt revision changed; stage preview is stale")
                if "cache_revision" in identity:
                    if revision <= identity["cache_revision"]:
                        raise InvestmentError("Monitoring cache sequence did not advance")
                    identity["cache_revision"] = revision
                    connection.execute("UPDATE monitor_identity SET payload=? WHERE id=1",
                                       (canonical(identity),))
                page_count = connection.execute("PRAGMA page_count").fetchone()[0]
                page_size = connection.execute("PRAGMA page_size").fetchone()[0]
                if page_count * page_size > MAX_CACHE_BYTES:
                    raise InvestmentError("Monitoring cache size limit reached; explicitly purge expired evidence")
    except CommittedMonitoringError as exc:
        exc.revision = revision
        raise
    return revision


def read_cache(path, profile, now, *, include_cursor=False):
    now = explicit_clock(now)
    with _connection(path) as connection:
        identity = _identity(connection, profile)
        rows = connection.execute("SELECT revision,id,payload FROM monitor_attempts ORDER BY revision LIMIT ?",
                                  (MAX_ATTEMPTS + 1,)).fetchall()
        if len(rows) > MAX_ATTEMPTS:
            raise InvestmentError("Monitoring attempt limit exceeded")
        cutoff = now - timedelta(seconds=identity["retention_seconds"])
        attempts = []
        attempt_revisions = {}
        for revision, identifier, payload in rows:
            attempt = _attempt_payload(identifier, payload)
            _validate_attempt(attempt, profile, now)
            if explicit_clock(attempt["attempted_at"]) > cutoff:
                attempts.append(attempt)
                if include_cursor:
                    attempt_revisions[attempt["id"]] = revision
        revision = identity.get("cache_revision", rows[-1][0] if rows else 0)
        if rows and revision < rows[-1][0]:
            raise InvestmentError("Monitoring cache revision precedes retained evidence")
        result = {"revision": revision, "attempts": attempts,
                  "retention_seconds": identity["retention_seconds"],
                  "instance_id": (identity.get("instance_id")
                                  if "cache_revision" in identity else None)}
        if include_cursor:
            sequence = connection.execute(
                "SELECT seq FROM sqlite_sequence WHERE name='monitor_attempts' LIMIT 2").fetchall()
            if (len(sequence) > 1 or (sequence and (
                    type(sequence[0][0]) is not int or sequence[0][0] < 0))):
                raise InvestmentError("Monitoring cache sequence is invalid")
            next_revision = max(sequence[0][0] if sequence else 0,
                                rows[-1][0] if rows else 0) + 1
            if next_revision <= revision:
                raise InvestmentError("Monitoring cache sequence does not follow its revision")
            result["next_revision"] = next_revision
            result["attempt_revisions"] = attempt_revisions
            result["predecessor_hash"] = _predecessor_hash(identity, revision, rows)
        return result


def purge_expired(path, profile, now):
    """Explicit cache maintenance; never reads or changes the investment journal."""
    now = explicit_clock(now)
    with _connection(path, write=True) as connection:
        identity = _identity(connection, profile)
        connection.execute("PRAGMA secure_delete=ON")
        rows = connection.execute("SELECT revision,id,payload FROM monitor_attempts").fetchall()
        cutoff = now - timedelta(seconds=identity["retention_seconds"])
        expired = [revision for revision, identifier, payload in rows
                   if explicit_clock(_attempt_payload(identifier, payload)["attempted_at"]) <= cutoff]
        for revision in expired:
            connection.execute("DELETE FROM monitor_attempts WHERE revision=?", (revision,))
        return {"deleted_attempts": len(expired)}
