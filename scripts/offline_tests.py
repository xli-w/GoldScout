"""Run regression tests without dotenv credentials or Python socket access."""

import argparse
from contextlib import ExitStack, contextmanager
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def refuse_network(*args, **kwargs):
    raise RuntimeError("Network access is disabled by the offline test runner")


@contextmanager
def offline_environment():
    environment = {
        "ALPACA_API_KEY": "",
        "ALPACA_SECRET_KEY": "",
        "APCA_API_KEY_ID": "",
        "APCA_API_SECRET_KEY": "",
        "ALPACA_BASE_URL": "https://paper-api.alpaca.markets",
        "CONFIRM_LIVE_TRADING": "",
        "TRADER_PROXY_URL": "",
        "TRADER_DNS_IPV4": "",
        "PYTHON_DOTENV_DISABLED": "1",
        "MPLBACKEND": "Agg",
    }
    environment.update({
        name: "" for name in os.environ
        if name.upper().startswith(("GOLDSCOUT_ETORO_", "TRADER_AI_"))
    })
    with tempfile.TemporaryDirectory(prefix="trader-offline-") as directory, ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, environment))
        targets = [f"socket.{name}" for name in (
            "create_connection", "getaddrinfo", "gethostbyname", "gethostbyname_ex",
            "gethostbyaddr", "getnameinfo",
        )]
        targets.extend(f"socket.socket.{name}" for name in (
            "connect", "connect_ex", "send", "sendall", "sendto", "sendmsg",
            "bind", "listen", "accept",
        ) if hasattr(socket.socket, name))
        for target in targets:
            stack.enter_context(patch(target, refuse_network))
        stack.enter_context(patch("dotenv.load_dotenv", return_value=False))
        stack.enter_context(patch("dotenv.dotenv_values", return_value={}))
        stack.enter_context(patch("proxy_config._read_proxy_dotenv", return_value=""))
        temporary_root = Path(directory)
        yield temporary_root


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pattern", default="test_*.py", help="unittest discovery filename pattern")
    arguments = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    with offline_environment():
        suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern=arguments.pattern)
        if suite.countTestCases() == 0:
            parser.error("No tests matched the pattern")
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())