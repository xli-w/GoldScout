"""Shared HTTP proxy configuration without import-time IO."""

import base64
from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Optional, TextIO
from urllib.parse import unquote, urlsplit

from dotenv.parser import parse_stream


DOTENV_PATH = Path(__file__).parent / ".env"


class ProxyConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class HTTPProxy:
    url: str = field(repr=False)
    host: str
    port: int
    authorization: Optional[str] = field(repr=False)


def _dotenv_loading_disabled() -> bool:
    return os.getenv("PYTHON_DOTENV_DISABLED", "").casefold() in {"1", "true", "t", "yes", "y"}


def _parse_proxy_dotenv(stream: TextIO) -> str:
    value = ""
    for binding in parse_stream(stream):
        if binding.error:
            raise ProxyConfigurationError("Cannot safely read TRADER_PROXY_URL from malformed dotenv configuration")
        if binding.key == "TRADER_PROXY_URL":
            if binding.value is None:
                raise ProxyConfigurationError("TRADER_PROXY_URL requires an explicit dotenv value")
            value = binding.value
    return value


def _read_proxy_dotenv() -> str:
    try:
        with DOTENV_PATH.open(encoding="utf-8") as stream:
            return _parse_proxy_dotenv(stream)
    except FileNotFoundError:
        return ""


def configured_proxy_url() -> str:
    value = os.getenv("TRADER_PROXY_URL")
    if value is not None:
        return value
    if _dotenv_loading_disabled():
        return ""
    return _read_proxy_dotenv()


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
