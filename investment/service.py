"""Stable report contract for CLI and dashboard; pure demo, read-only recorded mode."""

from decimal import Decimal, localcontext
from datetime import timedelta

from . import InvestmentError
from .core import MAX_EVENTS, ZERO, clock, fmt, iso, number, replay
from .planner import plan, review_schedule
from .store import canonical, digest, read, read_with_clock


LIMITATIONS = [
    "Manual source evidence is not broker truth, custody verification or execution ownership.",
    "Sleeves are human declarations, not proof of unleveraged UK access. Eligibility, "
    "custody/ownership, settlement and venue costs remain unverified.",
    "GBP only; FX, other currencies, corrections and actions other than splits are unsupported.",
    "Only evidenced interest/dividend payments are recorded; no accrual, entitlement or "
    "payment-coverage claim is made.",
    "No opening-position cost basis or inception is invented; empty evidenced inception required.",
    "Actual fill fees reduce cash/equity and gain; no forecast fees or automatic income adjustments.",
    "MWR is unavailable; inflation, tax, research, broker features and automatic execution deferred.",
    "Proposals are expiring export-only suggestions. Fees/slippage for future trades are not "
    "estimated; a human must recheck funding, costs, product suitability and prices.",
    "Broker cash covers estimated purchase notionals only; unknown fees and settlement mean "
    "full funding/executability remains unverified. Scheduled contributions are not spendable cash.",
    "Review acknowledgements are manual records, not trade approvals. Five-point drift "
    "remains a trigger; proposed sales do not fund purchases or prove settlement.",
]


def report(ledger, now, source_mode="recorded"):
    if ledger.spec["schema_version"] == 2:
        return opening_report(ledger, now, source_mode)
    with localcontext() as context:
        context.prec = 100
        equity, as_of = ledger.valuation()
        products = {p["id"]: p for p in ledger.spec["products"]}
        holdings = []
        values = {"equity": ZERO, "bond": ZERO, "cash": sum(ledger.cash.values(), ZERO)}
        for (account, product_id), quantity in sorted(ledger.holdings.items()):
            if quantity == 0:
                continue
            product = products[product_id]
            mark = ledger.marks.get((account, product_id))
            value = quantity * mark[0] if mark else None
            if value is not None:
                values[product["sleeve"]] += value
            holdings.append({"account_id": account, "product_id": product_id,
                             "name": product["name"], "sleeve": product["sleeve"],
                             "quantity": fmt(quantity), "value_gbp": fmt(value)})
        allocation = []
        for sleeve in ("equity", "bond", "cash"):
            target = number(ledger.spec["policy"]["targets"][sleeve])
            actual = values[sleeve] / equity * 100 if equity is not None and equity > 0 else None
            allocation.append({"sleeve": sleeve, "target_pct": fmt(target),
                               "actual_pct": fmt(actual),
                               "drift_pp": fmt(actual - target) if actual is not None else None})
        warnings = []
        if equity is None:
            warnings.append("Incomplete valuation: all nonzero holdings need common-clock marks "
                            "at or after the latest event; equity/gain unavailable.")
        elif now - as_of >= timedelta(seconds=ledger.spec["policy"]["max_mark_age_seconds"]):
            warnings.append("Stale valuation shown explicitly as-of; proposal blocked.")
        performance = ledger.performance_valid and ledger.units > 0 and equity is not None
        if not performance:
            warnings.append("TWR/drawdown unavailable: no fully evidenced common-clock "
                            "valuation/flow chain from inception.")
        proposal = plan(ledger, now, equity, as_of, allocation)
        return {
            "schema_version": 1, "source_mode": source_mode,
            "portfolio_id": ledger.spec["portfolio_id"], "revision": ledger.revision,
            "generated_at": iso(now), "as_of": iso(as_of) if as_of else None,
            "currency": "GBP", "complete": equity is not None, "equity_gbp": fmt(equity),
            "net_contributions_gbp": fmt(ledger.net),
            "gain_gbp": fmt(equity - ledger.net) if equity is not None else None,
            "twr_pct": fmt((ledger.unit_price - 1) * 100) if performance else None,
            "max_drawdown_pct": fmt(-ledger.drawdown * 100) if performance else None,
            "accounts": [{"id": a["id"], "name": a["name"], "kind": a["kind"],
                          "cash_gbp": fmt(ledger.cash[a["id"]])} for a in ledger.spec["accounts"]],
            "holdings": holdings, "allocation": allocation, "proposal": proposal,
            "warnings": warnings, "limitations": LIMITATIONS.copy(),
        }


