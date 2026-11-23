"""Local-only public-brief relevance; no provider, model or workspace discovery."""

import os
from pathlib import Path
import secrets
import stat

from investment import InvestmentError
from investment.core import explicit_clock, fmt, iso, profile
from investment.store import (
    canonical, digest, parse_json, read_snapshot_with_clock,
)

from .settings import SettingsError, _file_lock
from .workspace import path_identity


TOPICS = ("inflation", "employment", "growth", "monetary_policy")
GEOGRAPHIES = ("UK", "US")
MAX_MAPPING_BYTES = 65536
MAX_MAPPINGS = 100
MAX_REVISION = 2**31 - 1


class RelevanceError(SettingsError):
    def __init__(self, code, status=400):
        super().__init__(code)
        self.code = code
        self.status = status


def _exposures(portfolio):
    accounts = {item["id"]: item for item in portfolio.spec["accounts"]}
    products = {item["id"]: item for item in portfolio.spec["products"]}
    result = []
    for account, amount in sorted(portfolio.cash.items()):
        if amount > 0:
            result.append({"account_id": account, "label": accounts[account]["name"] + " cash",
                           "classification": "cash", "look_through": "unknown"})
    for (account, instrument), quantity in sorted(portfolio.holdings.items()):
        if quantity > 0:
            result.append({"account_id": account, "instrument_id": instrument,
                           "label": products[instrument]["name"],
                           "classification": products[instrument]["sleeve"],
                           "look_through": "unknown"})
    return result


def _key(item):
    return item["account_id"], item.get("instrument_id")


def _validate_mappings(mappings, portfolio):
    if type(mappings) is not list or len(mappings) > MAX_MAPPINGS:
        raise RelevanceError("relevance_invalid_mappings")
    accounts = {item["id"]: item for item in portfolio.spec["accounts"]}
    products = {item["id"] for item in portfolio.spec["products"]}
    seen = set()
    for item in mappings:
        if (type(item) is not dict
                or set(item) not in ({"account_id", "topics", "geographies"},
                                     {"account_id", "instrument_id", "topics", "geographies"})
                or type(item["account_id"]) is not str
                or item["account_id"] not in accounts):
            raise RelevanceError("relevance_invalid_mappings")
        if "instrument_id" in item and (
                type(item["instrument_id"]) is not str
                or item["instrument_id"] not in products
                or accounts[item["account_id"]]["kind"] != "broker"):
            raise RelevanceError("relevance_invalid_mappings")
        if _key(item) in seen:
            raise RelevanceError("relevance_duplicate_mapping")
        seen.add(_key(item))
        for field, allowed in (("topics", TOPICS), ("geographies", GEOGRAPHIES)):
            tags = item[field]
            if (type(tags) is not list or not 1 <= len(tags) <= len(allowed)
                    or any(type(tag) is not str or tag not in allowed for tag in tags)
                    or len(set(tags)) != len(tags)):
                raise RelevanceError("relevance_invalid_tags")
    if len(canonical(mappings).encode("utf-8")) > MAX_MAPPING_BYTES // 2:
        raise RelevanceError("relevance_invalid_mappings")
    return mappings


