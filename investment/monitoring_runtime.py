"""Explicit monitoring orchestration for gated eToro read collection."""

import threading
import uuid

from . import InvestmentError
from .core import clock, explicit_clock, iso
from .monitoring import validate_profile
from .monitoring_store import append_attempt, read_cache
from .store import read_with_clock


class WatchCredentialRevision:
    """Keep one effective credential snapshot for the lifetime of a watch."""

    def __init__(self):
        self._snapshot = None

    def check(self, snapshot):
        if self._snapshot is None:
            self._snapshot = snapshot
        elif self._snapshot != snapshot:
            raise InvestmentError("eToro credentials changed during watch; collection stopped")


def collect_once(database, profile, cache_path, account_id, *, allow_network=False,
                 now=None, transport=None, credential_accessor=None, collector=None, completion_clock=None,
                 watch_credential_revision=None):
    ledger, started = read_with_clock(database, now)
    validate_profile(profile, ledger, started)
    if allow_network is not True:
        raise InvestmentError("Collection is off; explicit --allow-network is required")
    mapping = next((a for a in profile["accounts"] if a["account_id"] == account_id), None)
    if mapping is None or mapping["provider"] != "etoro":
        raise InvestmentError("Select an explicitly mapped eToro account")
    read_cache(cache_path, profile, started)
    from proxy_config import ProxyConfigurationError, connection_snapshot

    try:
        bound_credentials = connection_snapshot(
            "GOLDSCOUT_ETORO_API_KEY", "GOLDSCOUT_ETORO_USER_KEY")
    except ProxyConfigurationError:
        raise InvestmentError("eToro credential configuration is unavailable") from None
    if watch_credential_revision is not None:
        watch_credential_revision.check(bound_credentials)
    from .etoro import EToroError, EToroTransport, collect_portfolio

    def credentials():
        values = bound_credentials.values
        expected = {"x-api-key": values["GOLDSCOUT_ETORO_API_KEY"],
                    "x-user-key": values["GOLDSCOUT_ETORO_USER_KEY"]}
        if credential_accessor is not None and credential_accessor() != expected:
            raise EToroError("credentials_unavailable")
        return expected

    identifier = str(uuid.uuid4())
    clock_lock = threading.Lock()
    last_checked = started
    dispatch_clock_failure = None

    def failed(code, completed):
        append_attempt(cache_path, profile, {
            "id": identifier, "account_id": account_id, "provider": "etoro",
            "attempted_at": iso(completed), "status": "failed", "error_code": code,
            "observation": None,
        }, completed)

    def completion():
        nonlocal last_checked
        with clock_lock:
            try:
                checked = explicit_clock(completion_clock()) if completion_clock is not None else clock()
            except Exception:
                raise InvestmentError("Collection clock unavailable; no attempt recorded") from None
            if checked < last_checked:
                raise InvestmentError("Collection clock moved backwards; no attempt recorded")
            last_checked = checked
            return checked

    def check_dispatch():
        nonlocal dispatch_clock_failure
        try:
            completion()
        except InvestmentError as exc:
            dispatch_clock_failure = str(exc)
            raise EToroError("approval_interrupted") from None
        try:
            if connection_snapshot(
                    "GOLDSCOUT_ETORO_API_KEY", "GOLDSCOUT_ETORO_USER_KEY"
            ) != bound_credentials:
                raise EToroError("approval_interrupted")
        except ProxyConfigurationError:
            raise EToroError("approval_interrupted") from None

    try:
        selected_transport = transport if transport is not None else EToroTransport(before_request=check_dispatch)
        check_dispatch()

        def guarded_transport(path, headers):
            if (type(headers) is not dict
                    or headers.get("x-api-key") != bound_credentials.values["GOLDSCOUT_ETORO_API_KEY"]
                    or headers.get("x-user-key") != bound_credentials.values["GOLDSCOUT_ETORO_USER_KEY"]):
                raise EToroError("approval_interrupted")
            check_dispatch()
            return selected_transport(path, headers)

        result = (collector or collect_portfolio)(guarded_transport, credentials)
    except EToroError as exc:
        if exc.code == "cancellation_unconfirmed":
            warning = "eToro cancellation could not be confirmed; a request may remain in flight"
            try:
                completed = completion()
            except InvestmentError:
                raise InvestmentError(warning + "; collection clock unavailable or moved backwards; "
                                      "no attempt recorded") from None
            try:
                failed("provider_unavailable", completed)
            except Exception:
                raise InvestmentError(warning + "; failed attempt recording could not be confirmed") from None
            raise InvestmentError(warning + "; a failed attempt was recorded") from None
        if exc.code == "approval_interrupted" and dispatch_clock_failure is not None:
            raise InvestmentError(dispatch_clock_failure) from None
        completed = completion()
        if exc.code == "approval_interrupted":
            failed("collection_interrupted", completed)
            raise InvestmentError("Collection interrupted: credentials changed or dispatch check unavailable") from None
        code = ("permission_denied" if exc.code in ("authentication_refused", "permission_refused")
                else "invalid_response" if exc.code in ("schema_invalid", "schema_unsupported", "payload_invalid")
                else "provider_unavailable")
        failed(code, completed)
        raise InvestmentError("eToro read failed; a sanitized failed attempt was recorded: " + code) from None
    completed = completion()
    try:
        revision_unchanged = connection_snapshot(
            "GOLDSCOUT_ETORO_API_KEY", "GOLDSCOUT_ETORO_USER_KEY") == bound_credentials
    except ProxyConfigurationError:
        revision_unchanged = False
    if not revision_unchanged:
        failed("collection_interrupted", completed)
        raise InvestmentError("Collection interrupted: credentials changed or revision unavailable before publication") from None
    # This endpoint has no verified source clock, currency-complete holdings or
    # account identity. Never call receipt time the valuation's as-of time.
    observation = {
        "id": "receipt-" + identifier, "account_id": account_id, "provider": "etoro",
        "observed_at": None, "available_at": iso(completed), "currency": result["account_currency"],
        "cash": None, "holdings": [], "coverage": "partial",
        "evidence": "eToro Read snapshot received; native units/prices, account identity and source clock unresolved",
        "native_summary": {"credit_usd": result["raw"]["credit"],
                           "position_count": len(result["raw"]["positions"])},
    }
    revision = append_attempt(cache_path, profile, {
        "id": identifier, "account_id": account_id, "provider": "etoro",
        "attempted_at": iso(completed), "status": "success", "error_code": None,
        "observation": observation,
    }, completed)
    return {"revision": revision, "status": "partial_observation", "currency": result["account_currency"],
            "source_as_of": None, "live_validation": "unvalidated",
            "position_count": len(result["raw"]["positions"]),
            "accounting_writes": False, "affects_trading": False}
