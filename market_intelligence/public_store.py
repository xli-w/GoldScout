"""Append-only public acquisitions and fenced, durable synthesis operations."""

from datetime import timedelta
import hashlib
import json
import uuid

from market_intelligence import store as storage
from market_intelligence.analysis import AnalysisLimits, strict_json_loads
from market_intelligence.compilation import (
    PublicBundle, canonical, compile_bundle, restore_bundle, restore_document, timestamp,
)
from market_intelligence.contracts import identifier, utc_time


def public_storage_schema():
    definitions = {
        ("table", "public_payloads"): "CREATE TABLE public_payloads(input_hash TEXT PRIMARY KEY CHECK(length(input_hash)=64), body BLOB NOT NULL CHECK(typeof(body)='blob' AND length(body)>0 AND length(body)<=1048576))",
        ("table", "public_acquisitions"): "CREATE TABLE public_acquisitions(sequence INTEGER PRIMARY KEY, acquisition_id TEXT UNIQUE NOT NULL, source TEXT NOT NULL, settings_revision INTEGER NOT NULL REFERENCES settings_history(revision), received_at TEXT NOT NULL, input_hash TEXT NOT NULL REFERENCES public_payloads(input_hash), payload TEXT NOT NULL, payload_hash TEXT NOT NULL)",
        ("table", "public_documents"): "CREATE TABLE public_documents(evidence_id TEXT PRIMARY KEY, first_acquisition_id TEXT NOT NULL REFERENCES public_acquisitions(acquisition_id), payload TEXT NOT NULL, payload_hash TEXT NOT NULL)",
        ("table", "public_attempts"): "CREATE TABLE public_attempts(sequence INTEGER PRIMARY KEY, source TEXT NOT NULL, observed_at TEXT NOT NULL, settings_revision INTEGER NOT NULL REFERENCES settings_history(revision), acquisition_id TEXT REFERENCES public_acquisitions(acquisition_id), error_code TEXT, CHECK((acquisition_id IS NULL AND error_code IS NOT NULL) OR (acquisition_id IS NOT NULL AND error_code IS NULL)))",
        ("table", "public_owners"): "CREATE TABLE public_owners(sequence INTEGER PRIMARY KEY, owner_id TEXT UNIQUE NOT NULL, observed_at TEXT NOT NULL)",
        ("table", "public_operations"): "CREATE TABLE public_operations(sequence INTEGER PRIMARY KEY, operation_id TEXT UNIQUE NOT NULL, settings_revision INTEGER NOT NULL REFERENCES settings_history(revision), payload TEXT NOT NULL, payload_hash TEXT NOT NULL)",
        ("table", "public_operation_events"): "CREATE TABLE public_operation_events(sequence INTEGER PRIMARY KEY, operation_id TEXT NOT NULL REFERENCES public_operations(operation_id), owner_id TEXT NOT NULL REFERENCES public_owners(owner_id), state TEXT NOT NULL CHECK(state IN ('queued','reserved','dispatching','outcome_uncertain','validated','committed','failed')), observed_at TEXT NOT NULL, payload TEXT NOT NULL, payload_hash TEXT NOT NULL)",
        ("table", "public_briefings"): "CREATE TABLE public_briefings(sequence INTEGER PRIMARY KEY, briefing_id TEXT UNIQUE NOT NULL, operation_id TEXT UNIQUE NOT NULL REFERENCES public_operations(operation_id), payload TEXT NOT NULL, payload_hash TEXT NOT NULL)",
        ("table", "public_selections"): "CREATE TABLE public_selections(sequence INTEGER PRIMARY KEY, briefing_id TEXT NOT NULL REFERENCES public_briefings(briefing_id), settings_revision INTEGER NOT NULL REFERENCES settings_history(revision), owner_id TEXT NOT NULL REFERENCES public_owners(owner_id), selected_at TEXT NOT NULL)",
        ("table", "public_configurations"): "CREATE TABLE public_configurations(settings_revision INTEGER PRIMARY KEY REFERENCES settings_history(revision), owner_id TEXT NOT NULL REFERENCES public_owners(owner_id), observed_at TEXT NOT NULL, payload TEXT NOT NULL, payload_hash TEXT NOT NULL)",
        ("table", "public_configuration_requests"): "CREATE TABLE public_configuration_requests(request_id TEXT PRIMARY KEY REFERENCES commands(request_id), payload TEXT NOT NULL, payload_hash TEXT NOT NULL)",
        ("table", "public_requests"): "CREATE TABLE public_requests(sequence INTEGER PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, action TEXT NOT NULL CHECK(action IN ('sources','briefing')), requested_at TEXT NOT NULL)",
        ("table", "public_receipts"): "CREATE TABLE public_receipts(sequence INTEGER PRIMARY KEY, request_id TEXT UNIQUE NOT NULL REFERENCES public_requests(request_id), payload TEXT NOT NULL, payload_hash TEXT NOT NULL)",
        ("table", "public_calendar_events"): "CREATE TABLE public_calendar_events(sequence INTEGER PRIMARY KEY, source TEXT NOT NULL, event_id TEXT NOT NULL, revision INTEGER NOT NULL, calendar_revision INTEGER NOT NULL REFERENCES calendar_history(revision), payload TEXT NOT NULL, payload_hash TEXT NOT NULL, UNIQUE(source,event_id,revision))",
        ("index", "public_source_sequence"): "CREATE INDEX public_source_sequence ON public_acquisitions(source,sequence)",
        ("index", "public_operation_sequence"): "CREATE INDEX public_operation_sequence ON public_operation_events(operation_id,sequence)",
        ("index", "public_calendar_identity"): "CREATE INDEX public_calendar_identity ON public_calendar_events(source,event_id,revision)",
    }
    keys = {"public_payloads": "input_hash", "public_documents": "evidence_id", "public_acquisitions": "acquisition_id",
            "public_owners": "owner_id", "public_operations": "operation_id",
            "public_briefings": "briefing_id", "public_requests": "request_id",
            "public_receipts": "request_id", "public_configurations": "settings_revision",
            "public_configuration_requests": "request_id"}
    for table in (name for kind, name in tuple(definitions) if kind == "table"):
        for action in ("UPDATE", "DELETE"):
            name = f"immutable_{table}_{action.lower()}"
            definitions[("trigger", name)] = (
                f"CREATE TRIGGER {name} BEFORE {action} ON {table} "
                "BEGIN SELECT RAISE(ABORT, 'Public evidence is append-only'); END")
        key = keys.get(table, "sequence")
        condition = f"{key}=NEW.{key}"
        if key != "sequence" and table not in (
                "public_payloads", "public_documents", "public_configurations", "public_configuration_requests"):
            condition += " OR sequence=NEW.sequence"
        name = f"immutable_{table}_replace"
        definitions[("trigger", name)] = (
            f"CREATE TRIGGER {name} BEFORE INSERT ON {table} "
            f"WHEN EXISTS(SELECT 1 FROM {table} WHERE {condition}) "
            "BEGIN SELECT RAISE(ABORT, 'Public evidence is append-only'); END")
    return definitions


