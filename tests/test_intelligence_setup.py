"""Managed intelligence uses temporary public stores and no provider calls."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from contextlib import redirect_stdout, redirect_stderr
import io
import json
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from dashboard.app import create_app
from dashboard.intelligence_setup import IntelligenceSetup, load_managed_config
from test_dashboard_api import MemoryEventLoop


class FakeService:
    instances = []

    def __init__(self, store, **options):
        self.store, self.options = store, options
        self.started, self.stopped = threading.Event(), threading.Event()
        self.requests = []
        self.__class__.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.stopped.set()

    def run(self, stop_event):
        self.started.set()
        stop_event.wait(5)

    def request(self, action, request_id):
        self.requests.append((action, request_id))
        return {"operation_id": request_id, "state": "pending"}


class IntelligenceSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name) / "portfolio"
        self.now = datetime(2026, 11, 23, 12, tzinfo=timezone.utc)
        FakeService.instances = []

    def manager(self):
        return IntelligenceSetup(self.workspace, clock=lambda: self.now,
                                 service_factory=FakeService)

    def config(self, enabled=False):
        return {"enabled": enabled, "calendar_sources": ["bls", "ons"],
                "content_sources": [], "profile": None}

    def test_reads_do_not_create_and_paths_are_outside_journal(self):
        manager = self.manager()
        self.assertEqual(manager.status()["state"], "not_initialized")
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])
        self.assertEqual(manager.path.parent,
                         Path(self.temp.name) / ".goldscout-intelligence" / "portfolio")
        self.assertFalse(manager.path.is_relative_to(self.workspace))

    def test_startup_never_initializes_or_upgrades_storage(self):
        from market_intelligence.store import initialize
        manager = self.manager()
        manager.start()
        manager.stop()
        self.assertFalse(manager.path.parent.exists())

        manager.path.parent.mkdir(parents=True)
        initialize(manager.path, self.now, schema_version=6)
        before = {path.name: path.read_bytes() for path in manager.path.parent.iterdir()}
        manager.start()
        self.addCleanup(manager.stop)
        self.assertEqual(manager.status()["state"], "upgrade_required")
        self.assertEqual(before, {path.name: path.read_bytes() for path in manager.path.parent.iterdir()})

    def test_initialize_is_explicit_and_never_touches_journal(self):
        manager = self.manager()
        manager.initialize("initialize")
        identity = manager.workspace.store.database_id
        manager.initialize("initialize")
        self.assertEqual(manager.workspace.store.database_id, identity)
        self.assertFalse(self.workspace.exists())
        self.assertEqual(manager.status()["state"], "ready")

    def test_config_is_nonsecret_bounded_and_no_urls_or_paths(self):
        manager = self.manager()
        manager.initialize("initialize")
        for invalid in (
            {**self.config(), "path": "/tmp/store"},
            {**self.config(), "calendar_sources": ["unknown"]},
            {**self.config(), "enabled": 1},
            {**self.config(), "calendar_sources": []},
            {**self.config(), "profile": {"name": "a", "kind": "local", "model": "m"}},
            {**self.config(), "profile": {"name": "a", "kind": "openai", "model": "m",
                                        "key_env": "ACTUAL_SECRET"}},
            {**self.config(), "profile": {"name": "a", "kind": "local", "model": "m",
                                        "url": "http://remote.invalid"}},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                manager.save(invalid)
        manager.save(self.config())
        self.assertEqual(manager.status()["config"], self.config())

    def test_real_foundation_bootstrap_applies_settings_and_releases_sole_owner(self):
        from market_intelligence.briefing import FoundationService
        from market_intelligence.providers import ProviderError
        from market_intelligence.service import ServiceLock

        services = []

        class OfflineTransport:
            def fetch(self, *args, **kwargs):
                raise ProviderError("provider_unavailable")

            def cancel(self):
                pass

        def factory(store, **options):
            service = FoundationService(store, transport=OfflineTransport(), **options)
            services.append(service)
            return service

        manager = IntelligenceSetup(self.workspace, clock=lambda: self.now, service_factory=factory)
        manager.initialize("initialize")
        manager.save(self.config(True))
        manager.start()
        self.addCleanup(manager.stop)
        deadline = time.monotonic() + 3
        while manager.workspace.store.settings().revision == 0 and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertTrue(manager.workspace.store.settings().enabled)
        self.assertEqual(len(services), 1)
        self.assertIsNone(manager.error_code)
        manager.stop()
        owner = ServiceLock(manager.path.with_suffix(".service.lock"))
        owner.close()

    def test_fastapi_lifespan_starts_saved_enabled_and_stops_worker(self):
        manager = self.manager()
        manager.initialize("initialize")
        manager.save(self.config(True))
        with patch("dashboard.intelligence_setup.IntelligenceSetup", return_value=manager):
            app = create_app(serve_frontend=False,
                             investment_database=self.workspace / "investment.sqlite3")
        with TestClient(app, base_url="http://localhost",
                        backend_options={"loop_factory": MemoryEventLoop}):
            self.assertTrue(FakeService.instances[0].started.wait(2))
        self.assertTrue(FakeService.instances[0].stopped.is_set())

    def test_persisted_enabled_starts_once_and_stops_with_lifespan(self):
        manager = self.manager()
        manager.initialize("initialize")
        manager.save(self.config(True))
        self.assertFalse(FakeService.instances)
        restarted = self.manager()
        restarted.start()
        self.addCleanup(restarted.stop)
        self.assertTrue(FakeService.instances[-1].started.wait(2))
        restarted.start()
        self.assertEqual(len(FakeService.instances), 1)
        service = FakeService.instances[0]
        self.assertEqual(service.options["calendar_sources"], ("bls", "ons"))
        with patch.object(restarted.workspace.store, "request_public_refresh",
                          return_value={"request_id": "refresh-1"}) as request:
            restarted.refresh({"action": "sources", "request_id": "refresh-1"})
        request.assert_called_once_with("sources", "refresh-1", self.now)
        restarted.stop()
        self.assertTrue(service.stopped.is_set())

    def test_disable_stops_worker_and_does_not_call_provider(self):
        manager = self.manager()
        manager.initialize("initialize")
        manager.save(self.config(True))
        manager.start()
        self.addCleanup(manager.stop)
        self.assertTrue(FakeService.instances[0].started.wait(2))
        manager.save(self.config(False))
        self.assertTrue(FakeService.instances[0].stopped.is_set())
        self.assertFalse(manager.workspace.store.settings().enabled)
        with self.assertRaises(ValueError):
            manager.refresh({"action": "briefing", "request_id": "disabled"})

    def test_disable_queues_for_cli_owner_and_reports_requested_vs_applied(self):
        from market_intelligence.service import IntelligenceService
        manager = self.manager()
        manager.initialize("initialize")
        manager.save(self.config(True))
        with IntelligenceService(manager.workspace.store, clock=lambda: self.now) as cli:
            status = manager.save(self.config(False))
            self.assertFalse(status["config"]["enabled"])
            self.assertTrue(status["applied_settings"]["enabled"])
            self.assertEqual(status["control"]["status"], "pending")
            cli.step()
        self.assertFalse(manager.workspace.store.settings().enabled)
        self.assertEqual(manager.status()["control"]["status"], "applied")

    def test_restart_recovers_disable_persisted_before_enqueue_crash(self):
        manager = self.manager()
        manager.initialize("initialize")
        manager.save(self.config(True))
        with patch.object(manager, "_apply_config_control", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                manager.save(self.config(False))
        self.assertFalse(manager.config()["enabled"])
        self.assertTrue(manager.workspace.store.settings().enabled)

        restarted = self.manager()
        restarted.start()
        self.addCleanup(restarted.stop)
        self.assertFalse(restarted.workspace.store.settings().enabled)
        self.assertFalse(restarted.status()["worker_running"])
        self.assertEqual(restarted.status()["control"]["status"], "applied")
        self.assertFalse(FakeService.instances)

    def test_restart_queues_crash_recovery_disable_for_another_owner(self):
        from market_intelligence.service import IntelligenceService
        manager = self.manager()
        manager.initialize("initialize")
        manager.save(self.config(True))
        with patch.object(manager, "_apply_config_control", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                manager.save(self.config(False))

        with IntelligenceService(manager.workspace.store, clock=lambda: self.now) as cli:
            restarted = self.manager()
            restarted.start()
            self.addCleanup(restarted.stop)
            status = restarted.status()
            self.assertFalse(status["config"]["enabled"])
            self.assertTrue(status["applied_settings"]["enabled"])
            self.assertEqual(status["control"]["status"], "pending")
            self.assertFalse(status["worker_running"])
            cli.step()
        self.assertFalse(restarted.workspace.store.settings().enabled)
        self.assertEqual(restarted.status()["control"]["status"], "applied")

    def test_source_and_model_only_edits_advance_service_settings_revision(self):
        manager = self.manager()
        manager.initialize("initialize")
        config = {**self.config(), "content_sources": ["ons_cpi"],
                  "profile": {"name": "local", "kind": "local", "model": "first"}}
        manager.save(config)
        revision = manager.workspace.store.settings().revision
        manager.save({**config, "calendar_sources": ["bls"]})
        self.assertEqual(manager.workspace.store.settings().revision, revision + 1)
        manager.save({**config, "calendar_sources": ["bls"], "profile": {**config["profile"], "model": "second"}})
        self.assertEqual(manager.workspace.store.settings().revision, revision + 2)

    def test_refresh_is_durable_without_dashboard_worker_and_conflicts_are_rejected(self):
        manager = self.manager()
        manager.initialize("initialize")
        manager.save(self.config(True))
        request = {"action": "sources", "request_id": "refresh-with-cli-owner"}
        manager.refresh(request)
        manager.refresh(request)
        requests = manager.workspace.store.pending_public_requests()
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["action"], "sources")
        from market_intelligence.store import StoreError
        with self.assertRaises(StoreError):
            manager.refresh({**request, "action": "briefing"})

    def test_worker_constructor_failure_reports_committed_config_separately(self):
        manager = self.manager()
        manager.initialize("initialize")
        manager.start()
        manager.service_factory = lambda *a, **k: (_ for _ in ()).throw(ValueError("failed"))
        status = manager.save(self.config(True))
        self.assertEqual(status["config"], self.config(True))
        self.assertEqual(status["error_code"], "intelligence_start_failed")
        self.assertFalse(status["worker_running"])

    def test_profile_only_references_existing_names_without_loading_values(self):
        manager = self.manager()
        manager.initialize("initialize")
        config = {**self.config(), "content_sources": ["ons_cpi"], "profile": {
            "name": "public", "kind": "openai", "model": "test-model", "key_env": "TRADER_AI_OPENAI_KEY"}}
        with patch("market_intelligence.runtime.configured_connection_values",
                   side_effect=AssertionError("Must not load credentials")):
            manager.save(config)
            self.assertEqual(manager.status()["config"], config)
        with self.assertRaises(ValueError):
            manager.save({**config, "profile": {**config["profile"], "key_env": "TRADER_AI_CUSTOM_KEY"}})

    def test_explicit_cli_config_loader_preserves_intent_and_missing_is_not_defaulted(self):
        manager = self.manager()
        with self.assertRaises(FileNotFoundError):
            load_managed_config(manager.config_path)
        manager.initialize("initialize")
        manager.save(self.config(False))
        self.assertEqual(load_managed_config(manager.config_path), self.config(False))
        self.assertFalse(load_managed_config(manager.config_path)["enabled"])

    def cli(self, arguments):
        from market_intelligence.__main__ import main
        output = io.StringIO()
        with redirect_stdout(output), patch("market_intelligence.__main__.datetime") as clock:
            clock.now.return_value = self.now
            result = main(arguments)
        return result, output.getvalue()

    def test_managed_cli_rejects_broadening_overrides_and_mismatched_store(self):
        from market_intelligence.__main__ import main
        manager = self.manager()
        base = ["run", "--managed-config", str(manager.config_path), "--allow-network"]
        invalid = (
            ["run", "--managed-config", str(manager.config_path)],
            base + ["--calendar-source", "bea"],
            base + ["--collect-news"],
            base + ["--allow-ai"],
            base + ["--ai-profiles", str(Path(self.temp.name) / "profiles.json")],
            base + ["--check-source", "bls"],
            base + ["--database", str(Path(self.temp.name) / "other.sqlite3")],
            ["initialize", "--managed-config", str(manager.config_path)],
            ["check", "--managed-config", str(manager.config_path), "--allow-network", "--check-source", "bls"],
            ["status"],
        )
        for arguments in invalid:
            with self.subTest(arguments=arguments), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    main(arguments)
                self.assertEqual(raised.exception.code, 2)
        self.assertFalse(manager.path.parent.exists())

    def test_managed_cli_status_is_read_only_and_exposes_only_saved_nonsecret_selection(self):
        manager = self.manager()
        manager.initialize("initialize")
        manager.save(self.config(False))
        before = {path.name: path.read_bytes() for path in manager.path.parent.iterdir()}
        with patch("market_intelligence.runtime.configured_connection_values",
                   side_effect=AssertionError("status must not load credentials")), \
                patch("market_intelligence.__main__.FoundationService",
                      side_effect=AssertionError("status must not create a service")):
            result, output = self.cli(["status", "--managed-config", str(manager.config_path),
                                       "--database", str(manager.path)])
        self.assertEqual(result, 0)
        value = json.loads(output)
        self.assertEqual(value["managed_configuration"], self.config(False))
        self.assertFalse(value["network_permitted"])
        self.assertEqual(before, {path.name: path.read_bytes() for path in manager.path.parent.iterdir()})

    def test_managed_cli_once_is_control_only_even_when_saved_enabled(self):
        manager = self.manager()
        manager.initialize("initialize")
        manager.save(self.config(True))
        with patch("market_intelligence.service.datetime") as clock, \
                patch("market_intelligence.__main__.FoundationService",
                      side_effect=AssertionError("once must not create a collecting service")), \
                patch("market_intelligence.runtime.ProviderProfile.provider",
                      side_effect=AssertionError("once must not create providers")):
            clock.now.return_value = self.now
            result, output = self.cli(["once", "--managed-config", str(manager.config_path)])
        self.assertEqual(result, 0, output)
        self.assertTrue(json.loads(output)["managed_configuration"]["enabled"])
        self.assertFalse(json.loads(output)["network_permitted"])

    def test_managed_cli_run_can_start_disabled_and_reloads_exact_saved_selection(self):
        manager = self.manager()
        manager.initialize("initialize")
        manager.save(self.config(False))
        seen = []

        class ManagedFakeService(FakeService):
            def run(service, stop_event):
                loader = service.options["configuration_loader"]
                seen.append(loader())
                manager.save({**manager.config(), "calendar_sources": ["ons"]})
                seen.append(loader())

        with patch("market_intelligence.__main__.FoundationService", ManagedFakeService), \
                patch("market_intelligence.runtime.configured_connection_values",
                      side_effect=AssertionError("No credentials in the CLI test")):
            result, output = self.cli(["run", "--managed-config", str(manager.config_path), "--allow-network"])
        self.assertEqual(result, 0, output)
        self.assertFalse(seen[0]["enabled"])
        self.assertEqual(seen[0]["calendar_sources"], ["bls", "ons"])
        self.assertEqual(seen[1]["calendar_sources"], ["ons"])
        service = FakeService.instances[-1]
        self.assertEqual(service.options["content_sources"], ())
        self.assertEqual(service.options["providers"], {})

    def test_managed_cli_missing_config_or_store_is_not_initialized_by_read(self):
        manager = self.manager()
        result, output = self.cli(["status", "--managed-config", str(manager.config_path)])
        self.assertEqual(result, 1, output)
        self.assertFalse(manager.path.parent.exists())

    def test_managed_cli_bootstrap_atomically_records_exact_source_model_binding(self):
        manager = self.manager()
        manager.initialize("initialize")
        config = {**self.config(False), "content_sources": ["ons_cpi"],
                  "profile": {"name": "local", "kind": "local", "model": "exact-model", "port": 11435}}
        manager._persist(config)
        selected = {key: config[key] for key in ("calendar_sources", "content_sources", "profile")}

        class BootstrapFake(FakeService):
            def run(service, stop_event):
                service.store.apply_pending(self.now, profiles=("local",))

        with patch("market_intelligence.__main__.FoundationService", BootstrapFake):
            result, output = self.cli(["run", "--managed-config", str(manager.config_path), "--allow-network"])
        self.assertEqual(result, 0, output)
        current = manager.workspace.store.settings()
        self.assertEqual(current.revision, 1)
        self.assertEqual(manager.workspace.store.public_configuration(current.revision), selected)

    def test_bad_store_or_config_has_actionable_status_not_reader_repair(self):
        manager = self.manager()
        manager.path.parent.mkdir(parents=True)
        manager.path.write_bytes(b"not sqlite")
        before = manager.path.read_bytes()
        self.assertEqual(manager.status()["state"], "unavailable")
        self.assertEqual(manager.path.read_bytes(), before)
        self.assertFalse(manager.config_path.exists())

    def test_upcoming_calendar_is_selected_before_legacy_history_truncation(self):
        from market_intelligence.contracts import CalendarEvent, CalendarSnapshot, SourceCoverage
        manager = self.manager()
        manager.initialize("initialize")
        manager.save(self.config(True))
        store = manager.workspace.store
        source = SourceCoverage("bls", ("inflation",), self.now, None, None, "unavailable")
        events = tuple(CalendarEvent(
            f"past-{index}", "bls", "https://www.bls.gov/schedule/", "inflation", ("SPY",),
            self.now - timedelta(days=index + 1), self.now, f"Past event {index}")
            for index in range(101))
        future = CalendarEvent("future", "bls", "https://www.bls.gov/schedule/", "inflation",
                               ("SPY",), self.now + timedelta(days=1), self.now, "Future event")
        store.publish_calendar(CalendarSnapshot(1, self.now, (source,), events + (future,)),
                               store.settings().revision, self.now)
        with patch("dashboard.intelligence.datetime") as mocked:
            mocked.now.return_value = self.now
            result = manager.workspace.snapshot()
        self.assertEqual(len(result["calendar"]), 100)
        self.assertNotIn("future", [item["event_id"] for item in result["calendar"]])
        self.assertEqual([item["event_id"] for item in result["upcoming_calendar"]], ["future"])
        self.assertEqual(result["upcoming_calendar_total"], 1)

    def test_api_setup_and_save_require_origin_csrf_and_fixed_fields(self):
        app = create_app(serve_frontend=False,
                         investment_database=self.workspace / "investment.sqlite3")
        with TestClient(app, base_url="http://localhost",
                        backend_options={"loop_factory": MemoryEventLoop}) as client:
            snapshot = client.get("/api/v1/intelligence")
            self.assertEqual(snapshot.status_code, 200)
            self.assertEqual(snapshot.json()["setup"]["state"], "not_initialized")
            self.assertFalse(self.workspace.exists())
            session = client.get("/api/v1/intelligence/session").json()
            headers = {"Origin": "http://localhost", "X-CSRF-Token": session["csrf"]}
            endpoint = "/api/v1/intelligence/setup"
            self.assertEqual(client.post(endpoint, json={"action": "initialize"}).status_code, 403)
            self.assertEqual(client.post(endpoint, headers={"Origin": "http://localhost"},
                                         json={"action": "initialize"}).status_code, 403)
            self.assertEqual(client.post(endpoint, headers=headers,
                                         json={"action": "initialize", "path": "bad"}).status_code, 400)
            self.assertEqual(client.post(endpoint, headers=headers,
                                         json={"action": "initialize"}).status_code, 200)
            result = client.post("/api/v1/intelligence/settings", headers=headers, json=self.config())
            self.assertEqual(result.status_code, 200, result.text)
            with patch("market_intelligence.runtime.ProviderProfile.provider",
                       side_effect=AssertionError("GET must never create a provider")):
                self.assertEqual(client.get("/api/v1/intelligence").status_code, 200)
            self.assertFalse(self.workspace.exists())


if __name__ == "__main__":
    unittest.main()
