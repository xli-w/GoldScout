from dataclasses import FrozenInstanceError, asdict, replace
from datetime import datetime, timedelta, timezone
import json
import importlib.util
from pathlib import Path
import threading
import unittest
from unittest.mock import Mock

from market_intelligence.analysis import (
    AdvisoryAnalyzer, AnalysisError, AnalysisLimits, DailyAnalysisBudget, JSONSummaryProvider, ProviderResponse,
)
from market_intelligence.contracts import IntelligenceSettings, NewsEvidence
from market_intelligence.news import ArchivedNews, FED_NEWS_URL, NewsBatch, NewsObservation


class AdvisoryAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
        self.item = NewsEvidence("evidence-1", "fed_monetary", "federal-reserve",
                                 "https://www.federalreserve.gov/newsevents/pressreleases/monetary20261113a.htm",
                                 "Federal Reserve statement", "The policy rate is unchanged.", None, self.now,
                                 "a" * 64, "cluster-1")
        batch = NewsBatch("fed_monetary", FED_NEWS_URL, self.now, "b" * 64, (self.item,))
        self.archive = ArchivedNews("attempt-1", 1, 1, self.now, batch)
        self.status = NewsObservation("attempt-1", 1, 1, self.now, self.now, self.now,
                                      "successful", None, "attempt-1", "attempt-1")
        self.settings = IntelligenceSettings(enabled=True, ai_enabled=True, ai_profile="offline", revision=2)
        self.output = {"claims": [{"text": "The policy rate is unchanged.", "evidence_ids": ["evidence-1"]}],
                       "interpretation": "Review potential volatility around the statement.",
                       "uncertainties": ["Future market direction remains uncertain."],
                       "symbols": ["SPY", "QQQ"], "recommendation": "watch"}
        self.callback = Mock(return_value=ProviderResponse(json.dumps(self.output), 100))
        self.provider = JSONSummaryProvider("offline", "fixture-model", self.callback)
        self.analyzer = AdvisoryAnalyzer({"offline": self.provider}, clock=lambda: self.now, monotonic=lambda: 0.0)

    def analyze(self, analyzer=None, cancelled=None):
        return (analyzer or self.analyzer).analyze(self.archive, self.settings, "cluster-1",
                                                  lambda: self.settings, lambda: self.status, cancelled)

    def test_default_off_does_not_read_sources_or_call_provider(self):
        settings = IntelligenceSettings()
        reader = Mock(side_effect=AssertionError("Disabled analysis must not read"))
        self.assertIsNone(self.analyzer.analyze(None, settings, "cluster-1", reader, reader))
        self.callback.assert_not_called()
        self.assertEqual(self.analyzer.budget.calls, 0)

    def test_valid_output_retains_quotes_and_server_owned_unverified_provenance(self):
        brief = self.analyze()
        self.assertEqual(brief.verification, "unverified")
        self.assertEqual(brief.provider, "offline")
        self.assertEqual(brief.settings_revision, 2)
        self.assertEqual(brief.available_at, self.now)
        self.assertEqual(brief.claims[0].text, self.item.snippet)
        request = self.callback.call_args.args[0]
        projected = json.loads(request.evidence_json)
        self.assertEqual(set(projected), {"instruments", "evidence"})
        self.assertNotIn("received_at", projected["evidence"][0])
        self.assertGreater(request.max_output_tokens, 0)

    def test_cache_reuses_unchanged_evidence_without_spending_budget(self):
        original = self.analyze()
        self.assertEqual(self.analyze(), original)
        self.callback.assert_called_once()
        self.assertEqual(self.analyzer.budget.calls, 1)

    def test_unsupported_claims_references_orders_and_extra_metadata_are_rejected(self):
        invalid = [dict(self.output, recommendation="buy"), dict(self.output, verification="confirmed"),
                   dict(self.output, claims=[{"text": "Rates increased.", "evidence_ids": ["evidence-1"]}]),
                   dict(self.output, claims=[{"text": self.item.snippet, "evidence_ids": ["unknown"]}]),
                   dict(self.output, uncertainties=[])]
        for output in invalid:
            with self.subTest(output=output):
                callback = Mock(return_value=ProviderResponse(json.dumps(output)))
                analyzer = AdvisoryAnalyzer({"offline": JSONSummaryProvider("offline", "fixture-model", callback)},
                                            clock=lambda: self.now, monotonic=lambda: 0.0)
                with self.assertRaises(AnalysisError) as result:
                    self.analyze(analyzer)
                self.assertEqual(result.exception.code, "output_invalid")
                self.assertEqual(analyzer.budget.calls, 1)

    def test_stale_failed_or_future_source_does_not_call_provider(self):
        original = self.status
        for status in (replace(original, status="unavailable", error_code="source_unavailable", acquisition_id=None),
                       replace(original, available_at=self.now + timedelta(seconds=1))):
            self.status = status
            with self.assertRaises(AnalysisError):
                self.analyze()
        self.status = original
        self.now += timedelta(seconds=901)
        with self.assertRaises(AnalysisError):
            self.analyze()
        self.callback.assert_not_called()

    def test_settings_changes_or_cancellation_discard_completed_output(self):
        original = self.settings
        def complete(request, cancelled):
            self.settings = replace(original, revision=3)
            return ProviderResponse(json.dumps(self.output))
        self.callback.side_effect = complete
        with self.assertRaises(AnalysisError) as result:
            self.analyze()
        self.assertEqual(result.exception.code, "settings_changed")
        self.settings = original
        cancelled = threading.Event()
        self.callback.side_effect = lambda request, event: (event.set(), ProviderResponse(json.dumps(self.output)))[1]
        with self.assertRaises(AnalysisError) as result:
            self.analyze(cancelled=cancelled)
        self.assertEqual(result.exception.code, "analysis_cancelled")

    def test_daily_budget_counts_failures_and_resets_only_on_forward_utc_day(self):
        limits = AnalysisLimits(daily_calls=1)
        budget = DailyAnalysisBudget(limits)
        budget.reserve(self.now, 100)
        with self.assertRaises(AnalysisError):
            budget.reserve(self.now, 100)
        with self.assertRaises(AnalysisError):
            budget.reserve(self.now - timedelta(seconds=1), 100)
        budget.reserve(self.now + timedelta(days=1), 100)
        self.assertEqual(budget.calls, 1)

    def test_failed_forward_day_reservation_cannot_reopen_previous_day(self):
        budget = DailyAnalysisBudget(AnalysisLimits(daily_calls=1, daily_input_bytes=100))
        budget.reserve(self.now, 100)
        with self.assertRaises(AnalysisError):
            budget.reserve(self.now + timedelta(days=1), 101)
        with self.assertRaises(AnalysisError) as result:
            budget.reserve(self.now, 100)
        self.assertEqual(result.exception.code, "clock_invalid")

    def test_cache_expiring_during_guard_is_regenerated_not_returned(self):
        analyzer = AdvisoryAnalyzer({"offline": self.provider}, limits=AnalysisLimits(brief_ttl_seconds=10),
                                    clock=lambda: self.now, monotonic=lambda: 0.0)
        original = self.analyze(analyzer)
        readings = iter((self.now + timedelta(seconds=9), self.now + timedelta(seconds=10)))
        analyzer.clock = lambda: next(readings, self.now + timedelta(seconds=10))
        current = self.analyze(analyzer)
        self.assertGreaterEqual(current.available_at, original.expires_at)
        self.assertEqual(self.callback.call_count, 2)
        self.assertNotEqual(current.brief_id, original.brief_id)

    def test_settings_change_during_reservation_prevents_dispatch(self):
        reserve = self.analyzer.budget.reserve
        def change_settings(now, size):
            reserve(now, size)
            self.settings = replace(self.settings, revision=3)
        self.analyzer.budget.reserve = change_settings
        with self.assertRaises(AnalysisError) as result:
            self.analyze()
        self.assertEqual(result.exception.code, "settings_changed")
        self.callback.assert_not_called()

    def test_late_provider_output_is_discarded_without_refunding_reservation(self):
        self.analyzer.monotonic = Mock(side_effect=(0.0, 31.0))
        with self.assertRaises(AnalysisError) as result:
            self.analyze()
        self.assertEqual(result.exception.code, "analysis_expired")
        self.assertEqual(self.analyzer.budget.calls, 1)
        self.assertEqual(self.analyzer.cache, {})

    def test_provider_can_be_swapped_only_to_registered_profile(self):
        other = JSONSummaryProvider("other", "other-model", Mock(return_value=ProviderResponse(json.dumps(self.output))))
        analyzer = AdvisoryAnalyzer({"offline": self.provider, "other": other}, clock=lambda: self.now, monotonic=lambda: 0.0)
        self.settings = replace(self.settings, ai_profile="other", revision=3)
        self.assertEqual(self.analyze(analyzer).model, "other-model")
        self.callback.assert_not_called()
        self.settings = replace(self.settings, ai_profile="unregistered", revision=4)
        with self.assertRaises(AnalysisError):
            self.analyze(analyzer)

    def test_duplicate_json_fields_and_nonfinite_values_are_rejected(self):
        for output in ('{"claims":[],"claims":[]}', '{"claims":NaN}'):
            self.callback.return_value = ProviderResponse(output)
            with self.assertRaises(AnalysisError):
                self.analyze()

    def test_pre_cancelled_analysis_does_not_spend_or_invoke(self):
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(AnalysisError):
            self.analyze(cancelled=cancelled)
        self.callback.assert_not_called()
        self.assertEqual(self.analyzer.budget.calls, 0)

    def test_provider_metadata_is_frozen_and_replacement_discards_output(self):
        with self.assertRaises(FrozenInstanceError):
            self.provider.model = "changed-model"
        def complete(request, cancelled):
            self.analyzer.providers["offline"] = JSONSummaryProvider("offline", "replacement", self.callback)
            return ProviderResponse(json.dumps(self.output))
        self.callback.side_effect = complete
        with self.assertRaises(AnalysisError) as result:
            self.analyze()
        self.assertEqual(result.exception.code, "profile_changed")

    def test_limits_block_input_and_output_before_provider_or_cache_acceptance(self):
        limits = AnalysisLimits(max_input_bytes=1)
        analyzer = AdvisoryAnalyzer({"offline": self.provider}, limits=limits, clock=lambda: self.now)
        with self.assertRaises(AnalysisError) as result:
            self.analyze(analyzer)
        self.assertEqual(result.exception.code, "input_too_large")
        self.callback.assert_not_called()
        with self.assertRaises(ValueError):
            ProviderResponse("x" * 32769)

    def test_brief_matches_existing_wire_schema(self):
        if importlib.util.find_spec("jsonschema") is None:
            self.skipTest("Install dashboard development dependencies for wire validation")
        from jsonschema import Draft202012Validator, FormatChecker
        brief = self.analyze()
        payload = json.loads(json.dumps(asdict(brief), default=lambda value: value.isoformat().replace("+00:00", "Z")))
        schema = json.loads((Path(__file__).resolve().parents[1] / "market_intelligence" / "schema.json").read_text())
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(payload)


if __name__ == "__main__":
    unittest.main()