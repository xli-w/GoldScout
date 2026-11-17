"""Synthetic end-to-end v2 onboarding rehearsal using temporary local files."""

from datetime import timedelta
from decimal import Decimal
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from investment.__main__ import main
from investment.core import iso
from test_investment_opening import NOW, cash_observation, event, opening, profile


def _role(identifier, account, product, role, amount):
    return {
        "id": identifier, "type": "role_assignment", "timestamp": iso(NOW),
        "available_at": iso(NOW), "evidence": "Synthetic role classification",
        "account_id": account, "product_id": product, "role": role, "amount": amount,
    }


class InvestmentOnboardingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.database = self.root / "investment.sqlite3"
        self.monitoring_cache = self.root / "monitoring.sqlite3"

    def command(self, arguments, now):
        output, error = StringIO(), StringIO()
        result = main(arguments, stdout=output, stderr=error, now=now)
        self.assertEqual(result, 0, error.getvalue())
        return json.loads(output.getvalue())

    def test_synthetic_onboarding_reconciles_monitors_and_restores(self):
        profile_path = self.root / "profile.json"
        profile_path.write_text(json.dumps(profile()), encoding="utf-8")
        self.command(["init", "--profile", str(profile_path),
                      "--database", str(self.database)], NOW)

        mark_at = NOW + timedelta(days=1)
        events_path = self.root / "events.json"
        events_path.write_text(json.dumps([
            opening(),
            _role("bank-core-cash", "bank", None, "core", "1000"),
            _role("broker-dynamic-cash", "broker", None, "dynamic", "100"),
            _role("equity-core", "broker", "equity", "core", "30"),
            _role("equity-dynamic", "broker", "equity", "dynamic", "20"),
            _role("bond-core", "broker", "bond", "core", "20"),
            event("equity-mark", "mark", mark_at, "broker",
                  product_id="equity", price_gbp="11"),
            event("bond-mark", "mark", mark_at, "broker",
                  product_id="bond", price_gbp="21"),
            cash_observation(timestamp=mark_at, account="bank", balance="1000",
                             identifier="bank-cash-check"),
        ]), encoding="utf-8")
        self.command(["import", "--events", str(events_path),
                      "--database", str(self.database)], mark_at)

        monitoring = self.command([
            "monitor-template", "--database", str(self.database),
            "--max-age-seconds", str(7 * 86400), "--review-interval-days", "30",
        ], mark_at)
        for account in monitoring["accounts"]:
            account.update(
                provider="manual", wrapper="other",
                denominator=account["account_id"] == "broker",
                last_reviewed_at=iso(mark_at),
                evidence="Synthetic onboarding classification; not provider verification",
            )
        monitoring_path = self.root / "monitoring.json"
        monitoring_path.write_text(json.dumps(monitoring), encoding="utf-8")
        self.command([
            "monitor-init", "--database", str(self.database),
            "--monitoring-profile", str(monitoring_path),
            "--cache", str(self.monitoring_cache), "--retention-seconds", str(7 * 86400),
        ], mark_at)

        observed_at = mark_at + timedelta(days=1)
        account_rows = []
        bindings = {item["id"]: item["binding"] for item in profile()["accounts"]}
        for account, cash, holdings in (
                ("bank", "1000", []),
                ("broker", "100", [
                    {"source_product": "equity", "quantity": "50"},
                    {"source_product": "bond", "quantity": "20"},
                ])):
            attempt = {
                "id": "attempt-" + account, "account_id": account, "provider": "manual",
                "attempted_at": iso(observed_at + timedelta(seconds=1)),
                "status": "success", "error_code": None,
                "observation": {
                    "id": "observation-" + account, "account_id": account,
                    "provider": "manual", "observed_at": iso(observed_at),
                    "available_at": iso(observed_at), "currency": "GBP",
                    "cash": cash, "holdings": holdings, "coverage": "complete",
                    "evidence": "Synthetic statement observation",
                },
            }
            if holdings:
                attempt["observation"].update(
                    product_mappings=[
                        {"source_product": product, "product_id": product,
                         "evidence": "Synthetic product mapping"}
                        for product in ("equity", "bond")
                    ],
                    mapping_available_at=iso(mark_at),
                )
            attempt_path = self.root / f"{account}-attempt.json"
            attempt_path.write_text(json.dumps(attempt), encoding="utf-8")
            self.command([
                "monitor-stage", "--database", str(self.database),
                "--monitoring-profile", str(monitoring_path),
                "--cache", str(self.monitoring_cache), "--attempt", str(attempt_path),
            ], observed_at + timedelta(seconds=2))
            account_rows.append({
                "source_account": account, "observed_at": iso(observed_at),
                "available_at": iso(observed_at), "cash_coverage": "complete",
                "holdings_coverage": "complete", "coverage_evidence": "Synthetic statement",
                "cash_gbp": cash, "holdings": holdings,
            })

        reconciliation = {
            "schema_version": 1, "portfolio_id": monitoring["portfolio_id"],
            "currency": "GBP", "source": "synthetic rehearsal",
            "evidence": "Synthetic reconciled account observations",
            "mapping_observed_at": iso(mark_at),
            "account_mappings": [
                {"source_account": account, "account_id": account,
                 "account_binding": bindings[account], "evidence": "Synthetic account mapping"}
                for account in ("bank", "broker")
            ],
            "product_mappings": [
                {"source_product": product, "product_id": product,
                 "evidence": "Synthetic product mapping"}
                for product in ("equity", "bond")
            ],
            "accounts": account_rows,
        }
        reconciliation_path = self.root / "observations.json"
        reconciliation_path.write_text(json.dumps(reconciliation), encoding="utf-8")
        comparison = self.command([
            "reconcile-preview", "--database", str(self.database),
            "--observations", str(reconciliation_path),
            "--max-age-seconds", str(7 * 86400),
        ], observed_at + timedelta(seconds=2))
        self.assertEqual(comparison["status"], "matched")

        journal_before_monitor = self.database.read_bytes()
        report = self.command([
            "monitor", "--database", str(self.database),
            "--monitoring-profile", str(monitoring_path),
            "--cache", str(self.monitoring_cache),
        ], observed_at + timedelta(seconds=2))
        self.assertEqual(report["eligible_holdings_gbp"], "970")
        self.assertEqual(Decimal(report["dynamic_pct"]).quantize(Decimal("0.01")),
                         Decimal("22.68"))
        self.assertTrue(all(item["last_attempt_status"] == "success" and
                            item["freshness"] == "fresh" for item in report["providers"]))
        self.assertFalse(any(item["rule"] in ("cash_difference", "observation_difference")
                             for item in report["findings"]))
        self.assertEqual(self.database.read_bytes(), journal_before_monitor)

        backup = self.root / "investment-backup.sqlite3"
        restored = self.root / "investment-restored.sqlite3"
        self.command(["backup", "--database", str(self.database),
                      "--destination", str(backup)], observed_at + timedelta(seconds=2))
        self.command(["restore", "--database", str(backup),
                      "--destination", str(restored)], observed_at + timedelta(seconds=2))
        restored_report = self.command(["report", "--database", str(restored)],
                                       observed_at + timedelta(seconds=2))
        original_report = self.command(["report", "--database", str(self.database)],
                                       observed_at + timedelta(seconds=2))
        self.assertEqual(restored_report, original_report)
