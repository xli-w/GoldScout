"""
Strategy 3 - Trend Following (GLD, USO) on 4-hour candles.

Long  : 50 EMA crosses above the 200 EMA (on the latest closed candle).
Exit / short : 50 EMA crosses below the 200 EMA.
Stops : 3x ATR trailing stop (plus the 1% hard stop floor).
"""
from typing import Optional

import pandas as pd

import config
from bot import indicators
from bot.strategies.base import Action, Signal, Strategy


class TrendFollowingStrategy(Strategy):
    name = "trend_following"

    def __init__(self, cfg: dict = None, research: bool = False):
        cfg = cfg or config.TREND_FOLLOWING
        config.validate_strategy(self.name, cfg, research=research)
        self.symbols = cfg["symbols"]
        self.timeframe = cfg["timeframe"]
        self.lookback_days = cfg["lookback_days"]
        self.check_interval = cfg["check_interval"]
        self.fast = cfg["fast"]
        self.slow = cfg["slow"]
        self.trailing_atr_mult = cfg["trailing_atr_mult"]
        self.min_bars = self.slow + 50   # EMA needs warm-up to converge

    def evaluate(self, symbol: str, bars: pd.DataFrame, position_side: Optional[str],
                 live_price: Optional[float] = None, new_bar: bool = True) -> Signal:
        if not new_bar:
            return self.hold(symbol, "no new candle")

        close = bars["close"]
        fast = indicators.ema(close, self.fast)
        slow = indicators.ema(close, self.slow)
        f_prev, f_now = fast.iloc[-2], fast.iloc[-1]
        s_prev, s_now = slow.iloc[-2], slow.iloc[-1]

        if f_prev <= s_prev and f_now > s_now:
            return Signal(Action.ENTER_LONG, symbol,
                          f"golden cross: EMA{self.fast} {f_now:.2f} > EMA{self.slow} {s_now:.2f}")
        if f_prev >= s_prev and f_now < s_now:
            return Signal(Action.ENTER_SHORT, symbol,
                          f"death cross: EMA{self.fast} {f_now:.2f} < EMA{self.slow} {s_now:.2f}")
        return self.hold(symbol, "no cross")
