"""Explicit monitoring orchestration; only approved read collection may contact eToro."""

from datetime import timedelta
import os
import uuid

from . import InvestmentError
from .core import clock, explicit_clock, fields, iso, text
from .monitoring import validate_profile
from .monitoring_store import append_attempt, read_cache
from .store import canonical, digest, read_with_clock


def validate_approval(value, profile, account_id, now):
    fields(value, ("profile_hash", "account_id", "approved_at", "expires_at",
                   "read_scope_verified", "eligibility_verified", "retention_rights_verified",
                   "account_binding_verified", "supervised_read_approved", "evidence"))
    if value["profile_hash"] != digest(canonical(profile)) or value["account_id"] != account_id:
        raise InvestmentError("Collection approval does not match the monitoring profile/account")
    approved, expires = explicit_clock(value["approved_at"]), explicit_clock(value["expires_at"])
    if not approved <= now < expires or not timedelta(0) < expires - approved <= timedelta(days=1):
        raise InvestmentError("Collection approval expired or outside its maximum one-day window")
    for key in ("read_scope_verified", "eligibility_verified", "retention_rights_verified",
                "account_binding_verified", "supervised_read_approved"):
        if value[key] is not True:
            raise InvestmentError("Read access, identity, rights and supervised-read approval are required")
    text(value["evidence"])


def collect_once(database, profile, cache_path, account_id, approval, *, allow_network=False,
                 now=None, transport=None, credential_accessor=None, collector=None, completion_clock=None):
    ledger, started = read_with_clock(database, now)
    validate_profile(profile, ledger, started)
    if allow_network is not True:
        raise InvestmentError("Collection is off; explicit --allow-network and approval are required")
    validate_approval(approval, profile, account_id, started)
    mapping = next((a for a in profile["accounts"] if a["account_id"] == account_id), None)
    if mapping is None or mapping["provider"] != "etoro":
        raise InvestmentError("Select an explicitly mapped eToro account")
    read_cache(cache_path, profile, started)
    from .etoro import EToroError, EToroTransport, collect_portfolio

    def credentials():
        return {"x-api-key": os.environ.get("GOLDSCOUT_ETORO_API_KEY", ""),
                "x-user-key": os.environ.get("GOLDSCOUT_ETORO_USER_KEY", "")}

    identifier = str(uuid.uuid4())

    def failed(code, completed):
        append_attempt(cache_path, profile, {
            "id": identifier, "account_id": account_id, "provider": "etoro",
            "attempted_at": iso(completed), "status": "failed", "error_code": code,
            "observation": None,
        }, completed)

    def completion():
        return explicit_clock(completion_clock()) if completion_clock else clock(now)

    try:
        result = (collector or collect_portfolio)(
            transport if transport is not None else EToroTransport(),
            credential_accessor or credentials)
    except EToroError as exc:
        completed = completion()
        code = ("permission_denied" if exc.code in ("authentication_refused", "permission_refused")
                else "invalid_response" if exc.code in ("schema_invalid", "schema_unsupported", "payload_invalid")
                else "provider_unavailable")
        failed(code, completed)
        raise InvestmentError("eToro read failed; a sanitized failed attempt was recorded: " + code) from None
    completed = completion()
    try:
        validate_approval(approval, profile, account_id, completed)
    except InvestmentError:
        failed("collection_interrupted", completed)
        raise
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
