"""Provider-neutral, read-only reconciliation of operator-staged observations."""

from datetime import timedelta
from decimal import Decimal, localcontext

from . import InvestmentError
from .core import RECORDED_BALANCE, ZERO, clock, fields, fmt, iso, text
from .store import canonical, digest, read_with_clock

_COVERAGE = ("complete", "partial", "unknown")


def _timestamp(value):
    if not isinstance(value, str):
        raise InvestmentError("Observation timestamps must be explicit UTC strings")
    return clock(value)


def _observed_number(value):
    if not isinstance(value, str) or not RECORDED_BALANCE.fullmatch(value):
        raise InvestmentError("Observed values must be bounded exact nonnegative decimal strings")
    return Decimal(value)


def _mapping(items, source_field, target_field, targets, accounts=None):
    if type(items) is not list or len(items) > 100:
        raise InvestmentError("Mappings must be bounded lists")
    result, used = {}, set()
    required = (source_field, target_field, "evidence")
    if accounts is not None:
        required += ("account_binding",)
    for item in items:
        fields(item, required)
        for field in required:
            text(item[field])
        source, target = item[source_field], item[target_field]
        if source in result or target in used or target not in targets:
            raise InvestmentError("Unknown or ambiguous observation mapping")
        if accounts is not None and item["account_binding"] != accounts[target]["binding"]:
            raise InvestmentError("Observation account binding does not match the investment profile")
        result[source] = item
        used.add(target)
    return result


def _comparison(observed, recorded, comparable):
    if observed is None or not comparable:
        return {"observed": fmt(observed), "recorded": None, "difference": None,
                "status": "unavailable"}
    difference = observed - recorded
    return {"observed": fmt(observed), "recorded": fmt(recorded),
            "difference": fmt(difference), "status": "match" if difference == 0 else "difference"}


