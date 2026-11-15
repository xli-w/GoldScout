"""
Backtester for the three strategies.   Run from the project root:

    python -m bot.backtest                 # pull 6 months from Alpaca and run everything
    python -m bot.backtest --use-cache     # reuse fresh caches; may still request data from Alpaca
    python -m bot.backtest --synthetic     # offline smoke test on fake data (results are meaningless)

How it simulates trading (it re-uses the live Strategy and RiskManager classes, so the
rules being tested are the rules the bot trades):

* Signals are computed on a COMPLETED candle and filled at the NEXT candle's open, so
  there is no look-ahead.
* Slippage (default 0.05%) is applied against you on every fill, entries and exits.
    Equity commission is $0; crypto fees are configured separately.
* Position size comes from RiskManager.size_position (1% of equity per 1 ATR, with the
  same notional caps as the live bot). Hard stop = 1 ATR, plus the 2x / 3x ATR trailing
  stops, are checked against each candle's high/low. A gap through a stop fills at the open.
* Correlation filter, no-short-crypto rule and the daily-loss breaker match live behaviour.
* Buying power: equities may use margin up to `margin_multiplier` x equity; crypto must be
  paid for from positive cash (like Alpaca's non-marginable buying power).
* Annual funding-rate assumptions are configurable. Partial fills and queue position are not modeled.

Per-instrument rows are STANDALONE runs (each instrument alone with the full starting
capital). Per-strategy rows group a strategy's instruments. The combined row runs all five
together on one account with the correlation filter active.
"""
import argparse
import copy
import json
import logging
import math
import hashlib
from dataclasses import dataclass, field
from itertools import groupby
from heapq import merge
from pathlib import Path
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

import config
from bot import indicators
from bot.sessions import regular_hours, aggregate_equities, aggregate_crypto, schedule, validate_execution, session_bounds
from bot.corporate_actions import adjusted_window, continuous_prices, effective_time, ordered_actions
from bot.utils import create_api
from bot.statistics import daily_returns, sharpe_interval, downside_metrics
from bot.statistics import bootstrap_draws
from bot.research import ResearchRun, manifest, write_json
from bot.risk_manager import RiskManager
from bot.strategies.base import Action, Strategy
from bot.strategies.mean_reversion import MeanReversionStrategy
from bot.strategies.momentum_breakout import MomentumBreakoutStrategy
from bot.strategies.trend_following import TrendFollowingStrategy

log = logging.getLogger("backtest")
ET = ZoneInfo(config.MARKET_TZ)

STRATEGY_CLASSES = {
    "mean_reversion": (MeanReversionStrategy, config.MEAN_REVERSION),
    "momentum_breakout": (MomentumBreakoutStrategy, config.MOMENTUM_BREAKOUT),
    "trend_following": (TrendFollowingStrategy, config.TREND_FOLLOWING),
}
MIN_IS_TRADES = 30
MIN_OOS_TRADES = 20


def build_strategy(name: str, overrides: Optional[dict] = None) -> Strategy:
    cls, base = STRATEGY_CLASSES[name]
    cfg = copy.deepcopy(base)
    cfg.update(copy.deepcopy(overrides or {}))
    return cls(cfg)


# =============================================================================
# Simulation engine
# =============================================================================
@dataclass
class BtConfig:
    initial_capital: float = 100_000.0
    slippage: float = 0.0005          # 0.05% against you on every fill
    commission: float = 0.0           # $ per fill (Alpaca: zero)
    margin_multiplier: float = 2.0    # equity buying power = multiplier x equity - gross exposure
    corr_filter: bool = True
    daily_loss_limit: bool = True
    crypto_taker_fee: float = config.RISK["crypto_taker_fee"]
    crypto_maker_fee: float = config.RISK["crypto_maker_fee"]
    crypto_software_stops: bool = True
    risk_free_rate: float = 0.0
    margin_rate: float = 0.0
    short_borrow_rate: float = 0.0
    cash_yield_rate: float = 0.0

    def __post_init__(self):
        if not math.isfinite(self.initial_capital) or self.initial_capital <= 0 or not math.isfinite(self.commission) or self.commission < 0:
            raise ValueError("Capital must be positive and commission nonnegative")
        if not all(math.isfinite(rate) and rate >= 0 for rate in
                   (self.risk_free_rate, self.margin_rate, self.short_borrow_rate, self.cash_yield_rate)):
            raise ValueError("Annual funding rates must be finite and nonnegative")
        if not all(math.isfinite(rate) and 0 <= rate < 1 for rate in
                   (self.slippage, self.crypto_taker_fee, self.crypto_maker_fee)):
            raise ValueError("Slippage and fee rates must be finite fractions in [0, 1)")


@dataclass
class Pending:
    close: bool = False
    reason: str = ""
    open_side: Optional[str] = None
    atr: float = 0.0
    open_reason: str = ""


@dataclass
class BtPosition:
    symbol: str
    strategy: str
    side: str
    qty: float
    entry_price: float
    entry_time: pd.Timestamp
    atr: float
    hard_stop: float
    extreme: float
    trailing_mult: Optional[float]
    trailing_stop: Optional[float] = None
    entry_cost: float = 0.0
    entry_fee: float = 0.0
    stop_triggered: bool = False
    stop_limit_price: Optional[float] = None
    stop_trigger_time: Optional[pd.Timestamp] = None
    distributions: float = 0.0


@dataclass
class BacktestResult:
    symbols: List[str]
    initial: float
    equity: pd.Series
    trades: pd.DataFrame
    has_crypto: bool
    blocked_corr: int = 0
    blocked_daily: int = 0
    skipped_size: int = 0
    slippage_cost: float = 0.0
    fees_cost: float = 0.0
    unfilled_stop_limits: int = 0
    risk_free_rate: float = 0.0
    turnover_notional: float = 0.0
    average_gross_exposure: float = 0.0
    financing_cost: float = 0.0
    cash_yield: float = 0.0


TRADE_COLS = ["symbol", "strategy", "side", "entry_time", "exit_time", "entry_price",
              "exit_price", "qty", "pnl", "exit_reason", "fees"]


