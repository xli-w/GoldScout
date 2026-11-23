from datetime import datetime, timedelta, timezone
import base64
import importlib.util
import io
import json
import os
import socket
import ssl
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

import proxy_config

from market_intelligence.providers import (
    BEA_CALENDAR_URL, BLS_CALENDAR_URL, BOE_MPC_DATES_URL, FED_NEWS_URL,
    ONS_CANCELLED_URL, ONS_UPCOMING_URL, CalendarTransport, PinnedHTTPSConnection,
    ProviderError, parse_bea_calendar, parse_bls_calendar, parse_boe_mpc_calendar,
    parse_ons_calendar,
)
from market_intelligence.contracts import ServiceFailure
from market_intelligence.service import IntelligenceService
from market_intelligence.__main__ import main
from proxy_config import ProxyConfigurationError
from test_intelligence_controls import IntelligenceStoreFixture


def calendar(start="DTSTART;TZID=America/New_York:20261113T083000", extra="", title="Consumer Price Index", uid="release-1@bls.gov"):
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Offline fixture//EN\r\n"
            "BEGIN:VEVENT\r\nUID:" + uid + "\r\nSUMMARY:" + title + "\r\n" + start + "\r\n" + extra +
            "END:VEVENT\r\nEND:VCALENDAR\r\n").encode("utf-8")


def ons_release(uri, title, release_date, cancelled=False, finalised=True,
                postponed=False, provisional_date=None, date_changes=None):
    description = {
        "title": title, "release_date": release_date, "cancelled": cancelled,
        "finalised": finalised, "postponed": postponed,
    }
    if provisional_date is not None:
        description["provisional_date"] = provisional_date
    result = {"uri": uri, "description": description}
    if date_changes is not None:
        result["date_changes"] = date_changes
    return result


def ons_response(*releases):
    return json.dumps({"breakdown": {"total": len(releases)}, "releases": releases}).encode("utf-8")


class FakeResponse:
    def __init__(self, body=b"calendar", status=200, headers=None):
        self.body = io.BytesIO(body)
        self.status = status
        self.headers = {"Content-Type": "text/calendar", **(headers or {})}

    def getheader(self, name, default=None):
        return self.headers.get(name, default)

    def read1(self, maximum):
        return self.body.read1(maximum)

    def close(self):
        self.body.close()


