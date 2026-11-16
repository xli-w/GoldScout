"""Synthetic, temporary-store checks for evidenced investment inception."""

from copy import deepcopy
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from investment import InvestmentError
from investment import store
from investment.core import iso
from investment.service import demo_profile, proposal_export, review_status, snapshot
from investment.store import import_events, initialize, read


NOW = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)


def profile():
    return {
        "schema_version": 2,
        "portfolio_id": "opening-test",
        "currency": "GBP",
        "created_at": iso(NOW),
        "accounts": [
            {"id": "bank", "name": "Savings", "kind": "deposit",
             "binding": "bank-test", "currency": "GBP"},
            {"id": "broker", "name": "Broker", "kind": "broker",
             "binding": "broker-test", "currency": "GBP"},
        ],
        "products": [
            {"id": "equity", "name": "Equity fund", "sleeve": "equity",
             "currency": "GBP", "quantity_increment": "1", "minimum_notional_gbp": "1"},
            {"id": "bond", "name": "Bond fund", "sleeve": "bond",
             "currency": "GBP", "quantity_increment": "1", "minimum_notional_gbp": "1"},
        ],
        "policy": None,
    }


def opening():
    return {
        "id": "opening", "type": "opening", "timestamp": iso(NOW),
        "available_at": iso(NOW), "evidence": "synthetic opening evidence",
        "balances_gbp": {"bank": "1000", "broker": "100"},
        "holdings": [
            {"account_id": "broker", "product_id": "equity", "quantity": "50",
             "price_gbp": "10"},
            {"account_id": "broker", "product_id": "bond", "quantity": "20",
             "price_gbp": "20"},
        ],
    }


def savings_terms():
    return {
        "annual_rate_pct": "4.25", "rate_basis": "AER",
        "effective_from": "2026-01-01",
        "observed_at": iso(NOW - timedelta(hours=1)),
        "available_at": iso(NOW), "source": "synthetic savings statement",
        "maturity_date": "2027-01-01", "notice_days": 90,
    }


def cash_observation(timestamp=NOW, available_at=None, account="bank", balance="999",
                     identifier="bank-statement"):
    available_at = timestamp if available_at is None else available_at
    return {
        "id": identifier, "type": "cash_observation", "timestamp": iso(timestamp),
        "available_at": iso(available_at), "evidence": "synthetic statement evidence",
        "source": "synthetic bank statement", "account_id": account,
        "balance_gbp": balance,
    }


def savings_terms_event(timestamp, available_at=None, account="bank",
                        identifier="bank-terms-update"):
    available_at = timestamp if available_at is None else available_at
    return {
        "id": identifier, "type": "savings_terms", "timestamp": iso(timestamp),
        "available_at": iso(available_at), "evidence": "synthetic rate notice",
        "account_id": account, "annual_rate_pct": "3.75", "rate_basis": "AER",
        "effective_from": "2026-02-01", "source": "synthetic bank notice",
        "maturity_date": "2027-02-01", "notice_days": 30,
    }


def event(identifier, kind, timestamp, account, **values):
    return dict(id=identifier, type=kind, timestamp=iso(timestamp), available_at=iso(timestamp),
                account_id=account, currency="GBP", evidence="synthetic event evidence",
                **values)


class InvestmentOpeningTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.database = Path(self.temporary.name) / "investment.sqlite3"

    def test_existing_holdings_have_an_evidenced_baseline_not_a_fabricated_purchase(self):
        initialize(self.database, profile(), NOW)
        import_events(self.database, [opening()], NOW)
        result = snapshot(self.database, NOW)
        self.assertEqual(result["equity_gbp"], "2000")
        self.assertEqual(result["net_contributions_gbp"], "0")
        self.assertEqual(result["since_baseline_gain_gbp"], "0")
        self.assertEqual(result["lifetime_gain_gbp"], None)
        self.assertEqual(result["baseline_as_of"], iso(NOW))
        self.assertEqual(result["proposal"]["status"], "blocked")
        self.assertEqual(result["valuation_history"], [{
            "timestamp": iso(NOW), "source_available_at": iso(NOW), "as_of": iso(NOW),
            "complete": True, "equity_gbp": "2000", "capital_basis_gbp": "2000",
            "net_contributions_gbp": "0", "gain_since_baseline_gbp": "0",
            "twr_pct": "0", "max_drawdown_pct": "0", "gap_reason": None,
        }])
        self.assertFalse(result["proposal"]["execution_enabled"])
        self.assertEqual(read(self.database, NOW).cash["bank"], 1000)

    def test_growth_history_separates_recorded_capital_from_gain_and_preserves_gaps(self):
        initialize(self.database, profile(), NOW)
        initial = opening()
        initial["holdings"][0]["price_gbp"] = None
        import_events(self.database, [initial], NOW)
        first = snapshot(self.database, NOW)["valuation_history"][0]
        self.assertFalse(first["complete"])
        self.assertIsNone(first["equity_gbp"])
        self.assertEqual(first["gap_reason"],
                         "A complete contemporaneous valuation is unavailable.")

        baseline_at = NOW + timedelta(days=1)
        import_events(self.database, [
            event("baseline-equity", "mark", baseline_at, "broker",
                  product_id="equity", price_gbp="10"),
            event("baseline-bond", "mark", baseline_at, "broker",
                  product_id="bond", price_gbp="20"),
        ], baseline_at)
        growth_at = NOW + timedelta(days=2)
        import_events(self.database, [
            event("growth-equity", "mark", growth_at, "broker",
                  product_id="equity", price_gbp="11"),
            event("growth-bond", "mark", growth_at, "broker",
                  product_id="bond", price_gbp="20"),
            event("growth-contribution", "contribution", growth_at,
                  "bank", amount_gbp="100"),
        ], growth_at)

        history = snapshot(self.database, growth_at)["valuation_history"]
        self.assertEqual(len(history), 3)
        self.assertFalse(history[0]["complete"])
        self.assertEqual(history[1]["equity_gbp"], "2000")
        self.assertEqual(history[1]["gain_since_baseline_gbp"], "0")
        self.assertEqual(history[2]["equity_gbp"], "2150")
        self.assertEqual(history[2]["capital_basis_gbp"], "2100")
        self.assertEqual(history[2]["net_contributions_gbp"], "100")
        self.assertEqual(history[2]["gain_since_baseline_gbp"], "50")
        self.assertEqual(history[2]["twr_pct"], "2.5")
        self.assertEqual(history[2]["source_available_at"], iso(growth_at))

    def test_complete_since_baseline_return_excludes_new_contribution(self):
        initialize(self.database, profile(), NOW)
        import_events(self.database, [opening()], NOW)
        later = NOW + timedelta(days=1)
        import_events(self.database, [
            event("equity-mark", "mark", later, "broker", product_id="equity", price_gbp="11"),
            event("bond-mark", "mark", later, "broker", product_id="bond", price_gbp="20"),
            event("deposit", "contribution", later, "bank", amount_gbp="100"),
        ], later)
        result = snapshot(self.database, later)
        self.assertEqual(result["equity_gbp"], "2150")
        self.assertEqual(result["net_contributions_gbp"], "100")
        self.assertEqual(result["since_baseline_gain_gbp"], "50")
        self.assertEqual(result["twr_pct"], "2.5")

    def test_same_clock_later_interest_cannot_rewrite_the_opening_baseline(self):
        initialize(self.database, profile(), NOW)
        import_events(self.database, [opening()], NOW)
        import_events(self.database, [
            event("interest", "interest", NOW, "bank", amount_gbp="100"),
        ], NOW)
        result = snapshot(self.database, NOW)
        self.assertEqual(result["equity_gbp"], "2100")
        self.assertEqual(result["since_baseline_gain_gbp"], "100")
        self.assertEqual(result["twr_pct"], "5")
        self.assertEqual(read(self.database, NOW).baseline_value, 2000)

    def test_missing_opening_mark_cannot_bypass_complete_valuation_bound(self):
        initialize(self.database, profile(), NOW)
        initial = opening()
        initial["holdings"][0]["quantity"] = "999999999999999"
        initial["holdings"][0]["price_gbp"] = None
        import_events(self.database, [initial], NOW)
        with self.assertRaisesRegex(InvestmentError, "bound"):
            import_events(self.database, [
                event("huge-mark", "mark", NOW, "broker", product_id="equity",
                      price_gbp="999999999999999"),
            ], NOW)
        self.assertEqual(read(self.database, NOW).revision, 1)

    def test_partial_marks_across_commits_anchor_before_same_clock_income(self):
        initialize(self.database, profile(), NOW)
        initial = opening()
        for holding in initial["holdings"]:
            holding["price_gbp"] = None
        import_events(self.database, [initial], NOW)
        later = NOW + timedelta(days=1)
        import_events(self.database, [
            event("equity-mark", "mark", later, "broker", product_id="equity", price_gbp="10"),
        ], later)
        self.assertIsNone(snapshot(self.database, later)["baseline_as_of"])
        import_events(self.database, [
            event("bond-mark", "mark", later, "broker", product_id="bond", price_gbp="20"),
        ], later)
        import_events(self.database, [
            event("interest", "interest", later, "bank", amount_gbp="100"),
        ], later)
        result = snapshot(self.database, later)
        self.assertEqual(result["baseline_as_of"], iso(later))
        self.assertEqual(result["since_baseline_gain_gbp"], "100")
        self.assertEqual(result["twr_pct"], "5")

    def test_same_clock_income_before_baseline_mark_is_refused(self):
        initialize(self.database, profile(), NOW)
        initial = opening()
        for holding in initial["holdings"]:
            holding["price_gbp"] = None
        import_events(self.database, [initial], NOW)
        later = NOW + timedelta(days=1)
        with self.assertRaisesRegex(InvestmentError, "income must follow"):
            import_events(self.database, [
                event("interest-first", "interest", later, "bank", amount_gbp="100"),
                event("equity-mark", "mark", later, "broker",
                      product_id="equity", price_gbp="10"),
                event("bond-mark", "mark", later, "broker",
                      product_id="bond", price_gbp="20"),
            ], later)
        self.assertEqual(read(self.database, later).revision, 1)

    def test_same_clock_flow_before_required_marks_is_refused(self):
        initialize(self.database, profile(), NOW)
        import_events(self.database, [opening()], NOW)
        later = NOW + timedelta(days=1)
        with self.assertRaisesRegex(InvestmentError, "marks for existing holdings"):
            import_events(self.database, [
                event("deposit-first", "contribution", later,
                      "bank", amount_gbp="100"),
                event("equity-mark", "mark", later, "broker",
                      product_id="equity", price_gbp="11"),
                event("bond-mark", "mark", later, "broker",
                      product_id="bond", price_gbp="20"),
            ], later)
        result = snapshot(self.database, later)
        self.assertEqual(result["revision"], 1)
        self.assertEqual(result["net_contributions_gbp"], "0")
        self.assertEqual(result["baseline_as_of"], iso(NOW))

    def test_unpriced_post_baseline_flow_keeps_performance_unavailable(self):
        initialize(self.database, profile(), NOW)
        import_events(self.database, [opening()], NOW)
        later = NOW + timedelta(days=1)
        import_events(self.database, [
            event("unpriced-flow", "contribution", later,
                  "bank", amount_gbp="100"),
        ], later)
        complete_at = NOW + timedelta(days=2)
        import_events(self.database, [
            event("equity-mark", "mark", complete_at, "broker",
                  product_id="equity", price_gbp="11"),
            event("bond-mark", "mark", complete_at, "broker",
                  product_id="bond", price_gbp="20"),
        ], complete_at)
        result = snapshot(self.database, complete_at)
        self.assertTrue(result["complete"])
        self.assertIsNotNone(result["since_baseline_gain_gbp"])
        self.assertIsNone(result["twr_pct"])
        self.assertIsNone(result["max_drawdown_pct"])

    def test_late_mark_requires_a_new_contemporaneous_baseline(self):
        initialize(self.database, profile(), NOW)
        initial = opening()
        initial["holdings"][0]["price_gbp"] = None
        import_events(self.database, [initial], NOW)
        later = NOW + timedelta(days=1)
        delayed = event("delayed", "mark", NOW, "broker", product_id="equity", price_gbp="10")
        delayed["available_at"] = iso(later)
        import_events(self.database, [delayed], later)
        self.assertIsNone(snapshot(self.database, later)["baseline_as_of"])
        next_day = later + timedelta(days=1)
        import_events(self.database, [
            event("equity-mark", "mark", next_day, "broker", product_id="equity", price_gbp="10"),
            event("bond-mark", "mark", next_day, "broker", product_id="bond", price_gbp="20"),
        ], next_day)
        self.assertEqual(snapshot(self.database, next_day)["baseline_as_of"], iso(next_day))

    def test_later_complete_valuation_cannot_exceed_accounting_bound(self):
        initialize(self.database, profile(), NOW)
        initial = opening()
        initial["holdings"][0]["quantity"] = "2000000000"
        initial["holdings"][0]["price_gbp"] = "1"
        import_events(self.database, [initial], NOW)
        later = NOW + timedelta(days=1)
        with self.assertRaisesRegex(InvestmentError, "bound"):
            import_events(self.database, [
                event("huge-mark", "mark", later, "broker", product_id="equity",
                      price_gbp="999999999999999"),
                event("bond-mark", "mark", later, "broker", product_id="bond", price_gbp="20"),
            ], later)
        self.assertEqual(read(self.database, later).revision, 1)

    def test_invalid_intermediate_commit_is_refused(self):
        initialize(self.database, profile(), NOW)
        import_events(self.database, [
            opening(), event("interest", "interest", NOW, "bank", amount_gbp="100"),
        ], NOW)
        with closing(sqlite3.connect(self.database)) as connection:
            with connection:
                connection.execute("INSERT INTO commits VALUES (1, ?)", ("invalid",))
        with self.assertRaisesRegex(InvestmentError, "replay verification"):
            read(self.database, NOW)

    def test_complete_redemption_and_refunding_preserve_since_baseline_return(self):
        initialize(self.database, profile(), NOW)
        initial = opening()
        initial["holdings"] = []
        initial["balances_gbp"]["broker"] = "0"
        import_events(self.database, [initial], NOW)
        income, withdrawal, refund = (NOW + timedelta(days=n) for n in (1, 2, 3))
        import_events(self.database, [
            event("interest", "interest", income, "bank", amount_gbp="100"),
            event("withdrawal", "withdrawal", withdrawal, "bank", amount_gbp="1100"),
        ], withdrawal)
        self.assertEqual(snapshot(self.database, withdrawal)["equity_gbp"], "0")
        import_events(self.database, [
            event("refund", "contribution", refund, "bank", amount_gbp="1000"),
        ], refund)
        result = snapshot(self.database, refund)
        self.assertEqual(result["since_baseline_gain_gbp"], "100")
        self.assertEqual(result["twr_pct"], "10")
        self.assertEqual(result["max_drawdown_pct"], "0")

    def test_failed_sqlite_commit_does_not_publish_opening_and_can_retry(self):
        initialize(self.database, profile(), NOW)
        connect = store._connect
        denied = []

        def deny_commit(database, readonly):
            connection = connect(database, readonly)

            def authorize(action, arg1, arg2, database_name, trigger):
                if action == sqlite3.SQLITE_TRANSACTION and arg1 == "COMMIT":
                    denied.append(True)
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK

            connection.set_authorizer(authorize)
            return connection

        with patch("investment.store._connect", side_effect=deny_commit):
            with self.assertRaises(InvestmentError):
                import_events(self.database, [opening()], NOW)
        self.assertEqual(denied, [True])
        self.assertEqual(read(self.database, NOW).revision, 0)
        self.assertEqual(import_events(self.database, [opening()], NOW), 1)
        self.assertEqual(snapshot(self.database, NOW)["equity_gbp"], "2000")

    def test_failed_savings_terms_commit_preserves_prior_report_and_can_retry(self):
        initialize(self.database, profile(), NOW)
        import_events(self.database, [opening()], NOW)
        connect = store._connect
        denied = []

        def deny_commit(database, readonly):
            connection = connect(database, readonly)

            def authorize(action, arg1, arg2, database_name, trigger):
                if action == sqlite3.SQLITE_TRANSACTION and arg1 == "COMMIT":
                    denied.append(True)
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK

            connection.set_authorizer(authorize)
            return connection

        observed_at = NOW + timedelta(days=1)
        update = savings_terms_event(observed_at)
        with patch("investment.store._connect", side_effect=deny_commit):
            with self.assertRaises(InvestmentError):
                import_events(self.database, [update], observed_at)
        self.assertEqual(denied, [True])
        self.assertEqual(read(self.database, observed_at).revision, 1)
        bank = next(account for account in snapshot(self.database, observed_at)["accounts"]
                    if account["id"] == "bank")
        self.assertIsNone(bank["terms"])
        self.assertEqual(import_events(self.database, [update], observed_at), 2)
        bank = next(account for account in snapshot(self.database, observed_at)["accounts"]
                    if account["id"] == "bank")
        self.assertEqual(bank["terms"]["annual_rate_pct"], "3.75")

    def test_v2_store_rejects_mismatched_version_and_schema_tampering(self):
        initialize(self.database, profile(), NOW)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("PRAGMA user_version=1")
        with self.assertRaisesRegex(InvestmentError, "version disagree"):
            read(self.database, NOW)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("PRAGMA user_version=2")
            connection.execute("DROP TRIGGER immutable_events_delete")
        with self.assertRaises(InvestmentError):
            read(self.database, NOW)

    def test_missing_opening_mark_does_not_invent_complete_value(self):
        initialize(self.database, profile(), NOW)
        initial = opening()
        initial["holdings"][0]["price_gbp"] = None
        import_events(self.database, [initial], NOW)
        result = snapshot(self.database, NOW)
        self.assertIsNone(result["equity_gbp"])
        self.assertIsNone(result["baseline_as_of"])
        self.assertIsNone(result["twr_pct"])

    def test_duplicate_opening_is_idempotent_but_conflicting_evidence_is_refused(self):
        initialize(self.database, profile(), NOW)
        initial = opening()
        self.assertEqual(import_events(self.database, [initial], NOW), 1)
        self.assertEqual(import_events(self.database, [initial], NOW), 1)
        conflicting = deepcopy(initial)
        conflicting["balances_gbp"]["bank"] = "999"
        with self.assertRaises(InvestmentError):
            import_events(self.database, [conflicting], NOW)
        self.assertEqual(snapshot(self.database, NOW)["equity_gbp"], "2000")

    def test_late_cash_opening_cannot_create_a_retrospective_baseline(self):
        initialize(self.database, profile(), NOW)
        later = NOW + timedelta(days=1)
        initial = opening()
        initial["holdings"] = []
        initial["available_at"] = iso(later)
        import_events(self.database, [initial], later)
        result = snapshot(self.database, later)
        self.assertIsNone(result["equity_gbp"])
        self.assertIsNone(result["baseline_as_of"])
        confirmation = {
            "id": "confirmation", "type": "cash_confirmation", "timestamp": iso(later),
            "available_at": iso(later), "evidence": "synthetic confirmation",
            "balances_gbp": {"bank": "1000", "broker": "100"},
        }
        import_events(self.database, [confirmation], later)
        result = snapshot(self.database, later)
        self.assertEqual(result["equity_gbp"], "1100")
        self.assertEqual(result["baseline_as_of"], iso(later))
        self.assertEqual(result["since_baseline_gain_gbp"], "0")

    def test_unimplemented_policy_commands_fail_explicitly(self):
        initialize(self.database, profile(), NOW)
        import_events(self.database, [opening()], NOW)
        for operation in (review_status, proposal_export):
            with self.assertRaisesRegex(InvestmentError, "policy"):
                operation(self.database, NOW)

    def test_opening_requires_valid_unique_broker_holdings(self):
        initialize(self.database, profile(), NOW)
        for mutation in ("duplicate", "deposit", "quantity", "oversized"):
            initial = opening()
            if mutation == "duplicate":
                initial["holdings"].append(deepcopy(initial["holdings"][0]))
            elif mutation == "deposit":
                initial["holdings"][0]["account_id"] = "bank"
            elif mutation == "quantity":
                initial["holdings"][0]["quantity"] = "1.5"
            else:
                initial["holdings"][0]["quantity"] = "999999999999999"
                initial["holdings"][0]["price_gbp"] = "999999999999999"
            with self.subTest(mutation=mutation), self.assertRaises(InvestmentError):
                import_events(self.database, [initial], NOW)
        self.assertEqual(read(self.database, NOW).revision, 0)

    def test_savings_terms_are_profile_bound_facts_not_valuation_inputs(self):
        spec = profile()
        spec["accounts"][0]["terms"] = savings_terms()
        initialize(self.database, spec, NOW)
        import_events(self.database, [opening()], NOW)
        result = snapshot(self.database, NOW)
        self.assertEqual(result["equity_gbp"], "2000")
        self.assertEqual(result["accounts"][0]["terms"], savings_terms())
        self.assertEqual(read(self.database, NOW).cash["bank"], 1000)

    def test_announced_future_savings_terms_are_displayable_without_accrual(self):
        spec = profile()
        terms = savings_terms()
        terms["effective_from"] = "2026-02-01"
        spec["accounts"][0]["terms"] = terms
        initialize(self.database, spec, NOW)
        import_events(self.database, [opening()], NOW)
        result = snapshot(self.database, NOW)
        self.assertEqual(result["accounts"][0]["terms"]["effective_from"], "2026-02-01")
        self.assertEqual(result["equity_gbp"], "2000")

    def test_savings_terms_update_validation_and_journal_clocks(self):
        initialize(self.database, profile(), NOW)
        import_events(self.database, [opening()], NOW)
        observed_at = NOW + timedelta(days=1)
        available_at = observed_at + timedelta(days=1)
        mutations = ("broker", "basis_without_rate", "bad_date", "future_available", "extra")
        for mutation in mutations:
            update = savings_terms_event(
                observed_at, identifier="invalid-terms-" + mutation)
            if mutation == "broker":
                update["account_id"] = "broker"
            elif mutation == "basis_without_rate":
                update["annual_rate_pct"] = None
            elif mutation == "bad_date":
                update["effective_from"] = "2026-2-1"
            elif mutation == "future_available":
                update["available_at"] = iso(available_at + timedelta(days=1))
            else:
                update["unverified_rate"] = "1"
            with self.subTest(mutation=mutation), self.assertRaises(InvestmentError):
                import_events(self.database, [update], available_at)
            self.assertEqual(read(self.database, available_at).revision, 1)

        update = savings_terms_event(observed_at, available_at)
        import_events(self.database, [update], available_at)
        earlier_transaction = event(
            "before-terms", "interest", NOW + timedelta(hours=12),
            "bank", amount_gbp="1")
        with self.assertRaisesRegex(InvestmentError, "timestamp ordered"):
            import_events(self.database, [earlier_transaction], available_at)

        earlier_confirmation = {
            "id": "before-terms-available", "type": "cash_confirmation",
            "timestamp": iso(observed_at + timedelta(hours=12)),
            "available_at": iso(observed_at + timedelta(hours=12)),
            "evidence": "synthetic confirmation",
            "balances_gbp": {"bank": "1000", "broker": "100"},
        }
        with self.assertRaisesRegex(InvestmentError, "Prior evidence was not available"):
            import_events(self.database, [earlier_confirmation], available_at)

    def test_savings_terms_update_is_version_2_only(self):
        spec = demo_profile(NOW)
        deposit = next(account for account in spec["accounts"]
                       if account["kind"] == "deposit")
        initialize(self.database, spec, NOW)
        update = savings_terms_event(NOW, account=deposit["id"])
        with self.assertRaisesRegex(InvestmentError, "version 2 store"):
            import_events(self.database, [update], NOW)
        self.assertEqual(read(self.database, NOW).revision, 0)

    def test_savings_terms_event_updates_display_only_facts_without_changing_valuation(self):
        spec = profile()
        spec["accounts"][0]["terms"] = savings_terms()
        initialize(self.database, spec, NOW)
        import_events(self.database, [opening()], NOW)
        observed_at = NOW + timedelta(days=1)
        available_at = observed_at + timedelta(hours=1)
        update = savings_terms_event(observed_at, available_at)

        self.assertEqual(import_events(self.database, [update], available_at), 2)
        result = snapshot(self.database, available_at)
        bank = next(account for account in result["accounts"] if account["id"] == "bank")
        self.assertEqual(bank["terms"], {
            "annual_rate_pct": "3.75", "rate_basis": "AER",
            "effective_from": "2026-02-01", "observed_at": iso(observed_at),
            "available_at": iso(available_at), "source": "synthetic bank notice",
            "maturity_date": "2027-02-01", "notice_days": 30,
        })
        self.assertEqual(bank["cash_gbp"], "1000")
        self.assertTrue(result["complete"])
        self.assertEqual(result["equity_gbp"], "2000")
        self.assertEqual(result["as_of"], iso(NOW))
        self.assertEqual(result["baseline_as_of"], iso(NOW))
        self.assertEqual(result["twr_pct"], "0")
        self.assertEqual(import_events(self.database, [update], available_at), 2)
        self.assertEqual(snapshot(self.database, available_at)["revision"], 2)

    def test_cash_observation_reports_difference_without_adjusting_recorded_balance(self):
        initialize(self.database, profile(), NOW)
        import_events(self.database, [opening()], NOW)
        observation = cash_observation()
        import_events(self.database, [observation], NOW)
        import_events(self.database, [
            cash_observation(account="broker", balance="99", identifier="broker-statement"),
        ], NOW)
        import_events(self.database, [observation], NOW)
        result = snapshot(self.database, NOW)
        account = next(item for item in result["accounts"] if item["id"] == "bank")
        self.assertEqual(account["cash_gbp"], "1000")
        self.assertEqual(account["cash_observation"]["balance_gbp"], "999")
        self.assertEqual(account["cash_observation"]["difference_gbp"], "-1")
        self.assertEqual(result["equity_gbp"], "2000")
        self.assertEqual(result["since_baseline_gain_gbp"], "0")
        self.assertTrue(any("no correction was applied" in warning for warning in result["warnings"]))
        self.assertTrue(any("GBP -1" in warning and "synthetic bank statement" in warning
                            for warning in result["warnings"]))
        broker = next(item for item in result["accounts"] if item["id"] == "broker")
        self.assertEqual(broker["cash_observation"]["difference_gbp"], "-1")
        self.assertEqual(read(self.database, NOW).cash["bank"], 1000)

    def test_later_matching_cash_observation_does_not_hide_unresolved_difference(self):
        initialize(self.database, profile(), NOW)
        import_events(self.database, [opening()], NOW)
        mismatch = cash_observation(identifier="bank-mismatch", balance="999")
        import_events(self.database, [mismatch], NOW)
        later = NOW + timedelta(days=1)
        import_events(self.database, [
            cash_observation(later, account="bank", balance="1000",
                             identifier="bank-matching"),
        ], later)

        result = snapshot(self.database, later)
        bank = next(account for account in result["accounts"] if account["id"] == "bank")
        self.assertEqual(bank["cash_observation"]["difference_gbp"], "0")
        self.assertTrue(any("2026-01-01T12:00:00Z" in warning and
                            "no correction was applied" in warning and
                            "later matching observations do not resolve it" in warning
                            for warning in result["warnings"]))

    def test_cash_observation_is_historical_and_does_not_refresh_valuation(self):
        initialize(self.database, profile(), NOW)
        import_events(self.database, [opening()], NOW)
        observed_at = NOW + timedelta(days=1)
        import_events(self.database, [cash_observation(observed_at, balance="950")], observed_at)
        result = snapshot(self.database, observed_at)
        bank = next(account for account in result["accounts"] if account["id"] == "bank")
        self.assertIsNone(result["equity_gbp"])
        self.assertEqual(bank["cash_gbp"], "1000")
        self.assertEqual(bank["cash_observation"]["difference_gbp"], "-50")

    def test_cash_observation_must_follow_other_events_at_its_clock(self):
        initialize(self.database, profile(), NOW)
        import_events(self.database, [opening()], NOW)
        observation = cash_observation()
        later_same_clock = event("same-clock-funding", "contribution", NOW,
                                 "bank", amount_gbp="1")
        with self.assertRaisesRegex(InvestmentError, "final at their timestamp"):
            import_events(self.database, [observation, later_same_clock], NOW)
        self.assertEqual(read(self.database, NOW).revision, 1)

    def test_invalid_cash_observations_are_refused_without_mutation(self):
        initialize(self.database, profile(), NOW)
        import_events(self.database, [opening()], NOW)
        mutations = ("unknown_account", "negative", "too_large", "late_publication", "future", "extra")
        for mutation in mutations:
            observation = cash_observation(identifier="invalid-" + mutation)
            if mutation == "unknown_account":
                observation["account_id"] = "outside"
            elif mutation == "negative":
                observation["balance_gbp"] = "-1"
            elif mutation == "too_large":
                observation["balance_gbp"] = "9999999999999999999999999"
            elif mutation == "late_publication":
                observation["available_at"] = iso(NOW - timedelta(seconds=1))
            elif mutation == "future":
                observation["timestamp"] = iso(NOW + timedelta(seconds=1))
                observation["available_at"] = observation["timestamp"]
            else:
                observation["unchecked_adjustment"] = "1"
            with self.subTest(mutation=mutation), self.assertRaises(InvestmentError):
                import_events(self.database, [observation], NOW)
            self.assertEqual(read(self.database, NOW).revision, 1)

    def test_late_cash_observation_cannot_refresh_an_earlier_valuation(self):
        initialize(self.database, profile(), NOW)
        import_events(self.database, [opening()], NOW)
        available = NOW + timedelta(days=1)
        import_events(self.database, [
            cash_observation(NOW, available, balance="1000"),
        ], available)
        self.assertIsNone(snapshot(self.database, available)["equity_gbp"])
        self.assertEqual(read(self.database, available).revision, 2)

    def test_invalid_savings_terms_fail_profile_initialization(self):
        mutations = ("broker", "bad_date", "late_available", "basis", "rate",
                     "notice", "maturity", "extra")
        for mutation in mutations:
            spec = profile()
            account = spec["accounts"][0]
            account["terms"] = savings_terms()
            if mutation == "broker":
                account["kind"] = "broker"
            elif mutation == "bad_date":
                account["terms"]["effective_from"] = "2026-1-2"
            elif mutation == "late_available":
                account["terms"]["available_at"] = iso(NOW + timedelta(seconds=1))
            elif mutation == "basis":
                account["terms"]["rate_basis"] = None
            elif mutation == "rate":
                account["terms"]["annual_rate_pct"] = "100.01"
            elif mutation == "notice":
                account["terms"]["notice_days"] = True
            elif mutation == "maturity":
                account["terms"]["maturity_date"] = "2025-12-31"
            else:
                account["terms"]["unverified_yield"] = "10"
            with self.subTest(mutation=mutation), self.assertRaises(InvestmentError):
                initialize(self.database, spec, NOW)
            self.database.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
