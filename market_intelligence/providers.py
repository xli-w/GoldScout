"""Allowlisted calendar adapters. Importing this module performs no IO."""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import http.client
import ipaddress
import logging
import socket
import ssl
import threading
import time
from typing import Tuple

from market_intelligence.contracts import CalendarEvent, SourceCoverage, SYMBOLS, utc_time
from proxy_config import configured_proxy_url, parse_proxy_url


logger = logging.getLogger(__name__)
_SOCKET_TYPE = socket.socket

BLS_CALENDAR_URL = "https://www.bls.gov/schedule/news_release/bls.ics"
BEA_CALENDAR_URL = "https://www.bea.gov/news/schedule/ics/online-calendar-subscription.ics"
FED_NEWS_URL = "https://www.federalreserve.gov/feeds/press_monetary.xml"
CALENDAR_ENDPOINTS = {
    BLS_CALENDAR_URL: ("www.bls.gov", "/schedule/news_release/bls.ics"),
    BEA_CALENDAR_URL: ("www.bea.gov", "/news/schedule/ics/online-calendar-subscription.ics"),
}
RSS_ENDPOINTS = {
    FED_NEWS_URL: ("www.federalreserve.gov", "/feeds/press_monetary.xml"),
}
MAX_DOWNLOAD_BYTES = 1048576
SERIES = {
    "consumer price index": "inflation",
    "producer price index": "inflation",
    "employment situation": "employment",
    "job openings and labor turnover survey": "employment",
    "employment cost index": "employment",
    "productivity and costs": "growth",
}
BEA_SERIES = {
    "gross domestic product": "growth",
    "gdp": "growth",
    "personal income and outlays": "inflation",
}


class ProviderError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def abort_connection(connection, active_socket=None):
    if connection is None:
        return True
    active = active_socket or connection.sock
    try:
        if active is not None:
            if isinstance(active, _SOCKET_TYPE):
                # SSLSocket.shutdown clears TLS state before the native shutdown.
                _SOCKET_TYPE.shutdown(active, socket.SHUT_RDWR)
                return True
            active.shutdown(socket.SHUT_RDWR)
        connection.close()
        return True
    except Exception:
        logger.warning("Connection cancellation could not be confirmed; a request may remain in flight")
        return False


