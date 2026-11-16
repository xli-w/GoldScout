"""Bounded, exact GBP accounting for explicitly evidenced manual events."""

from datetime import datetime, timezone, timedelta
from decimal import Decimal, localcontext, InvalidOperation
import re

from . import InvestmentError

MAX_EVENTS = 10000
MAX_TEXT = 200
DECIMAL = re.compile(r"(?:0|[1-9][0-9]{0,14})(?:\.[0-9]{1,8})?\Z")
RECORDED_BALANCE = re.compile(r"(?:0|[1-9][0-9]{0,24})(?:\.[0-9]{1,16})?\Z")
ZERO = Decimal(0)


def clock(value=None):
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, str):
        if len(value) > 40:
            raise InvestmentError("Invalid UTC timestamp")
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise InvestmentError("Invalid UTC timestamp") from exc
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise InvestmentError("An aware UTC timestamp is required")
    return value.astimezone(timezone.utc)


def iso(value):
    return clock(value).isoformat().replace("+00:00", "Z")


def number(value, positive=False):
    if not isinstance(value, str) or not DECIMAL.fullmatch(value):
        raise InvestmentError("Decimal values must be bounded exact nonnegative strings")
    result = Decimal(value)
    if positive and result <= 0:
        raise InvestmentError("A positive decimal is required")
    return result


def text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_TEXT:
        raise InvestmentError("Invalid bounded identifier/text")
    return value


def fields(value, required):
    if not isinstance(value, dict) or set(value) != set(required):
        raise InvestmentError("Missing or unknown fields")


def fmt(value):
    if value is None:
        return None
    result = format(value, "f")
    return result.rstrip("0").rstrip(".") if "." in result else result


def profile(value):
    fields(value, ("schema_version", "portfolio_id", "currency", "created_at",
                   "accounts", "products", "policy"))
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise InvestmentError("Only explicit schema version 1 is supported; no migrations")
    if value["currency"] != "GBP":
        raise InvestmentError("FX and other currencies are unsupported")
    text(value["portfolio_id"])
    clock(value["created_at"])
    if not isinstance(value["accounts"], list) or not 1 <= len(value["accounts"]) <= 50:
        raise InvestmentError("Between 1 and 50 accounts are required")
    if not isinstance(value["products"], list) or len(value["products"]) > 100:
        raise InvestmentError("At most 100 products")
    ids, bindings = set(), set()
    for account in value["accounts"]:
        fields(account, ("id", "name", "kind", "binding", "currency"))
        for key in ("id", "name", "binding"):
            text(account[key])
        if account["kind"] not in ("broker", "deposit") or account["currency"] != "GBP":
            raise InvestmentError("Only GBP broker cash or bank deposit accounts")
        if account["id"] in ids or account["binding"] in bindings:
            raise InvestmentError("Duplicate account identity/binding")
        ids.add(account["id"])
        bindings.add(account["binding"])
    ids = set()
    for product in value["products"]:
        fields(product, ("id", "name", "sleeve", "currency", "quantity_increment",
                         "minimum_notional_gbp"))
        text(product["id"])
        text(product["name"])
        if product["id"] in ids or product["sleeve"] not in ("equity", "bond"):
            raise InvestmentError("Duplicate or unsupported product")
        if product["currency"] != "GBP":
            raise InvestmentError("FX and other currencies are unsupported")
        number(product["quantity_increment"], positive=True)
        number(product["minimum_notional_gbp"])
        ids.add(product["id"])
    policy = value["policy"]
    fields(policy, ("id", "targets", "max_mark_age_seconds", "proposal_ttl_seconds",
                    "monthly_contribution_gbp"))
    text(policy["id"])
    fields(policy["targets"], ("equity", "bond", "cash"))
    if sum(number(v) for v in policy["targets"].values()) != 100:
        raise InvestmentError("Human-chosen targets must sum to 100 percent")
    for key, maximum in (("max_mark_age_seconds", 31 * 86400),
                         ("proposal_ttl_seconds", 7 * 86400)):
        if type(policy[key]) is not int or not 1 <= policy[key] <= maximum:
            raise InvestmentError("Invalid bounded age/expiry")
    number(policy["monthly_contribution_gbp"])
    return value