def require_schema(handle):
    if handle.execute("PRAGMA user_version").fetchone()[0] < 7:
        raise storage.StoreError("upgrade_required")


def read_payload(row):
    if (row is None or not isinstance(row["payload"], str)
            or len(row["payload"].encode("utf-8")) > storage.MAX_PAYLOAD
            or storage.digest(row["payload"]) != row["payload_hash"]):
        raise storage.StoreError("storage_invalid")
    try:
        return strict_json_loads(row["payload"])
    except (ValueError, TypeError, RecursionError) as exc:
        raise storage.StoreError("storage_invalid") from exc


def write_payload(value):
    payload = canonical(value)
    if len(payload.encode("utf-8")) > storage.MAX_PAYLOAD:
        raise ValueError("Public evidence exceeds storage bound")
    return payload, storage.digest(payload)


def payload_columns():
    return (storage.bounded_column("payload", storage.MAX_PAYLOAD) + ","
            + storage.bounded_column("payload_hash", 64))


def require_owner(handle, owner_id):
    identifier(owner_id)
    row = handle.execute("SELECT owner_id FROM public_owners ORDER BY sequence DESC LIMIT 1").fetchone()
    if row is None or row[0] != owner_id:
        raise storage.StoreError("owner_superseded")


def operation_record(handle, operation_id):
    row = handle.execute(f"SELECT settings_revision,{payload_columns()} FROM public_operations "
                         "WHERE operation_id=?", (operation_id,)).fetchone()
    if row is None:
        return None
    metadata = read_payload(row)
    try:
        identity = {key: metadata[key] for key in ("input_hash", "provider", "provider_kind", "model", "prompt_version", "schema_version")}
        bundle = restore_bundle(metadata["manifest"])
        if (set(metadata) != {*identity, "manifest", "acquisition_ids", "settings_revision"}
                or metadata["settings_revision"] != row["settings_revision"]
                or bundle.input_hash != metadata["input_hash"]
                or operation_id != "synthesis-" + storage.digest(canonical(identity))):
            raise ValueError("Invalid synthesis provenance")
    except (ValueError, KeyError, TypeError) as exc:
        raise storage.StoreError("storage_invalid") from exc
    events = handle.execute(f"SELECT owner_id,state,observed_at,{payload_columns()} "
                            "FROM public_operation_events WHERE operation_id=? ORDER BY sequence LIMIT 6",
                            (operation_id,)).fetchall()
    try:
        transitions = {
            "queued": {"reserved", "failed"}, "reserved": {"dispatching", "failed"},
            "dispatching": {"validated", "failed", "outcome_uncertain"},
            "validated": {"committed", "failed"},
            "committed": set(), "failed": set(), "outcome_uncertain": set(),
        }
        if not events or events[0]["state"] != "queued" or len(events) > 5:
            raise ValueError("Invalid synthesis transition history")
        previous_state, previous_time, bound_reservation = None, bundle.cutoff, None
        for event in events:
            name, state = event["state"], read_payload(event)
            if previous_state is not None and name not in transitions[previous_state]:
                raise ValueError("Invalid synthesis transition history")
            expected = {"error_code"} if name == "queued" else {"error_code", "reservation_id"}
            if name == "validated":
                expected.add("output")
            if type(state) is not dict or set(state) != expected:
                raise ValueError("Invalid synthesis state payload")
            identifier(event["owner_id"])
            observed_at = storage.parse_time(event["observed_at"])
            if observed_at < previous_time:
                raise ValueError("Synthesis chronology moved backwards")
            if state["error_code"] is not None:
                identifier(state["error_code"])
            reservation_id = state.get("reservation_id")
            if name not in ("queued", "failed") and reservation_id is None:
                raise ValueError("Synthesis requires a durable reservation")
            if bound_reservation is not None and reservation_id != bound_reservation:
                raise ValueError("Synthesis changed its reservation")
            if reservation_id is not None:
                identifier(reservation_id)
                reservation = handle.execute(
                    f"SELECT accepted,observed_at,{payload_columns()} FROM analysis_reservations WHERE reservation_id=?",
                    (reservation_id,)).fetchone()
                if reservation is None or read_payload(reservation) != {"operation_id": operation_id, **metadata}:
                    raise ValueError("Invalid synthesis reservation binding")
                reserved_at = storage.parse_time(reservation["observed_at"])
                if (reserved_at > observed_at
                        or bound_reservation is None and reserved_at < previous_time
                        or name != "failed" and reservation["accepted"] != 1):
                    raise ValueError("Invalid synthesis reservation chronology or allowance")
                bound_reservation = reservation_id
            if name == "validated":
                from market_intelligence.briefing import validate_output
                validate_output(state["output"], metadata["manifest"])
            previous_state, previous_time = name, observed_at
    except (ValueError, KeyError, TypeError) as exc:
        raise storage.StoreError("storage_invalid") from exc
    latest = events[-1]
    return {"operation_id": operation_id, "metadata": metadata, "state": latest["state"],
            "observed_at": latest["observed_at"], "owner_id": latest["owner_id"], **state}


