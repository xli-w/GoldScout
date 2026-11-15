"""
Strategy 2 - Momentum Breakout (BTC/USD) on 1-hour candles.

Long  : close breaks above the prior 20-period high AND volume >= 1.5x the
        prior 20-period average volume.
Short : close breaks below the prior 20-period low with the same volume filter.
        Alpaca cannot short crypto, so for BTC/USD this closes any long (exit only).
Stops : 2x ATR trailing stop (plus the 1% hard stop floor).
"""
from typing import Optional

import pandas as pd

import config
from bot.strategies.base import Action, Signal, Strategy


class MomentumBreakoutStrategy(Strategy):
    name = "momentum_breakout"

    def __init__(self, cfg: dict = None, research: bool = False):
        cfg = cfg or config.MOMENTUM_BREAKOUT
        config.validate_strategy(self.name, cfg, research=research)
        self.symbols = cfg["symbols"]
        self.timeframe = cfg["timeframe"]
        self.lookback_days = cfg["lookback_days"]
        self.check_interval = cfg["check_interval"]
        self.period = cfg["period"]
        self.volume_mult = cfg["volume_mult"]
        self.trailing_atr_mult = cfg["trailing_atr_mult"]
        self.min_bars = self.period + 15

    def evaluate(self, symbol: str, bars: pd.DataFrame, position_side: Optional[str],
                 live_price: Optional[float] = None, new_bar: bool = True) -> Signal:
        if not new_bar:
            return self.hold(symbol, "no new candle")

        # Channel and average volume are built from the 20 candles BEFORE the latest one,
        # otherwise the latest close could never exceed its own high.
        prior_high = bars["high"].rolling(self.period).max().shift(1).iloc[-1]
        prior_low = bars["low"].rolling(self.period).min().shift(1).iloc[-1]
        avg_volume = bars["volume"].rolling(self.period).mean().shift(1).iloc[-1]
        close = bars["close"].iloc[-1]
        volume = bars["volume"].iloc[-1]
        if pd.isna(prior_high) or pd.isna(prior_low) or pd.isna(avg_volume) or avg_volume <= 0:
            return self.hold(symbol, "indicators not ready")

        volume_ok = volume >= self.volume_mult * avg_volume
        vol_txt = f"vol {volume:.2f} vs {self.volume_mult}x avg {avg_volume:.2f}"

        if close > prior_high and volume_ok:
            return Signal(Action.ENTER_LONG, symbol, f"close {close:.2f} > 20-high {prior_high:.2f}; {vol_txt}")
        if close < prior_low and volume_ok:
            return Signal(Action.ENTER_SHORT, symbol, f"close {close:.2f} < 20-low {prior_low:.2f}; {vol_txt}")
        return self.hold(symbol, "no confirmed breakout")