@dataclass(frozen=True)
class CalendarBatch:
    source: SourceCoverage
    events: Tuple[CalendarEvent, ...]
    input_hash: str
    ignored_events: int

    def __post_init__(self):
        if not isinstance(self.source, SourceCoverage) or type(self.events) is not tuple or len(self.events) > 1000:
            raise ValueError("Provider batch must contain bounded immutable source evidence")
        if any(not isinstance(event, CalendarEvent) or event.source != self.source.source for event in self.events):
            raise ValueError("Provider events must match their source")
        if self.source.input_hash != self.input_hash or self.source.checked_at is None:
            raise ValueError("Provider batch requires matching downloaded provenance")
        if type(self.ignored_events) is not int or not 0 <= self.ignored_events <= 1000:
            raise ValueError("Invalid ignored event count")


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, address, timeout, proxy=None):
        super().__init__(host, port=443, timeout=timeout, context=ssl.create_default_context())
        self.address = address
        self.proxy = proxy
        if proxy is not None:
            authority = f"[{address}]:443" if ":" in address else f"{address}:443"
            headers = {"Host": authority}
            if proxy.authorization is not None:
                headers["Proxy-Authorization"] = proxy.authorization
            self.set_tunnel(address, 443, headers=headers)

    def _connect_proxy(self, cancelled):
        records = socket.getaddrinfo(self.proxy.host, self.proxy.port, type=socket.SOCK_STREAM)
        last_error = None
        for family, kind, protocol, _, address in records:
            if cancelled is not None and cancelled.is_set():
                raise ProviderError("request_timeout")
            raw = None
            try:
                raw = socket.socket(family, kind, protocol)
                self.sock = raw
                if cancelled is not None and cancelled.is_set():
                    raise ProviderError("request_timeout")
                raw.settimeout(self.timeout)
                raw.connect(address)
                if cancelled is not None and cancelled.is_set():
                    raise ProviderError("request_timeout")
                return raw
            except OSError as exc:
                last_error = exc
                if raw is not None:
                    raw.close()
                self.sock = None
                if cancelled is not None and cancelled.is_set():
                    raise ProviderError("request_timeout") from None
            except ProviderError:
                if raw is not None:
                    raw.close()
                self.sock = None
                raise
        if last_error is not None:
            raise last_error
        raise OSError("Proxy hostname has no usable addresses")

    def connect(self):
        cancelled = getattr(self, "cancel_event", None)
        if cancelled is not None and cancelled.is_set():
            raise ProviderError("request_timeout")
        raw = (self._connect_proxy(cancelled) if self.proxy is not None
               else socket.create_connection((self.address, 443), timeout=self.timeout))
        self.sock = raw
        try:
            if cancelled is not None and cancelled.is_set():
                raise ProviderError("request_timeout")
            if self.proxy is not None:
                debuglevel = self.debuglevel
                try:
                    self.debuglevel = 0
                    self._tunnel()
                finally:
                    self.debuglevel = debuglevel
            if cancelled is not None and cancelled.is_set():
                raise ProviderError("request_timeout")
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host, do_handshake_on_connect=False)
            if cancelled is not None and cancelled.is_set():
                raise ProviderError("request_timeout")
            self.sock.do_handshake()
            if cancelled is not None and cancelled.is_set():
                raise ProviderError("request_timeout")
        except BaseException:
            if self.sock is not None:
                self.sock.close()
            raw.close()
            self.sock = None
            raise


