import tempfile
import unittest
from pathlib import Path

import pandas as pd

from bot.research import ResearchRun, write_json, frame_hash
from bot.statistics import bootstrap_draws
from bot.history_cache import MinuteHistory
from bot.data import MarketData
from bot.portfolio import Portfolio, Position
from bot.backtest import fetch_history, Backtester, BtConfig, BacktestResult, optimize_strategy, compute_metrics
import math
from bot.strategies.base import Action, Signal
from bot.risk_manager import RiskManager
from unittest.mock import Mock, patch
from types import SimpleNamespace
from datetime import datetime
from zoneinfo import ZoneInfo
import config
import numpy as np
import json


class FinalRepairs(unittest.TestCase):
    def test_cash_only_negligible_variance_has_no_finite_sharpe(self):
        index = pd.date_range("2026-01-01", periods=100, freq="D", tz="UTC")
        years = (index - index[0]).total_seconds() / (365 * 86400)
        equity = pd.Series(100000 * 1.03 ** years, index=index)
        result = BacktestResult(["SPY"], 100000., equity,
                                pd.DataFrame(columns=["pnl", "exit_reason"]), False, risk_free_rate=.03)
        metrics = compute_metrics(result)
        self.assertTrue(math.isnan(metrics["sharpe"]))
        self.assertEqual(metrics["sharpe_ci_95"], [None, None])

    def test_cash_only_irregular_observations_have_no_spurious_sharpe(self):
        index = pd.to_datetime(["2026-11-09 14:30", "2026-11-09 21:00", "2026-11-10 20:30",
                                "2026-11-11 21:00", "2026-11-12 20:00"], utc=True)
        years = (index - index[0]).total_seconds() / (365 * 86400)
        equity = pd.Series(100000 * 1.03 ** years, index=index)
        result = BacktestResult(["SPY"], 100000., equity, pd.DataFrame(columns=["pnl", "exit_reason"]),
                                False, risk_free_rate=.03)
        self.assertTrue(math.isnan(compute_metrics(result)["sharpe"]))

    def test_equity_returns_do_not_add_weekday_market_holidays(self):
        from bot.statistics import daily_returns

        index = pd.to_datetime(["2026-11-25 21:00Z", "2026-11-27 21:00Z"], utc=True)
        equity = pd.Series([100., 101.], index=index)

        returns = daily_returns(equity, crypto=False, risk_free=.03)

        self.assertEqual(len(returns), 1)
        expected = .01 - ((1.03 ** (2 / 365)) - 1)
        self.assertAlmostEqual(returns.iloc[0], expected)

        crypto_returns = daily_returns(equity, crypto=True)
        self.assertEqual(len(crypto_returns), 2)
        self.assertAlmostEqual(crypto_returns.iloc[0], 0.)
        self.assertAlmostEqual(crypto_returns.iloc[1], .01)

    def test_stale_partition_refresh_preserves_longer_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MinuteHistory(Mock(), Path(directory))
            start = pd.Timestamp("2026-11-09", tz="UTC")

            def fetch(symbol, asset, timeframe, beginning, ending):
                index = pd.date_range(beginning, ending, freq="min", inclusive="left")
                return pd.DataFrame(dict(open=100., high=101., low=99., close=100., volume=1.), index=index)

            store.market._fetch = Mock(side_effect=fetch)
            longer = start + pd.Timedelta(minutes=10)
            store.get("BTC/USD", "crypto", start, longer)
            metadata_path = next(Path(directory).glob("*.json"))
            metadata = json.loads(metadata_path.read_text())
            metadata["refreshed_at"] = "2000-01-01T00:00:00+00:00"
            write_json(metadata_path, metadata)
            store.get("BTC/USD", "crypto", start, start + pd.Timedelta(minutes=5))
            self.assertEqual(pd.Timestamp(store.market._fetch.call_args.args[3]), start)
            self.assertEqual(pd.Timestamp(store.market._fetch.call_args.args[4]), longer)
            self.assertEqual(len(store.get("BTC/USD", "crypto", start, longer)), 10)
            self.assertEqual(store.market._fetch.call_count, 2)

    def test_final_holdout_cannot_change_locked_parameters(self):
        start = pd.Timestamp("2023-01-01", tz="UTC")
        end = pd.Timestamp("2026-01-01", tz="UTC")
        split = start + (end - start) * .8

        def engine(data, strategies, cfg):
            strategy = strategies[0]

            def run(symbols, beginning, ending):
                index = pd.date_range(beginning, ending, periods=80)
                gain = .01 if strategy.period == 30 else .001
                if beginning >= split:
                    gain = -.005
                returns = gain + np.sin(np.arange(80)) * .0005
                equity = pd.Series(100000 * np.cumprod(1 + returns), index=index)
                equity.iloc[0] = 100000
                trades = pd.DataFrame({"pnl": [10.] * 40, "exit_reason": ["natural"] * 40})
                return BacktestResult(symbols, 100000., equity, trades, False)

            return SimpleNamespace(run=run)

        with tempfile.TemporaryDirectory() as directory:
            research = ResearchRun(Path(directory), {})
            with patch("bot.backtest.build_strategy", side_effect=lambda name, overrides=None:
                       SimpleNamespace(symbols=["SPY"], period=(overrides or {}).get("period", 20))), \
                    patch("bot.backtest.candidate_overrides", return_value=[{"period": 30}]), \
                    patch("bot.backtest.Backtester", side_effect=engine):
                info = optimize_strategy("mean_reversion", {}, start, end, BtConfig(), research)
            self.assertTrue(info["adopted"])
            self.assertEqual(info["overrides"], {"period": 30})
            self.assertLess(info["final_holdout_candidate"]["sharpe"], 0)
    def test_partitioned_history_runs_with_real_metadata(self):
        index = pd.date_range("2026-11-09", periods=180, freq="min", tz="UTC")
        execution = pd.DataFrame(dict(open=100., high=101., low=99., close=100., volume=1.), index=index)
        strat = SimpleNamespace(name="momentum_breakout", symbols=["BTC/USD"], timeframe=(1, "Hour"),
                                lookback_days=1, min_bars=1, trailing_atr_mult=None,
                                evaluate=lambda *args, **kwargs: Signal(Action.HOLD, "BTC/USD"))
        with tempfile.TemporaryDirectory() as directory, patch("bot.backtest.create_api", return_value=Mock()), \
                patch("bot.history_cache.MinuteHistory.get", return_value=execution):
            data = fetch_history([strat], 1, Path(directory), True)
            engine = Backtester(data, [strat], BtConfig())
            result = engine.run(["BTC/USD"], index[0], index[-1] + pd.Timedelta(minutes=1))
            self.assertTrue(result.equity.index.is_monotonic_increasing)
            self.assertEqual(result.equity.iloc[-1], 100000.)

    def test_live_warmup_fetches_chunks_and_retains_only_features(self):
        market = MarketData(Mock())
        market.get_corporate_actions = Mock(return_value=[])

        def fetch(symbol, asset, timeframe, beginning, ending):
            beginning, ending = pd.Timestamp(beginning), pd.Timestamp(ending)
            self.assertLessEqual(ending - beginning, pd.Timedelta(days=7))
            index = pd.date_range(beginning.ceil("min"), ending.floor("min"), freq="min", inclusive="left")
            return pd.DataFrame(dict(open=100., high=101., low=99., close=100., volume=1.), index=index)

        market._fetch = Mock(side_effect=fetch)
        features = market.equity_features("SPY", (15, "Min"), 10)
        self.assertFalse(features.empty)
        self.assertNotIn("execution", features.attrs)
        first_calls = market._fetch.call_count
        market.equity_features("SPY", (15, "Min"), 10)
        self.assertEqual(market._fetch.call_count, first_calls + 1)
        self.assertLess(pd.Timestamp(market._fetch.call_args.args[4]) - pd.Timestamp(market._fetch.call_args.args[3]),
                        pd.Timedelta(days=4))

    def test_minute_partitions_reuse_extend_and_reject_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MinuteHistory(Mock(), Path(directory))
            start = pd.Timestamp("2026-11-09", tz="UTC")

            def fetch(symbol, asset, timeframe, beginning, ending):
                index = pd.date_range(beginning, ending, freq="min", inclusive="left")
                return pd.DataFrame(dict(open=100., high=101., low=99., close=100., volume=1.), index=index)

            store.market._fetch = Mock(side_effect=fetch)
            end = start + pd.Timedelta(minutes=5)
            store.get("BTC/USD", "crypto", start, end)
            store.get("BTC/USD", "crypto", start, end)
            self.assertEqual(store.market._fetch.call_count, 1)
            extended = store.get("BTC/USD", "crypto", start, end + pd.Timedelta(minutes=2))
            self.assertEqual(len(extended), 7)
            self.assertEqual(pd.Timestamp(store.market._fetch.call_args.args[3]), start + pd.Timedelta(minutes=2))
            path = next(Path(directory).glob("*.csv"))
            path.write_text(path.read_text() + "broken")
            with self.assertRaisesRegex(ValueError, "checksum"):
                store.get("BTC/USD", "crypto", start, end)

    def test_flat_broker_cannot_receive_protective_order(self):
        with patch.object(Portfolio, "_load_state"):
            portfolio = Portfolio(Mock(), RiskManager())
        portfolio.broker_positions = Mock(return_value={})
        portfolio._submit = Mock()
        position = Position("SPY", "equity", "mean_reversion", "long", 10., 100.,
                            "2026-11-13T14:30:00Z", 2., 98., 100.)
        with self.assertRaisesRegex(RuntimeError, "confirmed matching"):
            portfolio._place_server_stop(position, 98.)
        portfolio._submit.assert_not_called()

    def test_invalid_cashflow_does_not_mutate_daily_amount(self):
        with patch.object(Portfolio, "_load_state"):
            portfolio = Portfolio(Mock(), RiskManager())
        portfolio._save_state = Mock()
        portfolio.day = datetime.now(ZoneInfo(config.MARKET_TZ)).date().isoformat()
        portfolio.day_start_equity = 100000.
        portfolio.api.get_activities.return_value = [SimpleNamespace(id="bad", net_amount="nan")]
        with self.assertRaisesRegex(ValueError, "cash-flow"):
            portfolio.update_daily(100000.)
        self.assertEqual(portfolio.daily_cash_flow, 0.)
        self.assertEqual(portfolio.cash_flow_ids, [])
        self.assertTrue(portfolio.daily_halted)

    def test_entry_filled_but_flat_does_not_arm_phantom_stop(self):
        with patch.object(Portfolio, "_load_state"):
            portfolio = Portfolio(Mock(), RiskManager())
        portfolio._save_state = Mock()
        portfolio.get_account = Mock(return_value=SimpleNamespace(equity="100000", buying_power="100000"))
        portfolio.api.get_asset.return_value = SimpleNamespace(tradable=True)
        portfolio.gross_exposure = Mock(return_value=0.)
        portfolio._entry_preflight = Mock(return_value="clear")
        portfolio.broker_positions = Mock(return_value={})
        portfolio._place_server_stop = Mock()
        portfolio._attach_existing_stop = Mock()
        order = SimpleNamespace(id="entry", status="filled", filled_qty="10", filled_avg_price="100")
        portfolio._submit = Mock(return_value=order)
        portfolio._wait_for_fill = Mock(return_value=order)
        self.assertIsNone(portfolio.open_position(config.INSTRUMENTS["SPY"], "long", 2., 100.,
                                                 "mean_reversion", None, "test"))
        self.assertTrue(portfolio.accounting_unresolved)
        self.assertTrue(portfolio.entry_intents)
        portfolio._place_server_stop.assert_not_called()
        portfolio._attach_existing_stop.assert_not_called()

    def test_split_like_change_retains_accounted_fill_units(self):
        with patch.object(Portfolio, "_load_state"):
            portfolio = Portfolio(Mock(), RiskManager())
        portfolio._save_state = Mock()
        position = Position("SPY", "equity", "mean_reversion", "long", 70., 100.,
                            "2026-11-13T14:30:00Z", 2., 98., 100., stop_order_id="stop",
                            stop_filled_qty=30., stop_filled_notional=2940.,
                            stop_order_fills={"stop": {"qty": 30., "notional": 2940.}})
        portfolio.positions["SPY"] = position
        portfolio.broker_positions = Mock(return_value={"SPY": SimpleNamespace(qty="140", avg_entry_price="50")})
        portfolio._get_order = Mock(return_value=SimpleNamespace(id="stop", filled_qty="30", filled_avg_price="98"))
        portfolio.reconcile()
        self.assertEqual(position.qty, 70.)
        self.assertEqual(position.stop_filled_qty, 30.)
        self.assertTrue(portfolio.accounting_unresolved)
        Portfolio._validate_state(portfolio._state_data())

    def test_bootstrap_resolves_multiple_trial_tails(self):
        draws = bootstrap_draws(28, 250)
        self.assertGreaterEqual(draws * (.05 / 28 / 2), 20)
        self.assertEqual(draws, 22400)

    def test_research_preserves_runs_and_refuses_reused_holdout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = ResearchRun(root, {"dataset": "a"})
            first.record("trial", {"parameter": 1})
            first.claim_holdout("trend", ["SPY"], pd.Timestamp("2026-01-01", tz="UTC"), pd.Timestamp("2026-06-01", tz="UTC"))
            second = ResearchRun(root, {"dataset": "b"})
            self.assertNotEqual(first.directory, second.directory)
            with self.assertRaisesRegex(ValueError, "overlaps"):
                second.claim_holdout("other", ["SPY"], pd.Timestamp("2026-02-01", tz="UTC"), pd.Timestamp("2026-07-01", tz="UTC"))

    def test_repeated_descriptive_reports_prevent_later_holdout_claim(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            start, end = pd.Timestamp("2026-01-01", tz="UTC"), pd.Timestamp("2026-06-01", tz="UTC")
            for _ in range(2):
                ResearchRun(root, {}).expose_sample(["SPY"], start, end)
            with self.assertRaisesRegex(ValueError, "overlaps"):
                ResearchRun(root, {}).claim_holdout("trend", ["SPY"], start, end)

    def test_reports_are_strict_json_and_hashes_detect_data_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "metrics.json"
            write_json(target, {"undefined": float("nan"), "infinite": float("inf")})
            self.assertNotIn("NaN", target.read_text())
            self.assertIn("null", target.read_text())
        frame = pd.DataFrame({"close": [100., 101.]})
        before = frame_hash(frame)
        frame.loc[1, "close"] = 102.
        self.assertNotEqual(before, frame_hash(frame))