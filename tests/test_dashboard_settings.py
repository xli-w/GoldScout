import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from dashboard.app import create_app
from proxy_config import (
    CONNECTION_NAMES, ProxyConfigurationError, configured_connection_values, connection_revision,
    connection_settings_enabled, read_connection_settings, update_connection_settings,
)
from test_dashboard_api import MemoryEventLoop


class DashboardSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.journal = root / "investment.sqlite3"
        self.preferences = root / ".goldscout-settings.json"
        self.connections = root / ".goldscout-connections.json"
        self.client = TestClient(create_app(
            serve_frontend=False, investment_database=self.journal,
            connections_path=self.connections), base_url="http://127.0.0.1",
            backend_options={"loop_factory": MemoryEventLoop})
        self.addCleanup(self.client.close)

    def get_settings(self):
        response = self.client.get("/api/v1/settings/session")
        self.assertEqual(response.status_code, 200)
        return response.json()

    @staticmethod
    def auth_headers(session):
        return {
            "Origin": "http://127.0.0.1",
            "X-CSRF-Token": session["csrf"],
        }

    def test_reads_do_not_create_settings_and_preferences_persist_with_revision(self):
        session = self.get_settings()
        self.assertFalse(self.preferences.exists())
        self.assertFalse(self.connections.exists())
        self.assertNotIn("secret", session)

        response = self.client.post("/api/v1/settings/preferences", json={
            "expected_revision": session["revision"],
            "preferences": {
                "compact_mode": True, "review_interval_days": 14,
                "savings_terms_interval_days": 180,
            },
        }, headers=self.auth_headers(session))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["revision"], 1)

        restarted = TestClient(create_app(
            serve_frontend=False, investment_database=self.journal,
            connections_path=self.connections), base_url="http://127.0.0.1",
            backend_options={"loop_factory": MemoryEventLoop})
        self.addCleanup(restarted.close)
        persisted = restarted.get("/api/v1/settings/session").json()
        self.assertEqual(persisted["preferences"], {
            "compact_mode": True, "review_interval_days": 14,
            "savings_terms_interval_days": 180,
        })

    def test_connection_secrets_are_write_only_and_updates_are_revisioned(self):
        session = self.get_settings()
        revision_before = connection_revision(
            "GOLDSCOUT_ETORO_API_KEY", "GOLDSCOUT_ETORO_USER_KEY",
            path=self.connections)
        secret = "synthetic-api-key-67890"
        response = self.client.post("/api/v1/settings/connections", json={
            "expected_revision": session["connection_revision"],
            "updates": {"GOLDSCOUT_ETORO_API_KEY": secret},
            "clear_names": [],
        }, headers=self.auth_headers(session))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(secret, response.text)
        self.assertTrue(response.json()["reauthorization_required"])
        self.assertTrue(response.json()["connection_overrides"]["GOLDSCOUT_ETORO_API_KEY"])
        self.assertEqual(
            configured_connection_values(
                "GOLDSCOUT_ETORO_API_KEY", connections_path=self.connections
            )["GOLDSCOUT_ETORO_API_KEY"], secret)
        self.assertNotEqual(revision_before, connection_revision(
            "GOLDSCOUT_ETORO_API_KEY", "GOLDSCOUT_ETORO_USER_KEY",
            path=self.connections))
        self.assertNotIn(secret, self.get_settings().__repr__())

        updated_session = self.get_settings()
        second_secret = "synthetic-user-key-12345"
        replacement = self.client.post("/api/v1/settings/connections", json={
            "expected_revision": updated_session["connection_revision"],
            "updates": {"GOLDSCOUT_ETORO_USER_KEY": second_secret},
            "clear_names": [],
        }, headers=self.auth_headers(updated_session))
        self.assertEqual(replacement.status_code, 200)
        self.assertNotIn(second_secret, replacement.text)

    def test_preferences_require_csrf_origin_and_current_revision(self):
        session = self.get_settings()
        payload = {
            "expected_revision": session["revision"],
            "preferences": {
                "compact_mode": True, "review_interval_days": 7,
                "savings_terms_interval_days": 90,
            },
        }
        self.assertEqual(self.client.post(
            "/api/v1/settings/preferences", json=payload,
            headers={"Origin": "http://127.0.0.1"}).status_code, 403)
        self.assertEqual(self.client.post(
            "/api/v1/settings/preferences", json=payload,
            headers={"X-CSRF-Token": session["csrf"],
                     "Origin": "https://attacker.invalid"}).status_code, 403)
        first = self.client.post(
            "/api/v1/settings/preferences", json=payload,
            headers=self.auth_headers(session))
        self.assertEqual(first.status_code, 200)
        stale = self.client.post(
            "/api/v1/settings/preferences", json=payload,
            headers=self.auth_headers(session))
        self.assertEqual(stale.status_code, 409)

    def test_invalid_or_malformed_settings_fail_closed(self):
        session = self.get_settings()
        invalid = self.client.post("/api/v1/settings/preferences", json={
            "expected_revision": session["revision"],
            "preferences": {
                "compact_mode": True, "review_interval_days": 0,
                "savings_terms_interval_days": 90,
            },
        }, headers=self.auth_headers(session))
        self.assertEqual(invalid.status_code, 400)
        self.preferences.write_text("{", encoding="utf-8")
        unavailable = self.client.get("/api/v1/settings/session")
        self.assertEqual(unavailable.status_code, 503)

    def test_connection_updates_reject_stale_revisions(self):
        first_session = self.get_settings()
        other_client = TestClient(create_app(
            serve_frontend=False, investment_database=self.journal,
            connections_path=self.connections), base_url="http://127.0.0.1",
            backend_options={"loop_factory": MemoryEventLoop})
        self.addCleanup(other_client.close)
        other_session = other_client.get("/api/v1/settings/session").json()
        update = {
            "expected_revision": first_session["connection_revision"],
            "updates": {"TRADER_AI_OPENAI_KEY": "synthetic-openai-key"},
            "clear_names": [],
        }
        self.assertEqual(self.client.post(
            "/api/v1/settings/connections", json=update,
            headers=self.auth_headers(first_session)).status_code, 200)
        update["expected_revision"] = other_session["connection_revision"]
        stale = other_client.post(
            "/api/v1/settings/connections", json=update,
            headers=self.auth_headers(other_session))
        self.assertEqual(stale.status_code, 409)

    def test_dns_override_is_validated_and_large_credential_revision_is_exact(self):
        session = self.get_settings()
        response = self.client.post("/api/v1/settings/connections", json={
            "expected_revision": session["connection_revision"],
            "updates": {"TRADER_DNS_IPV4": "8.8.8.8"},
            "clear_names": [],
        }, headers=self.auth_headers(session))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(configured_connection_values(
            "TRADER_DNS_IPV4", connections_path=self.connections)["TRADER_DNS_IPV4"], "8.8.8.8")

        updated = self.get_settings()
        invalid = self.client.post("/api/v1/settings/connections", json={
            "expected_revision": updated["connection_revision"],
            "updates": {"TRADER_DNS_IPV4": "not-an-ip"},
            "clear_names": [],
        }, headers=self.auth_headers(updated))
        self.assertEqual(invalid.status_code, 400)

        with patch("proxy_config.connection_revision", return_value=2**53 + 1):
            exact = self.client.get("/api/v1/settings/session")
        self.assertEqual(exact.status_code, 200)
        self.assertEqual(exact.json()["etoro_credential_revision"], str(2**53 + 1))

    def test_existing_connection_file_without_dns_revision_remains_usable(self):
        names = set(CONNECTION_NAMES) - {"TRADER_DNS_IPV4"}
        legacy = {
            "schema_version": 1,
            "revision": 4,
            "values": {"TRADER_AI_OPENAI_KEY": "synthetic-openai-key"},
            "versions": {name: 2 for name in names},
        }
        self.connections.write_text(json.dumps(legacy), encoding="utf-8")

        loaded = read_connection_settings(self.connections)
        self.assertEqual(loaded["versions"]["TRADER_DNS_IPV4"], 0)
        self.assertEqual(configured_connection_values(
            "TRADER_AI_OPENAI_KEY", connections_path=self.connections
        )["TRADER_AI_OPENAI_KEY"], "synthetic-openai-key")
        updated = update_connection_settings(
            {"TRADER_DNS_IPV4": "8.8.8.8"},
            expected_revision=4, path=self.connections)
        self.assertEqual(updated["revision"], 5)
        self.assertEqual(updated["values"]["TRADER_AI_OPENAI_KEY"], "synthetic-openai-key")
        persisted = read_connection_settings(self.connections)
        self.assertEqual(persisted["values"]["TRADER_DNS_IPV4"], "8.8.8.8")
        self.assertEqual(persisted["values"]["TRADER_AI_OPENAI_KEY"], "synthetic-openai-key")

    def test_disabled_private_sources_are_not_read_or_overwritten(self):
        stored = b'{"synthetic":"private"}'
        self.connections.write_bytes(stored)
        with patch.dict("os.environ", {"PYTHON_DOTENV_DISABLED": "1"}), \
                patch("proxy_config.CONNECTIONS_PATH", self.connections):
            self.assertFalse(connection_settings_enabled())
            self.assertEqual(read_connection_settings()["values"], {})
            with self.assertRaises(ProxyConfigurationError):
                update_connection_settings(
                    {"GOLDSCOUT_ETORO_API_KEY": "replacement"},
                    expected_revision=0)
        self.assertEqual(self.connections.read_bytes(), stored)


if __name__ == "__main__":
    unittest.main()
