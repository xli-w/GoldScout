"""Market data access: fetches OHLCV bars and returns only COMPLETED candles."""
import logging
from datetime import datetime, timedelta, timezone

import pandas as pd
import numpy as np
from alpaca_trade_api.rest import TimeFrame, TimeFrameUnit

import config
from bot.utils import retry
from bot.sessions import aggregate_equities, validate_execution
from bot.corporate_actions import normalize_actions, ordered_actions

log = logging.getLogger(__name__)

OHLCV = ["open", "high", "low", "close", "volume"]


def make_timeframe(amount: int, unit: str):
    unit_enum = TimeFrameUnit.Minute if unit == "Min" else TimeFrameUnit.Hour
    return TimeFrame(amount, unit_enum)


def bar_minutes(amount: int, unit: str) -> int:
    return amount * (1 if unit == "Min" else 60)


class MarketData:
    def __init__(self, api):
        self.api = api
        self._raw_cache = {}
        self._action_cache = {}
        self._feature_cache = {}

    def equity_features(self, symbol, timeframe, lookback_days):
        now = pd.Timestamp.now(tz="UTC")
        boundary = now - pd.Timedelta(days=lookback_days)
        duration = bar_minutes(*timeframe)
        key = symbol, timeframe, lookback_days
        cached = self._feature_cache.get(key)
        refresh = cached is None or now - cached[0] >= pd.Timedelta(days=1)
        cursor = boundary if refresh or cached[1].empty else cached[1].index[-1]
        frames = [] if refresh else [cached[1]]
        while cursor < now:
            ending = min(cursor.normalize() + pd.Timedelta(days=7), now)
            raw = self._fetch(symbol, "equity", make_timeframe(1, "Min"), cursor.isoformat(), ending.isoformat())
            if raw is not None and not raw.empty:
                if isinstance(raw.index, pd.MultiIndex):
                    raw = raw.droplevel(0)
                raw = raw[OHLCV].copy().astype(float)
                raw.index = pd.to_datetime(raw.index, utc=True)
                raw = raw.sort_index()
                raw = raw.loc[~raw.index.duplicated(keep="last")]
                raw = raw.loc[raw.index + pd.Timedelta(minutes=1) <= ending]
                if not np.isfinite(raw.to_numpy()).all() or (raw[OHLCV[:4]] <= 0).any().any() \
                        or (raw.volume < 0).any() or (raw.high < raw[["open", "close", "low"]].max(axis=1)).any() \
                        or (raw.low > raw[["open", "close", "high"]].min(axis=1)).any():
                    raise ValueError("Invalid feature warm-up OHLCV")
                candles = aggregate_equities(raw, duration)
                candles = candles.loc[candles.bar_end <= ending]
                if not candles.empty:
                    validate_execution(raw, True, candles.index[0], candles.bar_end.iloc[-1])
                frames.append(candles)
            cursor = ending
        result = pd.concat(frames).sort_index() if frames else pd.DataFrame(
            columns=OHLCV + ["bar_end"], index=pd.DatetimeIndex([], tz="UTC"))
        result = result.loc[~result.index.duplicated(keep="last")]
        result = result.loc[result.index >= boundary]
        from bot.sessions import schedule
        expected = schedule(boundary.tz_convert(config.MARKET_TZ).date().isoformat(),
                            now.tz_convert(config.MARKET_TZ).date().isoformat())
        required = []
        for _, session in expected.iterrows():
            for opening in pd.date_range(session["open"], session["close"], freq=f"{duration}min", inclusive="left"):
                closing = min(opening + pd.Timedelta(minutes=duration), session["close"])
                if opening >= boundary and closing <= now:
                    required.append(opening)
        if required and not pd.DatetimeIndex(required).isin(result.index).all():
            raise ValueError("Live feature history is missing a requested completed candle boundary")
        result.attrs = {}
        self._feature_cache[key] = (now if refresh else cached[0], result.copy())
        result.attrs["corporate_actions"] = self.get_corporate_actions(symbol, boundary.date().isoformat(), now.date().isoformat())
        return result

    @retry(budget=120)
    def get_corporate_actions(self, symbol, start, end):
        key = (symbol, start, end)
        now = datetime.now(timezone.utc)
        cached = self._action_cache.get(key)
        if cached is not None and now - cached[0] < timedelta(minutes=5):
            return [dict(action) for action in cached[1]]
        actions = []
        token = None
        seen = set()
        while True:
            payload = self.api.data_get("/corporate-actions", data=dict(
                symbols=symbol, start=start, end=end, limit=1000, page_token=token), api_version="v1")
            actions.extend(normalize_actions(payload["corporate_actions"]))
            token = payload.get("next_page_token")
            if not token:
                break
            if token in seen:
                raise ValueError("Corporate action pagination repeated a token")
            seen.add(token)
        result = ordered_actions(actions)
        self._action_cache[key] = (now, result)
        return [dict(action) for action in result]

    @retry(budget=120)
    def _fetch(self, symbol: str, asset_class: str, tf, start: str, end: str) -> pd.DataFrame:
        if asset_class == "crypto":
            bars = self.api.get_crypto_bars(symbol, tf, start=start, end=end, limit=None)
        else:
            bars = self.api.get_bars(
                symbol, tf, start=start, end=end, adjustment="raw",
                limit=None, feed=config.STOCK_DATA_FEED,
            )
        return bars.df

    def get_completed_bars(self, symbol: str, asset_class: str, timeframe: tuple,
                           lookback_days: int) -> pd.DataFrame:
        """Return OHLCV for fully closed candles only (in-progress bar is dropped)."""
        amount, unit = timeframe
        now = datetime.now(timezone.utc)
        duration = bar_minutes(amount, unit)
        boundary = pd.Timestamp(now - timedelta(days=lookback_days))
        if asset_class == "crypto":
            boundary = boundary.floor(f"{duration}min")
        start = boundary.isoformat()
        end = now.isoformat()
        key = (symbol, asset_class, timeframe, lookback_days)
        cached = self._raw_cache.get(key)
        reuse = cached is not None and now - cached[0] < timedelta(days=1) and not cached[1].empty
        if reuse:
            overlap = pd.Timedelta(minutes=duration if asset_class == "crypto" else 2)
            start = (cached[1].index[-1] - overlap).isoformat()
        fetch_tf = make_timeframe(1, "Min") if asset_class == "equity" else make_timeframe(amount, unit)
        df = self._fetch(symbol, asset_class, fetch_tf, start, end)

        if df is None or df.empty:
            if reuse:
                df = cached[1].copy()
            else:
                empty = pd.DataFrame(columns=OHLCV, index=pd.DatetimeIndex([], tz="UTC"), dtype=float)
                if asset_class == "equity":
                    empty["bar_end"] = pd.Series(index=empty.index, dtype="datetime64[ns, UTC]")
                    empty.attrs["execution"] = empty[OHLCV].copy()
                    empty.attrs["corporate_actions"] = []
                return empty
        if isinstance(df.index, pd.MultiIndex):          # (symbol, timestamp)
            df = df.droplevel(0)
        df.index = pd.to_datetime(df.index, utc=True)
        df = df[OHLCV].astype(float).sort_index()
        if asset_class == "crypto":
            if not df.index.equals(df.index.floor(f"{duration}min")):
                raise ValueError(f"{symbol}: crypto bars are not timeframe-aligned")
            df = df[df.index >= pd.Timestamp(start)]
        if not np.isfinite(df.to_numpy()).all() or (df[["open", "high", "low", "close"]] <= 0).any().any():
            raise ValueError(f"{symbol}: invalid OHLCV values")
        if (df["high"] < df[["open", "close", "low"]].max(axis=1)).any() or \
                (df["low"] > df[["open", "close", "high"]].min(axis=1)).any() or (df["volume"] < 0).any():
            raise ValueError(f"{symbol}: inconsistent OHLCV envelope")
        if reuse:
            df = pd.concat([cached[1], df]).sort_index()
        df = df[~df.index.duplicated(keep="last")].dropna()
        df = df[df.index >= boundary]
        self._raw_cache[key] = (cached[0] if reuse else now, df.copy())

        if asset_class == "equity":
            df = df[df.index + pd.Timedelta(minutes=1) <= now]
            minutes = df.copy()
            df = aggregate_equities(df, bar_minutes(amount, unit))
            df = df[df["bar_end"] <= now]
            if not df.empty:
                validate_execution(minutes, True, df.index[0], df["bar_end"].iloc[-1])
            df.attrs["execution"] = minutes
            df.attrs["corporate_actions"] = self.get_corporate_actions(symbol,
                (now - timedelta(days=lookback_days)).date().isoformat(), now.date().isoformat())
            return df

        bar_end = df.index + timedelta(minutes=bar_minutes(amount, unit))
        return df[bar_end <= now]
