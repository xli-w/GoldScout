"""Offline investment contracts; all stores and JSON fixtures are temporary."""

import builtins
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import io
import importlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

from investment import InvestmentError
from investment.__main__ import main
from investment.core import iso, replay
from investment.simulation import simulate_synthetic_fixture
from investment.service import (demo_events, demo_profile, demo_snapshot, proposal_export,
                                report, snapshot)
from investment.store import import_events, initialize, load_json, read
from investment import store


NOW = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)


def ev(identifier, kind, at=NOW, account="broker", **values):
    return dict(id=identifier, type=kind, timestamp=iso(at), available_at=iso(at),
                account_id=account, currency="GBP", evidence="explicit synthetic test evidence",
                **values)


class InvestmentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="investment-test-")
        self.addCleanup(self.temporary.cleanup)
        self.database = Path(self.temporary.name) / "investment.sqlite3"
        self.spec = demo_profile(NOW)

    def init_demo(self):
        initialize(self.database, self.spec, NOW)
        import_events(self.database, demo_events(NOW), NOW)

    def synthetic_bar(self, product_id, session_number, open_price, close_price):
        day = NOW + timedelta(days=session_number)
        open_at = day.replace(hour=9)
        close_at = day.replace(hour=16)
        return {
            "product_id": product_id,
            "session_id": "session-" + str(session_number),
            "open_at": iso(open_at),
            "open_available_at": iso(open_at),
            "open_gbp": open_price,
            "close_at": iso(close_at),
            "close_available_at": iso(close_at),
            "close_gbp": close_price,
        }

    def test_synthetic_simulator_uses_next_open_and_flow_neutral_contributions(self):
        products = [
            {"id": "equity", "quantity_increment": "1", "minimum_notional_gbp": "1"},
            {"id": "bond", "quantity_increment": "1", "minimum_notional_gbp": "1"},
        ]
        bars = [
            self.synthetic_bar("equity", 0, "10", "10"),
            self.synthetic_bar("bond", 0, "20", "20"),
            self.synthetic_bar("equity", 1, "10", "11"),
            self.synthetic_bar("bond", 1, "20", "20"),
            self.synthetic_bar("equity", 2, "11", "12.1"),
            self.synthetic_bar("bond", 2, "20", "19"),
        ]
        contribution_at = iso((NOW + timedelta(days=1)).replace(hour=16))
        result = simulate_synthetic_fixture(
            "1000", products, {"equity": "80", "bond": "20"}, bars,
            [{"id": "later-funding", "timestamp": contribution_at,
              "available_at": contribution_at, "amount_gbp": "100"}])

        self.assertTrue(result["synthetic_only"])
        self.assertFalse(result["supports_product_performance_claims"])
        self.assertEqual(result["curve"][0]["cash_gbp"], "1000")
        self.assertEqual(result["curve"][0]["fills"], [])
        self.assertEqual(
            [(fill["product_id"], fill["quantity"]) for fill in result["curve"][1]["fills"]],
            [("bond", "10"), ("equity", "80")])
        self.assertEqual(
            result["curve"][1]["fills"][0]["execution_at"],
            bars[2]["open_at"])
        self.assertEqual(result["curve"][1]["unit_value"], "1.08")
        self.assertEqual(result["curve"][1]["equity_gbp"], "1180")
        self.assertEqual(
            [(fill["product_id"], fill["quantity"]) for fill in result["curve"][2]["fills"]],
            [("bond", "1"), ("equity", "5")])
        self.assertEqual(result["summary"]["ending_equity_gbp"], "1262.5")
        self.assertEqual(result["summary"]["net_contributions_gbp"], "1100")
        self.assertEqual(result["summary"]["holdings"], [
            {"product_id": "bond", "quantity": "11"},
            {"product_id": "equity", "quantity": "85"},
        ])
        self.assertGreater(Decimal(result["summary"]["twr_pct"]), Decimal("15"))
        self.assertLess(Decimal(result["summary"]["twr_pct"]), Decimal("16"))

    def test_synthetic_simulator_costs_and_quantity_increment_conserve_cash(self):
        products = [{"id": "equity", "quantity_increment": "1",
                     "minimum_notional_gbp": "1"}]
        bars = [
            self.synthetic_bar("equity", 0, "10", "10"),
            self.synthetic_bar("equity", 1, "10", "10"),
        ]
        result = simulate_synthetic_fixture(
            "1000", products, {"equity": "100"}, bars, variable_cost_bps="100")

        self.assertEqual(result["curve"][1]["fills"][0]["quantity"], "99")
        self.assertEqual(result["curve"][1]["fills"][0]["fee_gbp"], "9.9")
        self.assertEqual(result["summary"]["cash_gbp"], "0.1")
        self.assertEqual(result["summary"]["ending_equity_gbp"], "990.1")
        self.assertEqual(result["summary"]["twr_pct"], "-0.99")
        self.assertEqual(result["summary"]["fees_gbp"], "9.9")

    def test_synthetic_simulator_reports_minimum_notional_skips(self):
        products = [{"id": "equity", "quantity_increment": "1",
                     "minimum_notional_gbp": "1001"}]
        bars = [
            self.synthetic_bar("equity", 0, "10", "10"),
            self.synthetic_bar("equity", 1, "10", "10"),
        ]
        result = simulate_synthetic_fixture("1000", products, {"equity": "100"}, bars)

        self.assertEqual(result["curve"][1]["fills"], [])
        self.assertEqual(result["curve"][1]["unfilled_allocations"], [{
            "product_id": "equity",
            "budget_gbp": "1000",
            "reason": "below_minimum_notional",
        }])
        self.assertEqual(result["summary"]["cash_gbp"], "1000")

    def test_synthetic_simulator_drawdown_survives_a_contribution(self):
        products = [{"id": "equity", "quantity_increment": "1",
                     "minimum_notional_gbp": "1"}]
        bars = [
            self.synthetic_bar("equity", 0, "10", "10"),
            self.synthetic_bar("equity", 1, "10", "8"),
            self.synthetic_bar("equity", 2, "8", "9"),
        ]
        contribution_at = iso((NOW + timedelta(days=1)).replace(hour=16))
        result = simulate_synthetic_fixture(
            "100", products, {"equity": "100"}, bars,
            [{"id": "fund-during-drawdown", "timestamp": contribution_at,
              "available_at": contribution_at, "amount_gbp": "100"}])

        self.assertEqual(result["curve"][1]["unit_value"], "0.8")
        self.assertEqual(result["curve"][1]["equity_gbp"], "180")
        self.assertEqual(result["curve"][1]["drawdown_pct"], "20")
        self.assertEqual(result["summary"]["max_drawdown_pct"], "20")

    def test_synthetic_simulator_rejects_future_prices_incomplete_sessions_and_late_flows(self):
        products = [
            {"id": "equity", "quantity_increment": "1", "minimum_notional_gbp": "1"},
            {"id": "bond", "quantity_increment": "1", "minimum_notional_gbp": "1"},
        ]
        bars = [
            self.synthetic_bar("equity", 0, "10", "10"),
            self.synthetic_bar("bond", 0, "20", "20"),
            self.synthetic_bar("equity", 1, "10", "10"),
            self.synthetic_bar("bond", 1, "20", "20"),
        ]
        bad_close = deepcopy(bars)
        bad_close[0]["close_available_at"] = iso(
            NOW.replace(hour=16) + timedelta(minutes=1))
        with self.assertRaises(InvestmentError):
            simulate_synthetic_fixture("1000", products, {"equity": "80", "bond": "20"},
                                       bad_close)

        bad_open = deepcopy(bars)
        bad_open[0]["open_available_at"] = iso(
            NOW.replace(hour=9) + timedelta(minutes=1))
        with self.assertRaises(InvestmentError):
            simulate_synthetic_fixture("1000", products, {"equity": "80", "bond": "20"},
                                       bad_open)

        with self.assertRaises(InvestmentError):
            simulate_synthetic_fixture("1000", products, {"equity": "80", "bond": "20"},
                                       bars[:-1])

        late_flow = [{"id": "late", "timestamp": iso(NOW.replace(hour=16)),
                      "available_at": iso(NOW.replace(hour=17)), "amount_gbp": "100"}]
        with self.assertRaises(InvestmentError):
            simulate_synthetic_fixture("1000", products, {"equity": "80", "bond": "20"},
                                       bars, late_flow)


    def test_pure_demo_contract_no_files_or_broker_imports(self):
        original_import = builtins.__import__

        def guarded(name, *args, **kwargs):
            if name == "config" or name == "bot" or name.startswith(("bot.", "alpaca")):
                raise AssertionError("Investment must never import broker/config")
            return original_import(name, *args, **kwargs)

        with patch("builtins.open", side_effect=AssertionError("demo filesystem")), \
                patch("sqlite3.connect", side_effect=AssertionError("demo database")), \
                patch("builtins.__import__", side_effect=guarded):
            for name in ("investment.core", "investment.store", "investment.planner",
                         "investment.service", "investment.__main__"):
                importlib.reload(sys.modules[name])
            result = demo_snapshot(NOW)
            stream = io.StringIO()
            self.assertEqual(main(["demo"], stdout=stream, now=NOW), 0)
        self.assertEqual(result["equity_gbp"], "1000")
        self.assertEqual(result["net_contributions_gbp"], "1000")
        self.assertEqual(result["gain_gbp"], "0")
        self.assertEqual(result["source_mode"], "demo")
        self.assertEqual([a["actual_pct"] for a in result["allocation"]], ["80", "0", "20"])
        self.assertFalse(result["proposal"]["execution_enabled"])
        self.assertEqual(result["as_of"], iso(NOW))
        self.assertEqual(json.loads(stream.getvalue()), result)
        self.assertEqual(demo_snapshot()["source_mode"], "demo")

    def test_exact_fills_fees_payments_and_transfer(self):
        values = [
            ev("fund", "contribution", amount_gbp="1000"),
            ev("transfer", "transfer", amount_gbp="200", destination_account_id="savings"),
            ev("mark", "mark", product_id="equity", price_gbp="10.25"),
            ev("buy", "buy", product_id="equity", quantity="50", price_gbp="10.25", fee_gbp="1.25"),
            ev("sell", "sell", product_id="equity", quantity="10", price_gbp="10.25", fee_gbp="0.75"),
            ev("interest", "interest", account="savings", amount_gbp="0.1"),
            ev("dividend", "dividend", product_id="equity", amount_gbp="5.25"),
            ev("withdraw", "withdrawal", account="savings", amount_gbp="20"),
        ]
        result = report(replay(self.spec, values, NOW), NOW)
        self.assertEqual(result["equity_gbp"], "983.35")
        self.assertEqual(result["net_contributions_gbp"], "980")
        self.assertEqual(result["gain_gbp"], "3.35")
        self.assertEqual(result["holdings"][0]["quantity"], "40")
        self.assertEqual([a["cash_gbp"] for a in result["accounts"]], ["393.25", "180.1"])

    def test_read_hash_unchanged_and_restart_replay(self):
        self.init_demo()
        before = self.database.read_bytes()
        result = snapshot(self.database, NOW)
        self.assertEqual(result, snapshot(self.database, NOW))
        self.assertEqual(before, self.database.read_bytes())
        self.assertEqual(result["equity_gbp"], "1000")
        self.assertEqual(read(self.database, NOW).revision, 4)

    def test_identical_replay_and_conflicting_duplicate(self):
        self.init_demo()
        before = self.database.read_bytes()
        self.assertEqual(import_events(self.database, demo_events(NOW), NOW), 4)
        self.assertEqual(self.database.read_bytes(), before)
        wrong = deepcopy(demo_events(NOW))
        wrong[0]["amount_gbp"] = "801"
        with self.assertRaises(InvestmentError):
            import_events(self.database, wrong, NOW)
        self.assertEqual(snapshot(self.database, NOW)["equity_gbp"], "1000")

    def test_invalid_batch_rolls_back_valid_prefix_and_restarts(self):
        self.init_demo()
        values = [
            ev("valid", "contribution", amount_gbp="100"),
            ev("invalid", "buy", product_id="equity", quantity="1000",
               price_gbp="10", fee_gbp="0"),
        ]
        with self.assertRaises(InvestmentError):
            import_events(self.database, values, NOW)
        result = snapshot(self.database, NOW)
        self.assertEqual(result["revision"], 4)
        self.assertEqual(result["net_contributions_gbp"], "1000")
        import_events(self.database, values[:1], NOW)
        self.assertEqual(snapshot(self.database, NOW)["revision"], 5)

    def test_database_failure_rolls_back_inserted_event(self):
        self.init_demo()
        real_digest = store.state_digest
        calls = []

        def fail_late(ledger):
            calls.append(ledger.revision)
            if len(calls) == 2:
                raise sqlite3.OperationalError("Injected commit record failure")
            return real_digest(ledger)

        with patch("investment.store.state_digest", side_effect=fail_late):
            with self.assertRaises(InvestmentError):
                import_events(self.database, [ev("new", "contribution", amount_gbp="1")], NOW)
        self.assertEqual(calls, [4, 5])
        self.assertEqual(snapshot(self.database, NOW)["revision"], 4)

    def test_read_missing_and_init_existing_refused(self):
        with self.assertRaises(InvestmentError):
            snapshot(self.database, NOW)
        self.assertFalse(self.database.exists())
        initialize(self.database, self.spec, NOW)
        with self.assertRaises(InvestmentError):
            initialize(self.database, self.spec, NOW)

    def test_unknown_store_future_version_and_immutable_evidence(self):
        self.init_demo()
        with closing(sqlite3.connect(self.database)) as connection, connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("DELETE FROM events")
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE identity SET profile='{}'")
            connection.execute("PRAGMA user_version=2")
        with self.assertRaises(InvestmentError):
            snapshot(self.database, NOW)

    def test_replay_hash_tamper_fails_closed(self):
        self.init_demo()
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("DROP TRIGGER immutable_events_update")
            connection.execute("UPDATE events SET payload='{}' WHERE revision=1")
        with self.assertRaises(InvestmentError):
            snapshot(self.database, NOW)

    def test_invalid_numbers_fields_currencies_future_and_sizes(self):
        for invalid in ("NaN", "Infinity", "-1", "1e2", "0.000000001", "1.0x",
                        "1000000000000000", 1, 0.1, True):
            with self.subTest(invalid=invalid), self.assertRaises(InvestmentError):
                replay(self.spec, [ev("x", "contribution", amount_gbp=invalid)], NOW)
        for update in ({"currency": "USD"}, {"unexpected": 1},
                       {"available_at": iso(NOW + timedelta(seconds=1))},
                       {"timestamp": "2026-01-01T12:00:00"}):
            value = ev("x", "contribution", amount_gbp="1")
            value.update(update)
            with self.subTest(update=update), self.assertRaises(InvestmentError):
                replay(self.spec, [value], NOW)
        with self.assertRaises(InvestmentError):
            replay(self.spec, [ev("x", "contribution", amount_gbp="1")] * 10001, NOW)
        with self.assertRaises(InvestmentError):
            replay(self.spec, [ev("x", "fx", amount_gbp="1")], NOW)

    def test_binding_product_increment_and_no_short_negative_cash(self):
        values = [
            ev("bad-account", "contribution", account="unknown", amount_gbp="1"),
            ev("negative", "withdrawal", amount_gbp="1"),
            ev("short", "sell", product_id="equity", quantity="1", price_gbp="10", fee_gbp="0"),
            ev("bad-increment", "buy", product_id="equity", quantity="0.1",
               price_gbp="10", fee_gbp="0"),
            ev("deposit-buy", "buy", account="savings", product_id="equity",
               quantity="1", price_gbp="10", fee_gbp="0"),
        ]
        for value in values:
            with self.subTest(value=value), self.assertRaises(InvestmentError):
                replay(self.spec, [value], NOW)
        spec = deepcopy(self.spec)
        spec["accounts"][1]["binding"] = spec["accounts"][0]["binding"]
        with self.assertRaises(InvestmentError):
            replay(spec, [], NOW)

    def test_missing_mixed_clock_and_stale_marks(self):
        ledger = replay(self.spec, [ev("fund", "contribution", amount_gbp="100"),
                                   ev("buy", "buy", product_id="equity", quantity="10",
                                      price_gbp="10", fee_gbp="0")], NOW)
        missing = report(ledger, NOW)
        self.assertFalse(missing["complete"])
        self.assertIsNone(missing["equity_gbp"])
        self.assertIsNone(missing["holdings"][0]["value_gbp"])
        self.assertEqual(missing["proposal"]["status"], "blocked")
        stale = report(replay(self.spec, demo_events(NOW), NOW), NOW + timedelta(days=2))
        self.assertTrue(stale["complete"])
        self.assertEqual(stale["equity_gbp"], "1000")
        self.assertEqual(stale["as_of"], iso(NOW))
        self.assertEqual(stale["proposal"]["status"], "blocked")
        later = NOW + timedelta(hours=1)
        changed = demo_events(NOW) + [ev("new-flow", "contribution", at=later, amount_gbp="100")]
        result = report(replay(self.spec, changed, later), later)
        self.assertFalse(result["complete"])
        self.assertIsNone(result["twr_pct"])

    def test_split_requires_new_mark_and_exact_quantity(self):
        split_time = NOW + timedelta(days=1)
        values = demo_events(NOW) + [
            ev("split", "split", at=split_time, product_id="equity", ratio="2")]
        ledger = replay(self.spec, values, split_time)
        self.assertEqual(ledger.holdings[("broker", "equity")], 160)
        self.assertFalse(report(ledger, split_time)["complete"])
        values.append(ev("post-split", "mark", at=split_time, product_id="equity", price_gbp="5"))
        result = report(replay(self.spec, values, split_time), split_time)
        self.assertEqual(result["equity_gbp"], "1000")
        self.assertEqual(result["gain_gbp"], "0")

    def test_flow_neutral_twr_drawdown_and_actual_fees(self):
        t1, t2 = NOW + timedelta(days=1), NOW + timedelta(days=2)
        values = demo_events(NOW) + [
            ev("up", "mark", at=t1, product_id="equity", price_gbp="12.5"),
            ev("flow", "contribution", at=t1, amount_gbp="600"),
            ev("down", "mark", at=t2, product_id="equity", price_gbp="10"),
        ]
        result = report(replay(self.spec, values, t2), t2)
        self.assertEqual(result["equity_gbp"], "1600")
        # 20% return before flow, then 11.111..% loss on GBP1800.
        self.assertTrue(result["twr_pct"].startswith("6.666666"))
        self.assertTrue(result["max_drawdown_pct"].startswith("11.111111"))
        self.assertEqual(result["net_contributions_gbp"], "1600")
        with_fee = deepcopy(demo_events(NOW))
        with_fee[0]["amount_gbp"] = "801"
        with_fee[-1]["fee_gbp"] = "1"
        fee_result = report(replay(self.spec, with_fee, NOW), NOW)
        self.assertEqual(fee_result["gain_gbp"], "-1")
        self.assertLess(float(fee_result["twr_pct"]), 0)

    def test_funded_contribution_first_proposal_binding_and_external_deposit(self):
        self.init_demo()
        import_events(self.database, [ev("extra", "contribution", amount_gbp="100")], NOW)
        result = snapshot(self.database, NOW)
        self.assertEqual(result["proposal"]["status"], "review")
        self.assertEqual(result["proposal"]["orders"][0]["quantity"], "8")
        self.assertEqual(result["proposal"]["orders"][0]["estimated_notional_gbp"], "80")
        binding = proposal_export(self.database, NOW)
        self.assertEqual(binding["portfolio_id"], "synthetic-demo")
        self.assertEqual(binding["revision"], 5)
        self.assertEqual(binding["policy_id"], "human-demo-80-20")
        self.assertEqual(len(binding["policy_hash"]), 64)
        self.assertFalse(binding["execution_enabled"])
        self.assertIsNotNone(binding["expires_at"])
        import_events(self.database, [
            ev("external", "contribution", account="savings", amount_gbp="1000")], NOW)
        proposal = snapshot(self.database, NOW)["proposal"]
        self.assertLessEqual(sum(float(o["estimated_notional_gbp"]) for o in proposal["orders"]), 100)
        self.assertTrue(proposal["manual_transfers"])

    def test_external_savings_never_funds_broker_purchase(self):
        values = demo_events(NOW) + [
            ev("external", "contribution", account="savings", amount_gbp="1000")]
        result = report(replay(self.spec, values, NOW), NOW)
        self.assertEqual(result["proposal"]["orders"], [])
        self.assertTrue(result["proposal"]["manual_transfers"])

    def test_cli_local_json_functions_and_no_overwrite(self):
        root = Path(self.temporary.name)
        profile_path, events_path = root / "profile.json", root / "events.json"
        profile_path.write_text(json.dumps(self.spec), encoding="utf-8")
        events_path.write_text(json.dumps(demo_events(NOW)), encoding="utf-8")
        stream, errors = io.StringIO(), io.StringIO()
        self.assertEqual(main(["init", "--profile", str(profile_path), "--database",
                               str(self.database)], stdout=stream, stderr=errors, now=NOW), 0)
        self.assertEqual(main(["import", "--events", str(events_path), "--database",
                               str(self.database)], stdout=stream, stderr=errors, now=NOW), 0)
        for command in ("report", "propose", "export"):
            self.assertEqual(main([command, "--database", str(self.database)],
                                  stdout=stream, stderr=errors, now=NOW), 0)
        self.assertEqual(main(["init", "--profile", str(profile_path), "--database",
                               str(self.database)], stdout=stream, stderr=errors, now=NOW), 2)
        self.assertEqual(snapshot(self.database, NOW)["revision"], 4)
        demo = io.StringIO()
        self.assertEqual(main(["demo", "--contribution"], stdout=demo, now=NOW), 0)
        self.assertEqual(json.loads(demo.getvalue())["equity_gbp"], "1100")

    def test_json_duplicate_and_nonfinite_refused(self):
        file = Path(self.temporary.name) / "input.json"
        for raw in ('{"a":1,"a":2}', '[NaN]', '["unterminated]', '[' * 2000):
            file.write_text(raw, encoding="utf-8")
            with self.assertRaises(InvestmentError):
                load_json(file)

    def test_delayed_mark_cannot_support_preflow_return(self):
        t1 = NOW + timedelta(days=1)
        values = demo_events(NOW)
        delayed = ev("delayed", "mark", at=t1, product_id="equity", price_gbp="12.5")
        delayed["available_at"] = iso(t1 + timedelta(hours=1))
        values += [delayed, ev("flow", "contribution", at=t1, amount_gbp="100")]
        result = report(replay(self.spec, values, t1 + timedelta(hours=1)),
                        t1 + timedelta(hours=1))
        self.assertFalse(result["complete"])
        self.assertIsNone(result["equity_gbp"])
        self.assertIsNone(result["as_of"])
        self.assertEqual(result["proposal"]["status"], "blocked")
        self.assertEqual(result["proposal"]["orders"], [])
        self.assertIsNone(result["twr_pct"])
        self.assertIsNone(result["max_drawdown_pct"])

    def test_delayed_purchase_only_reference_blocks_proposal(self):
        available = NOW + timedelta(hours=1)
        mark = ev("late-reference", "mark", product_id="equity", price_gbp="10")
        mark["available_at"] = iso(available)
        values = [ev("fund", "contribution", amount_gbp="1000"), mark]
        result = report(replay(self.spec, values, available), available)
        # Cash needs no security mark, but the late reference cannot fund a
        # contemporaneous purchase proposal at the cash valuation clock.
        self.assertEqual(result["equity_gbp"], "1000")
        self.assertEqual(result["proposal"]["status"], "blocked")
        self.assertEqual(result["proposal"]["orders"], [])

    def test_full_withdrawal_refunding_preserves_linked_return_and_restart(self):
        t1, t2, t3 = (NOW + timedelta(days=d) for d in (1, 2, 3))
        values = [
            ev("fund", "contribution", amount_gbp="1000"),
            ev("income", "interest", at=t1, amount_gbp="100"),
            ev("all-out", "withdrawal", at=t2, amount_gbp="1100"),
            ev("refund", "contribution", at=t3, amount_gbp="1000"),
        ]
        initialize(self.database, self.spec, NOW)
        import_events(self.database, values[:3], t2)
        emptied = snapshot(self.database, t2)
        self.assertEqual(emptied["equity_gbp"], "0")
        import_events(self.database, values[3:], t3)
        expected = report(replay(self.spec, values, t3), t3)
        self.assertEqual(expected["twr_pct"], "10")
        self.assertEqual(expected["max_drawdown_pct"], "0")
        self.assertEqual(expected["net_contributions_gbp"], "900")
        self.assertEqual(expected["gain_gbp"], "100")
        self.assertEqual(snapshot(self.database, t3), expected)
        import_events(self.database, values, t3)
        self.assertEqual(snapshot(self.database, t3), expected)

    def test_missing_and_partial_product_selection_blocks_all_funded_orders(self):
        for products in ([], self.spec["products"]):
            spec = deepcopy(self.spec)
            spec["products"] = deepcopy(products)
            spec["policy"]["targets"] = {"equity": "60", "bond": "20", "cash": "20"}
            values = [ev("fund", "contribution", amount_gbp="1000")]
            if products:
                values.append(ev("reference", "mark", product_id="equity", price_gbp="10"))
            with self.subTest(products=products):
                result = report(replay(spec, values, NOW), NOW)
                self.assertTrue(result["complete"])
                self.assertEqual(result["proposal"]["status"], "blocked")
                self.assertIn("product", result["proposal"]["reason"].lower())
                self.assertIn("manual", result["proposal"]["reason"].lower())
                self.assertEqual(result["proposal"]["orders"], [])
                self.assertIsNone(result["proposal"]["expires_at"])

    def test_unused_unmarked_product_does_not_change_minimum_notional_no_trade(self):
        spec = deepcopy(self.spec)
        spec["products"][0]["minimum_notional_gbp"] = "1000"
        values = [
            ev("fund", "contribution", amount_gbp="1000"),
            ev("reference", "mark", product_id="equity", price_gbp="10"),
        ]
        baseline = report(replay(spec, values, NOW), NOW)["proposal"]
        self.assertEqual(baseline["status"], "no_trade")
        spec["products"].append(dict(self.spec["products"][0], id="unused-bond",
                                     name="Unused synthetic bond", sleeve="bond"))
        unused = report(replay(spec, values, NOW), NOW)["proposal"]
        self.assertEqual(unused, baseline)

    def test_unmarked_minimum_or_unfunded_purchase_is_no_trade_not_reference_block(self):
        minimum = deepcopy(self.spec)
        minimum["products"][0]["minimum_notional_gbp"] = "1000"
        funded = [ev("fund", "contribution", amount_gbp="1000")]
        result = report(replay(minimum, funded, NOW), NOW)
        self.assertEqual(result["proposal"]["status"], "no_trade")
        external_only = [ev("external", "contribution", account="savings", amount_gbp="1000")]
        result = report(replay(self.spec, external_only, NOW), NOW)
        self.assertEqual(result["proposal"]["status"], "no_trade")
        self.assertEqual(result["proposal"]["orders"], [])

    def test_partial_purchase_reference_warning_with_valid_alternative_and_other_sleeve(self):
        available = NOW + timedelta(hours=1)
        for unsupported in ("missing", "late"):
            spec = deepcopy(self.spec)
            spec["policy"]["targets"] = {"equity": "60", "bond": "20", "cash": "20"}
            spec["products"] = [
                dict(self.spec["products"][0], id="unsupported-equity"),
                dict(self.spec["products"][0], id="supported-equity"),
                dict(self.spec["products"][0], id="supported-bond", sleeve="bond"),
            ]
            values = [
                ev("fund", "contribution", amount_gbp="1000"),
                ev("equity-reference", "mark", product_id="supported-equity", price_gbp="10"),
                ev("bond-reference", "mark", product_id="supported-bond", price_gbp="20"),
            ]
            if unsupported == "late":
                late = ev("late-reference", "mark", product_id="unsupported-equity",
                          price_gbp="10")
                late["available_at"] = iso(available)
                values.append(late)
            with self.subTest(unsupported=unsupported):
                result = report(replay(spec, values, available), available)
                self.assertEqual(result["proposal"]["status"], "review")
                self.assertEqual(
                    [o["product_id"] for o in result["proposal"]["orders"]],
                    ["supported-equity", "supported-bond"])
                self.assertEqual(
                    [o["estimated_notional_gbp"] for o in result["proposal"]["orders"]],
                    ["600", "200"])
                self.assertIn("partial-reference", result["proposal"]["reason"].lower())
                self.assertIn("excluded", result["proposal"]["reason"].lower())

    def test_partial_purchase_reference_warning_for_unavailable_underweight_sleeve(self):
        available = NOW + timedelta(hours=1)
        spec = deepcopy(self.spec)
        spec["policy"]["targets"] = {"equity": "60", "bond": "20", "cash": "20"}
        spec["products"].append(dict(self.spec["products"][0], id="bond", sleeve="bond"))
        late = ev("late-bond-reference", "mark", product_id="bond", price_gbp="20")
        late["available_at"] = iso(available)
        values = [
            ev("fund", "contribution", amount_gbp="1000"),
            ev("reference", "mark", product_id="equity", price_gbp="10"),
            late,
        ]
        result = report(replay(spec, values, available), available)
        self.assertEqual(result["proposal"]["status"], "review")
        self.assertEqual([o["product_id"] for o in result["proposal"]["orders"]], ["equity"])
        self.assertIn("partial-reference", result["proposal"]["reason"].lower())
        self.assertIn("excluded", result["proposal"]["reason"].lower())

    def test_actual_sqlite_commit_failure_rolls_back_and_restart_can_retry(self):
        self.init_demo()
        actual_connect = store._connect
        actions = []

        def deny_commit(database, readonly):
            connection = actual_connect(database, readonly)

            def authorizer(action, arg1, arg2, database_name, trigger):
                if action == sqlite3.SQLITE_INSERT and arg1 == "events":
                    actions.append("insert-event")
                if action == sqlite3.SQLITE_TRANSACTION and arg1 == "COMMIT":
                    actions.append("commit-denied")
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK

            connection.set_authorizer(authorizer)
            return connection

        value = ev("new", "contribution", amount_gbp="100")
        with patch("investment.store._connect", side_effect=deny_commit):
            with self.assertRaises(InvestmentError):
                import_events(self.database, [value], NOW)
        self.assertEqual(actions, ["insert-event", "commit-denied"])
        self.assertEqual(snapshot(self.database, NOW)["revision"], 4)
        self.assertEqual(snapshot(self.database, NOW)["equity_gbp"], "1000")
        import_events(self.database, [value], NOW)
        self.assertEqual(snapshot(self.database, NOW)["revision"], 5)

    def test_actual_sqlite_writer_lock_fails_closed_then_retry_succeeds(self):
        self.init_demo()
        value = ev("new", "contribution", amount_gbp="100")
        with closing(sqlite3.connect(self.database)) as lock:
            lock.execute("BEGIN IMMEDIATE")
            with self.assertRaises(InvestmentError):
                import_events(self.database, [value], NOW)
            lock.rollback()
        self.assertEqual(snapshot(self.database, NOW)["revision"], 4)
        import_events(self.database, [value], NOW)
        self.assertEqual(snapshot(self.database, NOW)["equity_gbp"], "1100")

    def test_stdout_failure_does_not_undo_committed_import_or_export_read(self):
        self.init_demo()
        file = Path(self.temporary.name) / "new-events.json"
        file.write_text(json.dumps([ev("new", "contribution", amount_gbp="100")]),
                        encoding="utf-8")
        broken = io.StringIO()
        with patch.object(broken, "write", side_effect=OSError("Injected output failure")):
            with self.assertRaises(OSError):
                main(["import", "--events", str(file), "--database", str(self.database)],
                     stdout=broken, now=NOW)
        self.assertEqual(snapshot(self.database, NOW)["revision"], 5)
        before = self.database.read_bytes()
        with patch.object(broken, "write", side_effect=OSError("Injected output failure")):
            with self.assertRaises(OSError):
                main(["export", "--database", str(self.database)], stdout=broken, now=NOW)
        self.assertEqual(self.database.read_bytes(), before)
        self.assertEqual(snapshot(self.database, NOW)["equity_gbp"], "1100")
        self.assertEqual(main(["import", "--events", str(file), "--database", str(self.database)],
                              stdout=io.StringIO(), now=NOW), 0)
        self.assertEqual(snapshot(self.database, NOW)["revision"], 5)

    def test_pure_replay_identical_old_event_and_atomic_transition(self):
        later = NOW + timedelta(days=1)
        values = demo_events(NOW) + [
            ev("later", "mark", at=later, product_id="equity", price_gbp="11")]
        first = report(replay(self.spec, values, later), later)
        repeated = report(replay(self.spec, values + [values[0]], later), later)
        self.assertEqual(first, repeated)
        ledger = replay(self.spec, values, later)
        with self.assertRaises(InvestmentError):
            ledger.apply(ev("bad", "withdrawal", at=later, amount_gbp="10000"), later)
        self.assertEqual(report(ledger, later), first)

    def test_schema_accepts_reports_and_rejects_extras_nonfinite_and_false_complete(self):
        from jsonschema import Draft202012Validator, FormatChecker

        schema = json.loads((Path(__file__).parents[1] / "investment" /
                             "response.schema.json").read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        value = demo_snapshot(NOW)
        self.assertEqual(list(validator.iter_errors(value)), [])
        for changes in ({"equity_gbp": "NaN"}, {"unexpected": 1},
                        {"equity_gbp": None}, {"generated_at": "today"}):
            invalid = deepcopy(value)
            invalid.update(changes)
            self.assertTrue(list(validator.iter_errors(invalid)), changes)

    def test_monthly_annual_or_five_point_drift_and_minimum_no_trade(self):
        values = demo_events(NOW) + [
            ev("small", "contribution", amount_gbp="10")]
        same_month = report(replay(self.spec, values, NOW), NOW)
        self.assertEqual(same_month["proposal"]["status"], "no_trade")
        next_month = NOW + timedelta(days=31)
        monthly_values = values + [
            ev("fresh", "mark", at=next_month, product_id="equity", price_gbp="10")]
        monthly = report(replay(self.spec, monthly_values, next_month), next_month)
        # GBP8 deficit rounds below one-unit GBP10 product minimum.
        self.assertEqual(monthly["proposal"]["status"], "no_trade")
        anniversary = NOW.replace(year=2027)
        annual_values = demo_events(NOW) + [
            ev("annual", "mark", at=anniversary, product_id="equity", price_gbp="10")]
        annual = report(replay(self.spec, annual_values, anniversary), anniversary)
        self.assertIn("Annual", annual["proposal"]["reason"])
        minimum = deepcopy(self.spec)
        minimum["products"][0]["minimum_notional_gbp"] = "1000"
        drift_values = demo_events(NOW) + [
            ev("larger", "contribution", amount_gbp="100")]
        rejected = report(replay(minimum, drift_values, NOW), NOW)
        self.assertEqual(rejected["proposal"]["status"], "no_trade")

    def test_multiple_products_accounts_common_clock_and_projected_funding(self):
        spec = deepcopy(self.spec)
        spec["products"].append(dict(spec["products"][0], id="bond", name="Synthetic bond",
                                     sleeve="bond", quantity_increment="0.5"))
        spec["policy"]["targets"] = {"equity": "60", "bond": "20", "cash": "20"}
        values = [
            ev("fund", "contribution", amount_gbp="1000"),
            ev("equity-ref", "mark", product_id="equity", price_gbp="10"),
            ev("bond-ref", "mark", product_id="bond", price_gbp="20"),
        ]
        result = report(replay(spec, values, NOW), NOW)
        orders = result["proposal"]["orders"]
        self.assertEqual([o["estimated_notional_gbp"] for o in orders], ["600", "200"])
        self.assertLessEqual(sum(float(o["estimated_notional_gbp"]) for o in orders), 1000)
        bought = values + [
            ev("equity-buy", "buy", product_id="equity", quantity="60",
               price_gbp="10", fee_gbp="0"),
            ev("bond-buy", "buy", product_id="bond", quantity="10",
               price_gbp="20", fee_gbp="0")]
        later = NOW + timedelta(hours=1)
        mixed = bought + [
            ev("one-fresh", "mark", at=later, product_id="equity", price_gbp="11")]
        incomplete = report(replay(spec, mixed, later), later)
        self.assertFalse(incomplete["complete"])
        self.assertIsNone(incomplete["equity_gbp"])
        self.assertEqual(incomplete["proposal"]["orders"], [])
        mixed += [ev("all-fresh", "mark", at=later, product_id="bond", price_gbp="20")]
        complete = report(replay(spec, mixed, later), later)
        self.assertTrue(complete["complete"])
        self.assertEqual(complete["equity_gbp"], "1060")

    def test_unsupported_opening_actions_and_profile_fields_refused(self):
        for extra in ("opening_positions", "opening_cash_gbp", "optimizer", "leverage"):
            spec = deepcopy(self.spec)
            spec[extra] = "unsupported"
            with self.subTest(extra=extra), self.assertRaises(InvestmentError):
                replay(spec, [], NOW)
        for kind in ("correction", "accrual", "fx", "merger", "security_transfer",
                     "execute", "margin", "atr_stop"):
            with self.subTest(kind=kind), self.assertRaises(InvestmentError):
                replay(self.spec, [ev("unsupported", kind)], NOW)

    def test_new_id_cannot_replace_same_clock_mark_evidence(self):
        for price in ("10", "20"):
            values = demo_events(NOW) + [
                ev("different-source-id", "mark", product_id="equity", price_gbp=price)]
            with self.subTest(price=price), self.assertRaises(InvestmentError):
                replay(self.spec, values, NOW)


if __name__ == "__main__":
    unittest.main()
