"""Single-owner advisory service with explicitly enabled calendar collection."""

from dataclasses import dataclass
from datetime import datetime, timezone
import os
import queue
import threading
import time
import uuid

from market_intelligence.contracts import CalendarSnapshot, SourceCoverage, identifier
from market_intelligence.providers import CALENDAR_SOURCES, CalendarTransport, ProviderError
from market_intelligence.news import FED_NEWS_URL, NEWS_ERROR_CODES, parse_fed_news
from market_intelligence.analysis import AdvisoryAnalyzer, AnalysisLimits, AnalysisError
from market_intelligence.runtime import PersistentAnalysisBudget
from market_intelligence.store import IntelligenceStore, StoreError


class ServiceLock:
    def __init__(self, path):
        self.handle = None
        if path.is_symlink():
            raise StoreError("unsafe_service_lock")
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
            self.handle = os.fdopen(descriptor, "r+b")
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.close()
            raise StoreError("service_lock_unavailable") from exc

    def close(self):
        if self.handle is not None:
            self.handle.close()
            self.handle = None


@dataclass(frozen=True)
class NewsJobResult:
    attempt_id: str
    started_at: datetime
    completed_at: datetime
    batch: object
    raw: bytes | None


class IntelligenceService:
    def __init__(self, store, profiles=(), clock=None, allow_network=False, calendar_transport=None, monotonic=None,
                 calendar_sources=("bls", "bea"), collect_news=False, ai_providers=None, allow_ai=False):
        if not isinstance(store, IntelligenceStore) or type(profiles) is not tuple or len(profiles) > 50:
            raise ValueError("Service requires a validated store and bounded profile registry")
        for profile in profiles:
            identifier(profile)
        if len(set(profiles)) != len(profiles):
            raise ValueError("AI profile registry must be unique")
        self.store = store
        if ai_providers is not None and (type(ai_providers) is not dict or not ai_providers):
            raise ValueError("AI provider registry must be a nonempty profile mapping")
        if ai_providers and not allow_ai:
            raise ValueError("Provider profiles require explicit AI capability")
        self.profiles = tuple(dict.fromkeys(profiles + tuple(ai_providers or {})))
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.lock: ServiceLock | None = None
        if type(allow_network) is not bool:
            raise ValueError("Network capability requires explicit boolean enablement")
        self.allow_network = allow_network
        if (type(calendar_sources) is not tuple or not calendar_sources or len(calendar_sources) > len(CALENDAR_SOURCES)
            or any(not isinstance(name, str) for name in calendar_sources)
                or len(set(calendar_sources)) != len(calendar_sources)
                or any(name not in CALENDAR_SOURCES for name in calendar_sources)):
            raise ValueError("Calendar sources must be unique registered names")
        self.calendar_sources = calendar_sources
        if type(collect_news) is not bool:
            raise ValueError("News collection capability must be boolean")
        if collect_news and store.schema_version < 3:
            raise StoreError("upgrade_required")
        self.collect_news = collect_news
        if allow_ai and (not allow_network or not collect_news or store.schema_version < 4 or not ai_providers):
            raise ValueError("AI requires network/news capability, schema 4 and registered providers")
        self.analyzer: AdvisoryAnalyzer | None = None
        if allow_ai:
            limits = AnalysisLimits()
            self.analyzer = AdvisoryAnalyzer(ai_providers, limits=limits, clock=self.clock,
                                             budget=PersistentAnalysisBudget(store, limits))
        self.ai_due = {}
        self.ai_error = None
        self.fetch_sources = calendar_sources + (("fed_monetary",) if collect_news else ())
        self.calendar_transport = calendar_transport or CalendarTransport(proxy_url=None if allow_network else "")
        self.monotonic = monotonic or time.monotonic
        self.worker = None
        self.results = queue.Queue(maxsize=1)
        self.generation = 0
        self.last_settings_revision = None
        self.next_fetch_at = {name: 0.0 for name in self.fetch_sources}
        self.provider_errors: dict[str, str | None] = {name: None for name in self.fetch_sources}
        self.job_cancel = None

    @property
    def last_provider_error(self):
        return next((error for error in self.provider_errors.values() if error is not None), None)

    def __enter__(self):
        if self.lock is not None:
            raise StoreError("service_already_owned")
        self.lock = ServiceLock(self.store.path.with_suffix(".service.lock"))
        self.generation += 1
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.generation += 1
        if self.job_cancel is not None:
            self.job_cancel.set()
        lock=self.lock
        try:
            self.calendar_transport.cancel()
        finally:
            if lock is not None:
                lock.close()
            self.lock = None

    def step(self):
        if self.lock is None:
            raise StoreError("service_not_owned")
        now = self.clock()
        receipts = self.store.apply_pending(now, profiles=self.profiles)
        if not self.allow_network:
            return receipts
        settings = self.store.settings()
        if settings.revision != self.last_settings_revision:
            self.last_settings_revision = settings.revision
            self.next_fetch_at = {name: 0.0 for name in self.fetch_sources}
            if self.collect_news:
                previous_status = self.store.news_status()
                if previous_status is not None:
                    self.provider_errors["fed_monetary"] = previous_status.error_code
                    if previous_status.settings_revision == settings.revision:
                        age = (now - previous_status.available_at).total_seconds()
                        if age < 0:
                            raise StoreError("clock_invalid")
                        self.next_fetch_at["fed_monetary"] = self.monotonic() + max(0.0, 300.0 - age)
            if self.job_cancel is not None:
                self.job_cancel.set()
            self.calendar_transport.cancel()
        if self.worker is not None and not self.worker.is_alive():
            batch, error, revision, generation, source_name = self.results.get_nowait()
            self.worker = None
            permitted = settings.ai_enabled and settings.news_enabled if source_name == "analysis" else settings.news_enabled if source_name == "fed_monetary" else settings.calendar_enabled
            if settings.revision == revision and self.generation == generation and settings.enabled and permitted:
                now = self.clock()
                if source_name == "analysis":
                    if error is None and batch is not None:
                        self.store.publish_brief(batch, now)
                    self.ai_error = error
                elif source_name == "fed_monetary":
                    self.store.complete_news_attempt(batch.attempt_id, revision, batch.started_at, batch.completed_at, now,
                                                     batch=batch.batch, raw=batch.raw, error_code=error)
                else:
                    error = self._publish_calendar_result(batch, error, revision, source_name, now)
                if source_name != "analysis":
                    self.provider_errors[source_name] = error
                    self.next_fetch_at[source_name] = self.monotonic() + (300.0 if error or source_name == "fed_monetary" else 3600.0)
        elapsed = self.monotonic()
        due = [name for name in self.fetch_sources if elapsed >= self.next_fetch_at[name]
               and (settings.news_enabled if name == "fed_monetary" else settings.calendar_enabled)]
        if settings.enabled and self.worker is None and due:
            source_name = min(due, key=lambda name: (self.next_fetch_at[name], self.fetch_sources.index(name)))
            news_job = source_name == "fed_monetary"
            if news_job:
                url, parser = FED_NEWS_URL, parse_fed_news
            else:
                url, parser, _ = CALENDAR_SOURCES[source_name]
            revision, generation = settings.revision, self.generation
            started_at = self.clock()
            attempt_id = "attempt-" + uuid.uuid4().hex
            cancelled = threading.Event()
            self.job_cancel = cancelled

            def fetch():
                batch, error = None, None
                try:
                    if cancelled.is_set():
                        raise ProviderError("request_timeout")
                    payload = self.calendar_transport.fetch(url, cancel_event=cancelled)
                    if cancelled.is_set():
                        raise ProviderError("request_timeout")
                    batch = parser(payload, self.clock())
                except ProviderError as exc:
                    error = exc.code if not news_job or exc.code in NEWS_ERROR_CODES else "source_unavailable"
                except Exception:
                    error = "source_unavailable"
                if news_job:
                    batch = NewsJobResult(attempt_id, started_at, self.clock(),
                                          batch if error is None else None, payload if error is None else None)
                self.results.put_nowait((batch, error, revision, generation, source_name))

            self.worker = threading.Thread(target=fetch, name="intelligence-calendar", daemon=True)
            self.worker.start()
        else:
            analyzer=self.analyzer
            if settings.enabled and settings.news_enabled and settings.ai_enabled and analyzer is not None and self.worker is None:
                status = self.store.news_status()
                if status is not None and status.status == "successful":
                    archive = self.store.news_acquisition(status.acquisition_id)
                    candidates = sorted({item.cluster_id for item in archive.batch.items
                                         if item.published_at is not None and 0 <= (now-item.published_at).total_seconds() <= 86400})
                    for cluster_id in candidates[:5]:
                        key = (settings.revision, settings.ai_profile, cluster_id, archive.batch.input_hash)
                        if elapsed < self.ai_due.get(key, 0.0):
                            continue
                        self.ai_due[key] = elapsed + 300.0
                        if len(self.ai_due) > 256:
                            self.ai_due.pop(next(iter(self.ai_due)))
                        cancelled = threading.Event()
                        self.job_cancel = cancelled
                        revision, generation = settings.revision, self.generation
                        def analyze():
                            brief, error = None, None
                            try:
                                brief = analyzer.analyze(archive, settings, cluster_id, self.store.settings,
                                                         self.store.news_status, cancelled)
                            except (AnalysisError, StoreError) as exc:
                                error = exc.code
                            except Exception:
                                error = "analysis_failed"
                            self.results.put_nowait((brief, error, revision, generation, "analysis"))
                        self.worker = threading.Thread(target=analyze, name="intelligence-analysis", daemon=True)
                        self.worker.start()
                        break
        return receipts

    def _publish_calendar_result(self, batch, error, revision, source_name, now):
        previous = self.store.calendar()
        old_sources = previous.sources if previous else ()
        old_events = previous.events if previous else ()
        if error is None:
            previous_events = {(event.source, event.event_id): event for event in old_events}
            facts = ("category", "symbols", "scheduled_at", "title", "status", "source_timezone", "published_at")
            for event in batch.events:
                prior = previous_events.get((event.source, event.event_id))
                if prior is not None and (event.revision < prior.revision or (
                        event.revision == prior.revision and tuple(getattr(event, name) for name in facts)
                        != tuple(getattr(prior, name) for name in facts))):
                    error = "revision_conflict"
                    break
        if error is None:
            retained = {(event.source, event.event_id): event for event in old_events if event.source == source_name}
            retained.update({(event.source, event.event_id): event for event in batch.events})
            events = tuple(retained[key] for key in sorted(retained))
            if len(events) + sum(event.source != source_name for event in old_events) > 1000:
                error = "calendar_capacity"
            else:
                source = batch.source
        if error is not None:
            prior = next((source for source in old_sources if source.source == source_name), None)
            source = SourceCoverage(source_name, CALENDAR_SOURCES[source_name][2],
                                    prior.checked_at if prior else None, None, None,
                                    "invalid" if error in ("invalid_calendar", "unsupported_calendar", "revision_conflict", "calendar_capacity") else "unavailable",
                                    prior.input_hash if prior else None, prior.source_url if prior else None)
            events = tuple(event for event in old_events if event.source == source_name)
        sources = tuple(source for source in old_sources if source.source != source_name) + (source,)
        combined = tuple(event for event in old_events if event.source != source_name) + events
        snapshot = CalendarSnapshot(previous.revision + 1 if previous else 1, now, sources, combined)
        self.store.publish_calendar(snapshot, revision, now)
        return error

    def run(self, stop_event):
        while not stop_event.is_set():
            self.step()
            stop_event.wait(1.0)