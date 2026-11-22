"""Synthetic-only read-only eToro contract tests; no account or credential IO."""

import copy
from decimal import Decimal
import importlib
import io
import json
import socket
import ssl
import threading
import traceback
import unittest
from unittest.mock import Mock, patch
from contextlib import redirect_stdout, redirect_stderr

from investment import etoro


def credentials():
    return {"x-api-key": "synthetic-partner", "x-user-key": "synthetic-user"}


def headers():
    return {**credentials(), "x-request-id": "d09321f4-3368-4144-917d-b23962cc9124"}


def fixture():
    return {"clientPortfolio": {
        "credit": 123.45, "unrealizedPnL": -2.5,
        "positions": [{"positionID": 1, "instrumentID": 7, "mirrorID": 0,
                       "amount": 10, "units": 0.125, "openRate": 423.5,
                       "leverage": 1, "totalFees": -0.5}],
        "mirrors": [], "orders": [], "ordersForOpen": [],
    }}


def encoded(document=None):
    return json.dumps(fixture() if document is None else document).encode()


class Response:
    def __init__(self, body=None, status=200, **header_values):
        self.body = encoded() if body is None else body
        self.status = status
        self.headers = {"Content-Type": "application/json", **header_values}
        self.read_sizes = []
        self.closed = False

    def getheader(self, name, default=None):
        return self.headers.get(name, default)

    def read1(self, size):
        self.read_sizes.append(size)
        result, self.body = self.body[:size], self.body[size:]
        return result

    def close(self):
        self.closed = True


def records(address="8.8.8.8"):
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, 443))]


