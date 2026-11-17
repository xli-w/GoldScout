"""Bounded, exact GBP accounting for explicitly evidenced manual events."""

from datetime import date, datetime, timezone, timedelta
from decimal import Decimal, localcontext, InvalidOperation
from copy import deepcopy
import json
import re

from . import InvestmentError

MAX_EVENTS = 10000
MAX_TEXT = 200
DECIMAL = re.compile(r"(?:0|[1-9][0-9]{0,14})(?:\.[0-9]{1,8})?\Z")
RECORDED_BALANCE = re.compile(r"(?:0|[1-9][0-9]{0,24})(?:\.[0-9]{1,16})?\Z")
ZERO = Decimal(0)
ROLES = ("core", "dynamic", "unassigned")
ROLE_EVENTS = {"contribution", "withdrawal", "transfer", "interest",
               "dividend", "buy", "sell"}


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


def explicit_clock(value):
    if isinstance(value, datetime) and type(value) is not datetime:
        raise InvestmentError("Datetime subclasses are not supported for explicit evidence clocks")
    if value is None or isinstance(value, str) and re.search(r"[.,][0-9]{7}", value):
        raise InvestmentError("An explicit UTC timestamp with at most microsecond precision is required")
    return clock(value)


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


def validate_savings_terms(terms, available_by):
    fields(terms, ("annual_rate_pct", "rate_basis", "effective_from",
                   "observed_at", "available_at", "source", "maturity_date",
                   "notice_days"))
    text(terms["source"])
    try:
        effective = date.fromisoformat(terms["effective_from"])
        maturity = (date.fromisoformat(terms["maturity_date"])
                    if terms["maturity_date"] is not None else None)
    except (TypeError, ValueError) as exc:
        raise InvestmentError("Invalid savings-terms date") from exc
    observed, available = clock(terms["observed_at"]), clock(terms["available_at"])
    if (effective.isoformat() != terms["effective_from"] or
            maturity is not None and maturity.isoformat() != terms["maturity_date"] or
            maturity is not None and maturity < effective or
            observed > available or available > available_by):
        raise InvestmentError("Invalid savings-terms evidence clock or date")
    rate, basis = terms["annual_rate_pct"], terms["rate_basis"]
    if rate is None:
        if basis is not None:
            raise InvestmentError("A rate basis requires a recorded rate")
    elif (number(rate) > 100 or basis not in ("AER", "gross")):
        raise InvestmentError("Invalid bounded savings rate or basis")
    notice = terms["notice_days"]
    if notice is not None and (type(notice) is not int or not 0 <= notice <= 3650):
        raise InvestmentError("Invalid savings notice period")


def fmt(value):
    if value is None:
        return None
    result = format(value, "f")
    return result.rstrip("0").rstrip(".") if "." in result else result


def profile(value):
    fields(value, ("schema_version", "portfolio_id", "currency", "created_at",
                   "accounts", "products", "policy"))
    if type(value["schema_version"]) is not int or value["schema_version"] not in (1, 2):
        raise InvestmentError("Only explicit schema versions 1 and 2 are supported; no migrations")
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
        required = ("id", "name", "kind", "binding", "currency")
        allowed = required + (("terms",) if value["schema_version"] == 2 else ())
        if not isinstance(account, dict) or not set(required) <= set(account) or set(account) - set(allowed):
            raise InvestmentError("Missing or unknown account fields")
        for key in ("id", "name", "binding"):
            text(account[key])
        if account["kind"] not in ("broker", "deposit") or account["currency"] != "GBP":
            raise InvestmentError("Only GBP broker cash or bank deposit accounts")
        if account["id"] in ids or account["binding"] in bindings:
            raise InvestmentError("Duplicate account identity/binding")
        if value["schema_version"] == 2 and account.get("terms") is not None:
            if account["kind"] != "deposit":
                raise InvestmentError("Savings terms require a deposit account")
            validate_savings_terms(account["terms"], clock(value["created_at"]))
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
    if value["schema_version"] == 2:
        if policy is not None:
            raise InvestmentError("Version 2 opening accounts do not yet support allocation policies")
        return value
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

CORRECTABLE_CASH_EVENTS = {"contribution", "withdrawal", "interest"}
CORRECTABLE_EVENTS = set(EVENT_FIELDS) | {"dividend"}
CORRECTABLE_EVENTS.discard("role_assignment")


