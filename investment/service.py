"""Stable report contract for CLI and dashboard; pure demo, read-only recorded mode."""

from decimal import Decimal, localcontext
from datetime import timedelta

from .core import ZERO, clock, fmt, iso, number, replay
from .planner import plan
from .store import canonical, digest, read


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
    "Monthly reviews use profile inception; review acknowledgements are deferred. "
    "Proposed sales do not fund purchases; actual settlement is not verified.",
]


def report(ledger, now, source_mode="recorded"):
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
        elif now - as_of > timedelta(seconds=ledger.spec["policy"]["max_mark_age_seconds"]):
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


def snapshot(database, now=None):
    now = clock(now)
    return report(read(database, now), now)


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
    result = report(ledger, now)
    return {"schema_version": 1, "portfolio_id": result["portfolio_id"],
            "revision": result["revision"], "policy_id": ledger.spec["policy"]["id"],
            "policy_hash": digest(canonical(ledger.spec["policy"])),
            "generated_at": result["generated_at"], "expires_at": result["proposal"]["expires_at"],
            "proposal": result["proposal"], "execution_enabled": False}
