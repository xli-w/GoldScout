"""Validate generated frontend artifacts without importing runtime dependencies."""

import hashlib
import json
from pathlib import Path, PurePosixPath


class FrontendBuildError(ValueError):
    pass


def frontend_directory():
    return Path(__file__).resolve().parent / "static"


def verify_frontend(directory=None, schema=None):
    directory = Path(directory) if directory else frontend_directory()
    investment_schema = Path(__file__).resolve().parent.parent / "investment" / "response-v2.schema.json"
    schema = Path(schema) if schema else investment_schema
    try:
        if directory.is_symlink() or (directory / "dashboard-build.json").is_symlink():
            raise FrontendBuildError("Frontend build must not contain symbolic links")
        manifest = json.loads((directory / "dashboard-build.json").read_text(encoding="utf-8"))
        if manifest.get("version") != 1 or manifest.get("schema_sha256") != hashlib.sha256(schema.read_bytes()).hexdigest():
            raise FrontendBuildError("Frontend contract does not match this dashboard")
        if schema == investment_schema:
            setup_schema = Path(__file__).with_name("setup.schema.json")
            if manifest.get("setup_schema_sha256") != hashlib.sha256(setup_schema.read_bytes()).hexdigest():
                raise FrontendBuildError("Frontend setup contract does not match this dashboard")
            awareness_schema = investment_schema.with_name("awareness.schema.json")
            if manifest.get("awareness_schema_sha256") != hashlib.sha256(awareness_schema.read_bytes()).hexdigest():
                raise FrontendBuildError("Frontend portfolio awareness contract does not match this dashboard")
            monitoring_schema = investment_schema.with_name("monitoring.schema.json")
            if manifest.get("monitoring_schema_sha256") != hashlib.sha256(monitoring_schema.read_bytes()).hexdigest():
                raise FrontendBuildError("Frontend monitoring contract does not match this dashboard")
        files = manifest.get("files")
        if not isinstance(files, dict) or "index.html" not in files or not any(name.startswith("assets/") for name in files):
            raise FrontendBuildError("Frontend build is incomplete")
        root = directory.resolve()
        actual = set()
        for candidate in directory.rglob("*"):
            if candidate.is_symlink():
                raise FrontendBuildError("Frontend build must not contain symbolic links")
            if candidate.is_file():
                actual.add(candidate.relative_to(directory).as_posix())
        if actual != set(files) | {"dashboard-build.json"}:
            raise FrontendBuildError("Frontend build contains unverified or missing files")
        for name, digest in files.items():
            relative = PurePosixPath(name)
            if (relative.is_absolute() or ".." in relative.parts or "\\" in name
                    or name != "index.html" and (not relative.parts or relative.parts[0] != "assets")):
                raise FrontendBuildError("Frontend manifest contains an invalid asset path")
            asset = (directory / name).resolve()
            if not asset.is_relative_to(root) or hashlib.sha256(asset.read_bytes()).hexdigest() != digest:
                raise FrontendBuildError("Frontend asset is missing or changed")
        return directory
    except FrontendBuildError:
        raise
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        raise FrontendBuildError("Frontend build is missing or invalid") from exc


def build_guidance():
    checkout = Path(__file__).resolve().parent.parent
    if (checkout / "frontend" / "package.json").is_file():
        return "Run npm --prefix frontend ci and npm --prefix frontend run build first, or use --api-only for development."
    return "Reinstall a dashboard wheel containing its built frontend, or use --api-only for development."