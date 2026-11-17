"""Durable local review workflow for monitoring findings; never accounting evidence."""

from contextlib import closing, contextmanager
import hashlib
import os
from pathlib import Path
import sqlite3

from . import InvestmentError
from .core import explicit_clock, iso, text
from .store import canonical, digest, parse_json

MAX_DATABASE_BYTES = 16 * 1024 * 1024
MAX_CASES = 5000
MAX_ACTIONS = 10000
MAX_SYNCS = 10000
MAX_NOTE_LENGTH = 500

TABLES = {
    "review_identity": (
        "CREATE TABLE review_identity (id INTEGER PRIMARY KEY CHECK(id=1), "
        "payload TEXT NOT NULL)"),
    "review_cases": (
        "CREATE TABLE review_cases (case_id TEXT PRIMARY KEY, finding_key TEXT NOT NULL, "
        "generation INTEGER NOT NULL, finding_id TEXT NOT NULL, fingerprint TEXT NOT NULL, "
        "payload TEXT NOT NULL, lifecycle TEXT NOT NULL, first_seen_at TEXT NOT NULL, "
        "last_seen_at TEXT NOT NULL, source_portfolio_revision INTEGER NOT NULL, "
        "source_cache_revision INTEGER, UNIQUE(finding_key,generation))"),
    "review_actions": (
        "CREATE TABLE review_actions (revision INTEGER PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, "
        "case_id TEXT NOT NULL, action TEXT NOT NULL, action_at TEXT NOT NULL, note TEXT NOT NULL)"),
    "review_syncs": (
        "CREATE TABLE review_syncs (revision INTEGER PRIMARY KEY, report_hash TEXT NOT NULL, "
        "synced_at TEXT NOT NULL, source_portfolio_revision INTEGER NOT NULL, "
        "source_cache_revision INTEGER, active_count INTEGER NOT NULL)"),
}
TRIGGERS = {
    "immutable_review_actions_update": (
        "CREATE TRIGGER immutable_review_actions_update BEFORE UPDATE ON review_actions "
        "BEGIN SELECT RAISE(ABORT, 'immutable review evidence'); END"),
    "immutable_review_actions_delete": (
        "CREATE TRIGGER immutable_review_actions_delete BEFORE DELETE ON review_actions "
        "BEGIN SELECT RAISE(ABORT, 'immutable review evidence'); END"),
    "immutable_review_syncs_update": (
        "CREATE TRIGGER immutable_review_syncs_update BEFORE UPDATE ON review_syncs "
        "BEGIN SELECT RAISE(ABORT, 'immutable review evidence'); END"),
    "immutable_review_syncs_delete": (
        "CREATE TRIGGER immutable_review_syncs_delete BEFORE DELETE ON review_syncs "
        "BEGIN SELECT RAISE(ABORT, 'immutable review evidence'); END"),
}


def _identity(portfolio_id, profile_hash, cache_binding=None):
    text(portfolio_id)
    if not isinstance(profile_hash, str) or len(profile_hash) != 64:
        raise InvestmentError("Invalid monitoring profile binding")
    try:
        int(profile_hash, 16)
    except ValueError as exc:
        raise InvestmentError("Invalid monitoring profile binding") from exc
    if cache_binding is not None:
        if not isinstance(cache_binding, str) or len(cache_binding) != 64:
            raise InvestmentError("Invalid monitoring cache binding")
        try:
            int(cache_binding, 16)
        except ValueError as exc:
            raise InvestmentError("Invalid monitoring cache binding") from exc
    return {"schema_version": 1, "portfolio_id": portfolio_id,
            "profile_hash": profile_hash, "cache_binding": cache_binding,
            "revision": 0, "last_sync_hash": None}


def _validate_note(note):
    if (not isinstance(note, str) or len(note) > MAX_NOTE_LENGTH
            or any(ord(char) < 32 and char not in "\t\n" for char in note)):
        raise InvestmentError("Review note must be at most 500 characters without control characters")


