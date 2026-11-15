"""Write reproducible experiment outputs: results.csv/json, summary.md, locked.json, trades.csv, curves/."""
import math
from pathlib import Path

import pandas as pd

from bot.research import json_value, write_json

CAVEATS = [
    "Backtests are simulations: fills use next-bar/minute prices with modelled slippage and fees, not real execution.",
    "Holdout windows are report-only and are consumed once; re-running the same holdout is refused by design.",
    "Results are per strategy entry on flat, independent windows; no combined multi-strategy portfolio is simulated.",
    "Trial-adjusted intervals include earlier registered trials on this same dataset; unregistered/manual searches "
    "and trials on other datasets are not covered.",
    "Nothing here establishes live profitability or readiness; offline or paper success is not execution evidence.",
]


def _fmt(value, digits=2):
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "n/a"
    return f"{value:.{digits}f}"


def _table(headers, rows):
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return "\n".join(lines)


def _summary(outcome) -> str:
    spec, rows = outcome.spec, pd.DataFrame(outcome.rows)
    out = [f"# Experiment: {spec.name}", "", spec.description, "",
           f"- Run: `{outcome.run_id}`", f"- Dataset: `{outcome.dataset_id}`", f"- Spec hash: `{spec.spec_hash}`",
           f"- Trials: {len(outcome.trials)} (invalid combinations: {len(outcome.invalid)})",
           f"- Earlier runs on this dataset: {outcome.history}",
           f"- Prior registered trials included in adjustment: "
           f"{outcome.trial_adjustment_count - len(outcome.trials)} "
           f"(current trials: {len(outcome.trials)}; total adjustment count: {outcome.trial_adjustment_count})", ""]
    out += ["## Windows", "", _table(["window", "start", "end", "role"],
            [[w.name, f"{w.start:%Y-%m-%d}", f"{w.end:%Y-%m-%d}", w.role] for w in spec.windows]), ""]
    if outcome.excluded:
        out += ["## Excluded (data coverage)", "", _table(["strategy", "window", "reason"],
                [[e["entry"], e["window"], e["reason"]] for e in outcome.excluded]), ""]
    if outcome.invalid:
        out += ["## Invalid parameter combinations (not run)", ""]
        out += [f"- {json_value(item)}" for item in outcome.invalid[:25]] + [""]
    for entry in spec.entries:
        ranking = outcome.rankings[entry.entry_id]
        locked = outcome.locked[entry.entry_id]
        out += [f"## {entry.entry_id} ({entry.strategy_type}: {', '.join(entry.symbols)})", "",
                f"Selection uses selection windows only, ranked by `{spec.selection.rank_by}`. "
                f"Locked trial: **{locked or 'none (no candidate passed the gates)'}**.", ""]
        top = ranking[:spec.selection.report_top]
        out += [_table(["rank", "trial", "params", "eligible", "sharpe", "CI low (trial-adj)", "mean win ret %",
                        "worst win ret %", "trades", "pos win frac", "nbr gap", "why not"],
                [[i["rank"], i["trial_id"], i["label"], "yes" if i["eligible"] else "no", _fmt(i["pooled_sharpe"]),
                  _fmt(i["sharpe_ci_low"]), _fmt(i["mean_window_return"]), _fmt(i["worst_window_return"]),
                  i["trades"], _fmt(i["positive_window_fraction"]), _fmt(i["stability_gap"]),
                  "; ".join(i["reasons"])] for i in top]), ""]
        finalists = outcome.holdout_trials[entry.entry_id]
        frame = rows[(rows["entry_id"] == entry.entry_id) & (rows["stress"] == "base")]
        if finalists:
            out += ["### Per-window results (finalists vs buy-and-hold)", ""]
            out += [_table(["trial", "window", "role", "return %", "benchmark %", "excess %", "max dd %", "sharpe",
                            "trades", "cost drag %"],
                    [[r.trial_id + (" (locked)" if r.trial_id == locked else " (baseline)"), r.window,
                      r.role + (" REPORT-ONLY" if r.report_only else ""), _fmt(r.total_return),
                      _fmt(r.benchmark_return), _fmt(r.excess_return), _fmt(r.max_dd), _fmt(r.sharpe), r.trades,
                      _fmt(r.cost_drag_pct)]
                     for r in frame[frame["trial_id"].isin(finalists)].itertuples()]), ""]
            if spec.stress:
                stressed = rows[(rows["entry_id"] == entry.entry_id) & rows["trial_id"].isin(finalists)
                                & (rows["stress"] != "base")]
                out += ["### Cost stress (finalists)", "",
                        _table(["trial", "scenario", "window", "return %", "trades"],
                               [[r.trial_id, r.stress, r.window, _fmt(r.total_return), r.trades]
                                for r in stressed.itertuples()]), ""]
        if locked is None:
            out += ["No parameters were locked; only the baseline is reported.", ""]
    out += ["## Caveats", ""] + [f"- {item}" for item in CAVEATS] + [""]
    return "\n".join(out)


def write_locked(directory, run_id, dataset_id, spec_hash, trials, locked) -> None:
    """Persist the selected parameters before holdout simulation begins."""
    overrides = {trial.trial_id: trial.overrides for trial in trials}
    write_json(Path(directory) / "locked.json", dict(
        run_id=run_id, dataset_id=dataset_id, spec_hash=spec_hash,
        locked={key: (dict(trial_id=value, overrides=overrides[value]) if value else None)
                for key, value in locked.items()}))


def write_reports(outcome) -> None:
    directory = Path(outcome.directory)
    pd.DataFrame(outcome.rows).to_csv(directory / "results.csv", index=False)
    if not outcome.trades.empty:
        outcome.trades.to_csv(directory / "trades.csv", index=False)
    pd.DataFrame(outcome.breakdown).to_csv(directory / "breakdown.csv", index=False)
    curves = directory / "curves"
    curves.mkdir(exist_ok=True)
    for name, series in outcome.curves.items():
        series.rename("equity").to_csv(curves / f"{name}.csv", header=True)
    write_json(directory / "results.json", dict(
        schema_version=1, run_id=outcome.run_id, dataset_id=outcome.dataset_id, spec=outcome.spec.normalized,
        rows=outcome.rows, rankings=outcome.rankings, locked=outcome.locked, invalid=outcome.invalid,
        excluded=outcome.excluded, holdout_trials=outcome.holdout_trials, prior_trials_on_dataset=outcome.history,
        trial_adjustment=dict(total_trials=outcome.trial_adjustment_count, current_trials=len(outcome.trials),
                              prior_trials_used=outcome.trial_adjustment_count - len(outcome.trials))))
    (directory / "summary.md").write_text(_summary(outcome), encoding="utf-8")
