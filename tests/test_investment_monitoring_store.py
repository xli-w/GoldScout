from copy import deepcopy
from contextlib import closing
from datetime import timedelta
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from investment import InvestmentError
from investment.core import iso
from investment.monitoring_store import append_attempt, initialize_cache, purge_expired, read_cache
from test_investment_monitoring import monitoring_profile
from test_investment_opening import NOW, profile


def attempt():
    return {
        "id": "batch-1", "account_id": "broker", "provider": "etoro", "attempted_at": iso(NOW),
        "status": "success", "error_code": None,
        "observation": {
            "id": "source-revision-1", "account_id": "broker", "provider": "etoro",
            "observed_at": iso(NOW), "available_at": iso(NOW), "currency": "USD",
            "cash": "10", "holdings": [{"source_product": "synthetic", "quantity": "2"}],
            "coverage": "partial", "evidence": "synthetic observation",
        },
    }


class MonitoringStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "monitor.sqlite3"
        self.profile = monitoring_profile(profile())
        initialize_cache(self.path, self.profile, 86400)

    def test_restart_idempotency_conflicts_and_failed_collection(self):
        value = attempt()
        self.assertEqual(append_attempt(self.path, self.profile, value, NOW), 1)
        self.assertEqual(append_attempt(self.path, self.profile, deepcopy(value), NOW), 1)
        changed = deepcopy(value)
        changed["observation"]["cash"] = "11"
        with self.assertRaises(InvestmentError):
            append_attempt(self.path, self.profile, changed, NOW)
        failed = {**value, "id": "failed", "status": "failed", "observation": None,
                  "error_code": "provider_unavailable"}
        append_attempt(self.path, self.profile, failed, NOW)
        reopened = read_cache(self.path, self.profile, NOW)
        self.assertEqual([a["status"] for a in reopened["attempts"]], ["success", "failed"])
        self.assertEqual(reopened["attempts"][0]["observation"]["coverage"], "partial")

    def test_reader_does_not_create_or_repair_foreign_or_missing_stores(self):
        missing = self.path.with_name("missing.sqlite3")
        with self.assertRaises(InvestmentError):
            read_cache(missing, self.profile, NOW)
        self.assertFalse(missing.exists())
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("CREATE TABLE foreign_table (value TEXT)")
        before = self.path.read_bytes()
        with self.assertRaises(InvestmentError):
            read_cache(self.path, self.profile, NOW)
        self.assertEqual(self.path.read_bytes(), before)

    def test_retention_and_binding_are_explicit_and_purge_is_separate(self):
        append_attempt(self.path, self.profile, attempt(), NOW)
        before = self.path.read_bytes()
        self.assertEqual(read_cache(self.path, self.profile, NOW + timedelta(days=1))["attempts"], [])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(purge_expired(self.path, self.profile, NOW + timedelta(days=1)), {"deleted_attempts": 1})
        changed = deepcopy(self.profile)
        changed["accounts"][1]["evidence"] = "changed binding"
        with self.assertRaises(InvestmentError):
            read_cache(self.path, changed, NOW)

    def test_invalid_batch_never_publishes_success(self):
        for edit in (
            lambda v: v["observation"].update(currency="XXX"),
            lambda v: v["observation"].update(available_at=iso(NOW + timedelta(seconds=1))),
            lambda v: v["observation"].update(cash="NaN"),
            lambda v: v.update(provider="foreign"),
            lambda v: v.update(status="failed", error_code="secret exception text"),
        ):
            value = attempt()
            edit(value)
            with self.assertRaises(InvestmentError):
                append_attempt(self.path, self.profile, value, NOW)
        self.assertEqual(read_cache(self.path, self.profile, NOW)["revision"], 0)

    def test_commit_and_lock_failures_publish_no_batch(self):
        connect = sqlite3.connect

        class FailedCommit(sqlite3.Connection):
            def commit(self):
                raise sqlite3.OperationalError("synthetic commit failure")

        with patch("investment.monitoring_store.sqlite3.connect",
                   side_effect=lambda *args, **kwargs: connect(*args, **kwargs, factory=FailedCommit)):
            with self.assertRaises(InvestmentError):
                append_attempt(self.path, self.profile, attempt(), NOW)
        self.assertEqual(read_cache(self.path, self.profile, NOW)["revision"], 0)
        with closing(connect(self.path)) as blocker:
            blocker.execute("BEGIN EXCLUSIVE")
            with self.assertRaises(InvestmentError):
                append_attempt(self.path, self.profile, attempt(), NOW)
            blocker.rollback()
        self.assertEqual(read_cache(self.path, self.profile, NOW)["revision"], 0)