def append_event(handle, operation_id, owner_id, state, now, details):
    payload, checksum = write_payload(details)
    handle.execute("INSERT INTO public_operation_events VALUES(NULL,?,?,?,?,?,?)",
                   (operation_id, owner_id, state, timestamp(now), payload, checksum))


def require_settings(store, handle, revision, profile=None):
    settings, at = store._settings(handle)
    if (settings.revision != revision or not settings.enabled or not settings.news_enabled
            or profile is not None and (not settings.ai_enabled or settings.ai_profile != profile)):
        raise storage.StoreError("settings_changed")
    return settings, at


def requested_configuration(handle, settings_revision):
    row = handle.execute(
        "SELECT config.payload,config.payload_hash FROM public_configuration_requests config "
        "JOIN commands ON commands.request_id=config.request_id WHERE commands.applied_revision=?",
        (settings_revision,)).fetchone()
    return read_payload(row) if row is not None else None


def selection_payload(selection, settings):
    from market_intelligence.content import CONTENT_SOURCES
    from market_intelligence.providers import CALENDAR_SOURCES
    from market_intelligence.runtime import ProviderProfile
    if type(selection) is not dict or set(selection) != {"calendar_sources", "content_sources", "profile"}:
        raise ValueError("Invalid public configuration selection")
    for field, registry in (("calendar_sources", CALENDAR_SOURCES), ("content_sources", CONTENT_SOURCES)):
        names = selection[field]
        if (type(names) is not list or len(names) > len(registry)
                or any(not isinstance(name, str) or name not in registry for name in names)
                or len(names) != len(set(names))):
            raise ValueError("Invalid configured public sources")
    if not selection["calendar_sources"] or any(
            not CONTENT_SOURCES[name].supported for name in selection["content_sources"]):
        raise ValueError("Unsupported configured sources")
    profile = ProviderProfile(**selection["profile"]) if selection["profile"] is not None else None
    if (settings.calendar_enabled != bool(selection["calendar_sources"])
            or settings.news_enabled != bool(selection["content_sources"])
            or settings.ai_enabled != (profile is not None)
            or settings.ai_profile != (profile.name if profile is not None else None)):
        raise ValueError("Settings and source/model selection disagree")
    payload, checksum = write_payload(selection)
    if len(payload.encode("utf-8")) > 16384:
        raise ValueError("Public selection exceeds bound")
    return payload, checksum


