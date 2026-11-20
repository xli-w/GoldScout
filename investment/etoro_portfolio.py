"""Read-only, bounded projection of eToro portfolio and GBP balance evidence."""

from datetime import datetime, timezone
from decimal import Decimal
import json
import re
import uuid

from . import etoro


_CURRENCY = re.compile(r"[A-Z]{3}")
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
    if type(value) is not str or not value.endswith("Z"):
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


def _decode(payload):
    if type(payload) is not bytes or not 0 < len(payload) <= etoro.MAX_BYTES:
        raise etoro.EToroError("payload_invalid")
    try:
        document = json.loads(
            payload.decode("utf-8"), parse_float=Decimal,
            parse_constant=lambda _: _invalid(), object_pairs_hook=etoro._pairs)
        etoro._bounded(document)
        return _object(document)
    except etoro.EToroError:
        raise
    except (ValueError, UnicodeError, RecursionError, ArithmeticError):
        raise etoro.EToroError("schema_invalid") from None


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
                      expected_account_id=None, before_balance=None):
    """Fetch one fixed portfolio GET and one fixed GBP balance GET without retries."""
    if environment not in etoro.AGGREGATE_PORTFOLIO_PATHS:
        raise etoro.EToroError("environment_invalid")
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
    received_at = (now or (lambda: datetime.now(timezone.utc)))()
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
    return portfolio
