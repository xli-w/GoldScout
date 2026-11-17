"""Explicit verified copies of the separate investment journal."""

from contextlib import closing
import os
from pathlib import Path
import sqlite3
import tempfile
import time

from . import InvestmentError
from .core import MAX_EVENTS, clock
from .store import MAX_DATABASE_BYTES, MAX_JSON_BYTES, _connect, _read, canonical, digest, state_digest


def _copy_database(source, target):
    deadline = time.monotonic() + 5

    def progress(status, remaining, total):
        if time.monotonic() > deadline:
            raise InvestmentError("Investment copy exceeded its time budget")

    source.backup(target, pages=64, progress=progress, sleep=0.05)


def _has_sidecar(path):
    return any(os.path.lexists(str(path) + suffix)
               for suffix in ("-journal", "-wal", "-shm"))


def copy_verified(database, destination, now=None):
    """Copy one validated snapshot to a new path; never replace a store."""
    destination = Path(destination).absolute()
    if destination.name.lower().endswith(("-journal", "-wal", "-shm")):
        raise InvestmentError("Copy destination cannot be a SQLite sidecar name")
    now = clock(now) if now is not None else None
    directory = None
    temporary = None
    published = False
    sidecar_conflict = False
    try:
        with closing(_connect(database, True)) as source:
            source.execute("BEGIN")
            source.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
            now = clock() if now is None else now
            spec, events, ledger, head = _read(source, now)
            commits = source.execute(
                "SELECT revision,state_hash FROM commits ORDER BY revision").fetchall()
            source_digest = state_digest(ledger)
            directory = Path(tempfile.mkdtemp(prefix=".investment-copy-", dir=destination.parent))
            temporary = directory / "journal.sqlite3"
            fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
            os.close(fd)
            with closing(sqlite3.connect(temporary, timeout=1)) as target:
                _copy_database(source, target)
            with closing(_connect(temporary, True)) as target:
                target.execute("BEGIN")
                copied_spec, copied_events, copied_ledger, copied_head = _read(target, now)
                copied_commits = target.execute(
                    "SELECT revision,state_hash FROM commits ORDER BY revision").fetchall()
            if (canonical(spec) != canonical(copied_spec)
                    or canonical(events) != canonical(copied_events)
                    or commits != copied_commits or head != copied_head
                    or source_digest != state_digest(copied_ledger)):
                raise InvestmentError("Investment copy does not match the pinned source")
            copy_size = temporary.stat().st_size
            if _has_sidecar(destination):
                sidecar_conflict = True
                raise InvestmentError("Copy destination has an existing SQLite sidecar")
            os.link(temporary, destination)
            published = True
            if _has_sidecar(destination):
                sidecar_conflict = True
                raise InvestmentError(
                    "Published copy has an unexpected SQLite sidecar; inspect the destination")
            return {"schema_version": spec["schema_version"],
                    "portfolio_id": spec["portfolio_id"], "revision": ledger.revision,
                    "event_chain_hash": head, "state_hash": source_digest,
                    "commit_history_hash": digest(canonical(commits)),
                    "capacity": {
                        "event_count": len(events), "event_limit": MAX_EVENTS,
                        "evidence_bytes": (len(canonical(spec).encode("utf-8"))
                                           + len(canonical(events).encode("utf-8"))),
                        "evidence_limit_bytes": MAX_JSON_BYTES,
                        "copy_size_bytes": copy_size,
                        "database_limit_bytes": MAX_DATABASE_BYTES,
                    }}
    except InvestmentError:
        raise
    except KeyboardInterrupt as exc:
        raise InvestmentError("Investment copy interrupted; inspect the destination") from exc
    except (sqlite3.Error, OSError, TypeError, AttributeError, ValueError) as exc:
        raise InvestmentError("Investment copy failed; inspect the destination") from exc
    finally:
        if directory is not None:
            try:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
                directory.rmdir()
            except OSError as exc:
                if published:
                    status = "destination was published and requires inspection"
                else:
                    status = "destination may require inspection"
                if sidecar_conflict or _has_sidecar(destination):
                    status += "; SQLite sidecar detected"
                raise InvestmentError(
                    f"Investment copy cleanup failed; {status}; inspect temporary directory {directory}"
                ) from exc
