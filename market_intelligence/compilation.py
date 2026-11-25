"""Deterministic public evidence selection; never imports portfolio records."""

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
import hashlib
import json
from typing import Optional, Tuple
from urllib.parse import urlsplit

from market_intelligence.contracts import CalendarSnapshot, identifier, utc_time


COMPILER_VERSION = "public-topics-v2"
SUPPORTED_COMPILER_VERSIONS = ("public-topics-v1", COMPILER_VERSION)
PUBLIC_TOPICS = ("inflation", "employment", "growth", "monetary_policy")
DOCUMENT_LOOKBACK_DAYS = {"inflation": 60, "employment": 45, "growth": 100, "monetary_policy": 90}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False)


def timestamp(value):
    return utc_time(value).isoformat().replace("+00:00", "Z")


def plain_text(value, maximum):
    if (not isinstance(value, str) or not value.strip() or len(value) > maximum
            or any(ord(char) < 32 or char in "<>" for char in value)):
        raise ValueError("Public evidence requires bounded plain text")
    return value


@dataclass(frozen=True)
class SourceDocument:
    evidence_id: str
    source: str
    publisher_family: str
    source_url: str
    title: str
    text: str
    topic: str
    geography: str
    kind: str
    published_at: Optional[datetime]
    received_at: datetime
    scheduled_at: Optional[datetime] = None
    published_on: Optional[date] = None

    def __post_init__(self):
        for value in (self.evidence_id, self.source, self.publisher_family):
            identifier(value)
        plain_text(self.title, 500)
        plain_text(self.text, 12000)
        if self.topic not in PUBLIC_TOPICS or self.geography not in ("UK", "US"):
            raise ValueError("Unsupported public topic or geography")
        if self.kind not in ("release", "policy", "schedule"):
            raise ValueError("Unsupported public document kind")
        utc_time(self.received_at)
        for value in (self.published_at, self.scheduled_at):
            if value is not None:
                utc_time(value)
        if (self.published_on is not None
                and (type(self.published_on) is not date or self.published_at is not None)):
            raise ValueError("Publication date must retain day precision without an instant")
        if self.published_at is not None and self.published_at > self.received_at:
            raise ValueError("Publication cannot follow receipt")
        if (not isinstance(self.source_url, str) or len(self.source_url) > 2048
                or any(char.isspace() or ord(char) < 32 for char in self.source_url)):
            raise ValueError("Invalid public source URL")
        try:
            url = urlsplit(self.source_url)
            valid = (url.scheme == "https" and bool(url.hostname) and url.username is None
                     and url.password is None and url.port in (None, 443))
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("Public sources require HTTPS")

    def wire(self):
        value = asdict(self)
        for name in ("published_at", "received_at", "scheduled_at"):
            value[name] = timestamp(value[name]) if value[name] is not None else None
        value["published_on"] = self.published_on.isoformat() if self.published_on is not None else None
        return value

    def semantic(self):
        return {key: value for key, value in self.wire().items()
                if key not in ("evidence_id", "received_at")}


def document_id(source, url, title, text, published_at=None):
    value = (source, url, title, text, timestamp(published_at) if published_at else None)
    return "public-" + hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def restore_document(value):
    fields = set(SourceDocument.__dataclass_fields__)
    if type(value) is not dict or set(value) not in (fields, fields - {"published_on"}):
        raise ValueError("Invalid public evidence fields")
    from market_intelligence.store import parse_time
    result = dict(value)
    result.setdefault("published_on", None)
    for name in ("published_at", "received_at", "scheduled_at"):
        if result[name] is not None:
            result[name] = parse_time(result[name])
    if result["published_on"] is not None:
        if type(result["published_on"]) is not str:
            raise ValueError("Invalid publication date")
        try:
            result["published_on"] = date.fromisoformat(result["published_on"])
        except ValueError as exc:
            raise ValueError("Invalid publication date") from exc
    return SourceDocument(**result)