class IntelligenceCalendarTransportTests(unittest.TestCase):
    def setUp(self):
        self.settings_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.settings_directory.cleanup)
        self.connection_path_patch = patch.object(
            proxy_config, "CONNECTIONS_PATH",
            Path(self.settings_directory.name) / ".goldscout-connections.json")
        self.connection_path_patch.start()
        self.addCleanup(self.connection_path_patch.stop)
        self.connection = Mock(sock=None)
        self.response = FakeResponse()
        self.connection.getresponse.return_value = self.response
        self.factory = Mock(return_value=self.connection)
        self.resolver = Mock(return_value=[(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 443))])
        self.proxy_records = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("192.0.2.1", 8080))]
        self.transport = CalendarTransport(self.resolver, self.factory, monotonic=lambda: 0.0)

    def test_only_allowlisted_endpoint_is_requested_and_address_is_pinned(self):
        self.assertEqual(self.transport.fetch(BLS_CALENDAR_URL), b"calendar")
        self.factory.assert_called_once_with("www.bls.gov", "93.184.216.34", 3.0)
        args = self.connection.request.call_args
        self.assertEqual(args.args[:2], ("GET", "/schedule/news_release/bls.ics"))
        self.assertEqual(args.kwargs["headers"]["Accept-Encoding"], "identity")
        self.assertTrue(self.connection.close.called)

    def test_bea_endpoint_uses_its_own_host_and_path(self):
        self.assertEqual(self.transport.fetch(BEA_CALENDAR_URL), b"calendar")
        self.resolver.assert_called_once_with("www.bea.gov", 443, type=socket.SOCK_STREAM)
        self.factory.assert_called_once_with("www.bea.gov", "93.184.216.34", 3.0)
        self.assertEqual(self.connection.request.call_args.args[:2],
                         ("GET", "/news/schedule/ics/online-calendar-subscription.ics"))

    def test_uk_endpoints_use_source_specific_hosts_paths_and_content_types(self):
        cases = (
            (ONS_UPCOMING_URL, "api.beta.ons.gov.uk",
             "/v1/search/releases?limit=1000&sort=release_date_asc&release-type=type-upcoming",
             "application/json"),
            (ONS_CANCELLED_URL, "api.beta.ons.gov.uk",
             "/v1/search/releases?limit=1000&sort=release_date_asc&release-type=type-cancelled",
             "application/json"),
            (BOE_MPC_DATES_URL, "www.bankofengland.co.uk",
             "/monetary-policy/upcoming-mpc-dates", "text/html"),
        )
        for url, hostname, path, content_type in cases:
            with self.subTest(url=url):
                self.connection.getresponse.return_value = FakeResponse(
                    headers={"Content-Type": content_type})
                self.assertEqual(self.transport.fetch(url), b"calendar")
                self.resolver.assert_called_with(hostname, 443, type=socket.SOCK_STREAM)
                self.assertEqual(self.factory.call_args.args[:2], (hostname, "93.184.216.34"))
                request = self.connection.request.call_args
                self.assertEqual(request.args[:2], ("GET", path))
                self.assertEqual(request.kwargs["headers"]["Accept"], content_type)
                self.assertEqual(request.kwargs["headers"]["User-Agent"],
                                 "GoldScoutMarketIntelligence/1.0 (+https://github.com/xli-w/GoldScout)")

    def test_supported_content_registry_uses_real_allowlist_without_expanding_discovery(self):
        from urllib.parse import urlsplit
        from market_intelligence.content import CONTENT_SOURCES
        for source in CONTENT_SOURCES.values():
            with self.subTest(source=source.institution, url=source.url):
                if not source.supported:
                    before = self.resolver.call_count
                    with self.assertRaises(ProviderError) as refused:
                        self.transport.fetch(source.url)
                    self.assertEqual(refused.exception.code, "endpoint_refused")
                    self.assertEqual(self.resolver.call_count, before)
                    continue
                mime = "application/rss+xml" if source.url.endswith(".xml") else "text/html"
                self.connection.getresponse.return_value = FakeResponse(headers={"Content-Type": mime})
                self.assertEqual(self.transport.fetch(source.url), b"calendar")
                url = urlsplit(source.url)
                self.resolver.assert_called_with(url.hostname, 443, type=socket.SOCK_STREAM)
                self.assertEqual(self.connection.request.call_args.args[:2], ("GET", url.path))

    def test_authenticated_proxy_preserves_destination_pin_and_tls_name(self):
        proxy = "http://sample:p%40ss@proxy.invalid:8080"
        for url, hostname, content_type in (
            (BLS_CALENDAR_URL, "www.bls.gov", "text/calendar"),
            (BEA_CALENDAR_URL, "www.bea.gov", "text/calendar"),
            (FED_NEWS_URL, "www.federalreserve.gov", "application/rss+xml"),
        ):
            with self.subTest(url=url):
                raw, secured, context = Mock(), Mock(), Mock()
                raw.makefile.return_value = io.BytesIO(b"HTTP/1.0 200 Connection established\r\n\r\n")
                context.wrap_socket.return_value = secured
                response = FakeResponse(headers={"Content-Type": content_type})
                with patch.dict(os.environ, {"TRADER_PROXY_URL": proxy}), \
                        patch("market_intelligence.providers.socket.getaddrinfo", return_value=self.proxy_records) as resolve_proxy, \
                        patch("market_intelligence.providers.socket.socket", return_value=raw), \
                        patch("market_intelligence.providers.ssl.create_default_context", return_value=context), \
                        patch.object(PinnedHTTPSConnection, "getresponse", return_value=response):
                    transport = CalendarTransport(self.resolver, monotonic=lambda: 0.0)
                    self.assertEqual(transport.fetch(url), b"calendar")

                resolve_proxy.assert_called_once_with("proxy.invalid", 8080, type=socket.SOCK_STREAM)
                raw.connect.assert_called_once_with(("192.0.2.1", 8080))
                tunnel = raw.sendall.call_args.args[0]
                self.assertIn(b"CONNECT 93.184.216.34:443 ", tunnel)
                self.assertIn(b"Proxy-Authorization: Basic " + base64.b64encode(b"sample:p@ss"), tunnel)
                origin_request = secured.sendall.call_args.args[0]
                self.assertIn(f"Host: {hostname}\r\n".encode("ascii"), origin_request)
                self.assertNotIn(b"Proxy-Authorization", origin_request)
                context.wrap_socket.assert_called_once_with(
                    raw, server_hostname=hostname, do_handshake_on_connect=False)

    def test_malformed_dotenv_proxy_never_becomes_direct(self):
        for line in ("TRADER_PROXY_URL=\"http://sample:secret@proxy.invalid:8080\n",
                     "export TRADER_PROXY_URL='http://sample:secret@proxy.invalid:8080\n",
                     "TRADER_PROXY_URL\n", "OTHER_SETTING=\"unterminated\n"):
            with self.subTest(line=line), patch.dict(os.environ, {"TRADER_PROXY_URL": "http://environment.invalid:8080"}), \
                    patch("proxy_config._dotenv_loading_disabled", return_value=False), \
                    patch("proxy_config._read_proxy_dotenv",
                          side_effect=lambda names: proxy_config._parse_proxy_dotenv(io.StringIO(line), names)):
                with self.assertRaises(ProxyConfigurationError) as error:
                    CalendarTransport(self.resolver)
                self.assertNotIn("secret", str(error.exception))
        self.resolver.assert_not_called()

    def test_dotenv_parser_returns_only_requested_last_literal_values(self):
        contents = ("ALPACA_SECRET_KEY=unused-sentinel\n"
                    "TRADER_PROXY_URL=http://first.invalid:8080\n"
                    "TRADER_PROXY_URL='http://sample:p%40ss@proxy.invalid:8080'\n"
                    "TRADER_AI_TEST=synthetic-key\n")
        values = proxy_config._parse_proxy_dotenv(
            io.StringIO(contents), ("TRADER_PROXY_URL", "TRADER_AI_TEST"))
        self.assertEqual(values, {"TRADER_PROXY_URL": "http://sample:p%40ss@proxy.invalid:8080",
                                  "TRADER_AI_TEST": "synthetic-key"})
        self.assertEqual(proxy_config._parse_proxy_dotenv(
            io.StringIO("TRADER_PROXY_URL=\n"), ("TRADER_PROXY_URL",)), {"TRADER_PROXY_URL": ""})
        self.assertEqual(proxy_config._parse_proxy_dotenv(
            io.StringIO("OTHER=value\n"), ("TRADER_PROXY_URL",)), {})
        self.assertEqual(os.environ["ALPACA_SECRET_KEY"], "")

    def test_synthetic_connection_file_overrides_environment_and_missing_uses_environment(self):
        names = ("TRADER_PROXY_URL", "TRADER_AI_TEST", "GOLDSCOUT_ETORO_API_KEY")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.txt"
            path.write_text("TRADER_PROXY_URL=http://file.invalid:8080\n"
                            "TRADER_AI_TEST=\n", encoding="utf-8")
            environment = {"TRADER_PROXY_URL": "http://environment.invalid:8080",
                           "TRADER_AI_TEST": "environment-key",
                           "GOLDSCOUT_ETORO_API_KEY": "environment-api"}
            with patch.dict(os.environ, environment), \
                    patch("proxy_config._dotenv_loading_disabled", return_value=False), \
                    patch("proxy_config._read_proxy_dotenv",
                          side_effect=lambda requested: proxy_config._read_connection_file(path, requested)):
                self.assertEqual(proxy_config.configured_connection_values(*names), {
                    "TRADER_PROXY_URL": "http://file.invalid:8080",
                    "TRADER_AI_TEST": "", "GOLDSCOUT_ETORO_API_KEY": "environment-api"})
                self.assertEqual(os.environ["TRADER_AI_TEST"], "environment-key")
                path.write_bytes(b"TRADER_PROXY_URL=\xff")
                with self.assertRaises(ProxyConfigurationError):
                    proxy_config.configured_proxy_url()

    def test_existing_but_unreadable_connection_file_never_falls_back(self):
        names = ("TRADER_PROXY_URL",)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.txt"
            self.assertEqual(proxy_config._read_connection_file(path, names), {})
            path.write_text("TRADER_PROXY_URL=http://fixture.invalid:8080\n", encoding="utf-8")

            class BrokenFile(io.StringIO):
                def read(self, *args, **kwargs):
                    raise FileNotFoundError("synthetic-private-value")

            for operation in ("open", "read"):
                with self.subTest(operation=operation), \
                        patch.object(Path, "open") as opener, \
                        patch.dict(os.environ, {"TRADER_PROXY_URL": "http://environment.invalid:8080"}), \
                        patch("proxy_config._dotenv_loading_disabled", return_value=False), \
                        patch("proxy_config._read_proxy_dotenv",
                              side_effect=lambda requested: proxy_config._read_connection_file(path, requested)):
                    if operation == "open":
                        opener.side_effect = FileNotFoundError("synthetic-private-value")
                    else:
                        opener.return_value = BrokenFile()
                    with self.assertRaises(ProxyConfigurationError) as error:
                        CalendarTransport(self.resolver)
                    self.assertNotIn("synthetic-private-value", str(error.exception))
            self.resolver.assert_not_called()

    def test_dotenv_key_matching_follows_environment_case_rules(self):
        text = "trader_proxy_url=http://file.invalid:8080\nTRADER_AI_TEST=\n"
        names = ("TRADER_PROXY_URL", "TRADER_AI_test")
        for system, expected in (("nt", {"TRADER_PROXY_URL": "http://file.invalid:8080",
                                        "TRADER_AI_test": ""}),
                                 ("posix", {})):
            with self.subTest(system=system), patch.object(proxy_config.os, "name", system):
                self.assertEqual(proxy_config._parse_proxy_dotenv(io.StringIO(text), names), expected)

    def test_disabled_file_loading_keeps_environment_credentials(self):
        environment = {"PYTHON_DOTENV_DISABLED": "1", "TRADER_AI_TEST": "environment-ai",
                       "GOLDSCOUT_ETORO_USER_KEY": "environment-user"}
        with patch.dict(os.environ, environment), \
                patch("proxy_config._read_proxy_dotenv", side_effect=AssertionError("file must not be read")):
            self.assertEqual(proxy_config.configured_connection_values(
                "TRADER_AI_TEST", "GOLDSCOUT_ETORO_USER_KEY"),
                {"TRADER_AI_TEST": "environment-ai", "GOLDSCOUT_ETORO_USER_KEY": "environment-user"})

    def test_proxy_wire_debug_does_not_print_authentication(self):
        proxy = "http://sample:secret@proxy.invalid:8080"
        raw, secured, context = Mock(), Mock(), Mock()
        raw.makefile.return_value = io.BytesIO(b"HTTP/1.0 200 Connection established\r\n\r\n")
        context.wrap_socket.return_value = secured
        connection = PinnedHTTPSConnection("www.bls.gov", "93.184.216.34", 3.0,
                                          proxy_config.parse_proxy_url(proxy))
        connection._context = context
        connection.set_debuglevel(1)
        output = io.StringIO()
        with patch.object(connection, "_connect_proxy", return_value=raw), \
                patch.object(connection, "getresponse", return_value=FakeResponse()), redirect_stdout(output):
            transport = CalendarTransport(self.resolver, Mock(return_value=connection),
                                          monotonic=lambda: 0.0, proxy_url=proxy)
            self.assertEqual(transport.fetch(BLS_CALENDAR_URL), b"calendar")
        self.assertNotIn("secret", output.getvalue())
        self.assertNotIn(base64.b64encode(b"sample:secret").decode("ascii"), output.getvalue())

    def test_cancellation_during_proxy_socket_connect_stops_other_addresses(self):
        raw, second, context = Mock(), Mock(), Mock()
        transport = CalendarTransport(self.resolver, monotonic=lambda: 0.0,
                                      proxy_url="http://proxy.invalid:8080")
        records = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", 8080)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.2", 8080)),
        ]
        def cancel_connect(address):
            transport.cancel()
            raise OSError("cancelled")
        raw.connect.side_effect = cancel_connect
        with patch("market_intelligence.providers.socket.getaddrinfo", return_value=records), \
                patch("market_intelligence.providers.socket.socket", side_effect=[raw, second]), \
                patch("market_intelligence.providers.ssl.create_default_context", return_value=context):
            with self.assertRaises(ProviderError) as error:
                transport.fetch(BLS_CALENDAR_URL)
        self.assertEqual(error.exception.code, "request_timeout")
        raw.connect.assert_called_once()
        raw.shutdown.assert_called_once_with(socket.SHUT_RDWR)
        second.connect.assert_not_called()
        context.wrap_socket.assert_not_called()

    def test_proxy_refusal_does_not_fall_back_to_direct_connection(self):
        raw, context = Mock(), Mock()
        raw.makefile.return_value = io.BytesIO(b"HTTP/1.0 407 Proxy Authentication Required\r\n\r\n")
        with patch("market_intelligence.providers.socket.getaddrinfo", return_value=self.proxy_records), \
                patch("market_intelligence.providers.socket.socket", return_value=raw), \
                patch("market_intelligence.providers.ssl.create_default_context", return_value=context):
            transport = CalendarTransport(self.resolver, monotonic=lambda: 0.0,
                                          proxy_url="http://sample:secret@proxy.invalid:8080")
            with self.assertRaises(ProviderError) as error:
                transport.fetch(BLS_CALENDAR_URL)
        self.assertEqual(error.exception.code, "source_unavailable")
        self.assertNotIn("secret", str(error.exception))
        raw.connect.assert_called_once_with(("192.0.2.1", 8080))
        context.wrap_socket.assert_not_called()

    def test_direct_rss_transport_reads_only_proxy_from_dotenv(self):
        proxy = "http://sample:secret@proxy.invalid:8080"
        response = FakeResponse(headers={"Content-Type": "application/rss+xml"})
        self.connection.getresponse.return_value = response
        with patch.dict(os.environ, {"TRADER_PROXY_URL": ""}), \
                patch("proxy_config._dotenv_loading_disabled", return_value=False), \
                patch("proxy_config._read_proxy_dotenv", return_value={"TRADER_PROXY_URL": proxy}) as load:
            os.environ.pop("TRADER_PROXY_URL")
            transport = CalendarTransport(self.resolver, self.factory, monotonic=lambda: 0.0)
            self.assertEqual(transport.fetch(FED_NEWS_URL), b"calendar")
            self.assertEqual(os.environ["ALPACA_SECRET_KEY"], "")
        self.assertEqual(self.factory.call_args.args[:3],
                         ("www.federalreserve.gov", "93.184.216.34", 3.0))
        self.assertEqual(self.factory.call_args.args[3].url, proxy)
        load.assert_called_once()

    def test_dotenv_proxy_overrides_empty_environment_setting(self):
        with patch.dict(os.environ, {"TRADER_PROXY_URL": ""}), \
                patch("proxy_config._dotenv_loading_disabled", return_value=False), \
                patch("proxy_config._read_proxy_dotenv",
                      return_value={"TRADER_PROXY_URL": "http://proxy.invalid:8080"}) as load:
            transport = CalendarTransport(self.resolver, self.factory, monotonic=lambda: 0.0)
            self.assertEqual(transport.fetch(BLS_CALENDAR_URL), b"calendar")
        self.assertEqual(self.factory.call_args.args[3].host, "proxy.invalid")
        load.assert_called_once()

    def test_empty_dotenv_proxy_explicitly_disables_environment_proxy(self):
        with patch.dict(os.environ, {"TRADER_PROXY_URL": "http://environment.invalid:8080"}), \
                patch("proxy_config._dotenv_loading_disabled", return_value=False), \
                patch("proxy_config._read_proxy_dotenv", return_value={"TRADER_PROXY_URL": ""}):
            transport = CalendarTransport(self.resolver, self.factory, monotonic=lambda: 0.0)
            self.assertEqual(transport.fetch(BLS_CALENDAR_URL), b"calendar")
        self.factory.assert_called_once_with("www.bls.gov", "93.184.216.34", 3.0)

    def test_missing_dotenv_proxy_falls_back_to_environment(self):
        with patch.dict(os.environ, {"TRADER_PROXY_URL": "http://environment.invalid:8080"}), \
                patch("proxy_config._dotenv_loading_disabled", return_value=False), \
                patch("proxy_config._read_proxy_dotenv", return_value={}):
            transport = CalendarTransport(self.resolver, self.factory, monotonic=lambda: 0.0)
            self.assertEqual(transport.fetch(BLS_CALENDAR_URL), b"calendar")
        self.assertEqual(self.factory.call_args.args[3].host, "environment.invalid")

    def test_disabled_dotenv_never_reads_proxy_file(self):
        with patch.dict(os.environ, {"TRADER_PROXY_URL": "", "PYTHON_DOTENV_DISABLED": "1"}), \
                patch("proxy_config._read_proxy_dotenv") as load:
            os.environ.pop("TRADER_PROXY_URL")
            transport = CalendarTransport(self.resolver, self.factory, monotonic=lambda: 0.0)
            self.assertEqual(transport.fetch(BLS_CALENDAR_URL), b"calendar")
        self.factory.assert_called_once_with("www.bls.gov", "93.184.216.34", 3.0)
        load.assert_not_called()

    def test_proxy_does_not_bypass_source_address_validation(self):
        self.resolver.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
        transport = CalendarTransport(self.resolver, proxy_url="http://proxy.invalid:8080")
        with patch("market_intelligence.providers.socket.socket") as connect:
            with self.assertRaises(ProviderError) as error:
                transport.fetch(BLS_CALENDAR_URL)
        self.assertEqual(error.exception.code, "address_refused")
        connect.assert_not_called()

    def test_invalid_proxy_is_refused_before_any_source_connection(self):
        with self.assertRaises(ProxyConfigurationError) as error:
            CalendarTransport(self.resolver, proxy_url="https://sample:secret@proxy.invalid:8080")
        self.assertNotIn("secret", str(error.exception))
        self.resolver.assert_not_called()

    def test_proxy_tunnels_ipv6_address_without_authentication(self):
        address = "2606:4700:4700::1111"
        self.resolver.return_value = [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", (address, 443, 0, 0))]
        raw, secured, context = Mock(), Mock(), Mock()
        raw.makefile.return_value = io.BytesIO(b"HTTP/1.0 200 Connection established\r\n\r\n")
        context.wrap_socket.return_value = secured
        with patch("market_intelligence.providers.socket.getaddrinfo", return_value=self.proxy_records), \
                patch("market_intelligence.providers.socket.socket", return_value=raw), \
                patch("market_intelligence.providers.ssl.create_default_context", return_value=context), \
                patch.object(PinnedHTTPSConnection, "getresponse", return_value=FakeResponse()):
            transport = CalendarTransport(self.resolver, monotonic=lambda: 0.0,
                                          proxy_url="http://proxy.invalid:8080")
            self.assertEqual(transport.fetch(BLS_CALENDAR_URL), b"calendar")
        tunnel = raw.sendall.call_args.args[0]
        self.assertIn(b"CONNECT [2606:4700:4700::1111]:443 ", tunnel)
        self.assertNotIn(b"Proxy-Authorization", tunnel)

    def test_cancellation_during_proxy_connect_stops_before_tls(self):
        raw, context = Mock(), Mock()
        transport = CalendarTransport(self.resolver, monotonic=lambda: 0.0,
                                      proxy_url="http://proxy.invalid:8080")
        with patch("market_intelligence.providers.socket.getaddrinfo", return_value=self.proxy_records), \
                patch("market_intelligence.providers.socket.socket", return_value=raw), \
                patch("market_intelligence.providers.ssl.create_default_context", return_value=context), \
                patch.object(PinnedHTTPSConnection, "_tunnel", side_effect=transport.cancel):
            with self.assertRaises(ProviderError) as error:
                transport.fetch(BLS_CALENDAR_URL)
        self.assertEqual(error.exception.code, "request_timeout")
        raw.shutdown.assert_called_once_with(socket.SHUT_RDWR)
        context.wrap_socket.assert_not_called()

    def test_arbitrary_urls_are_refused_without_dns_or_requests(self):
        for url in ("http://www.bls.gov/schedule/news_release/bls.ics", "https://127.0.0.1/", BLS_CALENDAR_URL + "?redirect=1"):
            with self.subTest(url=url), self.assertRaises(ProviderError):
                self.transport.fetch(url)
        self.resolver.assert_not_called()
        self.factory.assert_not_called()

    def test_private_reserved_and_multicast_dns_results_are_refused(self):
        for address in ("127.0.0.1", "10.0.0.1", "169.254.169.254", "203.0.113.1", "224.0.0.1", "::1"):
            self.resolver.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))]
            with self.subTest(address=address), self.assertRaises(ProviderError):
                self.transport.fetch(BLS_CALENDAR_URL)
        self.factory.assert_not_called()

    def test_redirects_compression_and_unexpected_content_are_refused(self):
        for response in (FakeResponse(status=302, headers={"Location": "https://127.0.0.1/"}),
                         FakeResponse(headers={"Content-Encoding": "gzip"}),
                         FakeResponse(headers={"Content-Type": "text/html"})):
            self.connection.getresponse.return_value = response
            with self.assertRaises(ProviderError):
                self.transport.fetch(BLS_CALENDAR_URL)
        self.assertEqual(self.factory.call_count, 3)

    def test_declared_streamed_and_incomplete_bodies_are_bounded(self):
        for response in (FakeResponse(headers={"Content-Length": "1048577"}),
                         FakeResponse(body=b"x" * 1048577), FakeResponse(body=b"x", headers={"Content-Length": "2"}),
                         FakeResponse(headers={"Content-Length": "invalid"})):
            self.connection.getresponse.return_value = response
            with self.assertRaises(ProviderError):
                self.transport.fetch(BLS_CALENDAR_URL)

    def test_deadline_after_dns_or_during_read_is_refused(self):
        for readings in ([0.0, 15.0], [0.0, 0.0, 15.0]):
            transport = CalendarTransport(self.resolver, self.factory, monotonic=Mock(side_effect=readings))
            with self.assertRaises(ProviderError) as result:
                transport.fetch(BLS_CALENDAR_URL)
            self.assertEqual(result.exception.code, "request_timeout")

    def test_pre_cancelled_job_never_resolves_or_connects(self):
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(ProviderError):
            self.transport.fetch(BLS_CALENDAR_URL, cancel_event=cancelled)
        self.resolver.assert_not_called()
        self.factory.assert_not_called()

    def test_cancellation_during_tls_handshake_closes_wrapped_socket_before_get(self):
        connection = PinnedHTTPSConnection("www.bls.gov", "93.184.216.34", 3.0)
        self.assertTrue(connection._context.check_hostname)
        self.assertEqual(connection._context.verify_mode, ssl.CERT_REQUIRED)
        raw, wrapped = Mock(), Mock()
        def wrap_socket(sock, **kwargs):
            raw.fileno.return_value = -1
            wrapped.fileno.return_value = 123
            return wrapped
        connection._context = Mock()
        connection._context.wrap_socket.side_effect = wrap_socket
        transport = CalendarTransport(self.resolver, Mock(return_value=connection), monotonic=lambda: 0.0)
        wrapped.do_handshake.side_effect = transport.cancel
        with patch("market_intelligence.providers.socket.create_connection", return_value=raw):
            with self.assertRaises(ProviderError):
                transport.fetch(BLS_CALENDAR_URL)
        connection._context.wrap_socket.assert_called_once_with(raw, server_hostname="www.bls.gov", do_handshake_on_connect=False)
        wrapped.shutdown.assert_called_once_with(socket.SHUT_RDWR)
        wrapped.sendall.assert_not_called()


class UKCalendarParserTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)

    def test_ons_parser_preserves_selected_release_state_and_date_change_notes(self):
        upcoming = ons_response(
            ons_release(
                "/releases/cpi", "Consumer price inflation, October 2026",
                "2026-11-18T07:00:00Z",
                date_changes=[{"previous_date": "2026-11-17",
                               "change_notice": "The release date has changed."}],
            ),
            ons_release(
                "/releases/labour-market", "Labour market overview, November 2026",
                None, finalised=False, provisional_date="December 2026",
            ),
            ons_release(
                "/releases/other", "Population estimates, mid-2026", "2026-12-01T00:00:00Z",
            ),
        )
        cancelled = ons_response(ons_release(
            "/releases/cancelled-cpi", "Consumer price inflation, July 2026",
            "2026-11-20T07:00:00Z", cancelled=True,
        ))
        batch = parse_ons_calendar(upcoming, cancelled, self.now, self.now)

        self.assertTrue(batch.complete_snapshot)
        self.assertEqual(batch.source.source, "ons")
        self.assertEqual(batch.source.source_urls, (ONS_UPCOMING_URL, ONS_CANCELLED_URL))
        self.assertEqual(batch.ignored_events, 1)
        events = {event.event_id: event for event in batch.events}
        cpi = next(event for event in events.values() if event.title.startswith("Consumer price"))
        labour = next(event for event in events.values() if event.title.startswith("Labour market"))
        cancelled_event = next(event for event in events.values() if event.status == "cancelled")
        self.assertEqual((cpi.category, cpi.status, cpi.symbols), ("inflation", "scheduled", ()))
        self.assertEqual(cpi.scheduled_at, datetime(2026, 11, 18, 7, tzinfo=timezone.utc))
        self.assertEqual(cpi.available_at, self.now)
        self.assertEqual(cpi.source_notes,
                         ("Previous scheduled date: 2026-11-17", "The release date has changed."))
        self.assertEqual((labour.category, labour.status), ("employment", "tentative"))
        self.assertEqual(labour.source_notes, ("Provisional date: December 2026",))
        self.assertEqual(cancelled_event.status, "cancelled")
        self.assertEqual(len(batch.input_hash), 64)

    def test_ons_parser_rejects_partial_or_misclassified_response(self):
        incomplete = json.dumps({"breakdown": {"total": 1}, "releases": []}).encode("utf-8")
        with self.assertRaises(ProviderError) as result:
            parse_ons_calendar(ons_response(), incomplete, self.now, self.now)
        self.assertEqual(result.exception.code, "response_incomplete")
        inconsistent = ons_response(ons_release(
            "/releases/cancelled", "Consumer price inflation", "2026-11-20T07:00:00Z",
            cancelled=True,
        ))
        with self.assertRaises(ProviderError) as result:
            parse_ons_calendar(inconsistent, ons_response(), self.now, self.now)
        self.assertEqual(result.exception.code, "invalid_calendar")
        duplicate_field = b'{"breakdown":{"total":0,"total":0},"releases":[]}'
        with self.assertRaises(ProviderError) as result:
            parse_ons_calendar(duplicate_field, ons_response(), self.now, self.now)
        self.assertEqual(result.exception.code, "invalid_calendar")

    def test_boe_parser_converts_confirmed_schedule_using_london_dst(self):
        dates_2026 = (
            "Thursday 5 February", "Thursday 19 March", "Thursday 7 May",
            "Thursday 18 June", "Thursday 6 August", "Thursday 17 September",
            "Thursday 5 November", "Thursday 17 December",
        )
        dates_2027 = (
            "Thursday 4 February", "Thursday 18 March", "Thursday 29 April",
            "Thursday 17 June", "Thursday 5 August", "Thursday 16 September",
            "Thursday 4 November", "Thursday 16 December",
        )
        payload = (
            "<h2>2026 confirmed dates</h2>" + "".join(f"<p>{date}</p>" for date in dates_2026)
            + "<h2>2027 confirmed dates</h2>" + "".join(f"<p>{date}</p>" for date in dates_2027)
            + "<p>Current Bank Rate</p>"
        ).encode("utf-8")
        batch = parse_boe_mpc_calendar(payload, self.now)
        self.assertTrue(batch.complete_snapshot)
        self.assertEqual(batch.source.source, "boe_mpc")
        self.assertEqual(len(batch.events), 9)
        winter = next(event for event in batch.events if event.event_id == "boe-mpc-2026-12-17")
        summer = next(event for event in batch.events if event.event_id == "boe-mpc-2027-06-17")
        self.assertEqual(winter.scheduled_at, datetime(2026, 12, 17, 12, tzinfo=timezone.utc))
        self.assertEqual(summer.scheduled_at, datetime(2027, 6, 17, 11, tzinfo=timezone.utc))
        self.assertTrue(all(event.scheduled_at > self.now for event in batch.events))
        self.assertEqual((summer.category, summer.symbols, summer.source_timezone),
                         ("monetary_policy", (), "Europe/London"))
        self.assertIn("12:00 noon", summer.source_notes[0])

    def test_boe_parser_fails_closed_for_incomplete_or_invalid_year_sections(self):
        payload = b"<h2>2026 confirmed dates</h2><p>Thursday 5 February</p>"
        with self.assertRaises(ProviderError) as result:
            parse_boe_mpc_calendar(payload, self.now)
        self.assertEqual(result.exception.code, "invalid_calendar")
        current_year_only = (
            "<h2>2026 confirmed dates</h2>" +
            "".join(f"<p>{date}</p>" for date in (
                "Thursday 5 February", "Thursday 19 March", "Thursday 7 May",
                "Thursday 18 June", "Thursday 6 August", "Thursday 17 September",
                "Thursday 5 November", "Thursday 17 December",
            )) + "<p>Current Bank Rate</p>"
        ).encode("utf-8")
        with self.assertRaises(ProviderError) as result:
            parse_boe_mpc_calendar(current_year_only, self.now)
        self.assertEqual(result.exception.code, "invalid_calendar")
        invalid_weekday = (
            "<h2>2026 confirmed dates</h2>" +
            "".join(f"<p>{date}</p>" for date in (
                "Thursday 5 February", "Thursday 19 March", "Thursday 7 May",
                "Thursday 18 June", "Thursday 6 August", "Thursday 17 September",
                "Thursday 5 November", "Thursday 18 December",
            )) + "<p>Current Bank Rate</p>"
        ).encode("utf-8")
        with self.assertRaises(ProviderError) as result:
            parse_boe_mpc_calendar(invalid_weekday, self.now)
        self.assertEqual(result.exception.code, "invalid_calendar")


