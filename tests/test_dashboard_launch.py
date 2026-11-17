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

    def test_production_bundle_binds_investment_schema_as_well_as_legacy_schema(self):
        production_schema = self.root / "dashboard" / "response.schema.json"
        production_schema.parent.mkdir()
        production_schema.write_text("{}")
        setup_schema = production_schema.with_name("setup.schema.json")
        setup_schema.write_text("{}")
        investment_schema = self.root / "investment" / "response.schema.json"
        investment_schema.parent.mkdir()
        investment_schema.write_text("{}")
        opening_schema = investment_schema.with_name("response-v2.schema.json")
        opening_schema.write_text("{}")
        awareness_schema = investment_schema.with_name("awareness.schema.json")
        awareness_schema.write_text("{}")
        monitoring_schema = investment_schema.with_name("monitoring.schema.json")
        monitoring_schema.write_text("{}")
        self.manifest["investment_schema_sha256"] = self.digest(investment_schema)
        self.manifest["setup_schema_sha256"] = self.digest(setup_schema)
        self.manifest["investment_v2_schema_sha256"] = self.digest(opening_schema)
        self.manifest["awareness_schema_sha256"] = self.digest(awareness_schema)
        self.manifest["monitoring_schema_sha256"] = self.digest(monitoring_schema)
        self.save_manifest()
        with patch("dashboard.assets.__file__", str(production_schema.with_name("assets.py"))):
            self.assertEqual(verify_frontend(self.static), self.static)
            investment_schema.write_text('{"changed":true}')
            with self.assertRaises(FrontendBuildError):
                verify_frontend(self.static)
            investment_schema.write_text("{}")
            opening_schema.write_text('{"changed":true}')
            with self.assertRaises(FrontendBuildError):
                verify_frontend(self.static)
            opening_schema.write_text("{}")
            awareness_schema.write_text('{"changed":true}')
            with self.assertRaises(FrontendBuildError):
                verify_frontend(self.static)
            awareness_schema.write_text("{}")
            monitoring_schema.write_text('{"changed":true}')
            with self.assertRaises(FrontendBuildError):
                verify_frontend(self.static)

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

    def test_trading_guide_direct_link_serves_frontend(self):
        try:
            from fastapi.testclient import TestClient
            from dashboard.app import create_app
        except ModuleNotFoundError:
            self.skipTest("Install dashboard development dependencies for factory checks")
        from test_dashboard_api import MemoryEventLoop

        with patch("dashboard.app.verify_frontend", return_value=self.static):
            app = create_app(static_directory=self.static)
        with TestClient(app, base_url="http://localhost", backend_options={"loop_factory": MemoryEventLoop}) as client:
            response = client.get("/guide")
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
        self.assertTrue(factory.call_args.kwargs["setup_enabled"])
        self.assertIn("/setup", output.getvalue())
        self.assertFalse(factory.call_args.kwargs["investment_recording_enabled"])

    def test_explicit_workspace_is_mutually_exclusive_with_other_sources(self):
        for flag in ("--demo", "--database", "--investment-database"):
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
        factory.assert_called_once_with(database=None, scenario="healthy_recorded", telemetry_path=None,
                                        static_directory=None, serve_frontend=False,
                                        intelligence_database=None, intelligence_demo=False,
                                        analysis_controls_enabled=False, analysis_profiles=(), investment_database=None,
                                        investment_recording_enabled=False, awareness_profile=None,
                                        monitoring_profile=None, monitoring_cache=None,
                                        monitoring_reviews=None, monitoring_workflow_enabled=False)
        server.assert_called_once_with("synthetic-app", host="127.0.0.1", port=8766, proxy_headers=False, access_log=False)

    def test_invalid_modes_and_ports_fail_before_runtime_imports(self):
        for args in (["--demo", "--telemetry", "unused"], ["--demo", "--port", "80"],
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
        self.assertIsNone(factory.call_args.kwargs["database"])
        self.assertIn("/investment", output.getvalue())
        self.assertFalse((self.root / "investment.sqlite3").exists())

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
        self.assertFalse(factory.call_args.kwargs["investment_recording_enabled"])
        self.assertFalse((self.root / "investment.sqlite3").exists())


if __name__ == "__main__":
    unittest.main()