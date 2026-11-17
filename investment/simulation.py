"""Synthetic-only daily portfolio mechanics; not a product research runner."""

from decimal import Decimal, ROUND_DOWN, localcontext

from . import InvestmentError
from .core import explicit_clock, fields, fmt, iso, number, text

MAX_PRODUCTS = 25
MAX_SESSIONS = 10000
MAX_BARS = 250000
MAX_CASH_FLOWS = 10000
MAX_ACTIONS = 10000
MAX_VALUE = Decimal("1e24")

LIMITATIONS = [
    "Callers must supply synthetic fixtures; input provenance is not verified. Output is not product-performance evidence or an investment recommendation.",
    "All products must have synchronized GBP open/close observations; calendars and source rights are not verified.",
    "Only positive contributions, cash-funded withdrawals, and buy-only fixed-target allocation are modeled; no sales or withdrawal-funded liquidation.",
    "Only synthetic splits and distributions at modeled opens and cash rates at modeled opens/closes are supported; cash accrual uses declared simple ACT/365 assumptions, not verified account terms.",
    "Distributions require unadjusted synthetic prices, accrue from modeled ex-open holdings, and pay at the supplied later close; price basis, taxes, FX, inflation, fixed fees, spreads and slippage are not verified.",
    "The proportional variable-cost input is an assumption, not a validated execution-cost estimate.",
    "No CLI, campaign dataset, broker, account, or live investment store is connected.",
]


def _bounded(value, positive=False):
    result = number(value, positive=positive)
    if result > MAX_VALUE:
        raise InvestmentError("Simulation value exceeds its bound")
    return result


def _portfolio_value(cash, holdings, prices, receivables):
    total = cash
    for product_id, quantity in holdings.items():
        total += quantity * prices[product_id]
    total += sum(receivables.values(), Decimal(0))
    if total < 0 or total > MAX_VALUE:
        raise InvestmentError("Simulated portfolio value exceeds its bound")
    return total


def _products(values, targets):
    if not isinstance(values, list) or not 1 <= len(values) <= MAX_PRODUCTS:
        raise InvestmentError(f"Between 1 and {MAX_PRODUCTS} synthetic products are required")
    if not isinstance(targets, dict):
        raise InvestmentError("A product target mapping is required")

    products = {}
    for item in values:
        fields(item, ("id", "quantity_increment", "minimum_notional_gbp"))
        product_id = text(item["id"])
        if product_id in products:
            raise InvestmentError("Duplicate synthetic product")
        increment = _bounded(item["quantity_increment"], positive=True)
        minimum = _bounded(item["minimum_notional_gbp"])
        products[product_id] = {
            "increment": increment,
            "minimum": minimum,
        }
    if set(targets) != set(products):
        raise InvestmentError("Targets must name every synthetic product exactly once")
    for product_id, product in products.items():
        weight = _bounded(targets[product_id])
        if weight > 100:
            raise InvestmentError("Product target must not exceed 100 percent")
        product["weight"] = weight
    if not 0 < sum((item["weight"] for item in products.values()), Decimal(0)) <= 100:
        raise InvestmentError("Product targets must total more than zero and at most 100 percent")
    return products


def _sessions(rows, products):
    if not isinstance(rows, list) or not rows or len(rows) > MAX_BARS:
        raise InvestmentError("A bounded, nonempty synthetic bar list is required")
    grouped = {}
    seen = set()
    required = ("product_id", "session_id", "open_at", "open_available_at", "open_gbp",
                "close_at", "close_available_at", "close_gbp")
    for row in rows:
        fields(row, required)
        product_id = text(row["product_id"])
        session_id = text(row["session_id"])
        if product_id not in products:
            raise InvestmentError("Bar references an unconfigured product")
        key = (session_id, product_id)
        if key in seen:
            raise InvestmentError("Duplicate product/session bar")
        seen.add(key)
        open_at = explicit_clock(row["open_at"])
        open_available_at = explicit_clock(row["open_available_at"])
        close_at = explicit_clock(row["close_at"])
        close_available_at = explicit_clock(row["close_available_at"])
        if (open_at >= close_at or open_available_at != open_at
                or close_available_at != close_at):
            raise InvestmentError("Synthetic prices must be available at their exact observation clocks")
        session = grouped.setdefault(session_id, {
            "open_at": open_at, "close_at": close_at, "bars": {},
        })
        if session["open_at"] != open_at or session["close_at"] != close_at:
            raise InvestmentError("All products must share each session's open and close clocks")
        session["bars"][product_id] = {
            "open": _bounded(row["open_gbp"], positive=True),
            "close": _bounded(row["close_gbp"], positive=True),
        }

    if len(grouped) > MAX_SESSIONS:
        raise InvestmentError(f"At most {MAX_SESSIONS} synthetic sessions are supported")
    ordered = sorted(grouped.items(), key=lambda item: item[1]["open_at"])
    previous_close = None
    for _, session in ordered:
        if set(session["bars"]) != set(products):
            raise InvestmentError("Every product needs a bar for every synchronized session")
        if previous_close is not None and session["open_at"] <= previous_close:
            raise InvestmentError("Sessions must be strictly ordered and nonoverlapping")
        previous_close = session["close_at"]
    return ordered


