"""Confirmed first-run creation; the existing journal remains accounting authority.

These helpers grant no authentication. The server must retain previews in memory
and bind them to an authenticated, explicit confirmation. Hashes are consistency
checks, not protection against an actor with local filesystem write access.
"""

from contextlib import contextmanager, closing
from datetime import timedelta
import os
from pathlib import Path
import sqlite3
import uuid

from . import InvestmentError, store
from .core import clock, iso, profile as validate_profile, replay
from .service import opening_report


MAX_SETUP_BYTES = 128 * 1024
PREVIEW_SECONDS = 600
CLAIM = "claim.json"
STAGE = "journal.sqlite3"
RECEIPT = "receipt.json"
LOCK = "operation.lock"


def workspace_status(path, now=None):
    """Public convenience export; workspace classification is dashboard-owned."""
    from dashboard.workspace import workspace_status as classify
    return classify(path, now)


def _paths(path):
    from dashboard.workspace import DATABASE, PROFILE, SETUP, resolve_workspace
    path = resolve_workspace(path)
    return path, path / DATABASE, path / PROFILE, path / SETUP


def _json(path):
    from dashboard.workspace import path_identity
    path_identity(path)
    if not path.is_file() or path.stat().st_size > MAX_SETUP_BYTES:
        raise InvestmentError("Invalid bounded setup metadata")
    with path.open("rb") as handle:
        raw = handle.read(MAX_SETUP_BYTES + 1)
    if len(raw) > MAX_SETUP_BYTES:
        raise InvestmentError("Setup metadata exceeds size bound")
    return store.parse_json(raw.decode("utf-8"))


def _read(path, now):
    if not path.is_file() or path.stat().st_size > store.MAX_DATABASE_BYTES:
        raise InvestmentError("Missing or oversized investment store")
    try:
        # Own closure even when a corrupt header fails during connection setup.
        with closing(sqlite3.connect(path.absolute().as_uri() + "?mode=ro",
                                     uri=True, timeout=1)) as connection:
            connection.execute("PRAGMA query_only=ON")
            if connection.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
                raise InvestmentError("Unsupported store journal mode")
            connection.execute("BEGIN")
            return store._read(connection, now)
    except sqlite3.Error as exc:
        raise InvestmentError("Invalid or unreadable investment store") from exc


def _claim(path):
    claim = _json(path)
    fields = {"version", "operation_id", "profile_hash", "opening_hash", "opening_id", "profile"}
    if (not isinstance(claim, dict) or set(claim) != fields
            or type(claim["version"]) is not int or claim["version"] != 1
            or not isinstance(claim["operation_id"], str)
            or len(claim["operation_id"]) != 32
            or any(c not in "0123456789abcdef" for c in claim["operation_id"])
            or any(not isinstance(claim[k], str) or len(claim[k]) != 64
                   or any(c not in "0123456789abcdef" for c in claim[k])
                   for k in ("profile_hash", "opening_hash"))
            or not isinstance(claim["opening_id"], str)
            or not 1 <= len(claim["opening_id"]) <= 200):
        raise InvestmentError("Invalid setup provenance")
    spec = validate_profile(claim["profile"])
    if (spec["schema_version"] != 2
            or store.digest(store.canonical(spec)) != claim["profile_hash"]):
        raise InvestmentError("Invalid setup provenance profile")
    return claim


def _matches(spec, events, claim, allow_empty=False):
    if store.digest(store.canonical(spec)) != claim["profile_hash"]:
        raise InvestmentError("Setup profile binding mismatch")
    if allow_empty and not events:
        return
    if (not events or events[0].get("type") != "opening"
            or events[0].get("id") != claim["opening_id"]
            or store.digest(store.canonical(events[0])) != claim["opening_hash"]):
        raise InvestmentError("Setup opening binding mismatch")


