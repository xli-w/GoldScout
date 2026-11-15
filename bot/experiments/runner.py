"""Execute an experiment: every trial on every selection window, lock parameters, then report-only holdouts."""
import copy
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import pandas as pd

import config
from bot.backtest import Backtester, buy_and_hold, compute_metrics
from bot.experiments import selection as picking
from bot.experiments.dataset import Dataset, DatasetError
from bot.experiments.spec import (ExperimentSpec, Stress, Trial, Window, expand_all, make_strategy, required_coverage,
                                  warmup)
from bot.research import ResearchRun, code_fingerprint, dependency_versions, trial_history
from bot.sessions import aggregate_crypto, aggregate_equities, validate_execution
from bot.statistics import bootstrap_draws, daily_returns
import sys

log = logging.getLogger("experiments")

RESULT_SCHEMA_VERSION = 1
MAX_ESTIMATED_SIMULATIONS = 250
BOOTSTRAP_SEED = 17   # fixed inside bot.statistics.sharpe_interval
ROW_COLUMNS = [
    "trial_id", "entry_id", "strategy_type", "label", "is_baseline", "window", "role", "report_only", "stress",
    "total_return", "benchmark_return", "excess_return", "max_dd", "sharpe", "sharpe_ci95_unadjusted_low",
    "sharpe_ci95_unadjusted_high", "sortino", "calmar", "cagr", "trades", "forced_closes", "win_rate",
    "profit_factor", "avg_win", "avg_loss", "turnover", "average_gross_exposure", "fees", "slippage_cost",
    "financing_cost", "cost_drag_pct", "blocked_corr", "blocked_daily", "skipped_size", "unfilled_stop_limits",
    "final_equity"]


class ExperimentDataError(ValueError):
    def __init__(self, issues):
        self.issues = issues
        lines = [f"  {item['entry']} / {item['window']}: {item['reason']}" for item in issues[:20]]
        extra = f"\n  ... and {len(issues) - 20} more" if len(issues) > 20 else ""
        super().__init__("Dataset cannot support the experiment:\n" + "\n".join(lines) + extra)


@dataclass
class Cell:
    row: dict
    returns: pd.Series
    has_crypto: bool
    trades: pd.DataFrame
    breakdown: List[dict]
    equity: Optional[pd.Series] = None


class WindowData:
    """Per-window history slices; nothing after window.end and nothing before the warm-up start is exposed."""

    def __init__(self, dataset: Dataset):
        self.dataset = dataset
        self._minutes: Dict[tuple, pd.DataFrame] = {}
        self._bars: Dict[tuple, pd.DataFrame] = {}

    def minutes(self, symbol, lookback_days, window: Window) -> pd.DataFrame:
        key = (symbol, lookback_days, window.name)
        if key not in self._minutes:
            warm_start = window.start - pd.Timedelta(days=lookback_days + 7)
            if not self.dataset.covers(symbol, warm_start, window.end):
                spans = "; ".join(f"{a:%Y-%m-%d}..{b:%Y-%m-%d}" for a, b in self.dataset.coverage(symbol))
                raise DatasetError(f"{symbol}: dataset covers [{spans}], not required "
                                   f"{warm_start:%Y-%m-%d}..{window.end:%Y-%m-%d} warm-up/window interval")
            frame = self.dataset.minutes(symbol)
            frame = frame.loc[(frame.index >= warm_start) & (frame.index + pd.Timedelta(minutes=1) <= window.end)]
            try:
                # Warm-up observations drive indicators too; validate their continuity.
                validate_execution(frame, config.INSTRUMENTS[symbol].asset_class == "equity", warm_start,
                                   window.end)
            except ValueError as exc:
                raise DatasetError(f"{symbol}: {exc}") from exc
            self._minutes[key] = frame
        return self._minutes[key].copy(deep=True)

    def bars(self, symbol, timeframe, lookback_days, window: Window) -> pd.DataFrame:
        key = (symbol, tuple(timeframe), lookback_days, window.name)
        if key not in self._bars:
            minutes = self.minutes(symbol, lookback_days, window)
            amount, unit = timeframe
            duration = amount * (1 if unit == "Min" else 60)
            equity = config.INSTRUMENTS[symbol].asset_class == "equity"
            bars = aggregate_equities(minutes, duration) if equity else aggregate_crypto(minutes, duration)
            bars.attrs["execution"] = minutes
            bars.attrs["corporate_actions"] = self.dataset.actions(symbol)
            self._bars[key] = bars
        result = self._bars[key].copy(deep=True)
        result.attrs = copy.deepcopy(self._bars[key].attrs)
        return result

    def frames(self, trial: Trial, window: Window):
        """Return ({symbol: bars}, strategy) after verifying warm-up and in-window coverage."""
        strategy = make_strategy(trial.strategy_type, trial.symbols, trial.overrides)
        data = {}
        for symbol in trial.symbols:
            bars = self.bars(symbol, strategy.timeframe, int(trial.config["lookback_days"]), window)
            before = int((bars.index < window.start).sum())
            if before < strategy.min_bars:
                raise DatasetError(f"{symbol}: {before} bars before {window.start:%Y-%m-%d} but trial "
                                   f"'{trial.label}' needs {strategy.min_bars} for warm-up")
            if not ((bars.index >= window.start) & (pd.DatetimeIndex(bars["bar_end"]) <= window.end)).any():
                raise DatasetError(f"{symbol}: no completed strategy bars inside window {window.name}")
            data[symbol] = bars
        return data, strategy


