"""Immutable calendar and advisory settings contracts, independent of trading."""

from dataclasses import dataclass
from datetime import datetime, timedelta
import re
from typing import Optional, Tuple
from urllib.parse import urlsplit


SYMBOLS = ("SPY", "QQQ", "BTC/USD", "GLD", "USO")
CATEGORIES = ("monetary_policy", "inflation", "employment", "growth", "energy")
CALENDAR_SOURCE_NAMES = ("bls", "bea", "ons", "boe_mpc")
NEWS_ERROR_CODES = frozenset({
    "endpoint_refused", "request_timeout", "request_in_progress", "address_refused", "http_unavailable",
    "encoding_refused", "content_refused", "response_too_large", "response_incomplete",
    "source_unavailable", "invalid_news", "parser_unavailable", "article_refused",
})
CALENDAR_ERROR_CODES = frozenset({
    "endpoint_refused", "request_timeout", "request_in_progress", "address_refused", "http_unavailable",
    "encoding_refused", "content_refused", "response_too_large", "response_incomplete",
    "source_unavailable", "invalid_calendar", "unsupported_calendar", "parser_unavailable",
    "revision_conflict", "calendar_capacity",
})
ANALYSIS_ERROR_CODES = frozenset({
    "analysis_failed", "analysis_in_progress", "profile_unavailable", "analysis_cancelled",
    "settings_changed", "profile_changed", "source_unavailable", "evidence_unavailable",
    "input_too_large", "budget_exhausted", "clock_invalid", "provider_unavailable",
    "analysis_expired", "output_invalid", "storage_unavailable", "storage_invalid",
    "reservation_invalid", "upgrade_required",
})
SERVICE_ERROR_CODES = {source: CALENDAR_ERROR_CODES for source in CALENDAR_SOURCE_NAMES}
SERVICE_ERROR_CODES.update({"fed_monetary": NEWS_ERROR_CODES, "analysis": ANALYSIS_ERROR_CODES})
SERVICE_HEARTBEAT_SECONDS = 300
SERVICE_OBSERVATION_MAX_AGE_SECONDS = 600


