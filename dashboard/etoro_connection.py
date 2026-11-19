"""Explicit eToro connection consent and process-local portfolio observations."""

from copy import deepcopy
from datetime import timedelta
import json
import os
from pathlib import Path
import secrets
import threading

from dashboard.workspace import path_identity
from investment import InvestmentError, etoro
from investment.core import clock, explicit_clock, iso
from investment.etoro_portfolio import collect_portfolio


MAX_PERMISSION_BYTES = 16 * 1024
PREVIEW_TTL = timedelta(minutes=5)


def _permission(value, now):
    fields = {
        "schema_version", "revision", "environment", "account_id", "approved_at",
        "expires_at", "retention_rights_verified", "read_scope_verified",
        "trade_capable_key_risk_accepted", "eligibility_verified",
        "account_binding_verified", "revoked_at",
    }
    if type(value) is not dict or set(value) != fields:
        raise InvestmentError("eToro connection permission is invalid; keep its files unchanged")
    if (type(value["schema_version"]) is not int or value["schema_version"] != 1
            or type(value["revision"]) is not int or value["revision"] < 1
            or value["environment"] not in ("real", "demo")
            or type(value["account_id"]) is not int or value["account_id"] < 1):
        raise InvestmentError("eToro connection permission is invalid; keep its files unchanged")
    approved = explicit_clock(value["approved_at"])
    expiry = explicit_clock(value["expires_at"]) if value["expires_at"] is not None else None
    revoked = explicit_clock(value["revoked_at"]) if value["revoked_at"] is not None else None
    if (expiry is not None and not timedelta(0) < expiry - approved <= timedelta(days=30)
            or any(type(value[key]) is not bool for key in (
                "retention_rights_verified", "read_scope_verified",
                "trade_capable_key_risk_accepted", "eligibility_verified",
                "account_binding_verified"))
            or value["read_scope_verified"] == value["trade_capable_key_risk_accepted"]
            or value["retention_rights_verified"] is not True
            or value["eligibility_verified"] is not True
            or value["account_binding_verified"] is not True
            or (expiry is not None and expiry <= approved)
            or revoked is not None and revoked < approved):
        raise InvestmentError("eToro connection permission is invalid; keep its files unchanged")
    return value, approved, expiry, revoked


