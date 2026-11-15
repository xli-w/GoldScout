"""Pure freshness rules shared by dashboard fixtures and the future API."""

import math
import re
from datetime import datetime, timezone


def freshness(observed_at, now, max_age_seconds):
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if (isinstance(max_age_seconds, bool) or not isinstance(max_age_seconds, (int, float))
            or not math.isfinite(max_age_seconds) or max_age_seconds <= 0):
        raise ValueError("max_age_seconds must be finite and positive")
    if observed_at is None:
        return "missing"
    if not isinstance(observed_at, str) or not re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?Z", observed_at):
        return "invalid"
    try:
        observed = datetime.fromisoformat(observed_at[:-1] + "+00:00")
    except ValueError:
        return "invalid"
    age = (now.astimezone(timezone.utc) - observed).total_seconds()
    if age < 0:
        return "invalid"
    return "stale" if age > max_age_seconds else "fresh"