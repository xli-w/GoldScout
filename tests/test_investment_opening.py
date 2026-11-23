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
from investment import core, store
from investment.core import iso
from investment.service import snapshot
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


def correction(original, replacement_amount, available_at):
    evidence = "synthetic correction statement"
    replacement = deepcopy(original)
    replacement.update({
        "id": "replacement-" + original["id"],
        "amount_gbp": replacement_amount,
        "available_at": iso(available_at),
        "evidence": evidence,
    })
    return {
        "id": "correction-" + original["id"], "type": "correction",
        "timestamp": original["timestamp"], "available_at": iso(available_at),
        "evidence": evidence, "original_event_id": original["id"],
        "replacement_event": replacement,
    }


def historical_correction(original, replacement_values, correction_at):
    evidence = "synthetic historical correction evidence"
    replacement = deepcopy(original)
    replacement.update(replacement_values)
    replacement.update({
        "id": "replacement-" + original["id"],
        "available_at": iso(correction_at),
        "evidence": evidence,
    })
    return {
        "id": "historical-correction-" + original["id"], "type": "correction",
        "timestamp": iso(correction_at), "available_at": iso(correction_at),
        "evidence": evidence, "original_event_id": original["id"],
        "replacement_event": replacement,
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


def funded_scope(timestamp):
    return {
        "id": "scope-addition", "type": "scope_addition",
        "timestamp": iso(timestamp), "available_at": iso(timestamp),
        "evidence": "synthetic common-clock statements",
        "account": {
            "id": "scope", "name": "Additional broker", "kind": "broker",
            "binding": "scope-test", "currency": "GBP",
        },
        "balances_gbp": {"bank": "1000", "broker": "100", "scope": "10"},
        "holdings": [
            {"account_id": "broker", "product_id": "equity", "quantity": "50",
             "price_gbp": "10"},
            {"account_id": "broker", "product_id": "bond", "quantity": "20",
             "price_gbp": "20"},
            {"account_id": "scope", "product_id": "equity", "quantity": "2",
             "price_gbp": "5"},
        ],
    }


class ConnectionCloseFailure:
    def __init__(self, connection):
        self.connection = connection

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def __enter__(self):
        self.connection.__enter__()
        return self

    def __exit__(self, *args):
        return self.connection.__exit__(*args)

    def close(self):
        self.connection.close()
        raise sqlite3.OperationalError("synthetic post-commit close failure")


class TransactionExitFailure(ConnectionCloseFailure):
    def __exit__(self, *args):
        result = self.connection.__exit__(*args)
        if args[0] is None:
            raise sqlite3.OperationalError("synthetic transaction exit failure")
        return result

    def close(self):
        self.connection.close()


class CommitResultFailure(ConnectionCloseFailure):
    def commit(self):
        self.connection.commit()
        raise sqlite3.OperationalError("synthetic lost commit result")

    def close(self):
        self.connection.close()


class InvestmentOpeningTests(unittest.TestCase):
    def test_existing_opening_checkpoint_hash_is_unchanged(self):
        from investment.core import replay

        # Immutable checkpoint projection for this synthetic, already released fixture.
        self.assertEqual(
            store.state_digest(replay(profile(), [opening()], NOW)),
            "f3a01d4a83ed25e640e39c2fd73dfc0ad6fbb41b8f3de7308440530b6e492adc")

    def test_multi_commit_v2_checkpoints_match_fixed_legacy_hashes(self):
        funded_at = NOW + timedelta(seconds=1)
        observation_at = NOW + timedelta(seconds=2)
        events = [
            opening(),
            event("funding", "contribution", funded_at, "bank", amount_gbp="100"),
            cash_observation(observation_at, balance="1099"),
        ]
        initialize(self.database, profile(), NOW)
        # Fixed schema-v2 checkpoints pin the projection across separate appends.
        expected = [
            "f647c4108d9ee8b952091bdc59daa5f871e10e5fda9a51ccfd0bc1331c633c99",
            "f3a01d4a83ed25e640e39c2fd73dfc0ad6fbb41b8f3de7308440530b6e492adc",
            "546c415d71dc826db4bf5b2c953c55f03736ef7d118137c6fd009ac780f05c86",
            "79c09b9e737bb0a8ea8bc0890fe78b1b4553aae5bc7ff55e7013498125c816da",
        ]
        for offset, value in enumerate(events):
            import_events(self.database, [value], NOW + timedelta(seconds=offset))
        with closing(sqlite3.connect(self.database)) as connection:
            checkpoints = connection.execute(
                "SELECT revision,state_hash FROM commits ORDER BY revision").fetchall()
        self.assertEqual(checkpoints, list(enumerate(expected)))
        self.assertEqual(read(self.database, observation_at).revision, 3)

    def test_retired_review_event_in_v2_journal_is_refused_without_writes(self):
        initialize(self.database, profile(), NOW)
        import_events(self.database, [opening()], NOW)
        legacy_review = {
            "id": "review", "type": "review_acknowledgement",
            "timestamp": iso(NOW), "available_at": iso(NOW),
            "evidence": "synthetic policy review", "review_kind": "monthly",
            "reviewed_revision": 1, "valuation_as_of": iso(NOW),
        }
        payload = store.canonical(legacy_review)
        with closing(sqlite3.connect(self.database)) as connection, connection:
            previous = connection.execute(
                "SELECT hash FROM events WHERE revision=1").fetchone()[0]
            connection.execute(
                "INSERT INTO events VALUES (?,?,?,?,?)",
                (2, legacy_review["id"], payload, previous, store.digest(previous + payload)),
            )
            connection.execute("INSERT INTO commits VALUES (?,?)", (2, "0" * 64))
        before = self.database.read_bytes()
        with self.assertRaisesRegex(InvestmentError, "Review acknowledgements require"):
            read(self.database, NOW)
        self.assertEqual(self.database.read_bytes(), before)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.database = Path(self.temporary.name) / "investment.sqlite3"

    def test_required_evidence_clocks_never_default_or_lose_precision(self):
        fixed_clock = core.clock

        def clock_without_implicit_time(value=None):
            return NOW if value is None else fixed_clock(value)

        invalid_clocks = (
            None,
            "2026-01-01T12:00:00.0000001Z",
            "2026-01-01T12:00:00+00:00:00.0000001",
        )
        with patch("investment.core.clock", side_effect=clock_without_implicit_time):
            for index, value in enumerate(invalid_clocks):
                with self.subTest(field="created_at", value=value):
                    spec = profile()
                    spec["created_at"] = value
                    database = Path(self.temporary.name) / f"invalid-profile-{index}.sqlite3"
                    with self.assertRaises(InvestmentError):
                        initialize(database, spec, NOW)
                    self.assertFalse(database.exists())
                for field in ("observed_at", "available_at"):
                    with self.subTest(field=f"terms.{field}", value=value):
                        spec = profile()
                        spec["accounts"][0]["terms"] = savings_terms()
                        spec["accounts"][0]["terms"][field] = value
                        database = Path(self.temporary.name) / f"invalid-terms-{index}-{field}.sqlite3"
                        with self.assertRaises(InvestmentError):
                            initialize(database, spec, NOW)
                        self.assertFalse(database.exists())
                for field in ("timestamp", "available_at"):
                    with self.subTest(field=f"opening.{field}", value=value):
                        database = Path(self.temporary.name) / f"invalid-opening-{index}-{field}.sqlite3"
                        initialize(database, profile(), NOW)
                        before = database.read_bytes()
                        initial = opening()
                        initial[field] = value
                        with self.assertRaises(InvestmentError):
                            import_events(database, [initial], NOW + timedelta(microseconds=1))
                        self.assertEqual(database.read_bytes(), before)

    def test_exact_zero_padded_clocks_remain_readable_without_rewriting(self):
        spec = profile()
        spec["created_at"] = "2026-01-01T12:00:00.000000000Z"
        initial = opening()
        initial["timestamp"] = initial["available_at"] = "2026-01-01T12:00:00.0000000Z"
        initialize(self.database, spec, NOW)
        import_events(self.database, [initial], NOW)
        before = self.database.read_bytes()
        ledger = read(self.database, NOW)
        self.assertEqual(ledger.ids["opening"]["timestamp"], initial["timestamp"])
        self.assertEqual(ledger.baseline_at, NOW)
        self.assertEqual(self.database.read_bytes(), before)

    def test_revision_bound_preview_and_commit_are_retry_safe(self):
        initialize(self.database, profile(), NOW)
        original = self.database.read_bytes()
        preview = store.preview_record(self.database, opening(), NOW)
        self.assertEqual(preview["revision"], 0)
        self.assertEqual(preview["after"]["equity_gbp"], "2000")
        self.assertEqual(self.database.read_bytes(), original)
        receipt = store.commit_record(self.database, preview, NOW)
        self.assertEqual(receipt["revision"], 1)
        self.assertEqual(read(self.database, NOW).cash["bank"], 1000)
        later = event("later-interest", "interest", NOW, "bank", amount_gbp="5")
        import_events(self.database, [later], NOW)
        self.assertEqual(store.commit_record(self.database, preview, NOW), receipt)
        self.assertEqual(read(self.database, NOW).revision, 2)

    def test_profile_changing_commits_are_retryable_against_their_original_predecessor(self):
        changed_at = NOW + timedelta(hours=1)
        rename = {
            "id": "rename-product", "type": "profile_change",
            "timestamp": iso(changed_at), "available_at": iso(changed_at),
            "evidence": "synthetic product documentation", "action": "rename",
            "target": "product", "entity_id": "equity", "name": "Renamed equity",
        }
        later = changed_at + timedelta(hours=1)
        later_rename = {
            "id": "rename-bank", "type": "profile_change",
            "timestamp": iso(later), "available_at": iso(later),
            "evidence": "synthetic account documentation", "action": "rename",
            "target": "account", "entity_id": "bank", "name": "Renamed bank",
        }
        for value in (rename, funded_scope(changed_at)):
            with self.subTest(kind=value["type"]):
                database = self.database.with_name(value["id"] + ".sqlite3")
                initialize(database, profile(), NOW)
                import_events(database, [opening()], NOW)
                preview = store.preview_record(database, value, changed_at)
                receipt = store.commit_record(database, preview, changed_at)
                self.assertEqual(store.commit_record(database, preview, changed_at), receipt)
                import_events(database, [later_rename], later)
                before = database.read_bytes()
                self.assertEqual(store.commit_record(database, preview, later), receipt)
                for field in ("profile_hash", "previous_hash"):
                    changed = deepcopy(preview)
                    changed[field] = "0" * 64
                    with self.subTest(field=field), self.assertRaises(InvestmentError):
                        store.commit_record(database, changed, later)
                self.assertEqual(database.read_bytes(), before)
                self.assertEqual(read(database, later).revision, 3)

    def test_profile_amendments_are_append_only_and_survive_replay(self):
        initialize(self.database, profile(), NOW)
        rename_at = NOW + timedelta(hours=1)
        add_product = {
            "id": "add-product", "type": "profile_change",
            "timestamp": iso(rename_at), "available_at": iso(rename_at),
            "evidence": "synthetic product documentation", "action": "add_product",
            "product": {"id": "cash-fund", "name": "Cash fund", "sleeve": "bond",
                        "currency": "GBP", "quantity_increment": "1",
                        "minimum_notional_gbp": "1"},
        }
        rename_product = {
            "id": "rename-product", "type": "profile_change",
            "timestamp": iso(rename_at + timedelta(hours=1)),
            "available_at": iso(rename_at + timedelta(hours=1)),
            "evidence": "synthetic corrected product name", "action": "rename",
            "target": "product", "entity_id": "cash-fund", "name": "Short gilt fund",
        }
        import_events(self.database, [opening(), add_product, rename_product],
                       rename_at + timedelta(hours=1))

        ledger = read(self.database, rename_at + timedelta(hours=1))
        self.assertEqual(
            next(item for item in ledger.spec["products"] if item["id"] == "cash-fund")["name"],
            "Short gilt fund",
        )
        self.assertEqual(ledger.revision, 3)
        self.assertEqual(len(ledger.profile_history), 2)

    def test_funded_account_scope_addition_is_unitization_neutral(self):
        initialize(self.database, profile(), NOW)
        scope_at = NOW + timedelta(hours=1)
        import_events(self.database, [opening(), funded_scope(scope_at)], scope_at)

        result = snapshot(self.database, scope_at)
        ledger = read(self.database, scope_at)
        self.assertEqual(ledger.net, 0)
        self.assertEqual(ledger.units, 2020)
        self.assertEqual(ledger.unit_price, 1)
        self.assertEqual(ledger.scope_additions, 20)
        self.assertEqual(result["since_baseline_gain_gbp"], "0")

    def test_funded_scope_requires_complete_matching_contemporaneous_evidence(self):
        initialize(self.database, profile(), NOW)
        import_events(self.database, [opening()], NOW)
        original = self.database.read_bytes()
        scope_at = NOW + timedelta(hours=1)
        for mutation in ("missing_balance", "changed_balance", "missing_holding",
                         "missing_price", "late_availability"):
            candidate = funded_scope(scope_at)
            if mutation == "missing_balance":
                del candidate["balances_gbp"]["broker"]
            elif mutation == "changed_balance":
                candidate["balances_gbp"]["bank"] = "999"
            elif mutation == "missing_holding":
                candidate["holdings"].pop(1)
            elif mutation == "missing_price":
                candidate["holdings"][0]["price_gbp"] = None
            else:
                candidate["available_at"] = iso(scope_at + timedelta(seconds=1))
            with self.subTest(mutation=mutation), self.assertRaises(InvestmentError):
                import_events(self.database, [candidate], scope_at + timedelta(seconds=1))
            self.assertEqual(self.database.read_bytes(), original)
            self.assertEqual(read(self.database, scope_at).revision, 1)

        unpriced = Path(self.temporary.name) / "unpriced.sqlite3"
        initial = opening()
        initial["holdings"][0]["price_gbp"] = None
        initialize(unpriced, profile(), NOW)
        import_events(unpriced, [initial], NOW)
        original = unpriced.read_bytes()
        with self.assertRaisesRegex(InvestmentError, "valid performance baseline"):
            import_events(unpriced, [funded_scope(scope_at)], scope_at)
        self.assertEqual(unpriced.read_bytes(), original)

    def test_funded_scope_keeps_new_cash_and_holdings_unassigned(self):
        initialize(self.database, profile(), NOW)
        assignment = {
            "id": "core-bank", "type": "role_assignment", "timestamp": iso(NOW),
            "available_at": iso(NOW), "evidence": "synthetic role evidence",
            "account_id": "bank", "product_id": None, "role": "core", "amount": "400",
        }
        scope_at = NOW + timedelta(hours=1)
        import_events(self.database, [opening(), assignment, funded_scope(scope_at)], scope_at)
        ledger = read(self.database, scope_at)
        self.assertEqual(ledger.role_cash[("bank", "core")], 400)
        self.assertEqual(ledger.role_cash[("scope", "unassigned")], 10)
        self.assertEqual(ledger.role_holdings[("scope", "equity", "unassigned")], 2)

    def test_retirement_requires_zero_exposure_and_preserves_identity(self):
        initialize(self.database, profile(), NOW)
        timestamp = NOW + timedelta(hours=1)
        retirement = {
            "id": "retire-bank", "type": "profile_change",
            "timestamp": iso(timestamp), "available_at": iso(timestamp),
            "evidence": "synthetic retirement record", "action": "retire",
            "target": "account", "entity_id": "bank",
        }
        with self.assertRaisesRegex(InvestmentError, "zero"):
            import_events(self.database, [opening(), retirement], timestamp)

        emptying = event("withdraw-bank", "withdrawal", timestamp, "bank",
                         amount_gbp="1000")
        import_events(self.database, [opening(), emptying], timestamp)
        retired_at = timestamp + timedelta(hours=1)
        retirement["timestamp"] = iso(retired_at)
        retirement["available_at"] = iso(retired_at)
        import_events(self.database, [retirement], retired_at)
        ledger = read(self.database, retired_at)
        self.assertIn("bank", ledger.cash)
        self.assertIn("bank", ledger.retired_accounts)

    def test_scope_addition_cannot_reintroduce_retired_product(self):
        initialize(self.database, profile(), NOW)
        sold_at = NOW + timedelta(hours=1)
        retire_at = sold_at + timedelta(hours=1)
        retired_product = {
            "id": "retire-bond", "type": "profile_change",
            "timestamp": iso(retire_at), "available_at": iso(retire_at),
            "evidence": "synthetic retirement record", "action": "retire",
            "target": "product", "entity_id": "bond",
        }
        import_events(self.database, [
            opening(),
            event("sell-bond", "sell", sold_at, "broker", product_id="bond",
                  quantity="20", price_gbp="20", fee_gbp="0"),
            retired_product,
        ], retire_at)

        scope_at = retire_at + timedelta(hours=1)
        scope_addition = {
            "id": "scope-with-retired-bond", "type": "scope_addition",
            "timestamp": iso(scope_at), "available_at": iso(scope_at),
            "evidence": "synthetic common-clock statements",
            "account": {"id": "scope", "name": "Additional broker", "kind": "broker",
                        "binding": "scope-test", "currency": "GBP"},
            "balances_gbp": {"bank": "1000", "broker": "500", "scope": "10"},
            "holdings": [
                {"account_id": "broker", "product_id": "equity", "quantity": "50",
                 "price_gbp": "10"},
                {"account_id": "scope", "product_id": "bond", "quantity": "1",
                 "price_gbp": "20"},
            ],
        }
        before = self.database.read_bytes()
        with self.assertRaisesRegex(InvestmentError, "retired product"):
            import_events(self.database, [scope_addition], scope_at)
        self.assertEqual(self.database.read_bytes(), before)

    def test_latest_cash_flow_correction_preserves_evidence_and_replaces_accounting_effect(self):
        initialize(self.database, profile(), NOW)
        contribution = event("funding", "contribution", NOW, "bank", amount_gbp="100")
        import_events(self.database, [opening(), contribution], NOW)
        available = NOW + timedelta(days=1)
        correction_event = correction(contribution, "80", available)
        import_events(self.database, [correction_event], available)

        ledger = read(self.database, available)
        result = snapshot(self.database, available)
        self.assertEqual(ledger.cash["bank"], 1080)
        self.assertEqual(ledger.net, 80)
        self.assertIn("funding", ledger.ids)
        self.assertIn("replacement-funding", ledger.ids)
        self.assertIn("funding", ledger.superseded_ids)
        self.assertEqual(result["net_contributions_gbp"], "80")
        self.assertEqual([item["id"] for item in result["recorded_flow_events"]],
                         ["replacement-funding"])
        self.assertEqual(result["recorded_corrections"], [{
            "id": "correction-funding", "original_event_id": "funding",
            "replacement_event_id": "replacement-funding", "replaced_type": "contribution",
            "account_id": "bank", "role": "unassigned", "role_evidence": None, "original_amount_gbp": "100",
            "replacement_amount_gbp": "80", "timestamp": iso(NOW),
            "available_at": iso(available), "evidence": "synthetic correction statement",
        }])
        self.assertIsNone(result["equity_gbp"])
        self.assertEqual(import_events(self.database, [correction_event], available), 3)

    def test_historical_correction_replays_downstream_and_invalidates_performance(self):
        initialize(self.database, profile(), NOW)
        opening_event = dict(opening(), balances_gbp={"bank": "1000", "broker": "100"},
                             holdings=[])
        first = event("first-flow", "contribution", NOW + timedelta(hours=1),
                      "bank", amount_gbp="100")
        interest = event("interest", "interest", NOW + timedelta(hours=2),
                         "bank", amount_gbp="5")
        last = event("last-flow", "contribution", NOW + timedelta(hours=3),
                     "bank", amount_gbp="10")
        import_events(self.database, [opening_event, first, interest, last],
                      NOW + timedelta(hours=3))

        correction_at = NOW + timedelta(hours=4)
        correction_event = historical_correction(
            interest, {"amount_gbp": "2"}, correction_at)
        import_events(self.database, [correction_event], correction_at)

        ledger = read(self.database, correction_at)
        result = snapshot(self.database, correction_at)
        self.assertEqual(ledger.cash["bank"], 1112)
        self.assertEqual(ledger.net, 110)
        self.assertEqual(ledger.ids["interest"]["amount_gbp"], "5")
        self.assertIn("interest", ledger.superseded_ids)
        self.assertFalse(ledger.performance_valid)
        self.assertIsNone(result["since_baseline_gain_gbp"])
        self.assertIsNone(result["twr_pct"])
        self.assertEqual(result["recorded_corrections"][0]["replaced_type"], "interest")

    def test_correction_cannot_precede_the_original_evidence_availability(self):
        initialize(self.database, profile(), NOW)
        late = NOW + timedelta(days=2)
        contribution = event("late-funding", "contribution", NOW + timedelta(hours=1),
                             "bank", amount_gbp="100")
        contribution["available_at"] = iso(late)
        import_events(self.database, [
            dict(opening(), holdings=[]), contribution,
        ], late)
        corrected_at = NOW + timedelta(days=1)
        correction_event = historical_correction(
            contribution, {"amount_gbp": "80"}, corrected_at)
        confirmation_at = corrected_at + timedelta(hours=1)
        confirmation = {
            "id": "early-confirmation", "type": "cash_confirmation",
            "timestamp": iso(confirmation_at), "available_at": iso(confirmation_at),
            "evidence": "synthetic confirmation",
            "balances_gbp": {"bank": "1080", "broker": "100"},
        }
        before = self.database.read_bytes()
        with self.assertRaisesRegex(InvestmentError, "Correction.*availability"):
            import_events(self.database, [correction_event, confirmation], late)
        self.assertEqual(self.database.read_bytes(), before)
        self.assertEqual(read(self.database, late).revision, 2)

        payload = store.canonical(correction_event)
        with closing(sqlite3.connect(self.database)) as connection, connection:
            previous = connection.execute(
                "SELECT hash FROM events WHERE revision=2").fetchone()[0]
            connection.execute(
                "INSERT INTO events VALUES (?,?,?,?,?)",
                (3, correction_event["id"], payload, previous, store.digest(previous + payload)))
            connection.execute("INSERT INTO commits VALUES (?,?)", (3, "0" * 64))
        invalid = self.database.read_bytes()
        with self.assertRaisesRegex(InvestmentError, "Correction.*availability"):
            read(self.database, late)
        self.assertEqual(self.database.read_bytes(), invalid)

    def test_later_correction_preserves_journal_order_of_recorded_flows(self):
        initialize(self.database, profile(), NOW)
        first = event("first", "contribution", NOW + timedelta(seconds=1),
                      "bank", amount_gbp="1")
        second = event("second", "contribution", NOW + timedelta(seconds=2),
                       "bank", amount_gbp="1")
        first_correction = historical_correction(
            first, {"amount_gbp": "2"}, NOW + timedelta(seconds=3))
        flows = [
            event(f"flow-{index:03}", "contribution",
                  NOW + timedelta(seconds=4 + index), "bank", amount_gbp="1")
            for index in range(101)
        ]
        second_at = NOW + timedelta(seconds=105)
        import_events(self.database, [
            dict(opening(), holdings=[]), first, second, first_correction, *flows,
        ], second_at)
        # Pin the prior schema-v2 checkpoint despite the reporting-order fix.
        self.assertEqual(
            store.state_digest(read(self.database, second_at)),
            "0bb6a8633e05f58cfaeec24e6f6b7f8ba90524ecd5893dc9e64799d7178aa19e")
        before = snapshot(self.database, second_at)["recorded_flow_events"]
        self.assertEqual(before[0]["id"], "second")
        self.assertEqual(before[1]["id"], "replacement-first")
        second_correction = historical_correction(second, {"amount_gbp": "3"}, second_at)
        import_events(self.database, [second_correction], second_at)
        self.assertEqual(
            store.state_digest(read(self.database, second_at)),
            "ca105ca877a93b35164e18f266824c6a2530c4ad4435dc0d36920d98492228c9")
        identifiers = [
            value["id"] for value in snapshot(self.database, second_at)["recorded_flow_events"]
        ]
        self.assertEqual(
            identifiers, ["replacement-first", *[value["id"] for value in flows],
                          "replacement-second"])
        self.assertEqual(identifiers[-100:],
                         [*[value["id"] for value in flows[-99:]], "replacement-second"])

    def test_correction_replacements_cannot_be_imported_as_source_events(self):
        original = event("funding", "contribution", NOW + timedelta(seconds=1),
                         "bank", amount_gbp="100")
        correction_at = NOW + timedelta(seconds=2)
        correction_event = historical_correction(original, {"amount_gbp": "80"}, correction_at)
        after = correction_at + timedelta(seconds=1)
        extra = event("extra", "interest", after, "bank", amount_gbp="1")
        replacement = correction_event["replacement_event"]
        for index, batch in enumerate(([replacement, extra], [replacement], [extra, replacement])):
            database = self.database.with_name(f"existing-correction-{index}.sqlite3")
            initialize(database, profile(), NOW)
            import_events(database, [opening(), original, correction_event], correction_at)
            before = database.read_bytes()
            with self.subTest(ids=[value["id"] for value in batch]):
                with self.assertRaises(InvestmentError):
                    import_events(database, batch, after)
                self.assertEqual(database.read_bytes(), before)
                self.assertEqual(read(database, after).revision, 3)

        for index, batch in enumerate((
                [correction_event, replacement, extra],
                [replacement, correction_event, extra])):
            database = self.database.with_name(f"new-correction-{index}.sqlite3")
            initialize(database, profile(), NOW)
            import_events(database, [opening(), original], correction_at)
            before = database.read_bytes()
            with self.subTest(ids=[value["id"] for value in batch]):
                with self.assertRaises(InvestmentError):
                    import_events(database, batch, after)
                self.assertEqual(database.read_bytes(), before)
                self.assertEqual(read(database, after).revision, 2)

        ledger = core.replay(profile(), [opening(), original, correction_event], after)
        duplicate = core.replay(
            profile(), [opening(), opening(), original, correction_event], after)
        self.assertEqual(duplicate.revision, ledger.revision)
        self.assertEqual(store.state_digest(duplicate), store.state_digest(ledger))
        with self.assertRaises(InvestmentError):
            core.replay(profile(), [opening(), original, correction_event, replacement, extra], after)

    def test_replacement_id_cannot_overwrite_an_earlier_correction(self):
        initialize(self.database, profile(), NOW)
        original = event("interest", "interest", NOW + timedelta(seconds=1),
                         "bank", amount_gbp="5")
        first_at = NOW + timedelta(seconds=2)
        first = historical_correction(original, {"amount_gbp": "4"}, first_at)
        import_events(self.database, [opening(), original, first], first_at)
        next_at = NOW + timedelta(seconds=3)
        second = historical_correction(first["replacement_event"], {"amount_gbp": "3"}, next_at)
        second["replacement_event"]["id"] = first["id"]
        before = self.database.read_bytes()
        with self.assertRaises(InvestmentError):
            import_events(self.database, [second], next_at)
        self.assertEqual(self.database.read_bytes(), before)
        ledger = read(self.database, next_at)
        self.assertEqual(ledger.revision, 3)
        self.assertEqual(ledger.ids[first["id"]], first)
        self.assertEqual(ledger.cash["bank"], 1004)

    def test_correction_clocks_compare_instants_not_timestamp_spelling(self):
        initialize(self.database, profile(), NOW)
        original = event("interest", "interest", NOW, "bank", amount_gbp="5")
        import_events(self.database, [opening(), original], NOW)
        correction_at = NOW + timedelta(microseconds=500000)
        replacement = historical_correction(original, {"amount_gbp": "4"}, correction_at)
        invalid = deepcopy(replacement)
        invalid["replacement_event"]["timestamp"] = iso(NOW + timedelta(microseconds=1))
        before = self.database.read_bytes()
        with self.assertRaises(InvestmentError):
            import_events(self.database, [invalid], correction_at)
        self.assertEqual(self.database.read_bytes(), before)
        replacement["replacement_event"]["timestamp"] = original["timestamp"].replace("Z", "+00:00")
        replacement["replacement_event"]["available_at"] = iso(correction_at).replace("Z", "+00:00")
        import_events(self.database, [replacement], correction_at)
        ledger = read(self.database, correction_at)
        self.assertEqual(ledger.cash["bank"], 1004)
        self.assertEqual(ledger.ids[original["id"]], original)
        self.assertEqual(ledger.ids[replacement["id"]], replacement)

    def test_historical_correction_is_rejected_when_downstream_replay_breaks_cash(self):
        initialize(self.database, profile(), NOW)
        opening_event = dict(opening(), balances_gbp={"bank": "1000", "broker": "100"},
                             holdings=[])
        funding = event("broker-funding", "contribution", NOW + timedelta(hours=1),
                        "broker", amount_gbp="100")
        purchase = event("later-purchase", "buy", NOW + timedelta(hours=2),
                         "broker", product_id="equity", quantity="150",
                         price_gbp="1", fee_gbp="0")
        import_events(self.database, [opening_event, funding, purchase],
                      NOW + timedelta(hours=2))
        correction_at = NOW + timedelta(hours=3)
        correction_event = historical_correction(
            funding, {"amount_gbp": "20"}, correction_at)
        with self.assertRaisesRegex(InvestmentError, "cash"):
            import_events(self.database, [correction_event], correction_at)
        self.assertEqual(read(self.database, correction_at).cash["broker"], 50)

    def test_historical_correction_is_rejected_when_downstream_sale_would_be_short(self):
        initialize(self.database, profile(), NOW)
        buy_at, sell_at, correction_at = (NOW + timedelta(hours=n) for n in (1, 2, 3))
        purchase = event("purchase", "buy", buy_at, "broker", product_id="equity",
                         quantity="100", price_gbp="1", fee_gbp="0")
        sale = event("sale", "sell", sell_at, "broker", product_id="equity",
                     quantity="130", price_gbp="1", fee_gbp="0")
        import_events(self.database, [opening(), purchase, sale], sell_at)
        original = self.database.read_bytes()
        correction_event = historical_correction(purchase, {"quantity": "50"}, correction_at)
        with self.assertRaisesRegex(InvestmentError, "short position"):
            import_events(self.database, [correction_event], correction_at)
        self.assertEqual(self.database.read_bytes(), original)
        ledger = read(self.database, correction_at)
        self.assertEqual(ledger.revision, 3)
        self.assertEqual(ledger.holdings[("broker", "equity")], 20)

    def test_historical_cash_correction_can_replay_earlier_amounts_but_not_observations(self):
        initialize(self.database, profile(), NOW)
        contribution = event("funding", "contribution", NOW, "bank", amount_gbp="100")
        interest = event("interest", "interest", NOW, "bank", amount_gbp="5")
        import_events(self.database, [opening(), contribution, interest], NOW)
        later = NOW + timedelta(days=1)
        import_events(self.database, [correction(contribution, "80", later)], later)
        self.assertEqual(read(self.database, later).cash["bank"], 1085)
        observation = cash_observation(NOW, identifier="observation")
        observation["timestamp"] = iso(later)
        observation["available_at"] = iso(later)
        import_events(self.database, [observation], later)
        correction_at = later + timedelta(days=1)
        import_events(
            self.database,
            [historical_correction(interest, {"amount_gbp": "4"}, correction_at)],
            correction_at,
        )
        self.assertEqual(read(self.database, correction_at).cash["bank"], 1084)
        self.assertEqual(read(self.database, correction_at).revision, 6)
        with self.assertRaisesRegex(InvestmentError, "eligible"):
            import_events(
                self.database,
                [historical_correction(observation, {"balance_gbp": "999"},
                                        correction_at + timedelta(days=1))],
                correction_at + timedelta(days=1),
            )

    def test_revision_bound_commit_rejects_stale_or_conflicting_input(self):
        initialize(self.database, profile(), NOW)
        import_events(self.database, [opening()], NOW)
        first = event("first", "interest", NOW, "bank", amount_gbp="5")
        second = event("second", "interest", NOW, "bank", amount_gbp="7")
        stale = store.preview_record(self.database, first, NOW)
        import_events(self.database, [second], NOW)
        with self.assertRaisesRegex(InvestmentError, "revision"):
            store.commit_record(self.database, stale, NOW)
        self.assertEqual(read(self.database, NOW).cash["bank"], 1007)

        committed = store.preview_record(
            self.database, event("duplicate", "interest", NOW, "bank", amount_gbp="2"), NOW)
        store.commit_record(self.database, committed, NOW)
        changed = deepcopy(committed)
        changed["event"]["amount_gbp"] = "9"
        with self.assertRaises(InvestmentError):
            store.commit_record(self.database, changed, NOW)
        self.assertEqual(read(self.database, NOW).cash["bank"], 1009)

    def test_revision_bound_commit_failure_rolls_back_and_can_retry(self):
        initialize(self.database, profile(), NOW)
        preview = store.preview_record(self.database, opening(), NOW)
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

        with patch.object(store, "_connect", side_effect=deny_commit):
            with self.assertRaisesRegex(InvestmentError, "outcome uncertain"):
                store.commit_record(self.database, preview, NOW)
        self.assertEqual(denied, [True])
        self.assertEqual(read(self.database, NOW).revision, 0)
        self.assertEqual(store.commit_record(self.database, preview, NOW)["revision"], 1)

    def test_post_commit_close_failure_preserves_initialized_journal(self):
        connect = sqlite3.connect
        with patch.object(store.sqlite3, "connect",
                          side_effect=lambda *args, **kwargs: ConnectionCloseFailure(
                              connect(*args, **kwargs))):
            with self.assertRaisesRegex(InvestmentError, "initialized at revision 0"):
                initialize(self.database, profile(), NOW)
        self.assertTrue(self.database.exists())
        self.assertEqual(read(self.database, NOW).revision, 0)

    def test_post_commit_close_failures_report_revision_and_allow_exact_retries(self):
        initialize(self.database, profile(), NOW)
        connect = store._connect

        def fail_close(database, readonly):
            connection = connect(database, readonly)
            return connection if readonly else ConnectionCloseFailure(connection)

        with patch.object(store, "_connect", side_effect=fail_close):
            with self.assertRaisesRegex(InvestmentError, "import completed at revision 1"):
                import_events(self.database, [opening()], NOW)
        self.assertEqual(read(self.database, NOW).revision, 1)
        self.assertEqual(import_events(self.database, [opening()], NOW), 1)

        update = event("interest-after-close", "interest", NOW, "bank", amount_gbp="1")
        preview = store.preview_record(self.database, update, NOW)
        with patch.object(store, "_connect", side_effect=fail_close):
            with self.assertRaisesRegex(InvestmentError, "record committed at revision 2"):
                store.commit_record(self.database, preview, NOW)
        self.assertEqual(read(self.database, NOW).revision, 2)
        self.assertEqual(store.commit_record(self.database, preview, NOW),
                         {"id": update["id"], "revision": 2})

    def test_transaction_exit_failure_after_known_commit_preserves_evidence(self):
        connect = sqlite3.connect
        with patch.object(store.sqlite3, "connect",
                          side_effect=lambda *args, **kwargs: TransactionExitFailure(
                              connect(*args, **kwargs))):
            with self.assertRaisesRegex(InvestmentError, "initialized at revision 0"):
                initialize(self.database, profile(), NOW)
        self.assertEqual(read(self.database, NOW).revision, 0)
        store_connect = store._connect

        def fail_exit(database, readonly):
            connection = store_connect(database, readonly)
            return connection if readonly else TransactionExitFailure(connection)

        with patch.object(store, "_connect", side_effect=fail_exit):
            with self.assertRaisesRegex(InvestmentError, "import completed at revision 1"):
                import_events(self.database, [opening()], NOW)
        self.assertEqual(read(self.database, NOW).revision, 1)
        self.assertEqual(import_events(self.database, [opening()], NOW), 1)

    def test_lost_commit_result_preserves_evidence_and_never_claims_rollback(self):
        connect = sqlite3.connect
        with patch.object(store.sqlite3, "connect",
                          side_effect=lambda *args, **kwargs: CommitResultFailure(
                              connect(*args, **kwargs))):
            with self.assertRaises(InvestmentError) as failed_init:
                initialize(self.database, profile(), NOW)
        self.assertNotIn("rolled back", str(failed_init.exception))
        self.assertEqual(read(self.database, NOW).revision, 0)
        store_connect = store._connect

        def lose_result(database, readonly):
            connection = store_connect(database, readonly)
            return connection if readonly else CommitResultFailure(connection)

        with patch.object(store, "_connect", side_effect=lose_result):
            with self.assertRaises(InvestmentError) as failed_import:
                import_events(self.database, [opening()], NOW)
        self.assertNotIn("rolled back", str(failed_import.exception))
        self.assertEqual(read(self.database, NOW).revision, 1)
        self.assertEqual(import_events(self.database, [opening()], NOW), 1)

    def test_connection_validation_failure_closes_the_handle(self):
        initialize(self.database, profile(), NOW)
        connect = sqlite3.connect
        opened = []

        class InvalidConnection:
            def __init__(self, connection):
                self.connection = connection
                self.closed = False

            def execute(self, *args, **kwargs):
                raise sqlite3.OperationalError("synthetic PRAGMA failure")

            def close(self):
                self.connection.close()
                self.closed = True

        def invalid_connection(*args, **kwargs):
            connection = InvalidConnection(connect(*args, **kwargs))
            opened.append(connection)
            return connection

        with patch.object(store.sqlite3, "connect", side_effect=invalid_connection):
            with self.assertRaisesRegex(InvestmentError, "Cannot open investment store"):
                store._connect(self.database, False)
        try:
            self.assertEqual(len(opened), 1)
            self.assertTrue(opened[0].closed)
        finally:
            for connection in opened:
                if not connection.closed:
                    connection.close()

    def test_database_size_limit_rolls_back_import_batch(self):
        initialize(self.database, profile(), NOW)
        before = self.database.read_bytes()
        with closing(sqlite3.connect(self.database)) as connection:
            page_count = connection.execute("PRAGMA page_count").fetchone()[0]
            page_size = connection.execute("PRAGMA page_size").fetchone()[0]
        byte_limit = max(self.database.stat().st_size, page_count * page_size)
        observations = [
            cash_observation(
                timestamp=NOW + timedelta(seconds=index + 1),
                account="bank" if index % 2 else "broker",
                identifier=f"capacity-observation-{index}",
                balance="1000" if index % 2 else "100",
            )
            for index in range(100)
        ]
        with patch.object(store, "MAX_DATABASE_BYTES", byte_limit):
            with self.assertRaisesRegex(InvestmentError, "size bound.*rolled back"):
                import_events(self.database, [opening(), *observations],
                              NOW + timedelta(seconds=101))
        self.assertEqual(self.database.read_bytes(), before)
        self.assertEqual(read(self.database, NOW).revision, 0)

    def test_retry_rejects_type_changed_evidence_even_with_matching_hash(self):
        initialize(self.database, profile(), NOW)
        import_events(self.database, [opening()], NOW)
        later = NOW + timedelta(days=1)
        update = savings_terms_event(later)
        update["notice_days"] = 1
        preview = store.preview_record(self.database, update, later)
        store.commit_record(self.database, preview, later)
        changed = deepcopy(preview)
        changed["event"]["notice_days"] = True
        changed["event_hash"] = store.digest(store.canonical(changed["event"]))
        with self.assertRaises(InvestmentError):
            store.commit_record(self.database, changed, later)

    def test_import_rejects_type_changed_duplicate_without_appending_batch(self):
        initialize(self.database, profile(), NOW)
        import_events(self.database, [opening()], NOW)
        later = NOW + timedelta(days=1)
        update = savings_terms_event(later)
        update["notice_days"] = 1
        import_events(self.database, [update], later)
        changed = deepcopy(update)
        changed["notice_days"] = True
        extra = event("extra", "interest", later, "bank", amount_gbp="1")
        with self.assertRaisesRegex(InvestmentError, "Conflicting duplicate"):
            import_events(self.database, [changed, extra], later)
        self.assertEqual(read(self.database, later).revision, 2)

    def test_preview_fails_when_commit_input_would_exceed_bound(self):
        initialize(self.database, profile(), NOW)
        full = store.preview_record(self.database, opening(), NOW)
        limit = len(store.canonical(full).encode("utf-8")) - 1
        with patch.object(store, "MAX_JSON_BYTES", limit):
            with self.assertRaisesRegex(InvestmentError, "bound"):
                store.preview_record(self.database, opening(), NOW)
        self.assertEqual(read(self.database, NOW).revision, 0)

    def test_preview_fails_when_combined_evidence_exceeds_bound(self):
        initialize(self.database, profile(), NOW)
        size = len(store.canonical(profile()).encode("utf-8")) + len(
            store.canonical([opening()]).encode("utf-8"))
        with patch.object(store, "MAX_JSON_BYTES", size - 1):
            with self.assertRaisesRegex(InvestmentError, "bound"):
                store.preview_record(self.database, opening(), NOW)
        self.assertEqual(read(self.database, NOW).revision, 0)

    def test_retry_does_not_claim_an_uncommitted_intermediate_revision(self):
        initialize(self.database, profile(), NOW)
        import_events(self.database, [opening()], NOW)
        first = event("batch-first", "interest", NOW, "bank", amount_gbp="2")
        second = event("batch-second", "interest", NOW, "bank", amount_gbp="3")
        preview = store.preview_record(self.database, first, NOW)
        import_events(self.database, [first, second], NOW)
        with self.assertRaisesRegex(InvestmentError, "revision"):
            store.commit_record(self.database, preview, NOW)
        fabricated = deepcopy(preview)
        fabricated["revision"] = 2
        fabricated["event"] = second
        fabricated["event_hash"] = store.digest(store.canonical(second))
        with closing(sqlite3.connect(self.database)) as connection:
            fabricated["previous_hash"] = connection.execute(
                "SELECT hash FROM events WHERE revision=2").fetchone()[0]
        with self.assertRaisesRegex(InvestmentError, "revision"):
            store.commit_record(self.database, fabricated, NOW)
        self.assertEqual(read(self.database, NOW).revision, 3)

    def test_preview_binding_rejects_other_profile_and_invalid_event(self):
        initialize(self.database, profile(), NOW)
        preview = store.preview_record(self.database, opening(), NOW)
        other = self.database.with_name("other.sqlite3")
        other_profile = profile()
        other_profile["portfolio_id"] = "another"
        initialize(other, other_profile, NOW)
        with self.assertRaisesRegex(InvestmentError, "profile"):
            store.commit_record(other, preview, NOW)
        invalid = deepcopy(opening())
        invalid["balances_gbp"]["bank"] = "-1"
        with self.assertRaises(InvestmentError):
            store.preview_record(self.database, invalid, NOW)
        self.assertEqual(read(self.database, NOW).revision, 0)
        self.assertEqual(read(other, NOW).revision, 0)

    def test_preview_profile_hash_is_not_unique_store_identity(self):
        initialize(self.database, profile(), NOW)
        other = self.database.with_name("same-profile.sqlite3")
        initialize(other, profile(), NOW)
        first = store.preview_record(self.database, opening(), NOW)
        second = store.preview_record(other, opening(), NOW)
        self.assertEqual(first["profile_hash"], second["profile_hash"])
        self.assertEqual(first["previous_hash"], second["previous_hash"])

    def test_preview_preserves_same_clock_mark_before_flow_rule(self):
        initialize(self.database, profile(), NOW)
        import_events(self.database, [opening()], NOW)
        later = NOW + timedelta(days=1)
        flow = event("flow", "contribution", later, "bank", amount_gbp="100")
        store.commit_record(self.database, store.preview_record(self.database, flow, later), later)
        mark = event("mark", "mark", later, "broker", product_id="equity", price_gbp="10")
        with self.assertRaisesRegex(InvestmentError, "must precede"):
            store.preview_record(self.database, mark, later)
        self.assertEqual(read(self.database, later).revision, 2)

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

    def test_v2_report_attributes_only_recorded_fees_and_income(self):
        initialize(self.database, profile(), NOW)
        events = [
            opening(),
            event("recorded-buy", "buy", NOW, "broker", product_id="equity",
                  quantity="1", price_gbp="10", fee_gbp="2"),
            event("recorded-interest", "interest", NOW, "bank", amount_gbp="5"),
            event("recorded-dividend", "dividend", NOW, "broker",
                  product_id="equity", amount_gbp="3"),
        ]
        import_events(self.database, events, NOW)

        result = snapshot(self.database, NOW)
        self.assertEqual(result["equity_gbp"], "2006")
        self.assertEqual(result["recorded_fees_gbp"], "2")
        self.assertEqual(result["recorded_interest_gbp"], "5")
        self.assertEqual(result["recorded_dividends_gbp"], "3")
        self.assertEqual(result["recorded_income_gbp"], "8")
        self.assertEqual(result["recorded_fee_events"], [{
            "id": "recorded-buy", "type": "buy", "account_id": "broker",
            "product_id": "equity", "quantity": "1", "price_gbp": "10",
            "fee_gbp": "2", "role": "unassigned", "timestamp": iso(NOW), "available_at": iso(NOW),
            "evidence": "synthetic event evidence",
        }])
        self.assertEqual(result["recorded_income_events"], [
            {"id": "recorded-interest", "type": "interest", "account_id": "bank",
             "product_id": None, "role": "unassigned", "role_evidence": None, "amount_gbp": "5", "timestamp": iso(NOW),
             "available_at": iso(NOW), "evidence": "synthetic event evidence"},
            {"id": "recorded-dividend", "type": "dividend", "account_id": "broker",
             "product_id": "equity", "role": "unassigned", "role_evidence": None, "amount_gbp": "3", "timestamp": iso(NOW),
             "available_at": iso(NOW), "evidence": "synthetic event evidence"},
        ])
        self.assertEqual(import_events(self.database, events[1:], NOW), result["revision"])
        self.assertEqual(snapshot(self.database, NOW), result)

    def test_v2_report_exposes_recorded_cash_flows_in_journal_order(self):
        initialize(self.database, profile(), NOW)
        events = [
            opening(),
            event("recorded-contribution", "contribution", NOW, "bank",
                  amount_gbp="100"),
            event("recorded-transfer", "transfer", NOW, "bank", amount_gbp="50",
                  destination_account_id="broker"),
            event("recorded-withdrawal", "withdrawal", NOW, "bank",
                  amount_gbp="20"),
        ]
        import_events(self.database, events, NOW)

        result = snapshot(self.database, NOW)
        self.assertEqual(result["net_contributions_gbp"], "80")
        self.assertEqual(result["recorded_flow_events"], [
            {"id": "recorded-contribution", "type": "contribution",
             "account_id": "bank", "destination_account_id": None, "role": "unassigned",
             "amount_gbp": "100", "timestamp": iso(NOW), "available_at": iso(NOW),
             "evidence": "synthetic event evidence"},
            {"id": "recorded-transfer", "type": "transfer",
             "account_id": "bank", "destination_account_id": "broker", "role": "unassigned",
             "amount_gbp": "50", "timestamp": iso(NOW), "available_at": iso(NOW),
             "evidence": "synthetic event evidence"},
            {"id": "recorded-withdrawal", "type": "withdrawal",
             "account_id": "bank", "destination_account_id": None, "role": "unassigned",
             "amount_gbp": "20", "timestamp": iso(NOW), "available_at": iso(NOW),
             "evidence": "synthetic event evidence"},
        ])
        self.assertEqual(import_events(self.database, events[1:], NOW), result["revision"])
        self.assertEqual(snapshot(self.database, NOW), result)

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
        self.assertEqual(
            [point["twr_pct"] for point in result["valuation_history"]],
            ["0", "10", None, "10"],
        )

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
        with self.assertRaisesRegex(InvestmentError, "schema_version=2.*no migrations"):
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

    def test_unimplemented_review_events_fail_without_mutation(self):
        initialize(self.database, profile(), NOW)
        import_events(self.database, [opening()], NOW)
        before = self.database.read_bytes()
        with self.assertRaisesRegex(InvestmentError, "policy"):
            import_events(self.database, [{"id": "review", "type": "review_acknowledgement"}], NOW)
        self.assertEqual(self.database.read_bytes(), before)

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
        self.assertEqual([
            (item["account_id"], item["timestamp"], item["difference_gbp"])
            for item in result["cash_observations"]
        ], [("bank", iso(NOW), "-1"), ("broker", iso(NOW), "-1")])
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
        self.assertEqual([
            (item["timestamp"], item["difference_gbp"])
            for item in result["cash_observations"]
        ], [(iso(NOW), "-1"), (iso(later), "0")])
        self.assertEqual(result["cash_observations"][0]["evidence"],
                         mismatch["evidence"])
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
