"""Local dashboard preferences, separate from journal and provider evidence."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import stat
import threading

from dashboard.workspace import path_identity


INTERVALS = (7, 14, 30, 90, 180, 365)
DEFAULT_PREFERENCES = {
    "compact_mode": False,
    "review_interval_days": 30,
    "savings_terms_interval_days": 90,
}
MAX_SETTINGS_BYTES = 8192


class SettingsError(ValueError):
    def __init__(self, code, status=400):
        super().__init__(code)
        self.code = code
        self.status = status


def _read(path):
    path = Path(path).absolute()
    try:
        path_identity(path)
        try:
            info = path.lstat()
        except FileNotFoundError:
            return {"schema_version": 1, "revision": 0, **DEFAULT_PREFERENCES}
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_SETTINGS_BYTES:
            raise SettingsError("settings_unavailable", 503)
        with path.open("rb") as handle:
            value = json.loads(handle.read(MAX_SETTINGS_BYTES + 1).decode("utf-8"))
    except SettingsError:
        raise
    except (OSError, ValueError, UnicodeError, RecursionError):
        raise SettingsError("settings_unavailable", 503) from None
    if (type(value) is not dict
            or set(value) != {"schema_version", "revision", *DEFAULT_PREFERENCES}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or type(value["revision"]) is not int or value["revision"] < 0
            or type(value["compact_mode"]) is not bool
            or type(value["review_interval_days"]) is not int
            or value["review_interval_days"] not in INTERVALS
            or type(value["savings_terms_interval_days"]) is not int
            or value["savings_terms_interval_days"] not in INTERVALS):
        raise SettingsError("settings_invalid", 503)
    return value


@contextmanager
def _file_lock(path):
    lock_path = path.with_name(path.name + ".lock")
    descriptor = None
    locked = False
    try:
        path_identity(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path_identity(lock_path)
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(lock_path, flags, 0o600)
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"\0")
            os.fsync(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        locked = True
        yield
    except SettingsError:
        raise
    except (OSError, ValueError) as exc:
        raise SettingsError("settings_save_failed", 503) from exc
    finally:
        if descriptor is not None:
            try:
                if locked:
                    if os.name == "nt":
                        import msvcrt
                        os.lseek(descriptor, 0, os.SEEK_SET)
                        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


class LocalSettings:
    def __init__(self, path):
        self.path = Path(path).absolute()
        self.lock = threading.RLock()

    def read(self):
        with self.lock:
            return _read(self.path)

    def save_preferences(self, expected_revision, preferences):
        if (type(expected_revision) is not int or expected_revision < 0
                or type(preferences) is not dict
                or set(preferences) != set(DEFAULT_PREFERENCES)
                or type(preferences["compact_mode"]) is not bool
                or type(preferences["review_interval_days"]) is not int
                or preferences["review_interval_days"] not in INTERVALS
                or type(preferences["savings_terms_interval_days"]) is not int
                or preferences["savings_terms_interval_days"] not in INTERVALS):
            raise SettingsError("settings_invalid", 400)
        with self.lock:
            with _file_lock(self.path):
                current = _read(self.path)
                if current["revision"] != expected_revision:
                    raise SettingsError("settings_changed", 409)
                if all(current[key] == preferences[key] for key in DEFAULT_PREFERENCES):
                    return current
                value = {
                    "schema_version": 1,
                    "revision": current["revision"] + 1,
                    **preferences,
                }
                self._write(value)
                return value

    def _write(self, value):
        try:
            path_identity(self.path)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            path_identity(self.path)
        except (OSError, ValueError):
            raise SettingsError("settings_save_failed", 503) from None
        temporary = self.path.with_name(f"{self.path.name}.{secrets.token_hex(16)}.tmp")
        try:
            descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())
                handle.flush()
                os.fsync(handle.fileno())
            path_identity(self.path)
            os.replace(temporary, self.path)
        except (OSError, ValueError):
            raise SettingsError("settings_save_failed", 503) from None
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
