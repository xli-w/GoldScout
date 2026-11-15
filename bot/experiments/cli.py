"""python -m bot.experiments prepare|validate|run|list|show. Only `prepare --allow-network` can reach the broker."""
import argparse
import json
import sys
from pathlib import Path

import config
from bot.experiments.dataset import Dataset, DatasetError, list_datasets, prepare_dataset, resolve_dataset
from bot.experiments.runner import (MAX_ESTIMATED_SIMULATIONS, ExperimentDataError, check_simulation_budget,
                                    preflight, run_experiment)
from bot.experiments.spec import SpecError, expand_all, load_spec, required_coverage
from bot.research import prior_exposures


def _parser():
    ap = argparse.ArgumentParser(prog="python -m bot.experiments", description=__doc__)
    ap.add_argument("--dataset-dir", type=Path, default=config.BASE_DIR / "experiment_data")
    ap.add_argument("--research-dir", type=Path, default=config.BASE_DIR / "research_runs")
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("prepare", "validate", "run"):
        command = sub.add_parser(name)
        command.add_argument("spec", type=Path)
        if name != "prepare":
            command.add_argument("--dataset", default=None, help="dataset id or 'latest' (default: spec value or latest)")
        if name == "run":
            command.add_argument("--allow-partial", action="store_true",
                                 help="skip strategy/window pairs the dataset cannot support (reported)")
    sub.choices["prepare"].add_argument("--allow-network", action="store_true",
                                        help="required: fetch minute history from the broker data service")
    sub.add_parser("list")
    show = sub.add_parser("show")
    show.add_argument("run_id")
    return ap


def _plan(spec):
    trials, invalid = expand_all(spec)
    return trials, invalid, required_coverage(spec, trials)


def _prepare(args) -> int:
    spec = load_spec(args.spec)
    trials, _, needs = _plan(spec)
    print(f"{len(trials)} trials; required coverage:")
    for symbol, spans in sorted(needs.items()):
        print(f"  {symbol}: " + ", ".join(f"{a:%Y-%m-%d}..{b:%Y-%m-%d}" for a, b in spans))
    if not args.allow_network:
        print("Dry run only. Re-run with --allow-network to download and snapshot this data.")
        return 0
    from bot.history_cache import MinuteHistory
    from bot.utils import create_api
    history = MinuteHistory(create_api(), args.dataset_dir / "minute_partitions")
    identifier = prepare_dataset(args.dataset_dir, needs, history)
    print(f"Dataset ready: {identifier}")
    return 0


def _open(args, spec):
    return Dataset.open(args.dataset_dir, args.dataset or spec.dataset or "latest")


def _validate(args) -> int:
    spec = load_spec(args.spec)
    trials, invalid, needs = _plan(spec)
    estimated = check_simulation_budget(spec, trials)
    print(f"Spec OK: {len(trials)} trials, {len(invalid)} invalid combinations, "
          f"~{estimated} estimated simulations (hard limit {MAX_ESTIMATED_SIMULATIONS}; approximate)")
    for window in spec.holdout_windows():
        for entry in spec.entries:
            clashes = prior_exposures(args.research_dir, list(entry.symbols), window.start, window.end)
            if clashes:
                print(f"  WARNING holdout {window.name} for {entry.entry_id} overlaps earlier exposure; a run would be refused")
    issues = preflight(spec, trials, _open(args, spec))
    for item in issues:
        print(f"  DATA {item['entry']} / {item['window']}: {item['reason']}")
    print("Dataset supports the experiment." if not issues else f"{len(issues)} data problems.")
    return 1 if issues else 0


def _run(args) -> int:
    spec = load_spec(args.spec)
    dataset = _open(args, spec)
    outcome = run_experiment(spec, dataset, args.research_dir, args.allow_partial, progress=lambda m: print(m, flush=True))
    print(f"Run {outcome.run_id} complete: {outcome.directory / 'summary.md'}")
    return 0


def _list(args) -> int:
    for item in list_datasets(args.dataset_dir):
        print(f"{item['dataset_id']}  {item['created_at']}  {item['feed']}  {','.join(item['symbols'])}")
    return 0


def _show(args) -> int:
    run_id = args.run_id
    if not run_id.isalnum():
        raise SpecError("Invalid run id")
    path = args.research_dir / run_id / "summary.md"
    if not path.is_file():
        raise SpecError(f"No summary for run {run_id}")
    print(path.read_text(encoding="utf-8"))
    return 0


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    handlers = dict(prepare=_prepare, validate=_validate, run=_run, list=_list, show=_show)
    try:
        return handlers[args.command](args)
    except (SpecError, DatasetError, ExperimentDataError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
