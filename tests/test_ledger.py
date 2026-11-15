import sqlite3
import io
import tempfile
import unittest
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import Mock, patch
from pathlib import Path
from contextlib import closing, redirect_stdout

import requests

import config
from bot.ledger import Ledger, PersistenceError
from bot.portfolio import Portfolio, Position
from bot.risk_manager import RiskManager
from bot.utils import create_api


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.ledger = Ledger(self.root / "ledger.sqlite3")

    def test_commit_rollback_and_journal_revision(self):
        state = {"positions": {}}
        self.ledger.commit(state, {"execution_id": "fill-1"})
        with self.assertRaises(sqlite3.IntegrityError):
            self.ledger.commit({"positions": {"SPY": 1}}, {"execution_id": "fill-1"})
        self.assertEqual(self.ledger.load(), state)
        with self.ledger.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0], 2)

    def test_binding_and_backup(self):
        self.ledger.bind("paper-1", "paper")
        self.ledger.commit({"positions": {}})
        with self.assertRaises(PersistenceError):
            self.ledger.bind("live-1", "live")
        destination = self.root / "backup.sqlite3"
        self.ledger.backup(destination)
        self.assertEqual(Ledger(destination).load(), self.ledger.load())

    def test_conflicting_activity_and_idempotency(self):
        activity = {"id": "activity-1", "qty": "0.001", "price": "123.45"}
        self.ledger.ingest_activity(activity)
        self.ledger.ingest_activity(activity)
        with self.assertRaises(PersistenceError):
            self.ledger.ingest_activity(dict(activity, qty="0.002"))
        with self.ledger.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM broker_activities").fetchone()[0], 1)

    def test_invalid_revision_and_future_schema_are_refused(self):
        self.ledger.commit({"positions": {}})
        with self.ledger.connect() as connection:
            connection.execute("UPDATE state SET payload='{}'")
        with self.assertRaises(PersistenceError):
            self.ledger.load()
        with self.ledger.connect() as connection:
            connection.execute("PRAGMA user_version=999")
        with self.assertRaises(PersistenceError):
            Ledger(self.ledger.path)

    def test_daily_projection_and_export(self):
        row = {"date": "2026-11-12", "realized_pnl": "12.34"}
        self.ledger.commit({}, daily_rows=[row])
        self.ledger.export(self.root / "daily.csv", ["date", "realized_pnl"], daily=True)
        self.assertIn("2026-11-12,12.34", (self.root / "daily.csv").read_text())

    def test_old_schema_migration_preserves_payload_and_repeats_safely(self):
        path = self.root / "old.sqlite3"
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("CREATE TABLE state (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
            connection.execute("INSERT INTO state VALUES (1, '{\"positions\": {}}')")
            connection.commit()
        ledger = Ledger(path)
        self.assertEqual(ledger.load(), {"positions": {}})
        self.assertEqual(ledger.replay(), ledger.load())
        self.assertEqual(Ledger(path).summary()["counts"]["events"], 1)

    def test_correction_is_atomic_idempotent_and_preserves_original(self):
        state = {"positions": {}, "daily": {"day": "2026-11-12", "realized": 10}}
        event = dict(execution_id="fill-1", accounting_date="2026-11-12", direction="long",
                     entry_price=100, exit_price=101, position_size=10, profit_loss=10)
        self.ledger.commit(state, event, [dict(date="2026-11-12", realized_pnl=10)])
        self.ledger.ingest_activity({"id": "fee-1", "net_amount": "-2"})
        correction = dict(execution_id="fill-1", correction_id="correction-1", activity_id="fee-1",
                          exit_price="101", fees="2", reason="settled total fees verified")
        self.ledger.apply_correction(**correction)
        self.ledger.apply_correction(**correction)
        self.assertEqual(self.ledger.load()["daily"]["realized"], 8)
        self.assertEqual(self.ledger.replay(), self.ledger.load())
        with self.ledger.connect(write=False) as connection:
            original = connection.execute("SELECT payload FROM exits").fetchone()[0]
            daily = connection.execute("SELECT payload FROM daily_accounts").fetchone()[0]
        self.assertEqual(Ledger.decode(original), event)
        self.assertEqual(Ledger.decode(daily)["realized_pnl"], 8)
        with self.assertRaises(PersistenceError):
            self.ledger.apply_correction(**dict(correction, fees="3"))
        self.ledger.export_corrections(self.root / "corrections.csv")
        self.assertIn("correction-1", (self.root / "corrections.csv").read_text())

    def test_missing_evidence_cannot_apply_correction(self):
        with self.assertRaises(PersistenceError):
            self.ledger.apply_correction("missing", "correction-1", "fee-1", "101", "2", "fee")
        self.assertEqual(self.ledger.summary()["counts"]["corrections"], 0)

    def test_unknown_price_requires_correction_before_explicit_resolution(self):
        state = {"positions": {}, "daily_controls": {"accounting_unresolved": True, "halted": True}}
        event = dict(execution_id="unknown-fill", direction="long", entry_price=100, position_size=10,
                     price_verified=False, profit_loss=None, accounting_date="2026-11-12")
        self.ledger.commit(state, event)
        with self.assertRaises(PersistenceError):
            self.ledger.resolve_accounting("review complete")
        self.ledger.ingest_activity({"id": "broker-fill", "price": "101", "qty": "10"})
        self.ledger.apply_correction("unknown-fill", "price-correction", "broker-fill", "101", "0", "verified fill")
        self.ledger.resolve_accounting("broker holdings and activities reconciled")
        self.assertFalse(self.ledger.load()["daily_controls"]["accounting_unresolved"])
        self.assertTrue(self.ledger.load()["daily_controls"]["halted"])

    def test_events_are_append_only(self):
        self.ledger.record("test_event", {})
        for statement in ("DELETE FROM events", "UPDATE events SET kind='changed'"):
            with self.assertRaises(sqlite3.IntegrityError):
                with self.ledger.connect() as connection:
                    connection.execute(statement)

    def test_prior_day_fill_restates_only_historical_realized(self):
        self.ledger.commit({}, daily_rows=[dict(date="2026-11-11", realized_pnl=0, trades_closed=0)])
        state = {"daily": {"day": "2026-11-12", "realized": 0}}
        event = dict(execution_id="old-fill", accounting_date="2026-11-11", profit_loss=10)
        self.ledger.commit(state, event)
        self.assertEqual(self.ledger.load()["daily"]["realized"], 0)
        with self.ledger.connect(write=False) as connection:
            daily = Ledger.decode(connection.execute("SELECT payload FROM daily_accounts").fetchone()[0])
        self.assertEqual(daily["realized_pnl"], 10)
        self.assertEqual(daily["trades_closed"], 1)

    def test_audit_cli_summary_and_replay(self):
        from bot.audit import main
        position = Position("SPY", "equity", "mean_reversion", "long", 10., 100.,
                            "2026-11-13T14:30:00Z", 2., 98., 100.)
        self.ledger.commit({"positions": {"SPY": asdict(position)}})
        for command in ("summary", "replay"):
            with redirect_stdout(io.StringIO()) as output:
                main(["--database", str(self.ledger.path), command])
            self.assertIsInstance(Ledger.decode(output.getvalue()), dict)


class PortfolioPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        for name, filename in (("LEDGER_FILE", "ledger.sqlite3"), ("STATE_FILE", "state.json"),
                               ("TRADES_CSV", "trades.csv"), ("DAILY_PNL_CSV", "daily.csv")):
            patcher = patch.object(config, name, self.root / filename)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.portfolio = Portfolio(Mock(), RiskManager())
        self.position = Position("SPY", "equity", "mean_reversion", "long", 100., 100.,
                                 "2026-11-12T14:30:00Z", 2., 98., 100., identity="entry-1")
        self.portfolio.positions["SPY"] = self.position
        self.portfolio._save_state()

    def test_failed_fill_commit_does_not_publish_or_advance_counters(self):
        with patch.object(self.portfolio._ledger, "commit", side_effect=sqlite3.OperationalError("disk full")):
            with self.assertRaises(PersistenceError):
                self.portfolio._account_exit_fill(self.position, SimpleNamespace(
                    id="exit-1", filled_qty="70", filled_avg_price="101"), "partial")
        self.assertEqual(self.position.qty, 100.)
        self.assertEqual(self.position.exit_filled_qty, 0.)
        self.assertEqual(self.portfolio.realized_today, 0.)
        self.assertEqual(self.portfolio._ledger.load()["positions"]["SPY"]["qty"], 100.)
        self.assertFalse(self.portfolio.entries_safe())
        with self.assertRaises(PersistenceError):
            self.portfolio._submit(symbol="SPY", qty=1)
        self.portfolio.api.submit_order.assert_not_called()

    def test_export_failure_does_not_invalidate_partial_exit(self):
        with patch.object(self.portfolio._ledger, "export", side_effect=OSError("CSV locked")):
            self.portfolio._record_close(self.position, 101., "partial", 70.)
        self.assertFalse(self.portfolio.persistence_fault)
        restored = Portfolio(Mock(), RiskManager())
        self.assertEqual(restored.positions["SPY"].qty, 30.)
        self.assertEqual(len(config.TRADES_CSV.read_text().splitlines()), 2)

    def test_exit_projects_each_derived_report_once(self):
        with patch.object(self.portfolio._ledger, "export", wraps=self.portfolio._ledger.export) as export:
            self.portfolio._record_close(self.position, 101., "full")

        calls = [(call.args[0], call.kwargs.get("daily", False)) for call in export.call_args_list]
        self.assertEqual(calls, [(config.TRADES_CSV, False), (config.DAILY_PNL_CSV, True)])

    def test_invalid_recovery_is_quarantined_without_overwrite(self):
        with self.portfolio._ledger.connect() as connection:
            connection.execute("UPDATE state SET payload='[]'")
        restored = Portfolio(Mock(), RiskManager())
        self.assertTrue(restored.persistence_fault)
        with self.assertRaises(PersistenceError):
            restored._save_state()
        with self.portfolio._ledger.connect() as connection:
            self.assertEqual(connection.execute("SELECT payload FROM state").fetchone()[0], "[]")

    def test_order_intent_precedes_broker_call_and_ack_survives_restart(self):
        def submit(**parameters):
            with self.portfolio._ledger.connect() as connection:
                row = connection.execute("SELECT payload FROM orders WHERE client_id=?",
                                         (parameters["client_order_id"],)).fetchone()
            self.assertEqual(Ledger.decode(row[0])["qty"], "0.001")
            return SimpleNamespace(id="broker-1", client_order_id=parameters["client_order_id"],
                                   qty="0.001", status="accepted")
        self.portfolio.api.submit_order.side_effect = submit
        self.portfolio._submit(symbol="BTC/USD", qty=0.001, client_order_id="intent-1")
        with self.portfolio._ledger.connect() as connection:
            row = connection.execute("SELECT payload FROM orders WHERE client_id='intent-1'").fetchone()
        self.assertEqual(Ledger.decode(row[0])["observation"]["id"], "broker-1")

    def test_proxy_failures_reuse_durable_order_intent_after_failed_lookup(self):
        proxy = "http://sample:secret@proxy.invalid:8080"
        api = Mock()
        api._session.request.side_effect = requests.exceptions.ProxyError(proxy)
        calls = []

        def submit(**parameters):
            client_id = parameters["client_order_id"]
            calls.append(("submit", client_id))
            with self.portfolio._ledger.connect(write=False) as connection:
                row = connection.execute("SELECT payload FROM orders WHERE client_id=?", (client_id,)).fetchone()
            self.assertEqual(Ledger.decode(row[0])["qty"], "0.001")
            return api._session.request("POST", "https://paper-api.alpaca.markets/v2/orders")

        def lookup(client_id):
            calls.append(("lookup", client_id))
            return api._session.request("GET", "https://paper-api.alpaca.markets/v2/orders:by_client_order_id")

        api.submit_order.side_effect = submit
        api.get_order_by_client_order_id.side_effect = lookup
        with patch.object(config, "TRADER_PROXY_URL", proxy), \
                patch("bot.utils.tradeapi.REST", return_value=api), \
                patch("bot.portfolio.time.sleep"), self.assertLogs("bot.portfolio", level="WARNING") as logs:
            self.portfolio.api = create_api()
            with self.assertRaises(requests.exceptions.ProxyError) as error:
                self.portfolio._submit(symbol="BTC/USD", qty=0.001, client_order_id="intent-1")

        self.assertEqual(calls, [("submit", "intent-1"), ("lookup", "intent-1"),
                                 ("submit", "intent-1"), ("lookup", "intent-1"),
                                 ("submit", "intent-1")])
        self.assertNotIn("secret", str(error.exception))
        self.assertNotIn("secret", "\n".join(logs.output))
        with self.portfolio._ledger.connect(write=False) as connection:
            row = connection.execute("SELECT payload FROM orders WHERE client_id='intent-1'").fetchone()
        self.assertNotIn("observation", Ledger.decode(row[0]))

    def test_locked_database_latches_fault(self):
        self.portfolio._ledger.timeout = 0.01
        with self.portfolio._ledger.connect():
            with self.assertRaises(PersistenceError):
                self.portfolio._save_state()
        self.assertTrue(self.portfolio.persistence_fault)

    def test_scheduler_stops_after_persistence_failure(self):
        from bot.main import Task, TradingBot
        bot = TradingBot.__new__(TradingBot)
        bot.portfolio = self.portfolio
        bot.running = True
        task = Task("monitor", 30, Mock(side_effect=PersistenceError("failed write")))
        with self.assertRaises(PersistenceError):
            bot._run_task(task)
        self.assertFalse(bot.running)

    def test_ack_write_failure_keeps_intent_for_restart(self):
        self.portfolio.api.submit_order.return_value = SimpleNamespace(id="broker-1", client_order_id="intent-1")
        with patch.object(self.portfolio._ledger, "observe_order", side_effect=sqlite3.OperationalError("disk full")):
            with self.assertRaises(PersistenceError):
                self.portfolio._submit(symbol="BTC/USD", qty=0.001, client_order_id="intent-1")
        self.portfolio.api.submit_order.assert_called_once()
        with self.portfolio._ledger.connect(write=False) as connection:
            self.assertIsNotNone(connection.execute("SELECT payload FROM orders WHERE client_id='intent-1'").fetchone())

    def test_activity_sync_deduplicates_and_keeps_failed_page_checkpoint(self):
        activity = SimpleNamespace(id="transfer-1", _raw={"id": "transfer-1", "net_amount": "100"})
        self.portfolio.api.get_activities.return_value = [activity]
        self.portfolio.sync_activities()
        self.portfolio.sync_activities()
        self.assertEqual(self.portfolio._ledger.summary()["counts"]["broker_activities"], 1)
        checkpoint = self.portfolio._ledger.get_metadata("activities_synced_at")
        self.portfolio.api.get_activities.return_value = [activity] * 100
        with self.assertRaises(TimeoutError):
            self.portfolio.sync_activities(max_pages=1)
        self.assertEqual(self.portfolio._ledger.get_metadata("activities_synced_at"), checkpoint)