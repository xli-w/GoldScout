"""Experiment specification: strict JSON schema, parameter-grid expansion, and trial identities."""
import copy
import hashlib
import itertools
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

import config
from bot.backtest import STRATEGY_CLASSES, BtConfig

SCHEMA_VERSION = 1
MAX_SPEC_BYTES = 1_000_000
MAX_TRIALS_PER_STRATEGY = 125   # sharpe_interval's bootstrap budget supports at most 125 adjusted trials
MAX_TOTAL_TRIALS = 500
MAX_WINDOWS = 50
MAX_GRID_VALUES = 25
NAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
COST_KEYS = ("slippage", "commission", "crypto_taker_fee", "crypto_maker_fee", "risk_free_rate",
             "margin_rate", "short_borrow_rate", "cash_yield_rate")
RATE_KEYS = ("slippage", "crypto_taker_fee", "crypto_maker_fee")
RANK_METRICS = ("pooled_sharpe", "mean_window_return", "worst_window_return")
ROLES = ("selection", "holdout")


class SpecError(ValueError):
    pass


def _no_duplicates(pairs):
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise SpecError(f"Duplicate JSON keys: {sorted({key for key in keys if keys.count(key) > 1})}")
    return dict(pairs)


def _reject_constant(value):
    raise SpecError(f"Non-finite JSON constant {value} is not allowed")


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value, length=16) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()[:length]


