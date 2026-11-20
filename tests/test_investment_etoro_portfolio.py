"""Synthetic-only tests for the eToro portfolio projection."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import unittest
from unittest.mock import Mock

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
        self.assertEqual(result["account_totals"]["accountBalance"], "105")
        for call in transport.call_args_list:
            self.assertTrue(etoro._headers_valid(call.args[1]))
        self.assertEqual(transport.call_count, 2)

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
