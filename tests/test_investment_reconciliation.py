"""Offline observation previews use only synthetic, temporary evidence."""

from copy import deepcopy
from datetime import timedelta
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from investment import InvestmentError
from investment.__main__ import main
from investment.store import import_events, initialize
from test_investment_opening import (
    NOW, cash_observation, event, iso, opening, profile, savings_terms_event,
)


def staged():
    return {
        "schema_version": 1,
        "portfolio_id": "opening-test",
        "currency": "GBP",
        "source": "synthetic export",
        "evidence": "operator-reviewed local export",
        "mapping_observed_at": iso(NOW),
        "account_mappings": [
            {"source_account": "source-bank", "account_id": "bank",
             "account_binding": "bank-test", "evidence": "operator mapping"},
            {"source_account": "source-broker", "account_id": "broker",
             "account_binding": "broker-test", "evidence": "operator mapping"},
        ],
        "product_mappings": [
            {"source_product": "source-equity", "product_id": "equity",
             "evidence": "operator mapping"},
            {"source_product": "source-bond", "product_id": "bond",
             "evidence": "operator mapping"},
        ],
        "accounts": [
            {"source_account": "source-bank", "observed_at": iso(NOW + timedelta(seconds=1)),
             "available_at": iso(NOW + timedelta(seconds=2)),
             "cash_coverage": "complete", "holdings_coverage": "complete",
             "coverage_evidence": "operator checked source pages",
             "cash_gbp": "999", "holdings": []},
            {"source_account": "source-broker", "observed_at": iso(NOW + timedelta(seconds=1)),
             "available_at": iso(NOW + timedelta(seconds=2)),
             "cash_coverage": "complete", "holdings_coverage": "complete",
             "coverage_evidence": "operator checked source pages",
             "cash_gbp": "100", "holdings": [
                 {"source_product": "source-equity", "quantity": "51"},
                 {"source_product": "source-bond", "quantity": "20"},
             ]},
        ],
    }


class ObservationPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.database = Path(self.temporary.name) / "investment.sqlite3"
        initialize(self.database, profile(), NOW)
        import_events(self.database, [opening()], NOW)
        self.now = NOW + timedelta(seconds=3)

    def preview(self, value, now=None, maximum_age=60):
        from investment.reconciliation import preview_observations
        return preview_observations(self.database, value, maximum_age, now or self.now)

    def test_exact_discrepancies_and_cli_do_not_write_source_or_journal(self):
        data = staged()
        path = Path(self.temporary.name) / "observations.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        before_db, before_source = self.database.read_bytes(), path.read_bytes()
        result = self.preview(data)
        self.assertEqual(result["revision"], 1)
        bank, broker = result["accounts"]
        self.assertEqual((bank["cash"]["status"], bank["cash"]["difference"]), ("difference", "-1"))
        self.assertEqual((broker["holdings"][0]["status"], broker["holdings"][0]["difference"]),
                         ("difference", "1"))
        self.assertEqual(broker["holdings"][1]["status"], "match")
        out, err = StringIO(), StringIO()
        self.assertEqual(main(["reconcile-preview", "--database", str(self.database),
                               "--observations", str(path), "--max-age-seconds", "60"],
                              stdout=out, stderr=err, now=self.now), 0)
        self.assertEqual(json.loads(out.getvalue()), result)
        self.assertEqual(err.getvalue(), "")
        self.assertEqual(self.database.read_bytes(), before_db)
        self.assertEqual(path.read_bytes(), before_source)

    def test_missing_partial_unmapped_and_stale_are_not_clean(self):
        data = staged()
        data["accounts"][1]["holdings_coverage"] = "partial"
        data["accounts"][1]["holdings"] = [
            {"source_product": "not-mapped", "quantity": "2"}]
        result = self.preview(data, now=NOW + timedelta(seconds=61), maximum_age=60)
        broker = result["accounts"][1]
        self.assertEqual(result["status"], "attention")
        self.assertEqual(broker["freshness"], "stale")
        self.assertEqual(broker["holdings"][0]["status"], "unmapped")
        self.assertEqual(set(broker["missing_recorded_products"]), {"equity", "bond"})
        self.assertEqual(broker["holdings_coverage"], "partial")

    def test_matched_status_requires_all_declared_accounts_and_complete_coverage(self):
        data = staged()
        data["accounts"][0]["cash_gbp"] = "1000"
        data["accounts"][1]["holdings"][0]["quantity"] = "50"
        result = self.preview(data)
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["missing_recorded_accounts"], [])
        data["accounts"].pop(0)
        result = self.preview(data)
        self.assertEqual(result["status"], "attention")
        self.assertEqual(result["missing_recorded_accounts"], ["bank"])
        data["accounts"][0]["cash_coverage"] = "unknown"
        self.assertEqual(self.preview(data)["status"], "attention")

    def test_unmapped_account_and_explicit_empty_cash_are_unknown(self):
        data = staged()
        data["account_mappings"] = []
        data["accounts"][0]["cash_gbp"] = None
        result = self.preview(data)
        bank = result["accounts"][0]
        self.assertEqual(bank["comparison_unavailable_reason"], "unmapped_account")
        self.assertEqual(bank["cash"]["status"], "unavailable")
        self.assertIsNone(bank["cash"]["recorded"])
        self.assertEqual(result["missing_recorded_accounts"], ["bank", "broker"])

    def test_later_or_delayed_journal_cannot_be_compared_as_observation_clock(self):
        later = NOW + timedelta(seconds=2)
        import_events(self.database, [event("interest", "interest", later, "bank",
                                            amount_gbp="1")], later)
        result = self.preview(staged())
        self.assertEqual(result["accounts"][0]["cash"]["status"], "unavailable")
        self.assertIsNone(result["accounts"][0]["cash"]["difference"])

    def test_invalid_future_ambiguous_mappings_and_values_fail_closed(self):
        for change in (
            lambda value: value["accounts"][0].update(available_at=iso(self.now + timedelta(seconds=1))),
            lambda value: value["accounts"][0].update(cash_gbp="NaN"),
            lambda value: value["account_mappings"][0].update(account_binding="wrong"),
            lambda value: value["product_mappings"].append(deepcopy(value["product_mappings"][0])),
            lambda value: value["accounts"][1]["holdings"].append(
                deepcopy(value["accounts"][1]["holdings"][0])),
        ):
            value = staged()
            change(value)
            with self.subTest(value=value), self.assertRaises(InvestmentError):
                self.preview(value)

    def test_same_clock_is_uncertain_and_unopened_store_is_not_zero(self):
        value = staged()
        value["accounts"][0]["observed_at"] = iso(NOW)
        self.assertEqual(self.preview(value)["accounts"][0]["cash"]["status"], "unavailable")
        database = Path(self.temporary.name) / "unopened.sqlite3"
        initialize(database, profile(), NOW)
        from investment.reconciliation import preview_observations
        with self.assertRaisesRegex(InvestmentError, "opening"):
            preview_observations(database, value, 60, self.now)

    def test_delayed_journal_evidence_is_not_backdated(self):
        database = Path(self.temporary.name) / "delayed.sqlite3"
        initialize(database, profile(), NOW)
        entry = opening()
        entry["available_at"] = iso(NOW + timedelta(seconds=2))
        import_events(database, [entry], self.now)
        from investment.reconciliation import preview_observations
        result = preview_observations(database, staged(), 60, self.now)
        self.assertEqual(result["accounts"][0]["cash"]["status"], "unavailable")
        self.assertIsNone(result["accounts"][0]["cash"]["difference"])

    def test_existing_cash_discrepancy_remains_visible_after_a_later_match(self):
        observed_at = NOW + timedelta(seconds=1)
        import_events(self.database, [cash_observation(observed_at)], observed_at)
        data = staged()
        data["accounts"][0].update(observed_at=iso(NOW + timedelta(seconds=2)),
                                   available_at=iso(NOW + timedelta(seconds=2)), cash_gbp="1000")
        data["accounts"][1].update(observed_at=iso(NOW + timedelta(seconds=2)),
                                   available_at=iso(NOW + timedelta(seconds=2)))
        data["accounts"][1]["holdings"][0]["quantity"] = "50"
        result = self.preview(data)
        self.assertEqual(result["accounts"][0]["cash"]["status"], "match")
        self.assertEqual(result["status"], "attention")
        self.assertEqual(result["previous_cash_discrepancies"][0]["difference_gbp"], "-1")

    def test_recorded_sixteen_place_cash_and_large_quantity_are_exact(self):
        spec = profile()
        spec["products"][0]["quantity_increment"] = "0.00000001"
        database = Path(self.temporary.name) / "precise.sqlite3"
        initialize(database, spec, NOW)
        initial = opening()
        initial["holdings"][0]["quantity"] = "999999999999999"
        later = NOW + timedelta(seconds=1)
        purchase = event("tiny-fill", "buy", later, "broker", product_id="equity",
                         quantity="0.00000001", price_gbp="0.00000001", fee_gbp="0")
        import_events(database, [initial, purchase], later)
        data = staged()
        for account in data["accounts"]:
            account.update(observed_at=iso(NOW + timedelta(seconds=2)),
                           available_at=iso(NOW + timedelta(seconds=2)))
        data["accounts"][1]["cash_gbp"] = "99.9999999999999999"
        data["accounts"][1]["holdings"][0]["quantity"] = "999999999999999.00000001"
        from investment.reconciliation import preview_observations
        broker = preview_observations(database, data, 60, self.now)["accounts"][1]
        self.assertEqual(broker["cash"]["status"], "match")
        self.assertEqual(broker["holdings"][0]["status"], "match")

    def test_mapping_not_yet_known_at_source_clock_is_unavailable(self):
        data = staged()
        data["mapping_observed_at"] = iso(NOW + timedelta(seconds=2))
        bank = self.preview(data)["accounts"][0]
        self.assertEqual(bank["cash"]["status"], "unavailable")
        self.assertEqual(bank["comparison_unavailable_reason"], "mapping_not_available_at_observation")

    def test_metadata_events_at_or_after_observation_prevent_comparison(self):
        source_time = NOW + timedelta(seconds=1)
        role = {
            "id": "role", "type": "role_assignment", "timestamp": iso(source_time),
            "available_at": iso(source_time), "evidence": "synthetic role",
            "account_id": "bank", "product_id": None, "role": "core", "amount": "1",
        }
        cases = [
            role,
            savings_terms_event(source_time),
            savings_terms_event(NOW, available_at=NOW + timedelta(seconds=2)),
        ]
        from investment.reconciliation import preview_observations
        for index, value in enumerate(cases):
            database = Path(self.temporary.name) / f"metadata-{index}.sqlite3"
            initialize(database, profile(), NOW)
            import_events(database, [opening(), value], self.now)
            with self.subTest(kind=value["type"], available=value["available_at"]):
                result = preview_observations(database, staged(), 60, self.now)
                self.assertEqual(result["accounts"][0]["cash"]["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