@unittest.skipIf(importlib.util.find_spec("icalendar") is None, "Install requirements-intelligence.txt for ICS tests")
class IntelligenceCalendarServiceTests(IntelligenceStoreFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.transport = Mock()
        self.transport.fetch.return_value = calendar()
        self.elapsed = 0.0

    def service(self, allow_network=True, sources=("bls",)):
        return IntelligenceService(self.store, clock=lambda: self.now, allow_network=allow_network,
                                   calendar_transport=self.transport, monotonic=lambda: self.elapsed,
                                   calendar_sources=sources)

    def complete(self, service):
        service.worker.join(timeout=2)
        self.assertFalse(service.worker.is_alive())
        service.step()

    def test_registered_source_selection_is_explicit_and_validated(self):
        for sources in ((), ("unknown",), ("bls", "bls"), ["bls"], ("bls", [])):
            with self.subTest(sources=sources), self.assertRaises(ValueError):
                self.service(sources=sources)

    def test_supervised_check_fetches_only_the_selected_source_once(self):
        self.store.enqueue(self.request(enabled=True), self.now)
        self.transport.fetch.return_value = calendar(title="Gross Domestic Product", uid="bea-release")
        with self.service(sources=("bls", "bea")) as service:
            result, receipts = service.check_once("bea")
            self.assertEqual(result, {"source": "bea", "status": "succeeded", "error_code": None})
            self.assertEqual(len(receipts), 1)
            self.elapsed = 7200.0
            service.step()
        self.assertEqual([call.args[0] for call in self.transport.fetch.call_args_list], [BEA_CALENDAR_URL])
        self.assertEqual({source.source for source in self.store.calendar().sources}, {"bea"})

    def test_supervised_check_does_not_dispatch_when_disabled(self):
        with self.service(sources=("bls", "bea")) as service:
            result, _ = service.check_once("bls")
            self.store.enqueue(self.request(enabled=True), self.now)
            service.step()
        self.assertEqual(result["status"], "not_started")
        self.transport.fetch.assert_not_called()
        self.assertIsNone(self.store.calendar())

    def test_supervised_check_records_failure_without_retry(self):
        self.store.enqueue(self.request(enabled=True), self.now)
        self.transport.fetch.side_effect = ProviderError("http_unavailable")
        with self.service(sources=("bls", "bea")) as service:
            result, _ = service.check_once("bls")
            self.assertEqual(result, {"source": "bls", "status": "failed",
                                      "error_code": "http_unavailable"})
            self.elapsed = 7200.0
            service.step()
        self.assertEqual(self.transport.fetch.call_count, 1)
        self.assertEqual(self.store.calendar().sources[0].status, "unavailable")

    def test_supervised_check_does_not_hide_another_sources_failure(self):
        self.store.enqueue(self.request(enabled=True), self.now)
        self.store.apply_pending(self.now)
        previous = self.store.observe_service(
            "previous", "service-previous", 1, self.now, True, ("bls", "bea"), False, False,
            (ServiceFailure("bea", "source_unavailable", self.now),))
        with self.service(sources=("bls",)) as service:
            result, _ = service.check_once("bls")
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(self.store.service_status(), previous)
        with self.service(sources=("bls", "bea")) as restarted:
            restarted.step()
            self.complete(restarted)
            self.assertEqual(self.store.service_status().failures[0].source, "bea")
            self.complete(restarted)
        self.assertEqual(self.store.service_status().failures, ())

    def test_supervised_check_discards_result_after_settings_change(self):
        self.store.enqueue(self.request(enabled=True), self.now)

        def fetch(url, cancel_event=None):
            self.store.enqueue(self.request("disable", 1, enabled=False), self.now)
            return calendar()

        self.transport.fetch.side_effect = fetch
        with self.service(sources=("bls",)) as service:
            result, _ = service.check_once("bls")
        self.assertEqual(result["status"], "discarded")
        self.assertIsNone(self.store.calendar())
        self.assertEqual(self.transport.fetch.call_count, 1)

    def test_supervised_check_does_not_dispatch_after_deadline(self):
        self.store.enqueue(self.request(enabled=True), self.now)
        ticks = iter((100.0, 146.0))
        with self.service(sources=("bls",)) as service:
            with patch("market_intelligence.service.time.monotonic", side_effect=lambda: next(ticks, 146.0)):
                result, _ = service.check_once("bls")
                service.step()
        self.assertEqual(result["status"], "interrupted")
        self.transport.fetch.assert_not_called()
        self.assertIsNone(self.store.calendar())

    def test_check_worker_started_after_deadline_does_not_fetch(self):
        self.store.enqueue(self.request(enabled=True), self.now)
        current = [100.0]

        def start_late(worker):
            current[0] = 146.0
            worker.run()

        with self.service(sources=("bls",)) as service, \
             patch("market_intelligence.service.time.monotonic", side_effect=lambda: current[0]), \
             patch("market_intelligence.service.threading.Thread.start", new=start_late):
            result, _ = service.check_once("bls")
        self.assertEqual(result["status"], "interrupted")
        self.transport.fetch.assert_not_called()
        self.assertIsNone(self.store.calendar())

    def test_timed_out_check_does_not_publish_a_late_result(self):
        self.store.enqueue(self.request(enabled=True), self.now)
        current = [100.0]

        def late_fetch(*args, **kwargs):
            current[0] = 146.0
            return calendar()

        self.transport.fetch.side_effect = late_fetch
        with self.service(sources=("bls",)) as service, \
             patch("market_intelligence.service.time.monotonic", side_effect=lambda: current[0]), \
             patch("market_intelligence.service.threading.Thread.start", new=lambda worker: worker.run()):
            result, _ = service.check_once("bls")
            service.step()
        self.assertEqual(result["status"], "interrupted")
        self.assertEqual(self.transport.fetch.call_count, 1)
        self.assertIsNone(self.store.calendar())

    def test_check_does_not_publish_when_deadline_passes_during_worker_join(self):
        self.store.enqueue(self.request(enabled=True), self.now)
        current = [100.0]

        def join_after_deadline(worker, timeout=None):
            self.assertFalse(worker.is_alive())
            current[0] = 146.0

        with self.service(sources=("bls",)) as service, \
             patch("market_intelligence.service.time.monotonic", side_effect=lambda: current[0]), \
             patch("market_intelligence.service.threading.Thread.start", new=lambda worker: worker.run()), \
             patch("market_intelligence.service.threading.Thread.join", new=join_after_deadline):
            result, _ = service.check_once("bls")
        self.assertEqual(result["status"], "interrupted")
        self.assertEqual(self.transport.fetch.call_count, 1)
        self.assertIsNone(self.store.calendar())

    def test_check_does_not_publish_when_worker_finishes_after_deadline_check(self):
        self.store.enqueue(self.request(enabled=True), self.now)
        current = [100.0]

        def finish_after_deadline(worker):
            current[0] = 146.0
            return False

        with self.service(sources=("bls",)) as service, \
             patch("market_intelligence.service.time.monotonic", side_effect=lambda: current[0]), \
             patch("market_intelligence.service.threading.Thread.start", new=lambda worker: worker.run()), \
             patch("market_intelligence.service.threading.Thread.join", new=lambda worker, timeout=None: None), \
             patch("market_intelligence.service.threading.Thread.is_alive", new=finish_after_deadline):
            result, _ = service.check_once("bls")
        self.assertEqual(result["status"], "interrupted")
        self.assertEqual(self.transport.fetch.call_count, 1)
        self.assertIsNone(self.store.calendar())

    def test_check_does_not_publish_when_calendar_read_crosses_deadline(self):
        self.store.enqueue(self.request(enabled=True), self.now)
        current = [100.0]
        read_calendar = self.store.calendar

        def read_after_deadline():
            previous = read_calendar()
            current[0] = 146.0
            return previous

        with self.service(sources=("bls",)) as service, \
             patch.object(self.store, "calendar", side_effect=read_after_deadline), \
             patch("market_intelligence.service.time.monotonic", side_effect=lambda: current[0]), \
             patch("market_intelligence.service.threading.Thread.start", new=lambda worker: worker.run()), \
             patch("market_intelligence.service.threading.Thread.join", new=lambda worker, timeout=None: None):
            result, _ = service.check_once("bls")
        self.assertEqual(result["status"], "interrupted")
        self.assertEqual(self.transport.fetch.call_count, 1)
        self.assertIsNone(self.store.calendar())

    def test_bls_and_bea_publish_independent_evidence_for_same_uid(self):
        def fetch(url, cancel_event=None):
            title = "Consumer Price Index" if url == BLS_CALENDAR_URL else "GDP (Second Estimate), Third Quarter 2026"
            return calendar(title=title, uid="shared-uid", extra="SEQUENCE:1\r\n")
        self.transport.fetch.side_effect = fetch
        self.store.enqueue(self.request(enabled=True), self.now)
        with self.service(sources=("bls", "bea")) as service:
            service.step()
            self.complete(service)
            self.assertIsNotNone(service.worker)
            self.complete(service)
            snapshot = self.store.calendar()
            self.assertEqual({source.source for source in snapshot.sources}, {"bls", "bea"})
            self.assertEqual({event.source for event in snapshot.events}, {"bls", "bea"})
            self.assertEqual(len(snapshot.events), 2)
            self.assertEqual(len({event.event_id for event in snapshot.events}), 1)
            self.assertTrue(all(source.status == "unavailable" for source in snapshot.sources))
            self.assertIsNone(service.last_provider_error)
            self.assertEqual([call.args[0] for call in self.transport.fetch.call_args_list],
                             [BLS_CALENDAR_URL, BEA_CALENDAR_URL])

    def test_one_source_failure_does_not_erase_or_mask_the_other(self):
        fail_bls = True
        def fetch(url, cancel_event=None):
            if url == BLS_CALENDAR_URL:
                if fail_bls:
                    raise ProviderError("source_unavailable")
                return calendar()
            return calendar(title="Personal Income and Outlays, October 2026", uid="bea-release")
        self.transport.fetch.side_effect = fetch
        self.store.enqueue(self.request(enabled=True), self.now)
        with self.service(sources=("bls", "bea")) as service:
            service.step()
            self.complete(service)
            self.complete(service)
            self.assertEqual({event.source for event in self.store.calendar().events}, {"bea"})
            self.assertEqual(service.provider_errors, {"bls": "source_unavailable", "bea": None})
            self.assertEqual(service.last_provider_error, "source_unavailable")
            self.elapsed = 299.0
            service.step()
            self.assertEqual(self.transport.fetch.call_count, 2)
            fail_bls = False
            self.elapsed = 300.0
            service.step()
            self.complete(service)
            self.assertEqual({event.source for event in self.store.calendar().events}, {"bls", "bea"})
            self.assertEqual(service.provider_errors, {"bls": None, "bea": None})

    def test_bea_only_selection_never_fetches_bls(self):
        self.transport.fetch.return_value = calendar(title="Personal Income and Outlays, October 2026", uid="bea-release")
        self.store.enqueue(self.request(enabled=True), self.now)
        with self.service(sources=("bea",)) as service:
            service.step()
            self.complete(service)
            self.assertEqual(self.transport.fetch.call_args.args[0], BEA_CALENDAR_URL)
            self.assertEqual({source.source for source in self.store.calendar().sources}, {"bea"})

    def test_ons_complete_snapshots_revision_changes_and_reject_partial_pairs(self):
        first = ons_release(
            "/releases/cpi", "Consumer price inflation, October 2026",
            (self.now + timedelta(days=5)).isoformat(),
            date_changes=[{"previous_date": (self.now + timedelta(days=4)).date().isoformat(),
                           "change_notice": "Release date moved by one day."}],
        )
        revised = ons_release(
            "/releases/cpi", "Consumer price inflation, October 2026",
            (self.now + timedelta(days=8)).isoformat(), postponed=True,
            provisional_date="December 2026",
            date_changes=[{"previous_date": (self.now + timedelta(days=5)).date().isoformat(),
                           "change_notice": "The release has been postponed."}],
        )
        initial_upcoming, cancelled = ons_response(first), ons_response()
        revised_upcoming = ons_response(revised)
        incomplete_cancelled = json.dumps(
            {"breakdown": {"total": 1}, "releases": []}).encode("utf-8")
        self.transport.fetch.side_effect = (
            initial_upcoming, cancelled, revised_upcoming, cancelled,
            revised_upcoming, incomplete_cancelled,
        )
        self.store.enqueue(self.request(enabled=True), self.now)

        with self.service(sources=("ons",)) as service:
            result, _ = service.check_once("ons")
        self.assertEqual(result["status"], "succeeded")
        original = self.store.calendar().events[0]
        self.assertEqual((original.status, original.revision), ("scheduled", 0))
        self.assertIn("Release date moved by one day.", original.source_notes)

        self.now += timedelta(minutes=1)
        with self.service(sources=("ons",)) as service:
            result, _ = service.check_once("ons")
        self.assertEqual(result["status"], "succeeded")
        updated = self.store.calendar().events[0]
        self.assertEqual((updated.status, updated.revision), ("postponed", 1))
        self.assertEqual(updated.source_notes[0], "Previous scheduled date: " +
                         (self.now + timedelta(days=5)).date().isoformat())
        self.assertEqual(updated.available_at, self.now)

        self.now += timedelta(minutes=1)
        with self.service(sources=("ons",)) as service:
            result, _ = service.check_once("ons")
        self.assertEqual((result["status"], result["error_code"]),
                         ("failed", "response_incomplete"))
        snapshot = self.store.calendar()
        self.assertEqual(snapshot.events, (updated,))
        self.assertEqual(snapshot.sources[0].status, "unavailable")
        self.assertEqual(snapshot.sources[0].source_urls, (ONS_UPCOMING_URL, ONS_CANCELLED_URL))
        self.assertEqual(self.transport.fetch.call_count, 6)

    def test_oldest_due_source_runs_first_when_both_are_overdue(self):
        self.transport.fetch.return_value = calendar(title="GDP (Second Estimate)", uid="bea-release")
        self.store.enqueue(self.request(enabled=True), self.now)
        with self.service(sources=("bls", "bea")) as service:
            service.last_settings_revision = 1
            service.next_fetch_at = {"bls": 20.0, "bea": 10.0}
            self.elapsed = 30.0
            service.step()
            service.worker.join(timeout=2)
            self.assertEqual(self.transport.fetch.call_args.args[0], BEA_CALENDAR_URL)

    def test_bea_failure_retains_prior_provenance_while_bls_refreshes(self):
        fail_bea = False
        def fetch(url, cancel_event=None):
            if url == BLS_CALENDAR_URL:
                return calendar()
            if fail_bea:
                raise ProviderError("source_unavailable")
            return calendar(title="Personal Income and Outlays, October 2026", uid="bea-release")
        self.transport.fetch.side_effect = fetch
        self.store.enqueue(self.request(enabled=True), self.now)
        with self.service(sources=("bls", "bea")) as service:
            service.step()
            self.complete(service)
            self.complete(service)
            snapshot = self.store.calendar()
            prior_source = next(source for source in snapshot.sources if source.source == "bea")
            prior_events = tuple(event for event in snapshot.events if event.source == "bea")
            self.now += timedelta(hours=1)
            self.elapsed = 3600.0
            fail_bea = True
            service.step()
            self.complete(service)
            self.complete(service)
            refreshed = self.store.calendar()
            bea = next(source for source in refreshed.sources if source.source == "bea")
            self.assertEqual((bea.checked_at, bea.input_hash, bea.source_url),
                             (prior_source.checked_at, prior_source.input_hash, prior_source.source_url))
            self.assertEqual(tuple(event for event in refreshed.events if event.source == "bea"), prior_events)
            self.assertEqual(next(source for source in refreshed.sources if source.source == "bls").checked_at, self.now)
            self.assertEqual(service.provider_errors, {"bls": None, "bea": "source_unavailable"})

    def test_disabling_a_bea_job_discards_it_and_reenable_resumes_collection(self):
        started, release = threading.Event(), threading.Event()
        def fetch(url, cancel_event=None):
            if url == BLS_CALENDAR_URL:
                return calendar()
            started.set()
            release.wait(timeout=2)
            return calendar(title="GDP (Second Estimate)", uid="bea-release")
        self.transport.fetch.side_effect = fetch
        self.store.enqueue(self.request(enabled=True), self.now)
        with self.service(sources=("bls", "bea")) as service:
            service.step()
            self.complete(service)
            self.assertTrue(started.wait(timeout=1))
            self.store.enqueue(self.request("off", base_revision=1, enabled=False), self.now)
            service.step()
            release.set()
            self.complete(service)
            self.assertEqual({event.source for event in self.store.calendar().events}, {"bls"})
            self.store.enqueue(self.request("on", base_revision=2, enabled=True), self.now)
            service.step()
            self.complete(service)
            self.complete(service)
            self.assertEqual({event.source for event in self.store.calendar().events}, {"bls", "bea"})

    def test_cli_default_explicit_duplicate_and_unknown_source_choices(self):
        for flags, expected in (([], ("bls", "bea")), (["--calendar-source", "bea"], ("bea",)),
                                (["--calendar-source", "bea", "--calendar-source", "bls"], ("bea", "bls"))):
            with self.subTest(flags=flags), patch("market_intelligence.__main__.IntelligenceService") as factory:
                factory.return_value.__enter__.return_value.step.return_value = ()
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(main(["once", "--database", str(self.path)] + flags), 0)
                self.assertEqual(factory.call_args.kwargs["calendar_sources"], expected)
                self.assertFalse(factory.call_args.kwargs["allow_network"])
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(["once", "--database", str(self.path), "--calendar-source", "bls", "--calendar-source", "bls"]), 1)
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as result:
            main(["once", "--database", str(self.path), "--calendar-source", "unknown"])
        self.assertEqual(result.exception.code, 2)

    def test_cli_reads_proxy_only_for_network_enabled_run(self):
        proxy = "http://sample:secret@proxy.invalid:8080"
        with patch.dict(os.environ, {"TRADER_PROXY_URL": ""}), \
                patch("proxy_config._dotenv_loading_disabled", return_value=False), \
                patch("proxy_config._read_proxy_dotenv", return_value={"TRADER_PROXY_URL": proxy}) as load, \
                patch.object(IntelligenceService, "run", autospec=True) as run, \
                redirect_stdout(io.StringIO()):
            os.environ.pop("TRADER_PROXY_URL")
            self.assertEqual(main(["once", "--database", str(self.path)]), 0)
            load.assert_not_called()
            self.assertEqual(main(["run", "--allow-network", "--database", str(self.path)]), 0)
            load.assert_called_once()
            self.assertEqual(run.call_args.args[0].calendar_transport.proxy.url, proxy)

    def test_cli_reports_invalid_proxy_without_exposing_credentials(self):
        output = io.StringIO()
        with patch.dict(os.environ, {"TRADER_PROXY_URL": "https://sample:secret@proxy.invalid:8080"}), \
                redirect_stdout(output):
            result = main(["run", "--allow-network", "--database", str(self.path)])
        self.assertEqual(result, 1)
        self.assertIn("TRADER_PROXY_URL", output.getvalue())
        self.assertNotIn("secret", output.getvalue())

    def test_network_capability_and_master_enablement_are_both_required(self):
        with self.service() as service:
            service.step()
            self.transport.fetch.assert_not_called()
        self.store.enqueue(self.request(enabled=True), self.now)
        with self.service(allow_network=False) as service:
            service.step()
            self.transport.fetch.assert_not_called()
        self.assertIsNone(self.store.calendar())

    def test_calendar_publication_retains_download_provenance_and_hourly_cadence(self):
        self.store.enqueue(self.request(enabled=True), self.now)
        with self.service() as service:
            service.step()
            self.complete(service)
            snapshot = self.store.calendar()
            self.assertEqual(snapshot.revision, 1)
            self.assertEqual(snapshot.sources[0].source_url, BLS_CALENDAR_URL)
            self.assertEqual(len(snapshot.sources[0].input_hash), 64)
            self.assertEqual(len(snapshot.events), 1)
            self.assertEqual(snapshot.sources[0].status, "unavailable")
            service.step()
            self.assertEqual(self.transport.fetch.call_count, 1)
            self.elapsed = 3600.0
            service.step()
            self.complete(service)
            self.assertEqual(self.store.calendar().revision, 2)

    def test_disabling_during_a_job_cancels_and_discards_its_result(self):
        started, release = threading.Event(), threading.Event()
        def fetch(url, cancel_event=None):
            started.set()
            release.wait(timeout=2)
            return calendar()
        self.transport.fetch.side_effect = fetch
        self.store.enqueue(self.request(enabled=True), self.now)
        with self.service() as service:
            service.step()
            self.assertTrue(started.wait(timeout=1))
            self.store.enqueue(self.request("off", base_revision=1, enabled=False), self.now)
            receipts = service.step()
            self.assertEqual(receipts[0].status, "applied")
            self.assertFalse(self.store.settings().enabled)
            release.set()
            self.complete(service)
            self.assertIsNone(self.store.calendar())
            self.assertTrue(self.transport.cancel.called)

    def test_provider_failure_is_visible_and_retried_after_five_minutes(self):
        self.transport.fetch.side_effect = ProviderError("source_unavailable")
        self.store.enqueue(self.request(enabled=True), self.now)
        with self.service() as service:
            service.step()
            self.complete(service)
            self.assertEqual(service.last_provider_error, "source_unavailable")
            self.assertEqual(self.store.calendar().sources[0].status, "unavailable")
            self.assertEqual(self.store.calendar().events, ())
            self.elapsed = 299.0
            service.step()
            self.assertEqual(self.transport.fetch.call_count, 1)
            self.elapsed = 300.0
            self.transport.fetch.side_effect = None
            service.step()
            self.complete(service)
            self.assertIsNone(service.last_provider_error)
            self.assertEqual(len(self.store.calendar().events), 1)

    def test_results_from_a_closed_service_generation_are_not_published(self):
        started, release = threading.Event(), threading.Event()
        def fetch(url, cancel_event=None):
            started.set()
            release.wait(timeout=2)
            return calendar()
        self.transport.fetch.side_effect = fetch
        self.store.enqueue(self.request(enabled=True), self.now)
        service = self.service()
        with service:
            service.step()
            self.assertTrue(started.wait(timeout=1))
        release.set()
        service.worker.join(timeout=2)
        with service:
            service.step()
            self.assertIsNone(self.store.calendar())
            service.worker.join(timeout=2)

    def test_cancel_before_worker_entry_does_not_start_a_fetch(self):
        class DeferredWorker:
            def __init__(self, target, **kwargs):
                self.running = True
                def invoke():
                    self.running = False
                    target()
                self.target = invoke
            def start(self):
                pass
            def is_alive(self):
                return self.running
            def join(self):
                if self.running:
                    self.target()
        self.store.enqueue(self.request(enabled=True), self.now)
        with patch("market_intelligence.service.threading.Thread", DeferredWorker):
            with self.service() as service:
                service.step()
                self.store.enqueue(self.request("off", base_revision=1, enabled=False), self.now)
                service.step()
                service.worker.target()
                self.transport.fetch.assert_not_called()

    def test_publication_clock_is_sampled_after_receiving_completed_result(self):
        self.store.enqueue(self.request(enabled=True), self.now)
        with self.service() as service:
            service.step()
            service.worker.join(timeout=2)
            received = self.now + timedelta(seconds=1)
            batch = parse_bls_calendar(calendar(), received)
            service.results.get_nowait()
            service.results.put_nowait((batch, None, 1, service.generation, "bls"))
            service.clock = Mock(side_effect=[self.now, received + timedelta(seconds=1),
                                              received + timedelta(seconds=1)])
            service.step()
            self.assertGreaterEqual(self.store.calendar().available_at, received)
            self.assertGreaterEqual(self.store.service_status().observed_at, self.store.calendar().available_at)

    def test_older_event_sequence_cannot_resurrect_a_cancellation(self):
        self.transport.fetch.return_value = calendar(extra="SEQUENCE:2\r\nSTATUS:CANCELLED\r\n")
        self.store.enqueue(self.request(enabled=True), self.now)
        with self.service() as service:
            service.step()
            self.complete(service)
            for sequence in (1, 2):
                with self.subTest(sequence=sequence):
                    self.transport.fetch.return_value = calendar(extra=f"SEQUENCE:{sequence}\r\n")
                    self.elapsed += 3600.0
                    service.step()
                    self.complete(service)
                    self.assertEqual(self.store.calendar().events[0].status, "cancelled")
                    self.assertEqual(self.store.calendar().events[0].revision, 2)
                    self.assertEqual(service.last_provider_error, "revision_conflict")
            self.transport.fetch.return_value = calendar(extra="SEQUENCE:3\r\n")
            self.elapsed += 3600.0
            service.step()
            self.complete(service)
            self.assertEqual(self.store.calendar().events[0].status, "scheduled")
            self.assertIsNone(service.last_provider_error)

    def test_omission_and_restart_do_not_erase_event_revision_baseline(self):
        self.transport.fetch.return_value = calendar(extra="SEQUENCE:2\r\nSTATUS:CANCELLED\r\n")
        self.store.enqueue(self.request(enabled=True), self.now)
        with self.service() as service:
            service.step()
            self.complete(service)
            original = self.store.calendar().events[0]
            self.transport.fetch.return_value = b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Offline//EN\r\nEND:VCALENDAR\r\n"
            self.elapsed = 3600.0
            service.step()
            self.complete(service)
            self.assertEqual(self.store.calendar().events, (original,))
        self.transport.fetch.return_value = calendar(extra="SEQUENCE:1\r\n")
        with self.service() as restarted:
            restarted.step()
            self.complete(restarted)
            self.assertEqual(self.store.calendar().events, (original,))
            self.assertEqual(restarted.last_provider_error, "revision_conflict")


