"""Pure, bounded official release excerpts; no discovery, persistence or AI.

Supported means the specific documented shape, not live validation or complete
coverage. Some URLs deliberately identify a dated release, not "the latest".
See the release-content matrix in docs/intelligence.md for scope and rights.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
import re
from types import MappingProxyType
import xml.etree.ElementTree as ET

from market_intelligence.compilation import SourceDocument, document_id


MAX_CONTENT_BYTES = 1024 * 1024


class ContentError(ValueError):
    """Unsupported source, malformed content, cancellation or invalid clock."""


@dataclass(frozen=True)
class ContentSource:
    institution: str
    url: str
    kind: str
    topic: str
    geography: str
    supported: bool
    reason: str


CONTENT_SOURCES = MappingProxyType({
    "ons_cpi": ContentSource(
        "Office for National Statistics",
        "https://www.ons.gov.uk/economy/inflationandpriceindices/bulletins/consumerpriceinflation/latest",
        "release", "inflation", "UK", True,
        "CPI/CPIH Main points only; date-only publication stays unknown."),
    "boe_policy": ContentSource(
        "Bank of England",
        "https://www.bankofengland.co.uk/monetary-policy-summary-and-minutes/2026/november-2026",
        "policy", "monetary_policy", "UK", True,
        "Dated November 2026 summary only, not minutes or rolling coverage; "
        "personal/internal non-commercial use only under Bank legal terms."),
    "bls_cpi": ContentSource(
        "Bureau of Labor Statistics", "https://www.bls.gov/news.release/cpi.nr0.htm",
        "release", "inflation", "US", False,
        "Official public research returned Access Denied; release shape unverified. "
        "No bypass or speculative parser."),
    "bls_employment": ContentSource(
        "Bureau of Labor Statistics", "https://www.bls.gov/news.release/empsit.nr0.htm",
        "release", "employment", "US", False,
        "Official public research returned Access Denied; release shape unverified. "
        "No bypass or speculative parser."),
    "bea_gdp": ContentSource(
        "Bureau of Economic Analysis", "https://apps.bea.gov/rss/rss.xml",
        "release", "growth", "US", True,
        "First national GDP estimate RSS item only, official description excerpt; "
        "not full release, regional GDP, tables or numeric extraction."),
    "bea_pce": ContentSource(
        "Bureau of Economic Analysis",
        "https://www.bea.gov/news/2026/personal-income-and-outlays-september-2026",
        "release", "inflation", "US", True,
        "Dated September 2026 PCE price-index monthly/yearly paragraphs only; "
        "not rolling coverage or structured extraction."),
    "fed_statement": ContentSource(
        "Federal Reserve",
        "https://www.federalreserve.gov/newsevents/pressreleases/monetary20261028a.htm",
        "policy", "monetary_policy", "US", True,
        "Dated 28 October 2026 FOMC statement paragraphs only; no implementation "
        "note, minutes, schedule or rolling discovery."),
})


def _source(name):
    if not isinstance(name, str) or name not in CONTENT_SOURCES:
        raise ContentError("unknown content source")
    source = CONTENT_SOURCES[name]
    if not source.supported:
        raise ContentError("unsupported content source: " + name)
    return source


def _utc(value):
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ContentError("content clock must be aware UTC")
    return value.astimezone(timezone.utc)


def _bounded(value, maximum):
    value = " ".join(value.split())
    if not value or len(value) > maximum or any(ord(c) < 32 or c in "<>" for c in value):
        raise ContentError("invalid or oversized content text")
    return value


@dataclass
class _Node:
    tag: str
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)
    closed: bool = False

    def find(self, predicate):
        result = []
        for child in self.children:
            if isinstance(child, _Node):
                if predicate(child):
                    result.append(child)
                result.extend(child.find(predicate))
        return result

    def has_class(self, name):
        return name in self.attrs.get("class", "").split()

    def text(self):
        if self.tag in {"script", "style", "noscript", "template"}:
            return ""
        return "".join(c.text() if isinstance(c, _Node) else c for c in self.children)


class _Page(HTMLParser):
    # Tolerate optional closing tags outside selected regions. Every selected
    # block itself must have an explicit close; implicit repair is not evidence.
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input",
            "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, value):
        super().__init__(convert_charrefs=True)
        self.root = _Node("root")
        self.stack = [self.root]
        self.count = 0
        self.feed(value)
        self.close()

    def handle_starttag(self, tag, attrs):
        self.count += 1
        if self.count > 30000 or len(self.stack) > 128:
            raise ContentError("HTML complexity limit")
        if len(dict(attrs)) != len(attrs):
            raise ContentError("duplicate HTML attribute")
        node = _Node(tag, {k: v or "" for k, v in attrs})
        self.stack[-1].children.append(node)
        if tag in self.VOID:
            node.closed = True
            if tag == "br":
                node.children.append(" ")
        else:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                self.stack[index].closed = True
                del self.stack[index:]
                return

    def handle_data(self, value):
        self.stack[-1].children.append(value)


def _one(nodes):
    if len(nodes) != 1 or not nodes[0].closed:
        raise ContentError("missing, ambiguous or incomplete selected block")
    return nodes[0]


def _clean(node, maximum=12000):
    if not node.closed or node.find(lambda n: n.tag in {
            "script", "style", "iframe", "form", "template", "noscript"}):
        raise ContentError("incomplete or active selected text")
    if node.find(lambda n: not n.closed):
        raise ContentError("unclosed selected text")
    return _bounded(node.text(), maximum)


def _page(payload):
    try:
        value = payload.decode("utf-8-sig")
    except UnicodeError as error:
        raise ContentError("content must be UTF-8") from error
    if "\x00" in value or not re.search(r"</html>\s*$", value, re.I):
        raise ContentError("incomplete HTML page")
    if "<!ENTITY" in value.upper():
        raise ContentError("HTML entity declarations unsupported")
    return _Page(value).root


def _date_only(value):
    """Validate an explicit date without manufacturing its UTC release time."""
    try:
        datetime.strptime(value, "%d %B %Y")
    except ValueError as error:
        raise ContentError("invalid source date") from error
    return None


def _us_time(date, clock):
    match = re.fullmatch(r"(\d{1,2}):(\d{2}) ([ap])\.m\. (EDT|EST)", clock)
    if not match:
        raise ContentError("ambiguous US publication time")
    hour, minute = int(match[1]), int(match[2])
    if not 1 <= hour <= 12 or minute > 59:
        raise ContentError("invalid US publication time")
    try:
        day = datetime.strptime(date, "%B %d, %Y")
    except ValueError as error:
        raise ContentError("invalid US publication date") from error
    offset = timezone(timedelta(hours=-4 if match[4] == "EDT" else -5))
    return day.replace(hour=hour % 12 + (12 if match[3] == "p" else 0),
                       minute=minute, tzinfo=offset).astimezone(timezone.utc)


def _ons(root):
    title = _clean(_one(root.find(lambda n: n.tag == "h1")), 500)
    if not title.startswith("Consumer price inflation, UK: "):
        raise ContentError("unexpected ONS release")
    section = _one(root.find(lambda n: n.attrs.get("id") == "main-points"))
    _clean(section, MAX_CONTENT_BYTES)  # Reject repaired/incomplete list wrappers too.
    points = section.find(lambda n: n.tag == "li")
    if not 2 <= len(points) <= 20:
        raise ContentError("incomplete ONS main points")
    text = "\n".join(_clean(point) for point in points)
    if "(CPIH)" not in text or "(CPI)" not in text:
        raise ContentError("missing ONS CPI/CPIH evidence")
    # Only the release-date label, never the next-release date or reference month.
    labels = root.find(lambda n: n.tag == "p" and "Release date:" in n.text())
    if len(labels) > 1:
        raise ContentError("ambiguous ONS release date")
    if labels:
        value = _bounded(labels[0].text(), 200)
        _date_only(value.removeprefix("Release date:").strip())
    return title, text, None


def _boe(root):
    title = _clean(_one(root.find(lambda n: n.tag == "h1")), 500)
    if not title.endswith("November 2026 Monetary Policy Summary and Minutes"):
        raise ContentError("unexpected BoE release")
    sections = root.find(lambda n: n.tag == "section" and n.has_class("page-section"))
    selected = [s for s in sections if any(
        n.tag == "h2" and _bounded(n.text(), 500) == "Monetary Policy Summary, November 2026"
        for n in s.children if isinstance(n, _Node))]
    section = _one(selected)
    points = [n for n in section.children if isinstance(n, _Node) and n.tag == "p"]
    if not 2 <= len(points) <= 20:
        raise ContentError("incomplete BoE summary")
    text = "\n".join(_clean(point) for point in points)
    if "Monetary Policy Committee" not in text or "Bank Rate" not in text:
        raise ContentError("missing BoE decision evidence")
    dates = root.find(lambda n: n.has_class("published-date"))
    if len(dates) > 1:
        raise ContentError("ambiguous BoE publication date")
    if dates:
        _date_only(_bounded(dates[0].text(), 200).removeprefix("Published on").strip())
    return title, text, None


def _fed(root):
    article = _one(root.find(lambda n: n.attrs.get("id") == "article"))
    title = _clean(_one(article.find(lambda n: n.tag == "h3" and n.has_class("title"))), 500)
    if title != "Federal Reserve issues FOMC statement":
        raise ContentError("unexpected Fed release")
    bodies = article.find(lambda n: n.tag == "div" and n.has_class("col-md-8")
                          and any(isinstance(c, _Node) and c.tag == "p"
                                  and c.text().startswith("The Federal Open Market Committee")
                                  for c in n.children))
    body = _one(bodies)
    points = []
    ended = False
    for node in body.children:
        if not isinstance(node, _Node) or node.tag != "p":
            continue
        if node.text().startswith("For media inquiries,"):
            ended = True
            break
        if node.text().strip():
            points.append(_clean(node))
    if not ended or len(points) < 3 or "federal funds rate" not in " ".join(points):
        raise ContentError("incomplete Fed statement")
    dates = article.find(lambda n: n.has_class("article__time"))
    times = article.find(lambda n: n.has_class("releaseTime"))
    if not dates and not times:
        published = None
    else:
        day = _bounded(_one(dates).text(), 100)
        # Official releaseTime <p> has an omitted end tag before sharing <ul>.
        time_node = times[0] if len(times) == 1 else None
        if time_node is None:
            raise ContentError("ambiguous Fed release time")
        leading = "".join(c for c in time_node.children if isinstance(c, str))
        clock = _bounded(leading, 100).removeprefix("For release at ")
        published = _us_time(day, clock)
    return title, "\n".join(points), published


def _bea_pce(root):
    articles = root.find(lambda n: n.tag == "article" and any(
        h.text().strip() == "Personal Income and Outlays, September 2026"
        for h in n.find(lambda x: x.tag == "h1")))
    article = _one(articles)
    title = _clean(_one(article.find(lambda n: n.tag == "h1")), 500)
    body = _one(article.find(lambda n: n.has_class("field--name-body")))
    points = []
    for node in body.children:
        if not isinstance(node, _Node):
            continue
        if node.tag in {"h2", "h3"} or node.has_class("table-responsive"):
            break
        if node.tag == "p" and "PCE price index" in node.text():
            points.append(_clean(node))
    if len(points) != 2 or not points[0].startswith("From the preceding month,") or not (
            points[1].startswith("From the same month one year ago,")):
        raise ContentError("incomplete BEA PCE price paragraphs")
    dates = article.find(lambda n: n.has_class("field--name-field-release-date"))
    published = None
    if dates:
        value = _bounded(_one(dates).text(), 200)
        match = re.fullmatch(
            r"EMBARGOED UNTIL RELEASE AT (\d{1,2}:\d{2} [ap]\.m\. (?:EDT|EST)), "
            r"(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday), (.+)", value)
        if not match:
            raise ContentError("ambiguous BEA publication time")
        published = _us_time(match[3], match[1])
        if published.strftime("%A") != match[2]:
            raise ContentError("inconsistent BEA publication weekday")
    return title, "\n".join(points), published


def _bea_gdp(payload):
    # ElementTree does not fetch external entities; reject declarations as well.
    try:
        value = payload.decode("utf-8-sig")
    except UnicodeError as error:
        raise ContentError("RSS must be UTF-8/ASCII") from error
    if "\x00" in value or "<!DOCTYPE" in value.upper() or "<!ENTITY" in value.upper():
        raise ContentError("RSS declarations unsupported")
    try:
        root = ET.fromstring(value)
    except ET.ParseError as error:
        raise ContentError("invalid RSS") from error
    channels = root.findall("channel")
    if root.tag != "rss" or len(channels) != 1:
        raise ContentError("unexpected RSS shape")
    items = channels[0].findall("item")
    if len(items) > 1000:
        raise ContentError("RSS item limit")
    # Only the FIRST matching item is selected. This feed includes historical
    # entries without descriptions, and regional GDP is deliberately excluded.
    pattern = r"^(?:GDP|Gross Domestic Product) \((?:Advance|Second|Third) Estimate\), "
    matches = [item for item in items if re.match(pattern, item.findtext("title", ""))]
    if not matches:
        raise ContentError("national GDP summary unavailable")
    item = matches[0]
    def field_text(name, required=True):
        fields = item.findall(name)
        if not fields and not required:
            return None
        if len(fields) != 1 or list(fields[0]) or not fields[0].text:
            raise ContentError("invalid selected GDP field")
        return fields[0].text
    title = _bounded(field_text("title"), 500)
    link = _bounded(field_text("link"), 2048)
    if not re.fullmatch(r"https://www\.bea\.gov/news/\d{4}/[a-z0-9-]+", link):
        raise ContentError("invalid GDP source link")
    description = field_text("description")
    fragment = _Page(description).root
    blocks = [n for n in fragment.children if isinstance(n, _Node)]
    if blocks:
        if any(n.tag != "p" for n in blocks) or any(
                isinstance(n, str) and n.strip() for n in fragment.children):
            raise ContentError("unsupported GDP description markup")
        text = "\n".join(_clean(n) for n in blocks)
    else:
        text = _bounded(fragment.text(), 12000)
    if "gross domestic product" not in text.lower():
        raise ContentError("missing GDP release evidence")
    raw_date = field_text("pubDate", required=False)
    published = None
    if raw_date is not None:
        if not re.fullmatch(
                r"(Mon|Tue|Wed|Thu|Fri|Sat|Sun), \d{1,2} [A-Z][a-z]{2} \d{4} "
                r"\d{2}:\d{2}:\d{2} (?:EST|EDT|GMT|[+-]\d{4})", raw_date):
            raise ContentError("ambiguous GDP publication date")
        try:
            value = parsedate_to_datetime(raw_date)
            published = value.astimezone(timezone.utc)
        except (ValueError, TypeError, OverflowError) as error:
            raise ContentError("invalid GDP publication date") from error
        if value.strftime("%a") != raw_date[:3]:
            raise ContentError("inconsistent GDP publication weekday")
    return title, text, published, link


def parse_content(source_name: str, payload: bytes,
                  received_at: datetime) -> tuple[SourceDocument, ...]:
    """Parse one selected official excerpt atomically, never a partial tuple."""
    source = _source(source_name)
    received_at = _utc(received_at)
    if type(payload) is not bytes or not 0 < len(payload) <= MAX_CONTENT_BYTES:
        raise ContentError("content byte limit or invalid payload")
    url = source.url
    if source_name == "bea_gdp":
        title, text, published, url = _bea_gdp(payload)
    else:
        root = _page(payload)
        title, text, published = {
            "ons_cpi": _ons, "boe_policy": _boe, "fed_statement": _fed,
            "bea_pce": _bea_pce,
        }[source_name](root)
    title = _bounded(title, 500)
    text = _bounded(text, 12000)
    if published is not None and published > received_at:
        raise ContentError("release publication is after receipt")
    evidence_id = document_id(source_name, url, title, text, published)
    family = {"ons_cpi": "ons", "boe_policy": "boe", "bea_gdp": "bea",
              "bea_pce": "bea", "fed_statement": "fed"}[source_name]
    return (SourceDocument(
        evidence_id=evidence_id, source=source_name,
        publisher_family=family, source_url=url, title=title,
        text=text, topic=source.topic, geography=source.geography,
        kind=source.kind, published_at=published, received_at=received_at,
    ),)


def collect_content(source_name, transport, clock, cancel_event):
    """One fixed fetch; supplied transport owns network policy and byte bounds."""
    source = _source(source_name)
    if cancel_event is not None and cancel_event.is_set():
        raise ContentError("content collection cancelled")
    payload = transport.fetch(source.url, cancel_event=cancel_event)
    if cancel_event is not None and cancel_event.is_set():
        raise ContentError("content collection cancelled")
    documents = parse_content(source_name, payload, clock())
    if cancel_event is not None and cancel_event.is_set():
        raise ContentError("content collection cancelled")
    return payload, documents