def event(value, spec, now):
    if isinstance(value, dict) and value.get("type") == "role_assignment":
        if spec["schema_version"] != 2:
            raise InvestmentError("Role attribution requires a version 2 store")
        fields(value, ("id", "type", "timestamp", "available_at", "evidence",
                       "account_id", "product_id", "role", "amount"))
        for key in ("id", "evidence", "account_id"):
            text(value[key])
        timestamp, available = clock(value["timestamp"]), clock(value["available_at"])
        if timestamp < clock(spec["created_at"]) or available != timestamp or available > now:
            raise InvestmentError("Role attribution requires contemporaneous evidence")
        if value["role"] not in ("core", "dynamic"):
            raise InvestmentError("Role attribution requires core or dynamic")
        accounts = {account["id"]: account for account in spec["accounts"]}
        products = {product["id"]: product for product in spec["products"]}
        account, product = value["account_id"], value["product_id"]
        if account not in accounts:
            raise InvestmentError("Unknown account binding")
        amount = number(value["amount"], positive=True)
        if product is not None:
            if product not in products or accounts[account]["kind"] != "broker":
                raise InvestmentError("Role holding requires an included broker and product")
            with localcontext() as context:
                context.prec = 100
                if amount % number(products[product]["quantity_increment"], positive=True):
                    raise InvestmentError("Role quantity is not an exact product increment")
        return value
    if isinstance(value, dict) and value.get("type") == "correction":
        if spec["schema_version"] != 2:
            raise InvestmentError("Corrections require an explicit version 2 store")
        fields(value, ("id", "type", "timestamp", "available_at", "evidence",
                       "original_event_id", "replacement_event"))
        for key in ("id", "evidence", "original_event_id"):
            text(value[key])
        timestamp, available = clock(value["timestamp"]), clock(value["available_at"])
        if (timestamp < clock(spec["created_at"]) or available < timestamp or
                available > now):
            raise InvestmentError("Invalid correction evidence clock")
        replacement = value["replacement_event"]
        if (not isinstance(replacement, dict) or
                replacement.get("type") not in CORRECTABLE_EVENTS):
            raise InvestmentError("This event type is not eligible for correction")
        event(replacement, spec, now)
        if (replacement["timestamp"] > value["timestamp"] or
                replacement["available_at"] != value["available_at"] or
                replacement["evidence"] != value["evidence"] or
                replacement["id"] == value["original_event_id"] or
                replacement["id"] == value["id"]):
            raise InvestmentError("Correction replacement must preserve its effective clock "
                                  "and use the correction evidence clock")
        return value
    if isinstance(value, dict) and value.get("type") == "opening":
        if spec["schema_version"] != 2:
            raise InvestmentError("Opening evidence requires an explicit version 2 store")
        fields(value, ("id", "type", "timestamp", "available_at", "evidence",
                       "balances_gbp", "holdings"))
        text(value["id"])
        text(value["evidence"])
        timestamp, available = clock(value["timestamp"]), clock(value["available_at"])
        if timestamp < clock(spec["created_at"]) or available < timestamp or available > now:
            raise InvestmentError("Invalid opening evidence clock")
        accounts = {a["id"]: a for a in spec["accounts"]}
        products = {p["id"]: p for p in spec["products"]}
        balances = value["balances_gbp"]
        if not isinstance(balances, dict) or set(balances) != set(accounts):
            raise InvestmentError("Opening evidence requires every included account balance")
        for amount in balances.values():
            number(amount)
        holdings = value["holdings"]
        if not isinstance(holdings, list) or len(holdings) > 5000:
            raise InvestmentError("Opening holdings exceed the supported bound")
        seen = set()
        for holding in holdings:
            fields(holding, ("account_id", "product_id", "quantity", "price_gbp"))
            account, product = holding["account_id"], holding["product_id"]
            if (account not in accounts or accounts[account]["kind"] != "broker"
                    or product not in products or (account, product) in seen):
                raise InvestmentError("Invalid or duplicate opening holding")
            seen.add((account, product))
            quantity = number(holding["quantity"], positive=True)
            if quantity % number(products[product]["quantity_increment"], positive=True):
                raise InvestmentError("Opening quantity is not an exact product increment")
            if holding["price_gbp"] is not None:
                number(holding["price_gbp"], positive=True)
        return value
    if isinstance(value, dict) and value.get("type") == "profile_change":
        if spec["schema_version"] != 2:
            raise InvestmentError("Profile amendments require an explicit version 2 store")
        common = ("id", "type", "timestamp", "available_at", "evidence", "action")
        text(value.get("id"))
        text(value.get("evidence"))
        timestamp, available = clock(value.get("timestamp")), clock(value.get("available_at"))
        if timestamp < clock(spec["created_at"]) or available != timestamp or available > now:
            raise InvestmentError("Profile amendments require a contemporaneous evidence clock")
        action = value.get("action")
        candidate = deepcopy(spec)
        if action == "add_product":
            fields(value, common + ("product",))
            candidate["products"].append(value["product"])
        elif action == "rename":
            fields(value, common + ("target", "entity_id", "name"))
            target, entity_id = value["target"], text(value["entity_id"])
            text(value["name"])
            if target == "account":
                entities = candidate["accounts"]
            elif target == "product":
                entities = candidate["products"]
            else:
                raise InvestmentError("Profile rename target must be an account or product")
            entity = next((item for item in entities if item["id"] == entity_id), None)
            if entity is None:
                raise InvestmentError("Unknown profile identity")
            entity["name"] = value["name"]
        elif action == "retire":
            fields(value, common + ("target", "entity_id"))
            target, entity_id = value["target"], text(value["entity_id"])
            if target == "account":
                entities = candidate["accounts"]
            elif target == "product":
                entities = candidate["products"]
            else:
                raise InvestmentError("Profile retirement target must be an account or product")
            if not any(item["id"] == entity_id for item in entities):
                raise InvestmentError("Unknown profile identity")
        else:
            raise InvestmentError("Unsupported profile amendment")
        profile(candidate)
        return value
    if isinstance(value, dict) and value.get("type") == "scope_addition":
        if spec["schema_version"] != 2:
            raise InvestmentError("Scope additions require an explicit version 2 store")
        fields(value, ("id", "type", "timestamp", "available_at", "evidence",
                       "account", "balances_gbp", "holdings"))
        for key in ("id", "evidence"):
            text(value[key])
        timestamp, available = clock(value["timestamp"]), clock(value["available_at"])
        if timestamp < clock(spec["created_at"]) or available != timestamp or available > now:
            raise InvestmentError("Scope additions require complete contemporaneous evidence")
        candidate = deepcopy(spec)
        account = value["account"]
        if (not isinstance(account, dict) or set(account) !=
                {"id", "name", "kind", "binding", "currency"}):
            raise InvestmentError("Scope addition requires a new immutable account identity")
        candidate["accounts"].append(account)
        profile(candidate)
        balances = value["balances_gbp"]
        account_ids = {item["id"] for item in candidate["accounts"]}
        if (not isinstance(balances, dict) or account["id"] not in balances or
                not set(balances) <= account_ids):
            raise InvestmentError("Scope addition requires every account balance")
        if any(not isinstance(amount, str) or not RECORDED_BALANCE.fullmatch(amount)
               or Decimal(amount) > Decimal("1e24") for amount in balances.values()):
            raise InvestmentError("Invalid exact scope-addition cash balance")
        holdings = value["holdings"]
        if not isinstance(holdings, list) or len(holdings) > 5000:
            raise InvestmentError("Scope-addition holdings exceed the supported bound")
        accounts = {item["id"]: item for item in candidate["accounts"]}
        products = {item["id"]: item for item in candidate["products"]}
        seen = set()
        for holding in holdings:
            fields(holding, ("account_id", "product_id", "quantity", "price_gbp"))
            account_id, product_id = holding["account_id"], holding["product_id"]
            if (account_id not in accounts or accounts[account_id]["kind"] != "broker"
                    or product_id not in products or (account_id, product_id) in seen):
                raise InvestmentError("Invalid or duplicate scope-addition holding")
            seen.add((account_id, product_id))
            quantity = number(holding["quantity"], positive=True)
            increment = number(products[product_id]["quantity_increment"], positive=True)
            if quantity % increment:
                raise InvestmentError("Scope-addition quantity is not an exact product increment")
            number(holding["price_gbp"], positive=True)
        return value
    if isinstance(value, dict) and value.get("type") == "cash_observation":
        if spec["schema_version"] != 2:
            raise InvestmentError("Cash observations require an explicit version 2 store")
        fields(value, ("id", "type", "timestamp", "available_at", "evidence",
                       "source", "account_id", "balance_gbp"))
        for key in ("id", "evidence", "source", "account_id"):
            text(value[key])
        timestamp, available = clock(value["timestamp"]), clock(value["available_at"])
        if timestamp < clock(spec["created_at"]) or available < timestamp or available > now:
            raise InvestmentError("Invalid cash observation evidence clock")
        if value["account_id"] not in {account["id"] for account in spec["accounts"]}:
            raise InvestmentError("Cash observation requires an included account")
        if (not isinstance(value["balance_gbp"], str) or
                not RECORDED_BALANCE.fullmatch(value["balance_gbp"]) or
                Decimal(value["balance_gbp"]) > Decimal("1e24")):
            raise InvestmentError("Invalid bounded observed cash balance")
        return value
    if isinstance(value, dict) and value.get("type") == "savings_terms":
        if spec["schema_version"] != 2:
            raise InvestmentError("Savings-terms updates require an explicit version 2 store")
        fields(value, ("id", "type", "timestamp", "available_at", "evidence",
                       "account_id", "annual_rate_pct", "rate_basis", "effective_from",
                       "source", "maturity_date", "notice_days"))
        for key in ("id", "evidence", "account_id"):
            text(value[key])
        timestamp, available = clock(value["timestamp"]), clock(value["available_at"])
        if timestamp < clock(spec["created_at"]) or available < timestamp or available > now:
            raise InvestmentError("Invalid savings-terms update evidence clock")
        accounts = {account["id"]: account for account in spec["accounts"]}
        if (value["account_id"] not in accounts or
                accounts[value["account_id"]]["kind"] != "deposit"):
            raise InvestmentError("Savings-terms updates require an included deposit account")
        terms = {
            "annual_rate_pct": value["annual_rate_pct"],
            "rate_basis": value["rate_basis"],
            "effective_from": value["effective_from"],
            "observed_at": value["timestamp"],
            "available_at": value["available_at"],
            "source": value["source"],
            "maturity_date": value["maturity_date"],
            "notice_days": value["notice_days"],
        }
        validate_savings_terms(terms, now)
        return value
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
        if spec["schema_version"] == 2:
            raise InvestmentError("Review acknowledgements require an implemented allocation policy")
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
    required = ("id", "type", "account_id", "timestamp", "available_at", "currency",
                "evidence") + EVENT_FIELDS[value["type"]]
    has_role = "role" in value
    has_role_evidence = "role_evidence" in value
    if has_role and (spec["schema_version"] != 2 or value["type"] not in ROLE_EVENTS
                     or value["role"] not in ROLES):
        raise InvestmentError("Unsupported management role")
    if has_role_evidence:
        if spec["schema_version"] != 2 or value["type"] not in ("interest", "dividend"):
            raise InvestmentError("Role income evidence is not applicable")
        text(value["role_evidence"])
    fields(value, required + (("role",) if has_role else ()) +
           (("role_evidence",) if has_role_evidence else ()))
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
        self.spec = deepcopy(profile(spec))
        self.cash = {a["id"]: ZERO for a in self.spec["accounts"]}
        self.holdings = {}
        self.marks = {}
        self.cash_observations = {}
        self.cash_observation_history = {}
        self.account_terms = {a["id"]: a.get("terms") for a in self.spec["accounts"]}
        self.updated_account_terms = set()
        self.terms_last_time = clock(spec["created_at"])
        self.terms_latest_available_at = self.terms_last_time
        self.net = ZERO
        self.fees = ZERO
        self.active_accounts = {a["id"] for a in self.spec["accounts"]}
        self.active_products = {p["id"] for p in self.spec["products"]}
        self.retired_accounts = set()
        self.retired_products = set()
        self.profile_revision = 0
        self.profile_history = []
        self.scope_additions = ZERO
        self.scope_addition_history = []
        self.historical_correction = False
        self.correction_available_at = self.terms_last_time
        self.baseline_suppressed = False
        self.revision = 0
        self.last_time = clock(spec["created_at"])
        self.profile_last_time = self.last_time
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
        if spec["schema_version"] == 2:
            self.opened = False
            self.baseline_at = None
            self.baseline_value = None
            self.baseline_net = None
            self.baseline_scope_additions = ZERO
            self.valuation_history = []
            self.superseded_ids = set()
            self.role_active = False
            self.role_cash = {}
            self.role_holdings = {}
            self.role_last_time = self.last_time

    def activate_roles(self):
        if not self.role_active:
            products = {item["id"]: item for item in self.spec["products"]}
            if any(quantity % number(products[product]["quantity_increment"], positive=True)
                   for (_, product), quantity in self.holdings.items()):
                raise InvestmentError("Existing holding has an unsupported role quantity increment")
            self.role_cash = {(account, "unassigned"): amount
                              for account, amount in self.cash.items()}
            self.role_holdings = {(*key, "unassigned"): quantity
                                  for key, quantity in self.holdings.items()}
            self.role_active = True

    def valuation(self, timestamp=None):
        if self.spec["schema_version"] == 2 and not self.opened:
            return None, None
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
        if self.spec["schema_version"] == 2 and self.latest_available_at > as_of:
            return None, None
        if self.spec["schema_version"] == 2 and total > Decimal("1e24"):
            raise InvestmentError("Portfolio valuation exceeds the accounting bound")
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
        if self.spec["schema_version"] == 2 and self.baseline_at is None:
            if total is not None and not self.baseline_suppressed:
                self.baseline_at = timestamp
                self.baseline_value = total
                self.baseline_net = self.net
                self.baseline_scope_additions = self.scope_additions
                self.units = total
                self.unit_price = Decimal(1)
                self.performance_valid = True
            return
        if total is None:
            self.performance_valid = False
        else:
            self.observe(total)

    def record_valuation(self, timestamp):
        if self.spec["schema_version"] != 2:
            return
        timestamp = iso(timestamp)
        if self.valuation_history and self.valuation_history[-1]["timestamp"] == timestamp:
            return
        at = clock(timestamp)
        equity, as_of = self.valuation(at)
        complete = equity is not None and as_of == at
        available_at = self.latest_available_at
        gain = None
        capital_basis = None
        twr = None
        drawdown = None
        if complete and not self.historical_correction:
            capital_basis = (self.baseline_value + self.net - self.baseline_net +
                             self.scope_additions - self.baseline_scope_additions)
            gain = equity - capital_basis
            if self.performance_valid and self.units > 0 and self.baseline_at is not None:
                twr = (self.unit_price - 1) * 100
                drawdown = -self.drawdown * 100
        if self.historical_correction:
            gap_reason = "Historical correction invalidated performance evidence; no new baseline was established."
        elif available_at > at:
            gap_reason = "Some valuation evidence became available after this event clock."
        elif not complete:
            gap_reason = "A complete contemporaneous valuation is unavailable."
        else:
            gap_reason = None
        self.valuation_history.append({
            "timestamp": timestamp,
            "source_available_at": iso(available_at),
            "as_of": iso(as_of) if complete else None,
            "complete": complete,
            "equity_gbp": fmt(equity) if complete else None,
            "capital_basis_gbp": fmt(capital_basis) if complete else None,
            "net_contributions_gbp": (
                fmt(self.net) if available_at <= at else None),
            "gain_since_baseline_gbp": fmt(gain) if complete else None,
            "twr_pct": fmt(twr),
            "max_drawdown_pct": fmt(drawdown),
            "gap_reason": gap_reason,
        })

    def observe(self, total):
        if total is not None and self.units > 0:
            self.unit_price = total / self.units
            self.peak = max(self.peak, self.unit_price)
            if self.peak:
                self.drawdown = min(self.drawdown, self.unit_price / self.peak - 1)

    def apply(self, value, now):
        saved = self.__dict__.copy()
        for key in ("cash", "holdings", "marks", "ids", "cash_observations",
                    "account_terms"):
            saved[key] = saved[key].copy()
        saved["spec"] = deepcopy(self.spec)
        saved["active_accounts"] = self.active_accounts.copy()
        saved["active_products"] = self.active_products.copy()
        saved["retired_accounts"] = self.retired_accounts.copy()
        saved["retired_products"] = self.retired_products.copy()
        saved["profile_history"] = self.profile_history.copy()
        saved["scope_addition_history"] = self.scope_addition_history.copy()
        if self.spec["schema_version"] == 2:
            saved["role_cash"] = self.role_cash.copy()
            saved["role_holdings"] = self.role_holdings.copy()
        saved["historical_correction"] = self.historical_correction
        saved["scope_additions"] = self.scope_additions
        saved["scope_addition_history"] = self.scope_addition_history.copy()
        saved["profile_history"] = self.profile_history.copy()
        saved["updated_account_terms"] = saved["updated_account_terms"].copy()
        saved["cash_observation_history"] = {
            account: observations.copy()
            for account, observations in self.cash_observation_history.items()
        }
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
        order_time = max(self.last_time, self.monthly_review_at or self.last_time)
        if self.spec["schema_version"] == 2:
            order_time = max(order_time, self.terms_last_time, self.role_last_time,
                             self.profile_last_time)
        if timestamp < order_time:
            raise InvestmentError("Events must be timestamp ordered")
        if self.historical_correction and self.correction_available_at > timestamp:
            raise InvestmentError("Prior correction evidence was not available at this event clock")
        kind = value["type"]
        if kind != "cash_observation" and any(
                clock(observation["timestamp"]) == timestamp
                for observation in self.cash_observations.values()):
            raise InvestmentError("Cash observations must be final at their timestamp")
        if self.spec["schema_version"] == 2 and kind == "opening":
            if self.revision != 0 or self.opened:
                raise InvestmentError("Opening evidence must be the first event")
            self.cash = {account: number(amount)
                         for account, amount in value["balances_gbp"].items()}
            for holding in value["holdings"]:
                key = (holding["account_id"], holding["product_id"])
                self.holdings[key] = number(holding["quantity"], positive=True)
                if holding["price_gbp"] is not None:
                    self.marks[key] = (number(holding["price_gbp"], positive=True),
                                       timestamp, clock(value["available_at"]))
            if (sum(self.cash.values(), ZERO) + sum(
                    self.holdings[key] * mark[0] for key, mark in self.marks.items())
                    > Decimal("1e24")):
                raise InvestmentError("Opening valuation exceeds the accounting bound")
            self.opened = True
            self.ids[value["id"]] = value.copy()
            self.revision += 1
            self.last_time = timestamp
            self.cash_valuation_at = timestamp
            self.latest_available_at = clock(value["available_at"])
            self.checkpoint(timestamp)
            return True
        if self.spec["schema_version"] == 2 and not self.opened:
            raise InvestmentError("Explicit opening evidence is required before other events")
        if self.spec["schema_version"] == 2 and kind not in (
                "profile_change", "scope_addition", "correction"):
            account_id = value.get("account_id")
            product_id = value.get("product_id")
            destination = value.get("destination_account_id")
            if (account_id is not None and account_id not in self.active_accounts or
                    product_id is not None and product_id not in self.active_products or
                    destination is not None and destination not in self.active_accounts):
                raise InvestmentError("Events cannot use a retired account or product")
        if self.spec["schema_version"] == 2 and kind == "profile_change":
            if max(self.latest_available_at, self.terms_latest_available_at) > timestamp:
                raise InvestmentError("Prior evidence was not available at the profile-change clock")
            action = value["action"]
            if action == "add_product":
                self.spec["products"].append(deepcopy(value["product"]))
                self.active_products.add(value["product"]["id"])
            elif action == "rename":
                collection = (self.spec["accounts"] if value["target"] == "account"
                              else self.spec["products"])
                entity = next(item for item in collection if item["id"] == value["entity_id"])
                entity["name"] = value["name"]
            elif action == "retire":
                entity_id = value["entity_id"]
                if value["target"] == "account":
                    if (self.cash[entity_id] != 0 or any(
                            account == entity_id and quantity != 0
                            for (account, _), quantity in self.holdings.items())):
                        raise InvestmentError("Account retirement requires zero cash and holdings")
                    self.active_accounts.remove(entity_id)
                    self.retired_accounts.add(entity_id)
                else:
                    if any(product == entity_id and quantity != 0
                           for (_, product), quantity in self.holdings.items()):
                        raise InvestmentError("Product retirement requires zero holdings")
                    self.active_products.remove(entity_id)
                    self.retired_products.add(entity_id)
            self.profile_revision += 1
            self.profile_history.append(value.copy())
            self.ids[value["id"]] = value.copy()
            self.revision += 1
            self.profile_last_time = timestamp
            return True
        if self.spec["schema_version"] == 2 and kind == "scope_addition":
            if max(self.latest_available_at, self.terms_latest_available_at) > timestamp:
                raise InvestmentError("Prior evidence was not available at the scope-addition clock")
            if any(item["product_id"] not in self.active_products
                   for item in value["holdings"]):
                raise InvestmentError("Scope addition cannot use a retired product")
            account = value["account"]
            account_id = account["id"]
            if account_id in self.active_accounts:
                raise InvestmentError("Scope addition account identity already exists")
            balances = value["balances_gbp"]
            expected_accounts = self.active_accounts | {account_id}
            if set(balances) != expected_accounts:
                raise InvestmentError("Scope addition requires every active account balance")
            if any(Decimal(balances[item]) != amount for item, amount in self.cash.items()
                   if item in self.active_accounts):
                raise InvestmentError("Scope addition cash snapshot differs from recorded balances")
            supplied = {
                (item["account_id"], item["product_id"]): item
                for item in value["holdings"]
            }
            existing = {
                key: amount for key, amount in self.holdings.items()
                if amount > 0 and key[0] in self.active_accounts
            }
            new_holdings = {
                key: item for key, item in supplied.items() if key[0] == account_id
            }
            if any(supplied.get(key, {}).get("quantity") != fmt(amount)
                   for key, amount in existing.items()):
                raise InvestmentError("Scope addition holdings differ from recorded quantities")
            if set(supplied) != set(existing) | set(new_holdings):
                raise InvestmentError("Scope addition must cover all existing and added holdings")
            if not self.baseline_at or not self.performance_valid or self.units <= 0:
                raise InvestmentError("Scope addition requires an existing valid performance baseline")
            before = sum((self.cash[item] for item in self.active_accounts), ZERO)
            for key, amount in existing.items():
                before += amount * number(supplied[key]["price_gbp"], positive=True)
            addition = Decimal(balances[account_id])
            for key, item in new_holdings.items():
                addition += number(item["quantity"], positive=True) * number(
                    item["price_gbp"], positive=True)
            if before <= 0 or before + addition > Decimal("1e24"):
                raise InvestmentError("Scope addition requires bounded positive prior equity")
            if addition > Decimal("1e24"):
                raise InvestmentError("Scope addition exceeds the accounting bound")
            previous_units, previous_price = self.units, self.unit_price
            self.observe(before)
            previous_price = self.unit_price
            self.spec["accounts"].append(deepcopy(account))
            self.active_accounts.add(account_id)
            self.cash[account_id] = Decimal(balances[account_id])
            self.account_terms[account_id] = None
            for key, item in supplied.items():
                self.marks[key] = (
                    number(item["price_gbp"], positive=True), timestamp, timestamp)
            for key, item in new_holdings.items():
                self.holdings[key] = number(item["quantity"], positive=True)
            if self.role_active:
                self.role_cash[(account_id, "unassigned")] = self.cash[account_id]
                for key, item in new_holdings.items():
                    self.role_holdings[(*key, "unassigned")] = number(
                        item["quantity"], positive=True)
                self.role_last_time = timestamp
            after = before + addition
            self.scope_additions += addition
            self.units = previous_units * after / before
            self.unit_price = previous_price
            self.cash_valuation_at = timestamp
            self.last_time = timestamp
            self.latest_available_at = timestamp
            self.profile_revision += 1
            self.profile_history.append(value.copy())
            self.scope_addition_history.append({
                "id": value["id"], "account_id": account_id,
                "amount_gbp": fmt(addition), "timestamp": value["timestamp"],
                "available_at": value["available_at"], "evidence": value["evidence"],
            })
            self.ids[value["id"]] = value.copy()
            self.revision += 1
            self.observe(after)
            if self.spec["schema_version"] == 2 and after > Decimal("1e24"):
                raise InvestmentError("Scope-addition valuation exceeds the accounting bound")
            return True
        if kind == "role_assignment":
            if max(self.latest_available_at, self.terms_latest_available_at) > timestamp:
                raise InvestmentError("Prior evidence was not available at the attribution clock")
            self.activate_roles()
            account, product, role = value["account_id"], value["product_id"], value["role"]
            balances = self.role_cash if product is None else self.role_holdings
            source = (account, "unassigned") if product is None else (account, product, "unassigned")
            target = (account, role) if product is None else (account, product, role)
            amount = number(value["amount"], positive=True)
            if balances.get(source, ZERO) < amount:
                raise InvestmentError("Role attribution exceeds unassigned balance")
            balances[source] -= amount
            balances[target] = balances.get(target, ZERO) + amount
            self.ids[value["id"]] = value.copy()
            self.revision += 1
            self.role_last_time = timestamp
            return True
        if kind in ("cash_confirmation", "review_acknowledgement") and max(
                self.latest_available_at, self.terms_latest_available_at) > timestamp:
            raise InvestmentError("Prior evidence was not available at the attestation clock")
        if kind == "savings_terms":
            account = value["account_id"]
            previous = self.account_terms.get(account)
            if previous is not None and clock(previous["observed_at"]) >= timestamp:
                raise InvestmentError("Savings-terms updates must follow prior account terms")
            terms = {
                "annual_rate_pct": value["annual_rate_pct"],
                "rate_basis": value["rate_basis"],
                "effective_from": value["effective_from"],
                "observed_at": value["timestamp"],
                "available_at": value["available_at"],
                "source": value["source"],
                "maturity_date": value["maturity_date"],
                "notice_days": value["notice_days"],
            }
            self.account_terms[account] = terms
            self.updated_account_terms.add(account)
            self.terms_last_time = timestamp
            self.terms_latest_available_at = max(
                self.terms_latest_available_at, clock(value["available_at"]))
            self.ids[value["id"]] = value.copy()
            self.revision += 1
            return True
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
            if self.spec["schema_version"] == 2 and self.baseline_at is None:
                self.checkpoint(timestamp)
            return True
        if kind == "cash_observation":
            account = value["account_id"]
            previous = self.cash_observations.get(account)
            if previous is not None and clock(previous["timestamp"]) == timestamp:
                raise InvestmentError("A different cash observation already exists at this clock")
            observed = Decimal(value["balance_gbp"])
            observation = {
                "balance_gbp": value["balance_gbp"],
                "difference_gbp": fmt(observed - self.cash[account]),
                "timestamp": value["timestamp"],
                "available_at": value["available_at"],
                "source": value["source"],
                "evidence": value["evidence"],
            }
            self.cash_observations[account] = observation
            self.cash_observation_history.setdefault(account, []).append(observation)
            self.ids[value["id"]] = value.copy()
            self.revision += 1
            self.last_time = timestamp
            self.latest_available_at = max(self.latest_available_at, clock(value["available_at"]))
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
        if self.spec["schema_version"] == 2:
            if "role" in value:
                self.activate_roles()
            if self.role_active:
                role = value.get("role", "unassigned")
                cash_key = (account, role)
                holding_key = (account, value.get("product_id"), role)
                if kind in ("interest", "dividend") and "role_evidence" not in value:
                    raise InvestmentError("Role income requires explicit allocation evidence")
                if kind == "interest" and self.role_cash.get(cash_key, ZERO) <= 0:
                    raise InvestmentError("Interest role requires evidenced role cash")
                if kind == "dividend" and self.role_holdings.get(holding_key, ZERO) <= 0:
                    raise InvestmentError("Dividend role requires an evidenced role holding")
                if kind in ("contribution", "interest", "dividend"):
                    self.role_cash[cash_key] = self.role_cash.get(cash_key, ZERO) + amount
                elif kind == "withdrawal":
                    self.role_cash[cash_key] = self.role_cash.get(cash_key, ZERO) - amount
                elif kind == "transfer":
                    destination = (value["destination_account_id"], role)
                    self.role_cash[cash_key] = self.role_cash.get(cash_key, ZERO) - amount
                    self.role_cash[destination] = self.role_cash.get(destination, ZERO) + amount
                elif kind in ("buy", "sell"):
                    quantity, price, fee = (number(value[k]) for k in
                                            ("quantity", "price_gbp", "fee_gbp"))
                    sign = 1 if kind == "buy" else -1
                    self.role_cash[cash_key] = self.role_cash.get(cash_key, ZERO) - sign * quantity * price - fee
                    self.role_holdings[holding_key] = self.role_holdings.get(holding_key, ZERO) + sign * quantity
                elif kind == "split":
                    product = next(item for item in self.spec["products"]
                                   if item["id"] == value["product_id"])
                    increment = number(product["quantity_increment"], positive=True)
                    for role_name in ROLES:
                        role_key = (account, value["product_id"], role_name)
                        if role_key in self.role_holdings:
                            self.role_holdings[role_key] *= number(value["ratio"])
                            if self.role_holdings[role_key] % increment:
                                raise InvestmentError("Split produces unsupported role quantity increment")
                if (any(v < 0 for v in self.role_cash.values()) or
                        any(v < 0 or v.normalize().as_tuple().exponent < -8
                            for v in self.role_holdings.values())):
                    raise InvestmentError("Insufficient role cash or holdings, or unsupported role quantity")
                if kind in ROLE_EVENTS or kind == "split":
                    self.role_last_time = timestamp
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
        if kind not in ("mark", "cash_observation"):
            self.cash_valuation_at = timestamp
        self.latest_available_at = max(self.latest_available_at, clock(value["available_at"]))
        if self.spec["schema_version"] == 2 and self.baseline_at is None:
            self.checkpoint(timestamp)
        if kind not in ("mark", "cash_observation"):
            total, _ = self.valuation(timestamp)
            self.observe(total)
        return True