def _finite_number(value, label, low=None, high=None, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise SpecError(f"{label} must be a finite number")
    if integer and not isinstance(value, int):
        raise SpecError(f"{label} must be an integer")
    if low is not None and value < low or high is not None and value > high:
        raise SpecError(f"{label} must be within [{low}, {high}]")
    return value


def _only_keys(mapping, allowed, label, required=()):
    if not isinstance(mapping, dict):
        raise SpecError(f"{label} must be an object")
    unknown = sorted(set(mapping) - set(allowed))
    if unknown:
        raise SpecError(f"{label}: unknown keys {unknown}")
    missing = sorted(set(required) - set(mapping))
    if missing:
        raise SpecError(f"{label}: missing keys {missing}")


def _name(value, label):
    if not isinstance(value, str) or not NAME.match(value):
        raise SpecError(f"{label} must match {NAME.pattern}")
    return value


def _timestamp(value, label) -> pd.Timestamp:
    if not isinstance(value, str):
        raise SpecError(f"{label} must be an ISO date/time string")
    try:
        stamp = pd.Timestamp(value)
    except (ValueError, TypeError) as exc:
        raise SpecError(f"{label}: invalid timestamp {value!r}") from exc
    if pd.isna(stamp):
        raise SpecError(f"{label}: invalid timestamp {value!r}")
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


@dataclass(frozen=True)
class Window:
    name: str
    start: pd.Timestamp
    end: pd.Timestamp
    role: str = "selection"


@dataclass(frozen=True)
class Selection:
    rank_by: str = "pooled_sharpe"
    min_trades: int = 30
    min_positive_window_fraction: float = 0.5
    require_ci_lower_positive: bool = True
    report_top: int = 10


@dataclass(frozen=True)
class Stress:
    name: str
    slippage_multiplier: float = 1.0
    fee_multiplier: float = 1.0


@dataclass(frozen=True)
class Trial:
    trial_id: str
    entry_id: str
    strategy_type: str
    symbols: tuple
    config: dict                 # complete effective strategy settings
    overrides: dict              # settings changed from the live defaults
    grid_values: dict            # grid path -> value
    grid_index: dict             # grid path -> index within that grid axis
    is_baseline: bool
    label: str


@dataclass
class StrategyEntry:
    entry_id: str
    strategy_type: str
    symbols: tuple
    params: dict
    grid: Dict[str, list]
    baseline: bool


@dataclass
class ExperimentSpec:
    name: str
    description: str
    capital: float
    costs: dict
    stress: List[Stress]
    windows: List[Window]
    entries: List[StrategyEntry]
    selection: Selection
    dataset: Optional[str]
    normalized: dict = field(repr=False)

    @property
    def spec_hash(self) -> str:
        return hashlib.sha256(canonical(self.normalized).encode()).hexdigest()

    def selection_windows(self):
        return [window for window in self.windows if window.role == "selection"]

    def holdout_windows(self):
        return [window for window in self.windows if window.role == "holdout"]

    def bt_config(self, stress: Optional[Stress] = None) -> BtConfig:
        values = dict(self.costs)
        if stress is not None:
            defaults = BtConfig()
            for key in ("slippage",):
                values[key] = values.get(key, getattr(defaults, key)) * stress.slippage_multiplier
            for key in ("commission", "crypto_taker_fee", "crypto_maker_fee"):
                values[key] = values.get(key, getattr(defaults, key)) * stress.fee_multiplier
        return BtConfig(initial_capital=self.capital, **values)


def make_strategy(strategy_type: str, symbols, settings: dict):
    """Build a strategy for offline research; pairings are not restricted to live instrument ownership."""
    cls, base = STRATEGY_CLASSES[strategy_type]
    cfg = copy.deepcopy(base)
    cfg["symbols"] = list(symbols)
    cfg.update(copy.deepcopy(settings))
    cfg["timeframe"] = tuple(cfg["timeframe"])
    return cls(cfg, research=True)


def _allowed_params(strategy_type):
    return sorted(set(STRATEGY_CLASSES[strategy_type][1]) - {"symbols"})


def _check_path(path, strategy_type, params, symbols):
    parts = path.split(".")
    if len(parts) > 2 or not all(parts):
        raise SpecError(f"Invalid parameter path {path!r}")
    if parts[0] not in _allowed_params(strategy_type):
        raise SpecError(f"{strategy_type}: unknown or non-tunable parameter {parts[0]!r}; "
                        f"allowed {_allowed_params(strategy_type)}")
    if len(parts) == 2:
        if not isinstance(params.get(parts[0], STRATEGY_CLASSES[strategy_type][1].get(parts[0])), dict):
            raise SpecError(f"{path!r}: {parts[0]!r} is not a mapping")
        if parts[0] == "entry_std" and (parts[1] not in config.INSTRUMENTS or parts[1] not in symbols):
            raise SpecError(f"{path!r}: entry_std keys must be selected known symbols {list(symbols)}")


def _json_value(value, label):
    try:
        canonical(value)
    except (TypeError, ValueError) as exc:
        raise SpecError(f"{label}: values must be finite JSON") from exc


def _parse_entry(raw, position) -> StrategyEntry:
    label = f"strategies[{position}]"
    _only_keys(raw, ("id", "type", "symbols", "params", "grid", "baseline"), label, ("id", "type", "symbols"))
    entry_id = _name(raw["id"], f"{label}.id")
    if not isinstance(raw["type"], str) or raw["type"] not in STRATEGY_CLASSES:
        raise SpecError(f"{label}.type must be one of {sorted(STRATEGY_CLASSES)}")
    symbols = raw["symbols"]
    if not isinstance(symbols, list) or not symbols or any(not isinstance(symbol, str) for symbol in symbols) \
            or len(symbols) != len(set(symbols)) or any(symbol not in config.INSTRUMENTS for symbol in symbols):
        raise SpecError(f"{label}.symbols must be unique known instruments {sorted(config.INSTRUMENTS)}")
    params = raw.get("params", {})
    _only_keys(params, _allowed_params(raw["type"]), f"{label}.params")
    _json_value(params, f"{label}.params")
    try:
        make_strategy(raw["type"], symbols, params)
    except (ValueError, KeyError, TypeError) as exc:
        raise SpecError(f"{label}.params: invalid strategy settings: {exc}") from exc
    if raw["type"] == "mean_reversion" and isinstance(params.get("entry_std"), dict):
        unknown = set(params["entry_std"]) - set(symbols)
        if unknown:
            raise SpecError(f"{label}.params.entry_std has unused or unknown symbols {sorted(unknown)}")
    grid = raw.get("grid", {})
    if not isinstance(grid, dict):
        raise SpecError(f"{label}.grid must be an object")
    for path, values in grid.items():
        _check_path(path, raw["type"], params, symbols)
        if "." not in path and path in params:
            raise SpecError(f"{label}: {path!r} appears in both params and grid")
        if not isinstance(values, list) or not 1 <= len(values) <= MAX_GRID_VALUES:
            raise SpecError(f"{label}.grid[{path!r}] must list 1-{MAX_GRID_VALUES} values")
        _json_value(values, f"{label}.grid[{path!r}]")
        if len({canonical(value) for value in values}) != len(values):
            raise SpecError(f"{label}.grid[{path!r}] has duplicate values")
    baseline = raw.get("baseline", True)
    if not isinstance(baseline, bool):
        raise SpecError(f"{label}.baseline must be a boolean")
    return StrategyEntry(entry_id, raw["type"], tuple(symbols), copy.deepcopy(params), copy.deepcopy(grid), baseline)


def _entry_candidate_count(entry):
    count = math.prod(len(values) for values in entry.grid.values()) if entry.grid else 1
    return count + 1 if entry.grid and entry.baseline else count


def _validate_entry_expansion_limit(entry):
    count = _entry_candidate_count(entry)
    if count > MAX_TRIALS_PER_STRATEGY:
        raise SpecError(f"{entry.entry_id}: {count} possible trials including baseline exceed the limit of "
                        f"{MAX_TRIALS_PER_STRATEGY}")
    return count


def _validate_expansion_limits(entries):
    """Reject oversized grids from their axis lengths before constructing Cartesian products."""
    total = sum(_validate_entry_expansion_limit(entry) for entry in entries)
    if total > MAX_TOTAL_TRIALS:
        raise SpecError(f"{total} possible trials exceed the experiment limit of {MAX_TOTAL_TRIALS}")


def expand_trials(entry: StrategyEntry):
    """Return (trials, invalid); invalid lists grid combinations rejected by strategy validation."""
    _validate_entry_expansion_limit(entry)
    base = STRATEGY_CLASSES[entry.strategy_type][1]
    paths = sorted(entry.grid)
    combos = list(itertools.product(*(list(enumerate(entry.grid[path])) for path in paths))) if paths else [()]
    trials, invalid, seen = [], [], {}

    def build(assignments, is_baseline):
        overrides = copy.deepcopy(entry.params)
        for path, (_, value) in zip(paths, assignments):
            top, _, leaf = path.partition(".")
            if leaf:
                overrides.setdefault(top, copy.deepcopy(base[top]))
                overrides[top][leaf] = copy.deepcopy(value)
            else:
                overrides[top] = copy.deepcopy(value)
        settings = copy.deepcopy(base)
        settings["symbols"] = list(entry.symbols)
        settings.update(copy.deepcopy(overrides))
        settings["timeframe"] = list(settings["timeframe"])
        grid_values = {path: value for path, (_, value) in zip(paths, assignments)}
        label = ", ".join(f"{path}={canonical(value)}" for path, value in grid_values.items()) or "baseline"
        try:
            make_strategy(entry.strategy_type, entry.symbols, overrides)
        except (ValueError, KeyError, TypeError) as exc:
            invalid.append(dict(entry=entry.entry_id, label=label, reason=str(exc)))
            return
        trial_id = f"{entry.entry_id}-{digest([entry.strategy_type, settings], 8)}"
        if trial_id in seen:
            if is_baseline:
                old = trials[seen[trial_id]]
                trials[seen[trial_id]] = Trial(old.trial_id, old.entry_id, old.strategy_type, old.symbols, old.config,
                                               old.overrides, old.grid_values, old.grid_index, True,
                                               old.label + " (= baseline)")
            return
        seen[trial_id] = len(trials)
        trials.append(Trial(trial_id, entry.entry_id, entry.strategy_type, entry.symbols, settings, overrides,
                            grid_values, {path: index for path, (index, _) in zip(paths, assignments)},
                            is_baseline, label))

    for combo in combos:
        build(combo, is_baseline=not paths)
    if paths and entry.baseline:
        build((), is_baseline=True)
    return trials, invalid


def expand_all(spec: ExperimentSpec):
    _validate_expansion_limits(spec.entries)
    trials, invalid = [], []
    for entry in spec.entries:
        entry_trials, entry_invalid = expand_trials(entry)
        if not entry_trials:
            raise SpecError(f"{entry.entry_id}: no valid parameter combinations; first error: "
                            f"{entry_invalid[0]['reason'] if entry_invalid else 'none'}")
        if len(entry_trials) > MAX_TRIALS_PER_STRATEGY:
            raise SpecError(f"{entry.entry_id}: {len(entry_trials)} trials exceed the limit of {MAX_TRIALS_PER_STRATEGY}")
        trials.extend(entry_trials)
        invalid.extend(entry_invalid)
    if len(trials) > MAX_TOTAL_TRIALS:
        raise SpecError(f"{len(trials)} trials exceed the experiment limit of {MAX_TOTAL_TRIALS}")
    return trials, invalid


def parse_spec(raw: dict) -> ExperimentSpec:
    _only_keys(raw, ("schema_version", "name", "description", "capital", "costs", "stress", "windows",
                     "strategies", "selection", "dataset"), "spec", ("schema_version", "name", "windows", "strategies"))
    if type(raw["schema_version"]) is not int or raw["schema_version"] != SCHEMA_VERSION:
        raise SpecError(f"Unsupported schema_version {raw['schema_version']!r}; expected integer {SCHEMA_VERSION}")
    name = _name(raw["name"], "name")
    description = raw.get("description", "")
    if not isinstance(description, str) or len(description) > 2000:
        raise SpecError("description must be a string of at most 2000 characters")
    capital = _finite_number(raw.get("capital", 100_000.0), "capital", low=1.0)

    costs = raw.get("costs", {})
    _only_keys(costs, COST_KEYS + ("crypto_software_stops",), "costs")
    for key, value in costs.items():
        if key == "crypto_software_stops":
            if not isinstance(value, bool):
                raise SpecError("costs.crypto_software_stops must be a boolean")
        else:
            _finite_number(value, f"costs.{key}", low=0.0, high=0.999999 if key in RATE_KEYS else None)

    stress = []
    if not isinstance(raw.get("stress", []), list):
        raise SpecError("stress must be a list")
    for position, item in enumerate(raw.get("stress", [])):
        _only_keys(item, ("name", "slippage_multiplier", "fee_multiplier"), f"stress[{position}]", ("name",))
        stress_name = _name(item["name"], f"stress[{position}].name")
        if stress_name == "base" or any(existing.name == stress_name for existing in stress):
            raise SpecError(f"stress[{position}].name must be unique and not 'base'")
        stress.append(Stress(stress_name,
                             _finite_number(item.get("slippage_multiplier", 1.0), "slippage_multiplier", 0.0, 10.0),
                             _finite_number(item.get("fee_multiplier", 1.0), "fee_multiplier", 0.0, 10.0)))

    windows = []
    if not isinstance(raw["windows"], list) or not 1 <= len(raw["windows"]) <= MAX_WINDOWS:
        raise SpecError(f"windows must list 1-{MAX_WINDOWS} entries")
    for position, item in enumerate(raw["windows"]):
        _only_keys(item, ("name", "start", "end", "role"), f"windows[{position}]", ("name", "start", "end"))
        window = Window(_name(item["name"], f"windows[{position}].name"),
                        _timestamp(item["start"], f"windows[{position}].start"),
                        _timestamp(item["end"], f"windows[{position}].end"), item.get("role", "selection"))
        if window.role not in ROLES:
            raise SpecError(f"windows[{position}].role must be one of {ROLES}")
        if window.end - window.start < pd.Timedelta(days=1):
            raise SpecError(f"{window.name}: window must span at least one day (end is exclusive)")
        if any(existing.name == window.name for existing in windows):
            raise SpecError(f"Duplicate window name {window.name!r}")
        windows.append(window)
    ordered = sorted(windows, key=lambda window: window.start)
    for first, second in zip(ordered, ordered[1:]):
        if first.end > second.start:
            raise SpecError(f"Windows {first.name!r} and {second.name!r} overlap; windows must be disjoint "
                            "so pooled statistics never double count and holdouts stay untouched")
    if not any(window.role == "selection" for window in windows):
        raise SpecError("At least one selection window is required")

    if not isinstance(raw["strategies"], list) or not raw["strategies"]:
        raise SpecError("strategies must be a nonempty list")
    entries = [_parse_entry(item, position) for position, item in enumerate(raw["strategies"])]
    if len({entry.entry_id for entry in entries}) != len(entries):
        raise SpecError("Duplicate strategy ids")

    selection_raw = raw.get("selection", {})
    _only_keys(selection_raw, ("rank_by", "min_trades", "min_positive_window_fraction",
                               "require_ci_lower_positive", "report_top"), "selection")
    defaults = Selection()
    rank_by = selection_raw.get("rank_by", defaults.rank_by)
    if rank_by not in RANK_METRICS:
        raise SpecError(f"selection.rank_by must be one of {RANK_METRICS}")
    require_ci = selection_raw.get("require_ci_lower_positive", defaults.require_ci_lower_positive)
    if not isinstance(require_ci, bool):
        raise SpecError("selection.require_ci_lower_positive must be a boolean")
    selection = Selection(
        rank_by,
        _finite_number(selection_raw.get("min_trades", defaults.min_trades), "selection.min_trades", 0, integer=True),
        float(_finite_number(selection_raw.get("min_positive_window_fraction", defaults.min_positive_window_fraction),
                             "selection.min_positive_window_fraction", 0.0, 1.0)),
        require_ci,
        _finite_number(selection_raw.get("report_top", defaults.report_top), "selection.report_top", 1, 100, True))

    dataset = raw.get("dataset")
    if dataset is not None:
        _name(dataset, "dataset")

    normalized = dict(schema_version=SCHEMA_VERSION, name=name, description=description, capital=capital,
                      costs=copy.deepcopy(costs), stress=[vars(item) for item in stress],
                      windows=[dict(name=w.name, start=w.start.isoformat(), end=w.end.isoformat(), role=w.role)
                               for w in windows],
                      strategies=[dict(id=e.entry_id, type=e.strategy_type, symbols=list(e.symbols),
                                       params=e.params, grid=e.grid, baseline=e.baseline) for e in entries],
                      selection=vars(selection), dataset=dataset)
    spec = ExperimentSpec(name, description, capital, dict(costs), stress, windows, entries, selection, dataset, normalized)
    spec.bt_config()
    trials, _ = expand_all(spec)
    for trial in trials:
        for holdout in spec.holdout_windows():
            for window in spec.selection_windows():
                if holdout.start < window.start and holdout.end > window.start - warmup(trial):
                    raise SpecError(f"Selection window {window.name!r} warm-up for '{trial.label}' reaches into "
                                    f"holdout {holdout.name!r}; leave at least {warmup(trial).days} days between them")
    return spec


def load_spec(path) -> ExperimentSpec:
    path = Path(path)
    if path.stat().st_size > MAX_SPEC_BYTES:
        raise SpecError("Experiment file is too large")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_no_duplicates,
                         parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        raise SpecError(f"{path.name}: invalid JSON: {exc}") from exc
    return parse_spec(raw)


def warmup(trial: Trial) -> pd.Timedelta:
    return pd.Timedelta(days=int(trial.config["lookback_days"]) + 7)


def required_coverage(spec: ExperimentSpec, trials: List[Trial]) -> Dict[str, List[tuple]]:
    """Per-symbol merged (start, end) intervals: each window with the longest warm-up of any trial on that symbol."""
    longest: Dict[str, pd.Timedelta] = {}
    for trial in trials:
        for symbol in trial.symbols:
            longest[symbol] = max(longest.get(symbol, pd.Timedelta(0)), warmup(trial))
    needs = {}
    for symbol, lead in longest.items():
        merged: List[list] = []
        for window in sorted(spec.windows, key=lambda item: item.start):
            interval = [window.start - lead, window.end]
            if merged and interval[0] <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], interval[1])
            else:
                merged.append(interval)
        needs[symbol] = [tuple(item) for item in merged]
    return needs
