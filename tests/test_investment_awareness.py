"""Local-only portfolio awareness with synthetic evidence and temporary stores."""

import io
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch

from fastapi.testclient import TestClient

from dashboard.app import create_app
from investment import InvestmentError
from investment.__main__ import main
from investment.awareness import awareness_snapshot, project_awareness
from investment.store import import_events, initialize, read
from market_intelligence.analysis import AnalysisBrief, AnalysisLimits, EvidenceClaim, analysis_input
from market_intelligence.contracts import (
    CalendarEvent, CalendarSnapshot, ControlRequest, IntelligenceSettings, SourceCoverage,
)
from market_intelligence.news import NewsObservation, parse_fed_news
from market_intelligence.store import initialize as initialize_intelligence
from test_dashboard_api import MemoryEventLoop
from test_investment_opening import NOW, iso, opening, profile


def awareness_profile(database_id="synthetic-intelligence"):
    return {
        "schema_version": 1, "portfolio_id": profile()["portfolio_id"],
        "intelligence_database_id": database_id, "observed_at": iso(NOW),
        "calendar_window_days": 7, "max_source_age_seconds": 7200,
        "exposures": [{
            "account_id": "broker", "product_id": "equity",
            "calendar": [{"source": "bls", "category": "inflation"}],
            "research_symbols": ["SPY"], "evidence": "Synthetic US macro proxy, not look-through",
        }],
        "reviews": [{
            "id": "mandate", "title": "Review the recorded portfolio",
            "last_reviewed_at": iso(NOW - timedelta(days=30)), "interval_days": 30,
            "evidence": "Operator-selected reminder only",
        }],
    }


def calendar(at=NOW):
    source = SourceCoverage("bls", ("inflation",), at, at - timedelta(days=1),
                            at + timedelta(days=8), "healthy")
    event = CalendarEvent("cpi", "bls", "https://www.bls.gov/schedule/", "inflation",
                          ("SPY",), at + timedelta(days=1), at, "Synthetic CPI")
    return CalendarSnapshot(1, at, (source,), (event,))


def evidence():
    return {
        "database_id": "synthetic-intelligence", "settings_at": NOW,
        "settings": IntelligenceSettings(enabled=True, ai_enabled=True, ai_profile="local"),
        "calendar": calendar(), "briefs": (), "news_status": NewsObservation(
            "news", 1, 0, NOW, NOW, NOW, "successful", None, "news", "news"),
    }


def thesis():
    return {
        "id": "long-term-case", "title": "Synthetic long-term case",
        "account_id": "broker", "product_id": "equity",
        "history": [
            {"observed_at": iso(NOW - timedelta(days=1)), "status": "watch",
             "thesis": "Initial hypothesis", "invalidation": "Evidence contradicts premise",
             "evidence": "Operator note one"},
            {"observed_at": iso(NOW), "status": "active",
             "thesis": "Updated hypothesis", "invalidation": "Evidence contradicts premise",
             "evidence": "Operator note two"},
        ],
    }


def calendar_watch():
    return {
        "id": "uk-macro", "title": "Synthetic UK release", "account_id": "broker",
        "product_id": "equity", "scheduled_on": "2026-12-17", "time_note": "Time not stated",
        "status": "tentative", "observed_at": iso(NOW), "source": "Operator-checked calendar",
    }


class InvestmentAwarenessTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "investment.sqlite3"
        initialize(self.path, profile(), NOW)
        import_events(self.path, [opening()], NOW)
        self.ledger = read(self.path, NOW)

    def test_explicit_local_matching_preserves_totals_and_has_no_authority(self):
        before = self.path.read_bytes()
        value = project_awareness(self.ledger, awareness_profile(), evidence(), NOW)
        self.assertEqual(value["alerts"][0]["matches"][0]["product_id"], "equity")
        self.assertEqual(value["alerts"][0]["evidence_status"], "fresh")
        self.assertEqual(value["alerts"][0]["available_at"], iso(NOW))
        self.assertEqual(value["reminders"][0]["status"], "due")
        self.assertFalse(value["affects_trading"])
        self.assertFalse(value["accounting_writes"])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertTrue(any("Unmapped" in warning for warning in value["warnings"]))

    def test_source_age_coverage_and_future_evidence_remain_unknown(self):
        for offset, expected in ((7200, "fresh"), (7201, "unknown")):
            with self.subTest(offset=offset):
                result = project_awareness(self.ledger, awareness_profile(), evidence(),
                                           NOW + timedelta(seconds=offset))
                self.assertEqual(result["alerts"][0]["evidence_status"], expected)
        future = evidence()
        future["calendar"] = calendar(NOW + timedelta(seconds=1))
        result = project_awareness(self.ledger, awareness_profile(), future, NOW)
        self.assertEqual(result["alerts"], [])
        self.assertEqual(result["calendar_status"], "unavailable")

    def test_no_inferred_lookthrough_unknown_times_and_disabled_collection(self):
        data = evidence()
        event = data["calendar"].events[0]
        data["calendar"] = CalendarSnapshot(2, NOW, data["calendar"].sources, (
            CalendarEvent(event.event_id, event.source, event.source_url, event.category,
                          event.symbols, None, NOW, event.title, status="tentative"),
        ))
        result = project_awareness(self.ledger, awareness_profile(), data, NOW)
        self.assertIsNone(result["alerts"][0]["scheduled_at"])
        self.assertEqual(result["alerts"][0]["evidence_status"], "unknown")
        config = awareness_profile()
        config["exposures"] = []
        self.assertEqual(project_awareness(self.ledger, config, data, NOW)["alerts"], [])
        data["settings"] = IntelligenceSettings()
        result = project_awareness(self.ledger, awareness_profile(), data, NOW)
        self.assertEqual(result["calendar_status"], "disabled")
        self.assertEqual(result["alerts"], [])
        self.assertEqual(result["reminders"][0]["status"], "due")

    def test_alternate_source_cannot_repair_originating_coverage(self):
        data = evidence()
        original = data["calendar"].sources[0]
        alternate = replace(original, source="alternate")
        for source in (replace(original, status="unavailable"),
                       replace(original, coverage_start=NOW + timedelta(days=2))):
            with self.subTest(source=source):
                data["calendar"] = CalendarSnapshot(2, NOW, (source, alternate),
                                                    calendar().events)
                result = project_awareness(self.ledger, awareness_profile(), data, NOW)
                self.assertEqual(result["alerts"][0]["evidence_status"], "unknown")
                self.assertTrue(any("bls / inflation" in warning for warning in result["warnings"]))

    def test_profiles_are_bound_and_invalid_or_future_inputs_fail_closed(self):
        for key, value in (("portfolio_id", "foreign"), ("intelligence_database_id", "foreign"),
                           ("observed_at", iso(NOW + timedelta(seconds=1))),
                           ("calendar_window_days", True)):
            config = awareness_profile()
            config[key] = value
            with self.subTest(key=key), self.assertRaises(InvestmentError):
                project_awareness(self.ledger, config, evidence(), NOW)
        config = awareness_profile()
        config["exposures"][0]["product_id"] = "foreign"
        with self.assertRaises(InvestmentError):
            project_awareness(self.ledger, config, evidence(), NOW)

    def test_profile_and_review_clocks_reject_submicrosecond_future_evidence(self):
        config = awareness_profile()
        config["observed_at"] = "2026-01-01T12:00:00.0000001Z"
        with self.assertRaises(InvestmentError):
            project_awareness(self.ledger, config, evidence(), NOW)
        config = awareness_profile()
        config["reviews"][0]["last_reviewed_at"] = "2026-01-01T12:00:00.0000001Z"
        with self.assertRaises(InvestmentError):
            project_awareness(self.ledger, config, evidence(), NOW)
        config["reviews"][0]["last_reviewed_at"] = "2026-01-01T12:00:00,0000001+00:00"
        with self.assertRaises(InvestmentError):
            project_awareness(self.ledger, config, evidence(), NOW)
        config["observed_at"] = "2026-01-01T12:00:00,000000+00:00"
        config["reviews"][0]["last_reviewed_at"] = "2026-01-01T11:59:59,999999+00:00"
        self.assertEqual(project_awareness(self.ledger, config, evidence(), NOW)
                         ["reminders"][0]["status"], "upcoming")

    def test_review_boundaries_and_never_reviewed_are_not_approvals(self):
        config = awareness_profile()
        with self.assertRaises(InvestmentError):
            project_awareness(self.ledger, config, evidence(), NOW - timedelta(seconds=1))
        config["reviews"][0]["last_reviewed_at"] = iso(NOW - timedelta(days=30) +
                                                       timedelta(seconds=1))
        result = project_awareness(self.ledger, config, evidence(), NOW)
        self.assertEqual(result["reminders"][0]["status"], "upcoming")
        config["reviews"][0]["last_reviewed_at"] = None
        result = project_awareness(self.ledger, config, evidence(), NOW)
        self.assertEqual(result["reminders"][0]["status"], "never_reviewed")
        self.assertIsNone(result["reminders"][0]["due_at"])

    def test_operator_thesis_history_is_dated_local_context_not_authority(self):
        config = awareness_profile()
        config["theses"] = [thesis()]
        before = self.path.read_bytes()
        result = project_awareness(self.ledger, config, evidence(), NOW, version=2)
        self.assertEqual(result["theses"], config["theses"])
        self.assertFalse(result["affects_trading"])
        self.assertFalse(result["accounting_writes"])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertNotIn("theses", project_awareness(self.ledger, awareness_profile(), evidence(), NOW))

    def test_thesis_history_rejects_bad_binding_clocks_and_states(self):
        config = awareness_profile()
        config["theses"] = [thesis()]
        config["observed_at"] = "2026-01-01T12:00:00.0000001Z"
        with self.assertRaises(InvestmentError):
            project_awareness(self.ledger, config, evidence(), NOW, version=2)
        for change in ("foreign_account", "foreign_product", "future", "duplicate_time", "excess_precision",
                       "null_time", "bad_status", "duplicate_id", "empty_history"):
            config = awareness_profile()
            item = thesis()
            config["theses"] = [item]
            if change == "foreign_account":
                item["account_id"] = "foreign"
            elif change == "foreign_product":
                item["product_id"] = "foreign"
            elif change == "future":
                item["history"][-1]["observed_at"] = iso(NOW + timedelta(seconds=1))
            elif change == "duplicate_time":
                item["history"][-1]["observed_at"] = item["history"][0]["observed_at"]
            elif change == "excess_precision":
                item["history"][-1]["observed_at"] = "2026-01-01T12:00:00.0000001Z"
            elif change == "null_time":
                item["history"][0]["observed_at"] = None
            elif change == "bad_status":
                item["history"][-1]["status"] = "approved"
            elif change == "duplicate_id":
                config["theses"].append(thesis())
            else:
                item["history"] = []
            with self.subTest(change=change), self.assertRaises(InvestmentError):
                project_awareness(self.ledger, config, evidence(), NOW)

    def test_operator_calendar_watch_is_local_unverified_and_does_not_create_coverage(self):
        config = awareness_profile()
        config["calendar_watch"] = [calendar_watch()]
        data = evidence()
        data["settings"] = IntelligenceSettings()
        result = project_awareness(self.ledger, config, data, NOW, version=2)
        self.assertEqual(result["calendar_watch"], config["calendar_watch"])
        self.assertEqual(result["calendar_status"], "disabled")
        self.assertEqual(result["alerts"], [])
        self.assertFalse(result["affects_trading"])
        self.assertFalse(result["accounting_writes"])

    def test_calendar_evidence_invalid_unicode_fails_closed(self):
        data = evidence()
        current = data["calendar"]
        data["calendar"] = CalendarSnapshot(current.revision, NOW, current.sources, (
            replace(current.events[0], title="\ud800"),
        ))
        with self.assertRaises(InvestmentError):
            project_awareness(self.ledger, awareness_profile(), data, NOW)

    def test_operator_calendar_watch_rejects_invalid_dates_clocks_and_bindings(self):
        for key, value in (("scheduled_on", "2026-02-30"), ("scheduled_on", "20261217"),
                           ("observed_at", None), ("observed_at", "2026-01-01T12:00:00.0000001Z"),
                           ("observed_at", iso(NOW + timedelta(seconds=1))),
                           ("status", "confirmed"), ("product_id", "foreign")):
            config = awareness_profile()
            item = calendar_watch()
            item[key] = value
            config["calendar_watch"] = [item]
            with self.subTest(key=key, value=value), self.assertRaises(InvestmentError):
                project_awareness(self.ledger, config, evidence(), NOW)

    def test_dated_briefs_keep_sources_expiry_and_public_proxy_mapping(self):
        raw = (b"<rss version='2.0'><channel><item><title>Policy statement</title>"
               b"<link>https://www.federalreserve.gov/newsevents/pressreleases/monetary20260101a.htm</link>"
               b"<description>Rates unchanged.</description></item></channel></rss>")
        item = parse_fed_news(raw, NOW).items[0]
        brief = AnalysisBrief(
            "brief", item.cluster_id, (EvidenceClaim("Rates unchanged.", (item.evidence_id,)),),
            "Unverified synthetic interpretation", ("Coverage is limited.",), ("SPY",),
            "review", "unverified", NOW, NOW + timedelta(minutes=30), "local", "synthetic",
            "news-extractive-v1", "1", 0, "a" * 64, None,
        )
        data = evidence()
        data["briefs"] = ((brief, (item,), "news"),)
        result = project_awareness(self.ledger, awareness_profile(), data, NOW)
        self.assertEqual(result["briefs"][0]["status"], "current")
        self.assertEqual(result["briefs"][0]["sources"][0]["source_url"], item.source_url)
        self.assertEqual(result["briefs"][0]["matches"][0]["product_id"], "equity")
        result = project_awareness(self.ledger, awareness_profile(), data,
                                   NOW + timedelta(seconds=901))
        self.assertEqual(result["briefs"][0]["status"], "source_unknown")
        data["briefs"] = ((replace(brief, available_at=NOW + timedelta(seconds=1)),
                           (item,), "news"),)
        self.assertEqual(project_awareness(self.ledger, awareness_profile(), data, NOW)["briefs"], [])
        data["briefs"] = ((brief, (item,), "news"),)
        result = project_awareness(self.ledger, awareness_profile(), data,
                                   NOW + timedelta(minutes=30))
        self.assertEqual(result["briefs"][0]["status"], "expired")
        data["settings"] = IntelligenceSettings(revision=1)
        result = project_awareness(self.ledger, awareness_profile(), data, NOW)
        self.assertEqual(result["briefs"][0]["status"], "disabled")

    def test_calendar_window_is_half_open_and_cancellations_are_retained(self):
        data = evidence()
        base = data["calendar"].events[0]
        data["calendar"] = CalendarSnapshot(2, NOW, data["calendar"].sources, (
            replace(base, event_id="at-start", scheduled_at=NOW),
            replace(base, event_id="at-end", scheduled_at=NOW + timedelta(days=7)),
            replace(base, event_id="cancelled", status="cancelled"),
            replace(base, event_id="past", scheduled_at=NOW - timedelta(seconds=1)),
        ))
        result = project_awareness(self.ledger, awareness_profile(), data, NOW)
        self.assertEqual([item["event_id"] for item in result["alerts"]], ["at-start", "cancelled"])
        self.assertEqual(result["alerts"][1]["status"], "cancelled")

    def test_persisted_brief_source_attempt_and_settings_changes_never_refresh_it(self):
        path = Path(self.directory.name) / "research.sqlite3"
        store = initialize_intelligence(path, NOW)
        settings = IntelligenceSettings(enabled=True, ai_enabled=True, ai_profile="local", revision=1)
        store.enqueue(ControlRequest("on", store.database_id, 0, NOW, NOW + timedelta(minutes=1),
                                    settings), NOW)
        store.apply_pending(NOW, profiles=("local",))
        raw = (b"<rss version='2.0'><channel><item><title>Policy statement</title>"
               b"<link>https://www.federalreserve.gov/newsevents/pressreleases/monetary20260101a.htm</link>"
               b"<description>Rates unchanged.</description></item></channel></rss>")
        batch = parse_fed_news(raw, NOW)
        status = store.complete_news_attempt("news", 1, NOW, NOW, NOW, batch=batch, raw=raw)
        item = batch.items[0]
        _, size, checksum = analysis_input(store.news_acquisition(status.acquisition_id), item.cluster_id)
        context = {"acquisition_id": status.acquisition_id, "source_attempt_id": status.attempt_id,
                   "settings_revision": 1, "profile": "local", "model": "synthetic",
                   "cluster_id": item.cluster_id, "input_hash": checksum, "prompt_version": "news-extractive-v1"}
        reservation = store.reserve_analysis(NOW, size, AnalysisLimits(), context)
        store.publish_brief(AnalysisBrief(
            "persisted", item.cluster_id, (EvidenceClaim(item.snippet, (item.evidence_id,)),),
            "Synthetic stored interpretation", ("Not verified.",), ("SPY",),
            "review", "unverified", NOW, NOW + timedelta(minutes=30), "local", "synthetic",
            "news-extractive-v1", "1", 1, checksum, None, reservation), NOW)
        config_path = Path(self.directory.name) / "research-profile.json"
        config_path.write_text(json.dumps(awareness_profile(store.database_id)), encoding="utf-8")
        original = path.read_bytes()
        for seconds, expected in ((900, "current"), (901, "source_unknown"), (1800, "expired")):
            result = awareness_snapshot(self.path, path, config_path, NOW + timedelta(seconds=seconds))
            self.assertEqual(result["briefs"][0]["status"], expected)
        self.assertEqual(path.read_bytes(), original)
        later = NOW + timedelta(seconds=1)
        store.complete_news_attempt("new-news", 1, later, later, later,
                                    batch=parse_fed_news(raw, later), raw=raw)
        self.assertEqual(awareness_snapshot(self.path, path, config_path, later)["briefs"][0]["status"],
                         "source_unknown")
        store.enqueue(ControlRequest("revision", store.database_id, 1, later, later + timedelta(minutes=1),
                                    replace(settings, revision=2)), later)
        store.apply_pending(later, profiles=("local",))
        self.assertEqual(awareness_snapshot(self.path, path, config_path, later)["briefs"][0]["status"],
                         "superseded")

    def test_cli_and_api_use_read_only_stores_and_never_initialize_missing_sources(self):
        intelligence_path = Path(self.directory.name) / "intelligence.sqlite3"
        store = initialize_intelligence(intelligence_path, NOW)
        store.enqueue(ControlRequest("on", store.database_id, 0, NOW, NOW + timedelta(minutes=1),
                                    IntelligenceSettings(enabled=True, revision=1)), NOW)
        store.apply_pending(NOW)
        store.publish_calendar(calendar(), 1, NOW)
        config_path = Path(self.directory.name) / "awareness.json"
        config = awareness_profile(store.database_id)
        config["theses"] = [thesis()]
        config["calendar_watch"] = [calendar_watch()]
        config_path.write_text(json.dumps(config), encoding="utf-8")
        originals = {path: path.read_bytes() for path in (self.path, intelligence_path)}
        expected = awareness_snapshot(self.path, intelligence_path, config_path, NOW, version=2)
        legacy = awareness_snapshot(self.path, intelligence_path, config_path, NOW)
        self.assertEqual(legacy["schema_version"], 1)
        self.assertNotIn("theses", legacy)
        self.assertNotIn("calendar_watch", legacy)
        output = io.StringIO()
        self.assertEqual(main(["awareness", "--database", str(self.path),
                               "--intelligence-database", str(intelligence_path),
                               "--awareness-profile", str(config_path), "--schema-version", "2"],
                              stdout=output, now=NOW), 0)
        self.assertEqual(json.loads(output.getvalue()), expected)
        app = create_app(serve_frontend=False, clock=lambda: NOW, investment_database=self.path,
                         intelligence_database=intelligence_path, awareness_profile=config_path)
        with TestClient(app, base_url="http://127.0.0.1",
                        backend_options={"loop_factory": MemoryEventLoop}) as client:
            self.assertEqual(client.get("/api/investment/v1/awareness").json(), legacy)
            response = client.get("/api/investment/v2/awareness")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), expected)
            self.assertEqual(response.json()["theses"][0]["history"], thesis()["history"])
            self.assertEqual(response.json()["calendar_watch"], [calendar_watch()])
            for version in (1, 2):
                self.assertEqual(client.post(f"/api/investment/v{version}/awareness", json={},
                                             headers={"Origin": "http://127.0.0.1"}).status_code, 405)
            self.assertEqual(client.get("/api/investment/v3/awareness").status_code, 404)
            with patch("investment.awareness.project_awareness", side_effect=InvestmentError("invalid")):
                self.assertEqual(client.get("/api/investment/v2/awareness").status_code, 503)
            config["theses"][0]["history"][0]["thesis"] = "\ud800"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            self.assertEqual(client.get("/api/investment/v2/awareness").status_code, 503)
        for path, original in originals.items():
            self.assertEqual(path.read_bytes(), original)
        missing = Path(self.directory.name) / "missing.sqlite3"
        with self.assertRaises(InvestmentError):
            awareness_snapshot(self.path, missing, config_path, NOW)
        self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()
