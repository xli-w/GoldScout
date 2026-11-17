"""Monitoring review workflow persists only bounded finding metadata and actions."""

from contextlib import closing
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from investment import InvestmentError
from investment.__main__ import main
from investment.core import iso
from investment.monitoring import project
from investment.monitoring_reviews import (
    initialize_review_store, monitoring_cache_binding, review_inbox,
    sync_review_findings, update_review,
)
from investment.monitoring_store import initialize_cache, read_cache
from investment.store import canonical, digest, import_events, initialize, read
from test_investment_monitoring import monitoring_profile
from test_investment_opening import NOW, opening, profile


class MonitoringReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.database = self.root / "investment.sqlite3"
        self.reviews = self.root / "reviews.sqlite3"
        self.spec = profile()
        initialize(self.database, self.spec, NOW)
        import_events(self.database, [opening()], NOW)
        self.monitoring_profile = monitoring_profile(self.spec)
        self.profile_hash = digest(canonical(self.monitoring_profile))
        initialize_review_store(self.reviews, self.spec["portfolio_id"], self.profile_hash)
        self.report = project(read(self.database, NOW), self.monitoring_profile, NOW)

    def sync(self, report=None, now=NOW):
        return sync_review_findings(
            self.reviews, self.report if report is None else report,
            self.spec["portfolio_id"], self.profile_hash, now)

    def test_explicit_init_binding_and_readers_never_create_or_repair(self):
        missing = self.root / "missing.sqlite3"
        with self.assertRaises(InvestmentError):
            review_inbox(missing)
        self.assertFalse(missing.exists())
        with self.assertRaises(InvestmentError):
            initialize_review_store(self.reviews, self.spec["portfolio_id"], self.profile_hash)
        with self.assertRaises(InvestmentError):
            sync_review_findings(
                self.reviews, self.report, self.spec["portfolio_id"], "f" * 64, NOW)
        before = self.reviews.read_bytes()
        with closing(sqlite3.connect(self.reviews)) as connection, connection:
            connection.execute("CREATE TABLE unrelated (value TEXT)")
        changed = self.reviews.read_bytes()
        with self.assertRaises(InvestmentError):
            review_inbox(self.reviews)
        self.assertEqual(self.reviews.read_bytes(), changed)
        self.assertNotEqual(before, changed)

    def test_sync_is_idempotent_and_does_not_persist_raw_evidence(self):
        result = self.sync()
        self.assertGreater(result["created"], 0)
        repeated = self.sync()
        self.assertTrue(repeated["unchanged"])
        self.assertEqual(repeated["revision"], result["revision"])
        inbox = review_inbox(self.reviews)
        self.assertEqual(len(inbox["cases"]), result["active_cases"])
        for case in inbox["cases"]:
            self.assertNotIn("evidence", case["finding"])
            self.assertEqual(len(case["finding"]["evidence_sha256"]), 64)
        self.assertNotIn(b"synthetic mapping evidence", self.reviews.read_bytes())
        self.assertFalse(any(case["review_status"] == "resolved" for case in inbox["cases"]))

    def test_actions_are_idempotent_revision_bound_and_never_resolve(self):
        result = self.sync()
        case = review_inbox(self.reviews)["cases"][0]
        action = update_review(
            self.reviews, case["case_id"], "ack-1", result["revision"],
            "acknowledged", "Checked source date", NOW)
        self.assertFalse(action["duplicate"])
        duplicate = update_review(
            self.reviews, case["case_id"], "ack-1", result["revision"],
            "acknowledged", "Checked source date", NOW + timedelta(seconds=1))
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(duplicate["revision"], action["revision"])
        with self.assertRaisesRegex(InvestmentError, "changed"):
            update_review(
                self.reviews, case["case_id"], "stale", result["revision"],
                "reviewed", "", NOW)
        refreshed = review_inbox(self.reviews)
        stored = next(item for item in refreshed["cases"] if item["case_id"] == case["case_id"])
        self.assertEqual(stored["review_status"], "acknowledged")
        self.assertEqual(stored["note"], "Checked source date")
        self.assertNotEqual(stored["lifecycle"], "resolved")

    def test_changed_evidence_supersedes_and_absence_is_not_resolution(self):
        original_result = self.sync()
        original = review_inbox(self.reviews)["cases"][0]
        changed = deepcopy(self.report)
        finding = next(item for item in changed["findings"]
                       if item["id"] == original["finding_id"])
        finding["evidence"] = "new source revision"
        changed["portfolio_revision"] += 1
        self.sync(changed, NOW + timedelta(seconds=1))
        inbox = review_inbox(self.reviews)
        previous = next(item for item in inbox["cases"] if item["case_id"] == original["case_id"])
        self.assertEqual(previous["lifecycle"], "superseded")
        replacement = next(item for item in inbox["cases"]
                           if item["finding_id"] == original["finding_id"]
                           and item["lifecycle"] == "active")
        self.assertNotEqual(replacement["case_id"], original["case_id"])
        no_findings = deepcopy(changed)
        no_findings["findings"] = []
        no_findings["portfolio_revision"] += 1
        self.sync(no_findings, NOW + timedelta(seconds=2))
        current = review_inbox(self.reviews)
        self.assertTrue(all(case["lifecycle"] != "active" for case in current["cases"]))
        self.assertFalse(any(case["lifecycle"] == "resolved" for case in current["cases"]))
        no_findings["portfolio_revision"] += 1
        self.sync(no_findings, NOW + timedelta(seconds=3))
        self.assertEqual(review_inbox(self.reviews)["revision"], current["revision"] + 1)
        self.assertGreater(original_result["created"], 0)

    def test_reappearing_finding_gets_a_new_open_case(self):
        self.sync()
        original = review_inbox(self.reviews)["cases"][0]
        absent = deepcopy(self.report)
        absent["findings"] = []
        absent["portfolio_revision"] += 1
        self.sync(absent, NOW + timedelta(seconds=1))
        reappeared = deepcopy(self.report)
        reappeared["portfolio_revision"] += 2
        self.sync(reappeared, NOW + timedelta(seconds=2))
        inbox = review_inbox(self.reviews)
        active = next(case for case in inbox["cases"] if case["finding_id"] == original["finding_id"]
                      and case["lifecycle"] == "active")
        self.assertNotEqual(active["case_id"], original["case_id"])
        self.assertEqual(active["review_status"], "new")

    def test_older_snapshot_cannot_regress_a_newer_sync(self):
        newer = deepcopy(self.report)
        newer["portfolio_revision"] += 1
        newer["generated_at"] = iso(NOW + timedelta(seconds=2))
        self.sync(newer, NOW + timedelta(seconds=2))
        current = review_inbox(self.reviews)
        with self.assertRaisesRegex(InvestmentError, "Stale monitoring report"):
            self.sync(self.report, NOW + timedelta(seconds=3))
        self.assertEqual(review_inbox(self.reviews), current)

    def test_replacing_cache_at_same_path_requires_new_review_store(self):
        cache_path = self.root / "monitor-cache.sqlite3"
        initialize_cache(cache_path, self.monitoring_profile, 86400)
        first_cache = read_cache(cache_path, self.monitoring_profile, NOW)
        first_binding = monitoring_cache_binding(cache_path, first_cache["instance_id"])
        review_path = self.root / "cache-bound-reviews.sqlite3"
        initialize_review_store(
            review_path, self.spec["portfolio_id"], self.profile_hash, first_binding)
        first_report = project(
            read(self.database, NOW), self.monitoring_profile, NOW, first_cache)
        sync_review_findings(
            review_path, first_report, self.spec["portfolio_id"], self.profile_hash,
            NOW, first_binding)
        before = review_inbox(review_path)

        cache_path.unlink()
        initialize_cache(cache_path, self.monitoring_profile, 86400)
        replacement = read_cache(cache_path, self.monitoring_profile, NOW)
        replacement_binding = monitoring_cache_binding(cache_path, replacement["instance_id"])
        self.assertNotEqual(first_cache["instance_id"], replacement["instance_id"])
        replacement_report = project(
            read(self.database, NOW), self.monitoring_profile, NOW, replacement)
        with self.assertRaisesRegex(InvestmentError, "another portfolio, profile or cache"):
            sync_review_findings(
                review_path, replacement_report, self.spec["portfolio_id"],
                self.profile_hash, NOW + timedelta(seconds=1), replacement_binding)
        self.assertEqual(review_inbox(review_path), before)

    def test_invalid_source_report_and_size_failure_do_not_publish(self):
        with self.assertRaisesRegex(InvestmentError, "recorded monitoring report"):
            self.sync({**self.report, "source_mode": "demo"})
        with closing(sqlite3.connect(self.reviews)) as connection:
            page_count = connection.execute("PRAGMA page_count").fetchone()[0]
            page_size = connection.execute("PRAGMA page_size").fetchone()[0]
        byte_limit = max(self.reviews.stat().st_size, page_count * page_size)
        with patch("investment.monitoring_reviews.MAX_DATABASE_BYTES", byte_limit):
            with self.assertRaisesRegex(InvestmentError, "rolled back"):
                self.sync()
        self.assertEqual(review_inbox(self.reviews)["cases"], [])
        self.assertEqual(review_inbox(self.reviews)["revision"], 0)

    def test_failed_commit_publishes_neither_cases_nor_revision(self):
        connect = sqlite3.connect

        class FailedCommit(sqlite3.Connection):
            def commit(self):
                raise sqlite3.OperationalError("synthetic commit failure")

        with patch("investment.monitoring_reviews.sqlite3.connect",
                   side_effect=lambda *args, **kwargs:
                   connect(*args, **kwargs, factory=FailedCommit)):
            with self.assertRaisesRegex(InvestmentError, "persistence failed"):
                self.sync()
        inbox = review_inbox(self.reviews)
        self.assertEqual(inbox["cases"], [])
        self.assertEqual(inbox["revision"], 0)

    def test_cli_init_sync_inbox_and_update(self):
        profile_path = self.root / "monitoring.json"
        cli_reviews = self.root / "cli-reviews.sqlite3"
        cli_cache = self.root / "cli-cache.sqlite3"
        profile_path.write_text(json.dumps(self.monitoring_profile), encoding="utf-8")
        initialize_cache(cli_cache, self.monitoring_profile, 86400)

        def run(arguments, now=NOW):
            from io import StringIO
            stdout, stderr = StringIO(), StringIO()
            status = main(arguments, stdout=stdout, stderr=stderr, now=now)
            self.assertEqual(status, 0, stderr.getvalue())
            return json.loads(stdout.getvalue())

        run(["review-init", "--database", str(self.database),
             "--monitoring-profile", str(profile_path), "--reviews", str(cli_reviews),
             "--cache", str(cli_cache)])
        synced = run(["review-sync", "--database", str(self.database),
                      "--monitoring-profile", str(profile_path), "--reviews", str(cli_reviews),
                      "--cache", str(cli_cache)])
        inbox = run(["review-inbox", "--reviews", str(cli_reviews)])
        self.assertEqual(inbox["revision"], synced["revision"])
        case = inbox["cases"][0]
        updated = run([
            "review-update", "--reviews", str(cli_reviews), "--case-id", case["case_id"],
            "--request-id", "cli-action", "--expected-revision", str(inbox["revision"]),
            "--action", "reviewed", "--note", "Evidence checked",
        ])
        self.assertFalse(updated["duplicate"])
        refreshed = run(["review-inbox", "--reviews", str(cli_reviews)],
                        NOW + timedelta(seconds=1))
        self.assertEqual(refreshed["revision"], updated["revision"])
        self.assertEqual(
            next(item for item in refreshed["cases"] if item["case_id"] == case["case_id"])
            ["review_status"], "reviewed")


if __name__ == "__main__":
    unittest.main()
