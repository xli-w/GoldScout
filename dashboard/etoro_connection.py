"""Explicit eToro connection activation and process-local portfolio observations."""

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
_UNSET = object()


class ManualRefreshRejected(InvestmentError):
    """The manual read was rejected before collecting provider data."""


def _permission(value, now):
    if type(value) is dict and value.get("schema_version") == 3:
        fields = {"schema_version", "revision", "environment", "account_id",
                  "enabled_at", "revoked_at", "credential_revision"}
        if (set(value) != fields or type(value["schema_version"]) is not int
                or type(value["revision"]) is not int or value["revision"] < 1
                or (value["credential_revision"] is not None and
                    (type(value["credential_revision"]) is not int
                     or value["credential_revision"] < 0))
                or (value["environment"] is None) != (value["account_id"] is None)
                or (value["environment"] is not None and
                    (value["environment"] not in ("real", "demo")
                     or type(value["account_id"]) is not int
                     or not 1 <= value["account_id"] <= 2**63 - 1))):
            raise InvestmentError("eToro connection activation is invalid; keep its files unchanged")
        enabled = explicit_clock(value["enabled_at"]) if value["enabled_at"] is not None else None
        revoked = explicit_clock(value["revoked_at"]) if value["revoked_at"] is not None else None
        if ((value["account_id"] is None and
             (enabled is not None or revoked is None or value["credential_revision"] is not None))
                or (value["account_id"] is not None and enabled is None)
                or (revoked is not None and enabled is not None and revoked < enabled)):
            raise InvestmentError("eToro connection activation is invalid; keep its files unchanged")
        return value, enabled, None, revoked
    legacy_fields = {
        "schema_version", "revision", "environment", "account_id", "approved_at",
        "expires_at", "retention_rights_verified", "read_scope_verified",
        "trade_capable_key_risk_accepted", "eligibility_verified",
        "account_binding_verified", "revoked_at",
    }
    if type(value) is not dict or value.get("schema_version") not in (1, 2):
        raise InvestmentError("eToro connection permission is invalid; keep its files unchanged")
    expected_fields = legacy_fields | ({"credential_revision"} if value["schema_version"] == 2 else set())
    if set(value) != expected_fields:
        raise InvestmentError("eToro connection permission is invalid; keep its files unchanged")
    if (type(value["schema_version"]) is not int
            or type(value["revision"]) is not int or value["revision"] < 1
            or value["environment"] not in ("real", "demo")
            or type(value["account_id"]) is not int or not 1 <= value["account_id"] <= 2**63 - 1
            or value["schema_version"] == 2 and
            (type(value["credential_revision"]) is not int or value["credential_revision"] < 0)):
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
    """Keep portfolio data in memory; persist explicit account-bound activation."""

    def __init__(self, permission_path, clock_fn, *, transport_factory=None,
                 credential_snapshot_accessor=None):
        self.permission_path = Path(permission_path).absolute()
        self.clock_fn = clock_fn
        self.transport_factory = transport_factory
        self.credential_snapshot_accessor = credential_snapshot_accessor
        self.lock = threading.RLock()
        self.collection_lock = threading.Lock()
        self.requests = {}
        self.snapshot = None
        self._snapshot_permission = None
        self._status_permission = None
        self.last_status = "not_collected"
        self._startup_started = False
        self._startup_pending_permission = None
        self._stop = threading.Event()
        self._revocation_generation = 0
        self._worker = None
        self._file_lock = None

    def _read_permission(self, now):
        try:
            path_identity(self.permission_path)
            if self.permission_path.parent.exists() and not self.permission_path.parent.is_dir():
                raise InvestmentError("eToro connection location is unavailable")
            if self.permission_path.exists() and not self.permission_path.is_file():
                raise InvestmentError("eToro connection permission is not a regular file")
            if not self.permission_path.is_file():
                return None
            if self.permission_path.stat().st_size > MAX_PERMISSION_BYTES:
                raise InvestmentError("eToro connection permission exceeds its supported size")
            with self.permission_path.open("rb") as handle:
                raw = handle.read(MAX_PERMISSION_BYTES + 1)
            value = json.loads(
                raw.decode("utf-8"), object_pairs_hook=etoro._pairs,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            return _permission(value, now)
        except InvestmentError:
            raise
        except (OSError, ValueError, UnicodeError, RecursionError, etoro.EToroError):
            raise InvestmentError("eToro connection permission is unreadable; keep its files unchanged") from None

    def _current_credential_revision(self):
        return self._credential_snapshot()[0]

    def _credential_snapshot(self):
        try:
            if self.credential_snapshot_accessor is None:
                from proxy_config import connection_snapshot
                snapshot = connection_snapshot("GOLDSCOUT_ETORO_API_KEY", "GOLDSCOUT_ETORO_USER_KEY")
                value = (snapshot.revision, {
                    "x-api-key": snapshot.values["GOLDSCOUT_ETORO_API_KEY"],
                    "x-user-key": snapshot.values["GOLDSCOUT_ETORO_USER_KEY"]})
            else:
                value = self.credential_snapshot_accessor()
        except (OSError, RuntimeError, ValueError):
            raise InvestmentError("eToro credential configuration is unavailable") from None
        if (type(value) is not tuple or len(value) != 2 or type(value[0]) is not int
                or value[0] < 0 or type(value[1]) is not dict
                or set(value[1]) != {"x-api-key", "x-user-key"}
                or any(type(item) is not str for item in value[1].values())):
            raise InvestmentError("eToro credential configuration is unavailable")
        return value[0], dict(value[1])

    def _credentials_current(self, permission):
        current = self._current_credential_revision()
        return current is None or permission[0].get("credential_revision") == current

    def _active(self, permission, now):
        value, enabled, _, revoked = permission
        return (value["schema_version"] == 3 and value["account_id"] is not None
                and revoked is None and enabled <= now
                and self._credentials_current(permission))

    def status(self, now):
        now = clock(now)
        with self.lock:
            try:
                permission = self._read_permission(now)
                credentials_current = (
                    permission is None or permission[0]["schema_version"] != 3
                    or permission[0]["account_id"] is None or permission[3] is not None
                    or self._credentials_current(permission))
            except InvestmentError as exc:
                return {"state": "unavailable", "message": str(exc),
                        "snapshot": None, "last_status": self.last_status}
            if permission is None:
                state = "not_connected"
                public = None
            else:
                value, _, expiry, revoked = permission
                state = ("not_connected" if value["account_id"] is None else
                         "revoked" if revoked is not None else
                         "reauthorization_required" if value["schema_version"] != 3 else
                         "reauthorization_required" if not credentials_current else
                         "startup_refresh_enabled" if permission[1] <= now else
                         "unavailable")
                public = None if value["account_id"] is None else {
                    "environment": value["environment"],
                    "account_id": value["account_id"],
                    "revoked_at": iso(revoked) if revoked is not None else None,
                    "revision": value["revision"],
                    "reauthorization_required": state == "reauthorization_required",
                }
            snapshot_matches = (self.snapshot is not None and permission is not None
                                and permission[0] == self._snapshot_permission)
            status_matches = (self._status_permission is None or
                              permission is not None and permission[0] == self._status_permission)
            status = (("startup_refresh_revoked" if state == "revoked" else "connection_changed")
                      if not status_matches else self.last_status)
            return {"state": state, "connection": public,
                    "connection_revision": permission[0]["revision"] if permission else None,
                    "snapshot": deepcopy(self.snapshot) if snapshot_matches else None,
                    "last_status": status,
                    "startup_pending": (self._startup_pending_permission is not None
                                        and permission is not None
                                        and permission[0] == self._startup_pending_permission),
                    "read_generation": self._revocation_generation}

    def _collect(self, environment, approval_check=None, expected_account_id=None,
                 include_closed_history=False, on_dispatch=None, credentials=None):
        if self.transport_factory is None:
            transport = etoro.EToroTransport(
                before_request=approval_check, on_dispatch=on_dispatch)
        else:
            selected_transport = self.transport_factory()

            def guarded_transport(path, headers):
                if approval_check is not None:
                    approval_check()
                if on_dispatch is not None:
                    on_dispatch()
                return selected_transport(path, headers)

            transport = guarded_transport
        return collect_portfolio(
            transport, lambda: dict(credentials), environment, now=self.clock_fn,
            expected_account_id=expected_account_id, before_balance=approval_check,
            include_closed_history=include_closed_history, before_history=approval_check)

    def begin_generation(self):
        with self.lock:
            return self._revocation_generation

    def capture_connection(self, now):
        with self.lock:
            return self._read_permission(clock(now))

    def begin(self, token, request_id, value, now, generation=None, initial=_UNSET,
              expected_revision=_UNSET):
        if (type(value) is not dict or set(value) != {"environment"}
                or value["environment"] not in ("real", "demo")):
            raise InvestmentError("eToro connection request is invalid")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            raise InvestmentError("eToro request identifier is invalid")
        now = clock(now)
        if generation is None:
            generation = self.begin_generation()
        request_dispatched = False
        if initial is _UNSET:
            try:
                initial = self.capture_connection(now)
            except InvestmentError as exc:
                initial = exc
        fingerprint = (token, value["environment"], generation, expected_revision)

        def mark_dispatched():
            nonlocal request_dispatched
            request_dispatched = True

        def interrupt_code():
            return ("connection_read_outcome_uncertain" if request_dispatched
                    else "approval_interrupted")

        def verify():
            with self.lock:
                if generation != self._revocation_generation:
                    raise etoro.EToroError("approval_interrupted")
                try:
                    checked = clock(self.clock_fn())
                    current = self._read_permission(checked)
                    credentials_current = self._credential_snapshot() == (credential_revision, credentials)
                except (InvestmentError, OSError, RuntimeError):
                    raise etoro.EToroError("approval_interrupted") from None
                if current != initial or not credentials_current:
                    raise etoro.EToroError("approval_interrupted")

        with self.collection_lock:
            with self.lock:
                prior = self.requests.get(request_id)
                if prior is not None:
                    if prior["fingerprint"] != fingerprint:
                        raise InvestmentError("eToro request identifier was reused")
                    if prior["error"] is not None:
                        raise etoro.EToroError(prior["error"])
                    current_status = self.status(now)
                    committed = prior["saved"][0]
                    current_status["activation_receipt"] = {
                        "committed": True, "revision": committed["revision"],
                        "environment": committed["environment"], "account_id": committed["account_id"]}
                    return current_status
                if generation != self._revocation_generation:
                    raise etoro.EToroError(interrupt_code())
                if isinstance(initial, InvestmentError):
                    raise initial
                if len(self.requests) >= 128:
                    raise InvestmentError("Too many eToro connection requests; restart the dashboard")
                if (expected_revision is not _UNSET and
                        expected_revision != (initial[0]["revision"] if initial else None)):
                    raise InvestmentError("eToro connection changed; reload status before enabling")
                credential_revision, credentials = self._credential_snapshot()
            try:
                with self._claim_startup():
                    verify()
                    snapshot = self._collect(
                        value["environment"], approval_check=verify,
                        on_dispatch=mark_dispatched, credentials=credentials)
                    with self.lock, self._claim_mutation():
                        verify()
                        try:
                            completed = clock(self.clock_fn())
                        except (InvestmentError, OSError, RuntimeError):
                            raise etoro.EToroError(interrupt_code()) from None
                        saved = {
                            "schema_version": 3,
                            "revision": initial[0]["revision"] + 1 if initial else 1,
                            "environment": value["environment"],
                            "account_id": snapshot["account_id"],
                            "enabled_at": iso(completed), "revoked_at": None,
                            "credential_revision": credential_revision,
                        }
                        self._write_permission(saved)
                        self._stop.clear()
                        self.snapshot = deepcopy(snapshot)
                        self._snapshot_permission = deepcopy(saved)
                        self._status_permission = deepcopy(saved)
                        self.last_status = "success"
                        response = self.status(completed)
                        response["activation_receipt"] = {
                            "committed": True, "revision": saved["revision"],
                            "environment": saved["environment"], "account_id": saved["account_id"]}
                        self.requests[request_id] = {
                            "fingerprint": fingerprint, "error": None,
                            "saved": _permission(saved, completed)}
                        return response
            except (InvestmentError, OSError, RuntimeError) as exc:
                if request_dispatched or (isinstance(exc, etoro.EToroError)
                                          and exc.code == "cancellation_unconfirmed"):
                    code = (exc.code if isinstance(exc, etoro.EToroError)
                            else "connection_read_outcome_uncertain")
                    if code in ("approval_interrupted", "cancellation_unconfirmed", "request_in_progress"):
                        code = "connection_read_outcome_uncertain"
                    with self.lock:
                        self.requests[request_id] = {"fingerprint": fingerprint, "error": code}
                        if code == "connection_read_outcome_uncertain":
                            self.last_status = code
                            self._status_permission = deepcopy(initial[0]) if initial else None
                    raise etoro.EToroError(code) from None
                raise

    def activation_receipt(self, token, request_id, value, now, generation, expected_revision):
        if (type(value) is not dict or set(value) != {"environment"}
                or value["environment"] not in ("real", "demo")
                or not isinstance(request_id, str) or not 1 <= len(request_id) <= 128):
            raise InvestmentError("eToro connection request is invalid")
        fingerprint = (token, value["environment"], generation, expected_revision)
        with self.lock:
            prior = self.requests.get(request_id)
            if prior is None:
                return {"receipt_status": "unknown"}
            if prior["fingerprint"] != fingerprint:
                raise InvestmentError("eToro request identifier was reused")
            if prior["error"] is not None:
                return {"receipt_status": "unknown"}
            current = self.status(now)
            committed = prior["saved"][0]
            current["receipt_status"] = "committed"
            current["activation_receipt"] = {
                "committed": True, "revision": committed["revision"],
                "environment": committed["environment"], "account_id": committed["account_id"]}
            return current

    def revoke(self, now):
        now = clock(now)
        with self.lock:
            self._revocation_generation += 1
            self._startup_started = True
            self._stop.set()
            self.last_status = "startup_refresh_revoked"
            with self._claim_mutation():
                permission = self._read_permission(now)
                if permission is None:
                    value = {"schema_version": 3, "revision": 1,
                             "environment": None, "account_id": None,
                             "enabled_at": None, "revoked_at": iso(now),
                             "credential_revision": None}
                    self._write_permission(value)
                else:
                    value = deepcopy(permission[0])
                    value["revision"] += 1
                    if value["revoked_at"] is None:
                        value["revoked_at"] = iso(now)
                    _permission(value, now)
                    self._write_permission(value)
                self._status_permission = deepcopy(value)
            return self.status(now)

    def refresh_explicitly(self, now, generation=None, expected_connection=None):
        now = clock(now)
        with self.lock:
            permission = self._read_permission(now)
            if (type(generation) is not int or generation != self._revocation_generation
                    or permission is None or not self._active(permission, now)
                    or expected_connection != (
                        permission[0]["revision"], permission[0]["account_id"],
                        permission[0]["environment"])):
                raise ManualRefreshRejected("eToro connection is unavailable or interrupted")
            value = deepcopy(permission[0])
            self._stop.clear()
        try:
            with self._claim_startup():
                return self._refresh(value, now, manual=True, generation=generation)
        except etoro.EToroError as exc:
            with self.lock:
                self.last_status = exc.code
                self._status_permission = deepcopy(value)
            return {"state": "refresh_failed", "error_code": exc.code}

    def _refresh(self, expected, now, *, manual=False, generation=None):
        dispatched = False
        try:
            bound_credentials = self._credential_snapshot()
        except InvestmentError:
            with self.lock:
                self.last_status = "refresh_unavailable"
                self._status_permission = deepcopy(expected)
            return {"state": "refresh_failed", "error_code": "refresh_unavailable"}

        def mark_dispatched():
            nonlocal dispatched
            dispatched = True

        def verify():
            if self._stop.is_set():
                raise etoro.EToroError("approval_interrupted")
            try:
                checked = clock(self.clock_fn())
                current = self._read_permission(checked)
                active = current is not None and self._active(current, checked)
                credentials_current = self._credential_snapshot() == bound_credentials
            except (InvestmentError, OSError, RuntimeError):
                raise etoro.EToroError("approval_interrupted") from None
            if (current is None or current[3] is not None
                    or not active or not credentials_current
                    or bound_credentials[0] != expected["credential_revision"]
                    or (manual and generation != self._revocation_generation)
                    or current[0] != expected):
                raise etoro.EToroError("approval_interrupted")

        try:
            verify()
            result = self._collect(expected["environment"], approval_check=verify,
                                   expected_account_id=expected["account_id"],
                                   include_closed_history=manual, on_dispatch=mark_dispatched,
                                   credentials=bound_credentials[1])
            verify()
            if result["account_id"] != expected["account_id"]:
                raise etoro.EToroError("account_binding_changed")
        except etoro.EToroError as exc:
            code = ("connection_read_outcome_uncertain"
                    if exc.code == "cancellation_unconfirmed" or dispatched and exc.code == "approval_interrupted"
                    else exc.code)
            with self.lock:
                self.last_status = code
                self._status_permission = deepcopy(expected)
            return {"state": "refresh_failed", "error_code": code}
        except InvestmentError:
            with self.lock:
                self.last_status = "refresh_unavailable"
                self._status_permission = deepcopy(expected)
            raise
        except (OSError, RuntimeError):
            with self.lock:
                self.last_status = "refresh_unavailable"
                self._status_permission = deepcopy(expected)
            return {"state": "refresh_failed", "error_code": "refresh_unavailable"}
        try:
            with self.lock, self._claim_mutation():
                verify()
                self.snapshot = result
                self._snapshot_permission = deepcopy(expected)
                self._status_permission = deepcopy(expected)
                self.last_status = "success"
                return {"state": "refreshed", "snapshot": deepcopy(result)}
        except (InvestmentError, OSError, RuntimeError) as exc:
            with self.lock:
                self.last_status = ("connection_read_outcome_uncertain"
                                    if dispatched else
                                    exc.code if isinstance(exc, etoro.EToroError) else "refresh_unavailable")
                self._status_permission = deepcopy(expected)
                return {"state": "refresh_failed", "error_code": self.last_status}

    def start_once(self):
        with self.lock:
            if self._startup_started:
                return
            self._startup_started = True
            try:
                now = clock(self.clock_fn())
                permission = self._read_permission(now)
                active = permission is not None and self._active(permission, now)
            except InvestmentError:
                self.last_status = "permission_unavailable"
                return
            if not active:
                return
            expected = deepcopy(permission[0])
            self._stop.clear()
            self._startup_pending_permission = deepcopy(expected)
            self._worker = threading.Thread(
                target=self._refresh_startup, args=(expected, now),
                name="etoro-startup-read", daemon=True)
            try:
                self._worker.start()
            except RuntimeError:
                self._startup_pending_permission = None
                self.last_status = "startup_unavailable"
                self._status_permission = expected

    def _refresh_startup(self, expected, now):
        try:
            with self._claim_startup():
                if self._stop.is_set():
                    return
                self._refresh(expected, now)
        except etoro.EToroError as exc:
            with self.lock:
                self.last_status = exc.code
                self._status_permission = deepcopy(expected)
        finally:
            with self.lock:
                if self._startup_pending_permission == expected:
                    self._startup_pending_permission = None

    def _claim_startup(self):
        try:
            return self._claim_file("startup.lock")
        except etoro.EToroError:
            raise
        except (InvestmentError, OSError):
            raise etoro.EToroError("startup_lock_unavailable") from None

    def _claim_mutation(self):
        return self._claim_file("connection.lock", blocking=True)

    def _claim_file(self, name, blocking=False):
        path = self.permission_path.parent / name
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
                    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
                except OSError:
                    raise etoro.EToroError("request_in_progress") from None
            else:
                import fcntl
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
                except OSError:
                    raise etoro.EToroError("request_in_progress") from None
            return _FileClaim(handle)
        except Exception:
            handle.close()
            raise

    def stop(self):
        self._stop.set()
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