class EToroConnection:
    """Keep portfolio data in memory; persist only explicit refresh permission."""

    def __init__(self, permission_path, clock_fn, *, transport_factory=None,
                 credential_accessor=None):
        self.permission_path = Path(permission_path).absolute()
        self.clock_fn = clock_fn
        self.transport_factory = transport_factory
        self.credential_accessor = credential_accessor
        self.lock = threading.RLock()
        self.collection_lock = threading.Lock()
        self.pending = {}
        self.snapshot = None
        self.last_status = "not_collected"
        self._startup_started = False
        self._stop = threading.Event()
        self._worker = None
        self._file_lock = None

    def _remove_pending(self, request_id):
        pending = self.pending.pop(request_id, None)
        if pending is not None:
            timer = pending.get("timer")
            if timer is not None:
                timer.cancel()

    def _purge_pending(self, now):
        for request_id, pending in tuple(self.pending.items()):
            if pending["expires"] <= now:
                self._remove_pending(request_id)

    def _expire_pending(self, request_id, token, preview_id):
        with self.lock:
            pending = self.pending.get(request_id)
            if (pending is not None and pending["token"] == token
                    and pending["response"]["preview_id"] == preview_id):
                self._remove_pending(request_id)

    def _clear_pending(self):
        for request_id in tuple(self.pending):
            self._remove_pending(request_id)

    def _read_permission(self, now):
        path_identity(self.permission_path)
        if self.permission_path.parent.exists() and not self.permission_path.parent.is_dir():
            raise InvestmentError("eToro connection location is unavailable")
        if self.permission_path.exists() and not self.permission_path.is_file():
            raise InvestmentError("eToro connection permission is not a regular file")
        if not self.permission_path.is_file():
            return None
        if self.permission_path.stat().st_size > MAX_PERMISSION_BYTES:
            raise InvestmentError("eToro connection permission exceeds its supported size")
        try:
            with self.permission_path.open("rb") as handle:
                raw = handle.read(MAX_PERMISSION_BYTES + 1)
            value = json.loads(
                raw.decode("utf-8"), object_pairs_hook=etoro._pairs,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            return _permission(value, now)
        except InvestmentError:
            raise
        except (OSError, ValueError, UnicodeError, RecursionError):
            raise InvestmentError("eToro connection permission is unreadable; keep its files unchanged") from None

    @staticmethod
    def _active(permission, now):
        value, _, expiry, revoked = permission
        return (expiry is not None and now < expiry and revoked is None
                and value["read_scope_verified"] is True)

    def status(self, now):
        now = clock(now)
        with self.lock:
            try:
                permission = self._read_permission(now)
            except InvestmentError as exc:
                return {"state": "unavailable", "message": str(exc),
                        "snapshot": deepcopy(self.snapshot), "last_status": self.last_status}
            if permission is None:
                state = "not_connected"
                public = None
            else:
                value, _, expiry, revoked = permission
                state = ("revoked" if revoked is not None else
                         "expired" if expiry is not None and expiry <= now else
                         "startup_refresh_enabled" if self._active(permission, now) else
                         "connected")
                public = {
                    "environment": value["environment"],
                    "account_id": value["account_id"],
                    "expires_at": iso(expiry) if expiry is not None else None,
                    "revoked_at": iso(revoked) if revoked is not None else None,
                    "revision": value["revision"],
                }
            return {"state": state, "connection": public,
                    "snapshot": deepcopy(self.snapshot), "last_status": self.last_status}

    def _credentials(self):
        if self.credential_accessor is not None:
            return self.credential_accessor()
        from proxy_config import configured_connection_values

        values = configured_connection_values("GOLDSCOUT_ETORO_API_KEY", "GOLDSCOUT_ETORO_USER_KEY")
        return {"x-api-key": values["GOLDSCOUT_ETORO_API_KEY"],
                "x-user-key": values["GOLDSCOUT_ETORO_USER_KEY"]}

    def _collect(self, environment, approval_check=None):
        transport = (self.transport_factory() if self.transport_factory is not None
                     else etoro.EToroTransport(before_request=approval_check))
        return collect_portfolio(
            transport, self._credentials, environment, now=self.clock_fn)

    @staticmethod
    def _validate_initial_consent(value):
        fields = {
            "environment", "read_scope_verified", "trade_capable_key_risk_accepted",
            "eligibility_verified", "retention_rights_verified",
        }
        if type(value) is not dict or set(value) != fields:
            raise InvestmentError("eToro connection request is invalid")
        if value["environment"] not in ("real", "demo"):
            raise InvestmentError("Select the eToro account environment explicitly")
        if (type(value["read_scope_verified"]) is not bool
                or type(value["trade_capable_key_risk_accepted"]) is not bool
                or value["read_scope_verified"] == value["trade_capable_key_risk_accepted"]):
            raise InvestmentError("Verify Read scope or explicitly accept broader-key risk")
        if value["eligibility_verified"] is not True or value["retention_rights_verified"] is not True:
            raise InvestmentError("Confirm account eligibility and permitted local display/retention")

    def begin(self, token, request_id, value, now):
        self._validate_initial_consent(value)
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            raise InvestmentError("eToro request identifier is invalid")
        now = clock(now)
        with self.collection_lock:
            with self.lock:
                self._purge_pending(now)
                prior = self.pending.get(request_id)
                if prior is not None:
                    if prior["token"] != token or prior["request"] != value:
                        raise InvestmentError("eToro request identifier was reused with different consent")
                    if prior["expires"] <= now:
                        raise InvestmentError("eToro portfolio preview expired; request a fresh read")
                    return deepcopy(prior["response"])
                if len(self.pending) >= 32:
                    raise InvestmentError("Too many pending eToro previews; restart the local dashboard")
            with self._claim_startup():
                snapshot = self._collect(value["environment"])
            preview_id = secrets.token_urlsafe(32)
            response = {"preview_id": preview_id, "expires_at": iso(now + PREVIEW_TTL),
                        "snapshot": snapshot}
            timer = threading.Timer(
                PREVIEW_TTL.total_seconds(), self._expire_pending,
                args=(request_id, token, preview_id))
            timer.daemon = True
            with self.lock:
                pending = {
                    "token": token, "request": deepcopy(value), "response": response,
                    "snapshot": snapshot, "expires": now + PREVIEW_TTL, "timer": timer,
                }
                self.pending[request_id] = pending
                try:
                    timer.start()
                except RuntimeError:
                    self._remove_pending(request_id)
                    raise InvestmentError("eToro preview expiry could not be scheduled") from None
            return deepcopy(response)

    def confirm(self, token, preview_id, confirm_account, startup_refresh, refresh_days, now):
        now = clock(now)
        with self.lock:
            self._purge_pending(now)
            match = next((item for item in self.pending.values()
                          if item["response"]["preview_id"] == preview_id), None)
            if match is None or match["token"] != token or match["expires"] <= now:
                raise InvestmentError("eToro portfolio preview expired or is unavailable")
            if confirm_account is not True:
                raise InvestmentError("Confirm the returned eToro account identifier before saving consent")
            if type(startup_refresh) is not bool:
                raise InvestmentError("Startup refresh selection is invalid")
            if startup_refresh and match["request"]["read_scope_verified"] is not True:
                raise InvestmentError("Automatic refresh requires verified Read-only key scope")
            if startup_refresh and (type(refresh_days) is not int or not 1 <= refresh_days <= 30):
                raise InvestmentError("Choose a startup-read permission expiry from 1 to 30 days")
            if not startup_refresh and refresh_days is not None:
                raise InvestmentError("Do not provide an expiry when startup refresh is disabled")

            old = self._read_permission(now)
            revision = old[0]["revision"] + 1 if old is not None else 1
            expiry = now + timedelta(days=refresh_days) if startup_refresh else None
            consent = match["request"]
            value = {
                "schema_version": 1, "revision": revision,
                "environment": consent["environment"],
                "account_id": match["snapshot"]["account_id"],
                "approved_at": iso(now), "expires_at": iso(expiry) if expiry else None,
                "retention_rights_verified": consent["retention_rights_verified"],
                "read_scope_verified": consent["read_scope_verified"],
                "trade_capable_key_risk_accepted": consent["trade_capable_key_risk_accepted"],
                "eligibility_verified": consent["eligibility_verified"],
                "account_binding_verified": True, "revoked_at": None,
            }
            self._write_permission(value)
            if startup_refresh:
                self._stop.clear()
            self.snapshot = deepcopy(match["snapshot"])
            self.last_status = "success"
            self._clear_pending()
            return self.status(now)

    def discard(self, token, preview_id):
        with self.lock:
            match = next((request_id for request_id, item in self.pending.items()
                          if item["token"] == token
                          and item["response"]["preview_id"] == preview_id), None)
            if match is None:
                return False
            self._remove_pending(match)
            return True

    def revoke(self, now):
        now = clock(now)
        with self.lock:
            self._clear_pending()
            permission = self._read_permission(now)
            if permission is None:
                return self.status(now)
            value = deepcopy(permission[0])
            if value["revoked_at"] is None:
                value["revision"] += 1
                value["revoked_at"] = iso(now)
                self._write_permission(value)
            self._startup_started = True
            self._stop.set()
            self.last_status = "startup_refresh_revoked"
            return self.status(now)

    def refresh_explicitly(self, now):
        now = clock(now)
        with self.lock:
            permission = self._read_permission(now)
            if permission is None or not self._active(permission, now):
                raise InvestmentError("Renew explicit eToro startup-read permission before refreshing")
            value = deepcopy(permission[0])
        try:
            with self._claim_startup():
                return self._refresh(value, now)
        except etoro.EToroError as exc:
            self.last_status = exc.code
            return {"state": "refresh_failed", "error_code": exc.code}

    def _refresh(self, expected, now):
        def verify():
            if self._stop.is_set():
                raise etoro.EToroError("approval_interrupted")
            try:
                checked = clock(self.clock_fn())
                current = self._read_permission(checked)
            except InvestmentError:
                raise etoro.EToroError("approval_interrupted") from None
            if (current is None or not self._active(current, checked)
                    or current[0]["revision"] != expected["revision"]
                    or current[0]["account_id"] != expected["account_id"]
                    or current[0]["environment"] != expected["environment"]):
                raise etoro.EToroError("approval_interrupted")

        try:
            result = self._collect(expected["environment"], approval_check=verify)
            verify()
            if result["account_id"] != expected["account_id"]:
                raise etoro.EToroError("account_binding_changed")
        except etoro.EToroError as exc:
            with self.lock:
                self.last_status = exc.code
            return {"state": "refresh_failed", "error_code": exc.code}
        with self.lock:
            try:
                verify()
            except etoro.EToroError as exc:
                self.last_status = exc.code
                return {"state": "refresh_failed", "error_code": exc.code}
            self.snapshot = result
            self.last_status = "success"
            return {"state": "refreshed", "snapshot": deepcopy(result)}

    def start_once(self):
        with self.lock:
            if self._startup_started:
                return
            self._startup_started = True
            try:
                permission = self._read_permission(self.clock_fn())
            except InvestmentError:
                self.last_status = "permission_unavailable"
                return
            if permission is None or not self._active(permission, self.clock_fn()):
                return
            expected = deepcopy(permission[0])
            self._stop.clear()
            self._worker = threading.Thread(
                target=self._refresh_startup, args=(expected, self.clock_fn()),
                name="etoro-startup-read", daemon=True)
            self._worker.start()

    def _refresh_startup(self, expected, now):
        try:
            with self._claim_startup():
                if self._stop.is_set():
                    return
                self._refresh(expected, now)
        except etoro.EToroError as exc:
            with self.lock:
                self.last_status = exc.code

    def _claim_startup(self):
        path = self.permission_path.parent / "startup.lock"
        path_identity(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path_identity(path)
        handle = path.open("a+b")
        try:
            if os.fstat(handle.fileno()).st_size == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError:
                    raise etoro.EToroError("request_in_progress") from None
            else:
                import fcntl
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    raise etoro.EToroError("request_in_progress") from None
            return _FileClaim(handle)
        except Exception:
            handle.close()
            raise

    def stop(self):
        self._stop.set()
        with self.lock:
            self._clear_pending()
        worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(timeout=1)

    def _write_permission(self, value):
        target = self.permission_path
        directory = target.parent
        path_identity(directory)
        if directory.exists() and not directory.is_dir():
            raise InvestmentError("eToro connection location is not a directory")
        directory.mkdir(parents=True, exist_ok=True)
        path_identity(directory)
        temp = directory / (".connection-" + secrets.token_hex(16) + ".tmp")
        encoded = json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_PERMISSION_BYTES:
            raise InvestmentError("eToro permission exceeds its supported size")
        try:
            with temp.open("xb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            path_identity(target)
            os.replace(temp, target)
        except OSError:
            try:
                temp.unlink()
            except FileNotFoundError:
                pass
            raise InvestmentError("eToro permission could not be saved; preserve existing files") from None


class _FileClaim:
    def __init__(self, handle):
        self.handle = handle

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