def project_relevance(portfolio, briefing, mappings, *, journal_revision=None,
                      mapping_revision=0):
    """Pure projection of replayed evidence, a public briefing and declared tags.

    Instrument IDs are journal product IDs. An account-only mapping means that
    account's cash, never an implicit mapping of every holding in that account.
    """
    mappings = _validate_mappings(mappings, portfolio)
    declared = {_key(item): item for item in mappings}
    exposures = _exposures(portfolio)
    if briefing is not None and (
            type(briefing) is not dict or type(briefing.get("briefing_id")) is not str
            or not 1 <= len(briefing["briefing_id"]) <= 256):
        raise RelevanceError("relevance_invalid_briefing")
    result = {
        "state": "no_briefing" if briefing is None else "available",
        "journal_revision": journal_revision,
        "mapping_revision": mapping_revision,
        "briefing_id": None if briefing is None else briefing["briefing_id"],
        "matches": [], "unmapped": [],
        "valuation": {"state": "incomplete", "observed_at": None},
        "scope": "UK/US macro context only; no currency, issuer or name inference.",
        "look_through": "unknown",
        "editor": {"exposures": exposures, "mappings": mappings,
                   "topics": list(TOPICS), "geographies": list(GEOGRAPHIES)},
    }
    total, observed = portfolio.valuation()
    if total is not None and observed is not None:
        result["valuation"] = {"state": "complete", "observed_at": iso(observed),
                               "total_gbp": fmt(total)}
    for exposure in exposures:
        target = {key: exposure[key] for key in ("account_id", "instrument_id") if key in exposure}
        if _key(exposure) not in declared:
            result["unmapped"].append({
                **target, "reason": "No declared topic/geography mapping; look-through is unknown.",
            })
    if briefing is None:
        return result
    developments = briefing.get("output", {}).get("developments", [])
    if type(developments) is not list or len(developments) > 100:
        raise RelevanceError("relevance_invalid_briefing")
    for index, development in enumerate(developments):
        if type(development) is not dict:
            raise RelevanceError("relevance_invalid_briefing")
        topic, geography = development.get("topic"), development.get("geography")
        if topic not in TOPICS or geography not in GEOGRAPHIES:
            continue
        for exposure in exposures:
            mapping = declared.get(_key(exposure))
            reasons = []
            if mapping and topic in mapping["topics"] and geography in mapping["geographies"]:
                kind = "declared"
                reasons.append(f"Locally declared {geography} / {topic} context; not verified look-through.")
            else:
                classification = exposure["classification"]
                topics = (("inflation", "monetary_policy") if classification == "cash"
                          else TOPICS if classification in ("equity", "bond") else ())
                if topic not in topics:
                    continue
                kind = "classified"
                reasons.append(
                    f"Recorded {classification} class has a possible {topic} macro channel; "
                    f"{geography} is public context, not inferred geographic exposure.")
            target = {key: exposure[key] for key in ("account_id", "instrument_id") if key in exposure}
            result["matches"].append({
                "development_index": index, **target, "reasons": reasons, "match_kind": kind,
                "review_question": (
                    "Does this public development change the evidence for your recorded exposure "
                    "or its next review? Check the source and unknown look-through."),
            })
    return result