class Backtester:
    def __init__(self, data: Dict[str, pd.DataFrame], strategies: List[Strategy],
                 cfg: Optional[BtConfig] = None, risk: Optional[RiskManager] = None):
        self.cfg = cfg or BtConfig()
        self.risk = risk or RiskManager()
        self.data = dict(data)
        self.execution = {}
        self.actions = {symbol: ordered_actions(frame.attrs.get("corporate_actions", [])) for symbol, frame in data.items()}
        self.strat_for = {sym: s for s in strategies for sym in s.symbols if sym in data}
        self.idx, self.arr, self.atr, self.delta = {}, {}, {}, {}
        for sym, strat in self.strat_for.items():
            df = data[sym]
            if config.INSTRUMENTS[sym].asset_class == "equity":
                df = regular_hours(df)
                self.data[sym] = df
            execution = df.attrs.get("execution")
            if execution is not None:
                self.execution[sym] = regular_hours(execution) if config.INSTRUMENTS[sym].asset_class == "equity" else execution
            amount, unit = strat.timeframe
            self.idx[sym] = df.index
            self.arr[sym] = {k: df[k].to_numpy(float) for k in ("open", "high", "low", "close")}
            continuous, factor = continuous_prices(df, self.actions[sym])
            self.atr[sym] = (indicators.atr(continuous, self.risk.atr_period) / factor).to_numpy()
            self.delta[sym] = pd.Timedelta(minutes=amount * (1 if unit == "Min" else 60))
            if sym in self.execution:
                execution = self.execution[sym]
                boundaries = ((df.index, "open"),
                              (pd.DatetimeIndex(df["bar_end"]) - pd.Timedelta(minutes=1)
                               if "bar_end" in df else df.index + self.delta[sym] - pd.Timedelta(minutes=1), "close"))
                for timestamps, column in boundaries:
                    offsets = execution.index.get_indexer(timestamps)
                    available = offsets >= 0
                    expected = df[column].to_numpy(float)[available]
                    observed = execution[column].to_numpy(float)[offsets[available]]
                    if not np.allclose(expected, observed, rtol=1e-8, atol=1e-8):
                        raise ValueError(f"{sym}: strategy/execution {column} boundary prices disagree")
                numeric = execution[["open", "high", "low", "close", "volume"]].copy()
                numeric.attrs = {}
                if config.INSTRUMENTS[sym].asset_class == "equity":
                    rebuilt = aggregate_equities(numeric, int(self.delta[sym].total_seconds() / 60))
                else:
                    duration = int(self.delta[sym].total_seconds() / 60)
                    labels = numeric.index.floor(f"{duration}min")
                    rebuilt = numeric.groupby(labels).agg({"open": "first", "high": "max", "low": "min",
                                                           "close": "last", "volume": "sum"})
                    timestamps = pd.Series(numeric.index, index=labels)
                    first = timestamps.groupby(level=0).first()
                    last = timestamps.groupby(level=0).last() + pd.Timedelta(minutes=1)
                    complete = first.eq(pd.Series(rebuilt.index, index=rebuilt.index)) & last.eq(
                        pd.Series(rebuilt.index + self.delta[sym], index=rebuilt.index))
                    rebuilt = rebuilt.loc[complete]
                common = df.index.intersection(rebuilt.index)
                for column in ("open", "high", "low", "close", "volume"):
                    if not np.allclose(df.loc[common, column].to_numpy(float), rebuilt.loc[common, column].to_numpy(float),
                                       rtol=1e-8, atol=1e-8):
                        raise ValueError(f"{sym}: strategy/execution aggregated {column} values disagree")

    # ------------------------------------------------------------------ run
    def run(self, symbols: List[str], start: pd.Timestamp, end: pd.Timestamp) -> BacktestResult:
        symbols = sorted(s for s in symbols if s in self.strat_for)
        self.cash = self.cfg.initial_capital
        self.positions: Dict[str, BtPosition] = {}
        self.pending: Dict[str, Pending] = {}
        self.last_price: Dict[str, float] = {}
        self.last_price_ts: Dict[str, pd.Timestamp] = {}
        self.trades: List[dict] = []
        self.blocked_corr = self.blocked_daily = self.skipped_size = 0
        self.slippage_cost = 0.0
        self.fees_cost = 0.0
        self.unfilled_stop_limits = 0
        self.day, self.day_start_equity = None, self.cfg.initial_capital
        self.feature_index = {}
        self.receivables = {}
        self.financing_cost = self.cash_yield = self.turnover_notional = 0.0
        self.funding_time = start
        exposure_samples = []
        self.daily_halted = False

        streams = []
        for sym in symbols:
            actions = []
            for action_index, action in enumerate(self.actions[sym]):
                timestamp = effective_time(action)
                if start <= timestamp <= end:
                    actions.append((timestamp, -3, sym, action_index))
                if action["kind"] == "dividend":
                    payment = pd.Timestamp(action["payable_date"], tz=ET).tz_convert("UTC")
                    if start <= payment <= end:
                        actions.append((payment, -2, sym, action_index))
            streams.append(iter(sorted(actions)))
            streams.append(self._bar_events(sym, start, end))
            if sym in self.execution:
                streams.append(self._minute_events(sym, start, end))

        curve = [(start, self.cfg.initial_capital)]
        final_t = start
        for timestamp, batch in groupby(merge(*streams), key=lambda event: event[0]):
            final_t = timestamp
            batch = list(batch)
            self._roll_day(timestamp)
            for _, phase, sym, index in batch:
                if phase in (-3, -2):
                    self._apply_action(sym, self.actions[sym][index], phase == -2)
            monitors = [event for event in batch if event[1] == -1]
            for _, _, sym, index in monitors:
                self.last_price[sym] = float(self.execution[sym]["close"].iloc[index])
                self.last_price_ts[sym] = timestamp
            for _, _, sym, index in monitors:
                self._monitor_execution(sym, index, timestamp, range_only=True)
            closes = [event for event in batch if event[1] == 0]
            opens = [event for event in batch if event[1] == 1]
            for _, _, sym, index in closes:
                self.last_price[sym] = self.arr[sym]["close"][index]
                self.last_price_ts[sym] = timestamp
            for _, _, sym, index in closes:
                self._on_bar(sym, index)
                self.feature_index[sym] = index
            for _, _, sym, index in monitors:
                self._monitor_execution(sym, index, timestamp, skip_range=True)
            for _, _, sym, index in closes:
                atr_value = self.atr[sym][index]
                if index + 1 < len(self.idx[sym]) and math.isfinite(atr_value):
                    self._decide(sym, index, self.arr[sym]["close"][index], atr_value)
            for _, _, sym, index in opens:
                self.last_price[sym] = self.arr[sym]["open"][index]
                self.last_price_ts[sym] = timestamp
            for _, _, sym, index in opens:
                self._on_open(sym, index)
            curve.append((timestamp, self._equity()))
            exposure_samples.append(sum(abs(position.qty * self.last_price[symbol])
                for symbol, position in self.positions.items()) / max(self._equity(), 1e-9))

        # Close anything still open at the last known price so every trade is counted.
        for sym in list(self.positions):
            self._close(sym, self.last_price[sym], final_t, "end of test")
        curve.append((final_t, self._equity()))

        equity = pd.Series([v for _, v in curve], index=pd.DatetimeIndex([t for t, _ in curve]))
        trades = pd.DataFrame(self.trades, columns=TRADE_COLS)
        return BacktestResult(
            symbols=symbols, initial=self.cfg.initial_capital, equity=equity, trades=trades,
            has_crypto=any(config.INSTRUMENTS[s].asset_class == "crypto" for s in symbols),
            blocked_corr=self.blocked_corr, blocked_daily=self.blocked_daily,
            skipped_size=self.skipped_size, slippage_cost=self.slippage_cost,
            fees_cost=self.fees_cost, unfilled_stop_limits=self.unfilled_stop_limits,
            risk_free_rate=self.cfg.risk_free_rate, turnover_notional=self.turnover_notional,
            average_gross_exposure=float(np.mean(exposure_samples)) if exposure_samples else 0.0,
            financing_cost=self.financing_cost, cash_yield=self.cash_yield,
        )

    # ------------------------------------------------------------ per-candle logic
    def _bar_events(self, symbol, start, end):
        index = self.idx[symbol]
        for position in range(index.searchsorted(start), len(index)):
            closing = pd.Timestamp(self.data[symbol]["bar_end"].iloc[position]) if "bar_end" in self.data[symbol] \
                else index[position] + self.delta[symbol]
            if closing > end:
                break
            if closing <= index[position] or position + 1 < len(index) and closing > index[position + 1]:
                raise ValueError("Strategy bars must have positive, nonoverlapping intervals")
            yield index[position], 1, symbol, position
            yield closing, 0, symbol, position

    def _minute_events(self, symbol, start, end):
        index = self.execution[symbol].index
        for position in range(index.searchsorted(start), len(index)):
            closing = index[position] + pd.Timedelta(minutes=1)
            if closing > end:
                break
            yield closing, -1, symbol, position

    def _apply_action(self, sym, action, payment=False):
        if payment:
            self.cash += self.receivables.pop(action["id"], 0.0)
            return
        pos = self.positions.get(sym)
        if action["kind"] == "dividend":
            if pos:
                entitlement = (1 if pos.side == "long" else -1) * pos.qty * action["rate"]
                self.receivables[action["id"]] = entitlement
                pos.distributions += entitlement
            return
        ratio = action["ratio"]
        if sym in self.last_price:
            self.last_price[sym] /= ratio
        if pos:
            pos.qty *= ratio
            for field_name in ("entry_price", "atr", "hard_stop", "extreme", "trailing_stop", "stop_limit_price"):
                value = getattr(pos, field_name)
                if value is not None:
                    setattr(pos, field_name, value / ratio)
        pending = self.pending.get(sym)
        if pending:
            pending.atr /= ratio

    def _on_open(self, sym: str, i: int) -> None:
        o = self.arr[sym]["open"][i]
        ts = self.idx[sym][i]
        self.last_price[sym] = o
        self.last_price_ts[sym] = ts
        pos = self.positions.get(sym)
        if pos:
            stop = self.risk.effective_stop(pos.side, pos.hard_stop, pos.trailing_stop)
            if config.INSTRUMENTS[sym].asset_class == "crypto":
                self._crypto_stop(sym, o, o, o, ts)
            elif (pos.side == "long" and o <= stop) or (pos.side == "short" and o >= stop):
                self._close(sym, o, ts, "gap stop")
        pend = self.pending.pop(sym, None)
        if pend:
            if pend.close and sym in self.positions:
                self._close(sym, o, ts, pend.reason)
            if pend.open_side and sym not in self.positions:
                if self.cfg.daily_loss_limit and self._daily_exceeded():
                    self.blocked_daily += 1
                    return
                if self.cfg.corr_filter:
                    sides = {symbol: position.side for symbol, position in self.positions.items()}
                    for symbol, pending in self.pending.items():
                        if pending.open_side:
                            sides[symbol] = pending.open_side
                    if not self.risk.correlation_filter(sym, pend.open_side, sides)[0]:
                        self.blocked_corr += 1
                        return
                self._open(sym, pend.open_side, o, ts, pend.atr)

    def _on_bar(self, sym: str, i: int) -> None:
        a = self.arr[sym]
        o, h, l, c = a["open"][i], a["high"][i], a["low"][i], a["close"][i]
        ts = pd.Timestamp(self.data[sym]["bar_end"].iloc[i]) if "bar_end" in self.data[sym] else self.idx[sym][i] + self.delta[sym]

        # 2) stops are checked against this candle's range
        pos = self.positions.get(sym)
        if pos and sym not in self.execution:
            stop = self.risk.effective_stop(pos.side, pos.hard_stop, pos.trailing_stop)
            if config.INSTRUMENTS[sym].asset_class == "crypto":
                self._crypto_stop(sym, o, h, l, ts)
                pos = self.positions.get(sym)
                if pos and self.cfg.crypto_software_stops and (
                        (pos.side == "long" and c <= stop) or (pos.side == "short" and c >= stop)):
                    self._close(sym, c, ts, "software stop (bar-close fallback)")
            elif pos.side == "long" and l <= stop:
                self._close(sym, stop, ts, "stop")
            elif pos.side == "short" and h >= stop:
                self._close(sym, stop, ts, "stop")

        # 3) candle close: update trailing stop (applies from the next candle), then decide
        self.last_price[sym] = c
        self.last_price_ts[sym] = ts
        atr_i = self.atr[sym][i]
        pos = self.positions.get(sym)
        if pos and not pos.stop_triggered and not math.isnan(atr_i):
            pos.atr = atr_i
            if sym in self.execution:
                return
            observed = c if sym in self.execution else h if pos.side == "long" else l
            pos.extreme = max(pos.extreme, observed) if pos.side == "long" else min(pos.extreme, observed)
            if pos.trailing_mult:
                t = self.risk.trailing_stop(pos.side, pos.extreme, pos.atr, pos.trailing_mult)
                if pos.trailing_stop is None or (pos.side == "long" and t > pos.trailing_stop) \
                        or (pos.side == "short" and t < pos.trailing_stop):
                    pos.trailing_stop = t

    def _monitor_execution(self, sym: str, index: int, timestamp: pd.Timestamp,
                           range_only: bool = False, skip_range: bool = False) -> None:
        pos = self.positions.get(sym)
        if pos is None:
            return
        row = self.execution[sym].iloc[index]
        stop = self.risk.effective_stop(pos.side, pos.hard_stop, pos.trailing_stop)
        if not skip_range:
            if config.INSTRUMENTS[sym].asset_class == "crypto":
                self._crypto_stop(sym, float(row.open), float(row.high), float(row.low), timestamp)
            elif (pos.side == "long" and row.low <= stop) or (pos.side == "short" and row.high >= stop):
                reference = min(float(row.open), stop) if pos.side == "long" else max(float(row.open), stop)
                self._close(sym, reference, timestamp, "minute stop")
        if range_only:
            return
        pos = self.positions.get(sym)
        if pos is None:
            return
        price = float(row.close)
        pos.extreme = max(pos.extreme, price) if pos.side == "long" else min(pos.extreme, price)
        if pos.trailing_mult and not pos.stop_triggered:
            trail = self.risk.trailing_stop(pos.side, pos.extreme, pos.atr, pos.trailing_mult)
            pos.trailing_stop = self.risk.effective_stop(pos.side, pos.trailing_stop or pos.hard_stop, trail)
        stop = self.risk.effective_stop(pos.side, pos.hard_stop, pos.trailing_stop)
        software_enabled = config.INSTRUMENTS[sym].asset_class == "equity" or self.cfg.crypto_software_stops
        if software_enabled and (price <= stop if pos.side == "long" else price >= stop):
            self._close(sym, price, timestamp, "minute software stop after update")
            return
        if sym in self.feature_index and self.strat_for[sym].name == "mean_reversion":
            feature = self.feature_index[sym]
            first = self.idx[sym].searchsorted(timestamp - pd.Timedelta(days=self.strat_for[sym].lookback_days))
            window = adjusted_window(self.data[sym].iloc[first:feature + 1], self.actions[sym], timestamp)
            signal = self.strat_for[sym].evaluate(sym, window, pos.side, live_price=price, new_bar=False)
            if signal.action == Action.EXIT:
                self._close(sym, price, timestamp, signal.reason)

    def _crypto_stop(self, sym: str, opening: float, high: float, low: float,
                     timestamp: pd.Timestamp) -> None:
        pos = self.positions.get(sym)
        if pos is None:
            return
        stop = self.risk.effective_stop(pos.side, pos.hard_stop, pos.trailing_stop)
        buying = pos.side == "short"
        breached = high >= stop if buying else low <= stop
        if not pos.stop_triggered:
            if not breached:
                return
            pos.stop_triggered = True
            pos.stop_trigger_time = timestamp
            pos.stop_limit_price = round(stop * (1 + self.risk.crypto_buffer if buying
                                                  else 1 - self.risk.crypto_buffer), 2)
            reference = max(opening, stop) if buying else min(opening, stop)
            limit = pos.stop_limit_price
            executable = reference <= limit if buying else reference >= limit
            if executable:
                fill = self._fill_price(reference, buying)
                fill = min(fill, limit) if buying else max(fill, limit)
                self._close(sym, reference, timestamp, "crypto stop-limit", fill_override=fill)
                return
            self.unfilled_stop_limits += 1
            return
        limit = pos.stop_limit_price
        if limit is None or timestamp <= pos.stop_trigger_time:
            return
        executable = low <= limit if buying else high >= limit
        if executable:
            reference = min(opening, limit) if buying else max(opening, limit)
            fill = self._fill_price(reference, buying)
            fill = min(fill, limit) if buying else max(fill, limit)
            self._close(sym, reference, timestamp, "resting crypto stop-limit",
                        fill_override=fill, fee_rate=self.cfg.crypto_maker_fee)

    def _decide(self, sym: str, i: int, close: float, atr: float) -> None:
        strat = self.strat_for[sym]
        inst = config.INSTRUMENTS[sym]
        idx = self.idx[sym]
        lo = idx.searchsorted(idx[i] - pd.Timedelta(days=strat.lookback_days), side="left")
        asof = pd.Timestamp(self.data[sym]["bar_end"].iloc[i]) if "bar_end" in self.data[sym] else idx[i] + self.delta[sym]
        window = adjusted_window(self.data[sym].iloc[lo:i + 1], self.actions[sym], asof)
        if len(window) < strat.min_bars:
            return
        pos = self.positions.get(sym)
        sig = strat.evaluate(sym, window, pos.side if pos else None,
                             live_price=close if pos else None, new_bar=True)
        if sig.action == Action.HOLD:
            return
        if sig.action == Action.EXIT:
            if pos:
                self.pending[sym] = Pending(close=True, reason=sig.reason)
            return

        want = "long" if sig.action == Action.ENTER_LONG else "short"
        if pos and pos.side == want:
            return
        pend = Pending(close=bool(pos), reason=f"reversal: {sig.reason}")
        # (same order of checks as the live bot: leave the old position first, then gate the new one)
        if want == "short" and not inst.allow_short:
            if pos:
                self.pending[sym] = pend
            return
        if self.cfg.daily_loss_limit and self._daily_exceeded():
            self.blocked_daily += 1
            if pos:
                self.pending[sym] = pend
            return
        if self.cfg.corr_filter:
            sides = {s: p.side for s, p in self.positions.items() if s != sym}
            for s, pd_ in self.pending.items():          # entries already decided count as exposure
                if s != sym and pd_.open_side:
                    sides[s] = pd_.open_side
            ok, _ = self.risk.correlation_filter(sym, want, sides)
            if not ok:
                self.blocked_corr += 1
                if pos:
                    self.pending[sym] = pend
                return
        pend.open_side, pend.atr, pend.open_reason = want, atr, sig.reason
        self.pending[sym] = pend

    # ------------------------------------------------------------------ fills
    def _fill_price(self, ref: float, buying: bool) -> float:
        s = self.cfg.slippage
        return ref * (1 + s) if buying else ref * (1 - s)

    def _open(self, sym: str, side: str, ref: float, ts: pd.Timestamp, atr: float) -> None:
        if any(timestamp > ts for timestamp in self.last_price_ts.values()):
            raise RuntimeError("Execution cannot use future portfolio marks")
        inst = config.INSTRUMENTS[sym]
        strat = self.strat_for[sym]
        fill = self._fill_price(ref, buying=(side == "long"))
        equity = self._equity()
        gross = sum(abs(p.qty * self.last_price[s]) for s, p in self.positions.items())
        if inst.asset_class == "crypto":
            buying_power = max(self.cash, 0.0)
        else:
            buying_power = max(0.0, self.cfg.margin_multiplier * equity - gross)
        size = self.risk.size_position(equity, fill, atr, inst.asset_class, buying_power, gross)
        if size.qty <= 0:
            self.skipped_size += 1
            return
        entry_fee = size.qty * fill * self.cfg.crypto_taker_fee if inst.asset_class == "crypto" else 0.0
        qty = size.qty * (1 - self.cfg.crypto_taker_fee) if inst.asset_class == "crypto" else size.qty
        entry_cost = size.qty * fill + self.cfg.commission
        pos = BtPosition(
            symbol=sym, strategy=strat.name, side=side, qty=qty, entry_price=fill, entry_time=ts,
            atr=atr, hard_stop=self.risk.hard_stop_price(side, fill, atr, qty, equity),
            extreme=fill, trailing_mult=strat.trailing_atr_mult,
            entry_cost=entry_cost, entry_fee=entry_fee,
        )
        if pos.trailing_mult:
            pos.trailing_stop = self.risk.trailing_stop(side, fill, atr, pos.trailing_mult)
        self.cash += (-1 if side == "long" else 1) * size.qty * fill - self.cfg.commission
        self.slippage_cost += size.qty * ref * self.cfg.slippage
        self.fees_cost += entry_fee + self.cfg.commission
        self.positions[sym] = pos
        self.turnover_notional += size.qty * fill

    def _close(self, sym: str, ref: float, ts: pd.Timestamp, reason: str,
               fill_override: Optional[float] = None, fee_rate: Optional[float] = None) -> None:
        pos = self.positions.pop(sym)
        long = pos.side == "long"
        fill = self._fill_price(ref, buying=not long) if fill_override is None else fill_override
        crypto = config.INSTRUMENTS[sym].asset_class == "crypto"
        rate = self.cfg.crypto_taker_fee if fee_rate is None else fee_rate
        exit_fee = pos.qty * fill * rate if crypto else 0.0
        self.cash += (1 if long else -1) * pos.qty * fill - self.cfg.commission - exit_fee
        self.slippage_cost += abs(fill - ref) * pos.qty
        self.fees_cost += exit_fee + self.cfg.commission
        self.turnover_notional += pos.qty * fill
        pnl = (1 if long else -1) * (fill - pos.entry_price) * pos.qty
        pnl += pos.distributions - pos.entry_fee - exit_fee - 2 * self.cfg.commission
        self.trades.append(dict(
            symbol=sym, strategy=pos.strategy, side=pos.side, entry_time=pos.entry_time, exit_time=ts,
            entry_price=pos.entry_price, exit_price=fill, qty=pos.qty, pnl=pnl, exit_reason=reason,
            fees=pos.entry_fee + exit_fee + 2 * self.cfg.commission))

    def _equity(self) -> float:
        eq = self.cash + sum(self.receivables.values())
        for s, p in self.positions.items():
            eq += (1 if p.side == "long" else -1) * p.qty * self.last_price[s]
        return eq

    def _roll_day(self, t_end: pd.Timestamp) -> None:
        elapsed = (t_end - self.funding_time).total_seconds() / (365 * 86400)
        borrowing = max(-self.cash, 0) * self.cfg.margin_rate
        borrowing += sum(pos.qty * self.last_price[symbol] for symbol, pos in self.positions.items()
                         if pos.side == "short") * self.cfg.short_borrow_rate
        short_proceeds = sum(pos.qty * pos.entry_price for pos in self.positions.values() if pos.side == "short")
        interest = max(0.0, self.cash - short_proceeds) * ((1 + self.cfg.cash_yield_rate) ** elapsed - 1)
        cost = borrowing * elapsed
        self.cash += interest - cost
        self.financing_cost += cost
        self.cash_yield += interest
        self.funding_time = t_end
        d = t_end.tz_convert(ET).date()
        if d != self.day:
            self.day, self.day_start_equity = d, self._equity()
            self.daily_halted = False

    def _daily_exceeded(self):
        self.daily_halted = self.daily_halted or self.risk.daily_loss_exceeded(self.day_start_equity, self._equity())
        return self.daily_halted