@unittest.skipIf(importlib.util.find_spec("icalendar") is None, "Install requirements-intelligence.txt for ICS tests")
class IntelligenceCalendarParserTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)

    def test_regular_release_retains_identity_time_and_provenance(self):
        payload = calendar(extra="SEQUENCE:2\r\n")
        batch = parse_bls_calendar(payload, self.now)
        self.assertEqual(len(batch.events), 1)
        event = batch.events[0]
        self.assertEqual(event.scheduled_at, datetime(2026, 11, 13, 13, 30, tzinfo=timezone.utc))
        self.assertEqual(event.available_at, self.now)
        self.assertEqual(event.revision, 2)
        self.assertEqual(event.source_url, BLS_CALENDAR_URL)
        self.assertEqual(len(batch.input_hash), 64)
        self.assertEqual(event.event_id, parse_bls_calendar(payload, self.now).events[0].event_id)

    def test_bea_gdp_and_pce_names_keep_source_identity_and_uncertainty(self):
        for title, category in (("GDP (Second Estimate) and Corporate Profits, 3rd Quarter 2026", "growth"),
                                ("Gross Domestic Product, Third Quarter 2026", "growth"),
                                ("Personal Income and Outlays, October 2026", "inflation"),
                                ("pErSoNaL InCoMe AnD OuTlAyS", "inflation")):
            with self.subTest(title=title):
                payload = calendar(title=title, uid="release-1@bea.gov", extra="SEQUENCE:3\r\n")
                batch = parse_bea_calendar(payload, self.now)
                self.assertEqual(len(batch.events), 1)
                self.assertEqual(batch.events[0].source, "bea")
                self.assertEqual(batch.events[0].category, category)
                self.assertEqual(batch.events[0].source_url, BEA_CALENDAR_URL)
                self.assertEqual(batch.events[0].revision, 3)
                self.assertEqual(batch.source.status, "unavailable")
                self.assertIsNone(batch.source.coverage_end)
                self.assertEqual(batch.source.input_hash, batch.input_hash)
        for title in ("GDP by County", "Gross Domestic Product by State", "Personal Income and Outlays by State", "GDPish", "Personal Income and OutlaysExtra"):
            self.assertEqual(parse_bea_calendar(calendar(title=title), self.now).events, ())

    def test_eastern_dst_changes_utc_without_invented_times(self):
        for start, expected in (("20260701T083000", datetime(2026, 7, 1, 12, 30, tzinfo=timezone.utc)),
                    ("20261113T083000", datetime(2026, 11, 13, 13, 30, tzinfo=timezone.utc))):
            with self.subTest(start=start):
                event = parse_bls_calendar(calendar("DTSTART;TZID=America/New_York:" + start), self.now).events[0]
                self.assertEqual(event.scheduled_at, expected)

    def test_date_only_floating_and_ambiguous_times_remain_unknown(self):
        for start in ("DTSTART;VALUE=DATE:20261113", "DTSTART:20261113T083000",
                  "DTSTART;TZID=America/New_York:20261101T013000", "DTSTART;TZID=America/New_York:20260308T023000"):
            with self.subTest(start=start):
                event = parse_bls_calendar(calendar(start), self.now).events[0]
                self.assertIsNone(event.scheduled_at)
                self.assertEqual(event.status, "tentative")

    def test_cancelled_release_and_future_schedule_are_retained(self):
        cancelled = parse_bls_calendar(calendar(extra="STATUS:CANCELLED\r\n"), self.now).events[0]
        self.assertEqual(cancelled.status, "cancelled")
        future = parse_bls_calendar(calendar("DTSTART:20261201T083000Z"), self.now).events[0]
        self.assertGreater(future.scheduled_at, self.now)

    def test_successful_download_is_not_complete_coverage_evidence(self):
        batch = parse_bls_calendar(calendar(), self.now)
        self.assertEqual(batch.source.status, "unavailable")
        self.assertEqual(batch.source.checked_at, self.now)
        self.assertIsNone(batch.source.coverage_start)
        self.assertIsNone(batch.source.coverage_end)
        ignored = parse_bls_calendar(calendar(title="Real Earnings"), self.now)
        self.assertEqual(ignored.events, ())
        self.assertEqual(ignored.ignored_events, 1)

    def test_duplicates_recurrence_and_future_observations_fail_explicitly(self):
        for extra in ("UID:duplicate@bls.gov\r\n", "RRULE:FREQ=MONTHLY\r\n", "DTSTAMP:20261201T000000Z\r\n",
                      "SEQUENCE:-1\r\n", "STATUS:UNKNOWN\r\n"):
            with self.subTest(extra=extra), self.assertRaises(ProviderError):
                parse_bls_calendar(calendar(extra=extra), self.now)

    def test_multiple_calendars_bad_payload_and_duplicate_uids_are_rejected(self):
        event = calendar().split(b"BEGIN:VEVENT", 1)[1].split(b"END:VEVENT", 1)[0]
        duplicate = calendar().replace(b"END:VCALENDAR", b"BEGIN:VEVENT" + event + b"END:VEVENT\r\nEND:VCALENDAR")
        for payload in (calendar() + calendar(), b"not a calendar", b"", b"x" * 1048577,
                        duplicate):
            with self.subTest(size=len(payload)), self.assertRaises(ProviderError):
                parse_bls_calendar(payload, self.now)


if __name__ == "__main__":
    unittest.main()