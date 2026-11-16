"""Synthetic-only daily portfolio mechanics; not a product research runner."""

from decimal import Decimal, ROUND_DOWN, localcontext

from . import InvestmentError
from .core import clock, fields, fmt, iso, number, text

MAX_PRODUCTS = 25
MAX_SESSIONS = 10000
MAX_BARS = 250000
MAX_CONTRIBUTIONS = 10000
MAX_ACTIONS = 10000
MAX_VALUE = Decimal("1e24")

LIMITATIONS = [
    "Callers must supply synthetic fixtures; input provenance is not verified. Output is not product-performance evidence or an investment recommendation.",
    "All products must have synchronized GBP open/close observations; calendars and source rights are not verified.",
    "Only positive external contributions and buy-only fixed-target allocation are modeled; no sales or withdrawals.",
    "Only synthetic splits effective at modeled session opens and known by then are supported; distributions, cash interest, FX, taxes, inflation, fixed fees, spreads and slippage are unsupported.",
    "The proportional variable-cost input is an assumption, not a validated execution-cost estimate.",
    "No CLI, campaign dataset, broker, account, or live investment store is connected.",
]


def _bounded(value, positive=False):
    result = number(value, positive=positive)
    if result > MAX_VALUE:
        raise InvestmentError("Simulation value exceeds its bound")
    return result


def _portfolio_value(cash, holdings, prices):
    total = cash
    for product_id, quantity in holdings.items():
        total += quantity * prices[product_id]
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
        open_at = clock(row["open_at"])
        open_available_at = clock(row["open_available_at"])
        close_at = clock(row["close_at"])
        close_available_at = clock(row["close_available_at"])
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


def _contributions(values, close_times):
    if not isinstance(values, (list, tuple)) or len(values) > MAX_CONTRIBUTIONS:
        raise InvestmentError(f"At most {MAX_CONTRIBUTIONS} synthetic contributions are supported")
    required = ("id", "timestamp", "available_at", "amount_gbp")
    parsed = {}
    identifiers = set()
    previous_key = None
    for row in values:
        fields(row, required)
        identifier = text(row["id"])
        timestamp = clock(row["timestamp"])
        available_at = clock(row["available_at"])
        amount = _bounded(row["amount_gbp"], positive=True)
        key = (timestamp, identifier)
        if identifier in identifiers:
            raise InvestmentError("Duplicate synthetic contribution ID")
        if previous_key is not None and key <= previous_key:
            raise InvestmentError("Contributions must be ordered by timestamp and ID")
        if timestamp not in close_times or available_at > timestamp:
            raise InvestmentError("Contribution must be available by a modeled close clock")
        identifiers.add(identifier)
        parsed.setdefault(timestamp, Decimal(0))
        parsed[timestamp] += amount
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
        effective_at = clock(row["effective_at"])
        available_at = clock(row["available_at"])
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


def _allocation_budgets(products, holdings, prices, cash, flow):
    if flow <= 0 or cash <= 0:
        return {}
    equity = _portfolio_value(cash, holdings, prices)
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
                               contributions=(), variable_cost_bps="0", splits=()):
    """Run a bounded synthetic buy-only portfolio clock for engineering tests.

    A close-time contribution creates allocation budgets for the next common
    session's open. This function loads no external data; callers must provide
    synthetic fixtures, whose provenance it cannot independently verify.
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
    external_flows = _contributions(contributions, close_times)
    split_events = _splits(splits, product_state, open_times)

    cash = Decimal(0)
    holdings = {}
    prices = {}
    units = Decimal(0)
    unit_value = Decimal(1)
    peak = Decimal(1)
    worst_drawdown = Decimal(0)
    total_contributions = Decimal(0)
    total_fees = Decimal(0)
    pending = {}
    curve = []
    fill_events = []
    unfilled_allocations = []
    applied_split_ids = []

    with localcontext() as context:
        context.prec = 100
        for index, (session_id, session) in enumerate(sessions):
            session_bars = session["bars"]
            applied_splits = split_events.get(session["open_at"], [])
            for action in applied_splits:
                _apply_split(action, product_state, holdings)
                applied_split_ids.append(action["id"])
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

            prices = {product_id: session_bars[product_id]["close"]
                      for product_id in product_state}
            before_flow = _portfolio_value(cash, holdings, prices)
            if units:
                unit_value = before_flow / units
                peak = max(peak, unit_value)
                worst_drawdown = min(worst_drawdown, unit_value / peak - 1)

            flow = external_flows.get(session["close_at"], Decimal(0))
            if index == 0:
                flow += initial_cash
            if flow:
                if units:
                    units += flow / unit_value
                else:
                    units = flow
                    unit_value = Decimal(1)
                cash += flow
                total_contributions += flow
                pending = _allocation_budgets(
                    product_state, holdings, prices, cash, flow)

            equity = _portfolio_value(cash, holdings, prices)
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
                "net_contributions_gbp": fmt(total_contributions),
                "unit_value": fmt(unit_value),
                "twr_pct": fmt((unit_value - 1) * 100),
                "drawdown_pct": fmt(-worst_drawdown * 100),
                "fees_gbp": fmt(total_fees),
                "fills": fills,
                "splits": [action["id"] for action in applied_splits],
                "unfilled_allocations": unfilled,
            })

    ending_equity = _portfolio_value(cash, holdings, prices)
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
            "net_contributions_gbp": fmt(total_contributions),
            "gain_gbp": fmt(ending_equity - total_contributions),
            "twr_pct": fmt((unit_value - 1) * 100),
            "max_drawdown_pct": fmt(-worst_drawdown * 100),
            "fees_gbp": fmt(total_fees),
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
