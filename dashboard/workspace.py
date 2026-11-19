"""Explicit workspace selection and bounded, read-only startup classification."""

import os
from pathlib import Path
import stat

from investment import InvestmentError


DATABASE = "investment.sqlite3"
PROFILE = "profile.json"
SETUP = ".investment-setup"
ETORO = ".etoro"


def resolve_workspace(explicit=None):
    """Anchor the default to this checkout, never to cwd or site-packages."""
    if explicit is not None:
        return Path(os.path.abspath(os.path.expanduser(os.fspath(explicit))))
    root = Path(__file__).absolute().parent.parent
    if not ((root / ".git").exists() and (root / "pyproject.toml").is_file()
            and (root / "investment").is_dir()):
        raise InvestmentError("Outside a source checkout; select --workspace explicitly")
    return root / "local-investment"


def path_identity(path):
    """Reject existing reparse points, including junctions, in every ancestor."""
    path = resolve_workspace(path)
    identities = []
    for item in reversed((path, *path.parents)):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if (stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & 0x400):
            raise InvestmentError("Workspace path contains a symlink or reparse point")
        identities.append([str(item), info.st_dev, info.st_ino])
    return identities


def directory_entries(path):
    """Refuse unbounded directories; do not follow their entries."""
    names = []
    with os.scandir(path) as entries:
        for entry in entries:
            if len(names) >= 16:
                raise InvestmentError("Workspace contains too many entries")
            path_identity(Path(path) / entry.name)
            names.append(entry.name)
    return set(names)


def workspace_status(path, now=None):
    """Never create, repair, adopt or fall back from an unavailable workspace."""
    from investment.bootstrap import inspect_workspace

    path = resolve_workspace(path)
    result = {"state": "blocked", "workspace": str(path),
              "database": str(path / DATABASE), "profile": str(path / PROFILE),
              "revision": None, "reason": "", "message": "", "issues": []}
    try:
        result.update(inspect_workspace(path, now))
    except (InvestmentError, OSError, ValueError, TypeError) as exc:
        result["reason"] = str(exc) or "Workspace is unavailable"
    result["message"] = result["reason"]
    return result
