"""Separate local planning goals; never part of the investment journal."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import threading

from dashboard.monitoring_workflow import MonitoringWorkflowSessions
from investment import InvestmentError


MAX_PROFILE_BYTES = 64 * 1024
MAX_GOALS = 20
MAX_GOAL_ACCOUNTS = 50
PREVIEW_TTL = timedelta(minutes=5)
AMOUNT_PATTERN = re.compile(r"^(?:0|[1-9][0-9]{0,14})(?:\.[0-9]{1,2})?$")
DATE_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
LEGACY_GOAL_FIELDS = {"name", "target_amount_gbp", "target_date", "notes"}
GOAL_V2_FIELDS = LEGACY_GOAL_FIELDS | {
    "purpose", "access_date", "liquidity", "loss_tolerance",
    "current_amount_gbp", "monthly_contribution_gbp",
}
GOAL_FIELDS = GOAL_V2_FIELDS | {"linked_portfolio_id", "linked_account_ids"}
PURPOSES = {"not_set", "first_home", "retirement", "education",
            "major_purchase", "financial_resilience", "other"}
LIQUIDITY = {"unknown", "immediate", "within_1_year", "within_5_years", "flexible"}
LOSS_TOLERANCE = {"not_set", "avoid_loss", "small_fluctuations",
                  "substantial_fluctuations", "not_sure"}


class PlanningError(ValueError):
    def __init__(self, code, status=409):
        super().__init__(code)
        self.code = code
        self.status = status


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


def _goals(value, allow_legacy=False):
    if type(value) is not list or len(value) > MAX_GOALS:
        raise PlanningError("invalid_goals", 400)
    normalized = []
    accepted_fields = [GOAL_FIELDS]
    if allow_legacy:
        accepted_fields.extend((LEGACY_GOAL_FIELDS, GOAL_V2_FIELDS))
    for item in value:
        if type(item) is not dict or set(item) not in accepted_fields:
            raise PlanningError("invalid_goal", 400)
        goal = {
            "purpose": "not_set", "access_date": None, "liquidity": "unknown",
            "loss_tolerance": "not_set", "current_amount_gbp": None,
            "monthly_contribution_gbp": None, "linked_portfolio_id": None,
            "linked_account_ids": [], **item,
        }
        name, amount, target_date, notes = (
            goal["name"], goal["target_amount_gbp"], goal["target_date"], goal["notes"])
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
            raise PlanningError("invalid_goal_name", 400)
        for key in ("target_amount_gbp", "current_amount_gbp", "monthly_contribution_gbp"):
            amount_value = goal[key]
            if amount_value is not None and (
                    not isinstance(amount_value, str)
                    or not AMOUNT_PATTERN.fullmatch(amount_value)
                    or key == "target_amount_gbp"
                    and not any(character in "123456789" for character in amount_value)):
                raise PlanningError("invalid_goal_amount", 400)
        for key in ("target_date", "access_date"):
            date_value = goal[key]
            if date_value is None:
                continue
            if not isinstance(date_value, str) or not DATE_PATTERN.fullmatch(date_value):
                raise PlanningError("invalid_goal_date", 400)
            try:
                datetime.strptime(date_value, "%Y-%m-%d")
            except ValueError as exc:
                raise PlanningError("invalid_goal_date", 400) from exc
        if not isinstance(notes, str) or len(notes) > 500:
            raise PlanningError("invalid_goal_notes", 400)
        if (not isinstance(goal["purpose"], str) or goal["purpose"] not in PURPOSES
                or not isinstance(goal["liquidity"], str) or goal["liquidity"] not in LIQUIDITY
                or not isinstance(goal["loss_tolerance"], str)
                or goal["loss_tolerance"] not in LOSS_TOLERANCE):
            raise PlanningError("invalid_goal_constraints", 400)
        portfolio_id, account_ids = (
            goal["linked_portfolio_id"], goal["linked_account_ids"])
        if (portfolio_id is not None and
                (not isinstance(portfolio_id, str) or not portfolio_id.strip()
                 or len(portfolio_id) > 200)):
            raise PlanningError("invalid_goal_links", 400)
        if (type(account_ids) is not list or len(account_ids) > MAX_GOAL_ACCOUNTS
                or any(not isinstance(account_id, str) or not account_id.strip()
                       or len(account_id) > 200 for account_id in account_ids)
                or len(set(account_ids)) != len(account_ids)
                or bool(account_ids) != (portfolio_id is not None)
                or account_ids and goal["current_amount_gbp"] is not None):
            raise PlanningError("invalid_goal_links", 400)
        normalized.append({
            "id": secrets.token_hex(16),
            "name": name.strip(),
            "target_amount_gbp": goal["target_amount_gbp"],
            "target_date": goal["target_date"],
            "notes": notes.strip(),
            "purpose": goal["purpose"],
            "access_date": goal["access_date"],
            "liquidity": goal["liquidity"],
            "loss_tolerance": goal["loss_tolerance"],
            "current_amount_gbp": goal["current_amount_gbp"],
            "monthly_contribution_gbp": goal["monthly_contribution_gbp"],
            "linked_portfolio_id": portfolio_id,
            "linked_account_ids": account_ids.copy(),
        })
    assigned = set()
    linked_portfolios = set()
    for goal in normalized:
        portfolio_id = goal["linked_portfolio_id"]
        if portfolio_id is None:
            continue
        linked_portfolios.add(portfolio_id)
        for account_id in goal["linked_account_ids"]:
            key = (portfolio_id, account_id)
            if key in assigned:
                raise PlanningError("goal_account_already_assigned", 400)
            assigned.add(key)
    if len(linked_portfolios) > 1:
        raise PlanningError("goal_portfolios_must_match", 400)
    return normalized


class PlanningProfile:
    def __init__(self, path, investment_database=None):
        self.path = Path(path).absolute()
        self.investment_database = (
            Path(investment_database).absolute()
            if investment_database is not None else None)
        self.sessions = MonitoringWorkflowSessions()
        self.lock = threading.RLock()

    def _linked_journal_snapshot(self, goals, now):
        linked = [goal for goal in goals if goal["linked_account_ids"]]
        if not linked:
            return None
        if self.investment_database is None:
            raise PlanningError("planning_portfolio_unavailable", 503)
        from investment.store import read

        try:
            ledger = read(self.investment_database, now)
        except (InvestmentError, OSError) as exc:
            raise PlanningError("planning_portfolio_unavailable", 503) from exc
        if not ledger.opened:
            raise PlanningError("planning_portfolio_unavailable", 409)
        portfolio_id = linked[0]["linked_portfolio_id"]
        if portfolio_id != ledger.spec["portfolio_id"]:
            raise PlanningError("planning_portfolio_changed", 409)
        active_accounts = ledger.active_accounts
        if any(account_id not in active_accounts
               for goal in linked for account_id in goal["linked_account_ids"]):
            raise PlanningError("planning_account_unavailable", 409)
        return portfolio_id, ledger.revision

    def session_payload(self, token, now):
        result = self.sessions.session_payload(token, now)
        token = result.pop("_token")
        profile = self._read()
        result.update({
            "revision": profile["revision"],
            "profile_hash": profile["profile_hash"],
            "goals": profile["goals"],
        })
        return result, token

    def authorize(self, token, csrf, now):
        return self.sessions.authorize(token, csrf, now)

    def _read(self):
        from dashboard.workspace import path_identity

        try:
            path_identity(self.path)
            info = self.path.stat()
        except FileNotFoundError:
            empty = {"schema_version": 2, "revision": 0, "saved_at": None, "goals": []}
            return {**empty, "profile_hash": hashlib.sha256(_canonical(empty)).hexdigest()}
        except InvestmentError as exc:
            raise PlanningError("planning_profile_unavailable", 503) from exc
        if not self.path.is_file() or info.st_size > MAX_PROFILE_BYTES:
            raise PlanningError("planning_profile_unavailable", 503)
        try:
            with self.path.open("rb") as handle:
                raw = handle.read(MAX_PROFILE_BYTES + 1)
            if len(raw) > MAX_PROFILE_BYTES:
                raise PlanningError("planning_profile_unavailable", 503)
            value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs)
        except (OSError, UnicodeError, ValueError, RecursionError) as exc:
            raise PlanningError("planning_profile_unavailable", 503) from exc
        if (type(value) is not dict
                or set(value) != {"schema_version", "revision", "saved_at", "goals"}
                or type(value["schema_version"]) is not int
                or value["schema_version"] not in (1, 2, 3)
                or type(value["revision"]) is not int or value["revision"] < 1
                or not isinstance(value["saved_at"], str)
                or not isinstance(value["goals"], list)):
            raise PlanningError("planning_profile_unavailable", 503)
        saved_at = value["saved_at"]
        try:
            parsed = datetime.fromisoformat(saved_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise PlanningError("planning_profile_unavailable", 503) from exc
        if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
            raise PlanningError("planning_profile_unavailable", 503)
        if len(value["goals"]) > MAX_GOALS:
            raise PlanningError("planning_profile_unavailable", 503)
        validated = []
        for goal in value["goals"]:
            if (type(goal) is not dict
                    or not isinstance(goal.get("id"), str)
                    or not re.fullmatch(r"[0-9a-f]{32}", goal["id"])):
                raise PlanningError("planning_profile_unavailable", 503)
            fields = (
                LEGACY_GOAL_FIELDS if value["schema_version"] == 1 else
                GOAL_V2_FIELDS if value["schema_version"] == 2 else GOAL_FIELDS)
            if set(goal) != {"id"} | fields:
                raise PlanningError("planning_profile_unavailable", 503)
            try:
                normalized = _goals(
                    [{key: goal[key] for key in fields}], allow_legacy=True)[0]
            except PlanningError as exc:
                raise PlanningError("planning_profile_unavailable", 503) from exc
            normalized["id"] = goal["id"]
            validated.append(normalized)
        if len(validated) != len(value["goals"]):
            raise PlanningError("planning_profile_unavailable", 503)
        checked = _goals([{
            key: goal[key] for key in GOAL_FIELDS
        } for goal in validated])
        for normalized, original in zip(checked, validated):
            normalized["id"] = original["id"]
        validated = checked
        return {
            "schema_version": 3, "revision": value["revision"],
            "saved_at": saved_at, "goals": validated,
            "profile_hash": hashlib.sha256(raw).hexdigest(),
        }

    def preview(self, token, request_id, expected_revision, expected_hash, goals,
                expected_portfolio_id, expected_journal_revision, now):
        if (not isinstance(request_id, str) or not 1 <= len(request_id) <= 128
                or type(expected_revision) is not int
                or not isinstance(expected_hash, str)
                or not re.fullmatch(r"[0-9a-f]{64}", expected_hash)):
            raise PlanningError("invalid_request", 400)
        normalized = _goals(goals)
        has_links = any(goal["linked_account_ids"] for goal in normalized)
        if (has_links and (
                not isinstance(expected_portfolio_id, str)
                or not expected_portfolio_id.strip() or len(expected_portfolio_id) > 200
                or type(expected_journal_revision) is not int
                or expected_journal_revision < 0)
                or not has_links and
                (expected_portfolio_id is not None or expected_journal_revision is not None)):
            raise PlanningError("invalid_request", 400)
        profile = self._read()
        if (expected_revision != profile["revision"]
                or not secrets.compare_digest(expected_hash, profile["profile_hash"])):
            raise PlanningError("planning_profile_changed")
        now = now.astimezone(timezone.utc)
        linked_journal_snapshot = self._linked_journal_snapshot(normalized, now)
        if has_links and linked_journal_snapshot != (
                expected_portfolio_id, expected_journal_revision):
            raise PlanningError("planning_portfolio_changed")
        request = {
            "expected_revision": expected_revision, "expected_hash": expected_hash,
            "goals": [{key: goal[key] for key in GOAL_FIELDS} for goal in normalized],
            "linked_journal_snapshot": linked_journal_snapshot,
        }
        request_hash = hashlib.sha256(_canonical(request)).hexdigest()
        existing = self.sessions.existing_preview(token, request_id, request_hash, now)
        if existing is not None:
            return existing
        preview_id = secrets.token_urlsafe(32)
        response = {
            "preview_id": preview_id,
            "revision": profile["revision"] + 1,
            "goals": normalized,
            "expires_at": (now + PREVIEW_TTL).isoformat().replace("+00:00", "Z"),
        }
        pending = {
            "preview_id": preview_id,
            "expires": now + PREVIEW_TTL,
            "response": response,
            "receipt": None,
            "expected_hash": expected_hash,
            "expected_revision": expected_revision,
            "linked_journal_snapshot": linked_journal_snapshot,
            "goals": normalized,
        }
        self.sessions.remember_preview(token, request_id, request_hash, pending, now)
        return response

    def confirm(self, token, preview_id, now):
        if not isinstance(preview_id, str) or not 1 <= len(preview_id) <= 256:
            raise PlanningError("invalid_request", 400)
        now = now.astimezone(timezone.utc)
        pending = self.sessions.pending_preview(token, preview_id, now)
        if pending["receipt"] is not None:
            return pending["receipt"]
        with self.lock:
            with self._file_lock():
                linked_journal_snapshot = self._linked_journal_snapshot(
                    pending["goals"], now)
                if linked_journal_snapshot != pending["linked_journal_snapshot"]:
                    raise PlanningError("planning_portfolio_changed")
                current = self._read()
                if (current["revision"] != pending["expected_revision"]
                        or not secrets.compare_digest(
                            current["profile_hash"], pending["expected_hash"])):
                    raise PlanningError("planning_profile_changed")
                profile = {
                    "schema_version": 3,
                    "revision": current["revision"] + 1,
                    "saved_at": now.isoformat().replace("+00:00", "Z"),
                    "goals": pending["goals"],
                }
                self._write(_canonical(profile))
            receipt = {"status": "saved", "revision": profile["revision"]}
            self.sessions.set_receipt(token, preview_id, receipt, now)
            return receipt

    @contextmanager
    def _file_lock(self):
        from dashboard.workspace import path_identity

        descriptor = None
        try:
            path_identity(self.path)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            lock_path = self.path.with_name(self.path.name + ".lock")
            path_identity(lock_path)
            flags = os.O_CREAT | os.O_RDWR
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(lock_path, flags, 0o600)
            path_identity(lock_path)
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
                os.fsync(descriptor)
            os.lseek(descriptor, 0, os.SEEK_SET)
        except (InvestmentError, OSError) as exc:
            if descriptor is not None:
                os.close(descriptor)
            raise PlanningError("planning_profile_lock_failed", 503) from exc
        locked = False
        try:
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise PlanningError("planning_profile_busy", 409) from exc
            locked = True
            yield
        finally:
            try:
                if locked:
                    if os.name == "nt":
                        import msvcrt
                        os.lseek(descriptor, 0, os.SEEK_SET)
                        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    def _write(self, raw):
        from dashboard.workspace import path_identity

        if len(raw) > MAX_PROFILE_BYTES:
            raise PlanningError("planning_profile_too_large", 400)
        try:
            path_identity(self.path)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            path_identity(self.path)
        except (InvestmentError, OSError) as exc:
            raise PlanningError("planning_profile_save_failed", 503) from exc
        temporary = self.path.with_name(
            f".{self.path.name}.{secrets.token_hex(16)}.tmp")
        try:
            descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                path_identity(self.path)
            except InvestmentError as exc:
                raise PlanningError("planning_profile_save_failed", 503) from exc
            os.replace(temporary, self.path)
        except OSError as exc:
            raise PlanningError("planning_profile_save_failed", 503) from exc
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
