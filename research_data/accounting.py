"""Synthetic, cash-and-quantity-only accounting contract for GBP fills."""

import re
from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import MAX_EMAX, MIN_EMIN, Decimal, localcontext

from research_data.dataset import RFC3339, SAFE_ID

MAX_EVENTS = 100_000
MAX_DECIMAL_TEXT = 128
MAX_SIGNIFICANT_DIGITS = 64
ARITHMETIC_PRECISION = MAX_SIGNIFICANT_DIGITS * 4 + len(str(MAX_EVENTS))
DECIMAL_TEXT = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
SUBMICROSECOND = re.compile(r"\.\d{6}(\d+)(?:Z|[+-]\d{2}:\d{2})$")
FILL_FIELDS = {
    "event_id", "timestamp", "sequence", "product_id", "side", "quantity",
    "price_gbp", "fees_gbp", "currency", "price_unit",
}


class CampaignAccountingError(ValueError):
    """Malformed or cash/quantity-inconsistent synthetic GBP fill ledger."""


def _decimal(value, where, *, positive):
    if not isinstance(value, str) or len(value) > MAX_DECIMAL_TEXT or not DECIMAL_TEXT.fullmatch(value):
        raise CampaignAccountingError(f"{where} must be a non-negative, non-exponent decimal string")
    if sum(character.isdigit() for character in value) > MAX_SIGNIFICANT_DIGITS:
        raise CampaignAccountingError(f"{where} exceeds {MAX_SIGNIFICANT_DIGITS} digits")
    result = Decimal(value)
    if positive and result <= 0:
        raise CampaignAccountingError(f"{where} must be greater than zero")
    return result


