import json
from pathlib import Path
import unittest
from dataclasses import FrozenInstanceError, asdict, replace
from datetime import datetime, timedelta, timezone

from market_intelligence.contracts import (
    CalendarEvent, CalendarSnapshot, ControlReceipt, ControlRequest, IntelligenceSettings, SourceCoverage, utc_time,
)
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
        self.source = SourceCoverage(
            "bls", ("inflation",), self.now - timedelta(hours=1),
            self.now - timedelta(days=1), self.now + timedelta(days=30), "healthy")
        self.event = CalendarEvent(
            "cpi-2026-11", "bls", "https://www.bls.gov/schedule/", "inflation",
            ("SPY", "QQQ", "BTC/USD", "GLD"), self.now, self.now - timedelta(hours=1),
            "Consumer Price Index")
        self.snapshot = CalendarSnapshot(3, self.now - timedelta(hours=1),
                                         (self.source,), (self.event,))

@unittest.skipIf(Draft202012Validator is None, "Install requirements-dashboard-dev.txt for wire schema checks")
class IntelligenceWireTests(CalendarFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        path = Path(__file__).resolve().parents[1] / "market_intelligence" / "schema.json"
        self.schema = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(self.schema)
        self.validator = Draft202012Validator(self.schema, format_checker=FormatChecker())

    def test_calendar_rejects_future_evidence_but_preserves_unknown_event_time(self):
        unknown = replace(self.event, scheduled_at=None, status="tentative")
        self.assertIsNone(replace(self.snapshot, events=(unknown,)).events[0].scheduled_at)
        with self.assertRaises(ValueError):
            replace(self.snapshot, events=(replace(self.event, available_at=self.now),))
        with self.assertRaises(ValueError):
            replace(self.snapshot, sources=(replace(self.source, checked_at=self.now),))
        with self.assertRaises(ValueError):
            replace(self.snapshot, events=(self.event, replace(self.event, revision=1)))

    def test_settings_cannot_grant_trading_authority(self):
        self.assertFalse(IntelligenceSettings().enabled)
        self.assertFalse(IntelligenceSettings().ai_enabled)
        for mode in ("entry_gate", "live", "deterministic"):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                replace(self.settings, mode=mode)
        with self.assertRaises(FrozenInstanceError):
            self.settings.enabled = True

    def wire(self, record):
        payload = asdict(record)
        if hasattr(record, "affects_trading"):
            payload["affects_trading"] = record.affects_trading
        return json.loads(json.dumps(payload, default=lambda value: utc_time(value).isoformat().replace("+00:00", "Z")))

    def test_real_calendar_and_settings_match_wire_schema(self):
        request = ControlRequest("request-1", "intelligence-1", self.settings.revision, self.now,
                                 self.now + timedelta(minutes=5), replace(self.settings, revision=3))
        receipt = ControlReceipt("request-1", "intelligence-1", "pending", None, None, "Awaiting service")
        for record in (self.settings, self.snapshot, request, receipt):
            self.validator.validate(self.wire(record))

    def test_calendar_wire_allows_unlinked_macro_sources_only(self):
        us_snapshot = self.wire(self.snapshot)
        us_snapshot["events"][0]["symbols"] = []
        self.assertFalse(self.validator.is_valid(us_snapshot))

        macro_source = SourceCoverage(
            "ons", ("inflation",), self.now - timedelta(hours=1),
            self.now - timedelta(hours=1), self.now + timedelta(days=1), "healthy",
            "a" * 64, "https://api.beta.ons.gov.uk/v1/search/releases?type=upcoming",
            ("https://api.beta.ons.gov.uk/v1/search/releases?type=upcoming",
             "https://api.beta.ons.gov.uk/v1/search/releases?type=cancelled"))
        macro_event = replace(
            self.event, event_id="ons-release", source="ons",
            source_url="https://www.ons.gov.uk/releases/cpi", symbols=(), source_timezone="UTC")
        macro_snapshot = CalendarSnapshot(
            1, self.now - timedelta(hours=1), (macro_source,), (macro_event,))
        self.validator.validate(self.wire(macro_snapshot))
        missing_ons_endpoints = self.wire(macro_snapshot)
        missing_ons_endpoints["sources"][0]["source_urls"] = []
        self.assertFalse(self.validator.is_valid(missing_ons_endpoints))

        unknown_source = self.wire(macro_snapshot)
        unknown_source["events"][0]["source"] = "unknown"
        self.assertFalse(self.validator.is_valid(unknown_source))

    def test_legacy_calendar_event_without_source_notes_remains_readable(self):
        snapshot = self.wire(self.snapshot)
        snapshot["events"][0].pop("source_notes")
        restored = construct(CalendarSnapshot, snapshot)
        self.assertEqual(restored.events[0].source_notes, ())

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
        snapshot = self.wire(self.snapshot)
        snapshot["available_at"] = "2026-11-13T13:30:00+00:00"
        self.assertFalse(self.validator.is_valid(snapshot))
        settings = self.wire(self.settings)
        for field, value in (("affects_trading", True), ("risk_per_trade_pct", 0.5)):
            self.assertFalse(self.validator.is_valid({**settings, field: value}))

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