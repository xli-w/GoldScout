"""Dashboard-owned public intelligence configuration and bounded worker lifecycle.

Reads never create stores or settings. Only server-selected sibling paths are used;
credentials are referenced by their existing names, never loaded here.
"""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import secrets
import tempfile
import threading

from dashboard.intelligence import ControlSessions, IntelligenceWorkspace
from dashboard.workspace import path_identity
from market_intelligence.contracts import ControlRequest, IntelligenceSettings, identifier
from market_intelligence.runtime import ProviderProfile
from market_intelligence.store import initialize, upgrade, SCHEMA_VERSION, StoreError, encode


DEFAULT_CONFIG = {"enabled": False, "calendar_sources": ["bls", "bea"],
                  "content_sources": [], "profile": None}


class IntelligenceSetupError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def source_catalog():
    from market_intelligence.providers import CALENDAR_SOURCES
    try:
        from market_intelligence.content import CONTENT_SOURCES
    except ImportError:
        CONTENT_SOURCES = {}
    return {
        "calendar": [{"source": source, "geography": "UK" if source in ("ons", "boe_mpc") else "US"}
                     for source in CALENDAR_SOURCES],
        "content": [{"source": source, **{
            key: getattr(metadata, key, None)
            for key in ("institution", "kind", "topic", "geography", "supported", "reason")}}
                    for source, metadata in CONTENT_SOURCES.items()],
    }


def validate_config(value):
    if type(value) is not dict or set(value) != set(DEFAULT_CONFIG):
        raise ValueError("Invalid intelligence settings")
    if type(value["enabled"]) is not bool:
        raise ValueError("Invalid enabled setting")
    if not value["calendar_sources"]:
        raise IntelligenceSetupError("calendar_sources_required")
    catalog = source_catalog()
    for key, available in (
            ("calendar_sources", {item["source"] for item in catalog["calendar"]}),
            ("content_sources", {item["source"] for item in catalog["content"] if item["supported"]})):
        sources = value[key]
        if (type(sources) is not list or len(sources) > 20
                or any(type(source) is not str or source not in available for source in sources)
                or len(sources) != len(set(sources))):
            raise ValueError("Unsupported source selection")
    profile = value["profile"]
    if profile is not None:
        if not value["content_sources"]:
            raise IntelligenceSetupError("briefing_content_source_required")
        if (type(profile) is not dict or not {"name", "kind", "model"} <= set(profile)
                or set(profile) - {"name", "kind", "model", "key_env", "port"}):
            raise ValueError("Invalid provider profile")
        # ProviderProfile validates fixed kinds, model bounds and key-name/port rules.
        ProviderProfile(**profile)
        if profile["kind"] != "local" and profile.get("key_env") not in (
                "TRADER_AI_OPENAI_KEY", "TRADER_AI_ANTHROPIC_KEY", "TRADER_AI_GEMINI_KEY"):
            raise ValueError("Reference an existing intelligence credential name")
    return json.loads(json.dumps(value))


def load_managed_config(path):
    """Read an explicitly selected CLI/dashboard nonsecret config, without defaults.

    The store is its sibling ``intelligence.sqlite3``. CLI callers must retain
    the saved enabled flag/source/profile intent and their explicit network gate.
    """
    path = Path(path).absolute()
    path_identity(path)
    if path.suffix.lower() != ".json" or path.stat().st_size > 16384:
        raise ValueError("Invalid intelligence configuration")
    with path.open("rb") as handle:
        raw = handle.read(16385)
    if len(raw) > 16384:
        raise ValueError("Invalid intelligence configuration")
    return validate_config(json.loads(raw))


def queue_managed_configuration(store, configuration, now, force=False):
    """Durably bind exact source/model selection to its requested flag revision."""
    config = validate_config(configuration)
    current = store.settings()
    profile = config["profile"]
    selection = {key: config[key] for key in ("calendar_sources", "content_sources", "profile")}
    desired = IntelligenceSettings(
        enabled=config["enabled"], calendar_enabled=bool(config["calendar_sources"]),
        news_enabled=bool(config["content_sources"]), ai_enabled=profile is not None,
        ai_profile=profile["name"] if profile else None, revision=current.revision + 1)
    if (not force and store.public_configuration(current.revision) == selection
            and all(getattr(current, field) == getattr(desired, field) for field in
                    ("enabled", "calendar_enabled", "news_enabled", "ai_enabled", "ai_profile"))):
        return None
    request = ControlRequest("dashboard-" + secrets.token_hex(16), store.database_id,
                             current.revision, now, now + timedelta(minutes=5), desired)
    return store.enqueue(request, now, public_configuration=selection)


