import hashlib
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from dashboard.assets import FrontendBuildError, build_guidance, verify_frontend
from dashboard.__main__ import main


class DashboardLaunchTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.schema = self.root / "schema.json"
        self.schema.write_text("{}")
        self.static = self.root / "static"
        (self.static / "assets").mkdir(parents=True)
        (self.static / "index.html").write_text('<script src="/assets/app.js"></script>')
        (self.static / "assets" / "app.js").write_text("void 0;")
        self.manifest = {"version": 1, "schema_sha256": self.digest(self.schema),
                         "files": {"index.html": self.digest(self.static / "index.html"),
                                   "assets/app.js": self.digest(self.static / "assets" / "app.js")}}
        self.save_manifest()

    @staticmethod
    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def save_manifest(self):
        (self.static / "dashboard-build.json").write_text(json.dumps(self.manifest))

    def test_valid_bundle_and_changed_asset(self):
        self.assertEqual(verify_frontend(self.static, self.schema), self.static)
        (self.static / "assets" / "app.js").write_text("changed")
        with self.assertRaises(FrontendBuildError):
            verify_frontend(self.static, self.schema)

    def test_production_bundle_binds_current_portfolio_contracts(self):
        module = self.root / "dashboard" / "assets.py"
        module.parent.mkdir()
        setup_schema = module.with_name("setup.schema.json")
        setup_schema.write_text("{}")
        investment_schema = self.root / "investment" / "response-v2.schema.json"
        investment_schema.parent.mkdir()
        investment_schema.write_text("{}")
        awareness_schema = investment_schema.with_name("awareness.schema.json")
        awareness_schema.write_text("{}")
        monitoring_schema = investment_schema.with_name("monitoring.schema.json")
        monitoring_schema.write_text("{}")
        self.manifest["schema_sha256"] = self.digest(investment_schema)
        self.manifest["setup_schema_sha256"] = self.digest(setup_schema)
        self.manifest["awareness_schema_sha256"] = self.digest(awareness_schema)
        self.manifest["monitoring_schema_sha256"] = self.digest(monitoring_schema)
        self.save_manifest()
        with patch("dashboard.assets.__file__", str(module)):
            self.assertEqual(verify_frontend(self.static), self.static)
            for schema in (investment_schema, setup_schema, awareness_schema, monitoring_schema):
                schema.write_text('{"changed":true}')
                with self.subTest(schema=schema.name), self.assertRaises(FrontendBuildError):
                    verify_frontend(self.static)
                schema.write_text("{}")

    def test_unlisted_assets_and_removed_manifest_entries_are_rejected(self):
        extra = self.static / "assets" / "unlisted.js"
        extra.write_text("void 0;")
        with self.assertRaises(FrontendBuildError):
            verify_frontend(self.static, self.schema)
        extra.unlink()
        self.manifest["files"].pop("assets/app.js")
        self.manifest["files"]["assets/missing.js"] = "0" * 64
        self.save_manifest()
        with self.assertRaises(FrontendBuildError):
            verify_frontend(self.static, self.schema)

    def test_direct_factory_requires_assets_in_production(self):
        try:
            from dashboard.app import create_app
        except ModuleNotFoundError:
            self.skipTest("Install dashboard development dependencies for factory checks")
        with self.assertRaises(FrontendBuildError):
            create_app(static_directory=self.root / "missing")
        self.assertIsNotNone(create_app(serve_frontend=False))

    def test_direct_and_legacy_links_serve_frontend(self):
        try:
            from fastapi.testclient import TestClient
            from dashboard.app import create_app
        except ModuleNotFoundError:
            self.skipTest("Install dashboard development dependencies for factory checks")
        from test_dashboard_api import MemoryEventLoop

        with patch("dashboard.app.verify_frontend", return_value=self.static):
            app = create_app(static_directory=self.static)
        with TestClient(app, base_url="http://localhost", backend_options={"loop_factory": MemoryEventLoop}) as client:
            for path in ("/investment-guide", "/etoro-portfolio", "/investment/update"):
                with self.subTest(path=path):
                    response = client.get(path)
                    self.assertEqual(response.status_code, 200)
                    self.assertIn('<script src="/assets/app.js"></script>', response.text)

    def test_installed_build_guidance_does_not_require_checkout(self):
        with patch("dashboard.assets.__file__", str(self.root / "package" / "assets.py")):
            self.assertIn("Reinstall a dashboard wheel", build_guidance())

    def test_missing_build_schema_mismatch_and_traversal_are_refused(self):
        with self.assertRaises(FrontendBuildError):
            verify_frontend(self.root / "missing", self.schema)
        self.schema.write_text('{"changed":true}')
        with self.assertRaises(FrontendBuildError):
            verify_frontend(self.static, self.schema)
        self.manifest["schema_sha256"] = self.digest(self.schema)
        self.manifest["files"]["../outside"] = "0" * 64
        self.save_manifest()
        with self.assertRaises(FrontendBuildError):
            verify_frontend(self.static, self.schema)

    def test_launch_requires_production_bundle(self):
        with patch("dashboard.__main__.verify_frontend", side_effect=FrontendBuildError("Missing build")):
            with redirect_stderr(io.StringIO()) as output, self.assertRaises(SystemExit):
                main(["--demo"])
        self.assertIn("npm --prefix frontend run build", output.getvalue())

    def test_default_startup_selects_checkout_workspace_without_creating_files(self):
        server = Mock()
        factory = Mock(return_value="setup-app")
        workspace = self.root / "private"
        with patch.dict(sys.modules, {"uvicorn": SimpleNamespace(run=server),
                                     "dashboard.app": SimpleNamespace(create_app=factory)}):
            with patch("dashboard.workspace.resolve_workspace", return_value=workspace), \
                    patch("dashboard.__main__.verify_frontend", return_value=self.static), \
                    redirect_stdout(io.StringIO()) as output:
                main([])
        self.assertFalse(workspace.exists())
        self.assertEqual(factory.call_args.kwargs["investment_database"], workspace / "investment.sqlite3")
        self.assertEqual(factory.call_args.kwargs["setup_workspace"], workspace)
        planning_path = workspace.parent / ".goldscout-planning" / "private.json"
        self.assertEqual(factory.call_args.kwargs["planning_profile"], planning_path)
        self.assertTrue(factory.call_args.kwargs["setup_enabled"])
        self.assertIn("/setup", output.getvalue())
        self.assertTrue(factory.call_args.kwargs["investment_recording_enabled"])
        self.assertFalse(planning_path.parent.exists())

    def test_explicit_workspace_is_mutually_exclusive_with_other_sources(self):
        for flag in ("--demo", "--investment-database"):
            arguments = ["--workspace", str(self.root / "private"), flag]
            if flag != "--demo":
                arguments.append(str(self.root / "unused"))
            with self.subTest(flag=flag), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                main(arguments)

    def test_api_only_never_starts_bot_or_opens_database(self):
        server = Mock()
        factory = Mock(return_value="synthetic-app")
        with patch.dict(sys.modules, {"uvicorn": SimpleNamespace(run=server), "dashboard.app": SimpleNamespace(create_app=factory)}):
            with patch("dashboard.__main__.verify_frontend") as verify, redirect_stdout(io.StringIO()):
                main(["--demo", "--api-only", "--port", "8766"])
        verify.assert_not_called()
        factory.assert_called_once_with(static_directory=None, serve_frontend=False,
                                        intelligence_database=None, intelligence_demo=False,
                                        analysis_controls_enabled=False, analysis_profiles=(), investment_database=None,
                                        investment_recording_enabled=False, awareness_profile=None,
                                        monitoring_profile=None, monitoring_cache=None,
                                        monitoring_reviews=None, monitoring_workflow_enabled=False,
                                        etoro_connection_path=None, planning_profile=None)
        server.assert_called_once_with("synthetic-app", host="127.0.0.1", port=8766, proxy_headers=False, access_log=False)

    def test_invalid_modes_and_ports_fail_before_runtime_imports(self):
        for args in (["--demo", "--telemetry", "unused"], ["--demo", "--port", "80"],
                     ["--demo", "--planning-profile", str(self.root / "goals.json")],
                     ["--database", "unused", "--scenario", "partial_exit"],
                     ["--investment-database", "unused", "--telemetry", "unused"],
                     ["--investment-database", "unused", "--demo"]):
            with self.subTest(args=args), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                main(args)

    def test_investment_launch_passes_only_explicit_store_and_does_not_open_it(self):
        server = Mock()
        factory = Mock(return_value="investment-app")
        with patch.dict(sys.modules, {"uvicorn": SimpleNamespace(run=server), "dashboard.app": SimpleNamespace(create_app=factory)}):
            with redirect_stdout(io.StringIO()) as output:
                main(["--investment-database", str(self.root / "investment.sqlite3"), "--api-only"])
        self.assertEqual(factory.call_args.kwargs["investment_database"], self.root / "investment.sqlite3")
        self.assertTrue(factory.call_args.kwargs["investment_recording_enabled"])
        self.assertNotIn("database", factory.call_args.kwargs)
        self.assertIn("/investment", output.getvalue())
        self.assertFalse((self.root / "investment.sqlite3").exists())

    def test_explicit_investment_journal_can_select_separate_goals_profile(self):
        server = Mock()
        factory = Mock(return_value="investment-app")
        journal = self.root / "investment.sqlite3"
        planning = self.root / "private" / "goals.json"
        args = ["--investment-database", str(journal),
                "--planning-profile", str(planning), "--api-only"]
        with patch.dict(sys.modules, {"uvicorn": SimpleNamespace(run=server),
                                     "dashboard.app": SimpleNamespace(create_app=factory)}):
            with redirect_stdout(io.StringIO()):
                main(args)
        self.assertEqual(factory.call_args.kwargs["planning_profile"], planning)
        self.assertFalse(journal.exists())
        self.assertFalse(planning.exists())

    def test_monitoring_workflow_cli_requires_separate_review_store_and_enables_without_password(self):
        server = Mock()
        factory = Mock(return_value="workflow-app")
        args = ["--investment-database", str(self.root / "investment.sqlite3"),
                "--monitoring-profile", str(self.root / "monitoring.json"),
                "--monitoring-reviews", str(self.root / "reviews.sqlite3"),
                "--monitoring-workflow", "--api-only"]
        with patch.dict(sys.modules, {"uvicorn": SimpleNamespace(run=server),
                                     "dashboard.app": SimpleNamespace(create_app=factory)}):
            with redirect_stdout(io.StringIO()):
                main(args)
        self.assertEqual(factory.call_args.kwargs["monitoring_reviews"], self.root / "reviews.sqlite3")
        self.assertTrue(factory.call_args.kwargs["monitoring_workflow_enabled"])
        self.assertTrue(factory.call_args.kwargs["investment_recording_enabled"])
        self.assertFalse((self.root / "investment.sqlite3").exists())

    def test_journal_mode_flags_and_first_run_setup_permissions(self):
        for flags, recording, setup in (
                ([], True, True),
                (["--investment-recording"], True, True),
                (["--investment-read-only"], False, False)):
            workspace = self.root / "private"
            factory = Mock(return_value="setup-app")
            with self.subTest(flags=flags), patch.dict(sys.modules, {
                    "uvicorn": SimpleNamespace(run=Mock()),
                    "dashboard.app": SimpleNamespace(create_app=factory)}):
                with redirect_stdout(io.StringIO()):
                    main(["--workspace", str(workspace), "--api-only", *flags])
            self.assertEqual(factory.call_args.kwargs["investment_recording_enabled"], recording)
            self.assertEqual(factory.call_args.kwargs["setup_enabled"], setup)
            self.assertFalse(workspace.exists())
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(["--investment-read-only", "--investment-recording"])

    def test_existing_journal_launch_modes_have_real_recording_behavior(self):
        from fastapi.testclient import TestClient
        from dashboard.app import create_app
        from investment.bootstrap import workspace_status
        from investment.store import initialize, import_events
        from test_dashboard_api import MemoryEventLoop
        from test_investment_opening import NOW, opening, profile

        workspace = self.root / "private"
        workspace.mkdir()
        database = workspace / "investment.sqlite3"
        initialize(database, profile(), NOW)
        import_events(database, [opening()], NOW)
        original = database.read_bytes()
        for source in (["--workspace", str(workspace)],
                       ["--investment-database", str(database)]):
            for flags, enabled in (([], True), (["--investment-recording"], True),
                                   (["--investment-read-only"], False)):
                def serve(app, **kwargs):
                    with TestClient(app, base_url="http://localhost",
                                    backend_options={"loop_factory": MemoryEventLoop}) as client:
                        session = client.get("/api/investment/v1/recording/session")
                        self.assertEqual(session.status_code, 200, session.text)
                        self.assertEqual(session.json()["enabled"], enabled)
                        self.assertFalse(client.get("/api/investment/v1/setup/session").json()["enabled"])
                        self.assertEqual(client.get("/api/investment/v1/snapshot").status_code, 200)

                with self.subTest(source=source[0], flags=flags), patch.dict(
                        sys.modules, {"uvicorn": SimpleNamespace(run=serve)}):
                    with patch("dashboard.app.create_app", side_effect=lambda **kw: create_app(clock=lambda: NOW, **kw)), \
                            patch("investment.bootstrap.workspace_status", side_effect=lambda path, now=None: workspace_status(path, NOW)), \
                            redirect_stdout(io.StringIO()):
                        main([*source, "--api-only", *flags])
        self.assertEqual(database.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()