"""Offline investment contracts; all stores and JSON fixtures are temporary."""

import builtins
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
import io
import importlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from threading import Barrier, Event, local
import unittest
from unittest.mock import patch

from investment import InvestmentError
from investment.__main__ import main
from investment.core import iso, replay
from investment.simulation import simulate_synthetic_fixture
from investment.service import (demo_events, demo_profile, demo_snapshot, proposal_export,
                                report, review_status, snapshot)
from investment.store import acknowledge_review, import_events, initialize, load_json, read
from investment import store


NOW = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)


def ev(identifier, kind, at=NOW, account="broker", **values):
    return dict(id=identifier, type=kind, timestamp=iso(at), available_at=iso(at),
                account_id=account, currency="GBP", evidence="explicit synthetic test evidence",
                **values)


def append_legacy_batch(database, values, state_hash):
    with closing(sqlite3.connect(database)) as connection, connection:
        revision, previous = connection.execute(
            "SELECT revision,hash FROM events ORDER BY revision DESC LIMIT 1").fetchone()
        for value in values:
            revision += 1
            payload = store.canonical(value)
            new_hash = store.digest(previous + payload)
            connection.execute("INSERT INTO events VALUES (?,?,?,?,?)",
                               (revision, value["id"], payload, previous, new_hash))
            previous = new_hash
        connection.execute("INSERT INTO commits VALUES (?,?)", (revision, state_hash))


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

    def zero_cash_rates(self, bars):
        first_open = min(row["open_at"] for row in bars)
        return [{"id": "explicit-zero-rate", "effective_at": first_open,
                 "available_at": first_open, "annual_rate": "0"}]

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
              "available_at": contribution_at, "amount_gbp": "100"}],
            cash_rates=self.zero_cash_rates(bars))

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
            "1000", products, {"equity": "100"}, bars, variable_cost_bps="100",
            cash_rates=self.zero_cash_rates(bars))

        self.assertEqual(result["curve"][1]["fills"][0]["quantity"], "99")
        self.assertEqual(result["curve"][1]["fills"][0]["fee_gbp"], "9.9")
        self.assertEqual(result["summary"]["cash_gbp"], "0.1")
        self.assertEqual(result["summary"]["ending_equity_gbp"], "990.1")
        self.assertEqual(result["summary"]["twr_pct"], "-0.99")
        self.assertEqual(result["summary"]["fees_gbp"], "9.9")

    def test_synthetic_cash_interest_uses_only_rates_known_at_interval_start(self):
        products = [{"id": "equity", "quantity_increment": "1",
                     "minimum_notional_gbp": "1"}]
        bars = [
            self.synthetic_bar("equity", 0, "10", "10"),
            self.synthetic_bar("equity", 1, "10", "10"),
        ]
        rates = [
            {"id": "initial-zero", "effective_at": bars[0]["open_at"],
             "available_at": bars[0]["open_at"], "annual_rate": "0"},
            {"id": "close-rate", "effective_at": bars[0]["close_at"],
             "available_at": bars[0]["close_at"], "annual_rate": "0.365"},
            {"id": "open-reset", "effective_at": bars[1]["open_at"],
             "available_at": bars[1]["open_at"], "annual_rate": "0.73"},
        ]
        result = simulate_synthetic_fixture(
            "1000", products, {"equity": "50"}, bars, cash_rates=rates)

        with localcontext() as context:
            context.prec = 100
            year_microseconds = Decimal(365 * 86400 * 1_000_000)
            overnight_interest = (
                Decimal(1000) * Decimal("0.365") * Decimal(17 * 3600 * 1_000_000)
                / year_microseconds)
            cash_after_fill = Decimal(500) + overnight_interest
            intraday_interest = (
                cash_after_fill * Decimal("0.73") * Decimal(7 * 3600 * 1_000_000)
                / year_microseconds)
            expected_interest = overnight_interest + intraday_interest
            actual_interest = Decimal(result["summary"]["cash_interest_gbp"])
            ending_equity = Decimal(result["summary"]["ending_equity_gbp"])
            self.assertEqual(actual_interest, expected_interest)
            self.assertEqual(ending_equity, Decimal(1000) + expected_interest)
        self.assertEqual(result["curve"][1]["fills"][0]["quantity"], "50")
        self.assertEqual(result["summary"]["cash_rates"], [
            {"id": item["id"], "effective_at": item["effective_at"],
             "available_at": item["available_at"],
             "annual_rate": item["annual_rate"]} for item in rates])

    def test_synthetic_cash_interest_requires_explicit_causal_rate_schedule(self):
        products = [{"id": "equity", "quantity_increment": "1",
                     "minimum_notional_gbp": "1"}]
        bars = [
            self.synthetic_bar("equity", 0, "10", "10"),
            self.synthetic_bar("equity", 1, "10", "10"),
        ]
        first_open = bars[0]["open_at"]
        second_open = bars[1]["open_at"]
        schedules = (
            [],
            [{"id": "late-initial", "effective_at": first_open,
              "available_at": iso(NOW + timedelta(hours=10)), "annual_rate": "0.01"}],
            [{"id": "off-clock", "effective_at": iso(NOW + timedelta(hours=1)),
              "available_at": iso(NOW + timedelta(hours=1)), "annual_rate": "0.01"}],
            [{"id": "missing-initial", "effective_at": second_open,
              "available_at": second_open, "annual_rate": "0.01"}],
        )
        for schedule in schedules:
            with self.subTest(schedule=schedule), self.assertRaises(InvestmentError):
                simulate_synthetic_fixture(
                    "1000", products, {"equity": "100"}, bars, cash_rates=schedule)

    def test_synthetic_cash_withdrawal_is_flow_neutral_and_does_not_sell(self):
        products = [{"id": "equity", "quantity_increment": "1",
                     "minimum_notional_gbp": "1"}]
        bars = [
            self.synthetic_bar("equity", 0, "10", "10"),
            self.synthetic_bar("equity", 1, "10", "11"),
            self.synthetic_bar("equity", 2, "11", "12"),
        ]
        withdrawal_at = bars[1]["close_at"]
        result = simulate_synthetic_fixture(
            "1000", products, {"equity": "80"}, bars,
            withdrawals=[{"id": "cash-withdrawal", "timestamp": withdrawal_at,
                          "available_at": withdrawal_at, "amount_gbp": "100"}],
            cash_rates=self.zero_cash_rates(bars))

        self.assertEqual(result["curve"][1]["cash_gbp"], "100")
        self.assertEqual(
            [(fill["product_id"], fill["quantity"]) for fill in result["curve"][1]["fills"]],
            [("equity", "80")])
        self.assertEqual(result["curve"][1]["unit_value"], "1.08")
        self.assertEqual(result["curve"][1]["net_contributions_gbp"], "900")
        self.assertEqual(result["summary"]["ending_equity_gbp"], "1060")
        self.assertEqual(result["summary"]["total_contributions_gbp"], "1000")
        self.assertEqual(result["summary"]["total_withdrawals_gbp"], "100")
        self.assertEqual(result["summary"]["net_contributions_gbp"], "900")
        self.assertEqual(result["summary"]["gain_gbp"], "160")
        self.assertEqual(result["summary"]["holdings"], [
            {"product_id": "equity", "quantity": "80"}])

    def test_synthetic_same_close_contribution_and_withdrawal_net_before_unitization(self):
        products = [{"id": "equity", "quantity_increment": "1",
                     "minimum_notional_gbp": "1"}]
        bars = [
            self.synthetic_bar("equity", 0, "10", "10"),
            self.synthetic_bar("equity", 1, "10", "10"),
        ]
        flow_at = bars[0]["close_at"]
        result = simulate_synthetic_fixture(
            "1000", products, {"equity": "100"}, bars,
            contributions=[{"id": "same-clock-in", "timestamp": flow_at,
                            "available_at": flow_at, "amount_gbp": "100"}],
            withdrawals=[{"id": "same-clock-out", "timestamp": flow_at,
                          "available_at": flow_at, "amount_gbp": "1050"}],
            cash_rates=self.zero_cash_rates(bars))

        self.assertEqual(result["curve"][0]["cash_gbp"], "50")
        self.assertEqual(result["curve"][0]["unit_value"], "1")
        self.assertEqual(result["curve"][0]["net_contributions_gbp"], "50")
        self.assertEqual(result["summary"]["total_contributions_gbp"], "1100")
        self.assertEqual(result["summary"]["total_withdrawals_gbp"], "1050")
        self.assertEqual(result["summary"]["net_contributions_gbp"], "50")

    def test_synthetic_distribution_receivable_survives_split_until_pay_date(self):
        products = [{"id": "equity", "quantity_increment": "1",
                     "minimum_notional_gbp": "1"}]
        bars = [
            self.synthetic_bar("equity", 0, "10", "10"),
            self.synthetic_bar("equity", 1, "10", "10"),
            self.synthetic_bar("equity", 2, "10", "9"),
            self.synthetic_bar("equity", 3, "4.5", "4.5"),
        ]
        ex_at = bars[2]["open_at"]
        pay_at = bars[3]["close_at"]
        split_at = bars[3]["open_at"]
        result = simulate_synthetic_fixture(
            "1000", products, {"equity": "100"}, bars,
            splits=[{"id": "post-ex-split", "product_id": "equity",
                     "effective_at": split_at, "available_at": split_at,
                     "numerator": "2", "denominator": "1"}],
            cash_rates=self.zero_cash_rates(bars),
            distributions=[{"id": "synthetic-dividend", "product_id": "equity",
                            "ex_at": ex_at, "pay_at": pay_at, "available_at": ex_at,
                            "amount_per_unit_gbp": "1"}])

        self.assertEqual(result["curve"][2]["cash_gbp"], "0")
        self.assertEqual(result["curve"][2]["distribution_receivables_gbp"], "100")
        self.assertEqual(result["curve"][2]["unit_value"], "1")
        self.assertEqual(result["curve"][2]["distributions"], ["synthetic-dividend"])
        self.assertEqual(result["curve"][3]["cash_gbp"], "100")
        self.assertEqual(result["curve"][3]["distribution_receivables_gbp"], "0")
        self.assertEqual(result["curve"][3]["distribution_payments"], ["synthetic-dividend"])
        self.assertEqual(result["curve"][3]["unit_value"], "1")
        self.assertEqual(result["summary"]["distributions_gbp"], "100")
        self.assertEqual(result["summary"]["distributions_paid_gbp"], "100")
        self.assertEqual(result["summary"]["ending_equity_gbp"], "1000")
        self.assertEqual(result["summary"]["distributions"], [{
            "id": "synthetic-dividend",
            "product_id": "equity",
            "ex_at": ex_at,
            "available_at": ex_at,
            "pay_at": pay_at,
            "amount_per_unit_gbp": "1",
            "entitlement_quantity": "100",
            "amount_gbp": "100",
        }])

    def test_synthetic_distribution_rejects_late_unclocked_or_unheld_events(self):
        products = [{"id": "equity", "quantity_increment": "1",
                     "minimum_notional_gbp": "1"}]
        bars = [
            self.synthetic_bar("equity", 0, "10", "10"),
            self.synthetic_bar("equity", 1, "10", "10"),
            self.synthetic_bar("equity", 2, "10", "10"),
        ]
        valid = {"id": "dividend", "product_id": "equity",
                 "ex_at": bars[2]["open_at"], "pay_at": bars[2]["close_at"],
                 "available_at": bars[2]["open_at"], "amount_per_unit_gbp": "1"}
        invalid = (
            {**valid, "ex_at": iso(NOW + timedelta(days=2, hours=1))},
            {**valid, "available_at": iso(NOW + timedelta(days=2, hours=10))},
            {**valid, "pay_at": bars[1]["close_at"]},
        )
        for event in invalid:
            with self.subTest(event=event), self.assertRaises(InvestmentError):
                simulate_synthetic_fixture(
                    "1000", products, {"equity": "100"}, bars,
                    cash_rates=self.zero_cash_rates(bars), distributions=[event])

        split_on_ex_open = {
            "id": "same-clock-split", "product_id": "equity",
            "effective_at": bars[2]["open_at"], "available_at": bars[2]["open_at"],
            "numerator": "2", "denominator": "1",
        }
        with self.assertRaises(InvestmentError):
            simulate_synthetic_fixture(
                "1000", products, {"equity": "100"}, bars,
                splits=[split_on_ex_open], cash_rates=self.zero_cash_rates(bars),
                distributions=[valid])

        event_at_first_buy = {
            **valid, "ex_at": bars[1]["open_at"], "pay_at": bars[1]["close_at"],
            "available_at": bars[1]["open_at"],
        }
        with self.assertRaisesRegex(InvestmentError, "positive ex-time holding"):
            simulate_synthetic_fixture(
                "1000", products, {"equity": "100"}, bars,
                cash_rates=self.zero_cash_rates(bars), distributions=[event_at_first_buy])

    def test_synthetic_distribution_payment_can_fund_same_close_withdrawal(self):
        products = [{"id": "equity", "quantity_increment": "1",
                     "minimum_notional_gbp": "1"}]
        bars = [
            self.synthetic_bar("equity", 0, "10", "10"),
            self.synthetic_bar("equity", 1, "10", "10"),
            self.synthetic_bar("equity", 2, "10", "9"),
        ]
        ex_at = bars[2]["open_at"]
        pay_at = bars[2]["close_at"]
        result = simulate_synthetic_fixture(
            "1000", products, {"equity": "100"}, bars,
            withdrawals=[{"id": "pay-date-withdrawal", "timestamp": pay_at,
                          "available_at": pay_at, "amount_gbp": "100"}],
            cash_rates=self.zero_cash_rates(bars),
            distributions=[{"id": "same-day-pay", "product_id": "equity",
                            "ex_at": ex_at, "pay_at": pay_at, "available_at": ex_at,
                            "amount_per_unit_gbp": "1"}])

        self.assertEqual(result["curve"][2]["cash_gbp"], "0")
        self.assertEqual(result["curve"][2]["distribution_receivables_gbp"], "0")
        self.assertEqual(result["curve"][2]["unit_value"], "1")
        self.assertEqual(result["summary"]["total_withdrawals_gbp"], "100")
        self.assertEqual(result["summary"]["distributions_paid_gbp"], "100")
        self.assertEqual(result["summary"]["ending_equity_gbp"], "900")
        self.assertEqual(result["summary"]["gain_gbp"], "0")

    def test_synthetic_distribution_receivable_accrues_cash_interest_only_after_payment(self):
        products = [{"id": "equity", "quantity_increment": "1",
                     "minimum_notional_gbp": "1"}]
        bars = [
            self.synthetic_bar("equity", 0, "10", "10"),
            self.synthetic_bar("equity", 1, "10", "10"),
            self.synthetic_bar("equity", 2, "10", "9"),
            self.synthetic_bar("equity", 3, "9", "9"),
            self.synthetic_bar("equity", 4, "9", "9"),
        ]
        rates = [
            {"id": "zero-before-pay", "effective_at": bars[0]["open_at"],
             "available_at": bars[0]["open_at"], "annual_rate": "0"},
            {"id": "interest-after-pay", "effective_at": bars[3]["close_at"],
             "available_at": bars[3]["close_at"], "annual_rate": "0.365"},
        ]
        distributions = [{
            "id": "dividend-paid-later",
            "product_id": "equity",
            "ex_at": bars[2]["open_at"],
            "pay_at": bars[3]["close_at"],
            "available_at": bars[2]["open_at"],
            "amount_per_unit_gbp": "1",
        }]
        result = simulate_synthetic_fixture(
            "1000", products, {"equity": "100"}, bars,
            cash_rates=rates, distributions=distributions)

        with localcontext() as context:
            context.prec = 100
            year_microseconds = Decimal(365 * 86400 * 1_000_000)
            overnight = (Decimal(100) * Decimal("0.365")
                         * Decimal(17 * 3600 * 1_000_000) / year_microseconds)
            intraday = ((Decimal(100) + overnight) * Decimal("0.365")
                        * Decimal(7 * 3600 * 1_000_000) / year_microseconds)
            expected_interest = overnight + intraday
            expected_cash = Decimal(100) + expected_interest

        self.assertEqual(result["curve"][2]["cash_interest_gbp"], "0")
        self.assertEqual(result["curve"][3]["cash_interest_gbp"], "0")
        self.assertEqual(result["curve"][3]["distribution_receivables_gbp"], "0")
        self.assertEqual(result["curve"][3]["cash_gbp"], "100")
        self.assertEqual(
            Decimal(result["curve"][4]["cash_interest_gbp"]), expected_interest,
            msg=f"actual={result['curve'][4]['cash_interest_gbp']} expected={expected_interest}")
        self.assertEqual(
            Decimal(result["curve"][4]["cash_gbp"]), expected_cash,
            msg=f"cash={result['curve'][4]['cash_gbp']} expected={expected_cash}")

    def test_synthetic_contribution_allocation_values_unpaid_distribution_receivable(self):
        products = [{"id": "equity", "quantity_increment": "1",
                     "minimum_notional_gbp": "1"}]
        bars = [
            self.synthetic_bar("equity", 0, "10", "10"),
            self.synthetic_bar("equity", 1, "10", "10"),
            self.synthetic_bar("equity", 2, "10", "9"),
            self.synthetic_bar("equity", 3, "9", "9"),
            self.synthetic_bar("equity", 4, "9", "9"),
        ]
        contribution_at = bars[2]["close_at"]
        ex_at = bars[2]["open_at"]
        pay_at = bars[4]["close_at"]
        result = simulate_synthetic_fixture(
            "1000", products, {"equity": "80"}, bars,
            contributions=[{"id": "new-cash", "timestamp": contribution_at,
                            "available_at": contribution_at, "amount_gbp": "100"}],
            cash_rates=self.zero_cash_rates(bars),
            distributions=[{"id": "unpaid-distribution", "product_id": "equity",
                            "ex_at": ex_at, "pay_at": pay_at, "available_at": ex_at,
                            "amount_per_unit_gbp": "1"}])

        self.assertEqual(result["curve"][2]["equity_gbp"], "1100")
        self.assertEqual(result["curve"][2]["distribution_receivables_gbp"], "80")
        self.assertEqual(result["curve"][3]["fills"][0]["quantity"], "17")
        self.assertEqual(result["curve"][3]["fills"][0]["notional_gbp"], "153")
        self.assertEqual(result["curve"][3]["equity_gbp"], "1100")
        self.assertEqual(result["curve"][3]["cash_gbp"], "147")
        self.assertEqual(result["curve"][3]["distribution_receivables_gbp"], "80")
        self.assertEqual(result["curve"][4]["cash_gbp"], "227")
        self.assertEqual(result["curve"][4]["distribution_receivables_gbp"], "0")
        self.assertEqual(result["curve"][4]["equity_gbp"], "1100")
        self.assertEqual(result["summary"]["holdings"], [
            {"product_id": "equity", "quantity": "97"}])

    def test_synthetic_full_cash_withdrawal_and_refunding_preserve_unit_link(self):
        products = [{"id": "equity", "quantity_increment": "1",
                     "minimum_notional_gbp": "2000"}]
        bars = [
            self.synthetic_bar("equity", 0, "10", "10"),
            self.synthetic_bar("equity", 1, "10", "10"),
            self.synthetic_bar("equity", 2, "10", "10"),
        ]
        first_open = NOW.replace(hour=8)
        modeled_clocks = (
            (first_open, first_open + timedelta(hours=1)),
            (first_open + timedelta(days=1, hours=1),
             first_open + timedelta(days=1, hours=2)),
            (first_open + timedelta(days=2, hours=1),
             first_open + timedelta(days=2, hours=2)),
        )
        for bar, (open_at, close_at) in zip(bars, modeled_clocks):
            bar["open_at"] = bar["open_available_at"] = iso(open_at)
            bar["close_at"] = bar["close_available_at"] = iso(close_at)
        expected_unit_value = Decimal("1.001")
        withdrawal_amount = "1001"
        withdrawal_at = bars[1]["close_at"]
        contribution_at = bars[2]["close_at"]
        result = simulate_synthetic_fixture(
            "1000", products, {"equity": "100"}, bars,
            contributions=[{"id": "refunding", "timestamp": contribution_at,
                            "available_at": contribution_at, "amount_gbp": "1000"}],
            withdrawals=[{"id": "full-redemption", "timestamp": withdrawal_at,
                          "available_at": withdrawal_at, "amount_gbp": withdrawal_amount}],
            cash_rates=[
                {"id": "initial-rate", "effective_at": bars[0]["open_at"],
                 "available_at": bars[0]["open_at"], "annual_rate": "0.365"},
                {"id": "zero-rate", "effective_at": bars[1]["open_at"],
                 "available_at": bars[1]["open_at"], "annual_rate": "0"},
            ])

        self.assertEqual(result["curve"][1]["cash_gbp"], "0")
        self.assertEqual(Decimal(result["curve"][1]["unit_value"]), expected_unit_value)
        self.assertLess(
            abs(Decimal(result["curve"][2]["unit_value"]) - expected_unit_value),
            Decimal("1e-90"))
        self.assertEqual(result["summary"]["total_contributions_gbp"], "2000")
        self.assertEqual(result["summary"]["total_withdrawals_gbp"], withdrawal_amount)
        self.assertEqual(
            Decimal(result["summary"]["net_contributions_gbp"]),
            Decimal(999))
        self.assertEqual(Decimal(result["summary"]["gain_gbp"]), Decimal(1))

    def test_synthetic_cash_withdrawal_refuses_implicit_sales_and_late_events(self):
        products = [{"id": "equity", "quantity_increment": "1",
                     "minimum_notional_gbp": "1"}]
        bars = [
            self.synthetic_bar("equity", 0, "10", "10"),
            self.synthetic_bar("equity", 1, "10", "10"),
        ]
        withdrawal_at = bars[1]["close_at"]
        unsupported_sale = [{"id": "unfunded", "timestamp": withdrawal_at,
                             "available_at": withdrawal_at, "amount_gbp": "1"}]
        with self.assertRaisesRegex(InvestmentError, "cash.*sales are unsupported"):
            simulate_synthetic_fixture(
                "1000", products, {"equity": "100"}, bars,
                withdrawals=unsupported_sale, cash_rates=self.zero_cash_rates(bars))

        invalid_events = (
            [{"id": "off-clock", "timestamp": bars[1]["open_at"],
              "available_at": bars[1]["open_at"], "amount_gbp": "1"}],
            [{"id": "late", "timestamp": withdrawal_at,
              "available_at": iso(NOW + timedelta(days=1, hours=5)),
              "amount_gbp": "1"}],
        )
        for event in invalid_events:
            with self.subTest(event=event), self.assertRaises(InvestmentError):
                simulate_synthetic_fixture(
                    "1000", products, {"equity": "100"}, bars,
                    withdrawals=event, cash_rates=self.zero_cash_rates(bars))

    def test_synthetic_simulator_reports_minimum_notional_skips(self):
        products = [{"id": "equity", "quantity_increment": "1",
                     "minimum_notional_gbp": "1001"}]
        bars = [
            self.synthetic_bar("equity", 0, "10", "10"),
            self.synthetic_bar("equity", 1, "10", "10"),
        ]
        result = simulate_synthetic_fixture(
            "1000", products, {"equity": "100"}, bars,
            cash_rates=self.zero_cash_rates(bars))

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
              "available_at": contribution_at, "amount_gbp": "100"}],
            cash_rates=self.zero_cash_rates(bars))

        self.assertEqual(result["curve"][1]["unit_value"], "0.8")
        self.assertEqual(result["curve"][1]["equity_gbp"], "180")
        self.assertEqual(result["curve"][1]["drawdown_pct"], "20")
        self.assertEqual(result["summary"]["max_drawdown_pct"], "20")

    def test_synthetic_simulator_split_preserves_quantity_value_and_flow_return(self):
        products = [{"id": "equity", "quantity_increment": "1",
                     "minimum_notional_gbp": "1"}]
        bars = [
            self.synthetic_bar("equity", 0, "10", "10"),
            self.synthetic_bar("equity", 1, "10", "10"),
            self.synthetic_bar("equity", 2, "5", "5"),
        ]
        split_at = bars[2]["open_at"]
        contribution_at = bars[1]["close_at"]
        result = simulate_synthetic_fixture(
            "1000", products, {"equity": "100"}, bars,
            [{"id": "top-up", "timestamp": contribution_at,
              "available_at": contribution_at, "amount_gbp": "100"}],
            splits=[{"id": "two-for-one", "product_id": "equity",
                     "effective_at": split_at, "available_at": contribution_at,
                     "numerator": "2", "denominator": "1"}],
            cash_rates=self.zero_cash_rates(bars))

        self.assertEqual(result["curve"][1]["equity_gbp"], "1100")
        self.assertEqual(result["curve"][2]["equity_gbp"], "1100")
        self.assertEqual(result["curve"][2]["splits"], ["two-for-one"])
        self.assertEqual(result["curve"][2]["fills"][0]["execution_at"], split_at)
        self.assertEqual(result["curve"][2]["fills"][0]["price_gbp"], "5")
        self.assertEqual(result["curve"][2]["fills"][0]["quantity"], "20")
        self.assertEqual(result["summary"]["holdings"], [
            {"product_id": "equity", "quantity": "220"},
        ])
        self.assertEqual(result["summary"]["twr_pct"], "0")

    def test_synthetic_simulator_rejects_late_off_clock_duplicate_and_fractional_splits(self):
        products = [{"id": "equity", "quantity_increment": "1",
                     "minimum_notional_gbp": "1"}]
        bars = [
            self.synthetic_bar("equity", 0, "10", "10"),
            self.synthetic_bar("equity", 1, "10", "10"),
            self.synthetic_bar("equity", 2, "5", "5"),
        ]
        split_at = bars[2]["open_at"]
        split = {"id": "split", "product_id": "equity", "effective_at": split_at,
                 "available_at": split_at, "numerator": "1", "denominator": "3"}
        invalid_splits = (
            [dict(split, available_at=iso(NOW + timedelta(days=2, hours=1)))],
            [dict(split, effective_at=bars[2]["close_at"],
                  available_at=bars[2]["close_at"])],
            [split, dict(split, id="second")],
        )
        for values in invalid_splits:
            with self.subTest(values=values), self.assertRaises(InvestmentError):
                simulate_synthetic_fixture(
                    "1000", products, {"equity": "100"}, bars, splits=values,
                    cash_rates=self.zero_cash_rates(bars))

        two_products = [
            {"id": "equity", "quantity_increment": "1", "minimum_notional_gbp": "1"},
            {"id": "bond", "quantity_increment": "1", "minimum_notional_gbp": "1"},
        ]
        two_product_bars = [
            self.synthetic_bar(product_id, session, price, price)
            for session, price in ((0, "10"), (1, "10"), (2, "5"))
            for product_id in ("equity", "bond")
        ]
        out_of_order = [
            {"id": "equity-split", "product_id": "equity", "effective_at": split_at,
             "available_at": bars[1]["close_at"], "numerator": "2", "denominator": "1"},
            {"id": "bond-split", "product_id": "bond", "effective_at": split_at,
             "available_at": bars[1]["close_at"], "numerator": "2", "denominator": "1"},
        ]
        with self.assertRaisesRegex(InvestmentError, "stable event-time order"):
            simulate_synthetic_fixture(
                "1000", two_products, {"equity": "50", "bond": "50"},
                two_product_bars, splits=out_of_order,
                cash_rates=self.zero_cash_rates(two_product_bars))

        thirds = [dict(split, numerator="1", denominator="3")]
        with self.assertRaisesRegex(InvestmentError, "quantity increment"):
            simulate_synthetic_fixture(
                "1000", products, {"equity": "100"}, bars, splits=thirds,
                cash_rates=self.zero_cash_rates(bars))

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
                                       bad_close, cash_rates=self.zero_cash_rates(bad_close))

        bad_open = deepcopy(bars)
        bad_open[0]["open_available_at"] = iso(
            NOW.replace(hour=9) + timedelta(minutes=1))
        with self.assertRaises(InvestmentError):
            simulate_synthetic_fixture("1000", products, {"equity": "80", "bond": "20"},
                                       bad_open, cash_rates=self.zero_cash_rates(bad_open))

        with self.assertRaises(InvestmentError):
            simulate_synthetic_fixture("1000", products, {"equity": "80", "bond": "20"},
                                       bars[:-1], cash_rates=self.zero_cash_rates(bars[:-1]))

        late_flow = [{"id": "late", "timestamp": iso(NOW.replace(hour=16)),
                      "available_at": iso(NOW.replace(hour=17)), "amount_gbp": "100"}]
        with self.assertRaises(InvestmentError):
            simulate_synthetic_fixture("1000", products, {"equity": "80", "bond": "20"},
                                       bars, late_flow, cash_rates=self.zero_cash_rates(bars))


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

    def test_implicit_read_rejects_identity_view_without_executing_it(self):
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("PRAGMA user_version=1")
            connection.execute("CREATE VIEW identity AS SELECT tripwire() AS id")
        calls = []
        actual_connect = store._connect

        def instrumented_connect(database, readonly):
            connection = actual_connect(database, readonly)
            connection.create_function("tripwire", 0, lambda: calls.append(True) or 1)
            return connection

        with patch("investment.store._connect", side_effect=instrumented_connect):
            with self.assertRaises(InvestmentError):
                read(self.database)
        self.assertEqual(calls, [])

    def test_write_rejects_invalid_table_check_without_executing_it(self):
        self.init_demo()
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("PRAGMA writable_schema=ON")
            cursor = connection.execute(
                "UPDATE sqlite_master SET sql=replace(sql, 'CHECK(id=1)', 'CHECK(tripwire())') "
                "WHERE type='table' AND name='identity'")
            self.assertEqual(cursor.rowcount, 1)
        calls = []
        actual_connect = store._connect

        def instrumented_connect(database, readonly):
            connection = actual_connect(database, readonly)
            connection.create_function("tripwire", 0, lambda: calls.append(True) or 1)
            return connection

        original = self.database.read_bytes()
        with patch("investment.store._connect", side_effect=instrumented_connect):
            with self.assertRaises(InvestmentError):
                import_events(self.database, [ev("new", "contribution", amount_gbp="1")], NOW)
        self.assertEqual(calls, [])
        self.assertEqual(self.database.read_bytes(), original)

    def test_write_rejects_invalid_expression_index_without_executing_it(self):
        self.init_demo()
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.create_function("tripwire", 1, lambda _: 1, deterministic=True)
            connection.execute("CREATE INDEX unexpected_expression ON events(tripwire(payload))")
        calls = []
        actual_connect = store._connect

        def instrumented_connect(database, readonly):
            connection = actual_connect(database, readonly)
            connection.create_function("tripwire", 1, lambda _: calls.append(True) or 1,
                                       deterministic=True)
            return connection

        original = self.database.read_bytes()
        with patch("investment.store._connect", side_effect=instrumented_connect):
            with self.assertRaises(InvestmentError):
                import_events(self.database, [ev("new", "contribution", amount_gbp="1")], NOW)
        self.assertEqual(calls, [])
        self.assertEqual(self.database.read_bytes(), original)

    def test_implicit_read_pins_snapshot_before_clock_and_concurrent_commit(self):
        spec = deepcopy(self.spec)
        spec["products"] = []
        spec["policy"]["targets"] = {"equity": "0", "bond": "0", "cash": "100"}
        initialize(self.database, spec, NOW)
        import_events(self.database, [ev("fund", "contribution", amount_gbp="100")], NOW)
        before = NOW + timedelta(hours=1)
        later = before + timedelta(seconds=1)
        confirmation = {
            "id": "concurrent-cash", "type": "cash_confirmation", "timestamp": iso(later),
            "available_at": iso(later), "evidence": "synthetic",
            "balances_gbp": {"broker": "100", "savings": "0"},
        }
        actual_clock = store.clock

        def after_snapshot(value=None):
            if value is not None:
                return actual_clock(value)
            with closing(sqlite3.connect(self.database, timeout=0)) as writer:
                writer.execute("BEGIN IMMEDIATE")
                writer.execute("INSERT INTO events VALUES (2,'probe','{}','','')")
                try:
                    with self.assertRaisesRegex(sqlite3.OperationalError, "locked"):
                        writer.commit()
                finally:
                    writer.rollback()
            return before

        with patch("investment.store.clock", side_effect=after_snapshot):
            ledger, captured = store.read_with_clock(self.database)
        self.assertEqual(ledger.revision, 1)
        self.assertEqual(captured, before)
        self.assertEqual(import_events(self.database, [confirmation], later), 2)
        self.assertEqual(read(self.database, later).revision, 2)

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

        drift_time = NOW + timedelta(hours=1)
        for price, expected_status in (("7.51", "no_trade"), ("7.5", "review"),
                                       ("7.49", "review")):
            drift_values = demo_events(NOW) + [
                ev("drift-funding", "transfer", at=drift_time, account="savings",
                   amount_gbp="40", destination_account_id="broker"),
                ev("drift-mark", "mark", at=drift_time, product_id="equity",
                   price_gbp=price)]
            with self.subTest(price=price):
                drift = report(replay(self.spec, drift_values, drift_time), drift_time)
                self.assertEqual(drift["proposal"]["status"], expected_status)
                if expected_status == "review":
                    self.assertTrue(drift["proposal"]["orders"])
                else:
                    self.assertIn("below 5 percentage points", drift["proposal"]["reason"])

        minimum = deepcopy(self.spec)
        minimum["products"][0]["minimum_notional_gbp"] = "1000"
        minimum_values = demo_events(NOW) + [
            ev("larger", "contribution", amount_gbp="100")]
        rejected = report(replay(minimum, minimum_values, NOW), NOW)
        self.assertEqual(rejected["proposal"]["status"], "no_trade")

    def test_annual_review_acknowledgement_resets_calendar_trigger(self):
        self.init_demo()
        annual = NOW.replace(year=2027)
        import_events(self.database, [
            ev("annual-mark", "mark", at=annual, product_id="equity", price_gbp="11")
        ], annual)
        before = snapshot(self.database, annual)
        self.assertEqual(before["proposal"]["orders"][0]["side"], "sell")
        acknowledgement = {
            "id": "annual-review", "type": "review_acknowledgement",
            "timestamp": iso(annual), "available_at": iso(annual),
            "evidence": "synthetic manual review", "review_kind": "annual",
            "reviewed_revision": before["revision"], "valuation_as_of": before["as_of"],
        }
        import_events(self.database, [acknowledgement], annual)
        after = snapshot(self.database, annual)
        self.assertEqual(after["proposal"]["orders"], [])
        self.assertEqual(after["revision"], before["revision"] + 1)
        for field in ("as_of", "equity_gbp", "gain_gbp", "twr_pct", "max_drawdown_pct"):
            self.assertEqual(after[field], before[field])

    def test_monthly_and_annual_review_clocks_are_independent(self):
        self.init_demo()
        monthly = NOW.replace(month=2)
        import_events(self.database, [
            ev("feb-mark", "mark", at=monthly, product_id="equity", price_gbp="10"),
            ev("feb-contribution", "contribution", at=monthly, amount_gbp="40"),
        ], monthly)
        status = review_status(self.database, monthly)
        self.assertTrue(status["monthly_due"])
        self.assertFalse(status["annual_due"])
        self.assertFalse(status["drift_due"])
        self.assertEqual(snapshot(self.database, monthly)["proposal"]["orders"][0]["quantity"], "3")
        acknowledge_review(self.database, "feb-review", "monthly", status["revision"],
                           "reviewed synthetic allocation", monthly)
        status = review_status(self.database, monthly)
        self.assertFalse(status["monthly_due"])
        self.assertIsNone(status["last_annual_review_at"])
        self.assertEqual(snapshot(self.database, monthly)["proposal"]["orders"], [])

        next_month = NOW.replace(month=3)
        import_events(self.database, [
            ev("mar-mark", "mark", at=next_month, product_id="equity", price_gbp="10")
        ], next_month)
        self.assertTrue(review_status(self.database, next_month)["monthly_due"])
        annual = NOW.replace(year=2027)
        import_events(self.database, [
            ev("year-mark", "mark", at=annual, product_id="equity", price_gbp="10")
        ], annual)
        status = review_status(self.database, annual)
        self.assertTrue(status["annual_due"])
        acknowledge_review(self.database, "year-review", "annual", status["revision"],
                           "reviewed synthetic annual allocation", annual)
        status = review_status(self.database, annual)
        self.assertFalse(status["annual_due"])
        self.assertFalse(status["monthly_due"])
        self.assertEqual(status["last_annual_review_at"], iso(annual))
        self.assertEqual(status["last_monthly_review_at"], iso(annual))

        later = annual.replace(month=2)
        import_events(self.database, [
            ev("next-feb-mark", "mark", at=later, product_id="equity", price_gbp="10")
        ], later)
        acknowledge_review(self.database, "next-feb-review", "monthly",
                           review_status(self.database, later)["revision"],
                           "reviewed next month", later)
        anniversary = annual.replace(year=2028)
        self.assertFalse(review_status(self.database, anniversary - timedelta(microseconds=1))
                         ["annual_due"])
        self.assertTrue(review_status(self.database, anniversary)["annual_due"])

    def test_acknowledgement_never_suppresses_five_point_drift(self):
        spec = deepcopy(self.spec)
        spec["policy"]["targets"] = {"equity": "75", "bond": "0", "cash": "25"}
        initialize(self.database, spec, NOW)
        import_events(self.database, demo_events(NOW), NOW)
        review_time = NOW + timedelta(hours=1)
        before = snapshot(self.database, review_time)
        self.assertEqual(before["proposal"]["orders"][0]["quantity"], "5")
        self.assertTrue(review_status(self.database, review_time)["drift_due"])
        acknowledge_review(self.database, "drift-review", "annual", before["revision"],
                           "reviewed but did not trade", review_time)
        after = snapshot(self.database, review_time)
        status = review_status(self.database, review_time)
        self.assertTrue(status["drift_due"])
        self.assertFalse(status["monthly_due"])
        self.assertFalse(status["annual_due"])
        self.assertEqual(after["proposal"]["orders"], before["proposal"]["orders"])
        self.assertFalse(after["proposal"]["execution_enabled"])

    def test_review_status_agrees_with_proposal_at_exact_drift_boundary(self):
        spec = deepcopy(self.spec)
        spec["policy"]["targets"] = {"equity": "75", "bond": "0", "cash": "25"}
        spec["products"][0]["quantity_increment"] = "0.00000001"
        for label, buy_price, sell_price, due in (
                ("below", "0.00000001", "0.00000002", False),
                ("at", None, None, True),
                ("above", "0.00000002", "0.00000001", True)):
            with self.subTest(label=label):
                database = Path(self.temporary.name) / (label + ".sqlite3")
                initialize(database, spec, NOW)
                events = [
                    ev("fund", "contribution", amount_gbp="250000000000000.75"),
                    ev("buy", "buy", product_id="equity", quantity="1",
                       price_gbp="1", fee_gbp="0"),
                ]
                if buy_price is not None:
                    events += [
                        ev("small-buy", "buy", product_id="equity",
                           quantity="0.00000001", price_gbp=buy_price, fee_gbp="0"),
                        ev("small-sell", "sell", product_id="equity",
                           quantity="0.00000001", price_gbp=sell_price, fee_gbp="0"),
                    ]
                events.append(ev("mark", "mark", product_id="equity",
                                 price_gbp="999999999999999"))
                import_events(database, events, NOW)
                result = snapshot(database, NOW)
                drift = Decimal(result["allocation"][0]["drift_pp"]).copy_abs()
                self.assertEqual(drift >= 5, due)
                self.assertEqual(review_status(database, NOW)["drift_due"], due)
                self.assertEqual(result["proposal"]["status"], "review" if due else "no_trade")

    def test_review_acknowledgement_requires_fresh_complete_unchanged_evidence(self):
        self.init_demo()
        just_fresh = NOW + timedelta(days=1) - timedelta(microseconds=1)
        expiry = NOW + timedelta(days=1)
        self.assertTrue(review_status(self.database, just_fresh)["acknowledgement_available"])
        stale = review_status(self.database, expiry)
        self.assertEqual(stale["valuation_status"], "stale")
        self.assertIsNone(stale["drift_due"])
        self.assertFalse(stale["acknowledgement_available"])
        original = self.database.read_bytes()
        with self.assertRaisesRegex(InvestmentError, "complete, fresh"):
            acknowledge_review(self.database, "stale-review", "monthly", 4, "test", expiry)
        with self.assertRaisesRegex(InvestmentError, "revision changed"):
            acknowledge_review(self.database, "wrong-revision", "monthly", 3, "test", NOW)
        self.assertEqual(self.database.read_bytes(), original)

        incomplete_path = Path(self.temporary.name) / "incomplete.sqlite3"
        initialize(incomplete_path, self.spec, NOW)
        import_events(incomplete_path, [
            ev("fund-incomplete", "contribution", amount_gbp="100"),
            ev("unmarked-buy", "buy", product_id="equity", quantity="10",
               price_gbp="10", fee_gbp="0"),
        ], NOW)
        incomplete = review_status(incomplete_path, NOW)
        self.assertEqual(incomplete["valuation_status"], "incomplete")
        self.assertIsNone(incomplete["drift_due"])
        with self.assertRaisesRegex(InvestmentError, "complete, fresh"):
            acknowledge_review(incomplete_path, "incomplete-review", "monthly", 2, "test", NOW)
        self.assertEqual(read(incomplete_path, NOW).revision, 2)

    def test_cash_only_confirmation_refreshes_review_without_changing_money(self):
        spec = deepcopy(self.spec)
        spec["products"] = []
        spec["policy"]["targets"] = {"equity": "0", "bond": "0", "cash": "100"}
        initialize(self.database, spec, NOW)
        import_events(self.database, [
            ev("broker-funds", "contribution", amount_gbp="800"),
            ev("bank-funds", "contribution", account="savings", amount_gbp="200"),
        ], NOW)
        review_time = NOW + timedelta(days=400)
        before = snapshot(self.database, review_time)
        self.assertEqual(review_status(self.database, review_time)["valuation_status"], "stale")
        confirmation = {
            "id": "cash-confirmation", "type": "cash_confirmation",
            "timestamp": iso(review_time), "available_at": iso(review_time),
            "evidence": "synthetic contemporaneous cash evidence",
            "balances_gbp": {"broker": "800.00", "savings": "200"},
        }
        import_events(self.database, [confirmation], review_time)
        after = snapshot(self.database, review_time)
        self.assertEqual(after["as_of"], iso(review_time))
        for field in ("accounts", "equity_gbp", "net_contributions_gbp", "gain_gbp",
                      "twr_pct", "max_drawdown_pct"):
            self.assertEqual(after[field], before[field])
        self.assertTrue(review_status(self.database, review_time)["acknowledgement_available"])
        acknowledge_review(self.database, "cash-annual-review", "annual", after["revision"],
                           "synthetic cash review", review_time)
        self.assertFalse(review_status(self.database, review_time)["annual_due"])

    def test_unheld_mark_does_not_refresh_cash_only_valuation(self):
        initialize(self.database, self.spec, NOW)
        import_events(self.database, [
            ev("broker-funds", "contribution", amount_gbp="800"),
            ev("bank-funds", "contribution", account="savings", amount_gbp="200"),
        ], NOW)
        later = NOW + timedelta(days=2)
        import_events(self.database, [
            ev("unheld-mark", "mark", at=later, product_id="equity", price_gbp="10")
        ], later)
        result = snapshot(self.database, later)
        status = review_status(self.database, later)
        self.assertEqual(result["as_of"], iso(NOW))
        self.assertEqual(status["valuation_status"], "stale")
        self.assertFalse(status["acknowledgement_available"])
        self.assertEqual(result["proposal"]["status"], "blocked")
        with self.assertRaisesRegex(InvestmentError, "complete, fresh"):
            acknowledge_review(self.database, "stale-cash-review", "monthly",
                               status["revision"], "synthetic", later)

        confirmation = {
            "id": "cash-check", "type": "cash_confirmation", "timestamp": iso(later),
            "available_at": iso(later), "evidence": "synthetic",
            "balances_gbp": {"broker": "800", "savings": "200"},
        }
        import_events(self.database, [confirmation], later)
        result = snapshot(self.database, later)
        self.assertEqual(result["as_of"], iso(later))
        self.assertEqual(result["proposal"]["status"], "review")
        acknowledge_review(self.database, "confirmed-review", "monthly",
                           result["revision"], "synthetic", later)

    def test_parent_v1_cash_quote_replays_without_refreshing_cash(self):
        initialize(self.database, self.spec, NOW)
        import_events(self.database, [
            ev("broker-funds", "contribution", amount_gbp="800"),
            ev("bank-funds", "contribution", account="savings", amount_gbp="200"),
        ], NOW)
        later = NOW + timedelta(days=2)
        # State hashes were captured from synthetic stores written by the parent version.
        append_legacy_batch(self.database, [
            ev("unheld-mark", "mark", at=later, product_id="equity", price_gbp="10")
        ], "00933985b96547482d3f21d90b904ad18ffa7d47ffa00e4e4561d32da177dd80")
        self.assertEqual(read(self.database, later).revision, 3)
        self.assertEqual(snapshot(self.database, later)["as_of"], iso(NOW))
        self.assertEqual(review_status(self.database, later)["valuation_status"], "stale")

        review = {
            "id": "invalid-cash-review", "type": "review_acknowledgement",
            "timestamp": iso(later), "available_at": iso(later), "evidence": "synthetic",
            "review_kind": "monthly", "reviewed_revision": 3, "valuation_as_of": iso(later),
        }
        append_legacy_batch(
            self.database, [review],
            "b5fa38aa8eacd499ed8aad82aa64d17769954345705cfc9bda9d2fae080c7955")
        before = self.database.read_bytes()
        with self.assertRaisesRegex(InvestmentError, "complete, fresh"):
            read(self.database, later)
        self.assertEqual(self.database.read_bytes(), before)

    def test_cash_confirmation_rejects_missing_discrepant_or_late_evidence(self):
        spec = deepcopy(self.spec)
        spec["products"] = []
        spec["policy"]["targets"] = {"equity": "0", "bond": "0", "cash": "100"}
        initialize(self.database, spec, NOW)
        import_events(self.database, [
            ev("broker-funds", "contribution", amount_gbp="800"),
            ev("bank-funds", "contribution", account="savings", amount_gbp="200"),
        ], NOW)
        later = NOW + timedelta(days=2)
        confirmation = {
            "id": "cash-check", "type": "cash_confirmation", "timestamp": iso(later),
            "available_at": iso(later), "evidence": "synthetic",
            "balances_gbp": {"broker": "800", "savings": "200"},
        }
        cases = [
            {"balances_gbp": {"broker": "800"}},
            {"balances_gbp": {"broker": "800", "savings": "199.99"}},
            {"balances_gbp": {"broker": "800", "savings": "NaN"}},
            {"balances_gbp": {"broker": "800", "savings": "-1"}},
            {"balances_gbp": {"broker": "800", "savings": 200}},
            {"available_at": iso(later + timedelta(seconds=1))},
            {"account_id": "broker"},
        ]
        original = self.database.read_bytes()
        for change in cases:
            with self.subTest(change=change), self.assertRaises(InvestmentError):
                import_events(self.database, [dict(confirmation, **change)], later + timedelta(seconds=1))
            self.assertEqual(self.database.read_bytes(), original)
        import_events(self.database, [confirmation], later)
        self.assertEqual(import_events(self.database, [confirmation], later), 3)
        conflicting = dict(confirmation, balances_gbp={"broker": "800", "savings": "201"})
        with self.assertRaisesRegex(InvestmentError, "Conflicting duplicate"):
            import_events(self.database, [conflicting], later)
        ledger = replay(spec, [
            ev("broker-funds", "contribution", amount_gbp="800"),
            ev("bank-funds", "contribution", account="savings", amount_gbp="200"),
            confirmation,
        ], later)
        confirmation["balances_gbp"]["savings"] = "201"
        with self.assertRaisesRegex(InvestmentError, "Conflicting duplicate"):
            ledger.apply(confirmation, later)

        held_path = Path(self.temporary.name) / "held.sqlite3"
        initialize(held_path, self.spec, NOW)
        import_events(held_path, demo_events(NOW), NOW)
        held_confirmation = dict(confirmation, balances_gbp={"broker": "0", "savings": "200"})
        with self.assertRaisesRegex(InvestmentError, "cash-only"):
            import_events(held_path, [held_confirmation], later)
        self.assertEqual(read(held_path, later).revision, 4)

    def test_cash_confirmation_preserves_exact_recorded_fill_precision(self):
        spec = deepcopy(self.spec)
        spec["products"][0]["quantity_increment"] = "0.00000001"
        initialize(self.database, spec, NOW)
        import_events(self.database, [
            ev("fund-small", "contribution", amount_gbp="1"),
            ev("tiny-buy", "buy", product_id="equity", quantity="0.00000001",
               price_gbp="0.00000001", fee_gbp="0"),
            ev("tiny-sell", "sell", product_id="equity", quantity="0.00000001",
               price_gbp="0.00000002", fee_gbp="0"),
        ], NOW)
        self.assertEqual(snapshot(self.database, NOW)["accounts"][0]["cash_gbp"],
                         "1.0000000000000001")
        later = NOW + timedelta(days=2)
        import_events(self.database, [{
            "id": "precise-confirmation", "type": "cash_confirmation",
            "timestamp": iso(later), "available_at": iso(later), "evidence": "synthetic",
            "balances_gbp": {"broker": "1.0000000000000001", "savings": "0"},
        }], later)
        result = snapshot(self.database, later)
        self.assertEqual(result["accounts"][0]["cash_gbp"], "1.0000000000000001")
        self.assertEqual(result["as_of"], iso(later))

    def test_failed_cash_confirmation_commit_does_not_refresh_valuation(self):
        spec = deepcopy(self.spec)
        spec["products"] = []
        spec["policy"]["targets"] = {"equity": "0", "bond": "0", "cash": "100"}
        initialize(self.database, spec, NOW)
        import_events(self.database, [
            ev("fund", "contribution", amount_gbp="100"),
        ], NOW)
        later = NOW + timedelta(days=2)
        confirmation = {
            "id": "cash-check", "type": "cash_confirmation", "timestamp": iso(later),
            "available_at": iso(later), "evidence": "synthetic",
            "balances_gbp": {"broker": "100", "savings": "0"},
        }
        actual_digest = store.state_digest

        def fail_commit_record(ledger):
            if ledger.revision == 2:
                raise sqlite3.OperationalError("Injected commit record failure")
            return actual_digest(ledger)

        with patch("investment.store.state_digest", side_effect=fail_commit_record):
            with self.assertRaises(InvestmentError):
                import_events(self.database, [confirmation], later)
        self.assertEqual(snapshot(self.database, later)["as_of"], iso(NOW))
        self.assertEqual(read(self.database, later).revision, 1)
        self.assertEqual(review_status(self.database, later)["valuation_status"], "stale")
        import_events(self.database, [confirmation], later)
        self.assertEqual(snapshot(self.database, later)["as_of"], iso(later))

    def test_review_and_cash_confirmation_cannot_use_late_prior_evidence(self):
        initialize(self.database, self.spec, NOW)
        known_later = NOW + timedelta(hours=3)
        current = NOW + timedelta(hours=4)
        funding = ev("delayed-funding", "contribution", amount_gbp="100")
        funding["available_at"] = iso(known_later)
        import_events(self.database, [funding], current)
        premature = NOW + timedelta(hours=1)
        confirmation = {
            "id": "early-cash", "type": "cash_confirmation", "timestamp": iso(premature),
            "available_at": iso(premature), "evidence": "synthetic",
            "balances_gbp": {"broker": "100", "savings": "0"},
        }
        review = {
            "id": "early-review", "type": "review_acknowledgement",
            "timestamp": iso(premature), "available_at": iso(premature), "evidence": "synthetic",
            "review_kind": "monthly", "reviewed_revision": 1, "valuation_as_of": iso(NOW),
        }
        for value in (confirmation, review):
            with self.subTest(kind=value["type"]), self.assertRaises(InvestmentError):
                import_events(self.database, [value], current)
            self.assertEqual(read(self.database, current).revision, 1)
        confirmation.update(id="current-cash", timestamp=iso(current), available_at=iso(current))
        import_events(self.database, [confirmation], current)
        acknowledge_review(self.database, "current-review", "monthly", 2, "synthetic", current)
        self.assertEqual(review_status(self.database, current)["revision"], 3)

    def test_review_retry_is_idempotent_and_preserves_accounting_clocks(self):
        self.init_demo()
        reviewed = NOW + timedelta(hours=1)
        before = snapshot(self.database, reviewed)
        receipt = acknowledge_review(self.database, "manual-review", "monthly", 4,
                                     "synthetic review evidence", reviewed)
        after = snapshot(self.database, reviewed)
        for field in ("as_of", "equity_gbp", "gain_gbp", "twr_pct", "max_drawdown_pct"):
            self.assertEqual(after[field], before[field])
        self.assertEqual(receipt["valuation_as_of"], iso(NOW))
        retry = acknowledge_review(self.database, "manual-review", "monthly", 4,
                                   "synthetic review evidence", reviewed + timedelta(hours=1))
        self.assertEqual(retry, receipt)
        self.assertEqual(read(self.database, reviewed + timedelta(hours=1)).revision, 5)
        with self.assertRaisesRegex(InvestmentError, "Conflicting duplicate"):
            acknowledge_review(self.database, "manual-review", "annual", 4,
                               "synthetic review evidence", reviewed)
        with self.assertRaisesRegex(InvestmentError, "timestamp ordered"):
            import_events(self.database, [
                ev("backdated", "contribution", at=NOW + timedelta(minutes=30), amount_gbp="1")
            ], reviewed)
        self.assertEqual(read(self.database, reviewed).revision, 5)
        later = NOW + timedelta(hours=2)
        financial_events = [
            ev("later-mark", "mark", at=later, product_id="equity", price_gbp="12.5"),
            ev("later-flow", "contribution", at=later, amount_gbp="600"),
        ]
        import_events(self.database, financial_events, later)
        with_review = snapshot(self.database, later)
        without_review = report(replay(self.spec, demo_events(NOW) + financial_events, later), later)
        for field in ("as_of", "equity_gbp", "gain_gbp", "twr_pct", "max_drawdown_pct"):
            self.assertEqual(with_review[field], without_review[field])

    def test_concurrent_identical_review_requests_return_one_receipt(self):
        self.init_demo()
        first_has_lock = Event()
        second_attempted = Event()
        worker = local()
        actual_read = store._read
        actual_connect = store._connect

        def held_read(connection, now):
            if worker.first:
                first_has_lock.set()
                if not second_attempted.wait(timeout=5):
                    raise AssertionError("Second acknowledgement did not attempt the writer lock")
            return actual_read(connection, now)

        def traced_connect(database, readonly):
            connection = actual_connect(database, readonly)
            if not readonly and not worker.first:
                connection.set_trace_callback(
                    lambda statement: second_attempted.set()
                    if statement == "BEGIN IMMEDIATE" else None)
            return connection

        def acknowledge(first, at):
            worker.first = first
            if not first and not first_has_lock.wait(timeout=5):
                raise AssertionError("First acknowledgement did not acquire the writer lock")
            return acknowledge_review(self.database, "same-review", "monthly", 4,
                                      "synthetic concurrent review", at)

        first_time = NOW + timedelta(hours=1)
        with patch("investment.store._read", side_effect=held_read), \
                patch("investment.store._connect", side_effect=traced_connect), \
                ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(acknowledge, True, first_time)
            second = pool.submit(acknowledge, False, first_time + timedelta(seconds=1))
            receipts = [first.result(timeout=15), second.result(timeout=15)]
        self.assertTrue(second_attempted.is_set())
        self.assertEqual(receipts[0], receipts[1])
        self.assertEqual(read(self.database, first_time + timedelta(seconds=1)).revision, 5)

    def test_concurrent_cli_retries_use_the_committed_review_clock(self):
        self.init_demo()
        started = Barrier(2)
        later_finished = Event()
        worker = local()
        actual_clock = store.clock
        earlier = NOW + timedelta(hours=1)
        later = earlier + timedelta(seconds=1)
        retry = later + timedelta(seconds=1)

        def cli_clock(value=None):
            if value is not None:
                return actual_clock(value)
            worker.clock_calls += 1
            if worker.clock_calls == 1:
                started.wait(timeout=10)
                if worker.earlier and not later_finished.wait(timeout=10):
                    raise AssertionError("Concurrent acknowledgement did not finish")
                return earlier if worker.earlier else later
            return retry

        def store_clock(value=None):
            if value is not None:
                return actual_clock(value)
            return retry if worker.earlier else later

        def run(first):
            worker.earlier = first
            worker.clock_calls = 0
            output, errors = io.StringIO(), io.StringIO()
            arguments = ["acknowledge-review", "--database", str(self.database), "--kind",
                         "monthly", "--reviewed-revision", "4", "--id", "same-cli-review",
                         "--evidence", "synthetic concurrent review"]
            try:
                result = main(arguments, stdout=output, stderr=errors)
                return result, output.getvalue(), errors.getvalue()
            finally:
                if not first:
                    later_finished.set()

        with patch("investment.__main__.clock", side_effect=cli_clock), \
                patch("investment.store.clock", side_effect=store_clock), \
                ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(run, True)
            second = pool.submit(run, False)
            results = [first.result(timeout=15), second.result(timeout=15)]
        for result, _, errors in results:
            self.assertEqual(result, 0, errors)
        self.assertEqual(json.loads(results[0][1])["acknowledgement"],
                         json.loads(results[1][1])["acknowledgement"])
        self.assertEqual(read(self.database, retry).revision, 5)

    def test_cli_receipt_survives_a_later_commit_before_status_read(self):
        spec = deepcopy(self.spec)
        spec["products"] = []
        spec["policy"]["targets"] = {"equity": "0", "bond": "0", "cash": "100"}
        initialize(self.database, spec, NOW)
        import_events(self.database, [ev("fund", "contribution", amount_gbp="100")], NOW)
        reviewed = NOW + timedelta(hours=1)
        later = reviewed + timedelta(seconds=1)
        current = later + timedelta(seconds=1)
        confirmation = {
            "id": "concurrent-cash", "type": "cash_confirmation", "timestamp": iso(later),
            "available_at": iso(later), "evidence": "synthetic",
            "balances_gbp": {"broker": "100", "savings": "0"},
        }
        actual_clock = store.clock
        actual_connect = store._connect
        inserted = False

        def inject_later_commit(database, readonly):
            nonlocal inserted
            if readonly and not inserted:
                inserted = True
                with patch("investment.store._connect", side_effect=actual_connect):
                    import_events(database, [confirmation], later)
            return actual_connect(database, readonly)

        def store_clock(value=None):
            if value is not None:
                return actual_clock(value)
            return current if inserted else reviewed

        def cli_clock(value=None):
            return reviewed if value is None else actual_clock(value)

        def service_clock(value=None):
            return current if value is None else actual_clock(value)

        arguments = ["acknowledge-review", "--database", str(self.database), "--kind",
                     "monthly", "--reviewed-revision", "1", "--id", "review",
                     "--evidence", "synthetic"]
        output, errors = io.StringIO(), io.StringIO()
        with patch("investment.__main__.clock", side_effect=cli_clock), \
                patch("investment.service.clock", side_effect=service_clock), \
                patch("investment.store.clock", side_effect=store_clock), \
                patch("investment.store._connect", side_effect=inject_later_commit):
            code = main(arguments, stdout=output, stderr=errors)
        self.assertTrue(inserted)
        self.assertEqual(code, 0, errors.getvalue())
        result = json.loads(output.getvalue())
        self.assertEqual(result["acknowledgement"]["reviewed_revision"], 1)
        self.assertEqual(result["review_status"]["revision"], 3)
        self.assertEqual(read(self.database, current).revision, 3)

    def test_cli_reports_committed_receipt_when_status_read_fails(self):
        self.init_demo()
        arguments = ["acknowledge-review", "--database", str(self.database), "--kind",
                     "monthly", "--reviewed-revision", "4", "--id", "review",
                     "--evidence", "synthetic"]
        output, errors = io.StringIO(), io.StringIO()
        with patch("investment.__main__.review_status",
                   side_effect=InvestmentError("Synthetic status read failure")):
            code = main(arguments, stdout=output, stderr=errors, now=NOW)
        self.assertEqual(code, 2)
        result = json.loads(output.getvalue())
        self.assertEqual(result["acknowledgement"]["reviewed_revision"], 4)
        self.assertIsNone(result["review_status"])
        self.assertIn("Synthetic status read failure", result["review_status_error"])
        self.assertIn("recorded", errors.getvalue())
        self.assertEqual(read(self.database, NOW).revision, 5)

    def test_review_status_cli_includes_commit_before_its_snapshot(self):
        spec = deepcopy(self.spec)
        spec["products"] = []
        spec["policy"]["targets"] = {"equity": "0", "bond": "0", "cash": "100"}
        initialize(self.database, spec, NOW)
        import_events(self.database, [ev("fund", "contribution", amount_gbp="100")], NOW)
        before = NOW + timedelta(hours=1)
        later = before + timedelta(seconds=1)
        confirmation = {
            "id": "concurrent-cash", "type": "cash_confirmation", "timestamp": iso(later),
            "available_at": iso(later), "evidence": "synthetic",
            "balances_gbp": {"broker": "100", "savings": "0"},
        }
        actual_clock = store.clock
        actual_connect = store._connect
        inserted = False

        def inject_later_commit(database, readonly):
            nonlocal inserted
            if readonly and not inserted:
                inserted = True
                with patch("investment.store._connect", side_effect=actual_connect):
                    import_events(database, [confirmation], later)
            return actual_connect(database, readonly)

        def store_clock(value=None):
            if value is not None:
                return actual_clock(value)
            return later if inserted else before

        output, errors = io.StringIO(), io.StringIO()
        with patch("investment.__main__.clock", return_value=before), \
                patch("investment.store.clock", side_effect=store_clock), \
                patch("investment.store._connect", side_effect=inject_later_commit):
            code = main(["review-status", "--database", str(self.database)],
                        stdout=output, stderr=errors)
        self.assertTrue(inserted)
        self.assertEqual(code, 0, errors.getvalue())
        self.assertEqual(json.loads(output.getvalue())["revision"], 2)

    def test_imported_review_cannot_bypass_revision_or_valuation_checks(self):
        self.init_demo()
        review = {
            "id": "manual-review", "type": "review_acknowledgement",
            "timestamp": iso(NOW), "available_at": iso(NOW), "evidence": "synthetic",
            "review_kind": "monthly", "reviewed_revision": 4, "valuation_as_of": iso(NOW),
        }
        for change in ({"reviewed_revision": 3}, {"reviewed_revision": True},
                       {"valuation_as_of": iso(NOW - timedelta(seconds=1))},
                       {"available_at": iso(NOW + timedelta(seconds=1))},
                       {"review_kind": "drift"}, {"account_id": "broker"}):
            with self.subTest(change=change), self.assertRaises(InvestmentError):
                import_events(self.database, [dict(review, **change)], NOW)
            self.assertEqual(read(self.database, NOW).revision, 4)

    def test_review_must_reference_a_previously_committed_revision(self):
        self.init_demo()
        later = NOW + timedelta(hours=1)
        mark = ev("new-mark", "mark", at=later, product_id="equity", price_gbp="11")
        review = {
            "id": "batch-review", "type": "review_acknowledgement",
            "timestamp": iso(later), "available_at": iso(later), "evidence": "synthetic",
            "review_kind": "annual", "reviewed_revision": 5, "valuation_as_of": iso(later),
        }
        with self.assertRaisesRegex(InvestmentError, "committed"):
            import_events(self.database, [mark, review], later)
        self.assertEqual(read(self.database, later).revision, 4)
        import_events(self.database, [mark], later)
        import_events(self.database, [review], later)
        self.assertEqual(read(self.database, later).revision, 6)
        self.assertEqual(review_status(self.database, later)["last_annual_review_at"], iso(later))

    def test_parent_v1_review_snapshot_remains_readable(self):
        self.init_demo()
        later = NOW + timedelta(hours=1)
        review = {
            "id": "valid-review", "type": "review_acknowledgement",
            "timestamp": iso(later), "available_at": iso(later), "evidence": "synthetic",
            "review_kind": "monthly", "reviewed_revision": 4, "valuation_as_of": iso(NOW),
        }
        append_legacy_batch(
            self.database, [review],
            "0467843a04cc39608068bd2b1dd3e100c7b870ba4da7d7e40a4185c002dd3481")
        self.assertEqual(read(self.database, later).revision, 5)
        self.assertEqual(review_status(self.database, later)["last_monthly_review_at"], iso(later))

    def test_parent_v1_uncommitted_review_is_refused_without_mutation(self):
        self.init_demo()
        later = NOW + timedelta(days=2)
        mark = ev("new-mark", "mark", at=later, product_id="equity", price_gbp="11")
        review = {
            "id": "batch-review", "type": "review_acknowledgement",
            "timestamp": iso(later), "available_at": iso(later), "evidence": "synthetic",
            "review_kind": "annual", "reviewed_revision": 5, "valuation_as_of": iso(later),
        }
        append_legacy_batch(
            self.database, [mark, review],
            "09526beca2593cabb099804bdd82d6574e0b706efd50f1ea5d82cb24c64f2e40")
        before = self.database.read_bytes()
        with self.assertRaisesRegex(InvestmentError, "committed revision"):
            read(self.database, later)
        self.assertEqual(self.database.read_bytes(), before)

    def test_review_anniversary_for_leap_day_is_february_28(self):
        leap_day = datetime(2024, 2, 29, 12, tzinfo=timezone.utc)
        initialize(self.database, demo_profile(leap_day), leap_day)
        import_events(self.database, demo_events(leap_day), leap_day)
        acknowledge_review(self.database, "leap-review", "annual", 4, "synthetic", leap_day)
        anniversary = datetime(2025, 2, 28, 12, tzinfo=timezone.utc)
        self.assertFalse(review_status(self.database, anniversary - timedelta(microseconds=1))
                         ["annual_due"])
        self.assertTrue(review_status(self.database, anniversary)["annual_due"])

    def test_review_revision_race_and_failed_commit_do_not_record_acknowledgement(self):
        self.init_demo()
        review_time = NOW + timedelta(hours=1)
        actual_import = store.import_events
        actual_connect = store._connect
        inserted = False

        def concurrent_connect(database, readonly):
            nonlocal inserted
            if not readonly and not inserted:
                inserted = True
                with patch("investment.store._connect", side_effect=actual_connect):
                    actual_import(database, [
                        ev("concurrent", "contribution", at=review_time, amount_gbp="1")
                    ], review_time)
            return actual_connect(database, readonly)

        with patch("investment.store._connect", side_effect=concurrent_connect):
            with self.assertRaisesRegex(InvestmentError, "revision changed"):
                acknowledge_review(self.database, "raced-review", "monthly", 4, "test", review_time)
        self.assertTrue(inserted)
        ledger = read(self.database, review_time)
        self.assertEqual(ledger.revision, 5)
        self.assertNotIn("raced-review", ledger.ids)

        import_events(self.database, [
            ev("fresh-after-race", "mark", at=review_time, product_id="equity", price_gbp="10")
        ], review_time)
        actual_connect = store._connect

        def deny_commit(database, readonly):
            connection = actual_connect(database, readonly)
            if not readonly:
                def authorizer(action, arg1, arg2, database_name, trigger):
                    if action == sqlite3.SQLITE_TRANSACTION and arg1 == "COMMIT":
                        return sqlite3.SQLITE_DENY
                    return sqlite3.SQLITE_OK
                connection.set_authorizer(authorizer)
            return connection

        with patch("investment.store._connect", side_effect=deny_commit):
            with self.assertRaises(InvestmentError):
                acknowledge_review(self.database, "commit-review", "monthly", 6, "test", review_time)
        self.assertEqual(read(self.database, review_time).revision, 6)
        self.assertIsNone(review_status(self.database, review_time)["last_monthly_review_at"])
        acknowledge_review(self.database, "commit-review", "monthly", 6, "test", review_time)
        self.assertEqual(read(self.database, review_time).revision, 7)

    def test_review_cli_uses_explicit_revision_and_retries_after_output_failure(self):
        self.init_demo()
        status_output = io.StringIO()
        self.assertEqual(main(["review-status", "--database", str(self.database)],
                              stdout=status_output, now=NOW), 0)
        self.assertEqual(json.loads(status_output.getvalue())["revision"], 4)
        arguments = ["acknowledge-review", "--database", str(self.database), "--kind", "monthly",
                     "--reviewed-revision", "4", "--id", "cli-review",
                     "--evidence", "synthetic manual review"]
        broken = io.StringIO()
        with patch.object(broken, "write", side_effect=OSError("Injected output failure")):
            with self.assertRaises(OSError):
                main(arguments, stdout=broken, now=NOW)
        self.assertEqual(read(self.database, NOW).revision, 5)
        output = io.StringIO()
        self.assertEqual(main(arguments, stdout=output, now=NOW + timedelta(minutes=1)), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["acknowledgement"]["timestamp"], iso(NOW))
        self.assertEqual(result["review_status"]["revision"], 5)
        self.assertFalse(result["review_status"]["monthly_due"])

    def test_sell_rebalance_waits_for_recorded_proceeds_before_buying(self):
        spec = deepcopy(self.spec)
        spec["policy"]["targets"] = {"equity": "60", "bond": "20", "cash": "20"}
        spec["products"].append(dict(spec["products"][0], id="bond", sleeve="bond"))
        values = demo_events(NOW) + [
            ev("bond-reference", "mark", product_id="bond", price_gbp="10")]
        proposal = report(replay(spec, values, NOW), NOW)["proposal"]
        self.assertEqual(proposal["status"], "review")
        self.assertEqual(proposal["orders"], [{
            "account_id": "broker", "product_id": "equity", "side": "sell",
            "quantity": "20", "estimated_notional_gbp": "200",
        }])
        self.assertFalse(proposal["execution_enabled"])
        self.assertIn("recorded", proposal["reason"].lower())

        values.append(ev("actual-sale", "sell", product_id="equity",
                         quantity="20", price_gbp="10", fee_gbp="0"))
        after = report(replay(spec, values, NOW), NOW)["proposal"]
        self.assertEqual(after["orders"], [{
            "account_id": "broker", "product_id": "bond", "side": "buy",
            "quantity": "20", "estimated_notional_gbp": "200",
        }])

    def test_sell_rebalance_requires_review_and_respects_increment_and_minimum(self):
        later = NOW + timedelta(days=31)
        values = demo_events(NOW) + [
            ev("fresh", "mark", at=later, product_id="equity", price_gbp="11")]
        monthly = report(replay(self.spec, values, later), later)["proposal"]
        self.assertEqual(monthly["orders"], [])

        annual = NOW.replace(year=2027)
        values[-1] = ev("annual", "mark", at=annual, product_id="equity", price_gbp="11")
        proposal = report(replay(self.spec, values, annual), annual)["proposal"]
        self.assertEqual(proposal["orders"][0]["side"], "sell")
        self.assertEqual(proposal["orders"][0]["quantity"], "1")
        self.assertEqual(proposal["orders"][0]["estimated_notional_gbp"], "11")
        expensive_minimum = deepcopy(self.spec)
        expensive_minimum["products"][0]["minimum_notional_gbp"] = "12"
        self.assertEqual(report(replay(expensive_minimum, values, annual), annual)
                         ["proposal"]["orders"], [])

    def test_sell_rebalance_conserves_excess_across_accounts_and_blocks_incomplete_marks(self):
        spec = deepcopy(self.spec)
        spec["accounts"].append(dict(spec["accounts"][0], id="broker-2",
                                     binding="synthetic-broker-2"))
        spec["policy"]["targets"] = {"equity": "75", "bond": "0", "cash": "25"}
        values = [
            ev("fund-a", "contribution", amount_gbp="30"),
            ev("fund-b", "contribution", account="broker-2", amount_gbp="770"),
            ev("fund-savings", "contribution", account="savings", amount_gbp="200"),
            ev("mark-a", "mark", product_id="equity", price_gbp="10"),
            ev("mark-b", "mark", account="broker-2", product_id="equity", price_gbp="10"),
            ev("buy-a", "buy", product_id="equity", quantity="3", price_gbp="10", fee_gbp="0"),
            ev("buy-b", "buy", account="broker-2", product_id="equity", quantity="77",
               price_gbp="10", fee_gbp="0"),
        ]
        result = report(replay(spec, values, NOW), NOW)
        self.assertEqual(result["proposal"]["orders"], [
            {"account_id": "broker", "product_id": "equity", "side": "sell",
             "quantity": "3", "estimated_notional_gbp": "30"},
            {"account_id": "broker-2", "product_id": "equity", "side": "sell",
             "quantity": "2", "estimated_notional_gbp": "20"},
        ])
        later = NOW + timedelta(hours=1)
        values.append(ev("only-one-fresh", "mark", at=later, product_id="equity",
                         price_gbp="10"))
        blocked = report(replay(spec, values, later), later)["proposal"]
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(blocked["orders"], [])
        self.assertIsNone(blocked["expires_at"])

    def test_sell_rebalance_with_partial_buy_reference_uses_only_recorded_cash(self):
        spec = deepcopy(self.spec)
        spec["policy"]["targets"] = {"equity": "60", "bond": "20", "cash": "20"}
        spec["products"].extend([
            dict(spec["products"][0], id="unmarked-bond", sleeve="bond"),
            dict(spec["products"][0], id="bond", sleeve="bond"),
        ])
        values = demo_events(NOW) + [
            ev("extra-cash", "contribution", amount_gbp="100"),
            ev("bond-reference", "mark", product_id="bond", price_gbp="10"),
        ]
        proposal = report(replay(spec, values, NOW), NOW)["proposal"]
        self.assertEqual(proposal["orders"], [
            {"account_id": "broker", "product_id": "bond", "side": "buy",
             "quantity": "8", "estimated_notional_gbp": "80"},
            {"account_id": "broker", "product_id": "equity", "side": "sell",
             "quantity": "14", "estimated_notional_gbp": "140"},
        ])
        self.assertIn("partial-reference", proposal["reason"])
        self.assertIn("proposed sale proceeds do not fund purchases", proposal["reason"])

    def test_sell_rebalance_remains_blocked_when_funded_target_product_is_missing(self):
        spec = deepcopy(self.spec)
        spec["policy"]["targets"] = {"equity": "60", "bond": "20", "cash": "20"}
        values = demo_events(NOW) + [
            ev("extra-cash", "contribution", amount_gbp="100")]
        proposal = report(replay(spec, values, NOW), NOW)["proposal"]
        self.assertEqual(proposal["status"], "blocked")
        self.assertIn("Missing product", proposal["reason"])
        self.assertEqual(proposal["orders"], [])
        self.assertIsNone(proposal["expires_at"])

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
