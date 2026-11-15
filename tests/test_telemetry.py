import json
import copy
import tempfile
import threading
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bot.telemetry import TelemetryPublisher
from bot.main import Task, TradingBot
from bot.ledger import PersistenceError
from dashboard.reader import metadata
from dashboard.telemetry import TelemetryReader


class TelemetryPublisherTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "telemetry.json"

    def publisher(self):
        publisher = TelemetryPublisher(self.path, "synthetic-ledger", "paper", 100.0)
        self.addCleanup(lambda: publisher._thread.join(timeout=2))
        self.addCleanup(publisher.close)
        return publisher

    def test_atomic_snapshot_contains_only_supplied_nonsecret_facts(self):
        publisher = self.publisher()
        self.assertTrue(publisher.submit({"observed_at": "2026-11-13T15:00:00Z", "runtime": "starting"}))
        publisher.close()
        publisher._thread.join(timeout=2)
        payload = json.loads(self.path.read_text())
        self.assertEqual(payload["run_id"], publisher.run_id)
        self.assertEqual(payload["database_id"], "synthetic-ledger")
        self.assertEqual(payload["runtime"], "starting")
        self.assertEqual(set(self.path.parent.iterdir()), {self.path})

    def test_bot_telemetry_uses_portfolio_ledger_identity_boundary(self):
        import sys
        import config
        from bot.ledger import Ledger
        from bot.main import TradingBot
        from bot.portfolio import Portfolio

        ledger = Ledger(self.path.with_name("ledger.sqlite3"))
        portfolio = Portfolio.__new__(Portfolio)
        portfolio._ledger = ledger
        database_id = portfolio.ledger_database_id
        bot = TradingBot.__new__(TradingBot)
        bot.portfolio = SimpleNamespace(ledger_database_id=database_id)
        bot._publish_telemetry = Mock()
        process = Mock()
        process.create_time.return_value = 123.0
        psutil = SimpleNamespace(Process=Mock(return_value=process))

        with patch.dict(sys.modules, {"psutil": psutil}), \
                patch("bot.main.TelemetryPublisher") as publisher_class:
            bot._start_telemetry()

        publisher_class.assert_called_once_with(
            config.STATE_FILE.with_name("bot_telemetry.json"),
            database_id,
            "paper" if config.IS_PAPER else "live",
            123.0,
        )
        self.assertIs(bot.telemetry, publisher_class.return_value)
        bot._publish_telemetry.assert_called_once_with("starting")

    def test_blocked_writer_does_not_block_latest_only_submission(self):
        entered, release = threading.Event(), threading.Event()

        def blocked(encoded):
            entered.set()
            release.wait(timeout=2)

        with patch.object(TelemetryPublisher, "_write", side_effect=blocked):
            publisher = self.publisher()
            self.addCleanup(release.set)
            publisher.submit({"sequence": 1})
            self.assertTrue(entered.wait(timeout=2))
            self.assertTrue(publisher.submit({"sequence": 2}))
            self.assertTrue(publisher.submit({"sequence": 3}))
            self.assertEqual(json.loads(publisher._queue.get_nowait())["sequence"], 3)
            monitor = Mock()
            monitor()
            monitor.assert_called_once()
            release.set()

    def test_failed_write_is_contained_and_temp_file_removed(self):
        publisher = self.publisher()
        with patch("bot.telemetry.os.replace", side_effect=OSError("synthetic disk failure")):
            publisher.submit({"runtime": "running"})
            publisher.close()
            publisher._thread.join(timeout=2)
        self.assertTrue(publisher.failed)
        self.assertFalse(self.path.exists())
        self.assertEqual(list(self.path.parent.iterdir()), [])

    def test_nonfinite_and_oversized_payloads_are_refused(self):
        publisher = self.publisher()
        self.assertFalse(publisher.submit({"number": float("nan")}))
        self.assertFalse(publisher.submit({"value": "x" * TelemetryPublisher.MAX_BYTES}))
        self.assertTrue(publisher.failed)