def preview_observations(database, staged, max_age_seconds, now=None):
    """Compare explicit observations with a pinned investment revision without writing."""
    if type(max_age_seconds) is not int or not 1 <= max_age_seconds <= 365 * 86400:
        raise InvestmentError("Explicit maximum observation age must be 1 to 31536000 seconds")
    ledger, now = read_with_clock(database, now)
    if ledger.spec["schema_version"] != 2 or not ledger.opened:
        raise InvestmentError("Reconciliation requires a v2 store with opening evidence")
    fields(staged, ("schema_version", "portfolio_id", "currency", "source", "evidence",
                    "mapping_observed_at", "account_mappings", "product_mappings", "accounts"))
    if (type(staged["schema_version"]) is not int or staged["schema_version"] != 1
            or staged["portfolio_id"] != ledger.spec["portfolio_id"] or staged["currency"] != "GBP"):
        raise InvestmentError("Observation schema, portfolio binding or currency is invalid")
    text(staged["source"])
    text(staged["evidence"])
    mapping_at = _timestamp(staged["mapping_observed_at"])
    if mapping_at > now:
        raise InvestmentError("Observation mapping is not yet available")
    account_spec = {item["id"]: item for item in ledger.spec["accounts"]}
    product_spec = {item["id"]: item for item in ledger.spec["products"]}
    accounts = _mapping(staged["account_mappings"], "source_account", "account_id",
                        account_spec, account_spec)
    products = _mapping(staged["product_mappings"], "source_product", "product_id", product_spec)
    sources = staged["accounts"]
    if type(sources) is not list or len(sources) > 50:
        raise InvestmentError("Observed accounts must be a bounded list")

    journal_at = max(ledger.last_time, ledger.role_last_time, ledger.terms_last_time)
    journal_available_at = max(ledger.latest_available_at, ledger.terms_latest_available_at)
    output, source_ids, covered_accounts = [], set(), set()
    attention = False
    with localcontext() as context:
        context.prec = 100
        for source in sources:
            fields(source, ("source_account", "observed_at", "available_at", "cash_coverage",
                            "holdings_coverage", "coverage_evidence", "cash_gbp", "holdings"))
            source_id = text(source["source_account"])
            if source_id in source_ids:
                raise InvestmentError("Duplicate observed source account")
            source_ids.add(source_id)
            observed_at, available_at = _timestamp(source["observed_at"]), _timestamp(source["available_at"])
            if observed_at > available_at or available_at > now:
                raise InvestmentError("Observation evidence is future-dated or out of order")
            if source["cash_coverage"] not in _COVERAGE or source["holdings_coverage"] not in _COVERAGE:
                raise InvestmentError("Invalid source coverage declaration")
            text(source["coverage_evidence"])
            cash = None if source["cash_gbp"] is None else _observed_number(source["cash_gbp"])
            holdings = source["holdings"]
            if type(holdings) is not list or len(holdings) > 100:
                raise InvestmentError("Observed holdings must be a bounded list")
            mapping = accounts.get(source_id)
            account_id = mapping["account_id"] if mapping else None
            if account_id is not None:
                covered_accounts.add(account_id)
            freshness = "stale" if now - observed_at >= timedelta(seconds=max_age_seconds) else "fresh"
            comparable = (account_id is not None and mapping_at <= observed_at and
                          journal_at < observed_at and journal_available_at <= observed_at)
            reason = None
            if account_id is None:
                reason = "unmapped_account"
            elif mapping_at > observed_at:
                reason = "mapping_not_available_at_observation"
            elif not comparable:
                reason = "journal_clock_or_availability_not_comparable"
            cash_result = _comparison(cash, ledger.cash[account_id] if account_id else ZERO, comparable)
            rows, seen, mapped = [], set(), set()
            for holding in holdings:
                fields(holding, ("source_product", "quantity"))
                source_product = text(holding["source_product"])
                if source_product in seen:
                    raise InvestmentError("Duplicate observed source product")
                seen.add(source_product)
                quantity = _observed_number(holding["quantity"])
                product = products.get(source_product)
                product_id = product["product_id"] if product else None
                if product_id is not None:
                    mapped.add(product_id)
                holding_comparable = (comparable and product is not None and
                                      account_spec[account_id]["kind"] == "broker")
                comparison = _comparison(quantity, ledger.holdings.get((account_id, product_id), ZERO),
                                         holding_comparable)
                if product is None or account_id is None:
                    comparison["status"] = "unmapped"
                rows.append({"source_product": source_product, "product_id": product_id,
                             "mapping_evidence": product["evidence"] if product else None,
                             **comparison})
            missing = sorted(product for (account, product), quantity in ledger.holdings.items()
                             if account == account_id and quantity > 0 and product not in mapped)
            result = {
                "source_account": source_id, "account_id": account_id,
                "mapping_evidence": mapping["evidence"] if mapping else None,
                "observed_at": iso(observed_at), "available_at": iso(available_at),
                "freshness": freshness, "comparison_unavailable_reason": reason,
                "cash_coverage": source["cash_coverage"], "holdings_coverage": source["holdings_coverage"],
                "coverage_evidence": source["coverage_evidence"], "cash": cash_result,
                "holdings": rows, "missing_recorded_products": missing,
            }
            if (freshness != "fresh" or not comparable or cash_result["status"] != "match"
                    or source["cash_coverage"] != "complete"
                    or source["holdings_coverage"] != "complete" or missing
                    or any(row["status"] != "match" for row in rows)):
                attention = True
            output.append(result)
    missing_accounts = sorted(set(account_spec) - covered_accounts)
    previous_discrepancies = [
        {"account_id": account, **observation}
        for account, observations in sorted(ledger.cash_observation_history.items())
        for observation in observations if Decimal(observation["difference_gbp"]) != 0
    ]
    return {
        "schema_version": 1, "portfolio_id": ledger.spec["portfolio_id"],
        "profile_sha256": digest(canonical(ledger.spec)), "revision": ledger.revision,
        "journal_last_event_at": iso(journal_at), "journal_evidence_available_at": iso(journal_available_at),
        "generated_at": iso(now), "source": staged["source"], "evidence": staged["evidence"],
        "mapping_observed_at": iso(mapping_at), "observation_sha256": digest(canonical(staged)),
        "max_age_seconds": max_age_seconds,
        "status": "attention" if attention or missing_accounts or previous_discrepancies else "matched",
        "accounts": output, "missing_recorded_accounts": missing_accounts,
        "previous_cash_discrepancies": previous_discrepancies,
        "limitations": [
            "Local source, completeness and mappings are operator declarations, not independently verified.",
            "No omitted observation is treated as zero; this preview does not correct or change the journal.",
            "Matches are as of observation clocks, not proof of present custody, settlement or buying power.",
        ],
    }
