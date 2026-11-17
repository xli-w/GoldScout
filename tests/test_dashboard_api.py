import asyncio
import selectors
import threading
import unittest

from dashboard.app import create_app
from fastapi.testclient import TestClient


class MemorySelector(selectors.BaseSelector):
    """ASGI scheduling only: file descriptors and network IO are forbidden."""

    def __init__(self):
        self.ready = threading.Event()

    def register(self, fileobj, events, data=None):
        raise AssertionError("ASGI tests must not register network/file IO")

    def unregister(self, fileobj):
        raise AssertionError("ASGI tests must not register network/file IO")

    def select(self, timeout=None):
        self.ready.wait(timeout)
        self.ready.clear()
        return []

    def get_map(self):
        return {}


class MemoryEventLoop(asyncio.SelectorEventLoop):
    """Replace asyncio's socket wakeup with an in-memory threading event."""

    def __init__(self):
        super().__init__(selector=MemorySelector())

    def _make_self_pipe(self):
        pass

    def _close_self_pipe(self):
        pass

    def _write_to_self(self):
        self._selector.ready.set()


class PortfolioAPITests(unittest.TestCase):
    def client(self):
        return TestClient(create_app(serve_frontend=False), base_url="http://localhost",
                          backend_options={"loop_factory": MemoryEventLoop})

    def test_only_portfolio_demo_is_available_and_trading_routes_are_retired(self):
        with self.client() as client:
            self.assertEqual(client.get("/api/v1/settings").json(),
                             {"mode": "demo", "workspace": "demo"})
            report = client.get("/api/investment/v1/snapshot")
            self.assertEqual(report.status_code, 200, report.text)
            self.assertEqual(report.json()["schema_version"], 2)
            self.assertFalse(report.json()["proposal"]["execution_enabled"])
            for path in ("/api/v1/overview", "/api/v1/positions", "/api/v1/health",
                         "/api/v1/events", "/api/v1/orders", "/api/v1/performance",
                         "/legacy", "/positions", "/guide"):
                with self.subTest(path=path):
                    self.assertEqual(client.get(path).status_code, 404)

    def test_local_origin_and_mutation_boundaries_remain_enforced(self):
        with self.client() as client:
            self.assertEqual(client.get("/api/v1/settings", headers={"Host": "remote.invalid"}).status_code, 400)
            self.assertEqual(client.get("/api/v1/settings", headers={"Origin": "https://remote.invalid"}).status_code, 403)
            for path in ("/api/v1/orders", "/api/investment/v1/recording/confirm"):
                self.assertEqual(client.post(path, json={}, headers={"Origin": "http://localhost"}).status_code, 405)
            response = client.get("/api/investment/v1/snapshot")
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])