class SchedulerTelemetryTests(unittest.TestCase):
    def setUp(self):
        self.bot = TradingBot.__new__(TradingBot)
        self.bot.running = True
        self.bot._monitor_ready = False
        self.bot._last_monitor_started_monotonic = None
        self.bot.telemetry = Mock()
        self.bot._telemetry_monitor_at = None
        self.bot._telemetry_protection = {}
        self.bot._telemetry_failures = {}
        self.bot._telemetry_sync_at = None
        self.bot._telemetry_sync_status = "unknown"
        self.bot.portfolio = SimpleNamespace(
            persistence_fault=False, accounting_unresolved=False, daily_halted=False,
            unmanaged_symbols=set(), entry_intents={}, positions={},
            get_account=Mock(return_value=SimpleNamespace(equity="100000")),
            daily_loss_exceeded=Mock(return_value=False), side_map=Mock(return_value={}),
            open_position=Mock(), close_position=Mock())
        self.bot.risk = Mock()
        self.bot.risk.correlation_filter.return_value = (True, "")
        self.bot.intelligence_observer = None

    def test_monitor_completion_is_recorded_only_on_success(self):
        self.bot._run_task(Task("monitor", 30, Mock()))
        observed = self.bot._telemetry_monitor_at
        self.assertIsNotNone(observed)
        self.bot._run_task(Task("monitor", 30, Mock(side_effect=TimeoutError("synthetic timeout"))))
        self.assertEqual(self.bot._telemetry_monitor_at, observed)
        self.assertEqual(self.bot._telemetry_failures, {"monitor": 1})

    def test_slow_monitor_is_stale_and_next_pass_keeps_its_original_cadence(self):
        import config

        task = Task("monitor", config.MONITOR_INTERVAL, Mock())
        with patch("bot.main.time.monotonic", side_effect=(100.0, 145.0)):
            self.bot._run_task(task)

        self.assertEqual(task.next_run, 100.0 + config.MONITOR_INTERVAL)
        self.assertIn("monitor_not_ready",
                      self.bot.telemetry.submit.call_args.args[0]["block_reasons"])

    def test_monitor_failure_blocks_entries_until_recovery_but_preserves_exits(self):
        import config
        from bot.strategies.base import Action, Signal
        from bot.strategies.mean_reversion import MeanReversionStrategy

        strategy = MeanReversionStrategy()
        instrument = config.INSTRUMENTS["SPY"]
        enter = Signal(Action.ENTER_LONG, "SPY", "fixture")
        self.bot._run_task(Task("monitor", 30, Mock()))
        self.assertNotIn("monitor_not_ready", self.bot.telemetry.submit.call_args.args[0]["block_reasons"])
        self.bot._execute(enter, strategy, instrument, 1, 100)
        self.assertEqual(self.bot.portfolio.open_position.call_count, 1)

        self.bot._run_task(Task("monitor", 30, Mock(side_effect=TimeoutError("synthetic timeout"))))
        self.assertIn("monitor_not_ready", self.bot.telemetry.submit.call_args.args[0]["block_reasons"])
        self.bot._execute(enter, strategy, instrument, 1, 100)
        self.assertEqual(self.bot.portfolio.open_position.call_count, 1)

        self.bot.portfolio.positions["SPY"] = SimpleNamespace(side="long")
        self.bot._execute(Signal(Action.EXIT, "SPY", "exit"), strategy, instrument, 1, 100)
        self.bot.portfolio.close_position.assert_called_once_with("SPY", "exit")
        self.bot.portfolio.positions.clear()

        self.bot._run_task(Task("monitor", 30, Mock()))
        self.assertNotIn("monitor_not_ready", self.bot.telemetry.submit.call_args.args[0]["block_reasons"])
        self.bot._execute(enter, strategy, instrument, 1, 100)
        self.assertEqual(self.bot.portfolio.open_position.call_count, 2)

    def test_initial_monitor_failure_enters_retryable_blocked_state(self):
        import config
        from bot.strategies.base import Action, Signal
        from bot.strategies.mean_reversion import MeanReversionStrategy

        strategy = MeanReversionStrategy()
        instrument = config.INSTRUMENTS["SPY"]
        enter = Signal(Action.ENTER_LONG, "SPY", "fixture")
        self.bot.portfolio.get_account = Mock(return_value=SimpleNamespace(
            id="synthetic-account", trading_blocked=False, account_blocked=False,
            equity="100000", buying_power="100000"))
        self.bot.portfolio.bind_account = Mock()
        self.bot.portfolio.update_daily = Mock()
        self.bot.portfolio.manage_positions = Mock(side_effect=TimeoutError("synthetic startup timeout"))

        with patch.object(self.bot, "_start_telemetry"):
            self.bot.startup()
        self.assertFalse(self.bot._monitor_ready)
        self.bot._publish_telemetry("starting")
        self.assertIn("monitor_not_ready", self.bot.telemetry.submit.call_args.args[0]["block_reasons"])

        self.bot._execute(enter, strategy, instrument, 1, 100)
        self.assertEqual(self.bot.portfolio.open_position.call_count, 0)
        self.bot.portfolio.positions["SPY"] = SimpleNamespace(side="long")
        self.bot._execute(Signal(Action.EXIT, "SPY", "exit"), strategy, instrument, 1, 100)
        self.bot.portfolio.close_position.assert_called_once_with("SPY", "exit")

        self.bot.portfolio.positions.clear()
        self.bot._run_task(Task("monitor", 30, Mock()))
        self.bot._execute(enter, strategy, instrument, 1, 100)
        self.assertEqual(self.bot.portfolio.open_position.call_count, 1)

    def test_initial_monitor_persistence_failure_still_aborts_startup(self):
        self.bot.portfolio.get_account = Mock(return_value=SimpleNamespace(
            id="synthetic-account", trading_blocked=False, account_blocked=False,
            equity="100000", buying_power="100000"))
        self.bot.portfolio.bind_account = Mock()
        self.bot.portfolio.update_daily = Mock()

        for error, persistence_fault in (
                (PersistenceError("synthetic persistence failure"), False),
                (RuntimeError("synthetic persistence fault"), True)):
            with self.subTest(persistence_fault=persistence_fault):
                self.bot.portfolio.persistence_fault = persistence_fault
                self.bot.portfolio.manage_positions = Mock(side_effect=error)
                with patch.object(self.bot, "_start_telemetry"):
                    with self.assertRaises(type(error)):
                        self.bot.startup()
                self.assertFalse(self.bot._monitor_ready)

    def test_telemetry_failure_cannot_fail_monitor_task(self):
        self.bot.telemetry.submit.side_effect = OSError("synthetic telemetry disk failure")
        monitor = Mock()
        task = Task("monitor", 30, monitor)
        self.bot._run_task(task)
        monitor.assert_called_once()
        self.assertEqual(task.failures, 0)
        self.assertTrue(self.bot.running)

    def test_bad_optional_position_observation_cannot_fail_monitor(self):
        self.bot.portfolio.positions = {"SPY": object()}
        task = Task("monitor", 30, Mock())
        self.bot._run_task(task)
        self.assertEqual(task.failures, 0)
        self.assertTrue(self.bot.running)

    def test_slow_monitor_cannot_refresh_old_protection_time(self):
        self.bot.portfolio.positions = {"SPY": SimpleNamespace(
            identity="synthetic-entry", qty=6, server_stop=98, protection_verified=True,
            exit_order_id=None, exit_client_order_id=None)}
        began = datetime(2026, 11, 13, 15, tzinfo=timezone.utc)
        ended = began + timedelta(seconds=91)
        with patch("bot.main.datetime") as clock:
            clock.now.side_effect = [began, ended, ended]
            self.bot._run_task(Task("monitor", 30, Mock()))
        sample = self.bot._telemetry_protection["SPY"]
        self.assertEqual(sample["observed_at"], "2026-11-13T15:00:00Z")
        self.assertEqual(self.bot._telemetry_monitor_at, "2026-11-13T15:01:31Z")

    def test_persistence_exception_reports_fault_without_flag(self):
        with self.assertRaises(PersistenceError):
            self.bot._run_task(Task("monitor", 30, Mock(side_effect=PersistenceError("synthetic fault"))))
        observed = self.bot.telemetry.submit.call_args.args[0]
        self.assertEqual(observed["runtime"], "faulted")
        self.assertTrue(observed["persistence_fault"])

    def test_persistence_quarantine_still_stops_scheduler_and_reports_fault(self):
        self.bot.portfolio.persistence_fault = True
        with self.assertRaises(PersistenceError):
            self.bot._run_task(Task("monitor", 30, Mock(side_effect=PersistenceError("synthetic commit failure"))))
        self.assertFalse(self.bot.running)
        self.assertEqual(self.bot.telemetry.submit.call_args.args[0]["runtime"], "faulted")

    def test_stop_requested_during_monitor_skips_ready_strategy_job(self):
        strategy = SimpleNamespace(name="fixture", check_interval=30)
        job = Mock()
        job.done.return_value = True
        self.bot.strategies = [strategy]
        self.bot.data_jobs = {"fixture": job}
        self.bot.portfolio.manage_positions = Mock()
        self.bot.startup = Mock()
        self.bot._publish_telemetry = Mock()
        self.bot.shutdown = Mock()
        self.bot.run_strategy = Mock()

        def stop_after_monitor(task):
            if task.name == "monitor":
                self.bot.running = False

        self.bot._run_task = stop_after_monitor
        with patch("bot.main.time.monotonic", return_value=0), patch("bot.main.time.sleep"):
            self.bot.run()

        self.bot.run_strategy.assert_not_called()
        self.assertIn("fixture", self.bot.data_jobs)
        self.bot.shutdown.assert_called_once()

    def test_stop_request_blocks_entry_but_preserves_exit(self):
        import time
        import config
        from bot.strategies.base import Action, Signal
        from bot.strategies.mean_reversion import MeanReversionStrategy

        strategy = MeanReversionStrategy()
        instrument = config.INSTRUMENTS["SPY"]
        self.bot.running = False
        self.bot._monitor_ready = True
        self.bot._last_monitor_started_monotonic = time.monotonic()
        self.bot._execute(Signal(Action.ENTER_LONG, "SPY", "fixture"), strategy, instrument, 1, 100)
        self.bot.portfolio.open_position.assert_not_called()

        self.bot.portfolio.positions["SPY"] = SimpleNamespace(side="long")
        self.bot._execute(Signal(Action.EXIT, "SPY", "shutdown exit"), strategy, instrument, 1, 100)
        self.bot.portfolio.close_position.assert_called_once_with("SPY", "shutdown exit")

    def test_stale_monitor_blocks_entries_but_preserves_exits(self):
        import config
        from bot.strategies.base import Action, Signal
        from bot.strategies.mean_reversion import MeanReversionStrategy

        strategy = MeanReversionStrategy()
        instrument = config.INSTRUMENTS["SPY"]
        self.bot._monitor_ready = True
        self.bot._last_monitor_started_monotonic = 100.0
        stale_time = 100.0 + config.MONITOR_INTERVAL + 0.1
        with patch("bot.main.time.monotonic", return_value=stale_time):
            self.bot._publish_telemetry()
            self.assertIn("monitor_not_ready",
                          self.bot.telemetry.submit.call_args.args[0]["block_reasons"])
            self.bot._execute(Signal(Action.ENTER_LONG, "SPY", "fixture"), strategy, instrument, 1, 100)
        self.bot.portfolio.open_position.assert_not_called()

        self.bot.portfolio.positions["SPY"] = SimpleNamespace(side="long")
        self.bot._execute(Signal(Action.EXIT, "SPY", "stale monitor exit"), strategy, instrument, 1, 100)
        self.bot.portfolio.close_position.assert_called_once_with("SPY", "stale monitor exit")

    def test_entry_guard_rechecks_monitor_freshness_after_preflight(self):
        import config
        from bot.strategies.base import Action, Signal
        from bot.strategies.mean_reversion import MeanReversionStrategy

        self.bot._monitor_ready = True
        self.bot._last_monitor_started_monotonic = 100.0
        with patch("bot.main.time.monotonic",
                   side_effect=(100.0 + config.MONITOR_INTERVAL - 0.1,
                                100.0 + config.MONITOR_INTERVAL + 0.1)):
            self.bot._execute(
                Signal(Action.ENTER_LONG, "SPY", "fixture"),
                MeanReversionStrategy(), config.INSTRUMENTS["SPY"], 1, 100)
            entry_guard = self.bot.portfolio.open_position.call_args.kwargs["entry_guard"]
            self.assertFalse(entry_guard())

    def test_cleanup_releases_lock_if_observer_close_fails(self):
        from bot.main import main

        bot = SimpleNamespace(
            portfolio=SimpleNamespace(persistence_fault=False),
            _telemetry_fault=False,
            _publish_telemetry=Mock(),
            telemetry=None,
            intelligence_observer=Mock(),
            data_workers=Mock(),
            run=Mock(side_effect=RuntimeError("synthetic run failure")),
        )
        bot.intelligence_observer.close.side_effect = AttributeError("synthetic close failure")
        lock = Mock()
        with patch("bot.main.config.initialize_broker_settings"), \
                patch("bot.main.setup_logging"), \
                patch("bot.main.ProcessLock", return_value=lock), \
                patch("bot.main.TradingBot", return_value=bot), \
                patch("bot.main.signal.signal"), \
                self.assertLogs("bot", level="CRITICAL"):
            with self.assertRaisesRegex(RuntimeError, "synthetic run failure"):
                main()
        bot.data_workers.shutdown.assert_called_once_with(wait=True, cancel_futures=True)
        lock.close.assert_called_once()


class DashboardTelemetryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "sample.json"
        self.now = datetime(2026, 11, 13, 15, tzinfo=timezone.utc)
        self.snapshot = {
            "meta": metadata(self.now, 1, "2026-11-13T14:59:50Z", "paper", "synthetic-ledger"),
            "overview": {"block_reasons": [], "entries": "unknown", "realized_pnl": "8"}, "positions": [],
        }
        self.payload = {
            "version": 1, "database_id": "synthetic-ledger", "environment": "paper", "run_id": "synthetic-run",
            "pid": 123, "process_created_at": 100.0, "observed_at": "2026-11-13T14:59:55Z",
            "last_monitor_completed_at": "2026-11-13T14:59:30Z", "runtime": "running",
            "persistence_fault": False, "accounting_unresolved": False, "daily_halted": False,
            "unmanaged_symbols": [], "block_reasons": [], "task_failures": {},
            "activity_sync": {"status": "unknown", "checkpoint_at": None}, "protection": {},
        }

    def read(self, live=True):
        self.path.write_text(json.dumps(self.payload))
        return TelemetryReader(self.path, process_check=lambda *args: live).augment(copy.deepcopy(self.snapshot), self.now)

    def test_fresh_current_process_and_monitor_are_running(self):
        result = self.read()
        self.assertEqual(result["health"]["runtime"], "running")
        self.assertEqual(result["meta"]["telemetry_run_id"], "synthetic-run")
        self.assertEqual(result["overview"]["entries"], "unknown")

    def test_stale_or_wrong_process_never_claims_running(self):
        self.assertEqual(self.read(live=False)["health"]["runtime"], "unknown")
        self.payload["observed_at"] = "2026-11-13T14:00:00Z"
        self.payload["last_monitor_completed_at"] = "2026-11-13T13:59:30Z"
        result = self.read()
        self.assertEqual(result["health"]["runtime"], "unknown")
        self.assertEqual(result["meta"]["telemetry"]["freshness"], "stale")

    def test_wrong_binding_corrupt_or_missing_is_nonfatal(self):
        self.payload["database_id"] = "another-ledger"
        self.assertEqual(self.read()["health"]["warnings"], ["telemetry_invalid"])
        self.path.write_text("not-json")
        result = TelemetryReader(self.path).augment(copy.deepcopy(self.snapshot), self.now)
        self.assertEqual(result["overview"]["realized_pnl"], "8")
        self.assertEqual(result["health"]["runtime"], "unknown")
        self.path.unlink()
        result = TelemetryReader(self.path).augment(copy.deepcopy(self.snapshot), self.now)
        self.assertEqual(result["health"]["warnings"], ["telemetry_missing"])

    def test_fresh_fault_is_reported_and_never_clears_accounting_block(self):
        self.payload["persistence_fault"] = True
        self.payload["block_reasons"] = ["persistence_fault"]
        self.snapshot["overview"]["block_reasons"] = ["accounting_unresolved"]
        self.snapshot["overview"]["entries"] = "blocked"
        result = self.read()
        self.assertEqual(result["health"]["runtime"], "faulted")
        self.assertIn("accounting_unresolved", result["overview"]["block_reasons"])
        self.assertTrue(result["health"]["accounting_unresolved"])

    def test_failed_monitor_cannot_present_running_health(self):
        self.payload["task_failures"] = {"monitor": 1}
        self.assertEqual(self.read()["health"]["runtime"], "unknown")

    def test_monitor_block_is_reported_only_from_fresh_telemetry(self):
        self.payload["block_reasons"] = ["monitor_not_ready"]
        self.payload["task_failures"] = {"monitor": 1}
        result = self.read()
        self.assertEqual(result["overview"]["entries"], "blocked")
        self.assertIn("monitor_not_ready", result["overview"]["block_reasons"])
        mismatched_process = self.read(live=False)
        self.assertEqual(mismatched_process["overview"]["entries"], "unknown")
        self.assertNotIn("monitor_not_ready", mismatched_process["overview"]["block_reasons"])

        self.payload["observed_at"] = "2026-11-13T14:00:00Z"
        self.payload["last_monitor_completed_at"] = "2026-11-13T13:59:30Z"
        result = self.read()
        self.assertEqual(result["overview"]["entries"], "unknown")
        self.assertNotIn("monitor_not_ready", result["overview"]["block_reasons"])

    def test_fractional_monitor_time_cannot_follow_publication(self):
        self.payload["observed_at"] = "2026-11-13T14:59:55Z"
        self.payload["last_monitor_completed_at"] = "2026-11-13T14:59:55.500000Z"
        self.assertEqual(self.read()["health"]["warnings"], ["telemetry_invalid"])

    def test_oversized_task_name_degrades_health_not_accounting(self):
        self.payload["task_failures"] = {"x" * 200: 1}
        result = self.read()
        self.assertEqual(result["health"]["runtime"], "unknown")
        self.assertEqual(result["overview"]["realized_pnl"], "8")

    def test_protection_requires_matching_fresh_evidence(self):
        self.snapshot["positions"] = [{
            "symbol": "SPY", "position_id": "synthetic-entry", "quantity": "6", "broker_stop": "98",
            "protection": "unknown", "protection_observed_at": None, "exit_pending": False,
        }]
        sample = {"position_id": "synthetic-entry", "quantity": "6", "broker_stop": "98",
                  "verified": True, "observed_at": "2026-11-13T14:59:00Z"}
        self.payload["protection"] = {"SPY": sample}
        self.assertEqual(self.read()["positions"][0]["protection"], "last_verified")
        for field, value in (("position_id", "other-entry"), ("quantity", "7"),
                             ("broker_stop", "97"), ("observed_at", "2026-11-13T14:58:00Z")):
            with self.subTest(field=field):
                self.payload["protection"]["SPY"] = dict(sample, **{field: value})
                self.assertEqual(self.read()["positions"][0]["protection"], "unknown")
        self.payload["protection"]["SPY"] = sample
        self.snapshot["positions"][0]["protection"] = "unverified"
        self.assertEqual(self.read()["positions"][0]["protection"], "unverified")


if __name__ == "__main__":
    unittest.main()