class PortfolioTests(unittest.TestCase):
    def collect(self, body):
        self.transport = Mock(return_value=body)
        return etoro.collect_portfolio(self.transport, credentials)

    def test_projection_preserves_source_fields_no_fx_or_derived_totals(self):
        source = fixture()
        source["clientPortfolio"]["orders"] = [{"amount": 5}]
        result = self.collect(encoded(source))
        self.assertEqual(result["account_currency"], "USD")
        self.assertIsNone(result["native_price_currency"])
        self.assertEqual(result["currency_status"], "unsupported_native_price_currency")
        self.assertEqual(result["raw"], {
            "credit": "123.45", "unrealizedPnL": "-2.5",
            "positions": [{"positionID": 1, "instrumentID": 7, "mirrorID": 0,
                           "amount": "10", "units": "0.125", "openRate": "423.5",
                           "leverage": "1"}]})
        self.assertEqual(result["omitted_counts"], {"mirrors": 0, "orders": 1, "ordersForOpen": 0})
        self.assertEqual(result["endpoint"], etoro.PORTFOLIO_URL)
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["source"], "etoro")
        self.assertEqual(set(result), {"schema_version", "source", "endpoint", "account_currency",
                                     "native_price_currency", "currency_status", "raw",
                                     "omitted_counts", "limitations"})
        self.assertEqual(len(result["limitations"]), 5)
        args = self.transport.call_args.args
        self.assertEqual(args[0], etoro.PORTFOLIO_PATH)
        self.assertTrue(etoro._headers_valid(args[1]))
        self.assertNotIn("synthetic", json.dumps(result))

    def test_exact_decimal_and_absent_optional_fields(self):
        body = b'{"clientPortfolio":{"credit":0.123456789012345678,"positions":[],"mirrors":[],"orders":[],"ordersForOpen":[]}}'
        self.assertEqual(self.collect(body)["raw"], {"credit": "0.123456789012345678", "positions": []})
        source = fixture()
        source["clientPortfolio"]["positions"] = [{"positionID": 2, "instrumentID": 3}]
        self.assertEqual(self.collect(encoded(source))["raw"]["positions"],
                         [{"positionID": 2, "instrumentID": 3}])

    def test_fail_closed_envelopes_and_malformed_payloads(self):
        for payload in (b"", b"x", b"\xff", b"[]", b"{}", b'{"error":"no"}',
                        b'{"clientPortfolio":null}', b'{"a":1,"a":2}',
                        b'{"x":NaN}', b'{"x":Infinity}', b'[' * 1100 + b']' * 1100,
                        b"x" * (etoro.MAX_BYTES + 1), fixture(), "not bytes"):
            with self.subTest(payload_type=type(payload)), self.assertRaises(etoro.EToroError):
                self.collect(payload)

    def test_required_fields_and_identifiers(self):
        base = fixture()
        cases = []
        for key in ("credit", "positions", "mirrors", "orders", "ordersForOpen"):
            document = copy.deepcopy(base)
            del document["clientPortfolio"][key]
            cases.append(document)
        for field in ("positions", "mirrors", "orders", "ordersForOpen"):
            for value in (None, {}, [None], [{}] * 1001):
                document = copy.deepcopy(base)
                document["clientPortfolio"][field] = value
                cases.append(document)
        for name in ("positionID", "instrumentID", "mirrorID"):
            for value in (True, -1, 0.5, "1", 2**63):
                document = copy.deepcopy(base)
                document["clientPortfolio"]["positions"][0][name] = value
                cases.append(document)
        document = copy.deepcopy(base)
        document["clientPortfolio"]["positions"] *= 2
        cases.append(document)
        for document in cases:
            with self.subTest(document=document), self.assertRaises(etoro.EToroError):
                self.collect(encoded(document))

    def test_numeric_contract_not_strings_bool_null_or_unbounded(self):
        for value in (True, None, "12", 1e19, 1e-19, float("nan"), float("inf")):
            for field in ("credit", "unrealizedPnL", "amount", "units", "openRate", "leverage"):
                document = fixture()
                target = (document["clientPortfolio"] if field in ("credit", "unrealizedPnL")
                          else document["clientPortfolio"]["positions"][0])
                target[field] = value
                with self.subTest(field=field, value=value), self.assertRaises(etoro.EToroError):
                    self.collect(encoded(document))
        for value in (0, -2, Decimal("1e18"), Decimal("-1e-18")):
            self.assertIsInstance(etoro._number(value), str)

    def test_unknown_fields_are_omitted_but_structurally_bounded(self):
        for value in ("x" * 4097, list(range(1001)), {str(i): 1 for i in range(129)}):
            document = fixture()
            document["unknown"] = value
            with self.assertRaises(etoro.EToroError):
                self.collect(encoded(document))
        document = fixture()
        document["secret_echo"] = "synthetic-user"
        self.assertNotIn("secret_echo", self.collect(encoded(document)))

    def test_accessor_failures_do_not_call_transport(self):
        transport = Mock()
        for value in (None, {}, {"x-api-key": "only"}, {**credentials(), "Authorization": "x"},
                      {**credentials(), "x-user-key": "bad\r\nHost: bad"},
                      {**credentials(), "x-user-key": ""}):
            with self.assertRaisesRegex(etoro.EToroError, "^credentials_unavailable$"):
                etoro.collect_portfolio(transport, lambda: value)
        transport.assert_not_called()

    def test_no_keys_in_logs_errors_or_formatted_tracebacks(self):
        def failed(*args):
            raise RuntimeError("synthetic-partner synthetic-user proxy-password")
        output = io.StringIO()
        for transport, accessor in ((failed, credentials), (Mock(), failed)):
            with redirect_stdout(output), redirect_stderr(output):
                try:
                    etoro.collect_portfolio(transport, accessor)
                except etoro.EToroError:
                    output.write(traceback.format_exc())
        for secret in ("synthetic-partner", "synthetic-user", "proxy-password"):
            self.assertNotIn(secret, output.getvalue())

    def test_collection_preserves_only_known_transport_error_codes(self):
        for code, expected in (("permission_refused", "permission_refused"),
                               ("synthetic-user", "collection_unavailable")):
            with self.assertRaisesRegex(etoro.EToroError, "^" + expected + "$"):
                etoro.collect_portfolio(Mock(side_effect=etoro.EToroError(code)), credentials)

    def test_import_has_no_io(self):
        with patch.object(etoro, "configured_proxy_url", side_effect=AssertionError), \
                patch("proxy_config.configured_proxy_url", side_effect=AssertionError), \
                patch("socket.getaddrinfo", side_effect=AssertionError), \
                patch("builtins.open", side_effect=AssertionError):
            importlib.reload(etoro)