def _cash_flows(contributions, withdrawals, close_times):
    if (not isinstance(contributions, (list, tuple))
            or not isinstance(withdrawals, (list, tuple))
            or len(contributions) + len(withdrawals) > MAX_CASH_FLOWS):
        raise InvestmentError(f"At most {MAX_CASH_FLOWS} synthetic cash flows are supported")
    required = ("id", "timestamp", "available_at", "amount_gbp")
    parsed = {}
    identifiers = set()
    for flow_name, values in (("contribution", contributions), ("withdrawal", withdrawals)):
        previous_key = None
        for row in values:
            fields(row, required)
            identifier = text(row["id"])
            timestamp = explicit_clock(row["timestamp"])
            available_at = explicit_clock(row["available_at"])
            amount = _bounded(row["amount_gbp"], positive=True)
            key = (timestamp, identifier)
            if identifier in identifiers:
                raise InvestmentError("Duplicate synthetic cash-flow ID")
            if previous_key is not None and key <= previous_key:
                raise InvestmentError(
                    f"Synthetic {flow_name}s must be ordered by timestamp and ID")
            if timestamp not in close_times or available_at > timestamp:
                raise InvestmentError(
                    f"Synthetic {flow_name} must be available by a modeled close clock")
            identifiers.add(identifier)
            flow = parsed.setdefault(timestamp, {
                "contributions": Decimal(0),
                "withdrawals": Decimal(0),
            })
            flow[flow_name + "s"] += amount
            previous_key = key
    return parsed


def _splits(values, products, open_times):
    if not isinstance(values, (list, tuple)) or len(values) > MAX_ACTIONS:
        raise InvestmentError(f"At most {MAX_ACTIONS} synthetic split actions are supported")
    required = ("id", "product_id", "effective_at", "available_at", "numerator", "denominator")
    parsed = {}
    identifiers = set()
    previous_key = None
    product_clocks = set()
    for row in values:
        fields(row, required)
        identifier = text(row["id"])
        product_id = text(row["product_id"])
        if product_id not in products:
            raise InvestmentError("Split references an unconfigured product")
        effective_at = explicit_clock(row["effective_at"])
        available_at = explicit_clock(row["available_at"])
        numerator = _bounded(row["numerator"], positive=True)
        denominator = _bounded(row["denominator"], positive=True)
        key = (effective_at, product_id, identifier)
        if identifier in identifiers:
            raise InvestmentError("Duplicate synthetic action ID")
        if previous_key is not None and key <= previous_key:
            raise InvestmentError("Synthetic split actions must be in stable event-time order")
        if (effective_at not in open_times or available_at > effective_at
                or (effective_at, product_id) in product_clocks):
            raise InvestmentError("Split must be known by a unique modeled session open")
        identifiers.add(identifier)
        product_clocks.add((effective_at, product_id))
        parsed.setdefault(effective_at, []).append({
            "id": identifier,
            "product_id": product_id,
            "numerator": numerator,
            "denominator": denominator,
        })
        previous_key = key
    return parsed


