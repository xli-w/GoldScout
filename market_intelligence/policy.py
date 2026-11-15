"""Pure hypothetical calendar policy. No result authorizes or blocks a trade."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Tuple

from market_intelligence.contracts import (
    CATEGORIES, SYMBOLS, CalendarSnapshot, IntelligenceSettings, identifier,
    nonnegative_integer, utc_time,
)


@dataclass(frozen=True)
class CalendarPolicy:
    categories: Tuple[str, ...] = CATEGORIES
    before_minutes: int = 15
    after_minutes: int = 30
    max_source_age_seconds: int = 7200
    version: str = "calendar-shadow-v1"

    def __post_init__(self):
        identifier(self.version)
        if type(self.categories) is not tuple or not self.categories or len(set(self.categories)) != len(self.categories):
            raise ValueError("Policy categories must be an immutable unique tuple")
        if any(category not in CATEGORIES for category in self.categories):
            raise ValueError("Unsupported policy category")
        for value in (self.before_minutes, self.after_minutes, self.max_source_age_seconds):
            nonnegative_integer(value)
        if self.before_minutes + self.after_minutes == 0 or max(self.before_minutes, self.after_minutes) > 1440:
            raise ValueError("Event window must be nonempty and bounded to one day per side")
        if not 1 <= self.max_source_age_seconds <= 86400:
            raise ValueError("Source age limit must lie between one second and one day")


@dataclass(frozen=True)
class EventReference:
    source: str
    event_id: str

    def __post_init__(self):
        identifier(self.source)
        identifier(self.event_id)


@dataclass(frozen=True)
class ShadowDecision:
    outcome: str
    reason: str
    observed_at: datetime
    settings_revision: int
    snapshot_revision: Optional[int]
    policy_version: str
    event_refs: Tuple[EventReference, ...] = ()

    @property
    def affects_trading(self) -> bool:
        return False


def evaluate_calendar(settings: IntelligenceSettings, snapshot: Optional[CalendarSnapshot],
                      symbol: str, action: str, now: datetime,
                      policy: Optional[CalendarPolicy] = None) -> ShadowDecision:
    utc_time(now)
    policy = policy or CalendarPolicy()
    if not isinstance(settings, IntelligenceSettings) or not isinstance(policy, CalendarPolicy):
        raise ValueError("Validated settings and policy are required")
    if snapshot is not None and not isinstance(snapshot, CalendarSnapshot):
        raise ValueError("Validated snapshot is required")
    if symbol not in SYMBOLS or action not in ("enter_long", "enter_short", "exit", "hold"):
        raise ValueError("Unsupported instrument or action")

    def result(outcome, reason, event_refs=()):
        return ShadowDecision(outcome, reason, now, settings.revision,
                              snapshot.revision if snapshot is not None else None,
                              policy.version, event_refs)

    if action in ("exit", "hold"):
        return result("not_applicable", "Calendar policy concerns new entries only")
    if not settings.enabled or not settings.calendar_enabled:
        return result("disabled", "Calendar shadow evaluation is disabled")
    if settings.mode != "shadow":
        return result("advisory", "Advisory mode does not evaluate entry restrictions")
    if snapshot is None or snapshot.available_at > now:
        return result("unknown", "No snapshot was available at decision time")

    before = timedelta(minutes=policy.before_minutes)
    after = timedelta(minutes=policy.after_minutes)
    required_start, required_end = now - after, now + before
    covered = set()
    eligible_sources = set()
    for source in snapshot.sources:
        if source.status != "healthy" or source.checked_at is None:
            continue
        age = (now - source.checked_at).total_seconds()
        if not 0 <= age <= policy.max_source_age_seconds:
            continue
        if source.coverage_start <= required_start and source.coverage_end > required_end:
            covered.update(source.categories)
            eligible_sources.add(source.source)
    if not set(policy.categories).issubset(covered):
        return result("unknown", "Relevant calendar coverage is missing, stale or incomplete")

    active = []
    for event in snapshot.events:
        if event.available_at > now or symbol not in event.symbols or event.category not in policy.categories:
            continue
        if event.source not in eligible_sources:
            return result("unknown", "Relevant event source evidence is stale or incomplete")
        if event.status == "cancelled":
            continue
        if event.status != "scheduled" or event.scheduled_at is None:
            return result("unknown", "Relevant event timing is unconfirmed")
        if event.scheduled_at - before <= now < event.scheduled_at + after:
            active.append(EventReference(event.source, event.event_id))
    if active:
        references = tuple(sorted(active, key=lambda reference: (reference.source, reference.event_id)))
        return result("would_block", "Inside a hypothetical event window", references)
    return result("would_allow", "Outside known hypothetical event windows")