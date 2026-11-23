"""Synthetic, fixed-clock checks for local relevance and separate CAS config."""

from copy import deepcopy
from contextlib import contextmanager
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dashboard.relevance import (
    MAX_MAPPINGS, MAX_REVISION, RelevanceError, RelevanceWorkspace, project_relevance,
)
from investment import bootstrap
from investment.store import import_events, initialize, read
from test_investment_opening import NOW, cash_observation, iso, opening, profile


def briefing():
    return {
        "briefing_id": "public-brief-one", "generated_at": iso(NOW),
        "provider": "synthetic", "model": "synthetic",
        "manifest": {"evidence": [], "gaps": [], "omitted": 0,
                     "input_hash": "public-hash", "cutoff": iso(NOW)},
        "output": {"headline": "Synthetic public context", "developments": [
            {"title": "CPI context", "topic": "inflation", "geography": "UK",
             "claims": [], "interpretation": {"text": "Public only", "evidence_ids": []},
             "uncertainties": []},
            {"title": "US jobs", "topic": "employment", "geography": "US",
             "claims": [], "interpretation": {"text": "Public only", "evidence_ids": []},
             "uncertainties": []},
        ]},
    }


def mapping():
    return {"account_id": "broker", "instrument_id": "equity",
            "topics": ["inflation"], "geographies": ["UK"]}


class RelevanceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.journal = self.root / "investment.sqlite3"
        self.profile = self.root / "profile.json"
        self.path = self.root / "relevance-mappings.json"
        self.profile.write_text(json.dumps(profile()), encoding="utf-8")
        initialize(self.journal, profile(), NOW)
        import_events(self.journal, [opening()], NOW)
        self.workspace = self.make_workspace()
        self.portfolio = read(self.journal, NOW)

    def make_workspace(self):
        return RelevanceWorkspace(journal_path=self.journal, profile_path=self.profile,
                                  mapping_path=self.path, clock_source=lambda: NOW)

    def payload(self):
        current = self.workspace.snapshot(briefing())
        return {"expected_revision": current["mapping_revision"],
                "expected_journal_revision": current["journal_revision"],
                "mappings": [mapping()]}

    def test_pure_default_classification_and_unknown_lookthrough(self):
        inputs = briefing()
        before = deepcopy(inputs)
        result = project_relevance(self.portfolio, inputs, [])
        self.assertEqual(inputs, before)
        self.assertEqual(result["state"], "available")
        self.assertTrue(result["matches"])
        self.assertTrue(all(match["match_kind"] == "classified" for match in result["matches"]))
        self.assertEqual(len(result["unmapped"]), 4)
        self.assertEqual(result["look_through"], "unknown")
        self.assertIn("no currency, issuer or name inference", result["scope"])
        self.assertEqual(result["valuation"], {
            "state": "complete", "observed_at": iso(NOW), "total_gbp": "2000"})
        self.assertFalse(any("amount" in match or "weight" in match
                             for match in result["matches"]))

    def test_declared_exact_pair_and_topic_geography_not_whole_account(self):
        result = project_relevance(self.portfolio, briefing(), [mapping()])
        declared = [item for item in result["matches"] if item["match_kind"] == "declared"]
        self.assertEqual(len(declared), 1)
        self.assertEqual(declared[0]["instrument_id"], "equity")
        self.assertEqual(declared[0]["development_index"], 0)
        self.assertEqual(len(result["unmapped"]), 3)
        config = mapping()
        del config["instrument_id"]
        result = project_relevance(self.portfolio, briefing(), [config])
        declared = [item for item in result["matches"] if item["match_kind"] == "declared"]
        self.assertEqual(len(declared), 1)
        self.assertNotIn("instrument_id", declared[0])

    def test_zero_exposure_never_matches_even_when_declared(self):
        self.portfolio.cash["broker"] = 0
        self.portfolio.holdings[("broker", "equity")] = 0
        result = project_relevance(self.portfolio, briefing(), [mapping()])
        self.assertFalse(any(item.get("instrument_id") == "equity" for item in result["matches"]))
        self.assertFalse(any(item["account_id"] == "broker" and "instrument_id" not in item
                             for item in result["editor"]["exposures"]))

    def test_missing_late_and_mixed_marks_omit_all_amounts(self):
        for change in ("missing", "late", "mixed"):
            portfolio = read(self.journal, NOW)
            key = ("broker", "equity")
            price, at, available = portfolio.marks[key]
            if change == "missing":
                del portfolio.marks[key]
            elif change == "late":
                portfolio.marks[key] = (price, at, available + timedelta(seconds=1))
            else:
                portfolio.marks[key] = (price, at - timedelta(seconds=1), available)
            result = project_relevance(portfolio, briefing(), [])
            self.assertEqual(result["valuation"], {"state": "incomplete", "observed_at": None})
            self.assertNotIn("total_gbp", result["valuation"])

    def test_names_and_currency_do_not_create_declared_or_issuer_matching(self):
        self.portfolio.spec["products"][0]["name"] = "US UK inflation issuer"
        data = briefing()
        data["output"]["developments"].append(
            {"topic": "issuer", "geography": "UK", "title": "US UK inflation issuer"})
        data["output"]["developments"].append(
            {"topic": "inflation", "geography": "Global", "title": "Global"})
        result = project_relevance(self.portfolio, data, [])
        self.assertTrue(all(item["development_index"] < 2 for item in result["matches"]))
        self.assertTrue(all(item["match_kind"] == "classified" for item in result["matches"]))

    def test_reads_have_no_writes_collection_or_models(self):
        before = {item.name: item.read_bytes() for item in self.root.iterdir()}
        public = briefing()
        with patch("os.replace", side_effect=AssertionError("read wrote")), \
                patch("dashboard.relevance._file_lock", side_effect=AssertionError("read locked")):
            first = self.workspace.snapshot(public)
            second = self.workspace.snapshot(public)
        self.assertEqual(first, second)
        self.assertEqual(first["mapping_revision"], 0)
        self.assertEqual(first["briefing_id"], public["briefing_id"])
        self.assertEqual(public, briefing())
        self.assertEqual(before, {item.name: item.read_bytes() for item in self.root.iterdir()})

    def test_missing_journal_or_profile_no_creation_or_fallback(self):
        for field in ("journal_path", "profile_path"):
            workspace = self.make_workspace()
            setattr(workspace, field, self.root / ("missing-" + field))
            before = set(self.root.iterdir())
            result = workspace.snapshot(briefing())
            self.assertEqual(result["state"], "unavailable")
            self.assertEqual(result["matches"], [])
            self.assertEqual(before, set(self.root.iterdir()))

    def test_no_briefing_still_supports_mapping_editor(self):
        result = self.workspace.snapshot()
        self.assertEqual(result["state"], "no_briefing")
        self.assertEqual(result["matches"], [])
        self.assertEqual(len(result["editor"]["exposures"]), 4)

    def test_save_cas_restart_and_journal_unchanged(self):
        before = self.journal.read_bytes()
        payload = self.payload()
        result = self.workspace.save_mappings(payload)
        self.assertEqual(result["state"], "saved")
        self.assertEqual(result["mapping_revision"], 1)
        restarted = self.make_workspace().snapshot(briefing())
        self.assertEqual(restarted["editor"]["mappings"], [mapping()])
        self.assertEqual(restarted["mapping_revision"], 1)
        self.assertEqual(before, self.journal.read_bytes())
        with self.assertRaises(RelevanceError) as caught:
            self.make_workspace().save_mappings(payload)
        self.assertEqual(caught.exception.status, 409)

    def test_save_stale_journal_and_mapping_revision_rejected(self):
        for field, invalid in (("expected_journal_revision", "old-head"),
                               ("expected_revision", 5)):
            payload = self.payload()
            payload[field] = invalid
            with self.assertRaises(RelevanceError) as caught:
                self.workspace.save_mappings(payload)
            self.assertEqual(caught.exception.status, 409)
            self.assertFalse(self.path.exists())

    def test_bad_tags_duplicates_unknown_targets_bounds_and_shape(self):
        invalids = []
        for field, value in (("topics", []), ("topics", ["issuer"]),
                              ("topics", ["inflation", "inflation"]),
                              ("geographies", ["Global"]), ("geographies", [True]),
                              ("account_id", "foreign"), ("instrument_id", None),
                              ("instrument_id", "foreign"), ("extra", "ignored")):
            item = mapping()
            item[field] = value
            invalids.append([item])
        item = mapping()
        item["account_id"] = "bank"
        invalids.extend([[item], [mapping(), mapping()], [mapping()] * (MAX_MAPPINGS + 1)])
        for invalid in invalids:
            with self.subTest(invalid=invalid):
                payload = self.payload()
                payload["mappings"] = invalid
                with self.assertRaises(RelevanceError):
                    self.workspace.save_mappings(payload)
                self.assertFalse(self.path.exists())
        for invalid in (True, -1, MAX_REVISION, 1.0):
            payload = self.payload()
            payload["expected_revision"] = invalid
            with self.assertRaises(RelevanceError):
                self.workspace.save_mappings(payload)

    def test_atomic_replace_failure_preserves_existing_config(self):
        self.workspace.save_mappings(self.payload())
        original = self.path.read_bytes()
        payload = self.payload()
        payload["mappings"] = []
        with patch("dashboard.relevance.os.replace", side_effect=OSError("synthetic failure")):
            with self.assertRaises(RelevanceError):
                self.workspace.save_mappings(payload)
        self.assertEqual(original, self.path.read_bytes())
        self.assertEqual(list(self.root.glob("*.tmp")), [])
        self.assertEqual(self.make_workspace().snapshot()["mapping_revision"], 1)

    def test_fsync_failure_preserves_existing_config(self):
        payload = self.payload()
        # Create the cooperating-writer lock first so failure is in config flush.
        self.workspace.save_mappings(payload)
        original = self.path.read_bytes()
        payload = self.payload()
        payload["mappings"] = []
        with patch("dashboard.relevance.os.fsync", side_effect=OSError("synthetic disk failure")):
            with self.assertRaises(RelevanceError):
                self.workspace.save_mappings(payload)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_real_journal_append_invalidates_old_save_head(self):
        payload = self.payload()
        import_events(self.journal, [cash_observation()], NOW)
        latest = self.workspace.snapshot(briefing())
        self.assertNotEqual(latest["journal_revision"], payload["expected_journal_revision"])
        with self.assertRaises(RelevanceError) as caught:
            self.workspace.save_mappings(payload)
        self.assertEqual(caught.exception.status, 409)
        self.assertFalse(self.path.exists())

    def test_real_journal_append_during_projection_withholds_personalization(self):
        def append_then_project(*args, **kwargs):
            import_events(self.journal, [cash_observation()], NOW)
            return project_relevance(*args, **kwargs)

        with patch("dashboard.relevance.project_relevance", side_effect=append_then_project):
            result = self.workspace.snapshot(briefing())
        self.assertEqual(result["state"], "changed")
        self.assertEqual(result["matches"], [])
        self.assertNotIn("editor", result)

    def test_postcommit_cleanup_error_reports_saved_not_rolled_back(self):
        @contextmanager
        def cleanup_failure(_):
            yield
            raise OSError("synthetic unlock failure")

        payload = self.payload()
        with patch("dashboard.relevance._file_lock", cleanup_failure):
            with self.assertRaises(RelevanceError) as caught:
                self.workspace.save_mappings(payload)
        self.assertEqual(caught.exception.code, "relevance_saved_status_unavailable")
        self.assertEqual(self.make_workspace().snapshot()["mapping_revision"], 1)

    def test_corrupt_foreign_and_oversized_config_not_repaired(self):
        self.workspace.save_mappings(self.payload())
        original = json.loads(self.path.read_text())
        invalids = ["{", '{"revision":1,"revision":2}', "x" * 65537]
        for field, value in (("binding", {}), ("revision", True), ("revision", 0),
                              ("revision", MAX_REVISION + 1), ("schema_version", 2)):
            altered = deepcopy(original)
            altered[field] = value
            invalids.append(json.dumps(altered))
        for raw in invalids:
            self.path.write_text(raw, encoding="utf-8")
            before = self.path.read_bytes()
            result = self.workspace.snapshot(briefing())
            self.assertEqual(result["state"], "unavailable")
            self.assertEqual(result["matches"], [])
            self.assertEqual(before, self.path.read_bytes())

    def test_rebound_database_or_profile_suppresses_personalization(self):
        self.workspace.save_mappings(self.payload())
        spec = profile()
        spec["portfolio_id"] = "foreign"
        self.profile.write_text(json.dumps(spec))
        result = self.workspace.snapshot(briefing())
        self.assertEqual(result["state"], "unavailable")
        self.assertEqual(result["matches"], [])
        self.profile.write_text(json.dumps(profile()))
        other = self.root / "other.sqlite3"
        initialize(other, profile(), NOW)
        import_events(other, [opening()], NOW)
        self.journal.unlink()
        other.rename(self.journal)
        self.assertEqual(self.workspace.snapshot()["state"], "unavailable")

    def test_journal_changes_during_read_suppress_all_private_output(self):
        original = self.workspace._portfolio
        calls = []

        def changed():
            ledger, now, head, binding = original()
            calls.append(head)
            return ledger, now, head if len(calls) == 1 else "new-head", binding

        with patch.object(self.workspace, "_portfolio", side_effect=changed):
            result = self.workspace.snapshot(briefing())
        self.assertEqual(result["state"], "changed")
        self.assertNotIn("editor", result)
        self.assertEqual(result["matches"], [])
        self.assertEqual(result["unmapped"], [])
        self.assertNotIn("total_gbp", result["valuation"])

    def test_mapping_changes_during_read_suppress_private_output(self):
        original = self.workspace._mappings
        calls = []

        def changed(*args):
            value = original(*args)
            calls.append(True)
            if len(calls) == 2:
                value["revision"] = 1
            return value

        with patch.object(self.workspace, "_mappings", side_effect=changed):
            self.assertEqual(self.workspace.snapshot(briefing())["state"], "changed")

    def test_future_briefing_and_cutoff_are_unavailable(self):
        for field in ("generated_at", "cutoff"):
            data = briefing()
            target = data if field == "generated_at" else data["manifest"]
            target[field] = iso(NOW + timedelta(seconds=1))
            result = self.workspace.snapshot(data)
            self.assertEqual(result["state"], "unavailable")
            self.assertEqual(result["matches"], [])

    def test_separate_config_save_preserves_ready_journal_workspace(self):
        journal_directory = self.root / "workspace"
        preview = bootstrap.preview_setup(journal_directory, profile(), opening(), NOW)
        self.assertEqual(
            bootstrap.confirm_setup(journal_directory, preview, NOW)["status"], "completed")
        before_status = bootstrap.inspect_workspace(journal_directory, NOW)
        self.assertEqual(before_status["state"], "ready")

        def contents():
            return {str(item.relative_to(journal_directory)):
                    item.read_bytes() if item.is_file() else None
                    for item in journal_directory.rglob("*")}

        before = contents()
        config_directory = self.root / ".goldscout-intelligence" / "workspace"
        mapping_path = config_directory / "relevance-mappings.json"
        workspace = RelevanceWorkspace(
            journal_path=journal_directory / "investment.sqlite3",
            profile_path=journal_directory / "profile.json",
            mapping_path=mapping_path, clock_source=lambda: NOW)
        snapshot = workspace.snapshot(briefing())
        self.assertEqual(snapshot["state"], "available")
        self.assertFalse(config_directory.exists())
        self.assertEqual(contents(), before)
        saved = workspace.save_mappings({
            "expected_revision": snapshot["mapping_revision"],
            "expected_journal_revision": snapshot["journal_revision"],
            "mappings": [mapping()],
        })
        self.assertEqual(saved["state"], "saved")
        self.assertEqual({item.name for item in config_directory.iterdir()},
                         {"relevance-mappings.json", "relevance-mappings.json.lock"})
        self.assertEqual(contents(), before)
        self.assertEqual(bootstrap.inspect_workspace(journal_directory, NOW), before_status)
        self.assertEqual(workspace.snapshot(briefing())["editor"]["mappings"], [mapping()])

    def test_explicit_paths_reject_exact_journal_profile_and_lock_collisions(self):
        for path in (self.journal, self.profile,
                     self.journal.with_name(self.journal.name + ".lock"),
                     self.profile.with_name(self.profile.name + ".lock"),
                     self.root / "other" / ".." / self.journal.name):
            with self.subTest(path=path), self.assertRaises(RelevanceError):
                RelevanceWorkspace(journal_path=self.journal, profile_path=self.profile,
                                   mapping_path=path)
        for field in ("journal_path", "profile_path"):
            paths = {"journal_path": self.journal, "profile_path": self.profile,
                     "mapping_path": self.root / "config.json"}
            paths[field] = self.root / "config.json.lock"
            with self.subTest(field=field), self.assertRaises(RelevanceError):
                RelevanceWorkspace(**paths)


if __name__ == "__main__":
    unittest.main()