class CalendarTransport:
    def __init__(self, resolver=None, connection_factory=None, monotonic=None, proxy_url=None):
        self.resolver = resolver or socket.getaddrinfo
        self.connection_factory = connection_factory or PinnedHTTPSConnection
        self.monotonic = monotonic or time.monotonic
        self.proxy = parse_proxy_url(configured_proxy_url() if proxy_url is None else proxy_url)
        self.state_lock = threading.Lock()
        self.fetch_lock = threading.Lock()
        self.active_abort = None

    def cancel(self):
        with self.state_lock:
            abort = self.active_abort
        if abort is not None:
            abort()

    def fetch(self, url, cancel_event=None):
        endpoints = {**CALENDAR_ENDPOINTS, **RSS_ENDPOINTS}
        if url not in endpoints:
            raise ProviderError("endpoint_refused")
        host, path = endpoints[url]
        rss = url in RSS_ENDPOINTS
        if cancel_event is not None and cancel_event.is_set():
            raise ProviderError("request_timeout")
        if not self.fetch_lock.acquire(blocking=False):
            raise ProviderError("request_in_progress")
        started = self.monotonic()
        connection = None
        active_socket = None
        response = None
        deadline = cancel_event or threading.Event()

        def abort():
            deadline.set()
            abort_connection(connection, active_socket)

        timer = threading.Timer(15.0, abort)
        timer.daemon = True
        with self.state_lock:
            self.active_abort = abort
        timer.start()
        stage = "resolve"
        try:
            if deadline.is_set():
                raise ProviderError("request_timeout")
            records = self.resolver(host, 443, type=socket.SOCK_STREAM)
            if not records or len(records) > 32:
                raise ProviderError("address_refused")
            addresses = []
            for record in records:
                address = record[4][0]
                parsed_address = ipaddress.ip_address(address)
                if ("%" in address or record[4][1] != 443 or not parsed_address.is_global
                    or parsed_address.is_multicast or parsed_address.is_reserved
                        or record[0] not in (socket.AF_INET, socket.AF_INET6)):
                    raise ProviderError("address_refused")
                addresses.append(address)
            if deadline.is_set() or self.monotonic() - started >= 15.0:
                raise ProviderError("request_timeout")
            stage = "request"
            if self.proxy is None:
                connection = self.connection_factory(host, addresses[0], 3.0)
            else:
                connection = self.connection_factory(host, addresses[0], 3.0, self.proxy)
            connection.cancel_event = deadline
            if deadline.is_set():
                raise ProviderError("request_timeout")
            connection.request("GET", path, headers={
                "Host": host,
                "User-Agent": "TraderMarketIntelligence/1 (calendar advisory)",
                "Accept": "application/rss+xml, application/xml, text/xml" if rss else "text/calendar",
                "Accept-Encoding": "identity", "Connection": "close"})
            active_socket = connection.sock
            stage = "response"
            response = connection.getresponse()
            if response.status != 200:
                raise ProviderError("http_unavailable")
            if response.getheader("Content-Encoding", "identity").lower() != "identity":
                raise ProviderError("encoding_refused")
            accepted_types = ("application/rss+xml", "application/xml", "text/xml") if rss else ("text/calendar", "text/plain")
            if response.getheader("Content-Type", "").split(";", 1)[0].strip().lower() not in accepted_types:
                raise ProviderError("content_refused")
            length = response.getheader("Content-Length")
            if length is not None and (not length.isascii() or not length.isdigit() or len(length) > 7 or int(length) > MAX_DOWNLOAD_BYTES):
                raise ProviderError("response_too_large")
            chunks, total = [], 0
            stage = "body"
            while True:
                if deadline.is_set() or self.monotonic() - started >= 15.0:
                    raise ProviderError("request_timeout")
                chunk = response.read1(min(16384, MAX_DOWNLOAD_BYTES + 1 - total))
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_DOWNLOAD_BYTES:
                    raise ProviderError("response_too_large")
                chunks.append(chunk)
            if length is not None and total != int(length):
                raise ProviderError("response_incomplete")
            if deadline.is_set() or self.monotonic() - started >= 15.0:
                raise ProviderError("request_timeout")
            return b"".join(chunks)
        except ProviderError:
            raise
        except (OSError, ValueError, http.client.HTTPException) as exc:
            if isinstance(exc, socket.gaierror):
                category = "dns"
            elif isinstance(exc, ssl.SSLError):
                category = "tls"
            elif isinstance(exc, TimeoutError):
                category = "timeout"
            elif isinstance(exc, http.client.HTTPException):
                category = "http_protocol"
            elif isinstance(exc, OSError):
                category = "transport"
            else:
                category = "invalid_data"
            logger.warning("Public feed transport failed: stage=%s category=%s", stage, category)
            if self.proxy is not None:
                raise ProviderError("request_timeout" if deadline.is_set() else "source_unavailable") from None
            raise ProviderError("request_timeout" if deadline.is_set() else "source_unavailable") from exc
        finally:
            timer.cancel()
            with self.state_lock:
                self.active_abort = None
            try:
                for item in (response, connection):
                    if item is not None:
                        try:
                            item.close()
                        except OSError:
                            pass
            finally:
                self.fetch_lock.release()


def parse_bls_calendar(payload: bytes, received_at: datetime) -> CalendarBatch:
    return _parse_calendar(payload, received_at, "bls", BLS_CALENDAR_URL, SERIES)


def parse_bea_calendar(payload: bytes, received_at: datetime) -> CalendarBatch:
    return _parse_calendar(payload, received_at, "bea", BEA_CALENDAR_URL, BEA_SERIES, dated_titles=True)


CALENDAR_SOURCES = {
    "bls": (BLS_CALENDAR_URL, parse_bls_calendar, tuple(sorted(set(SERIES.values())))),
    "bea": (BEA_CALENDAR_URL, parse_bea_calendar, tuple(sorted(set(BEA_SERIES.values())))),
}


