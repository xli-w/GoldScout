"""Shared strategy interface."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import pandas as pd


class Action(str, Enum):
    HOLD = "HOLD"
    ENTER_LONG = "ENTER_LONG"
    ENTER_SHORT = "ENTER_SHORT"   # while long this means "reverse / exit long"
    EXIT = "EXIT"


@dataclass
class Signal:
    action: Action
    symbol: str
    reason: str = ""


class Strategy(ABC):
    name: str = "base"
    symbols: list
    timeframe: tuple
    lookback_days: int
    check_interval: int
    min_bars: int
    trailing_atr_mult: Optional[float] = None

    @abstractmethod
    def evaluate(self, symbol: str, bars: pd.DataFrame, position_side: Optional[str],
                 live_price: Optional[float] = None, new_bar: bool = True) -> Signal:
        """
        bars          completed candles only (oldest -> newest)
        position_side "long", "short" or None (flat)
        live_price    current price of an open position (None when flat)
        new_bar       True when a candle closed since the last evaluation
        """

    def hold(self, symbol: str, reason: str = "") -> Signal:
        return Signal(Action.HOLD, symbol, reason)
