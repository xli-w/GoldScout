import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import pandas as pd

from bot.data import MarketData
from bot.sessions import aggregate_equities, validate_execution
from bot.corporate_actions import adjusted_window, continuous_prices, ordered_actions
from bot.backtest import Backtester, BtConfig, Pending, buy_and_hold
from bot.strategies.base import Signal, Action
from types import SimpleNamespace
import numpy as np


def minutes(start, end):
    index = pd.date_range(start, end, freq="min", inclusive="left", tz="UTC")
    return pd.DataFrame(dict(open=100., high=101., low=99., close=100., volume=1.), index=index)


class DataRepairs(unittest.TestCase):
    def paged_actions(self, conflict=False):
        dividend = dict(id="dividend", symbol="SPY", ex_date="2026-11-13", payable_date="2026-11-16", rate=1.)
        split = dict(id="split", symbol="SPY", ex_date="2026-11-13", new_rate=2., old_rate=1.)
        api = Mock()
        second = {"cash_dividends": [dict(dividend, rate=2.)]} if conflict else {"forward_splits": [split]}
        api.data_get.side_effect = [dict(corporate_actions={"cash_dividends": [dividend]}, next_page_token="next"),
                                    dict(corporate_actions=second, next_page_token=None)]
        return MarketData(api).get_corporate_actions("SPY", "2026-11-01", "2026-11-20")

    def test_paged_actions_sort_split_before_dividend(self):
        actions = self.paged_actions()
        self.assertEqual([action["kind"] for action in actions], ["split", "dividend"])
        index = pd.to_datetime(["2026-11-12 14:30", "2026-11-13 14:30"], utc=True)
        frame = pd.DataFrame(dict(open=[100., 49.], high=[100., 49.], low=[100., 49.],
                                  close=[100., 49.], volume=[10., 20.]), index=index)
        continuous, _ = continuous_prices(frame, list(reversed(actions)))
        self.assertEqual(continuous.close.iloc[-1], 100.)
        adjusted = adjusted_window(frame, list(reversed(actions)), index[-1])
        self.assertEqual(adjusted.close.iloc[0], 49.)

    def test_conflicting_page_ids_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Conflicting corporate action ID"):
            self.paged_actions(conflict=True)

    def test_duplicate_identical_actions_are_accounted_once(self):
        actions = self.paged_actions()
        self.assertEqual(len(ordered_actions(actions + actions)), 2)

    def test_actual_simulator_and_benchmark_share_action_order(self):
        actions = self.paged_actions()
        index = pd.to_datetime(["2026-11-12 14:30", "2026-11-12 14:45",
                                "2026-11-13 14:30", "2026-11-13 14:45"], utc=True)
        frame = pd.DataFrame(dict(open=[100., 100., 49., 49.], high=[100., 100., 49., 49.],
                                  low=[100., 100., 49., 49.], close=[100., 100., 49., 49.], volume=10.), index=index)
        frame.attrs["corporate_actions"] = list(reversed(actions))
        strat = SimpleNamespace(name="test", symbols=["SPY"], timeframe=(15, "Min"), lookback_days=10,
                                min_bars=1, trailing_atr_mult=None,
                                evaluate=lambda *args, **kwargs: Signal(Action.HOLD, "SPY"))
        bt = Backtester({"SPY": frame}, [strat], BtConfig(slippage=0))
        bt.atr["SPY"] = np.full(4, 10.)

        def decide(symbol, position, price, atr):
            if position == 0:
                bt.pending[symbol] = Pending(open_side="long", atr=10.)

        bt._decide = decide
        bt.run(["SPY"], index[0], index[-1] + pd.Timedelta(minutes=15))
        self.assertEqual(bt.receivables["dividend"], 200.)
        self.assertAlmostEqual(buy_and_hold(frame, index[0], index[-1]), 0.)

    def test_partial_candle_does_not_invent_earlier_open(self):
        frame = minutes("2026-11-13 17:00", "2026-11-13 21:00")
        result = aggregate_equities(frame, 240)
        self.assertEqual(list(result.index), [pd.Timestamp("2026-11-13 18:30", tz="UTC")])
        self.assertEqual(result.bar_end.iloc[0], pd.Timestamp("2026-11-13 21:00", tz="UTC"))

    def test_sparse_noon_only_session_fails_validation(self):
        frame = minutes("2026-11-13 17:00", "2026-11-13 17:01")
        with self.assertRaisesRegex(ValueError, "session boundary"):
            validate_execution(frame, True)
        self.assertTrue(aggregate_equities(frame, 240).empty)

    def test_complete_early_close_remains_valid(self):
        frame = minutes("2026-11-27 14:30", "2026-11-27 18:00")
        validate_execution(frame, True)
        result = aggregate_equities(frame, 240)
        self.assertEqual(len(result), 1)
        self.assertEqual(result.bar_end.iloc[0], pd.Timestamp("2026-11-27 18:00", tz="UTC"))

    def test_explicit_partial_session_interval_can_be_validated(self):
        frame = minutes("2026-11-13 17:00", "2026-11-13 18:00")
        validate_execution(frame, True, frame.index[0], pd.Timestamp("2026-11-13 18:00", tz="UTC"))

    def test_extended_hours_only_returns_empty_completed_schema(self):
        class Frozen(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 11, 13, 12, tzinfo=timezone.utc)

        market = MarketData(Mock())
        market._fetch = Mock(return_value=minutes("2026-11-13 11:00", "2026-11-13 11:10"))
        market.get_corporate_actions = Mock(return_value=[])
        with patch("bot.data.datetime", Frozen):
            result = market.get_completed_bars("SPY", "equity", (4, "Hour"), 1)
        self.assertTrue(result.empty)
        self.assertIn("bar_end", result)
        self.assertIsNotNone(result.bar_end.dt.tz)

    def test_empty_api_response_has_stable_equity_schema(self):
        market = MarketData(Mock())
        market._fetch = Mock(return_value=pd.DataFrame())
        result = market.get_completed_bars("SPY", "equity", (4, "Hour"), 1)
        self.assertTrue(result.empty)
        self.assertIsNotNone(result.index.tz)
        self.assertIsNotNone(result.bar_end.dt.tz)
        self.assertIn("execution", result.attrs)

    def test_missing_final_minute_drops_candle(self):
        frame = minutes("2026-11-13 14:30", "2026-11-13 14:44")
        self.assertTrue(aggregate_equities(frame, 15).empty)

    def test_crypto_execution_interval_rejects_missing_boundary(self):
        frame = minutes("2026-11-13 14:31", "2026-11-13 14:45")
        with self.assertRaisesRegex(ValueError, "opening boundary"):
            validate_execution(frame, False, pd.Timestamp("2026-11-13 14:30", tz="UTC"),
                               pd.Timestamp("2026-11-13 14:45", tz="UTC"))


if __name__ == "__main__":
    unittest.main()