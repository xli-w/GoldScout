"""Shared connection configuration without import-time IO."""

import base64
from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import secrets
import stat
import threading
from types import MappingProxyType
from typing import Dict, Mapping, Optional, TextIO, Tuple
from urllib.parse import unquote, urlsplit

from dotenv.parser import parse_stream


DOTENV_PATH = Path(__file__).parent / ".env"
CONNECTIONS_PATH = Path(__file__).parent / ".goldscout-connections.json"
CONNECTION_NAMES = (
    "GOLDSCOUT_ETORO_API_KEY", "GOLDSCOUT_ETORO_USER_KEY",
    "TRADER_AI_OPENAI_KEY", "TRADER_AI_ANTHROPIC_KEY", "TRADER_AI_GEMINI_KEY",
    "TRADER_PROXY_URL", "TRADER_DNS_IPV4",
)
_CONNECTION_FILE_LOCK = threading.RLock()


class ProxyConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class HTTPProxy:
    url: str = field(repr=False)
    host: str
    port: int
    authorization: Optional[str] = field(repr=False)


@dataclass(frozen=True, repr=False)
class ConnectionSnapshot:
    """Effective values and their revision; never include either in a repr."""

    revision: int
    values: Mapping[str, str] = field(repr=False)

    def __post_init__(self):
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))


def _dotenv_loading_disabled() -> bool:
    return os.getenv("PYTHON_DOTENV_DISABLED", "").casefold() in {"1", "true", "t", "yes", "y"}


def connection_settings_enabled(path=None) -> bool:
    selected = Path(path or CONNECTIONS_PATH).absolute()
    return not (_dotenv_loading_disabled() and selected == CONNECTIONS_PATH.absolute())


def _parse_proxy_dotenv(stream: TextIO, names: Tuple[str, ...]) -> Dict[str, str]:
    values = {}
    insensitive = os.name == "nt"
    for binding in parse_stream(stream):
        if binding.error:
            raise ProxyConfigurationError("Cannot safely read malformed connection configuration")
        if binding.key is not None:
            for name in names:
                match = binding.key.upper() == name.upper() if insensitive else binding.key == name
                if match:
                    if binding.value is None:
                        raise ProxyConfigurationError(f"{name} requires an explicit value")
                    values[name] = binding.value
    return values


def _read_connection_file(path: Path, names: Tuple[str, ...]) -> Dict[str, str]:
    try:
        path.lstat()
    except FileNotFoundError:
        return {}
    except OSError:
        raise ProxyConfigurationError("Cannot safely read connection configuration") from None
    try:
        with path.open(encoding="utf-8") as stream:
            return _parse_proxy_dotenv(stream, names)
    except (OSError, UnicodeError):
        raise ProxyConfigurationError("Cannot safely read connection configuration") from None


def _read_proxy_dotenv(names: Tuple[str, ...]) -> Dict[str, str]:
    # All connection reads pass through the offline runner's existing guard.
    return _read_connection_file(DOTENV_PATH, names)


def _connection_defaults():
    return {
        "schema_version": 1,
        "revision": 0,
        "values": {},
        "versions": {name: 0 for name in CONNECTION_NAMES},
    }