def opening_report(ledger, now, source_mode="recorded"):
    """Report version 2 opening evidence without inventing lifetime history."""
    with localcontext() as context:
        context.prec = 100
        equity, as_of = ledger.valuation()
        baseline = ledger.baseline_value
        gain = (equity - baseline - (ledger.net - ledger.baseline_net)
                if equity is not None and baseline is not None else None)
        products = {item["id"]: item for item in ledger.spec["products"]}
        holdings = []
        for (account, product_id), quantity in sorted(ledger.holdings.items()):
            if quantity <= 0:
                continue
            mark = ledger.marks.get((account, product_id))
            product = products[product_id]
            holdings.append({
                "account_id": account, "product_id": product_id,
                "name": product["name"], "sleeve": product["sleeve"],
                "quantity": fmt(quantity), "value_gbp": fmt(quantity * mark[0]) if mark else None,
                "mark_at": iso(mark[1]) if mark else None,
                "mark_available_at": iso(mark[2]) if mark else None,
            })
        performance = (ledger.performance_valid and ledger.units > 0
                       and equity is not None and ledger.baseline_at is not None)
        warnings = ([] if equity is not None else
                    ["A complete common-clock valuation is unavailable."])
        for account, observations in sorted(ledger.cash_observation_history.items()):
            discrepancies = [
                observation for observation in observations
                if Decimal(observation["difference_gbp"]) != 0
            ]
            if discrepancies:
                first, latest = discrepancies[0], discrepancies[-1]
                if first is latest:
                    warnings.append(
                        f"Recorded cash observation for account {account} differs by GBP "
                        f"{first['difference_gbp']} at {first['timestamp']} (source: "
                        f"{first['source']}; evidence: {first['evidence']}); no correction "
                        "was applied, and later matching observations do not resolve it.")
                else:
                    warnings.append(
                        f"{len(discrepancies)} recorded cash observations for account {account} "
                        f"differ from recorded balances. Earliest: GBP {first['difference_gbp']} "
                        f"at {first['timestamp']} (source: {first['source']}; evidence: "
                        f"{first['evidence']}). Latest: GBP {latest['difference_gbp']} at "
                        f"{latest['timestamp']} (source: {latest['source']}; evidence: "
                        f"{latest['evidence']}); no correction was applied, and later matching "
                        "observations do not resolve prior differences.")
        return {
            "schema_version": 2, "source_mode": source_mode,
            "portfolio_id": ledger.spec["portfolio_id"], "revision": ledger.revision,
            "generated_at": iso(now), "as_of": iso(as_of) if as_of else None,
            "baseline_as_of": iso(ledger.baseline_at) if ledger.baseline_at else None,
            "currency": "GBP", "complete": equity is not None,
            "equity_gbp": fmt(equity), "net_contributions_gbp": fmt(ledger.net),
            "since_baseline_gain_gbp": fmt(gain), "lifetime_gain_gbp": None,
            "twr_pct": fmt((ledger.unit_price - 1) * 100) if performance else None,
            "max_drawdown_pct": fmt(-ledger.drawdown * 100) if performance else None,
            "accounts": [{"id": a["id"], "name": a["name"], "kind": a["kind"],
                          "cash_gbp": fmt(ledger.cash[a["id"]]) if ledger.opened else None,
                          "terms": ledger.account_terms[a["id"]],
                          "cash_observation": ledger.cash_observations.get(a["id"])}
                         for a in ledger.spec["accounts"]],
            "valuation_history": ledger.valuation_history,
            "holdings": holdings,
            "proposal": {"status": "blocked", "reason": "Allocation and role policy are not implemented",
                         "expires_at": None, "orders": [], "manual_transfers": [],
                         "execution_enabled": False},
            "warnings": warnings,
            "limitations": [
                "Opening holdings and cash are operator-recorded evidence, not broker verification.",
                "Lifetime cost basis and pre-baseline gain are unavailable.",
                "Returns are since the first complete, contemporaneously evidenced baseline.",
                "No valuation freshness threshold is configured; check the recorded dates.",
                "Cash observations are evidence only; discrepancies do not update recorded balances.",
                "Allocation policy, proposals and execution are not implemented for this store.",
            ],
        }