def _distributions(values, products, open_times, close_times, split_events):
    if not isinstance(values, (list, tuple)) or len(values) > MAX_ACTIONS:
        raise InvestmentError(f"At most {MAX_ACTIONS} synthetic distributions are supported")
    required = ("id", "product_id", "ex_at", "pay_at", "available_at",
                "amount_per_unit_gbp")
    ex_events = {}
    pay_events = {}
    records = {}
    identifiers = set()
    previous_key = None
    product_ex_clocks = set()
    split_clocks = {
        (effective_at, action["product_id"])
        for effective_at, actions in split_events.items()
        for action in actions
    }
    for row in values:
        fields(row, required)
        identifier = text(row["id"])
        product_id = text(row["product_id"])
        if product_id not in products:
            raise InvestmentError("Distribution references an unconfigured product")
        ex_at = explicit_clock(row["ex_at"])
        pay_at = explicit_clock(row["pay_at"])
        available_at = explicit_clock(row["available_at"])
        amount_per_unit = _bounded(row["amount_per_unit_gbp"], positive=True)
        key = (ex_at, product_id, identifier)
        if identifier in identifiers:
            raise InvestmentError("Duplicate synthetic distribution ID")
        if previous_key is not None and key <= previous_key:
            raise InvestmentError("Synthetic distributions must be in stable ex-time order")
        if (ex_at not in open_times or pay_at not in close_times or pay_at <= ex_at
                or available_at > ex_at
                or (ex_at, product_id) in product_ex_clocks
                or (ex_at, product_id) in split_clocks):
            raise InvestmentError(
                "Distribution must be known by a unique modeled ex-open and paid at a later modeled close")
        identifiers.add(identifier)
        product_ex_clocks.add((ex_at, product_id))
        event = {
            "id": identifier,
            "product_id": product_id,
            "ex_at": ex_at,
            "pay_at": pay_at,
            "available_at": available_at,
            "amount_per_unit_gbp": amount_per_unit,
        }
        ex_events.setdefault(ex_at, []).append(event)
        pay_events.setdefault(pay_at, []).append(identifier)
        records[identifier] = event.copy()
        previous_key = key
    return ex_events, pay_events, records


def _cash_rates(values, open_times, close_times):
    if not isinstance(values, (list, tuple)) or not values or len(values) > MAX_ACTIONS:
        raise InvestmentError(f"Between 1 and {MAX_ACTIONS} synthetic cash rates are required")
    required = ("id", "effective_at", "available_at", "annual_rate")
    parsed = {}
    identifiers = set()
    previous_key = None
    valid_clocks = open_times | close_times
    for row in values:
        fields(row, required)
        identifier = text(row["id"])
        effective_at = explicit_clock(row["effective_at"])
        available_at = explicit_clock(row["available_at"])
        annual_rate = _bounded(row["annual_rate"])
        key = (effective_at, identifier)
        if identifier in identifiers:
            raise InvestmentError("Duplicate synthetic cash-rate ID")
        if previous_key is not None and key <= previous_key:
            raise InvestmentError("Synthetic cash rates must be in stable event-time order")
        if effective_at not in valid_clocks or available_at > effective_at or annual_rate > 1:
            raise InvestmentError("Cash rate must be known by a modeled clock and at most 100%")
        if effective_at in parsed:
            raise InvestmentError("Only one synthetic cash rate may change at a modeled clock")
        identifiers.add(identifier)
        parsed[effective_at] = {
            "id": identifier,
            "available_at": available_at,
            "annual_rate": annual_rate,
        }
        previous_key = key
    if not open_times or min(open_times) not in parsed:
        raise InvestmentError("An explicit synthetic cash rate is required at the first open")
    return parsed


def _cash_interest(cash, start, end, annual_rate):
    if end < start:
        raise InvestmentError("Cash accrual clocks must be ordered")
    delta = end - start
    elapsed_microseconds = ((delta.days * 86400 + delta.seconds) * 1_000_000
                            + delta.microseconds)
    year_microseconds = 365 * 86400 * 1_000_000
    with localcontext() as context:
        context.prec = 100
        return cash * annual_rate * Decimal(elapsed_microseconds) / Decimal(year_microseconds)


def _apply_split(action, products, holdings):
    product_id = action["product_id"]
    quantity = holdings.get(product_id, Decimal(0))
    if quantity <= 0:
        raise InvestmentError("Synthetic split requires an evidenced positive holding")
    with localcontext() as context:
        context.prec = 100
        adjusted = quantity * action["numerator"] / action["denominator"]
        if adjusted > MAX_VALUE or adjusted % products[product_id]["increment"]:
            raise InvestmentError("Split result exceeds bounds or declared quantity increment")
        holdings[product_id] = adjusted


