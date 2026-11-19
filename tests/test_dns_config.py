import io
import json
import os
import socket
import threading
import unittest
from unittest.mock import Mock, patch

import dns.exception
import dns.message
import dns.rcode
import dns.rdatatype
import dns.resolver
import dns.rrset

from dns_config import DNSConfigurationError, resolve_host
from investment import etoro
from market_intelligence.analysis import AnalysisError, SummaryRequest
from market_intelligence.providers import (
    BLS_CALENDAR_URL, CalendarTransport, PinnedHTTPSConnection, ProviderError,
)
from market_intelligence.runtime import ProviderProfile
from scripts.offline_tests import offline_environment
from test_intelligence_providers import FakeResponse
from test_investment_etoro import Response, encoded, headers


SERVER = "192.0.2.53"


def answer_with(addresses, nxdomain=()):
    def respond(query, server, **kwargs):
        question = query.question[0]
        name = question.name.to_text()
        kind = dns.rdatatype.to_text(question.rdtype)
        response = dns.message.make_response(query)
        if name in nxdomain:
            response.set_rcode(dns.rcode.NXDOMAIN)
        else:
            values = addresses.get((name, kind), ())
            if values:
                response.answer.append(dns.rrset.from_text(question.name, 60, "IN", kind, *values))
        return dns.message.from_wire(response.to_wire())
    return respond