# =============================================================================
# Metrics
# =============================================================================
def compute_metrics(res: BacktestResult) -> dict:
    natural = res.trades[res.trades["exit_reason"] != "end of test"] if len(res.trades) else res.trades
    pnl = natural["pnl"] if len(natural) else pd.Series(dtype=float)
    wins, losses = pnl[pnl > 0], pnl[pnl < 0]
    n = len(pnl)
    gross_loss = -losses.sum()
    if gross_loss > 0:
        pf = wins.sum() / gross_loss
    else:
        pf = math.inf if wins.sum() > 0 else math.nan
    eq = res.equity
    returns = daily_returns(eq, res.has_crypto, res.risk_free_rate)
    annual_days = 365 if res.has_crypto else 252
    downside = downside_metrics(eq, returns, res.initial)
    sharpe = float(returns.mean() / returns.std() * math.sqrt(annual_days)) if len(returns) > 1 and returns.std() > 1e-10 else math.nan
    return dict(
        **downside,
        forced_closes=len(res.trades) - len(natural),
        sharpe_ci_95=sharpe_interval(returns, annual_days),
        sortino=float(returns.mean() / downside["downside_deviation"] * math.sqrt(annual_days)) if downside["downside_deviation"] > 0 else None,
        turnover=res.turnover_notional / res.initial,
        average_gross_exposure=res.average_gross_exposure,
        financing_cost=res.financing_cost, cash_yield=res.cash_yield, fees=res.fees_cost,
        trades=n,
        win_rate=100 * len(wins) / n if n else math.nan,
        avg_win=float(wins.mean()) if len(wins) else 0.0,
        avg_loss=float(losses.mean()) if len(losses) else 0.0,
        profit_factor=pf,
        max_dd=100 * float((eq / eq.cummax() - 1).min()),
        sharpe=sharpe,
        total_return=100 * float(eq.iloc[-1] / res.initial - 1),
        final_equity=float(eq.iloc[-1]),
    )


