"""Pure evidence-bound monitoring. No collection, accounting writes or AI inputs."""

from datetime import timedelta
from decimal import Decimal, localcontext
import re

from . import InvestmentError
from .core import ZERO, explicit_clock, fields, fmt, iso, number, text
from .store import canonical, digest, load_json, read_with_clock

LIMITATIONS = [
    "Findings are review prompts, not orders, investment advice or verified custody.",
    "No assumed FX, tax, LISA eligibility, bonus entitlement or missing account history.",
    "Costs are recorded facts only; total expense and LISA bonus attribution may be incomplete.",
    "Instrument concentration is not fund look-through or correlated-exposure analysis.",
    "Provider observations do not update accounting or establish complete GBP performance.",
    "Journal and observation cache are pinned separately, not atomically across stores.",
]


def validate_profile(value, ledger, now):
    fields(value, ("schema_version", "portfolio_id", "investment_profile_sha256",
                   "observed_at", "accounts", "concentration_threshold_pct"))
    if (type(value["schema_version"]) is not int or value["schema_version"] != 1
            or ledger.spec["schema_version"] != 2 or not ledger.opened
            or value["portfolio_id"] != ledger.spec["portfolio_id"]
            or value["investment_profile_sha256"] != digest(canonical(ledger.spec))):
        raise InvestmentError("Monitoring requires an opened, explicitly bound v2 portfolio")
    if explicit_clock(value["observed_at"]) > now:
        raise InvestmentError("Monitoring profile is not yet available")
    items = value["accounts"]
    if type(items) is not list or not 1 <= len(items) <= 50:
        raise InvestmentError("Monitoring requires 1 to 50 explicit account mappings")
    accounts = {a["id"]: a for a in ledger.spec["accounts"]}
    seen = set()
    for item in items:
        fields(item, ("account_id", "wrapper", "denominator", "provider", "max_age_seconds",
                      "last_reviewed_at", "review_interval_days", "evidence"))
        identifier = item["account_id"]
        text(identifier)
        if identifier not in accounts or identifier in seen:
            raise InvestmentError("Unknown or duplicate monitoring account")
        seen.add(identifier)
        if (item["wrapper"] not in ("LISA", "other", "unknown")
                or item["denominator"] is not None and type(item["denominator"]) is not bool):
            raise InvestmentError("Invalid wrapper or denominator classification")
        if item["denominator"] is True and (
                item["wrapper"] != "other" or accounts[identifier]["kind"] != "broker"):
            raise InvestmentError("Savings, LISA and unknown wrappers cannot enter the denominator")
        for key, maximum in (("max_age_seconds", 365 * 86400), ("review_interval_days", 3660)):
            if type(item[key]) is not int or not 1 <= item[key] <= maximum:
                raise InvestmentError("Monitoring age and review intervals must be explicit and bounded")
        if (item["last_reviewed_at"] is not None and
                explicit_clock(item["last_reviewed_at"]) > explicit_clock(value["observed_at"])):
            raise InvestmentError("Review date follows profile observation")
        text(item["provider"])
        text(item["evidence"])
    if seen != set(accounts):
        raise InvestmentError("Every included journal account needs a monitoring classification")
    threshold = value["concentration_threshold_pct"]
    if threshold is not None and not ZERO < number(threshold) <= 100:
        raise InvestmentError("Concentration review threshold must be in (0,100]")
    return value