@contextmanager
def _connection(path, write=False):
    path = Path(path).absolute()
    if not path.is_file() or path.stat().st_size > MAX_DATABASE_BYTES:
        raise InvestmentError("Review store missing or exceeds size bound; explicit init required")
    try:
        with closing(sqlite3.connect(path.as_uri() + ("?mode=rw" if write else "?mode=ro"),
                                     uri=True, timeout=1)) as connection:
            if not write:
                connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            objects = connection.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master").fetchall()
            expected = {("table", name, name, sql) for name, sql in TABLES.items()}
            expected.update({
                ("index", "sqlite_autoindex_review_cases_1", "review_cases", None),
                ("index", "sqlite_autoindex_review_cases_2", "review_cases", None),
                ("index", "sqlite_autoindex_review_actions_1", "review_actions", None),
            })
            expected.update(("trigger", name, name.removeprefix("immutable_").rsplit("_", 1)[0],
                             sql) for name, sql in TRIGGERS.items())
            if (set(objects) != expected or len(objects) != len(expected)
                    or connection.execute("PRAGMA user_version").fetchone()[0] != 1
                    or connection.execute("PRAGMA journal_mode").fetchone()[0] != "delete"
                    or connection.execute("PRAGMA quick_check").fetchone()[0] != "ok"):
                raise InvestmentError("Unsupported or foreign review-store schema")
            yield connection
            if write:
                connection.commit()
    except (sqlite3.Error, OSError) as exc:
        raise InvestmentError("Review-store persistence failed; no success was published") from exc


def initialize_review_store(path, portfolio_id, profile_hash, cache_binding=None):
    """Create a new explicitly selected local workflow store."""
    identity = _identity(portfolio_id, profile_hash, cache_binding)
    path = Path(path)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
    except OSError as exc:
        raise InvestmentError("Review-store init requires a new explicit path") from exc
    try:
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("PRAGMA user_version=1")
            for sql in TABLES.values():
                connection.execute(sql)
            for sql in TRIGGERS.values():
                connection.execute(sql)
            connection.execute("INSERT INTO review_identity VALUES (1,?)",
                               (canonical(identity),))
    except (sqlite3.Error, OSError) as exc:
        path.unlink(missing_ok=True)
        raise InvestmentError("Review-store initialization failed") from exc


def monitoring_cache_binding(path, instance_id=None):
    if path is None:
        if instance_id is not None:
            raise InvestmentError("A cache instance requires an explicit cache path")
        return None
    if (not isinstance(instance_id, str) or len(instance_id) != 32
            or any(char not in "0123456789abcdef" for char in instance_id)):
        raise InvestmentError("Review workflow requires an identified monitoring cache instance")
    return digest(canonical({"path": str(Path(path).resolve()), "instance_id": instance_id}))


def _read_identity(connection):
    rows = connection.execute("SELECT payload FROM review_identity WHERE id=1").fetchall()
    if len(rows) != 1:
        raise InvestmentError("Review-store identity missing")
    value = parse_json(rows[0][0])
    if (type(value) is not dict
            or set(value) != {"schema_version", "portfolio_id", "profile_hash",
                              "cache_binding",
                              "revision", "last_sync_hash"}
            or value["schema_version"] != 1
            or type(value["revision"]) is not int or value["revision"] < 0
            or value["last_sync_hash"] is not None
            and (not isinstance(value["last_sync_hash"], str)
                 or len(value["last_sync_hash"]) != 64)):
        raise InvestmentError("Invalid review-store identity")
    expected = _identity(value["portfolio_id"], value["profile_hash"], value["cache_binding"])
    if (value["schema_version"] != expected["schema_version"]
            or value["portfolio_id"] != expected["portfolio_id"]
            or value["profile_hash"] != expected["profile_hash"]
            or value["cache_binding"] != expected["cache_binding"]):
        raise InvestmentError("Invalid review-store identity")
    if value["last_sync_hash"] is not None:
        try:
            int(value["last_sync_hash"], 16)
        except ValueError as exc:
            raise InvestmentError("Invalid review-store sync hash") from exc
    return value


