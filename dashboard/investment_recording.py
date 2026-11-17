"""Opt-in local investment-recording sessions and revision-bound previews."""

from datetime import timedelta
import secrets
import sqlite3
import threading

from investment import InvestmentError
from investment.core import CORRECTABLE_CASH_EVENTS, clock
from investment.store import canonical, commit_record, digest, preview_record, read


class RecordingError(ValueError):
    def __init__(self, code, status=409):
        super().__init__(code)
        self.code = code
        self.status = status


class RecordingSessions:
    SESSION_TTL = timedelta(minutes=15)
    PREVIEW_TTL = timedelta(minutes=5)

    def __init__(self, database, clock):
        self.database = database
        self.clock = clock
        self.lock = threading.Lock()
        self.sessions = {}
        self.path_identity = self._path_identity()
        ledger = read(database, clock())
        if ledger.spec["schema_version"] != 2:
            raise ValueError("Investment recording requires an existing version 2 store")
        self.profile_hash = digest(canonical(ledger.spec))

    def _path_identity(self):
        from pathlib import Path

        path = Path(self.database).resolve(strict=True)
        stat = path.stat()
        return str(path), stat.st_dev, stat.st_ino

    def _verify_store(self):
        try:
            if self._path_identity() != self.path_identity:
                raise RecordingError("investment_store_changed", 409)
            ledger = read(self.database, self.clock())
        except (InvestmentError, OSError) as exc:
            raise RecordingError("investment_store_unavailable", 503) from exc
        if (ledger.spec["schema_version"] != 2 or
                digest(canonical(ledger.spec)) != self.profile_hash):
            raise RecordingError("investment_store_changed", 409)
        return ledger

    def session(self, token, now, create=False):
        now = clock(now)
        with self.lock:
            self.sessions = {
                key: value for key, value in self.sessions.items()
                if value["expires"] > now
            }
            record = self.sessions.get(token)
            if record is None and create:
                if len(self.sessions) >= 128:
                    raise RecordingError("recording_sessions_full", 503)
                token = secrets.token_urlsafe(32)
                record = {
                    "csrf": secrets.token_urlsafe(32),
                    "expires": now + self.SESSION_TTL,
                    "commands": [],
                    "previews": {},
                    "requests": {},
                }
                self.sessions[token] = record
            return token, record

    def authorize(self, token, csrf, now):
        now = clock(now)
        _, record = self.session(token, now)
        valid = bool(
            record and isinstance(csrf, str) and
            secrets.compare_digest(record["csrf"], csrf)
        )
        if not valid:
            return False
        with self.lock:
            commands = [stamp for stamp in record["commands"]
                        if now - stamp < timedelta(minutes=1)]
            if len(commands) >= 30:
                return False
            commands.append(now)
            record["commands"] = commands
        return True

    def session_payload(self, token, now):
        token, record = self.session(token, now, create=True)
        result = {
            "enabled": True,
            "csrf": record["csrf"], "accounts": [], "products": [],
            "latest_correctable_event": None, "opened": False,
        }
        ledger = self._verify_store()
        result["opened"] = ledger.opened
        result["accounts"] = [
            {"id": account["id"], "name": account["name"], "kind": account["kind"]}
            for account in ledger.spec["accounts"]
        ]
        result["products"] = [
            {"id": product["id"], "name": product["name"],
             "sleeve": product["sleeve"],
             "quantity_increment": product["quantity_increment"]}
            for product in ledger.spec["products"]
        ]
        latest = next(reversed(ledger.ids.values()), None)
        if latest and latest.get("type") in CORRECTABLE_CASH_EVENTS:
            result["latest_correctable_event"] = {
                key: latest[key] for key in (
                    "id", "type", "account_id", "currency", "amount_gbp", "timestamp",
                    "available_at", "evidence")
            }
            if "role" in latest:
                result["latest_correctable_event"]["role"] = latest["role"]
            if "role_evidence" in latest:
                result["latest_correctable_event"]["role_evidence"] = latest["role_evidence"]
        response = dict(result)
        response["_token"] = token
        return response

    def preview(self, token, request_id, value, now):
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            raise RecordingError("invalid_request_id", 400)
        now = clock(now)
        request_hash = digest(canonical(value))
        _, record = self.session(token, now)
        if record is None:
            raise RecordingError("recording_session_expired", 401)
        with self.lock:
            prior = record["requests"].get(request_id)
            if prior is not None:
                if prior["event_hash"] != request_hash:
                    raise RecordingError("request_id_conflict", 409)
                preview = record["previews"].get(prior["preview_id"])
                if preview is None or preview["expires"] <= now:
                    raise RecordingError("preview_expired", 409)
                return preview["response"]
        ledger = self._verify_store()
        if ledger.spec["schema_version"] != 2:
            raise RecordingError("unsupported_investment_store", 409)
        try:
            preview = preview_record(self.database, value, now)
        except InvestmentError as exc:
            if isinstance(exc.__cause__, (sqlite3.Error, OSError)):
                raise RecordingError("investment_store_unavailable", 503) from exc
            raise RecordingError("preview_rejected", 409) from exc
        preview_id = secrets.token_urlsafe(32)
        response = {
            "preview_id": preview_id,
            "revision": preview["revision"],
            "event": preview["event"],
            "before": preview["before"],
            "after": preview["after"],
            "expires_at": (now + self.PREVIEW_TTL).isoformat().replace("+00:00", "Z"),
        }
        with self.lock:
            if len(record["previews"]) >= 20:
                oldest = next(iter(record["previews"]))
                record["previews"].pop(oldest, None)
            record["previews"][preview_id] = {
                "value": preview, "response": response,
                "event_hash": request_hash, "expires": now + self.PREVIEW_TTL,
                "receipt": None,
            }
            record["requests"][request_id] = {
                "event_hash": request_hash, "preview_id": preview_id,
            }
            while len(record["requests"]) > 40:
                record["requests"].pop(next(iter(record["requests"])))
        return response

    def confirm(self, token, preview_id, now):
        now = clock(now)
        _, record = self.session(token, now)
        if record is None:
            raise RecordingError("recording_session_expired", 401)
        with self.lock:
            pending = record["previews"].get(preview_id)
            if pending is None:
                raise RecordingError("preview_unavailable", 409)
            if pending["receipt"] is not None:
                return pending["receipt"]
            if pending["expires"] <= now:
                raise RecordingError("preview_expired", 409)
        self._verify_store()
        try:
            receipt = commit_record(self.database, pending["value"], now)
        except InvestmentError as exc:
            if isinstance(exc.__cause__, (sqlite3.Error, OSError)):
                raise RecordingError("investment_store_unavailable", 503) from exc
            raise RecordingError("confirmation_rejected", 409) from exc
        with self.lock:
            pending["receipt"] = receipt
        return receipt