def validate_observation(value, now):
    required = ("id", "account_id", "provider", "observed_at", "available_at",
                "currency", "cash", "holdings", "coverage", "evidence")
    optional = tuple(key for key in ("native_summary", "product_mappings", "mapping_available_at")
                     if isinstance(value, dict) and key in value)
    fields(value, required + optional)
    for key in ("id", "account_id", "provider", "evidence"):
        text(value[key])
    observed = explicit_clock(value["observed_at"]) if value["observed_at"] is not None else None
    available = explicit_clock(value["available_at"])
    if observed is not None and observed > available or available > now:
        raise InvestmentError("Observation clocks must satisfy as-of <= available <= now")
    if value["currency"] not in ("GBP", "USD", "EUR"):
        raise InvestmentError("Unsupported observation currency; no implicit FX")
    if value["coverage"] not in ("complete", "partial", "unknown"):
        raise InvestmentError("Explicit observation coverage required")
    if value["cash"] is not None:
        number(value["cash"])
    if "product_mappings" in value:
        mappings = value["product_mappings"]
        if type(mappings) is not list or len(mappings) > 100:
            raise InvestmentError("Bounded explicit observation product mappings required")
        sources, targets = set(), set()
        for mapping in mappings:
            fields(mapping, ("source_product", "product_id", "evidence"))
            for entry in mapping.values():
                text(entry)
            if mapping["source_product"] in sources or mapping["product_id"] in targets:
                raise InvestmentError("Duplicate or ambiguous observation product mapping")
            sources.add(mapping["source_product"])
            targets.add(mapping["product_id"])
        if mappings and "mapping_available_at" not in value:
            raise InvestmentError("Product mappings require an explicit availability clock")
    if "mapping_available_at" in value and explicit_clock(value["mapping_available_at"]) > available:
        raise InvestmentError("Mapping evidence cannot follow batch availability")
    if type(value["holdings"]) is not list or len(value["holdings"]) > 100:
        raise InvestmentError("At most 100 observed holdings")
    seen = set()
    for holding in value["holdings"]:
        fields(holding, ("source_product", "quantity"))
        text(holding["source_product"])
        number(holding["quantity"])
        if holding["source_product"] in seen:
            raise InvestmentError("Duplicate observed instrument")
        seen.add(holding["source_product"])
    if "native_summary" in value:
        summary = value["native_summary"]
        fields(summary, ("credit_usd", "position_count"))
        credit = summary["credit_usd"]
        if (not isinstance(credit, str) or not re.fullmatch(r"-?(?:0|[1-9][0-9]{0,18})(?:\.[0-9]{1,18})?", credit)
                or abs(Decimal(credit)) > Decimal("1e18")):
            raise InvestmentError("Invalid bounded native eToro credit")
        if (value["provider"] != "etoro" or value["currency"] != "USD"
                or type(summary["position_count"]) is not int or not 0 <= summary["position_count"] <= 1000):
            raise InvestmentError("Invalid native eToro summary")
    return value


def _compare_observation(ledger, profile, mapping, observation, now):
    from .reconciliation import compare_observations

    if observation["observed_at"] is None:
        return None
    identifier = mapping["account_id"]
    account = next(a for a in ledger.spec["accounts"] if a["id"] == identifier)
    mapping_at = max(explicit_clock(profile["observed_at"]),
                     explicit_clock(observation.get("mapping_available_at", profile["observed_at"])))
    staged = {
        "schema_version": 1, "portfolio_id": ledger.spec["portfolio_id"], "currency": "GBP",
        "source": observation["provider"], "evidence": observation["evidence"],
        "mapping_observed_at": iso(mapping_at),
        "account_mappings": [{"source_account": identifier, "account_id": identifier,
                              "account_binding": account["binding"], "evidence": mapping["evidence"]}],
        "product_mappings": observation.get("product_mappings", []),
        "accounts": [{
            "source_account": identifier, "observed_at": observation["observed_at"],
            "available_at": observation["available_at"],
            "cash_coverage": observation["coverage"] if observation["cash"] is not None else "unknown",
            "holdings_coverage": observation["coverage"], "coverage_evidence": observation["evidence"],
            "cash_gbp": observation["cash"], "holdings": observation["holdings"],
        }],
    }
    try:
        return compare_observations(ledger, staged, mapping["max_age_seconds"], now)["accounts"][0]
    except InvestmentError:
        # A retained operator mapping can be structurally valid but refer to a
        # product outside this journal. Keep the report, not a successful match.
        return None


