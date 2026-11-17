"""Synthetic-only order recovery trace; not an execution adapter."""

from decimal import Context, Decimal, DivisionByZero, InvalidOperation, Overflow, localcontext

from . import InvestmentError
from .core import explicit_clock, fields, fmt, number, text


_INTENT_FIELDS = (
    "type", "at", "intent_id", "approval_id", "portfolio_id", "account_id",
    "product_id", "role", "currency", "quantity", "max_spend_gbp",
    "policy_id", "portfolio_revision", "expires_at",
)


def simulate_pilot_trace(events):
    """Replay one hypothetical buy using synthetic acknowledgements and observations.

    This function performs no submission, persistence, approval verification,
    broker reconciliation, cash accounting, or integration with other stores.
    Input evidence is caller-supplied and is not verified to be synthetic.
    """
    context = Context(prec=100, traps=[InvalidOperation, DivisionByZero, Overflow])
    with localcontext(context):
        return _simulate(events)


def _simulate(events):
    if type(events) is not list or not 1 <= len(events) <= 256:
        raise InvestmentError("A bounded, nonempty synthetic event trace is required")
    state = "empty"
    intent = None
    quantity = Decimal(0)
    filled = Decimal(0)
    order_id = None
    attempts = 0
    previous_at = None
    deltas = []
    observed = False
    for event in events:
        if not isinstance(event, dict):
            raise InvestmentError("Invalid synthetic event")
        kind = event.get("type")
        at = explicit_clock(event.get("at"))
        if previous_at is not None and at < previous_at:
            raise InvestmentError("Synthetic event clocks must be ordered")
        previous_at = at
        if kind == "intent":
            fields(event, _INTENT_FIELDS)
            if state != "empty":
                raise InvestmentError("Only one synthetic intent is supported")
            for name in ("intent_id", "approval_id", "portfolio_id", "account_id",
                         "product_id", "policy_id"):
                text(event[name])
            if (event["currency"] != "GBP" or event["role"] not in ("core", "dynamic")
                    or type(event["portfolio_revision"]) is not int
                    or event["portfolio_revision"] < 0):
                raise InvestmentError("Invalid synthetic scope or portfolio revision")
            quantity = number(event["quantity"], positive=True)
            number(event["max_spend_gbp"], positive=True)
            if explicit_clock(event["expires_at"]) <= at:
                raise InvestmentError("Synthetic approval is expired")
            intent = event
            state = "intent_staged"
        elif kind == "attempt":
            fields(event, ("type", "at", "intent_id", "policy_id", "portfolio_revision"))
            if (state != "intent_staged" or event["intent_id"] != intent["intent_id"]
                    or event["policy_id"] != intent["policy_id"]
                    or type(event["portfolio_revision"]) is not int
                    or event["portfolio_revision"] != intent["portfolio_revision"]
                    or at >= explicit_clock(intent["expires_at"])):
                raise InvestmentError("Synthetic attempt lacks unchanged, unexpired approval")
            attempts += 1
            state = "awaiting_response"
        elif kind == "acknowledgement":
            fields(event, ("type", "at", "order_id", "account_id", "product_id",
                           "side", "quantity"))
            if state != "awaiting_response":
                raise InvestmentError("Synthetic acknowledgement has no unambiguous attempt")
            if (event["account_id"] != intent["account_id"]
                    or event["product_id"] != intent["product_id"]
                    or event["side"] != "buy"
                    or number(event["quantity"], positive=True) != quantity):
                raise InvestmentError("Synthetic acknowledgement conflicts with the intent scope")
            order_id = text(event["order_id"])
            state = "acknowledged"
        elif kind == "uncertain":
            fields(event, ("type", "at"))
            if state != "awaiting_response":
                raise InvestmentError("Synthetic uncertainty requires an awaiting attempt")
            state = "manual_review"
        elif kind == "restart":
            fields(event, ("type", "at"))
            if state == "empty":
                raise InvestmentError("Synthetic restart requires an intent")
            if state == "awaiting_response":
                state = "manual_review"
        elif kind == "observation":
            fields(event, ("type", "at", "order_id", "status", "filled_quantity"))
            if state not in ("acknowledged", "new", "partial", "filled", "cancelled"):
                raise InvestmentError("Synthetic observation has no acknowledged order")
            if event["order_id"] != order_id:
                raise InvestmentError("Synthetic order ownership is unknown")
            status = event["status"]
            cumulative = number(event["filled_quantity"])
            if (status not in ("new", "partial", "filled", "cancelled")
                    or cumulative < filled or cumulative > quantity
                    or status == "new" and cumulative != 0
                    or status == "partial" and not 0 < cumulative < quantity
                    or status == "filled" and cumulative != quantity
                    or status == "cancelled" and cumulative >= quantity
                    or state in ("filled", "cancelled") and (status != state or cumulative != filled)
                    or state == "partial" and status == "new"):
                raise InvestmentError("Inconsistent synthetic cumulative fill observation")
            if cumulative > filled:
                deltas.append(fmt(cumulative - filled))
                filled = cumulative
            observed = True
            state = status
        else:
            raise InvestmentError("Unsupported synthetic event type")
    if state == "awaiting_response":
        state = "manual_review"
    return {
        "synthetic_only": True, "execution_enabled": False, "state": state,
        "submission_attempts": attempts,
        "observed_filled_quantity": fmt(filled) if observed else None,
        "observed_unfilled_quantity": fmt(quantity - filled) if observed else None,
        "fill_deltas": deltas, "ownership_verified": False,
        "funding_check": "unavailable", "spend_cap_check": "unavailable",
        "limitations": [
            "All events, approval and order evidence are caller-supplied synthetic declarations.",
            "No venue calls, durable intent, cash or fill accounting, order ownership verification or policy authority are implemented.",
            "An ambiguous submission cannot be retried or resolved by matching an order in this model.",
        ],
    }
