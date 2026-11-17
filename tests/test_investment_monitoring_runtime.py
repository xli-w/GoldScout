from copy import deepcopy
from datetime import timedelta
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from investment import InvestmentError
from investment.__main__ import main
from investment.core import iso
from investment.monitoring import monitoring_snapshot
from investment.monitoring_runtime import collect_once
from investment.monitoring_store import append_attempt, initialize_cache, read_cache
from investment.store import canonical, digest, import_events, initialize
from test_investment_opening import NOW, opening, profile
from test_investment_monitoring import monitoring_profile, assignments


class MonitoringRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.database, self.cache = root / "journal.sqlite3", root / "cache.sqlite3"
        self.path = root / "profile.json"
        initialize(self.database, profile(), NOW)
        import_events(self.database, [opening(), *assignments()], NOW)
        self.profile = monitoring_profile(profile())
        self.path.write_text(json.dumps(self.profile), encoding="utf-8")
        initialize_cache(self.cache, self.profile, 86400)
        self.approval = {
            "profile_hash": digest(canonical(self.profile)), "account_id": "broker",
            "approved_at": iso(NOW), "expires_at": iso(NOW + timedelta(hours=1)),
            "read_scope_verified": True, "eligibility_verified": True,
            "retention_rights_verified": True, "account_binding_verified": True,
            "supervised_read_approved": True, "evidence": "synthetic approval only",
        }

    def collect(self, **kwargs):
        return collect_once(self.database, self.profile, self.cache, "broker", self.approval,
                            now=NOW, **kwargs)

    def test_collection_requires_all_gates_before_transport_or_secrets(self):
        collector, credentials = Mock(), Mock()
        with self.assertRaises(InvestmentError):
            self.collect(collector=collector, credential_accessor=credentials)
        for key in ("read_scope_verified", "retention_rights_verified", "account_binding_verified"):
            self.approval[key] = False
            with self.assertRaises(InvestmentError):
                self.collect(allow_network=True, collector=collector, credential_accessor=credentials)
            self.approval[key] = True
        self.approval["expires_at"] = iso(NOW)
        with self.assertRaises(InvestmentError):
            self.collect(allow_network=True, collector=collector, credential_accessor=credentials)
        collector.assert_not_called()
        credentials.assert_not_called()

    def test_partial_usd_receipt_has_no_invented_source_clock_and_survives_failure(self):
        from investment.etoro import EToroError
        before = self.database.read_bytes()
        collector = Mock(return_value={"account_currency": "USD", "raw": {"credit": "42", "positions": []}})
        result = self.collect(allow_network=True, transport=Mock(), collector=collector)
        self.assertEqual(result["status"], "partial_observation")
        report = monitoring_snapshot(self.database, self.path, self.cache, NOW)
        provider = next(p for p in report["providers"] if p["provider"] == "etoro")
        self.assertIsNone(provider["source_as_of"])
        self.assertEqual(provider["freshness"], "unavailable")
        self.assertEqual(provider["native_summary"]["credit_usd"], "42")
        collector.side_effect = EToroError("permission_refused")
        with self.assertRaisesRegex(InvestmentError, "permission_denied"):
            self.collect(allow_network=True, transport=Mock(), collector=collector)
        report = monitoring_snapshot(self.database, self.path, self.cache, NOW)
        self.assertTrue(any(f["rule"] == "collection_failed" for f in report["findings"]))
        self.assertEqual(self.database.read_bytes(), before)
        self.assertEqual(len(read_cache(self.cache, self.profile, NOW)["attempts"]), 2)

    def test_native_precision_is_preserved_and_expired_completion_records_failure(self):
        collector = Mock(return_value={
            "account_currency": "USD", "raw": {"credit": "0.123456789012345678", "positions": []}})
        self.collect(allow_network=True, transport=Mock(), collector=collector)
        observation = read_cache(self.cache, self.profile, NOW)["attempts"][0]["observation"]
        self.assertEqual(observation["native_summary"]["credit_usd"], "0.123456789012345678")
        with self.assertRaisesRegex(InvestmentError, "expired"):
            self.collect(allow_network=True, transport=Mock(), collector=collector,
                         completion_clock=lambda: NOW + timedelta(hours=1))
        attempts = read_cache(self.cache, self.profile, NOW + timedelta(hours=1))["attempts"]
        self.assertEqual(attempts[-1]["error_code"], "collection_interrupted")
        self.assertIsNone(attempts[-1]["observation"])

    def test_cli_report_is_offline_and_staging_does_not_change_journal(self):
        from test_investment_monitoring_store import attempt
        source = self.path.with_name("attempt.json")
        source.write_text(json.dumps(attempt()), encoding="utf-8")
        before = self.database.read_bytes()
        arguments = ["--database", str(self.database), "--monitoring-profile", str(self.path),
                     "--cache", str(self.cache)]
        for command, extra in (("monitor-stage", ["--attempt", str(source)]), ("monitor", [])):
            output, error = StringIO(), StringIO()
            with patch("investment.etoro.EToroTransport", side_effect=AssertionError("network forbidden")):
                self.assertEqual(main([command, *arguments, *extra], stdout=output, stderr=error, now=NOW), 0,
                                 error.getvalue())
            self.assertTrue(json.loads(output.getvalue()))
        self.assertEqual(self.database.read_bytes(), before)

    def comparable_attempt(self, identifier="first", offset=1, cash="100"):
        from test_investment_monitoring_store import attempt
        value = attempt()
        value.update(id=identifier, attempted_at=iso(NOW + timedelta(seconds=offset + 1)))
        value["observation"].update(
            id="source-" + identifier, observed_at=iso(NOW + timedelta(seconds=offset)),
            available_at=value["attempted_at"], currency="GBP", cash=cash, coverage="complete",
            holdings=[{"source_product": "source-equity", "quantity": "50"},
                      {"source_product": "source-bond", "quantity": "20"}],
            product_mappings=[
                {"source_product": "source-" + product, "product_id": product, "evidence": "synthetic mapping"}
                for product in ("equity", "bond")],
            mapping_available_at=iso(NOW))
        return value

    def test_retained_difference_survives_matching_observation_and_reopen(self):
        first = self.comparable_attempt(cash="999")
        second = self.comparable_attempt("second", 3)
        now = NOW + timedelta(seconds=4)
        append_attempt(self.cache, self.profile, first, now)
        append_attempt(self.cache, self.profile, second, now)
        result = monitoring_snapshot(self.database, self.path, self.cache, now)
        difference = next(f for f in result["findings"] if f["rule"] == "observation_difference")
        self.assertIn("source-first", difference["evidence"])
        self.assertIn("899", difference["calculation"])

    def test_invalid_or_late_product_mapping_never_poison_or_backdate_comparisons(self):
        now = NOW + timedelta(seconds=2)
        value = self.comparable_attempt()
        value["observation"]["product_mappings"].append(
            deepcopy(value["observation"]["product_mappings"][0]))
        with self.assertRaises(InvestmentError):
            append_attempt(self.cache, self.profile, value, now)
        value = self.comparable_attempt()
        value["observation"]["product_mappings"][0]["product_id"] = "not-in-journal"
        append_attempt(self.cache, self.profile, value, now)
        result = monitoring_snapshot(self.database, self.path, self.cache, now)
        self.assertTrue(any(f["rule"] == "comparison_unavailable" for f in result["findings"]))
        late = self.comparable_attempt("late", 3)
        late["observation"]["mapping_available_at"] = late["observation"]["available_at"]
        now = NOW + timedelta(seconds=4)
        append_attempt(self.cache, self.profile, late, now)
        result = monitoring_snapshot(self.database, self.path, self.cache, now)
        issue = next(f for f in result["findings"] if f["rule"] == "comparison_unavailable")
        self.assertEqual(issue["calculation"], "2 unavailable comparisons")
