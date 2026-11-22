"""Synthetic-only tests for the eToro portfolio projection."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import unittest
from unittest.mock import Mock, patch

from investment import etoro
from investment.etoro_portfolio import collect_portfolio, normalize_portfolio


NOW = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)


def instrument(instrument_id, *, units="2", exposure="20", leverage="1"):
    return {
        "instrumentId": instrument_id,
        "assetCurrency": "USD",
        "netContracts": Decimal(units),
        "netUnits": Decimal(units),
        "netCurrentExposureAssetCurrency": Decimal(exposure),
        "netCurrentExposureAccountCurrency": Decimal(exposure),
        "avgLeverage": Decimal(leverage),
    }


def aggregate():
    return {
        "cid": 12345,
        "timestamp": "2026-11-19T11:59:30.000Z",
        "accountCurrency": "USD",
        "accountTotals": {
            "accountAvailableCash": 100,
            "accountFrozenCash": 5,
            "accountCurrentPnl": -2,
            "accountTotalValue": 120,
            "accountTotalUsedMargin": 22,
            "accountBalance": 105,
        },
        "instrumentAggregates": [instrument(7)],
        "mirrors": [{
            "mirrorId": 88,
            "mirrorAvailableCash": 10,
            "instrumentAggregates": [instrument(7, units="-1", exposure="-10", leverage="2")],
            "mirrorTotals": {"mirrorLiquidationValue": 11},
        }],
    }


def balance_document():
    return {"balances": [{
        "accountId": "12345", "accountType": "Trading",
        "balance": 105, "currency": "USD", "displayBalance": 82.25,
        "displayCurrency": "GBP", "exchangeRate": 0.78,
    }]}


def closed_trade(order_id=201):
    return {
        "netProfit": Decimal("-1.25"),
        "closeRate": Decimal("12.5"),
        "closeTimestamp": "2026-11-18T15:04:05Z",
        "positionId": 101,
        "instrumentId": 7,
        "isBuy": True,
        "leverage": 1,
        "openRate": Decimal("10.5"),
        "openTimestamp": "2026-11-10T09:30:00+00:00",
        "orderId": order_id,
        "investment": Decimal("50"),
        "initialInvestment": Decimal("50"),
        "fees": Decimal("0.25"),
        "units": Decimal("4"),
    }


def full_closed_trade(order_id=201):
    trade = closed_trade(order_id)
    trade.update({
        "socialTradeId": 301,
        "parentPositionId": 102,
        "stopLossRate": Decimal("9.5"),
        "takeProfitRate": None,
        "trailingStopLoss": False,
    })
    return trade


def encoded(value):
    return json.dumps(
        value, separators=(",", ":"),
        default=lambda number: int(number) if number == number.to_integral_value() else float(number),
    ).encode("utf-8")


class PortfolioProjectionTests(unittest.TestCase):
    def test_normalizes_manual_and_copy_exposures_without_double_counting(self):
        result = normalize_portfolio(encoded(aggregate()), "real")
        self.assertEqual(result["account_id"], 12345)
        self.assertEqual(result["source_as_of"], "2026-11-19T11:59:30.000000Z")
        self.assertEqual(result["account_currency"], "USD")
        self.assertEqual(result["account_totals"]["accountFrozenCash"], "5")
        self.assertEqual(result["direct_positions"][0]["instrument_id"], 7)
        self.assertEqual(result["mirrors"][0]["positions"][0]["netUnits"], "-1")
        self.assertEqual(result["mirrors"][0]["positions"][0]["avgLeverage"], "2")
        self.assertEqual(result["coverage"]["instrument_type"], "not_provided")
        self.assertEqual(result["coverage"]["pending_orders"], "reserved_cash_total_only")
        self.assertEqual(set(result), {
            "schema_version", "source", "environment", "account_id", "source_as_of",
            "account_currency", "account_totals", "direct_positions", "mirrors", "coverage",
        })

    def test_rejects_missing_total_clock_duplicate_or_incomplete_rows(self):
        cases = []
        missing = aggregate()
        del missing["timestamp"]
        cases.append(missing)
        missing = aggregate()
        del missing["accountTotals"]["accountFrozenCash"]
        cases.append(missing)
        duplicate = aggregate()
        duplicate["instrumentAggregates"].append(instrument(7))
        cases.append(duplicate)
        incomplete = aggregate()
        del incomplete["instrumentAggregates"][0]["netUnits"]
        cases.append(incomplete)
        duplicate_mirror = aggregate()
        duplicate_mirror["mirrors"].append(duplicate_mirror["mirrors"][0])
        cases.append(duplicate_mirror)
        for value in cases:
            with self.subTest(value=value), self.assertRaises(etoro.EToroError):
                normalize_portfolio(encoded(value), "real")

    def test_rejects_unknown_environment_and_unsafe_numeric_values(self):
        with self.assertRaisesRegex(etoro.EToroError, "^environment_invalid$"):
            normalize_portfolio(encoded(aggregate()), "production")
        value = aggregate()
        value["accountTotals"]["accountBalance"] = float("nan")
        with self.assertRaises(etoro.EToroError):
            normalize_portfolio(encoded(value), "demo")

    def test_collect_uses_only_fixed_reads_and_keeps_gbp_separate(self):
        balance = {
            "balances": [{
                "accountId": "12345", "accountType": "Trading",
                "balance": 105, "currency": "USD", "displayBalance": 82.25,
                "displayCurrency": "GBP", "exchangeRate": 0.783333,
            }],
        }
        transport = Mock(side_effect=[encoded(aggregate()), encoded(balance)])
        credentials = {"x-api-key": "synthetic-public", "x-user-key": "synthetic-user"}
        result = collect_portfolio(
            transport, lambda: credentials, "real", now=lambda: NOW)

        self.assertEqual(
            [call.args[0] for call in transport.call_args_list],
            [etoro.AGGREGATE_PORTFOLIO_PATHS["real"],
             "/api/v1/balances/trading/12345?displayCurrency=GBP&expand=equityDetails"])
        self.assertEqual(result["gbp_balance"]["amount_gbp"], "82.25")
        self.assertEqual(result["gbp_balance"]["source_balance"], "105")
        self.assertIsNone(result["gbp_balance"]["source_as_of"])
        self.assertEqual(result["gbp_balance"]["received_at"], "2026-11-19T12:00:00.000000Z")
        self.assertEqual(
            result["gbp_balance"]["meaning"],
            "provider_total_account_value_not_spendable_cash")
        self.assertEqual(result["account_totals"]["accountBalance"], "105")
        self.assertEqual(result["closed_trade_history"]["trades"], [])
        for call in transport.call_args_list:
            self.assertTrue(etoro._headers_valid(call.args[1]))
        self.assertEqual(transport.call_count, 2)

    def test_manual_history_uses_bounded_fixed_get_pages_and_keeps_unknown_currency(self):
        first_page = [full_closed_trade(index) for index in range(1, 1001)]
        transport = Mock(side_effect=[
            encoded(aggregate()), encoded(balance_document()), encoded(first_page), encoded([]),
        ])
        result = collect_portfolio(
            transport, lambda: {
                "x-api-key": "synthetic-public", "x-user-key": "synthetic-user",
            }, "real", now=lambda: NOW, include_closed_history=True)
        history = result["closed_trade_history"]
        self.assertEqual(history["status"], "available")
        self.assertEqual(history["from_date"], "2025-11-20")
        self.assertEqual(history["through_date"], "2026-11-19")
        self.assertEqual(history["received_at"], "2026-11-19T12:00:00.000000Z")
        self.assertEqual(len(history["trades"]), 1000)
        self.assertEqual(history["trades"][0]["netProfit"], "-1.25")
        self.assertEqual(history["trades"][0]["socialTradeId"], 301)
        self.assertEqual(history["trades"][0]["stopLossRate"], "9.5")
        self.assertIsNone(history["trades"][0]["takeProfitRate"])
        self.assertFalse(history["trades"][0]["trailingStopLoss"])
        self.assertEqual(history["trades"][0]["closeTimestamp"], "2026-11-18T15:04:05.000000Z")
        self.assertEqual(history["trades"][0]["openTimestamp"], "2026-11-10T09:30:00.000000Z")
        self.assertNotIn("currency", history["trades"][0])
        self.assertEqual(
            [call.args[0] for call in transport.call_args_list[2:]],
            [
                etoro.CLOSED_TRADE_HISTORY_PATHS["real"]
                + "?minDate=2025-11-20&page=1&pageSize=1000",
                etoro.CLOSED_TRADE_HISTORY_PATHS["real"]
                + "?minDate=2025-11-20&page=2&pageSize=1000",
            ])
        self.assertEqual(
            len({call.args[1]["x-request-id"] for call in transport.call_args_list}), 4)

    def test_history_over_limit_is_unavailable_without_returning_partial_rows(self):
        page = [closed_trade(index) for index in range(1, 1001)]
        transport = Mock(side_effect=[
            encoded(aggregate()), encoded(balance_document()),
            encoded(page), encoded([closed_trade(2000)]),
        ])
        result = collect_portfolio(
            transport, lambda: {
                "x-api-key": "synthetic-public", "x-user-key": "synthetic-user",
            }, "real", now=lambda: NOW, include_closed_history=True)
        history = result["closed_trade_history"]
        self.assertEqual(history["status"], "unavailable")
        self.assertEqual(history["error_code"], "history_limit_exceeded")
        self.assertEqual(history["trades"], [])
        self.assertEqual(transport.call_count, 4)

    def test_history_failure_does_not_hide_a_valid_current_snapshot(self):
        transport = Mock(side_effect=[
            encoded(aggregate()), encoded(balance_document()),
            etoro.EToroError("permission_refused"),
        ])
        result = collect_portfolio(
            transport, lambda: {
                "x-api-key": "synthetic-public", "x-user-key": "synthetic-user",
            }, "real", now=lambda: NOW, include_closed_history=True)
        self.assertEqual(result["account_id"], 12345)
        self.assertEqual(result["closed_trade_history"]["status"], "unavailable")
        self.assertEqual(result["closed_trade_history"]["error_code"], "permission_refused")

    def test_history_schema_mismatch_is_explicitly_unavailable(self):
        bad_trade = closed_trade()
        bad_trade["isBuy"] = "buy"
        transport = Mock(side_effect=[
            encoded(aggregate()), encoded(balance_document()), encoded([bad_trade]),
        ])
        result = collect_portfolio(
            transport, lambda: {
                "x-api-key": "synthetic-public", "x-user-key": "synthetic-user",
            }, "real", now=lambda: NOW, include_closed_history=True)
        self.assertEqual(result["closed_trade_history"]["status"], "unavailable")
        self.assertEqual(result["closed_trade_history"]["error_code"], "schema_invalid")
        self.assertEqual(result["closed_trade_history"]["trades"], [])

    def test_history_rows_outside_the_displayed_window_are_unavailable(self):
        for close_timestamp in (
                "2025-11-19T23:59:59Z", "2026-11-20T00:00:00Z"):
            with self.subTest(close_timestamp=close_timestamp):
                trade = closed_trade()
                trade["closeTimestamp"] = close_timestamp
                trade["openTimestamp"] = "2024-01-01T00:00:00Z"
                transport = Mock(side_effect=[
                    encoded(aggregate()), encoded(balance_document()), encoded([trade]),
                ])
                result = collect_portfolio(
                    transport, lambda: {
                        "x-api-key": "synthetic-public", "x-user-key": "synthetic-user",
                    }, "real", now=lambda: NOW, include_closed_history=True)
                self.assertEqual(result["account_id"], 12345)
                self.assertEqual(result["closed_trade_history"]["status"], "unavailable")
                self.assertEqual(
                    result["closed_trade_history"]["error_code"], "history_date_out_of_range")
                self.assertEqual(result["closed_trade_history"]["trades"], [])

    def test_history_rejects_future_timestamp_on_the_current_date(self):
        trade = closed_trade()
        trade["closeTimestamp"] = "2026-11-19T12:00:01Z"
        transport = Mock(side_effect=[
            encoded(aggregate()), encoded(balance_document()), encoded([trade]),
        ])
        result = collect_portfolio(
            transport, lambda: {
                "x-api-key": "synthetic-public", "x-user-key": "synthetic-user",
            }, "real", now=lambda: NOW, include_closed_history=True)
        self.assertEqual(result["closed_trade_history"]["status"], "unavailable")
        self.assertEqual(
            result["closed_trade_history"]["error_code"], "history_date_out_of_range")
        self.assertEqual(result["closed_trade_history"]["trades"], [])

    def test_history_rejects_opening_after_close_but_accepts_older_openings(self):
        older_trade = closed_trade()
        older_trade["openTimestamp"] = "2020-01-01T00:00:00Z"
        transport = Mock(side_effect=[
            encoded(aggregate()), encoded(balance_document()), encoded([older_trade]),
        ])
        result = collect_portfolio(
            transport, lambda: {
                "x-api-key": "synthetic-public", "x-user-key": "synthetic-user",
            }, "real", now=lambda: NOW, include_closed_history=True)
        history = result["closed_trade_history"]
        self.assertEqual(history["status"], "available")
        self.assertEqual(history["trades"][0]["openTimestamp"], "2020-01-01T00:00:00.000000Z")

        impossible_trade = closed_trade()
        impossible_trade["openTimestamp"] = "2026-11-18T15:04:06Z"
        transport = Mock(side_effect=[
            encoded(aggregate()), encoded(balance_document()), encoded([impossible_trade]),
        ])
        result = collect_portfolio(
            transport, lambda: {
                "x-api-key": "synthetic-public", "x-user-key": "synthetic-user",
            }, "real", now=lambda: NOW, include_closed_history=True)
        history = result["closed_trade_history"]
        self.assertEqual(history["status"], "unavailable")
        self.assertEqual(history["error_code"], "schema_invalid")
        self.assertEqual(history["trades"], [])

    def test_history_accepts_trade_received_across_midnight(self):
        current = [datetime(2026, 11, 19, 23, 59, 59, tzinfo=timezone.utc)]
        trade = closed_trade()
        trade["closeTimestamp"] = "2026-11-20T00:00:01Z"

        def transport(path, headers):
            if "aggregate-portfolio" in path:
                return encoded(aggregate())
            if "/balances/" in path:
                return encoded(balance_document())
            current[0] = datetime(2026, 11, 20, 0, 0, 2, tzinfo=timezone.utc)
            return encoded([trade])

        result = collect_portfolio(
            transport,
            lambda: {"x-api-key": "synthetic-public", "x-user-key": "synthetic-user"},
            "real", now=lambda: current[0], include_closed_history=True)
        history = result["closed_trade_history"]
        self.assertEqual(history["status"], "available")
        self.assertEqual(history["through_date"], "2026-11-20")
        self.assertEqual(len(history["trades"]), 1)

    def test_history_uses_receipt_clock_before_parsing_response(self):
        current = [NOW]
        trade = closed_trade()
        trade["closeTimestamp"] = "2026-11-19T12:00:05Z"
        transport = Mock(side_effect=[
            encoded(aggregate()), encoded(balance_document()), encoded([trade]),
        ])
        from investment.etoro_portfolio import normalize_closed_trade_page

        def delayed_parse(payload):
            current[0] += timedelta(seconds=10)
            return normalize_closed_trade_page(payload)

        with patch("investment.etoro_portfolio.normalize_closed_trade_page",
                   side_effect=delayed_parse):
            result = collect_portfolio(
                transport,
                lambda: {"x-api-key": "synthetic-public", "x-user-key": "synthetic-user"},
                "real", now=lambda: current[0], include_closed_history=True)
        history = result["closed_trade_history"]
        self.assertEqual(history["status"], "unavailable")
        self.assertEqual(history["error_code"], "history_date_out_of_range")
        self.assertEqual(history["received_at"], "2026-11-19T12:00:00.000000Z")
        self.assertEqual(history["trades"], [])

    def test_history_rejects_sub_microsecond_timestamps(self):
        from investment.etoro_portfolio import normalize_closed_trade_page

        for field in ("closeTimestamp", "openTimestamp"):
            for value in ("2026-11-19T12:00:00.1234567Z",
                          "2026-11-19T12:00:00,0000009Z",
                          "2026-11-19T12:00:00.123456+0000",
                          "2026-11-19T12:00:00+00"):
                trade = closed_trade()
                trade[field] = value
                with self.subTest(field=field, value=value), self.assertRaises(etoro.EToroError):
                    normalize_closed_trade_page(encoded([trade]))

    def test_history_with_backward_receipt_clock_has_no_receipt_timestamp(self):
        current = [NOW]

        def transport(path, headers):
            if "aggregate-portfolio" in path:
                return encoded(aggregate())
            if "/balances/" in path:
                return encoded(balance_document())
            current[0] = NOW - timedelta(seconds=1)
            return encoded([closed_trade()])

        result = collect_portfolio(
            transport,
            lambda: {"x-api-key": "synthetic-public", "x-user-key": "synthetic-user"},
            "real", now=lambda: current[0], include_closed_history=True)
        history = result["closed_trade_history"]
        self.assertEqual(history["status"], "unavailable")
        self.assertEqual(history["error_code"], "clock_unavailable")
        self.assertIsNone(history["received_at"])

    def test_gbp_balance_identity_or_currency_mismatch_is_unavailable(self):
        balance = {"balances": [{
            "accountId": "99999", "accountType": "Trading",
            "balance": 105, "currency": "USD", "displayBalance": 82.25,
            "displayCurrency": "GBP", "exchangeRate": 0.78,
        }]}
        transport = Mock(side_effect=[encoded(aggregate()), encoded(balance)])
        result = collect_portfolio(
            transport,
            lambda: {"x-api-key": "synthetic-public", "x-user-key": "synthetic-user"},
            "real", now=lambda: NOW)
        self.assertEqual(result["gbp_balance"]["status"], "unavailable")
        self.assertEqual(result["gbp_balance"]["error_code"], "schema_invalid")

    def test_balance_receipt_time_is_after_the_balance_request(self):
        current = [NOW]
        def transport(path, headers):
            if "aggregate-portfolio" in path:
                return encoded(aggregate())
            current[0] += timedelta(seconds=5)
            return encoded({"balances": [{
                "accountId": "12345", "accountType": "Trading", "balance": 105,
                "currency": "USD", "displayBalance": 82.25, "displayCurrency": "GBP",
                "exchangeRate": 0.78,
            }]})
        result = collect_portfolio(
            transport, lambda: {"x-api-key": "synthetic-public", "x-user-key": "synthetic-user"},
            "real", now=lambda: current[0])
        self.assertEqual(result["gbp_balance"]["received_at"], "2026-11-19T12:00:05.000000Z")


if __name__ == "__main__":
    unittest.main()