def snapshot(database, now=None):
    now = clock(now)
    return report(read(database, now), now)


def review_status(database, now=None):
    ledger, now = read_with_clock(database, now)
    if ledger.spec["schema_version"] == 2:
        raise InvestmentError("Review status requires an implemented allocation policy")
    result = report(ledger, now)
    schedule = review_schedule(ledger, now, result["allocation"])
    valuation_status = ledger.valuation_status(now)
    if valuation_status != "fresh":
        schedule["drift_due"] = None
    blocked = None
    if valuation_status != "fresh":
        blocked = "A complete, fresh recorded valuation is required."
    elif ledger.revision >= MAX_EVENTS:
        blocked = "Investment event count has reached its limit."
    return {
        "schema_version": 1, "portfolio_id": result["portfolio_id"],
        "revision": ledger.revision, "generated_at": iso(now),
        "valuation_as_of": result["as_of"], "valuation_status": valuation_status,
        "last_monthly_review_at": iso(ledger.monthly_review_at) if ledger.monthly_review_at else None,
        "last_annual_review_at": iso(ledger.annual_review_at) if ledger.annual_review_at else None,
        **schedule, "acknowledgement_available": blocked is None,
        "acknowledgement_blocked_reason": blocked,
    }


def demo_profile(now):
    return {
        "schema_version": 1, "portfolio_id": "synthetic-demo", "currency": "GBP",
        "created_at": iso(now),
        "accounts": [
            {"id": "broker", "name": "Synthetic broker", "kind": "broker",
             "binding": "synthetic-broker", "currency": "GBP"},
            {"id": "savings", "name": "Synthetic external bank deposit", "kind": "deposit",
             "binding": "synthetic-deposit", "currency": "GBP"}],
        "products": [{"id": "equity", "name": "Synthetic GBP equity fund", "sleeve": "equity",
                      "currency": "GBP", "quantity_increment": "1",
                      "minimum_notional_gbp": "10"}],
        "policy": {"id": "human-demo-80-20", "targets": {"equity": "80", "bond": "0", "cash": "20"},
                   "max_mark_age_seconds": 86400, "proposal_ttl_seconds": 3600,
                   "monthly_contribution_gbp": "100"},
    }


def demo_events(now, contribution=False):
    common = {"timestamp": iso(now), "available_at": iso(now), "currency": "GBP",
              "evidence": "synthetic demonstration only"}
    values = [
        dict(common, id="broker-funding", type="contribution", account_id="broker", amount_gbp="800"),
        dict(common, id="deposit-funding", type="contribution", account_id="savings", amount_gbp="200"),
        dict(common, id="reference", type="mark", account_id="broker", product_id="equity",
             price_gbp="10"),
        dict(common, id="buy", type="buy", account_id="broker", product_id="equity",
             quantity="80", price_gbp="10", fee_gbp="0"),
    ]
    if contribution:
        values.append(dict(common, id="optional-100", type="contribution", account_id="broker",
                           amount_gbp="100"))
    return values


def demo_snapshot(now=None):
    now = clock(now)
    return report(replay(demo_profile(now), demo_events(now), now), now, "demo")


def proposal_export(database, now=None):
    """Binding envelope; never an executable instruction."""
    now = clock(now)
    ledger = read(database, now)
    if ledger.spec["schema_version"] == 2:
        raise InvestmentError("Proposal export requires an implemented allocation policy")
    result = report(ledger, now)
    return {"schema_version": 1, "portfolio_id": result["portfolio_id"],
            "revision": result["revision"], "policy_id": ledger.spec["policy"]["id"],
            "policy_hash": digest(canonical(ledger.spec["policy"])),
            "generated_at": result["generated_at"], "expires_at": result["proposal"]["expires_at"],
            "proposal": result["proposal"], "execution_enabled": False}