def _parse_connection_settings(raw: bytes) -> dict:
    if len(raw) > 32768:
        raise ProxyConfigurationError("Private connection settings exceed their supported size")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs)
    except (ValueError, UnicodeError, RecursionError):
        raise ProxyConfigurationError("Private connection settings are unreadable") from None
    legacy_names = set(CONNECTION_NAMES) - {"TRADER_DNS_IPV4"}
    if (type(value) is not dict or set(value) != {"schema_version", "revision", "values", "versions"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or type(value["revision"]) is not int or value["revision"] < 0
            or type(value["values"]) is not dict or type(value["versions"]) is not dict
            or set(value["values"]) - set(CONNECTION_NAMES)
            or set(value["versions"]) not in (legacy_names, set(CONNECTION_NAMES))
            or any(type(version) is not int or version < 0
                   for version in value["versions"].values())):
        raise ProxyConfigurationError("Private connection settings are invalid")
    if set(value["versions"]) == legacy_names:
        value["versions"]["TRADER_DNS_IPV4"] = 0
    for name, secret in value["values"].items():
        _validate_connection_value(name, secret)
    return value


def _validate_connection_value(name: str, secret: str):
    if (not isinstance(secret, str) or len(secret) > 4096
            or any(ord(char) < 32 or ord(char) == 127 for char in secret)):
        raise ProxyConfigurationError("Private connection settings are invalid")
    if name == "TRADER_PROXY_URL":
        parse_proxy_url(secret)
    elif name == "TRADER_DNS_IPV4":
        if secret:
            try:
                address = ipaddress.ip_address(secret.strip())
            except ValueError:
                raise ProxyConfigurationError(
                    "TRADER_DNS_IPV4 must be a unicast IPv4 address") from None
            if (not isinstance(address, ipaddress.IPv4Address) or address.is_unspecified
                    or address.is_multicast or address.is_reserved):
                raise ProxyConfigurationError(
                    "TRADER_DNS_IPV4 must be a unicast IPv4 address")
    elif not secret or any(not 33 <= ord(char) <= 126 for char in secret):
        raise ProxyConfigurationError("Private connection settings are invalid")


def _unique_pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate key")
        value[key] = item
    return value


def _connection_path_identity(path: Path):
    from dashboard.workspace import path_identity

    path_identity(path)


def _preserve_windows_security(source: Path, destination: Path):
    if os.name != "nt":
        return
    try:
        source.lstat()
    except FileNotFoundError:
        return
    import ctypes
    from ctypes import wintypes

    security_information = 0x00000001 | 0x00000002 | 0x00000004
    api = ctypes.WinDLL("advapi32", use_last_error=True)
    get_security = api.GetFileSecurityW
    get_security.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, ctypes.POINTER(wintypes.DWORD))
    get_security.restype = wintypes.BOOL
    set_security = api.SetFileSecurityW
    set_security.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPVOID)
    set_security.restype = wintypes.BOOL
    def descriptor_for(path):
        required = wintypes.DWORD()
        get_security(str(path), security_information, None, 0, ctypes.byref(required))
        if not required.value:
            raise OSError(ctypes.get_last_error(), "Unable to inspect connection file permissions")
        descriptor = ctypes.create_string_buffer(required.value)
        if not get_security(str(path), security_information, descriptor,
                            required.value, ctypes.byref(required)):
            raise OSError(ctypes.get_last_error(), "Unable to inspect connection file permissions")
        return descriptor, required.value

    descriptor, size = descriptor_for(source)
    if not set_security(str(destination), security_information, descriptor):
        raise OSError(ctypes.get_last_error(), "Unable to preserve connection file permissions")
    copied, copied_size = descriptor_for(destination)
    if copied_size != size or copied.raw[:size] != descriptor.raw[:size]:
        raise OSError("Connection file permissions could not be verified")


def read_connection_settings(path=None) -> dict:
    selected = Path(path or CONNECTIONS_PATH).absolute()
    if _dotenv_loading_disabled() and selected == CONNECTIONS_PATH.absolute():
        return _connection_defaults()
    with _CONNECTION_FILE_LOCK:
        try:
            _connection_path_identity(selected)
            try:
                info = selected.lstat()
            except FileNotFoundError:
                return _connection_defaults()
            if not stat.S_ISREG(info.st_mode) or info.st_size > 32768:
                raise ProxyConfigurationError("Private connection settings are unavailable")
            with selected.open("rb") as handle:
                return _parse_connection_settings(handle.read(32769))
        except ProxyConfigurationError:
            raise
        except (OSError, ValueError):
            raise ProxyConfigurationError("Private connection settings are unavailable") from None


