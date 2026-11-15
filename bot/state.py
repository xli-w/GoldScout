"""Persisted portfolio state model and validation contract."""
import math
from dataclasses import dataclass, field, fields
from datetime import datetime
from typing import Optional

import config
from bot.ledger import Ledger, PersistenceError


@dataclass
class Position:
    symbol: str
    asset_class: str
    strategy: str
    side: str                       # "long" | "short"
    qty: float
    entry_price: float
    entry_time: str
    atr: float                      # refreshed every strategy evaluation
    hard_stop: float
    extreme_price: float            # highest (long) / lowest (short) price since entry
    trailing_mult: Optional[float] = None
    trailing_stop: Optional[float] = None
    stop_order_id: Optional[str] = None
    server_stop: Optional[float] = None   # stop price currently resting at the broker
    exit_order_id: Optional[str] = None
    exit_client_order_id: Optional[str] = None
    exit_filled_qty: float = 0.0
    exit_filled_notional: float = 0.0
    protection_verified: bool = False
    stop_filled_qty: float = 0.0
    stop_filled_notional: float = 0.0
    entry_fees: float = 0.0
    fees_estimated: bool = False
    exit_target: Optional[float] = None
    identity: str = ""
    exit_sequence: int = 0
    last_fill_time: Optional[str] = None
    last_fill_order_id: Optional[str] = None
    stop_group_id: Optional[str] = None
    stop_order_fills: dict = field(default_factory=dict)