def preflight(spec: ExperimentSpec, trials: List[Trial], dataset: Dataset) -> List[dict]:
    """Report every (strategy, window) the dataset cannot support. No simulation is performed."""
    cache = WindowData(dataset)
    issues = []
    for entry in spec.entries:
        for window in spec.windows:
            for trial in (item for item in trials if item.entry_id == entry.entry_id):
                try:
                    cache.frames(trial, window)
                except (DatasetError, ValueError) as exc:
                    issues.append(dict(entry=entry.entry_id, window=window.name, trial=trial.trial_id, reason=str(exc)))
                    break
    return issues


def estimate_simulations(spec: ExperimentSpec, trials: List[Trial]) -> int:
    """Approximate synthetic backtest-cell count; it does not predict wall-clock runtime."""
    finalists = 2 * len(spec.entries) * len(spec.windows) * (1 + len(spec.stress))
    return len(trials) * len(spec.selection_windows()) + finalists


def check_simulation_budget(spec: ExperimentSpec, trials: List[Trial]) -> int:
    estimated = estimate_simulations(spec, trials)
    if estimated > MAX_ESTIMATED_SIMULATIONS:
        raise ValueError(f"{estimated} estimated simulations exceeds the hard limit of "
                         f"{MAX_ESTIMATED_SIMULATIONS}; the estimate is approximate, not a runtime guarantee")
    return estimated


def _check_bootstrap_budget(total_trials):
    try:
        bootstrap_draws(total_trials)
    except ValueError as exc:
        raise ValueError(f"Global trial-adjusted confidence interval budget exceeded "
                         f"({total_trials} current and prior trials): {exc}") from exc


def run_cell(spec: ExperimentSpec, trial: Trial, window: Window, stress: Optional[Stress], data: WindowData,
             keep_equity=False) -> Cell:
    frames, strategy = data.frames(trial, window)
    cfg = spec.bt_config(stress)
    result = Backtester(frames, [strategy], cfg).run(list(trial.symbols), window.start, window.end)
    metrics = compute_metrics(result)
    returns = daily_returns(result.equity, result.has_crypto, cfg.risk_free_rate)
    benchmarks = [buy_and_hold(frames[symbol], window.start, window.end) for symbol in trial.symbols]
    benchmark = float(sum(benchmarks) / len(benchmarks))
    low, high = metrics["sharpe_ci_95"]
    drag = (result.slippage_cost + result.fees_cost + result.financing_cost) / result.initial * 100
    row = dict(
        trial_id=trial.trial_id, entry_id=trial.entry_id, strategy_type=trial.strategy_type, label=trial.label,
        is_baseline=trial.is_baseline, window=window.name, role=window.role, report_only=window.role == "holdout",
        stress=stress.name if stress else "base", total_return=metrics["total_return"], benchmark_return=benchmark,
        excess_return=metrics["total_return"] - benchmark, max_dd=metrics["max_dd"], sharpe=metrics["sharpe"],
        sharpe_ci95_unadjusted_low=low, sharpe_ci95_unadjusted_high=high, sortino=metrics["sortino"],
        calmar=metrics["calmar"], cagr=metrics["cagr"], trades=metrics["trades"], forced_closes=metrics["forced_closes"],
        win_rate=metrics["win_rate"], profit_factor=metrics["profit_factor"], avg_win=metrics["avg_win"],
        avg_loss=metrics["avg_loss"], turnover=metrics["turnover"],
        average_gross_exposure=metrics["average_gross_exposure"], fees=metrics["fees"],
        slippage_cost=result.slippage_cost, financing_cost=result.financing_cost, cost_drag_pct=drag,
        blocked_corr=result.blocked_corr, blocked_daily=result.blocked_daily, skipped_size=result.skipped_size,
        unfilled_stop_limits=result.unfilled_stop_limits, final_equity=metrics["final_equity"])
    trades = result.trades.copy()
    trades.insert(0, "window", window.name)
    trades.insert(0, "stress", row["stress"])
    trades.insert(0, "trial_id", trial.trial_id)
    breakdown = []
    natural = result.trades[result.trades["exit_reason"] != "end of test"]
    for (symbol, side), group in natural.groupby(["symbol", "side"]):
        breakdown.append(dict(trial_id=trial.trial_id, window=window.name, stress=row["stress"], symbol=symbol,
                              side=side, trades=len(group), pnl=float(group["pnl"].sum()),
                              win_rate=100 * float((group["pnl"] > 0).mean())))
    return Cell(row, returns, result.has_crypto, trades, breakdown, result.equity if keep_equity else None)