def _validate_store(connection, identity):
    cases = connection.execute(
        "SELECT case_id,finding_key,generation,finding_id,fingerprint,payload,lifecycle,"
        "first_seen_at,last_seen_at,source_portfolio_revision,source_cache_revision "
        "FROM review_cases").fetchall()
    actions = connection.execute(
        "SELECT revision,request_id,case_id,action,action_at,note FROM review_actions").fetchall()
    syncs = connection.execute(
        "SELECT revision,report_hash,synced_at,source_portfolio_revision,"
        "source_cache_revision,active_count FROM review_syncs").fetchall()
    if len(cases) > MAX_CASES or len(actions) > MAX_ACTIONS or len(syncs) > MAX_SYNCS:
        raise InvestmentError("Review store exceeds its record limit")
    case_ids = set()
    action_revisions = set()
    sync_revisions = set()
    for row in cases:
        case_id, finding_key, generation, finding_id, fingerprint, raw, lifecycle = row[:7]
        payload = parse_json(raw)
        if (type(generation) is not int or generation < 1
                or lifecycle not in ("active", "inactive", "superseded")
                or type(payload) is not dict
                or set(payload) != {"id", "rule", "rule_version", "subject", "status",
                                    "severity", "explanation", "calculation",
                                    "recommendation", "evidence_sha256"}
                or payload["id"] != finding_id
                or type(payload["rule_version"]) is not int or payload["rule_version"] < 1
                or payload["status"] not in ("review", "unavailable")
                or payload["severity"] != "warning"
                or payload["calculation"] is not None
                and not isinstance(payload["calculation"], str)
                or not isinstance(payload["evidence_sha256"], str)
                or len(payload["evidence_sha256"]) != 64):
            raise InvestmentError("Invalid persisted review case")
        for key in ("id", "rule", "subject", "status", "severity",
                    "explanation", "recommendation"):
            text(payload[key])
        if payload["calculation"] is not None and len(payload["calculation"]) > 10000:
            raise InvestmentError("Invalid persisted review calculation")
        try:
            int(payload["evidence_sha256"], 16)
        except ValueError as exc:
            raise InvestmentError("Invalid persisted evidence reference") from exc
        expected_fingerprint = digest(canonical(payload))
        expected_key = digest(canonical({"finding_id": finding_id,
                                         "fingerprint": expected_fingerprint}))
        expected_case = digest(canonical({
            "portfolio_id": identity["portfolio_id"],
            "profile_hash": identity["profile_hash"],
            "finding_key": finding_key, "generation": generation,
        }))
        if (fingerprint != expected_fingerprint or finding_key != expected_key
                or case_id != expected_case or case_id in case_ids):
            raise InvestmentError("Review case integrity validation failed")
        if (type(row[9]) is not int or row[9] < 0
                or row[10] is not None and (type(row[10]) is not int or row[10] < 0)
                or explicit_clock(row[7]) > explicit_clock(row[8])):
            raise InvestmentError("Invalid review case clocks or source revisions")
        case_ids.add(case_id)
    for row in actions:
        revision, request_id, case_id, action, action_at, note = row
        text(request_id)
        if (type(revision) is not int or revision < 1 or revision in action_revisions
                or case_id not in case_ids
                or action not in ("acknowledged", "deferred", "reviewed")
                or not isinstance(note, str) or len(note) > MAX_NOTE_LENGTH):
            raise InvestmentError("Invalid persisted review action")
        _validate_note(note)
        explicit_clock(action_at)
        action_revisions.add(revision)
    for row in syncs:
        revision, report_hash, synced_at, portfolio_revision, cache_revision, active_count = row
        if (type(revision) is not int or revision < 1 or revision in sync_revisions
                or not isinstance(report_hash, str) or len(report_hash) != 64
                or type(portfolio_revision) is not int or portfolio_revision < 0
                or cache_revision is not None and (type(cache_revision) is not int or cache_revision < 0)
                or type(active_count) is not int or not 0 <= active_count <= MAX_CASES):
            raise InvestmentError("Invalid persisted review sync")
        if (identity["cache_binding"] is None) != (cache_revision is None):
            raise InvestmentError("Review sync cache binding changed")
        explicit_clock(synced_at)
        sync_revisions.add(revision)
    revisions = sorted(action_revisions | sync_revisions)
    if (len(revisions) != len(action_revisions) + len(sync_revisions)
            or revisions != list(range(1, identity["revision"] + 1))):
        raise InvestmentError("Review-store revision history is incomplete")
    latest_sync = max(sync_revisions) if sync_revisions else None
    expected_hash = None
    if latest_sync is not None:
        expected_hash = next(row[1] for row in syncs if row[0] == latest_sync)
    if identity["last_sync_hash"] != expected_hash:
        raise InvestmentError("Review-store sync identity does not match history")


