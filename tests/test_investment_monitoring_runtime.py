from copy import deepcopy
from datetime import timedelta
from io import BytesIO, StringIO
import builtins
import importlib
import json
from pathlib import Path
import socket
import tempfile
import traceback
import unittest
from unittest.mock import Mock, patch

from investment import InvestmentError
from investment.__main__ import main
from investment.core import iso
from investment.monitoring import monitoring_snapshot
from investment.monitoring_runtime import collect_once
from investment.monitoring_store import append_attempt, initialize_cache, read_cache
from investment.store import import_events, initialize
from test_investment_opening import NOW, opening, profile
from test_investment_monitoring import monitoring_profile, assignments


def synthetic_snapshot(revision=1, api="synthetic-api", user="synthetic-user"):
    from proxy_config import ConnectionSnapshot
    return ConnectionSnapshot(revision, {"GOLDSCOUT_ETORO_API_KEY": api,
                                         "GOLDSCOUT_ETORO_USER_KEY": user})


class MonitoringRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.database, self.cache = root / "journal.sqlite3", root / "cache.sqlite3"
        self.path = root / "profile.json"
        initialize(self.database, profile(), NOW)
        import_events(self.database, [opening(), *assignments()], NOW)
        self.profile = monitoring_profile(profile())
        self.path.write_text(json.dumps(self.profile), encoding="utf-8")
        initialize_cache(self.cache, self.profile, 86400)
        from proxy_config import connection_snapshot
        self.real_connection_snapshot = connection_snapshot
        snapshot = patch("proxy_config.connection_snapshot", return_value=synthetic_snapshot())
        snapshot.start()
        self.addCleanup(snapshot.stop)

    def collect(self, **kwargs):
        kwargs.setdefault("completion_clock", lambda: NOW)
        return collect_once(self.database, self.profile, self.cache, "broker",
                            now=NOW, **kwargs)

    def test_import_does_not_require_connection_dependencies(self):
        from investment import monitoring_runtime

        original_import = builtins.__import__

        def guarded_import(name, *args, **kwargs):
            if name == "proxy_config":
                raise ModuleNotFoundError("synthetic optional dependency unavailable")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=guarded_import):
            importlib.reload(monitoring_runtime)

    def test_collection_requires_network_gate_before_transport_or_secrets(self):
        collector, credentials = Mock(), Mock()
        with self.assertRaisesRegex(InvestmentError, "--allow-network"):
            self.collect(collector=collector, credential_accessor=credentials)
        collector.assert_not_called()
        credentials.assert_not_called()

    def test_collection_rejects_unmapped_or_wrong_provider_before_credentials(self):
        collector, credentials = Mock(), Mock()
        for account in ("unknown", "bank"):
            with self.subTest(account=account):
                with self.assertRaisesRegex(InvestmentError, "explicitly mapped eToro"):
                    collect_once(self.database, self.profile, self.cache, account,
                                 allow_network=True, now=NOW, collector=collector,
                                 credential_accessor=credentials)
        collector.assert_not_called()
        credentials.assert_not_called()

    def test_collection_rejects_mismatched_cache_before_credentials(self):
        changed = deepcopy(self.profile)
        changed["accounts"][0]["evidence"] = "different synthetic mapping evidence"
        collector, credentials = Mock(), Mock()
        with self.assertRaises(InvestmentError):
            collect_once(self.database, changed, self.cache, "broker", now=NOW,
                         allow_network=True, collector=collector, credential_accessor=credentials)
        collector.assert_not_called()
        credentials.assert_not_called()

    def test_credential_rotation_during_collection_stops_before_provider_dispatch(self):
        transport = Mock()
        credentials = Mock(return_value={
            "x-api-key": "synthetic-api", "x-user-key": "synthetic-user",
        })
        with patch("proxy_config.connection_snapshot",
                   side_effect=(synthetic_snapshot(1), synthetic_snapshot(1), synthetic_snapshot(2))):
            with self.assertRaisesRegex(InvestmentError, "credentials changed"):
                self.collect(allow_network=True, transport=transport,
                             credential_accessor=credentials)
        transport.assert_not_called()
        self.assertEqual(read_cache(self.cache, self.profile, NOW)["attempts"][-1]["error_code"],
                         "collection_interrupted")

    def test_aba_credential_accessor_cannot_dispatch_values_outside_snapshot(self):
        source = {"x-api-key": "synthetic-api", "x-user-key": "synthetic-user"}
        transport = Mock()

        def aba():
            source["x-api-key"] = "synthetic-other-api"
            captured = dict(source)
            source["x-api-key"] = "synthetic-api"
            return captured

        with self.assertRaisesRegex(InvestmentError, "provider_unavailable") as caught:
            self.collect(allow_network=True, transport=transport, credential_accessor=aba)
        transport.assert_not_called()
        self.assertNotIn("synthetic-other-api", "".join(traceback.format_exception(caught.exception)))
        attempt = read_cache(self.cache, self.profile, NOW)["attempts"][-1]
        self.assertEqual(attempt["status"], "failed")
        self.assertIsNone(attempt["observation"])

    def test_changed_values_with_same_revision_are_not_dispatched(self):
        transport = Mock()
        snapshots = (synthetic_snapshot(), synthetic_snapshot(),
                     synthetic_snapshot(api="synthetic-other-api"))
        with patch("proxy_config.connection_snapshot", side_effect=snapshots):
            with self.assertRaisesRegex(InvestmentError, "credentials changed"):
                self.collect(allow_network=True, transport=transport)
        transport.assert_not_called()
        self.assertIsNone(read_cache(self.cache, self.profile, NOW)["attempts"][-1]["observation"])

    def test_snapshot_uses_one_synthetic_source_read_and_preserves_precedence(self):
        import proxy_config

        names = ("GOLDSCOUT_ETORO_API_KEY", "GOLDSCOUT_ETORO_USER_KEY")
        settings = proxy_config._connection_defaults()
        settings["values"][names[0]] = "synthetic-saved"
        settings["versions"][names[0]] = 3
        environment = {names[0]: "synthetic-env-api", names[1]: "synthetic-env-user"}
        with patch.dict("os.environ", environment), \
             patch("proxy_config._dotenv_loading_disabled", return_value=False), \
             patch("proxy_config.read_connection_settings", return_value=settings) as read_settings, \
             patch("proxy_config._read_proxy_dotenv",
                   return_value={names[0]: "synthetic-file-api", names[1]: "synthetic-file-user"}) as read_file:
            snapshot = self.real_connection_snapshot(*names, path=self.path.with_name("connections.json"))
            read_settings.assert_called_once()
            read_file.assert_called_once_with(names)
            self.assertEqual(snapshot.revision, proxy_config.connection_revision(
                *names, path=self.path.with_name("connections.json")))
        self.assertEqual(snapshot.values, {names[0]: "synthetic-saved", names[1]: "synthetic-file-user"})
        self.assertNotIn("synthetic-", repr(snapshot))

    def test_snapshot_respects_disabled_private_sources_and_explicit_paths(self):
        import proxy_config

        names = ("GOLDSCOUT_ETORO_API_KEY", "GOLDSCOUT_ETORO_USER_KEY")
        environment = {names[0]: "synthetic-env-api", names[1]: "synthetic-env-user"}
        settings = proxy_config._connection_defaults()
        settings["values"][names[0]] = "synthetic-explicit-api"
        explicit_path = self.path.with_name("explicit-connections.json")
        with patch.dict("os.environ", environment), \
             patch("proxy_config._dotenv_loading_disabled", return_value=True), \
             patch("proxy_config._read_proxy_dotenv", side_effect=AssertionError("private source read")), \
             patch("proxy_config.read_connection_settings", return_value=settings) as read_settings:
            snapshot = self.real_connection_snapshot(*names)
            read_settings.assert_not_called()
            self.assertEqual(snapshot.values, environment)
            self.assertEqual(snapshot.revision, proxy_config.connection_revision(*names))
            explicit = self.real_connection_snapshot(*names, path=explicit_path)
            read_settings.assert_called_once_with(explicit_path.absolute())
            self.assertEqual(explicit.values, {
                names[0]: "synthetic-explicit-api", names[1]: "synthetic-env-user"})

    def test_snapshot_defensively_freezes_values(self):
        from proxy_config import ConnectionSnapshot

        original = {"GOLDSCOUT_ETORO_API_KEY": "synthetic-first"}
        snapshot = ConnectionSnapshot(1, original)
        original["GOLDSCOUT_ETORO_API_KEY"] = "synthetic-second"
        self.assertEqual(snapshot.values["GOLDSCOUT_ETORO_API_KEY"], "synthetic-first")
        with self.assertRaises(TypeError):
            snapshot.values["GOLDSCOUT_ETORO_API_KEY"] = "synthetic-third"
        self.assertNotIn("synthetic", repr(snapshot))

    def test_aba_in_synthetic_source_during_dispatch_never_sends_other_key(self):
        payload = (b'{"clientPortfolio":{"credit":42,"positions":[],"mirrors":[],'
                   b'"orders":[],"ordersForOpen":[]}}')
        values = {"GOLDSCOUT_ETORO_API_KEY": "synthetic-first",
                  "GOLDSCOUT_ETORO_USER_KEY": "synthetic-user"}
        reads = []
        transport = Mock(return_value=payload)

        def source(names):
            reads.append(names)
            if len(reads) == 3:
                values["GOLDSCOUT_ETORO_API_KEY"] = "synthetic-other"
                captured = dict(values)
                values["GOLDSCOUT_ETORO_API_KEY"] = "synthetic-first"
                return captured
            return dict(values)

        with patch("proxy_config.CONNECTIONS_PATH", self.path.with_name("connections.json")), \
             patch("proxy_config._dotenv_loading_disabled", return_value=False), \
             patch("proxy_config._read_proxy_dotenv", side_effect=source), \
             patch("proxy_config.connection_snapshot", self.real_connection_snapshot):
            with self.assertRaisesRegex(InvestmentError, "credentials changed"):
                self.collect(allow_network=True, transport=transport)
        self.assertEqual(values["GOLDSCOUT_ETORO_API_KEY"], "synthetic-first")
        transport.assert_not_called()
        attempt = read_cache(self.cache, self.profile, NOW)["attempts"][-1]
        self.assertEqual(attempt["error_code"], "collection_interrupted")
        self.assertIsNone(attempt["observation"])

    def test_credential_rotation_in_flight_withholds_response(self):
        payload = (b'{"clientPortfolio":{"credit":42,"positions":[],"mirrors":[],'
                   b'"orders":[],"ordersForOpen":[]}}')
        revision = [1]

        def respond(*args):
            revision[0] = 2
            return payload

        transport = Mock(side_effect=respond)
        with patch("proxy_config.connection_snapshot", side_effect=lambda *args: synthetic_snapshot(revision[0])):
            with self.assertRaisesRegex(InvestmentError, "credentials changed"):
                self.collect(allow_network=True, transport=transport,
                             credential_accessor=lambda: {"x-api-key": "synthetic-api",
                                                          "x-user-key": "synthetic-user"})
        transport.assert_called_once()
        attempts = read_cache(self.cache, self.profile, NOW)["attempts"]
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["error_code"], "collection_interrupted")
        self.assertIsNone(attempts[0]["observation"])

    def test_revision_unavailable_after_response_withholds_and_sanitizes(self):
        from proxy_config import ProxyConfigurationError

        payload = (b'{"clientPortfolio":{"credit":42,"positions":[],"mirrors":[],'
                   b'"orders":[],"ordersForOpen":[]}}')
        unavailable = [False]

        def revision(*args):
            if unavailable[0]:
                raise ProxyConfigurationError("synthetic-secret")
            return synthetic_snapshot()

        def respond(*args):
            unavailable[0] = True
            return payload

        with patch("proxy_config.connection_snapshot", side_effect=revision):
            with self.assertRaises(InvestmentError) as caught:
                self.collect(allow_network=True, transport=Mock(side_effect=respond),
                             credential_accessor=lambda: {"x-api-key": "synthetic-api",
                                                          "x-user-key": "synthetic-user"})
        self.assertNotIn("synthetic-secret", "".join(traceback.format_exception(caught.exception)))
        attempt = read_cache(self.cache, self.profile, NOW)["attempts"][-1]
        self.assertEqual(attempt["error_code"], "collection_interrupted")
        self.assertIsNone(attempt["observation"])

    def test_in_flight_rotation_cache_failure_never_reports_success(self):
        payload = (b'{"clientPortfolio":{"credit":42,"positions":[],"mirrors":[],'
                   b'"orders":[],"ordersForOpen":[]}}')
        revision = [1]

        def respond(*args):
            revision[0] = 2
            return payload

        with patch("proxy_config.connection_snapshot", side_effect=lambda *args: synthetic_snapshot(revision[0])), \
             patch("investment.monitoring_runtime.append_attempt",
                   side_effect=InvestmentError("synthetic cache write failed")):
            with self.assertRaisesRegex(InvestmentError, "cache write failed"):
                self.collect(allow_network=True, transport=Mock(side_effect=respond),
                             credential_accessor=lambda: {"x-api-key": "synthetic-api",
                                                          "x-user-key": "synthetic-user"})
        self.assertEqual(read_cache(self.cache, self.profile, NOW)["revision"], 0)

    def test_rotation_with_untrustworthy_completion_clock_records_no_attempt(self):
        payload = (b'{"clientPortfolio":{"credit":42,"positions":[],"mirrors":[],'
                   b'"orders":[],"ordersForOpen":[]}}')
        revision = [1]

        def respond(*args):
            revision[0] = 2
            return payload

        readings = iter((NOW, NOW, NOW - timedelta(seconds=1)))
        with patch("proxy_config.connection_snapshot", side_effect=lambda *args: synthetic_snapshot(revision[0])):
            with self.assertRaisesRegex(InvestmentError, "clock moved backwards"):
                self.collect(allow_network=True, transport=Mock(side_effect=respond),
                             credential_accessor=lambda: {"x-api-key": "synthetic-api",
                                                          "x-user-key": "synthetic-user"},
                             completion_clock=lambda: next(readings))
        self.assertEqual(read_cache(self.cache, self.profile, NOW)["revision"], 0)

    def test_explicit_collection_remains_fixed_endpoint_and_non_accounting(self):
        payload = (b'{"clientPortfolio":{"credit":42,"positions":[],"mirrors":[],'
                   b'"orders":[],"ordersForOpen":[]}}')
        transport = Mock(return_value=payload)
        credentials = Mock(return_value={"x-api-key": "synthetic-api",
                                         "x-user-key": "synthetic-user"})
        result = self.collect(allow_network=True, transport=transport,
                             credential_accessor=credentials)
        self.assertEqual(result["status"], "partial_observation")
        self.assertFalse(result["accounting_writes"])
        self.assertFalse(result["affects_trading"])
        transport.assert_called_once()
        self.assertEqual(transport.call_args.args[0], "/api/v1/trading/info/real/pnl")
        credentials.assert_called_once()

    def test_default_credentials_use_file_values_only_after_collection_gates(self):
        payload = (b'{"clientPortfolio":{"credit":42,"positions":[],"mirrors":[],'
                   b'"orders":[],"ordersForOpen":[]}}')
        transport = Mock(return_value=payload)
        values = {"GOLDSCOUT_ETORO_API_KEY": "file-api",
                  "GOLDSCOUT_ETORO_USER_KEY": "file-user"}
        connection_path = Path(self.temporary.name) / "connections.json"
        with patch.dict("os.environ", {"GOLDSCOUT_ETORO_API_KEY": "environment-api",
                                      "GOLDSCOUT_ETORO_USER_KEY": "environment-user"}), \
             patch("proxy_config.CONNECTIONS_PATH", connection_path), \
             patch("proxy_config._dotenv_loading_disabled", return_value=False), \
             patch("proxy_config._read_proxy_dotenv", return_value=values) as read, \
             patch("proxy_config.connection_snapshot", self.real_connection_snapshot):
            with self.assertRaises(InvestmentError):
                self.collect(transport=transport)
            read.assert_not_called()
            result = self.collect(allow_network=True, transport=transport)
        self.assertEqual(result["status"], "partial_observation")
        headers = transport.call_args.args[1]
        self.assertEqual((headers["x-api-key"], headers["x-user-key"]), ("file-api", "file-user"))
        self.assertGreaterEqual(read.call_count, 2)
        self.assertTrue(all(call.args == (("GOLDSCOUT_ETORO_API_KEY", "GOLDSCOUT_ETORO_USER_KEY"),)
                            for call in read.call_args_list))

    def test_blank_file_credentials_do_not_fall_back_or_contact_provider(self):
        transport = Mock()
        connection_path = Path(self.temporary.name) / "connections.json"
        with patch.dict("os.environ", {"GOLDSCOUT_ETORO_API_KEY": "environment-api",
                                      "GOLDSCOUT_ETORO_USER_KEY": "environment-user"}), \
             patch("proxy_config.CONNECTIONS_PATH", connection_path), \
             patch("proxy_config._dotenv_loading_disabled", return_value=False), \
             patch("proxy_config._read_proxy_dotenv",
                   return_value={"GOLDSCOUT_ETORO_API_KEY": "",
                                 "GOLDSCOUT_ETORO_USER_KEY": "file-user"}), \
             patch("proxy_config.connection_snapshot", self.real_connection_snapshot):
            with self.assertRaisesRegex(InvestmentError, "provider_unavailable"):
                self.collect(allow_network=True, transport=transport)
        transport.assert_not_called()

    def test_credential_rotation_during_credential_load_never_dispatches(self):
        transport = Mock()
        current = [NOW]
        values = {"GOLDSCOUT_ETORO_API_KEY": "file-api",
                  "GOLDSCOUT_ETORO_USER_KEY": "file-user"}
        connection_path = Path(self.temporary.name) / "connections.json"

        def delayed_credentials(names):
            captured = dict(values)
            current[0] = NOW + timedelta(hours=1)
            values["GOLDSCOUT_ETORO_API_KEY"] = "rotated-api"
            return captured

        with patch("proxy_config.CONNECTIONS_PATH", connection_path), \
             patch("proxy_config._dotenv_loading_disabled", return_value=False), \
             patch("proxy_config._read_proxy_dotenv", side_effect=delayed_credentials), \
             patch("proxy_config.connection_snapshot", self.real_connection_snapshot):
            with self.assertRaisesRegex(InvestmentError, "credentials changed"):
                self.collect(allow_network=True, transport=transport,
                             completion_clock=lambda: current[0])
        transport.assert_not_called()
        attempts = read_cache(self.cache, self.profile, current[0])["attempts"]
        self.assertEqual(attempts[-1]["error_code"], "collection_interrupted")
        self.assertIsNone(attempts[-1]["observation"])

    def test_credential_rotation_during_dns_never_sends_builtin_request(self):
        from investment.etoro import EToroTransport

        current = [NOW]
        revision = [1]
        factory = Mock()
        credentials = Mock(return_value={"x-api-key": "synthetic-api", "x-user-key": "synthetic-user"})

        def delayed_dns(*args, **kwargs):
            current[0] = NOW + timedelta(hours=1)
            revision[0] = 2
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", 443))]

        def create_transport(**kwargs):
            return EToroTransport(resolver=delayed_dns, connection_factory=factory, proxy_url="", **kwargs)

        with patch("investment.etoro.EToroTransport", side_effect=create_transport), \
             patch("proxy_config.connection_snapshot", side_effect=lambda *args: synthetic_snapshot(revision[0])):
            with self.assertRaisesRegex(InvestmentError, "credentials changed"):
                self.collect(allow_network=True, credential_accessor=credentials,
                             completion_clock=lambda: current[0])
        credentials.assert_called_once()
        factory.assert_not_called()
        attempts = read_cache(self.cache, self.profile, current[0])["attempts"]
        self.assertEqual(attempts[-1]["error_code"], "collection_interrupted")

    def test_credential_rotation_during_connection_setup_never_sends_request(self):
        from investment.etoro import EToroTransport

        current = [NOW]
        revision = [1]
        connection = Mock(sock=None)
        credentials = Mock(return_value={"x-api-key": "synthetic-api", "x-user-key": "synthetic-user"})

        def delayed_connection(*args):
            current[0] = NOW + timedelta(hours=1)
            revision[0] = 2
            return connection

        def create_transport(**kwargs):
            return EToroTransport(
                resolver=lambda *args, **kw: [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP,
                                               "", ("8.8.8.8", 443))],
                connection_factory=delayed_connection, proxy_url="", **kwargs)

        with patch("investment.etoro.EToroTransport", side_effect=create_transport), \
             patch("proxy_config.connection_snapshot", side_effect=lambda *args: synthetic_snapshot(revision[0])):
            with self.assertRaisesRegex(InvestmentError, "credentials changed"):
                self.collect(allow_network=True, credential_accessor=credentials,
                             completion_clock=lambda: current[0])
        connection.request.assert_not_called()
        connection.close.assert_called_once()

    def test_credential_rotation_during_tls_never_sends_account_headers(self):
        from investment.etoro import EToroTransport

        current = [NOW]
        revision = [1]
        tls_socket, raw_socket, context = Mock(), Mock(), Mock()
        context.wrap_socket.return_value = tls_socket
        def rotate_during_handshake():
            current[0] = NOW + timedelta(hours=1)
            revision[0] = 2
        tls_socket.do_handshake.side_effect = rotate_during_handshake
        credentials = Mock(return_value={"x-api-key": "synthetic-api", "x-user-key": "synthetic-user"})

        def create_transport(**kwargs):
            return EToroTransport(
                resolver=lambda *args, **kw: [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP,
                                               "", ("8.8.8.8", 443))],
                proxy_url="", **kwargs)

        with patch("market_intelligence.providers.ssl.create_default_context", return_value=context), \
             patch("market_intelligence.providers.socket.create_connection", return_value=raw_socket), \
             patch("investment.etoro.EToroTransport", side_effect=create_transport), \
             patch("proxy_config.connection_snapshot", side_effect=lambda *args: synthetic_snapshot(revision[0])):
            with self.assertRaisesRegex(InvestmentError, "credentials changed"):
                self.collect(allow_network=True, credential_accessor=credentials,
                             completion_clock=lambda: current[0])
        tls_socket.do_handshake.assert_called_once()
        tls_socket.sendall.assert_not_called()
        tls_socket.close.assert_called()
        attempts = read_cache(self.cache, self.profile, current[0])["attempts"]
        self.assertEqual(attempts[-1]["error_code"], "collection_interrupted")

    def test_explicit_collection_sends_one_get_over_the_connected_tls_socket(self):
        from investment.etoro import EToroTransport

        payload = (b'{"clientPortfolio":{"credit":42,"positions":[],"mirrors":[],'
                   b'"orders":[],"ordersForOpen":[]}}')
        tls_socket, raw_socket, context = Mock(), Mock(), Mock()
        tls_socket.makefile.return_value = BytesIO(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
            + str(len(payload)).encode("ascii") + b"\r\n\r\n" + payload)
        context.wrap_socket.return_value = tls_socket

        def create_transport(**kwargs):
            return EToroTransport(
                resolver=lambda *args, **kw: [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP,
                                               "", ("8.8.8.8", 443))],
                proxy_url="", **kwargs)

        with patch("market_intelligence.providers.ssl.create_default_context", return_value=context), \
             patch("market_intelligence.providers.socket.create_connection", return_value=raw_socket) as connect, \
             patch("investment.etoro.EToroTransport", side_effect=create_transport):
            result = self.collect(allow_network=True,
                                  credential_accessor=lambda: {"x-api-key": "synthetic-api",
                                                               "x-user-key": "synthetic-user"})
        self.assertEqual(result["status"], "partial_observation")
        connect.assert_called_once()
        tls_socket.do_handshake.assert_called_once()
        sent = b"".join(call.args[0] for call in tls_socket.sendall.call_args_list)
        self.assertEqual(sent.count(b"GET "), 1)
        self.assertIn(b"x-api-key: synthetic-api\r\n", sent)

    def test_historical_start_uses_live_completion_clock(self):
        from investment.core import clock

        collector = Mock(return_value={"account_currency": "USD", "raw": {"credit": "42", "positions": []}})
        wall_time = NOW + timedelta(hours=2)
        with patch("investment.monitoring_runtime.clock",
                   side_effect=lambda value=None: wall_time if value is None else clock(value)):
            self.collect(allow_network=True, transport=Mock(), collector=collector, completion_clock=None)
        attempt = read_cache(self.cache, self.profile, wall_time)["attempts"][-1]
        self.assertEqual(attempt["attempted_at"], iso(wall_time))

    def test_completion_clock_cannot_move_backwards_or_publish_evidence(self):
        collector = Mock(return_value={"account_currency": "USD", "raw": {"credit": "42", "positions": []}})
        readings = iter((NOW + timedelta(seconds=5), NOW + timedelta(seconds=3)))
        with self.assertRaisesRegex(InvestmentError, "clock moved backwards"):
            self.collect(allow_network=True, transport=Mock(), collector=collector,
                         completion_clock=lambda: next(readings))
        self.assertEqual(read_cache(self.cache, self.profile, NOW + timedelta(seconds=5))["attempts"], [])

    def test_clock_failure_is_sanitized_without_inventing_an_attempt_time(self):
        collector = Mock(return_value={"account_currency": "USD", "raw": {"credit": "42", "positions": []}})
        for values in ((RuntimeError("synthetic-clock-secret"),),
                       (NOW, RuntimeError("synthetic-clock-secret"))):
            with self.subTest(stage=len(values)):
                with self.assertRaisesRegex(InvestmentError, "clock unavailable") as caught:
                    self.collect(allow_network=True, transport=Mock(), collector=collector,
                                 completion_clock=Mock(side_effect=values))
                formatted = "".join(traceback.format_exception(caught.exception))
                self.assertNotIn("synthetic-clock-secret", formatted)
                self.assertIn("no attempt recorded", str(caught.exception))
                self.assertEqual(read_cache(self.cache, self.profile, NOW)["attempts"], [])

    def test_dispatch_clock_failure_survives_a_later_recovered_reading(self):
        readings = Mock(side_effect=(NOW, RuntimeError("synthetic-clock-secret"), NOW))
        transport = Mock()
        with self.assertRaisesRegex(InvestmentError, "clock unavailable; no attempt recorded") as caught:
            self.collect(allow_network=True, transport=transport,
                         credential_accessor=lambda: {"x-api-key": "synthetic-api",
                                                      "x-user-key": "synthetic-user"},
                         completion_clock=readings)
        self.assertNotIn("synthetic-clock-secret", "".join(traceback.format_exception(caught.exception)))
        self.assertEqual(readings.call_count, 2)
        transport.assert_not_called()
        self.assertEqual(read_cache(self.cache, self.profile, NOW)["revision"], 0)

    def test_builtin_transport_clock_failure_survives_recovery(self):
        from investment.etoro import EToroTransport

        readings = Mock(side_effect=(NOW, NOW, RuntimeError("synthetic-clock-secret"), NOW))
        factory = Mock()

        def create_transport(**kwargs):
            return EToroTransport(
                resolver=lambda *args, **kw: [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP,
                                               "", ("8.8.8.8", 443))],
                connection_factory=factory, proxy_url="", **kwargs)

        with patch("investment.etoro.EToroTransport", side_effect=create_transport):
            with self.assertRaisesRegex(InvestmentError, "clock unavailable; no attempt recorded") as caught:
                self.collect(allow_network=True,
                             credential_accessor=lambda: {"x-api-key": "synthetic-api",
                                                          "x-user-key": "synthetic-user"},
                             completion_clock=readings)
        self.assertNotIn("synthetic-clock-secret", "".join(traceback.format_exception(caught.exception)))
        self.assertEqual(readings.call_count, 3)
        factory.assert_not_called()
        self.assertEqual(read_cache(self.cache, self.profile, NOW)["revision"], 0)

    def test_unconfirmed_cancellation_reports_possible_inflight_request(self):
        from investment.etoro import EToroError

        transport = Mock(side_effect=EToroError("cancellation_unconfirmed"))
        with self.assertRaisesRegex(InvestmentError, "request may remain in flight"):
            self.collect(allow_network=True, transport=transport,
                         credential_accessor=lambda: {"x-api-key": "synthetic-api",
                                                      "x-user-key": "synthetic-user"})
        attempt = read_cache(self.cache, self.profile, NOW)["attempts"][-1]
        self.assertEqual(attempt["error_code"], "provider_unavailable")
        self.assertIsNone(attempt["observation"])

    def test_unconfirmed_cancellation_warning_survives_clock_and_cache_failures(self):
        from investment.etoro import EToroError

        def transport(*args):
            raise EToroError("cancellation_unconfirmed")

        credentials = lambda: {"x-api-key": "synthetic-api", "x-user-key": "synthetic-user"}
        readings = Mock(side_effect=(NOW, NOW, RuntimeError("synthetic-clock-secret")))
        with self.assertRaises(InvestmentError) as caught:
            self.collect(allow_network=True, transport=transport,
                         credential_accessor=credentials, completion_clock=readings)
        self.assertIn("request may remain in flight", str(caught.exception))
        self.assertIn("no attempt recorded", str(caught.exception))
        self.assertNotIn("synthetic-clock-secret", "".join(traceback.format_exception(caught.exception)))
        self.assertEqual(read_cache(self.cache, self.profile, NOW)["attempts"], [])

        def committed_then_failed(*args, **kwargs):
            append_attempt(*args, **kwargs)
            raise InvestmentError("synthetic-post-commit-secret")

        with patch("investment.monitoring_runtime.append_attempt", side_effect=committed_then_failed):
            with self.assertRaises(InvestmentError) as caught:
                self.collect(allow_network=True, transport=transport, credential_accessor=credentials)
        self.assertIn("request may remain in flight", str(caught.exception))
        self.assertIn("recording could not be confirmed", str(caught.exception))
        self.assertNotIn("no attempt recorded", str(caught.exception))
        self.assertNotIn("synthetic-post-commit-secret", "".join(traceback.format_exception(caught.exception)))
        self.assertEqual(len(read_cache(self.cache, self.profile, NOW)["attempts"]), 1)

    def test_partial_usd_receipt_has_no_invented_source_clock_and_survives_failure(self):
        from investment.etoro import EToroError
        before = self.database.read_bytes()
        collector = Mock(return_value={"account_currency": "USD", "raw": {"credit": "42", "positions": []}})
        result = self.collect(allow_network=True, transport=Mock(), collector=collector)
        self.assertEqual(result["status"], "partial_observation")
        report = monitoring_snapshot(self.database, self.path, self.cache, NOW)
        provider = next(p for p in report["providers"] if p["provider"] == "etoro")
        self.assertIsNone(provider["source_as_of"])
        self.assertEqual(provider["freshness"], "unavailable")
        self.assertEqual(provider["native_summary"]["credit_usd"], "42")
        collector.side_effect = EToroError("permission_refused")
        with self.assertRaisesRegex(InvestmentError, "permission_denied"):
            self.collect(allow_network=True, transport=Mock(), collector=collector)
        report = monitoring_snapshot(self.database, self.path, self.cache, NOW)
        self.assertTrue(any(f["rule"] == "collection_failed" for f in report["findings"]))
        self.assertEqual(self.database.read_bytes(), before)
        self.assertEqual(len(read_cache(self.cache, self.profile, NOW)["attempts"]), 2)

    def test_cache_write_failure_does_not_report_collection_success(self):
        collector = Mock(return_value={
            "account_currency": "USD", "raw": {"credit": "42", "positions": []}})
        with patch("investment.monitoring_runtime.append_attempt",
                   side_effect=InvestmentError("synthetic cache write failed")):
            with self.assertRaisesRegex(InvestmentError, "synthetic cache write failed"):
                self.collect(allow_network=True, transport=Mock(), collector=collector)
        self.assertEqual(read_cache(self.cache, self.profile, NOW)["revision"], 0)

    def test_explicit_collection_can_repeat_beyond_one_day(self):
        collector = Mock(return_value={
            "account_currency": "USD", "raw": {"credit": "42", "positions": []}})
        for offset in (0, 25):
            when = NOW + timedelta(hours=offset)
            result = self.collect(
                allow_network=True, transport=Mock(), collector=collector,
                completion_clock=lambda: when)
            self.assertEqual(result["status"], "partial_observation")
        self.assertEqual(collector.call_count, 2)
        self.assertEqual(read_cache(self.cache, self.profile, NOW + timedelta(hours=25))["revision"], 2)

    def test_native_precision_is_preserved_after_long_collection(self):
        collector = Mock(return_value={
            "account_currency": "USD", "raw": {"credit": "0.123456789012345678", "positions": []}})
        self.collect(allow_network=True, transport=Mock(), collector=collector)
        observation = read_cache(self.cache, self.profile, NOW)["attempts"][0]["observation"]
        self.assertEqual(observation["native_summary"]["credit_usd"], "0.123456789012345678")
        self.collect(allow_network=True, transport=Mock(), collector=collector,
                     completion_clock=lambda: NOW + timedelta(hours=1))
        attempts = read_cache(self.cache, self.profile, NOW + timedelta(hours=1))["attempts"]
        self.assertEqual(attempts[-1]["status"], "success")
        self.assertEqual(attempts[-1]["observation"]["native_summary"]["credit_usd"],
                         "0.123456789012345678")

    def test_cli_report_is_offline_and_staging_does_not_change_journal(self):
        from test_investment_monitoring_store import attempt
        source = self.path.with_name("attempt.json")
        source.write_text(json.dumps(attempt()), encoding="utf-8")
        before = self.database.read_bytes()
        arguments = ["--database", str(self.database), "--monitoring-profile", str(self.path),
                     "--cache", str(self.cache)]
        for command, extra in (("monitor-stage", ["--attempt", str(source)]), ("monitor", [])):
            output, error = StringIO(), StringIO()
            with patch("investment.etoro.EToroTransport", side_effect=AssertionError("network forbidden")):
                self.assertEqual(main([command, *arguments, *extra], stdout=output, stderr=error, now=NOW), 0,
                                 error.getvalue())
            self.assertTrue(json.loads(output.getvalue()))
        self.assertEqual(self.database.read_bytes(), before)

    def test_cli_watch_runs_multiple_days_and_stops_on_interrupt(self):
        payload = (b'{"clientPortfolio":{"credit":42,"positions":[],"mirrors":[],'
                   b'"orders":[],"ordersForOpen":[]}}')
        transport = Mock(return_value=payload)
        current = [NOW]
        waits = []

        def wait(seconds):
            waits.append(seconds)
            if len(waits) == 3:
                raise KeyboardInterrupt
            current[0] += timedelta(days=1, seconds=1)

        arguments = ["monitor-collect", "--database", str(self.database),
                     "--monitoring-profile", str(self.path), "--cache", str(self.cache),
                     "--account-id", "broker", "--allow-network", "--watch"]
        output, error = StringIO(), StringIO()
        before = self.database.read_bytes()
        with patch("investment.__main__.clock", side_effect=lambda *args: current[0]), \
             patch("investment.store.clock", side_effect=lambda *args: current[0]), \
             patch("investment.monitoring_runtime.clock", side_effect=lambda *args: current[0]), \
             patch("investment.etoro.EToroTransport", return_value=transport), \
             patch("investment.__main__.threading.Event", return_value=Mock(wait=wait)):
            self.assertEqual(main(arguments, stdout=output, stderr=error), 130)
        self.assertEqual(waits, [86400] * 3)
        self.assertEqual(transport.call_count, 3)
        self.assertTrue(all(call.args[0] == "/api/v1/trading/info/real/pnl"
                            for call in transport.call_args_list))
        self.assertEqual(len(output.getvalue().splitlines()), 3)
        self.assertEqual(read_cache(self.cache, self.profile, current[0])["revision"], 3)
        self.assertEqual(self.database.read_bytes(), before)

    def test_cli_watch_stops_on_collection_error(self):
        from investment.etoro import EToroError

        payload = (b'{"clientPortfolio":{"credit":42,"positions":[],"mirrors":[],'
                   b'"orders":[],"ordersForOpen":[]}}')
        transport = Mock(side_effect=(payload, EToroError("permission_refused")))
        arguments = ["monitor-collect", "--database", str(self.database),
                     "--monitoring-profile", str(self.path), "--cache", str(self.cache),
                     "--account-id", "broker", "--allow-network", "--watch"]
        output, error = StringIO(), StringIO()
        waiter = Mock()
        with patch("investment.monitoring_runtime.clock", return_value=NOW), \
             patch("investment.etoro.EToroTransport", return_value=transport), \
             patch("investment.__main__.threading.Event", return_value=waiter):
            self.assertEqual(main(arguments, stdout=output, stderr=error, now=NOW), 2)
        waiter.wait.assert_called_once_with(86400)
        self.assertIn("permission_denied", error.getvalue())
        self.assertEqual([a["status"] for a in read_cache(self.cache, self.profile, NOW)["attempts"]],
                         ["success", "failed"])

    def test_cli_watch_stops_before_dispatch_when_credentials_rotate_during_wait(self):
        payload = (b'{"clientPortfolio":{"credit":42,"positions":[],"mirrors":[],'
                   b'"orders":[],"ordersForOpen":[]}}')
        revision = [1]
        transport = Mock(return_value=payload)
        snapshots = Mock(side_effect=lambda *args: synthetic_snapshot(revision[0]))
        waiter = Mock()

        def wait(seconds):
            if revision[0] == 2:
                raise AssertionError("watch dispatched again after credential rotation")
            revision[0] = 2

        waiter.wait.side_effect = wait
        arguments = ["monitor-collect", "--database", str(self.database),
                     "--monitoring-profile", str(self.path), "--cache", str(self.cache),
                     "--account-id", "broker", "--allow-network", "--watch"]
        output, error = StringIO(), StringIO()
        before = self.database.read_bytes()
        with patch("investment.monitoring_runtime.clock", return_value=NOW), \
             patch("investment.etoro.EToroTransport", return_value=transport), \
             patch("proxy_config.connection_snapshot", snapshots), \
             patch("investment.__main__.threading.Event", return_value=waiter):
            self.assertEqual(main(arguments, stdout=output, stderr=error, now=NOW), 2)
        self.assertIn("credentials changed", error.getvalue())
        waiter.wait.assert_called_once_with(86400)
        transport.assert_called_once()
        self.assertGreaterEqual(snapshots.call_count, 2)
        self.assertEqual(len(output.getvalue().splitlines()), 1)
        attempts = read_cache(self.cache, self.profile, NOW)["attempts"]
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["status"], "success")
        self.assertEqual(self.database.read_bytes(), before)

    def test_cli_watch_pins_real_effective_revision_using_synthetic_configuration(self):
        payload = (b'{"clientPortfolio":{"credit":42,"positions":[],"mirrors":[],'
                   b'"orders":[],"ordersForOpen":[]}}')
        transport = Mock(return_value=payload)
        values = {"GOLDSCOUT_ETORO_API_KEY": "synthetic-first",
                  "GOLDSCOUT_ETORO_USER_KEY": "synthetic-user"}
        waiter = Mock()

        def wait(seconds):
            if values["GOLDSCOUT_ETORO_API_KEY"] == "synthetic-second":
                raise AssertionError("watch dispatched again after credential rotation")
            values["GOLDSCOUT_ETORO_API_KEY"] = "synthetic-second"

        waiter.wait.side_effect = wait
        arguments = ["monitor-collect", "--database", str(self.database),
                     "--monitoring-profile", str(self.path), "--cache", str(self.cache),
                     "--account-id", "broker", "--allow-network", "--watch"]
        output, error = StringIO(), StringIO()
        with patch("investment.monitoring_runtime.clock", return_value=NOW), \
             patch("investment.etoro.EToroTransport", return_value=transport), \
             patch("proxy_config.CONNECTIONS_PATH", Path(self.temporary.name) / "connections.json"), \
             patch("proxy_config._dotenv_loading_disabled", return_value=False), \
             patch("proxy_config._read_proxy_dotenv", side_effect=lambda names: dict(values)), \
             patch("proxy_config.connection_snapshot", self.real_connection_snapshot), \
             patch("investment.__main__.threading.Event", return_value=waiter):
            self.assertEqual(main(arguments, stdout=output, stderr=error, now=NOW), 2)
        self.assertIn("credentials changed", error.getvalue())
        transport.assert_called_once()
        self.assertEqual(transport.call_args.args[1]["x-api-key"], "synthetic-first")
        self.assertEqual(read_cache(self.cache, self.profile, NOW)["revision"], 1)

    def test_cli_watch_detects_versioned_settings_aba_during_wait(self):
        import proxy_config

        payload = (b'{"clientPortfolio":{"credit":42,"positions":[],"mirrors":[],'
                   b'"orders":[],"ordersForOpen":[]}}')
        transport = Mock(return_value=payload)
        path = self.path.with_name("connections.json")
        api, user = "GOLDSCOUT_ETORO_API_KEY", "GOLDSCOUT_ETORO_USER_KEY"
        waiter = Mock()

        def wait(seconds):
            proxy_config.update_connection_settings({api: "synthetic-second"},
                                                    expected_revision=1, path=path)
            proxy_config.update_connection_settings({api: "synthetic-first"},
                                                    expected_revision=2, path=path)

        waiter.wait.side_effect = wait
        arguments = ["monitor-collect", "--database", str(self.database),
                     "--monitoring-profile", str(self.path), "--cache", str(self.cache),
                     "--account-id", "broker", "--allow-network", "--watch"]
        output, error = StringIO(), StringIO()
        with patch("investment.monitoring_runtime.clock", return_value=NOW), \
             patch("investment.etoro.EToroTransport", return_value=transport), \
             patch("proxy_config.CONNECTIONS_PATH", path), \
             patch("proxy_config._dotenv_loading_disabled", return_value=False), \
             patch("proxy_config._read_proxy_dotenv", return_value={}), \
             patch("proxy_config.connection_snapshot", self.real_connection_snapshot), \
             patch("investment.__main__.threading.Event", return_value=waiter):
            proxy_config.update_connection_settings({api: "synthetic-first", user: "synthetic-user"},
                                                    expected_revision=0, path=path)
            self.assertEqual(main(arguments, stdout=output, stderr=error, now=NOW), 2)
        self.assertIn("credentials changed", error.getvalue())
        waiter.wait.assert_called_once_with(86400)
        transport.assert_called_once()
        self.assertEqual(transport.call_args.args[1]["x-api-key"], "synthetic-first")
        self.assertEqual(read_cache(self.cache, self.profile, NOW)["revision"], 1)

    def test_cli_collect_without_allow_network_is_offline(self):
        arguments = ["monitor-collect", "--database", str(self.database),
                     "--monitoring-profile", str(self.path), "--cache", str(self.cache),
                     "--account-id", "broker"]
        error = StringIO()
        with patch("investment.etoro.EToroTransport") as transport, \
             patch("proxy_config.connection_snapshot") as credentials:
            self.assertEqual(main(arguments, stdout=StringIO(), stderr=error, now=NOW), 2)
        self.assertIn("--allow-network", error.getvalue())
        transport.assert_not_called()
        credentials.assert_not_called()
        self.assertEqual(read_cache(self.cache, self.profile, NOW)["revision"], 0)

    def comparable_attempt(self, identifier="first", offset=1, cash="100"):
        from test_investment_monitoring_store import attempt
        value = attempt()
        value.update(id=identifier, attempted_at=iso(NOW + timedelta(seconds=offset + 1)))
        value["observation"].update(
            id="source-" + identifier, observed_at=iso(NOW + timedelta(seconds=offset)),
            available_at=value["attempted_at"], currency="GBP", cash=cash, coverage="complete",
            holdings=[{"source_product": "source-equity", "quantity": "50"},
                      {"source_product": "source-bond", "quantity": "20"}],
            product_mappings=[
                {"source_product": "source-" + product, "product_id": product, "evidence": "synthetic mapping"}
                for product in ("equity", "bond")],
            mapping_available_at=iso(NOW))
        return value

    def test_retained_difference_survives_matching_observation_and_reopen(self):
        first = self.comparable_attempt(cash="999")
        second = self.comparable_attempt("second", 3)
        now = NOW + timedelta(seconds=4)
        append_attempt(self.cache, self.profile, first, now)
        append_attempt(self.cache, self.profile, second, now)
        result = monitoring_snapshot(self.database, self.path, self.cache, now)
        difference = next(f for f in result["findings"] if f["rule"] == "observation_difference")
        self.assertIn("source-first", difference["evidence"])
        self.assertIn("899", difference["calculation"])

    def test_invalid_or_late_product_mapping_never_poison_or_backdate_comparisons(self):
        now = NOW + timedelta(seconds=2)
        value = self.comparable_attempt()
        value["observation"]["product_mappings"].append(
            deepcopy(value["observation"]["product_mappings"][0]))
        with self.assertRaises(InvestmentError):
            append_attempt(self.cache, self.profile, value, now)
        value = self.comparable_attempt()
        value["observation"]["product_mappings"][0]["product_id"] = "not-in-journal"
        append_attempt(self.cache, self.profile, value, now)
        result = monitoring_snapshot(self.database, self.path, self.cache, now)
        self.assertTrue(any(f["rule"] == "comparison_unavailable" for f in result["findings"]))
        late = self.comparable_attempt("late", 3)
        late["observation"]["mapping_available_at"] = late["observation"]["available_at"]
        now = NOW + timedelta(seconds=4)
        append_attempt(self.cache, self.profile, late, now)
        result = monitoring_snapshot(self.database, self.path, self.cache, now)
        issue = next(f for f in result["findings"] if f["rule"] == "comparison_unavailable")
        self.assertEqual(issue["calculation"], "2 unavailable comparisons")
