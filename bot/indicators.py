"""Pure-pandas technical indicators."""
import pandas as pd


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period).mean()


def rolling_std(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period).std()


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder's Average True Range."""
    high = pd.Series(df["high"].to_numpy(), index=df.index)
    low = pd.Series(df["low"].to_numpy(), index=df.index)
    prev_close = pd.Series(df["close"].to_numpy(), index=df.index).shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
