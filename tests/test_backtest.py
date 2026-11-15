import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock

import numpy as np
import pandas as pd

from bot.backtest import Backtester, BtConfig, BtPosition, Pending, main
from bot.risk_manager import RiskManager
from bot.strategies.base import Action, Signal


def bars(timestamps, prices):
    return pd.DataFrame({"open": prices, "high": np.array(prices) + 0.1,
                         "low": np.array(prices) - 0.1, "close": prices,
                         "volume": 100}, index=pd.to_datetime(timestamps, utc=True))


def strategy(symbol, timeframe):
    return Mock(symbols=[symbol], timeframe=timeframe, lookback_days=10, min_bars=1,
                trailing_atr_mult=None, evaluate=Mock(return_value=Signal(Action.HOLD, symbol)))


class BacktestTests(unittest.TestCase):
    def test_cache_help_warns_that_broker_access_is_possible(self):
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit) as stopped:
            main(["--help"])
        self.assertEqual(stopped.exception.code, 0)
        self.assertIn("may contact Alpaca", " ".join(output.getvalue().split()))

    def test_crypto_round_trip_debits_asset_and_cash_fees(self):
        data = {"BTC/USD": bars(["2026-11-12 13:00", "2026-11-12 14:00"], [100, 100])}
        bt = Backtester(data, [strategy("BTC/USD", (1, "Hour"))],
                        BtConfig(slippage=0, corr_filter=False))
        bt.run(["BTC/USD"], data["BTC/USD"].index[0], data["BTC/USD"].index[-1])
        timestamp = data["BTC/USD"].index[-1]
        bt._open("BTC/USD", "long", 100, timestamp, 10)
        self.assertAlmostEqual(bt.positions["BTC/USD"].qty, 99.75)
        bt._close("BTC/USD", 100, timestamp, "fee check")
        self.assertAlmostEqual(bt.cash, 100000 - 49.9375)
        self.assertAlmostEqual(bt.trades[-1]["pnl"], -49.9375)
        self.assertAlmostEqual(bt.fees_cost, 49.9375)

    def test_gap_through_crypto_limit_remains_unfilled(self):
        bt = Backtester.__new__(Backtester)
        bt.risk = RiskManager()
        bt.cfg = BtConfig(crypto_software_stops=False)
        bt.unfilled_stop_limits = 0
        timestamp = pd.Timestamp("2026-11-12 14:00", tz="UTC")
        pos = BtPosition("BTC/USD", "momentum_breakout", "long", 1, 105,
                         timestamp, 5, 100, 105, None)
        bt.positions = {"BTC/USD": pos}
        bt._close = Mock()
        bt._crypto_stop("BTC/USD", 98, 98, 97, timestamp)
        bt._close.assert_not_called()
        self.assertTrue(pos.stop_triggered)
        self.assertEqual(pos.stop_limit_price, 99)
        self.assertEqual(bt.unfilled_stop_limits, 1)
        bt._crypto_stop("BTC/USD", 98, 99.5, 97, timestamp + pd.Timedelta(hours=1))
        self.assertGreaterEqual(bt._close.call_args.kwargs["fill_override"], 99)

    def test_mixed_timeframe_entry_does_not_use_future_close(self):
        data = {
            "SPY": bars(["2026-11-12 14:30", "2026-11-12 14:45", "2026-11-12 15:00",
                         "2026-11-12 15:15", "2026-11-12 15:30", "2026-11-12 15:45"],
                        [100, 100, 100, 100, 120, 120]),
            "BTC/USD": bars(["2026-11-12 14:00", "2026-11-12 15:00"], [500, 500])}
        bt = Backtester(data, [strategy("SPY", (15, "Min")), strategy("BTC/USD", (1, "Hour"))],
                        BtConfig(slippage=0, corr_filter=False))
        bt.atr = {symbol: np.full(len(frame), 2.0) for symbol, frame in data.items()}
        observed = []
        original_open = bt._open

        def capture_open(symbol, side, price, timestamp, atr):
            observed.append((symbol, timestamp, bt._equity()))
            original_open(symbol, side, price, timestamp, atr)

        def decide(symbol, index, close, atr):
            if index == 0:
                bt.pending[symbol] = Pending(open_side="long", atr=atr)

        bt._open = capture_open
        bt._decide = decide
        result = bt.run(["SPY", "BTC/USD"], pd.Timestamp("2026-11-12 14:00", tz="UTC"),
                pd.Timestamp("2026-11-12 16:00", tz="UTC"))
        btc_entry = next(item for item in observed if item[0] == "BTC/USD")
        self.assertEqual(btc_entry[1], pd.Timestamp("2026-11-12 15:00", tz="UTC"))
        self.assertAlmostEqual(btc_entry[2], 100000)
        self.assertTrue(result.equity.index.is_monotonic_increasing)
        self.assertTrue((result.trades.exit_time >= result.trades.entry_time).all())


if __name__ == "__main__":
    unittest.main()