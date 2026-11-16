"""Human-targeted, funded suggestions only; no orders are executed."""

from datetime import timedelta
from decimal import Decimal, ROUND_FLOOR

from .core import ZERO, clock, fmt, iso, number


def review_schedule(ledger, now, allocation):
    created = clock(ledger.spec["created_at"])
    monthly_anchor = ledger.monthly_review_at or created
    annual_anchor = ledger.annual_review_at or created
    monthly = (now.year, now.month) > (monthly_anchor.year, monthly_anchor.month)
    if annual_anchor.year == 9999:
        annual = False
    else:
        try:
            anniversary = annual_anchor.replace(year=annual_anchor.year + 1)
        except ValueError:
            anniversary = annual_anchor.replace(year=annual_anchor.year + 1, day=28)
        annual = now >= anniversary
    drifts = [Decimal(a["drift_pp"]) for a in allocation if a["drift_pp"] is not None]
    return {"monthly_due": monthly, "annual_due": annual,
            "drift_due": any(drift.copy_abs() >= 5 for drift in drifts) if drifts else None}


def plan(ledger, now, equity, as_of, allocation):
    policy = ledger.spec["policy"]
    base = {"status": "blocked", "reason": "", "expires_at": None, "orders": [],
            "manual_transfers": [], "execution_enabled": False}
    if equity is None:
        return dict(base, reason="Incomplete common-clock valuation; missing marks")
    maximum = timedelta(seconds=policy["max_mark_age_seconds"])
    if as_of is None or now - as_of > maximum:
        return dict(base, reason="Stale marks; as-of values cannot authorize a proposal")
    expires = min(now + timedelta(seconds=policy["proposal_ttl_seconds"]), as_of + maximum)
    if expires <= now:
        return dict(base, reason="Marks expire now; fresh evidence required")
    schedule = review_schedule(ledger, now, allocation)
    monthly, annual, drift = (schedule["monthly_due"], schedule["annual_due"],
                              schedule["drift_due"])
    base.update(status="no_trade", expires_at=iso(expires),
                reason="Within human targets; no funded incremental purchase")
    if equity <= 0:
        return dict(base, reason="No funded portfolio capital")
    if not (monthly or annual or drift):
        return dict(base, reason="Monthly review not yet due; drift below 5 percentage points")
    # Preserve the human cash target across the included portfolio. External
    # deposits are valued but never treated as broker order funding.
    cash_target = equity * number(policy["targets"]["cash"]) / 100
    budget = max(ZERO, sum(ledger.cash.values(), ZERO) - cash_target)
    accounts = [a for a in ledger.spec["accounts"] if a["kind"] == "broker"]
    remaining_cash = ledger.cash.copy()
    orders = []
    deficits = {a["sleeve"]: max(ZERO, equity * number(a["target_pct"]) / 100 -
                               equity * Decimal(a["actual_pct"]) / 100)
                for a in allocation if a["sleeve"] != "cash"}
    if budget > 0 and any(remaining_cash[a["id"]] > 0 for a in accounts):
        missing = [sleeve for sleeve in ("equity", "bond")
                   if number(policy["targets"][sleeve]) > 0 and not any(
                       p["sleeve"] == sleeve for p in ledger.spec["products"])]
        if missing:
            return dict(base, status="blocked", expires_at=None,
                        reason="Missing product for targeted sleeve(s): " + ", ".join(missing) +
                        "; manual product selection required before any purchase proposal")
    stale_reference = False
    for sleeve in ("equity", "bond"):
        need = min(deficits[sleeve], budget)
        if need <= 0:
            continue
        for product in ledger.spec["products"]:
            if product["sleeve"] != sleeve:
                continue
            minimum = number(product["minimum_notional_gbp"])
            for account in accounts:
                funded = min(need, remaining_cash[account["id"]])
                # No reference is relevant when known cash/need cannot support
                # even the product minimum, regardless of its unit price.
                if funded <= 0 or funded < minimum:
                    continue
                mark = ledger.marks.get((account["id"], product["id"]))
                if mark is None or mark[2] > as_of or now - mark[1] >= maximum:
                    stale_reference = True
                    continue
                # References must share the valuation clock, not merely be recent.
                if mark[1] != as_of:
                    stale_reference = True
                    continue
                price = mark[0]
                increment = number(product["quantity_increment"])
                quantity = (funded / (price * increment)).to_integral_value(
                    rounding=ROUND_FLOOR) * increment
                notional = quantity * price
                if quantity <= 0 or notional < minimum:
                    continue
                orders.append({"account_id": account["id"], "product_id": product["id"],
                               "side": "buy", "quantity": fmt(quantity),
                               "estimated_notional_gbp": fmt(notional)})
                remaining_cash[account["id"]] -= notional
                budget -= notional
                need -= notional
                expires = min(expires, mark[1] + maximum)
    if annual or drift:
        products = {product["id"]: product for product in ledger.spec["products"]}
        excesses = {
            sleeve: max(ZERO, sum(
                quantity * ledger.marks[key][0]
                for key, quantity in ledger.holdings.items()
                if quantity > 0 and products[key[1]]["sleeve"] == sleeve
            ) - equity * number(policy["targets"][sleeve]) / 100)
            for sleeve in ("equity", "bond")
        }
        for (account_id, product_id), held in sorted(ledger.holdings.items()):
            product = products[product_id]
            excess = excesses[product["sleeve"]]
            if held <= 0 or excess <= 0:
                continue
            price = ledger.marks[(account_id, product_id)][0]
            increment = number(product["quantity_increment"])
            quantity = (min(held, excess / price) / increment).to_integral_value(
                rounding=ROUND_FLOOR) * increment
            notional = quantity * price
            if quantity <= 0 or notional < number(product["minimum_notional_gbp"]):
                continue
            orders.append({"account_id": account_id, "product_id": product_id,
                           "side": "sell", "quantity": fmt(quantity),
                           "estimated_notional_gbp": fmt(notional)})
            excesses[product["sleeve"]] -= notional
    manual = []
    if sum(ledger.cash[a["id"]] for a in ledger.spec["accounts"] if a["kind"] == "deposit"):
        manual.append("External bank deposits are not broker order funding. Record an actual "
                      "included-account transfer before proposing purchases with those funds.")
    base.update(manual_transfers=manual)
    if orders:
        base.update(status="review", orders=orders, expires_at=iso(expires),
                    reason=f"Human policy {policy['id']}: purchases use recorded broker cash only; "
                    "proposed sale proceeds do not fund purchases; "
                    "eligibility, ownership, settlement and venue costs unverified; "
                    "manual review of prices, fees and product suitability required")
        if stale_reference:
            base["reason"] += ("; partial-reference coverage: missing/stale/late-available "
                               "purchase references excluded; orders use supported references only")
    elif stale_reference and budget > 0 and any(deficits.values()):
        base.update(status="blocked", reason="Missing/stale or late-available common-clock "
                    "purchase reference marks",
                    expires_at=None)
    elif annual or drift:
        base["reason"] = ("Annual or >=5 percentage-point sleeve review due; no funded purchases. "
                          "No eligible sale meets the declared increment and minimum.")
    return base