def _validated_report(report, portfolio_id, profile_hash, now):
    if (type(report) is not dict or report.get("enabled") is not True
            or report.get("source_mode") != "recorded"
            or report.get("portfolio_id") != portfolio_id
            or report.get("profile_hash") != profile_hash
            or type(report.get("portfolio_revision")) is not int
            or report["portfolio_revision"] < 0
            or type(report.get("findings")) is not list
            or len(report["findings"]) > MAX_CASES):
        raise InvestmentError("Only a current recorded monitoring report bound to this profile can be synced")
    generated_at = explicit_clock(report.get("generated_at"))
    if generated_at > now:
        raise InvestmentError("Monitoring report is from the future")
    cache_revision = report.get("cache_revision")
    if cache_revision is not None and (type(cache_revision) is not int or cache_revision < 0):
        raise InvestmentError("Invalid monitoring cache revision")

    findings = []
    seen = set()
    for finding in report["findings"]:
        required = ("id", "rule", "rule_version", "subject", "status", "severity",
                    "explanation", "calculation", "evidence", "recommendation")
        if type(finding) is not dict or set(finding) != set(required):
            raise InvestmentError("Invalid monitoring finding")
        for key in ("id", "rule", "subject", "status", "severity",
                    "explanation", "recommendation", "evidence"):
            text(finding[key])
        if (type(finding["rule_version"]) is not int or finding["rule_version"] < 1
                or finding["status"] not in ("review", "unavailable")
                or finding["severity"] != "warning"
                or finding["calculation"] is not None
                and (not isinstance(finding["calculation"], str)
                     or len(finding["calculation"]) > 10000)):
            raise InvestmentError("Invalid monitoring finding")
        if finding["id"] in seen:
            raise InvestmentError("Duplicate monitoring finding ID")
        seen.add(finding["id"])
        evidence_hash = hashlib.sha256(finding["evidence"].encode("utf-8")).hexdigest()
        stored = {key: finding[key] for key in (
            "id", "rule", "rule_version", "subject", "status", "severity",
            "explanation", "calculation", "recommendation")}
        stored["evidence_sha256"] = evidence_hash
        fingerprint = digest(canonical(stored))
        finding_key = digest(canonical({"finding_id": finding["id"],
                                        "fingerprint": fingerprint}))
        findings.append({"finding_id": finding["id"], "finding_key": finding_key,
                         "fingerprint": fingerprint, "payload": stored})
    findings.sort(key=lambda item: item["finding_id"])
    report_hash = digest(canonical({
        "portfolio_id": portfolio_id, "profile_hash": profile_hash,
        "portfolio_revision": report["portfolio_revision"],
        "cache_revision": cache_revision,
        "findings": [{"finding_key": item["finding_key"]} for item in findings],
    }))
    return {
        "generated_at": iso(generated_at),
        "portfolio_revision": report["portfolio_revision"],
        "cache_revision": cache_revision,
        "findings": findings,
        "report_hash": report_hash,
    }


