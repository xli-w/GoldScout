import copy
import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from decimal import InvalidOperation, localcontext
from pathlib import Path
from unittest.mock import patch

from bot.campaign_data import CampaignDataset, CampaignDatasetError, import_campaign_dataset
from bot.campaign_data.__main__ import main as campaign_cli
from bot.campaign_data import readiness as readiness_module
from bot.campaign_data.readiness import audit_readiness, normalized_product_view


class CampaignReadinessTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.dataset_dir = self.root / "snapshots"
        self.rows = [
            ("2025-03-27T16:30:00Z", "2025-03-27T17:00:00Z"),
            ("2025-03-28T16:30:00Z", "2025-03-28T17:00:00Z"),
            ("2025-03-31T15:30:00Z", "2025-03-31T16:00:00Z"),
        ]
        self.quote = ("10", "11", "9", "10", "100")
        self.quote_overrides = {}
        self.extra_sources = {}
        self.cash_rows = [("2025-03-27T00:00:00Z", "2025-03-26T12:00:00Z", "0.04")]
        self.manifest = {
            "schema_version": 1,
            "provenance": {
                "provider": "synthetic fixture",
                "license_terms": "test-only",
                "retrieved_at": "2025-05-01T00:00:00Z",
            },
            "products": [{
                "id": "gbp-product", "identifier": "TEST-GBP", "currency": "GBP",
                "resolution": "daily", "timestamp_semantics": "period_end",
                "price_basis": "unadjusted_ohlcv", "file": "prices.csv",
            }],
            "fx": [],
            "cash_rates": [{
                "id": "cash", "benchmark_identifier": "TEST-CASH", "currency": "GBP",
                "rate_unit": "annual_decimal", "resolution": "daily",
                "timestamp_semantics": "effective_time", "file": "cash.csv",
            }],
            "corporate_actions": [],
        }
        evidence = {"status": "recorded", "reference": "synthetic fixture only", "as_of": "2025-05-01"}
        self.profile = {
            "schema_version": 1, "dataset_id": "", "synthetic": True,
            "products": [{
                "product_id": "gbp-product", "identifier": "TEST-GBP",
                "share_class": "synthetic", "isin": "TEST-ONLY",
                "venue": "XLON", "calendar": "XLON", "timezone": "Europe/London",
                "currency": "GBP", "price_unit": "GBP_PER_UNIT", "price_basis": "unadjusted_ohlcv",
                "start_date": "2025-03-27", "end_date": "2025-03-31", "inception_date": "2020-01-01",
                "audit_clock": "next_session_open", "max_availability_delay_seconds": 3600,
                "evidence": {name: copy.deepcopy(evidence) for name in
                             ("product_access", "license", "normalization", "price_basis", "actions")},
            }],
            "cash": {
                "series_id": "cash", "benchmark_identifier": "TEST-CASH", "rate_unit": "annual_decimal",
                "max_rate_age_seconds": 7 * 86400,
                "day_count": "unresolved", "compounding": "unresolved",
                "evidence": {name: copy.deepcopy(evidence) for name in ("eligibility", "terms", "license")},
            },
        }

    def import_fixture(self):
        source = self.root / "input"
        source.mkdir(exist_ok=True)
        (source / "prices.csv").write_text(
            "timestamp,available_at,open,high,low,close,volume\n" +
            "".join(f"{stamp},{available},{','.join(self.quote_overrides.get(stamp, self.quote))}\n"
                    for stamp, available in self.rows),
            encoding="utf-8",
        )
        (source / "cash.csv").write_text(
            "timestamp,available_at,annual_rate\n" +
            "".join(f"{stamp},{available},{rate}\n" for stamp, available, rate in self.cash_rows),
            encoding="utf-8",
        )
        for filename, payload in self.extra_sources.items():
            (source / filename).write_text(payload, encoding="utf-8")
        manifest = source / "manifest.json"
        manifest.write_text(json.dumps(self.manifest), encoding="utf-8")
        dataset_id = import_campaign_dataset(manifest, self.dataset_dir)
        self.profile["dataset_id"] = dataset_id
        return dataset_id

    def audit(self, dataset_id=None):
        if dataset_id is None:
            dataset_id = self.import_fixture()
        profile_path = self.root / "profile.json"
        profile_path.write_text(json.dumps(self.profile), encoding="utf-8")
        return audit_readiness(self.dataset_dir, dataset_id, profile_path)

    def normalized(self, dataset_id=None, as_of="2025-03-31T16:00:00Z", product_id="gbp-product"):
        if dataset_id is None:
            dataset_id = self.import_fixture()
        profile_path = self.root / "profile.json"
        profile_path.write_text(json.dumps(self.profile), encoding="utf-8")
        return normalized_product_view(self.dataset_dir, dataset_id, profile_path, product_id, as_of)

    def check(self, report, name, subject="gbp-product"):
        return next(check for check in report["checks"] if check["name"] == name and check["subject"] == subject)

    def test_schema_valid_missing_session_fails_readiness_without_mutation(self):
        self.rows.pop(1)
        dataset_id = self.import_fixture()
        dataset = CampaignDataset.open(self.dataset_dir, dataset_id)
        before = {path.relative_to(dataset.directory): path.read_bytes()
                  for path in dataset.directory.rglob("*") if path.is_file()}
        report = self.audit(dataset_id)
        check = self.check(report, "session_completeness")
        self.assertEqual(check["status"], "fail")
        self.assertEqual(check["issues"], 1)
        self.assertIn("2025-03-28", check["examples"][0])
        self.assertEqual(report["status"], "fail")
        after = {path.relative_to(dataset.directory): path.read_bytes()
                 for path in dataset.directory.rglob("*") if path.is_file()}
        self.assertEqual(before, after)

    def test_dst_and_early_close_holidays_pass_mechanical_checks(self):
        report = self.audit()
        self.assertEqual(self.check(report, "session_completeness")["status"], "pass")
        self.assertEqual(self.check(report, "bar_availability")["status"], "pass")
        self.assertEqual(self.check(report, "rate_availability", "cash")["status"], "pass")
        self.assertEqual(report["status"], "unresolved")
        self.assertTrue(report["remaining_campaign_gates"])
        self.rows = [
            ("2024-12-23T16:30:00Z", "2024-12-23T17:00:00Z"),
            ("2024-12-24T12:30:00Z", "2024-12-24T13:00:00Z"),
            ("2024-12-27T16:30:00Z", "2024-12-27T17:00:00Z"),
        ]
        self.profile["products"][0].update(start_date="2024-12-23", end_date="2024-12-27")
        self.cash_rows = [("2024-12-23T00:00:00Z", "2024-12-22T00:00:00Z", "0.04")]
        report = self.audit()
        self.assertEqual(report["products"][0]["expected_sessions"], 3)
        self.assertEqual(self.check(report, "session_grid")["status"], "pass")
        self.assertEqual(self.check(report, "session_completeness")["status"], "pass")

    def test_missing_requested_edge_and_off_grid_close_are_not_trimmed(self):
        self.profile["products"][0]["start_date"] = "2025-03-26"
        report = self.audit()
        self.assertEqual(self.check(report, "session_completeness")["status"], "fail")
        self.rows[-1] = ("2025-03-31T16:30:00Z", "2025-03-31T17:00:00Z")
        report = self.audit()
        self.assertEqual(self.check(report, "session_grid")["status"], "fail")
        self.assertEqual(self.check(report, "session_completeness")["issues"], 2)

    def test_late_bar_fails_deadline_and_next_open_availability(self):
        self.rows[0] = ("2025-03-27T16:30:00Z", "2025-03-28T09:00:00Z")
        report = self.audit()
        self.assertEqual(self.check(report, "bar_availability")["status"], "fail")
        self.assertIn("next session open", self.check(report, "bar_availability")["examples"][0])

    def test_publication_and_effective_rate_age_limits_are_inclusive(self):
        self.rows[0] = ("2025-03-27T16:30:00Z", "2025-03-28T08:00:00Z")
        self.profile["products"][0]["max_availability_delay_seconds"] = 15 * 3600 + 1800
        self.profile["cash"]["max_rate_age_seconds"] = 5 * 86400 + 7 * 3600
        report = self.audit()
        self.assertEqual(self.check(report, "bar_availability")["status"], "pass")
        self.assertEqual(self.check(report, "rate_availability", "cash")["status"], "pass")
        self.rows[0] = ("2025-03-27T16:30:00Z", "2025-03-28T08:00:01Z")
        self.profile["cash"]["max_rate_age_seconds"] -= 1
        report = self.audit()
        self.assertEqual(self.check(report, "bar_availability")["issues"], 1)
        self.assertEqual(self.check(report, "rate_availability", "cash")["issues"], 1)

    def test_future_effective_and_unavailable_latest_cash_rates_do_not_fall_back(self):
        self.cash_rows.extend([
            ("2025-03-28T00:00:00Z", "2025-03-28T09:00:00Z", "0.05"),
            ("2025-04-02T00:00:00Z", "2025-03-26T00:00:00Z", "0.06"),
        ])
        report = self.audit()
        self.assertEqual(self.check(report, "rate_availability", "cash")["status"], "fail")
        self.assertEqual(self.check(report, "rate_availability", "cash")["issues"], 1)
        self.cash_rows.pop(1)
        self.profile["cash"]["max_rate_age_seconds"] = 86400
        report = self.audit()
        self.assertEqual(self.check(report, "rate_availability", "cash")["status"], "fail")
        self.assertIn("stale", self.check(report, "rate_availability", "cash")["examples"][0])

    def test_units_weekly_and_unsupported_calendar_are_explicit(self):
        for unit in ("GBX", "UNKNOWN"):
            with self.subTest(unit=unit):
                self.profile["products"][0]["price_unit"] = unit
                report = self.audit()
                self.assertEqual(self.check(report, "gbp_units")["status"], "unsupported")
                self.assertIsNone(report["products"][0]["price_scale_to_gbp"])
                self.assertEqual(report["products"][0]["normalized_price_examples"], [])
        self.profile["products"][0]["price_unit"] = "GBP_PER_UNIT"
        self.manifest["products"][0]["resolution"] = "weekly"
        self.assertEqual(self.check(self.audit(), "daily_execution")["status"], "unsupported")
        self.manifest["products"][0]["resolution"] = "daily"
        self.profile["products"][0]["calendar"] = "XNYS"
        self.assertEqual(self.check(self.audit(), "session_calendar")["status"], "unsupported")
        self.profile["products"][0].update(calendar="XLON", start_date="1900-01-01", end_date="1900-01-02")
        self.assertEqual(self.check(self.audit(), "session_calendar")["status"], "unsupported")

    def test_explicit_pence_quotes_are_exactly_normalized_without_changing_source(self):
        self.profile["products"][0]["price_unit"] = "GBX_PER_UNIT"
        self.quote = ("123.456789012345678901234567890123", "150", "100", "125", "100")
        self.rows[-1] = ("2025-03-31T16:30:00+01:00", "2025-03-31T17:00:00+01:00")
        dataset_id = self.import_fixture()
        dataset = CampaignDataset.open(self.dataset_dir, dataset_id)
        source_before = dataset.source_file("prices.csv")
        with localcontext() as context:
            context.prec = 5
            report = self.audit(dataset_id)
        self.assertEqual(self.check(report, "gbp_units")["status"], "pass")
        self.assertEqual(report["products"][0]["price_scale_to_gbp"], "0.01")
        examples = report["products"][0]["normalized_price_examples"]
        self.assertEqual(len(examples), 2)
        self.assertEqual(examples[0], {
            "timestamp": "2025-03-27T16:30:00Z",
            "available_at": "2025-03-27T17:00:00Z",
            "open": "1.23456789012345678901234567890123",
            "high": "1.50", "low": "1.00", "close": "1.25", "volume": "100",
        })
        self.assertEqual(examples[-1]["timestamp"], "2025-03-31T15:30:00Z")
        self.assertEqual(examples[-1]["available_at"], "2025-03-31T16:00:00Z")
        self.assertEqual(dataset.source_file("prices.csv"), source_before)
        self.assertEqual(report["status"], "unresolved")
        self.assertTrue(report["remaining_campaign_gates"])

    def test_pound_quotes_remain_unscaled_and_unverified_pence_evidence_remains_unresolved(self):
        report = self.audit()
        self.assertEqual(report["products"][0]["price_scale_to_gbp"], "1")
        self.assertEqual(report["products"][0]["normalized_price_examples"][0]["close"], "10")
        self.profile["synthetic"] = False
        self.profile["products"][0]["price_unit"] = "GBX_PER_UNIT"
        self.profile["products"][0]["evidence"]["normalization"] = {
            "status": "unresolved", "reference": "", "as_of": None,
        }
        report = self.audit()
        self.assertEqual(self.check(report, "gbp_units")["status"], "pass")
        self.assertEqual(self.check(report, "evidence_recording")["status"], "unresolved")
        self.assertEqual(report["status"], "unresolved")

    def test_long_numeric_examples_are_omitted_instead_of_truncated(self):
        self.profile["products"][0]["price_unit"] = "GBX_PER_UNIT"
        self.quote = ("1." + "0" * 130, "11", "1", "10", "100")
        report = self.audit()
        self.assertEqual(self.check(report, "gbp_units")["status"], "pass")
        self.assertEqual(report["products"][0]["normalized_price_examples"], [])
        self.assertEqual(report["products"][0]["omitted_normalized_examples"], 2)
        self.assertLess(len(json.dumps(report)), 20000)

    def test_scientific_notation_examples_do_not_depend_on_decimal_context(self):
        self.profile["products"][0]["price_unit"] = "GBX_PER_UNIT"
        self.quote = ("1e-10", "2e-10", "5e-11", "1e-10", "100")
        dataset_id = self.import_fixture()
        with localcontext() as context:
            context.capitals = 0
            lower = self.audit(dataset_id)
        with localcontext() as context:
            context.capitals = 1
            upper = self.audit(dataset_id)
        self.assertEqual(lower, upper)
        self.assertEqual(lower["products"][0]["normalized_price_examples"][0]["open"], "1E-12")

    def test_submicrosecond_source_times_are_rejected_without_rounding(self):
        baseline_rows = list(self.rows)
        baseline_cash = list(self.cash_rows)
        for field in ("bar_timestamp", "bar_available", "cash_effective", "cash_available",
                      "retrieved_at", "product_retrieved_at", "cash_retrieved_at"):
            with self.subTest(field=field):
                self.rows = list(baseline_rows)
                self.cash_rows = list(baseline_cash)
                self.manifest["provenance"]["retrieved_at"] = "2025-05-01T00:00:00Z"
                self.manifest["products"][0].pop("provenance", None)
                self.manifest["cash_rates"][0].pop("provenance", None)
                if field == "bar_timestamp":
                    self.rows[0] = ("2025-03-27T16:30:00.0000001Z", "2025-03-27T17:00:00Z")
                elif field == "bar_available":
                    self.rows[0] = ("2025-03-27T16:30:00Z", "2025-03-28T08:00:00.0000001Z")
                elif field == "cash_effective":
                    self.cash_rows.append(("2025-03-28T08:00:00.0000001Z", "2025-03-26T12:00:00Z", "0.05"))
                elif field == "cash_available":
                    self.cash_rows.append(("2025-03-28T00:00:00Z", "2025-03-28T08:00:00.0000001Z", "0.05"))
                elif field == "retrieved_at":
                    self.manifest["provenance"]["retrieved_at"] = "2025-05-01T00:00:00.0000001Z"
                else:
                    provenance = copy.deepcopy(self.manifest["provenance"])
                    provenance["retrieved_at"] = "2025-05-01T00:00:00.0000001Z"
                    group = "products" if field == "product_retrieved_at" else "cash_rates"
                    self.manifest[group][0]["provenance"] = provenance
                with self.assertRaisesRegex(CampaignDatasetError, "sub-microsecond"):
                    self.audit()

    def test_extra_zero_timestamp_digits_remain_exactly_representable(self):
        self.rows[0] = ("2025-03-27T16:30:00.000000000Z", "2025-03-27T17:00:00.0000000Z")
        report = self.audit()
        self.assertEqual(self.check(report, "session_grid")["status"], "pass")
        self.assertEqual(self.check(report, "bar_availability")["status"], "pass")
        self.assertEqual(report["products"][0]["normalized_price_examples"][0]["timestamp"],
                         "2025-03-27T16:30:00Z")

    def test_cli_reports_unsupported_timestamp_precision(self):
        self.rows[0] = ("2025-03-27T16:30:00Z", "2025-03-28T08:00:00.0000001Z")
        dataset_id = self.import_fixture()
        profile_path = self.root / "profile.json"
        profile_path.write_text(json.dumps(self.profile), encoding="utf-8")
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = campaign_cli(["readiness", dataset_id, "--dataset-dir", str(self.dataset_dir),
                                 "--profile", str(profile_path)])
        self.assertEqual(code, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("sub-microsecond", stderr.getvalue())

    def test_examples_are_limited_to_selected_session_without_duplicates(self):
        self.profile["products"][0].update(
            price_unit="GBX_PER_UNIT", start_date="2025-03-28", end_date="2025-03-28",
        )
        report = self.audit()
        examples = report["products"][0]["normalized_price_examples"]
        self.assertEqual(len(examples), 1)
        self.assertEqual(examples[0]["timestamp"], "2025-03-28T16:30:00Z")
        self.assertEqual(examples[0]["close"], "0.10")
        self.assertEqual(report["products"][0]["outside_interval_rows"], 2)

    def test_complete_pence_view_preserves_all_bars_availability_and_provenance(self):
        self.profile["products"][0]["price_unit"] = "GBX_PER_UNIT"
        provenance = {"provider": "product fixture", "license_terms": "test-only",
                      "retrieved_at": "2025-05-01T01:00:00+01:00", "metadata": {"source": "synthetic"}}
        self.manifest["products"][0]["provenance"] = provenance
        self.quote = ("123.456789012345678901234567890123", "150", "100", "125", "100")
        self.quote_overrides["2025-03-28T16:30:00Z"] = ("200", "210", "190", "200", "12")
        dataset_id = self.import_fixture()
        dataset = CampaignDataset.open(self.dataset_dir, dataset_id)
        source_before = dataset.source_file("prices.csv")
        with localcontext() as context:
            context.prec = 5
            context.capitals = 0
            view = self.normalized(dataset_id)
        self.assertEqual(view["scope"], "offline_gbp_normalized_daily_view")
        self.assertEqual(view["dataset_id"], dataset_id)
        self.assertEqual(view["profile_sha256"], hashlib.sha256((self.root / "profile.json").read_bytes()).hexdigest())
        self.assertEqual(view["source_sha256"], dataset.checksums()["prices.csv"])
        self.assertEqual(view["source_provenance"], provenance)
        self.assertEqual(view["source_retrieved_at"], "2025-05-01T00:00:00Z")
        self.assertEqual(view["source_price_unit"], "GBX_PER_UNIT")
        self.assertEqual(view["price_unit"], "GBP_PER_UNIT")
        self.assertEqual(view["as_of"], "2025-03-31T16:00:00Z")
        self.assertEqual(view["readiness_status"], "unresolved")
        self.assertFalse(view["external_facts_verified"])
        self.assertFalse(view["point_in_time_vintage_verified"])
        self.assertFalse(view["supports_performance"])
        self.assertEqual(view["bar_count"], 3)
        self.assertEqual(view["bars"][0]["open"], "1.23456789012345678901234567890123")
        self.assertEqual(view["bars"][1], {
            "timestamp": "2025-03-28T16:30:00Z", "available_at": "2025-03-28T17:00:00Z",
            "open": "2.00", "high": "2.10", "low": "1.90", "close": "2.00", "volume": "12",
        })
        self.assertEqual(view["bars"][-1]["available_at"], view["as_of"])
        self.assertEqual(dataset.source_file("prices.csv"), source_before)
        self.assertEqual(view, self.normalized(dataset_id))

    def test_normalize_cli_keeps_nested_provenance_output_bounded(self):
        nested = [0] * 1000
        for _ in range(50):
            nested = [nested]
        self.manifest["provenance"]["metadata"] = {"nested": nested}
        dataset_id = self.import_fixture()
        profile_path = self.root / "profile.json"
        profile_path.write_text(json.dumps(self.profile), encoding="utf-8")
        output, error = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            code = campaign_cli(["normalize", dataset_id, "gbp-product", "--dataset-dir", str(self.dataset_dir),
                                 "--profile", str(profile_path), "--as-of", "2025-03-31T16:00:00Z"])
        self.assertEqual(code, 3)
        self.assertLess(len(output.getvalue()), 15000)
        self.assertEqual(json.loads(output.getvalue())["source_provenance"]["metadata"]["nested"], nested)

    def test_normalized_view_never_clips_requested_bars_or_uses_future_availability(self):
        dataset_id = self.import_fixture()
        with self.assertRaisesRegex(CampaignDatasetError, "available.*as_of"):
            self.normalized(dataset_id, as_of="2025-03-31T15:59:59.999999Z")
        for cutoff in ("2025-03-31T16:00:00", "2025-03-31T16:00:00.0000001Z",
                       "0001-01-01T00:00:00+14:00", "9999-12-31T23:59:59-14:00",
                       "2025-03-31T15:00:00-00:60"):
            with self.subTest(cutoff=cutoff), self.assertRaises(CampaignDatasetError):
                self.normalized(dataset_id, as_of=cutoff)
        view = self.normalized(dataset_id, as_of="2025-03-31T17:00:00+01:00")
        self.assertEqual(view["bar_count"], 3)
        self.assertEqual(view["as_of"], "2025-03-31T16:00:00Z")

    def test_normalized_view_rejects_missing_sessions_and_unsupported_actions_or_units(self):
        self.rows.pop(1)
        with self.assertRaisesRegex(CampaignDatasetError, "session_completeness"):
            self.normalized()
        self.rows.insert(1, ("2025-03-28T16:30:00Z", "2025-03-28T17:00:00Z"))
        self.rows[0] = ("2025-03-27T16:30:00Z", "2025-03-28T09:00:00Z")
        with self.assertRaisesRegex(CampaignDatasetError, "bar_availability"):
            self.normalized()
        self.rows[0] = ("2025-03-27T16:30:00Z", "2025-03-27T17:00:00Z")
        self.profile["cash"]["max_rate_age_seconds"] = 86400
        with self.assertRaisesRegex(CampaignDatasetError, "rate_availability"):
            self.normalized()
        self.profile["cash"]["max_rate_age_seconds"] = 7 * 86400
        self.profile["products"][0]["price_unit"] = "GBX"
        with self.assertRaisesRegex(CampaignDatasetError, "gbp_units"):
            self.normalized()
        self.profile["products"][0]["price_unit"] = "GBP_PER_UNIT"
        self.manifest["corporate_actions"] = [{
            "id": "distribution", "product_id": "gbp-product", "type": "distribution",
            "currency": "GBP", "effective_at": "2025-03-28T00:00:00Z",
            "available_at": "2025-03-27T00:00:00Z", "terms": {"amount": 0.1},
        }]
        with self.assertRaisesRegex(CampaignDatasetError, "action_semantics"):
            self.normalized()

    def test_normalized_view_rejects_exact_ohlc_inconsistency_hidden_by_float_rounding(self):
        self.quote_overrides["2025-03-28T16:30:00Z"] = (
            "1.0000000000000000000001", "1", "1", "1", "100",
        )
        dataset_id = self.import_fixture()
        self.assertEqual(self.check(self.audit(dataset_id), "session_completeness")["status"], "pass")
        with self.assertRaisesRegex(CampaignDatasetError, "OHLC"):
            self.normalized(dataset_id)

    def test_normalized_view_reports_unrepresentable_volume_as_a_dataset_error(self):
        self.quote = ("10", "11", "9", "10", "1e-" + "9" * 30)
        dataset_id = self.import_fixture()
        for trap in (True, False):
            with self.subTest(trap=trap), localcontext() as context:
                context.traps[InvalidOperation] = trap
                with self.assertRaisesRegex(CampaignDatasetError, "decimal"):
                    self.normalized(dataset_id)
        self.quote = ("10", "11", "9", "10", "-1e-500")
        dataset_id = self.import_fixture()
        with self.assertRaisesRegex(CampaignDatasetError, "volume"):
            self.normalized(dataset_id)

    def test_normalized_view_fails_instead_of_omitting_an_oversized_middle_bar(self):
        self.quote_overrides["2025-03-28T16:30:00Z"] = ("1." + "0" * 130, "11", "1", "10", "100")
        dataset_id = self.import_fixture()
        self.assertEqual(self.check(self.audit(dataset_id), "session_completeness")["status"], "pass")
        with self.assertRaisesRegex(CampaignDatasetError, "128"):
            self.normalized(dataset_id)

    def test_normalized_view_uses_only_declared_interval_and_selected_product(self):
        self.profile["products"][0].update(start_date="2025-03-28", end_date="2025-03-28")
        dataset_id = self.import_fixture()
        view = self.normalized(dataset_id)
        self.assertEqual(view["bar_count"], 1)
        self.assertEqual(view["bars"][0]["timestamp"], "2025-03-28T16:30:00Z")
        self.assertEqual(view["bars"][0]["close"], "10")
        with self.assertRaisesRegex(CampaignDatasetError, "selected"):
            self.normalized(dataset_id, product_id="other")
        self.profile["dataset_id"] = "a" * 64
        with self.assertRaisesRegex(CampaignDatasetError, "dataset_id"):
            self.normalized(dataset_id)

    def test_normalized_view_checks_all_selected_products_and_uses_selected_source(self):
        source = copy.deepcopy(self.manifest["products"][0])
        source.update(id="other-product", identifier="OTHER", file="other.csv")
        self.manifest["products"].append(source)
        product = copy.deepcopy(self.profile["products"][0])
        product.update(product_id="other-product", identifier="OTHER", price_unit="GBX_PER_UNIT")
        self.profile["products"].append(product)
        header = "timestamp,available_at,open,high,low,close,volume\n"
        rows = [f"{stamp},{available},200,210,190,200,12\n" for stamp, available in self.rows]
        self.extra_sources["other.csv"] = header + "".join(rows)
        dataset_id = self.import_fixture()
        dataset = CampaignDataset.open(self.dataset_dir, dataset_id)
        view = self.normalized(dataset_id, product_id="other-product")
        self.assertEqual(view["source_file"], "other.csv")
        self.assertEqual(view["source_sha256"], dataset.checksums()["other.csv"])
        self.assertEqual(view["bars"][1]["close"], "2.00")
        self.extra_sources["other.csv"] = header + rows[0] + rows[2]
        dataset_id = self.import_fixture()
        with self.assertRaisesRegex(CampaignDatasetError, "other-product.session_completeness"):
            self.normalized(dataset_id)

    def test_malformed_utc_offsets_are_rejected_for_all_encountered_source_times(self):
        original_rows = list(self.rows)
        original_cash = list(self.cash_rows)
        for field in ("bar_timestamp", "bar_available", "cash_effective", "cash_available", "retrieved_at"):
            self.rows = list(original_rows)
            self.cash_rows = list(original_cash)
            self.manifest["provenance"]["retrieved_at"] = "2025-05-01T00:00:00Z"
            if field == "bar_timestamp":
                self.rows[0] = ("2025-03-27T17:30:00+00:60", "2025-03-27T17:00:00Z")
            elif field == "bar_available":
                self.rows[0] = ("2025-03-27T16:30:00Z", "2025-03-27T16:00:00-00:60")
            elif field == "cash_effective":
                self.cash_rows[0] = ("2025-03-27T01:00:00+00:60", "2025-03-26T12:00:00Z", "0.04")
            elif field == "cash_available":
                self.cash_rows[0] = ("2025-03-27T00:00:00Z", "2025-03-26T11:00:00-00:60", "0.04")
            else:
                self.manifest["provenance"]["retrieved_at"] = "2025-05-01T01:00:00+00:60"
            with self.subTest(field=field), self.assertRaisesRegex(CampaignDatasetError, "offset"):
                self.audit()

    def test_normalized_view_rejects_malformed_cash_offsets_after_final_audit_clock(self):
        self.cash_rows.extend([
            ("2025-04-01T07:15:00Z", "2025-03-26T12:00:00Z", "0.05"),
            ("2025-04-01T06:30:00-00:60", "2025-03-26T12:00:00Z", "0.06"),
        ])
        dataset_id = self.import_fixture()
        with self.assertRaisesRegex(CampaignDatasetError, "offset"):
            self.normalized(dataset_id)
        args = ["normalize", dataset_id, "gbp-product", "--dataset-dir", str(self.dataset_dir),
                "--profile", str(self.root / "profile.json"), "--as-of", "2025-03-31T16:00:00Z"]
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(campaign_cli(args), 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("offset", stderr.getvalue())

    def test_normalized_view_rechecks_source_after_audit(self):
        dataset_id = self.import_fixture()
        original_audit = readiness_module._audit_dataset
        path = self.dataset_dir / "datasets" / dataset_id / "source" / "prices.csv"

        def audit_then_corrupt(*args):
            report = original_audit(*args)
            path.write_bytes(path.read_bytes().replace(b",100", b",101"))
            return report

        with patch("bot.campaign_data.readiness._audit_dataset", side_effect=audit_then_corrupt):
            with self.assertRaisesRegex(CampaignDatasetError, "checksum"):
                self.normalized(dataset_id)

    def test_normalize_cli_reports_unresolved_view_and_fails_without_partial_output(self):
        dataset_id = self.import_fixture()
        profile_path = self.root / "profile.json"
        profile_path.write_text(json.dumps(self.profile), encoding="utf-8")
        args = ["normalize", dataset_id, "gbp-product", "--dataset-dir", str(self.dataset_dir),
                "--profile", str(profile_path), "--as-of", "2025-03-31T16:00:00Z"]
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(campaign_cli(args), 3)
        view = json.loads(stdout.getvalue())
        self.assertEqual(view["bar_count"], 3)
        self.assertEqual(view["readiness_status"], "unresolved")
        self.assertFalse(view["supports_performance"])
        self.assertIn("unresolved", stderr.getvalue().lower())
        self.profile["synthetic"] = False
        profile_path.write_text(json.dumps(self.profile), encoding="utf-8")
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(campaign_cli(args), 0)
        view = json.loads(stdout.getvalue())
        self.assertEqual(view["readiness_status"], "pass")
        self.assertFalse(view["external_facts_verified"])
        self.assertFalse(view["point_in_time_vintage_verified"])
        self.assertFalse(view["supports_performance"])
        for cutoff in ("2025-03-31T15:59:59Z", "0001-01-01T00:00:00+14:00",
                       "2025-03-31T15:00:00-00:60"):
            args[-1] = cutoff
            stdout, stderr = io.StringIO(), io.StringIO()
            with self.subTest(cutoff=cutoff):
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    self.assertEqual(campaign_cli(args), 2)
                self.assertEqual(stdout.getvalue(), "")
                self.assertIn("as_of", stderr.getvalue())

    def test_currency_identity_and_price_basis_mismatches_fail(self):
        self.profile["products"][0]["currency"] = "USD"
        self.assertEqual(self.check(self.audit(), "product_identity")["status"], "fail")
        self.assertEqual(self.check(self.audit(), "gbp_units")["status"], "unsupported")
        self.profile["products"][0].update(currency="GBP", identifier="OTHER")
        self.assertEqual(self.check(self.audit(), "product_identity")["status"], "fail")
        self.profile["products"][0].update(identifier="TEST-GBP", price_basis="total_return_adjusted_ohlcv")
        self.assertEqual(self.check(self.audit(), "product_identity")["status"], "fail")

    def test_adjusted_prices_and_actions_are_not_declared_executable(self):
        self.manifest["products"][0]["price_basis"] = "split_adjusted_ohlcv"
        self.profile["products"][0]["price_basis"] = "split_adjusted_ohlcv"
        self.assertEqual(self.check(self.audit(), "action_semantics")["status"], "unsupported")
        self.manifest["products"][0]["price_basis"] = "unadjusted_ohlcv"
        self.profile["products"][0]["price_basis"] = "unadjusted_ohlcv"
        self.manifest["corporate_actions"] = [{
            "id": "distribution", "product_id": "gbp-product", "type": "distribution",
            "currency": "GBP", "effective_at": "2025-03-28T00:00:00Z",
            "available_at": "2025-03-27T00:00:00Z", "terms": {"amount": 0.1},
        }]
        self.assertEqual(self.check(self.audit(), "action_semantics")["status"], "unsupported")

    def test_profile_hash_evidence_and_output_are_deterministic(self):
        dataset_id = self.import_fixture()
        first = self.audit(dataset_id)
        self.assertEqual(first, self.audit(dataset_id))
        self.profile["products"][0]["evidence"]["license"] = {
            "status": "unresolved", "reference": "", "as_of": None,
        }
        second = self.audit(dataset_id)
        self.assertNotEqual(first["profile_sha256"], second["profile_sha256"])
        self.assertEqual(self.check(second, "evidence_recording")["status"], "unresolved")
        self.assertEqual(second["external_evidence"]["products"][0]["evidence"]["license"]["status"], "unresolved")

    def test_strict_profile_binding_corruption_and_invalid_limits_are_rejected(self):
        dataset_id = self.import_fixture()
        original = copy.deepcopy(self.profile)
        for change in ({"schema_version": True}, {"extra": 1}, {"dataset_id": "a" * 64}):
            self.profile = dict(original, **change)
            with self.subTest(change=change), self.assertRaises(CampaignDatasetError):
                self.audit(dataset_id)
        self.profile = original
        self.profile["products"][0]["max_availability_delay_seconds"] = -1
        with self.assertRaises(CampaignDatasetError):
            self.audit(dataset_id)
        self.profile["products"][0]["max_availability_delay_seconds"] = 3600
        path = self.dataset_dir / "datasets" / dataset_id / "source" / "prices.csv"
        original_bytes = path.read_bytes()
        changed_bytes = original_bytes.replace(b",100", b",101")
        self.assertNotEqual(original_bytes, changed_bytes)
        path.write_bytes(changed_bytes)
        with self.assertRaises(CampaignDatasetError):
            self.audit(dataset_id)

    def test_cli_reports_unresolved_and_failed_checks_with_nonzero_exit(self):
        self.profile["products"][0]["price_unit"] = "GBX_PER_UNIT"
        dataset_id = self.import_fixture()
        self.audit(dataset_id)
        args = ["readiness", dataset_id, "--dataset-dir", str(self.dataset_dir),
                "--profile", str(self.root / "profile.json")]
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = campaign_cli(args)
        self.assertNotEqual(code, 0)
        unresolved = json.loads(stdout.getvalue())
        self.assertEqual(unresolved["status"], "unresolved")
        self.assertEqual(unresolved["products"][0]["price_scale_to_gbp"], "0.01")
        self.assertEqual(unresolved["products"][0]["normalized_price_examples"][0]["close"], "0.10")
        self.assertIn("readiness", stderr.getvalue().lower())
        self.profile["synthetic"] = False
        self.audit(dataset_id)
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(campaign_cli(args), 0)
        scoped_pass = json.loads(stdout.getvalue())
        self.assertEqual(scoped_pass["status"], "pass")
        self.assertTrue(scoped_pass["remaining_campaign_gates"])
        self.rows.pop(1)
        dataset_id = self.import_fixture()
        self.audit(dataset_id)
        args[1] = dataset_id
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertNotEqual(campaign_cli(args), 0)
        self.assertEqual(json.loads(stdout.getvalue())["status"], "fail")


if __name__ == "__main__":
    unittest.main()