def _buy_at_open(product_id, budget, price, product, fee_rate, cash, holdings):
    if budget <= 0:
        return cash, None, "no_allocation_budget"
    if price <= 0:
        raise InvestmentError("Synthetic execution price must be positive")
    increment = product["increment"]
    quantity_steps = (budget / (price * (Decimal(1) + fee_rate) * increment)
                      ).to_integral_value(rounding=ROUND_DOWN)
    quantity = quantity_steps * increment
    if quantity <= 0:
        return cash, None, "below_quantity_increment"
    notional = quantity * price
    fee = notional * fee_rate
    debit = notional + fee
    if notional < product["minimum"]:
        return cash, None, "below_minimum_notional"
    if debit > budget or debit > cash:
        raise InvestmentError("Rounded synthetic fill exceeded its allocated cash")
    remaining = quantity % increment
    if remaining or quantity > MAX_VALUE:
        raise InvestmentError("Synthetic fill quantity violates its exact bound")
    if holdings.get(product_id, Decimal(0)) + quantity > MAX_VALUE:
        raise InvestmentError("Simulated holding quantity exceeds its bound")
    cash -= debit
    holdings[product_id] = holdings.get(product_id, Decimal(0)) + quantity
    return cash, {
        "product_id": product_id,
        "quantity": fmt(quantity),
        "price_gbp": fmt(price),
        "notional_gbp": fmt(notional),
        "fee_gbp": fmt(fee),
    }, None


def _allocation_budgets(products, holdings, prices, cash, flow, receivables):
    if flow <= 0 or cash <= 0:
        return {}
    equity = _portfolio_value(cash, holdings, prices, receivables)
    deficits = {}
    for product_id, product in products.items():
        target_value = equity * product["weight"] / Decimal(100)
        current_value = holdings.get(product_id, Decimal(0)) * prices[product_id]
        deficit = max(target_value - current_value, Decimal(0))
        if deficit:
            deficits[product_id] = deficit
    total_deficit = sum(deficits.values(), Decimal(0))
    budget = min(cash, total_deficit)
    if budget <= 0:
        return {}
    result = {}
    distributed = Decimal(0)
    eligible = sorted(deficits)
    with localcontext() as context:
        context.prec = 100
        for product_id in eligible[:-1]:
            amount = budget * deficits[product_id] / total_deficit
            result[product_id] = amount
            distributed += amount
        result[eligible[-1]] = budget - distributed
    return result


