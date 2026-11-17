"""Process-local previews for explicitly confirmed first-run setup."""

from datetime import timedelta
import secrets
import threading

from dashboard.monitoring_workflow import MonitoringWorkflowSessions, WorkflowError
from investment import InvestmentError
from investment.bootstrap import confirm_setup, preview_setup, recovery_preview, workspace_status
from investment.core import clock
from investment.store import canonical, digest


class InvestmentSetup:
    PREVIEW_TTL = timedelta(minutes=5)

    def __init__(self, workspace, enabled, now):
        self.workspace = workspace
        initial = workspace_status(workspace, now)
        self.security = (MonitoringWorkflowSessions()
                         if enabled and initial["state"] in ("empty", "recoverable") else None)
        self.completed = initial["state"] == "ready"
        self.confirm_lock = threading.Lock()

    def status(self, now):
        result = workspace_status(self.workspace, now)
        if result["state"] == "ready":
            self.completed = True
        if self.completed and result["state"] in ("empty", "manual_unopened", "recoverable"):
            result = {**result, "state": "blocked",
                      "message": "The selected completed workspace changed. Restart and inspect it; setup cannot reset it."}
        return result

    def session_payload(self, token, now):
        status = self.status(now)
        result = {"enabled": self.security is not None and not self.completed,
                  "csrf": None,
                  "state": status["state"], "workspace": str(self.workspace),
                  "message": status.get("message", "")}
        if self.security is not None:
            session = self.security.session_payload(token, now)
            result.update(session)
            result["enabled"] = not self.completed
            if "recovery" in status:
                result["recovery"] = status["recovery"]
        return result

    def authorize(self, token, csrf, now):
        return bool(self.security and self.security.authorize(token, csrf, now))

    def _remember(self, token, request_id, request_hash, value, now):
        preview_id = secrets.token_urlsafe(32)
        response = {
            "preview_id": preview_id,
            "expires_at": (clock(now) + self.PREVIEW_TTL).isoformat().replace("+00:00", "Z"),
            "profile": value["profile"], "opening": value["opening"],
            "report": value["report"],
        }
        self.security.remember_preview(token, request_id, request_hash, {
            "preview_id": preview_id, "expires": clock(now) + self.PREVIEW_TTL,
            "response": response, "value": value, "receipt": None,
        }, now)
        return response

    def preview(self, token, request_id, profile, opening, now):
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            raise WorkflowError("invalid_request_id", 400)
        request_hash = digest(canonical({"profile": profile, "opening": opening}))
        prior = self.security.existing_preview(token, request_id, request_hash, now)
        if prior is not None:
            return prior
        if self.completed:
            raise InvestmentError("This workspace is already established")
        value = preview_setup(self.workspace, profile, opening, now)
        return self._remember(token, request_id, request_hash, value, now)

    def recover(self, token, now):
        if self.completed:
            raise InvestmentError("This workspace is already established")
        value = recovery_preview(self.workspace, now)
        return self._remember(token, secrets.token_urlsafe(16),
                              digest(canonical(value)), value, now)

    def confirm(self, token, preview_id, now):
        with self.confirm_lock:
            pending = self.security.pending_preview(token, preview_id, now)
            if pending["receipt"] is not None:
                return pending["receipt"]
            if self.completed:
                raise InvestmentError("This workspace is already established")
            receipt = confirm_setup(self.workspace, pending["value"], now)
            self.security.set_receipt(token, preview_id, receipt, now)
            if receipt["status"] == "completed":
                self.completed = True
            return receipt
