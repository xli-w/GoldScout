"""Read-only, bounded projection of eToro portfolio and GBP balance evidence."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import re
import uuid

from . import etoro


_CURRENCY = re.compile(r"[A-Z]{3}")
_UTC_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?Z")
_HISTORY_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})")
_MONEY_FIELDS = (
    "accountAvailableCash", "accountFrozenCash", "accountCurrentPnl",
    "accountTotalValue", "accountTotalUsedMargin", "accountBalance",
)
_INSTRUMENT_FIELDS = (
    "totalMarginAccountCurrency", "totalFees", "totalFeesAcctCcy", "totalTaxes",
    "totalTaxesAcctCcy", "totalMarginAssetCurrency", "pnlAssetCurrency",
    "accountCurrencyRoePercent", "netContracts", "netUnits",
    "netCurrentExposureAssetCurrency", "netCurrentExposureAccountCurrency",
    "netInitialExposureAccountCurrency", "accountCurrencyReturn",
    "liquidationValueAccountCurrency", "liquidationValueAssetCurrency",
    "avgLeverage", "avgOpenRate", "netAvgOpenRate", "avgConversionRate",
)
_MIRROR_FIELDS = (
    "mirrorAvailableCash", "mirrorDepositTotal", "mirrorWithdrawalTotal",
    "mirrorStopLossPercentage", "mirrorStopLoss", "mirrorClosedPositionsPnl",
)
_MIRROR_TOTAL_FIELDS = (
    "mirrorNetFunding", "mirrorPositionsPnl", "mirrorLiquidationValue",
    "mirrorPositionsPnlPercent", "mirrorMarginPercent", "mirrorValuePercent",
    "mirrorActiveMargin",
)
_MAX_ITEMS = 1000
_CLOSED_HISTORY_PAGE_SIZE = 1000
_CLOSED_HISTORY_MAX_ITEMS = 1000
_CLOSED_HISTORY_MAX_PAGES = _CLOSED_HISTORY_MAX_ITEMS // _CLOSED_HISTORY_PAGE_SIZE
_CLOSED_TRADE_NUMBERS = (
    "netProfit", "closeRate", "leverage", "openRate", "stopLossRate",
    "takeProfitRate", "investment", "initialInvestment", "fees", "units",
)
_CLOSED_TRADE_IDS = (
    "positionId", "instrumentId", "orderId", "socialTradeId", "parentPositionId",
)
_CLOSED_TRADE_TIMESTAMPS = ("closeTimestamp", "openTimestamp")


def _invalid():
    raise etoro.EToroError("schema_invalid")


def _number(value, *, nullable=False):
    if nullable and value is None:
        return None
    if type(value) not in (int, Decimal):
        _invalid()
    number = Decimal(value)
    if (not number.is_finite() or abs(number) > Decimal("1e18")
            or number.as_tuple().exponent < -18 or len(number.as_tuple().digits) > 40):
        _invalid()
    return format(number, "f")


def _identifier(value):
    if type(value) is not int or not 1 <= value <= 2**63 - 1:
        _invalid()
    return value


def _currency(value):
    if type(value) is not str or _CURRENCY.fullmatch(value) is None:
        _invalid()
    return value


def _timestamp(value):
    if type(value) is not str or _UTC_TIMESTAMP.fullmatch(value) is None:
        _invalid()
    try:
        result = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        _invalid()
    if result.utcoffset() != timezone.utc.utcoffset(result):
        _invalid()
    return result.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _object(value):
    if type(value) is not dict:
        _invalid()
    return value


def _array(value):
    if type(value) is not list or len(value) > _MAX_ITEMS:
        _invalid()
    if any(type(item) is not dict for item in value):
        _invalid()
    return value


def _decode_document(payload, *, node_budget=20000):
    if type(payload) is not bytes or not 0 < len(payload) <= etoro.MAX_BYTES:
        raise etoro.EToroError("payload_invalid")
    try:
        document = json.loads(
            payload.decode("utf-8"), parse_float=Decimal,
            parse_constant=lambda _: _invalid(), object_pairs_hook=etoro._pairs)
        etoro._bounded(document, budget=[node_budget])
        return document
    except etoro.EToroError:
        raise
    except (ValueError, UnicodeError, RecursionError, ArithmeticError):
        raise etoro.EToroError("schema_invalid") from None


def _decode(payload):
    return _object(_decode_document(payload))


def _history_timestamp(value):
    if type(value) is not str or _HISTORY_TIMESTAMP.fullmatch(value) is None:
        _invalid()
    try:
        source = datetime.fromisoformat(
            value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        _invalid()
    if source.tzinfo is None or source.utcoffset() is None:
        _invalid()
    return source.astimezone(timezone.utc).isoformat(
        timespec="microseconds").replace("+00:00", "Z")


def _closed_trade(value):
    item = _object(value)
    if any(field not in item for field in (
            "positionId", "instrumentId", "closeTimestamp", "isBuy", "units")):
        _invalid()
    result = {}
    for field in _CLOSED_TRADE_IDS:
        if field in item:
            result[field] = _identifier(item[field])
    for field in _CLOSED_TRADE_NUMBERS:
        if field in item:
            result[field] = _number(
                item[field], nullable=field in ("stopLossRate", "takeProfitRate"))
    for field in _CLOSED_TRADE_TIMESTAMPS:
        if field in item:
            result[field] = _history_timestamp(item[field])
    for field in ("isBuy", "trailingStopLoss"):
        if field in item:
            if type(item[field]) is not bool:
                _invalid()
            result[field] = item[field]
    if ("openTimestamp" in result
            and result["openTimestamp"] > result["closeTimestamp"]):
        _invalid()
    return result


def normalize_closed_trade_page(payload):
    """Project one official closed-trade history page without deriving accounting."""
    document = _decode_document(payload, node_budget=50000)
    if type(document) is not list or len(document) > _CLOSED_HISTORY_PAGE_SIZE:
        _invalid()
    return [_closed_trade(item) for item in document]


def _history_clock(now):
    try:
        value = now()
    except (OSError, RuntimeError):
        raise etoro.EToroError("clock_unavailable") from None
    if (type(value) is not datetime or value.utcoffset() is None
            or value.utcoffset() != timezone.utc.utcoffset(value)):
        raise etoro.EToroError("clock_unavailable")
    return value.astimezone(timezone.utc)


def _history_result(status, *, from_date, through_date, received_at,
                    trades=(), error_code=None):
    result = {
        "status": status,
        "from_date": from_date,
        "through_date": through_date,
        "received_at": (None if received_at is None else received_at.isoformat(
            timespec="microseconds").replace("+00:00", "Z")),
        "trades": list(trades),
    }
    if error_code is not None:
        result["error_code"] = error_code
    return result


def _collect_closed_trade_history(transport, credentials, environment, now, before_request):
    requested_at = _history_clock(now)
    from_date = (requested_at.date() - timedelta(days=364)).isoformat()
    through_date = requested_at.date().isoformat()
    first_date = datetime.combine(
        datetime.fromisoformat(from_date).date(), datetime.min.time(), timezone.utc)
    path_prefix = etoro.CLOSED_TRADE_HISTORY_PATHS[environment]
    trades = []
    last_received_at = None

    def request_page(page):
        nonlocal through_date, last_received_at
        if before_request is not None:
            before_request()
        path = (f"{path_prefix}?minDate={from_date}&page={page}"
                f"&pageSize={_CLOSED_HISTORY_PAGE_SIZE}")
        headers = {**credentials, "x-request-id": str(uuid.uuid4())}
        if not etoro._headers_valid(headers):
            raise etoro.EToroError("credentials_unavailable")
        try:
            payload = transport(path, headers)
        except etoro.EToroError as exc:
            if exc.code in ("approval_interrupted", "cancellation_unconfirmed"):
                raise
            if exc.code in etoro._TRANSPORT_CODES:
                raise
            raise etoro.EToroError("collection_unavailable") from None
        except Exception:
            raise etoro.EToroError("collection_unavailable") from None
        received_at = _history_clock(now)
        if received_at < requested_at or (
                last_received_at is not None and received_at < last_received_at):
            last_received_at = None
            raise etoro.EToroError("clock_unavailable")
        last_received_at = received_at
        page_trades = normalize_closed_trade_page(payload)
        through_date = max(through_date, received_at.date().isoformat())
        if any(not first_date <= datetime.fromisoformat(
                    trade["closeTimestamp"].replace("Z", "+00:00")) <= received_at
               for trade in page_trades):
            raise etoro.EToroError("history_date_out_of_range")
        return page_trades

    for page in range(1, _CLOSED_HISTORY_MAX_PAGES + 1):
        try:
            page_trades = request_page(page)
        except etoro.EToroError as exc:
            if exc.code in ("approval_interrupted", "cancellation_unconfirmed"):
                raise
            return _history_result(
                "unavailable", from_date=from_date, through_date=through_date,
                received_at=last_received_at, error_code=exc.code)
        trades.extend(page_trades)
        if len(page_trades) < _CLOSED_HISTORY_PAGE_SIZE:
            trades.sort(key=lambda item: item["closeTimestamp"], reverse=True)
            return _history_result(
                "available", from_date=from_date, through_date=through_date,
                received_at=last_received_at, trades=trades)

    try:
        final_page = request_page(_CLOSED_HISTORY_MAX_PAGES + 1)
    except etoro.EToroError as exc:
        if exc.code in ("approval_interrupted", "cancellation_unconfirmed"):
            raise
        return _history_result(
            "unavailable", from_date=from_date, through_date=through_date,
            received_at=last_received_at, error_code=exc.code)
    if final_page:
        return _history_result(
            "unavailable", from_date=from_date, through_date=through_date,
            received_at=last_received_at, error_code="history_limit_exceeded")
    trades.sort(key=lambda item: item["closeTimestamp"], reverse=True)
    return _history_result(
        "available", from_date=from_date, through_date=through_date,
        received_at=last_received_at, trades=trades)


def _instrument(value):
    item = _object(value)
    result = {
        "instrument_id": _identifier(item.get("instrumentId")),
        "asset_currency": _currency(item.get("assetCurrency")),
    }
    for field in _INSTRUMENT_FIELDS:
        if field in item:
            result[field] = _number(item[field], nullable=field == "pnlAssetCurrency")
    for field in ("netUnits", "netContracts", "netCurrentExposureAssetCurrency",
                  "netCurrentExposureAccountCurrency", "avgLeverage"):
        if field not in item:
            _invalid()
    return result


def normalize_portfolio(payload, environment):
    """Keep only documented fields; never infer instrument type or position legs."""
    if environment not in ("real", "demo"):
        raise etoro.EToroError("environment_invalid")
    document = _decode(payload)
    required = ("cid", "timestamp", "accountCurrency", "accountTotals",
                "instrumentAggregates", "mirrors")
    if any(name not in document for name in required):
        _invalid()

    account_id = _identifier(document["cid"])
    account_currency = _currency(document["accountCurrency"])
    totals = _object(document["accountTotals"])
    account_totals = {}
    for field in _MONEY_FIELDS:
        if field not in totals:
            _invalid()
        account_totals[field] = _number(totals[field])

    direct = [_instrument(item) for item in _array(document["instrumentAggregates"])]
    if len({item["instrument_id"] for item in direct}) != len(direct):
        _invalid()

    mirrors = []
    seen_mirrors = set()
    for value in _array(document["mirrors"]):
        mirror = _object(value)
        mirror_id = _identifier(mirror.get("mirrorId"))
        if mirror_id in seen_mirrors:
            _invalid()
        seen_mirrors.add(mirror_id)
        item = {"mirror_id": mirror_id, "positions": [
            _instrument(position) for position in _array(mirror.get("instrumentAggregates"))
        ]}
        if len({position["instrument_id"] for position in item["positions"]}) != len(item["positions"]):
            _invalid()
        for field in _MIRROR_FIELDS:
            if field in mirror:
                item[field] = _number(mirror[field])
        mirror_totals = mirror.get("mirrorTotals")
        if mirror_totals is not None:
            totals_value = _object(mirror_totals)
            item["totals"] = {}
            for field in _MIRROR_TOTAL_FIELDS:
                if field in totals_value:
                    item["totals"][field] = _number(totals_value[field])
        mirrors.append(item)

    return {
        "schema_version": 1,
        "source": "etoro",
        "environment": environment,
        "account_id": account_id,
        "source_as_of": _timestamp(document["timestamp"]),
        "account_currency": account_currency,
        "account_totals": account_totals,
        "direct_positions": direct,
        "mirrors": mirrors,
        "coverage": {
            "provider_snapshot": "complete_as_documented",
            "positions": "aggregated_by_instrument",
            "copy_positions": "separate_mirror_aggregates",
            "instrument_type": "not_provided",
            "individual_position_legs": "not_provided",
            "pending_orders": "reserved_cash_total_only",
            "currency": "account_and_asset_currency_values",
        },
    }


def normalize_gbp_balance(payload, account_id, account_currency, received_at):
    """Retain the provider's GBP balance separately from the portfolio snapshot."""
    document = _decode(payload)
    entries = _array(document.get("balances"))
    if len(entries) != 1:
        _invalid()
    item = _object(entries[0])
    if (item.get("accountId") != str(account_id)
            or item.get("accountType") != "Trading"
            or item.get("currency") != account_currency
            or item.get("displayCurrency") != "GBP"):
        _invalid()
    if not all(name in item for name in ("balance", "displayBalance", "exchangeRate")):
        _invalid()
    return {
        "status": "provider_reported",
        "amount_gbp": _number(item["displayBalance"]),
        "source_balance": _number(item["balance"]),
        "source_currency": account_currency,
        "display_currency": "GBP",
        "exchange_rate": _number(item["exchangeRate"]),
        "source_as_of": None,
        "received_at": received_at,
        "meaning": "provider_total_account_value_not_spendable_cash",
    }


