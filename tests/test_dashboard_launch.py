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
        investment_schema = self.root / "investment" / "response.schema.json"
        investment_schema.parent.mkdir()
        investment_schema.write_text("{}")
        opening_schema = investment_schema.with_name("response-v2.schema.json")
        opening_schema.write_text("{}")
        self.manifest["investment_schema_sha256"] = self.digest(investment_schema)
        self.manifest["investment_v2_schema_sha256"] = self.digest(opening_schema)
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

    def test_launch_requires_explicit_source_and_production_bundle(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as result:
            main([])
        self.assertEqual(result.exception.code, 2)
        with patch("dashboard.__main__.verify_frontend", side_effect=FrontendBuildError("Missing build")):
            with redirect_stderr(io.StringIO()) as output, self.assertRaises(SystemExit):
                main(["--demo"])
        self.assertIn("npm --prefix frontend run build", output.getvalue())

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
                                        analysis_password=None, analysis_profiles=(), investment_database=None)
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


if __name__ == "__main__":
    unittest.main()