def _replay_corrected_prefix(spec, prefix, correction, ledger, now):
    event(correction, ledger.spec, now)
    latest_ordered = max(
        ledger.last_time, ledger.monthly_review_at or ledger.last_time,
        ledger.terms_last_time, ledger.role_last_time, ledger.profile_last_time)
    if clock(correction["timestamp"]) < latest_ordered:
        raise InvestmentError("Historical correction must not predate the latest event")
    by_id = {}
    replacements = {}
    superseded = set()
    corrections = []
    for source in prefix[:-1]:
        if source.get("type") != "correction":
            by_id[source["id"]] = source
            continue
        prior = by_id.get(source["original_event_id"])
        replacement = source["replacement_event"]
        if (prior is None or prior["id"] in superseded or
                prior.get("type") not in CORRECTABLE_EVENTS or
                replacement["type"] != prior["type"] or
                replacement["timestamp"] != prior["timestamp"] or
                replacement["id"] in by_id or replacement["id"] in replacements):
            raise InvestmentError("Correction target or replacement is not eligible")
        replacements[prior["id"]] = replacement
        superseded.add(prior["id"])
        by_id[replacement["id"]] = replacement
        corrections.append(source)

    original = by_id.get(correction["original_event_id"])
    replacement = correction["replacement_event"]
    if (original is None or original["id"] in superseded or
            original.get("type") not in CORRECTABLE_EVENTS or
            replacement["type"] != original["type"] or
            replacement["timestamp"] != original["timestamp"] or
            replacement["id"] in by_id or replacement["id"] in replacements):
        raise InvestmentError("Correction target or replacement is not eligible")
    replacements[original["id"]] = replacement
    superseded.add(original["id"])
    corrections.append(correction)

    effective = []
    for source in prefix:
        if source.get("type") == "correction":
            continue
        current = source
        visited = set()
        while current["id"] in replacements:
            if current["id"] in visited:
                raise InvestmentError("Correction chain contains a cycle")
            visited.add(current["id"])
            current = replacements[current["id"]]
        effective.append(current)

    rebuilt = replay(spec, effective, now)
    rebuilt.baseline_at = ledger.baseline_at
    rebuilt.baseline_value = ledger.baseline_value
    rebuilt.baseline_net = ledger.baseline_net
    rebuilt.baseline_scope_additions = ledger.baseline_scope_additions
    rebuilt.baseline_suppressed = ledger.baseline_suppressed or ledger.baseline_at is None
    rebuilt.historical_correction = True
    rebuilt.performance_valid = False
    rebuilt.correction_available_at = max(
        ledger.correction_available_at, clock(correction["available_at"]))
    rebuilt.last_time = max(rebuilt.last_time, clock(correction["timestamp"]))
    rebuilt.latest_available_at = max(
        rebuilt.latest_available_at, rebuilt.correction_available_at)
    rebuilt.revision = len(prefix)
    rebuilt.superseded_ids = superseded
    rebuilt.ids = {}
    for source in prefix:
        rebuilt.ids[source["id"]] = source.copy()
    for item in corrections:
        rebuilt.ids[item["replacement_event"]["id"]] = item["replacement_event"].copy()
    for point in rebuilt.valuation_history:
        point["gain_since_baseline_gbp"] = None
        point["twr_pct"] = None
        point["max_drawdown_pct"] = None
        point["gap_reason"] = (
            "Historical correction invalidated performance evidence; no new baseline was established.")
    return rebuilt