def sync_review_findings(path, report, portfolio_id, profile_hash, now, cache_binding=None):
    """Sync finding metadata and hashes; never persist source evidence or resolve issues."""
    now = explicit_clock(now)
    current = _validated_report(report, portfolio_id, profile_hash, now)
    if (cache_binding is None) != (current["cache_revision"] is None):
        raise InvestmentError("Monitoring report does not match the selected cache binding")
    with _connection(path, write=True) as connection:
        identity = _read_identity(connection)
        _validate_store(connection, identity)
        if (identity["portfolio_id"] != portfolio_id or identity["profile_hash"] != profile_hash
                or identity["cache_binding"] != cache_binding):
            raise InvestmentError("Review store is bound to another portfolio, profile or cache")
        latest_sync = connection.execute(
            "SELECT synced_at,source_portfolio_revision,source_cache_revision "
            "FROM review_syncs ORDER BY revision DESC LIMIT 1").fetchone()
        if latest_sync is not None:
            latest_at = explicit_clock(latest_sync[0])
            if (explicit_clock(current["generated_at"]) < latest_at
                    or current["portfolio_revision"] < latest_sync[1]):
                raise InvestmentError("Stale monitoring report cannot replace a newer review sync")
            if (identity["cache_binding"] is not None
                    and (current["cache_revision"] is None
                         or current["cache_revision"] < latest_sync[2])):
                raise InvestmentError("Monitoring cache evidence regressed; review sync refused")
        if identity["last_sync_hash"] == current["report_hash"]:
            return {"revision": identity["revision"], "created": 0, "inactive": 0,
                    "unchanged": True, "active_cases": _active_count(connection)}
        existing_cases = connection.execute(
            "SELECT case_id,finding_key,generation,finding_id,fingerprint,lifecycle "
            "FROM review_cases").fetchall()
        active_by_key = {row[1]: row for row in existing_cases if row[5] == "active"}
        incoming_ids = {item["finding_id"] for item in current["findings"]}
        active_case_ids = set()
        created = 0
        for item in current["findings"]:
            prior = active_by_key.get(item["finding_key"])
            if prior is not None:
                case_id = prior[0]
                connection.execute(
                    "UPDATE review_cases SET last_seen_at=?,source_portfolio_revision=?,"
                    "source_cache_revision=? WHERE case_id=?",
                    (current["generated_at"], current["portfolio_revision"],
                     current["cache_revision"], case_id))
            else:
                generation = connection.execute(
                    "SELECT COALESCE(MAX(generation),0)+1 FROM review_cases WHERE finding_key=?",
                    (item["finding_key"],)).fetchone()[0]
                case_id = digest(canonical({
                    "portfolio_id": portfolio_id, "profile_hash": profile_hash,
                    "finding_key": item["finding_key"], "generation": generation,
                }))
                connection.execute(
                    "INSERT INTO review_cases VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (case_id, item["finding_key"], generation, item["finding_id"],
                     item["fingerprint"], canonical(item["payload"]), "active",
                     current["generated_at"], current["generated_at"],
                     current["portfolio_revision"], current["cache_revision"]))
                created += 1
            active_case_ids.add(case_id)

        inactive = 0
        for row in existing_cases:
            if row[5] != "active" or row[0] in active_case_ids:
                continue
            lifecycle = "superseded" if row[3] in incoming_ids else "inactive"
            connection.execute("UPDATE review_cases SET lifecycle=? WHERE case_id=?",
                               (lifecycle, row[0]))
            inactive += 1
        if connection.execute("SELECT COUNT(*) FROM review_cases").fetchone()[0] > MAX_CASES:
            raise InvestmentError("Review inbox case limit reached")
        if connection.execute("SELECT COUNT(*) FROM review_syncs").fetchone()[0] >= MAX_SYNCS:
            raise InvestmentError("Review inbox sync limit reached")
        revision = identity["revision"] + 1
        connection.execute("INSERT INTO review_syncs VALUES (?,?,?,?,?,?)",
                           (revision, current["report_hash"], current["generated_at"],
                            current["portfolio_revision"], current["cache_revision"],
                            len(current["findings"])))
        identity["revision"] = revision
        identity["last_sync_hash"] = current["report_hash"]
        connection.execute("UPDATE review_identity SET payload=? WHERE id=1",
                           (canonical(identity),))
        page_count = connection.execute("PRAGMA page_count").fetchone()[0]
        page_size = connection.execute("PRAGMA page_size").fetchone()[0]
        if page_count * page_size > MAX_DATABASE_BYTES:
            raise InvestmentError("Review store size limit reached; sync was rolled back")
        return {"revision": revision, "created": created, "inactive": inactive,
                "unchanged": False, "active_cases": len(current["findings"])}


