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

from market_intelligence.news import NewsObservation, parse_fed_news
from market_intelligence.store import IntelligenceStore, StoreError
from market_intelligence.store import encode
from market_intelligence.__main__ import main
from market_intelligence.service import IntelligenceService
from market_intelligence.news import FED_NEWS_URL
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
            release.set()
            self.complete(service)
            self.assertIsNone(self.store.news_status())
            with closing(sqlite3.connect(self.path)) as handle:
                self.assertEqual(handle.execute("SELECT COUNT(*) FROM news_acquisitions").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()