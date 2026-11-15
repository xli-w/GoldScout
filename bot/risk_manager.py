"""
Risk management: ATR position sizing, stop calculation, correlation filter,
and the daily loss circuit-breaker.

Core rule: qty = (1% of equity) / ATR, so a 1 ATR adverse move costs 1% of equity.
Quiet instruments get bigger positions, volatile ones smaller -> constant risk.
"""
import logging
import math
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import config

log = logging.getLogger(__name__)


@dataclass
class SizeResult:
    qty: float
    notional: float
    risk_at_1atr: float   # dollars lost if price moves 1 ATR against the position
    note: str = ""


class RiskManager:
    def __init__(self, settings: dict = None):
        s = settings or config.RISK
        for key, value in s.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"Risk setting {key} must be finite and nonnegative")
        for key in ("risk_per_trade_pct", "max_daily_loss_pct", "crypto_stop_limit_buffer"):
            if not 0 < s[key] < 1:
                raise ValueError(f"{key} must lie strictly between zero and one")
        for key in ("crypto_taker_fee", "crypto_maker_fee"):
            if key in s and s[key] >= 1:
                raise ValueError(f"{key} must be below one")
        for key in ("atr_period", "hard_stop_atr_mult", "max_position_pct_of_equity", "max_gross_exposure_pct"):
            if s[key] <= 0:
                raise ValueError(f"{key} must be positive")
        if not isinstance(s["atr_period"], int):
            raise ValueError("ATR period must be an integer")
        self.risk_pct = s["risk_per_trade_pct"]
        self.atr_period = s["atr_period"]
        self.hard_stop_atr = s["hard_stop_atr_mult"]
        self.max_position_pct = s["max_position_pct_of_equity"]
        self.max_gross_pct = s["max_gross_exposure_pct"]
        self.max_daily_loss_pct = s["max_daily_loss_pct"]
        self.min_crypto_notional = s["min_crypto_notional"]
        self.crypto_buffer = s["crypto_stop_limit_buffer"]

    # ------------------------------------------------------------------ sizing
    def size_position(self, equity: float, price: float, atr: float, asset_class: str,
                      buying_power: float, gross_exposure: float) -> SizeResult:
        if asset_class not in ("equity", "crypto") or not all(math.isfinite(value) for value in
            (equity, price, atr, buying_power, gross_exposure)) or min(equity, price, atr) <= 0 \
            or buying_power <= 0 or gross_exposure < 0:
            return SizeResult(0, 0, 0, "invalid inputs")

        qty = (equity * self.risk_pct) / atr          # 1 ATR move == 1% of equity
        notes = []

        headroom = max(0.0, equity * self.max_gross_pct - gross_exposure)
        max_notional = min(equity * self.max_position_pct, headroom, buying_power * 0.98)
        if qty * price > max_notional:
            qty = max_notional / price
            notes.append(f"notional capped at ${max_notional:,.0f} (uncapped risk would exceed limits)")

        if asset_class == "crypto":
            qty = math.floor(qty * 1e6) / 1e6
            if qty * price < self.min_crypto_notional:
                qty = 0.0
        else:
            qty = float(math.floor(qty))               # whole shares, rounded DOWN

        return SizeResult(qty, qty * price, qty * atr, "; ".join(notes))

    # ------------------------------------------------------------------- stops
    def hard_stop_price(self, side: str, entry: float, atr: float, qty: float, equity: float) -> float:
        """
        Hard stop at 1 ATR, which equals 1% of equity at full size. It is additionally
        clamped so the loss at the stop can never exceed 1% of equity, whatever the size.
        """
        if side not in ("long", "short") or not all(math.isfinite(value) and value > 0
                for value in (entry, atr, qty, equity)):
            raise ValueError("Stop inputs must be finite, positive, and correctly sided")
        max_distance = (equity * self.risk_pct) / qty
        distance = min(atr * self.hard_stop_atr, max_distance)
        stop = entry - distance if side == "long" else entry + distance
        return max(stop, 0.01)

    @staticmethod
    def trailing_stop(side: str, extreme: float, atr: float, mult: float) -> float:
        return extreme - mult * atr if side == "long" else extreme + mult * atr

    @staticmethod
    def effective_stop(side: str, hard_stop: float, trailing_stop: Optional[float]) -> float:
        """The tighter (closer to price) of the hard stop and the trailing stop."""
        if trailing_stop is None:
            return hard_stop
        return max(hard_stop, trailing_stop) if side == "long" else min(hard_stop, trailing_stop)

    # ------------------------------------------------------------- portfolio rules
    def correlation_filter(self, symbol: str, side: str, open_sides: Dict[str, str]) -> Tuple[bool, str]:
        """Block new BTC/USD longs while SPY and QQQ are both long (stacked risk-on exposure)."""
        cf = config.CORRELATION_FILTER
        if symbol == cf["target"] and side == cf["blocked_side"]:
            if all(open_sides.get(s) == cf["blocked_side"] for s in cf["blocking_symbols"]):
                return False, (f"correlation filter: {', '.join(cf['blocking_symbols'])} already "
                               f"{cf['blocked_side']}, skipping new {symbol} {side}")
        return True, ""

    def daily_loss_exceeded(self, day_start_equity: float, equity: float) -> bool:
        if not day_start_equity:
            return False
        return (day_start_equity - equity) / day_start_equity >= self.max_daily_loss_pct
