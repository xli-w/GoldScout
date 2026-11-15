from datetime import datetime, timedelta, timezone
import base64
import importlib.util
import io
import os
import socket
import ssl
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import Mock, patch

import proxy_config

from market_intelligence.providers import BEA_CALENDAR_URL, BLS_CALENDAR_URL, FED_NEWS_URL, CalendarTransport, PinnedHTTPSConnection, ProviderError, parse_bea_calendar, parse_bls_calendar
from market_intelligence.service import IntelligenceService
from market_intelligence.__main__ import main
from proxy_config import ProxyConfigurationError
from test_intelligence_controls import IntelligenceStoreFixture


def calendar(start="DTSTART;TZID=America/New_York:20261113T083000", extra="", title="Consumer Price Index", uid="release-1@bls.gov"):
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Offline fixture//EN\r\n"
            "BEGIN:VEVENT\r\nUID:" + uid + "\r\nSUMMARY:" + title + "\r\n" + start + "\r\n" + extra +
            "END:VEVENT\r\nEND:VCALENDAR\r\n").encode("utf-8")


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
            with self.subTest(line=line), patch.dict(os.environ, {"TRADER_PROXY_URL": ""}), \
                    patch("proxy_config._dotenv_loading_disabled", return_value=False), \
                    patch("proxy_config._read_proxy_dotenv",
                          side_effect=lambda: proxy_config._parse_proxy_dotenv(io.StringIO(line))):
                os.environ.pop("TRADER_PROXY_URL")
                with self.assertRaises(ProxyConfigurationError) as error:
                    CalendarTransport(self.resolver)
                self.assertNotIn("secret", str(error.exception))
        self.resolver.assert_not_called()

    def test_dotenv_parser_returns_only_the_last_literal_proxy_value(self):
        contents = ("ALPACA_SECRET_KEY=unused-sentinel\n"
                    "TRADER_PROXY_URL=http://first.invalid:8080\n"
                    "TRADER_PROXY_URL='http://sample:p%40ss@proxy.invalid:8080'\n")
        proxy = proxy_config._parse_proxy_dotenv(io.StringIO(contents))
        self.assertEqual(proxy, "http://sample:p%40ss@proxy.invalid:8080")
        self.assertEqual(proxy_config._parse_proxy_dotenv(io.StringIO("TRADER_PROXY_URL=\n")), "")
        self.assertEqual(os.environ["ALPACA_SECRET_KEY"], "")

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
                patch("proxy_config._read_proxy_dotenv", return_value=proxy) as load:
            os.environ.pop("TRADER_PROXY_URL")
            transport = CalendarTransport(self.resolver, self.factory, monotonic=lambda: 0.0)
            self.assertEqual(transport.fetch(FED_NEWS_URL), b"calendar")
            self.assertEqual(os.environ["ALPACA_SECRET_KEY"], "")
        self.assertEqual(self.factory.call_args.args[:3],
                         ("www.federalreserve.gov", "93.184.216.34", 3.0))
        self.assertEqual(self.factory.call_args.args[3].url, proxy)
        load.assert_called_once()

    def test_empty_environment_setting_overrides_dotenv_proxy(self):
        with patch.dict(os.environ, {"TRADER_PROXY_URL": ""}), \
                patch("proxy_config._read_proxy_dotenv", return_value="http://proxy.invalid") as load:
            transport = CalendarTransport(self.resolver, self.factory, monotonic=lambda: 0.0)
            self.assertEqual(transport.fetch(BLS_CALENDAR_URL), b"calendar")
        self.factory.assert_called_once_with("www.bls.gov", "93.184.216.34", 3.0)
        load.assert_not_called()

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
                patch("proxy_config._read_proxy_dotenv", return_value=proxy) as load, \
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
                self.target = target
            def start(self):
                pass
            def is_alive(self):
                return True
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
            service.clock = Mock(side_effect=[self.now, received + timedelta(seconds=1)])
            service.step()
            self.assertGreaterEqual(self.store.calendar().available_at, received)

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