def _bounded_json(path):
    path_identity(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_MAPPING_BYTES:
        raise RelevanceError("relevance_file_unavailable", 503)
    with path.open("rb") as handle:
        raw = handle.read(MAX_MAPPING_BYTES + 1)
    if len(raw) > MAX_MAPPING_BYTES:
        raise RelevanceError("relevance_file_unavailable", 503)
    return parse_json(raw.decode("utf-8"))


class RelevanceWorkspace:
    """Explicit local paths only. Reads never create even a lock/config file."""

    def __init__(self, *, journal_path, profile_path, mapping_path, clock_source=None):
        self.journal_path = Path(os.path.abspath(journal_path))
        self.profile_path = Path(os.path.abspath(profile_path))
        self.mapping_path = Path(os.path.abspath(mapping_path))
        protected = {self.journal_path, self.profile_path}
        protected.update(path.with_name(path.name + ".lock") for path in tuple(protected))
        if {self.mapping_path, self.mapping_path.with_name(
                self.mapping_path.name + ".lock")} & protected:
            raise RelevanceError("relevance_invalid_path")
        self.clock_source = clock_source

    def _portfolio(self):
        path_identity(self.journal_path)
        spec = profile(_bounded_json(self.profile_path))
        portfolio, now, head = read_snapshot_with_clock(
            self.journal_path, clock_source=self.clock_source)
        if any(spec[key] != portfolio.spec[key]
               for key in ("portfolio_id", "created_at", "currency", "schema_version")):
            raise RelevanceError("relevance_profile_binding", 409)
        # Profile amendments may change names/scope; immutable original fields
        # still have to bind the explicitly selected profile to this journal.
        for field, immutable in (("accounts", ("kind", "binding", "currency")),
                                 ("products", ("sleeve", "currency", "quantity_increment",
                                               "minimum_notional_gbp"))):
            current = {item["id"]: item for item in portfolio.spec[field]}
            if any(item["id"] not in current or any(
                    item[key] != current[item["id"]][key] for key in immutable)
                   for item in spec[field]):
                raise RelevanceError("relevance_profile_binding", 409)
        info = self.journal_path.stat()
        binding = {
            "portfolio_id": spec["portfolio_id"],
            "profile_hash": digest(canonical(spec)),
            "journal_path_hash": digest(str(self.journal_path.resolve())),
            "journal_file_id": [info.st_dev, info.st_ino],
        }
        return portfolio, now, head, binding

    def _mappings(self, portfolio, binding):
        try:
            value = _bounded_json(self.mapping_path)
        except FileNotFoundError:
            return {"schema_version": 1, "revision": 0, "binding": binding, "mappings": []}
        if (type(value) is not dict
                or set(value) != {"schema_version", "revision", "binding", "mappings"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 1
                or type(value["revision"]) is not int
                or not 1 <= value["revision"] <= MAX_REVISION
                or value["binding"] != binding):
            raise RelevanceError("relevance_mapping_binding", 409)
        _validate_mappings(value["mappings"], portfolio)
        return value

    @staticmethod
    def _suppressed(state):
        return {"state": state, "journal_revision": None, "mapping_revision": None,
                "briefing_id": None, "matches": [], "unmapped": [],
                "valuation": {"state": "incomplete", "observed_at": None}}

    def snapshot(self, briefing=None):
        try:
            portfolio, now, head, binding = self._portfolio()
            mappings = self._mappings(portfolio, binding)
            if briefing is not None and (
                    explicit_clock(briefing["generated_at"]) > now
                    or explicit_clock(briefing["manifest"]["cutoff"]) > now):
                return self._suppressed("unavailable")
            result = project_relevance(
                portfolio, briefing, mappings["mappings"], journal_revision=head,
                mapping_revision=mappings["revision"])
            _, _, after, after_binding = self._portfolio()
            latest = self._mappings(portfolio, binding)
            if (after != head or after_binding != binding
                    or latest != mappings):
                return self._suppressed("changed")
            return result
        except (InvestmentError, RelevanceError, OSError, ValueError, TypeError, KeyError):
            return self._suppressed("unavailable")

    def save_mappings(self, payload):
        if (type(payload) is not dict
                or set(payload) != {"expected_revision", "expected_journal_revision", "mappings"}
                or type(payload["expected_revision"]) is not int
                or not 0 <= payload["expected_revision"] < MAX_REVISION
                or type(payload["expected_journal_revision"]) is not str):
            raise RelevanceError("relevance_invalid_save")
        committed = None
        try:
            with _file_lock(self.mapping_path):
                portfolio, _, head, binding = self._portfolio()
                current = self._mappings(portfolio, binding)
                if (head != payload["expected_journal_revision"]
                        or current["revision"] != payload["expected_revision"]):
                    raise RelevanceError("relevance_changed", 409)
                mappings = parse_json(canonical(_validate_mappings(payload["mappings"], portfolio)))
                value = {"schema_version": 1, "revision": current["revision"] + 1,
                         "binding": binding, "mappings": mappings}
                _, _, after, after_binding = self._portfolio()
                if after != head or after_binding != binding:
                    raise RelevanceError("relevance_changed", 409)
                self._write(value)
                # The config is committed, even if a concurrent journal update
                # makes its subsequent projection unavailable.
                committed = {"state": "saved", "mapping_revision": value["revision"],
                             "journal_revision": head}
            return committed
        except RelevanceError:
            raise
        except (InvestmentError, SettingsError, OSError, ValueError, TypeError, KeyError) as exc:
            if committed is not None:
                raise RelevanceError("relevance_saved_status_unavailable", 503) from exc
            raise RelevanceError("relevance_save_failed", 503) from exc

    def _write(self, value):
        path_identity(self.mapping_path)
        temporary = self.mapping_path.with_name(
            self.mapping_path.name + "." + secrets.token_hex(16) + ".tmp")
        try:
            descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(canonical(value).encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
            path_identity(self.mapping_path)
            os.replace(temporary, self.mapping_path)
        finally:
            temporary.unlink(missing_ok=True)
