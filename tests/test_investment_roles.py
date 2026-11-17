"""Synthetic v2 role accounting checks with temporary stores."""

import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from dashboard.app import create_app
from fastapi.testclient import TestClient
from investment import InvestmentError
from investment.service import demo_events, demo_profile, proposal_export, snapshot
from investment.store import import_events, initialize
from test_dashboard_api import MemoryEventLoop
from test_investment_opening import NOW, correction, event, iso, opening, profile


def assignment(identifier, role, account, amount, product=None):
    return {
        "id": identifier, "type": "role_assignment", "timestamp": iso(NOW),
        "available_at": iso(NOW), "evidence": "synthetic attribution evidence",
        "account_id": account, "product_id": product, "role": role, "amount": amount,
    }


class InvestmentRolesTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "roles.sqlite3"
        initialize(self.path, profile(), NOW)
        import_events(self.path, [opening()], NOW)

    def test_explicit_roles_split_shared_cash_and_product_without_changing_totals(self):
        before = snapshot(self.path, NOW)
        import_events(self.path, [
            assignment("bank-core", "core", "bank", "700"),
            assignment("bank-dynamic", "dynamic", "bank", "200"),
            assignment("equity-core", "core", "broker", "30", "equity"),
            assignment("equity-dynamic", "dynamic", "broker", "10", "equity"),
        ], NOW)
        report = snapshot(self.path, NOW)
        self.assertEqual(report["equity_gbp"], before["equity_gbp"])
        self.assertEqual(report["since_baseline_gain_gbp"], before["since_baseline_gain_gbp"])
        roles = {item["role"]: item for item in report["role_accounting"]}
        self.assertEqual(roles["core"]["value_gbp"], "1000")
        self.assertEqual(roles["dynamic"]["value_gbp"], "300")
        self.assertEqual(roles["unassigned"]["value_gbp"], "700")
        self.assertEqual(report["proposal"]["status"], "blocked")
        self.assertEqual(report["role_assignments"][0]["evidence"], "synthetic attribution evidence")
        self.assertEqual(report["role_accounting_at"], iso(NOW))
        self.assertEqual(snapshot(self.path, NOW), report)

    def test_dynamic_cannot_spend_core_or_move_core_to_dynamic(self):
        import_events(self.path, [
            assignment("core-cash", "core", "broker", "100"),
        ], NOW)
        buy = event("dynamic-buy", "buy", NOW, "broker", product_id="equity",
                    quantity="1", price_gbp="10", fee_gbp="0")
        buy["role"] = "dynamic"
        with self.assertRaises(InvestmentError):
            import_events(self.path, [buy], NOW)
        with self.assertRaises(InvestmentError):
            import_events(self.path, [
                assignment("not-a-transfer", "dynamic", "broker", "10"),
            ], NOW)
        self.assertEqual(snapshot(self.path, NOW)["equity_gbp"], "2000")

    def test_unassigned_legacy_fills_cannot_consume_assigned_cash(self):
        import_events(self.path, [assignment("core-cash", "core", "broker", "100")], NOW)
        buy = event("legacy-buy", "buy", NOW, "broker", product_id="equity",
                    quantity="1", price_gbp="10", fee_gbp="0")
        with self.assertRaises(InvestmentError):
            import_events(self.path, [buy], NOW)

    def test_role_fills_fees_transfer_and_correction_preserve_aggregate(self):
        interest = event("role-interest", "interest", NOW, "bank", amount_gbp="10")
        interest["role"] = "dynamic"
        interest["role_evidence"] = "Synthetic allocation for dynamic cash"
        corrected = correction(interest, "8", NOW)
        purchase = event("role-buy", "buy", NOW, "broker", product_id="equity",
                         quantity="1", price_gbp="10", fee_gbp="1")
        purchase["role"] = "dynamic"
        transfer = event("role-transfer", "transfer", NOW, "bank", amount_gbp="7",
                         destination_account_id="broker")
        transfer["role"] = "dynamic"
        import_events(self.path, [
            assignment("dynamic-bank", "dynamic", "bank", "100"),
            assignment("dynamic-broker", "dynamic", "broker", "50"),
            interest, corrected, transfer, purchase,
        ], NOW)
        report = snapshot(self.path, NOW)
        roles = {item["role"]: item for item in report["role_accounting"]}
        self.assertEqual(report["equity_gbp"], "2007")
        self.assertEqual(roles["dynamic"]["value_gbp"], "157")
        self.assertEqual(roles["dynamic"]["cash_gbp"], "147")
        self.assertEqual(report["recorded_interest_gbp"], "8")
        self.assertEqual(report["recorded_income_events"][0]["role"], "dynamic")
        self.assertEqual(report["recorded_fee_events"][0]["role"], "dynamic")
        self.assertEqual(report["recorded_flow_events"][0]["role"], "dynamic")
        self.assertEqual(report["recorded_corrections"][0]["role"], "dynamic")
        self.assertEqual(snapshot(self.path, NOW), report)

    def test_later_attribution_does_not_backdate_role_values(self):
        later = NOW + timedelta(hours=1)
        value = assignment("later", "core", "bank", "100")
        value["timestamp"] = value["available_at"] = iso(later)
        import_events(self.path, [value], later)
        report = snapshot(self.path, later)
        self.assertEqual(report["equity_gbp"], "2000")
        self.assertTrue(all(item["value_gbp"] is None for item in report["role_accounting"]))
        self.assertEqual(report["as_of"], iso(NOW))
        self.assertEqual(report["role_accounting_at"], iso(later))

    def test_income_requires_source_assets_in_the_same_role(self):
        import_events(self.path, [
            assignment("core-holding", "core", "broker", "50", "equity"),
            assignment("core-bank", "core", "bank", "1000"),
        ], NOW)
        dividend = event("misattributed-dividend", "dividend", NOW, "broker",
                         product_id="equity", amount_gbp="1")
        dividend["role"] = "dynamic"
        dividend["role_evidence"] = "Synthetic dynamic allocation"
        interest = event("misattributed-interest", "interest", NOW, "bank", amount_gbp="1")
        interest["role"] = "dynamic"
        interest["role_evidence"] = "Synthetic dynamic allocation"
        for value in (dividend, interest):
            with self.subTest(value=value), self.assertRaises(InvestmentError):
                import_events(self.path, [value], NOW)

    def test_shared_source_income_requires_explicit_allocation_evidence(self):
        import_events(self.path, [
            assignment("dynamic-share", "dynamic", "broker", "1", "equity"),
            assignment("dynamic-cash", "dynamic", "bank", "100"),
        ], NOW)
        dividend = event("shared-dividend", "dividend", NOW, "broker",
                         product_id="equity", amount_gbp="1")
        interest = event("shared-interest", "interest", NOW, "bank", amount_gbp="1")
        for value in (dividend, interest):
            value["role"] = "dynamic"
            with self.subTest(value=value), self.assertRaises(InvestmentError):
                import_events(self.path, [value], NOW)
            value["role_evidence"] = "Operator allocated this payment to the dynamic role"
            import_events(self.path, [value], NOW)
        unassigned = event("unassigned-interest", "interest", NOW, "bank", amount_gbp="1")
        with self.assertRaises(InvestmentError):
            import_events(self.path, [unassigned], NOW)
        unassigned["role_evidence"] = "Operator allocated this payment to unassigned cash"
        import_events(self.path, [unassigned], NOW)
        report = snapshot(self.path, NOW)
        self.assertEqual(len(report["recorded_income_events"]), 3)
        self.assertTrue(all(item["role_evidence"] for item in report["recorded_income_events"]))
        self.assertEqual(report["recorded_income_events"][-1]["role"], "unassigned")

    def test_shared_product_split_must_preserve_role_increments(self):
        import_events(self.path, [
            assignment("core-one", "core", "broker", "1", "equity"),
            assignment("dynamic-one", "dynamic", "broker", "1", "equity"),
        ], NOW)
        split = event("invalid-split", "split", NOW, "broker",
                      product_id="equity", ratio="1.5")
        with self.assertRaises(InvestmentError):
            import_events(self.path, [split], NOW)
        self.assertEqual(snapshot(self.path, NOW)["equity_gbp"], "2000")

    def test_activation_refuses_preexisting_off_increment_split(self):
        initial = opening()
        initial["holdings"][0]["quantity"] = "1"
        path = Path(self.directory.name) / "legacy-split.sqlite3"
        initialize(path, profile(), NOW)
        split = event("legacy-split", "split", NOW, "broker", product_id="equity", ratio="1.5")
        import_events(path, [initial, split], NOW)
        before = snapshot(path, NOW)
        with self.assertRaisesRegex(InvestmentError, "increment"):
            import_events(path, [assignment("assign-after-split", "core", "bank", "1")], NOW)
        self.assertEqual(snapshot(path, NOW), before)
        contribution = event("legacy-contribution", "contribution", NOW, "bank", amount_gbp="1")
        import_events(path, [contribution], NOW)
        self.assertEqual(snapshot(path, NOW)["accounts"][0]["cash_gbp"], "1001")

    def test_v1_legacy_proposals_are_blocked_without_role_policy(self):
        path = Path(self.directory.name) / "legacy.sqlite3"
        initialize(path, demo_profile(NOW), NOW)
        import_events(path, demo_events(NOW, contribution=True), NOW)
        proposal = snapshot(path, NOW)["proposal"]
        self.assertEqual(proposal["status"], "blocked")
        self.assertEqual(proposal["orders"], [])
        self.assertEqual(proposal_export(path, NOW)["proposal"], proposal)

    def test_assignment_commit_failure_and_retry_keep_journal_consistent(self):
        attribution = assignment("once", "core", "bank", "100")
        before = snapshot(self.path, NOW)
        with patch("investment.store._append_events", side_effect=InvestmentError("synthetic failure")):
            with self.assertRaises(InvestmentError):
                import_events(self.path, [attribution], NOW)
        self.assertEqual(snapshot(self.path, NOW), before)
        import_events(self.path, [attribution], NOW)
        after = snapshot(self.path, NOW)
        import_events(self.path, [attribution], NOW)
        self.assertEqual(snapshot(self.path, NOW), after)

    def test_dashboard_role_assignment_preview_and_tagged_correction(self):
        app = create_app(serve_frontend=False, clock=lambda: NOW,
                         investment_database=self.path,
                         investment_recording_enabled=True)
        with TestClient(app, base_url="http://127.0.0.1",
                        backend_options={"loop_factory": MemoryEventLoop}) as client:
            session = client.get("/api/investment/v1/recording/session").json()
            headers = {"Origin": "http://127.0.0.1", "X-CSRF-Token": session["csrf"]}
            attribution = assignment("dashboard-role", "dynamic", "bank", "100")
            preview = client.post(
                "/api/investment/v1/recording/preview",
                json={"request_id": "role-preview", "event": attribution}, headers=headers)
            self.assertEqual(preview.status_code, 200, preview.text)
            self.assertEqual(preview.json()["after"]["role_accounting"][1]["cash_gbp"], "100")
            confirmed = client.post("/api/investment/v1/recording/confirm",
                                    json={"preview_id": preview.json()["preview_id"]}, headers=headers)
            self.assertEqual(confirmed.status_code, 200, confirmed.text)
            income = event("role-interest", "interest", NOW, "bank", amount_gbp="10")
            income["role"] = "dynamic"
            income["role_evidence"] = "Synthetic allocation for dynamic cash"
            import_events(self.path, [income], NOW)
            latest = client.get("/api/investment/v1/recording/session").json()[
                "latest_correctable_event"]
            self.assertEqual(latest["role"], "dynamic")
            self.assertEqual(latest["role_evidence"], income["role_evidence"])
            self.assertEqual(latest["currency"], "GBP")
            corrected = correction(income, "8", NOW)
            corrected["replacement_event"] = dict(
                latest, id="replacement-from-session", amount_gbp="8",
                available_at=corrected["available_at"], evidence=corrected["evidence"])
            preview = client.post("/api/investment/v1/recording/preview",
                                  json={"request_id": "correct-role", "event": corrected},
                                  headers=headers)
            self.assertEqual(preview.status_code, 200, preview.text)
            self.assertEqual(preview.json()["after"]["role_accounting"][1]["cash_gbp"], "108")
            self.assertEqual(preview.json()["after"]["recorded_corrections"][0]["role_evidence"],
                             income["role_evidence"])
            confirmed = client.post("/api/investment/v1/recording/confirm",
                                    json={"preview_id": preview.json()["preview_id"]}, headers=headers)
            self.assertEqual(confirmed.status_code, 200, confirmed.text)
            self.assertEqual(snapshot(self.path, NOW)["recorded_corrections"][0]["role_evidence"],
                             income["role_evidence"])


if __name__ == "__main__":
    unittest.main()
