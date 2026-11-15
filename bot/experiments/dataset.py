"""Versioned, immutable minute-data snapshots for offline experiments.

`prepare_dataset` is the only step that may contact the broker's data service (through the caller-supplied
history object). Everything else reads verified local files and reports missing coverage explicitly.
"""
import gzip
import hashlib
import io
import json
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

import pandas as pd

import config
from bot.data import OHLCV
from bot.research import write_json

DATASET_VERSION = 1
SAFE_ID = re.compile(r"^[0-9a-f]{16}$")


class DatasetError(ValueError):
    pass


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _dataset_id(core: dict) -> str:
    return hashlib.sha256(_canonical(core).encode()).hexdigest()[:16]


def _file_name(symbol: str) -> str:
    return symbol.replace("/", "") + ".csv.gz"


def _validate_minutes(frame: pd.DataFrame, symbol: str) -> None:
    from bot.history_cache import MinuteHistory
    try:
        MinuteHistory.validate(frame)
    except ValueError as exc:
        raise DatasetError(f"{symbol}: {exc}") from exc


def _merged(intervals) -> List[tuple]:
    return [(pd.Timestamp(start), pd.Timestamp(end)) for start, end in intervals]


def prepare_dataset(root, requirements: Dict[str, list], history, now=None) -> str:
    """Download/validate minutes and corporate actions for each symbol interval; write an immutable snapshot.

    requirements: symbol -> merged [(start, end)] intervals. history: MinuteHistory-like (get, market).
    Returns the dataset id. An existing snapshot with identical content is reused.
    """
    root = Path(root)
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    symbols = {}
    payloads = {}
    for symbol in sorted(requirements):
        instrument = config.INSTRUMENTS[symbol]
        intervals = _merged(requirements[symbol])
        frames = []
        for start, end in intervals:
            if end > now.floor("min") - pd.Timedelta(minutes=5):
                raise DatasetError(f"{symbol}: window end {end} is not safely in the past; "
                                   "experiments use completed history only")
            minutes = history.get(symbol, instrument.asset_class, start, end, True)
            if minutes is None or minutes.empty:
                raise DatasetError(f"{symbol}: no minute history returned for {start} -> {end}")
            frames.append(minutes[OHLCV])
        frame = pd.concat(frames).sort_index()
        frame = frame.loc[~frame.index.duplicated(keep="last")]
        _validate_minutes(frame, symbol)
        actions = []
        if instrument.asset_class == "equity":
            actions = history.market.get_corporate_actions(symbol, intervals[0][0].date().isoformat(),
                                                           intervals[-1][1].date().isoformat())
        text = frame.to_csv().encode()
        compressed = gzip.compress(text, mtime=0)
        payloads[symbol] = compressed
        symbols[symbol] = dict(
            asset_class=instrument.asset_class, file=_file_name(symbol), rows=len(frame),
            frame_sha256=hashlib.sha256(text).hexdigest(), file_sha256=hashlib.sha256(compressed).hexdigest(),
            coverage=[[start.isoformat(), end.isoformat()] for start, end in intervals],
            first_minute=frame.index[0].isoformat(), last_minute=frame.index[-1].isoformat(),
            corporate_actions=actions)
    core = dict(version=DATASET_VERSION, feed=config.STOCK_DATA_FEED,
                symbols={symbol: {key: value for key, value in info.items() if key not in ("file", "file_sha256")}
                         for symbol, info in symbols.items()})
    identifier = _dataset_id(core)
    destination = root / "datasets" / identifier
    if destination.exists():
        return identifier
    staging = root / "datasets" / f".staging-{uuid.uuid4().hex}"
    staging.mkdir(parents=True)
    try:
        for symbol, data in payloads.items():
            (staging / symbols[symbol]["file"]).write_bytes(data)
        write_json(staging / "manifest.json", dict(core, dataset_id=identifier,
                   created_at=datetime.now(timezone.utc).isoformat(),
                   symbols=symbols))
        staging.replace(destination)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return identifier


def list_datasets(root) -> List[dict]:
    directory = Path(root) / "datasets"
    found = []
    if directory.is_dir():
        for child in sorted(directory.iterdir()):
            manifest = child / "manifest.json"
            if SAFE_ID.match(child.name) and manifest.is_file():
                data = json.loads(manifest.read_text())
                found.append(dict(dataset_id=data["dataset_id"], created_at=data["created_at"], feed=data["feed"],
                                  symbols=sorted(data["symbols"])))
    return sorted(found, key=lambda item: item["created_at"])


def resolve_dataset(root, identifier: str) -> str:
    if identifier == "latest":
        available = list_datasets(root)
        if not available:
            raise DatasetError("No prepared datasets found; run `prepare` first")
        return available[-1]["dataset_id"]
    if not SAFE_ID.match(identifier):
        raise DatasetError(f"Invalid dataset id {identifier!r}")
    return identifier


class Dataset:
    def __init__(self, directory: Path, manifest: dict):
        self.directory = directory
        self.manifest = manifest
        self.dataset_id = manifest["dataset_id"]
        self._minutes: Dict[str, pd.DataFrame] = {}

    @classmethod
    def open(cls, root, identifier: str) -> "Dataset":
        identifier = resolve_dataset(root, identifier)
        directory = Path(root) / "datasets" / identifier
        path = directory / "manifest.json"
        if not path.is_file():
            raise DatasetError(f"Dataset {identifier} not found under {Path(root) / 'datasets'}")
        manifest = json.loads(path.read_text())
        if manifest.get("version") != DATASET_VERSION:
            raise DatasetError("Unsupported dataset version")
        core = dict(version=manifest["version"], feed=manifest["feed"],
                    symbols={symbol: {key: value for key, value in info.items() if key not in ("file", "file_sha256")}
                             for symbol, info in manifest["symbols"].items()})
        if _dataset_id(core) != identifier or manifest["dataset_id"] != identifier:
            raise DatasetError("Dataset manifest does not match its identifier (tampered or corrupt)")
        return cls(directory, manifest)

    @property
    def symbols(self):
        return sorted(self.manifest["symbols"])

    def _info(self, symbol):
        try:
            return self.manifest["symbols"][symbol]
        except KeyError:
            raise DatasetError(f"{symbol}: not in dataset {self.dataset_id}") from None

    def coverage(self, symbol):
        return [(pd.Timestamp(start), pd.Timestamp(end)) for start, end in self._info(symbol)["coverage"]]

    def covers(self, symbol, start, end) -> bool:
        return any(first <= start and end <= last for first, last in self.coverage(symbol))

    def actions(self, symbol):
        return self._info(symbol)["corporate_actions"]

    def minutes(self, symbol) -> pd.DataFrame:
        if symbol not in self._minutes:
            info = self._info(symbol)
            path = self.directory / info["file"]
            if not path.is_file():
                raise DatasetError(f"{symbol}: dataset file {info['file']} is missing")
            blob = path.read_bytes()
            if hashlib.sha256(blob).hexdigest() != info["file_sha256"]:
                raise DatasetError(f"{symbol}: dataset file checksum mismatch")
            text = gzip.decompress(blob)
            if hashlib.sha256(text).hexdigest() != info["frame_sha256"]:
                raise DatasetError(f"{symbol}: dataset content checksum mismatch")
            frame = pd.read_csv(io.BytesIO(text), index_col=0)
            frame.index = pd.to_datetime(frame.index, utc=True)
            _validate_minutes(frame, symbol)
            self._minutes[symbol] = frame
        # Return a copy so callers cannot mutate the verified cache.
        return self._minutes[symbol].copy(deep=True)