def validate_state(data):
    def number(value, label, positive=False, integer=False):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise PersistenceError(f"Invalid {label}")
        if positive and value <= 0 or integer and (not isinstance(value, int) or value < 0):
            raise PersistenceError(f"Invalid {label} range or type")

    def timestamp(value, label):
        if not isinstance(value, str):
            raise PersistenceError(f"Invalid {label}")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise PersistenceError(f"Invalid {label}") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise PersistenceError(f"{label} requires timezone")

    def identity(value, label, optional=False, legacy_empty=False):
        if optional and value is None:
            return
        if not isinstance(value, str) or not legacy_empty and not value.strip():
            raise PersistenceError(f"Invalid {label}")

    if not isinstance(data, dict):
        raise PersistenceError("State must be an object")
    for name in ("positions", "entry_intents", "last_bar_ts", "daily", "daily_controls"):
        if name in data and not isinstance(data[name], dict):
            raise PersistenceError(f"Invalid state section {name}")
    Ledger.encode(data)
    for client_id, intent in data.get("entry_intents", {}).items():
        identity(client_id, "entry client ID")
        if not isinstance(client_id, str) or not isinstance(intent, dict) or not all(
                key in intent for key in ("symbol", "side", "atr", "price", "strategy", "trailing_mult")):
            raise PersistenceError("Invalid entry intent")
        if intent["symbol"] not in config.INSTRUMENTS or intent["side"] not in ("long", "short"):
            raise PersistenceError("Invalid entry ownership")
        instrument = config.INSTRUMENTS[intent["symbol"]]
        if intent["strategy"] != instrument.strategy or intent["side"] == "short" and not instrument.allow_short:
            raise PersistenceError("Invalid entry strategy or short permission")
        if intent["trailing_mult"] is not None:
            number(intent["trailing_mult"], "entry trailing multiplier", positive=True)
        if not all(isinstance(intent[key], (int, float)) and not isinstance(intent[key], bool)
                   and math.isfinite(intent[key]) and intent[key] > 0 for key in ("atr", "price")):
            raise PersistenceError("Invalid entry intent numbers")
    daily = data.get("daily", {})
    for key in ("start_equity", "last_equity", "realized", "trades"):
        value = daily.get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
            raise PersistenceError("Invalid daily numeric value")
    if daily.get("day") is not None:
        datetime.strptime(daily["day"], "%Y-%m-%d")
    for key in ("start_equity", "last_equity"):
        if daily.get(key) is not None:
            number(daily[key], key, positive=True)
    if "trades" in daily:
        number(daily["trades"], "daily trades", integer=True)
    controls = data.get("daily_controls", {})
    for key in ("halted", "accounting_unresolved"):
        if key in controls and not isinstance(controls[key], bool):
            raise PersistenceError("Invalid daily control flag")
    if "cash_flow_ids" in controls and (not isinstance(controls["cash_flow_ids"], list) or
                                         not all(isinstance(value, str) for value in controls["cash_flow_ids"])):
        raise PersistenceError("Invalid cash-flow IDs")
    if "cash_flow" in controls:
        number(controls["cash_flow"], "cash flow")
    cash_ids = controls.get("cash_flow_ids", [])
    if any(not value.strip() for value in cash_ids) or len(cash_ids) != len(set(cash_ids)):
        raise PersistenceError("Cash-flow IDs must be unique and nonempty")
    for symbol, value in data.get("last_bar_ts", {}).items():
        if symbol not in config.INSTRUMENTS:
            raise PersistenceError("Unknown processed-bar symbol")
        timestamp(value, "processed bar timestamp")
    names = {state_field.name for state_field in fields(Position)}
    for symbol, payload in data.get("positions", {}).items():
        if not isinstance(payload, dict):
            raise PersistenceError("Invalid position payload")
        position = Position(**{key: value for key, value in payload.items() if key in names})
        if set(payload) - names:
            raise PersistenceError("Position has unknown fields; explicit schema migration required")
        if position.symbol != symbol or position.side not in ("long", "short") or position.asset_class not in ("equity", "crypto"):
            raise PersistenceError("Invalid position identity or side")
        instrument = config.INSTRUMENTS.get(symbol)
        if instrument is None or instrument.asset_class != position.asset_class or instrument.strategy != position.strategy \
                or position.side == "short" and not instrument.allow_short:
            raise PersistenceError("Invalid configured position ownership")
        for key in ("trailing_mult", "trailing_stop", "server_stop", "exit_target"):
            value = getattr(position, key)
            if value is not None:
                number(value, key, positive=True)
        for key in ("protection_verified", "fees_estimated"):
            if not isinstance(getattr(position, key), bool):
                raise PersistenceError(f"Invalid {key} flag")
        for key in ("stop_order_id", "stop_group_id", "exit_order_id", "exit_client_order_id", "last_fill_order_id"):
            identity(getattr(position, key), key, optional=True)
        identity(position.identity, "position identity", legacy_empty=True)
        number(position.exit_sequence, "exit sequence", integer=True)
        if position.last_fill_time is not None:
            timestamp(position.last_fill_time, "last fill timestamp")
        for value in (position.qty, position.entry_price, position.atr, position.hard_stop, position.extreme_price):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise PersistenceError("Invalid position numeric value")
        for key in ("exit_filled_qty", "exit_filled_notional", "stop_filled_qty", "stop_filled_notional", "entry_fees", "exit_sequence"):
            value = getattr(position, key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise PersistenceError("Invalid fill counter or fees")
        for prefix in ("exit", "stop"):
            qty = getattr(position, prefix + "_filled_qty")
            notional = getattr(position, prefix + "_filled_notional")
            if (qty == 0) != (notional == 0):
                raise PersistenceError("Cumulative fill quantity and notional must both be zero or positive")
        if position.stop_group_id is not None and not isinstance(position.stop_group_id, str):
            raise PersistenceError("Invalid protective group ID")
        if not isinstance(position.stop_order_fills, dict):
            raise PersistenceError("Invalid protective order fill history")
        for order_id, counters in position.stop_order_fills.items():
            if not isinstance(order_id, str) or not order_id or not isinstance(counters, dict):
                raise PersistenceError("Invalid protective order fill identity")
            for key in ("qty", "notional"):
                value = counters.get(key)
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                    raise PersistenceError("Invalid protective order cumulative value")
            if (counters["qty"] == 0) != (counters["notional"] == 0):
                raise PersistenceError("Per-order fill quantity/notional relationship invalid")
        timestamp(position.entry_time, "entry timestamp")
        active = position.stop_order_fills.get(position.stop_order_id)
        if active is not None and (not math.isclose(active["qty"], position.stop_filled_qty, abs_tol=1e-8)
                                  or not math.isclose(active["notional"], position.stop_filled_notional, abs_tol=1e-8)):
            raise PersistenceError("Active stop counters disagree with per-order history")