def inspect_workspace(path, now=None):
    """Internal read-only inspection; called by dashboard.workspace."""
    from dashboard.workspace import ETORO, directory_entries, path_identity
    path, database, exported, operation = _paths(path)
    now = clock(now)
    path_identity(path)
    if not path.exists():
        return {"state": "empty", "reason": "Workspace does not exist"}
    names = directory_entries(path)
    if not names:
        return {"state": "empty", "reason": "Workspace is empty"}
    if ETORO in names:
        etoro_directory = path / ETORO
        if not etoro_directory.is_dir():
            raise InvestmentError("The recognized eToro state location is not a directory")
        if directory_entries(etoro_directory) - {"connection.json", "startup.lock", "connection.lock"}:
            raise InvestmentError("Unexpected eToro state entries; preserve and inspect them")
    if names - {database.name, exported.name, operation.name, ETORO}:
        raise InvestmentError("Unexpected workspace entries; preserve and inspect them")
    if names <= {ETORO}:
        return {"state": "empty", "reason": "Workspace has no journal; eToro connection is separate"}
    claim = None
    if operation.name in names:
        entries = directory_entries(operation)
        if CLAIM not in entries or entries - {CLAIM, STAGE, RECEIPT, LOCK}:
            raise InvestmentError("Unexpected or incomplete setup artifacts")
        claim = _claim(operation / CLAIM)
        if LOCK in entries and (not (operation / LOCK).is_file()
                                or (operation / LOCK).stat().st_size != 1):
            raise InvestmentError("Invalid setup lock artifact")
    if database.name in names:
        spec, events, ledger, _ = _read(database, now)
        if spec["schema_version"] != 2:
            raise InvestmentError("Setup requires a version 2 journal")
        if not ledger.opened:
            if claim is not None:
                raise InvestmentError("Published unopened journal cannot be adopted")
            return {"state": "manual_unopened", "revision": ledger.revision,
                    "reason": "Use the existing authenticated opening workflow"}
        issues = []
        if not exported.exists():
            issues.append("Profile export is missing; embedded journal profile is authoritative")
        else:
            try:
                if store.canonical(_json(exported)) != store.canonical(spec):
                    issues.append("Profile export conflicts; embedded journal profile is authoritative")
            except (InvestmentError, OSError, UnicodeError):
                issues.append("Profile export is unreadable; embedded journal profile is authoritative")
        if claim is not None:
            _matches(spec, events, claim)
            if (operation / STAGE).exists():
                staged_spec, staged_events, _, _ = _read(operation / STAGE, now)
                _matches(staged_spec, staged_events, claim)
                if len(staged_events) != 1:
                    raise InvestmentError("Unexpected staged revision")
            if (operation / RECEIPT).exists():
                _verify_receipt(_json(operation / RECEIPT), claim)
        result = {"state": "ready", "revision": ledger.revision, "issues": issues,
                  "reason": "Opened journal is authoritative"}
        if claim is not None:
            if (operation / STAGE).exists():
                issues.append("Published setup staging link requires confirmed cleanup")
            if not (operation / RECEIPT).exists():
                issues.append("Setup receipt is missing; explicit completion is required")
            if issues:
                result.update(state="recoverable", operation_id=claim["operation_id"],
                              reason="Journal is committed; confirmed setup completion is required")
            result["recovery"] = {"can_preview": True, "requires_opening": False,
                                  "profile": claim["profile"],
                                  "opening_id": claim["opening_id"]}
        return result
    if claim is None:
        raise InvestmentError("Occupied workspace without setup provenance")
    if exported.exists():
        raise InvestmentError("Profile export without published journal; inspect evidence")
    if (operation / RECEIPT).exists():
        raise InvestmentError("Receipt without published journal")
    has_opening = False
    if (operation / STAGE).exists():
        spec, events, ledger, _ = _read(operation / STAGE, now)
        _matches(spec, events, claim, allow_empty=True)
        if ledger.revision not in (0, 1):
            raise InvestmentError("Unexpected staged revision")
        has_opening = ledger.opened
    return {"state": "recoverable", "reason": "Matching setup provenance requires confirmed recovery",
            "operation_id": claim["operation_id"],
            "recovery": {"can_preview": has_opening,
                         "requires_opening": not has_opening,
                         "profile": claim["profile"], "opening_id": claim["opening_id"]}}


def _binding(path):
    from dashboard.workspace import directory_entries, path_identity
    result = {"ancestors": path_identity(path), "entries": []}
    if path.exists():
        for name in sorted(directory_entries(path)):
            item = path / name
            info = item.lstat()
            result["entries"].append([name, info.st_dev, info.st_ino,
                                      info.st_size, info.st_mtime_ns])
            if name in (".investment-setup", ".etoro"):
                for child in sorted(directory_entries(item)):
                    info = (item / child).lstat()
                    result["entries"].append([name + "/" + child, info.st_dev,
                                              info.st_ino, info.st_size, info.st_mtime_ns])
    return result