EVENT_FIELDS = {
    "contribution": ("amount_gbp",),
    "withdrawal": ("amount_gbp",),
    "transfer": ("amount_gbp", "destination_account_id"),
    "buy": ("product_id", "quantity", "price_gbp", "fee_gbp"),
    "sell": ("product_id", "quantity", "price_gbp", "fee_gbp"),
    "split": ("product_id", "ratio"),
    "interest": ("amount_gbp",),
    "dividend": ("amount_gbp", "product_id"),
    "mark": ("product_id", "price_gbp"),
}


def event(value, spec, now):
    if isinstance(value, dict) and value.get("type") == "cash_confirmation":
        fields(value, ("id", "type", "timestamp", "available_at", "evidence", "balances_gbp"))
        text(value["id"])
        text(value["evidence"])
        timestamp, available = clock(value["timestamp"]), clock(value["available_at"])
        if timestamp < clock(spec["created_at"]) or available != timestamp or available > now:
            raise InvestmentError("Cash confirmation requires a contemporaneous evidence clock")
        balances = value["balances_gbp"]
        if not isinstance(balances, dict) or set(balances) != {a["id"] for a in spec["accounts"]}:
            raise InvestmentError("Cash confirmation requires every included account")
        if any(not isinstance(amount, str) or not RECORDED_BALANCE.fullmatch(amount)
               for amount in balances.values()):
            raise InvestmentError("Invalid exact recorded cash balance")
        return value
    if isinstance(value, dict) and value.get("type") == "review_acknowledgement":
        fields(value, ("id", "type", "timestamp", "available_at", "evidence",
                       "review_kind", "reviewed_revision", "valuation_as_of"))
        text(value["id"])
        text(value["evidence"])
        timestamp, available = clock(value["timestamp"]), clock(value["available_at"])
        if (timestamp < clock(spec["created_at"]) or available != timestamp or available > now or
                clock(value["valuation_as_of"]) > timestamp):
            raise InvestmentError("Invalid review or valuation clock")
        if value["review_kind"] not in ("monthly", "annual"):
            raise InvestmentError("Review kind must be monthly or annual")
        if type(value["reviewed_revision"]) is not int or not 0 <= value["reviewed_revision"] < MAX_EVENTS:
            raise InvestmentError("Invalid reviewed revision")
        return value
    if not isinstance(value, dict) or value.get("type") not in EVENT_FIELDS:
        raise InvestmentError("Unsupported operation; corrections, FX and other actions refused")
    fields(value, ("id", "type", "account_id", "timestamp", "available_at", "currency",
                   "evidence") + EVENT_FIELDS[value["type"]])
    for key in ("id", "account_id", "evidence"):
        text(value[key])
    if value["currency"] != "GBP":
        raise InvestmentError("FX and other currencies are unsupported")
    timestamp, available = clock(value["timestamp"]), clock(value["available_at"])
    if timestamp < clock(spec["created_at"]) or available < timestamp or available > now:
        raise InvestmentError("Invalid inception or future source availability")
    accounts = {a["id"]: a for a in spec["accounts"]}
    products = {p["id"]: p for p in spec["products"]}
    if value["account_id"] not in accounts:
        raise InvestmentError("Unknown account binding")
    if "product_id" in value:
        if value["product_id"] not in products or accounts[value["account_id"]]["kind"] != "broker":
            raise InvestmentError("Unknown product or non-broker holding")
    if value["type"] == "transfer":
        target = value["destination_account_id"]
        if target not in accounts or target == value["account_id"]:
            raise InvestmentError("Transfer requires two distinct included accounts")
    for key in EVENT_FIELDS[value["type"]]:
        if key not in ("product_id", "destination_account_id"):
            number(value[key], positive=key != "fee_gbp")
    if value["type"] in ("buy", "sell"):
        with localcontext() as context:
            context.prec = 100
            if number(value["quantity"]) % number(products[value["product_id"]]["quantity_increment"]):
                raise InvestmentError("Fill quantity is not an exact product increment")
    return value