def buy_and_hold(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> float:
    w = df[(df.index >= start) & (df.index <= end)]
    if w.empty:
        return math.nan
    shares, distributions = 1.0, 0.0
    for action in ordered_actions(df.attrs.get("corporate_actions", [])):
        if w.index[0] < effective_time(action) <= end:
            if action["kind"] == "split":
                shares *= action["ratio"]
            else:
                distributions += shares * action["rate"]
    return 100 * ((shares * w["close"].iloc[-1] + distributions) / w["open"].iloc[0] - 1)


# =============================================================================
# Running everything
# =============================================================================
def run_all(strategies: List[Strategy], data: Dict[str, pd.DataFrame],
            start: pd.Timestamp, end: pd.Timestamp, cfg: BtConfig) -> dict:
    bt = Backtester(data, strategies, cfg)
    out = {"instruments": {}, "strategies": {}, "benchmarks": {}}
    for strat in strategies:
        for sym in strat.symbols:
            out["instruments"][sym] = bt.run([sym], start, end)
            out["benchmarks"][sym] = buy_and_hold(data[sym], start, end)
        out["strategies"][strat.name] = (out["instruments"][strat.symbols[0]] if len(strat.symbols) == 1
                                         else bt.run(strat.symbols, start, end))
    all_syms = [s for strat in strategies for s in strat.symbols]
    out["combined"] = bt.run(all_syms, start, end)
    out["portfolio_benchmarks"] = portfolio_benchmarks(data, all_syms, out["combined"], start, end, cfg)
    return out


def portfolio_benchmarks(data, symbols, result, start, end, cfg):
    index = result.equity.resample("D").last().index
    curves = {}
    for symbol in symbols:
        frame = data[symbol]
        window = frame[(frame.index >= start) & (frame.index < end)]
        if window.empty:
            continue
        value = pd.Series(1.0, index=window.index)
        shares, distributions = 1.0, 0.0
        actions = ordered_actions(frame.attrs.get("corporate_actions", []))
        action_index = 0
        for timestamp, close in window["close"].items():
            while action_index < len(actions) and effective_time(actions[action_index]) <= timestamp:
                action = actions[action_index]
                if effective_time(action) > window.index[0]:
                    if action["kind"] == "split":
                        shares *= action["ratio"]
                    else:
                        distributions += shares * action["rate"]
                action_index += 1
            value.loc[timestamp] = (shares * close + distributions) / window["open"].iloc[0]
        curves[symbol] = value.resample("D").last().reindex(index).ffill().fillna(1.0)
    elapsed = (index - start).total_seconds() / (365 * 86400)
    cash = pd.Series((1 + cfg.cash_yield_rate) ** np.maximum(elapsed, 0), index=index)
    passive = pd.concat(curves.values(), axis=1).mean(axis=1) if curves else cash
    strategy = result.equity.resample("D").last().reindex(index).ffill() / result.initial
    benchmark = curves.get("SPY", passive)
    strategy_returns = strategy.pct_change().dropna()
    benchmark_returns = benchmark.pct_change().reindex(strategy_returns.index)
    variance = benchmark_returns.var()
    beta = float(strategy_returns.cov(benchmark_returns) / variance) if variance > 0 else None
    passive_returns = passive.pct_change().dropna()
    scale = min(2.0, float(strategy_returns.std() / passive_returns.std())) if passive_returns.std() > 0 else 0.0
    matched = (1 + passive_returns * scale).cumprod()
    return dict(cash_return=100 * float(cash.iloc[-1] - 1),
                spy_total_return=100 * float(curves["SPY"].iloc[-1] - 1) if "SPY" in curves else None,
                equal_weight_return=100 * float(passive.iloc[-1] - 1),
                descriptive_risk_matched_return=100 * float(matched.iloc[-1] - 1) if len(matched) else 0.0,
                descriptive_risk_scale=scale, beta_to_spy=beta if "SPY" in curves else None,
                note="Passive comparisons gross of costs; full-sample risk match is descriptive, not investable")


# =============================================================================
# Explicit parameter research
# =============================================================================
def candidate_overrides(name: str) -> List[dict]:
    """Small grids around the spec. Risk rules (1% sizing, 1 ATR hard stop) are never touched."""
    if name == "mean_reversion":
        return [{"period": p, "entry_std": {"SPY": k, "QQQ": round(k + 0.3, 2)}}
                for k in (1.5, 2.0, 2.5) for p in (20, 30, 40)]
    if name == "momentum_breakout":
        return [{"period": p, "volume_mult": v, "trailing_atr_mult": t}
                for p in (20, 30, 40) for v in (1.0, 1.5, 2.0) for t in (2.0, 3.0, 4.0)]
    if name == "trend_following":
        return [{"fast": f, "slow": s, "trailing_atr_mult": t}
                for f, s in ((50, 200), (30, 100), (20, 100), (20, 50)) for t in (3.0, 4.0)]
    return []


def optimize_strategy(name: str, data, start, end, cfg: BtConfig, research=None) -> dict:
    """
    Select from expanding chronological validation folds in the first 80 percent.
    Lock parameters before reporting the untouched final holdout; start each fold flat.
    """
    split = start + (end - start) * .8
    info = dict(strategy=name, split=split.isoformat(), adopted=False, overrides={}, trials=[],
                boundary_policy="Each fold starts flat; forced boundary exits excluded from trade counts")
    if (end - start).days < 730:
        info.update(candidates_tested=0, reason="Research requires at least two years, including a separate final holdout")
        return info
    candidates = [{}] + candidate_overrides(name)
    if research is None:
        raise ValueError("Parameter research requires a persistent ResearchRun registry")
    info["candidates_tested"] = len(candidates)
    folds = [(start, start + (split - start) * fraction,
              start + (split - start) * (fraction + .15)) for fraction in (.55, .70, .85)]
    scored = []
    for overrides in candidates:
        strategy = build_strategy(name, overrides)
        engine = Backtester(data, [strategy], cfg)
        training_trades = compute_metrics(engine.run(strategy.symbols, start, folds[0][1]))["trades"]
        validation = []
        returns = []
        for _, train_end, validation_end in folds:
            result = engine.run(strategy.symbols, train_end, validation_end)
            metrics = compute_metrics(result)
            validation.append(metrics)
            returns.append(daily_returns(result.equity, result.has_crypto, cfg.risk_free_rate))
        combined = pd.concat(returns)
        annual_days = 365 if result.has_crypto else 252
        interval = sharpe_interval(combined, annual_days, trials=len(candidates))
        score = float(combined.mean() / combined.std() * math.sqrt(annual_days)) if combined.std() > 1e-10 else math.nan
        trial = dict(overrides=overrides, training_trades=training_trades, folds=validation,
                     validation_sharpe=score, selection_adjusted_ci=interval,
                     bootstrap_draws=bootstrap_draws(len(candidates)), strategy=name)
        research.record("candidate_evaluated", trial)
        info["trials"].append(trial)
        eligible = training_trades >= MIN_IS_TRADES and sum(metrics["trades"] for metrics in validation) >= MIN_OOS_TRADES
        eligible = eligible and all(metrics["sharpe"] > 0 for metrics in validation)
        eligible = eligible and interval[0] is not None and interval[0] > 0
        if eligible:
            scored.append((score, overrides))
    if not scored:
        info["reason"] = "No candidate passed all folds and trial-adjusted uncertainty gate"
        return info
    _, best = max(scored, key=lambda item: item[0])
    info.update(adopted=bool(best), overrides=best, best=best,
                reason="Parameters locked from validation only; final holdout is reporting-only")
    research.record("parameters_locked", dict(strategy=name, overrides=best, split=str(split)))
    research.claim_holdout(name, build_strategy(name).symbols, split, end)
    for key, overrides in (("final_holdout_candidate", best), ("final_holdout_spec", {})):
        strategy = build_strategy(name, overrides)
        info[key] = compute_metrics(Backtester(data, [strategy], cfg).run(strategy.symbols, split, end))
        research.record("holdout_evaluated", dict(strategy=name, kind=key, metrics=info[key]))
    return info


# =============================================================================
# Data
# =============================================================================
def backtest_window(data, strategies, months):
    end = min(pd.Timestamp(data[sym]["bar_end"].iloc[-1]) if "bar_end" in data[sym] else
              data[sym].index[-1] + pd.Timedelta(minutes=s.timeframe[0] * (1 if s.timeframe[1] == "Min" else 60))
              for s in strategies for sym in s.symbols)
    start = end - pd.DateOffset(months=months)
    for strategy in strategies:
        for symbol in strategy.symbols:
            frame = data[symbol]
            if frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
                raise ValueError(f"{symbol}: history must have unique chronological timestamps")
            if len(frame.loc[frame.index < start]) < strategy.min_bars:
                raise ValueError(f"{symbol}: insufficient indicator warm-up for requested window")
            if "execution" in frame.attrs:
                execution = frame.attrs["execution"]
                validate_execution(execution, config.INSTRUMENTS[symbol].asset_class == "equity", start, end)
                if execution.index[0] > frame.index[0] or execution.index[-1] + pd.Timedelta(minutes=1) < end:
                    raise ValueError(f"{symbol}: execution data does not cover strategy history")
    return start, end


def fetch_history(strategies, months, cache_dir: Path, use_cache: bool) -> Dict[str, pd.DataFrame]:
    from bot.history_cache import MinuteHistory

    api = create_api()
    store = MinuteHistory(api, cache_dir / "minute_partitions")
    end = pd.Timestamp.now(tz="UTC").floor("min")
    data = {}
    for strategy in strategies:
        amount, unit = strategy.timeframe
        duration = amount * (1 if unit == "Min" else 60)
        start = end - pd.DateOffset(months=months) - pd.Timedelta(days=strategy.lookback_days + 7)
        for symbol in strategy.symbols:
            instrument = config.INSTRUMENTS[symbol]
            execution = store.get(symbol, instrument.asset_class, start, end, use_cache)
            bars = aggregate_equities(execution, duration) if instrument.asset_class == "equity" else aggregate_crypto(execution, duration)
            if bars.empty:
                raise ValueError(f"{symbol}: no complete strategy history")
            bars.attrs["execution"] = execution
            bars.attrs["corporate_actions"] = store.market.get_corporate_actions(symbol, start.date().isoformat(),
                end.date().isoformat()) if instrument.asset_class == "equity" else []
            data[symbol] = bars
            path = cache_dir / f"{symbol.replace('/', '')}_{amount}{unit}.csv"
            bars.to_csv(path)
            write_json(path.with_suffix(".json"), dict(version=5, symbol=symbol, timeframe=[amount, unit],
                start=str(start), end=str(end), feed=config.STOCK_DATA_FEED, source="minute_partitions",
                corporate_actions=bars.attrs["corporate_actions"]))
            log.info("%s: %d strategy bars from %d canonical minutes", symbol, len(bars), len(execution))
    return data


def generate_synthetic_history(strategies, months, seed=7) -> Dict[str, pd.DataFrame]:
    """Random-walk OHLCV for offline smoke tests. Says NOTHING about real performance."""
    rng = np.random.default_rng(seed)
    end = pd.Timestamp.now(tz="UTC").floor("D")
    warm = max(s.lookback_days for s in strategies)
    start = end - pd.DateOffset(months=months) - pd.Timedelta(days=warm)
    data = {}
    for strat in strategies:
        amount, unit = strat.timeframe
        minutes = amount * (1 if unit == "Min" else 60)
        for sym in strat.symbols:
            inst = config.INSTRUMENTS[sym]
            if inst.asset_class == "crypto":
                idx = pd.date_range(start, end, freq=f"{minutes}min", inclusive="left")
                per_day, daily_vol, p0 = 1440 / minutes, 0.035, 60000.0
            else:
                per_day = max(1, int(390 // minutes)) if minutes < 240 else 2
                starts = []
                table = schedule(start.date().isoformat(), end.date().isoformat())
                for _, session in table.iterrows():
                    starts += list(pd.date_range(session["open"], session["close"], freq=f"{minutes}min", inclusive="left"))
                idx = pd.DatetimeIndex(starts).tz_convert("UTC")
                daily_vol, p0 = 0.010, {"SPY": 500.0, "QQQ": 430.0, "GLD": 230.0, "USO": 75.0}.get(sym, 100.0)
            n = len(idx)
            sigma = daily_vol / math.sqrt(per_day)
            drift = np.repeat(rng.normal(0, sigma * 0.15, n // 150 + 1), 150)[:n]
            ret = rng.normal(0, sigma, n) + drift
            close = p0 * np.exp(np.cumsum(ret))
            open_ = np.concatenate([[p0], close[:-1]])
            if inst.asset_class == "equity":
                dates = idx.tz_convert(ET).normalize()
                overnight = np.concatenate([[False], dates[1:] != dates[:-1]])
                open_[overnight] *= np.exp(rng.normal(0, daily_vol * 0.4, overnight.sum()))
            wick = np.abs(rng.normal(0, sigma * 0.4, n))
            vol = rng.lognormal(3, 0.5, n) * (1 + 3 * (rng.random(n) < 0.05))
            data[sym] = pd.DataFrame({
                "open": open_, "high": np.maximum(open_, close) * (1 + wick),
                "low": np.minimum(open_, close) * (1 - wick), "close": close, "volume": vol}, index=idx)
            if inst.asset_class == "equity":
                _, session_closes = session_bounds(data[sym].index)
                nominal = data[sym].index + pd.Timedelta(minutes=minutes)
                data[sym]["bar_end"] = session_closes.where(session_closes < nominal, nominal)
    return data


# =============================================================================
# Reporting
# =============================================================================
HEADER = ["", "Trades", "Win %", "Avg win $", "Avg loss $", "PF", "MaxDD %", "Sharpe", "Return %", "B&H %"]
WIDTHS = [26, 7, 7, 11, 11, 7, 9, 8, 10, 9]


def _num(x, spec, suffix=""):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "n/a"
    if isinstance(x, float) and math.isinf(x):
        return "inf"
    return format(x, spec) + suffix


def _row(label, m, bh=None):
    cells = [label, str(m["trades"]), _num(m["win_rate"], ".1f"), _num(m["avg_win"], ",.0f"),
             _num(m["avg_loss"], ",.0f"), _num(m["profit_factor"], ".2f"), _num(m["max_dd"], ".2f"),
             _num(m["sharpe"], ".2f"), _num(m["total_return"], "+.2f"), _num(bh, "+.1f") if bh is not None else ""]
    return "".join(c.ljust(w) if i == 0 else c.rjust(w) for i, (c, w) in enumerate(zip(cells, WIDTHS)))


def _section(title, rows):
    lines = [f"  {title}"]
    lines += ["  " + r for r in rows]
    return lines


def format_summary(base: dict, metrics: dict, start, end, cfg: BtConfig, adjusted: Optional[dict] = None,
                   adj_metrics: Optional[dict] = None, synthetic=False) -> str:
    bar = "=" * (sum(WIDTHS) + 4)
    head = "  " + "".join(h.ljust(w) if i == 0 else h.rjust(w) for i, (h, w) in enumerate(zip(HEADER, WIDTHS)))
    lines = [bar, "BACKTEST SUMMARY" + ("  ** SYNTHETIC DATA - NOT REAL RESULTS **" if synthetic else ""),
             f"Window {start:%Y-%m-%d} -> {end:%Y-%m-%d} | start capital ${cfg.initial_capital:,.0f} | "
             f"slippage {cfg.slippage * 100:.2f}%/fill | commission ${cfg.commission:.0f}", bar, head, "  " + "-" * (sum(WIDTHS))]
    lines += _section("Per instrument (standalone)", [_row(s, metrics["instruments"][s], base["benchmarks"][s])
                                                       for s in base["instruments"]])
    lines += _section("Per strategy", [_row(n, metrics["strategies"][n]) for n in base["strategies"]])
    comb = base["combined"]
    lines += _section("Combined portfolio (correlation filter ON)", [_row("all 5 instruments", metrics["combined"])])
    if adjusted:
        lines += ["", "  ADJUSTED PARAMETERS (see caveats above)"]
        lines += _section("Per strategy", [_row(n + " *", adj_metrics["strategies"][n]) for n in adjusted["strategies"]])
        lines += _section("Combined portfolio", [_row("all 5 (adjusted)", adj_metrics["combined"])])
    lines += ["  " + "-" * (sum(WIDTHS)),
              f"  Combined: {comb.blocked_corr} entries blocked by correlation filter, {comb.blocked_daily} by daily-loss "
              f"limit, {comb.skipped_size} skipped (size/buying power); slippage paid ${comb.slippage_cost:,.0f}",
              f"  Fees ${comb.fees_cost:,.2f}; {comb.unfilled_stop_limits} crypto stop-limit triggers initially unfilled.",
              f"  Crypto fees: taker {100 * cfg.crypto_taker_fee:.3f}% / maker {100 * cfg.crypto_maker_fee:.3f}%.",
              f"  Funding ${comb.financing_cost:,.2f}; cash yield ${comb.cash_yield:,.2f}; rf={cfg.risk_free_rate:.2%}.",
              f"  CAGR {metrics['combined']['cagr']:.2f}%; turnover {metrics['combined']['turnover']:.2f}x; "
              f"average sampled gross exposure {100 * comb.average_gross_exposure:.1f}%.",
              f"  Forced end-of-test closes: {metrics['combined']['forced_closes']} (excluded from trade statistics).",
              f"  Sharpe 95% block-bootstrap interval: {metrics['combined']['sharpe_ci_95']}.",
              "  Full downside metrics, funding assumptions and portfolio benchmarks: backtest_metrics.json.",
              "  Avg win/loss in $; PF = gross profit / gross loss; B&H = buy-and-hold over the same window.", bar]
    return "\n".join(lines)


def plot_results(res: dict, path: Path, title: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    comb = res["combined"]
    fig, axes = plt.subplots(3, 1, figsize=(13, 13), gridspec_kw={"height_ratios": [3, 3, 2]})
    ax = axes[0]
    ax.plot(comb.equity.index, comb.equity.values, color="black", lw=1.6, label="Combined portfolio")
    ax.axhline(comb.initial, color="grey", ls="--", lw=0.8)
    ax.set_title(f"{title}\nCombined portfolio equity (correlation filter active)")
    ax.set_ylabel("Equity ($)")
    ax.legend(loc="upper left")
    ax.grid(alpha=0.3)

    ax = axes[1]
    for sym, r in res["instruments"].items():
        ax.plot(r.equity.index, (r.equity / r.initial - 1) * 100, lw=1.1, label=sym)
    ax.plot(comb.equity.index, (comb.equity / comb.initial - 1) * 100, color="black", lw=1.6, label="Combined")
    ax.axhline(0, color="grey", ls="--", lw=0.8)
    ax.set_title("Return by instrument (each run standalone on the full starting capital)")
    ax.set_ylabel("Return (%)")
    ax.legend(ncol=6, loc="upper left")
    ax.grid(alpha=0.3)

    ax = axes[2]
    dd = (comb.equity / comb.equity.cummax() - 1) * 100
    ax.fill_between(dd.index, dd.values, 0, color="firebrick", alpha=0.5)
    ax.set_title("Combined portfolio drawdown")
    ax.set_ylabel("Drawdown (%)")
    ax.grid(alpha=0.3)

    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


# =============================================================================
# CLI
# =============================================================================
def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Backtest the three strategies on historical Alpaca data.")
    ap.add_argument("--months", type=int, default=6)
    ap.add_argument("--capital", type=float, default=100_000.0)
    ap.add_argument("--slippage", type=float, default=0.0005, help="fraction per fill (0.0005 = 0.05%%)")
    ap.add_argument("--crypto-taker-fee", type=float, default=config.RISK["crypto_taker_fee"])
    ap.add_argument("--crypto-maker-fee", type=float, default=config.RISK["crypto_maker_fee"])
    ap.add_argument("--broker-stops-only", action="store_true")
    ap.add_argument("--use-cache", action="store_true",
                    help="reuse fresh minute caches; may contact Alpaca for refreshes and corporate actions")
    ap.add_argument("--synthetic", action="store_true", help="offline smoke test on random data")
    ap.add_argument("--no-adjust", action="store_true", help="never search for better parameters")
    ap.add_argument("--research-optimize", action="store_true", help="explicit walk-forward research; requires two years")
    ap.add_argument("--research-dir", type=Path, default=config.BASE_DIR / "research_runs")
    ap.add_argument("--risk-free-rate", type=float, default=0.0)
    ap.add_argument("--margin-rate", type=float, default=0.0)
    ap.add_argument("--short-borrow-rate", type=float, default=0.0)
    ap.add_argument("--cash-yield-rate", type=float, default=0.0)
    ap.add_argument("--out-dir", default=str(config.BASE_DIR))
    args = ap.parse_args(argv)
    if args.months <= 0:
        ap.error("--months must be positive")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    cfg = BtConfig(initial_capital=args.capital, slippage=args.slippage,
                   crypto_taker_fee=args.crypto_taker_fee, crypto_maker_fee=args.crypto_maker_fee,
                   crypto_software_stops=not args.broker_stops_only, risk_free_rate=args.risk_free_rate,
                   margin_rate=args.margin_rate, short_borrow_rate=args.short_borrow_rate,
                   cash_yield_rate=args.cash_yield_rate)
    strategies = [build_strategy(n) for n in STRATEGY_CLASSES]
    if args.synthetic:
        log.warning("*** SYNTHETIC DATA: this only checks that the machinery runs ***")
        data = generate_synthetic_history(strategies, args.months)
    else:
        data = fetch_history(strategies, args.months, out_dir / "backtest_data", args.use_cache)
    start, end = backtest_window(data, strategies, args.months)
    research = None
    base_end = end
    if args.research_optimize and not args.no_adjust:
        if args.synthetic or (end - start).days < 730:
            ap.error("Parameter research requires at least two years of nonsynthetic history")
        metadata = manifest(data, dict(simulation=vars(cfg), strategies={name: settings
            for name, (_, settings) in STRATEGY_CLASSES.items()}), start, end, config.BASE_DIR)
        research = ResearchRun(args.research_dir, metadata)
        base_end = start + (end - start) * .8
        try:
            research.claim_holdout("portfolio", list(data), start + (end - start) * .8, end)
        except Exception as exc:
            research.record("failed", {"reason": str(exc)})
            raise
    elif not args.synthetic:
        exposure = ResearchRun(args.research_dir, manifest(data, vars(cfg), start, end, config.BASE_DIR))
        exposure.expose_sample(list(data), start, end)
    log.info("Backtesting %s -> %s ...", start.date(), end.date())

    try:
        base = run_all(strategies, data, start, base_end, cfg)
    except Exception as exc:
        if research is not None:
            research.record("failed", {"reason": str(exc), "phase": "base_run"})
        raise
    base_m = {"instruments": {k: compute_metrics(v) for k, v in base["instruments"].items()},
              "strategies": {k: compute_metrics(v) for k, v in base["strategies"].items()},
              "combined": compute_metrics(base["combined"])}
    plot_results(base, out_dir / "backtest_results.png",
                 "Backtest" + (" (SYNTHETIC DATA)" if args.synthetic else "") + f" {start:%Y-%m-%d} to {base_end:%Y-%m-%d}")
    base["combined"].trades.to_csv(out_dir / "backtest_trades.csv", index=False)
    write_json(out_dir / "backtest_metrics.json", dict(metrics=base_m,
        portfolio_benchmarks=base["portfolio_benchmarks"], assumptions=vars(cfg),
        report_start=str(start), report_end=str(base_end)))

    adjusted = adj_m = None
    if research is not None:
        log.info("Explicit walk-forward research; final 20%% holdout reserved before reports")
        try:
            results = {name: optimize_strategy(name, data, start, end, cfg, research) for name in STRATEGY_CLASSES}
        except Exception as exc:
            research.record("failed", {"reason": str(exc)})
            raise
        write_json(research.directory / "results.json", results)
        research.record("completed", results)
        write_json(out_dir / "backtest_research_trials.json", dict(run_id=research.identity,
            archive=str(research.directory), results=results))
        for n, info in results.items():
            log.info("  %s: tested %d parameter sets -> %s", n, info["candidates_tested"],
                     "ADOPTED " + json.dumps(info["overrides"]) if info["adopted"] else "no change (" + info["reason"] + ")")
        overrides = {n: i["overrides"] for n, i in results.items() if i["adopted"]}
        if overrides:
            log.info("  CAVEAT: the full-window re-run includes parameter-selection data and is exploratory. "
                     "Treat adjusted parameters as hypotheses to forward-test on paper, not as proven improvements.")
            adj_strats = [build_strategy(n, overrides.get(n)) for n in STRATEGY_CLASSES]
            adjusted = run_all(adj_strats, data, start, end, cfg)
            adj_m = {"strategies": {n: compute_metrics(adjusted["strategies"][n]) for n in overrides},
                     "combined": compute_metrics(adjusted["combined"])}
            adjusted["strategies"] = {n: adjusted["strategies"][n] for n in overrides}
            plot_results(adjusted, out_dir / "backtest_results_adjusted.png", "ADJUSTED parameters")
            write_json(out_dir / "backtest_suggested_params.json", overrides)
            log.info("  Suggested values saved to backtest_suggested_params.json (config.py is NOT modified).")

    print()
    print(format_summary(base, base_m, start, base_end, cfg, adjusted, adj_m, synthetic=args.synthetic))
    print(f"\nChart: {out_dir / 'backtest_results.png'}   Trades: {out_dir / 'backtest_trades.csv'}")


if __name__ == "__main__":
    main()
