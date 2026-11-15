from functools import lru_cache

import exchange_calendars as calendars
import pandas as pd


@lru_cache(maxsize=16)
def _calendar(first_year, last_year):
    return calendars.get_calendar("XNYS", start=f"{first_year}-01-01", end=f"{last_year}-12-31")


@lru_cache(maxsize=32)
def schedule(start: str, end: str) -> pd.DataFrame:
    calendar = _calendar(pd.Timestamp(start).year - 1, pd.Timestamp(end).year + 1)
    return calendar.schedule.loc[start:end]


def session_bounds(index: pd.DatetimeIndex):
    if index.empty:
        return pd.DatetimeIndex([], tz="UTC"), pd.DatetimeIndex([], tz="UTC")
    dates = index.tz_convert("America/New_York")
    table = schedule((dates.min() - pd.Timedelta(days=7)).date().isoformat(),
                     (dates.max() + pd.Timedelta(days=7)).date().isoformat())
    opens = pd.DatetimeIndex(table["open"])
    closes = pd.DatetimeIndex(table["close"])
    offsets = opens.searchsorted(index, side="right") - 1
    safe = offsets.clip(0)
    valid = (offsets >= 0) & (index >= opens[safe]) & (index < closes[safe])
    return opens[safe].where(valid), closes[safe].where(valid)


def regular_hours(frame: pd.DataFrame) -> pd.DataFrame:
    opens, _ = session_bounds(frame.index)
    return frame.loc[~opens.isna()].copy()


def validate_execution(frame: pd.DataFrame, equity: bool, start=None, end=None) -> None:
    if start is not None:
        frame = frame.loc[frame.index >= start]
    if end is not None:
        frame = frame.loc[frame.index + pd.Timedelta(minutes=1) <= end]
    if frame.empty or frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
        raise ValueError("Execution history must be nonempty, unique and chronological")
    if frame.index.tz is None or not frame.index.equals(frame.index.floor("min")):
        raise ValueError("Execution timestamps must be timezone-aware minute boundaries")
    if not equity:
        if start is not None and frame.index[0] > pd.Timestamp(start).ceil("min"):
            raise ValueError("Crypto execution history is missing its opening boundary")
        if end is not None and frame.index[-1] + pd.Timedelta(minutes=1) < pd.Timestamp(end).floor("min"):
            raise ValueError("Crypto execution history is missing its closing boundary")
        if (frame.index.to_series().diff().dropna() > pd.Timedelta(minutes=2)).any():
            raise ValueError("Crypto execution history has an unexplained gap over two minutes")
        return
    filtered = regular_hours(frame)
    if filtered.empty:
        raise ValueError("No regular-session execution data")
    opens, closes = session_bounds(filtered.index)
    dates = filtered.index.tz_convert("America/New_York").normalize()
    table = schedule(dates.min().date().isoformat(), dates.max().date().isoformat())
    observed = set(dates.date)
    missing = [session.date() for session in table.index if session.date() not in observed]
    if missing:
        raise ValueError(f"Execution history is missing exchange sessions: {missing[:5]}")
    if start is not None and end is not None:
        table = schedule(pd.Timestamp(start).tz_convert("America/New_York").date().isoformat(),
                         pd.Timestamp(end).tz_convert("America/New_York").date().isoformat())
    for _, session in table.iterrows():
        expected_open, expected_close = session["open"], session["close"]
        if start is not None:
            expected_open = max(expected_open, pd.Timestamp(start).ceil("min"))
        if end is not None:
            expected_close = min(expected_close, pd.Timestamp(end).floor("min"))
        if expected_open >= expected_close:
            continue
        observed_minutes = filtered.loc[(filtered.index >= expected_open) & (filtered.index < expected_close)]
        if observed_minutes.empty or observed_minutes.index[0] != expected_open or \
                observed_minutes.index[-1] + pd.Timedelta(minutes=1) != expected_close:
            raise ValueError("Execution history is missing an exchange session boundary")
    differences = filtered.index.to_series().diff()
    same_session = pd.Series(opens, index=filtered.index).eq(pd.Series(opens, index=filtered.index).shift())
    if (differences[same_session] > pd.Timedelta(minutes=5)).any():
        raise ValueError("Regular-session execution history has a gap over five minutes")


def aggregate_equities(minutes: pd.DataFrame, duration: int) -> pd.DataFrame:
    if not isinstance(duration, int) or duration <= 0:
        raise ValueError("Candle duration must be a positive integer")
    frame = regular_hours(minutes)
    if frame.index.has_duplicates or not frame.index.is_monotonic_increasing or \
            not frame.index.equals(frame.index.floor("min")):
        raise ValueError("Candle inputs must be unique chronological minute boundaries")
    if frame.empty:
        frame = frame.copy()
        frame["bar_end"] = pd.Series(index=frame.index, dtype="datetime64[ns, UTC]")
        return frame
    opens, closes = session_bounds(frame.index)
    labels = opens + ((frame.index - opens) // pd.Timedelta(minutes=duration)) * pd.Timedelta(minutes=duration)
    grouped = frame.groupby(labels)
    bars = grouped.agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    ends = pd.Series(closes, index=labels).groupby(level=0).first()
    nominal = bars.index + pd.Timedelta(minutes=duration)
    bars["bar_end"] = pd.DatetimeIndex(ends).where(pd.DatetimeIndex(ends) < nominal, nominal)
    timestamps = pd.Series(frame.index, index=labels)
    first = timestamps.groupby(level=0).first()
    last = timestamps.groupby(level=0).last() + pd.Timedelta(minutes=1)
    gaps = timestamps.groupby(level=0).diff().groupby(level=0).max()
    complete = first.eq(pd.Series(bars.index, index=bars.index)) & last.eq(bars["bar_end"])
    complete &= gaps.fillna(pd.Timedelta(0)) <= pd.Timedelta(minutes=5)
    return bars.loc[complete]


def aggregate_crypto(minutes: pd.DataFrame, duration: int) -> pd.DataFrame:
    frame = minutes.copy()
    frame.attrs = {}
    if frame.index.has_duplicates or not frame.index.is_monotonic_increasing or frame.index.tz is None:
        raise ValueError("Crypto aggregation requires unique chronological UTC minutes")
    labels = frame.index.floor(f"{duration}min")
    bars = frame.groupby(labels).agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    bars["bar_end"] = bars.index + pd.Timedelta(minutes=duration)
    timestamps = pd.Series(frame.index, index=labels)
    first = timestamps.groupby(level=0).first()
    last = timestamps.groupby(level=0).last() + pd.Timedelta(minutes=1)
    gaps = timestamps.groupby(level=0).diff().groupby(level=0).max()
    complete = first.eq(pd.Series(bars.index, index=bars.index)) & last.eq(bars["bar_end"])
    complete &= gaps.fillna(pd.Timedelta(0)) <= pd.Timedelta(minutes=2)
    return bars.loc[complete]