class TransportTests(unittest.TestCase):
    def setup_transport(self, response=None, dns=None, proxy_url=""):
        self.response = response or Response()
        self.connection = Mock()
        self.connection.getresponse.return_value = self.response
        self.factory = Mock(return_value=self.connection)
        self.resolver = Mock(return_value=records() if dns is None else dns)
        return etoro.EToroTransport(resolver=self.resolver, connection_factory=self.factory,
                                    proxy_url=proxy_url)

    def test_fixed_get_pinned_address_and_cleanup(self):
        transport = self.setup_transport()
        self.assertEqual(transport.get(etoro.PORTFOLIO_PATH, headers()), encoded())
        self.resolver.assert_called_once_with(etoro.HOST, 443, type=socket.SOCK_STREAM)
        self.factory.assert_called_once_with(etoro.HOST, "8.8.8.8", 3.0)
        self.connection.set_debuglevel.assert_called_once_with(0)
        args, kwargs = self.connection.request.call_args
        self.assertEqual(args, ("GET", etoro.PORTFOLIO_PATH))
        self.assertEqual(kwargs["headers"]["Host"], etoro.HOST)
        self.assertEqual(kwargs["headers"]["Accept-Encoding"], "identity")
        self.assertTrue(self.response.closed)
        self.connection.close.assert_called_once()

    def test_dispatch_callback_runs_immediately_before_request(self):
        self.setup_transport()
        events = []
        self.connection.connect.side_effect = lambda: events.append("connect")
        self.connection.request.side_effect = lambda *args, **kwargs: events.append("request")
        transport = etoro.EToroTransport(
            resolver=self.resolver, connection_factory=self.factory, proxy_url="",
            on_dispatch=lambda: events.append("dispatch"))

        self.assertEqual(transport(etoro.PORTFOLIO_PATH, headers()), encoded())
        self.assertEqual(events, ["connect", "dispatch", "request"])

    def test_other_paths_and_extra_headers_refused_before_dns(self):
        transport = self.setup_transport()
        for path in ("/", etoro.PORTFOLIO_URL, etoro.PORTFOLIO_PATH + "?x=y",
                     "/api/v1/trading/execution/market-open-orders/by-amount",
                     "/api/v1/trading/info/demo/pnl", "//evil.test/",
                     "/api/v1/trading/info/trade/history?minDate=2026-99-99&page=1&pageSize=100",
                     "/api/v1/trading/info/trade/history?minDate=2026-01-01&page=3&pageSize=1000",
                     "/api/v1/trading/info/trade/history?minDate=2026-01-01&page=1&pageSize=500",
                     "/api/v1/trading/info/trade/history?minDate=2026-01-01&page=1&pageSize=1000&extra=x"):
            with self.assertRaisesRegex(etoro.EToroError, "^endpoint_refused$"):
                transport(path, headers())
        for values in ({}, {**headers(), "Host": "evil.test"},
                       {**headers(), "Authorization": "Bearer synthetic"},
                       {**headers(), "x-request-id": "not-uuid"},
                       {**headers(), "x-user-key": "x\nbad"}):
            with self.assertRaisesRegex(etoro.EToroError, "^headers_invalid$"):
                transport(etoro.PORTFOLIO_PATH, values)
        self.resolver.assert_not_called()

    def test_only_bounded_real_and_demo_closed_trade_reads_are_allowed(self):
        for environment in ("real", "demo"):
            path = (etoro.CLOSED_TRADE_HISTORY_PATHS[environment]
                    + "?minDate=2026-01-01&page=2&pageSize=1000")
            transport = self.setup_transport()
            self.assertEqual(transport(path, headers()), encoded())
            self.assertEqual(self.connection.request.call_args.args, ("GET", path))
        for page in (0, 3, 10):
            transport = self.setup_transport()
            path = (etoro.CLOSED_TRADE_HISTORY_PATHS["real"]
                    + f"?minDate=2026-01-01&page={page}&pageSize=1000")
            with self.assertRaisesRegex(etoro.EToroError, "^endpoint_refused$"):
                transport(path, headers())
            self.resolver.assert_not_called()

    def test_all_dns_answers_must_be_public_and_well_formed(self):
        for address in ("127.0.0.1", "10.0.0.1", "169.254.169.254", "0.0.0.0",
                        "224.0.0.1", "::1", "fc00::1", "fe80::1%1", "::ffff:8.8.8.8",
                        "192.0.2.1"):
            transport = self.setup_transport(dns=records() + records(address))
            with self.subTest(address=address), self.assertRaises(etoro.EToroError):
                transport(etoro.PORTFOLIO_PATH, headers())
            self.factory.assert_not_called()
        for dns in ([], records() * 33, [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 80))]):
            transport = self.setup_transport(dns=dns)
            with self.assertRaises(etoro.EToroError):
                transport(etoro.PORTFOLIO_PATH, headers())
            self.factory.assert_not_called()

    def test_redirects_and_http_failures_never_follow_or_retry(self):
        for status, code in ((301, "redirect_refused"), (302, "redirect_refused"),
                             (307, "redirect_refused"), (308, "redirect_refused"),
                             (401, "authentication_refused"), (403, "permission_refused"),
                             (429, "rate_limited"), (500, "http_unavailable")):
            transport = self.setup_transport(Response(status=status, Location="https://evil.test"))
            with self.assertRaisesRegex(etoro.EToroError, "^" + code + "$"):
                transport(etoro.PORTFOLIO_PATH, headers())
            self.factory.assert_called_once()
            self.connection.request.assert_called_once()
            self.assertEqual(self.response.read_sizes, [])
            self.assertTrue(self.response.closed)

    def test_headers_bounds_and_incomplete_body(self):
        for values, code in (
            ({"Content-Encoding": "gzip"}, "encoding_refused"),
            ({"Content-Type": "text/html"}, "content_refused"),
            ({"Content-Length": "1048577"}, "response_too_large"),
            ({"Content-Length": "-1"}, "response_too_large"),
            ({"Content-Length": "1, 1"}, "response_too_large"),
            ({"Content-Length": "0"}, "response_incomplete"),
            ({"Transfer-Encoding": "gzip"}, "framing_refused"),
            ({"Transfer-Encoding": "chunked", "Content-Length": "1"}, "framing_refused"),
        ):
            transport = self.setup_transport(Response(**values))
            with self.assertRaisesRegex(etoro.EToroError, "^" + code + "$"):
                transport(etoro.PORTFOLIO_PATH, headers())
            self.assertTrue(self.response.closed)
        transport = self.setup_transport(Response(**{"Content-Length": str(len(encoded()))}))
        self.assertEqual(transport(etoro.PORTFOLIO_PATH, headers()), encoded())

    def test_stream_read_limit_with_and_without_content_length(self):
        for size in (etoro.MAX_BYTES, etoro.MAX_BYTES + 1):
            response = Response(body=b"x" * size)
            transport = self.setup_transport(response)
            if size > etoro.MAX_BYTES:
                with self.assertRaisesRegex(etoro.EToroError, "^response_too_large$"):
                    transport(etoro.PORTFOLIO_PATH, headers())
            else:
                self.assertEqual(len(transport(etoro.PORTFOLIO_PATH, headers())), size)
            self.assertTrue(all(0 < n <= 16384 for n in response.read_sizes))
            self.assertLessEqual(sum(response.read_sizes), etoro.MAX_BYTES + 1)
            self.assertTrue(response.closed)

    def test_configured_proxy_is_pinned_and_never_falls_back(self):
        with patch.object(etoro, "configured_proxy_url", return_value="http://user:synthetic@proxy.test:8080") as config:
            transport = self.setup_transport(proxy_url=None)
        config.assert_called_once_with()
        self.connection.request.side_effect = OSError("proxy secret synthetic")
        with self.assertRaisesRegex(etoro.EToroError, "^transport_unavailable$") as caught:
            transport(etoro.PORTFOLIO_PATH, headers())
        self.assertNotIn("synthetic", str(caught.exception))
        self.factory.assert_called_once()
        args = self.factory.call_args.args
        self.assertEqual(args[:3], (etoro.HOST, "8.8.8.8", 3.0))
        self.assertEqual(args[3].host, "proxy.test")
        self.assertEqual(args[3].port, 8080)
        self.assertEqual(self.connection.request.call_count, 1)

    def test_invalid_proxy_does_not_make_connection(self):
        for proxy in ("https://proxy.test", "socks5://proxy.test", "http://proxy.test:bad"):
            with self.assertRaisesRegex(etoro.EToroError, "^proxy_configuration_invalid$"):
                self.setup_transport(proxy_url=proxy)
            self.factory.assert_not_called()

    def test_transport_timeout_and_failure_are_sanitized(self):
        transport = self.setup_transport()
        self.connection.getresponse.side_effect = OSError("synthetic-user")
        with self.assertRaisesRegex(etoro.EToroError, "^transport_unavailable$"):
            transport(etoro.PORTFOLIO_PATH, headers())
        self.connection.close.assert_called_once()
        transport = self.setup_transport()
        with patch.object(etoro.time, "monotonic", side_effect=[0.0, 16.0]):
            with self.assertRaisesRegex(etoro.EToroError, "^request_timeout$"):
                transport(etoro.PORTFOLIO_PATH, headers())
        self.factory.assert_not_called()

    def test_cancellation_during_tls_setup_never_sends_request(self):
        transport = self.setup_transport()
        self.connection.connect.side_effect = lambda: self.connection.cancel_event.set()
        with self.assertRaisesRegex(etoro.EToroError, "^request_timeout$"):
            transport(etoro.PORTFOLIO_PATH, headers())
        self.connection.request.assert_not_called()
        self.connection.close.assert_called_once()

    def test_cancellation_during_final_approval_never_sends_request(self):
        self.setup_transport()
        checks = []

        def approval_check():
            checks.append(True)
            if len(checks) == 3:
                self.connection.cancel_event.set()

        transport = etoro.EToroTransport(resolver=self.resolver, connection_factory=self.factory,
                                         proxy_url="", before_request=approval_check)
        with self.assertRaisesRegex(etoro.EToroError, "^request_timeout$"):
            transport(etoro.PORTFOLIO_PATH, headers())
        self.connection.connect.assert_called_once()
        self.connection.request.assert_not_called()
        self.connection.close.assert_called_once()

    def test_timed_out_request_cannot_send_after_caller_returns(self):
        entered, release = threading.Event(), threading.Event()
        tls_socket, raw_socket, context = Mock(spec=ssl.SSLSocket), Mock(), Mock()
        context.wrap_socket.return_value = tls_socket
        state = {"connected": True}
        delivered = []

        def send(data):
            if not state["connected"]:
                raise OSError("synthetic disconnected socket")
            delivered.append(data)

        def shutdown(*args):
            state["connected"] = False

        tls_socket.sendall.side_effect = send

        class PausedConnection(etoro.PinnedHTTPSConnection):
            def request(self, *args, **kwargs):
                entered.set()
                release.wait(2.0)
                return super().request(*args, **kwargs)

        transport = etoro.EToroTransport(
            resolver=lambda *args, **kwargs: records(), connection_factory=PausedConnection, proxy_url="")
        with patch.object(etoro, "_DEADLINE_SECONDS", 0.25), \
             patch.object(etoro.threading, "Timer"), \
             patch.object(socket.socket, "shutdown", side_effect=shutdown), \
             patch("market_intelligence.providers.ssl.create_default_context", return_value=context), \
             patch("market_intelligence.providers.socket.create_connection", return_value=raw_socket):
            try:
                with self.assertRaisesRegex(etoro.EToroError, "^request_timeout$"):
                    transport(etoro.PORTFOLIO_PATH, headers())
                self.assertTrue(entered.is_set())
            finally:
                release.set()
                acquired = etoro._REQUEST_SLOT.acquire(timeout=2.0)
                if acquired:
                    etoro._REQUEST_SLOT.release()
            self.assertTrue(acquired)
        self.assertEqual(delivered, [])

    def test_timeout_shutdown_never_exposes_plaintext_during_concurrent_request(self):
        for shutdown_fails in (False, True):
            with self.subTest(shutdown_fails=shutdown_fails):
                entered, released, sent = threading.Event(), threading.Event(), threading.Event()
                state = {"protected": True, "connected": True}
                plaintext = []
                tls_socket = Mock(spec=ssl.SSLSocket)
                connection = Mock(sock=tls_socket)

                def request(*args, **kwargs):
                    entered.set()
                    released.wait(2.0)
                    if state["connected"] and not state["protected"]:
                        plaintext.append(kwargs["headers"]["x-user-key"])
                    sent.set()
                    raise OSError("synthetic cancelled request")

                def unsafe_shutdown(*args):
                    state["protected"] = False
                    released.set()
                    sent.wait(2.0)
                    state["connected"] = False

                def native_shutdown(*args):
                    if shutdown_fails:
                        raise OSError("synthetic shutdown failure")
                    state["connected"] = False
                    released.set()
                    sent.wait(2.0)

                def connection_close():
                    state["protected"] = False
                    released.set()
                    sent.wait(2.0)

                tls_socket.shutdown.side_effect = unsafe_shutdown
                connection.request.side_effect = request
                connection.close.side_effect = connection_close
                transport = etoro.EToroTransport(
                    resolver=lambda *args, **kwargs: records(),
                    connection_factory=lambda *args: connection, proxy_url="")
                with patch.object(etoro, "_DEADLINE_SECONDS", 0.25), \
                     patch.object(etoro.threading, "Timer"), \
                     patch.object(socket.socket, "shutdown", side_effect=native_shutdown):
                    try:
                        error = "cancellation_unconfirmed" if shutdown_fails else "request_timeout"
                        with self.assertRaisesRegex(etoro.EToroError, "^" + error + "$"):
                            transport(etoro.PORTFOLIO_PATH, headers())
                        self.assertTrue(entered.is_set())
                    finally:
                        released.set()
                        acquired = etoro._REQUEST_SLOT.acquire(timeout=2.0)
                        if acquired:
                            etoro._REQUEST_SLOT.release()
                    self.assertTrue(acquired)
                self.assertEqual(plaintext, [])

    def test_blocked_dns_returns_timeout_without_unbounded_workers_or_late_request(self):
        transport = self.setup_transport()
        release, finished = threading.Event(), threading.Event()

        def blocked(*args, **kwargs):
            try:
                release.wait(2.0)
                return records()
            finally:
                finished.set()

        self.resolver.side_effect = blocked
        try:
            with patch.object(etoro, "_DEADLINE_SECONDS", 0.02):
                with self.assertRaisesRegex(etoro.EToroError, "^request_timeout$"):
                    transport(etoro.PORTFOLIO_PATH, headers())
                with self.assertRaisesRegex(etoro.EToroError, "^request_in_progress$"):
                    transport(etoro.PORTFOLIO_PATH, headers())
                self.factory.assert_not_called()
        finally:
            release.set()
            self.assertTrue(finished.wait(2.0))
            acquired = etoro._REQUEST_SLOT.acquire(timeout=2.0)
            if acquired:
                etoro._REQUEST_SLOT.release()
            self.assertTrue(acquired)
        self.factory.assert_not_called()

    def test_reused_tls_primitive_verifies_certificates_and_hostname(self):
        # No connect: only create the stdlib context and pinned connection.
        connection = etoro.PinnedHTTPSConnection(etoro.HOST, "8.8.8.8", 3.0)
        self.assertEqual(connection._context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(connection._context.check_hostname)
        self.assertEqual(connection.host, etoro.HOST)
        self.assertEqual(connection.address, "8.8.8.8")
        connection.close()


if __name__ == "__main__":
    unittest.main()
