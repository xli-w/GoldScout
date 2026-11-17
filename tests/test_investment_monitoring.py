"""Monitoring regressions use fixed clocks and synthetic journals only."""

from copy import deepcopy
from datetime import timedelta
from pathlib import Path
import tempfile
import unittest

from investment import InvestmentError
from investment.core import iso
from investment.monitoring import project
from investment.store import canonical, digest, import_events, initialize, read
from test_investment_opening import NOW, opening, profile


def monitoring_profile(spec):
    return {
        "schema_version": 1, "portfolio_id": spec["portfolio_id"],
        "investment_profile_sha256": digest(canonical(spec)), "observed_at": iso(NOW),
        "concentration_threshold_pct": "50",
        "accounts": [
            {"account_id": a["id"], "provider": "etoro" if a["kind"] == "broker" else "manual",
             "wrapper": "other", "denominator": a["kind"] == "broker",
             "max_age_seconds": 86400, "last_reviewed_at": None,
             "review_interval_days": 30, "evidence": "synthetic mapping evidence"}
            for a in spec["accounts"]
        ],
    }


def assignments():
    return [
        {"id": "role-" + role, "type": "role_assignment", "timestamp": iso(NOW),
         "available_at": iso(NOW), "evidence": "synthetic role assignment",
         "account_id": "broker", "product_id": product, "role": role, "amount": amount}
        for role, product, amount in (("core", "equity", "50"), ("dynamic", "bond", "20"))
    ]


class MonitoringTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.database = Path(self.temporary.name) / "journal.sqlite3"
        self.spec = profile()
        initialize(self.database, self.spec, NOW)
        value = opening()
        value["holdings"][0]["price_gbp"] = "16"
        value["holdings"][1]["price_gbp"] = "10"
        import_events(self.database, [value, *assignments()], NOW)
        self.profile = monitoring_profile(self.spec)

    def report(self, now=NOW):
        return project(read(self.database, now), self.profile, now)

    def test_denominator_excludes_savings_lisa_and_cash_without_writes(self):
        before = self.database.read_bytes()
        report = self.report()
        self.assertEqual(report["eligible_holdings_gbp"], "1000")
        self.assertEqual(report["dynamic_pct"], "20")
        self.assertEqual(self.database.read_bytes(), before)
        self.profile["accounts"][1].update(wrapper="LISA", denominator=False)
        self.assertIsNone(self.report()["dynamic_pct"])
        self.assertTrue(any(f["rule"] == "lisa_evidence" for f in self.report()["findings"]))
        self.assertFalse(report["accounting_writes"])
        self.assertFalse(report["affects_trading"])

    def test_excluded_savings_do_not_change_holdings_ratio_and_unassigned_blocks(self):
        first = self.report()["dynamic_pct"]
        from test_investment_opening import event
        import_events(self.database, [
            event("more-savings", "contribution", NOW, "bank", amount_gbp="10000")], NOW)
        self.assertEqual(self.report()["dynamic_pct"], first)
        ledger = read(self.database, NOW)
        ledger.role_holdings[("broker", "equity", "core")] -= 1
        ledger.role_holdings[("broker", "equity", "unassigned")] = 1
        self.assertIsNone(project(ledger, self.profile, NOW)["dynamic_pct"])

    def test_exact_freshness_boundary_missing_and_future_evidence(self):
        self.assertIsNotNone(self.report(NOW + timedelta(seconds=86399))["dynamic_pct"])
        self.assertIsNone(self.report(NOW + timedelta(seconds=86400))["dynamic_pct"])
        ledger = read(self.database, NOW)
        del ledger.marks[("broker", "equity")]
        self.assertIsNone(project(ledger, self.profile, NOW)["eligible_holdings_gbp"])
        self.profile["observed_at"] = iso(NOW + timedelta(seconds=1))
        with self.assertRaises(InvestmentError):
            self.report()

    def test_late_available_marks_do_not_establish_allocation(self):
        from test_investment_opening import event
        effective = NOW + timedelta(seconds=1)
        later = NOW + timedelta(seconds=2)
        marks = []
        for product in ("equity", "bond"):
            mark = event("late-" + product, "mark", effective, "broker", product_id=product, price_gbp="10")
            mark["available_at"] = iso(later)
            marks.append(mark)
        import_events(self.database, marks, later)
        before = self.database.read_bytes()
        result = self.report(later)
        self.assertIsNone(result["dynamic_pct"])
        self.assertIsNone(result["eligible_holdings_gbp"])
        self.assertEqual(self.database.read_bytes(), before)

    def test_declared_complete_observation_cannot_hide_missing_mappings_or_differences(self):
        from test_investment_monitoring_store import attempt
        observation = attempt()
        observation["attempted_at"] = iso(NOW + timedelta(seconds=2))
        observation["observation"].update(
            currency="GBP", cash="999", coverage="complete",
            observed_at=iso(NOW + timedelta(seconds=1)), available_at=iso(NOW + timedelta(seconds=2)))
        result = project(read(self.database, NOW), self.profile, NOW + timedelta(seconds=2),
                         {"revision": 1, "attempts": [observation]})
        rules = {f["rule"] for f in result["findings"]}
        self.assertIn("comparison_unavailable", rules)
        self.assertIn("observation_difference", rules)

    def test_historical_correction_with_cash_observations_requires_attention(self):
        from test_investment_opening import (
            cash_observation, event, historical_correction,
        )

        funded_at = NOW + timedelta(seconds=1)
        observation_at = NOW + timedelta(seconds=2)
        funding = event("funding", "contribution", funded_at, "bank", amount_gbp="100")
        observation = cash_observation(observation_at, balance="1080")
        import_events(self.database, [funding, observation], observation_at)
        correction_at = NOW + timedelta(seconds=3)
        import_events(
            self.database,
            [historical_correction(funding, {"amount_gbp": "80"}, correction_at)],
            correction_at,
        )

        before_projection = self.database.read_bytes()
        result = self.report(correction_at)
        self.assertEqual(self.database.read_bytes(), before_projection)
        finding = next(item for item in result["findings"]
                       if item["rule"] == "historical_correction")
        self.assertEqual(result["status"], "attention")
        self.assertIn("Preserve earlier reports", finding["explanation"])

    def test_bindings_classification_thresholds_and_clocks_fail_closed(self):
        for change in (
            lambda p: p.update(investment_profile_sha256="wrong"),
            lambda p: p["accounts"][0].update(denominator=True),
            lambda p: p["accounts"][1].update(wrapper="LISA"),
            lambda p: p.update(concentration_threshold_pct="NaN"),
            lambda p: p["accounts"][0].update(max_age_seconds=True),
            lambda p: p.update(observed_at=None),
        ):
            value = deepcopy(self.profile)
            change(value)
            with self.assertRaises(InvestmentError):
                project(read(self.database, NOW), value, NOW)

    def test_illustrative_range_is_not_a_breach_rule(self):
        self.profile["concentration_threshold_pct"] = None
        findings = self.report()["findings"]
        self.assertFalse(any(f["rule"] == "concentration" for f in findings))
        self.assertTrue(any(f["rule"] == "cost_coverage" for f in findings))
        self.assertEqual(self.report()["providers"][1]["readiness"], "blocked_unvalidated")