@dataclass(frozen=True)
class PublicBundle:
    documents: Tuple[SourceDocument, ...]
    cutoff: datetime
    gaps: Tuple[str, ...]
    omitted: int
    compiler_version: str = COMPILER_VERSION

    def __post_init__(self):
        utc_time(self.cutoff)
        if (type(self.documents) is not tuple or not 1 <= len(self.documents) <= 40
                or any(not isinstance(item, SourceDocument) or item.received_at > self.cutoff
                       for item in self.documents)
                or len({item.evidence_id for item in self.documents}) != len(self.documents)):
            raise ValueError("Invalid public bundle documents")
        if type(self.gaps) is not tuple or len(self.gaps) > 30:
            raise ValueError("Invalid public coverage gaps")
        for gap in self.gaps:
            plain_text(gap, 500)
        if type(self.omitted) is not int or self.omitted < 0:
            raise ValueError("Invalid public omission count")
        if self.compiler_version not in SUPPORTED_COMPILER_VERSIONS:
            raise ValueError("Unsupported public compiler version")

    @property
    def input_hash(self):
        evidence = [item.semantic() for item in self.documents]
        if self.compiler_version == "public-topics-v1":
            evidence = [{key: value for key, value in item.items() if key != "published_on"}
                        for item in evidence]
        value = {"compiler": self.compiler_version, "evidence": evidence,
                 "gaps": self.gaps, "omitted": self.omitted}
        return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()

    def wire(self):
        return {"compiler_version": self.compiler_version, "input_hash": self.input_hash,
                "cutoff": timestamp(self.cutoff), "evidence": [item.wire() for item in self.documents],
                "gaps": list(self.gaps), "omitted": self.omitted}


def restore_bundle(value):
    from market_intelligence.store import parse_time
    expected = {"compiler_version", "input_hash", "cutoff", "evidence", "gaps", "omitted"}
    if (type(value) is not dict or set(value) != expected
            or value["compiler_version"] not in SUPPORTED_COMPILER_VERSIONS
            or type(value["evidence"]) is not list or type(value["gaps"]) is not list):
        raise ValueError("Invalid public manifest")
    bundle = PublicBundle(tuple(restore_document(item) for item in value["evidence"]),
                          parse_time(value["cutoff"]), tuple(value["gaps"]), value["omitted"],
                          value["compiler_version"])
    if bundle.input_hash != value["input_hash"]:
        raise ValueError("Public manifest semantic hash mismatch")
    return bundle


def compile_bundle(documents, calendar, now, gaps=(), max_bytes=32768):
    """Keep distinct institutions/geographies; receipt-only repolls are not changes."""
    utc_time(now)
    if (type(documents) is not tuple or any(not isinstance(item, SourceDocument) for item in documents)
            or len(documents) > 1000 or type(gaps) is not tuple
            or type(max_bytes) is not int or not 1024 <= max_bytes <= 131072):
        raise ValueError("Invalid public compilation inputs")
    latest = {}
    for item in documents:
        if item.received_at <= now:
            key = (item.source, item.source_url)
            prior = latest.get(key)
            if prior is None or item.received_at > prior.received_at:
                latest[key] = item
    candidates = []
    for item in latest.values():
        if item.published_on is not None:
            age_days = (now.date() - item.published_on).days
            eligible = 0 <= age_days <= DOCUMENT_LOOKBACK_DAYS[item.topic]
        else:
            published = item.published_at or item.received_at
            eligible = timedelta(0) <= now - published <= timedelta(
                days=DOCUMENT_LOOKBACK_DAYS[item.topic])
        if eligible:
            candidates.append(item)
    if calendar is not None:
        if not isinstance(calendar, CalendarSnapshot) or calendar.available_at > now:
            raise ValueError("Calendar evidence is not yet available")
        for event in calendar.events:
            if (event.category not in PUBLIC_TOPICS or event.available_at > now
                    or event.scheduled_at is not None
                    and not now <= event.scheduled_at <= now + timedelta(days=14)):
                continue
            geography = "UK" if event.source in ("ons", "boe_mpc") else "US"
            title = event.title
            content = f"Scheduled event only; status: {event.status}."
            if event.source_notes:
                content += " " + " ".join(event.source_notes)
            candidates.append(SourceDocument(
                f"schedule-{event.source}-{hashlib.sha256(event.event_id.encode()).hexdigest()}",
                event.source, event.source, event.source_url, title, content[:12000],
                event.category, geography, "schedule", None, event.available_at, event.scheduled_at))
    candidates.sort(key=lambda item: (
        item.kind == "schedule", -(item.published_at or item.received_at).timestamp(),
        item.geography, item.topic, item.source, item.evidence_id))
    selected, seen = [], set()
    for item in candidates:
        key = (item.publisher_family, item.source_url, item.title, item.text, item.scheduled_at)
        if key in seen:
            continue
        seen.add(key)
        if len(selected) >= 40:
            continue
        trial = selected + [item]
        payload = {"evidence": [record.wire() for record in trial], "gaps": list(gaps)}
        if len(canonical(payload).encode("utf-8")) <= max_bytes:
            selected.append(item)
    if not selected:
        return None
    selected.sort(key=lambda item: (item.geography, item.topic, item.source, item.evidence_id))
    return PublicBundle(tuple(selected), now, gaps, len(candidates) - len(selected))
