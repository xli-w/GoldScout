import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd

import config
from bot.main import TradingBot
from bot.strategies.base import Action, Signal
from bot.backtest import Backtester, BtPosition, BtConfig
from bot.risk_manager import RiskManager
import numpy as np


class MonitorRepairs(unittest.TestCase):
    def test_updated_equity_trail_is_enforced_at_same_observation(self):
        self.check_updated_trail("SPY", "long", 120., 90.)

    def test_updated_short_trail_is_enforced_at_same_observation(self):
        self.check_updated_trail("SPY", "short", 80., 110.)

    def test_updated_crypto_trail_is_enforced_at_same_observation(self):
        self.check_updated_trail("BTC/USD", "long", 120., 90.)

    def test_new_stop_is_not_applied_to_prior_minute_range(self):
        timestamp = pd.Timestamp("2026-11-13 15:01", tz="UTC")
        engine = Backtester.__new__(Backtester)
        engine.cfg, engine.risk = BtConfig(), RiskManager()
        engine.feature_index = {}
        engine.execution = {"SPY": pd.DataFrame(dict(open=100., high=106., low=94., close=105.),
                                               index=[timestamp - pd.Timedelta(minutes=1)])}
        position = BtPosition("SPY", "trend", "long", 1., 100., timestamp,
                              1., 90., 105., 2., trailing_stop=90.)
        engine.positions = {"SPY": position}
        engine._close = Mock()
        engine._monitor_execution("SPY", 0, timestamp, range_only=True)
        engine._monitor_execution("SPY", 0, timestamp, skip_range=True)
        self.assertEqual(position.trailing_stop, 103.)
        engine._close.assert_not_called()

    def check_updated_trail(self, symbol, side, extreme, hard_stop):
        timestamp = pd.Timestamp("2026-11-13 15:01", tz="UTC")
        engine = Backtester.__new__(Backtester)
        engine.cfg, engine.risk = BtConfig(), RiskManager()
        engine.feature_index = {}
        engine.execution = {symbol: pd.DataFrame(dict(open=100., high=101., low=99., close=100.),
                                                index=[timestamp - pd.Timedelta(minutes=1)])}
        engine.positions = {symbol: BtPosition(symbol, "trend", side, 1., 100., timestamp,
                                               1., hard_stop, extreme, 2., trailing_stop=hard_stop)}
        engine._close = Mock()
        engine._monitor_execution(symbol, 0, timestamp)
        engine._close.assert_called_once_with(symbol, 100., timestamp, "minute software stop after update")

    def test_inconsistent_execution_boundary_is_rejected(self):
        index = pd.date_range("2026-11-13 14:30", periods=2, freq="15min", tz="UTC")
        frame = pd.DataFrame(dict(open=100., high=101., low=99., close=100., volume=15.), index=index)
        execution_index = pd.date_range(index[0], periods=30, freq="min")
        execution = pd.DataFrame(dict(open=100., high=101., low=99., close=100., volume=1.), index=execution_index)
        execution.loc[index[0], "open"] = 110.
        frame.attrs["execution"] = execution
        strat = SimpleNamespace(name="test", symbols=["SPY"], timeframe=(15, "Min"))
        with self.assertRaisesRegex(ValueError, "boundary prices disagree"):
            Backtester({"SPY": frame}, [strat])

    def test_inconsistent_execution_volume_is_rejected(self):
        index = pd.date_range("2026-11-13 14:30", periods=2, freq="15min", tz="UTC")
        frame = pd.DataFrame(dict(open=100., high=101., low=99., close=100., volume=15.), index=index)
        execution_index = pd.date_range(index[0], periods=30, freq="min")
        frame.attrs["execution"] = pd.DataFrame(dict(open=100., high=101., low=99., close=100., volume=2.),
                                               index=execution_index)
        strat = SimpleNamespace(name="test", symbols=["SPY"], timeframe=(15, "Min"))
        with self.assertRaisesRegex(ValueError, "aggregated volume values disagree"):
            Backtester({"SPY": frame}, [strat])

    def test_completed_atr_is_published_before_updated_stop_enforcement(self):
        index = pd.date_range("2026-11-13 14:30", periods=2, freq="15min", tz="UTC")
        frame = pd.DataFrame(dict(open=100., high=101., low=99., close=100., volume=15.), index=index)
        execution_index = pd.date_range(index[0], periods=30, freq="min")
        frame.attrs["execution"] = pd.DataFrame(dict(open=100., high=101., low=99., close=100., volume=1.),
                                                index=execution_index)
        strat = SimpleNamespace(name="test", symbols=["SPY"], timeframe=(15, "Min"), lookback_days=10,
                                min_bars=1, trailing_atr_mult=2.,
                                evaluate=lambda *args, **kwargs: Signal(Action.HOLD, "SPY"))
        engine = Backtester({"SPY": frame}, [strat], BtConfig(slippage=0))
        engine.atr["SPY"] = np.array([1., 1.])
        original = engine._on_open

        def inject(symbol, position):
            original(symbol, position)
            if position == 0:
                engine.positions[symbol] = BtPosition(symbol, "test", "long", 1., 100., index[0],
                                                     10., 90., 105., 2., trailing_stop=90.)

        engine._on_open = inject
        result = engine.run(["SPY"], index[0], index[-1] + pd.Timedelta(minutes=15))
        self.assertEqual(result.trades.exit_time.iloc[0], index[1])
        self.assertEqual(result.trades.exit_price.iloc[0], 100.)
        self.assertEqual(result.trades.exit_reason.iloc[0], "minute software stop after update")

    def test_stale_crypto_breakdown_closes_long_without_entry(self):
        bot = TradingBot.__new__(TradingBot)
        bot.risk = SimpleNamespace(atr_period=14)
        bot.portfolio = Mock()
        bot.portfolio.positions = {"BTC/USD": SimpleNamespace(side="long", atr=2.)}
        bot.portfolio.last_bar_ts = {}
        bot.portfolio.current_price.return_value = 100.
        strat = SimpleNamespace(name="momentum_breakout", min_bars=14, timeframe=(1, "Hour"),
                                evaluate=lambda *args: Signal(Action.ENTER_SHORT, "BTC/USD", "breakdown"))
        index = pd.date_range("2026-11-01", periods=20, freq="h", tz="UTC")
        bars = pd.DataFrame(dict(open=100., high=101., low=99., close=100., volume=10.), index=index)
        bot._process_symbol(strat, config.INSTRUMENTS["BTC/USD"], bars)
        bot.portfolio.close_position.assert_called_once()
        bot.portfolio.open_position.assert_not_called()
        bot.portfolio.get_account.assert_not_called()
        bot.portfolio.mark_bar.assert_called_once()

    def test_stale_flat_entry_is_consumed_without_order(self):
        bot = TradingBot.__new__(TradingBot)
        bot.risk = SimpleNamespace(atr_period=14)
        bot.portfolio = Mock()
        bot.portfolio.positions, bot.portfolio.last_bar_ts = {}, {}
        strat = SimpleNamespace(name="momentum_breakout", min_bars=14, timeframe=(1, "Hour"),
                                evaluate=lambda *args: Signal(Action.ENTER_LONG, "BTC/USD", "breakout"))
        index = pd.date_range("2026-11-01", periods=20, freq="h", tz="UTC")
        bars = pd.DataFrame(dict(open=100., high=101., low=99., close=100., volume=10.), index=index)
        bot._process_symbol(strat, config.INSTRUMENTS["BTC/USD"], bars)
        bot.portfolio.close_position.assert_not_called()
        bot.portfolio.open_position.assert_not_called()
        bot.portfolio.mark_bar.assert_called_once()


if __name__ == "__main__":
    unittest.main()