import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from scripts.offline_tests import offline_environment
from scripts.validate_ai_setup import validate
from scripts.validate_ai_setup import ROOT


class CustomizationTests(unittest.TestCase):
    def test_repository_customization_is_valid(self):
        self.assertEqual(validate(), [])

    def test_disabled_hook_and_echo_only_ci_are_rejected(self):
        import shutil

        with tempfile.TemporaryDirectory() as directory:
            temporary_root = Path(directory)
            shutil.copytree(ROOT / ".github", temporary_root / ".github")
            shutil.copytree(ROOT / "docs", temporary_root / "docs")
            shutil.copytree(ROOT / "scripts", temporary_root / "scripts")
            hook = temporary_root / ".github/hooks/safety.json"
            hook.write_text('{"hooks":{"PreToolUse":[{"type":"command","windows":"echo disabled"}]}}', encoding="utf-8")
            workflow = temporary_root / ".github/workflows/offline-validation.yml"
            workflow.write_text('jobs:\n  offline:\n    steps:\n      - run: echo scripts/offline_tests.py\n', encoding="utf-8")
            errors = validate(temporary_root)
            self.assertIn("Safety hook must invoke the existing agent_guard.ps1 script", errors)
            self.assertIn("CI must execute the isolated offline runner", errors)

    def test_skipped_and_non_gating_ci_runner_are_rejected(self):
        import shutil

        with tempfile.TemporaryDirectory() as directory:
            temporary_root = Path(directory)
            shutil.copytree(ROOT / ".github", temporary_root / ".github")
            workflow = temporary_root / ".github/workflows/offline-validation.yml"
            for condition in ("if: false", "continue-on-error: true"):
                workflow.write_text(
                    'jobs:\n  offline:\n    steps:\n      - run: python scripts/offline_tests.py\n'
                    f'        {condition}\n', encoding="utf-8",
                )
                self.assertIn("CI regression execution must be unconditional and failure-gating", validate(temporary_root))


class OfflineEnvironmentTests(unittest.TestCase):
    def test_network_connections_and_dns_are_refused(self):
        with offline_environment():
            for operation, arguments in (
                (socket.create_connection, (("example.invalid", 443),)),
                (socket.getaddrinfo, ("example.invalid", 443)),
                (socket.gethostbyname, ("example.invalid",)),
                (socket.gethostbyname_ex, ("example.invalid",)),
                (socket.gethostbyaddr, ("127.0.0.1",)),
                (socket.getnameinfo, (("127.0.0.1", 443), 0)),
            ):
                with self.assertRaisesRegex(RuntimeError, "Network access is disabled"):
                    operation(*arguments)
            with socket.socket() as connection:
                with self.assertRaises(RuntimeError):
                    connection.connect(("127.0.0.1", 443))
                with self.assertRaises(RuntimeError):
                    connection.connect_ex(("127.0.0.1", 443))
                for operation, arguments in (
                    (connection.send, (b"test",)),
                    (connection.sendall, (b"test",)),
                    (connection.sendto, (b"test", ("127.0.0.1", 443))),
                    (connection.bind, (("127.0.0.1", 0),)),
                    (connection.listen, ()),
                    (connection.accept, ()),
                ):
                    with self.assertRaises(RuntimeError):
                        operation(*arguments)

    def test_credentials_and_temporary_workspace_are_isolated_and_restored(self):
        with offline_environment():
            self._assert_isolation_and_restoration()

    def test_proxy_credentials_are_isolated_and_restored(self):
        import proxy_config

        proxy = "http://sample:secret@proxy.invalid:8080"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.txt"
            path.write_text(f"TRADER_PROXY_URL={proxy}\n", encoding="utf-8")
            with patch.dict(os.environ, {"TRADER_PROXY_URL": proxy}), \
                    patch.object(proxy_config, "DOTENV_PATH", path), \
                    patch.object(proxy_config, "_dotenv_loading_disabled", return_value=False), \
                    patch.object(proxy_config, "_read_proxy_dotenv",
                                 side_effect=AssertionError("dotenv reader was not blocked")) as reader:
                with offline_environment():
                    self.assertEqual(os.environ["TRADER_PROXY_URL"], "")
                    os.environ.pop("TRADER_PROXY_URL")
                    self.assertEqual(proxy_config.configured_proxy_url(), "")
                reader.assert_not_called()
                self.assertEqual(os.environ["TRADER_PROXY_URL"], proxy)

    def _assert_isolation_and_restoration(self):
        credentials = {
            "ALPACA_API_KEY": "test-sentinel",
            "CONFIRM_LIVE_TRADING": "yes",
            "GOLDSCOUT_ETORO_API_KEY": "synthetic-api-key",
            "GOLDSCOUT_ETORO_USER_KEY": "synthetic-user-key",
            "TRADER_AI_SYNTHETIC": "synthetic-ai-key",
        }
        with patch.dict(os.environ, credentials):
            with offline_environment() as directory:
                for name in credentials:
                    self.assertFalse(os.environ.get(name))
                self.assertTrue(directory.exists())
            self.assertFalse(directory.exists())
            for name, value in credentials.items():
                self.assertEqual(os.environ[name], value)


if __name__ == "__main__":
    unittest.main()