def utc_time(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() != timedelta(0):
        raise ValueError("Timestamp must be an aware UTC datetime")
    return value


def identifier(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value):
        raise ValueError("Invalid identifier")
    return value


def nonnegative_integer(value: int) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("Value must be a nonnegative integer")
    return value


@dataclass(frozen=True)
class IntelligenceSettings:
    enabled: bool = False
    calendar_enabled: bool = True
    news_enabled: bool = True
    ai_enabled: bool = False
    mode: str = "advisory"
    ai_profile: Optional[str] = None
    revision: int = 0

    def __post_init__(self):
        for value in (self.enabled, self.calendar_enabled, self.news_enabled, self.ai_enabled):
            if type(value) is not bool:
                raise ValueError("Feature flags must be booleans")
        if self.mode not in ("advisory", "shadow"):
            raise ValueError("Only advisory and shadow modes are supported")
        if self.ai_profile is not None:
            identifier(self.ai_profile)
        if self.ai_enabled and self.ai_profile is None:
            raise ValueError("AI requires an operator-configured profile")
        nonnegative_integer(self.revision)


@dataclass(frozen=True)
class ServiceFailure:
    source: str
    error_code: str
    observed_at: datetime

    def __post_init__(self):
        if (not isinstance(self.source, str) or self.source not in SERVICE_ERROR_CODES
                or not isinstance(self.error_code, str)
                or self.error_code not in SERVICE_ERROR_CODES[self.source]):
            raise ValueError("Service failures require registered public error codes")
        utc_time(self.observed_at)


@dataclass(frozen=True)
class ServiceObservation:
    observation_id: str
    service_id: str
    revision: int
    settings_revision: int
    observed_at: datetime
    network_permitted: bool
    calendar_sources: Tuple[str, ...]
    news_permitted: bool
    ai_permitted: bool
    failures: Tuple[ServiceFailure, ...]

    def __post_init__(self):
        identifier(self.observation_id)
        identifier(self.service_id)
        nonnegative_integer(self.revision)
        nonnegative_integer(self.settings_revision)
        utc_time(self.observed_at)
        if self.revision == 0:
            raise ValueError("Service observations require a positive revision")
        if any(type(value) is not bool for value in (self.network_permitted, self.news_permitted, self.ai_permitted)):
            raise ValueError("Service capabilities must be booleans")
        if (type(self.calendar_sources) is not tuple or not 1 <= len(self.calendar_sources) <= len(CALENDAR_SOURCE_NAMES)
                or any(source not in CALENDAR_SOURCE_NAMES for source in self.calendar_sources)
                or len(set(self.calendar_sources)) != len(self.calendar_sources)):
            raise ValueError("Service calendars must be registered and unique")
        if self.ai_permitted and (not self.network_permitted or not self.news_permitted):
            raise ValueError("AI capability requires network and news capabilities")
        if (type(self.failures) is not tuple or len(self.failures) > len(CALENDAR_SOURCE_NAMES) + 2
                or any(not isinstance(failure, ServiceFailure) for failure in self.failures)
                or len({failure.source for failure in self.failures}) != len(self.failures)):
            raise ValueError("Service failures must be bounded and unique by source")
        for failure in self.failures:
            if (failure.observed_at > self.observed_at or not self.network_permitted
                    or failure.source in CALENDAR_SOURCE_NAMES and failure.source not in self.calendar_sources
                    or failure.source == "fed_monetary" and not self.news_permitted
                    or failure.source == "analysis" and not self.ai_permitted):
                raise ValueError("Service failure does not match the observed capabilities or clock")


def service_status_state(observation, settings, now):
    utc_time(now)
    if not isinstance(settings, IntelligenceSettings):
        raise ValueError("Validated settings are required")
    if observation is None:
        return "unknown"
    if not isinstance(observation, ServiceObservation):
        raise ValueError("Validated service observation is required")
    if observation.observed_at > now:
        return "future"
    if observation.settings_revision != settings.revision:
        return "superseded"
    if now - observation.observed_at > timedelta(seconds=SERVICE_OBSERVATION_MAX_AGE_SECONDS):
        return "stale"
    return "recent"


@dataclass(frozen=True)
class ControlRequest:
    request_id: str
    database_id: str
    base_revision: int
    requested_at: datetime
    expires_at: datetime
    settings: IntelligenceSettings

    def __post_init__(self):
        identifier(self.request_id)
        identifier(self.database_id)
        nonnegative_integer(self.base_revision)
        utc_time(self.requested_at)
        utc_time(self.expires_at)
        if not timedelta(0) < self.expires_at - self.requested_at <= timedelta(minutes=5):
            raise ValueError("Control requests require a positive lifetime of at most five minutes")
        if not isinstance(self.settings, IntelligenceSettings) or self.settings.revision != self.base_revision + 1:
            raise ValueError("Requested settings must advance the base revision exactly once")


@dataclass(frozen=True)
class ControlReceipt:
    request_id: str
    database_id: str
    status: str
    effective_revision: Optional[int]
    applied_at: Optional[datetime]
    reason: str

    def __post_init__(self):
        identifier(self.request_id)
        identifier(self.database_id)
        if self.status not in ("pending", "applied", "rejected", "expired"):
            raise ValueError("Unsupported control status")
        if self.effective_revision is not None:
            nonnegative_integer(self.effective_revision)
        if self.applied_at is not None:
            utc_time(self.applied_at)
        if self.status == "applied" and (self.effective_revision is None or self.applied_at is None):
            raise ValueError("Applied controls require committed revision and time")
        if self.status != "applied" and self.applied_at is not None:
            raise ValueError("Only applied controls have an application time")
        if not isinstance(self.reason, str) or not self.reason.strip() or len(self.reason) > 500:
            raise ValueError("Control reason is required and bounded")


@dataclass(frozen=True)
class NewsEvidence:
    evidence_id: str
    source: str
    publisher_family: str
    source_url: str
    title: str
    snippet: str
    published_at: Optional[datetime]
    received_at: datetime
    content_hash: str
    cluster_id: str
    supersedes: Optional[str] = None

    def __post_init__(self):
        for value in (self.evidence_id, self.source, self.publisher_family, self.cluster_id):
            identifier(value)
        if self.supersedes is not None:
            identifier(self.supersedes)
            if self.supersedes == self.evidence_id:
                raise ValueError("Evidence cannot supersede itself")
        utc_time(self.received_at)
        if self.published_at is not None:
            utc_time(self.published_at)
            if self.published_at > self.received_at:
                raise ValueError("Publication cannot follow receipt")
        if not isinstance(self.content_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", self.content_hash):
            raise ValueError("News content hash must be SHA256")
        for value, maximum, required in ((self.title, 500, True), (self.snippet, 2000, False)):
            if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()) or any(ord(character) < 32 for character in value):
                raise ValueError("News text must be bounded plain text")
        if not isinstance(self.source_url, str) or len(self.source_url) > 2048 or any(character.isspace() or ord(character) < 32 for character in self.source_url):
            raise ValueError("Invalid news reference URL")
        try:
            parsed = urlsplit(self.source_url)
            valid = parsed.scheme == "https" and bool(parsed.hostname) and parsed.username is None and parsed.password is None and parsed.port in (None, 443)
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("News references require HTTPS without credentials")


@dataclass(frozen=True)
class CalendarEvent:
    event_id: str
    source: str
    source_url: str
    category: str
    symbols: Tuple[str, ...]
    scheduled_at: Optional[datetime]
    available_at: datetime
    title: str
    status: str = "scheduled"
    source_timezone: str = "America/New_York"
    published_at: Optional[datetime] = None
    revision: int = 0
    source_notes: Tuple[str, ...] = ()

    def __post_init__(self):
        identifier(self.event_id)
        identifier(self.source)
        nonnegative_integer(self.revision)
        utc_time(self.available_at)
        if self.scheduled_at is not None:
            utc_time(self.scheduled_at)
        if self.published_at is not None:
            utc_time(self.published_at)
            if self.published_at > self.available_at:
                raise ValueError("Publication cannot follow event availability")
        if self.category not in CATEGORIES:
            raise ValueError("Unsupported calendar category")
        if type(self.symbols) is not tuple or len(set(self.symbols)) != len(self.symbols):
            raise ValueError("Symbols must be an immutable unique tuple")
        if any(symbol not in SYMBOLS for symbol in self.symbols):
            raise ValueError("Unsupported instrument")
        if not self.symbols and self.source not in ("ons", "boe_mpc"):
            raise ValueError("Only macro calendar events may be unlinked to instruments")
        if self.status not in ("scheduled", "tentative", "cancelled", "postponed"):
            raise ValueError("Unsupported event status")
        if (type(self.source_notes) is not tuple or len(self.source_notes) > 10
                or any(not isinstance(note, str) or not note.strip() or len(note) > 1000
                       or any(ord(character) < 32 or character in "<>" for character in note)
                       for note in self.source_notes)):
            raise ValueError("Source notes must be bounded plain text")
        if not isinstance(self.title, str) or not self.title.strip() or len(self.title) > 500:
            raise ValueError("Event title is required and bounded")
        if not isinstance(self.source_timezone, str) or not self.source_timezone or len(self.source_timezone) > 100:
            raise ValueError("Source timezone is required and bounded")
        if not isinstance(self.source_url, str) or len(self.source_url) > 2048 or any(
                character.isspace() or ord(character) < 32 for character in self.source_url):
            raise ValueError("Invalid source reference")
        try:
            parsed = urlsplit(self.source_url)
            valid = (parsed.scheme == "https" and bool(parsed.hostname) and parsed.username is None
                     and parsed.password is None and parsed.port in (None, 443))
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("Source reference must be HTTPS without credentials")


@dataclass(frozen=True)
class SourceCoverage:
    source: str
    categories: Tuple[str, ...]
    checked_at: Optional[datetime]
    coverage_start: Optional[datetime]
    coverage_end: Optional[datetime]
    status: str = "unavailable"
    input_hash: Optional[str] = None
    source_url: Optional[str] = None
    source_urls: Tuple[str, ...] = ()

    def __post_init__(self):
        identifier(self.source)
        if self.input_hash is not None and (not isinstance(self.input_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", self.input_hash)):
            raise ValueError("Source input hash must be SHA256")
        if (self.input_hash is None) != (self.source_url is None):
            raise ValueError("Downloaded source provenance requires both hash and URL")
        if (type(self.source_urls) is not tuple or len(self.source_urls) > 2
                or any(not isinstance(url, str) for url in self.source_urls)
                or len(set(self.source_urls)) != len(self.source_urls)):
            raise ValueError("Source URLs must be a bounded unique tuple")
        urls = self.source_urls or (() if self.source_url is None else (self.source_url,))
        if self.source_urls and (self.input_hash is None or self.source_url not in self.source_urls):
            raise ValueError("Additional source URLs require matching downloaded provenance")
        if self.source == "ons" and self.input_hash is not None and len(self.source_urls) != 2:
            raise ValueError("ONS provenance must identify both result endpoints")
        for source_url in urls:
            if not isinstance(source_url, str) or len(source_url) > 2048 or any(character.isspace() or ord(character) < 32 for character in source_url):
                raise ValueError("Invalid source provenance URL")
            try:
                parsed = urlsplit(source_url)
                valid = parsed.scheme == "https" and bool(parsed.hostname) and parsed.username is None and parsed.password is None and parsed.port in (None, 443)
            except ValueError:
                valid = False
            if not valid:
                raise ValueError("Invalid source provenance URL")
        if type(self.categories) is not tuple or not self.categories or len(set(self.categories)) != len(self.categories):
            raise ValueError("Coverage categories must be an immutable unique tuple")
        if any(category not in CATEGORIES for category in self.categories):
            raise ValueError("Unsupported coverage category")
        if self.status not in ("healthy", "unavailable", "invalid"):
            raise ValueError("Unsupported source status")
        for value in (self.checked_at, self.coverage_start, self.coverage_end):
            if value is not None:
                utc_time(value)
        if (self.coverage_start is None) != (self.coverage_end is None):
            raise ValueError("Coverage requires both bounds")
        if self.coverage_start is not None and self.coverage_start >= self.coverage_end:
            raise ValueError("Coverage interval must be nonempty")
        if self.status == "healthy" and (self.checked_at is None or self.coverage_start is None):
            raise ValueError("Healthy source requires observation and coverage evidence")


@dataclass(frozen=True)
class CalendarSnapshot:
    revision: int
    available_at: datetime
    sources: Tuple[SourceCoverage, ...]
    events: Tuple[CalendarEvent, ...]

    def __post_init__(self):
        nonnegative_integer(self.revision)
        utc_time(self.available_at)
        if type(self.sources) is not tuple or any(not isinstance(source, SourceCoverage) for source in self.sources):
            raise ValueError("Sources must be immutable coverage records")
        if type(self.events) is not tuple or any(not isinstance(event, CalendarEvent) for event in self.events):
            raise ValueError("Events must be immutable calendar records")
        sources = {source.source: source for source in self.sources}
        if len(sources) != len(self.sources):
            raise ValueError("Duplicate source coverage")
        if len({(event.source, event.event_id) for event in self.events}) != len(self.events):
            raise ValueError("Snapshot must contain only the current revision of each event")
        for source in self.sources:
            if source.checked_at is not None and source.checked_at > self.available_at:
                raise ValueError("Source observation cannot follow snapshot availability")
        for event in self.events:
            if event.source not in sources or event.category not in sources[event.source].categories:
                raise ValueError("Event source/category requires coverage metadata")
            if event.available_at > self.available_at:
                raise ValueError("Event cannot follow snapshot availability")