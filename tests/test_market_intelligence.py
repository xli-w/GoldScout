import json
from pathlib import Path
import unittest
from dataclasses import FrozenInstanceError, asdict, replace
from datetime import datetime, timedelta, timezone

from market_intelligence.contracts import (
    CalendarEvent, CalendarSnapshot, ControlReceipt, ControlRequest, IntelligenceSettings, SourceCoverage, utc_time,
)
from market_intelligence.policy import CalendarPolicy, evaluate_calendar
from market_intelligence.store import construct


try:
    from jsonschema import Draft202012Validator, FormatChecker
except ModuleNotFoundError:
    Draft202012Validator = None
    FormatChecker = None


class CalendarFixture:
    def setUp(self):
        self.now = datetime(2026, 11, 13, 13, 30, tzinfo=timezone.utc)
        self.settings = IntelligenceSettings(enabled=True, mode="shadow", revision=2)
        self.policy = CalendarPolicy(categories=("inflation",))
        self.source = SourceCoverage(
            "bls", ("inflation",), self.now - timedelta(hours=1),
            self.now - timedelta(days=1), self.now + timedelta(days=30), "healthy")
        self.event = CalendarEvent(
            "cpi-2026-11", "bls", "https://www.bls.gov/schedule/", "inflation",
            ("SPY", "QQQ", "BTC/USD", "GLD"), self.now, self.now - timedelta(hours=1),
            "Consumer Price Index")
        self.snapshot = CalendarSnapshot(3, self.now - timedelta(hours=1),
                                         (self.source,), (self.event,))

    def decision(self, now=None, snapshot=None, settings=None, symbol="SPY", action="enter_long"):
        return evaluate_calendar(settings or self.settings, snapshot or self.snapshot,
                                 symbol, action, now or self.now, self.policy)


