"""Narrow read-only eToro snapshot adapter; importing performs no IO.

Contract: collect_portfolio(get_callable, credential_accessor) -> dict.
The accessor returns exactly {"x-api-key": str, "x-user-key": str}; the
transport callable takes (path, headers) and returns bytes (not decoded JSON).
EToroTransport instances and their .get methods both satisfy that contract.
Injected callables are trusted dependencies, not sandboxed network clients.

Caller MUST gate account eligibility, rights, permission and collection approval
before invoking this function. GET success does not prove a read-only key scope.
There is no account discovery, credential storage, retry, accounting or execution.

Public reference checked 2026-11-17 (portal itself was Cloudflare-blocked):
https://github.com/etoro-builders/etoro-cursor-plugin/blob/
a18e511a7226df46a9a7587c6ce92582c3592ab2/rules/etoro-account-snapshot.mdc
and rules/etoro-api-conventions.mdc at the same revision.

Output schema v1:
  source="etoro", endpoint=<fixed HTTPS URL>, schema_version=1,
  account_currency="USD", native_price_currency=None,
  currency_status="unsupported_native_price_currency",
  raw={credit: decimal-string, positions: [position projection],
       unrealizedPnL?: decimal-string},
  omitted_counts={mirrors: int, orders: int, ordersForOpen: int},
  limitations=[fixed strings].
Position projections require positive positionID/instrumentID integers and may
include mirrorID (nonnegative integer), amount/units/openRate/leverage (decimal
strings). No other source fields are retained. "raw" means source field names,
not the original response bytes. No derived totals, FX, inferred prices, fees,
cost basis, timestamps, account identity or completeness claims are produced.
Source snapshots may lag ~10 seconds; caller records receipt/availability clocks.
"""

from decimal import Decimal
import ipaddress
import json
import socket
import threading
import time
import uuid

from market_intelligence.providers import PinnedHTTPSConnection, abort_connection
from proxy_config import configured_proxy_url, parse_proxy_url


HOST = "public-api.etoro.com"
PORTFOLIO_PATH = "/api/v1/trading/info/real/pnl"
PORTFOLIO_URL = "https://" + HOST + PORTFOLIO_PATH
MAX_BYTES = 1024 * 1024
MAX_ITEMS = 1000
_DEADLINE_SECONDS = 15.0
# One bounded worker across instances: an OS resolver cannot be interrupted.
# A timed-out resolver may finish later, but cannot accumulate workers or send
# a late request. Until it exits, subsequent collections fail closed as busy.
_REQUEST_SLOT = threading.Lock()
_TRANSPORT_CODES = frozenset({
    "endpoint_refused", "headers_invalid", "address_refused", "request_timeout",
    "request_in_progress", "redirect_refused", "authentication_refused",
    "permission_refused", "rate_limited", "http_unavailable", "encoding_refused",
    "content_refused", "framing_refused", "response_too_large",
    "response_incomplete", "transport_unavailable", "approval_interrupted",
    "cancellation_unconfirmed",
})
_LIMITATIONS = (
    "partial_source_snapshot_not_accounting_or_buying_power",
    "native_openRate_currency_unresolved_no_fx",
    "mirrors_and_pending_orders_not_projected_or_aggregated",
    "source_cache_may_lag_no_source_timestamp",
    "eligibility_rights_and_key_permissions_require_caller_verification",
)


