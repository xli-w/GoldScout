"""Allowlisted calendar adapters. Importing this module performs no IO."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
from html.parser import HTMLParser
import http.client
import ipaddress
import json
import logging
import re
import socket
import ssl
import threading
import time
from typing import Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dns_config import DNSConfigurationError, resolve_host
from market_intelligence.contracts import CalendarEvent, SourceCoverage, SYMBOLS, utc_time
from proxy_config import configured_proxy_url, parse_proxy_url


logger = logging.getLogger(__name__)
_SOCKET_TYPE = socket.socket

BLS_CALENDAR_URL = "https://www.bls.gov/schedule/news_release/bls.ics"
BEA_CALENDAR_URL = "https://www.bea.gov/news/schedule/ics/online-calendar-subscription.ics"
FED_NEWS_URL = "https://www.federalreserve.gov/feeds/press_monetary.xml"
ONS_UPCOMING_URL = "https://api.beta.ons.gov.uk/v1/search/releases?limit=1000&sort=release_date_asc&release-type=type-upcoming"
ONS_CANCELLED_URL = "https://api.beta.ons.gov.uk/v1/search/releases?limit=1000&sort=release_date_asc&release-type=type-cancelled"
BOE_MPC_DATES_URL = "https://www.bankofengland.co.uk/monetary-policy/upcoming-mpc-dates"
CALENDAR_ENDPOINTS = {
    BLS_CALENDAR_URL: ("www.bls.gov", "/schedule/news_release/bls.ics"),
    BEA_CALENDAR_URL: ("www.bea.gov", "/news/schedule/ics/online-calendar-subscription.ics"),
}
RSS_ENDPOINTS = {
    FED_NEWS_URL: ("www.federalreserve.gov", "/feeds/press_monetary.xml"),
    "https://apps.bea.gov/rss/rss.xml": ("apps.bea.gov", "/rss/rss.xml"),
}
JSON_ENDPOINTS = {
    ONS_UPCOMING_URL: ("api.beta.ons.gov.uk", "/v1/search/releases?limit=1000&sort=release_date_asc&release-type=type-upcoming"),
    ONS_CANCELLED_URL: ("api.beta.ons.gov.uk", "/v1/search/releases?limit=1000&sort=release_date_asc&release-type=type-cancelled"),
}
HTML_ENDPOINTS = {
    BOE_MPC_DATES_URL: ("www.bankofengland.co.uk", "/monetary-policy/upcoming-mpc-dates"),
    "https://www.ons.gov.uk/economy/inflationandpriceindices/bulletins/consumerpriceinflation/latest":
        ("www.ons.gov.uk", "/economy/inflationandpriceindices/bulletins/consumerpriceinflation/latest"),
    "https://www.bankofengland.co.uk/monetary-policy-summary-and-minutes/2026/november-2026":
        ("www.bankofengland.co.uk", "/monetary-policy-summary-and-minutes/2026/november-2026"),
    "https://www.bea.gov/news/2026/personal-income-and-outlays-september-2026":
        ("www.bea.gov", "/news/2026/personal-income-and-outlays-september-2026"),
    "https://www.federalreserve.gov/newsevents/pressreleases/monetary20261028a.htm":
        ("www.federalreserve.gov", "/newsevents/pressreleases/monetary20261028a.htm"),
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
    complete_snapshot: bool = False

    def __post_init__(self):
        if not isinstance(self.source, SourceCoverage) or type(self.events) is not tuple or len(self.events) > 1000:
            raise ValueError("Provider batch must contain bounded immutable source evidence")
        if any(not isinstance(event, CalendarEvent) or event.source != self.source.source for event in self.events):
            raise ValueError("Provider events must match their source")
        if self.source.input_hash != self.input_hash or self.source.checked_at is None:
            raise ValueError("Provider batch requires matching downloaded provenance")
        if type(self.ignored_events) is not int or not 0 <= self.ignored_events <= 1000:
            raise ValueError("Invalid ignored event count")
        if type(self.complete_snapshot) is not bool:
            raise ValueError("Snapshot completeness must be explicit")


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
        records = resolve_host(self.proxy.host, self.proxy.port, type=socket.SOCK_STREAM)
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
        self.resolver = resolver or resolve_host
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
        endpoints = {**CALENDAR_ENDPOINTS, **RSS_ENDPOINTS, **JSON_ENDPOINTS, **HTML_ENDPOINTS}
        if url not in endpoints:
            raise ProviderError("endpoint_refused")
        host, path = endpoints[url]
        if url in RSS_ENDPOINTS:
            accepted_types = ("application/rss+xml", "application/xml", "text/xml")
            accept_header = "application/rss+xml, application/xml, text/xml"
        elif url in JSON_ENDPOINTS:
            accepted_types = ("application/json",)
            accept_header = "application/json"
        elif url in HTML_ENDPOINTS:
            accepted_types = ("text/html",)
            accept_header = "text/html"
        else:
            accepted_types = ("text/calendar", "text/plain")
            accept_header = "text/calendar"
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
                "User-Agent": "GoldScoutMarketIntelligence/1.0 (+https://github.com/xli-w/GoldScout)",
                "Accept": accept_header,
                "Accept-Encoding": "identity", "Connection": "close"})
            active_socket = connection.sock
            stage = "response"
            response = connection.getresponse()
            if response.status != 200:
                raise ProviderError("http_unavailable")
            if response.getheader("Content-Encoding", "identity").lower() != "identity":
                raise ProviderError("encoding_refused")
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
            elif isinstance(exc, DNSConfigurationError):
                category = "dns_configuration"
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


def parse_ons_calendar(upcoming_payload, cancelled_payload, upcoming_received_at, cancelled_received_at):
    utc_time(upcoming_received_at)
    utc_time(cancelled_received_at)
    if cancelled_received_at < upcoming_received_at:
        raise ProviderError("invalid_calendar")

    def records(payload):
        if not isinstance(payload, bytes) or not payload or len(payload) > MAX_DOWNLOAD_BYTES:
            raise ProviderError("invalid_calendar")

        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("Duplicate JSON field")
                result[key] = value
            return result

        def reject_constant(value):
            raise ValueError(f"Invalid JSON constant: {value}")

        try:
            response = json.loads(payload, object_pairs_hook=unique_object, parse_constant=reject_constant)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
            raise ProviderError("invalid_calendar") from exc
        if type(response) is not dict or type(response.get("breakdown")) is not dict:
            raise ProviderError("invalid_calendar")
        total = response["breakdown"].get("total")
        releases = response.get("releases")
        if type(total) is not int or not 0 <= total <= 1000 or type(releases) is not list:
            raise ProviderError("invalid_calendar")
        if len(releases) != total:
            raise ProviderError("response_incomplete")
        return releases

    def release_event(item, received_at, cancelled=False):
        if type(item) is not dict or type(item.get("description")) is not dict:
            raise ProviderError("invalid_calendar")
        uri = item.get("uri")
        description = item["description"]
        title = description.get("title")
        if (not isinstance(uri, str) or not re.fullmatch(r"/releases/[A-Za-z0-9._~/-]{1,500}", uri)
                or ".." in uri.split("/") or not isinstance(title, str) or not title.strip()
                or len(title) > 500):
            raise ProviderError("invalid_calendar")
        category = _ons_category(title)
        if category is None:
            return None

        release_date = description.get("release_date")
        scheduled_at = None
        if release_date is not None:
            if not isinstance(release_date, str) or len(release_date) > 64:
                raise ProviderError("invalid_calendar")
            try:
                parsed = datetime.fromisoformat(release_date.replace("Z", "+00:00"))
                if parsed.utcoffset() is None:
                    raise ValueError
                scheduled_at = parsed.astimezone(timezone.utc)
            except ValueError as exc:
                raise ProviderError("invalid_calendar") from exc

        flags = ("cancelled", "finalised", "postponed")
        if any(type(description.get(name)) is not bool for name in flags):
            raise ProviderError("invalid_calendar")
        if cancelled != description["cancelled"]:
            raise ProviderError("invalid_calendar")
        provisional_date = description.get("provisional_date")
        if provisional_date is not None and (
                not isinstance(provisional_date, str) or len(provisional_date) > 128
                or any(ord(character) < 32 or character in "<>" for character in provisional_date)):
            raise ProviderError("invalid_calendar")

        changes = item.get("date_changes")
        notes = []
        if changes is not None:
            if type(changes) is not list or len(changes) > 10:
                raise ProviderError("invalid_calendar")
            for change in changes:
                if type(change) is not dict:
                    raise ProviderError("invalid_calendar")
                previous = change.get("previous_date")
                notice = change.get("change_notice")
                if previous is not None:
                    if not isinstance(previous, str) or len(previous) > 64:
                        raise ProviderError("invalid_calendar")
                    notes.append(f"Previous scheduled date: {previous}")
                if notice is not None:
                    if (not isinstance(notice, str) or not notice.strip() or len(notice) > 1000
                            or any(ord(character) < 32 or character in "<>" for character in notice)):
                        raise ProviderError("invalid_calendar")
                    notes.append(notice.strip())
        if provisional_date:
            notes.append(f"Provisional date: {provisional_date}")
        if len(notes) > 10:
            raise ProviderError("invalid_calendar")

        if cancelled:
            status = "cancelled"
        elif description["postponed"]:
            status = "postponed"
        elif provisional_date is not None or not description["finalised"]:
            status = "tentative"
        else:
            status = "scheduled"
        event_id = "ons-" + hashlib.sha256(uri.encode("utf-8")).hexdigest()
        return CalendarEvent(
            event_id, "ons", "https://www.ons.gov.uk" + uri, category, (), scheduled_at,
            received_at, title.strip(), status, source_timezone="UTC", source_notes=tuple(notes))

    upcoming = records(upcoming_payload)
    cancelled_records = records(cancelled_payload)
    events = []
    ignored = 0
    for item in upcoming:
        event = release_event(item, upcoming_received_at)
        if event is None:
            ignored += 1
        else:
            events.append(event)
    for item in cancelled_records:
        event = release_event(item, cancelled_received_at, cancelled=True)
        if event is None:
            ignored += 1
        elif event.scheduled_at is not None and event.scheduled_at < cancelled_received_at - timedelta(days=30):
            ignored += 1
        else:
            events.append(event)
    if len(events) > 1000 or len({event.event_id for event in events}) != len(events):
        raise ProviderError("invalid_calendar")

    checked_at = cancelled_received_at
    input_hash = hashlib.sha256(
        len(upcoming_payload).to_bytes(4, "big") + upcoming_payload
        + len(cancelled_payload).to_bytes(4, "big") + cancelled_payload).hexdigest()
    source = SourceCoverage(
        "ons", ("employment", "growth", "inflation"), checked_at,
        None, None, "unavailable", input_hash, ONS_UPCOMING_URL,
        (ONS_UPCOMING_URL, ONS_CANCELLED_URL))
    return CalendarBatch(source, tuple(events), input_hash, ignored, complete_snapshot=True)


def _ons_category(title):
    value = title.casefold()
    if any(term in value for term in (
            "consumer price", "producer price", "cpi", "cpih", "inflation")):
        return "inflation"
    if any(term in value for term in (
            "labour market", "labour force", "employment", "unemployment", "vacanc", "earnings")):
        return "employment"
    if any(term in value for term in (
            "gross domestic product", "gdp", "retail sales", "public sector finances",
            "trade in goods", "trade in services", "national accounts", "economic activity")):
        return "growth"
    return None


class _VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in ("script", "style"):
            self.skip_depth += 1

    def handle_endtag(self, tag):
        if tag.lower() in ("script", "style") and self.skip_depth:
            self.skip_depth -= 1

    def handle_data(self, data):
        if not self.skip_depth and data.strip():
            self.parts.append(data.strip())


def parse_boe_mpc_calendar(payload, received_at):
    utc_time(received_at)
    if not isinstance(payload, bytes) or not payload or len(payload) > MAX_DOWNLOAD_BYTES:
        raise ProviderError("invalid_calendar")
    try:
        text = payload.decode("utf-8")
        parser = _VisibleText()
        parser.feed(text)
        parser.close()
        content = " ".join(parser.parts)
    except (UnicodeDecodeError, ValueError):
        raise ProviderError("invalid_calendar") from None
    year_sections = re.findall(
        r"(20\d{2})\s+confirmed dates(.*?)(?=20\d{2}\s+confirmed dates|Current Bank Rate|"
        r"Monetary Policy Committee Reports and announcements|$)", content, re.IGNORECASE)
    if not year_sections:
        raise ProviderError("invalid_calendar")
    try:
        london = ZoneInfo("Europe/London")
    except ZoneInfoNotFoundError as exc:
        raise ProviderError("parser_unavailable") from exc

    events, seen_years, future_years = [], set(), set()
    months = {name.casefold(): index for index, name in enumerate(
        ("January", "February", "March", "April", "May", "June", "July", "August",
         "September", "October", "November", "December"), start=1)}
    for year_text, section in year_sections:
        year = int(year_text)
        if year in seen_years:
            raise ProviderError("invalid_calendar")
        seen_years.add(year)
        dates = re.findall(r"Thursday\s+(\d{1,2})\s+([A-Za-z]+)", section, re.IGNORECASE)
        if len(dates) != 8:
            raise ProviderError("invalid_calendar")
        seen_dates = set()
        for day_text, month_text in dates:
            try:
                month = months[month_text.casefold()]
                local = datetime(year, month, int(day_text), 12, 0, tzinfo=london)
            except (KeyError, ValueError) as exc:
                raise ProviderError("invalid_calendar") from exc
            if local.weekday() != 3 or local.date() in seen_dates:
                raise ProviderError("invalid_calendar")
            seen_dates.add(local.date())
            scheduled_at = local.astimezone(timezone.utc)
            if scheduled_at > received_at:
                events.append(CalendarEvent(
                    f"boe-mpc-{scheduled_at:%Y-%m-%d}", "boe_mpc", BOE_MPC_DATES_URL,
                    "monetary_policy", (), scheduled_at, received_at,
                    f"MPC Bank Rate announcement and minutes - {local:%d %B %Y}",
                    source_timezone="Europe/London",
                    source_notes=("The Bank states MPC summaries and minutes are published at 12:00 noon on Thursdays.",)))
                if year > received_at.year:
                    future_years.add(year)
    if len(events) > 24 or not future_years:
        raise ProviderError("invalid_calendar")
    events.sort(key=lambda event: event.scheduled_at)
    input_hash = hashlib.sha256(payload).hexdigest()
    source = SourceCoverage(
        "boe_mpc", ("monetary_policy",), received_at,
        None, None, "unavailable", input_hash, BOE_MPC_DATES_URL)
    return CalendarBatch(source, tuple(events), input_hash, 0, complete_snapshot=True)


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


CALENDAR_SOURCES = {
    "bls": (BLS_CALENDAR_URL, parse_bls_calendar, tuple(sorted(set(SERIES.values())))),
    "bea": (BEA_CALENDAR_URL, parse_bea_calendar, tuple(sorted(set(BEA_SERIES.values())))),
    "ons": (ONS_UPCOMING_URL, parse_ons_calendar, ("employment", "growth", "inflation")),
    "boe_mpc": (BOE_MPC_DATES_URL, parse_boe_mpc_calendar, ("monetary_policy",)),
}