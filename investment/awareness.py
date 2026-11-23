"""Read-only local matching. Portfolio evidence never enters an AI request."""

from datetime import date, timedelta
import json
import re

from market_intelligence.contracts import CATEGORIES, SYMBOLS, identifier
from market_intelligence.store import IntelligenceStore, StoreError

from . import InvestmentError
from .core import clock, explicit_clock, fields, iso, text
from .store import canonical, digest, load_json, read_with_clock


def _note_clock(value):
    if not isinstance(value, str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)", value):
        raise InvestmentError("Note clocks must be UTC timestamps with at most six fractional digits")
    return explicit_clock(value)


def validate_profile(value, ledger, database_id, now):
    required = ("schema_version", "portfolio_id", "intelligence_database_id", "observed_at",
                "calendar_window_days", "max_source_age_seconds", "exposures", "reviews")
    optional = tuple(key for key in ("theses", "calendar_watch")
                     if isinstance(value, dict) and key in value)
    fields(value, (*required, *optional))
    if (type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["portfolio_id"] != ledger.spec["portfolio_id"]
            or value["intelligence_database_id"] != database_id):
        raise InvestmentError("Awareness profile does not bind these stores")
    if len(canonical(value).encode("utf-8")) > 65536 or not isinstance(value["observed_at"], str):
        raise InvestmentError("Awareness profile is oversized or not yet available")
    profile_at = _note_clock(value["observed_at"]) if optional else explicit_clock(value["observed_at"])
    if profile_at > now:
        raise InvestmentError("Awareness profile is oversized or not yet available")
    try:
        json.dumps(value, ensure_ascii=False).encode("utf-8")
    except UnicodeEncodeError as exc:
        raise InvestmentError("Awareness profile contains invalid Unicode") from exc
    for key, lower, upper in (("calendar_window_days", 1, 30),
                              ("max_source_age_seconds", 60, 86400)):
        if type(value[key]) is not int or not lower <= value[key] <= upper:
            raise InvestmentError("Invalid awareness horizon or source age")
    accounts = {item["id"]: item for item in ledger.spec["accounts"]}
    products = {item["id"] for item in ledger.spec["products"]}

    def valid_association(account, product):
        return (account in accounts and (product is None or
                product in products and accounts[account]["kind"] == "broker"))

    if type(value["exposures"]) is not list or len(value["exposures"]) > 100:
        raise InvestmentError("Awareness exposures must be a bounded list")
    seen = set()
    for item in value["exposures"]:
        fields(item, ("account_id", "product_id", "calendar", "research_symbols", "evidence"))
        text(item["account_id"])
        if item["product_id"] is not None:
            text(item["product_id"])
        key = (item["account_id"], item["product_id"])
        if key in seen or not valid_association(*key):
            raise InvestmentError("Unknown or duplicate awareness exposure")
        seen.add(key)
        text(item["evidence"])
        if (type(item["calendar"]) is not list or len(item["calendar"]) > 20
                or type(item["research_symbols"]) is not list
                or len(item["research_symbols"]) > len(SYMBOLS)
                or any(not isinstance(symbol, str) or symbol not in SYMBOLS
                       for symbol in item["research_symbols"])
                or len(set(item["research_symbols"])) != len(item["research_symbols"])):
            raise InvestmentError("Invalid public awareness mapping")
        pairs = set()
        for match in item["calendar"]:
            fields(match, ("source", "category"))
            identifier(match["source"])
            if not isinstance(match["category"], str) or match["category"] not in CATEGORIES:
                raise InvestmentError("Unsupported awareness category")
            pair = (match["source"], match["category"])
            if pair in pairs:
                raise InvestmentError("Duplicate calendar mapping")
            pairs.add(pair)
    if type(value["reviews"]) is not list or len(value["reviews"]) > 20:
        raise InvestmentError("Review reminders must be a bounded list")
    seen = set()
    for item in value["reviews"]:
        fields(item, ("id", "title", "last_reviewed_at", "interval_days", "evidence"))
        for key in ("id", "title", "evidence"):
            text(item[key])
        if item["id"] in seen:
            raise InvestmentError("Duplicate review reminder")
        seen.add(item["id"])
        if (type(item["interval_days"]) is not int or not 1 <= item["interval_days"] <= 3660
                or item["last_reviewed_at"] is not None and
                explicit_clock(item["last_reviewed_at"]) > profile_at):
            raise InvestmentError("Invalid reminder interval or review evidence clock")
    if "theses" in value:
        theses = value["theses"]
        if type(theses) is not list or len(theses) > 20:
            raise InvestmentError("Theses must be a bounded list")
        seen = set()
        for item in theses:
            fields(item, ("id", "title", "account_id", "product_id", "history"))
            for key in ("id", "title", "account_id"):
                text(item[key])
            if item["product_id"] is not None:
                text(item["product_id"])
            if item["id"] in seen or not valid_association(item["account_id"], item["product_id"]):
                raise InvestmentError("Unknown thesis association or duplicate thesis ID")
            seen.add(item["id"])
            history = item["history"]
            if type(history) is not list or not 1 <= len(history) <= 50:
                raise InvestmentError("Thesis history must be a bounded nonempty list")
            previous = None
            for entry in history:
                fields(entry, ("observed_at", "status", "thesis", "invalidation", "evidence"))
                for key in ("thesis", "invalidation", "evidence"):
                    text(entry[key])
                if entry["status"] not in ("watch", "active", "invalidated", "closed"):
                    raise InvestmentError("Invalid thesis clock or status")
                observed = _note_clock(entry["observed_at"])
                if observed > profile_at or previous is not None and observed <= previous:
                    raise InvestmentError("Thesis history must be ordered and available by the profile clock")
                previous = observed
    if "calendar_watch" in value:
        watch = value["calendar_watch"]
        if type(watch) is not list or len(watch) > 100:
            raise InvestmentError("Calendar watch must be a bounded list")
        seen = set()
        for item in watch:
            fields(item, ("id", "title", "account_id", "product_id", "scheduled_on",
                          "time_note", "status", "observed_at", "source"))
            for key in ("id", "title", "account_id", "time_note", "source"):
                text(item[key])
            if item["product_id"] is not None:
                text(item["product_id"])
            if item["id"] in seen or not valid_association(item["account_id"], item["product_id"]):
                raise InvestmentError("Unknown calendar watch association or duplicate ID")
            seen.add(item["id"])
            if item["scheduled_on"] is not None:
                if not isinstance(item["scheduled_on"], str):
                    raise InvestmentError("Invalid calendar watch date")
                try:
                    scheduled = date.fromisoformat(item["scheduled_on"])
                except ValueError as exc:
                    raise InvestmentError("Invalid calendar watch date") from exc
                if scheduled.isoformat() != item["scheduled_on"]:
                    raise InvestmentError("Calendar watch date must be ISO formatted")
            if (item["status"] not in ("scheduled", "tentative", "cancelled")
                    or _note_clock(item["observed_at"]) > profile_at):
                raise InvestmentError("Invalid calendar watch status or observation clock")
    return value