def _parse_calendar(payload, received_at, source_name, source_url, series, dated_titles=False):
    utc_time(received_at)
    if not isinstance(payload, bytes) or not payload or len(payload) > MAX_DOWNLOAD_BYTES:
        raise ProviderError("invalid_calendar")
    try:
        from icalendar import Calendar
    except ImportError as exc:
        raise ProviderError("parser_unavailable") from exc
    try:
        calendars = Calendar.from_ical(payload, multiple=True)
        if len(calendars) != 1 or calendars[0].errors or str(calendars[0].get("VERSION")) != "2.0":
            raise ProviderError("invalid_calendar")
        items = calendars[0].walk("VEVENT")
        if len(items) > 1000:
            raise ProviderError("invalid_calendar")
        events, seen, ignored = [], set(), 0
        for item in items:
            if item.errors:
                raise ProviderError("invalid_calendar")
            for name in ("UID", "SUMMARY", "DTSTART", "SEQUENCE", "STATUS", "DTSTAMP", "LAST-MODIFIED"):
                if isinstance(item.get(name), list):
                    raise ProviderError("invalid_calendar")
            if any(name in item for name in ("RRULE", "RDATE", "EXDATE", "RECURRENCE-ID")):
                raise ProviderError("unsupported_calendar")
            uid = str(item.get("UID", ""))
            if not uid.strip() or len(uid) > 1024 or uid in seen:
                raise ProviderError("invalid_calendar")
            seen.add(uid)
            for name in ("DTSTAMP", "LAST-MODIFIED"):
                stamp = item.get(name)
                if stamp is not None:
                    value = stamp.dt
                    if not isinstance(value, datetime) or value.utcoffset() is None or value.astimezone(timezone.utc) > received_at:
                        raise ProviderError("invalid_calendar")
            title = str(item.get("SUMMARY", "")).strip()
            if not title or len(title) > 500:
                raise ProviderError("invalid_calendar")
            normalized = title.casefold()
            category = series.get(normalized)
            if category is None and dated_titles:
                for name, candidate in series.items():
                    if normalized.startswith(name):
                        suffix = normalized[len(name):].lstrip()
                        if suffix.startswith((",", "(")):
                            category = candidate
                            break
            if category is None:
                ignored += 1
                continue
            raw_status = str(item.get("STATUS", "CONFIRMED")).upper()
            if raw_status not in ("CONFIRMED", "TENTATIVE", "CANCELLED"):
                raise ProviderError("invalid_calendar")
            status = {"CONFIRMED": "scheduled", "TENTATIVE": "tentative", "CANCELLED": "cancelled"}[raw_status]
            start = item.get("DTSTART")
            scheduled_at, source_timezone = None, "unknown"
            if start is not None:
                value = start.dt
                source_timezone = str(start.params.get("TZID", "unknown"))
                if isinstance(value, datetime) and value.utcoffset() is not None:
                    ambiguous = value.replace(fold=0).utcoffset() != value.replace(fold=1).utcoffset()
                    if not ambiguous:
                        scheduled_at = value.astimezone(timezone.utc)
                    if source_timezone == "unknown":
                        source_timezone = str(value.tzinfo)
            if scheduled_at is None and status != "cancelled":
                status = "tentative"
            revision = int(item.get("SEQUENCE", 0))
            events.append(CalendarEvent(
                "uid-" + hashlib.sha256(uid.encode("utf-8")).hexdigest(), source_name, source_url,
                category, SYMBOLS, scheduled_at, received_at, title, status,
                source_timezone=source_timezone, revision=revision))
        input_hash = hashlib.sha256(payload).hexdigest()
        categories = tuple(sorted(set(series.values())))
        source = SourceCoverage(source_name, categories, received_at,
                None, None, "unavailable", input_hash, source_url)
        return CalendarBatch(source, tuple(events), input_hash, ignored)
    except ProviderError:
        raise
    except (ValueError, TypeError, AttributeError, OverflowError, RecursionError) as exc:
        raise ProviderError("invalid_calendar") from exc