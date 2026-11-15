"""
Strategy 1 - Mean Reversion (SPY, QQQ) on 15-minute candles.

Entry : close is more than k standard deviations away from the 20-period SMA
        (k = 1.5 for SPY, 1.8 for QQQ). Below -> long, above -> short.
Exit  : price returns to the 20-period SMA.
Stops : hard stop only (managed by the risk manager / portfolio).
"""
from typing import Optional

import pandas as pd

import config
from bot import indicators
from bot.strategies.base import Action, Signal, Strategy


class MeanReversionStrategy(Strategy):
    name = "mean_reversion"
    trailing_atr_mult = None

    def __init__(self, cfg: dict = None, research: bool = False):
        cfg = cfg or config.MEAN_REVERSION
        config.validate_strategy(self.name, cfg, research=research)
        self.symbols = cfg["symbols"]
        self.timeframe = cfg["timeframe"]
        self.lookback_days = cfg["lookback_days"]
        self.check_interval = cfg["check_interval"]
        self.period = cfg["period"]
        self.entry_std = cfg["entry_std"]
        self.min_bars = self.period + 15  # leaves room for the 14-period ATR

    def evaluate(self, symbol: str, bars: pd.DataFrame, position_side: Optional[str],
                 live_price: Optional[float] = None, new_bar: bool = True) -> Signal:
        close = bars["close"]
        mean = indicators.sma(close, self.period).iloc[-1]
        std = indicators.rolling_std(close, self.period).iloc[-1]
        if pd.isna(mean) or pd.isna(std) or std <= 0:
            return self.hold(symbol, "indicators not ready")

        # Exits are checked continuously against the live price.
        if position_side is not None:
            price = live_price if live_price else close.iloc[-1]
            if position_side == "long" and price >= mean:
                return Signal(Action.EXIT, symbol, f"price {price:.2f} reverted to SMA {mean:.2f}")
            if position_side == "short" and price <= mean:
                return Signal(Action.EXIT, symbol, f"price {price:.2f} reverted to SMA {mean:.2f}")
            return self.hold(symbol, "in position, waiting for reversion")

        # Entries only on a freshly closed candle.
        if not new_bar:
            return self.hold(symbol, "no new candle")
        k = self.entry_std[symbol]
        z = (close.iloc[-1] - mean) / std
        if z <= -k:
            return Signal(Action.ENTER_LONG, symbol, f"z={z:.2f} <= -{k} (close {close.iloc[-1]:.2f}, SMA {mean:.2f})")
        if z >= k:
            return Signal(Action.ENTER_SHORT, symbol, f"z={z:.2f} >= {k} (close {close.iloc[-1]:.2f}, SMA {mean:.2f})")
        return self.hold(symbol, f"z={z:.2f} inside band")
