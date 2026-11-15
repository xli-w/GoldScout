import copy
import importlib.util
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import config
from bot import audit, backtest, main, state as state_module, utils
from bot.experiments import cli as experiment_cli
from bot.ledger import Ledger, PersistenceError
from bot.state import Position, validate_state
from scripts.offline_tests import offline_environment


def fresh_config():
    name = "_isolated_test_config"
    spec = importlib.util.spec_from_file_location(name, config.__file__)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load isolated config")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {name: module}):
        spec.loader.exec_module(module)
    return module


class StateValidationTests(unittest.TestCase):
    def state(self):
        return dict(positions={"SPY": asdict(Position("SPY", "equity", "mean_reversion", "long", 10., 100.,
                                                     "2026-11-13T14:30:00Z", 2., 98., 100.))},
                    daily=dict(day="2026-11-13", start_equity=100000., last_equity=100000., realized=0., trades=0),
                    daily_controls=dict(halted=False, accounting_unresolved=False, cash_flow=0., cash_flow_ids=[]),
                    last_bar_ts={"SPY": "2026-11-13T14:30:00Z"}, entry_intents={})

    def test_legacy_optional_defaults_are_valid(self):
        state = self.state()
        validate_state(state)
        for key in ("identity", "stop_order_fills", "stop_group_id"):
            state["positions"]["SPY"].pop(key)
        validate_state(state)

    def test_optional_fields_cannot_escape_validation(self):
        for key, value in (("trailing_stop", -1), ("exit_target", "100"), ("trailing_mult", True),
                           ("exit_sequence", 1.5), ("protection_verified", "yes"), ("stop_order_id", ""),
                           ("entry_time", "2026-11-13T14:30:00"), ("strategy", "trend_following")):
            with self.subTest(key=key):
                state = self.state()
                state["positions"]["SPY"][key] = value
                with self.assertRaises(PersistenceError):
                    validate_state(state)

    def test_daily_and_bar_fields_are_validated(self):
        for section, key, value in (("daily_controls", "cash_flow", "10"), ("daily_controls", "cash_flow_ids", ["id", "id"]),
                                    ("daily", "trades", -1), ("daily", "start_equity", 0)):
            state = self.state()
            state[section][key] = value
            with self.assertRaises(PersistenceError):
                validate_state(state)
        state = self.state()
        state["last_bar_ts"]["SPY"] = "2026-11-13T14:30:00"
        with self.assertRaises(PersistenceError):
            validate_state(state)

    def test_positive_fill_quantity_requires_positive_notional(self):
        state = self.state()
        state["positions"]["SPY"]["stop_filled_qty"] = 30.
        state["positions"]["SPY"]["stop_filled_notional"] = 0.
        with self.assertRaisesRegex(PersistenceError, "both be zero or positive"):
            validate_state(state)

    def test_config_rejects_fractional_periods_and_boolean_timeframes(self):
        for key, value in (("period", 2.5), ("timeframe", (True, "Min")), ("timeframe", (60, "Min"))):
            settings = copy.deepcopy(config.MEAN_REVERSION)
            settings[key] = value
            with self.assertRaises(ValueError):
                config.validate_strategy("mean_reversion", settings)

    def test_local_flows_do_not_initialize_broker_settings(self):
        with patch.dict(os.environ, {"ALPACA_BASE_URL": "https://invalid.invalid"}), \
                patch("proxy_config.configured_proxy_url", side_effect=AssertionError("unexpected proxy lookup")) as proxy, \
                patch("dotenv.load_dotenv", side_effect=AssertionError("unexpected dotenv load")) as dotenv:
            local = fresh_config()
            with patch.object(state_module, "config", local), patch.object(audit, "config", local), \
                    patch.object(backtest, "config", local), patch.object(experiment_cli, "config", local):
                state_module.validate_state(self.state())
                for entry in (audit.main, backtest.main, experiment_cli.main):
                    with self.subTest(entry=entry.__module__), redirect_stdout(io.StringIO()), \
                            self.assertRaises(SystemExit) as stopped:
                        entry(["--help"])
                    self.assertEqual(stopped.exception.code, 0)
                with tempfile.TemporaryDirectory() as directory:
                    with redirect_stdout(io.StringIO()):
                        self.assertEqual(experiment_cli.main(["--dataset-dir", directory, "list"]), 0)
                    local.LEDGER_FILE = Path(directory) / "ledger.sqlite3"
                    local.LOCK_FILE = Path(directory) / "ledger.lock"
                    Ledger(local.LEDGER_FILE).commit(self.state())
                    with redirect_stdout(io.StringIO()) as output:
                        audit.main(["--database", str(local.LEDGER_FILE), "replay"])
                    self.assertEqual(Ledger.decode(output.getvalue()), self.state())
            proxy.assert_not_called()
            dotenv.assert_not_called()

    def test_invalid_broker_endpoint_is_rejected_before_api_creation(self):
        with patch.dict(os.environ, {"ALPACA_BASE_URL": "https://invalid.invalid"}), \
                patch("proxy_config.configured_proxy_url", return_value=""), \
                patch("dotenv.load_dotenv", return_value=False):
            local = fresh_config()
            with patch.object(utils, "config", local), patch("bot.utils.tradeapi.REST") as rest:
                for _ in range(2):
                    with self.assertRaisesRegex(ValueError, "ALPACA_BASE_URL"):
                        utils.create_api()
                rest.assert_not_called()

    def test_broker_settings_keep_proxy_order_and_live_confirmation(self):
        environment = {
            "ALPACA_API_KEY": "synthetic-key",
            "ALPACA_SECRET_KEY": "synthetic-secret",
            "ALPACA_BASE_URL": "https://api.alpaca.markets",
            "ALPACA_DATA_FEED": "synthetic-feed",
            "CONFIRM_LIVE_TRADING": "",
        }
        events = []
        with patch.dict(os.environ, environment), \
                patch("proxy_config.configured_proxy_url",
                      side_effect=lambda: events.append("proxy") or "http://proxy.invalid:8080"), \
                patch("dotenv.load_dotenv", side_effect=lambda path: events.append("dotenv") or False):
            local = fresh_config()
            self.assertEqual(events, [])
            with patch.object(main, "config", local), patch.object(main, "create_api") as create_api:
                with self.assertRaisesRegex(SystemExit, "LIVE endpoint"):
                    main.TradingBot()
                create_api.assert_not_called()
            self.assertFalse(local.IS_PAPER)
            self.assertEqual(local.STOCK_DATA_FEED, "synthetic-feed")
            self.assertEqual(local.TRADER_PROXY_URL, "http://proxy.invalid:8080")
            self.assertEqual(events, ["proxy", "dotenv"])
            with patch.dict(os.environ, {"CONFIRM_LIVE_TRADING": "YES"}):
                confirmed = fresh_config()
                self.assertFalse(confirmed.IS_PAPER)
                self.assertTrue(confirmed.LIVE_TRADING_CONFIRMED)
                with patch.object(utils, "config", confirmed), patch("bot.utils.tradeapi.REST") as rest:
                    utils.create_api()
                rest.assert_called_once_with("synthetic-key", "synthetic-secret",
                                             "https://api.alpaca.markets", api_version="v2")
                self.assertEqual(events, ["proxy", "dotenv", "proxy", "dotenv"])

    def test_offline_guard_restores_uninitialized_broker_settings(self):
        proxy = "http://proxy.invalid:8080"
        environment = {
            "ALPACA_API_KEY": "synthetic-key",
            "ALPACA_SECRET_KEY": "synthetic-secret",
            "ALPACA_BASE_URL": "https://paper-api.alpaca.markets",
            "TRADER_PROXY_URL": proxy,
        }
        with patch.dict(os.environ, environment), patch("dotenv.load_dotenv", return_value=False):
            local = fresh_config()
            with patch.dict(sys.modules, {"config": local}):
                with offline_environment():
                    self.assertEqual(local.ALPACA_API_KEY, "")
                    self.assertEqual(local.TRADER_PROXY_URL, "")
                with patch.object(utils, "config", local), patch("bot.utils.tradeapi.REST") as rest:
                    request = rest.return_value._session.request
                    utils.create_api()._session.request("GET", "https://data.alpaca.markets/example")
                rest.assert_called_once_with("synthetic-key", "synthetic-secret",
                                             "https://paper-api.alpaca.markets", api_version="v2")
                self.assertEqual(request.call_args.kwargs["proxies"]["https"], proxy)

    def test_offline_guard_restores_initialized_settings_after_nesting_and_error(self):
        proxy = "http://proxy.invalid:8080"
        environment = {
            "ALPACA_API_KEY": "synthetic-key",
            "ALPACA_SECRET_KEY": "synthetic-secret",
            "ALPACA_BASE_URL": "https://paper-api.alpaca.markets",
            "ALPACA_DATA_FEED": "outer-feed",
            "TRADER_PROXY_URL": proxy,
        }
        with patch.dict(os.environ, environment), patch("dotenv.load_dotenv", return_value=False):
            local = fresh_config()
            local.initialize_broker_settings()
            with patch.dict(sys.modules, {"config": local}):
                with self.assertRaisesRegex(RuntimeError, "synthetic failure"):
                    with offline_environment():
                        self.assertEqual(local.ALPACA_API_KEY, "")
                        self.assertEqual(local._broker_settings()["ALPACA_API_KEY"], "")
                        self.assertEqual(local._broker_settings()["TRADER_PROXY_URL"], "")
                        self.assertEqual(local.STOCK_DATA_FEED, "outer-feed")
                        with patch.dict(os.environ, {"ALPACA_DATA_FEED": "nested-feed"}):
                            with self.assertRaisesRegex(RuntimeError, "nested failure"):
                                with offline_environment():
                                    self.assertEqual(local.STOCK_DATA_FEED, "nested-feed")
                                    self.assertEqual(local._broker_settings()["ALPACA_API_KEY"], "")
                                    self.assertEqual(local._broker_settings()["TRADER_PROXY_URL"], "")
                                    raise RuntimeError("nested failure")
                        self.assertEqual(local.STOCK_DATA_FEED, "outer-feed")
                        self.assertEqual(local.ALPACA_API_KEY, "")
                        self.assertEqual(local.TRADER_PROXY_URL, "")
                        self.assertEqual(local._broker_settings()["ALPACA_API_KEY"], "")
                        self.assertEqual(local._broker_settings()["TRADER_PROXY_URL"], "")
                        raise RuntimeError("synthetic failure")
                self.assertEqual(local.ALPACA_API_KEY, "synthetic-key")
                self.assertEqual(local.TRADER_PROXY_URL, proxy)

    def test_missing_credentials_block_trading_before_api_creation(self):
        environment = {
            "ALPACA_API_KEY": "", "ALPACA_SECRET_KEY": "", "ALPACA_BASE_URL": "https://paper-api.alpaca.markets",
            "CONFIRM_LIVE_TRADING": "",
        }
        with patch.dict(os.environ, environment), patch("proxy_config.configured_proxy_url", return_value=""), \
                patch("dotenv.load_dotenv", return_value=False):
            local = fresh_config()
            with patch.object(main, "config", local), patch.object(main, "create_api") as create_api:
                with self.assertRaisesRegex(SystemExit, "Missing ALPACA_API_KEY"):
                    main.TradingBot()
                create_api.assert_not_called()

    def test_trading_initializes_broker_settings_before_logging_or_locking(self):
        with patch.object(main.config, "initialize_broker_settings", side_effect=ValueError("invalid endpoint")), \
                patch.object(main, "setup_logging") as logging, patch.object(main, "ProcessLock") as lock:
            with self.assertRaisesRegex(ValueError, "invalid endpoint"):
                main.main()
            logging.assert_not_called()
            lock.assert_not_called()