def collect_portfolio(transport, credential_accessor, environment, *, now=None,
                      expected_account_id=None, before_balance=None,
                      include_closed_history=False, before_history=None):
    """Fetch one fixed portfolio GET and one fixed GBP balance GET without retries."""
    if environment not in etoro.AGGREGATE_PORTFOLIO_PATHS:
        raise etoro.EToroError("environment_invalid")
    if type(include_closed_history) is not bool:
        raise etoro.EToroError("history_permission_required")
    now = now or (lambda: datetime.now(timezone.utc))
    try:
        credentials = credential_accessor()
        headers = {**credentials, "x-request-id": str(uuid.uuid4())}
        if not etoro._headers_valid(headers):
            raise ValueError
    except Exception:
        raise etoro.EToroError("credentials_unavailable") from None

    portfolio_path = etoro.AGGREGATE_PORTFOLIO_PATHS[environment]
    try:
        portfolio = normalize_portfolio(transport(portfolio_path, headers), environment)
    except etoro.EToroError:
        raise
    except Exception:
        raise etoro.EToroError("collection_unavailable") from None

    if expected_account_id is not None and portfolio["account_id"] != expected_account_id:
        raise etoro.EToroError("account_binding_changed")
    if before_balance is not None:
        before_balance()

    balance_path = (
        f"/api/v1/balances/trading/{portfolio['account_id']}"
        "?displayCurrency=GBP&expand=equityDetails")
    balance_payload = None
    error_code = None
    try:
        balance_payload = transport(balance_path, {
            **credentials, "x-request-id": str(uuid.uuid4())})
    except etoro.EToroError as exc:
        error_code = exc.code
    except Exception:
        error_code = "collection_unavailable"
    received_at = now()
    if not isinstance(received_at, datetime) or received_at.utcoffset() != timezone.utc.utcoffset(received_at):
        raise etoro.EToroError("clock_unavailable")
    received_at = received_at.isoformat(timespec="microseconds").replace("+00:00", "Z")
    if error_code is None:
        try:
            portfolio["gbp_balance"] = normalize_gbp_balance(
                balance_payload, portfolio["account_id"], portfolio["account_currency"], received_at)
        except etoro.EToroError as exc:
            error_code = exc.code
    if error_code is not None:
        portfolio["gbp_balance"] = {
            "status": "unavailable", "error_code": error_code,
            "source_as_of": None, "received_at": received_at,
        }
    closed_history = {
        "status": "not_requested", "from_date": None, "through_date": None,
        "received_at": None, "trades": [],
    }
    if include_closed_history:
        closed_history = _collect_closed_trade_history(
            transport, credentials, environment, now, before_history)
    portfolio["closed_trade_history"] = closed_history
    return portfolio