def project(ledger, profile, now, cache=None, source_mode="recorded"):
    now = explicit_clock(now)
    validate_profile(profile, ledger, now)
    findings = []

    def finding(rule, subject, message, evidence, calculation=None, status="review"):
        findings.append({
            "id": rule + ":" + subject, "rule": rule, "rule_version": 1,
            "subject": subject, "status": status, "severity": "warning",
            "explanation": message, "calculation": calculation,
            "evidence": evidence, "recommendation": "review" if status == "review" else "wait",
        })

    account_spec = {a["id"]: a for a in ledger.spec["accounts"]}
    mappings = {a["account_id"]: a for a in profile["accounts"]}
    equity, as_of = ledger.valuation()
    if equity is None:
        finding("valuation", "portfolio", "A complete common-clock GBP valuation is unavailable.",
                "Journal revision " + str(ledger.revision), status="unavailable")
    if not ledger.performance_valid:
        finding("performance", "portfolio", "The causal valuation/flow chain does not support returns.",
                "Recorded journal; no historical reconstruction", status="unavailable")
    account_rows = []
    for identifier, mapping in sorted(mappings.items()):
        evidence = mapping["evidence"]
        age_limit = timedelta(seconds=mapping["max_age_seconds"])
        freshness = "unavailable" if as_of is None else "stale" if now - as_of >= age_limit else "fresh"
        if freshness != "fresh":
            finding("freshness", identifier, "Portfolio valuation is missing or stale for this account's review cadence.",
                    evidence, iso(as_of) if as_of else None, "unavailable")
        if mapping["wrapper"] == "unknown" or mapping["denominator"] is None:
            finding("classification", identifier, "Wrapper or denominator membership needs explicit evidence.",
                    evidence, status="unavailable")
        reviewed = mapping["last_reviewed_at"]
        if reviewed is None or now - explicit_clock(reviewed) >= timedelta(days=mapping["review_interval_days"]):
            finding("review_due", identifier, "Review the account records, terms and mandate.",
                    evidence, reviewed)
        if account_spec[identifier]["kind"] == "deposit" and ledger.account_terms[identifier] is None:
            finding("savings_terms", identifier, "Savings terms are missing; no interest or funding rule is inferred.",
                    evidence, status="unavailable")
        if mapping["wrapper"] == "LISA":
            finding("lisa_evidence", identifier,
                    "Verify first-home timing, terms, personal contributions, credited bonuses and charges locally.",
                    evidence, status="unavailable")
        history = ledger.cash_observation_history.get(identifier, [])
        differences = [o for o in history if Decimal(o["difference_gbp"]) != 0]
        if differences:
            finding("cash_difference", identifier, "Unresolved recorded cash differences remain, even after later matches.",
                    differences[-1]["evidence"], str(len(differences)))
        account_rows.append({
            "account_id": identifier, "provider": mapping["provider"], "wrapper": mapping["wrapper"],
            "denominator": mapping["denominator"], "freshness": freshness,
            "cash_gbp": fmt(ledger.cash[identifier]),
        })
    with localcontext() as context:
        context.prec = 100
        eligible = ZERO
        dynamic = ZERO
        valid = all(m["denominator"] is not None and m["wrapper"] != "unknown" for m in mappings.values())
        product_values = {}
        for (account, product), quantity in sorted(ledger.holdings.items()):
            if quantity <= 0 or mappings[account]["denominator"] is not True:
                continue
            mark = ledger.marks.get((account, product))
            if (mark is None or mark[1] < max(ledger.last_time, ledger.role_last_time)
                    or mark[2] > mark[1] or mark[2] > now
                    or now - mark[1] >= timedelta(seconds=mappings[account]["max_age_seconds"])):
                valid = False
                continue
            unassigned = (ledger.role_holdings.get((account, product, "unassigned"), ZERO)
                          if ledger.role_active else quantity)
            if unassigned > 0:
                valid = False
            value = quantity * mark[0]
            eligible += value
            dynamic += ledger.role_holdings.get((account, product, "dynamic"), ZERO) * mark[0]
            product_values[product] = product_values.get(product, ZERO) + value
        percentage = dynamic / eligible * 100 if valid and eligible > 0 else None
        if percentage is None:
            finding("dynamic_denominator", "portfolio",
                    "Dynamic percentage unavailable: require positive, fresh, fully valued and role-assigned eligible holdings.",
                    "Savings, LISA and all cash excluded; illustrative 10-20% is not a limit.",
                    status="unavailable")
        threshold = profile["concentration_threshold_pct"]
        if valid and eligible > 0 and threshold is not None:
            for product, value in sorted(product_values.items()):
                share = value / eligible * 100
                if share >= number(threshold):
                    finding("concentration", product, "Instrument share meets the configured review threshold.",
                            "Eligible non-LISA investment holdings only; no look-through",
                            f"Share {share:.2f}% (rounded); review threshold {threshold}%")
    finding("cost_coverage", "portfolio", "Recorded fees are not a complete total-cost assessment.",
            "Explicit journal fill fees only; fund costs may already be reflected in prices.",
            fmt(ledger.fees), "unavailable")
    provider_rows = []
    attempts = cache["attempts"] if cache else []
    for identifier, mapping in sorted(mappings.items()):
        selected = [a for a in attempts if a["account_id"] == identifier and a["provider"] == mapping["provider"]]
        latest = selected[-1] if selected else None
        successes = [a for a in selected if a["status"] == "success"]
        success = successes[-1] if successes else None
        observation = success["observation"] if success else None
        observation_freshness = "unavailable"
        if observation and observation["observed_at"] is not None:
            observation_freshness = ("stale" if now - explicit_clock(observation["observed_at"]) >=
                                     timedelta(seconds=mapping["max_age_seconds"]) else "fresh")
        status = "blocked_unvalidated" if mapping["provider"] == "etoro" else "manual"
        if latest and latest["status"] == "failed":
            finding("collection_failed", identifier, "Latest collection failed; last successful evidence is not refreshed.",
                    latest["error_code"], latest["attempted_at"], "unavailable")
        if observation is None or observation_freshness != "fresh" or observation["coverage"] != "complete":
            finding("source_coverage", identifier, "Provider/manual observation is missing, stale or not complete.",
                    mapping["evidence"], status="unavailable")
        if observation and observation["currency"] != "GBP":
            finding("native_currency", identifier, "Native-currency evidence cannot establish a GBP value or return.",
                    observation["evidence"], observation["currency"], "unavailable")
        unavailable, differences = 0, []
        for previous in successes:
            retained = previous["observation"]
            if retained["currency"] != "GBP":
                continue
            compared = _compare_observation(ledger, profile, mapping, retained, now)
            if (compared is None or compared["comparison_unavailable_reason"]
                    or compared["cash"]["status"] == "unavailable" or compared["missing_recorded_products"]
                    or any(row["status"] in ("unavailable", "unmapped") for row in compared["holdings"])):
                unavailable += 1
            if compared and (compared["cash"]["status"] == "difference"
                             or any(row["status"] == "difference" for row in compared["holdings"])):
                differences.append((retained, compared["cash"]["difference"]))
        if unavailable:
            finding("comparison_unavailable", identifier,
                    "Retained comparisons need review: verify clocks, explicit product mappings and omitted holdings.",
                    mapping["evidence"], str(unavailable) + " unavailable comparisons", "unavailable")
        if differences:
            first, last = differences[0], differences[-1]
            finding("observation_difference", identifier,
                    "Retained staged observations differ; later matches do not resolve earlier differences. No repair was applied.",
                    first[0]["id"] + " / " + last[0]["id"],
                    f"{len(differences)} differences; first cash {first[1]}, latest cash {last[1]}")
        provider_rows.append({
            "account_id": identifier, "provider": mapping["provider"], "readiness": status,
            "last_attempt_at": latest["attempted_at"] if latest else None,
            "last_attempt_status": latest["status"] if latest else None,
            "last_success_at": success["attempted_at"] if success else None,
            "source_as_of": observation["observed_at"] if observation else None,
            "freshness": observation_freshness,
            "currency": observation["currency"] if observation else None,
            "coverage": observation["coverage"] if observation else "unknown",
            "native_summary": observation.get("native_summary") if observation else None,
        })
    return {
        "enabled": True, "schema_version": 1, "source_mode": source_mode,
        "portfolio_id": ledger.spec["portfolio_id"], "portfolio_revision": ledger.revision,
        "profile_hash": digest(canonical(profile)), "generated_at": iso(now),
        "cache_revision": cache["revision"] if cache else None,
        "status": "attention" if findings else "not_evaluated",
        "dynamic_pct": fmt(percentage), "eligible_holdings_gbp": fmt(eligible) if valid else None,
        "accounts": account_rows, "providers": provider_rows,
        "findings": sorted(findings, key=lambda f: (f["status"] != "review", f["id"])),
        "limitations": LIMITATIONS.copy(), "affects_trading": False, "accounting_writes": False,
    }


def monitoring_snapshot(database, profile_path, cache_path=None, now=None):
    ledger, now = read_with_clock(database, now)
    profile = load_json(profile_path)
    validate_profile(profile, ledger, now)
    cache = None
    if cache_path is not None:
        from .monitoring_store import read_cache
        cache = read_cache(cache_path, profile, now)
    return project(ledger, profile, now, cache)
