import copy
import json
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

try:
    from jsonschema import Draft202012Validator, FormatChecker
except ModuleNotFoundError:
    Draft202012Validator = None
    FormatChecker = None

from dashboard.contracts import freshness


ROOT = Path(__file__).resolve().parents[1] / "dashboard"
requires_schema = unittest.skipIf(
    Draft202012Validator is None,
    "Install requirements-dashboard-dev.txt to run optional JSON Schema checks")


class DashboardContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads((ROOT / "response.schema.json").read_text(encoding="utf-8"))
        cls.fixtures = json.loads((ROOT / "fixtures.json").read_text(encoding="utf-8"))
        cls.validator = (Draft202012Validator(cls.schema, format_checker=FormatChecker())
                 if Draft202012Validator is not None else None)
        cls.scenarios = {scenario["name"]: scenario for scenario in cls.fixtures["scenarios"]}

    def response(self, scenario_name, resource):
        scenario = self.scenarios[scenario_name]
        meta = copy.deepcopy(self.fixtures["meta"])
        meta.update(copy.deepcopy(scenario["meta_overrides"]))
        return {"api_version": "1", "resource": resource, "meta": meta,
                "data": copy.deepcopy(scenario["responses"][resource])}

    @requires_schema
    def test_schema_and_all_synthetic_responses(self):
        Draft202012Validator.check_schema(self.schema)
        resources = set()
        for name, scenario in self.scenarios.items():
            for resource in scenario["responses"]:
                with self.subTest(scenario=name, resource=resource):
                    self.validator.validate(self.response(name, resource))
                    resources.add(resource)
        self.assertEqual(resources, set(self.schema["properties"]["resource"]["enum"]))
        self.assertEqual(len(self.scenarios), len(self.fixtures["scenarios"]))

    def test_fixture_freshness_matches_fixed_clock(self):
        now = datetime.fromisoformat(self.fixtures["generated_at"].replace("Z", "+00:00"))
        for name, scenario in self.scenarios.items():
            resource = next(iter(scenario["responses"]))
            meta = self.response(name, resource)["meta"]
            for source_name in ("ledger", "telemetry", "broker"):
                source = meta[source_name]
                with self.subTest(scenario=name, source=source_name):
                    if name == "ledger_invalid" and source_name == "ledger":
                        self.assertEqual(source["freshness"], "invalid")
                    else:
                        self.assertEqual(freshness(source["observed_at"], now, source["max_age_seconds"]),
                                         source["freshness"])

    def test_partial_exit_conserves_quantity_and_retains_identity(self):
        scenario = self.scenarios["partial_exit"]
        position = scenario["responses"]["positions"][0]
        exit_slice = scenario["responses"]["performance"]["exits"][0]
        order = scenario["responses"]["orders"]["items"][0]
        self.assertEqual(Decimal(position["quantity"]) + Decimal(exit_slice["quantity"]),
                         Decimal(scenario["original_quantity"]))
        self.assertEqual(exit_slice["quantity"], order["cumulative_filled_quantity"])
        self.assertEqual(position["position_id"], exit_slice["position_id"])
        self.assertEqual(position["pending_exit_order_id"], order["broker_order_id"])

    def test_correction_preserves_unknown_original_and_prior_day(self):
        original = self.response("unknown_exit_price", "performance")["data"]["exits"][0]
        corrected = self.response("evidence_linked_correction", "performance")["data"]
        self.assertEqual(original, corrected["exits"][0])
        self.assertEqual(corrected["corrections"][0]["execution_id"], original["execution_id"])
        self.assertEqual(corrected["daily"][0]["day"], original["accounting_day"])
        self.assertTrue(corrected["daily"][0]["includes_corrections"])

    @requires_schema
    def test_schema_rejects_invented_prices_numeric_money_and_extra_fields(self):
        response = self.response("unknown_exit_price", "performance")
        response["data"]["exits"][0]["exit_price"] = "98.00"
        self.assertFalse(self.validator.is_valid(response))
        for value in (100.0, "NaN", "Infinity", "1e5", ""):
            with self.subTest(value=value):
                response = self.response("healthy_recorded", "overview")
                response["data"]["recorded_equity"] = value
                self.assertFalse(self.validator.is_valid(response))
        response = self.response("healthy_recorded", "overview")
        response["data"]["api_key"] = "synthetic-forbidden-field"
        self.assertFalse(self.validator.is_valid(response))

    @requires_schema
    def test_schema_rejects_invalid_dates_and_unsubstantiated_protection(self):
        response = self.response("healthy_recorded", "positions")
        response["data"][0]["protection_observed_at"] = None
        self.assertFalse(self.validator.is_valid(response))
        for timestamp in ("2026-02-30T15:00:00Z", "2026-11-13T15:00:00", "2026-11-13T15:00:00+00:00"):
            response = self.response("healthy_recorded", "overview")
            response["meta"]["generated_at"] = timestamp
            self.assertFalse(self.validator.is_valid(response))

    @requires_schema
    def test_schema_rejects_stale_runtime_and_entry_permission(self):
        for resource in ("overview", "health"):
            response = self.response("healthy_recorded", resource)
            response["meta"]["telemetry"]["freshness"] = "stale"
            self.assertFalse(self.validator.is_valid(response))
            response = self.response("healthy_recorded", resource)
            response["meta"]["telemetry_run_id"] = None
            self.assertFalse(self.validator.is_valid(response))
        response = self.response("healthy_recorded", "health")
        response["data"]["persistence_fault"] = True
        self.assertFalse(self.validator.is_valid(response))

    def test_fault_and_stale_scenarios_do_not_claim_entry_permission(self):
        for name in ("persistence_fault", "stale_telemetry", "stopped_empty_account", "unknown_exit_price"):
            self.assertNotEqual(self.response(name, "overview")["data"]["entries"], "last_observed_allowed")
        self.assertEqual(self.response("stale_telemetry", "health")["data"]["runtime"], "unknown")
        self.assertIsNone(self.response("stale_telemetry", "health")["data"]["persistence_fault"])


class DashboardFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 13, 15, tzinfo=timezone.utc)

    def test_exact_boundary_and_stale(self):
        self.assertEqual(freshness("2026-11-13T14:58:30Z", self.now, 90), "fresh")
        self.assertEqual(freshness("2026-11-13T14:58:29.999999Z", self.now, 90), "stale")

    def test_missing_future_and_malformed(self):
        self.assertEqual(freshness(None, self.now, 90), "missing")
        for value in ("2026-11-13T15:00:01Z", "not-a-date", "2026-11-13T15:00:00",
                      "2026-11-13Z", "2026-02-30T15:00:00Z", 123):
            with self.subTest(value=value):
                self.assertEqual(freshness(value, self.now, 90), "invalid")

    def test_timezone_conversion_and_invalid_policy(self):
        local_now = self.now.astimezone(timezone(timedelta(hours=-5)))
        self.assertEqual(freshness("2026-11-13T15:00:00Z", local_now, 90), "fresh")
        with self.assertRaises(ValueError):
            freshness(None, self.now.replace(tzinfo=None), 90)
        for value in (0, -1, float("nan"), float("inf"), True, "90"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                freshness(None, self.now, value)


if __name__ == "__main__":
    unittest.main()