def replay(spec, events, now=None, on_revision=None):
    now = clock(now)
    if not isinstance(events, list) or len(events) > MAX_EVENTS:
        raise InvestmentError("Event count exceeds bound")
    with localcontext() as context:
        context.prec = 100
        try:
            ledger = Ledger(spec)
            if clock(spec["created_at"]) > now:
                raise InvestmentError("Future profile inception")
            if on_revision is not None:
                on_revision(ledger, None)
            previous = None
            history_relevant = False
            group_timestamp = None
            same_clock_income = False
            unpriced_flow_holdings = set()
            last_event = None
            last_reversible = None
            for index, value in enumerate(events):
                if isinstance(value, dict) and value.get("id") in ledger.ids:
                    event(value, ledger.spec, now)
                    if json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) != json.dumps(
                            ledger.ids[value["id"]], sort_keys=True, separators=(",", ":"), allow_nan=False):
                        raise InvestmentError("Conflicting duplicate event")
                    continue
                timestamp = clock(value.get("timestamp")) if isinstance(value, dict) else None
                if timestamp != group_timestamp:
                    group_timestamp = timestamp
                    same_clock_income = False
                    unpriced_flow_holdings = set()
                kind = value.get("type") if isinstance(value, dict) else None
                metadata_only = (isinstance(value, dict) and
                                 kind in ("savings_terms", "role_assignment", "profile_change"))
                valuation_event = (not metadata_only and isinstance(value, dict) and
                                   kind != "cash_observation")
                if isinstance(value, dict) and kind == "review_acknowledgement":
                    ledger.apply(value, now)
                    if on_revision is not None:
                        on_revision(ledger, previous)
                    last_event = value
                    last_reversible = None
                    continue
                if previous is not None and timestamp != previous:
                    ledger.checkpoint(previous)
                    if history_relevant:
                        ledger.record_valuation(previous)
                if kind == "correction":
                    ledger = _replay_corrected_prefix(
                        spec, events[:index + 1], value, ledger, now)
                    if on_revision is not None:
                        on_revision(ledger, previous)
                    last_event = value
                    last_reversible = None
                    previous = ledger.last_time
                    group_timestamp = previous
                    history_relevant = True
                    same_clock_income = False
                    unpriced_flow_holdings.clear()
                    continue
                if (spec["schema_version"] == 2 and kind in
                        ("contribution", "withdrawal")):
                    total, _ = ledger.valuation(timestamp)
                    if total is None:
                        unpriced_flow_holdings.update(
                            key for key, quantity in ledger.holdings.items() if quantity > 0)
                baseline_before = ledger.baseline_at if spec["schema_version"] == 2 else None
                reversible = None
                if (spec["schema_version"] == 2 and kind in CORRECTABLE_CASH_EVENTS):
                    reversible = {
                        "cash": ledger.cash[value["account_id"]],
                        "role_cash": ledger.role_cash.copy(),
                        "role_holdings": ledger.role_holdings.copy(),
                        "role_active": ledger.role_active,
                        "role_last_time": ledger.role_last_time,
                        "history_length": len(ledger.valuation_history),
                        "scalars": {
                            name: getattr(ledger, name) for name in (
                                "net", "fees", "revision", "last_time",
                                "cash_valuation_at", "latest_available_at", "units",
                                "unit_price", "peak", "drawdown", "performance_valid",
                                "baseline_at", "baseline_value", "baseline_net",
                            )
                        },
                    }
                ledger.apply(value, now)
                if (spec["schema_version"] == 2 and kind == "mark" and
                        (value["account_id"], value["product_id"]) in unpriced_flow_holdings):
                    raise InvestmentError(
                        "Same-clock marks for existing holdings must precede external cash flows")
                if (spec["schema_version"] == 2 and baseline_before is None and
                        ledger.baseline_at is not None and same_clock_income):
                    raise InvestmentError(
                        "Same-clock income must follow the first complete valuation baseline")
                if (spec["schema_version"] == 2 and kind in ("interest", "dividend") and
                        baseline_before is None and ledger.baseline_at is None):
                    same_clock_income = True
                if on_revision is not None:
                    on_revision(ledger, previous if metadata_only else timestamp)
                last_event = value
                last_reversible = reversible
                if not metadata_only:
                    if timestamp != previous:
                        history_relevant = valuation_event
                    elif valuation_event:
                        history_relevant = True
                    previous = timestamp
            if previous is not None:
                ledger.checkpoint(previous)
                if history_relevant:
                    ledger.record_valuation(previous)
            return ledger
        except (InvalidOperation, OverflowError, TypeError, KeyError) as exc:
            raise InvestmentError("Invalid accounting evidence") from exc