def preview_setup(path, profile, opening, now=None):
    """Validate exactly one opening in memory; no filesystem writes."""
    from dashboard.workspace import workspace_status
    now = clock(now)
    path, _, _, operation = _paths(path)
    spec = store.parse_json(store.canonical(profile))
    validate_profile(spec)
    value = store.parse_json(store.canonical(opening))
    if spec["schema_version"] != 2 or not isinstance(value, dict) or value.get("type") != "opening":
        raise InvestmentError("Setup requires a version 2 profile and exactly one opening")
    ledger = replay(spec, [value], now)
    status = workspace_status(path, now)
    if status["state"] not in ("empty", "recoverable", "ready"):
        raise InvestmentError(status["reason"])
    claim = {"version": 1, "operation_id": uuid.uuid4().hex,
             "profile_hash": store.digest(store.canonical(spec)),
             "opening_hash": store.digest(store.canonical(value)), "opening_id": value["id"],
             "profile": spec}
    if status["state"] != "empty":
        if not (operation / CLAIM).exists():
            raise InvestmentError("Existing journal is not a wizard operation")
        existing = _claim(operation / CLAIM)
        claim["operation_id"] = existing["operation_id"]
        if existing != claim:
            raise InvestmentError("Re-entered evidence does not match setup provenance")
    result = {"version": 1, "workspace": str(path), "profile": spec, "opening": value,
              "claim": claim, "operation_id": claim["operation_id"],
              "expires_at": iso(now + timedelta(seconds=PREVIEW_SECONDS)),
              "binding": _binding(path), "report": opening_report(ledger, now)}
    if len(store.canonical(result).encode("utf-8")) > MAX_SETUP_BYTES:
        raise InvestmentError("Setup preview exceeds size bound")
    result["preview_hash"] = store.digest(store.canonical(result))
    return result


def recovery_preview(path, now=None):
    """Re-preview persisted opening evidence; revision zero needs re-entered evidence."""
    from dashboard.workspace import workspace_status
    path, database, _, operation = _paths(path)
    status = workspace_status(path, now)
    if status["state"] not in ("ready", "recoverable") or not (operation / CLAIM).exists():
        raise InvestmentError("No verifiable wizard operation to recover")
    source = database if database.exists() else operation / STAGE
    if not source.exists():
        raise InvestmentError("Opening evidence must be re-entered for recovery")
    spec, events, _, _ = _read(source, clock(now))
    if not events:
        raise InvestmentError("Opening evidence must be re-entered for recovery")
    _matches(spec, events, _claim(operation / CLAIM))
    return preview_setup(path, spec, events[0], now)


