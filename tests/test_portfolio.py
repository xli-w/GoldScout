import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bot.portfolio import Portfolio, Position
from bot.risk_manager import RiskManager


class PortfolioTests(unittest.TestCase):
    def setUp(self):
        with patch.object(Portfolio, "_load_state"):
            self.portfolio = Portfolio(Mock(), RiskManager())
        self.portfolio._save_state = Mock()
        self.portfolio._cancel_orders_for = Mock()
        self.portfolio._place_server_stop = Mock()
        self.position = Position("SPY", "equity", "mean_reversion", "long", 100,
                                 100, "2026-11-12T14:30:00Z", 2, 98, 100,
                                 stop_order_id="stop-1", server_stop=98)
        self.portfolio.positions["SPY"] = self.position
        self.portfolio._submit = Mock(return_value=SimpleNamespace(id="exit-1"))

    def test_partial_exit_retains_and_protects_residual(self):
        self.portfolio.broker_positions = Mock(side_effect=[
            {"SPY": SimpleNamespace(qty="100")}, {"SPY": SimpleNamespace(qty="100")},
            {"SPY": SimpleNamespace(qty="30")}])
        self.portfolio._wait_for_fill = Mock(return_value=SimpleNamespace(
            status="canceled", filled_qty="70", filled_avg_price="101"))
        self.assertFalse(self.portfolio.close_position("SPY", "partial"))
        self.assertEqual(self.portfolio.positions["SPY"].qty, 30)
        self.assertEqual(self.portfolio._pending_exit["position_size"], 70)
        self.assertEqual(self.portfolio._pending_exit["profit_loss"], 70)
        self.portfolio._place_server_stop.assert_called_once_with(self.position, 98)

    def test_full_exit_requires_broker_flatness(self):
        self.portfolio.broker_positions = Mock(side_effect=[
            {"SPY": SimpleNamespace(qty="100")}, {"SPY": SimpleNamespace(qty="100")}, {}])
        self.portfolio._wait_for_fill = Mock(return_value=SimpleNamespace(
            status="filled", filled_qty="100", filled_avg_price="101"))
        self.assertTrue(self.portfolio.close_position("SPY", "full"))
        self.assertNotIn("SPY", self.portfolio.positions)
        self.assertEqual(self.portfolio._pending_exit["position_size"], 100)

    def test_submission_failure_recovers_protection(self):
        self.portfolio.broker_positions = Mock(return_value={"SPY": SimpleNamespace(qty="100")})
        self.portfolio._submit = Mock(side_effect=RuntimeError("submission failed"))
        self.portfolio.api.get_order_by_client_order_id.return_value = SimpleNamespace(id="exit-1")
        self.portfolio._cancel_and_confirm = Mock(return_value=SimpleNamespace(
            status="rejected", filled_qty="0"))
        self.assertFalse(self.portfolio.close_position("SPY", "failure"))
        self.portfolio._place_server_stop.assert_called_once_with(self.position, 98)

    def test_cumulative_exit_fills_are_accounted_once(self):
        order = SimpleNamespace(filled_qty="70", filled_avg_price="101")
        self.portfolio._account_exit_fill(self.position, order, "first")
        self.portfolio._account_exit_fill(self.position, order, "repeat")
        self.assertEqual(self.position.qty, 30)
        self.assertEqual(self.portfolio._pending_exit["position_size"], 70)
        order = SimpleNamespace(filled_qty="100", filled_avg_price="101.3")
        self.portfolio._account_exit_fill(self.position, order, "last")
        self.assertEqual(self.portfolio._pending_exit["exit_price"], 102)
        self.assertEqual(self.portfolio._pending_exit["profit_loss"], 60)

    def test_unresolved_cancellation_blocks_entries(self):
        self.portfolio.broker_positions = Mock(return_value={"SPY": SimpleNamespace(qty="100")})
        self.portfolio._cancel_orders_for = Mock(side_effect=TimeoutError("pending cancel"))
        self.assertFalse(self.portfolio.close_position("SPY", "failure"))
        self.assertFalse(self.portfolio.entries_safe())
        self.portfolio._submit.assert_not_called()
        self.portfolio._place_server_stop.assert_not_called()

    def test_cancellation_waits_for_terminal_state(self):
        self.portfolio._get_order = Mock(side_effect=[
            SimpleNamespace(id="stop-1", status="new", filled_qty="0"),
            SimpleNamespace(id="stop-1", status="pending_cancel", filled_qty="0"),
            SimpleNamespace(id="stop-1", status="canceled", filled_qty="0")])
        with patch("bot.portfolio.time.sleep"):
            result = self.portfolio._cancel_and_confirm("stop-1")
        self.assertEqual(result.status, "canceled")
        self.portfolio.api.cancel_order.assert_called_once_with("stop-1")

    def test_canceled_stop_is_repaired_without_price_movement(self):
        self.portfolio._get_order = Mock(return_value=SimpleNamespace(
            status="canceled", filled_qty="0", qty="100", side="sell", type="stop", stop_price="98"))
        self.portfolio.broker_positions = Mock(return_value={
            "SPY": SimpleNamespace(qty="100", current_price="100")})
        self.portfolio.market_is_open = Mock(return_value=False)
        self.portfolio.manage_positions()
        self.portfolio._place_server_stop.assert_called_once_with(self.position, 98)

    def test_partial_stop_fill_updates_quantity_and_pnl(self):
        self.portfolio._get_order = Mock(return_value=SimpleNamespace(
            status="partially_filled", filled_qty="70", filled_avg_price="98"))
        self.portfolio.broker_positions = Mock(return_value={"SPY": SimpleNamespace(qty="30")})
        self.portfolio.reconcile()
        self.portfolio.reconcile()
        self.assertEqual(self.position.qty, 30)
        self.assertEqual(self.portfolio._pending_exit["profit_loss"], -140)

    def test_replacement_chain_is_verified(self):
        self.portfolio.broker_positions = Mock(return_value={"SPY": SimpleNamespace(qty="100")})
        self.portfolio._get_order = Mock(side_effect=[
            SimpleNamespace(id="stop-1", status="replaced", replaced_by="stop-2", filled_qty="0"),
            SimpleNamespace(id="stop-2", symbol="SPY", time_in_force="gtc", status="new", qty="100",
                            filled_qty="0", side="sell", type="stop", stop_price="98")])
        self.portfolio._ensure_protection(self.position)
        self.assertTrue(self.position.protection_verified)
        self.assertEqual(self.position.stop_order_id, "stop-2")
        self.portfolio._place_server_stop.assert_not_called()

    def test_crypto_log_deducts_fee_estimates(self):
        self.position.symbol = "BTC/USD"
        self.position.asset_class = "crypto"
        self.position.qty = 99.75
        self.position.entry_fees = 25
        self.position.fees_estimated = True
        self.portfolio._record_close(self.position, 100, "fees")
        row = self.portfolio._pending_exit
        self.assertEqual(row["gross_profit_loss"], 0)
        self.assertEqual(row["fees"], 49.94)
        self.assertEqual(row["profit_loss"], -49.94)
        self.assertTrue(row["fees_estimated"])


if __name__ == "__main__":
    unittest.main()