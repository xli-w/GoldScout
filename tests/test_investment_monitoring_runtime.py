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
from investment.store import canonical, digest, import_events, initialize
from test_investment_opening import NOW, opening, profile
from test_investment_monitoring import monitoring_profile, assignments


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
        from proxy_config import connection_revision
        self.approval = {
            "profile_hash": digest(canonical(self.profile)), "account_id": "broker",
            "approved_at": iso(NOW), "expires_at": iso(NOW + timedelta(hours=1)),
            "read_scope_verified": True, "trade_capable_key_risk_accepted": False,
            "eligibility_verified": True,
            "retention_rights_verified": True, "account_binding_verified": True,
            "supervised_read_approved": True,
            "credential_revision": connection_revision(
                "GOLDSCOUT_ETORO_API_KEY", "GOLDSCOUT_ETORO_USER_KEY"),
            "evidence": "synthetic approval only",
        }

    def collect(self, **kwargs):
        kwargs.setdefault("completion_clock", lambda: NOW)
        return collect_once(self.database, self.profile, self.cache, "broker", self.approval,
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

    def test_collection_requires_all_gates_before_transport_or_secrets(self):
        collector, credentials = Mock(), Mock()
        with self.assertRaises(InvestmentError):
            self.collect(collector=collector, credential_accessor=credentials)
        for key in ("eligibility_verified", "retention_rights_verified",
                    "account_binding_verified", "supervised_read_approved"):
            self.approval[key] = False
            with self.assertRaises(InvestmentError):
                self.collect(allow_network=True, collector=collector, credential_accessor=credentials)
            self.approval[key] = True
        for read_scope, risk_accepted in ((False, False), (True, True)):
            self.approval["read_scope_verified"] = read_scope
            self.approval["trade_capable_key_risk_accepted"] = risk_accepted
            with self.assertRaises(InvestmentError):
                self.collect(allow_network=True, collector=collector, credential_accessor=credentials)
        self.approval["read_scope_verified"] = False
        self.approval.pop("trade_capable_key_risk_accepted")
        with self.assertRaises(InvestmentError):
            self.collect(allow_network=True, collector=collector, credential_accessor=credentials)
        self.approval["trade_capable_key_risk_accepted"] = False
        self.approval["read_scope_verified"] = True
        self.approval["expires_at"] = iso(NOW)
        with self.assertRaises(InvestmentError):
            self.collect(allow_network=True, collector=collector, credential_accessor=credentials)
        collector.assert_not_called()
        credentials.assert_not_called()

    def test_collection_approval_must_match_current_eToro_credentials(self):
        collector, credentials = Mock(), Mock()
        self.approval["credential_revision"] += 1
        with self.assertRaisesRegex(InvestmentError, "different eToro credentials"):
            self.collect(allow_network=True, collector=collector, credential_accessor=credentials)
        collector.assert_not_called()
        credentials.assert_not_called()

    def test_credential_rotation_during_collection_stops_before_provider_dispatch(self):
        from proxy_config import connection_revision

        revision = connection_revision(
            "GOLDSCOUT_ETORO_API_KEY", "GOLDSCOUT_ETORO_USER_KEY")
        transport = Mock()
        credentials = Mock(return_value={
            "x-api-key": "synthetic-api-key", "x-user-key": "synthetic-user-key",
        })
        self.approval["credential_revision"] = revision
        with patch("proxy_config.connection_revision",
                   side_effect=(revision, revision, revision + 1)):
            with self.assertRaisesRegex(InvestmentError, "approval"):
                self.collect(allow_network=True, transport=transport,
                             credential_accessor=credentials)
        transport.assert_not_called()
        self.assertEqual(read_cache(self.cache, self.profile, NOW)["attempts"][-1]["error_code"],
                         "collection_interrupted")

    def test_trade_capable_key_requires_explicit_risk_acceptance_and_remains_get_only(self):
        payload = (b'{"clientPortfolio":{"credit":42,"positions":[],"mirrors":[],'
                   b'"orders":[],"ordersForOpen":[]}}')
        transport = Mock(return_value=payload)
        credentials = Mock(return_value={"x-api-key": "trade-capable-key",
                                         "x-user-key": "synthetic-user-key"})
        self.approval["read_scope_verified"] = False
        self.approval["trade_capable_key_risk_accepted"] = False
        with self.assertRaisesRegex(InvestmentError, "trade-capable key"):
            self.collect(allow_network=True, transport=transport,
                         credential_accessor=credentials)
        transport.assert_not_called()
        credentials.assert_not_called()

        self.approval["trade_capable_key_risk_accepted"] = True
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
             patch("proxy_config._read_proxy_dotenv", return_value=values) as read:
            with self.assertRaises(InvestmentError):
                self.collect(transport=transport)
            read.assert_not_called()
            from proxy_config import connection_revision
            revision = connection_revision(
                "GOLDSCOUT_ETORO_API_KEY", "GOLDSCOUT_ETORO_USER_KEY")
            self.approval["credential_revision"] = revision
            read.reset_mock()
            with patch("proxy_config.connection_revision", return_value=revision):
                result = self.collect(allow_network=True, transport=transport)
        self.assertEqual(result["status"], "partial_observation")
        headers = transport.call_args.args[1]
        self.assertEqual((headers["x-api-key"], headers["x-user-key"]), ("file-api", "file-user"))
        read.assert_called_once_with(("GOLDSCOUT_ETORO_API_KEY", "GOLDSCOUT_ETORO_USER_KEY"))

    def test_blank_file_credentials_do_not_fall_back_or_contact_provider(self):
        transport = Mock()
        connection_path = Path(self.temporary.name) / "connections.json"
        with patch.dict("os.environ", {"GOLDSCOUT_ETORO_API_KEY": "environment-api",
                                      "GOLDSCOUT_ETORO_USER_KEY": "environment-user"}), \
             patch("proxy_config.CONNECTIONS_PATH", connection_path), \
             patch("proxy_config._dotenv_loading_disabled", return_value=False), \
             patch("proxy_config._read_proxy_dotenv",
                   return_value={"GOLDSCOUT_ETORO_API_KEY": "",
                                 "GOLDSCOUT_ETORO_USER_KEY": "file-user"}):
            from proxy_config import connection_revision
            revision = connection_revision(
                "GOLDSCOUT_ETORO_API_KEY", "GOLDSCOUT_ETORO_USER_KEY")
            self.approval["credential_revision"] = revision
            with patch("proxy_config.connection_revision", return_value=revision):
                with self.assertRaisesRegex(InvestmentError, "provider_unavailable"):
                    self.collect(allow_network=True, transport=transport)
        transport.assert_not_called()

    def test_approval_expiring_during_credential_load_never_dispatches(self):
        transport = Mock()
        current = [NOW]
        values = {"GOLDSCOUT_ETORO_API_KEY": "file-api",
                  "GOLDSCOUT_ETORO_USER_KEY": "file-user"}
        connection_path = Path(self.temporary.name) / "connections.json"

        def delayed_credentials(names):
            current[0] = NOW + timedelta(hours=1)
            return values

        with patch("proxy_config.CONNECTIONS_PATH", connection_path), \
             patch("proxy_config._dotenv_loading_disabled", return_value=False), \
             patch("proxy_config._read_proxy_dotenv", side_effect=delayed_credentials):
            from proxy_config import connection_revision
            revision = connection_revision(
                "GOLDSCOUT_ETORO_API_KEY", "GOLDSCOUT_ETORO_USER_KEY")
            self.approval["credential_revision"] = revision
            with patch("proxy_config.connection_revision", return_value=revision):
                with self.assertRaisesRegex(InvestmentError, "expired"):
                    self.collect(allow_network=True, transport=transport,
                                 completion_clock=lambda: current[0])
        transport.assert_not_called()
        attempts = read_cache(self.cache, self.profile, current[0])["attempts"]
        self.assertEqual(attempts[-1]["error_code"], "collection_interrupted")
        self.assertIsNone(attempts[-1]["observation"])

    def test_approval_expiring_during_dns_never_sends_builtin_request(self):
        from investment.etoro import EToroTransport

        current = [NOW]
        factory = Mock()
        credentials = Mock(return_value={"x-api-key": "file-api", "x-user-key": "file-user"})

        def delayed_dns(*args, **kwargs):
            current[0] = NOW + timedelta(hours=1)
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", 443))]

        def create_transport(**kwargs):
            return EToroTransport(resolver=delayed_dns, connection_factory=factory, proxy_url="", **kwargs)

        with patch("investment.etoro.EToroTransport", side_effect=create_transport):
            with self.assertRaisesRegex(InvestmentError, "approval"):
                self.collect(allow_network=True, credential_accessor=credentials,
                             completion_clock=lambda: current[0])
        credentials.assert_called_once()
        factory.assert_not_called()
        attempts = read_cache(self.cache, self.profile, current[0])["attempts"]
        self.assertEqual(attempts[-1]["error_code"], "collection_interrupted")

    def test_approval_expiring_during_connection_setup_never_sends_request(self):
        from investment.etoro import EToroTransport

        current = [NOW]
        connection = Mock(sock=None)
        credentials = Mock(return_value={"x-api-key": "file-api", "x-user-key": "file-user"})

        def delayed_connection(*args):
            current[0] = NOW + timedelta(hours=1)
            return connection

        def create_transport(**kwargs):
            return EToroTransport(
                resolver=lambda *args, **kw: [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP,
                                               "", ("8.8.8.8", 443))],
                connection_factory=delayed_connection, proxy_url="", **kwargs)

        with patch("investment.etoro.EToroTransport", side_effect=create_transport):
            with self.assertRaisesRegex(InvestmentError, "approval"):
                self.collect(allow_network=True, credential_accessor=credentials,
                             completion_clock=lambda: current[0])
        connection.request.assert_not_called()
        connection.close.assert_called_once()

    def test_approval_expiring_during_tls_never_sends_account_headers(self):
        from investment.etoro import EToroTransport

        current = [NOW]
        tls_socket, raw_socket, context = Mock(), Mock(), Mock()
        context.wrap_socket.return_value = tls_socket
        tls_socket.do_handshake.side_effect = lambda: current.__setitem__(0, NOW + timedelta(hours=1))
        credentials = Mock(return_value={"x-api-key": "file-api", "x-user-key": "file-user"})

        def create_transport(**kwargs):
            return EToroTransport(
                resolver=lambda *args, **kw: [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP,
                                               "", ("8.8.8.8", 443))],
                proxy_url="", **kwargs)

        with patch("market_intelligence.providers.ssl.create_default_context", return_value=context), \
             patch("market_intelligence.providers.socket.create_connection", return_value=raw_socket), \
             patch("investment.etoro.EToroTransport", side_effect=create_transport):
            with self.assertRaisesRegex(InvestmentError, "approval"):
                self.collect(allow_network=True, credential_accessor=credentials,
                             completion_clock=lambda: current[0])
        tls_socket.do_handshake.assert_called_once()
        tls_socket.sendall.assert_not_called()
        tls_socket.close.assert_called()
        attempts = read_cache(self.cache, self.profile, current[0])["attempts"]
        self.assertEqual(attempts[-1]["error_code"], "collection_interrupted")

    def test_valid_approval_sends_one_request_over_the_connected_tls_socket(self):
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
                                  credential_accessor=lambda: {"x-api-key": "file-api", "x-user-key": "file-user"})
        self.assertEqual(result["status"], "partial_observation")
        connect.assert_called_once()
        tls_socket.do_handshake.assert_called_once()
        sent = b"".join(call.args[0] for call in tls_socket.sendall.call_args_list)
        self.assertEqual(sent.count(b"GET "), 1)
        self.assertIn(b"x-api-key: file-api\r\n", sent)

    def test_historical_start_does_not_freeze_live_approval_clock(self):
        from investment.core import clock

        transport, credentials = Mock(), Mock()
        wall_time = NOW + timedelta(hours=2)
        with patch("investment.monitoring_runtime.clock",
                   side_effect=lambda value=None: wall_time if value is None else clock(value)):
            with self.assertRaisesRegex(InvestmentError, "approval"):
                self.collect(allow_network=True, transport=transport,
                             credential_accessor=credentials, completion_clock=None)
        credentials.assert_not_called()
        transport.assert_not_called()

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

    def test_unconfirmed_cancellation_reports_possible_inflight_request(self):
        from investment.etoro import EToroError

        transport = Mock(side_effect=EToroError("cancellation_unconfirmed"))
        with self.assertRaisesRegex(InvestmentError, "request may remain in flight"):
            self.collect(allow_network=True, transport=transport,
                         credential_accessor=lambda: {"x-api-key": "file-api", "x-user-key": "file-user"})
        attempt = read_cache(self.cache, self.profile, NOW)["attempts"][-1]
        self.assertEqual(attempt["error_code"], "provider_unavailable")
        self.assertIsNone(attempt["observation"])

    def test_unconfirmed_cancellation_warning_survives_clock_and_cache_failures(self):
        from investment.etoro import EToroError

        def transport(*args):
            raise EToroError("cancellation_unconfirmed")

        credentials = lambda: {"x-api-key": "file-api", "x-user-key": "file-user"}
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

    def test_native_precision_is_preserved_and_expired_completion_records_failure(self):
        collector = Mock(return_value={
            "account_currency": "USD", "raw": {"credit": "0.123456789012345678", "positions": []}})
        self.collect(allow_network=True, transport=Mock(), collector=collector)
        observation = read_cache(self.cache, self.profile, NOW)["attempts"][0]["observation"]
        self.assertEqual(observation["native_summary"]["credit_usd"], "0.123456789012345678")
        with self.assertRaisesRegex(InvestmentError, "expired"):
            self.collect(allow_network=True, transport=Mock(), collector=collector,
                         completion_clock=lambda: NOW + timedelta(hours=1))
        attempts = read_cache(self.cache, self.profile, NOW + timedelta(hours=1))["attempts"]
        self.assertEqual(attempts[-1]["error_code"], "collection_interrupted")
        self.assertIsNone(attempts[-1]["observation"])

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
