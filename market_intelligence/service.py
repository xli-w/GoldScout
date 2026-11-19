"""Single-owner advisory service with explicitly enabled calendar collection."""

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import os
import queue
import threading
import time
import uuid

from market_intelligence.contracts import (
    SERVICE_ERROR_CODES, SERVICE_HEARTBEAT_SECONDS, CalendarSnapshot, ServiceFailure,
    SourceCoverage, identifier,
)
from market_intelligence.providers import CALENDAR_SOURCES, CalendarTransport, ProviderError
from market_intelligence.news import FED_NEWS_URL, NEWS_ERROR_CODES, parse_fed_news
from market_intelligence.analysis import AdvisoryAnalyzer, AnalysisLimits, AnalysisError
from market_intelligence.runtime import PersistentAnalysisBudget
from market_intelligence.store import IntelligenceStore, StoreBusy, StoreError


logger = logging.getLogger(__name__)


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


class _DeadlineEvent(threading.Event):
    def __init__(self, deadline):
        super().__init__()
        self.deadline = deadline

    def is_set(self):
        if time.monotonic() >= self.deadline:
            self.set()
        return super().is_set()

    def wait(self, timeout=None):
        if self.is_set():
            return True
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            self.set()
            return True
        if timeout is None or timeout > remaining:
            timeout = remaining
        return super().wait(timeout) or self.is_set()


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
        self.pending_result = None
        self.generation = 0
        self.last_settings_revision = None
        self.next_fetch_at = {name: 0.0 for name in self.fetch_sources}
        self.provider_errors: dict[str, str | None] = {name: None for name in self.fetch_sources}
        self.job_cancel = None
        self.service_id = None
        self.reported_failures: dict[str, ServiceFailure] = {}
        self.status_dirty = True
        self.next_heartbeat_at = 0.0
        self._check_source = None
        self._check_started = False
        self._check_result = None
        self._check_deadline = None

    @property
    def last_provider_error(self):
        return next((error for error in self.provider_errors.values() if error is not None), None)

    def __enter__(self):
        if self.lock is not None:
            raise StoreError("service_already_owned")
        self.lock = ServiceLock(self.store.path.with_suffix(".service.lock"))
        self.generation += 1
        self.service_id = "service-" + uuid.uuid4().hex
        self.last_settings_revision = None
        self.reported_failures = {}
        self.status_dirty = True
        self.next_heartbeat_at = 0.0
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

    def _restore_reported_failures(self, settings, now):
        self.reported_failures = {}
        if self.store.schema_version < 5:
            return
        previous = self.store.service_status()
        if previous is not None and previous.observed_at > now:
            raise StoreError("clock_invalid")
        allowed = set(self.calendar_sources) if self.allow_network else set()
        if self.allow_network and self.collect_news:
            allowed.add("fed_monetary")
        if self.analyzer is not None:
            allowed.add("analysis")
        if previous is not None and previous.settings_revision == settings.revision:
            self.reported_failures = {item.source: item for item in previous.failures if item.source in allowed}
        self.status_dirty = True

    def _record_result(self, source, error, now):
        if error is None:
            self.reported_failures.pop(source, None)
        else:
            fallback = "analysis_failed" if source == "analysis" else "source_unavailable"
            code = error if isinstance(error, str) and error in SERVICE_ERROR_CODES[source] else fallback
            self.reported_failures[source] = ServiceFailure(source, code, now)
        self.status_dirty = True

    def _cancel_job(self):
        if self.job_cancel is not None:
            self.job_cancel.set()
        self.calendar_transport.cancel()

    def _check_timed_out(self):
        return (self._check_source is not None and self._check_result is None
                and time.monotonic() >= self._check_deadline)

    def _interrupt_check(self):
        self._cancel_job()
        self._check_result = {"source": self._check_source, "status": "interrupted",
                              "error_code": "check_timeout"}

    @staticmethod
    def _retry_analysis_storage(operation, cancelled):
        while True:
            if cancelled.is_set():
                raise AnalysisError("analysis_cancelled")
            try:
                return operation()
            except StoreBusy:
                logger.warning("Intelligence storage is busy; retrying analysis storage.")
                if cancelled.wait(1.0):
                    raise AnalysisError("analysis_cancelled")

    def _observe_service(self, settings):
        if self.store.schema_version < 5 or self._check_source is not None:
            return
        elapsed = self.monotonic()
        if not self.status_dirty and elapsed < self.next_heartbeat_at:
            return
        try:
            self.store.observe_service(
                "observation-" + uuid.uuid4().hex, self.service_id, settings.revision, self.clock(),
                self.allow_network, self.calendar_sources, self.collect_news, self.analyzer is not None,
                tuple(self.reported_failures[key] for key in sorted(self.reported_failures)))
        except (StoreError, ValueError):
            logger.error("Service status could not be recorded; earlier settings or evidence may already be committed.")
            raise
        self.status_dirty = False
        self.next_heartbeat_at = elapsed + SERVICE_HEARTBEAT_SECONDS

    def step(self):
        if self.lock is None:
            raise StoreError("service_not_owned")
        now = self.clock()
        receipts = self.store.apply_pending(now, profiles=self.profiles)
        controls_applied = any(receipt.status == "applied" for receipt in receipts)
        if controls_applied:
            self._cancel_job()
        try:
            settings = self.store.settings()
        except StoreError:
            logger.error("Service settings could not be read; earlier settings or evidence may already be committed.")
            raise
        if settings.revision != self.last_settings_revision:
            try:
                if not controls_applied:
                    self._cancel_job()
                self._restore_reported_failures(settings, now)
                self.next_fetch_at = {name: 0.0 for name in self.fetch_sources}
                if self.allow_network and self.collect_news:
                    previous_status = self.store.news_status()
                    if previous_status is not None:
                        self.provider_errors["fed_monetary"] = previous_status.error_code
                        if previous_status.settings_revision == settings.revision:
                            age = (now - previous_status.available_at).total_seconds()
                            if age < 0:
                                raise StoreError("clock_invalid")
                            self.next_fetch_at["fed_monetary"] = self.monotonic() + max(0.0, 300.0 - age)
                            if settings.enabled and settings.news_enabled:
                                if previous_status.error_code is None:
                                    self.reported_failures.pop("fed_monetary", None)
                                else:
                                    self.reported_failures["fed_monetary"] = ServiceFailure(
                                        "fed_monetary", previous_status.error_code, previous_status.available_at)
            except (StoreError, ValueError):
                logger.error("Service status could not be restored; earlier settings or evidence may already be committed.")
                raise
            self.last_settings_revision = settings.revision
        if not self.allow_network:
            self._observe_service(settings)
            return receipts
        if self._check_started and self._check_timed_out():
            self._interrupt_check()
            return receipts
        if self.worker is not None and not self.worker.is_alive():
            if self._check_timed_out():
                self._interrupt_check()
                return receipts
            if self.pending_result is None:
                self.pending_result = self.results.get_nowait()
            batch, error, revision, generation, source_name = self.pending_result
            permitted = settings.ai_enabled and settings.news_enabled if source_name == "analysis" else settings.news_enabled if source_name == "fed_monetary" else settings.calendar_enabled
            eligible = (settings.revision == revision and self.generation == generation and settings.enabled
                        and permitted and (self._check_source != source_name or self._check_result is None))
            if eligible:
                now = self.clock()
                if self._check_timed_out():
                    self._interrupt_check()
                    return receipts
                record_result = True
                if source_name == "analysis":
                    brief, generated = batch
                    if error is None and brief is not None:
                        self.store.publish_brief(brief, now)
                        previous_failure = self.reported_failures.get("analysis")
                        # A cache hit is not a new provider success.
                        if previous_failure is not None and not generated:
                            record_result = False
                    elif error is None:
                        error = "analysis_failed"
                    self.ai_error = error
                elif source_name == "fed_monetary":
                    self.store.complete_news_attempt(batch.attempt_id, revision, batch.started_at, batch.completed_at, now,
                                                     batch=batch.batch, raw=batch.raw, error_code=error)
                else:
                    error = self._publish_calendar_result(batch, error, revision, source_name, now)
                    if self._check_result is not None:
                        return receipts
                if record_result:
                    self._record_result(source_name, error, now)
                if source_name != "analysis":
                    self.provider_errors[source_name] = error
                    self.next_fetch_at[source_name] = self.monotonic() + (300.0 if error or source_name == "fed_monetary" else 3600.0)
            if self._check_source == source_name and self._check_result is None:
                if not eligible:
                    status, code = "discarded", "settings_changed"
                elif error is not None:
                    failure = self.reported_failures.get(source_name)
                    status = "failed"
                    code = failure.error_code if failure is not None else (
                        "analysis_failed" if source_name == "analysis" else "source_unavailable")
                elif source_name == "analysis" and not generated:
                    status, code = "cached", None
                else:
                    status, code = "succeeded", None
                self._check_result = {"source": source_name, "status": status, "error_code": code}
            self.pending_result = None
            self.worker = None
        if self.worker is None or self.status_dirty:
            self._observe_service(settings)
        if not self._check_started and self._check_timed_out():
            self._interrupt_check()
            return receipts
        elapsed = self.monotonic()
        due = [name for name in self.fetch_sources if elapsed >= self.next_fetch_at[name]
               and (settings.news_enabled if name == "fed_monetary" else settings.calendar_enabled)
               and (self._check_source is None or
                    (self._check_source == name and not self._check_started and self._check_result is None))]
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
            cancelled = threading.Event() if self._check_source is None else _DeadlineEvent(self._check_deadline)
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
                    logger.warning("Public feed worker failed unexpectedly.")
                    error = "source_unavailable"
                if news_job:
                    batch = NewsJobResult(attempt_id, started_at, self.clock(),
                                          batch if error is None else None, payload if error is None else None)
                self.results.put_nowait((batch, error, revision, generation, source_name))

            self.worker = threading.Thread(target=fetch, name="intelligence-calendar", daemon=True)
            if self._check_source is not None:
                self._check_started = True
            self.worker.start()
        else:
            analyzer=self.analyzer
            if (settings.enabled and settings.news_enabled and settings.ai_enabled and analyzer is not None
                    and self.worker is None and (self._check_source is None
                    or (self._check_source == "analysis" and not self._check_started
                        and self._check_result is None))):
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
                        cancelled = threading.Event() if self._check_source is None else _DeadlineEvent(self._check_deadline)
                        self.job_cancel = cancelled
                        revision, generation = settings.revision, self.generation
                        def analyze():
                            brief, generated, error = None, False, None
                            try:
                                def reserve(requested_at, input_bytes, context):
                                    def commit_reservation():
                                        current = self.clock()
                                        if current < requested_at:
                                            raise AnalysisError("clock_invalid")
                                        return analyzer.budget.reserve_request(current, input_bytes, context)
                                    return self._retry_analysis_storage(commit_reservation, cancelled)

                                brief, generated = analyzer._analyze_with_freshness(
                                    archive, settings, cluster_id,
                                    lambda: self._retry_analysis_storage(self.store.settings, cancelled),
                                    lambda: self._retry_analysis_storage(self.store.news_status, cancelled),
                                    cancelled, reserve)
                            except (AnalysisError, StoreError) as exc:
                                error = exc.code
                            except Exception:
                                error = "analysis_failed"
                            self.results.put_nowait(((brief, generated), error, revision, generation, "analysis"))
                        self.worker = threading.Thread(target=analyze, name="intelligence-analysis", daemon=True)
                        if self._check_source is not None:
                            self._check_started = True
                        self.worker.start()
                        break
        return receipts

    def check_once(self, source):
        if (self.lock is None or self.generation != 1 or self.last_settings_revision is not None
                or self.worker is not None or self._check_source is not None):
            raise ValueError("A supervised check requires a fresh, owned service")
        if (not self.allow_network or source not in (*self.calendar_sources, "fed_monetary", "analysis")
                or (source == "fed_monetary" and not self.collect_news)
                or (source == "analysis" and self.analyzer is None)):
            raise ValueError("The selected source requires its explicit capability")
        self._check_source = source
        receipts = []
        deadline = time.monotonic() + 45.0
        self._check_deadline = deadline
        while True:
            receipts.extend(self.step())
            if self._check_result is not None:
                return self._check_result, tuple(receipts)
            if not self._check_started:
                self._check_result = {"source": source, "status": "not_started",
                                      "error_code": "unavailable_or_disabled"}
                return self._check_result, tuple(receipts)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._interrupt_check()
                return self._check_result, tuple(receipts)
            self.worker.join(timeout=min(remaining, 0.2))

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
        if self._check_timed_out():
            self._interrupt_check()
            return error
        self.store.publish_calendar(snapshot, revision, now)
        return error

    def run(self, stop_event):
        while not stop_event.is_set():
            try:
                self.step()
            except StoreBusy:
                logger.warning("Intelligence storage is busy; retrying. Earlier settings or evidence may already be committed.")
            stop_event.wait(1.0)