class DNSResolverTests(unittest.TestCase):
    def test_blank_override_keeps_system_resolution(self):
        records = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        for value in ("", "  "):
            with self.subTest(value=value), patch.dict(os.environ, {"TRADER_DNS_IPV4": value}), \
                    patch("socket.getaddrinfo", return_value=records) as system:
                self.assertEqual(resolve_host("service.invalid", 443), records)
                system.assert_called_once_with("service.invalid", 443, type=socket.SOCK_STREAM)

    def test_private_setting_precedes_environment_including_blank(self):
        response = answer_with({("service.invalid.", "A"): ("93.184.216.34",)})
        with patch.dict(os.environ, {"TRADER_DNS_IPV4": "192.0.2.54"}), \
                patch("proxy_config._dotenv_loading_disabled", return_value=False), \
                patch("proxy_config._read_proxy_dotenv", return_value={"TRADER_DNS_IPV4": SERVER}), \
                patch("dns.query.udp", side_effect=response) as query, \
                patch("socket.getaddrinfo", side_effect=AssertionError("system DNS fallback")):
            records = resolve_host("service.invalid", 443)
        self.assertEqual(records[0][4], ("93.184.216.34", 443))
        self.assertEqual([call.args[1] for call in query.call_args_list], [SERVER, SERVER])

        with patch.dict(os.environ, {"TRADER_DNS_IPV4": SERVER}), \
                patch("proxy_config._dotenv_loading_disabled", return_value=False), \
                patch("proxy_config._read_proxy_dotenv", return_value={"TRADER_DNS_IPV4": ""}), \
                patch("socket.getaddrinfo", return_value=[]) as system, \
                patch("dns.query.udp", side_effect=AssertionError("override must be disabled")):
            self.assertEqual(resolve_host("service.invalid", 443), [])
            system.assert_called_once()

    def test_invalid_server_never_falls_back_or_sends_dns(self):
        for value in ("not-an-address", "127.0.0.1:53", "::1", "0.0.0.0",
                      "224.0.0.1", "255.255.255.255"):
            with self.subTest(value=value), patch.dict(os.environ, {"TRADER_DNS_IPV4": value}), \
                    patch("socket.getaddrinfo", side_effect=AssertionError("system DNS fallback")), \
                    patch("dns.query.udp", side_effect=AssertionError("invalid server was used")):
                with self.assertRaises(DNSConfigurationError) as caught:
                    resolve_host("service.invalid", 443)
                self.assertNotIn(value, str(caught.exception))

    def test_cname_a_and_aaaa_use_only_the_configured_ipv4_server(self):
        def respond(query, server, **kwargs):
            question = query.question[0]
            kind = dns.rdatatype.to_text(question.rdtype)
            address = {"A": "93.184.216.34", "AAAA": "2606:4700:4700::1111"}[kind]
            response = dns.message.make_response(query)
            response.answer.append(dns.rrset.from_text(
                question.name, 60, "IN", "CNAME", "alias.invalid."))
            response.answer.append(dns.rrset.from_text("alias.invalid.", 60, "IN", kind, address))
            return dns.message.from_wire(response.to_wire())

        with patch.dict(os.environ, {"TRADER_DNS_IPV4": SERVER}), \
                patch("socket.getaddrinfo", side_effect=AssertionError("system DNS fallback")), \
                patch("dns.query.udp", side_effect=respond) as query:
            records = resolve_host("service.invalid", 443)
        self.assertEqual(records, [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 443)),
            (socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "",
             ("2606:4700:4700::1111", 443, 0, 0)),
        ])
        self.assertEqual(len(query.call_args_list), 2)
        for invocation in query.call_args_list:
            self.assertEqual(invocation.args[1], SERVER)
            self.assertEqual(invocation.kwargs["port"], 53)
            self.assertLessEqual(invocation.kwargs["timeout"], 1.0)

    def test_no_a_record_can_still_return_ipv6(self):
        response = answer_with({("service.invalid.", "AAAA"): ("2606:4700:4700::1111",)})
        with patch.dict(os.environ, {"TRADER_DNS_IPV4": SERVER}), \
                patch("socket.getaddrinfo", side_effect=AssertionError("system DNS fallback")), \
                patch("dns.query.udp", side_effect=response):
            records = resolve_host("service.invalid", 443)
        self.assertEqual(records[0][0], socket.AF_INET6)
        self.assertEqual(records[0][4], ("2606:4700:4700::1111", 443, 0, 0))

    def test_truncated_udp_reply_retries_tcp_only_on_the_configured_server(self):
        complete = answer_with({("service.invalid.", "A"): ("93.184.216.34",)})

        def truncated_a(query, server, **kwargs):
            if query.question[0].rdtype == dns.rdatatype.A:
                raise dns.message.Truncated
            return complete(query, server, **kwargs)

        with patch.dict(os.environ, {"TRADER_DNS_IPV4": SERVER}), \
                patch("socket.getaddrinfo", side_effect=AssertionError("system DNS fallback")), \
                patch("dns.query.udp", side_effect=truncated_a), \
                patch("dns.query.tcp", side_effect=complete) as tcp:
            records = resolve_host("service.invalid", 443)
        self.assertEqual(records[0][4], ("93.184.216.34", 443))
        self.assertEqual(tcp.call_args.args[1], SERVER)
        self.assertEqual(tcp.call_args.kwargs["port"], 53)

    def test_dns_failures_and_excess_answers_do_not_fall_back(self):
        many_addresses = tuple(f"93.184.216.{index}" for index in range(1, 34))
        responses = (
            answer_with({}, nxdomain=("service.invalid.",)),
            answer_with({}),
            answer_with({("service.invalid.", "A"): many_addresses}),
        )
        for respond in responses:
            with self.subTest(respond=respond), patch.dict(os.environ, {"TRADER_DNS_IPV4": SERVER}), \
                    patch("socket.getaddrinfo", side_effect=AssertionError("system DNS fallback")), \
                    patch("dns.query.udp", side_effect=respond):
                with self.assertRaises(socket.gaierror):
                    resolve_host("service.invalid", 443)
        with patch.dict(os.environ, {"TRADER_DNS_IPV4": SERVER}), \
                patch("socket.getaddrinfo", side_effect=AssertionError("system DNS fallback")), \
                patch("dns.resolver.Resolver.resolve",
                      side_effect=dns.exception.Timeout("sensitive resolver detail")):
            with self.assertRaises(socket.gaierror) as caught:
                resolve_host("service.invalid", 443)
            self.assertNotIn("sensitive resolver detail", str(caught.exception))

    def test_numeric_proxy_host_uses_only_numeric_address_conversion(self):
        records = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 8080))]
        with patch.dict(os.environ, {"TRADER_DNS_IPV4": SERVER}), \
                patch("socket.getaddrinfo", return_value=records) as system, \
                patch("dns.query.udp", side_effect=AssertionError("numeric host sent to DNS")):
            self.assertEqual(resolve_host("127.0.0.1", 8080), records)
        system.assert_called_once_with(
            "127.0.0.1", 8080, type=socket.SOCK_STREAM, flags=socket.AI_NUMERICHOST)

    def test_offline_guard_blocks_configured_dns_queries(self):
        with offline_environment(), patch.dict(os.environ, {"TRADER_DNS_IPV4": SERVER}), \
                patch("socket.socket.sendto", wraps=socket.socket.sendto) as blocked:
            with self.assertRaises(socket.gaierror):
                resolve_host("service.invalid", 443)
        blocked.assert_called()


