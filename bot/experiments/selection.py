"""Selection on selection windows only: gates, ranking, and neighbouring-parameter stability."""
import math
from typing import Dict, List

import numpy as np
import pandas as pd

from bot.statistics import bootstrap_draws, sharpe_interval


def annual_days(has_crypto: bool) -> int:
    return 365 if has_crypto else 252


def pooled_sharpe(returns: pd.Series, days: int):
    if len(returns) < 2:
        return math.nan
    deviation = float(returns.std())
    return float(returns.mean() / deviation * math.sqrt(days)) if deviation > 1e-10 else math.nan


def score_trial(trial, windows, cells, selection, trial_count: int) -> dict:
    """cells: {(trial_id, window_name): Cell}; only selection windows are passed in."""
    used = [window for window in windows if (trial.trial_id, window.name) in cells]
    rows = [cells[(trial.trial_id, window.name)].row for window in used]
    returns = [cells[(trial.trial_id, window.name)].returns for window in used]
    pooled = pd.concat(returns) if returns else pd.Series(dtype=float)
    days = annual_days(any(cells[(trial.trial_id, window.name)].has_crypto for window in used))
    window_returns = [row["total_return"] for row in rows]
    trades = int(sum(row["trades"] for row in rows))
    positive = sum(value > 0 for value in window_returns) / len(window_returns) if window_returns else 0.0
    score = dict(
        trial_id=trial.trial_id, entry_id=trial.entry_id, label=trial.label, is_baseline=trial.is_baseline,
        windows=len(used), trades=trades, positive_window_fraction=positive,
        pooled_sharpe=pooled_sharpe(pooled, days),
        mean_window_return=float(np.mean(window_returns)) if window_returns else math.nan,
        worst_window_return=float(np.min(window_returns)) if window_returns else math.nan,
        worst_max_drawdown=float(np.min([row["max_dd"] for row in rows])) if rows else math.nan,
        mean_cost_drag_pct=float(np.mean([row["cost_drag_pct"] for row in rows])) if rows else math.nan,
        sharpe_ci_low=None, sharpe_ci_high=None, trial_count=trial_count)
    reasons = []
    if trades < selection.min_trades:
        reasons.append(f"natural exits {trades} < {selection.min_trades}")
    if positive < selection.min_positive_window_fraction:
        reasons.append(f"profitable windows {positive:.2f} < {selection.min_positive_window_fraction:.2f}")
    if not math.isfinite(score[selection.rank_by]):
        reasons.append(f"{selection.rank_by} undefined")
    if not reasons:
        try:
            bootstrap_draws(trial_count)
        except ValueError:
            interval = [None, None]   # cumulative trial budget exceeds the supported bootstrap resolution; fail closed
        else:
            interval = sharpe_interval(pooled, days, trials=trial_count)
        score["sharpe_ci_low"], score["sharpe_ci_high"] = interval
        if selection.require_ci_lower_positive:
            if interval[0] is None:
                reasons.append("trial-adjusted Sharpe interval undefined (too few observations or no variance)")
            elif interval[0] <= 0:
                reasons.append(f"trial-adjusted Sharpe lower bound {interval[0]:.2f} <= 0")
    score["eligible"] = not reasons
    score["reasons"] = reasons
    return score


def add_neighbour_stability(scores: List[dict], trials: Dict[str, object], rank_by: str) -> None:
    """Compare each trial with grid neighbours (one axis, one step); isolated peaks are fragile."""
    indices = {trial_id: trial.grid_index for trial_id, trial in trials.items()}
    for item in scores:
        mine = indices[item["trial_id"]]
        values = []
        if mine:
            for other in scores:
                theirs = indices[other["trial_id"]]
                if other is item or set(theirs) != set(mine) or not math.isfinite(other[rank_by]):
                    continue
                differing = [key for key in mine if mine[key] != theirs[key]]
                if len(differing) == 1 and abs(mine[differing[0]] - theirs[differing[0]]) == 1:
                    values.append(other[rank_by])
        item["neighbours"] = len(values)
        item["neighbour_mean"] = float(np.mean(values)) if values else None
        item["stability_gap"] = item[rank_by] - item["neighbour_mean"] \
            if values and math.isfinite(item[rank_by]) else None


def rank(scores: List[dict], rank_by: str) -> List[dict]:
    def key(item):
        value = item[rank_by]
        return (not item["eligible"], -value if math.isfinite(value) else math.inf, item["trial_id"])
    ordered = sorted(scores, key=key)
    for position, item in enumerate(ordered, 1):
        item["rank"] = position
    return ordered


def choose_locked(ranked: List[dict]):
    """Best eligible trial id, or None. Called exactly once, before any holdout window is evaluated."""
    for item in ranked:
        if item["eligible"]:
            return item["trial_id"]
    return None
