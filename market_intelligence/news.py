"""Official RSS evidence only; no AI, trading decisions or IO on import."""

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
from html.parser import HTMLParser
import json
from typing import Tuple
from urllib.parse import urlsplit, urlunsplit

from market_intelligence.contracts import NewsEvidence, identifier, nonnegative_integer, utc_time
from market_intelligence.providers import FED_NEWS_URL, MAX_DOWNLOAD_BYTES, ProviderError


NEWS_ERROR_CODES = {
    "endpoint_refused", "request_timeout", "request_in_progress", "address_refused", "http_unavailable",
    "encoding_refused", "content_refused", "response_too_large", "response_incomplete",
    "source_unavailable", "invalid_news", "parser_unavailable", "article_refused",
}


@dataclass(frozen=True)
class NewsBatch:
    source: str
    feed_url: str
    received_at: datetime
    input_hash: str
    items: Tuple[NewsEvidence, ...]

    def __post_init__(self):
        utc_time(self.received_at)
        if self.source != "fed_monetary" or self.feed_url != FED_NEWS_URL:
            raise ValueError("Unregistered news batch source")
        if not isinstance(self.input_hash, str) or len(self.input_hash) != 64 or any(character not in "0123456789abcdef" for character in self.input_hash):
            raise ValueError("News batch requires downloaded SHA256 provenance")
        if type(self.items) is not tuple or len(self.items) > 200:
            raise ValueError("News items must be a bounded immutable tuple")
        if any(not isinstance(item, NewsEvidence) or item.source != self.source or item.received_at != self.received_at for item in self.items):
            raise ValueError("News evidence must match its receipt/source")
        if len({item.evidence_id for item in self.items}) != len(self.items):
            raise ValueError("Duplicate news evidence")


@dataclass(frozen=True)
class ArchivedNews:
    acquisition_id: str
    revision: int
    settings_revision: int
    available_at: datetime
    batch: NewsBatch
    parser_version: str = "fed-rss-v1"

    def __post_init__(self):
        identifier(self.acquisition_id)
        nonnegative_integer(self.revision)
        nonnegative_integer(self.settings_revision)
        utc_time(self.available_at)
        if self.revision == 0 or not isinstance(self.batch, NewsBatch) or self.available_at < self.batch.received_at:
            raise ValueError("Archive availability must follow a validated source receipt")
        if self.parser_version != "fed-rss-v1":
            raise ValueError("Unsupported archived parser version")


@dataclass(frozen=True)
class NewsObservation:
    attempt_id: str
    revision: int
    settings_revision: int
    started_at: datetime
    completed_at: datetime
    available_at: datetime
    status: str
    error_code: str | None
    acquisition_id: str | None
    last_success_id: str | None
    source: str = "fed_monetary"

    def __post_init__(self):
        identifier(self.attempt_id)
        nonnegative_integer(self.revision)
        nonnegative_integer(self.settings_revision)
        for value in (self.started_at, self.completed_at, self.available_at):
            utc_time(value)
        if self.revision == 0 or not self.started_at <= self.completed_at <= self.available_at:
            raise ValueError("News observation times must follow submission, completion and commit")
        if self.source != "fed_monetary" or self.status not in ("successful", "unavailable", "invalid"):
            raise ValueError("Unsupported news source status")
        for value in (self.error_code, self.acquisition_id, self.last_success_id):
            if value is not None:
                identifier(value)
        if self.status == "successful":
            if self.error_code is not None or self.acquisition_id != self.attempt_id or self.last_success_id != self.acquisition_id:
                raise ValueError("Successful source status requires its own archive acquisition")
        elif self.error_code is None or self.acquisition_id is not None:
            raise ValueError("Failed source status requires a sanitized error and no acquisition")
        if self.error_code is not None and self.error_code not in NEWS_ERROR_CODES:
            raise ValueError("Unregistered source error code")