class PublicStoreMethods:
    def bind_public_configuration(self, settings_revision, owner_id, configuration, now):
        utc_time(now)
        payload, checksum = write_payload(configuration)
        if len(payload.encode("utf-8")) > 16384:
            raise ValueError("Public configuration exceeds bound")
        with storage.connection(self.path, writable=True, expected_id=self.database_id) as handle:
            require_schema(handle)
            require_owner(handle, owner_id)
            settings, at = self._settings(handle)
            if settings.revision != settings_revision:
                raise storage.StoreError("settings_changed")
            if at > now:
                raise storage.StoreError("clock_invalid")
            requested = requested_configuration(handle, settings_revision)
            if requested is not None and requested != configuration:
                raise storage.StoreError("configuration_conflict")
            existing = handle.execute(f"SELECT {payload_columns()} FROM public_configurations WHERE settings_revision=?",
                                      (settings_revision,)).fetchone()
            if existing is not None:
                if read_payload(existing) != configuration:
                    raise storage.StoreError("configuration_conflict")
                return
            handle.execute("INSERT INTO public_configurations VALUES(?,?,?,?,?)",
                           (settings_revision, owner_id, timestamp(now), payload, checksum))

    def public_configuration(self, settings_revision):
        with storage.connection(self.path, expected_id=self.database_id) as handle:
            if handle.execute("PRAGMA user_version").fetchone()[0] < 7:
                return None
            row = handle.execute(f"SELECT {payload_columns()} FROM public_configurations WHERE settings_revision=?",
                                 (settings_revision,)).fetchone()
            return read_payload(row) if row is not None else requested_configuration(handle, settings_revision)

    def has_managed_configuration(self):
        with storage.connection(self.path, expected_id=self.database_id) as handle:
            if handle.execute("PRAGMA user_version").fetchone()[0] < 7:
                return False
            return bool(handle.execute("SELECT EXISTS(SELECT 1 FROM public_configurations) OR "
                                       "EXISTS(SELECT 1 FROM public_configuration_requests)").fetchone()[0])

    def _record_calendar_baselines(self, handle, snapshot):
        from market_intelligence.contracts import CalendarEvent
        facts = ("source_url", "category", "symbols", "scheduled_at", "title", "status",
                 "source_timezone", "published_at", "source_notes")
        for event in snapshot.events:
            row = handle.execute(
                f"SELECT {payload_columns()} FROM public_calendar_events WHERE source=? AND event_id=? "
                "ORDER BY revision DESC LIMIT 1", (event.source, event.event_id)).fetchone()
            prior = storage.decode(CalendarEvent, row["payload"], row["payload_hash"]) if row else None
            if prior is not None:
                changed = any(getattr(prior, name) != getattr(event, name) for name in facts)
                if event.revision < prior.revision or event.revision == prior.revision and changed:
                    raise storage.StoreError("revision_conflict")
                if event.revision == prior.revision:
                    continue
            payload = storage.encode(event)
            handle.execute("INSERT INTO public_calendar_events VALUES(NULL,?,?,?,?,?,?)",
                           (event.source, event.event_id, event.revision, snapshot.revision,
                            payload, storage.digest(payload)))

    def _seed_calendar_baselines(self, handle):
        from market_intelligence.contracts import CalendarSnapshot
        rows = handle.execute(f"SELECT {payload_columns()} FROM calendar_history ORDER BY revision")
        for row in rows:
            snapshot = storage.decode(CalendarSnapshot, row["payload"], row["payload_hash"])
            self._record_calendar_baselines(handle, snapshot)

    def calendar_event_baselines(self, source):
        from market_intelligence.contracts import CalendarEvent, CalendarSnapshot
        identifier(source)
        with storage.connection(self.path, expected_id=self.database_id) as handle:
            results = {}
            if handle.execute("PRAGMA user_version").fetchone()[0] >= 7:
                rows = handle.execute(
                    f"SELECT a.payload,a.payload_hash FROM public_calendar_events a WHERE a.source=? "
                    "AND a.revision=(SELECT MAX(b.revision) FROM public_calendar_events b "
                    "WHERE b.source=a.source AND b.event_id=a.event_id) LIMIT 1001", (source,)).fetchall()
                if len(rows) > 1000:
                    raise storage.StoreError("calendar_capacity")
                for row in rows:
                    event = storage.decode(CalendarEvent, row["payload"], row["payload_hash"])
                    results[(source, event.event_id)] = event
            else:
                # Legacy stores retain their historical wire shape until explicit upgrade.
                snapshot = self._calendar(handle)
                for event in snapshot.events if snapshot else ():
                    if event.source == source:
                        results[(source, event.event_id)] = event
            return results

    def claim_public_owner(self, owner_id, now):
        identifier(owner_id)
        utc_time(now)
        with storage.connection(self.path, writable=True, expected_id=self.database_id) as handle:
            require_schema(handle)
            last = handle.execute("SELECT observed_at FROM public_owners ORDER BY sequence DESC LIMIT 1").fetchone()
            if last is not None and storage.parse_time(last[0]) > now:
                raise storage.StoreError("clock_invalid")
            handle.execute("INSERT INTO public_owners VALUES(NULL,?,?)", (owner_id, timestamp(now)))
            uncertain = handle.execute(
                "SELECT operation_id FROM public_operations WHERE operation_id IN "
                "(SELECT operation_id FROM public_operation_events WHERE state='dispatching') "
                "ORDER BY sequence DESC LIMIT 100").fetchall()
            for row in uncertain:
                record = operation_record(handle, row[0])
                if record["state"] == "dispatching":
                    if storage.parse_time(record["observed_at"]) > now:
                        raise storage.StoreError("clock_invalid")
                    append_event(handle, row[0], owner_id, "outcome_uncertain", now,
                                 {"reservation_id": record["reservation_id"],
                                  "error_code": "owner_changed_after_dispatch"})

    def publish_public_acquisition(self, source, body, received_at, settings_revision, owner_id):
        from market_intelligence.content import parse_content
        identifier(source)
        utc_time(received_at)
        if type(body) is not bytes or not 1 <= len(body) <= 1048576:
            raise ValueError("Public acquisitions require bounded raw bytes")
        documents = parse_content(source, body, received_at)
        if type(documents) is not tuple or len(documents) > 200:
            raise ValueError("Invalid public acquisition document count")
        if any(item.source != source or item.received_at != received_at for item in documents):
            raise ValueError("Public acquisition source or clock mismatch")
        raw_hash = hashlib.sha256(body).hexdigest()
        acquisition_id = "public-acquisition-" + uuid.uuid4().hex
        payload, checksum = write_payload({"parser_version": "official-content-v1",
                                          "documents": [item.wire() for item in documents]})
        with storage.connection(self.path, writable=True, expected_id=self.database_id) as handle:
            require_schema(handle)
            require_owner(handle, owner_id)
            _, settings_at = require_settings(self, handle, settings_revision)
            if received_at < settings_at:
                raise storage.StoreError("clock_invalid")
            last = handle.execute("SELECT observed_at FROM public_attempts ORDER BY sequence DESC LIMIT 1").fetchone()
            if last is not None and storage.parse_time(last[0]) > received_at:
                raise storage.StoreError("clock_invalid")
            old = handle.execute("SELECT body FROM public_payloads WHERE input_hash=?", (raw_hash,)).fetchone()
            if old is None:
                handle.execute("INSERT INTO public_payloads VALUES(?,?)", (raw_hash, body))
            elif old[0] != body:
                raise storage.StoreError("storage_invalid")
            handle.execute("INSERT INTO public_acquisitions VALUES(NULL,?,?,?,?,?,?,?)",
                           (acquisition_id, source, settings_revision, timestamp(received_at),
                            raw_hash, payload, checksum))
            for document in documents:
                row = handle.execute(f"SELECT {payload_columns()} FROM public_documents WHERE evidence_id=?",
                                     (document.evidence_id,)).fetchone()
                if row is not None:
                    prior = restore_document(read_payload(row))
                    if prior.semantic() != document.semantic():
                        raise storage.StoreError("evidence_conflict")
                else:
                    document_payload, document_hash = write_payload(document.wire())
                    handle.execute("INSERT INTO public_documents VALUES(?,?,?,?)",
                                   (document.evidence_id, acquisition_id, document_payload, document_hash))
            handle.execute("INSERT INTO public_attempts VALUES(NULL,?,?,?,?,NULL)",
                           (source, timestamp(received_at), settings_revision, acquisition_id))
        return acquisition_id

    def fail_public_source(self, source, now, settings_revision, owner_id, error_code):
        identifier(source)
        identifier(error_code)
        utc_time(now)
        with storage.connection(self.path, writable=True, expected_id=self.database_id) as handle:
            require_schema(handle)
            require_owner(handle, owner_id)
            _, at = require_settings(self, handle, settings_revision)
            last = handle.execute("SELECT observed_at FROM public_attempts ORDER BY sequence DESC LIMIT 1").fetchone()
            if now < at or last is not None and storage.parse_time(last[0]) > now:
                raise storage.StoreError("clock_invalid")
            handle.execute("INSERT INTO public_attempts VALUES(NULL,?,?,?,NULL,?)",
                           (source, timestamp(now), settings_revision, error_code))

    def _public_documents(self, handle, sources=None):
        rows = handle.execute(f"SELECT acquisition_id,source,received_at,input_hash,{payload_columns()} "
                              "FROM public_acquisitions a WHERE sequence=(SELECT MAX(b.sequence) "
                              "FROM public_acquisitions b WHERE b.source=a.source) "
                              "ORDER BY sequence LIMIT 20").fetchall()
        documents, acquisition_ids = [], []
        for row in rows:
            if sources is not None and row["source"] not in sources:
                continue
            payload = read_payload(row)
            try:
                records = payload["documents"]
                if (set(payload) != {"parser_version", "documents"} or payload["parser_version"] != "official-content-v1"
                        or type(records) is not list or len(records) > 200):
                    raise ValueError("Invalid public archive")
                received_at = storage.parse_time(row["received_at"])
                raw = handle.execute("SELECT body FROM public_payloads WHERE input_hash=?",
                                     (row["input_hash"],)).fetchone()
                if (raw is None or type(raw[0]) is not bytes or not 1 <= len(raw[0]) <= 1048576
                        or hashlib.sha256(raw[0]).hexdigest() != row["input_hash"]):
                    raise ValueError("Invalid public acquisition hash")
                from market_intelligence.content import parse_content
                reparsed = parse_content(row["source"], raw[0], received_at)
                if [item.wire() for item in reparsed] != records:
                    raise ValueError("Public acquisition does not replay")
                for item in records:
                    document = restore_document(item)
                    if document.source != row["source"] or document.received_at != received_at:
                        raise ValueError("Public archive binding mismatch")
                    original = handle.execute(f"SELECT {payload_columns()} FROM public_documents WHERE evidence_id=?",
                                              (document.evidence_id,)).fetchone()
                    first = restore_document(read_payload(original))
                    if first.semantic() != document.semantic() or first.received_at > document.received_at:
                        raise ValueError("Conflicting public evidence identity")
                    documents.append(first)
                acquisition_ids.append(row["acquisition_id"])
            except (ValueError, KeyError, TypeError) as exc:
                raise storage.StoreError("storage_invalid") from exc
        return tuple(documents[-1000:]), tuple(acquisition_ids)

    def _validate_public_manifest(self, handle, bundle):
        for item in bundle.documents:
            if item.kind == "schedule":
                calendar = self._calendar(handle)
                compiled = compile_bundle((), calendar, bundle.cutoff) if calendar is not None else None
                if compiled is None or item not in compiled.documents:
                    raise storage.StoreError("evidence_unavailable")
            else:
                row = handle.execute(f"SELECT {payload_columns()} FROM public_documents WHERE evidence_id=?",
                                     (item.evidence_id,)).fetchone()
                if row is None or restore_document(read_payload(row)) != item:
                    raise storage.StoreError("evidence_unavailable")

    def public_compilation_inputs(self, now, sources=None):
        utc_time(now)
        if sources is not None and (type(sources) is not tuple or len(sources) > 10):
            raise ValueError("Public selection must be a bounded source tuple")
        with storage.connection(self.path, expected_id=self.database_id) as handle:
            require_schema(handle)
            documents, acquisitions = self._public_documents(handle, sources)
            return {"documents": documents, "acquisition_ids": acquisitions,
                    "calendar": self._calendar(handle), "settings": self._settings(handle)[0],
                    "source_health": self._public_health(handle, now)}

    def _public_health(self, handle, now):
        rows = handle.execute("SELECT a.source,a.observed_at,a.acquisition_id,a.error_code "
                              "FROM public_attempts a WHERE a.sequence=(SELECT MAX(b.sequence) FROM public_attempts b "
                              "WHERE b.source=a.source) ORDER BY a.source LIMIT 20").fetchall()
        result = []
        for row in rows:
            at = storage.parse_time(row["observed_at"])
            last = handle.execute("SELECT received_at FROM public_acquisitions WHERE source=? "
                                  "ORDER BY sequence DESC LIMIT 1", (row["source"],)).fetchone()
            result.append({"source": row["source"], "attempted_at": timestamp(at),
                           "succeeded_at": last[0] if last else None, "error_code": row["error_code"],
                           "freshness": ("future" if at > now else "stale" if now - at > timedelta(hours=6)
                                         else "recent"), "coverage": "selected_documents_only"})
        return result

    def ensure_public_operation(self, bundle, provider, model, prompt_version, now, owner_id,
                                acquisition_ids=(), provider_kind="custom"):
        if not isinstance(bundle, PublicBundle):
            raise ValueError("Validated public bundle required")
        for value in (provider, prompt_version):
            identifier(value)
        from market_intelligence.compilation import plain_text
        plain_text(model, 128)
        utc_time(now)
        if provider_kind not in ("custom", "local", "openai", "anthropic", "gemini"):
            raise ValueError("Unsupported summary provider kind")
        identity = {"input_hash": bundle.input_hash, "provider": provider, "provider_kind": provider_kind, "model": model,
                    "prompt_version": prompt_version, "schema_version": "1"}
        operation_id = "synthesis-" + storage.digest(canonical(identity))
        metadata = {**identity, "manifest": bundle.wire(), "acquisition_ids": list(acquisition_ids)}
        with storage.connection(self.path, writable=True, expected_id=self.database_id) as handle:
            require_schema(handle)
            require_owner(handle, owner_id)
            settings, at = self._settings(handle)
            require_settings(self, handle, settings.revision, provider)
            if not at <= bundle.cutoff <= now:
                raise storage.StoreError("clock_invalid")
            existing = operation_record(handle, operation_id)
            if existing is not None:
                return existing
            self._validate_public_manifest(handle, bundle)
            lineage = set(acquisition_ids)
            for item in bundle.documents:
                if item.kind != "schedule":
                    row = handle.execute("SELECT first_acquisition_id FROM public_documents WHERE evidence_id=?",
                                         (item.evidence_id,)).fetchone()
                    lineage.add(row[0])
            if len(lineage) > 100:
                raise ValueError("Public acquisition lineage exceeds bound")
            for acquisition_id in lineage:
                identifier(acquisition_id)
                row = handle.execute("SELECT received_at FROM public_acquisitions WHERE acquisition_id=?",
                                     (acquisition_id,)).fetchone()
                if row is None or storage.parse_time(row[0]) > bundle.cutoff:
                    raise storage.StoreError("evidence_unavailable")
            metadata["acquisition_ids"] = sorted(lineage)
            payload, checksum = write_payload({**metadata, "settings_revision": settings.revision})
            handle.execute("INSERT INTO public_operations VALUES(NULL,?,?,?,?)",
                           (operation_id, settings.revision, payload, checksum))
            append_event(handle, operation_id, owner_id, "queued", now, {"error_code": None})
            return operation_record(handle, operation_id)

    def reserve_public_operation(self, operation_id, owner_id, now, limits):
        if not isinstance(limits, AnalysisLimits):
            raise ValueError("Analysis limits required")
        utc_time(now)
        exhausted = False
        with storage.connection(self.path, writable=True, expected_id=self.database_id) as handle:
            require_schema(handle)
            require_owner(handle, owner_id)
            record = operation_record(handle, operation_id)
            if record is None:
                raise storage.StoreError("operation_missing")
            if record["state"] != "queued":
                return record
            metadata = record["metadata"]
            require_settings(self, handle, metadata["settings_revision"], metadata["provider"])
            from market_intelligence.briefing import public_request
            request = public_request(metadata["manifest"], limits)
            input_bytes = len(request.system_prompt.encode("utf-8")) + len(request.evidence_json.encode("utf-8"))
            if input_bytes > limits.max_input_bytes:
                raise storage.StoreError("input_too_large")
            latest = handle.execute("SELECT observed_at FROM analysis_reservations ORDER BY sequence DESC LIMIT 1").fetchone()
            if (latest and storage.parse_time(latest[0]) > now
                    or storage.parse_time(record["observed_at"]) > now):
                raise storage.StoreError("clock_invalid")
            day, limits_hash = now.date().isoformat(), storage.digest(storage.encode(limits))
            usage = handle.execute("SELECT COUNT(*),COALESCE(SUM(input_bytes),0),COALESCE(SUM(output_allowance),0) "
                                   "FROM analysis_reservations WHERE day=? AND accepted=1", (day,)).fetchone()
            policy = handle.execute("SELECT limits_hash FROM analysis_reservations WHERE day=? AND accepted=1 LIMIT 1",
                                    (day,)).fetchone()
            exhausted = bool(policy and policy[0] != limits_hash or usage[0] >= limits.daily_calls
                             or usage[1] + input_bytes > limits.daily_input_bytes
                             or usage[2] + limits.max_output_tokens > limits.daily_output_allowance)
            reservation_id = "reservation-" + uuid.uuid4().hex
            payload, checksum = write_payload({"operation_id": operation_id, **metadata})
            handle.execute("INSERT INTO analysis_reservations VALUES(NULL,?,?,?,?,?,?,?,?,?)",
                           (reservation_id, timestamp(now), day, int(not exhausted), input_bytes,
                            limits.max_output_tokens, payload, checksum, limits_hash))
            append_event(handle, operation_id, owner_id, "failed" if exhausted else "reserved", now,
                         {"reservation_id": reservation_id, "error_code": "budget_exhausted" if exhausted else None})
            result = operation_record(handle, operation_id)
        return result

    def transition_public_operation(self, operation_id, owner_id, expected_state, new_state, now,
                                    output=None, error_code=None):
        utc_time(now)
        allowed = {("reserved", "dispatching"), ("dispatching", "validated"),
                   ("dispatching", "outcome_uncertain"), ("dispatching", "failed"),
                   ("queued", "failed"), ("reserved", "failed"), ("validated", "committed"),
                   ("validated", "failed")}
        if (expected_state, new_state) not in allowed:
            raise ValueError("Invalid synthesis state transition")
        if error_code is not None:
            identifier(error_code)
        with storage.connection(self.path, writable=True, expected_id=self.database_id) as handle:
            require_schema(handle)
            require_owner(handle, owner_id)
            record = operation_record(handle, operation_id)
            if record is None or record["state"] != expected_state:
                raise storage.StoreError("operation_conflict")
            if storage.parse_time(record["observed_at"]) > now:
                raise storage.StoreError("clock_invalid")
            metadata = record["metadata"]
            if new_state not in ("failed", "outcome_uncertain"):
                require_settings(self, handle, metadata["settings_revision"], metadata["provider"])
            details = {"reservation_id": record.get("reservation_id"), "error_code": error_code}
            if new_state == "validated":
                from market_intelligence.briefing import validate_output
                details["output"] = validate_output(output, metadata["manifest"])
            if new_state == "committed":
                from market_intelligence.briefing import validate_output
                result = {"briefing_id": operation_id, "generated_at": timestamp(now),
                          "provider": metadata["provider"], "model": metadata["model"],
                          "prompt_version": metadata["prompt_version"], "schema_version": "1",
                          "manifest": metadata["manifest"], "verification": "unverified",
                          "output": validate_output(record["output"], metadata["manifest"])}
                payload, checksum = write_payload(result)
                handle.execute("INSERT INTO public_briefings VALUES(NULL,?,?,?,?)",
                               (operation_id, operation_id, payload, checksum))
                handle.execute("INSERT INTO public_selections VALUES(NULL,?,?,?,?)",
                               (operation_id, metadata["settings_revision"], owner_id, timestamp(now)))
            append_event(handle, operation_id, owner_id, new_state, now, details)
            return operation_record(handle, operation_id)

    def activate_public_briefing(self, operation_id, owner_id, now):
        utc_time(now)
        with storage.connection(self.path, writable=True, expected_id=self.database_id) as handle:
            require_schema(handle)
            require_owner(handle, owner_id)
            record = operation_record(handle, operation_id)
            if record is None or record["state"] != "committed":
                raise storage.StoreError("operation_conflict")
            settings, _ = self._settings(handle)
            require_settings(self, handle, settings.revision, record["metadata"]["provider"])
            latest = handle.execute("SELECT briefing_id,selected_at FROM public_selections ORDER BY sequence DESC LIMIT 1").fetchone()
            if latest is not None:
                if storage.parse_time(latest[1]) > now:
                    raise storage.StoreError("clock_invalid")
                if latest[0] == operation_id:
                    return
            handle.execute("INSERT INTO public_selections VALUES(NULL,?,?,?,?)",
                           (operation_id, settings.revision, owner_id, timestamp(now)))

    def public_operation(self, operation_id):
        identifier(operation_id)
        with storage.connection(self.path, expected_id=self.database_id) as handle:
            require_schema(handle)
            return operation_record(handle, operation_id)

    def request_public_refresh(self, action, request_id, now):
        identifier(request_id)
        utc_time(now)
        if action not in ("sources", "briefing"):
            raise ValueError("Unsupported intelligence refresh")
        with storage.connection(self.path, writable=True, expected_id=self.database_id) as handle:
            require_schema(handle)
            existing = handle.execute("SELECT action FROM public_requests WHERE request_id=?", (request_id,)).fetchone()
            if existing is not None:
                if existing[0] != action:
                    raise storage.StoreError("request_conflict")
                return
            handle.execute("INSERT INTO public_requests VALUES(NULL,?,?,?)", (request_id, action, timestamp(now)))

    def pending_public_requests(self):
        with storage.connection(self.path, expected_id=self.database_id) as handle:
            require_schema(handle)
            return tuple(dict(row) for row in handle.execute(
                "SELECT request_id,action,requested_at FROM public_requests WHERE request_id NOT IN "
                "(SELECT request_id FROM public_receipts) ORDER BY sequence LIMIT 50").fetchall())

    def complete_public_request(self, request_id, now, state, operation_id=None, error_code=None):
        identifier(request_id)
        utc_time(now)
        if state not in ("completed", "failed"):
            raise ValueError("Invalid refresh receipt state")
        payload, checksum = write_payload({"request_id": request_id, "observed_at": timestamp(now),
                                          "state": state, "operation_id": operation_id, "error_code": error_code})
        with storage.connection(self.path, writable=True, expected_id=self.database_id) as handle:
            require_schema(handle)
            if handle.execute("SELECT 1 FROM public_receipts WHERE request_id=?", (request_id,)).fetchone() is None:
                handle.execute("INSERT INTO public_receipts VALUES(NULL,?,?,?)", (request_id, payload, checksum))

    def foundation_snapshot(self, now):
        from zoneinfo import ZoneInfo
        utc_time(now)
        with storage.connection(self.path, expected_id=self.database_id) as handle:
            if handle.execute("PRAGMA user_version").fetchone()[0] < 7:
                return {"schema_capable": False, "briefing": None, "source_health": [], "operation": None}
            settings, _ = self._settings(handle)
            row = handle.execute(f"SELECT {payload_columns()} FROM public_briefings WHERE briefing_id="
                                 "(SELECT briefing_id FROM public_selections ORDER BY sequence DESC LIMIT 1)").fetchone()
            briefing = read_payload(row) if row is not None else None
            if briefing is not None:
                from market_intelligence.briefing import validate_output
                try:
                    validate_output(briefing["output"], briefing["manifest"])
                    generated = storage.parse_time(briefing["generated_at"])
                    if storage.parse_time(briefing["manifest"]["cutoff"]) > generated:
                        raise ValueError("Briefing precedes its public inputs")
                except (ValueError, KeyError, TypeError) as exc:
                    raise storage.StoreError("storage_invalid") from exc
                if generated > now:
                    raise storage.StoreError("clock_invalid")
                briefing["edition_date"] = now.astimezone(ZoneInfo("Europe/London")).date().isoformat()
                briefing["freshness"] = "stale" if now - generated > timedelta(days=1) else "recent"
                briefing["profile_current"] = settings.ai_profile == briefing["provider"]
            row = handle.execute("SELECT operation_id FROM public_operations ORDER BY sequence DESC LIMIT 1").fetchone()
            operation = operation_record(handle, row[0]) if row is not None else None
            if operation is not None:
                operation = {key: operation.get(key) for key in ("operation_id", "state", "observed_at", "error_code")}
            requests = handle.execute("SELECT request_id,action,requested_at FROM public_requests "
                                      "ORDER BY sequence DESC LIMIT 10").fetchall()
            refreshes = []
            for row in requests:
                receipt = handle.execute(f"SELECT {payload_columns()} FROM public_receipts WHERE request_id=?",
                                         (row["request_id"],)).fetchone()
                refreshes.append({**dict(row), **(read_payload(receipt) if receipt else {"state": "queued"})})
            return {"schema_capable": True, "briefing": briefing, "source_health": self._public_health(handle, now),
                    "operation": operation, "refreshes": refreshes}
