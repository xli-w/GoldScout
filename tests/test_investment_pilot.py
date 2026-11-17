"""Synthetic Trading 212 pilot recovery mechanics; no broker or real account."""

from copy import deepcopy
from decimal import Inexact, ROUND_DOWN, localcontext
from unittest.mock import patch
import unittest

from investment import InvestmentError
from investment.pilot import simulate_pilot_trace


def intent():
    return {
        "type": "intent", "at": "2026-01-01T10:00:00Z",
        "intent_id": "synthetic-intent", "approval_id": "synthetic-approval",
        "portfolio_id": "synthetic-portfolio", "account_id": "synthetic-account",
        "product_id": "synthetic-product", "role": "core", "currency": "GBP",
        "quantity": "10", "max_spend_gbp": "100", "policy_id": "synthetic-policy",
        "portfolio_revision": 5, "expires_at": "2026-01-01T11:00:00Z",
    }


def attempt():
    return {"type": "attempt", "at": "2026-01-01T10:01:00Z",
            "intent_id": "synthetic-intent", "policy_id": "synthetic-policy",
            "portfolio_revision": 5}


def acknowledgement():
    return {"type": "acknowledgement", "at": "2026-01-01T10:01:01Z",
            "order_id": "synthetic-order", "account_id": "synthetic-account",
            "product_id": "synthetic-product", "side": "buy", "quantity": "10"}


def observation(at, status, filled, order_id="synthetic-order"):
    return {"type": "observation", "at": at, "order_id": order_id,
            "status": status, "filled_quantity": filled}


class PilotTraceTests(unittest.TestCase):
    def test_partial_fill_repeat_cancel_and_restart_conserve_quantity(self):
        events = [intent(), attempt(), acknowledgement(),
                  observation("2026-01-01T10:02:00Z", "partial", "3"),
                  observation("2026-01-01T10:03:00Z", "partial", "3"),
                  {"type": "restart", "at": "2026-01-01T10:04:00Z"},
                  observation("2026-01-01T10:05:00Z", "cancelled", "4")]
        before = deepcopy(events)
        with patch("builtins.open", side_effect=AssertionError("unexpected file access")), \
                patch("sqlite3.connect", side_effect=AssertionError("unexpected database access")):
            result = simulate_pilot_trace(events)
        self.assertEqual(events, before)
        self.assertTrue(result["synthetic_only"])
        self.assertFalse(result["execution_enabled"])
        self.assertEqual(result["submission_attempts"], 1)
        self.assertEqual(result["state"], "cancelled")
        self.assertEqual(result["observed_filled_quantity"], "4")
        self.assertEqual(result["observed_unfilled_quantity"], "6")
        self.assertEqual(result["fill_deltas"], ["3", "1"])
        self.assertEqual(result["funding_check"], "unavailable")
        self.assertEqual(result["spend_cap_check"], "unavailable")
        self.assertFalse(result["ownership_verified"])

    def test_timeout_and_restart_never_retry_or_adopt_unowned_order(self):
        prefix = [intent(), attempt(),
                  {"type": "uncertain", "at": "2026-01-01T10:01:01Z"},
                  {"type": "restart", "at": "2026-01-01T10:02:00Z"}]
        result = simulate_pilot_trace(prefix)
        self.assertEqual(result["state"], "manual_review")
        self.assertEqual(result["submission_attempts"], 1)
        self.assertIsNone(result["observed_filled_quantity"])
        self.assertIsNone(result["observed_unfilled_quantity"])
        for extra in (
                attempt(),
                acknowledgement(),
                observation("2026-01-01T10:03:00Z", "filled", "10")):
            extra["at"] = "2026-01-01T10:03:00Z"
            with self.subTest(extra=extra), self.assertRaises(InvestmentError):
                simulate_pilot_trace(prefix + [extra])

    def test_interrupted_attempt_is_ambiguous_without_response(self):
        result = simulate_pilot_trace([intent(), attempt(),
                                      {"type": "restart", "at": "2026-01-01T10:02:00Z"}])
        self.assertEqual(result["state"], "manual_review")
        self.assertEqual(result["submission_attempts"], 1)

    def test_acknowledgement_must_match_synthetic_buy_scope(self):
        for field, value in (("account_id", "foreign"), ("product_id", "foreign"),
                             ("side", "sell"), ("quantity", "9")):
            ack = acknowledgement()
            ack[field] = value
            with self.subTest(field=field), self.assertRaises(InvestmentError):
                simulate_pilot_trace([intent(), attempt(), ack])

    def test_terminal_duplicate_and_hostile_decimal_context(self):
        events = [intent(), attempt(), acknowledgement(),
                  observation("2026-01-01T10:02:00Z", "filled", "10"),
                  observation("2026-01-01T10:03:00Z", "filled", "10")]
        expected = simulate_pilot_trace(events)
        with localcontext() as context:
            context.prec = 1
            context.rounding = ROUND_DOWN
            context.traps[Inexact] = True
            self.assertEqual(simulate_pilot_trace(events), expected)
        self.assertEqual(expected["fill_deltas"], ["10"])
        with self.assertRaises(InvestmentError):
            simulate_pilot_trace(events + [
                observation("2026-01-01T10:01:00Z", "filled", "10")])

    def test_wrong_order_stale_fill_overfill_and_terminal_conflict_are_refused(self):
        prefix = [intent(), attempt(), acknowledgement(),
                  observation("2026-01-01T10:02:00Z", "partial", "3")]
        for extra in (
                observation("2026-01-01T10:03:00Z", "partial", "4", "foreign"),
                observation("2026-01-01T10:03:00Z", "partial", "2"),
                observation("2026-01-01T10:03:00Z", "filled", "11"),
                observation("2026-01-01T10:03:00Z", "filled", "3")):
            with self.subTest(extra=extra), self.assertRaises(InvestmentError):
                simulate_pilot_trace(prefix + [extra])
        terminal = prefix + [observation("2026-01-01T10:03:00Z", "cancelled", "3")]
        with self.assertRaises(InvestmentError):
            simulate_pilot_trace(terminal + [
                observation("2026-01-01T10:04:00Z", "partial", "4")])

    def test_approval_scope_revision_expiry_and_order_are_required(self):
        for change in (
                lambda e: e[0].update(currency="USD"),
                lambda e: e[0].update(role="unassigned"),
                lambda e: e[0].update(quantity="0"),
                lambda e: e[1].update(intent_id="other"),
                lambda e: e[1].update(policy_id="other"),
                lambda e: e[1].update(portfolio_revision=6),
                lambda e: e[1].update(at="2026-01-01T11:00:00Z")):
            events = [intent(), attempt()]
            change(events)
            with self.subTest(events=events), self.assertRaises(InvestmentError):
                simulate_pilot_trace(events)
        with self.assertRaises(InvestmentError):
            simulate_pilot_trace([attempt()])