@contextmanager
def _connection_file_lock(path: Path):
    lock_path = path.with_name(path.name + ".lock")
    descriptor = None
    locked = False
    try:
        _connection_path_identity(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        _connection_path_identity(lock_path)
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
    except ProxyConfigurationError:
        raise
    except (OSError, ValueError) as exc:
        raise ProxyConfigurationError("Private connection settings could not be locked or saved") from exc
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


def connection_revision(*names: str, path=None) -> int:
    selected = Path(path or CONNECTIONS_PATH).absolute()
    if not names or any(name not in CONNECTION_NAMES for name in names):
        raise ProxyConfigurationError("Connection revision request is invalid")
    private_sources_disabled = (
        _dotenv_loading_disabled() and selected == CONNECTIONS_PATH.absolute())
    settings = _connection_defaults() if private_sources_disabled else read_connection_settings(selected)
    values = configured_connection_values(*names, connections_path=selected)
    return int.from_bytes(
        hashlib.sha256(
            json.dumps(
                [[settings["versions"][name] for name in names],
                 [values[name] for name in names]],
                separators=(",", ":"),
            ).encode("utf-8")
        ).digest()[:8], "big")


def connection_snapshot(*names: str, path=None) -> ConnectionSnapshot:
    """Resolve effective values once and derive the revision from those same values."""
    selected = Path(path or CONNECTIONS_PATH).absolute()
    if not names or any(name not in CONNECTION_NAMES for name in names):
        raise ProxyConfigurationError("Connection revision request is invalid")
    disabled = _dotenv_loading_disabled()
    private_sources_disabled = disabled and selected == CONNECTIONS_PATH.absolute()
    settings = _connection_defaults() if private_sources_disabled else read_connection_settings(selected)
    dotenv = {} if disabled else _read_proxy_dotenv(names) or {}
    saved = settings["values"]
    environment = {name: os.environ.get(name, "") for name in names
                   if name not in saved and name not in dotenv}
    values = {name: saved[name] if name in saved else
              dotenv[name] if name in dotenv else environment.get(name, "") for name in names}
    revision = int.from_bytes(
        hashlib.sha256(json.dumps(
            [[settings["versions"][name] for name in names],
             [values[name] for name in names]], separators=(",", ":")
        ).encode("utf-8")).digest()[:8], "big")
    return ConnectionSnapshot(revision, values)


def update_connection_settings(updates: dict, clear_names=(), *,
                               expected_revision: int, path=None) -> dict:
    selected = Path(path or CONNECTIONS_PATH).absolute()
    if _dotenv_loading_disabled() and selected == CONNECTIONS_PATH.absolute():
        raise ProxyConfigurationError(
            "Private connection settings are disabled by PYTHON_DOTENV_DISABLED")
    if (type(updates) is not dict or set(updates) - set(CONNECTION_NAMES)
            or type(clear_names) not in (list, tuple, set)
            or any(name not in CONNECTION_NAMES for name in clear_names)
            or set(updates) & set(clear_names)
            or type(expected_revision) is not int or expected_revision < 0):
        raise ProxyConfigurationError("Private connection update is invalid")
    for name, secret in updates.items():
        _validate_connection_value(name, secret)
    with _CONNECTION_FILE_LOCK:
        with _connection_file_lock(selected):
            current = read_connection_settings(selected)
            if current["revision"] != expected_revision:
                raise ProxyConfigurationError("Private connection settings changed; reload before saving")
            changed = set(clear_names)
            changed.update(name for name, value in updates.items()
                           if current["values"].get(name) != value)
            if not changed:
                return {**current, "changed_names": []}
            value = {
                "schema_version": 1,
                "revision": current["revision"] + 1,
                "values": {**current["values"], **updates},
                "versions": dict(current["versions"]),
            }
            for name in clear_names:
                value["values"].pop(name, None)
            for name in changed:
                value["versions"][name] += 1
            encoded = json.dumps(value, ensure_ascii=True, sort_keys=True,
                                 separators=(",", ":")).encode("utf-8")
            temporary = selected.with_name(f"{selected.name}.{secrets.token_hex(16)}.tmp")
            descriptor = None
            try:
                _connection_path_identity(selected)
                descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                _preserve_windows_security(selected, temporary)
                with os.fdopen(descriptor, "wb") as handle:
                    descriptor = None
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                _connection_path_identity(selected)
                os.replace(temporary, selected)
            except (OSError, ValueError) as exc:
                raise ProxyConfigurationError("Private connection settings could not be saved") from exc
            finally:
                if descriptor is not None:
                    os.close(descriptor)
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass
            return {**value, "changed_names": sorted(changed)}


def configured_connection_values(*names: str, connections_path=None) -> Dict[str, str]:
    if any(name not in CONNECTION_NAMES and not (
            name.startswith("TRADER_AI_") and name.replace("_", "").isalnum())
           for name in names):
        raise ProxyConfigurationError("Connection setting name is invalid")
    selected = Path(connections_path or CONNECTIONS_PATH).absolute()
    if (_dotenv_loading_disabled() and selected == CONNECTIONS_PATH.absolute()):
        return {name: os.getenv(name, "") for name in names}
    saved = read_connection_settings(selected)["values"]
    values = {} if _dotenv_loading_disabled() else _read_proxy_dotenv(names) or {}
    return {name: saved[name] if name in saved else
            values[name] if name in values else os.getenv(name, "") for name in names}


def configured_proxy_url() -> str:
    return configured_connection_values("TRADER_PROXY_URL")["TRADER_PROXY_URL"]


def parse_proxy_url(value: str) -> Optional[HTTPProxy]:
    message = "TRADER_PROXY_URL must be an HTTP proxy URL with a valid host and port"
    if not isinstance(value, str):
        raise ProxyConfigurationError(message)
    value = value.strip()
    if not value:
        return None
    if "\\" in value or any(ord(char) < 33 or ord(char) == 127 for char in value):
        raise ProxyConfigurationError(message)
    try:
        parsed = urlsplit(value)
        host, port = parsed.hostname, parsed.port
    except ValueError:
        raise ProxyConfigurationError(message) from None
    if (parsed.scheme != "http" or not host or parsed.path not in ("", "/")
            or parsed.query or parsed.fragment or "?" in value or "#" in value
            or parsed.netloc.rsplit("@", 1)[-1].endswith(":")
            or (port is not None and not 1 <= port <= 65535)):
        raise ProxyConfigurationError(message)

    authorization = None
    if parsed.username is not None:
        if parsed.password is None:
            raise ProxyConfigurationError("TRADER_PROXY_URL requires an explicit password with a username")
        try:
            username = unquote(parsed.username, errors="strict")
            password = unquote(parsed.password or "", errors="strict")
            if not username:
                raise ProxyConfigurationError(message)
            encoded = base64.b64encode(f"{username}:{password}".encode("latin-1")).decode("ascii")
        except UnicodeError:
            raise ProxyConfigurationError("TRADER_PROXY_URL credentials must be URL-encoded and Latin-1 compatible") from None
        authorization = f"Basic {encoded}"
    return HTTPProxy(value, host, port if port is not None else 80, authorization)
