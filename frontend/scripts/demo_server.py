"""Own a loopback-only synthetic dashboard for an explicitly requested browser run."""

import os
from pathlib import Path
import socket
import tempfile


def main():
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    for name in tuple(os.environ):
        if name.upper().startswith(("GOLDSCOUT_ETORO_", "TRADER_AI_", "ALPACA_", "APCA_")):
            os.environ[name] = ""
    os.environ["TRADER_PROXY_URL"] = ""
    os.environ["TRADER_DNS_IPV4"] = ""

    import uvicorn
    from dashboard.app import create_app

    class SyntheticServer(uvicorn.Server):
        async def startup(self, sockets=None):
            await super().startup(sockets=sockets)
            if self.started:
                print(f"GOLDSCOUT_SYNTHETIC_PORT={sockets[0].getsockname()[1]}", flush=True)

    with tempfile.TemporaryDirectory(prefix="goldscout-browser-") as directory:
        app = create_app(intelligence_demo=True, connections_path=Path(directory) / "connections.json")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(128)
            server = SyntheticServer(uvicorn.Config(app, access_log=False))
            server.run(sockets=[listener])


if __name__ == "__main__":
    main()
