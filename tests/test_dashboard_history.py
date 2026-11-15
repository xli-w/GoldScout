import base64
import json
import tempfile
import unittest
from pathlib import Path

from bot.ledger import Ledger
from dashboard.history import HistoryReader
from dashboard.reader import ReadError


class DashboardHistoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "ledger.sqlite3"
        self.ledger = Ledger(self.path)
        self.ledger.bind("synthetic-account", "paper")
        self.ledger.commit({"positions": {}, "daily": {"day": "2026-11-13", "realized": 0}})
        self.reader = HistoryReader(self.path)

    def test_event_cursor_is_bound_to_all_journal_changes(self):
        self.ledger.record("synthetic_event", {"api_key": "synthetic-forbidden", "symbol": "SPY"})
        page = self.reader.page("events", limit=1)
        self.assertNotIn("synthetic-forbidden", json.dumps(page))
        cursor = page["data"]["next_cursor"]
        self.assertIsNotNone(cursor)
        next_page = self.reader.page("events", limit=1, cursor=cursor)
        self.assertGreater(next_page["data"]["items"][0]["sequence"], page["data"]["items"][0]["sequence"])
        self.ledger.record("new_observation", {})
        with self.assertRaises(ReadError) as result:
            self.reader.page("events", limit=1, cursor=cursor)
        self.assertEqual(result.exception.code, "snapshot_changed")

    def test_order_missing_fill_quantity_is_not_zero(self):
        self.ledger.commit({"positions": {}}, order={"client_order_id": "synthetic-client", "symbol": "SPY", "side": "buy", "qty": "6"})
        order = self.reader.page("orders")["data"]["items"][0]
        self.assertIsNone(order["cumulative_filled_quantity"])
        self.assertEqual(order["requested_quantity"], "6")

    def test_corrections_do_not_modify_original_exit_or_double_count_daily(self):
        state = {"positions": {}, "daily": {"day": "2026-11-13", "realized": 0}}
        event = {"execution_id": "synthetic-entry:1", "instrument": "SPY", "direction": "long", "entry_price": 100,
                 "position_size": 4, "exit_price": None, "price_verified": False, "profit_loss": None, "accounting_date": "2026-11-12"}
        self.ledger.commit(state, event, [{"date": "2026-11-12", "realized_pnl": 0}])
        self.ledger.ingest_activity({"id": "synthetic-evidence"})
        self.ledger.apply_correction("synthetic-entry:1", "synthetic-correction", "synthetic-evidence", "102", "0.5", "Synthetic verified evidence")
        page = self.reader.page("performance", start="2026-11-12", end="2026-11-12")["data"]
        self.assertIsNone(page["exits"][0]["exit_price"])
        self.assertEqual(page["daily"][0]["realized_pnl"], "7.5")
        self.assertTrue(page["daily"][0]["includes_corrections"])
        self.assertEqual(page["corrections"][0]["fees"], "0.5")

    def test_invalid_filters_and_cursors_are_rejected(self):
        for kwargs in ({"limit": 0}, {"cursor": "bad"}, {"start": "2026-02-30"}, {"start": "2026-11-13", "end": "2026-11-12"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ReadError) as result:
                self.reader.page("performance", **kwargs)
            self.assertEqual(result.exception.code, "invalid_request")

    def test_oversized_and_boolean_offsets_are_invalid_requests(self):
        for kwargs in ({"after": 1 << 70}, {"after": True}, {"limit": True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ReadError) as result:
                self.reader.page("events", **kwargs)
            self.assertEqual(result.exception.code, "invalid_request")
        token = {"resource": "events", "filters": [None, None, None], "limit": 100, "last": [1 << 70, 0, 0]}
        cursor = base64.urlsafe_b64encode(json.dumps(token).encode()).decode()
        with self.assertRaises(ReadError) as result:
            self.reader.page("events", cursor=cursor)
        self.assertEqual(result.exception.code, "invalid_request")

    def test_demo_cursor_marker_must_be_boolean_true(self):
        from dashboard.history import demo_page
        token = {"demo": "yes", "resource": "orders", "filters": [], "limit": 1, "last": [0, 0, 0]}
        cursor = base64.urlsafe_b64encode(json.dumps(token).encode()).decode()
        with self.assertRaises(ReadError) as result:
            demo_page({"items": []}, "orders", 1, cursor, [])
        self.assertEqual(result.exception.code, "invalid_request")

    def test_cursor_page_size_cannot_be_boolean_in_either_mode(self):
        from dashboard.history import demo_page
        for demo in (False, True):
            filters = [] if demo else [None, None, None]
            token = {"demo": demo, "resource": "orders", "filters": filters, "limit": True, "last": [0, 0, 0]}
            cursor = base64.urlsafe_b64encode(json.dumps(token).encode()).decode()
            with self.subTest(demo=demo), self.assertRaises(ReadError) as result:
                if demo:
                    demo_page({"items": []}, "orders", 1, cursor, filters)
                else:
                    self.reader.page("orders", limit=1, cursor=cursor)
            self.assertEqual(result.exception.code, "invalid_request")

    def test_legacy_blank_values_flags_and_known_zero_are_preserved(self):
        row = {"_ledger_id": "legacy-row-1", "instrument": "SPY", "position_size": "4", "exit_price": "102", "price_verified": "True", "fees": 0, "fees_estimated": "False", "gross_profit_loss": "", "profit_loss": "8"}
        projected = self.reader.exit(row)
        self.assertTrue(projected["price_verified"])
        self.assertFalse(projected["fees_estimated"])
        self.assertEqual(projected["fees"], "0")
        self.assertIsNone(projected["gross_pnl"])
        self.assertIsNone(projected["accounting_day"])

    def test_legacy_timezone_is_not_guessed_and_zero_exit_quantity_is_invalid(self):
        row = {"_ledger_id": "legacy-row", "instrument": "SPY", "position_size": "4", "timestamp": "2020-01-01T12:00:00", "exit_price": "102", "fees": "-0.00"}
        projected = self.reader.exit(row)
        self.assertIsNone(projected["fill_at"])
        self.assertEqual(projected["fees"], "0.00")
        row["position_size"] = "0"
        with self.assertRaises(ReadError):
            self.reader.exit(row)

    def test_invalid_order_quantities_are_not_reported_as_normal(self):
        for quantity, filled in (("-1", "0"), ("4", "5"), ("4", "-1")):
            with self.subTest(quantity=quantity, filled=filled), self.assertRaises(ReadError):
                self.reader.order({"client_order_id": "synthetic", "symbol": "SPY", "side": "buy", "qty": quantity, "filled_qty": filled})
        self.assertEqual(self.reader.order({"client_order_id": "synthetic", "symbol": "SPY", "side": "buy"})["status"], "unknown")

    def test_correction_decimals_are_fixed_point(self):
        correction = self.reader.correction({"correction_id": "synthetic", "execution_id": "synthetic:1", "activity_id": "synthetic-evidence", "exit_price": "1E+2", "fees": "1E-7", "reason": "Synthetic"})
        self.assertEqual(correction["exit_price"], "100")
        self.assertEqual(correction["fees"], "0.0000001")

    def test_unflagged_unknown_exit_makes_daily_projection_incomplete(self):
        self.ledger.commit({"positions": {}}, {"execution_id": "synthetic:1", "instrument": "SPY", "position_size": 4, "exit_price": None, "accounting_date": "2026-11-12"}, [{"date": "2026-11-12", "realized_pnl": 0}])
        row = self.reader.page("performance")["data"]["daily"][0]
        self.assertFalse(row["accounting_complete"])
        self.assertIsNone(row["realized_pnl"])

    def test_blank_legacy_verification_flag_makes_daily_projection_incomplete(self):
        self.ledger.commit({"positions": {}}, {"execution_id": "synthetic-blank:1", "instrument": "SPY", "position_size": "4", "exit_price": "", "price_verified": "", "accounting_date": "2026-11-12"}, [{"date": "2026-11-12", "realized_pnl": 0}])
        page = self.reader.page("performance")["data"]
        self.assertFalse(page["exits"][0]["price_verified"])
        self.assertFalse(page["daily"][0]["accounting_complete"])
        self.assertIsNone(page["daily"][0]["realized_pnl"])

    def test_demo_pages_do_not_discard_unequal_collections(self):
        from dashboard.history import demo_page
        original = {"daily": [1, 2, 3], "exits": [1, 2], "corrections": [1], "next_cursor": None}
        cursor, days, exits = None, [], []
        for _ in range(3):
            page = demo_page(json.loads(json.dumps(original)), "performance", 1, cursor, [])
            days.extend(page["daily"])
            exits.extend(page["exits"])
            cursor = page["next_cursor"]
        self.assertEqual(days, [1, 2, 3])
        self.assertEqual(exits, [1, 2])
        self.assertIsNone(cursor)


if __name__ == "__main__":
    unittest.main()