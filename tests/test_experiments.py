import copy
import io
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd

import config
from bot.experiments import cli
from bot.experiments.dataset import Dataset, DatasetError, prepare_dataset
from bot.experiments.runner import ExperimentDataError, WindowData, estimate_simulations, run_experiment
from bot.experiments.spec import SpecError, expand_all, parse_spec, required_coverage
from bot.research import prior_exposures

START = pd.Timestamp("2025-01-01", tz="UTC")
DAYS = 32
NOW = pd.Timestamp("2026-01-01", tz="UTC")


def minutes(days=DAYS, seed=3):
    index = pd.date_range(START, periods=days * 1440, freq="1min", tz="UTC")
    rng = np.random.default_rng(seed)
    close = 20000 * np.exp(np.cumsum(rng.normal(0, 0.0007, len(index)) + 0.00001 * np.sin(np.arange(len(index)) / 3000)))
    volume = rng.uniform(1, 3, len(index)) * np.where(rng.random(len(index)) < 0.01, 6, 1)
    open_ = np.r_[close[0], close[:-1]]
    return pd.DataFrame(dict(open=open_, high=np.maximum(open_, close) * 1.0002, low=np.minimum(open_, close) * 0.9998,
                             close=close, volume=volume), index=index)


class FakeHistory:
    def __init__(self, frame):
        self.frame, self.calls = frame, 0
        self.market = SimpleNamespace(get_corporate_actions=lambda *a: [])

    def get(self, symbol, asset_class, start, end, use_cache=True):
        self.calls += 1
        return self.frame.loc[(self.frame.index >= start) & (self.frame.index < end)]


def raw_spec(**changes):
    raw = dict(
        schema_version=1, name="demo",
        windows=[dict(name="a", start="2025-01-12", end="2025-01-17"),
                 dict(name="b", start="2025-01-17", end="2025-01-22"),
                 dict(name="hold", start="2025-01-22", end="2025-01-27", role="holdout")],
        strategies=[dict(id="mb", type="momentum_breakout", symbols=["BTC/USD"], params={"lookback_days": 3}, grid={"period": [10, 20]})],
        selection=dict(min_trades=0, require_ci_lower_positive=False, min_positive_window_fraction=0.0))
    raw.update(changes)
    return raw


class ExperimentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frame = minutes()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def prepare(self, spec, frame=None, root=None):
        trials, _ = expand_all(spec)
        identifier = prepare_dataset(root or self.root / "data", required_coverage(spec, trials),
                                     FakeHistory(self.frame if frame is None else frame), now=NOW)
        return Dataset.open(root or self.root / "data", identifier)

    def test_spec_validation(self):
        bad = [raw_spec(windows=[dict(name="a", start="2025-01-12", end="2025-01-17"),
                                 dict(name="b", start="2025-01-15", end="2025-01-22")]),
               raw_spec(windows=[dict(name="h", start="2025-01-12", end="2025-01-17", role="holdout")]),
               raw_spec(strategies=[dict(id="mb", type="momentum_breakout", symbols=["BTC/USD"],
                                         grid={"nonsense": [1]})]),
               raw_spec(strategies=[dict(id="mb", type="momentum_breakout", symbols=["BTC/USD"],
                                         params={"timeframe": None})]),
               raw_spec(strategies=[dict(id="mb", type=[], symbols=["BTC/USD"])]),
               raw_spec(strategies=[dict(id="mb", type="momentum_breakout", symbols=[["BTC/USD"]])]),
               raw_spec(strategies=[dict(id="mr", type="mean_reversion", symbols=["SPY"],
                                         params={"entry_std": {"SPY": 1.5, "QQQ": 2.0}})]),
               raw_spec(strategies=[dict(id="mr", type="mean_reversion", symbols=["SPY"],
                                         grid={"entry_std.QQQ": [2.0]})]),
               raw_spec(windows=[dict(name="h", start="2025-01-02", end="2025-01-10", role="holdout"),
                                 dict(name="s", start="2025-01-12", end="2025-01-17")]),
               raw_spec(unknown=1)]
        for raw in bad:
            with self.assertRaises(SpecError):
                parse_spec(raw)

    def test_boolean_entry_std_is_rejected_for_params_and_grid(self):
        settings = copy.deepcopy(config.MEAN_REVERSION)
        settings["symbols"] = ["SPY"]
        settings["entry_std"]["SPY"] = True
        with self.assertRaises(ValueError):
            config.validate_strategy("mean_reversion", settings)

        params = dict(id="mr", type="mean_reversion", symbols=["SPY"],
                      params={"entry_std": {"SPY": True}})
        with self.assertRaises(SpecError):
            parse_spec(raw_spec(strategies=[params]))

        grid = dict(id="mr", type="mean_reversion", symbols=["SPY"],
                    grid={"entry_std.SPY": [True]}, baseline=False)
        with self.assertRaisesRegex(SpecError, "no valid parameter combinations"):
            parse_spec(raw_spec(strategies=[grid]))

    def test_schema_version_must_be_an_integer(self):
        for value in (True, 1.0):
            with self.subTest(schema_version=value), self.assertRaises(SpecError):
                parse_spec(raw_spec(schema_version=value))

    def test_estimated_simulation_limit_is_checked_before_preflight_and_simulation(self):
        grid = {key: list(range(1, 6)) for key in ("period", "lookback_days", "volume_mult")}
        spec = parse_spec(raw_spec(strategies=[dict(id="mb", type="momentum_breakout", symbols=["BTC/USD"],
                                                    grid=grid, baseline=False)]))
        trials, _ = expand_all(spec)
        self.assertEqual(estimate_simulations(spec, trials), 256)
        with patch("bot.experiments.runner.preflight", return_value=[]) as check_data, \
                patch("bot.experiments.runner.run_cell") as simulate:
            with self.assertRaisesRegex(ValueError, "256 estimated simulations.*250"):
                run_experiment(spec, None, self.root / "r")
        check_data.assert_not_called()
        simulate.assert_not_called()

        spec_path = self.root / "oversized.json"
        spec_path.write_text(json.dumps(spec.normalized))
        with patch("bot.experiments.cli.preflight", return_value=[]) as check_cli_data, \
                patch("sys.stderr", new_callable=io.StringIO) as stderr:
            result = cli.main(["--dataset-dir", str(self.root / "data"), "validate", str(spec_path)])
        self.assertEqual(result, 2)
        self.assertIn("256 estimated simulations", stderr.getvalue())
        self.assertIn("250", stderr.getvalue())
        check_cli_data.assert_not_called()

    def test_oversized_grid_is_rejected_before_cartesian_expansion(self):
        grid = {key: list(range(5)) for key in ("period", "lookback_days", "volume_mult")}
        with patch("bot.experiments.spec.itertools.product",
                   side_effect=AssertionError("oversized grid was expanded")) as product:
            with self.assertRaisesRegex(SpecError, "including baseline"):
                parse_spec(raw_spec(strategies=[dict(id="mb", type="momentum_breakout",
                                                      symbols=["BTC/USD"], grid=grid)]))
        product.assert_not_called()

    def test_aggregate_grid_limit_is_checked_before_cartesian_expansion(self):
        grid = {key: list(range(5)) for key in ("period", "lookback_days", "volume_mult")}
        strategies = [dict(id=f"mb{i}", type="momentum_breakout", symbols=["BTC/USD"],
                           grid=grid, baseline=False) for i in range(5)]
        with patch("bot.experiments.spec.itertools.product",
                   side_effect=AssertionError("oversized grids were expanded")) as product:
            with self.assertRaisesRegex(SpecError, "experiment limit"):
                parse_spec(raw_spec(strategies=strategies))
        product.assert_not_called()

    def test_research_pairing_allowed_but_live_rejects(self):
        spec = parse_spec(raw_spec(strategies=[dict(id="tf", type="trend_following", symbols=["BTC/USD"])]))
        self.assertEqual(len(expand_all(spec)[0]), 1)
        cfg = copy.deepcopy(config.TREND_FOLLOWING)
        cfg["symbols"] = ["BTC/USD"]
        with self.assertRaises(ValueError):
            config.validate_strategy("trend_following", cfg)

    def test_dataset_detects_tamper_and_missing_coverage(self):
        spec = parse_spec(raw_spec())
        dataset = self.prepare(spec)
        self.assertFalse(dataset.covers("BTC/USD", START - pd.Timedelta(days=5), START))
        path = dataset.directory / "BTCUSD.csv.gz"
        path.write_bytes(path.read_bytes() + b"x")
        with self.assertRaises(DatasetError):
            Dataset.open(self.root / "data", dataset.dataset_id).minutes("BTC/USD")
        late = parse_spec(raw_spec(windows=[        dict(name="a", start="2025-01-12", end="2025-01-17"),
                                            dict(name="z", start="2025-06-01", end="2025-06-20")]))
        with self.assertRaises(ExperimentDataError):
            run_experiment(late, self.prepare(spec, root=self.root / "d2"), self.root / "r")

    def test_locks_before_holdout_is_deterministic_and_holdout_is_single_use(self):
        spec = parse_spec(raw_spec())
        dataset = self.prepare(spec)
        import bot.experiments.runner as experiment_runner
        original_run_cell = experiment_runner.run_cell

        def check_lock_before_holdout(*args, **kwargs):
            if args[2].role == "holdout":
                with closing(sqlite3.connect(self.root / "r1" / "registry.sqlite3")) as db:
                    records = [json.loads(payload) for (payload,) in db.execute(
                        "SELECT payload FROM records WHERE kind='parameters_locked' ORDER BY id")]
                self.assertTrue(records)
                run_dir = next(path for path in (self.root / "r1").iterdir() if path.is_dir())
                artifact = json.loads((run_dir / "locked.json").read_text())
                lock_record = next(record for record in records if record["entry"] == args[1].entry_id)
                expected_lock = (dict(trial_id=lock_record["trial_id"], overrides=lock_record["overrides"])
                                 if lock_record["trial_id"] else None)
                self.assertEqual(artifact["locked"][args[1].entry_id], expected_lock)
            return original_run_cell(*args, **kwargs)

        with patch("bot.experiments.runner.trial_history",
                   side_effect=[dict(runs=2, trials=3), dict(runs=3, trials=5)]), \
                patch("bot.experiments.runner.run_cell", side_effect=check_lock_before_holdout):
            first = run_experiment(spec, dataset, self.root / "r1")
        second = run_experiment(spec, dataset, self.root / "r2")
        self.assertEqual([r["final_equity"] for r in first.rows], [r["final_equity"] for r in second.rows])
        for name in ("summary.md", "results.csv", "results.json", "locked.json"):
            self.assertTrue((first.directory / name).is_file())
        results = json.loads((first.directory / "results.json").read_text())
        adjustment = results["trial_adjustment"]
        self.assertEqual(adjustment["current_trials"], len(first.trials))
        self.assertEqual(adjustment["prior_trials_used"], 3)
        self.assertEqual(adjustment["total_trials"], len(first.trials) + 3)
        self.assertIn("Prior registered trials included in adjustment: 3", (first.directory / "summary.md").read_text())
        summary = (first.directory / "summary.md").read_text()
        self.assertIn("include earlier registered trials on this same dataset", summary)
        self.assertIn("unregistered/manual searches and trials on other datasets are not covered", summary)
        with closing(sqlite3.connect(self.root / "r1" / "registry.sqlite3")) as db:
            kinds = [k for (k,) in db.execute("SELECT kind FROM records ORDER BY id")]
        self.assertLess(kinds.index("parameters_locked"), kinds.index("holdout_evaluated") if "holdout_evaluated" in kinds
                        else len(kinds))
        self.assertEqual(kinds[-1], "completed")
        holdout_rows = [r for r in first.rows if r["report_only"]]
        self.assertTrue(holdout_rows)
        with self.assertRaises(ValueError):
            run_experiment(spec, dataset, self.root / "r1")
        reuse = parse_spec(raw_spec(windows=[dict(name="a", start="2025-01-12", end="2025-01-17"),
                                             dict(name="hold", start="2025-01-22", end="2025-01-27")]))
        with self.assertRaisesRegex(ValueError, "holdout"):
            run_experiment(reuse, dataset, self.root / "r1")
        self.assertTrue(prior_exposures(self.root / "r1", ["BTC/USD"], pd.Timestamp("2025-01-23", tz="UTC"), pd.Timestamp("2025-01-24", tz="UTC")))

    def test_window_slices_expose_no_future_or_pre_warmup_data(self):
        spec = parse_spec(raw_spec())
        data = WindowData(self.prepare(spec))
        window = spec.windows[0]
        sliced = data.minutes("BTC/USD", 3, window)
        self.assertEqual(sliced.index[0], window.start - pd.Timedelta(days=10))
        self.assertLessEqual(sliced.index[-1] + pd.Timedelta(minutes=1), window.end)
        bars = data.bars("BTC/USD", (1, "Hour"), 3, window)
        self.assertLessEqual(pd.DatetimeIndex(bars["bar_end"]).max(), window.end)

    def test_window_slices_require_full_warmup_in_one_coverage_interval(self):
        spec = parse_spec(raw_spec())
        window = spec.windows[0]
        warm_start = window.start - pd.Timedelta(days=10)
        split = warm_start + pd.Timedelta(days=5)
        intervals = ([(window.start, window.end)], [(warm_start, split), (split, window.end)])
        for number, coverage in enumerate(intervals):
            with self.subTest(coverage=coverage):
                root = self.root / f"partial-{number}"
                identifier = prepare_dataset(root, {"BTC/USD": coverage}, FakeHistory(self.frame), now=NOW)
                dataset = Dataset.open(root, identifier)
                with self.assertRaisesRegex(DatasetError, "required"):
                    WindowData(dataset).minutes("BTC/USD", 3, window)

    def test_dataset_identity_tracks_content_not_creation_time(self):
        spec = parse_spec(raw_spec())
        trials, _ = expand_all(spec)
        coverage = required_coverage(spec, trials)
        first = prepare_dataset(self.root / "d1", coverage, FakeHistory(self.frame), now=NOW)
        repeated = prepare_dataset(self.root / "d2", coverage, FakeHistory(self.frame),
                                   now=NOW + pd.Timedelta(days=1))
        changed = prepare_dataset(self.root / "d3", coverage, FakeHistory(minutes(seed=4)), now=NOW)
        self.assertEqual(first, repeated)
        self.assertNotEqual(first, changed)

    def test_returned_data_cannot_mutate_verified_or_window_caches(self):
        spec = parse_spec(raw_spec())
        dataset = self.prepare(spec)
        original = dataset.minutes("BTC/USD").close.iloc[0]
        exposed = dataset.minutes("BTC/USD")
        exposed.iloc[0, exposed.columns.get_loc("close")] = 0
        self.assertEqual(dataset.minutes("BTC/USD").close.iloc[0], original)

        data = WindowData(dataset)
        window = spec.windows[0]
        first = data.minutes("BTC/USD", 3, window)
        original = first.close.iloc[0]
        first.iloc[0, first.columns.get_loc("close")] = 0
        self.assertEqual(data.minutes("BTC/USD", 3, window).close.iloc[0], original)

        bars = data.bars("BTC/USD", (1, "Hour"), 3, window)
        original = bars.close.iloc[0]
        bars.iloc[0, bars.columns.get_loc("close")] = 0
        self.assertEqual(data.bars("BTC/USD", (1, "Hour"), 3, window).close.iloc[0], original)

    def test_sparse_warmup_minutes_are_rejected(self):
        spec = parse_spec(raw_spec())
        window = spec.windows[0]
        gap_start = window.start - pd.Timedelta(days=4)
        sparse = self.frame.drop(self.frame.index[
            (self.frame.index >= gap_start) & (self.frame.index < gap_start + pd.Timedelta(minutes=4))])
        dataset = self.prepare(spec, frame=sparse)
        with self.assertRaisesRegex(DatasetError, "gap over two minutes"):
            WindowData(dataset).minutes("BTC/USD", 3, window)

    def test_bootstrap_budget_is_checked_before_simulation(self):
        spec = parse_spec(raw_spec())
        dataset = self.prepare(spec)
        with patch("bot.experiments.runner.trial_history", return_value={"runs": 1, "trials": 124}), \
                patch("bot.experiments.runner.run_cell") as simulate:
            with self.assertRaisesRegex(ValueError, "Global trial-adjusted confidence interval budget exceeded"):
                run_experiment(spec, dataset, self.root / "r")
        simulate.assert_not_called()

    def test_bootstrap_budget_is_rechecked_after_run_registration(self):
        spec = parse_spec(raw_spec())
        dataset = self.prepare(spec)
        history = [{"runs": 1, "trials": 120}, {"runs": 2, "trials": 126}]
        with patch("bot.experiments.runner.trial_history", side_effect=history), \
                patch("bot.experiments.runner.run_cell") as simulate:
            with self.assertRaisesRegex(ValueError, "Global trial-adjusted confidence interval budget exceeded"):
                run_experiment(spec, dataset, self.root / "r")
        simulate.assert_not_called()

    def test_cli_validate_makes_no_network_call(self):
        spec = parse_spec(raw_spec())
        dataset = self.prepare(spec)
        path = self.root / "spec.json"
        path.write_text(json.dumps(raw_spec()))
        with patch.dict(os.environ, {"ALPACA_BASE_URL": "https://invalid.invalid"}), \
                patch.dict(vars(config), {"_runtime_settings": None}), \
                patch.object(config, "configured_proxy_url", side_effect=AssertionError("unexpected proxy lookup")) as proxy, \
                patch.object(config, "load_dotenv", side_effect=AssertionError("unexpected dotenv load")) as dotenv:
            for name in config._RUNTIME_FIELDS:
                vars(config).pop(name, None)
            code = cli.main(["--dataset-dir", str(self.root / "data"), "--research-dir", str(self.root / "r"),
                             "validate", str(path), "--dataset", dataset.dataset_id])
            self.assertEqual(code, 0)
            self.assertEqual(cli.main(["--dataset-dir", str(self.root / "data"), "prepare", str(path)]), 0)
            proxy.assert_not_called()
            dotenv.assert_not_called()


if __name__ == "__main__":
    unittest.main()
