"""User-initiated verified backup downloads and non-destructive restore rehearsal."""

import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import zipfile
import zlib

from investment import InvestmentError
from investment.core import clock
from investment.store import MAX_DATABASE_BYTES


MAX_RECEIPT_BYTES = 64 * 1024
MAX_ARCHIVE_BYTES = MAX_DATABASE_BYTES + MAX_RECEIPT_BYTES + 4096
_RECEIPT_FIELDS = {
    "schema_version", "portfolio_id", "revision", "event_chain_hash",
    "state_hash", "commit_history_hash", "capacity",
}
_CAPACITY_FIELDS = {
    "event_count", "event_limit", "evidence_bytes", "evidence_limit_bytes",
    "copy_size_bytes", "database_limit_bytes",
}


def create_backup_archive(database, now=None):
    """Create a verified SQLite snapshot and its receipt in a temporary archive."""
    from investment.lifecycle import copy_verified

    temporary = tempfile.TemporaryDirectory(prefix="goldscout-backup-")
    directory = Path(temporary.name)
    database_copy = directory / "investment.sqlite3"
    archive_path = directory / "goldscout-verified-backup.zip"
    try:
        receipt = copy_verified(database, database_copy, now)
        receipt_path = directory / "backup-receipt.json"
        receipt_path.write_text(json.dumps(
            receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            encoding="utf-8")
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.write(database_copy, "investment.sqlite3")
            archive.write(receipt_path, "backup-receipt.json")
        return temporary, archive_path, receipt
    except Exception:
        temporary.cleanup()
        raise


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate receipt field")
        result[key] = value
    return result


def _read_receipt(archive, info, database_size):
    if info.file_size > MAX_RECEIPT_BYTES:
        raise InvestmentError("Backup receipt is too large")
    try:
        with archive.open(info) as handle:
            raw = handle.read(MAX_RECEIPT_BYTES + 1)
        if len(raw) > MAX_RECEIPT_BYTES:
            raise InvestmentError("Backup receipt is too large")
        receipt = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs)
    except InvestmentError:
        raise
    except (OSError, UnicodeError, ValueError, RecursionError, EOFError,
            zipfile.BadZipFile, zlib.error, RuntimeError) as exc:
        raise InvestmentError("Backup receipt is invalid") from exc
    if (type(receipt) is not dict or set(receipt) != _RECEIPT_FIELDS
            or type(receipt["schema_version"]) is not int or receipt["schema_version"] != 2
            or type(receipt["revision"]) is not int or receipt["revision"] < 0
            or not isinstance(receipt["portfolio_id"], str) or not receipt["portfolio_id"]
            or any(not isinstance(receipt[key], str)
                   or re.fullmatch(r"[0-9a-f]{64}", receipt[key]) is None
                   for key in ("event_chain_hash", "state_hash", "commit_history_hash"))
            or type(receipt["capacity"]) is not dict
            or set(receipt["capacity"]) != _CAPACITY_FIELDS
            or any(type(value) is not int or value < 0
                   for value in receipt["capacity"].values())
            or receipt["capacity"]["copy_size_bytes"] != database_size):
        raise InvestmentError("Backup receipt does not match the archive")
    return receipt


def rehearse_backup_archive(archive_path, now=None):
    """Verify a backup by copying it to a new temporary destination; never restores in place."""
    from investment.lifecycle import copy_verified

    now = clock(now) if now is not None else None
    with tempfile.TemporaryDirectory(prefix="goldscout-restore-rehearsal-") as raw_directory:
        directory = Path(raw_directory)
        database_path = directory / "selected-backup.sqlite3"
        rehearsal_path = directory / "rehearsal.sqlite3"
        try:
            with zipfile.ZipFile(archive_path, "r") as archive:
                entries = archive.infolist()
                if (len(entries) != 2 or
                        {item.filename for item in entries} !=
                        {"investment.sqlite3", "backup-receipt.json"}):
                    raise InvestmentError("Backup must contain only the journal and its receipt")
                by_name = {item.filename: item for item in entries}
                database_info = by_name["investment.sqlite3"]
                receipt_info = by_name["backup-receipt.json"]
                if (database_info.is_dir() or receipt_info.is_dir()
                        or database_info.file_size > MAX_DATABASE_BYTES
                        or database_info.flag_bits & 0x1
                        or receipt_info.flag_bits & 0x1):
                    raise InvestmentError("Backup archive contains an unsupported entry")
                receipt = _read_receipt(archive, receipt_info, database_info.file_size)
                written = 0
                with archive.open(database_info) as source:
                    descriptor = os.open(database_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                    with os.fdopen(descriptor, "wb") as target:
                        while True:
                            chunk = source.read(1024 * 1024)
                            if not chunk:
                                break
                            written += len(chunk)
                            if written > MAX_DATABASE_BYTES:
                                raise InvestmentError("Backup journal exceeds its supported size")
                            target.write(chunk)
                if written != database_info.file_size:
                    raise InvestmentError("Backup journal size does not match the archive directory")
        except InvestmentError:
            raise
        except (OSError, ValueError, EOFError, zipfile.BadZipFile, RuntimeError, zlib.error) as exc:
            raise InvestmentError("Backup archive could not be read") from exc

        verified = copy_verified(database_path, rehearsal_path, now)
        capacity = receipt["capacity"]
        verified_capacity = verified["capacity"]
        comparable = (
            "schema_version", "portfolio_id", "revision", "event_chain_hash",
            "state_hash", "commit_history_hash",
        )
        if (any(receipt[key] != verified[key] for key in comparable)
                or any(capacity[key] != verified_capacity[key] for key in (
                    "event_count", "event_limit", "evidence_bytes",
                    "evidence_limit_bytes", "database_limit_bytes"))
                or capacity["copy_size_bytes"] != database_path.stat().st_size):
            raise InvestmentError("Backup receipt does not match the verified journal")
        return {
            "status": "verified",
            "schema_version": verified["schema_version"],
            "portfolio_id": verified["portfolio_id"],
            "revision": verified["revision"],
            "copy_size_bytes": database_path.stat().st_size,
            "rehearsal_size_bytes": rehearsal_path.stat().st_size,
            "event_count": verified_capacity["event_count"],
            "event_limit": verified_capacity["event_limit"],
            "evidence_bytes": verified_capacity["evidence_bytes"],
            "evidence_limit_bytes": verified_capacity["evidence_limit_bytes"],
            "database_limit_bytes": verified_capacity["database_limit_bytes"],
        }