def _format(value):
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _timestamp(value, where):
    if not isinstance(value, str) or not RFC3339.fullmatch(value):
        raise CampaignAccountingError(f"{where} must be RFC3339 with an explicit UTC offset")
    fractional = SUBMICROSECOND.search(value)
    if fractional and fractional.group(1).strip("0"):
        raise CampaignAccountingError(f"{where} has unsupported non-zero sub-microsecond precision")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CampaignAccountingError(f"{where} must be a valid timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CampaignAccountingError(f"{where} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _parse_fill(event, index):
    where = f"events[{index}]"
    if not isinstance(event, Mapping):
        raise CampaignAccountingError(f"{where} must be an object")
    missing = FILL_FIELDS - set(event)
    extra = set(event) - FILL_FIELDS
    if missing or extra:
        details = []
        if missing:
            details.append("missing " + ", ".join(sorted(missing)))
        if extra:
            details.append("unsupported " + ", ".join(sorted(extra)))
        raise CampaignAccountingError(f"{where}: " + "; ".join(details))
    for field in ("event_id", "product_id"):
        value = event[field]
        if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
            raise CampaignAccountingError(f"{where}.{field} must be a safe identifier")
    if type(event["sequence"]) is not int or event["sequence"] < 0:
        raise CampaignAccountingError(f"{where}.sequence must be a non-negative integer")
    if event["side"] not in ("buy", "sell"):
        raise CampaignAccountingError(f"{where}.side must be buy or sell")
    if event["currency"] != "GBP" or event["price_unit"] != "GBP_PER_UNIT":
        raise CampaignAccountingError(f"{where} must use explicitly GBP-normalized prices")
    return {
        "event_id": event["event_id"],
        "timestamp": _timestamp(event["timestamp"], where + ".timestamp"),
        "sequence": event["sequence"],
        "product_id": event["product_id"],
        "side": event["side"],
        "quantity": _decimal(event["quantity"], where + ".quantity", positive=True),
        "price_gbp": _decimal(event["price_gbp"], where + ".price_gbp", positive=True),
        "fees_gbp": _decimal(event["fees_gbp"], where + ".fees_gbp", positive=False),
    }


def account_fills(initial_cash_gbp, fills):
    """Apply ordered synthetic fills; return cash/quantities, never equity or returns.

    The caller supplies already GBP-normalized fill prices and explicit fees.
    No cash accrual, corporate actions, external marks, risk sizing or persistence
    is implemented by this contract.
    """
    initial_cash = _decimal(initial_cash_gbp, "initial_cash_gbp", positive=True)
    if not isinstance(fills, (list, tuple)) or len(fills) > MAX_EVENTS:
        raise CampaignAccountingError(f"fills must be a list or tuple of at most {MAX_EVENTS} events")
    events = [_parse_fill(event, index) for index, event in enumerate(fills)]
    event_ids = set()
    sequences = set()
    previous_key = None
    for event in events:
        timestamp = event["timestamp"]
        event_id = event["event_id"]
        sequence = event["sequence"]
        if event_id in event_ids:
            raise CampaignAccountingError(f"duplicate event_id {event_id!r}")
        event_ids.add(event_id)
        sequence_key = (timestamp, sequence)
        if sequence_key in sequences:
            raise CampaignAccountingError(f"duplicate sequence {sequence} at {timestamp.isoformat()}")
        sequences.add(sequence_key)
        if previous_key is not None and sequence_key <= previous_key:
            raise CampaignAccountingError("fills must already be ordered by timestamp and sequence")
        previous_key = sequence_key

    cash = initial_cash
    positions = {}
    total_fees = Decimal("0")
    gross_notional = Decimal("0")
    output_events = []
    with localcontext() as context:
        context.prec = ARITHMETIC_PRECISION
        context.Emax = MAX_EMAX
        context.Emin = MIN_EMIN
        for event in events:
            quantity = event["quantity"]
            price = event["price_gbp"]
            fees = event["fees_gbp"]
            notional = quantity * price
            product_id = event["product_id"]
            current_quantity = positions.get(product_id, Decimal("0"))
            if event["side"] == "buy":
                debit = notional + fees
                if debit > cash:
                    raise CampaignAccountingError(
                        f"{event['event_id']}: buy debit exceeds available GBP cash"
                    )
                cash -= debit
                positions[product_id] = current_quantity + quantity
            else:
                if quantity > current_quantity:
                    raise CampaignAccountingError(
                        f"{event['event_id']}: sell quantity exceeds the long position"
                    )
                proceeds = notional - fees
                if cash + proceeds < 0:
                    raise CampaignAccountingError(
                        f"{event['event_id']}: sell fees would make GBP cash negative"
                    )
                cash += proceeds
                remaining = current_quantity - quantity
                if remaining:
                    positions[product_id] = remaining
                else:
                    positions.pop(product_id, None)
            total_fees += fees
            gross_notional += notional
            output_events.append({
                "event_id": event["event_id"],
                "timestamp": event["timestamp"].isoformat().replace("+00:00", "Z"),
                "sequence": event["sequence"],
                "product_id": product_id,
                "side": event["side"],
                "quantity": _format(quantity),
                "price_gbp": _format(price),
                "notional_gbp": _format(notional),
                "fees_gbp": _format(fees),
                "cash_balance_gbp": _format(cash),
                "position_quantity": _format(positions.get(product_id, Decimal("0"))),
            })
    return {
        "schema_version": 1,
        "scope": "synthetic_gbp_long_only_fill_accounting",
        "accounting_basis": "cash_and_quantities_only",
        "initial_cash_gbp": _format(initial_cash),
        "cash_balance_gbp": _format(cash),
        "positions": [
            {"product_id": product_id, "quantity": _format(quantity)}
            for product_id, quantity in sorted(positions.items())
        ],
        "total_fees_gbp": _format(total_fees),
        "gross_traded_notional_gbp": _format(gross_notional),
        "events": output_events,
        "supports_cash_accrual": False,
        "supports_corporate_actions": False,
        "supports_equity_valuation": False,
        "supports_performance_claims": False,
    }
