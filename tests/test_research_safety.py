import base64
from contextlib import redirect_stdout
import io
import traceback
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd
import requests
import urllib3.connection

import config
from bot.sessions import schedule, aggregate_equities, validate_execution
from bot.corporate_actions import adjusted_window, continuous_prices, normalize_actions
from bot.backtest import Backtester, BtPosition, BtConfig, backtest_window
from bot.strategies.mean_reversion import MeanReversionStrategy
from bot.main import TradingBot, Task
from bot.data import MarketData
from bot.portfolio import Portfolio
from bot.risk_manager import RiskManager
from bot.utils import create_api
from proxy_config import ProxyConfigurationError, parse_proxy_url


class ResearchSafetyTests(unittest.TestCase):
    def test_missing_exchange_session_is_rejected(self):
        index = pd.to_datetime(["2026-11-09 14:30", "2026-11-11 14:30"], utc=True)
        frame = pd.DataFrame(dict(open=100., high=101., low=99., close=100., volume=1.), index=index)
        with self.assertRaisesRegex(ValueError, "missing exchange sessions"):
            validate_execution(frame, True)

    def test_minute_monitor_exits_at_mean_target(self):
        index = pd.to_datetime(["2026-11-12 14:30", "2026-11-12 14:45"], utc=True)
        frame = pd.DataFrame(dict(open=[99., 101.], high=[99., 101.], low=[99., 101.],
                                  close=[99., 101.], volume=1.), index=index)
        strat = MeanReversionStrategy(dict(symbols=["SPY"], timeframe=(15, "Min"), lookback_days=10,
                                          check_interval=60, period=2, entry_std={"SPY": 1.5}))
        bt = Backtester({"SPY": frame}, [strat], BtConfig(slippage=0))
        bt.run(["SPY"], index[0], index[-1] + pd.Timedelta(minutes=15))
        bt.positions["SPY"] = BtPosition("SPY", "mean_reversion", "long", 10., 99., index[0], 2., 97., 99., None)
        bt.feature_index["SPY"] = 1
        bt.execution["SPY"] = pd.DataFrame(dict(open=101., high=101., low=100., close=101., volume=1.),
                                          index=pd.to_datetime(["2026-11-12 15:00"], utc=True))
        timestamp = pd.Timestamp("2026-11-12 15:01", tz="UTC")
        bt._monitor_execution("SPY", 0, timestamp)
        self.assertNotIn("SPY", bt.positions)
        self.assertEqual(bt.trades[-1]["exit_time"], timestamp)

    def test_side_mismatch_does_not_cancel_broker_orders(self):
        with patch.object(Portfolio, "_load_state"):
            portfolio = Portfolio(Mock(), RiskManager())
        portfolio._save_state = Mock()
        portfolio.broker_positions = Mock(return_value={"SPY": SimpleNamespace(qty="-100")})
        position = SimpleNamespace(symbol="SPY", side="long", exit_order_id=None,
                                   exit_client_order_id=None, protection_verified=True)
        portfolio._ensure_protection(position)
        self.assertFalse(position.protection_verified)
        portfolio.api.cancel_order.assert_not_called()

    def test_worker_download_failure_is_visible_to_scheduler(self):
        bot = TradingBot.__new__(TradingBot)
        bot.symbol_failures = {}
        bot.portfolio = Mock()
        bot.portfolio.market_is_open.return_value = True
        strat = SimpleNamespace(name="mean_reversion", symbols=["SPY"])
        with self.assertRaises(RuntimeError):
            bot.run_strategy(strat, {"SPY": TimeoutError("download deadline")})
        self.assertEqual(bot.symbol_failures["SPY"], 1)
        bot.portfolio.open_position.assert_not_called()

    def test_calendar_early_close_and_holiday(self):
        table = schedule("2026-11-26", "2026-11-27")
        self.assertEqual(len(table), 1)
        index = pd.date_range("2026-11-27 14:30", "2026-11-27 18:00", freq="min", inclusive="left", tz="UTC")
        frame = pd.DataFrame(dict(open=100., high=101., low=99., close=100., volume=1.), index=index)
        result = aggregate_equities(frame, 240)
        self.assertEqual(len(result), 1)
        self.assertEqual(result.bar_end.iloc[-1], pd.Timestamp("2026-11-27 18:00", tz="UTC"))

    def test_history_has_no_sdk_total_limit(self):
        api = Mock()
        api.get_crypto_bars.return_value = SimpleNamespace(df=pd.DataFrame())
        MarketData(api)._fetch("BTC/USD", "crypto", "1Hour", "start", "end")
        self.assertIsNone(api.get_crypto_bars.call_args.kwargs["limit"])

    def test_corporate_action_pages_are_consumed(self):
        api = Mock()
        api.data_get.side_effect = [dict(corporate_actions={}, next_page_token="next"),
                                    dict(corporate_actions={}, next_page_token=None)]
        self.assertEqual(MarketData(api).get_corporate_actions("SPY", "2020-01-01", "2026-01-01"), [])
        self.assertEqual(api.data_get.call_count, 2)

    def test_split_features_and_dividend_liability(self):
        index = pd.to_datetime(["2026-11-11 15:00", "2026-11-12 15:00"], utc=True)
        frame = pd.DataFrame(dict(open=[100., 50.], high=[100., 50.], low=[100., 50.],
                                  close=[100., 50.], volume=[10., 20.]), index=index)
        action = dict(id="split", kind="split", ratio=2., ex_date="2026-11-12")
        self.assertEqual(adjusted_window(frame, [action], index[0]).close.iloc[0], 100)
        self.assertEqual(adjusted_window(frame, [action], index[1]).close.iloc[0], 50)
        continuous, factor = continuous_prices(frame, [action])
        self.assertEqual(continuous.close.iloc[-1], 100)
        bt = Backtester.__new__(Backtester)
        bt.cash, bt.receivables, bt.pending = 0., {}, {}
        bt.last_price = {"SPY": 100.}
        bt.positions = {"SPY": BtPosition("SPY", "trend", "short", 10., 100., index[0], 2., 102., 100., None)}
        bt._apply_action("SPY", action)
        self.assertEqual(bt.positions["SPY"].qty, 20)
        self.assertEqual(bt.positions["SPY"].hard_stop, 51)
        dividend = dict(id="div", kind="dividend", rate=1.)
        bt._apply_action("SPY", dividend)
        self.assertEqual(bt.receivables["div"], -20)
        bt._apply_action("SPY", dividend, payment=True)
        self.assertEqual(bt.cash, -20)

    def test_unsupported_actions_fail_closed(self):
        with self.assertRaises(ValueError):
            normalize_actions({"spin_offs": [{"symbol": "SPY"}]})

    def test_adoption_does_not_claim_unlinked_existing_stop(self):
        with patch.object(Portfolio, "_load_state"):
            portfolio = Portfolio(Mock(), RiskManager())
        portfolio._save_state = Mock()
        portfolio.get_account = Mock(return_value=SimpleNamespace(equity="100000"))
        order = SimpleNamespace(id="protect", symbol="SPY", time_in_force="gtc", qty="100", filled_qty="0", filled_avg_price=None,
                                status="new", type="stop", side="sell", stop_price="99")
        portfolio._list_open_orders = Mock(return_value=[order])
        portfolio._place_server_stop = Mock()
        portfolio.adopt_position(config.INSTRUMENTS["SPY"], "mean_reversion", None, float("nan"),
                                 SimpleNamespace(qty="100", current_price="100", avg_entry_price="100"))
        self.assertFalse(portfolio.positions["SPY"].protection_verified)
        self.assertIsNone(portfolio.positions["SPY"].stop_order_id)
        self.assertTrue(portfolio.accounting_unresolved)
        portfolio.api.cancel_order.assert_not_called()
        portfolio._place_server_stop.assert_not_called()

    def test_request_timeout_is_explicit(self):
        api = Mock()
        with patch("bot.utils.tradeapi.REST", return_value=api):
            original = api._session.request
            create_api()._session.request("GET", "https://example.invalid")
            self.assertEqual(original.call_args.kwargs["timeout"], (3., 5.))
            self.assertEqual(api._retry, 0)

    def test_configured_proxy_routes_alpaca_requests(self):
        proxy = "http://sample:p%40ss@proxy.invalid:8080"
        api = Mock()
        with patch.object(config, "TRADER_PROXY_URL", proxy), \
                patch("bot.utils.tradeapi.REST", return_value=api):
            original = api._session.request
            session = create_api()._session
            session.request("GET", "https://data.alpaca.markets/example")
            session.request("GET", "http://data.alpaca.markets/example")
            session.request("POST", "https://paper-api.alpaca.markets/v2/orders",
                            proxies={"https": "http://other.invalid:8080"})
            for call in original.call_args_list:
                self.assertEqual(call.kwargs["proxies"]["http"], proxy)
                self.assertEqual(call.kwargs["proxies"]["https"], proxy)
                self.assertEqual(requests.utils.select_proxy(call.args[1], call.kwargs["proxies"]), proxy)

    def test_explicit_proxy_overrides_ambient_proxy_and_bypass(self):
        proxy = "http://sample:secret@proxy.invalid:8080"
        api = Mock()
        api._session = requests.Session()
        url = "https://paper-api.alpaca.markets/v2/account"
        with patch.object(config, "TRADER_PROXY_URL", proxy), \
                patch("bot.utils.tradeapi.REST", return_value=api), \
                patch("requests.sessions.get_netrc_auth", return_value=None), \
                patch("requests.sessions.get_environ_proxies", side_effect=[{"https": "http://other.invalid:8080"}, {}]), \
                patch.object(api._session, "send", return_value=Mock()) as send:
            session = create_api()._session
            session.request("GET", url)
            session.request("GET", url)
        for call in send.call_args_list:
            self.assertEqual(requests.utils.select_proxy(url, call.kwargs["proxies"]), proxy)

    def test_explicit_proxy_overrides_host_specific_session_proxies(self):
        proxy = "http://sample:secret@proxy.invalid:8080"
        url = "https://paper-api.alpaca.markets/v2/account"
        for override in ("", "http://other.invalid:8080"):
            with self.subTest(override=override):
                api = Mock()
                api._session = requests.Session()
                api._session.proxies["https://paper-api.alpaca.markets"] = override
                with patch.object(config, "TRADER_PROXY_URL", proxy), \
                        patch("bot.utils.tradeapi.REST", return_value=api), \
                        patch("requests.sessions.get_netrc_auth", return_value=None), \
                        patch("requests.sessions.get_environ_proxies", return_value={}), \
                        patch.object(api._session, "send", return_value=Mock()) as send:
                    create_api()._session.request("GET", url)
                self.assertEqual(requests.utils.select_proxy(url, send.call_args.kwargs["proxies"]), proxy)

    def test_empty_proxy_preserves_existing_request_options(self):
        api = Mock()
        with patch.object(config, "TRADER_PROXY_URL", ""), \
                patch("bot.utils.tradeapi.REST", return_value=api):
            original = api._session.request
            session = create_api()._session
            session.request("GET", "https://example.invalid")
            self.assertNotIn("proxies", original.call_args.kwargs)
            session.request("GET", "https://example.invalid", proxies={"https": "http://other.invalid"})
            self.assertEqual(original.call_args.kwargs["proxies"], {"https": "http://other.invalid"})

    def test_invalid_proxy_is_rejected_without_exposing_credentials(self):
        invalid = (
            "https://sample:s3cr3t@proxy.invalid:8080",
            "socks5://sample:s3cr3t@proxy.invalid:8080",
            "http://sample:s3cr3t@proxy.invalid:0",
            "http://sample:s3cr3t@proxy.invalid:65536",
            "http://sample:s3cr3t@proxy.invalid:invalid",
            "http://sample:s3cr3t@proxy.invalid/path",
            "http://:s3cr3t@proxy.invalid:8080",
            "http://sample@proxy.invalid:8080",
        )
        for proxy in invalid:
            with self.subTest(proxy=proxy), patch.object(config, "TRADER_PROXY_URL", proxy), \
                    patch("bot.utils.tradeapi.REST") as rest:
                with self.assertRaises(ProxyConfigurationError) as error:
                    create_api()
                self.assertNotIn("s3cr3t", str(error.exception))
                rest.assert_not_called()
        self.assertNotIn("s3cr3t", repr(parse_proxy_url("http://sample:s3cr3t@proxy.invalid:8080")))

    def test_empty_proxy_password_matches_requests_authentication(self):
        for proxy in ("http://sample:@proxy.invalid:8080", "http://sample:p%40ss@proxy.invalid:8080"):
            with self.subTest(proxy=proxy):
                authorization = requests.adapters.HTTPAdapter().proxy_headers(proxy).get("Proxy-Authorization")
                self.assertEqual(parse_proxy_url(proxy).authorization, authorization)

    def test_proxy_request_errors_do_not_expose_credentials(self):
        proxy = "http://sample:s3cr3t@proxy.invalid:8080"
        for error_type in (requests.exceptions.ProxyError, requests.exceptions.ConnectionError,
                           requests.exceptions.InvalidURL, requests.exceptions.ReadTimeout):
            with self.subTest(error_type=error_type):
                api = Mock()
                api._session.request.side_effect = error_type(proxy)
                with patch.object(config, "TRADER_PROXY_URL", proxy), \
                        patch("bot.utils.tradeapi.REST", return_value=api):
                    with self.assertRaises(error_type) as error:
                        create_api()._session.request("GET", "https://example.invalid")
                self.assertNotIn("s3cr3t", str(error.exception))
                self.assertNotIn("s3cr3t", "".join(traceback.format_exception(error.exception)))

    def test_authenticated_alpaca_proxy_does_not_expose_wire_debug(self):
        proxy = "http://sample:s3cr3t@proxy.invalid:8080"
        for url, connection_type in (
            ("https://paper-api.alpaca.markets/v2/account", urllib3.connection.HTTPSConnection),
            ("http://paper-api.alpaca.markets/v2/account", urllib3.connection.HTTPConnection),
        ):
            with self.subTest(url=url):
                api = Mock()
                api._session = requests.Session()
                raw = Mock()
                raw.makefile.return_value = io.BytesIO(b"HTTP/1.0 200 Connection established\r\n\r\n")
                output = io.StringIO()
                with patch.object(config, "TRADER_PROXY_URL", proxy), \
                        patch("bot.utils.tradeapi.REST", return_value=api), \
                        patch("requests.sessions.get_netrc_auth", return_value=None), \
                        patch("requests.sessions.get_environ_proxies", return_value={}), \
                        patch.object(connection_type, "debuglevel", 1), \
                        patch.object(connection_type, "_new_conn", return_value=raw), \
                        patch("urllib3.connection.ssl_wrap_socket", side_effect=OSError("synthetic TLS failure")), \
                        redirect_stdout(output):
                    with self.assertRaisesRegex(ProxyConfigurationError, "wire debugging"):
                        create_api()._session.request("GET", url)
                self.assertNotIn(base64.b64encode(b"sample:s3cr3t").decode("ascii"), output.getvalue())
                raw.makefile.assert_not_called()

    def test_proxy_submit_failure_checks_client_id_before_resubmission(self):
        proxy = "http://sample:s3cr3t@proxy.invalid:8080"
        api = Mock()
        api._session.request.side_effect = requests.exceptions.ProxyError(proxy)
        acknowledged = SimpleNamespace(id="order-1")
        events = []

        def submit(**kwargs):
            events.append("submit")
            return api._session.request("POST", "https://paper-api.alpaca.markets/v2/orders")

        def lookup(client_order_id):
            events.append(("lookup", client_order_id))
            return acknowledged

        api.submit_order.side_effect = submit
        api.get_order_by_client_order_id.side_effect = lookup
        with patch.object(config, "TRADER_PROXY_URL", proxy), \
                patch("bot.utils.tradeapi.REST", return_value=api), \
                patch.object(Portfolio, "_load_state"), patch("bot.portfolio.time.sleep"):
            portfolio = Portfolio(create_api(), RiskManager())
            portfolio._canonical_order = Mock(return_value={"symbol": "SPY"})
            portfolio._save_state = Mock(side_effect=lambda **kwargs: events.append("intent"))
            portfolio._observe_order = Mock()
            with self.assertLogs("bot.portfolio", level="WARNING") as logs:
                result = portfolio._submit(symbol="SPY", qty=1, side="buy", type="market", time_in_force="day")

        client_order_id = api.submit_order.call_args.kwargs["client_order_id"]
        self.assertEqual(events, ["intent", "submit", ("lookup", client_order_id)])
        self.assertIs(result, acknowledged)
        api.submit_order.assert_called_once()
        portfolio._observe_order.assert_called_once_with(acknowledged)
        self.assertNotIn("s3cr3t", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()