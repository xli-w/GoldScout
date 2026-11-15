import math

import numpy as np
import pandas as pd


def daily_returns(equity, crypto, risk_free=0.0):
    daily = equity.resample("D").last()
    observed = pd.Series(equity.index, index=equity.index).resample("D").last()
    if crypto:
        daily = daily.ffill()
        observed = observed.ffill()
    if not crypto:
        daily = daily.loc[(daily.index.day_of_week < 5) & daily.notna()]
        observed = observed.reindex(daily.index)
    returns = daily.pct_change().dropna()
    elapsed_days = observed.diff().dt.total_seconds() / 86400
    return returns - ((1 + risk_free) ** (elapsed_days.reindex(returns.index) / 365) - 1)


def bootstrap_draws(trials=1, samples=1000):
    if isinstance(trials, bool) or not isinstance(trials, int) or trials < 1:
        raise ValueError("Trial count must be a positive integer")
    if isinstance(samples, bool) or not isinstance(samples, int) or samples < 1:
        raise ValueError("Bootstrap sample count must be a positive integer")
    draws = max(samples, math.ceil(20 / (0.05 / trials / 2)))
    if draws > 100000:
        raise ValueError("Requested bootstrap tail resolution exceeds supported draw budget")
    return draws


def sharpe_interval(returns, annual_days, trials=1, samples=1000):
    samples = bootstrap_draws(trials, samples)
    values = np.asarray(returns, dtype=float)
    if len(values) < 30 or not np.isfinite(values).all() or np.std(values) <= 1e-10:
        return [None, None]
    rng = np.random.default_rng(17)
    block = max(5, int(len(values) ** (1 / 3)))
    estimates = []
    for offset in range(0, samples, 128):
        count = min(128, samples - offset)
        starts = rng.integers(0, len(values), size=(count, math.ceil(len(values) / block), 1))
        indices = ((starts + np.arange(block)) % len(values)).reshape(count, -1)[:, :len(values)]
        sample = values[indices]
        deviations = np.std(sample, axis=1, ddof=1)
        valid = deviations > 0
        estimates.extend((np.mean(sample[valid], axis=1) / deviations[valid] * math.sqrt(annual_days)).tolist())
    alpha = 0.05 / max(1, trials)
    if len(estimates) * alpha / 2 < 20:
        return [None, None]
    return [float(value) for value in np.quantile(estimates, [alpha / 2, 1 - alpha / 2])] if estimates else [None, None]


def downside_metrics(equity, returns, initial):
    days = max((equity.index[-1] - equity.index[0]).total_seconds() / 86400, 1)
    growth = float(equity.iloc[-1] / initial)
    cagr = growth ** (365 / days) - 1 if growth > 0 else -1.0
    drawdown = equity / equity.cummax() - 1
    max_dd = float(drawdown.min())
    longest, since = 0.0, None
    for timestamp, underwater in (drawdown < 0).items():
        if underwater and since is None:
            since = timestamp
        if since is not None:
            longest = max(longest, (timestamp - since).total_seconds() / 86400)
        if not underwater:
            since = None
    downside = np.sqrt(np.mean(np.minimum(np.asarray(returns), 0) ** 2)) if len(returns) else 0
    tail = returns[returns <= returns.quantile(.05)] if len(returns) else returns
    return dict(cagr=100 * cagr, calmar=cagr / abs(max_dd) if max_dd < 0 else None,
                expected_shortfall_95=100 * float(tail.mean()) if len(tail) else None,
                drawdown_days=longest, downside_deviation=float(downside))