def _active_count(connection):
    return connection.execute(
        "SELECT COUNT(*) FROM review_cases WHERE lifecycle='active'").fetchone()[0]


def review_inbox(path):
    """Read the explicit review store without creating or changing it."""
    with _connection(path) as connection:
        identity = _read_identity(connection)
        _validate_store(connection, identity)
        cases = connection.execute(
            "SELECT c.case_id,c.finding_id,c.payload,c.lifecycle,c.first_seen_at,c.last_seen_at,"
            "c.source_portfolio_revision,c.source_cache_revision,"
            "a.action,a.action_at,a.note "
            "FROM review_cases c LEFT JOIN review_actions a ON a.revision=("
            "SELECT MAX(x.revision) FROM review_actions x WHERE x.case_id=c.case_id) "
            "ORDER BY CASE c.lifecycle WHEN 'active' THEN 0 WHEN 'superseded' THEN 1 ELSE 2 END,"
            "c.finding_id,c.case_id").fetchall()
        return {
            "portfolio_id": identity["portfolio_id"],
            "profile_hash": identity["profile_hash"],
            "revision": identity["revision"],
            "cases": [{
                "case_id": row[0], "finding_id": row[1], "finding": parse_json(row[2]),
                "lifecycle": row[3], "first_seen_at": row[4], "last_seen_at": row[5],
                "source_portfolio_revision": row[6], "source_cache_revision": row[7],
                "review_status": row[8] or "new", "reviewed_at": row[9], "note": row[10],
            } for row in cases],
            "limitations": [
                "Acknowledged, deferred and reviewed mean workflow status only; none means resolved or trade-approved.",
                "A missing finding is marked inactive, not resolved. Changed evidence creates a new case.",
                "Raw monitoring evidence is not retained; consult the bound local report and source revisions.",
                "The inbox does not update accounting, collect from providers or suppress active findings.",
            ],
        }


def update_review(path, case_id, request_id, expected_revision, action, note, now):
    """Append a revision-bound workflow action for an active finding."""
    text(case_id)
    text(request_id)
    if action not in ("acknowledged", "deferred", "reviewed"):
        raise InvestmentError("Review action must be acknowledged, deferred or reviewed")
    if note is None:
        note = ""
    _validate_note(note)
    now = iso(explicit_clock(now))
    if type(expected_revision) is not int or expected_revision < 0:
        raise InvestmentError("Expected review revision must be a nonnegative integer")
    with _connection(path, write=True) as connection:
        identity = _read_identity(connection)
        _validate_store(connection, identity)
        request = connection.execute(
            "SELECT revision,case_id,action,action_at,note FROM review_actions WHERE request_id=?",
            (request_id,)).fetchone()
        if request is not None:
            if request[1] != case_id or request[2] != action or request[4] != note:
                raise InvestmentError("Conflicting review request ID")
            return {"revision": request[0], "duplicate": True, "action_at": request[3]}
        if identity["revision"] != expected_revision:
            raise InvestmentError("Review inbox changed; refresh before updating")
        case = connection.execute(
            "SELECT lifecycle FROM review_cases WHERE case_id=?", (case_id,)).fetchone()
        if case is None:
            raise InvestmentError("Review case not found")
        if case[0] != "active":
            raise InvestmentError("Only active findings can be acknowledged; no case is resolved")
        if connection.execute("SELECT COUNT(*) FROM review_actions").fetchone()[0] >= MAX_ACTIONS:
            raise InvestmentError("Review inbox action limit reached")
        revision = identity["revision"] + 1
        connection.execute("INSERT INTO review_actions VALUES (?,?,?,?,?,?)",
                           (revision, request_id, case_id, action, now, note))
        identity["revision"] = revision
        connection.execute("UPDATE review_identity SET payload=? WHERE id=1",
                           (canonical(identity),))
        page_count = connection.execute("PRAGMA page_count").fetchone()[0]
        page_size = connection.execute("PRAGMA page_size").fetchone()[0]
        if page_count * page_size > MAX_DATABASE_BYTES:
            raise InvestmentError("Review store size limit reached; update was rolled back")
        return {"revision": revision, "duplicate": False}