@dataclass
class Outcome:
    run_id: str
    directory: Path
    spec: ExperimentSpec
    dataset_id: str
    trials: List[Trial]
    invalid: List[dict]
    excluded: List[dict]
    rankings: Dict[str, List[dict]]
    locked: Dict[str, Optional[str]]
    rows: List[dict]
    breakdown: List[dict]
    trades: pd.DataFrame
    curves: Dict[str, pd.Series]
    history: dict
    trial_adjustment_count: int
    holdout_trials: Dict[str, List[str]] = field(default_factory=dict)


def run_experiment(spec: ExperimentSpec, dataset: Dataset, research_dir, allow_partial=False,
                   progress: Callable[[str], None] = lambda message: None) -> Outcome:
    trials, invalid = expand_all(spec)
    check_simulation_budget(spec, trials)
    issues = preflight(spec, trials, dataset)
    if issues and not allow_partial:
        raise ExperimentDataError(issues)
    excluded = {(item["entry"], item["window"]): item for item in issues}
    for entry in spec.entries:
        if not any((entry.entry_id, window.name) not in excluded for window in spec.selection_windows()):
            raise ExperimentDataError([item for item in issues if item["entry"] == entry.entry_id])

    def windows_for(entry_id, role=None):
        return [window for window in spec.windows if (entry_id, window.name) not in excluded
                and (role is None or window.role == role)]

    history = trial_history(research_dir, dataset.dataset_id)
    total_trials = len(trials) + history["trials"]
    _check_bootstrap_budget(total_trials)
    metadata = dict(
        schema_version=RESULT_SCHEMA_VERSION,
        experiment=dict(name=spec.name, spec_hash=spec.spec_hash, dataset_id=dataset.dataset_id, trials=len(trials)),
        spec=spec.normalized, dataset=dict(dataset_id=dataset.dataset_id, feed=dataset.manifest["feed"],
            created_at=dataset.manifest["created_at"],
            symbols={s: {k: v for k, v in info.items() if k != "corporate_actions"} | dict(
                corporate_actions=len(info["corporate_actions"])) for s, info in dataset.manifest["symbols"].items()}),
        bootstrap_seed=BOOTSTRAP_SEED, prior_trials_on_dataset=history, excluded=list(excluded.values()),
        invalid_combinations=invalid, sources=code_fingerprint(Path(config.__file__).resolve().parent),
        python=sys.version, dependencies=dependency_versions())
    research = ResearchRun(Path(research_dir), metadata)
    try:
        # The started record reserves this run's trials. Recheck after registration so concurrent
        # runs on the same dataset cannot all pass against the same stale history count.
        total_trials = trial_history(research_dir, dataset.dataset_id)["trials"]
        _check_bootstrap_budget(total_trials)
        return _execute(spec, dataset, trials, invalid, list(excluded.values()), windows_for, research, history,
                        total_trials, progress)
    except BaseException as exc:
        research.record("failed", dict(reason=str(exc), kind=type(exc).__name__))
        raise