class Ledger:
    """Replay from empty inception; exact cash/holdings, conservative performance."""

    def __init__(self, spec):
        self.spec = profile(spec)
        self.cash = {a["id"]: ZERO for a in spec["accounts"]}
        self.holdings = {}
        self.marks = {}
        self.net = ZERO
        self.fees = ZERO
        self.revision = 0
        self.last_time = clock(spec["created_at"])
        self.cash_valuation_at = self.last_time
        self.ids = {}
        self.units = ZERO
        self.performance_valid = True
        self.peak = Decimal(1)
        self.unit_price = Decimal(1)
        self.drawdown = ZERO
        self.monthly_review_at = None
        self.annual_review_at = None
        self.latest_available_at = self.last_time
        self.has_portfolio_attestation = False

    def valuation(self, timestamp=None):
        total = sum(self.cash.values(), ZERO)
        times = set()
        for key, quantity in self.holdings.items():
            if quantity == 0:
                continue
            mark = self.marks.get(key)
            # This report is contemporaneous, not retrospective: a price
            # published after its own observation clock cannot value that clock.
            if mark is None or mark[2] > mark[1] or (
                    timestamp is not None and mark[1] != timestamp):
                return None, None
            times.add(mark[1])
            total += quantity * mark[0]
        if len(times) > 1:
            return None, None
        as_of = next(iter(times)) if times else self.cash_valuation_at
        # A mark before a cash/holding event is not a common-clock valuation.
        if times and as_of < self.last_time:
            return None, None
        return total, as_of

    def valuation_status(self, timestamp):
        total, as_of = self.valuation()
        if total is None or as_of is None:
            return "incomplete"
        maximum = timedelta(seconds=self.spec["policy"]["max_mark_age_seconds"])
        if as_of > timestamp or timestamp - as_of >= maximum:
            return "stale"
        return "fresh"

    def checkpoint(self, timestamp):
        total, _ = self.valuation(timestamp)
        if total is None:
            self.performance_valid = False
        else:
            self.observe(total)

    def observe(self, total):
        if total is not None and self.units > 0:
            self.unit_price = total / self.units
            self.peak = max(self.peak, self.unit_price)
            if self.peak:
                self.drawdown = min(self.drawdown, self.unit_price / self.peak - 1)

    def apply(self, value, now):
        saved = self.__dict__.copy()
        for key in ("cash", "holdings", "marks", "ids"):
            saved[key] = saved[key].copy()
        try:
            return self._apply(value, now)
        except Exception:
            self.__dict__.clear()
            self.__dict__.update(saved)
            raise

    def _apply(self, value, now):
        event(value, self.spec, now)
        if value["id"] in self.ids:
            if value != self.ids[value["id"]]:
                raise InvestmentError("Conflicting duplicate event")
            return False
        timestamp = clock(value["timestamp"])
        if timestamp < max(self.last_time, self.monthly_review_at or self.last_time):
            raise InvestmentError("Events must be timestamp ordered; no corrections")
        kind = value["type"]
        if kind in ("cash_confirmation", "review_acknowledgement") and self.latest_available_at > timestamp:
            raise InvestmentError("Prior evidence was not available at the attestation clock")
        if kind == "cash_confirmation":
            if any(quantity > 0 for quantity in self.holdings.values()):
                raise InvestmentError("Cash confirmation requires a cash-only portfolio")
            if any(Decimal(value["balances_gbp"][account]) != balance
                   for account, balance in self.cash.items()):
                raise InvestmentError("Cash confirmation disagrees with recorded balances")
            self.ids[value["id"]] = dict(value, balances_gbp=value["balances_gbp"].copy())
            self.revision += 1
            self.last_time = timestamp
            self.cash_valuation_at = timestamp
            self.latest_available_at = timestamp
            self.has_portfolio_attestation = True
            return True
        if kind == "review_acknowledgement":
            _, as_of = self.valuation()
            if value["reviewed_revision"] != self.revision:
                raise InvestmentError("Reviewed revision changed; review the current snapshot")
            if (self.valuation_status(timestamp) != "fresh" or
                    as_of != clock(value["valuation_as_of"])):
                raise InvestmentError("Review requires the same complete, fresh recorded valuation")
            self.monthly_review_at = timestamp
            if value["review_kind"] == "annual":
                self.annual_review_at = timestamp
            self.ids[value["id"]] = value.copy()
            self.revision += 1
            self.latest_available_at = timestamp
            self.has_portfolio_attestation = True
            return True
        account = value["account_id"]
        key = (account, value.get("product_id"))
        amount = number(value["amount_gbp"]) if "amount_gbp" in value else ZERO
        before, _ = self.valuation(timestamp)
        if kind in ("contribution", "withdrawal"):
            self.observe(before)
            flow = amount if kind == "contribution" else -amount
            if before is None or (before == 0 and self.units != 0):
                self.performance_valid = False
            elif before == 0:
                # Restarting capital buys units at the last linked unit price,
                # not a new inception price of one.
                self.units += flow / self.unit_price
            elif self.units:
                # Full redemption must leave exactly zero units even when
                # earlier subscriptions required a recurring decimal ratio.
                self.units = (ZERO if flow == -before else
                              self.units + flow / (before / self.units))
            self.cash[account] += flow
            self.net += flow
        elif kind == "transfer":
            self.cash[account] -= amount
            self.cash[value["destination_account_id"]] += amount
        elif kind in ("interest", "dividend"):
            self.cash[account] += amount
        elif kind in ("buy", "sell"):
            quantity, price, fee = (number(value[k]) for k in ("quantity", "price_gbp", "fee_gbp"))
            sign = 1 if kind == "buy" else -1
            self.cash[account] -= sign * quantity * price + fee
            self.holdings[key] = self.holdings.get(key, ZERO) + sign * quantity
            self.fees += fee
        elif kind == "split":
            if self.holdings.get(key, ZERO) <= 0:
                raise InvestmentError("Split requires an evidenced holding")
            self.holdings[key] *= number(value["ratio"])
            self.marks.pop(key, None)
        elif kind == "mark":
            candidate = (number(value["price_gbp"]), timestamp, clock(value["available_at"]))
            previous_mark = self.marks.get(key)
            if previous_mark is not None and previous_mark[1] == timestamp:
                raise InvestmentError("Duplicate/conflicting product/account mark clock; "
                                      "replay the identical source ID instead")
            self.marks[key] = candidate
        if any(v < 0 for v in self.cash.values()) or any(v < 0 for v in self.holdings.values()):
            raise InvestmentError("Negative cash or short position: no implicit external funding")
        if any(abs(v) > Decimal("1e24") for v in (*self.cash.values(), *self.holdings.values(), self.net)):
            raise InvestmentError("Accounting value exceeds bounds")
        self.holdings = {k: v.normalize() for k, v in self.holdings.items()}
        if any(v.as_tuple().exponent < -8 for v in self.holdings.values()):
            raise InvestmentError("Resulting quantity exceeds exact eight-place bound")
        self.ids[value["id"]] = value.copy()
        self.revision += 1
        self.last_time = timestamp
        if kind != "mark":
            self.cash_valuation_at = timestamp
        self.latest_available_at = max(self.latest_available_at, clock(value["available_at"]))
        if kind != "mark":
            total, _ = self.valuation(timestamp)
            self.observe(total)
        return True


def replay(spec, events, now=None):
    now = clock(now)
    if not isinstance(events, list) or len(events) > MAX_EVENTS:
        raise InvestmentError("Event count exceeds bound")
    with localcontext() as context:
        context.prec = 100
        try:
            ledger = Ledger(spec)
            if clock(spec["created_at"]) > now:
                raise InvestmentError("Future profile inception")
            previous = None
            for value in events:
                if isinstance(value, dict) and value.get("id") in ledger.ids:
                    event(value, spec, now)
                    if value != ledger.ids[value["id"]]:
                        raise InvestmentError("Conflicting duplicate event")
                    continue
                timestamp = clock(value.get("timestamp")) if isinstance(value, dict) else None
                if isinstance(value, dict) and value.get("type") == "review_acknowledgement":
                    ledger.apply(value, now)
                    continue
                if previous is not None and timestamp != previous:
                    ledger.checkpoint(previous)
                ledger.apply(value, now)
                previous = timestamp
            if previous is not None:
                ledger.checkpoint(previous)
            return ledger
        except (InvalidOperation, OverflowError, TypeError, KeyError) as exc:
            raise InvestmentError("Invalid accounting evidence") from exc
