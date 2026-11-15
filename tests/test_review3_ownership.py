import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import config
from bot.portfolio import Portfolio, Position
from bot.risk_manager import RiskManager


class FreshEntryOwnershipTests(unittest.TestCase):
    def test_entry_rejects_same_symbol_position_seen_after_monitor_pass(self):
        portfolio = self.new_portfolio()
        portfolio.get_account = Mock(return_value=SimpleNamespace(equity="100000", buying_power="100000"))
        portfolio.api.get_asset.return_value = SimpleNamespace(tradable=True)
        portfolio.gross_exposure = Mock(return_value=0.0)
        portfolio.broker_positions = Mock(return_value={"SPY": SimpleNamespace(qty="10")})
        portfolio._list_open_orders = Mock(return_value=[])
        self.make_entry_fill(portfolio)

        result = portfolio.open_position(config.INSTRUMENTS["SPY"], "long", 2.0, 100.0,
                                         "mean_reversion", None, "test")

        self.assertIsNone(result)
        self.assertFalse(portfolio.entry_intents)
        portfolio._submit.assert_not_called()

    def test_entry_rejects_same_symbol_open_order_without_position(self):
        portfolio = self.new_portfolio()
        portfolio.get_account = Mock(return_value=SimpleNamespace(equity="100000", buying_power="100000"))
        portfolio.api.get_asset.return_value = SimpleNamespace(tradable=True)
        portfolio.gross_exposure = Mock(return_value=0.0)
        portfolio.broker_positions = Mock(return_value={})
        portfolio._list_open_orders = Mock(return_value=[self.stop()])
        self.make_entry_fill(portfolio)

        result = portfolio.open_position(config.INSTRUMENTS["SPY"], "long", 2.0, 100.0,
                                         "mean_reversion", None, "test")

        self.assertIsNone(result)
        self.assertFalse(portfolio.entry_intents)
        portfolio._submit.assert_not_called()

    def test_stop_during_entry_preflight_clears_intent_without_submission(self):
        portfolio = self.new_portfolio()
        portfolio.get_account = Mock(return_value=SimpleNamespace(equity="100000", buying_power="100000"))
        portfolio.api.get_asset.return_value = SimpleNamespace(tradable=True)
        portfolio.gross_exposure = Mock(return_value=0.0)
        portfolio.broker_positions = Mock(return_value={})
        portfolio._list_open_orders = Mock(return_value=[])
        self.make_entry_fill(portfolio)
        entry_guard = Mock(side_effect=(True, False))

        result = portfolio.open_position(config.INSTRUMENTS["SPY"], "long", 2.0, 100.0,
                                         "mean_reversion", None, "test", entry_guard=entry_guard)

        self.assertIsNone(result)
        self.assertEqual(entry_guard.call_count, 2)
        self.assertFalse(portfolio.entry_intents)
        portfolio._submit.assert_not_called()

    def test_stop_during_order_intent_commit_clears_unsubmitted_entry(self):
        portfolio = self.new_portfolio()
        portfolio.get_account = Mock(return_value=SimpleNamespace(equity="100000", buying_power="100000"))
        portfolio.api.get_asset.return_value = SimpleNamespace(tradable=True)
        portfolio.gross_exposure = Mock(return_value=0.0)
        portfolio.broker_positions = Mock(return_value={})
        portfolio._list_open_orders = Mock(return_value=[])
        self.make_entry_fill(portfolio)
        entry_guard = Mock(side_effect=(True, True, False))
        portfolio._submit = lambda **kwargs: Portfolio._submit.__wrapped__(portfolio, **kwargs)

        result = portfolio.open_position(config.INSTRUMENTS["SPY"], "long", 2.0, 100.0,
                                         "mean_reversion", None, "test", entry_guard=entry_guard)

        self.assertIsNone(result)
        self.assertEqual(entry_guard.call_count, 3)
        self.assertFalse(portfolio.entry_intents)
        self.assertIn("order", portfolio._save_state.call_args_list[1].kwargs)
        portfolio.api.submit_order.assert_not_called()

    def test_stop_after_ambiguous_submit_keeps_entry_intent_for_recovery(self):
        portfolio = self.new_portfolio()
        portfolio.get_account = Mock(return_value=SimpleNamespace(equity="100000", buying_power="100000"))
        portfolio.api.get_asset.return_value = SimpleNamespace(tradable=True)
        portfolio.gross_exposure = Mock(return_value=0.0)
        portfolio.broker_positions = Mock(return_value={})
        portfolio._list_open_orders = Mock(return_value=[])
        self.make_entry_fill(portfolio)
        entry_guard = Mock(side_effect=(True, True, True, False))
        portfolio.api.submit_order.side_effect = TimeoutError("submit response lost")
        portfolio.api.get_order_by_client_order_id.side_effect = TimeoutError("lookup unavailable")
        portfolio._submit = lambda **kwargs: Portfolio._submit.__wrapped__(portfolio, **kwargs)

        with patch("bot.portfolio.time.sleep"), self.assertRaisesRegex(
                TimeoutError, "submit response lost"):
            portfolio.open_position(config.INSTRUMENTS["SPY"], "long", 2.0, 100.0,
                                    "mean_reversion", None, "test", entry_guard=entry_guard)

        self.assertEqual(portfolio.api.submit_order.call_count, 1)
        self.assertEqual(entry_guard.call_count, 4)
        self.assertEqual(len(portfolio.entry_intents), 1)

    def test_entry_fill_cannot_claim_extra_quantity_from_racing_position(self):
        portfolio = self.new_portfolio()
        portfolio.get_account = Mock(return_value=SimpleNamespace(equity="100000", buying_power="100000"))
        portfolio.api.get_asset.return_value = SimpleNamespace(tradable=True)
        portfolio.gross_exposure = Mock(return_value=0.0)
        portfolio.broker_positions = Mock(side_effect=[{}, {"SPY": SimpleNamespace(qty="150")}])
        portfolio._list_open_orders = Mock(return_value=[])
        self.make_entry_fill(portfolio)

        result = portfolio.open_position(config.INSTRUMENTS["SPY"], "long", 2.0, 100.0,
                                         "mean_reversion", None, "test")

        self.assertIsNone(result)
        self.assertFalse(portfolio.positions)
        self.assertTrue(portfolio.accounting_unresolved)
        self.assertTrue(portfolio.entry_intents)
        portfolio._attach_existing_stop.assert_not_called()

    def test_matching_foreign_stop_is_not_attached_or_canceled(self):
        for parent_id in (None, "unrelated-parent"):
            with self.subTest(parent_id=parent_id):
                portfolio = self.new_portfolio()
                position = Position("SPY", "equity", "mean_reversion", "long", 100.0, 100.0,
                                    "2026-11-13T14:30:00Z", 2.0, 98.0, 100.0,
                                    stop_group_id="entry-parent")
                portfolio.positions[position.symbol] = position
                portfolio._list_open_orders = Mock(return_value=[self.stop()])
                portfolio._order_groups = {"foreign-stop": parent_id} if parent_id else {}

                self.assertFalse(portfolio._attach_existing_stop(position))
                self.assertIsNone(position.stop_order_id)
                with self.assertRaisesRegex(RuntimeError, "Untracked"):
                    portfolio._cancel_orders_for(position.symbol)
                portfolio.api.cancel_order.assert_not_called()

    def test_matching_stop_in_entry_group_can_be_attached(self):
        portfolio = self.new_portfolio()
        position = Position("SPY", "equity", "mean_reversion", "long", 100.0, 100.0,
                            "2026-11-13T14:30:00Z", 2.0, 98.0, 100.0,
                            stop_group_id="entry-parent")
        portfolio._list_open_orders = Mock(return_value=[self.stop()])
        portfolio._order_groups = {"foreign-stop": "entry-parent"}

        self.assertTrue(portfolio._attach_existing_stop(position))
        self.assertEqual(position.stop_order_id, "foreign-stop")

    def test_adoption_does_not_add_stop_beside_unowned_open_order(self):
        portfolio = self.new_portfolio()
        portfolio.get_account = Mock(return_value=SimpleNamespace(equity="100000"))
        portfolio._list_open_orders = Mock(return_value=[self.stop()])
        portfolio._place_server_stop = Mock()
        broker_position = SimpleNamespace(qty="100", avg_entry_price="100", current_price="100")

        portfolio.adopt_position(config.INSTRUMENTS["SPY"], "mean_reversion", None, 2.0, broker_position)

        portfolio._place_server_stop.assert_not_called()
        self.assertTrue(portfolio.accounting_unresolved)
        self.assertFalse(portfolio.positions["SPY"].protection_verified)

    def test_recovered_entry_cannot_claim_excess_broker_quantity(self):
        portfolio = self.new_portfolio()
        self.configure_recovered_entry(portfolio, "150")

        portfolio._recover_entries()

        self.assertIn("entry-client", portfolio.entry_intents)
        self.assertTrue(portfolio.accounting_unresolved)
        portfolio.adopt_position.assert_not_called()

    def test_recovered_equity_entry_passes_parent_id_to_adoption(self):
        portfolio = self.new_portfolio()
        self.configure_recovered_entry(portfolio, "100")

        portfolio._recover_entries()

        portfolio.adopt_position.assert_called_once()
        self.assertEqual(portfolio.adopt_position.call_args.kwargs["stop_group_id"], "entry-parent")

    def test_crypto_fee_allowance_is_bounded_by_configured_estimate(self):
        self.assertTrue(Portfolio._entry_fill_explains_position("crypto", 100.0, 99.8))
        self.assertFalse(Portfolio._entry_fill_explains_position("crypto", 100.0, 99.0))

    def test_recovered_crypto_shortfall_quarantines_across_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.multiple(config, LEDGER_FILE=root / "ledger.sqlite3", STATE_FILE=root / "state.json",
                                TRADES_CSV=root / "trades.csv", DAILY_PNL_CSV=root / "daily.csv"):
                portfolio = Portfolio(Mock(), RiskManager())
                portfolio.entry_intents["entry-client"] = dict(
                    symbol="BTC/USD", side="long", atr=100.0, price=50000.0,
                    strategy="momentum_breakout", trailing_mult=None)
                portfolio._save_state()
                order = SimpleNamespace(id="crypto-entry", status="canceled", filled_qty="100")
                portfolio.api.get_order_by_client_order_id.return_value = order
                portfolio._observe_order = Mock()
                portfolio._cancel_and_confirm = Mock(return_value=order)
                portfolio.broker_positions = Mock(return_value={"BTCUSD": SimpleNamespace(qty="50")})
                portfolio.adopt_position = Mock()

                portfolio._recover_entries()

                self.assertIn("entry-client", portfolio.entry_intents)
                self.assertTrue(portfolio.accounting_unresolved)
                portfolio.adopt_position.assert_not_called()
                restored = Portfolio(Mock(), RiskManager())
                self.assertTrue(restored.accounting_unresolved)
                self.assertIn("entry-client", restored.entry_intents)
                restored.broker_positions = Mock(return_value={"BTCUSD": SimpleNamespace(qty="50")})
                restored.adopt_position = Mock()
                with patch.object(config, "ADOPT_CONFIGURED_POSITIONS", True):
                    restored.reconcile()
                restored.adopt_position.assert_not_called()
                restored.api.submit_order.assert_not_called()

    @staticmethod
    def configure_recovered_entry(portfolio, broker_qty):
        intent = dict(symbol="SPY", side="long", atr=2.0, price=100.0,
                      strategy="mean_reversion", trailing_mult=None)
        portfolio.entry_intents["entry-client"] = intent
        order = SimpleNamespace(id="entry-parent", status="canceled", filled_qty="100")
        portfolio.api.get_order_by_client_order_id.return_value = order
        portfolio._observe_order = Mock()
        portfolio._cancel_and_confirm = Mock(return_value=order)
        portfolio.broker_positions = Mock(return_value={"SPY": SimpleNamespace(qty=broker_qty)})
        portfolio.adopt_position = Mock()

    @staticmethod
    def new_portfolio():
        with patch.object(Portfolio, "_load_state"):
            portfolio = Portfolio(Mock(), RiskManager())
        portfolio._save_state = Mock()
        portfolio._submit = Mock()
        return portfolio

    @staticmethod
    def make_entry_fill(portfolio):
        entry = SimpleNamespace(id="entry-parent", status="filled", filled_qty="100",
                                filled_avg_price="100")
        portfolio._submit.return_value = entry
        portfolio._wait_for_fill = Mock(return_value=entry)
        portfolio._attach_existing_stop = Mock(return_value=False)
        portfolio._cancel_orders_for = Mock()
        portfolio._place_server_stop = Mock()

    @staticmethod
    def stop():
        return SimpleNamespace(id="foreign-stop", symbol="SPY", type="stop", side="sell",
                               status="new", time_in_force="gtc", qty="100", filled_qty="0",
                               filled_avg_price="0", stop_price="98", legs=None)


if __name__ == "__main__":
    unittest.main()