def project_awareness(ledger, profile, evidence, now, version=1):
    if type(version) is not int or version not in (1, 2):
        raise InvestmentError("Unsupported awareness report version")
    now = clock(now)
    profile = validate_profile(profile, ledger, evidence["database_id"], now)
    if evidence["settings_at"] > now:
        raise InvestmentError("Intelligence settings are not yet available")
    settings = evidence["settings"]
    horizon = now + timedelta(days=profile["calendar_window_days"])
    maximum_age = timedelta(seconds=profile["max_source_age_seconds"])
    warnings = [
        "Local declared relevance is not verified fund look-through or complete exposure coverage.",
        "Calendar coverage is limited to configured sources; an empty result is not an all-clear.",
        "Briefs are unverified public research context, not recommendations to trade or policy approval.",
    ]
    active = {(account, None) for account, amount in ledger.cash.items() if amount > 0}
    active.update(key for key, quantity in ledger.holdings.items() if quantity > 0)
    mappings = {}
    for item in profile["exposures"]:
        key = (item["account_id"], item["product_id"])
        if key in active:
            mappings[key] = item
    for account, product in sorted(active - mappings.keys(), key=lambda key: (key[0], key[1] or "")):
        warnings.append(f"Unmapped recorded exposure: {account} / {product or 'cash'}.")

    def target(key):
        account, product = key
        if ledger.spec["schema_version"] == 2 and ledger.role_active:
            balances = ledger.role_cash if product is None else ledger.role_holdings
            roles = [role for role in ("core", "dynamic", "unassigned")
                     if balances.get((account, role) if product is None else (*key, role), 0) > 0]
        else:
            roles = ["unassigned"]
        return {"account_id": account, "product_id": product, "roles": roles,
                "mapping_evidence": mappings[key]["evidence"]}

    alerts = []
    snapshot = evidence["calendar"]
    calendar_status = "disabled" if not settings.enabled or not settings.calendar_enabled else "unavailable"
    if calendar_status != "disabled" and snapshot is not None and snapshot.available_at <= now:
        calendar_status = "available"
        sources = {source.source: source for source in snapshot.sources}
        required = {(match["source"], match["category"]) for item in mappings.values()
                    for match in item["calendar"]}

        def healthy(source, category, start, end):
            return bool(source and source.status == "healthy" and category in source.categories
                        and source.checked_at is not None and timedelta(0) <= now - source.checked_at <= maximum_age
                        and source.coverage_start is not None and source.coverage_start <= start
                        and source.coverage_end >= end)

        for source, category in sorted(required):
            if not healthy(sources.get(source), category, now, horizon):
                warnings.append(f"Unknown or incomplete calendar coverage: {source} / {category}.")
        for event in snapshot.events:
            if ((event.source, event.category) not in required or event.available_at > now
                    or event.scheduled_at is not None and not now <= event.scheduled_at < horizon):
                continue
            matches = [target(key) for key, item in mappings.items()
                       if {"source": event.source, "category": event.category} in item["calendar"]]
            fresh = event.scheduled_at is not None and healthy(
                sources.get(event.source), event.category, event.scheduled_at,
                event.scheduled_at + timedelta(microseconds=1))
            alerts.append({
                "event_id": event.event_id, "revision": event.revision, "source": event.source,
                "source_url": event.source_url, "category": event.category, "title": event.title,
                "scheduled_at": iso(event.scheduled_at) if event.scheduled_at else None,
                "available_at": iso(event.available_at), "status": event.status,
                "evidence_status": "fresh" if fresh else "unknown", "matches": matches,
            })
        alerts.sort(key=lambda item: (item["scheduled_at"] or "", item["source"], item["event_id"]))
        if len(alerts) > 100:
            warnings.append("Calendar alerts truncated to the first 100 matching records.")
            alerts = alerts[:100]
    elif calendar_status == "unavailable":
        warnings.append("Calendar evidence is missing or not yet available.")

    briefs = []
    for brief, citations, attempt_id in evidence["briefs"]:
        if brief.available_at > now or any(item.received_at > now for item in citations):
            warnings.append("A future-dated research brief was withheld.")
            continue
        matches = [target(key) for key, item in mappings.items()
                   if set(brief.symbols) & set(item["research_symbols"])]
        if not matches:
            continue
        news = evidence["news_status"]
        if now >= brief.expires_at:
            status = "expired"
        elif not settings.enabled or not settings.news_enabled or not settings.ai_enabled:
            status = "disabled"
        elif brief.settings_revision != settings.revision or brief.provider != settings.ai_profile:
            status = "superseded"
        elif (news is None or news.status != "successful" or news.attempt_id != attempt_id
              or news.settings_revision != settings.revision
              or not timedelta(0) <= now - news.available_at <= timedelta(seconds=900)):
            status = "source_unknown"
        else:
            status = "current"
        briefs.append({
            "brief_id": brief.brief_id, "available_at": iso(brief.available_at),
            "expires_at": iso(brief.expires_at), "status": status, "verification": brief.verification,
            "provider": brief.provider, "model": brief.model, "prompt_version": brief.prompt_version,
            "input_hash": brief.input_hash, "interpretation": brief.interpretation,
            "uncertainties": list(brief.uncertainties), "recommendation": brief.recommendation,
            "symbols": list(brief.symbols), "matches": matches,
            "claims": [{"text": claim.text, "evidence_ids": list(claim.evidence_ids)} for claim in brief.claims],
            "sources": [{"evidence_id": item.evidence_id, "source_url": item.source_url,
                         "title": item.title, "published_at": iso(item.published_at) if item.published_at else None,
                         "received_at": iso(item.received_at)} for item in citations],
        })
    reminders = []
    for item in profile["reviews"]:
        reviewed = clock(item["last_reviewed_at"]) if item["last_reviewed_at"] else None
        due = reviewed + timedelta(days=item["interval_days"]) if reviewed else None
        reminders.append({
            **item, "last_reviewed_at": iso(reviewed) if reviewed else None,
            "due_at": iso(due) if due else None,
            "status": "never_reviewed" if due is None else "due" if now >= due else "upcoming",
        })
    result = {
        "enabled": True, "schema_version": version, "source_mode": "recorded",
        "portfolio_id": ledger.spec["portfolio_id"], "portfolio_revision": ledger.revision,
        "intelligence_database_id": evidence["database_id"], "settings_revision": settings.revision,
        "profile_hash": digest(canonical(profile)), "profile_observed_at": iso(clock(profile["observed_at"])),
        "generated_at": iso(now), "calendar_status": calendar_status,
        "calendar_available_at": iso(snapshot.available_at) if snapshot and snapshot.available_at <= now else None,
        "calendar_revision": snapshot.revision if snapshot and snapshot.available_at <= now else None,
        "alerts": alerts, "briefs": briefs, "reminders": reminders, "warnings": warnings,
        "affects_trading": False, "accounting_writes": False,
    }
    if version == 2 and "theses" in profile:
        result["theses"] = [
            {**item, "history": [{**entry, "observed_at": iso(clock(entry["observed_at"]))}
                                 for entry in item["history"]]}
            for item in profile["theses"]
        ]
    if version == 2 and "calendar_watch" in profile:
        result["calendar_watch"] = [
            {**item, "observed_at": iso(clock(item["observed_at"]))}
            for item in profile["calendar_watch"]
        ]
    try:
        json.dumps(result, ensure_ascii=False).encode("utf-8")
    except UnicodeEncodeError as exc:
        raise InvestmentError("Awareness evidence contains invalid Unicode") from exc
    return result


def awareness_snapshot(database, intelligence_database, profile_path, now=None, version=1, *,
                       clock_source=None):
    try:
        ledger, now = read_with_clock(database, now, clock_source=clock_source)
        profile = load_json(profile_path)
        evidence = IntelligenceStore(intelligence_database).portfolio_evidence()
        return project_awareness(ledger, profile, evidence, now, version)
    except (StoreError, ValueError, OSError) as exc:
        raise InvestmentError("Portfolio awareness unavailable: validate the local profile and evidence stores") from exc
