from dataclasses import replace
from datetime import datetime, timedelta, timezone
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import threading
from contextlib import closing
from contextlib import redirect_stdout, redirect_stderr
import unittest
from unittest.mock import Mock, patch

from dashboard.app import create_app
from dashboard.intelligence import IntelligenceWorkspace
from fastapi.testclient import TestClient
from market_intelligence.analysis import JSONSummaryProvider, ProviderResponse
from market_intelligence.contracts import CalendarEvent, CalendarSnapshot, ServiceFailure, SourceCoverage
from market_intelligence.news import NewsObservation, parse_fed_news
from market_intelligence.providers import BEA_CALENDAR_URL, BLS_CALENDAR_URL, ProviderError
from market_intelligence.store import IntelligenceStore, StoreBusy, StoreError
from market_intelligence.store import encode
from market_intelligence.__main__ import main
from market_intelligence.service import IntelligenceService
from market_intelligence.news import FED_NEWS_URL
from test_dashboard_api import MemoryEventLoop
from test_intelligence_controls import IntelligenceStoreFixture
from test_intelligence_news import feed
from test_intelligence_providers import calendar


class NewsObservationContractTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
        self.success = NewsObservation("attempt-1", 1, 1, self.now, self.now, self.now,
                                       "successful", None, "attempt-1", "attempt-1")

    def test_success_requires_a_committed_acquisition_identity(self):
        for changes in ({"acquisition_id": None}, {"last_success_id": None}, {"error_code": "http_unavailable"}):
            with self.assertRaises(ValueError):
                replace(self.success, **changes)

    def test_failure_retains_last_success_without_claiming_new_evidence(self):
        failure = replace(self.success, attempt_id="attempt-2", revision=2, status="unavailable",
                          error_code="source_unavailable", acquisition_id=None)
        self.assertEqual(failure.last_success_id, "attempt-1")
        with self.assertRaises(ValueError):
            replace(failure, acquisition_id="attempt-2")

    def test_observation_clocks_are_causal_and_errors_are_bounded_identifiers(self):
        for changes in ({"completed_at": self.now - timedelta(seconds=1)},
                        {"available_at": self.now - timedelta(seconds=1)}, {"revision": 0},
                        {"started_at": self.now.replace(tzinfo=None)}):
            with self.assertRaises(ValueError):
                replace(self.success, **changes)
        with self.assertRaises(ValueError):
            replace(self.success, status="unavailable", acquisition_id=None, error_code="private path or secret")