def _execute(spec, dataset, trials, invalid, excluded, windows_for, research, history, total_trials, progress) -> Outcome:
    # Reserve holdouts and register every other interval (with its warm-up history) as exposed before any simulation.
    for entry in spec.entries:
        lead = max(warmup(trial) for trial in trials if trial.entry_id == entry.entry_id)
        for window in windows_for(entry.entry_id):
            if window.role == "holdout":
                research.claim_holdout(entry.entry_id, list(entry.symbols), window.start, window.end)
        for window in windows_for(entry.entry_id):
            if window.role != "holdout":
                research.expose_sample(list(entry.symbols), window.start - lead, window.end, forbid_held_out=True)

    data = WindowData(dataset)
    rows, breakdown, trade_frames, curves = [], [], [], {}
    cells: Dict[Tuple[str, str], Cell] = {}
    rankings, locked, by_id = {}, {}, {trial.trial_id: trial for trial in trials}

    for entry in spec.entries:
        entry_trials = [trial for trial in trials if trial.entry_id == entry.entry_id]
        selection_windows = windows_for(entry.entry_id, "selection")
        scores = []
        for number, trial in enumerate(entry_trials, 1):
            progress(f"{entry.entry_id}: trial {number}/{len(entry_trials)} {trial.label}")
            for window in selection_windows:
                cells[(trial.trial_id, window.name)] = run_cell(spec, trial, window, None, data)
            score = picking.score_trial(trial, selection_windows, cells, spec.selection, total_trials)
            scores.append(score)
            research.record("candidate_evaluated", dict(trial_id=trial.trial_id, label=trial.label,
                            overrides=trial.overrides, score=score))
        picking.add_neighbour_stability(scores, {t.trial_id: t for t in entry_trials}, spec.selection.rank_by)
        rankings[entry.entry_id] = picking.rank(scores, spec.selection.rank_by)
        locked[entry.entry_id] = picking.choose_locked(rankings[entry.entry_id])
        for trial in entry_trials:
            for window in selection_windows:
                cell = cells[(trial.trial_id, window.name)]
                rows.append(cell.row)
                breakdown.extend(cell.breakdown)
                trade_frames.append(cell.trades)

    # Parameters are recorded as locked before any holdout window is simulated.
    for entry in spec.entries:
        research.record("parameters_locked", dict(entry=entry.entry_id, trial_id=locked[entry.entry_id],
            overrides=by_id[locked[entry.entry_id]].overrides if locked[entry.entry_id] else None,
            reason=None if locked[entry.entry_id] else "no candidate passed the selection gates"))
    from bot.experiments.report import write_locked
    write_locked(research.directory, research.identity, dataset.dataset_id, spec.spec_hash, trials, locked)
    holdout_trials: Dict[str, List[str]] = {}
    stresses = [None] + list(spec.stress)
    for entry in spec.entries:
        entry_trials = [trial for trial in trials if trial.entry_id == entry.entry_id]
        finalists = []
        if locked[entry.entry_id]:
            finalists.append(by_id[locked[entry.entry_id]])
        finalists += [trial for trial in entry_trials if trial.is_baseline and trial.trial_id not in
                      {item.trial_id for item in finalists}]
        holdout_trials[entry.entry_id] = [trial.trial_id for trial in finalists]
        for trial in finalists:
            for window in windows_for(entry.entry_id):
                for stress in stresses:
                    progress(f"{entry.entry_id}: finalist {trial.label} / {window.name} / {stress.name if stress else 'base'}")
                    cell = run_cell(spec, trial, window, stress, data, keep_equity=stress is None)
                    cached = cells.get((trial.trial_id, window.name)) if stress is None else None
                    if cached is not None:
                        if not math.isclose(cached.row["final_equity"], cell.row["final_equity"], rel_tol=1e-12):
                            raise RuntimeError(f"Non-deterministic simulation for {trial.trial_id}/{window.name}")
                    else:
                        rows.append(cell.row)
                        breakdown.extend(cell.breakdown)
                        trade_frames.append(cell.trades)
                        if window.role == "holdout" and stress is None:
                            research.record("holdout_evaluated", dict(entry=entry.entry_id, trial_id=trial.trial_id,
                                            window=window.name, locked=trial.trial_id == locked[entry.entry_id],
                                            metrics=cell.row))
                    if stress is None:
                        index = cell.equity.resample("1h").last().dropna()
                        curves[f"{trial.trial_id}__{window.name}"] = index

    trades = pd.concat(trade_frames, ignore_index=True) if trade_frames else pd.DataFrame()
    outcome = Outcome(research.identity, research.directory, spec, dataset.dataset_id, trials, invalid, excluded,
                      rankings, locked, rows, breakdown, trades, curves, history, total_trials, holdout_trials)
    from bot.experiments.report import write_reports
    write_reports(outcome)
    research.record("completed", dict(locked=locked, directory=str(research.directory)))
    return outcome
