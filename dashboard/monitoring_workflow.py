"""Opt-in local sessions and ephemeral previews for monitoring workflow actions."""

from datetime import timedelta
import secrets
import threading

from investment.core import clock


class WorkflowError(ValueError):
    def __init__(self, code, status=409):
        super().__init__(code)
        self.code = code
        self.status = status


class MonitoringWorkflowSessions:
    SESSION_TTL = timedelta(minutes=15)
    PREVIEW_TTL = timedelta(minutes=5)

    def __init__(self):
        self.lock = threading.Lock()
        self.sessions = {}

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
                    raise WorkflowError("workflow_sessions_full", 503)
                token = secrets.token_urlsafe(32)
                record = {
                    "csrf": secrets.token_urlsafe(32),
                    "expires": now + self.SESSION_TTL, "commands": [],
                    "previews": {}, "requests": {},
                }
                self.sessions[token] = record
            return token, record

    def session_payload(self, token, now):
        token, record = self.session(token, now, create=True)
        return {"enabled": True, "csrf": record["csrf"], "_token": token}

    def authorize(self, token, csrf, now):
        now = clock(now)
        _, record = self.session(token, now)
        if (record is None or not isinstance(csrf, str)
                or not secrets.compare_digest(record["csrf"], csrf)):
            return False
        with self.lock:
            commands = [stamp for stamp in record["commands"]
                        if now - stamp < timedelta(minutes=1)]
            if len(commands) >= 30:
                return False
            commands.append(now)
            record["commands"] = commands
        return True

    def existing_preview(self, token, request_id, request_hash, now):
        now = clock(now)
        _, record = self.session(token, now)
        if record is None:
            raise WorkflowError("workflow_session_expired", 401)
        with self.lock:
            prior = record["requests"].get(request_id)
            if prior is None:
                return None
            if prior["request_hash"] != request_hash:
                raise WorkflowError("request_id_conflict")
            pending = record["previews"].get(prior["preview_id"])
            if pending is None or pending["expires"] <= now:
                raise WorkflowError("preview_expired")
            return pending["response"]

    def remember_preview(self, token, request_id, request_hash, pending, now):
        now = clock(now)
        _, record = self.session(token, now)
        if record is None:
            raise WorkflowError("workflow_session_expired", 401)
        with self.lock:
            if request_id in record["requests"]:
                raise WorkflowError("request_id_conflict")
            while len(record["previews"]) >= 20:
                oldest = next(iter(record["previews"]))
                record["previews"].pop(oldest, None)
            record["previews"][pending["preview_id"]] = pending
            record["requests"][request_id] = {
                "request_hash": request_hash, "preview_id": pending["preview_id"]}
            while len(record["requests"]) > 40:
                record["requests"].pop(next(iter(record["requests"])))

    def pending_preview(self, token, preview_id, now):
        now = clock(now)
        _, record = self.session(token, now)
        if record is None:
            raise WorkflowError("workflow_session_expired", 401)
        with self.lock:
            pending = record["previews"].get(preview_id)
            if pending is None:
                raise WorkflowError("preview_unavailable")
            if pending["receipt"] is not None:
                return pending
            if pending["expires"] <= now:
                raise WorkflowError("preview_expired")
            return pending

    def set_receipt(self, token, preview_id, receipt, now):
        now = clock(now)
        _, record = self.session(token, now)
        if record is None:
            raise WorkflowError("workflow_session_expired", 401)
        with self.lock:
            pending = record["previews"].get(preview_id)
            if pending is None:
                raise WorkflowError("preview_unavailable")
            if pending["receipt"] is not None:
                return pending["receipt"]
            pending["receipt"] = receipt
            return receipt


def request_fingerprint(value):
    from investment.store import canonical, digest

    return digest(canonical(value))
