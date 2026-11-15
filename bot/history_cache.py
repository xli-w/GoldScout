import hashlib
import json

import pandas as pd
import numpy as np

import config
from bot.data import MarketData, make_timeframe, OHLCV
from bot.research import write_json
from bot.sessions import regular_hours


class MinuteHistory:
    def __init__(self, api, directory):
        self.market = MarketData(api)
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)

    def get(self, symbol, asset_class, start, end, use_cache=True):
        start, end = pd.Timestamp(start), pd.Timestamp(end)
        block = start.normalize() - pd.Timedelta(days=start.dayofweek)
        frames = []
        while block < end:
            block_end = block + pd.Timedelta(days=7)
            requested_end = min(block_end, end)
            name = f"{symbol.replace('/', '')}_{asset_class}_{config.STOCK_DATA_FEED}_{block.date()}"
            path = self.directory / f"{name}.csv"
            metadata_path = path.with_suffix(".json")
            old, metadata = None, {}
            if use_cache and path.exists() and metadata_path.exists():
                metadata = json.loads(metadata_path.read_text())
                if metadata.get("version") != 1 or metadata.get("start") != block.isoformat() \
                        or metadata.get("symbol") != symbol or metadata.get("asset_class") != asset_class:
                    raise ValueError("Minute cache provenance mismatch")
                if hashlib.sha256(path.read_bytes()).hexdigest() != metadata.get("sha256"):
                    raise ValueError("Minute cache checksum mismatch")
                old = pd.read_csv(path, index_col=0)
                old.index = pd.to_datetime(old.index, utc=True)
                self.validate(old)
            covered = old is not None and pd.Timestamp(metadata["end"]) >= requested_end
            refreshed = pd.Timestamp(metadata["refreshed_at"]) if metadata.get("refreshed_at") else None
            recent = block_end >= end - pd.Timedelta(days=7)
            correction_refresh = old is not None and recent and (
                refreshed is None or pd.Timestamp.now(tz="UTC") - refreshed > pd.Timedelta(minutes=5))
            if correction_refresh:
                covered = False
            if not covered:
                fetch_start = block
                fetch_end = max(requested_end, pd.Timestamp(metadata["end"])) if old is not None else requested_end
                if old is not None and not old.empty and not correction_refresh and pd.Timestamp(metadata["end"]) < requested_end:
                    fetch_start = max(block, old.index[-1] - pd.Timedelta(minutes=2))
                fresh = self.market._fetch(symbol, asset_class, make_timeframe(1, "Min"),
                                           fetch_start.isoformat(), fetch_end.isoformat())
                if fresh is None:
                    fresh = pd.DataFrame(columns=OHLCV, index=pd.DatetimeIndex([], tz="UTC"))
                if isinstance(fresh.index, pd.MultiIndex):
                    fresh = fresh.droplevel(0)
                fresh = fresh.copy()
                fresh.index = pd.to_datetime(fresh.index, utc=True)
                fresh = fresh.reindex(columns=OHLCV).astype(float)
                combined = pd.concat([old, fresh]) if old is not None else fresh
                combined = combined.sort_index()
                combined = combined.loc[~combined.index.duplicated(keep="last")]
                combined = combined.loc[(combined.index >= block) &
                                         (combined.index + pd.Timedelta(minutes=1) <= fetch_end)]
                if asset_class == "equity":
                    combined = regular_hours(combined)
                self.validate(combined)
                temporary = path.with_suffix(".tmp")
                combined.to_csv(temporary)
                temporary.replace(path)
                metadata = dict(version=1, symbol=symbol, asset_class=asset_class, start=block.isoformat(),
                                end=fetch_end.isoformat(), feed=config.STOCK_DATA_FEED,
                                refreshed_at=pd.Timestamp.now(tz="UTC").isoformat(),
                                sha256=hashlib.sha256(path.read_bytes()).hexdigest())
                write_json(metadata_path, metadata)
                old = combined
            frames.append(old)
            block = block_end
        if not frames:
            return pd.DataFrame(columns=OHLCV, index=pd.DatetimeIndex([], tz="UTC"))
        frame = pd.concat(frames).sort_index()
        return frame.loc[(frame.index >= start) & (frame.index + pd.Timedelta(minutes=1) <= end)]

    @staticmethod
    def validate(frame):
        if frame.index.tz is None or frame.index.has_duplicates or not frame.index.is_monotonic_increasing \
                or not frame.index.equals(frame.index.floor("min")):
            raise ValueError("Invalid cached minute timestamps")
        if not np.isfinite(frame[OHLCV].to_numpy(float)).all() or (frame[OHLCV[:4]] <= 0).any().any() \
                or (frame.volume < 0).any() or (frame.high < frame[["open", "close", "low"]].max(axis=1)).any() \
                or (frame.low > frame[["open", "close", "high"]].min(axis=1)).any():
            raise ValueError("Invalid minute cache OHLCV observations")