class CalendarShadowTests(CalendarFixture, unittest.TestCase):
    def test_default_is_off_and_has_no_trading_authority(self):
        result = self.decision(settings=IntelligenceSettings())
        self.assertEqual(result.outcome, "disabled")
        self.assertFalse(result.affects_trading)

    def test_modes_cannot_enable_an_execution_gate(self):
        for mode, outcome in (("advisory", "advisory"), ("shadow", "would_block")):
            with self.subTest(mode=mode):
                result = self.decision(settings=replace(self.settings, mode=mode))
                self.assertEqual(result.outcome, outcome)
                self.assertFalse(result.affects_trading)
        for mode in ("entry_gate", "live", "deterministic", ""):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                replace(self.settings, mode=mode)

    def test_exits_and_holds_are_never_restricted_even_without_data(self):
        for action in ("exit", "hold"):
            result = evaluate_calendar(self.settings, None, "SPY", action, self.now, self.policy)
            self.assertEqual(result.outcome, "not_applicable")
            self.assertFalse(result.affects_trading)

    def test_window_is_start_inclusive_end_exclusive(self):
        for offset, outcome in ((-901, "would_allow"), (-900, "would_block"),
                                (0, "would_block"), (1799, "would_block"), (1800, "would_allow")):
            with self.subTest(offset=offset):
                result = self.decision(now=self.now + timedelta(seconds=offset))
                self.assertEqual(result.outcome, outcome)
                self.assertFalse(result.affects_trading)
        self.assertEqual([(reference.source, reference.event_id) for reference in self.decision().event_refs],
                 [("bls", "cpi-2026-11")])
        self.assertEqual(self.decision().settings_revision, 2)
        self.assertEqual(self.decision().snapshot_revision, 3)

    def test_only_affected_instruments_and_entries_are_evaluated(self):
        self.assertEqual(self.decision(symbol="USO").outcome, "would_allow")
        self.assertEqual(self.decision(action="enter_short").outcome, "would_block")
        with self.assertRaises(ValueError):
            self.decision(symbol="UNKNOWN")

    def test_cancelled_and_rescheduled_events_use_current_revision(self):
        cancelled = replace(self.event, status="cancelled", revision=1)
        self.assertEqual(self.decision(snapshot=replace(self.snapshot, events=(cancelled,))).outcome, "would_allow")
        later = replace(self.event, scheduled_at=self.now + timedelta(days=1), revision=2)
        self.assertEqual(self.decision(snapshot=replace(self.snapshot, events=(later,))).outcome, "would_allow")
        with self.assertRaises(ValueError):
            replace(self.snapshot, events=(self.event, later))

    def test_unknown_time_and_tentative_dates_are_not_invented(self):
        for event in (replace(self.event, scheduled_at=None), replace(self.event, status="tentative")):
            self.assertEqual(self.decision(snapshot=replace(self.snapshot, events=(event,))).outcome, "unknown")

    def test_empty_calendar_requires_positive_coverage_evidence(self):
        self.assertEqual(self.decision(snapshot=replace(self.snapshot, events=())).outcome, "would_allow")
        self.assertEqual(self.decision(snapshot=replace(self.snapshot, events=(), sources=())).outcome, "unknown")
        missing = evaluate_calendar(self.settings, None, "SPY", "enter_long", self.now, self.policy)
        self.assertEqual(missing.outcome, "unknown")

    def test_stale_invalid_and_incomplete_sources_are_unknown(self):
        sources = (
            replace(self.source, checked_at=self.now - timedelta(seconds=7201)),
            replace(self.source, status="invalid"),
            replace(self.source, status="unavailable"),
            replace(self.source, coverage_start=self.now),
            replace(self.source, coverage_end=self.now + timedelta(minutes=15)),
        )
        for source in sources:
            with self.subTest(source=source):
                result = self.decision(snapshot=replace(self.snapshot, sources=(source,)))
                self.assertEqual(result.outcome, "unknown")
                self.assertFalse(result.affects_trading)

    def test_source_age_boundary_is_inclusive(self):
        source = replace(self.source, checked_at=self.now - timedelta(seconds=7200))
        self.assertEqual(self.decision(snapshot=replace(self.snapshot, sources=(source,))).outcome, "would_block")

    def test_fresh_alternate_coverage_does_not_validate_stale_event_evidence(self):
        alternate = replace(self.source, source="alternate")
        for original in (replace(self.source, checked_at=self.now - timedelta(seconds=7201)),
                         replace(self.source, status="invalid")):
            snapshot = replace(self.snapshot, sources=(original, alternate))
            self.assertEqual(self.decision(snapshot=snapshot).outcome, "unknown")

    def test_snapshot_not_available_yet_is_not_replayed_into_past(self):
        later = replace(self.snapshot, available_at=self.now + timedelta(seconds=1))
        self.assertEqual(self.decision(snapshot=later).outcome, "unknown")
        event = replace(self.event, available_at=self.now + timedelta(seconds=1))
        with self.assertRaises(ValueError):
            replace(self.snapshot, events=(event,))

    def test_future_schedule_is_valid_but_future_observation_is_not(self):
        future = replace(self.event, scheduled_at=self.now + timedelta(days=10))
        self.assertEqual(self.decision(snapshot=replace(self.snapshot, events=(future,))).outcome, "would_allow")
        source = replace(self.source, checked_at=self.now + timedelta(seconds=1))
        with self.assertRaises(ValueError):
            replace(self.snapshot, sources=(source,))

    def test_calendar_and_ai_switches_are_independent(self):
        settings = replace(self.settings, ai_enabled=True, ai_profile="local-test")
        self.assertEqual(self.decision(settings=settings).outcome, "would_block")
        self.assertEqual(self.decision(settings=replace(settings, ai_enabled=False)).outcome, "would_block")
        self.assertEqual(self.decision(settings=replace(settings, calendar_enabled=False)).outcome, "disabled")

    def test_contracts_are_immutable_and_reject_mutable_collections(self):
        with self.assertRaises(FrozenInstanceError):
            self.settings.enabled = False
        with self.assertRaises(FrozenInstanceError):
            self.event.scheduled_at = self.now
        with self.assertRaises(FrozenInstanceError):
            self.decision().outcome = "execute"
        for changes in ({"symbols": ["SPY"]}, {"symbols": ("SPY", "SPY")}, {"symbols": ("OTHER",)}):
            with self.assertRaises(ValueError):
                replace(self.event, **changes)
        with self.assertRaises(ValueError):
            replace(self.snapshot, events=[self.event])

    def test_bad_settings_timestamps_urls_and_policies_are_rejected(self):
        for changes in ({"enabled": 1}, {"revision": True}, {"revision": -1},
                        {"ai_enabled": True}, {"ai_profile": "https://arbitrary.example"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(self.settings, **changes)
        for timestamp in (self.now.replace(tzinfo=None), self.now.astimezone(timezone(timedelta(hours=1)))):
            with self.assertRaises(ValueError):
                utc_time(timestamp)
        for url in ("http://example.com", "https://user:secret@example.com", "https://example.com:80", "https://example.com/\n"):
            with self.assertRaises(ValueError):
                replace(self.event, source_url=url)
        for changes in ({"before_minutes": -1}, {"before_minutes": float("nan")},
                        {"after_minutes": 1441}, {"max_source_age_seconds": 0}, {"categories": ["inflation"]}):
            with self.assertRaises(ValueError):
                replace(self.policy, **changes)


@unittest.skipIf(Draft202012Validator is None, "Install requirements-dashboard-dev.txt for wire schema checks")
class IntelligenceWireTests(CalendarFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        path = Path(__file__).resolve().parents[1] / "market_intelligence" / "schema.json"
        self.schema = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(self.schema)
        self.validator = Draft202012Validator(self.schema, format_checker=FormatChecker())

    def wire(self, record):
        payload = asdict(record)
        if hasattr(record, "affects_trading"):
            payload["affects_trading"] = record.affects_trading
        return json.loads(json.dumps(payload, default=lambda value: utc_time(value).isoformat().replace("+00:00", "Z")))

    def test_real_calendar_and_settings_match_wire_schema(self):
        request = ControlRequest("request-1", "intelligence-1", self.settings.revision, self.now,
                                 self.now + timedelta(minutes=5), replace(self.settings, revision=3))
        receipt = ControlReceipt("request-1", "intelligence-1", "pending", None, None, "Awaiting service")
        for record in (self.settings, self.snapshot, self.decision(), request, receipt):
            self.validator.validate(self.wire(record))

    def test_legacy_source_provenance_remains_unknown_and_incomplete_pairs_are_rejected(self):
        snapshot = self.wire(self.snapshot)
        snapshot["sources"][0].pop("input_hash")
        snapshot["sources"][0].pop("source_url")
        self.validator.validate(snapshot)
        restored = construct(CalendarSnapshot, snapshot)
        self.assertIsNone(restored.sources[0].input_hash)
        self.assertIsNone(restored.sources[0].source_url)
        snapshot["sources"][0]["input_hash"] = "a" * 64
        self.assertFalse(self.validator.is_valid(snapshot))

    def test_event_references_preserve_ambiguous_identifier_pairs(self):
        first_source = replace(self.source, source="a:b")
        second_source = replace(self.source, source="a")
        first_event = replace(self.event, source="a:b", event_id="c")
        second_event = replace(self.event, source="a", event_id="b:c")
        snapshot = replace(self.snapshot, sources=(first_source, second_source), events=(first_event, second_event))
        decision = self.wire(self.decision(snapshot=snapshot))
        self.assertEqual(decision["event_refs"], [{"source": "a", "event_id": "b:c"},
                                                 {"source": "a:b", "event_id": "c"}])
        self.validator.validate(decision)

    def test_wire_and_python_reject_identifier_newlines_and_non_https_ports(self):
        settings = self.wire(self.settings)
        settings["ai_profile"] = "local-test\n"
        self.assertFalse(self.validator.is_valid(settings))
        with self.assertRaises(ValueError):
            replace(self.settings, ai_profile="local-test\n")
        for url in ("https://example.com:80/", "https://example.com:444/", "https://example.com:invalid/"):
            snapshot = self.wire(self.snapshot)
            snapshot["events"][0]["source_url"] = url
            self.assertFalse(self.validator.is_valid(snapshot))
            with self.assertRaises(ValueError):
                replace(self.event, source_url=url)

    def test_wire_rejects_authority_extra_settings_and_local_time(self):
        for field, value in (("affects_trading", True), ("outcome", "execute"),
                             ("observed_at", "2026-11-13T13:30:00+00:00")):
            payload = self.wire(self.decision())
            payload[field] = value
            self.assertFalse(self.validator.is_valid(payload))
        settings = self.wire(self.settings)
        settings["risk_per_trade_pct"] = 0.5
        self.assertFalse(self.validator.is_valid(settings))

    def test_ai_brief_requires_evidence_provenance_and_advisory_recommendations(self):
        brief = {
            "brief_id": "brief-1", "cluster_id": "cluster-1",
            "claims": [{"text": "An official release is scheduled.", "evidence_ids": ["evidence-1"]}],
            "interpretation": "Review possible volatility around publication.",
            "uncertainties": ["Direction of the market reaction is unknown."], "symbols": ["SPY"],
            "recommendation": "watch", "verification": "developing",
            "available_at": "2026-11-13T13:00:00Z", "expires_at": "2026-11-13T14:00:00Z",
            "provider": "mock", "model": "offline-fixture", "prompt_version": "summary-v1",
            "schema_version": "1", "settings_revision": 2, "input_hash": "a" * 64, "tokens_used": None,
        }
        self.validator.validate(brief)
        brief["recommendation"] = "buy"
        self.assertFalse(self.validator.is_valid(brief))
        brief["recommendation"] = "watch"
        brief["claims"][0]["evidence_ids"] = []
        self.assertFalse(self.validator.is_valid(brief))

    def test_pending_receipt_is_not_service_acknowledgement(self):
        receipt = {
            "request_id": "request-1", "database_id": "intelligence-1", "status": "pending",
            "effective_revision": None, "applied_at": None, "reason": "Awaiting service",
        }
        self.validator.validate(receipt)
        receipt["status"] = "applied"
        self.assertFalse(self.validator.is_valid(receipt))
        receipt.update(effective_revision=3, applied_at="2026-11-13T13:30:00Z")
        self.validator.validate(receipt)

    def test_control_requests_are_bounded_feature_settings_only(self):
        request = {
            "request_id": "request-1", "database_id": "intelligence-1", "base_revision": 2,
            "requested_at": "2026-11-13T13:30:00Z", "expires_at": "2026-11-13T13:35:00Z",
            "settings": self.wire(replace(self.settings, revision=3)),
        }
        self.validator.validate(request)
        request["settings"]["mode"] = "entry_gate"
        self.assertFalse(self.validator.is_valid(request))
        request["settings"]["mode"] = "shadow"
        request["settings"]["ai_profile"] = "https://unapproved.example"
        self.assertFalse(self.validator.is_valid(request))

    def test_news_keeps_missing_publication_and_syndication_evidence(self):
        evidence = {
            "evidence_id": "evidence-1", "source": "rss", "publisher_family": "official-bls",
            "source_url": "https://www.bls.gov/news.release/", "title": "Official release",
            "snippet": "", "published_at": None, "received_at": "2026-11-13T13:30:00Z",
            "content_hash": "b" * 64, "cluster_id": "cluster-1", "supersedes": None,
        }
        self.validator.validate(evidence)
        evidence["source_url"] = "https://user:secret@example.com/"
        self.assertFalse(self.validator.is_valid(evidence))
        evidence["source_url"] = "https://www.bls.gov/news.release/"
        evidence["snippet"] = "x" * 2001
        self.assertFalse(self.validator.is_valid(evidence))


if __name__ == "__main__":
    unittest.main()