class PlainText(HTMLParser):
    breaks = {"p", "div", "br", "hr", "li", "ul", "ol", "table", "tr", "td", "th", "section", "article", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "iframe", "object"):
            self.hidden.append(tag)
        if not self.hidden and tag in self.breaks:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if self.hidden and tag == self.hidden[-1]:
            self.hidden.pop()
        if not self.hidden and tag in self.breaks:
            self.parts.append(" ")

    def handle_startendtag(self, tag, attrs):
        if not self.hidden and tag in self.breaks:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain_text(value, maximum):
    parser = PlainText()
    parser.feed(value)
    parser.close()
    text = " ".join("".join(parser.parts).split())
    if len(text) > maximum or any(ord(character) < 32 for character in text):
        raise ProviderError("invalid_news")
    return text


def fed_article_url(value):
    if not isinstance(value, str) or len(value) > 2048 or any(character.isspace() or ord(character) < 32 for character in value):
        raise ProviderError("article_refused")
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or parsed.hostname != "www.federalreserve.gov" or parsed.username is not None
                or parsed.password is not None or parsed.port not in (None, 443) or parsed.query
                or not parsed.path.startswith("/newsevents/pressreleases/") or not parsed.path.endswith(".htm")
                or "%" in parsed.path or any(segment in (".", "..", "") for segment in parsed.path.split("/")[1:])
                or "\\" in parsed.path):
            raise ProviderError("article_refused")
        return urlunsplit(("https", "www.federalreserve.gov", parsed.path, "", ""))
    except ValueError as exc:
        raise ProviderError("article_refused") from exc


def parse_fed_news(payload: bytes, received_at: datetime) -> NewsBatch:
    utc_time(received_at)
    if not isinstance(payload, bytes) or not payload or len(payload) > MAX_DOWNLOAD_BYTES:
        raise ProviderError("invalid_news")
    try:
        from defusedxml.ElementTree import fromstring
        from defusedxml.common import DefusedXmlException
        from xml.etree.ElementTree import ParseError
    except ImportError as exc:
        raise ProviderError("parser_unavailable") from exc
    try:
        root = fromstring(payload, forbid_dtd=True, forbid_entities=True, forbid_external=True)
        if root.tag != "rss" or root.get("version") != "2.0" or len(root.findall("channel")) != 1:
            raise ProviderError("invalid_news")
        nodes, stack = 0, [(root, 0)]
        while stack:
            node, depth = stack.pop()
            nodes += 1
            if nodes > 4000 or depth > 32:
                raise ProviderError("invalid_news")
            stack.extend((child, depth + 1) for child in node)
        items = root.find("channel").findall("item")
        if len(items) > 200:
            raise ProviderError("invalid_news")
        records = {}
        for item in items:
            values = {}
            for name in ("title", "link", "description", "pubDate", "guid"):
                matches = item.findall(name)
                if len(matches) > 1 or (matches and len(matches[0])):
                    raise ProviderError("invalid_news")
                values[name] = matches[0].text or "" if matches else ""
            title = plain_text(values["title"], 500)
            if not title:
                raise ProviderError("invalid_news")
            snippet = plain_text(values["description"], 2000)
            url = fed_article_url(values["link"].strip())
            published_at = None
            if values["pubDate"].strip():
                published = parsedate_to_datetime(values["pubDate"].strip())
                if published.utcoffset() is not None:
                    published_at = published.astimezone(timezone.utc)
                    if published_at > received_at:
                        raise ProviderError("invalid_news")
            content = json.dumps({"url": url, "title": title, "snippet": snippet,
                                  "published_at": published_at.isoformat() if published_at else None},
                                 sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            evidence_id = "news-" + content_hash
            cluster_id = "cluster-" + hashlib.sha256(("federal-reserve:" + url).encode("utf-8")).hexdigest()
            records[evidence_id] = NewsEvidence(evidence_id, "fed_monetary", "federal-reserve", url,
                                                 title, snippet, published_at, received_at, content_hash, cluster_id)
        return NewsBatch("fed_monetary", FED_NEWS_URL, received_at, hashlib.sha256(payload).hexdigest(),
                         tuple(records[key] for key in sorted(records)))
    except ProviderError:
        raise
    except (DefusedXmlException, ParseError, ValueError, TypeError, AttributeError, OverflowError, RecursionError) as exc:
        raise ProviderError("invalid_news") from exc