class DNSConnectionTests(unittest.TestCase):
    def setUp(self):
        self.settings = patch.dict(os.environ, {
            "TRADER_DNS_IPV4": SERVER,
            "TRADER_AI_TEST": "synthetic-key",
            "PYTHON_DOTENV_DISABLED": "1",
        })
        self.settings.start()
        self.addCleanup(self.settings.stop)
        self.system_dns = patch("socket.getaddrinfo", side_effect=AssertionError("system DNS fallback"))
        self.system_dns.start()
        self.addCleanup(self.system_dns.stop)

    def test_public_feed_and_proxy_host_use_override_with_pinning_and_tls(self):
        response = answer_with({
            ("www.bls.gov.", "A"): ("93.184.216.34",),
            ("proxy.invalid.", "A"): ("192.0.2.1",),
        })
        raw, secured, context = Mock(), Mock(), Mock()
        raw.makefile.return_value = io.BytesIO(b"HTTP/1.0 200 Connection established\r\n\r\n")
        context.wrap_socket.return_value = secured
        with patch("dns.query.udp", side_effect=response) as query, \
                patch("market_intelligence.providers.socket.socket", return_value=raw), \
                patch("market_intelligence.providers.ssl.create_default_context", return_value=context), \
                patch.object(PinnedHTTPSConnection, "getresponse", return_value=FakeResponse()), \
                patch("socket.create_connection", side_effect=AssertionError("direct fallback")):
            transport = CalendarTransport(proxy_url="http://proxy.invalid:8080")
            self.assertEqual(transport.fetch(BLS_CALENDAR_URL), b"calendar")
        self.assertEqual([call.args[0].question[0].name.to_text() for call in query.call_args_list],
                         ["www.bls.gov.", "www.bls.gov.", "proxy.invalid.", "proxy.invalid."])
        self.assertTrue(all(call.args[1] == SERVER for call in query.call_args_list))
        raw.connect.assert_called_once_with(("192.0.2.1", 8080))
        self.assertIn(b"CONNECT 93.184.216.34:443 ", raw.sendall.call_args.args[0])
        context.wrap_socket.assert_called_once_with(
            raw, server_hostname="www.bls.gov", do_handshake_on_connect=False)

    def test_proxy_dns_failure_cannot_fall_back_to_direct_connection(self):
        response = answer_with({("www.bls.gov.", "A"): ("93.184.216.34",)},
                               nxdomain=("proxy.invalid.",))
        with patch("dns.query.udp", side_effect=response), \
                patch("market_intelligence.providers.socket.socket") as proxy_socket, \
                patch("market_intelligence.providers.ssl.create_default_context", return_value=Mock()), \
                patch("socket.create_connection", side_effect=AssertionError("direct fallback")) as direct:
            transport = CalendarTransport(proxy_url="http://proxy.invalid:8080")
            with self.assertRaises(ProviderError) as caught:
                transport.fetch(BLS_CALENDAR_URL)
        self.assertEqual(caught.exception.code, "source_unavailable")
        proxy_socket.assert_not_called()
        direct.assert_not_called()

    def test_override_does_not_bypass_public_address_validation(self):
        response = answer_with({
            ("www.bls.gov.", "A"): ("93.184.216.34", "127.0.0.1"),
        })
        factory = Mock()
        with patch("dns.query.udp", side_effect=response):
            transport = CalendarTransport(connection_factory=factory, proxy_url="")
            with self.assertRaises(ProviderError) as caught:
                transport.fetch(BLS_CALENDAR_URL)
        self.assertEqual(caught.exception.code, "address_refused")
        factory.assert_not_called()

    def test_invalid_override_is_reported_without_connecting(self):
        factory = Mock()
        with patch.dict(os.environ, {"TRADER_DNS_IPV4": "invalid-private-setting"}), \
                self.assertLogs("market_intelligence.providers", level="WARNING") as logs:
            transport = CalendarTransport(connection_factory=factory, proxy_url="")
            with self.assertRaises(ProviderError) as caught:
                transport.fetch(BLS_CALENDAR_URL)
        self.assertEqual(caught.exception.code, "source_unavailable")
        self.assertIn("category=dns_configuration", logs.output[0])
        self.assertNotIn("invalid-private-setting", logs.output[0])
        factory.assert_not_called()

    def test_all_hosted_ai_adapters_use_the_override(self):
        examples = (
            ("openai", "api.openai.com", {
                "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "{}"}}]}),
            ("anthropic", "api.anthropic.com", {
                "type": "message", "role": "assistant", "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "{}"}]}),
            ("gemini", "generativelanguage.googleapis.com", {
                "candidates": [{"finishReason": "STOP",
                                "content": {"role": "model", "parts": [{"text": "{}"}]}}]}),
        )
        for kind, host, payload in examples:
            with self.subTest(kind=kind):
                connection = Mock(sock=None)
                connection.getresponse.return_value = FakeResponse(json.dumps(payload).encode())
                response = answer_with({(host + ".", "A"): ("93.184.216.34",)})
                with patch("dns.query.udp", side_effect=response) as query, \
                        patch("market_intelligence.runtime.configured_proxy_url", return_value=""), \
                        patch("market_intelligence.runtime.PinnedHTTPSConnection",
                              return_value=connection) as factory:
                    result = ProviderProfile("test", kind, "model", "TRADER_AI_TEST").complete(
                        SummaryRequest("system", "{}", 64), threading.Event())
                self.assertEqual(result.text, "{}")
                factory.assert_called_once_with(host, "93.184.216.34", 3.0, proxy=None)
                self.assertEqual([call.args[0].question[0].name.to_text() for call in query.call_args_list],
                                 [host + ".", host + "."])
                self.assertTrue(all(call.args[1] == SERVER for call in query.call_args_list))

    def test_hosted_ai_rejects_mixed_public_and_private_answers(self):
        response = answer_with({
            ("api.openai.com.", "A"): ("93.184.216.34", "127.0.0.1"),
        })
        with patch("dns.query.udp", side_effect=response), \
                patch("market_intelligence.runtime.PinnedHTTPSConnection") as factory:
            with self.assertRaises(AnalysisError) as caught:
                ProviderProfile("test", "openai", "model", "TRADER_AI_TEST").complete(
                    SummaryRequest("system", "{}", 64), threading.Event())
        self.assertEqual(caught.exception.code, "provider_unavailable")
        factory.assert_not_called()

    def test_local_ai_does_not_read_or_use_dns_configuration(self):
        connection = Mock(sock=None)
        connection.getresponse.return_value = FakeResponse(json.dumps({
            "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "{}"}}],
        }).encode())
        with patch.dict(os.environ, {"TRADER_DNS_IPV4": "invalid"}), \
                patch("dns_config.configured_connection_values",
                      side_effect=AssertionError("local AI consulted DNS configuration")), \
                patch("market_intelligence.runtime.http.client.HTTPConnection", return_value=connection) as factory:
            result = ProviderProfile("local", "local", "model").complete(
                SummaryRequest("system", "{}", 64), threading.Event())
        self.assertEqual(result.text, "{}")
        factory.assert_called_once_with("127.0.0.1", 11434, timeout=3.0)

    def test_approved_etoro_transport_uses_override_before_request(self):
        connection = Mock(sock=None)
        connection.getresponse.return_value = Response()
        factory = Mock(return_value=connection)
        before_request = Mock()
        response = answer_with({("public-api.etoro.com.", "A"): ("8.8.8.8",)})
        with patch("dns.query.udp", side_effect=response) as query:
            transport = etoro.EToroTransport(connection_factory=factory, proxy_url="",
                                             before_request=before_request)
            self.assertEqual(transport.get(etoro.PORTFOLIO_PATH, headers()), encoded())
        factory.assert_called_once_with(etoro.HOST, "8.8.8.8", 3.0)
        self.assertEqual(before_request.call_count, 3)
        self.assertEqual([call.args[0].question[0].name.to_text() for call in query.call_args_list],
                         ["public-api.etoro.com.", "public-api.etoro.com."])
        self.assertTrue(all(call.args[1] == SERVER for call in query.call_args_list))

    def test_etoro_rejects_mixed_public_and_private_answers_before_approval(self):
        response = answer_with({
            ("public-api.etoro.com.", "A"): ("8.8.8.8", "127.0.0.1"),
        })
        factory, before_request = Mock(), Mock()
        with patch("dns.query.udp", side_effect=response):
            transport = etoro.EToroTransport(connection_factory=factory, proxy_url="",
                                             before_request=before_request)
            with self.assertRaises(etoro.EToroError) as caught:
                transport.get(etoro.PORTFOLIO_PATH, headers())
        self.assertEqual(caught.exception.code, "address_refused")
        factory.assert_not_called()
        before_request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