class IntelligenceSetup:
    def __init__(self, workspace, *, clock=None, service_factory=None):
        selected = Path(workspace).absolute()
        directory = selected.parent / ".goldscout-intelligence" / (selected.name or "workspace-root")
        self.path = directory / "intelligence.sqlite3"
        self.config_path = directory / "settings.json"
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.service_factory = service_factory
        self.security = ControlSessions()
        self.workspace = None
        self.worker = None
        self.service = None
        self.stop_event = None
        self.active = False
        self.error_code = None
        self.control_id = None
        self.lock = threading.RLock()

    def config(self):
        path_identity(self.config_path)
        if not self.config_path.exists():
            return json.loads(json.dumps(DEFAULT_CONFIG))
        return load_managed_config(self.config_path)

    def _open(self):
        if self.workspace is None and self.path.exists():
            self.workspace = IntelligenceWorkspace(self.path, controls_enabled=True)
        return self.workspace

    def status(self):
        try:
            config = self.config()
            workspace = self._open()
            state = ("not_initialized" if workspace is None else
                     "upgrade_required" if workspace.store.schema_version < SCHEMA_VERSION else "ready")
            return {"available": True, "state": state, "config": config,
                    "catalog": source_catalog(), "worker_running": bool(self.worker and self.worker.is_alive()),
                    "error_code": self.error_code,
                    "applied_settings": json.loads(encode(workspace.store.settings())) if workspace else None,
                    "control": json.loads(encode(workspace.store.receipt(self.control_id)))
                    if workspace and self.control_id else None}
        except (ValueError, OSError, StoreError):
            return {"available": True, "state": "unavailable", "config": None,
                    "catalog": source_catalog(), "worker_running": False,
                    "error_code": "intelligence_setup_unavailable"}

    def initialize(self, action):
        if action not in ("initialize", "upgrade"):
            raise ValueError("Explicit initialization or upgrade required")
        with self.lock:
            self.stop_worker()
            path_identity(self.path)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            path_identity(self.path)
            store = upgrade(self.path) if action == "upgrade" else initialize(self.path, self.clock())
            self.workspace = IntelligenceWorkspace(store.path, controls_enabled=True)
            if self.active:
                self._start_worker()
            return self.status()

    def _persist(self, config):
        # Same-directory replacement leaves either the old or the complete new config.
        path_identity(self.config_path)
        fd, name = tempfile.mkstemp(prefix=".settings-", suffix=".tmp", dir=self.config_path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(config, handle, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            path_identity(self.config_path)
            os.replace(temporary, self.config_path)
        finally:
            temporary.unlink(missing_ok=True)

    def _queue_config(self, config, force=False):
        receipt = queue_managed_configuration(self.workspace.store, config, self.clock(), force=force)
        if receipt is not None:
            self.control_id = receipt.request_id

    def _apply_config_control(self, config, force=False):
        """Apply under the sole existing owner, or durably queue for a CLI owner."""
        from market_intelligence.service import IntelligenceService
        names = (config["profile"]["name"],) if config["profile"] else ()
        controller = IntelligenceService(self.workspace.store, profiles=names, clock=self.clock,
                                         allow_network=False,
                                         calendar_sources=tuple(config["calendar_sources"]))
        try:
            with controller:
                # Settle previous pending controls before binding the new revision;
                # this owner never has network capability.
                controller.step()
                self._queue_config(config, force=force)
                controller.step()
        except StoreError as exc:
            if exc.code != "service_lock_unavailable":
                raise
            self._queue_config(config, force=force)
            self.error_code = "settings_awaiting_service"

    def save(self, payload):
        config = validate_config(payload)
        with self.lock:
            if self.status()["state"] != "ready":
                raise ValueError("Initialize or upgrade intelligence first")
            previous = self.config()
            self.stop_worker()
            self._persist(config)
            self.error_code = None
            try:
                # Disable is a durable service command even without a dashboard
                # worker; a control-only owner applies it before returning.
                # Source/model-only edits also invalidate the settings revision.
                self._apply_config_control(config, force=previous != config)
                if self.active:
                    self._start_worker()
            except Exception:
                # The complete config already committed. Report the failed
                # application/start separately, never imply a save rollback.
                self.error_code = "intelligence_start_failed"
            return self.status()

    def start(self):
        with self.lock:
            self.active = True
            try:
                # Recover an interruption between config replacement and command
                # enqueue, including a saved disable. Startup never initializes
                # or upgrades storage, and this processor cannot collect.
                if self.status()["state"] == "ready":
                    self._apply_config_control(self.config())
                self._start_worker()
            except Exception:
                # A bad public store/config or another CLI owner must not prevent
                # the journal dashboard from opening an actionable setup page.
                self.error_code = "intelligence_start_failed"

    def _start_worker(self):
        config = self.config()
        if (not config["enabled"] or self.status()["state"] != "ready"
                or self.worker and self.worker.is_alive()):
            return
        self._queue_config(config)
        profile = ProviderProfile(**config["profile"]) if config["profile"] else None
        providers = {profile.name: profile.provider()} if profile else {}
        self.workspace.profiles = tuple(providers)
        if self.service_factory is None:
            from market_intelligence.briefing import FoundationService
            factory = FoundationService
        else:
            factory = self.service_factory
        service = factory(self.workspace.store, calendar_sources=tuple(config["calendar_sources"]),
                          content_sources=tuple(config["content_sources"]), providers=providers,
                          clock=self.clock, configuration_loader=self.config)
        stop = threading.Event()
        ready = threading.Event()
        self.stop_event = stop
        self.error_code = None

        def run():
            try:
                # FoundationService alone owns the store's existing service lock.
                with service:
                    self.service = service
                    ready.set()
                    service.run(stop)
            except Exception:
                self.error_code = "intelligence_worker_failed"
            finally:
                self.service = None
                ready.set()

        self.worker = threading.Thread(target=run, name="goldscout-intelligence", daemon=True)
        self.worker.start()
        ready.wait(2)

    def stop_worker(self):
        if self.stop_event is not None:
            self.stop_event.set()
        if self.service is not None and hasattr(self.service, "cancel"):
            self.service.cancel()
        if self.worker is not None:
            self.worker.join(timeout=30)
            if self.worker.is_alive():
                raise ValueError("Intelligence worker is still stopping")
        self.worker = None
        self.service = None

    def stop(self):
        with self.lock:
            self.active = False
            self.stop_worker()

    def refresh(self, payload):
        if (type(payload) is not dict or set(payload) != {"action", "request_id"}
                or payload["action"] not in ("sources", "briefing")):
            raise ValueError("Invalid refresh")
        identifier(payload["request_id"])
        with self.lock:
            if not self.config()["enabled"] or self.workspace is None:
                raise ValueError("Enable intelligence before refreshing")
            return self.workspace.store.request_public_refresh(
                payload["action"], payload["request_id"], self.clock())