@unittest.skipIf(importlib.util.find_spec("defusedxml") is None, "Install requirements-intelligence.txt for source status tests")
class NewsObservationStoreTests(IntelligenceStoreFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.store.enqueue(self.request(enabled=True), self.now)
        self.store.apply_pending(self.now)
        self.raw = feed()
        self.batch = parse_fed_news(self.raw, self.now)

    def success(self, attempt="attempt-1"):
        return self.store.complete_news_attempt(attempt, 1, self.now, self.now, self.now,
                                                batch=self.batch, raw=self.raw)

    def test_success_commits_source_status_and_raw_archive_together(self):
        status = self.success()
        self.assertEqual(status.status, "successful")
        self.assertEqual(self.store.news_status(), status)
        self.assertEqual(self.store.news_body("attempt-1"), self.raw)
        self.assertEqual(IntelligenceStore(self.path).news_status(), status)

    def test_cli_status_reports_recorded_observation_without_connectivity_claim(self):
        status = self.success()
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["status", "--database", str(self.path)]), 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["last_news_observation"], json.loads(encode(status)))
        self.assertIsNone(payload["providers_connected"])

    def test_cli_check_reports_the_committed_news_observation(self):
        self.success()
        transport = Mock()
        transport.fetch.return_value = feed()
        later = self.now + timedelta(minutes=6)
        with patch("market_intelligence.service.CalendarTransport", return_value=transport), \
             patch("market_intelligence.service.datetime") as service_clock, \
             patch("market_intelligence.__main__.datetime") as cli_clock, redirect_stdout(io.StringIO()) as output:
            service_clock.now.return_value = later
            cli_clock.now.return_value = later
            result = main(["check", "--database", str(self.path), "--allow-network", "--collect-news",
                           "--check-source", "fed_monetary"])
        payload = json.loads(output.getvalue())
        self.assertEqual(result, 0)
        self.assertEqual(payload["check"]["status"], "succeeded")
        self.assertEqual(payload["last_news_observation"], json.loads(encode(self.store.news_status())))

    def test_cli_status_pins_settings_through_observation_time(self):
        self.store.observe_service("observation", "service", 1, self.now, False, ("bls", "bea"),
                                   False, False, ())
        self.store.enqueue(self.request("change", base_revision=1, enabled=True, news_enabled=False), self.now)
        attempted = []

        def clock_now(zone):
            try:
                self.store.apply_pending(self.now)
            except StoreError as exc:
                self.assertEqual(exc.code, "storage_unavailable")
                attempted.append(False)
            else:
                attempted.append(True)
            return self.now

        with patch("market_intelligence.__main__.datetime") as clock, redirect_stdout(io.StringIO()) as output:
            clock.now.side_effect = clock_now
            self.assertEqual(main(["status", "--database", str(self.path)]), 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(attempted, [False])
        self.assertEqual(payload["settings"]["revision"], self.store.settings().revision)
        self.assertEqual(payload["service_status_state"], "recent")
        self.assertEqual(self.store.receipt("change").status, "pending")
        self.assertEqual(self.store.apply_pending(self.now)[0].status, "applied")

    def test_observations_match_wire_contract(self):
        if importlib.util.find_spec("jsonschema") is None:
            self.skipTest("Install dashboard development dependencies for schema checks")
        from jsonschema import Draft202012Validator, FormatChecker
        schema = json.loads((Path(__file__).resolve().parents[1] / "market_intelligence" / "schema.json").read_text())
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        success = self.success()
        validator.validate(json.loads(encode(success)))
        later = self.now + timedelta(minutes=5)
        failure = self.store.complete_news_attempt("attempt-2", 1, later, later, later, error_code="http_unavailable")
        validator.validate(json.loads(encode(failure)))

    def test_failure_retains_last_success_across_restart(self):
        self.success()
        later = self.now + timedelta(minutes=5)
        failure = self.store.complete_news_attempt("attempt-2", 1, later, later, later,
                                                   error_code="source_unavailable")
        self.assertEqual(failure.last_success_id, "attempt-1")
        self.assertIsNone(failure.acquisition_id)
        self.assertEqual(IntelligenceStore(self.path).news_status(), failure)
        self.assertEqual(self.store.news_body(failure.last_success_id), self.raw)

    def test_attempt_retries_are_idempotent_and_conflicts_are_refused(self):
        original = self.success()
        self.assertEqual(self.success(), original)
        with self.assertRaises(StoreError):
            self.store.complete_news_attempt("attempt-1", 1, self.now, self.now, self.now,
                                             error_code="source_unavailable")

    def test_failed_status_write_rolls_back_new_acquisition(self):
        original = self.store._news_observation
        count = 0
        def fail_final(handle, attempt):
            nonlocal count
            count += 1
            if count == 2:
                raise StoreError("injected failure")
            return original(handle, attempt)
        with patch.object(self.store, "_news_observation", side_effect=fail_final):
            with self.assertRaises(StoreError):
                self.success()
        self.assertIsNone(self.store.news_acquisition("attempt-1"))
        self.assertIsNone(self.store.news_status())

    def test_observations_are_immutable_and_disable_blocks_new_status(self):
        self.success()
        with closing(sqlite3.connect(self.path)) as handle:
            with self.assertRaises(sqlite3.IntegrityError):
                handle.execute("INSERT OR REPLACE INTO news_observations SELECT * FROM news_observations")
        self.store.enqueue(self.request("off", base_revision=1, enabled=False), self.now)
        self.store.apply_pending(self.now)
        with self.assertRaises(StoreError) as result:
            self.store.complete_news_attempt("attempt-2", 1, self.now, self.now, self.now,
                                             error_code="source_unavailable")
        self.assertEqual(result.exception.code, "settings_changed")


@unittest.skipIf(importlib.util.find_spec("defusedxml") is None, "Install requirements-intelligence.txt for polling tests")
class ScheduledNewsTests(IntelligenceStoreFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.transport = Mock()
        self.transport.fetch.return_value = feed()
        self.elapsed = 0.0

    def service(self, collect_news=True, allow_network=True):
        return IntelligenceService(self.store, clock=lambda: self.now, monotonic=lambda: self.elapsed,
                                   calendar_transport=self.transport, allow_network=allow_network,
                                   collect_news=collect_news, calendar_sources=("bls",))

    def complete(self, service):
        service.worker.join(timeout=2)
        self.assertFalse(service.worker.is_alive())
        service.step()

    def enable(self):
        self.store.enqueue(self.request(enabled=True, calendar_enabled=False, news_enabled=True), self.now)

    def test_cli_news_flag_requires_run_and_network_permission(self):
        for action in ("status", "once", "initialize"):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                main([action, "--database", str(self.path), "--collect-news"])
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(["run", "--database", str(self.path), "--collect-news"])

    def test_cli_supervised_check_requires_explicit_source_and_network(self):
        for extra in ([], ["--allow-network"], ["--check-source", "bls"],
                      ["--allow-network", "--check-source", "bls", "--check-source", "bea"]):
            with self.subTest(extra=extra), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                main(["check", "--database", str(self.path), *extra])
        output = io.StringIO()
        with redirect_stdout(output):
            result = main(["check", "--database", str(self.path), "--allow-network",
                           "--check-source", "bls"])
        self.assertEqual(result, 1)
        snapshot = json.loads(output.getvalue())
        self.assertEqual(snapshot["check"]["status"], "not_started")
        self.assertIsNone(snapshot["providers_connected"])

    def test_supervised_news_check_does_not_fetch_due_calendars(self):
        self.store.enqueue(self.request(enabled=True, calendar_enabled=True, news_enabled=True), self.now)
        with self.service() as service:
            result, _ = service.check_once("fed_monetary")
            self.assertEqual(result["status"], "succeeded")
            self.elapsed = 7200.0
            service.step()
        self.assertEqual([call.args[0] for call in self.transport.fetch.call_args_list], [FED_NEWS_URL])
        self.assertEqual(self.store.news_status().status, "successful")
        self.assertIsNone(self.store.calendar())

    def test_news_requires_explicit_capability_network_and_persisted_flags(self):
        self.enable()
        for collect, network in ((False, True), (True, False)):
            with self.service(collect, network) as service:
                service.step()
                self.transport.fetch.assert_not_called()
        self.assertIsNone(self.store.news_status())

    def test_five_minute_polling_archives_and_restart_preserves_cadence(self):
        self.enable()
        with self.service() as service:
            service.step()
            self.complete(service)
            status = self.store.news_status()
            self.assertEqual(status.status, "successful")
            self.assertEqual(self.store.news_body(status.acquisition_id), feed())
            self.assertEqual(self.transport.fetch.call_args.args[0], FED_NEWS_URL)
            service.step()
            self.assertEqual(self.transport.fetch.call_count, 1)
        with self.service() as restarted:
            restarted.step()
            self.assertEqual(self.transport.fetch.call_count, 1)
            self.now += timedelta(seconds=300)
            self.elapsed = 300.0
            restarted.step()
            self.complete(restarted)
            self.assertEqual(self.transport.fetch.call_count, 2)
            self.assertEqual(self.store.news_status().revision, 2)

    def test_failure_is_persisted_and_does_not_erase_prior_success(self):
        self.enable()
        with self.service() as service:
            service.step()
            self.complete(service)
            original = self.store.news_status()
            self.now += timedelta(minutes=5)
            self.elapsed = 300.0
            self.transport.fetch.side_effect = RuntimeError("private secret details")
            service.step()
            self.complete(service)
            failed = self.store.news_status()
            self.assertEqual(failed.error_code, "source_unavailable")
            self.assertEqual(failed.last_success_id, original.acquisition_id)
            self.assertEqual(self.store.news_body(failed.last_success_id), feed())
        self.assertEqual(IntelligenceStore(self.path).news_status(), failed)
        with patch("dashboard.intelligence.datetime") as clock:
            clock.now.return_value = self.now
            snapshot = IntelligenceWorkspace(self.path).snapshot()
        self.assertEqual(snapshot["news_last_success_at"], original.available_at.isoformat().replace("+00:00", "Z"))
        self.assertEqual(snapshot["source_status"]["available_at"], failed.available_at.isoformat().replace("+00:00", "Z"))
        self.assertEqual(snapshot["service_status"]["failures"][0]["error_code"], "source_unavailable")
        self.assertNotIn("private secret details", json.dumps(snapshot))

    def test_restart_after_failure_restores_error_and_delays_retry(self):
        self.transport.fetch.side_effect = Exception("not public")
        self.enable()
        with self.service() as service:
            service.step()
            self.complete(service)
        with self.service() as restarted:
            restarted.step()
            self.assertEqual(restarted.provider_errors["fed_monetary"], "source_unavailable")
            self.assertEqual(self.transport.fetch.call_count, 1)
            self.now += timedelta(seconds=299)
            self.elapsed = 299.0
            restarted.step()
            self.assertEqual(self.transport.fetch.call_count, 1)
            self.now += timedelta(seconds=1)
            self.elapsed = 300.0
            restarted.step()
            self.complete(restarted)
            self.assertEqual(self.transport.fetch.call_count, 2)

    def test_restart_reconciles_news_commits_after_status_write_failure(self):
        self.enable()
        self.transport.fetch.side_effect = RuntimeError("private-debug-detail")
        with self.service() as first:
            first.step()
            first.worker.join(timeout=2)
            self.assertFalse(first.worker.is_alive())
            with patch.object(self.store, "observe_service", side_effect=StoreError("storage_unavailable")):
                with self.assertLogs("market_intelligence.service", level="ERROR"):
                    with self.assertRaises(StoreError):
                        first.step()
        self.assertEqual(self.store.news_status().error_code, "source_unavailable")
        with self.service() as second:
            second.step()
            self.assertEqual(self.store.service_status().failures[0].error_code, "source_unavailable")
            self.now += timedelta(seconds=300)
            self.elapsed = 300.0
            self.transport.fetch.side_effect = None
            second.step()
            second.worker.join(timeout=2)
            self.assertFalse(second.worker.is_alive())
            with patch.object(self.store, "observe_service", side_effect=StoreError("storage_unavailable")):
                with self.assertLogs("market_intelligence.service", level="ERROR"):
                    with self.assertRaises(StoreError):
                        second.step()
        self.assertEqual(self.store.news_status().status, "successful")
        with self.service() as third:
            third.step()
            self.assertEqual(self.store.service_status().failures, ())

    def test_unknown_provider_code_is_mapped_to_registered_public_error(self):
        from market_intelligence.news import ProviderError
        self.transport.fetch.side_effect = ProviderError("private_detail_code")
        self.enable()
        with self.service() as service:
            service.step()
            self.complete(service)
            self.assertEqual(self.store.news_status().error_code, "source_unavailable")

    def test_news_and_calendars_share_worker_without_erasing_each_other(self):
        def fetch(url, cancel_event=None):
            return feed() if url == FED_NEWS_URL else calendar()
        self.transport.fetch.side_effect = fetch
        self.store.enqueue(self.request(enabled=True), self.now)
        with self.service() as service:
            service.step()
            self.complete(service)
            self.complete(service)
            original_calendar = self.store.calendar()
            self.assertEqual(len(original_calendar.events), 1)
            self.assertEqual(self.store.news_status().status, "successful")
            self.now += timedelta(minutes=5)
            self.elapsed = 300.0
            service.step()
            self.complete(service)
            self.assertEqual(self.store.news_status().revision, 2)
            self.assertEqual(self.store.calendar(), original_calendar)
            self.assertEqual(self.transport.fetch.call_args.args[0], FED_NEWS_URL)

    def test_disabling_inflight_news_discards_payload_and_status(self):
        started, release = threading.Event(), threading.Event()
        def fetch(url, cancel_event=None):
            started.set()
            release.wait(timeout=2)
            return feed()
        self.transport.fetch.side_effect = fetch
        self.enable()
        with self.service() as service:
            service.step()
            self.assertTrue(started.wait(timeout=1))
            self.store.enqueue(self.request("off", base_revision=1, enabled=False), self.now)
            service.step()
            self.assertTrue(service.worker.is_alive())
            self.assertEqual(self.store.service_status().settings_revision, 2)
            release.set()
            self.complete(service)
            self.assertIsNone(self.store.news_status())
            self.assertEqual(self.store.service_status().settings_revision, 2)
            self.assertEqual(self.store.service_status().failures, ())
            with closing(sqlite3.connect(self.path)) as handle:
                self.assertEqual(handle.execute("SELECT COUNT(*) FROM news_acquisitions").fetchone()[0], 0)


class ServiceDashboardIntegrationTests(IntelligenceStoreFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.elapsed = 0.0
        self.transport = Mock()
        self.transport.fetch.side_effect = lambda url, cancel_event=None: {
            BLS_CALENDAR_URL: calendar(),
            BEA_CALENDAR_URL: calendar(title="Gross Domestic Product", uid="release-bea"),
            FED_NEWS_URL: feed(),
        }[url]

    def service(self, complete, calendar_enabled):
        self.store.enqueue(self.request(
            enabled=True, calendar_enabled=calendar_enabled, news_enabled=True,
            ai_enabled=True, ai_profile="fixture"), self.now)
        provider = JSONSummaryProvider("fixture", "synthetic-model", complete)
        return IntelligenceService(
            self.store, clock=lambda: self.now, monotonic=lambda: self.elapsed,
            allow_network=True, calendar_transport=self.transport, collect_news=True,
            allow_ai=True, ai_providers={"fixture": provider})

    def run_jobs(self, service, count):
        service.step()
        for _ in range(count):
            self.assertIsNotNone(service.worker)
            service.worker.join(timeout=5)
            self.assertFalse(service.worker.is_alive())
            service.step()

    def snapshot(self):
        with patch("dashboard.intelligence.datetime") as clock:
            clock.now.return_value = self.now
            app = create_app(serve_frontend=False, intelligence_database=self.path,
                             analysis_profiles=("fixture",))
            with TestClient(app, base_url="http://127.0.0.1",
                            backend_options={"loop_factory": MemoryEventLoop}) as client:
                response = client.get("/api/v1/intelligence")
        self.assertEqual(response.status_code, 200)
        return response.json()

    def complete(self, request, cancelled):
        projected = json.loads(request.evidence_json)
        self.assertEqual(set(projected), {"instruments", "evidence"})
        item = projected["evidence"][0]
        return ProviderResponse(json.dumps({
            "claims": [{"text": item["snippet"], "evidence_ids": [item["evidence_id"]]}],
            "interpretation": "Synthetic review context.",
            "uncertainties": ["Market reaction is unknown."],
            "symbols": ["SPY"], "recommendation": "watch",
        }), 10)

    def test_dashboard_sorts_calendar_and_reports_truncated_total(self):
        self.store.enqueue(self.request(enabled=True), self.now)
        self.store.apply_pending(self.now)
        source = SourceCoverage(
            "ons", ("inflation",), self.now, self.now + timedelta(minutes=1),
            self.now + timedelta(minutes=102), "healthy")
        events = tuple(
            CalendarEvent(
                f"ons-event-{index:03d}", "ons", "https://www.ons.gov.uk/releases/example",
                "inflation", (), self.now + timedelta(minutes=index + 1), self.now,
                f"Release {index:03d}", source_timezone="UTC")
            for index in range(101)
        ) + (CalendarEvent(
            "ons-unknown-date", "ons", "https://www.ons.gov.uk/releases/unknown",
            "inflation", (), None, self.now, "Unscheduled release", "tentative",
            source_timezone="UTC"),)
        self.store.publish_calendar(CalendarSnapshot(1, self.now, (source,), events), 1, self.now)

        snapshot = self.snapshot()
        self.assertEqual((snapshot["calendar_total"], len(snapshot["calendar"]),
                          snapshot["calendar_truncated"]), (102, 100, True))
        self.assertEqual(snapshot["calendar"][0]["event_id"], "ons-event-000")
        self.assertEqual(snapshot["calendar"][-1]["event_id"], "ons-event-099")

    def test_supervised_analysis_uses_one_completion_and_no_feed_fetch(self):
        provider = Mock(side_effect=self.complete)
        service = self.service(provider, calendar_enabled=True)
        self.store.apply_pending(self.now, profiles=("fixture",))
        batch = parse_fed_news(feed(), self.now)
        self.store.complete_news_attempt("attempt-1", 1, self.now, self.now, self.now,
                                         batch=batch, raw=feed())
        with service:
            result, _ = service.check_once("analysis")
            self.assertEqual(result["status"], "succeeded")
            self.elapsed = 7200.0
            service.step()
        provider.assert_called_once()
        self.transport.fetch.assert_not_called()
        self.assertEqual(self.store.analysis_usage(self.now)["calls"], 1)
        self.assertEqual(len(self.store.recent_briefs()), 1)

    def test_check_expiring_during_reservation_does_not_dispatch_analysis(self):
        provider = Mock(side_effect=self.complete)
        service = self.service(provider, calendar_enabled=True)
        self.store.apply_pending(self.now, profiles=("fixture",))
        batch = parse_fed_news(feed(), self.now)
        self.store.complete_news_attempt("attempt-1", 1, self.now, self.now, self.now,
                                         batch=batch, raw=feed())
        current = [100.0]
        reserve = service.analyzer.budget.reserve_request

        def reserve_then_expire(*args):
            reservation = reserve(*args)
            current[0] = 146.0
            return reservation

        with service, patch.object(service.analyzer.budget, "reserve_request", side_effect=reserve_then_expire), \
             patch("market_intelligence.service.time.monotonic", side_effect=lambda: current[0]), \
             patch("market_intelligence.service.threading.Thread.start", new=lambda worker: worker.run()):
            result, _ = service.check_once("analysis")
        self.assertEqual(result["status"], "interrupted")
        self.assertEqual(self.store.analysis_usage(self.now)["calls"], 1)
        provider.assert_not_called()
        self.transport.fetch.assert_not_called()
        self.assertEqual(self.store.recent_briefs(), ())

    def test_check_limits_busy_storage_retry_to_remaining_deadline(self):
        provider = Mock(side_effect=self.complete)
        service = self.service(provider, calendar_enabled=True)
        self.store.apply_pending(self.now, profiles=("fixture",))
        batch = parse_fed_news(feed(), self.now)
        self.store.complete_news_attempt("attempt-1", 1, self.now, self.now, self.now,
                                         batch=batch, raw=feed())
        current, waits = [100.0], []

        def busy_reservation(*args):
            current[0] = 144.75
            raise StoreBusy()

        def wait_until_deadline(event, timeout):
            waits.append(timeout)
            current[0] = 145.0
            return False

        with service, patch.object(service.analyzer.budget, "reserve_request", side_effect=busy_reservation), \
             patch("market_intelligence.service.time.monotonic", side_effect=lambda: current[0]), \
             patch("market_intelligence.service.threading.Event.wait", new=wait_until_deadline), \
             patch("market_intelligence.service.threading.Thread.start", new=lambda worker: worker.run()):
            result, _ = service.check_once("analysis")
        self.assertEqual(result["status"], "interrupted")
        self.assertEqual(waits, [0.25])
        self.assertEqual(self.store.analysis_usage(self.now)["calls"], 0)
        provider.assert_not_called()
        self.transport.fetch.assert_not_called()

    def test_failed_analysis_check_reports_error_without_masking_prior_failure(self):
        provider = Mock(side_effect=RuntimeError("private-debug-detail"))
        service = self.service(provider, calendar_enabled=True)
        self.store.apply_pending(self.now, profiles=("fixture",))
        batch = parse_fed_news(feed(), self.now)
        self.store.complete_news_attempt("attempt-1", 1, self.now, self.now, self.now,
                                         batch=batch, raw=feed())
        previous = self.store.observe_service(
            "previous", "service-previous", 1, self.now, True, ("bls", "bea"), True, True,
            (ServiceFailure("bea", "source_unavailable", self.now),))
        with service:
            result, _ = service.check_once("analysis")
        self.assertEqual(result, {"source": "analysis", "status": "failed",
                                  "error_code": "provider_unavailable"})
        self.assertEqual(self.store.service_status(), previous)
        self.assertEqual(self.store.analysis_usage(self.now)["calls"], 1)
        self.assertEqual(self.store.recent_briefs(), ())
        self.assertNotIn("private-debug-detail", json.dumps(result))

    def test_mocked_collection_analysis_and_dashboard_round_trip(self):
        provider = Mock(side_effect=self.complete)
        with self.service(provider, calendar_enabled=True) as service:
            self.run_jobs(service, 4)
        snapshot = self.snapshot()
        self.assertEqual((len(snapshot["calendar"]), len(snapshot["news"]), len(snapshot["briefs"])), (2, 1, 1))
        self.assertEqual(snapshot["briefs"][0]["verification"], "unverified")
        self.assertEqual(snapshot["budget"]["calls"], 1)
        self.assertEqual(snapshot["service_status_state"], "recent")
        self.assertTrue(snapshot["service_status"]["network_permitted"])
        self.assertEqual(snapshot["service_status"]["failures"], [])
        recorded_at = self.now.isoformat().replace("+00:00", "Z")
        self.assertEqual(snapshot["news_last_success_at"], recorded_at)
        self.assertEqual({source["source"]: source["checked_at"] for source in snapshot["calendar_sources"]},
                         {"bls": recorded_at, "bea": recorded_at})
        self.assertEqual(self.transport.fetch.call_count, 3)
        provider.assert_called_once()

    def test_dashboard_clock_covers_evidence_and_rejects_clock_rollback(self):
        with self.service(Mock(side_effect=self.complete), calendar_enabled=False) as service:
            self.run_jobs(service, 2)
        workspace = IntelligenceWorkspace(self.path, profiles=("fixture",))
        with patch("dashboard.intelligence.datetime") as clock:
            clock.now.side_effect = (self.now - timedelta(seconds=1), self.now)
            snapshot = workspace.snapshot()
        self.assertEqual(snapshot["generated_at"], self.now.isoformat().replace("+00:00", "Z"))
        with patch("dashboard.intelligence.datetime") as clock:
            clock.now.return_value = self.now - timedelta(seconds=1)
            with self.assertRaises(StoreError) as result:
                workspace.snapshot()
        self.assertEqual(result.exception.code, "clock_invalid")

    def test_dashboard_pins_settings_through_snapshot_generation(self):
        with self.service(Mock(side_effect=self.complete), calendar_enabled=False) as service:
            self.run_jobs(service, 2)
        self.store.enqueue(self.request("change", base_revision=1, enabled=True, news_enabled=False), self.now)
        workspace = IntelligenceWorkspace(self.path, profiles=("fixture",))
        attempted = []
        calls = 0

        def clock_now(zone):
            nonlocal calls
            calls += 1
            if calls == 2:
                try:
                    self.store.apply_pending(self.now)
                except StoreError as exc:
                    self.assertEqual(exc.code, "storage_unavailable")
                    attempted.append(False)
                else:
                    attempted.append(True)
            return self.now

        with patch("dashboard.intelligence.datetime") as clock:
            clock.now.side_effect = clock_now
            snapshot = workspace.snapshot()
        self.assertEqual(attempted, [False])
        self.assertEqual(snapshot["settings"]["revision"], self.store.settings().revision)
        self.assertEqual(snapshot["service_status_state"], "recent")
        self.assertEqual(self.store.receipt("change").status, "pending")
        self.assertEqual(self.store.apply_pending(self.now)[0].status, "applied")

    def test_service_retries_control_after_dashboard_read_lock_is_released(self):
        self.store.enqueue(self.request("change", enabled=True), self.now)
        workspace = IntelligenceWorkspace(self.path)
        snapshot_locked, release_snapshot = threading.Event(), threading.Event()
        attempted, applied, stop = threading.Event(), threading.Event(), threading.Event()
        failures = []
        calls = 0

        def clock_now(zone):
            nonlocal calls
            calls += 1
            if calls == 2:
                snapshot_locked.set()
                if not release_snapshot.wait(timeout=5):
                    raise AssertionError("Snapshot was not released")
            return self.now

        original_apply = self.store.apply_pending

        def apply_pending(*args, **kwargs):
            try:
                receipts = original_apply(*args, **kwargs)
                if any(receipt.status == "applied" for receipt in receipts):
                    applied.set()
                return receipts
            finally:
                attempted.set()

        def snapshot():
            try:
                workspace.snapshot()
            except Exception as exc:
                failures.append(exc)

        with IntelligenceService(self.store, clock=lambda: self.now) as service:
            def run():
                try:
                    service.run(stop)
                except Exception as exc:
                    failures.append(exc)

            snapshot_thread = threading.Thread(target=snapshot)
            service_thread = threading.Thread(target=run)
            with patch("dashboard.intelligence.datetime") as clock, patch.object(
                    self.store, "apply_pending", side_effect=apply_pending):
                clock.now.side_effect = clock_now
                snapshot_thread.start()
                try:
                    self.assertTrue(snapshot_locked.wait(timeout=2))
                    service_thread.start()
                    self.assertTrue(attempted.wait(timeout=2))
                    release_snapshot.set()
                    self.assertTrue(applied.wait(timeout=4), "Service did not retry the pending control")
                finally:
                    release_snapshot.set()
                    stop.set()
                    snapshot_thread.join(timeout=5)
                    if service_thread.ident is not None:
                        service_thread.join(timeout=5)
        self.assertFalse(snapshot_thread.is_alive())
        self.assertFalse(service_thread.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(self.store.receipt("change").status, "applied")
        self.assertEqual(self.store.service_status().settings_revision, 1)

    def test_completed_analysis_survives_a_busy_publication(self):
        provider = Mock(side_effect=self.complete)
        with self.service(provider, calendar_enabled=False) as service:
            self.run_jobs(service, 1)
            service.worker.join(timeout=5)
            self.assertFalse(service.worker.is_alive())
            publish = self.store.publish_brief
            attempts = 0

            def publish_after_contention(*args):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise StoreBusy()
                return publish(*args)

            with patch.object(self.store, "publish_brief", side_effect=publish_after_contention):
                with self.assertRaises(StoreBusy):
                    service.step()
                self.assertEqual(self.store.recent_briefs(), ())
                service.step()
            self.assertEqual(attempts, 2)
            self.assertEqual(len(self.store.recent_briefs()), 1)
            self.assertEqual(self.store.service_status().failures, ())
        provider.assert_called_once()

    def assert_analysis_retries_store_contention(self, phase):
        provider_finished = threading.Event()
        attempts = []
        original_reserve = self.store.reserve_analysis
        original_settings = self.store.settings

        def complete(request, cancelled):
            provider_finished.set()
            return self.complete(request, cancelled)

        def reserve(*args):
            if phase == "reservation" and not attempts:
                attempts.append(phase)
                raise StoreBusy()
            return original_reserve(*args)

        def settings():
            if (phase == "post-provider" and provider_finished.is_set() and not attempts
                    and threading.current_thread().name == "intelligence-analysis"):
                attempts.append(phase)
                raise StoreBusy()
            return original_settings()

        provider = Mock(side_effect=complete)
        with patch.object(self.store, "reserve_analysis", side_effect=reserve), patch.object(
                self.store, "settings", side_effect=settings):
            with self.service(provider, calendar_enabled=False) as service:
                self.run_jobs(service, 2)
        self.assertEqual(attempts, [phase])
        provider.assert_called_once()
        self.assertEqual(len(self.store.recent_briefs()), 1)
        self.assertEqual(self.store.analysis_usage(self.now)["calls"], 1)
        self.assertEqual(self.store.service_status().failures, ())

    def test_analysis_retries_busy_reservation_without_repeating_provider_call(self):
        self.assert_analysis_retries_store_contention("reservation")

    def test_analysis_retries_busy_post_provider_validation_without_repeating_provider_call(self):
        self.assert_analysis_retries_store_contention("post-provider")

    def test_analysis_rechecks_source_age_after_busy_pre_dispatch_validation(self):
        reserved = threading.Event()
        original_reserve = self.store.reserve_analysis
        original_settings = self.store.settings
        attempts = []

        def reserve(*args):
            result = original_reserve(*args)
            reserved.set()
            return result

        def settings():
            if (reserved.is_set() and not attempts
                    and threading.current_thread().name == "intelligence-analysis"):
                attempts.append("busy")
                self.now += timedelta(seconds=901)
                raise StoreBusy()
            return original_settings()

        provider = Mock(side_effect=self.complete)
        with patch.object(self.store, "reserve_analysis", side_effect=reserve), patch.object(
                self.store, "settings", side_effect=settings):
            with self.service(provider, calendar_enabled=False) as service:
                self.run_jobs(service, 2)
        self.assertEqual(attempts, ["busy"])
        provider.assert_not_called()
        self.assertEqual(self.store.analysis_usage(self.now)["calls"], 1)
        self.assertEqual(self.store.recent_briefs(), ())
        self.assertEqual(self.store.service_status().failures[0].error_code, "source_unavailable")

    def test_disabling_interrupts_analysis_storage_retry(self):
        attempted = threading.Event()

        def reserve(*args):
            attempted.set()
            raise StoreBusy()

        provider = Mock(side_effect=self.complete)
        with patch.object(self.store, "reserve_analysis", side_effect=reserve):
            with self.service(provider, calendar_enabled=False) as service:
                self.run_jobs(service, 1)
                self.assertTrue(attempted.wait(timeout=2))
                self.store.enqueue(self.request("off", base_revision=1, enabled=False), self.now)
                worker = service.worker
                service.step()
                worker.join(timeout=2)
                self.assertFalse(worker.is_alive())
                service.step()
        provider.assert_not_called()
        self.assertEqual(self.store.analysis_usage(self.now)["calls"], 0)
        self.assertEqual(self.store.service_status().settings_revision, 2)
        self.assertEqual(self.store.service_status().failures, ())

    def test_busy_status_retry_preserves_already_committed_control(self):
        self.store.enqueue(self.request("change", enabled=True), self.now)
        stop = Mock()
        stop.is_set.side_effect = (False, False, True)
        observe = self.store.observe_service
        attempts = 0

        def observe_after_contention(*args):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise StoreBusy()
            return observe(*args)

        with IntelligenceService(self.store, clock=lambda: self.now) as service:
            with patch.object(self.store, "observe_service", side_effect=observe_after_contention):
                with self.assertLogs("market_intelligence.service", level="WARNING") as logs:
                    service.run(stop)
        self.assertTrue(any("retrying" in entry for entry in logs.output))
        self.assertEqual(attempts, 2)
        self.assertEqual(self.store.receipt("change").status, "applied")
        self.assertEqual(self.store.settings().revision, 1)
        self.assertEqual(self.store.service_status().revision, 1)

    def test_service_does_not_retry_other_storage_failures(self):
        stop = threading.Event()

        def fail(*args, **kwargs):
            stop.set()
            raise StoreError("storage_invalid")

        with IntelligenceService(self.store, clock=lambda: self.now) as service:
            with patch.object(self.store, "apply_pending", side_effect=fail):
                with self.assertRaises(StoreError) as result:
                    service.run(stop)
        self.assertEqual(result.exception.code, "storage_invalid")

    def test_sqlite_busy_is_identified_without_python_311_exception_attributes(self):
        with patch("market_intelligence.store.sqlite3.connect",
                   side_effect=sqlite3.OperationalError("database is locked")):
            with self.assertRaises(StoreBusy) as result:
                self.store.settings()
        self.assertEqual(result.exception.code, "storage_unavailable")

    def test_failed_analysis_is_persisted_sanitized_and_eventually_stale(self):
        provider = Mock(side_effect=RuntimeError("private-debug-detail"))
        with self.service(provider, calendar_enabled=False) as service:
            self.run_jobs(service, 2)
        snapshot = self.snapshot()
        self.assertEqual(snapshot["service_status_state"], "recent")
        failures = snapshot["service_status"]["failures"]
        self.assertEqual([(item["source"], item["error_code"]) for item in failures],
                         [("analysis", "provider_unavailable")])
        self.assertNotIn("private-debug-detail", json.dumps(snapshot))
        self.assertEqual(snapshot["briefs"], [])
        self.assertEqual(snapshot["budget"]["calls"], 1)

        self.now += timedelta(seconds=601)
        self.assertEqual(self.snapshot()["service_status_state"], "stale")

    def test_restart_retains_failure_until_a_successful_analysis(self):
        with self.service(Mock(side_effect=RuntimeError("private-detail")), calendar_enabled=False) as service:
            self.run_jobs(service, 2)
        provider = JSONSummaryProvider("fixture", "synthetic-model", self.complete)
        with IntelligenceService(self.store, clock=lambda: self.now, monotonic=lambda: self.elapsed,
                                 allow_network=True, calendar_transport=self.transport, collect_news=True,
                                 allow_ai=True, ai_providers={"fixture": provider}) as restarted:
            restarted.step()
            self.assertIsNotNone(restarted.worker)
            restarted.worker.join(timeout=5)
            self.assertFalse(restarted.worker.is_alive())
            self.assertEqual(self.store.service_status().failures[0].error_code, "provider_unavailable")
            restarted.step()
        self.assertEqual(self.store.service_status().failures, ())
        self.assertEqual(len(self.store.recent_briefs()), 1)

    def test_unchanged_news_reuses_the_committed_brief_after_a_new_acquisition(self):
        provider = Mock(side_effect=self.complete)
        with self.service(provider, calendar_enabled=False) as service:
            self.run_jobs(service, 2)
            original = self.store.recent_briefs()
            self.now += timedelta(seconds=300)
            self.elapsed = 300.0
            self.run_jobs(service, 2)
        self.assertEqual(self.store.recent_briefs(), original)
        self.assertEqual(self.store.analysis_usage(self.now)["calls"], 1)
        self.assertEqual(self.store.service_status().failures, ())
        provider.assert_called_once()

    def test_cached_brief_does_not_erase_a_later_analysis_failure(self):
        second = feed(description="Another policy release.",
                      link="https://www.federalreserve.gov/newsevents/pressreleases/monetary20261113b.htm")
        item = second.split(b"<item>", 1)[1].split(b"</item>", 1)[0]
        self.transport.fetch.side_effect = lambda url, cancel_event=None: feed().replace(
            b"</channel>", b"<item>" + item + b"</item></channel>")

        def completion(request, cancelled):
            if provider.call_count == 1:
                return self.complete(request, cancelled)
            raise RuntimeError("private-debug-detail")

        provider = Mock(side_effect=completion)
        with self.service(provider, calendar_enabled=False) as service:
            self.run_jobs(service, 2)
            service.worker.join(timeout=5)
            self.assertFalse(service.worker.is_alive())
            service.step()
            self.assertEqual(self.store.service_status().failures[0].error_code, "provider_unavailable")
            self.now += timedelta(seconds=300)
            self.elapsed = 300.0
            self.run_jobs(service, 1)
            service.worker.join(timeout=5)
            self.assertFalse(service.worker.is_alive())
            self.assertEqual(provider.call_count, 2)
            service.step()
            try:
                self.assertEqual(self.store.service_status().failures[0].error_code, "provider_unavailable")
            finally:
                if service.worker is not None:
                    service.worker.join(timeout=5)
                    self.assertFalse(service.worker.is_alive())

    def test_unpublished_cached_result_does_not_erase_later_failure_after_reentry(self):
        second = feed(description="Another policy release.",
                      link="https://www.federalreserve.gov/newsevents/pressreleases/monetary20261113b.htm")
        item = second.split(b"<item>", 1)[1].split(b"</item>", 1)[0]
        self.transport.fetch.side_effect = lambda url, cancel_event=None: feed().replace(
            b"</channel>", b"<item>" + item + b"</item></channel>")

        def completion(request, cancelled):
            if provider.call_count == 1:
                return self.complete(request, cancelled)
            raise RuntimeError("private-debug-detail")

        provider = Mock(side_effect=completion)
        service = self.service(provider, calendar_enabled=False)
        with service:
            self.run_jobs(service, 1)
            service.worker.join(timeout=5)
            self.assertFalse(service.worker.is_alive())
        self.assertEqual(self.store.recent_briefs(), ())

        self.now += timedelta(seconds=1)
        self.elapsed = 2.0
        with service:
            service.step()
            service.worker.join(timeout=5)
            self.assertFalse(service.worker.is_alive())
            service.step()
            self.assertEqual(self.store.service_status().failures[0].error_code, "provider_unavailable")
            self.now += timedelta(seconds=299)
            self.elapsed = 300.0
            service.step()
            service.worker.join(timeout=5)
            self.assertFalse(service.worker.is_alive())
            self.assertEqual(provider.call_count, 2)
            service.step()
            self.assertEqual(len(self.store.recent_briefs()), 1)
            self.assertEqual(self.store.service_status().failures[0].error_code, "provider_unavailable")

    def test_clock_rollback_cannot_make_old_cached_analysis_clear_failure(self):
        second = feed(description="Another policy release.",
                      link="https://www.federalreserve.gov/newsevents/pressreleases/monetary20261113b.htm")
        item = second.split(b"<item>", 1)[1].split(b"</item>", 1)[0]
        self.transport.fetch.side_effect = lambda url, cancel_event=None: feed().replace(
            b"</channel>", b"<item>" + item + b"</item></channel>")
        started, release = threading.Event(), threading.Event()
        original_time = self.now

        def completion(request, cancelled):
            if provider.call_count == 1:
                started.set()
                if not release.wait(timeout=5):
                    raise AssertionError("Provider was not released")
                return self.complete(request, cancelled)
            raise RuntimeError("private-debug-detail")

        provider = Mock(side_effect=completion)
        service = self.service(provider, calendar_enabled=False)
        try:
            with service:
                self.run_jobs(service, 1)
                self.assertTrue(started.wait(timeout=2))
                self.now = original_time + timedelta(seconds=10)
                release.set()
                service.worker.join(timeout=5)
                self.assertFalse(service.worker.is_alive())
        finally:
            release.set()
        self.now = original_time + timedelta(seconds=5)
        self.elapsed = 10.0
        with service:
            service.step()
            service.worker.join(timeout=5)
            self.assertFalse(service.worker.is_alive())
            service.step()
            self.assertEqual(self.store.service_status().failures[0].error_code, "provider_unavailable")
            self.now = original_time + timedelta(seconds=300)
            self.elapsed = 300.0
            service.step()
            service.worker.join(timeout=5)
            self.assertFalse(service.worker.is_alive())
            provider.assert_called()
            self.assertEqual(provider.call_count, 2)
            service.step()
            self.assertEqual(len(self.store.recent_briefs()), 1)
            self.assertEqual(self.store.service_status().failures[0].error_code, "provider_unavailable")

    def test_committed_disable_cancels_worker_even_if_settings_read_is_busy(self):
        started, release, dispatched = threading.Event(), threading.Event(), threading.Event()

        def complete(request, cancelled):
            started.set()
            if not release.wait(timeout=5):
                raise AssertionError("Provider was not released")
            if not cancelled.is_set():
                dispatched.set()
            return self.complete(request, cancelled)

        service = self.service(Mock(side_effect=complete), calendar_enabled=False)
        try:
            with service:
                self.run_jobs(service, 1)
                self.assertTrue(started.wait(timeout=2))
                self.store.enqueue(self.request("off", base_revision=1, enabled=False), self.now)
                with patch.object(self.store, "settings", side_effect=StoreBusy()):
                    with self.assertRaises(StoreBusy):
                        service.step()
                self.assertEqual(self.store.receipt("off").status, "applied")
                release.set()
                service.worker.join(timeout=5)
                self.assertFalse(service.worker.is_alive())
                self.assertFalse(dispatched.is_set())
        finally:
            release.set()
            if service.worker is not None:
                service.worker.join(timeout=5)

    def test_unknown_calendar_error_is_reported_without_private_details(self):
        self.store.enqueue(self.request(enabled=True, news_enabled=False), self.now)
        self.transport.fetch.side_effect = ProviderError("private-debug-detail")
        with IntelligenceService(self.store, clock=lambda: self.now, monotonic=lambda: self.elapsed,
                                 allow_network=True, calendar_transport=self.transport,
                                 calendar_sources=("bls",)) as service:
            self.run_jobs(service, 1)
        snapshot = self.snapshot()
        self.assertEqual([(item["source"], item["error_code"]) for item in snapshot["service_status"]["failures"]],
                         [("bls", "source_unavailable")])
        self.assertNotIn("private-debug-detail", json.dumps(snapshot))

    def test_disabling_inflight_analysis_discards_its_result_and_failure(self):
        started, release = threading.Event(), threading.Event()

        def delayed(request, cancelled):
            started.set()
            release.wait(timeout=5)
            return self.complete(request, cancelled)

        try:
            with self.service(delayed, calendar_enabled=False) as service:
                service.step()
                service.worker.join(timeout=5)
                self.assertFalse(service.worker.is_alive())
                service.step()
                self.assertTrue(started.wait(timeout=5))
                self.store.enqueue(self.request("off", base_revision=1, enabled=False), self.now)
                service.step()
                release.set()
                service.worker.join(timeout=5)
                self.assertFalse(service.worker.is_alive())
                service.step()
        finally:
            release.set()
        self.assertEqual(self.store.service_status().settings_revision, 2)
        self.assertEqual(self.store.service_status().failures, ())
        self.assertEqual(self.store.recent_briefs(), ())
        self.assertEqual(self.store.analysis_usage(self.now)["calls"], 1)


if __name__ == "__main__":
    unittest.main()