def _write_new(path, value):
    """Exclusive bounded metadata creation; an interrupted write is never adopted."""
    encoded = store.canonical(value).encode("utf-8")
    if len(encoded) > MAX_SETUP_BYTES:
        raise InvestmentError("Setup metadata exceeds size bound")
    with path.open("xb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


@contextmanager
def _locked_claim(path):
    """OS-released cross-process lock; no stale lock takeover on restart."""
    from dashboard.workspace import path_identity
    path = path.parent / LOCK
    try:
        with path.open("xb") as lockfile:
            lockfile.write(b"0")
    except FileExistsError:
        pass
    before = path_identity(path)
    with path.open("r+b") as handle:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            if before != path_identity(path) or (
                    os.fstat(handle.fileno()).st_ino != path.stat().st_ino):
                raise InvestmentError("Setup claim path changed")
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _receipt(claim):
    return {"version": 1, "operation_id": claim["operation_id"],
            "profile_hash": claim["profile_hash"], "opening_hash": claim["opening_hash"],
            "opening_id": claim["opening_id"], "revision": 1}


def _verify_receipt(receipt, claim):
    if store.canonical(receipt) != store.canonical(_receipt(claim)):
        raise InvestmentError("Setup receipt does not match provenance")


def _cleanup_stage(stage, database):
    """Remove only the operation's own published hard link, never accounting."""
    if stage.exists():
        if not os.path.samefile(stage, database):
            raise InvestmentError("Staging file is not the published journal")
        stage.unlink()


def confirm_setup(path, preview, now=None):
    """Explicitly create/recover the trusted preview, with truthful partial results.

    Complete and partial results both revoke ordinary creation authority at the
    API layer. A partial result needs fresh authentication/preview for recovery.
    """
    from dashboard.workspace import path_identity, workspace_status
    now = clock(now)
    path, database, exported, operation = _paths(path)
    preview = store.parse_json(store.canonical(preview))
    if len(store.canonical(preview).encode("utf-8")) > MAX_SETUP_BYTES:
        raise InvestmentError("Setup preview exceeds size bound")
    encoded = dict(preview)
    expected_hash = encoded.pop("preview_hash", None)
    if (store.digest(store.canonical(encoded)) != expected_hash
            or preview.get("workspace") != str(path)):
        raise InvestmentError("Setup preview binding mismatch")
    claim = preview["claim"]
    # Validate content again before any write, even for an authenticated caller.
    ledger = replay(preview["profile"], [preview["opening"]], now)
    _matches(ledger.spec, [preview["opening"]], claim)
    if preview["operation_id"] != claim["operation_id"]:
        raise InvestmentError("Setup operation binding mismatch")
    # Existing ancestor identities may never change, even on an exact retry.
    identities = path_identity(path)
    if any(item not in identities for item in preview["binding"]["ancestors"]):
        raise InvestmentError("Workspace path identity changed")
    status = workspace_status(path, now)
    if status["state"] not in ("empty", "recoverable", "ready"):
        raise InvestmentError(status["reason"])
    existing = (operation / CLAIM).exists()
    if existing:
        if _claim(operation / CLAIM) != claim:
            raise InvestmentError("Conflicting setup operation")
        for name, device, inode, _, _ in preview["binding"]["entries"]:
            artifact = path / name
            if not artifact.exists():
                # The staged hard link is removed after successful publication.
                if name == operation.name + "/" + STAGE and database.exists():
                    continue
                raise InvestmentError("Bound workspace artifact disappeared")
            info = artifact.lstat()
            if [info.st_dev, info.st_ino] != [device, inode]:
                raise InvestmentError("Bound workspace artifact was replaced")
    elif status["state"] != "empty":
        raise InvestmentError("Existing workspace cannot be adopted")
    if clock(preview["expires_at"]) < now:
        # A completed identical retry is receipt-only, never an expired write.
        if (existing and status["state"] == "ready" and database.exists()
                and (operation / RECEIPT).exists()):
            _verify_receipt(_json(operation / RECEIPT), claim)
            return {"status": "completed", "committed": True, "receipt": _receipt(claim),
                    "revision": 1, "message": "Previously completed operation"}
        raise InvestmentError("Setup preview expired")
    if not existing and _binding(path) != preview["binding"]:
        raise InvestmentError("Workspace changed since preview")
    committed = False
    try:
        if not existing:
            if not path.exists():
                path.mkdir()  # No implicit creation of missing parent directories.
            path_identity(path)
            operation.mkdir()
            _write_new(operation / CLAIM, claim)
        operation_identity = path_identity(operation / CLAIM)
        with _locked_claim(operation / CLAIM):
            if operation_identity != path_identity(operation / CLAIM):
                raise InvestmentError("Setup operation path identity changed")
            if _claim(operation / CLAIM) != claim:
                raise InvestmentError("Setup provenance changed")
            status = workspace_status(path, now)
            if status["state"] not in ("recoverable", "ready"):
                raise InvestmentError(status["reason"])
            stage = operation / STAGE
            if not database.exists():
                if not stage.exists():
                    store.initialize(stage, preview["profile"], now)
                stage_identity = path_identity(stage)
                spec, events, staged, _ = _read(stage, now)
                _matches(spec, events, claim, allow_empty=True)
                if staged.revision == 0:
                    store.import_events(stage, [preview["opening"]], now)
                spec, events, staged, _ = _read(stage, now)
                _matches(spec, events, claim)
                if staged.revision != 1:
                    raise InvestmentError("Unexpected staged revision")
                # All handles closed before a same-filesystem no-replace publish.
                if any(item not in path_identity(path)
                       for item in preview["binding"]["ancestors"]):
                    raise InvestmentError("Workspace path identity changed")
                if workspace_status(path, now)["state"] != "recoverable":
                    raise InvestmentError("Workspace changed before publication")
                if (operation_identity != path_identity(operation / CLAIM)
                        or stage_identity != path_identity(stage)):
                    raise InvestmentError("Setup operation path identity changed")
                os.link(stage, database)
                # The closed stage was already validated. Publication is a fact
                # even if every subsequent read is temporarily unavailable.
                committed = True
            spec, events, _, _ = _read(database, now)
            _matches(spec, events, claim)
            committed = True
            if workspace_status(path, now)["state"] not in ("ready", "recoverable"):
                raise InvestmentError("Journal published but workspace validation failed")
            _cleanup_stage(stage, database)
            if not exported.exists():
                _write_new(exported, spec)
            elif store.canonical(_json(exported)) != store.canonical(spec):
                raise InvestmentError("Journal committed; conflicting profile export preserved")
            receipt = _receipt(claim)
            if not (operation / RECEIPT).exists():
                _write_new(operation / RECEIPT, receipt)
            _verify_receipt(_json(operation / RECEIPT), claim)
            if workspace_status(path, now)["state"] != "ready":
                raise InvestmentError("Journal committed; setup completion validation failed")
            return {"status": "completed", "committed": True, "receipt": receipt,
                    "revision": 1,
                    "message": "Opened journal published; recording remains disabled"}
    except (OSError, InvestmentError, ValueError, TypeError) as exc:
        # Publication may have succeeded before validation/export/receipt failed.
        try:
            spec, events, _, _ = _read(database, now)
            _matches(spec, events, claim)
            committed = True
        except (OSError, InvestmentError, ValueError, TypeError):
            pass
        return {"status": "partial", "committed": committed, "receipt": None,
                "revision": 1 if committed else None,
                "message": f"Setup incomplete; preserve artifacts and explicitly recover: {exc}"}
