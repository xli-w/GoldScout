import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd

from bot import indicators
from bot.backtest import Backtester, BtConfig
from bot.portfolio import Portfolio, Position
from bot.risk_manager import RiskManager
from bot.strategies.mean_reversion import MeanReversionStrategy


class ReviewTwoRepairs(unittest.TestCase):
    def portfolio(self):
        with patch.object(Portfolio, "_load_state"):
            portfolio = Portfolio(Mock(), RiskManager())
        portfolio._save_state = Mock()
        portfolio._cancel_orders_for = Mock()
        portfolio._submit = Mock()
        position = Position("SPY", "equity", "mean_reversion", "long", 100., 100.,
                            "2026-11-13T14:30:00Z", 2., 98., 100., stop_order_id="stop-1")
        portfolio.positions["SPY"] = position
        return portfolio, position

    def test_wrong_side_close_preserves_orders(self):
        portfolio, _ = self.portfolio()
        portfolio.broker_positions = Mock(return_value={"SPY": SimpleNamespace(qty="-100")})
        self.assertFalse(portfolio.close_position("SPY", "test"))
        portfolio._cancel_orders_for.assert_not_called()
        portfolio._submit.assert_not_called()
        self.assertTrue(portfolio.accounting_unresolved)

    def test_side_changes_during_cancellation_do_not_submit(self):
        portfolio, _ = self.portfolio()
        portfolio.broker_positions = Mock(side_effect=[
            {"SPY": SimpleNamespace(qty="100")}, {"SPY": SimpleNamespace(qty="-100")}])
        self.assertFalse(portfolio.close_position("SPY", "test"))
        portfolio._submit.assert_not_called()

    def test_wrong_side_monitor_does_not_liquidate(self):
        portfolio, _ = self.portfolio()
        portfolio.reconcile = Mock(return_value={"SPY": SimpleNamespace(qty="-100", current_price="97")})
        portfolio.market_is_open = Mock(return_value=True)
        portfolio._ensure_protection = Mock()
        portfolio.close_position = Mock()
        portfolio.manage_positions()
        portfolio._ensure_protection.assert_not_called()
        portfolio.close_position.assert_not_called()

    def test_attributed_history_works_through_backtest_constructor(self):
        index = pd.date_range("2026-11-13 14:30", periods=40, freq="15min", tz="UTC")
        frame = pd.DataFrame(dict(open=100., high=101., low=99., close=100., volume=10.), index=index)
        expected = indicators.atr(frame)
        execution = frame.iloc[:2].copy()
        frame.attrs["execution"] = execution
        frame.attrs["corporate_actions"] = []
        pd.testing.assert_series_equal(indicators.atr(frame), expected)
        bt = Backtester({"SPY": frame}, [MeanReversionStrategy()], BtConfig())
        self.assertIn("SPY", bt.execution)
        self.assertTrue(pd.Series(bt.atr["SPY"]).dropna().eq(2.).all())
        self.assertIs(frame.attrs["execution"], execution)

    def test_new_stop_fill_refreshes_stale_position_snapshot(self):
        portfolio, position = self.portfolio()
        portfolio.broker_positions = Mock(side_effect=[
            {"SPY": SimpleNamespace(qty="100", avg_entry_price="100")},
            {"SPY": SimpleNamespace(qty="30", avg_entry_price="100")}])
        portfolio._get_order = Mock(return_value=SimpleNamespace(
            id="stop-1", status="partially_filled", filled_qty="70", filled_avg_price="98"))
        portfolio.reconcile()
        self.assertEqual(position.qty, 30)
        self.assertEqual(position.stop_filled_qty, 70)
        self.assertEqual(portfolio._pending_exit["profit_loss"], -140)

    def test_persistently_stale_snapshot_does_not_restore_exited_quantity(self):
        portfolio, position = self.portfolio()
        portfolio.broker_positions = Mock(return_value={"SPY": SimpleNamespace(qty="100", avg_entry_price="100")})
        portfolio._get_order = Mock(return_value=SimpleNamespace(
            id="stop-1", status="partially_filled", filled_qty="70", filled_avg_price="98"))
        portfolio.reconcile()
        self.assertEqual(position.qty, 30)
        self.assertTrue(portfolio.accounting_unresolved)
        self.assertEqual(portfolio._pending_exit["position_size"], 70)

    def test_cancel_fill_and_market_residual_are_both_accounted(self):
        portfolio, position = self.portfolio()
        del portfolio._cancel_orders_for
        portfolio._list_open_orders = Mock(return_value=[SimpleNamespace(id="stop-1", symbol="SPY")])
        terminal = SimpleNamespace(id="stop-1", status="canceled", filled_qty="70", filled_avg_price="98")
        portfolio._cancel_and_confirm = Mock(return_value=terminal)
        portfolio.broker_positions = Mock(side_effect=[
            {"SPY": SimpleNamespace(qty="100")}, {"SPY": SimpleNamespace(qty="30")}, {}])
        portfolio._submit = Mock(return_value=SimpleNamespace(id="exit-1"))
        portfolio._wait_for_fill = Mock(return_value=SimpleNamespace(
            id="exit-1", status="filled", filled_qty="30", filled_avg_price="101"))
        rows = []

        def capture_exit(data=None, order=None):
            if portfolio._pending_exit is not None:
                rows.append(dict(portfolio._pending_exit))
                portfolio._pending_exit = None

        portfolio._save_state.side_effect = capture_exit
        self.assertTrue(portfolio.close_position("SPY", "test"))
        self.assertEqual([row["position_size"] for row in rows], [70, 30])
        self.assertEqual([row["exit_price"] for row in rows], [98, 101])
        self.assertEqual(sum(row["profit_loss"] for row in rows), -110)
        self.assertNotIn("SPY", portfolio.positions)

    def test_repeated_cancellation_fill_is_not_recorded_twice(self):
        portfolio, position = self.portfolio()
        terminal = SimpleNamespace(id="stop-1", filled_qty="70", filled_avg_price="98")
        rows = []

        def capture_exit(data=None, order=None):
            if portfolio._pending_exit is not None:
                rows.append(dict(portfolio._pending_exit))
                portfolio._pending_exit = None

        portfolio._save_state.side_effect = capture_exit
        portfolio._account_stop_fill(position, terminal, "first")
        portfolio._account_stop_fill(position, terminal, "repeat")
        self.assertEqual(position.qty, 30)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["position_size"], 70)

    def test_quantity_mismatch_does_not_resize_protection(self):
        portfolio, position = self.portfolio()
        position.qty = 30
        portfolio.broker_positions = Mock(return_value={"SPY": SimpleNamespace(qty="100")})
        portfolio._place_server_stop = Mock()
        portfolio._ensure_protection(position)
        self.assertEqual(position.qty, 30)
        self.assertTrue(portfolio.accounting_unresolved)
        portfolio._cancel_orders_for.assert_not_called()
        portfolio._place_server_stop.assert_not_called()


if __name__ == "__main__":
    unittest.main()