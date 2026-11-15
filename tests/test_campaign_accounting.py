import copy
import json
import unittest
from decimal import Decimal, localcontext

from bot.campaign_data.accounting import (
    CampaignAccountingError,
    account_fills,
)


def fill(event_id, sequence, side, quantity, price, fees="0", **changes):
    event = {
        "event_id": event_id,
        "timestamp": "2025-03-27T09:00:00Z",
        "sequence": sequence,
        "product_id": "synthetic-gbp-product",
        "side": side,
        "quantity": quantity,
        "price_gbp": price,
        "fees_gbp": fees,
        "currency": "GBP",
        "price_unit": "GBP_PER_UNIT",
    }
    event.update(changes)
    return event


class CampaignAccountingTests(unittest.TestCase):
    def test_long_only_buy_sell_events_conserve_cash_quantity_fees_and_notional(self):
        events = [
            fill("buy-1", 0, "buy", "3", "100.25", "0.50"),
            fill("buy-2", 1, "buy", "2", "99.75", "0.25",
                 timestamp="2025-03-28T09:00:00Z"),
            fill("sell-1", 0, "sell", "1.5", "101", "0.10",
                 timestamp="2025-03-31T09:00:00Z"),
        ]
        result = account_fills("1000", events)
        self.assertEqual(result["initial_cash_gbp"], "1000")
        self.assertEqual(result["cash_balance_gbp"], "650.4")
        self.assertEqual(result["positions"], [{"product_id": "synthetic-gbp-product", "quantity": "3.5"}])
        self.assertEqual(result["total_fees_gbp"], "0.85")
        self.assertEqual(result["gross_traded_notional_gbp"], "651.75")
        self.assertEqual([item["cash_balance_gbp"] for item in result["events"]],
                         ["698.75", "499", "650.4"])
        self.assertNotIn("equity_gbp", result)

    def test_one_cash_balance_is_shared_across_products(self):
        events = [
            fill("buy-b", 0, "buy", "5", "10", product_id="product-b"),
            fill("buy-a", 1, "buy", "4", "10", product_id="product-a"),
        ]
        result = account_fills("100", events)
        self.assertEqual(result["cash_balance_gbp"], "10")
        self.assertEqual(result["positions"], [
            {"product_id": "product-a", "quantity": "4"},
            {"product_id": "product-b", "quantity": "5"},
        ])
        self.assertEqual(result["gross_traded_notional_gbp"], "90")

    def test_simultaneous_events_use_explicit_sequence_and_cash_is_checked_per_fill(self):
        events = [
            fill("sell", 1, "sell", "1", "10"),
            fill("buy", 0, "buy", "1", "10"),
        ]
        with self.assertRaisesRegex(CampaignAccountingError, "ordered"):
            account_fills("10", events)
        result = account_fills("10", list(reversed(events)))
        self.assertEqual([event["event_id"] for event in result["events"]], ["buy", "sell"])
        self.assertEqual(result["cash_balance_gbp"], "10")
        self.assertEqual(result["positions"], [])

    def test_initial_cash_is_explicit_positive_decimal_string(self):
        for value in (0, -1, "0", "-1", "1e3", " 1", "NaN", 1.25, True):
            with self.subTest(value=value), self.assertRaises(CampaignAccountingError):
                account_fills(value, [])

    def test_no_insufficient_cash_negative_cash_or_implicit_credit(self):
        for event, initial in (
            (fill("buy", 0, "buy", "2", "5"), "9"),
            (fill("sell", 0, "sell", "1", "5", "6"), "0"),
        ):
            with self.subTest(event=event), self.assertRaisesRegex(CampaignAccountingError, "cash"):
                account_fills(initial, [event])

    def test_no_short_sale_or_sale_beyond_position(self):
        with self.assertRaisesRegex(CampaignAccountingError, "exceeds"):
            account_fills("100", [fill("sell", 0, "sell", "1", "10")])
        events = [fill("buy", 0, "buy", "1", "10"),
                  fill("oversell", 1, "sell", "1.0001", "10")]
        with self.assertRaisesRegex(CampaignAccountingError, "exceeds"):
            account_fills("100", events)

    def test_duplicate_ids_and_duplicate_simultaneous_sequence_are_rejected(self):
        duplicate_id = [fill("same", 0, "buy", "1", "1"),
                        fill("same", 1, "buy", "1", "1", timestamp="2025-03-28T09:00:00Z")]
        with self.assertRaisesRegex(CampaignAccountingError, "event_id"):
            account_fills("10", duplicate_id)
        duplicate_sequence = [fill("one", 0, "buy", "1", "1"),
                              fill("two", 0, "buy", "1", "1")]
        with self.assertRaisesRegex(CampaignAccountingError, "sequence"):
            account_fills("10", duplicate_sequence)

    def test_out_of_order_time_or_sequence_is_not_silently_sorted(self):
        events = [fill("later", 0, "buy", "1", "1", timestamp="2025-03-28T09:00:00Z"),
                  fill("earlier", 0, "buy", "1", "1")]
        with self.assertRaisesRegex(CampaignAccountingError, "ordered"):
            account_fills("10", events)
        events = [fill("second", 1, "buy", "1", "1"),
                  fill("first", 0, "buy", "1", "1")]
        with self.assertRaisesRegex(CampaignAccountingError, "ordered"):
            account_fills("10", events)

    def test_fill_shape_currency_and_units_fail_closed(self):
        baseline = fill("buy", 0, "buy", "1", "1")
        for changes in (
            {"side": "BUY"}, {"side": "short"}, {"currency": "USD"},
            {"price_unit": "GBX_PER_UNIT"}, {"sequence": True},
            {"quantity": "0"}, {"quantity": "-1"}, {"quantity": 1.0},
            {"price_gbp": "0"}, {"price_gbp": "NaN"}, {"fees_gbp": "-0.01"},
            {"product_id": "../unknown"},
        ):
            event = dict(baseline, **changes)
            with self.subTest(changes=changes), self.assertRaises(CampaignAccountingError):
                account_fills("100", [event])

    def test_decimal_arithmetic_is_exact_and_independent_of_context(self):
        events = [fill("buy", 0, "buy", "1.000000000000000000000000000001",
                       "123.456789012345678901234567890123", "0.000000000000000000000000000001")]
        original = copy.deepcopy(events)
        with localcontext() as context:
            context.prec = 5
            result = account_fills("1000", events)
        self.assertEqual(events, original)
        self.assertEqual(result["gross_traded_notional_gbp"],
                         "123.456789012345678901234567890246456789012345678901234567890123")

    def test_large_proceeds_preserve_subpenny_fees_and_small_notional(self):
        quantity = "9" * 64
        small_price = "0." + "0" * 62 + "1"
        large_price = "9" * 64
        fee = "0." + "0" * 62 + "2"
        events = [
            fill("buy-small", 0, "buy", quantity, small_price),
            fill("sell-large", 0, "sell", quantity, large_price, fee,
                 timestamp="2025-03-28T09:00:00Z"),
        ]
        default_context_result = account_fills("10", events)
        with localcontext() as context:
            context.prec = 5
            context.Emax = 2
            context.Emin = -2
            result = account_fills("10", events)
        self.assertEqual(result, default_context_result)
        with localcontext() as context:
            context.prec = 300
            expected_cash = Decimal("10") - Decimal(quantity) * Decimal(small_price)
            expected_cash += Decimal(quantity) * Decimal(large_price) - Decimal(fee)
            expected_notional = Decimal(quantity) * Decimal(small_price)
            expected_notional += Decimal(quantity) * Decimal(large_price)
        self.assertEqual(result["cash_balance_gbp"], format(expected_cash, "f"))
        self.assertEqual(result["gross_traded_notional_gbp"], format(expected_notional, "f"))

    def test_timestamp_fraction_precision_is_causal_and_timezone_aware(self):
        for timestamp in ("2025-03-27T09:00:00", "2025-03-27T09:00:00.0000001Z"):
            with self.subTest(timestamp=timestamp), self.assertRaises(CampaignAccountingError):
                account_fills("10", [fill("buy", 0, "buy", "1", "1", timestamp=timestamp)])

    def test_timezone_offsets_compare_as_the_same_chronological_instant(self):
        events = [
            fill("first", 0, "buy", "1", "1", timestamp="2025-03-27T10:00:00+01:00"),
            fill("second", 1, "buy", "1", "1", timestamp="2025-03-27T09:00:00Z"),
        ]
        result = account_fills("10", events)
        self.assertEqual([item["event_id"] for item in result["events"]], ["first", "second"])
        self.assertEqual([item["timestamp"] for item in result["events"]],
                         ["2025-03-27T09:00:00Z", "2025-03-27T09:00:00Z"])

    def test_no_cash_deposits_withdrawals_marks_actions_or_return_claims(self):
        result = account_fills("100", [])
        self.assertEqual(result["cash_balance_gbp"], "100")
        self.assertEqual(result["positions"], [])
        self.assertFalse(result["supports_cash_accrual"])
        self.assertFalse(result["supports_corporate_actions"])
        self.assertFalse(result["supports_equity_valuation"])
        self.assertFalse(result["supports_performance_claims"])
        for extra in ("cash", "mark", "dividend", "fx"):
            event = fill("event", 0, "buy", "1", "1", **{extra: "10"})
            with self.subTest(extra=extra), self.assertRaises(CampaignAccountingError):
                account_fills("10", [event])

    def test_event_count_and_decimal_text_limits_are_bounded(self):
        too_many = [fill(f"e-{index}", index, "buy", "1", "1",
                         timestamp=f"2025-03-27T09:{index // 60:02d}:{index % 60:02d}Z")
                    for index in range(100001)]
        with self.assertRaisesRegex(CampaignAccountingError, "at most"):
            account_fills("1", too_many)
        with self.assertRaisesRegex(CampaignAccountingError, "digits"):
            account_fills("1", [fill("large", 0, "buy", "1" * 65, "1")])

    def test_ordered_event_report_size_is_bounded_for_large_fixture(self):
        events = [fill(f"event-{index}", index, "buy", "1", "1")
                  for index in range(5000)]
        result = account_fills("5000", events)
        serialized = json.dumps(result, separators=(",", ":"), allow_nan=False)
        self.assertLess(len(serialized), 2_000_000)
        self.assertEqual(len(result["events"]), len(events))


if __name__ == "__main__":
    unittest.main()