def simulate_synthetic_fixture(initial_cash_gbp, products, targets, bars,
                               contributions=(), variable_cost_bps="0", splits=(),
                               cash_rates=(), withdrawals=(), distributions=()):
    """Run a bounded synthetic buy-only portfolio clock for engineering tests.

    Close-time net contributions create allocation budgets for the next common
    session's open. Withdrawals use only available cash; sales are unsupported.
    Synthetic distributions accrue at a modeled ex-open and pay at a later close.
    Cash interest uses synthetic rates and simple ACT/365 accrual between
    modeled open/close clocks. This function loads no external data; callers
    must provide synthetic fixtures, whose provenance it cannot independently
    verify.
    """
    initial_cash = _bounded(initial_cash_gbp, positive=True)
    fee_bps = _bounded(variable_cost_bps)
    if fee_bps > 10000:
        raise InvestmentError("Variable cost must be between zero and 10000 basis points")
    fee_rate = fee_bps / Decimal(10000)
    product_state = _products(products, targets)
    sessions = _sessions(bars, product_state)
    close_times = {session["close_at"] for _, session in sessions}
    open_times = {session["open_at"] for _, session in sessions}
    external_flows = _cash_flows(contributions, withdrawals, close_times)
    split_events = _splits(splits, product_state, open_times)
    distribution_events, distribution_payments, distribution_records = _distributions(
        distributions, product_state, open_times, close_times, split_events)
    rate_events = _cash_rates(cash_rates, open_times, close_times)

    cash = Decimal(0)
    holdings = {}
    prices = {}
    receivables = {}
    units = Decimal(0)
    unit_value = Decimal(1)
    peak = Decimal(1)
    worst_drawdown = Decimal(0)
    total_contributions = Decimal(0)
    total_withdrawals = Decimal(0)
    total_distributions = Decimal(0)
    total_distributions_paid = Decimal(0)
    total_fees = Decimal(0)
    total_cash_interest = Decimal(0)
    pending = {}
    curve = []
    fill_events = []
    unfilled_allocations = []
    applied_split_ids = []
    active_rate = None
    previous_close = None

    with localcontext() as context:
        context.prec = 100
        for index, (session_id, session) in enumerate(sessions):
            session_bars = session["bars"]
            open_rate_event = rate_events.get(session["open_at"])
            if previous_close is not None:
                if active_rate is None:
                    raise InvestmentError("A synthetic cash rate is required for every accrual interval")
                interest = _cash_interest(
                    cash, previous_close, session["open_at"], active_rate["annual_rate"])
                cash += interest
                total_cash_interest += interest
            if open_rate_event is not None:
                active_rate = open_rate_event

            applied_splits = split_events.get(session["open_at"], [])
            for action in applied_splits:
                _apply_split(action, product_state, holdings)
                applied_split_ids.append(action["id"])
            accrued_distributions = []
            for event in distribution_events.get(session["open_at"], []):
                quantity = holdings.get(event["product_id"], Decimal(0))
                if quantity <= 0:
                    raise InvestmentError(
                        "Synthetic distribution requires a positive ex-time holding")
                amount = quantity * event["amount_per_unit_gbp"]
                if amount <= 0 or amount > MAX_VALUE:
                    raise InvestmentError("Synthetic distribution amount exceeds its bound")
                receivables[event["id"]] = amount
                distribution_records[event["id"]]["entitlement_quantity"] = quantity
                distribution_records[event["id"]]["amount_gbp"] = amount
                total_distributions += amount
                accrued_distributions.append(event["id"])
            prices = {product_id: session_bars[product_id]["open"]
                      for product_id in product_state}
            fills = []
            unfilled = []
            if pending:
                for product_id in sorted(pending):
                    budget = pending[product_id]
                    cash, fill, reason = _buy_at_open(
                        product_id, budget, prices[product_id],
                        product_state[product_id], fee_rate, cash, holdings)
                    if fill is not None:
                        fill["execution_at"] = iso(session["open_at"])
                        fills.append(fill)
                        total_fees += Decimal(fill["fee_gbp"])
                    else:
                        unfilled.append({
                            "product_id": product_id,
                            "budget_gbp": fmt(budget),
                            "reason": reason,
                        })
                pending = {}

            if active_rate is None:
                raise InvestmentError("An explicit synthetic cash rate is required at the first open")
            interest = _cash_interest(
                cash, session["open_at"], session["close_at"], active_rate["annual_rate"])
            cash += interest
            total_cash_interest += interest

            prices = {product_id: session_bars[product_id]["close"]
                      for product_id in product_state}
            paid_distributions = []
            for identifier in distribution_payments.get(session["close_at"], []):
                amount = receivables.pop(identifier, None)
                if amount is None:
                    raise InvestmentError("Synthetic distribution payment has no accrued entitlement")
                cash += amount
                total_distributions_paid += amount
                paid_distributions.append(identifier)
            before_flow = _portfolio_value(cash, holdings, prices, receivables)
            if units:
                unit_value = before_flow / units
                peak = max(peak, unit_value)
                worst_drawdown = min(worst_drawdown, unit_value / peak - 1)

            flows = external_flows.get(session["close_at"], {})
            contribution = flows.get("contributions", Decimal(0))
            withdrawal = flows.get("withdrawals", Decimal(0))
            if index == 0:
                contribution += initial_cash
            flow = contribution - withdrawal
            total_contributions += contribution
            total_withdrawals += withdrawal
            if flow:
                if cash + flow < 0:
                    raise InvestmentError(
                        "Synthetic withdrawal exceeds available cash; sales are unsupported")
                units += flow / unit_value
                if units < 0:
                    raise InvestmentError("Synthetic withdrawal exceeds invested unit value")
                cash += flow
            if flow > 0:
                pending = _allocation_budgets(
                    product_state, holdings, prices, cash, flow, receivables)
            elif flow < 0:
                pending = {}

            close_rate_event = rate_events.get(session["close_at"])
            if close_rate_event is not None:
                active_rate = close_rate_event
            previous_close = session["close_at"]

            equity = _portfolio_value(cash, holdings, prices, receivables)
            if units:
                unit_value = equity / units
            if unit_value > peak:
                peak = unit_value
            worst_drawdown = min(worst_drawdown, unit_value / peak - 1)
            fill_events.extend(fills)
            unfilled_allocations.extend(unfilled)
            curve.append({
                "session_id": session_id,
                "observation_at": iso(session["close_at"]),
                "known_at": iso(session["close_at"]),
                "equity_gbp": fmt(equity),
                "cash_gbp": fmt(cash),
                "distribution_receivables_gbp": fmt(
                    sum(receivables.values(), Decimal(0))),
                "distributions_gbp": fmt(total_distributions),
                "distributions_paid_gbp": fmt(total_distributions_paid),
                "total_contributions_gbp": fmt(total_contributions),
                "total_withdrawals_gbp": fmt(total_withdrawals),
                "net_contributions_gbp": fmt(total_contributions - total_withdrawals),
                "unit_value": fmt(unit_value),
                "twr_pct": fmt((unit_value - 1) * 100),
                "drawdown_pct": fmt(-worst_drawdown * 100),
                "fees_gbp": fmt(total_fees),
                "cash_interest_gbp": fmt(total_cash_interest),
                "fills": fills,
                "splits": [action["id"] for action in applied_splits],
                "distributions": accrued_distributions,
                "distribution_payments": paid_distributions,
                "unfilled_allocations": unfilled,
            })

    with localcontext() as context:
        context.prec = 100
        ending_equity = _portfolio_value(cash, holdings, prices, receivables)
        net_contributions = total_contributions - total_withdrawals
        total_gain = ending_equity - net_contributions
        total_return_pct = (unit_value - 1) * 100
        max_drawdown_pct = -worst_drawdown * 100
    return {
        "schema_version": 1,
        "scope": "synthetic_portfolio_mechanics_only",
        "synthetic_only": True,
        "supports_product_performance_claims": False,
        "currency": "GBP",
        "curve": curve,
        "summary": {
            "ending_equity_gbp": fmt(ending_equity),
            "cash_gbp": fmt(cash),
            "distribution_receivables_gbp": fmt(
                sum(receivables.values(), Decimal(0))),
            "distributions_gbp": fmt(total_distributions),
            "distributions_paid_gbp": fmt(total_distributions_paid),
            "total_contributions_gbp": fmt(total_contributions),
            "total_withdrawals_gbp": fmt(total_withdrawals),
            "net_contributions_gbp": fmt(net_contributions),
            "gain_gbp": fmt(total_gain),
            "twr_pct": fmt(total_return_pct),
            "max_drawdown_pct": fmt(max_drawdown_pct),
            "fees_gbp": fmt(total_fees),
            "cash_interest_gbp": fmt(total_cash_interest),
            "cash_rates": [
                {"id": event["id"], "effective_at": iso(effective_at),
                 "available_at": iso(event["available_at"]),
                 "annual_rate": fmt(event["annual_rate"])}
                for effective_at, event in sorted(rate_events.items())
            ],
            "distributions": [
                {"id": event["id"], "product_id": event["product_id"],
                 "ex_at": iso(event["ex_at"]), "available_at": iso(event["available_at"]),
                 "pay_at": iso(event["pay_at"]),
                 "amount_per_unit_gbp": fmt(event["amount_per_unit_gbp"]),
                 "entitlement_quantity": fmt(event["entitlement_quantity"]),
                 "amount_gbp": fmt(event["amount_gbp"])}
                for event in sorted(distribution_records.values(),
                                    key=lambda item: (item["ex_at"], item["id"]))
            ],
            "holdings": [
                {"product_id": product_id, "quantity": fmt(quantity)}
                for product_id, quantity in sorted(holdings.items()) if quantity
            ],
            "pending_allocations_gbp": [
                {"product_id": product_id, "budget_gbp": fmt(amount)}
                for product_id, amount in sorted(pending.items()) if amount
            ],
            "fills": fill_events,
            "splits": applied_split_ids,
            "unfilled_allocations": unfilled_allocations,
        },
        "limitations": LIMITATIONS.copy(),
    }
