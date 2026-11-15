import hashlib
import json
import math
import sqlite3
import uuid
import sys
from importlib.metadata import distributions
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd


def json_value(value):
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    if isinstance(value, (float, np.floating)):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, (pd.Timestamp, datetime, Path)):
        return str(value)
    return value


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(json_value(value), indent=2, allow_nan=False))
    temporary.replace(path)


def frame_hash(frame):
    digest = hashlib.sha256()
    digest.update(json.dumps(list(frame.columns)).encode())
    for start in range(0, len(frame), 10000):
        digest.update(pd.util.hash_pandas_object(frame.iloc[start:start + 10000], index=True).to_numpy().tobytes())
    return digest.hexdigest()


def code_fingerprint(source_root):
    sources = {str(path.relative_to(source_root)): hashlib.sha256(path.read_bytes()).hexdigest()
               for path in sorted((source_root / "bot").rglob("*.py"))}
    sources["config.py"] = hashlib.sha256((source_root / "config.py").read_bytes()).hexdigest()
    return sources


def dependency_versions():
    return {distribution.metadata["Name"]: distribution.version for distribution in distributions()
            if distribution.metadata["Name"]}


def manifest(data, settings, start, end, source_root):
    datasets = {}
    for symbol, frame in data.items():
        datasets[symbol] = dict(bars=frame_hash(frame), actions=json_value(frame.attrs.get("corporate_actions", [])))
        if "execution" in frame.attrs:
            datasets[symbol]["execution"] = frame_hash(frame.attrs["execution"])
    return dict(start=str(start), end=str(end), settings=json_value(settings), data=datasets,
                sources=code_fingerprint(source_root), python=sys.version, dependencies=dependency_versions())


def prior_exposures(directory, symbols, start, end):
    """Read-only: previously claimed/exposed intervals overlapping [start, end) for any symbol."""
    database = Path(directory) / "registry.sqlite3"
    if not database.exists():
        return []
    start = pd.Timestamp(start).tz_convert("UTC").isoformat()
    end = pd.Timestamp(end).tz_convert("UTC").isoformat()
    with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True, timeout=5)) as connection:
        rows = connection.execute(
            f"SELECT run, strategy, symbol, start, end FROM holdouts WHERE symbol IN ({','.join('?' * len(symbols))}) "
            "AND start<? AND end>?", [*symbols, end, start]).fetchall()
    return [dict(run=run, strategy=strategy, symbol=symbol, start=first, end=last)
            for run, strategy, symbol, first, last in rows]


def trial_history(directory, dataset_id, exclude_run=None):
    """Read-only: simulated trials already recorded in the registry for one dataset."""
    database = Path(directory) / "registry.sqlite3"
    if not database.exists():
        return dict(runs=0, trials=0)
    runs = trials = 0
    with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True, timeout=5)) as connection:
        for run, payload in connection.execute("SELECT run, payload FROM records WHERE kind='started'"):
            experiment = json.loads(payload).get("experiment")
            if run != exclude_run and isinstance(experiment, dict) and experiment.get("dataset_id") == dataset_id:
                runs += 1
                trials += int(experiment.get("trials", 0))
    return dict(runs=runs, trials=trials)


class ResearchRun:
    def __init__(self, directory, metadata):
        directory.mkdir(parents=True, exist_ok=True)
        self.database = directory / "registry.sqlite3"
        self.identity = uuid.uuid4().hex
        self.directory = directory / self.identity
        self.directory.mkdir()
        with closing(sqlite3.connect(self.database, timeout=5)) as connection, connection:
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("CREATE TABLE IF NOT EXISTS records (id INTEGER PRIMARY KEY, run TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS holdouts (run TEXT NOT NULL, strategy TEXT NOT NULL, symbol TEXT NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL)")
            for table in ("records", "holdouts"):
                for operation in ("UPDATE", "DELETE"):
                    connection.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{operation.lower()} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT, 'Research history is append-only'); END")
        self.record("started", dict(metadata, observed_at=datetime.now(timezone.utc).isoformat()))
        write_json(self.directory / "manifest.json", metadata)

    def record(self, kind, payload):
        with closing(sqlite3.connect(self.database, timeout=5)) as connection, connection:
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("INSERT INTO records(run, kind, payload) VALUES (?, ?, ?)",
                               (self.identity, kind, json.dumps(json_value(payload), allow_nan=False)))

    def claim_holdout(self, strategy, symbols, start, end):
        start = pd.Timestamp(start).tz_convert("UTC").isoformat()
        end = pd.Timestamp(end).tz_convert("UTC").isoformat()
        with closing(sqlite3.connect(self.database, timeout=5)) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            for symbol in symbols:
                if connection.execute("SELECT 1 FROM holdouts WHERE symbol=? AND run<>? AND start<? AND end>?",
                                      (symbol, self.identity, end, start)).fetchone():
                    raise ValueError(f"{symbol}: final holdout overlaps previously exposed research history")
            for symbol in symbols:
                if not connection.execute("SELECT 1 FROM holdouts WHERE run=? AND symbol=? AND start=? AND end=?",
                                          (self.identity, symbol, start, end)).fetchone():
                    connection.execute("INSERT INTO holdouts VALUES (?, ?, ?, ?, ?)",
                                       (self.identity, strategy, symbol, start, end))

    def expose_sample(self, symbols, start, end, forbid_held_out=False):
        start, end = pd.Timestamp(start).tz_convert("UTC").isoformat(), pd.Timestamp(end).tz_convert("UTC").isoformat()
        with closing(sqlite3.connect(self.database, timeout=5)) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            if forbid_held_out:
                for symbol in symbols:
                    if connection.execute("SELECT 1 FROM holdouts WHERE symbol=? AND run<>? AND strategy<>'reported_sample' "
                                          "AND start<? AND end>?", (symbol, self.identity, end, start)).fetchone():
                        raise ValueError(f"{symbol}: selection window overlaps a final holdout reserved by another run")
            connection.executemany("INSERT INTO holdouts VALUES (?, ?, ?, ?, ?)",
                                   [(self.identity, "reported_sample", symbol, start, end) for symbol in symbols])
        self.record("report_exposure", dict(start=start, end=end, symbols=symbols))