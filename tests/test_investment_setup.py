"""First-run setup and read-only handoff, using temporary stores."""

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
from test_investment_opening import NOW, opening, profile


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
        with self.client() as client:
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
        with self.client() as restarted:
            headers = self.session_headers(restarted)
            session = restarted.get("/api/investment/v1/setup/session").json()
            self.assertTrue(session["recovery"]["requires_opening"])
            preview = self.preview(restarted, headers)
            response = restarted.post("/api/investment/v1/setup/confirm", headers=headers,
                                      json={"preview_id": preview["preview_id"]})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["status"], "completed")

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
            with self.client() as restarted:
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

        with self.client() as client:
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
