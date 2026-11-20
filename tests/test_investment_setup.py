"""First-run setup and permission-bound recording handoff, using temporary stores."""

import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from dashboard.app import create_app
from investment.store import read
from investment import InvestmentError, bootstrap
from test_dashboard_api import MemoryEventLoop
from test_investment_opening import NOW, event, opening, profile


class InvestmentSetupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name) / "private"
        self.database = self.workspace / "investment.sqlite3"
        self.now = NOW + timedelta(minutes=1)
    def client(self, code=True, **options):
        arguments = {
            "serve_frontend": False, "clock": lambda: self.now,
            "investment_database": self.database, "setup_workspace": self.workspace,
            "setup_enabled": code,
        }
        arguments.update(options)
        return TestClient(
            create_app(**arguments), base_url="http://127.0.0.1",
            backend_options={"loop_factory": MemoryEventLoop})

    def session_headers(self, client):
        response = client.get("/api/investment/v1/setup/session")
        self.assertEqual(response.status_code, 200, response.text)
        session = response.json()
        self.assertTrue(session["enabled"])
        self.assertNotIn("authenticated", session)
        return {"Origin": "http://127.0.0.1", "X-CSRF-Token": session["csrf"]}

    def preview(self, client, headers, spec=None, value=None, request_id="first-preview"):
        response = client.post("/api/investment/v1/setup/preview", headers=headers, json={
            "request_id": request_id,
            "profile": profile() if spec is None else spec,
            "opening": opening() if value is None else value,
        })
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def assert_recording_blocked(self, client):
        self.assertFalse(client.get("/api/investment/v1/recording/session").json()["enabled"])
        for route in ("preview", "confirm"):
            self.assertEqual(client.post(
                f"/api/investment/v1/recording/{route}", json={},
                headers={"Origin": "http://127.0.0.1"}).status_code, 405)

    def assert_recording_works(self, client):
        session = client.get("/api/investment/v1/recording/session").json()
        self.assertTrue(session["enabled"])
        headers = {"Origin": "http://127.0.0.1", "X-CSRF-Token": session["csrf"]}
        value = event("after-setup", "contribution", self.now, "bank", amount_gbp="25")
        response = client.post("/api/investment/v1/recording/preview", headers=headers,
                               json={"request_id": "after-setup", "event": value})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["revision"], 1)
        confirmation = {"preview_id": response.json()["preview_id"]}
        response = client.post("/api/investment/v1/recording/confirm", headers=headers,
                               json=confirmation)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["revision"], 2)
        self.assertEqual(client.post("/api/investment/v1/recording/confirm",
                                    headers=headers, json=confirmation).json(), response.json())
        self.assertEqual(read(self.database, self.now).revision, 2)

    def test_completed_setup_enables_recording_without_restart_only_when_allowed(self):
        with self.client(investment_recording_enabled=True) as client:
            self.assert_recording_blocked(client)
            headers = self.session_headers(client)
            preview = self.preview(client, headers)
            self.assert_recording_blocked(client)
            self.assertFalse(self.workspace.exists())
            confirmation = {"preview_id": preview["preview_id"]}
            response = client.post("/api/investment/v1/setup/confirm", headers=headers,
                                   json=confirmation)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["status"], "completed")
            self.assertEqual(client.post("/api/investment/v1/setup/confirm", headers=headers,
                                        json=confirmation).json(), response.json())
            # Setup CSRF cannot authorize a journal append.
            self.assertEqual(client.post("/api/investment/v1/recording/preview",
                                        headers=headers, json={}).status_code, 403)
            self.assert_recording_works(client)

    def test_read_only_launch_permissions_block_setup_and_recording_without_creating_files(self):
        with self.client(code=False) as client:
            session = client.get("/api/investment/v1/setup/session").json()
            self.assertEqual(session["state"], "empty")
            self.assertFalse(session["enabled"])
            self.assertIsNone(session["csrf"])
            for route in ("preview", "recovery-preview", "confirm"):
                self.assertEqual(client.post(
                    f"/api/investment/v1/setup/{route}", json={},
                    headers={"Origin": "http://127.0.0.1"}).status_code, 405)
            self.assert_recording_blocked(client)
            self.assertEqual(client.get("/api/investment/v1/snapshot").status_code, 503)
        self.assertFalse(self.workspace.exists())

    def test_failed_recording_handoff_preserves_completed_setup_receipt(self):
        with self.client(investment_recording_enabled=True) as client:
            headers = self.session_headers(client)
            preview = self.preview(client, headers)
            with patch("dashboard.investment_recording.RecordingSessions",
                       side_effect=InvestmentError("Synthetic handoff failure")):
                response = client.post("/api/investment/v1/setup/confirm", headers=headers,
                                       json={"preview_id": preview["preview_id"]})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["status"], "completed")
            self.assertTrue(response.json()["committed"])
            self.assertIn("recording is unavailable", response.json()["message"])
            self.assert_recording_blocked(client)
            self.assertEqual(read(self.database, self.now).revision, 1)

    def test_partial_setup_stays_blocked_until_same_process_confirmed_recovery(self):
        original_write = bootstrap._write_new

        def fail_receipt(path, value):
            if path.name == bootstrap.RECEIPT:
                raise OSError("Synthetic receipt failure")
            return original_write(path, value)

        with self.client(investment_recording_enabled=True) as client:
            headers = self.session_headers(client)
            preview = self.preview(client, headers)
            confirmation = {"preview_id": preview["preview_id"]}
            with patch("investment.bootstrap._write_new", side_effect=fail_receipt):
                response = client.post("/api/investment/v1/setup/confirm", headers=headers,
                                       json=confirmation)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["status"], "partial")
            self.assertTrue(response.json()["committed"])
            journal = self.database.read_bytes()
            self.assert_recording_blocked(client)
            self.assertEqual(client.get("/api/v1/settings").json()["workspace"], "setup")
            self.assertEqual(client.get("/api/investment/v1/setup/session").json()["state"], "recoverable")
            # Retrying the uncertain/partial receipt is not a completion.
            self.assertEqual(client.post("/api/investment/v1/setup/confirm", headers=headers,
                                        json=confirmation).json(), response.json())
            self.assert_recording_blocked(client)
            recovered = client.post("/api/investment/v1/setup/recovery-preview",
                                    headers=headers, json={})
            self.assertEqual(recovered.status_code, 200, recovered.text)
            self.assert_recording_blocked(client)
            self.assertEqual(self.database.read_bytes(), journal)
            response = client.post("/api/investment/v1/setup/confirm", headers=headers,
                                   json={"preview_id": recovered.json()["preview_id"]})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["status"], "completed")
            self.assertEqual(self.database.read_bytes(), journal)
            self.assert_recording_works(client)

    def test_reads_cannot_enable_recording_after_partial_result_even_if_workspace_is_ready(self):
        original_confirm = bootstrap.confirm_setup

        def uncertain_completion(*args):
            result = original_confirm(*args)
            self.assertEqual(result["status"], "completed")
            return {**result, "status": "partial", "receipt": None,
                    "message": "Synthetic uncertain completion"}

        with self.client(investment_recording_enabled=True) as client:
            headers = self.session_headers(client)
            preview = self.preview(client, headers)
            with patch("dashboard.investment_setup.confirm_setup", side_effect=uncertain_completion):
                response = client.post("/api/investment/v1/setup/confirm", headers=headers,
                                       json={"preview_id": preview["preview_id"]})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["status"], "partial")
            original = self.database.read_bytes()
            self.assertEqual(client.get("/api/v1/settings").json()["workspace"], "investment")
            self.assertEqual(client.get("/api/investment/v1/setup/session").json()["state"], "ready")
            self.assertEqual(client.get("/api/investment/v1/snapshot").status_code, 200)
            self.assert_recording_blocked(client)
            self.assertEqual(self.database.read_bytes(), original)

    def test_read_only_recoverable_workspace_reads_and_rejected_writes_preserve_artifacts(self):
        original_write = bootstrap._write_new

        def fail_receipt(path, value):
            if path.name == bootstrap.RECEIPT:
                raise OSError("Synthetic receipt failure")
            return original_write(path, value)

        with self.client() as client:
            headers = self.session_headers(client)
            preview = self.preview(client, headers)
            with patch("investment.bootstrap._write_new", side_effect=fail_receipt):
                result = client.post("/api/investment/v1/setup/confirm", headers=headers,
                                     json={"preview_id": preview["preview_id"]})
            self.assertEqual(result.json()["status"], "partial")
        original = {path.relative_to(self.workspace): path.read_bytes()
                    for path in self.workspace.rglob("*") if path.is_file()}
        with self.client(code=False) as client:
            session = client.get("/api/investment/v1/setup/session").json()
            self.assertEqual(session["state"], "recoverable")
            self.assertFalse(session["enabled"])
            self.assertEqual(client.get("/api/v1/settings").json()["workspace"], "setup")
            self.assertEqual(client.get("/api/investment/v1/snapshot").status_code, 200)
            for route in ("preview", "recovery-preview", "confirm"):
                self.assertEqual(client.post(
                    f"/api/investment/v1/setup/{route}", json={},
                    headers={"Origin": "http://127.0.0.1"}).status_code, 405)
            self.assert_recording_blocked(client)
        self.assertEqual({path.relative_to(self.workspace): path.read_bytes()
                          for path in self.workspace.rglob("*") if path.is_file()}, original)

    def test_blocked_workspace_cannot_initialize_recording_or_repair_files(self):
        self.workspace.mkdir()
        collision = self.workspace / "profile.json"
        collision.write_text("synthetic unrelated file", encoding="utf-8")
        with self.client(investment_recording_enabled=True) as client:
            self.assertEqual(client.get("/api/investment/v1/setup/session").json()["state"], "blocked")
            self.assert_recording_blocked(client)
        self.assertEqual(collision.read_text(encoding="utf-8"), "synthetic unrelated file")
        self.assertFalse(self.database.exists())

    def test_unopened_workspace_is_not_adopted_as_completed_setup(self):
        from investment.store import initialize

        self.workspace.mkdir()
        initialize(self.database, profile(), self.now)
        original = self.database.read_bytes()
        with self.client(investment_recording_enabled=True) as client:
            session = client.get("/api/investment/v1/setup/session").json()
            self.assertEqual(session["state"], "manual_unopened")
            self.assertFalse(session["enabled"])
            self.assert_recording_blocked(client)
        self.assertEqual(self.database.read_bytes(), original)

    def test_completed_setup_cannot_handoff_to_a_different_profile(self):
        from investment.store import initialize, import_events

        original_confirm = bootstrap.confirm_setup
        replacement = Path(self.temporary.name) / "replacement.sqlite3"
        changed_profile = profile()
        changed_profile["portfolio_id"] = "different-portfolio"
        initialize(replacement, changed_profile, self.now)
        import_events(replacement, [opening()], self.now)

        def replaced_completion(*args):
            result = original_confirm(*args)
            self.assertEqual(result["status"], "completed")
            self.database.rename(Path(self.temporary.name) / "preserved.sqlite3")
            replacement.rename(self.database)
            return result

        with self.client(investment_recording_enabled=True) as client:
            headers = self.session_headers(client)
            preview = self.preview(client, headers)
            with patch("dashboard.investment_setup.confirm_setup", side_effect=replaced_completion):
                response = client.post("/api/investment/v1/setup/confirm", headers=headers,
                                       json={"preview_id": preview["preview_id"]})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["status"], "completed")
            self.assertTrue(response.json()["committed"])
            self.assertIn("recording is unavailable", response.json()["message"])
            self.assert_recording_blocked(client)

    def test_completed_setup_rejects_same_profile_journal_replacement_during_handoff(self):
        from dashboard.investment_recording import RecordingSessions
        from investment.store import initialize, import_events

        original_confirm = bootstrap.confirm_setup
        replacement = Path(self.temporary.name) / "replacement.sqlite3"
        preserved = Path(self.temporary.name) / "preserved.sqlite3"
        changed_opening = opening()
        changed_opening["id"] = "different-opening"
        initialize(replacement, profile(), self.now)
        import_events(replacement, [changed_opening], self.now)
        completed = {}

        def remember_completion(*args):
            result = original_confirm(*args)
            self.assertEqual(result["status"], "completed")
            completed.update(copy.deepcopy(result))
            return result

        def replace_before_capture(*args):
            # The workspace was ready when checked, but the constructor now
            # captures another valid journal with the very same profile hash.
            self.database.rename(preserved)
            replacement.rename(self.database)
            return RecordingSessions(*args)

        with self.client(investment_recording_enabled=True) as client:
            headers = self.session_headers(client)
            preview = self.preview(client, headers)
            with patch("dashboard.investment_setup.confirm_setup", side_effect=remember_completion), \
                    patch("dashboard.investment_recording.RecordingSessions",
                          side_effect=replace_before_capture):
                response = client.post("/api/investment/v1/setup/confirm", headers=headers,
                                       json={"preview_id": preview["preview_id"]})
            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()
            self.assertEqual(result["status"], "completed")
            self.assertTrue(result["committed"])
            self.assertEqual(result["receipt"], completed["receipt"])
            self.assertEqual(result["revision"], completed["revision"])
            self.assertIn("recording is unavailable", result["message"])
            self.assert_recording_blocked(client)
        self.assertEqual(read(preserved, self.now).revision, 1)
        self.assertEqual(read(self.database, self.now).revision, 1)
        self.assertIn("different-opening", read(self.database, self.now).ids)

    def test_setup_read_preview_and_abandon_create_no_files(self):
        with self.client() as client:
            self.assertEqual(client.get("/api/v1/settings").json()["workspace"], "setup")
            self.assertEqual(client.get("/api/investment/v1/snapshot").status_code, 503)
            self.assertEqual(client.get("/api/v1/overview").status_code, 404)
            headers = self.session_headers(client)
            preview = self.preview(client, headers)
            self.assertTrue(preview["report"]["complete"])
            self.assertEqual(preview["opening"], opening())
            self.assertFalse(self.workspace.exists())
        self.assertFalse(self.workspace.exists())

    def test_confirmation_is_idempotent_and_does_not_authorize_later_recording(self):
        with self.client() as client:
            headers = self.session_headers(client)
            preview = self.preview(client, headers)
            confirmation = {"preview_id": preview["preview_id"]}
            response = client.post(
                "/api/investment/v1/setup/confirm", json=confirmation, headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["status"], "completed")
            original = self.database.read_bytes()
            repeated = client.post(
                "/api/investment/v1/setup/confirm", json=confirmation, headers=headers)
            self.assertEqual(repeated.status_code, 200, repeated.text)
            self.assertEqual(repeated.json(), response.json())
            self.assertEqual(self.database.read_bytes(), original)
            self.assertEqual(client.get("/api/v1/settings").json()["workspace"], "investment")
            self.assertEqual(client.get("/api/investment/v1/snapshot").status_code, 200)
            self.assertFalse(client.get("/api/investment/v1/recording/session").json()["enabled"])
            self.assertEqual(client.post(
                "/api/investment/v1/recording/confirm", json={}, headers=headers).status_code, 405)
            self.assertFalse(client.get("/api/investment/v1/setup/session").json()["enabled"])
        self.assertEqual(read(self.database, self.now).revision, 1)
        with self.client(code=False) as restarted:
            self.assertEqual(restarted.get("/api/v1/settings").json()["workspace"], "investment")
            self.assertFalse(restarted.get("/api/investment/v1/setup/session").json()["enabled"])

    def test_opt_in_origin_csrf_and_mutation_scope_fail_closed(self):
        with self.client() as client:
            session = client.get("/api/investment/v1/setup/session").json()
            path = "/api/investment/v1/setup/preview"
            body = {"request_id": "untrusted", "profile": profile(), "opening": opening()}
            for headers in ({}, {"Origin": "https://foreign.invalid"},
                            {"Origin": "http://127.0.0.1", "X-CSRF-Token": "invalid"}):
                with self.subTest(headers=headers):
                    self.assertIn(client.post(path, headers=headers, json=body).status_code,
                                  (403, 405))
            headers = {"Origin": "http://127.0.0.1", "X-CSRF-Token": session["csrf"]}
            headers = self.session_headers(client)
            self.assertEqual(client.post("/api/v1/orders", json={}, headers=headers).status_code, 405)
            self.assertEqual(client.post(
                path, headers={**headers, "Sec-Fetch-Site": "cross-site"},
                json=body).status_code, 403)
            self.assertEqual(client.post(
                "/api/investment/v1/setup/login", headers=headers,
                json={"code": "unused"}).status_code, 405)
        self.assertFalse(self.workspace.exists())

    def test_expired_preview_and_changed_workspace_cannot_commit(self):
        with self.client() as client:
            headers = self.session_headers(client)
            preview = self.preview(client, headers)
            self.now += timedelta(minutes=6)
            response = client.post("/api/investment/v1/setup/confirm", headers=headers,
                                   json={"preview_id": preview["preview_id"]})
            self.assertEqual(response.status_code, 409)
            self.assertFalse(self.workspace.exists())
            preview = self.preview(client, headers, request_id="second-preview")
            self.workspace.mkdir()
            collision = self.workspace / "profile.json"
            collision.write_text("unrelated operator evidence", encoding="utf-8")
            original = collision.read_bytes()
            response = client.post("/api/investment/v1/setup/confirm", headers=headers,
                                   json={"preview_id": preview["preview_id"]})
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(collision.read_bytes(), original)
            self.assertFalse(self.database.exists())

    def test_unknown_price_stays_unavailable_and_invalid_input_creates_nothing(self):
        with self.client() as client:
            headers = self.session_headers(client)
            value = copy.deepcopy(opening())
            value["holdings"][0]["price_gbp"] = None
            preview = self.preview(client, headers, value=value)
            self.assertFalse(preview["report"]["complete"])
            self.assertIsNone(preview["report"]["equity_gbp"])
            for replacement in ({}, {"bank": "NaN", "broker": "100"}):
                value["balances_gbp"] = replacement
                response = client.post("/api/investment/v1/setup/preview", headers=headers, json={
                    "request_id": "bad-" + str(len(replacement)), "profile": profile(),
                    "opening": value})
                self.assertEqual(response.status_code, 409, response.text)
            duplicate = '{"request_id":"one","request_id":"two","profile":{},"opening":{}}'
            response = client.post(
                "/api/investment/v1/setup/preview",
                headers={**headers, "Content-Type": "application/json"}, content=duplicate)
            self.assertIn(response.status_code, (400, 409))
        self.assertFalse(self.workspace.exists())

    def test_setup_cannot_target_another_store_or_appear_for_explicit_missing_selection(self):
        with self.assertRaises(ValueError):
            create_app(
                serve_frontend=False, setup_workspace=self.workspace,
                investment_database=self.workspace / "different.sqlite3", setup_enabled=True)
        with TestClient(create_app(
                serve_frontend=False, clock=lambda: self.now, investment_database=self.database),
                base_url="http://127.0.0.1",
                backend_options={"loop_factory": MemoryEventLoop}) as client:
            self.assertEqual(client.get("/api/v1/settings").json()["workspace"], "investment")
            self.assertFalse(client.get("/api/investment/v1/setup/session").json()["enabled"])
            self.assertEqual(client.post("/api/investment/v1/setup/confirm", json={}).status_code, 405)
        self.assertFalse(self.workspace.exists())

    def test_uncommitted_partial_response_can_be_reentered_after_restart(self):
        with self.client(investment_recording_enabled=True) as client:
            headers = self.session_headers(client)
            preview = self.preview(client, headers)
            with patch("investment.bootstrap.store.initialize",
                       side_effect=InvestmentError("Synthetic initialization failure")):
                response = client.post("/api/investment/v1/setup/confirm", headers=headers,
                                       json={"preview_id": preview["preview_id"]})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["status"], "partial")
            self.assertFalse(response.json()["committed"])
            self.assertIsNone(response.json()["revision"])
            self.assertFalse(self.database.exists())
            self.assert_recording_blocked(client)
        with self.client(investment_recording_enabled=True) as restarted:
            self.assert_recording_blocked(restarted)
            headers = self.session_headers(restarted)
            session = restarted.get("/api/investment/v1/setup/session").json()
            self.assertTrue(session["recovery"]["requires_opening"])
            preview = self.preview(restarted, headers)
            response = restarted.post("/api/investment/v1/setup/confirm", headers=headers,
                                      json={"preview_id": preview["preview_id"]})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["status"], "completed")
            self.assert_recording_works(restarted)

    def test_committed_ancillary_failures_offer_restart_recovery(self):
        original_write = bootstrap._write_new
        original_unlink = Path.unlink
        for failure in ("profile", "receipt", "cleanup"):
            self.workspace = Path(self.temporary.name) / failure
            self.database = self.workspace / "investment.sqlite3"

            def failing_write(path, value):
                if ((failure == "profile" and path.name == "profile.json")
                        or (failure == "receipt" and path.name == bootstrap.RECEIPT)):
                    raise OSError("Synthetic ancillary write failure")
                return original_write(path, value)

            def failing_unlink(path, *args, **kwargs):
                if failure == "cleanup" and path.name == bootstrap.STAGE:
                    raise OSError("Synthetic staging cleanup failure")
                return original_unlink(path, *args, **kwargs)

            with self.subTest(failure=failure), self.client() as client:
                headers = self.session_headers(client)
                preview = self.preview(client, headers)
                with patch("investment.bootstrap._write_new", side_effect=failing_write), \
                        patch.object(Path, "unlink", autospec=True, side_effect=failing_unlink):
                    response = client.post("/api/investment/v1/setup/confirm", headers=headers,
                                           json={"preview_id": preview["preview_id"]})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["status"], "partial")
                self.assertTrue(response.json()["committed"])
                self.assertEqual(response.json()["revision"], 1)
                journal = self.database.read_bytes()
                self.assertEqual(client.get("/api/v1/settings").json()["workspace"], "setup")
            with self.client(investment_recording_enabled=True) as restarted:
                self.assert_recording_blocked(restarted)
                public = restarted.get("/api/investment/v1/setup/session").json()
                self.assertEqual(public["state"], "recoverable")
                self.assertIn("recovery", public)
                headers = self.session_headers(restarted)
                recovered = restarted.post(
                    "/api/investment/v1/setup/recovery-preview", json={}, headers=headers)
                self.assertEqual(recovered.status_code, 200, recovered.text)
                response = restarted.post("/api/investment/v1/setup/confirm", headers=headers,
                                          json={"preview_id": recovered.json()["preview_id"]})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["status"], "completed")
                self.assertEqual(self.database.read_bytes(), journal)
                self.assertEqual(read(self.database, self.now).revision, 1)
                self.assertEqual(restarted.get("/api/v1/settings").json()["workspace"], "investment")
                self.assert_recording_works(restarted)

    def test_competing_server_instances_cannot_overwrite_the_winning_opening(self):
        with self.client() as first, self.client() as second:
            first_headers, second_headers = self.session_headers(first), self.session_headers(second)
            first_preview = self.preview(first, first_headers)
            competing = opening()
            competing["id"] = "different-opening"
            second_preview = self.preview(second, second_headers, value=competing)

            def confirm(client, headers, preview):
                return client.post("/api/investment/v1/setup/confirm", headers=headers,
                                   json={"preview_id": preview["preview_id"]})

            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [
                    executor.submit(confirm, first, first_headers, first_preview),
                    executor.submit(confirm, second, second_headers, second_preview),
                ]
                responses = [future.result(timeout=10) for future in futures]
            completed = [response for response in responses
                         if response.status_code == 200 and response.json()["status"] == "completed"]
            self.assertEqual(len(completed), 1, [response.text for response in responses])
            ledger = read(self.database, self.now)
            self.assertEqual(ledger.revision, 1)
            self.assertIn(next(iter(ledger.ids)), ("opening", "different-opening"))

    def test_published_journal_stays_committed_when_verification_becomes_unavailable(self):
        original_link, original_read = bootstrap.os.link, bootstrap._read
        published = False

        def publish(source, destination, *args, **kwargs):
            nonlocal published
            result = original_link(source, destination, *args, **kwargs)
            if Path(destination) == self.database:
                published = True
            return result

        def unavailable_after_publication(path, now):
            if published and Path(path) == self.database:
                raise InvestmentError("Synthetic temporary verification failure")
            return original_read(path, now)

        with self.client(investment_recording_enabled=True) as client:
            headers = self.session_headers(client)
            preview = self.preview(client, headers)
            with patch("investment.bootstrap.os.link", side_effect=publish), \
                    patch("investment.bootstrap._read", side_effect=unavailable_after_publication):
                response = client.post("/api/investment/v1/setup/confirm", headers=headers,
                                       json={"preview_id": preview["preview_id"]})
            self.assertTrue(published)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["status"], "partial")
            self.assertTrue(response.json()["committed"])
            self.assertEqual(response.json()["revision"], 1)
            journal = self.database.read_bytes()
            self.assert_recording_blocked(client)
        with self.client() as restarted:
            headers = self.session_headers(restarted)
            recovered = restarted.post(
                "/api/investment/v1/setup/recovery-preview", json={}, headers=headers)
            self.assertEqual(recovered.status_code, 200, recovered.text)
            response = restarted.post("/api/investment/v1/setup/confirm", headers=headers,
                                      json={"preview_id": recovered.json()["preview_id"]})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["status"], "completed")
            self.assertEqual(self.database.read_bytes(), journal)
