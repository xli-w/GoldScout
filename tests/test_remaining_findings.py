import math
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import pandas as pd

import config
from bot.ledger import Ledger
from bot.portfolio import Portfolio, Position
from bot.risk_manager import RiskManager
from bot.statistics import sharpe_interval
from bot.backtest import optimize_strategy, BtConfig


class RemainingFindingsTests(unittest.TestCase):
    def test_partial_exit_restart_exports_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(config, "LEDGER_FILE", root / "ledger.sqlite3"), \
                    patch.object(config, "STATE_FILE", root / "state.json"), \
                    patch.object(config, "DAILY_PNL_CSV", root / "daily.csv"), \
                    patch.object(config, "TRADES_CSV", root / "trades.csv"):
                portfolio = Portfolio(Mock(), RiskManager())
                position = Position("SPY", "equity", "mean_reversion", "long", 100., 100.,
                                    "2026-11-12T14:30:00Z", 2., 98., 100., identity="entry-1")
                portfolio.positions["SPY"] = position
                portfolio._record_close(position, 101., "partial", 70.)
                restored = Portfolio(Mock(), RiskManager())
                self.assertEqual(restored.positions["SPY"].qty, 30.)
                self.assertEqual(restored.realized_today, 70.)
                self.assertEqual(len((root / "trades.csv").read_text().splitlines()), 2)
                restored._ledger.export(root / "trades.csv", __import__("bot.trade_logger", fromlist=["TRADE_FIELDS"]).TRADE_FIELDS)
                self.assertEqual(len((root / "trades.csv").read_text().splitlines()), 2)

    def test_nonfinite_risk_inputs_are_rejected(self):
        risk = RiskManager()
        self.assertEqual(risk.size_position(100000, 100, math.nan, "equity", 100000, 0).qty, 0)
        self.assertEqual(risk.size_position(100000, 100, 2, "equity", -1, 0).qty, 0)
        with self.assertRaises(ValueError):
            risk.hard_stop_price("long", 100, 2, 0, 100000)

    def test_ledger_exit_and_state_commit_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = Ledger(Path(directory) / "ledger.sqlite3")
            ledger.commit({"positions": {}}, {"execution_id": "exit-1", "profit_loss": 10})
            with self.assertRaises(Exception):
                ledger.commit({"positions": {"SPY": 10}}, {"execution_id": "exit-1"})
            self.assertEqual(ledger.load(), {"positions": {}})
            export = Path(directory) / "exits.csv"
            ledger.export(export, ["execution_id", "profit_loss"])
            ledger.export(export, ["execution_id", "profit_loss"])
            self.assertEqual(len(export.read_text().splitlines()), 2)

    def test_transfer_adjusted_loss_latches_until_next_day(self):
        with patch.object(Portfolio, "_load_state"):
            portfolio = Portfolio(Mock(), RiskManager())
        portfolio._save_state = Mock()
        portfolio._write_daily_row = Mock()
        portfolio.day = datetime.now(ZoneInfo(config.MARKET_TZ)).date().isoformat()
        portfolio.day_start_equity = 100000
        portfolio.api.get_activities.return_value = [SimpleNamespace(id="deposit", net_amount="10000")]
        portfolio.update_daily(106000)
        self.assertEqual(portfolio.api.get_activities.call_args.kwargs["activity_types"], ["CSD", "CSW"])
        self.assertTrue(portfolio.daily_halted)
        self.assertEqual(portfolio.daily_cash_flow, 10000)
        portfolio.update_daily(111000)
        self.assertTrue(portfolio.daily_halted)
        self.assertEqual(portfolio.daily_cash_flow, 10000)

    def test_short_history_cannot_select_parameters(self):
        result = optimize_strategy("mean_reversion", {}, pd.Timestamp("2026-01-01", tz="UTC"),
                                   pd.Timestamp("2026-07-01", tz="UTC"), BtConfig())
        self.assertFalse(result["adopted"])
        self.assertEqual(result["candidates_tested"], 0)
        self.assertEqual(sharpe_interval(pd.Series([.01, -.01]), 252), [None, None])


if __name__ == "__main__":
    unittest.main()