class EToroError(RuntimeError):
    """Fixed, non-sensitive failure code; never includes external error text."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _headers_valid(headers):
    if type(headers) is not dict or set(headers) != {
        "x-api-key", "x-user-key", "x-request-id"
    }:
        return False
    for value in headers.values():
        if (type(value) is not str or not 1 <= len(value) <= 4096
                or any(not 33 <= ord(char) <= 126 for char in value)):
            return False
    try:
        request_id = uuid.UUID(headers["x-request-id"])
        return request_id.version == 4 and str(request_id) == headers["x-request-id"]
    except ValueError:
        return False


class EToroTransport:
    """Fixed GET-only, pinned TLS transport. Construction reads proxy config only.

    Honors configured TRADER_PROXY_URL without direct fallback. Resolver and
    connection_factory injection is for trusted offline tests. Only the pinned
    socket primitive is reused; account data never enters intelligence providers.
    """

    def __init__(self, *, resolver=None, connection_factory=None, proxy_url=None,
                 before_request=None):
        self._resolver = resolver or socket.getaddrinfo
        self._connection_factory = connection_factory or PinnedHTTPSConnection
        if before_request is not None and not callable(before_request):
            raise ValueError("The request approval check must be callable")
        self._before_request = before_request
        try:
            self._proxy = parse_proxy_url(
                configured_proxy_url() if proxy_url is None else proxy_url)
        except Exception:
            raise EToroError("proxy_configuration_invalid") from None

    def __call__(self, path, headers):
        return self.get(path, headers)

    def get(self, path, headers):
        if path != PORTFOLIO_PATH:
            raise EToroError("endpoint_refused")
        if not _headers_valid(headers):
            raise EToroError("headers_invalid")
        if not _REQUEST_SLOT.acquire(blocking=False):
            raise EToroError("request_in_progress")
        done, cancelled = threading.Event(), threading.Event()
        result = {}
        request_headers = dict(headers)
        connection_lock = threading.Lock()
        active_connection = [None]

        def register_connection(connection):
            with connection_lock:
                active_connection[0] = connection

        def abort():
            cancelled.set()
            with connection_lock:
                connection = active_connection[0]
            return abort_connection(connection)

        def run():
            try:
                result["payload"] = self._get_one(request_headers, cancelled, register_connection, abort)
            except Exception as exc:
                code = (exc.code if isinstance(exc, EToroError)
                        and type(exc.code) is str and exc.code in _TRANSPORT_CODES
                        else "transport_unavailable")
                result["error"] = code
            finally:
                _REQUEST_SLOT.release()
                done.set()

        worker = threading.Thread(target=run, daemon=True, name="etoro-readonly")
        try:
            worker.start()
        except Exception:
            _REQUEST_SLOT.release()
            raise EToroError("transport_unavailable") from None
        if not done.wait(_DEADLINE_SECONDS):
            if not abort():
                raise EToroError("cancellation_unconfirmed")
            raise EToroError("request_timeout")
        if "error" in result:
            raise EToroError(result["error"])
        return result["payload"]

    def _get_one(self, headers, cancelled, register_connection, abort):
        connection = response = None
        started = time.monotonic()

        timer = threading.Timer(_DEADLINE_SECONDS, abort)
        timer.daemon = True
        timer.start()

        def check_deadline():
            if cancelled.is_set() or time.monotonic() - started >= _DEADLINE_SECONDS:
                raise EToroError("request_timeout")

        try:
            records = self._resolver(HOST, 443, type=socket.SOCK_STREAM)
            if not records or len(records) > 32:
                raise EToroError("address_refused")
            addresses = []
            for family, kind, protocol, _, address in records:
                parsed = ipaddress.ip_address(address[0])
                if (family not in (socket.AF_INET, socket.AF_INET6)
                        or kind != socket.SOCK_STREAM or protocol not in (0, socket.IPPROTO_TCP)
                        or address[1] != 443 or "%" in address[0]
                        or not parsed.is_global or parsed.is_multicast or parsed.is_reserved
                        or getattr(parsed, "ipv4_mapped", None) is not None
                        or (family == socket.AF_INET) != (parsed.version == 4)):
                    raise EToroError("address_refused")
                addresses.append(str(parsed))
            check_deadline()
            if self._before_request is not None:
                self._before_request()
                check_deadline()
            args = (HOST, addresses[0], 3.0)
            connection = (self._connection_factory(*args) if self._proxy is None
                          else self._connection_factory(*args, self._proxy))
            register_connection(connection)
            connection.cancel_event = cancelled
            connection.set_debuglevel(0)
            check_deadline()
            if self._before_request is not None:
                self._before_request()
                check_deadline()
            connection.connect()
            check_deadline()
            if self._before_request is not None:
                self._before_request()
                check_deadline()
            connection.request("GET", PORTFOLIO_PATH, headers={
                **headers, "Host": HOST, "Accept": "application/json",
                "Accept-Encoding": "identity", "Connection": "close",
                "Cache-Control": "no-store", "User-Agent": "GoldScoutPortfolioMonitor/1",
            })
            response = connection.getresponse()
            if 300 <= response.status < 400:
                raise EToroError("redirect_refused")
            if response.status != 200:
                code = {401: "authentication_refused", 403: "permission_refused",
                        429: "rate_limited"}.get(response.status, "http_unavailable")
                raise EToroError(code)
            if response.getheader("Content-Encoding", "identity").lower() != "identity":
                raise EToroError("encoding_refused")
            if response.getheader("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                raise EToroError("content_refused")
            length = response.getheader("Content-Length")
            transfer = response.getheader("Transfer-Encoding")
            if transfer is not None and (transfer.lower() != "chunked" or length is not None):
                raise EToroError("framing_refused")
            if length is not None and (len(length) > 7 or not length.isascii()
                                       or not length.isdigit() or int(length) > MAX_BYTES):
                raise EToroError("response_too_large")
            chunks, total = [], 0
            while True:
                check_deadline()
                chunk = response.read1(min(16384, MAX_BYTES + 1 - total))
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_BYTES:
                    raise EToroError("response_too_large")
                chunks.append(chunk)
            check_deadline()
            if length is not None and total != int(length):
                raise EToroError("response_incomplete")
            return b"".join(chunks)
        except EToroError:
            raise
        except Exception:
            # External errors may contain credential headers/proxy passwords.
            raise EToroError("request_timeout" if cancelled.is_set()
                             else "transport_unavailable") from None
        finally:
            timer.cancel()
            for resource in (response, connection):
                if resource is not None:
                    try:
                        resource.close()
                    except Exception:
                        pass  # Cleanup cannot replace the sanitized result/error.


def _invalid():
    raise EToroError("schema_invalid")


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _invalid()
        result[key] = value
    return result


def _bounded(value, depth=0, budget=None):
    budget = [20000] if budget is None else budget
    budget[0] -= 1
    if budget[0] < 0 or depth > 16:
        _invalid()
    if type(value) is dict:
        if len(value) > 128:
            _invalid()
        for key, item in value.items():
            if len(key) > 128:
                _invalid()
            _bounded(item, depth + 1, budget)
    elif type(value) is list:
        if len(value) > MAX_ITEMS:
            _invalid()
        for item in value:
            _bounded(item, depth + 1, budget)
    elif type(value) is str:
        if len(value) > 4096:
            _invalid()
    elif type(value) in (int, Decimal):
        _number(value)
    elif value is not None and type(value) is not bool:
        _invalid()


def _number(value):
    if type(value) not in (int, Decimal):
        _invalid()
    number = Decimal(value)
    if (not number.is_finite() or abs(number) > Decimal("1e18")
            or number.as_tuple().exponent < -18 or len(number.as_tuple().digits) > 40):
        _invalid()
    return format(number, "f")


def _identifier(value, minimum=1):
    if type(value) is not int or not minimum <= value <= 2**63 - 1:
        _invalid()
    return value


def _snapshot(payload):
    if type(payload) is not bytes or not 0 < len(payload) <= MAX_BYTES:
        raise EToroError("payload_invalid")
    try:
        document = json.loads(payload.decode("utf-8"), parse_float=Decimal,
                              parse_constant=lambda _: _invalid(), object_pairs_hook=_pairs)
        _bounded(document)
    except EToroError:
        raise
    except (ValueError, UnicodeError, RecursionError, ArithmeticError):
        raise EToroError("schema_invalid") from None
    if type(document) is not dict or type(document.get("clientPortfolio")) is not dict:
        raise EToroError("schema_unsupported")
    portfolio = document["clientPortfolio"]
    if "credit" not in portfolio:
        _invalid()
    for name in ("positions", "mirrors", "orders", "ordersForOpen"):
        if type(portfolio.get(name)) is not list:
            _invalid()
        if any(type(item) is not dict for item in portfolio[name]):
            _invalid()
    raw = {"credit": _number(portfolio["credit"]), "positions": []}
    if "unrealizedPnL" in portfolio:
        raw["unrealizedPnL"] = _number(portfolio["unrealizedPnL"])
    seen = set()
    for item in portfolio["positions"]:
        position = {name: _identifier(item.get(name))
                    for name in ("positionID", "instrumentID")}
        if position["positionID"] in seen:
            _invalid()
        seen.add(position["positionID"])
        if "mirrorID" in item:
            position["mirrorID"] = _identifier(item["mirrorID"], 0)
        for name in ("amount", "units", "openRate", "leverage"):
            if name in item:
                position[name] = _number(item[name])
        raw["positions"].append(position)
    return {
        "schema_version": 1, "source": "etoro", "endpoint": PORTFOLIO_URL,
        "account_currency": "USD", "native_price_currency": None,
        "currency_status": "unsupported_native_price_currency",
        "raw": raw,
        "omitted_counts": {name: len(portfolio[name])
                           for name in ("mirrors", "orders", "ordersForOpen")},
        "limitations": list(_LIMITATIONS),
    }


def collect_portfolio(transport, credential_accessor):
    """Collect one bounded projection. Caller owns all approval/permission gates."""
    try:
        credentials = credential_accessor()
        if type(credentials) is not dict or set(credentials) != {"x-api-key", "x-user-key"}:
            raise ValueError
        headers = {**credentials, "x-request-id": str(uuid.uuid4())}
        if not _headers_valid(headers):
            raise ValueError
    except Exception:
        raise EToroError("credentials_unavailable") from None
    try:
        payload = transport(PORTFOLIO_PATH, headers)
    except Exception as exc:
        # Only known adapter-owned codes survive; arbitrary injected errors don't.
        code = (exc.code if isinstance(exc, EToroError) and type(exc.code) is str
                and exc.code in _TRANSPORT_CODES else "collection_unavailable")
        raise EToroError(code) from None
    return _snapshot(payload)
