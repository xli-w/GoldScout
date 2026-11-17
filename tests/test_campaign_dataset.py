import hashlib
import io
import json
import errno
import os
import shutil
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

from research_data import CampaignDataset, CampaignDatasetError, import_campaign_dataset
from research_data import dataset as dataset_module
from research_data import readiness as readiness_module
from research_data.__main__ import main as campaign_cli


GBP_BARS = (
    "timestamp,available_at,open,high,low,close,volume\n"
    "2025-01-01T00:00:00Z,2025-01-02T00:00:00Z,100,103,99,102,10\n"
    "2025-01-02T00:00:00Z,2025-01-03T00:00:00Z,102,104,101,103,12\n"
)
USD_BARS = (
    "timestamp,available_at,open,high,low,close,volume\n"
    "2025-01-01T00:00:00Z,2025-01-02T00:00:00Z,50,53,49,52,20\n"
    "2025-01-08T00:00:00Z,2025-01-09T00:00:00Z,52,54,51,53,21\n"
)
USD_FX = (
    "timestamp,available_at,gbp_per_unit\n"
    "2025-01-01T00:00:00Z,2025-01-01T18:00:00Z,0.79\n"
    "2025-01-05T00:00:00Z,2025-01-05T18:00:00Z,0.80\n"
    "2025-01-08T00:00:00Z,2025-01-08T18:00:00Z,0.81\n"
)
CASH_RATES = (
    "timestamp,available_at,annual_rate\n"
    "2025-01-01T00:00:00Z,2025-01-01T09:00:00Z,0.04\n"
    "2025-01-02T00:00:00Z,2025-01-02T09:00:00Z,0.041\n"
)


def _action(**changes):
    action = {
        "id": "split-1",
        "product_id": "us-product",
        "type": "split",
        "currency": "USD",
        "effective_at": "2025-01-08T00:00:00Z",
        "available_at": "2025-01-02T12:00:00Z",
        "terms": {"numerator": 2, "denominator": 1},
        "source_metadata": {"notice": "synthetic input"},
    }
    action.update(changes)
    return action


def _manifest(**changes):
    manifest = {
        "schema_version": 1,
        "provenance": {
            "provider": "synthetic fixture",
            "license_terms": "test-only; no external data",
            "retrieved_at": "2025-01-10T12:00:00Z",
            "metadata": {"source_note": "retained verbatim"},
        },
        "products": [
            {
                "id": "gb-product",
                "identifier": "TEST-GBP",
                "currency": "GBP",
                "resolution": "daily",
                "timestamp_semantics": "period_end",
                "price_basis": "unadjusted_ohlcv",
                "file": "gbp_daily.csv",
                "metadata": {"share_class": "synthetic"},
            },
            {
                "id": "us-product",
                "identifier": "TEST-USD",
                "currency": "USD",
                "resolution": "weekly",
                "timestamp_semantics": "period_end",
                "price_basis": "unadjusted_ohlcv",
                "file": "usd_weekly.csv",
            },
        ],
        "fx": [
            {
                "id": "usd-gbp-fx",
                "currency": "USD",
                "direction": "GBP_PER_UNIT",
                "resolution": "daily",
                "timestamp_semantics": "observation_time",
                "file": "usd_fx.csv",
                "metadata": {"fixing": "synthetic"},
            },
        ],
        "cash_rates": [
            {
                "id": "gbp-cash",
                "benchmark_identifier": "synthetic cash benchmark",
                "currency": "GBP",
                "rate_unit": "annual_decimal",
                "resolution": "daily",
                "timestamp_semantics": "effective_time",
                "file": "cash.csv",
            },
        ],
        "corporate_actions": [_action()],
    }
    manifest.update(changes)
    return manifest


class CampaignDatasetTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.input_dir = self.root / "input"
        self.input_dir.mkdir()
        self.dataset_dir = self.root / "snapshots"
        self.write_fixture()

    def write_fixture(self, manifest=None, csv_overrides=None):
        csv_files = {
            "gbp_daily.csv": GBP_BARS,
            "usd_weekly.csv": USD_BARS,
            "usd_fx.csv": USD_FX,
            "cash.csv": CASH_RATES,
        }
        csv_files.update(csv_overrides or {})
        for filename, payload in csv_files.items():
            (self.input_dir / filename).write_text(payload, encoding="utf-8", newline="")
        source_manifest = _manifest() if manifest is None else manifest
        path = self.input_dir / "manifest.json"
        path.write_text(json.dumps(source_manifest, separators=(",", ":")), encoding="utf-8")
        return path

    def write_fx_profile(self, identifier, clock_values, **changes):
        profile = {
            "schema_version": 1,
            "dataset_id": identifier,
            "series_id": "usd-gbp-fx",
            "synthetic": True,
            "max_age_seconds": 86400,
            "clocks": clock_values,
        }
        profile.update(changes)
        path = self.root / "fx-profile.json"
        path.write_text(json.dumps(profile), encoding="utf-8")
        return path

    def test_import_reopen_preserves_payload_metadata_and_checksums(self):
        manifest_path = self.input_dir / "manifest.json"
        original_manifest = manifest_path.read_bytes()
        original_fx = (self.input_dir / "usd_fx.csv").read_bytes()
        identifier = import_campaign_dataset(manifest_path, self.dataset_dir)

        dataset = CampaignDataset.open(self.dataset_dir, identifier)
        self.assertEqual(len(identifier), 64)
        self.assertEqual(dataset.source_manifest, _manifest())
        self.assertEqual((dataset.directory / "source" / "manifest.json").read_bytes(), original_manifest)
        self.assertEqual(dataset.source_file("usd_fx.csv"), original_fx)
        self.assertEqual(dataset.checksums()["usd_fx.csv"], hashlib.sha256(original_fx).hexdigest())
        self.assertEqual(dataset.manifest["series"]["products"]["us-product"]["rows"], 2)
        self.assertEqual(dataset.manifest["series"]["products"]["us-product"]["resolution"], "weekly")
        self.assertEqual(
            dataset.manifest["series"]["products"]["us-product"]["price_basis"],
            "unadjusted_ohlcv",
        )
        self.assertTrue(any("calendar" in note for note in dataset.manifest["unresolved_checks"]))

    def test_series_provenance_override_is_retained(self):
        manifest = _manifest()
        manifest["products"][0]["provenance"] = {
            "provider": "separate product source",
            "license_terms": "synthetic test-only terms",
            "retrieved_at": "2025-01-04T00:00:00Z",
        }
        self.write_fixture(manifest)
        identifier = import_campaign_dataset(self.input_dir / "manifest.json", self.dataset_dir)
        dataset = CampaignDataset.open(self.dataset_dir, identifier)
        self.assertEqual(
            dataset.source_manifest["products"][0]["provenance"]["provider"],
            "separate product source",
        )

    def test_content_identity_is_idempotent(self):
        first = import_campaign_dataset(self.input_dir / "manifest.json", self.dataset_dir)
        second = import_campaign_dataset(self.input_dir / "manifest.json", self.dataset_dir)
        self.assertEqual(first, second)
        self.assertEqual(len(list((self.dataset_dir / "datasets").iterdir())), 1)

    def test_failed_staging_write_cleans_partial_import_and_preserves_existing_snapshot(self):
        manifest_path = self.input_dir / "manifest.json"
        existing_id = import_campaign_dataset(manifest_path, self.dataset_dir)
        changed_manifest = _manifest()
        changed_manifest["provenance"]["metadata"]["source_note"] = "different content identity"
        self.write_fixture(changed_manifest)

        original_write_bytes = Path.write_bytes

        def fail_during_staging(path, payload):
            if (
                path.name == "usd_fx.csv"
                and path.parent.name == "source"
                and path.parent.parent.name.startswith(".campaign-staging-")
            ):
                raise OSError("simulated staging interruption")
            return original_write_bytes(path, payload)

        with patch.object(Path, "write_bytes", fail_during_staging):
            with self.assertRaisesRegex(OSError, "simulated staging interruption"):
                import_campaign_dataset(manifest_path, self.dataset_dir)

        dataset_root = self.dataset_dir / "datasets"
        self.assertEqual([path.name for path in dataset_root.iterdir()], [existing_id])
        self.assertEqual(CampaignDataset.open(self.dataset_dir, existing_id).dataset_id, existing_id)

    def test_concurrent_identical_imports_publish_one_complete_snapshot(self):
        manifest_path = self.input_dir / "manifest.json"
        rename_barrier = Barrier(2)
        original_rename = Path.rename

        def synchronize_publication(path, target):
            if path.name.startswith(".campaign-staging-") and target.parent.name == "datasets":
                rename_barrier.wait(timeout=10)
            return original_rename(path, target)

        with patch.object(Path, "rename", synchronize_publication):
            with ThreadPoolExecutor(max_workers=2) as workers:
                identifiers = list(workers.map(
                    lambda _: import_campaign_dataset(manifest_path, self.dataset_dir),
                    range(2),
                ))

        self.assertEqual(identifiers[0], identifiers[1])
        dataset_root = self.dataset_dir / "datasets"
        self.assertEqual([path.name for path in dataset_root.iterdir()], [identifiers[0]])
        self.assertEqual(
            CampaignDataset.open(self.dataset_dir, identifiers[0]).dataset_id,
            identifiers[0],
        )

    def test_import_reuses_verified_snapshot_after_enotempty_publication_race(self):
        manifest_path = self.input_dir / "manifest.json"
        original_rename = Path.rename

        def publish_competitor(path, target):
            if path.name.startswith(".campaign-staging-") and target.parent.name == "datasets":
                competitor = path.parent / ".competing-staging"
                shutil.copytree(path, competitor)
                original_rename(competitor, target)
                raise OSError(errno.ENOTEMPTY, "simulated concurrent publication", str(target))
            return original_rename(path, target)

        with patch.object(Path, "rename", publish_competitor):
            identifier = import_campaign_dataset(manifest_path, self.dataset_dir)
        self.assertEqual(CampaignDataset.open(self.dataset_dir, identifier).dataset_id, identifier)
        self.assertEqual([path.name for path in (self.dataset_dir / "datasets").iterdir()], [identifier])

    def test_filename_aliases_are_rejected_case_insensitively(self):
        manifest = _manifest()
        manifest["products"][1]["file"] = "GBP_DAILY.csv"
        self.write_fixture(manifest)
        with self.assertRaisesRegex(CampaignDatasetError, "duplicate source filename"):
            import_campaign_dataset(self.input_dir / "manifest.json", self.dataset_dir)

    def test_replaced_fifo_is_rejected_without_blocking(self):
        if os.name == "nt" or not hasattr(os, "mkfifo"):
            self.skipTest("FIFO replacement is only available on POSIX")
        source = self.root / "source.csv"
        source.write_bytes(b"regular file")
        original_open = os.open

        def replace_with_fifo(path, flags, *args, **kwargs):
            if Path(path) == source:
                source.unlink()
                os.mkfifo(source)
            return original_open(path, flags, *args, **kwargs)

        with patch.object(dataset_module.os, "open", replace_with_fifo):
            with self.assertRaisesRegex(CampaignDatasetError, "changed or is unsafe"):
                dataset_module._read_bounded_file(source, 1024, "test source", "too large")

    def test_import_and_validate_cli_are_separate_and_local_only(self):
        output = io.StringIO()
        with patch("sys.stdout", output):
            result = campaign_cli([
                "import", str(self.input_dir / "manifest.json"), "--dataset-dir", str(self.dataset_dir),
            ])
            identifier = next((self.dataset_dir / "datasets").iterdir()).name
            validate_result = campaign_cli(["validate", identifier, "--dataset-dir", str(self.dataset_dir)])
        self.assertEqual((result, validate_result), (0, 0))
        self.assertIn("Coverage is timestamp-envelope only", output.getvalue())

    def test_raw_source_tampering_is_detected_on_reopen(self):
        identifier = import_campaign_dataset(self.input_dir / "manifest.json", self.dataset_dir)
        dataset = CampaignDataset.open(self.dataset_dir, identifier)
        path = dataset.directory / "source" / "usd_fx.csv"
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaisesRegex(CampaignDatasetError, "checksum/manifest mismatch"):
            CampaignDataset.open(self.dataset_dir, identifier)

    def test_source_file_rechecks_hash_after_open(self):
        identifier = import_campaign_dataset(self.input_dir / "manifest.json", self.dataset_dir)
        dataset = CampaignDataset.open(self.dataset_dir, identifier)
        path = dataset.directory / "source" / "usd_fx.csv"
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaisesRegex(CampaignDatasetError, "source checksum mismatch"):
            dataset.source_file("usd_fx.csv")

    def test_manifest_tampering_is_detected_on_reopen(self):
        identifier = import_campaign_dataset(self.input_dir / "manifest.json", self.dataset_dir)
        dataset = CampaignDataset.open(self.dataset_dir, identifier)
        path = dataset.directory / "dataset.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["unresolved_checks"] = []
        path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(CampaignDatasetError, "checksum/manifest mismatch"):
            CampaignDataset.open(self.dataset_dir, identifier)

    def test_reopen_enforces_input_manifest_and_aggregate_size_limits(self):
        identifier = import_campaign_dataset(self.input_dir / "manifest.json", self.dataset_dir)
        with patch("research_data.dataset.MAX_MANIFEST_BYTES", 1):
            with self.assertRaisesRegex(CampaignDatasetError, "source manifest exceeds"):
                CampaignDataset.open(self.dataset_dir, identifier)
        with patch("research_data.dataset.MAX_MANIFEST_BYTES", 2 * 1024 * 1024), \
                patch("research_data.dataset.MAX_TOTAL_SOURCE_BYTES", 1):
            with self.assertRaisesRegex(CampaignDatasetError, "aggregate size limit"):
                CampaignDataset.open(self.dataset_dir, identifier)

    def test_naive_manifest_and_series_timestamps_are_rejected(self):
        cases = [
            ("provenance retrieval", _manifest(provenance=dict(_manifest()["provenance"], retrieved_at="2025-01-10T12:00:00")),
             None),
            ("action effective", _manifest(corporate_actions=[_action(effective_at="2025-01-08T00:00:00")]), None),
            ("action available", _manifest(corporate_actions=[_action(available_at="2025-01-02T12:00:00")]), None),
            ("bar observation", None, {"gbp_daily.csv": GBP_BARS.replace("2025-01-01T00:00:00Z", "2025-01-01T00:00:00", 1)}),
            ("bar availability", None, {"gbp_daily.csv": GBP_BARS.replace("2025-01-02T00:00:00Z", "2025-01-02T00:00:00", 1)}),
        ]
        for label, manifest, csv_overrides in cases:
            with self.subTest(label=label):
                self.write_fixture(manifest, csv_overrides)
                with self.assertRaises(CampaignDatasetError):
                    import_campaign_dataset(self.input_dir / "manifest.json", self.dataset_dir)

    def test_invalid_ohlcv_is_rejected(self):
        invalid = GBP_BARS.replace(",100,103,99,102,", ",100,101,99,102,")
        self.write_fixture(csv_overrides={"gbp_daily.csv": invalid})
        with self.assertRaisesRegex(CampaignDatasetError, "high violates OHLC"):
            import_campaign_dataset(self.input_dir / "manifest.json", self.dataset_dir)

    def test_series_availability_must_not_precede_observation_time(self):
        early = GBP_BARS.replace(
            "2025-01-02T00:00:00Z,100,103",
            "2024-12-31T23:59:00Z,100,103",
            1,
        )
        self.write_fixture(csv_overrides={"gbp_daily.csv": early})
        with self.assertRaisesRegex(CampaignDatasetError, "available_at must not precede"):
            import_campaign_dataset(self.input_dir / "manifest.json", self.dataset_dir)

    def test_series_availability_must_not_exceed_source_retrieval_time(self):
        manifest = _manifest(provenance=dict(
            _manifest()["provenance"], retrieved_at="2025-01-08T12:00:00Z",
        ))
        self.write_fixture(manifest)
        with self.assertRaisesRegex(CampaignDatasetError, "available_at exceeds source retrieved_at"):
            import_campaign_dataset(self.input_dir / "manifest.json", self.dataset_dir)

        manifest = _manifest()
        manifest["fx"][0]["provenance"] = {
            "provider": "separate FX source",
            "license_terms": "synthetic test-only terms",
            "retrieved_at": "2025-01-08T12:00:00Z",
        }
        self.write_fixture(manifest)
        with self.assertRaisesRegex(CampaignDatasetError, "available_at exceeds source retrieved_at"):
            import_campaign_dataset(self.input_dir / "manifest.json", self.dataset_dir)

        manifest = _manifest()
        manifest["cash_rates"][0]["provenance"] = {
            "provider": "separate cash source",
            "license_terms": "synthetic test-only terms",
            "retrieved_at": "2025-01-01T08:00:00Z",
        }
        self.write_fixture(manifest)
        with self.assertRaisesRegex(CampaignDatasetError, "available_at exceeds source retrieved_at"):
            import_campaign_dataset(self.input_dir / "manifest.json", self.dataset_dir)

        manifest = _manifest()
        manifest["corporate_actions"][0]["provenance"] = {
            "provider": "separate actions source",
            "license_terms": "synthetic test-only terms",
            "retrieved_at": "2025-01-02T00:00:00Z",
        }
        self.write_fixture(manifest)
        with self.assertRaisesRegex(CampaignDatasetError, "available_at exceeds source retrieved_at"):
            import_campaign_dataset(self.input_dir / "manifest.json", self.dataset_dir)

    def test_cash_rate_can_be_available_before_its_effective_time(self):
        cash = CASH_RATES.replace(
            "2025-01-02T00:00:00Z,2025-01-02T09:00:00Z,0.041",
            "2025-01-02T00:00:00Z,2025-01-01T10:00:00Z,0.041",
        )
        self.write_fixture(csv_overrides={"cash.csv": cash})
        identifier = import_campaign_dataset(self.input_dir / "manifest.json", self.dataset_dir)
        dataset = CampaignDataset.open(self.dataset_dir, identifier)
        self.assertEqual(dataset.manifest["series"]["cash_rates"]["gbp-cash"]["rows"], 2)

    def test_series_timestamps_must_be_sorted_unique_and_numeric_values_finite(self):
        duplicate = GBP_BARS.replace(
            "2025-01-02T00:00:00Z,2025-01-03T00:00:00Z,102,104,101,103,12\n",
            "2025-01-01T00:00:00Z,2025-01-03T00:00:00Z,102,104,101,103,12\n",
        )
        self.write_fixture(csv_overrides={"gbp_daily.csv": duplicate})
        with self.assertRaisesRegex(CampaignDatasetError, "sorted and unique"):
            import_campaign_dataset(self.input_dir / "manifest.json", self.dataset_dir)

        unsorted = GBP_BARS.replace(
            "2025-01-01T00:00:00Z,2025-01-02T00:00:00Z,100,103,99,102,10\n"
            "2025-01-02T00:00:00Z,2025-01-03T00:00:00Z,102,104,101,103,12\n",
            "2025-01-02T00:00:00Z,2025-01-03T00:00:00Z,100,103,99,102,10\n"
            "2025-01-01T00:00:00Z,2025-01-04T00:00:00Z,102,104,101,103,12\n",
        )
        self.write_fixture(csv_overrides={"gbp_daily.csv": unsorted})
        with self.assertRaisesRegex(CampaignDatasetError, "sorted and unique"):
            import_campaign_dataset(self.input_dir / "manifest.json", self.dataset_dir)

        non_finite = GBP_BARS.replace(",100,103,99,102,10\n", ",100,103,99,inf,10\n")
        self.write_fixture(csv_overrides={"gbp_daily.csv": non_finite})
        with self.assertRaisesRegex(CampaignDatasetError, "must be finite"):
            import_campaign_dataset(self.input_dir / "manifest.json", self.dataset_dir)

    def test_cash_rate_benchmark_is_required_and_corporate_terms_are_numeric(self):
        manifest = _manifest(cash_rates=[])
        self.write_fixture(manifest)
        with self.assertRaisesRegex(CampaignDatasetError, "cash-rate benchmark"):
            import_campaign_dataset(self.input_dir / "manifest.json", self.dataset_dir)

        manifest = _manifest(corporate_actions=[_action(terms={"numerator": True, "denominator": 1})])
        self.write_fixture(manifest)
        with self.assertRaisesRegex(CampaignDatasetError, "must be numeric"):
            import_campaign_dataset(self.input_dir / "manifest.json", self.dataset_dir)

    def test_missing_fx_coverage_is_rejected(self):
        narrow_fx = (
            "timestamp,available_at,gbp_per_unit\n"
            "2025-01-02T00:00:00Z,2025-01-02T18:00:00Z,0.80\n"
            "2025-01-08T00:00:00Z,2025-01-08T18:00:00Z,0.81\n"
        )
        self.write_fixture(csv_overrides={"usd_fx.csv": narrow_fx})
        with self.assertRaisesRegex(CampaignDatasetError, "do not span product history"):
            import_campaign_dataset(self.input_dir / "manifest.json", self.dataset_dir)

    def test_fx_clock_audit_detects_unavailable_and_stale_observations(self):
        identifier = import_campaign_dataset(self.input_dir / "manifest.json", self.dataset_dir)
        profile_path = self.write_fx_profile(identifier, [
            "2025-01-01T12:00:00Z",
            "2025-01-03T00:00:00Z",
            "2025-01-05T18:00:00Z",
        ])

        report = readiness_module.audit_fx_readiness(self.dataset_dir, identifier, profile_path)

        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["coverage"]["issues"], 2)
        self.assertIn("no available observation", report["coverage"]["examples"][0])
        self.assertIn("stale", report["coverage"]["examples"][1])
        self.assertEqual(report["coverage"]["passed_clocks"], 1)
        self.assertEqual(report["source_sha256"], hashlib.sha256(USD_FX.encode("utf-8")).hexdigest())
        self.assertFalse(report["supports_performance"])
        self.assertFalse(report["point_in_time_vintage_verified"])

    def test_fx_clock_audit_uses_only_published_observations_and_inclusive_age(self):
        delayed = USD_FX.replace("2025-01-05T18:00:00Z", "2025-01-07T00:00:00Z")
        self.write_fixture(csv_overrides={"usd_fx.csv": delayed})
        identifier = import_campaign_dataset(self.input_dir / "manifest.json", self.dataset_dir)
        profile_path = self.write_fx_profile(identifier, [
            "2025-01-01T18:00:00Z",
            "2025-01-05T00:00:00Z",
            "2025-01-05T00:00:00.000001Z",
            "2025-01-07T00:00:00Z",
        ], max_age_seconds=4 * 86400)

        report = readiness_module.audit_fx_readiness(self.dataset_dir, identifier, profile_path)

        self.assertEqual(report["coverage"]["issues"], 1)
        self.assertEqual(report["coverage"]["passed_clocks"], 3)
        samples = report["coverage"]["sample_observations"]
        self.assertEqual(samples[1]["observation_at"], "2025-01-01T00:00:00Z")
        self.assertEqual(samples[2]["observation_at"], "2025-01-05T00:00:00Z")
        self.assertEqual(samples[2]["available_at"], samples[2]["clock"])
        self.assertIn("stale", report["coverage"]["examples"][0])

    def test_fx_clock_audit_does_not_replace_newer_data_with_late_old_publication(self):
        late_old = USD_FX.replace("2025-01-01T18:00:00Z", "2025-01-06T00:00:00Z")
        self.write_fixture(csv_overrides={"usd_fx.csv": late_old})
        identifier = import_campaign_dataset(self.input_dir / "manifest.json", self.dataset_dir)
        profile_path = self.write_fx_profile(identifier, [
            "2025-01-05T18:00:00Z", "2025-01-06T00:00:00Z",
        ], max_age_seconds=2 * 86400)

        report = readiness_module.audit_fx_readiness(self.dataset_dir, identifier, profile_path)

        self.assertEqual(report["status"], "unresolved")
        self.assertEqual(report["coverage"]["status"], "pass")
        self.assertEqual(
            [sample["observation_at"] for sample in report["coverage"]["sample_observations"]],
            ["2025-01-05T00:00:00Z", "2025-01-05T00:00:00Z"],
        )

    def test_fx_clock_profile_rejects_invalid_clocks_and_bindings(self):
        identifier = import_campaign_dataset(self.input_dir / "manifest.json", self.dataset_dir)
        valid_clocks = ["2025-01-01T18:00:00Z"]
        cases = [
            ({"dataset_id": "a" * 64}, "dataset_id"),
            ({"series_id": "unknown"}, "series_id"),
            ({"max_age_seconds": True}, "max_age_seconds"),
            ({"max_age_seconds": -1}, "max_age_seconds"),
            ({"clocks": []}, "clocks"),
            ({"clocks": ["2025-01-01T18:00:00Z", "2025-01-01T19:00:00+01:00"]}, "strictly increasing"),
            ({"clocks": ["2025-01-01T18:00:00"]}, "RFC3339"),
            ({"clocks": ["2025-01-01T18:00:00-00:60"]}, "offset"),
            ({"clocks": ["2025-01-01T18:00:00.000000001Z"]}, "sub-microsecond"),
        ]
        for changes, message in cases:
            with self.subTest(changes=changes):
                profile_path = self.write_fx_profile(identifier, valid_clocks, **changes)
                with self.assertRaisesRegex(CampaignDatasetError, message):
                    readiness_module.audit_fx_readiness(self.dataset_dir, identifier, profile_path)

    def test_fx_clock_audit_bounds_clock_count_and_diagnostics(self):
        identifier = import_campaign_dataset(self.input_dir / "manifest.json", self.dataset_dir)
        first = datetime(2025, 1, 1, 18, tzinfo=timezone.utc)
        clocks = [(first + timedelta(seconds=index)).strftime("%Y-%m-%dT%H:%M:%SZ")
                  for index in range(50000)]
        profile_path = self.write_fx_profile(identifier, clocks, max_age_seconds=2 * 86400)
        report = readiness_module.audit_fx_readiness(self.dataset_dir, identifier, profile_path)
        self.assertEqual(report["clock_count"], 50000)
        self.assertEqual(report["coverage"]["passed_clocks"], 50000)
        self.assertEqual(len(report["coverage"]["sample_observations"]), 5)
        view = readiness_module.fx_observation_view(self.dataset_dir, identifier, profile_path)
        self.assertEqual(len(view["observations"]), 50000)
        self.assertEqual(view["observations"][-1]["clock"], clocks[-1])
        self.assertEqual(view["observations"][-1]["gbp_per_unit"], "0.79")

        self.write_fx_profile(identifier, clocks + ["2025-01-02T07:53:20Z"])
        with self.assertRaisesRegex(CampaignDatasetError, "1 to 50000"):
            readiness_module.audit_fx_readiness(self.dataset_dir, identifier, profile_path)

        self.write_fx_profile(identifier, [f"2025-01-01T17:59:{index:02}Z" for index in range(7)])
        report = readiness_module.audit_fx_readiness(self.dataset_dir, identifier, profile_path)
        self.assertEqual(report["coverage"]["issues"], 7)
        self.assertEqual(len(report["coverage"]["examples"]), 5)
        self.assertEqual(report, readiness_module.audit_fx_readiness(self.dataset_dir, identifier, profile_path))

    def test_fx_clock_profile_enforces_file_size_bound(self):
        identifier = import_campaign_dataset(self.input_dir / "manifest.json", self.dataset_dir)
        profile_path = self.write_fx_profile(identifier, ["2025-01-01T18:00:00Z"])
        payload = profile_path.read_bytes()
        limit = 2 * 1024 * 1024
        profile_path.write_bytes(payload + b" " * (limit - len(payload)))
        self.assertEqual(
            readiness_module.audit_fx_readiness(self.dataset_dir, identifier, profile_path)["status"],
            "unresolved",
        )
        with profile_path.open("ab") as stream:
            stream.write(b" ")
        with self.assertRaisesRegex(CampaignDatasetError, "size limit"):
            readiness_module.audit_fx_readiness(self.dataset_dir, identifier, profile_path)

    def test_fx_audit_validates_source_timestamps_after_last_clock(self):
        imprecise = USD_FX.replace(
            "2025-01-08T18:00:00Z", "2025-01-08T18:00:00.000000001Z",
        )
        self.write_fixture(csv_overrides={"usd_fx.csv": imprecise})
        identifier = import_campaign_dataset(self.input_dir / "manifest.json", self.dataset_dir)
        profile_path = self.write_fx_profile(identifier, ["2025-01-01T18:00:00Z"])
        with self.assertRaisesRegex(CampaignDatasetError, "sub-microsecond"):
            readiness_module.audit_fx_readiness(self.dataset_dir, identifier, profile_path)

    def test_fx_import_rejects_newlines_inside_timestamps_but_accepts_crlf_records(self):
        for original, broken in (
            ("2025-01-01T00:00:00Z", '"2025-01-01T0\n0:00:00Z"'),
            ("2025-01-01T18:00:00Z", '"2025-01-01T1\r\n8:00:00Z"'),
        ):
            with self.subTest(original=original):
                self.write_fixture(csv_overrides={"usd_fx.csv": USD_FX.replace(original, broken, 1)})
                with self.assertRaisesRegex(CampaignDatasetError, "RFC3339"):
                    import_campaign_dataset(self.input_dir / "manifest.json", self.dataset_dir)
        self.write_fixture(csv_overrides={"usd_fx.csv": USD_FX.replace("\n", "\r\n")})
        identifier = import_campaign_dataset(self.input_dir / "manifest.json", self.dataset_dir)
        self.assertEqual(CampaignDataset.open(self.dataset_dir, identifier).source_file("usd_fx.csv"),
                         USD_FX.replace("\n", "\r\n").encode("utf-8"))

    def test_reopen_rejects_type_changed_embedded_provenance(self):
        manifest = _manifest()
        manifest["provenance"]["metadata"]["revision"] = 1
        self.write_fixture(manifest)
        identifier = import_campaign_dataset(self.input_dir / "manifest.json", self.dataset_dir)
        dataset = CampaignDataset.open(self.dataset_dir, identifier)
        path = dataset.directory / "dataset.json"
        altered = json.loads(path.read_text(encoding="utf-8"))
        altered["source_manifest"]["provenance"]["metadata"]["revision"] = True
        path.write_text(json.dumps(altered), encoding="utf-8")
        with self.assertRaisesRegex(CampaignDatasetError, "checksum/manifest mismatch"):
            CampaignDataset.open(self.dataset_dir, identifier)

    def test_provenance_decimal_precision_is_preserved_and_tampering_detected(self):
        manifest = _manifest()
        manifest["provenance"]["metadata"]["revision"] = 1.0
        self.write_fixture(manifest)
        source_manifest_path = self.input_dir / "manifest.json"
        raw = source_manifest_path.read_bytes().replace(
            b'"revision":1.0', b'"revision":1.0000000000000001',
        )
        source_manifest_path.write_bytes(raw)
        identifier = import_campaign_dataset(source_manifest_path, self.dataset_dir)
        dataset = CampaignDataset.open(self.dataset_dir, identifier)
        self.assertEqual(
            dataset.source_manifest["provenance"]["metadata"]["revision"],
            Decimal("1.0000000000000001"),
        )
        profile_path = self.write_fx_profile(identifier, ["2025-01-01T18:00:00Z"])
        output = io.StringIO()
        with patch("sys.stdout", output):
            result = campaign_cli([
                "fx-view", identifier, "--dataset-dir", str(self.dataset_dir),
                "--profile", str(profile_path),
            ])
        self.assertEqual(result, 3)
        self.assertIn('"revision":1.0000000000000001', output.getvalue())

        path = dataset.directory / "dataset.json"
        payload = path.read_bytes().replace(
            b'"revision":1.0000000000000001', b'"revision":1.0',
        )
        path.write_bytes(payload)
        with self.assertRaisesRegex(CampaignDatasetError, "checksum/manifest mismatch"):
            CampaignDataset.open(self.dataset_dir, identifier)

    def test_fx_csv_unterminated_quoted_rate_is_rejected_without_cli_output(self):
        identifier = import_campaign_dataset(self.input_dir / "manifest.json", self.dataset_dir)
        dataset = CampaignDataset.open(self.dataset_dir, identifier)
        source = dataset.directory / "source" / "usd_fx.csv"
        source.write_bytes(USD_FX.replace("0.81\n", '"0.81\n').encode("utf-8"))
        profile_path = self.write_fx_profile(identifier, ["2025-01-01T18:00:00Z"])
        output, error = io.StringIO(), io.StringIO()
        with patch("sys.stdout", output), patch("sys.stderr", error):
            result = campaign_cli([
                "fx-view", identifier, "--dataset-dir", str(self.dataset_dir),
                "--profile", str(profile_path),
            ])
        self.assertEqual((result, output.getvalue()), (2, ""))
        self.assertIn("invalid CSV", error.getvalue())

    def test_oversized_corporate_action_integer_fails_cleanly_in_import_and_fx_cli(self):
        manifest = _manifest(corporate_actions=[_action(terms={"numerator": 10 ** 400, "denominator": 1})])
        self.write_fixture(manifest)
        output, error = io.StringIO(), io.StringIO()
        with patch("sys.stdout", output), patch("sys.stderr", error):
            result = campaign_cli([
                "import", str(self.input_dir / "manifest.json"), "--dataset-dir", str(self.dataset_dir),
            ])
        self.assertEqual((result, output.getvalue()), (2, ""))
        self.assertNotIn("Traceback", error.getvalue())

        identifier = import_campaign_dataset(self.write_fixture(), self.dataset_dir)
        dataset = CampaignDataset.open(self.dataset_dir, identifier)
        source_manifest_path = dataset.directory / "source" / "manifest.json"
        tampered = json.loads(source_manifest_path.read_text(encoding="utf-8"))
        tampered["corporate_actions"][0]["terms"]["numerator"] = 10 ** 400
        source_manifest_path.write_text(json.dumps(tampered), encoding="utf-8")
        profile_path = self.write_fx_profile(identifier, ["2025-01-01T18:00:00Z"])
        output, error = io.StringIO(), io.StringIO()
        with patch("sys.stdout", output), patch("sys.stderr", error):
            result = campaign_cli([
                "fx-view", identifier, "--dataset-dir", str(self.dataset_dir),
                "--profile", str(profile_path),
            ])
        self.assertEqual((result, output.getvalue()), (2, ""))
        self.assertNotIn("Traceback", error.getvalue())

    def test_fx_reads_reject_files_growing_or_replaced_after_inspection(self):
        identifier = import_campaign_dataset(self.input_dir / "manifest.json", self.dataset_dir)
        dataset = CampaignDataset.open(self.dataset_dir, identifier)
        profile_path = self.write_fx_profile(identifier, ["2025-01-01T18:00:00Z"])
        original_path_open = Path.open
        original_os_open = os.open

        def grow_profile(path, flags, *args, **kwargs):
            if Path(path) == profile_path:
                with original_path_open(path, "ab") as stream:
                    stream.write(b" " * (2 * 1024 * 1024))
            return original_os_open(path, flags, *args, **kwargs)

        with patch.object(dataset_module.os, "open", grow_profile):
            with self.assertRaisesRegex(CampaignDatasetError, "size limit"):
                readiness_module.audit_fx_readiness(self.dataset_dir, identifier, profile_path)

        source = dataset.directory / "source" / "usd_fx.csv"
        replacement = self.root / "replacement.csv"
        replacement.write_bytes(source.read_bytes())

        def replace_source(path, flags, *args, **kwargs):
            if Path(path) == source:
                return original_os_open(replacement, flags, *args, **kwargs)
            return original_os_open(path, flags, *args, **kwargs)

        with patch.object(dataset_module.os, "open", replace_source):
            with self.assertRaisesRegex(CampaignDatasetError, "changed|unsafe"):
                dataset.source_file("usd_fx.csv")

        def grow_source(path, flags, *args, **kwargs):
            if Path(path) == source:
                with original_path_open(path, "r+b") as stream:
                    stream.seek(1024)
                    stream.write(b"x")
            return original_os_open(path, flags, *args, **kwargs)

        with patch("research_data.dataset.MAX_CSV_BYTES", 1024), \
                patch.object(dataset_module.os, "open", grow_source):
            with self.assertRaisesRegex(CampaignDatasetError, "size limit"):
                dataset.source_file("usd_fx.csv")

    def test_fx_cli_keeps_provenance_output_bounded(self):
        nested = [0] * 1000
        for _ in range(50):
            nested = [nested]
        manifest = _manifest()
        manifest["provenance"]["metadata"]["nested"] = nested
        self.write_fixture(manifest)
        identifier = import_campaign_dataset(self.input_dir / "manifest.json", self.dataset_dir)
        profile_path = self.write_fx_profile(identifier, ["2025-01-01T18:00:00Z"])
        for command in ("fx-readiness", "fx-view"):
            with self.subTest(command=command):
                output, error = io.StringIO(), io.StringIO()
                with patch("sys.stdout", output), patch("sys.stderr", error):
                    code = campaign_cli([command, identifier, "--dataset-dir", str(self.dataset_dir),
                                         "--profile", str(profile_path)])
                self.assertEqual(code, 3)
                self.assertLess(len(output.getvalue()), 15000)
                self.assertEqual(json.loads(output.getvalue())["source_provenance"]["metadata"]["nested"], nested)

    def test_fx_cli_escapes_control_characters_in_unknown_profile_keys(self):
        identifier = import_campaign_dataset(self.input_dir / "manifest.json", self.dataset_dir)
        profile_path = self.write_fx_profile(identifier, ["2025-01-01T18:00:00Z"])
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        profile["unknown\nFAKE SUCCESS\x1b[31m"] = 1
        profile_path.write_text(json.dumps(profile), encoding="utf-8")
        output, error = io.StringIO(), io.StringIO()
        with patch("sys.stdout", output), patch("sys.stderr", error):
            code = campaign_cli(["fx-view", identifier, "--dataset-dir", str(self.dataset_dir),
                                 "--profile", str(profile_path)])
        self.assertEqual((code, output.getvalue()), (2, ""))
        self.assertNotIn("\nFAKE SUCCESS", error.getvalue())
        self.assertNotIn("\x1b", error.getvalue())
        self.assertIn("\\n", error.getvalue())

    def test_fx_cli_reports_corrupt_timestamp_and_oversized_csv_cleanly(self):
        for case in ("timestamp", "csv"):
            with self.subTest(case=case):
                directory = self.root / f"snapshots-{case}"
                identifier = import_campaign_dataset(self.input_dir / "manifest.json", directory)
                dataset = CampaignDataset.open(directory, identifier)
                profile_path = self.write_fx_profile(identifier, ["2025-01-01T18:00:00Z"])
                if case == "timestamp":
                    path = dataset.directory / "source" / "manifest.json"
                    manifest = json.loads(path.read_text(encoding="utf-8"))
                    manifest["provenance"]["retrieved_at"] = "0001-01-01T00:00:00+14:00"
                    path.write_text(json.dumps(manifest), encoding="utf-8")
                else:
                    path = dataset.directory / "source" / "usd_fx.csv"
                    path.write_text(USD_FX.replace("0.79", "0." + "7" * 140000), encoding="utf-8")
                output, error = io.StringIO(), io.StringIO()
                with patch("sys.stdout", output), patch("sys.stderr", error):
                    code = campaign_cli(["fx-view", identifier, "--dataset-dir", str(directory),
                                         "--profile", str(profile_path)])
                self.assertEqual((code, output.getvalue()), (2, ""))
                self.assertTrue(error.getvalue().startswith("error:"))
                self.assertNotIn("Traceback", error.getvalue())

    def test_fx_view_returns_all_causal_observations_without_rounding_or_mutation(self):
        precise = "0.8000000000000000000123456789"
        fx = (USD_FX.replace("2025-01-01T18:00:00Z", "2025-01-06T00:00:00Z")
              .replace("2025-01-08T18:00:00Z", "2025-01-09T00:00:00Z")
              .replace("0.80", precise))
        self.write_fixture(csv_overrides={"usd_fx.csv": fx})
        identifier = import_campaign_dataset(self.input_dir / "manifest.json", self.dataset_dir)
        dataset = CampaignDataset.open(self.dataset_dir, identifier)
        before = {path.relative_to(dataset.directory): path.read_bytes()
                  for path in dataset.directory.rglob("*") if path.is_file()}
        clocks = ["2025-01-05T18:00:00Z"] + [
            f"2025-01-06T00:00:{second:02}Z" for second in range(7)
        ] + ["2025-01-08T18:00:00Z", "2025-01-09T00:00:00Z"]
        profile_path = self.write_fx_profile(identifier, clocks, max_age_seconds=4 * 86400)

        with localcontext() as context:
            context.prec = 4
            view = readiness_module.fx_observation_view(self.dataset_dir, identifier, profile_path)

        self.assertEqual(view["scope"], "offline_fx_observations_at_supplied_clocks")
        self.assertEqual(view["readiness_status"], "unresolved")
        self.assertEqual(view["clock_count"], len(clocks))
        self.assertEqual([item["clock"] for item in view["observations"]], clocks)
        self.assertEqual([item["gbp_per_unit"] for item in view["observations"]],
                         [precise] * (len(clocks) - 1) + ["0.81"])
        self.assertEqual(view["observations"][1]["observation_at"], "2025-01-05T00:00:00Z")
        self.assertEqual(view["observations"][-2]["available_at"], "2025-01-05T18:00:00Z")
        self.assertEqual(view["observations"][-1]["available_at"], clocks[-1])
        self.assertEqual(view["dataset_id"], identifier)
        self.assertEqual(view["profile_sha256"], hashlib.sha256(profile_path.read_bytes()).hexdigest())
        self.assertEqual(view["source_sha256"], hashlib.sha256(fx.encode("utf-8")).hexdigest())
        self.assertEqual(view["source_provenance"], dataset.source_manifest["provenance"])
        for flag in ("external_facts_verified", "clock_set_complete_verified",
                     "point_in_time_vintage_verified", "executable_fx_verified", "supports_performance"):
            self.assertFalse(view[flag])
        self.assertEqual(before, {path.relative_to(dataset.directory): path.read_bytes()
                                  for path in dataset.directory.rglob("*") if path.is_file()})
        self.assertEqual(view, readiness_module.fx_observation_view(self.dataset_dir, identifier, profile_path))

    def test_fx_view_rejects_missing_stale_and_oversized_values(self):
        identifier = import_campaign_dataset(self.input_dir / "manifest.json", self.dataset_dir)
        profile_path = self.write_fx_profile(identifier, ["2025-01-02T00:00:00Z"])
        view = readiness_module.fx_observation_view(self.dataset_dir, identifier, profile_path)
        self.assertEqual(view["observations"][0]["gbp_per_unit"], "0.79")
        for clocks, message in (
            (["2025-01-01T12:00:00Z"], "no available observation"),
            (["2025-01-01T18:00:00Z", "2025-01-02T00:00:00.000001Z"], "stale"),
        ):
            with self.subTest(clocks=clocks):
                profile_path = self.write_fx_profile(identifier, clocks)
                with self.assertRaisesRegex(CampaignDatasetError, message):
                    readiness_module.fx_observation_view(self.dataset_dir, identifier, profile_path)

        for digits in (126, 127):
            with self.subTest(rate_length=digits + 2):
                rate = "0." + "7" * digits
                self.write_fixture(csv_overrides={"usd_fx.csv": USD_FX.replace("0.79", rate)})
                identifier = import_campaign_dataset(self.input_dir / "manifest.json", self.dataset_dir)
                profile_path = self.write_fx_profile(identifier, ["2025-01-01T18:00:00Z"])
                if digits == 126:
                    view = readiness_module.fx_observation_view(self.dataset_dir, identifier, profile_path)
                    self.assertEqual(view["observations"][0]["gbp_per_unit"], rate)
                else:
                    with self.assertRaisesRegex(CampaignDatasetError, "128 characters"):
                        readiness_module.fx_observation_view(self.dataset_dir, identifier, profile_path)

    def test_fx_view_cli_exposes_unresolved_and_never_prints_a_partial_view(self):
        identifier = import_campaign_dataset(self.input_dir / "manifest.json", self.dataset_dir)
        profile_path = self.write_fx_profile(identifier, ["2025-01-01T18:00:00Z"])
        args = ["fx-view", identifier, "--dataset-dir", str(self.dataset_dir),
                "--profile", str(profile_path)]

        def run_cli():
            output, error = io.StringIO(), io.StringIO()
            with patch("sys.stdout", output), patch("sys.stderr", error):
                code = campaign_cli(args)
            return code, output.getvalue(), error.getvalue()

        code, output, error = run_cli()
        self.assertEqual(code, 3)
        self.assertEqual(json.loads(output)["readiness_status"], "unresolved")
        self.assertIn("unresolved", error)
        self.write_fx_profile(identifier, ["2025-01-01T18:00:00Z"], synthetic=False)
        code, output, error = run_cli()
        self.assertEqual((code, error), (0, ""))
        self.assertEqual(json.loads(output)["observations"][0]["gbp_per_unit"], "0.79")
        self.write_fx_profile(identifier, ["2025-01-01T18:00:00Z", "2025-01-03T00:00:00Z"])
        code, output, error = run_cli()
        self.assertEqual((code, output), (2, ""))
        self.assertIn("stale", error)
        self.write_fx_profile(identifier, ["2025-01-01T18:00:00Z"])
        source = self.dataset_dir / "datasets" / identifier / "source" / "usd_fx.csv"
        source.write_bytes(source.read_bytes() + b"\n")
        code, output, error = run_cli()
        self.assertEqual((code, output), (2, ""))
        self.assertIn("checksum", error)

    def test_fx_readiness_cli_is_scoped_read_only_and_fails_closed(self):
        identifier = import_campaign_dataset(self.input_dir / "manifest.json", self.dataset_dir)
        dataset = CampaignDataset.open(self.dataset_dir, identifier)
        before = {path.relative_to(dataset.directory): path.read_bytes()
                  for path in dataset.directory.rglob("*") if path.is_file()}
        clocks = ["2025-01-01T18:00:00Z", "2025-01-05T18:00:00Z"]
        profile_path = self.write_fx_profile(identifier, clocks)
        args = ["fx-readiness", identifier, "--dataset-dir", str(self.dataset_dir),
                "--profile", str(profile_path)]

        def run_cli():
            output, error = io.StringIO(), io.StringIO()
            with patch("sys.stdout", output), patch("sys.stderr", error):
                code = campaign_cli(args)
            return code, output.getvalue(), error.getvalue()

        code, output, error = run_cli()
        self.assertEqual(code, 3)
        self.assertEqual(json.loads(output)["status"], "unresolved")
        self.assertIn("unresolved", error)
        profile_path = self.write_fx_profile(identifier, clocks, synthetic=False)
        code, output, error = run_cli()
        report = json.loads(output)
        self.assertEqual((code, error), (0, ""))
        self.assertEqual(report["coverage"]["status"], "pass")
        self.assertEqual(report["profile_sha256"], hashlib.sha256(profile_path.read_bytes()).hexdigest())
        self.assertFalse(report["clock_set_complete_verified"])
        self.assertFalse(report["external_facts_verified"])
        self.assertFalse(report["executable_fx_verified"])
        self.assertFalse(report["supports_performance"])
        self.assertEqual(before, {path.relative_to(dataset.directory): path.read_bytes()
                                  for path in dataset.directory.rglob("*") if path.is_file()})
        self.write_fx_profile(identifier, ["2025-01-01T12:00:00Z"], synthetic=False)
        code, output, error = run_cli()
        self.assertEqual(code, 3)
        self.assertEqual(json.loads(output)["status"], "fail")
        self.assertIn("FX readiness fail", error)
        self.write_fx_profile(identifier, ["2025-01-01T12:00:00"], synthetic=False)
        code, output, error = run_cli()
        self.assertEqual((code, output), (2, ""))
        self.assertIn("RFC3339", error)

        self.write_fx_profile(identifier, clocks, synthetic=False)
        path = dataset.directory / "source" / "usd_fx.csv"
        path.write_bytes(path.read_bytes() + b"\n")
        code, output, error = run_cli()
        self.assertEqual((code, output), (2, ""))
        self.assertIn("checksum", error)

    def test_duplicate_action_id_is_rejected(self):
        duplicate = _action(
            id="split-1",
            effective_at="2025-01-09T00:00:00Z",
            available_at="2025-01-03T12:00:00Z",
        )
        manifest = _manifest(corporate_actions=[_action(), duplicate])
        self.write_fixture(manifest)
        with self.assertRaisesRegex(CampaignDatasetError, "duplicate id"):
            import_campaign_dataset(self.input_dir / "manifest.json", self.dataset_dir)

    def test_actions_must_be_in_supplied_deterministic_order(self):
        later = _action(
            id="split-2",
            effective_at="2025-01-09T00:00:00Z",
            available_at="2025-01-03T12:00:00Z",
        )
        manifest = _manifest(corporate_actions=[later, _action()])
        self.write_fixture(manifest)
        with self.assertRaisesRegex(CampaignDatasetError, "must already be ordered"):
            import_campaign_dataset(self.input_dir / "manifest.json", self.dataset_dir)

    def test_unsafe_paths_wrong_fx_direction_and_duplicate_series_ids_fail_closed(self):
        for manifest, message in (
            (_manifest(products=[dict(_manifest()["products"][0], file="../escape.csv"),
                                 _manifest()["products"][1]]), "safe CSV basename"),
            (_manifest(fx=[dict(_manifest()["fx"][0], direction="USD_PER_GBP")]), "direction"),
            (_manifest(cash_rates=[dict(_manifest()["cash_rates"][0], id="gb-product")]), "duplicate id"),
        ):
            with self.subTest(message=message):
                self.write_fixture(manifest)
                with self.assertRaisesRegex(CampaignDatasetError, message):
                    import_campaign_dataset(self.input_dir / "manifest.json", self.dataset_dir)

    def test_product_price_basis_must_be_declared(self):
        manifest = _manifest()
        del manifest["products"][0]["price_basis"]
        self.write_fixture(manifest)
        with self.assertRaisesRegex(CampaignDatasetError, "missing price_basis"):
            import_campaign_dataset(self.input_dir / "manifest.json", self.dataset_dir)


if __name__ == "__main__":
    unittest.main()
