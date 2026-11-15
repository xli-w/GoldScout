import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import pandas as pd

from bot.data import MarketData
from bot.portfolio import Portfolio, Position
from bot.risk_manager import RiskManager
from types import SimpleNamespace
import tempfile
from pathlib import Path
import config


class NextReviewRepairs(unittest.TestCase):
    def test_entry_protection_full_close_does_not_resurrect_position(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.multiple(config, LEDGER_FILE=root / "ledger.sqlite3", STATE_FILE=root / "state.json",
                                TRADES_CSV=root / "trades.csv", DAILY_PNL_CSV=root / "daily.csv"):
                portfolio, _ = self.portfolio()
                del portfolio._save_state
                portfolio.positions = {}
                portfolio.get_account = Mock(return_value=SimpleNamespace(equity="100000", buying_power="100000"))
                portfolio.api.get_asset.return_value = SimpleNamespace(tradable=True)
                portfolio.gross_exposure = Mock(return_value=0.)
                portfolio._entry_preflight = Mock(return_value="clear")
                portfolio.broker_positions = Mock(return_value={"SPY": SimpleNamespace(qty="100")})
                entry = SimpleNamespace(id="entry", status="filled", filled_qty="100", filled_avg_price="100")
                portfolio._submit = Mock(return_value=entry)
                portfolio._wait_for_fill = Mock(return_value=entry)
                portfolio._attach_existing_stop = Mock(return_value=False)
                portfolio._order_groups = {"old": "entry"}
                portfolio._list_open_orders = Mock(return_value=[self.stop()])
                portfolio._get_order = Mock(return_value=self.stop(status="filled", filled_qty="100", filled_avg_price="98"))
                self.assertIsNone(portfolio.open_position(config.INSTRUMENTS["SPY"], "long", 2., 100.,
                                                         "mean_reversion", None, "test"))
                self.assertFalse(portfolio.positions)
                self.assertFalse(portfolio.persistence_fault)
                self.assertTrue(portfolio.accounting_unresolved)
                self.assertTrue(portfolio.entry_intents)
                restored = Portfolio(Mock(), RiskManager())
                self.assertFalse(restored.positions)
                self.assertTrue(restored.accounting_unresolved)
                restored.broker_positions = Mock(return_value={"SPY": SimpleNamespace(qty="100")})
                restored.adopt_position = Mock()
                with patch.object(config, "ADOPT_CONFIGURED_POSITIONS", True):
                    restored.reconcile()
                self.assertFalse(restored.positions)
                restored.adopt_position.assert_not_called()
                restored.api.get_order_by_client_order_id.assert_not_called()
                restored.api.cancel_order.assert_not_called()
                restored.api.submit_order.assert_not_called()
                portfolio.api.submit_order.assert_not_called()

    def test_reconcile_cannot_override_rejected_entry_ownership(self):
        portfolio = self.recovering_entry("100", {"SPY": SimpleNamespace(qty="-100")})
        with patch.object(config, "ADOPT_CONFIGURED_POSITIONS", True):
            portfolio.reconcile()
        self.assertIn("entry-client", portfolio.entry_intents)
        portfolio.adopt_position.assert_not_called()
        portfolio.api.submit_order.assert_not_called()

    def test_full_replaced_stop_commits_quarantine_before_export_and_monitoring(self):
        for caller in ("reconcile", "_ensure_protection"):
            with self.subTest(caller=caller), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                with patch.multiple(config, LEDGER_FILE=root / "ledger.sqlite3", STATE_FILE=root / "state.json",
                                    TRADES_CSV=root / "trades.csv", DAILY_PNL_CSV=root / "daily.csv"):
                    portfolio, position = self.portfolio()
                    del portfolio._save_state
                    portfolio.broker_positions = Mock(return_value={"SPY": SimpleNamespace(qty="100")})
                    portfolio._get_order = Mock(return_value=self.stop(status="replaced", replaced_by="new",
                                                                       filled_qty="100", filled_avg_price="98"))

                    export_reports = portfolio._export_reports
                    recovered_states = []

                    def inspect_after_commit():
                        export_reports()
                        restored = Portfolio(Mock(), RiskManager())
                        self.assertFalse(restored.positions)
                        self.assertTrue(restored.accounting_unresolved)
                        self.assertEqual(position.qty, 100.)
                        recovered_states.append(restored)

                    with patch.object(portfolio, "_export_reports", side_effect=inspect_after_commit):
                        if caller == "reconcile":
                            portfolio.reconcile()
                        else:
                            portfolio._ensure_protection(position)
                    self.assertEqual(len(recovered_states), 1)
                    self.assertTrue(portfolio.accounting_unresolved)
                    portfolio._get_order.assert_called_once_with("old")
                    portfolio.api.submit_order.assert_not_called()

    def test_normal_entry_retains_filled_predecessor_intent(self):
        for broker in ({}, {"SPY": SimpleNamespace(qty="100")}):
            with self.subTest(broker=broker):
                portfolio, _ = self.portfolio()
                portfolio.positions = {}
                portfolio.get_account = Mock(return_value=SimpleNamespace(equity="100000", buying_power="100000"))
                portfolio.api.get_asset.return_value = SimpleNamespace(tradable=True)
                portfolio.gross_exposure = Mock(return_value=0.)
                portfolio._entry_preflight = Mock(return_value="clear")
                portfolio.broker_positions = Mock(return_value=broker)
                predecessor = SimpleNamespace(id="entry-old", status="replaced", replaced_by="entry-new", filled_qty="100")
                successor = SimpleNamespace(id="entry-new", status="canceled", filled_qty="0")
                portfolio._submit = Mock(return_value=predecessor)
                portfolio._get_order = Mock(side_effect=[predecessor, successor])
                portfolio._attach_existing_stop = Mock()
                portfolio._wait_for_fill = lambda order_id, observations: Portfolio._wait_for_fill.__wrapped__(
                    portfolio, order_id, timeout=0., observations=observations)
                result = portfolio.open_position(config.INSTRUMENTS["SPY"], "long", 2., 100.,
                                                 "mean_reversion", None, "test")
                self.assertIsNone(result)
                self.assertTrue(portfolio.entry_intents)
                self.assertTrue(portfolio.accounting_unresolved)
                self.assertFalse(portfolio.positions)
                portfolio._attach_existing_stop.assert_not_called()

    def test_recovered_entry_cannot_adopt_opposite_exposure(self):
        portfolio = self.recovering_entry("100", {"SPY": SimpleNamespace(qty="-100")})
        portfolio._recover_entries()
        self.assertTrue(portfolio.accounting_unresolved)
        self.assertIn("entry-client", portfolio.entry_intents)
        portfolio.adopt_position.assert_not_called()
        portfolio.api.submit_order.assert_not_called()

    def test_full_predecessor_close_quarantines_replacement_across_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.multiple(config, LEDGER_FILE=root / "ledger.sqlite3", STATE_FILE=root / "state.json",
                                TRADES_CSV=root / "trades.csv", DAILY_PNL_CSV=root / "daily.csv"):
                portfolio, _ = self.portfolio()
                del portfolio._save_state
                portfolio._get_order = Mock(return_value=self.stop(status="replaced", replaced_by="new",
                                                                   filled_qty="100", filled_avg_price="98"))
                with self.assertRaisesRegex(RuntimeError, "Fully closed"):
                    portfolio._cancel_and_confirm("old")
                restored = Portfolio(Mock(), RiskManager())
                self.assertFalse(restored.positions)
                self.assertTrue(restored.accounting_unresolved)
                portfolio.api.cancel_order.assert_not_called()

    def test_full_close_blocks_remaining_cancel_batch(self):
        portfolio, position = self.portfolio()
        position.stop_group_id = "parent"
        position.stop_order_fills["sibling"] = {"qty": 0., "notional": 0.}
        portfolio._list_open_orders = Mock(return_value=[self.stop(), self.stop(id="sibling")])
        portfolio._get_order = Mock(return_value=self.stop(status="filled", filled_qty="100", filled_avg_price="98"))
        with self.assertRaisesRegex(RuntimeError, "Fully closed"):
            portfolio._cancel_orders_for("SPY")
        self.assertTrue(portfolio.accounting_unresolved)
        self.assertFalse(portfolio.positions)
        portfolio._get_order.assert_called_once_with("old")

    def test_sibling_successor_fill_baseline_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.multiple(config, LEDGER_FILE=root / "ledger.sqlite3", STATE_FILE=root / "state.json",
                                TRADES_CSV=root / "trades.csv", DAILY_PNL_CSV=root / "daily.csv"):
                portfolio, position = self.portfolio()
                del portfolio._save_state
                position.stop_group_id = "parent"
                position.stop_order_fills["sibling"] = {"qty": 0.0, "notional": 0.0}
                portfolio._get_order = Mock(side_effect=[
                    self.stop(id="sibling", status="replaced", replaced_by="successor"),
                    self.stop(id="successor"),
                    self.stop(id="successor", status="canceled", filled_qty="20", filled_avg_price="101")])
                portfolio._cancel_and_confirm("sibling")
                restored = Portfolio(Mock(), RiskManager())
                residual = restored.positions["SPY"]
                self.assertEqual(residual.qty, 80.)
                self.assertEqual(residual.stop_order_id, "old")
                self.assertEqual(residual.stop_group_id, "parent")
                self.assertEqual(residual.stop_order_fills["successor"], {"qty": 20., "notional": 2020.})
                restored._account_cancel_observation(self.stop(id="successor", status="canceled",
                                                               filled_qty="20", filled_avg_price="101"))
                self.assertIsNone(restored._pending_exit)
                self.assertEqual(residual.qty, 80.)

    def test_exit_replacement_cannot_infer_inherited_fills(self):
        portfolio, position = self.portfolio()
        position.exit_order_id = "exit-old"
        portfolio._get_order = Mock(return_value=SimpleNamespace(
            id="exit-old", status="replaced", replaced_by="exit-new", filled_qty="20"))
        with self.assertRaisesRegex(RuntimeError, "provenance"):
            portfolio._cancel_and_confirm("exit-old")
        self.assertEqual(position.exit_order_id, "exit-old")
        self.assertTrue(portfolio.accounting_unresolved)
        portfolio.api.cancel_order.assert_not_called()

    def test_sibling_replacement_cancel_fill_retains_primary_identity(self):
        portfolio, position = self.portfolio()
        position.stop_group_id = "parent"
        position.stop_order_fills["sibling"] = {"qty": 0.0, "notional": 0.0}
        predecessor = self.stop(id="sibling", status="replaced", replaced_by="successor")
        terminal = self.stop(id="successor", status="canceled", filled_qty="20", filled_avg_price="101")
        portfolio._get_order = Mock(side_effect=[predecessor, self.stop(id="successor"), terminal])
        portfolio._cancel_and_confirm("sibling")
        portfolio._account_cancel_observation(terminal)
        self.assertEqual(position.qty, 80.)
        self.assertEqual(position.stop_order_id, "old")
        self.assertEqual(position.stop_group_id, "parent")
        self.assertEqual(position.stop_order_fills["successor"]["qty"], 20.)
        self.assertEqual(portfolio._pending_exit["position_size"], 20.)

    def test_filled_entry_predecessor_and_zero_fill_successor_remain_unresolved(self):
        portfolio = self.recovering_entry("0", {})
        del portfolio._cancel_and_confirm
        predecessor = SimpleNamespace(id="entry-order", status="replaced", replaced_by="entry-new", filled_qty="100")
        successor = SimpleNamespace(id="entry-new", status="canceled", filled_qty="0")
        portfolio.api.get_order_by_client_order_id.return_value = predecessor
        portfolio._get_order = Mock(side_effect=[predecessor, successor, predecessor, successor])
        portfolio._recover_entries()
        portfolio._recover_entries()
        self.assertTrue(portfolio.accounting_unresolved)
        self.assertIn("entry-client", portfolio.entry_intents)
        portfolio.adopt_position.assert_not_called()
        portfolio.api.submit_order.assert_not_called()

    def recovering_entry(self, filled, broker):
        portfolio, _ = self.portfolio()
        portfolio.positions = {}
        portfolio.entry_intents["entry-client"] = dict(symbol="SPY", side="long", atr=2., price=100.,
                                                      strategy="mean_reversion", trailing_mult=None)
        portfolio.api.get_order_by_client_order_id.return_value = SimpleNamespace(id="entry-order", filled_qty=filled)
        portfolio._observe_order = Mock()
        portfolio._cancel_and_confirm = Mock(return_value=SimpleNamespace(
            id="entry-order", status="canceled", filled_qty=filled))
        portfolio.broker_positions = Mock(return_value=broker)
        portfolio.adopt_position = Mock()
        return portfolio

    def test_recovered_filled_entry_flat_at_broker_retains_intent(self):
        portfolio = self.recovering_entry("100", {})
        portfolio._recover_entries()
        portfolio._recover_entries()
        self.assertIn("entry-client", portfolio.entry_intents)
        self.assertTrue(portfolio.accounting_unresolved)
        portfolio.adopt_position.assert_not_called()
        portfolio.api.submit_order.assert_not_called()

    def test_recovered_zero_fill_flat_entry_clears_only_intent(self):
        portfolio = self.recovering_entry("0", {})
        portfolio._recover_entries()
        self.assertNotIn("entry-client", portfolio.entry_intents)
        self.assertFalse(portfolio.accounting_unresolved)
        portfolio.adopt_position.assert_not_called()

    def test_zero_fill_entry_cannot_adopt_unrelated_broker_position(self):
        portfolio = self.recovering_entry("0", {"SPY": SimpleNamespace(qty="100")})
        portfolio._recover_entries()
        self.assertIn("entry-client", portfolio.entry_intents)
        self.assertTrue(portfolio.accounting_unresolved)
        portfolio.adopt_position.assert_not_called()

    def test_replacement_arriving_during_cancel_is_followed(self):
        portfolio, position = self.portfolio()
        portfolio._get_order = Mock(side_effect=[
            self.stop(), self.stop(status="pending_cancel"),
            self.stop(status="replaced", replaced_by="new", filled_qty="30", filled_avg_price="98"),
            self.stop(id="new", qty="70"), self.stop(id="new", qty="70", status="canceled")])
        with patch("bot.portfolio.time.sleep"):
            terminal = portfolio._cancel_and_confirm("old")
        self.assertEqual(terminal.id, "new")
        self.assertEqual(position.qty, 70.)
        self.assertEqual(position.stop_order_id, "new")
        self.assertEqual(portfolio._pending_exit["position_size"], 30.)
        self.assertEqual([call.args[0] for call in portfolio.api.cancel_order.call_args_list], ["old", "new"])

    def test_unknown_order_blocks_entire_cancel_batch(self):
        portfolio, position = self.portfolio()
        portfolio._list_open_orders = Mock(return_value=[self.stop(), self.stop(id="manual")])
        portfolio._cancel_and_confirm = Mock()
        with self.assertRaisesRegex(RuntimeError, "Untracked"):
            portfolio._cancel_orders_for("SPY")
        portfolio._cancel_and_confirm.assert_not_called()
        self.assertEqual(position.qty, 100.)

    def test_replacement_cycle_is_rejected_without_cancel(self):
        portfolio, _ = self.portfolio()
        portfolio._get_order = Mock(side_effect=[
            self.stop(status="replaced", replaced_by="new"),
            self.stop(id="new", status="replaced", replaced_by="old")])
        with self.assertRaisesRegex(RuntimeError, "cyclic"):
            portfolio._cancel_and_confirm("old")
        portfolio.api.cancel_order.assert_not_called()

    def portfolio(self):
        with patch.object(Portfolio, "_load_state"):
            portfolio = Portfolio(Mock(), RiskManager())
        portfolio._save_state = Mock()
        portfolio._audit = Mock()
        position = Position("SPY", "equity", "mean_reversion", "long", 100., 100.,
                            "2026-11-13T14:30:00Z", 2., 98., 100., stop_order_id="old")
        portfolio.positions["SPY"] = position
        return portfolio, position

    def stop(self, **overrides):
        values = dict(id="old", symbol="SPY", type="stop", side="sell", status="new",
                      time_in_force="gtc", qty="100", filled_qty="0", stop_price="98", legs=None)
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_day_and_expired_stops_are_not_durable(self):
        portfolio, position = self.portfolio()
        self.assertFalse(portfolio._check_stop(position, self.stop(time_in_force="day")))
        self.assertFalse(portfolio._check_stop(position, self.stop(expires_at="2020-01-01T00:00:00Z")))
        self.assertTrue(portfolio._check_stop(position, self.stop()))

    def test_legacy_day_stop_is_converted_after_confirmed_cancellation(self):
        portfolio, position = self.portfolio()
        portfolio.broker_positions = Mock(return_value={"SPY": SimpleNamespace(qty="100")})
        day = self.stop(time_in_force="day")
        portfolio._get_order = Mock(side_effect=[day, day, self.stop(status="canceled", time_in_force="day")])
        portfolio._list_open_orders = Mock(return_value=[day])
        portfolio._place_server_stop = Mock()
        portfolio._ensure_protection(position)
        portfolio.api.cancel_order.assert_called_once_with("old")
        portfolio._place_server_stop.assert_called_once_with(position, 98.)

    def test_crypto_limit_must_be_executable_and_bounded(self):
        portfolio, position = self.portfolio()
        portfolio.api.get_asset.return_value = SimpleNamespace(price_increment="0.01")
        position.symbol, position.asset_class = "BTC/USD", "crypto"
        self.assertFalse(portfolio._check_stop(position, self.stop(symbol="BTC/USD", type="stop_limit", limit_price="200")))
        self.assertTrue(portfolio._check_stop(position, self.stop(symbol="BTC/USD", type="stop_limit", limit_price="97.02")))

    def test_crypto_limit_accepts_tick_rounded_buffer(self):
        portfolio, position = self.portfolio()
        position.symbol, position.asset_class, position.hard_stop = "BTC/USD", "crypto", 123.
        portfolio.api.get_asset.return_value = SimpleNamespace(price_increment="1")
        self.assertTrue(portfolio._check_stop(position, self.stop(symbol="BTC/USD", type="stop_limit", stop_price="123", limit_price="121")))
        self.assertFalse(portfolio._check_stop(position, self.stop(symbol="BTC/USD", type="stop_limit", stop_price="123", limit_price="121.5")))

    def test_related_leg_fill_during_cancel_is_accounted_once(self):
        portfolio, position = self.portfolio()
        position.stop_group_id = "parent"
        leg = self.stop(id="sibling")
        portfolio._order_groups = {"sibling": "parent"}
        portfolio._list_open_orders = Mock(return_value=[leg])
        portfolio._get_order = Mock(return_value=self.stop(id="sibling", status="canceled", filled_qty="20", filled_avg_price="101"))
        portfolio._cancel_orders_for("SPY")
        portfolio._cancel_orders_for("SPY")
        self.assertEqual(position.qty, 80)
        self.assertEqual(position.stop_order_id, "old")
        self.assertEqual(portfolio._pending_exit["position_size"], 20.)

    def test_unrecognized_order_is_not_canceled(self):
        portfolio, _ = self.portfolio()
        portfolio._list_open_orders = Mock(return_value=[self.stop(id="manual")])
        with self.assertRaises(RuntimeError):
            portfolio._cancel_orders_for("SPY")
        portfolio.api.cancel_order.assert_not_called()

    def test_grouped_legs_are_flattened_and_deduplicated(self):
        portfolio, _ = self.portfolio()
        leg = self.stop(id="child")
        parent = self.stop(id="parent", type="market", legs=[leg])
        portfolio.api.list_orders.return_value = [parent, leg]
        self.assertEqual({order.id for order in portfolio._list_open_orders()}, {"parent", "child"})
        self.assertEqual(portfolio._order_groups["child"], "parent")
        self.assertTrue(portfolio.api.list_orders.call_args.kwargs["nested"])

    def test_predecessor_fill_is_accounted_before_zero_fill_replacement(self):
        portfolio, position = self.portfolio()
        old = self.stop(status="replaced", replaced_by="new", filled_qty="30", filled_avg_price="98")
        new = self.stop(id="new", qty="70", status="canceled")
        portfolio._get_order = Mock(side_effect=[old, new])
        portfolio._cancel_and_confirm("old")
        self.assertEqual(position.qty, 70)
        self.assertEqual(position.stop_order_id, "new")
        self.assertEqual(portfolio._pending_exit["position_size"], 30.)
        self.assertEqual(position.stop_order_fills["old"]["qty"], 30)

    def test_ambiguous_replacement_carryover_blocks_without_double_accounting(self):
        portfolio, position = self.portfolio()
        old = self.stop(status="replaced", replaced_by="new", filled_qty="30", filled_avg_price="98")
        new = self.stop(id="new", filled_qty="30", filled_avg_price="98")
        portfolio._get_order = Mock(side_effect=[old, new])
        with self.assertRaises(RuntimeError):
            portfolio._cancel_and_confirm("old")
        self.assertEqual(position.qty, 70)
        self.assertTrue(portfolio.accounting_unresolved)
        self.assertEqual(portfolio._pending_exit["position_size"], 30.)
        portfolio.api.cancel_order.assert_not_called()

    def test_stop_movement_accounts_cancel_fill_and_uses_residual(self):
        portfolio, position = self.portfolio()
        portfolio.broker_positions = Mock(side_effect=[{"SPY": SimpleNamespace(qty="100")},
                                                       {"SPY": SimpleNamespace(qty="70")}])
        portfolio._list_open_orders = Mock(return_value=[self.stop()])
        portfolio._get_order = Mock(return_value=self.stop(status="canceled", filled_qty="30", filled_avg_price="98"))
        portfolio._place_server_stop = Mock()
        portfolio._move_server_stop(position, 99.)
        self.assertEqual(position.qty, 70)
        self.assertEqual(portfolio._pending_exit["position_size"], 30.)
        portfolio._place_server_stop.assert_called_once_with(position, 99.)
        portfolio.api.replace_order.assert_not_called()

    def test_hourly_refresh_requests_complete_overlap(self):
        class Frozen(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 11, 13, 12, 30, tzinfo=timezone.utc)

        index = pd.to_datetime(["2026-11-13 10:00", "2026-11-13 11:00"], utc=True)
        frame = pd.DataFrame(dict(open=100., high=101., low=99., close=100., volume=300.), index=index)
        market = MarketData(Mock())
        market._raw_cache[("BTC/USD", "crypto", (1, "Hour"), 14)] = (
            Frozen(2026, 11, 13, 12, tzinfo=timezone.utc), frame)
        market._fetch = Mock(return_value=frame.copy())
        with patch("bot.data.datetime", Frozen):
            result = market.get_completed_bars("BTC/USD", "crypto", (1, "Hour"), 14)
        self.assertEqual(pd.Timestamp(market._fetch.call_args.args[3]), index[0])
        self.assertEqual(result.volume.iloc[-1], 300)


if __name__ == "__main__":
    unittest.main()