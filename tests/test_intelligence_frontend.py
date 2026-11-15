from datetime import datetime,timezone
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from dashboard.app import create_app
from fastapi.testclient import TestClient
from test_dashboard_api import MemoryEventLoop
from market_intelligence.store import initialize


class IntelligenceAPITests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path=Path(self.directory.name)/"intelligence.sqlite3"
        initialize(self.path,datetime.now(timezone.utc))

    def client(self,demo=False):
        return TestClient(create_app(serve_frontend=False,intelligence_demo=demo,
                           intelligence_database=None if demo else self.path,
                           analysis_password=None if demo else "test-password",analysis_profiles=("local",)),
                          base_url="http://127.0.0.1",backend_options={"loop_factory":MemoryEventLoop})

    def test_auth_origin_csrf_and_trading_routes_are_isolated(self):
        with self.client() as client:
            session=client.get("/api/v1/intelligence/session").json()
            snapshot=client.get("/api/v1/intelligence").json()
            settings={key:value for key,value in snapshot["settings"].items() if key!="revision"}
            payload={"request_id":"request-1","database_id":snapshot["database_id"],"base_revision":0,"settings":settings}
            headers={"Origin":"http://127.0.0.1","X-CSRF-Token":session["csrf"]}
            self.assertEqual(client.post("/api/v1/intelligence/controls",json=payload,headers=headers).status_code,403)
            self.assertEqual(client.post("/api/v1/intelligence/login",json={"password":"test-password"},headers=headers).status_code,200)
            self.assertEqual(client.post("/api/v1/intelligence/controls",json=payload).status_code,403)
            self.assertEqual(client.post("/api/v1/intelligence/controls",json=payload,headers={**headers,"Origin":"https://attacker.invalid"}).status_code,403)
            response=client.post("/api/v1/intelligence/controls",json=payload,headers=headers)
            self.assertEqual(response.status_code,202)
            self.assertEqual(response.json()["status"],"pending")
            self.assertEqual(client.post("/api/v1/orders",json={},headers=headers).status_code,405)
            payload["settings"]["risk_per_trade_pct"]=1
            self.assertEqual(client.post("/api/v1/intelligence/controls",json=payload,headers=headers).status_code,409)

    def test_demo_controls_apply_only_to_temporary_synthetic_workspace(self):
        with self.client(demo=True) as client:
            session=client.get("/api/v1/intelligence/session").json()
            snapshot=client.get("/api/v1/intelligence").json()
            self.assertEqual(snapshot["source_mode"],"demo")
            settings={key:value for key,value in snapshot["settings"].items() if key!="revision"}
            settings.update(enabled=True,ai_enabled=True,ai_profile="demo")
            payload={"request_id":"demo-command","database_id":snapshot["database_id"],"base_revision":snapshot["settings"]["revision"],"settings":settings}
            self.assertEqual(client.post("/api/v1/intelligence/controls",json=payload,headers={"Origin":"http://127.0.0.1","X-CSRF-Token":session["csrf"]}).status_code,202)
            response=client.get("/api/v1/intelligence")
            self.assertEqual(response.status_code,200)
            self.assertEqual(len(response.json()["briefs"]),1)
            self.assertEqual(response.json()["budget"]["calls"],1)

    def test_ai_capability_is_not_exposed_for_pre_v4_store(self):
        from market_intelligence.store import initialize
        older=Path(self.directory.name)/"version-three.sqlite3"
        initialize(older,datetime.now(timezone.utc),schema_version=3)
        client=TestClient(create_app(serve_frontend=False,intelligence_database=older,analysis_password="test-password"),
                          base_url="http://127.0.0.1",backend_options={"loop_factory":MemoryEventLoop})
        with client:
            snapshot=client.get("/api/v1/intelligence").json()
            self.assertFalse(snapshot["ai_capable"])
            self.assertTrue(snapshot["news_capable"])
            for _ in range(3):
                response=client.get("/api/v1/intelligence")
                self.assertEqual(response.status_code,200,response.text)

    def test_observer_failure_does_not_change_order_call_sequence(self):
        import config
        from bot.main import TradingBot
        from bot.strategies.base import Signal,Action
        from bot.strategies.mean_reversion import MeanReversionStrategy
        calls=[]
        for observer in (None,Mock()):
            bot=TradingBot.__new__(TradingBot)
            bot.portfolio=Mock()
            bot.portfolio.positions={}
            bot.portfolio.get_account.return_value.equity="100000"
            bot.portfolio.daily_loss_exceeded.return_value=False
            bot.risk=Mock()
            bot.risk.correlation_filter.return_value=(True,"")
            bot._monitor_ready=True
            bot._last_monitor_started_monotonic=time.monotonic()
            bot.intelligence_observer=observer
            if observer is not None:
                observer.record.side_effect=RuntimeError("observer failed")
            bot._execute(Signal(Action.ENTER_LONG,"SPY","fixture"),MeanReversionStrategy(),config.INSTRUMENTS["SPY"],1,100)
            bot.portfolio.open_position.assert_called_once()
            calls.append([
                (call[0], call[1], {key: value for key, value in call[2].items() if key != "entry_guard"})
                for call in bot.portfolio.mock_calls
            ])
        self.assertEqual(calls[0],calls[1])

    def test_missing_observer_dependency_does_not_stop_bot_initialization(self):
        import sys
        import config
        from bot.main import TradingBot

        with patch.dict(sys.modules, {"psutil": None}), \
                patch.object(config, "ALPACA_API_KEY", "synthetic-key"), \
                patch.object(config, "ALPACA_SECRET_KEY", "synthetic-secret"), \
                patch("bot.main.create_api"), patch("bot.main.MarketData"), \
                patch("bot.main.Portfolio"), patch("bot.main.ThreadPoolExecutor"), \
                self.assertLogs("bot.intelligence", level="WARNING") as captured:
            bot = TradingBot()
            observer = bot.intelligence_observer
            self.assertIsNotNone(observer)
            self.addCleanup(observer.close)
            observer.thread.join(timeout=2)
            self.assertFalse(observer.thread.is_alive())
        self.assertTrue(bot.running)
        self.assertIn("Intelligence observation unavailable; trading unchanged", captured.output[0])

    def test_observer_reports_process_identity_failure(self):
        import sys
        from market_intelligence import bridge

        psutil = Mock()
        psutil.Process.side_effect = OSError("synthetic failure")
        with patch.dict(sys.modules, {"psutil": psutil}), \
                patch.object(bridge.threading, "Thread"), \
                self.assertLogs("bot.intelligence", level="WARNING") as captured:
            observer = bridge.BotIntelligenceObserver(self.path)
            observer._run()
        self.assertIn("Intelligence observation unavailable; trading unchanged", captured.output[0])
        self.assertNotIn("synthetic failure", captured.output[0])

    def test_observer_publishes_a_verifiable_revision(self):
        import importlib.util
        import json
        from market_intelligence import bridge
        from market_intelligence.store import IntelligenceStore

        if importlib.util.find_spec("psutil") is None:
            self.skipTest("Install requirements-intelligence.txt for observer process checks")
        store = IntelligenceStore(self.path)
        with patch.object(bridge.threading, "Thread"):
            observer = bridge.BotIntelligenceObserver(self.path)
            observer.record("SPY", "hold")
            with patch.object(observer.stop, "is_set",
                              side_effect=(False, False, False, False, False, False, True)):
                observer._run()
        observed = bridge.observed_revision(self.path, store.database_id, datetime.now(timezone.utc))
        self.assertEqual(observed, 0)
        payload = json.loads(self.path.with_suffix(".bot-observation.json").read_text(encoding="utf-8"))
        self.assertIs(payload["affects_trading"], False)

    def test_close_prevents_publication_after_a_blocked_store_read(self):
        import threading
        from market_intelligence import bridge
        from market_intelligence.store import IntelligenceStore

        entered = threading.Event()
        release = threading.Event()
        original_settings = IntelligenceStore.settings

        def blocked_settings(store):
            entered.set()
            release.wait(timeout=2)
            return original_settings(store)

        target = self.path.with_suffix(".bot-observation.json")
        with patch.object(IntelligenceStore, "settings", blocked_settings):
            observer = bridge.BotIntelligenceObserver(self.path)
            observer.record("SPY", "hold")
            self.assertTrue(entered.wait(timeout=2))
            with patch.object(observer.thread, "join"):
                observer.close()
            release.set()
            observer.thread.join(timeout=2)

        self.assertFalse(observer.thread.is_alive())
        self.assertFalse(target.exists())

    def test_close_is_bounded_and_late_replacement_is_not_observable(self):
        import threading
        import time
        from market_intelligence import bridge
        from market_intelligence.store import IntelligenceStore

        store = IntelligenceStore(self.path)
        entered = threading.Event()
        release = threading.Event()
        original_replace = bridge.os.replace
        target = self.path.with_suffix(".bot-observation.json")

        def blocked_replace(source, destination):
            if Path(destination) == target:
                entered.set()
                release.wait(timeout=3)
            return original_replace(source, destination)

        observer = bridge.BotIntelligenceObserver(self.path)
        observer.record("SPY", "hold")
        with patch.object(bridge.os, "replace", side_effect=blocked_replace):
            self.assertTrue(entered.wait(timeout=2))
            started = time.monotonic()
            observer.close()
            self.assertLess(time.monotonic() - started, 1.5)
            self.assertIsNone(bridge.observed_revision(
                self.path, store.database_id, datetime.now(timezone.utc)))
            release.set()
            observer.thread.join(timeout=2)

        self.assertFalse(observer.thread.is_alive())
        self.assertIsNone(bridge.observed_revision(
            self.path, store.database_id, datetime.now(timezone.utc)))

    def test_close_marker_rejects_late_replacement_when_status_cannot_be_invalidated(self):
        import threading
        import time
        from market_intelligence import bridge
        from market_intelligence.store import IntelligenceStore

        store = IntelligenceStore(self.path)
        entered = threading.Event()
        release = threading.Event()
        original_replace = bridge.os.replace
        original_unlink = Path.unlink
        target = self.path.with_suffix(".bot-observation.json")
        observer = bridge.BotIntelligenceObserver(self.path)

        def blocked_replace(source, destination):
            if Path(destination) == target:
                entered.set()
                release.wait(timeout=3)
            return original_replace(source, destination)

        def refuse_status_unlink(path, *args, **kwargs):
            if path == observer.status_path:
                raise PermissionError("synthetic status unlink failure")
            return original_unlink(path, *args, **kwargs)

        observer.record("SPY", "hold")
        with patch.object(bridge.os, "replace", side_effect=blocked_replace), \
                patch.object(observer, "_write_status",
                             side_effect=OSError("synthetic status update failure")), \
                patch.object(Path, "unlink", refuse_status_unlink):
            self.assertTrue(entered.wait(timeout=2))
            started = time.monotonic()
            observer.close()
            self.assertLess(time.monotonic() - started, 1.5)
            self.assertIsNone(bridge.observed_revision(
                self.path, store.database_id, datetime.now(timezone.utc)))
            release.set()
            observer.thread.join(timeout=2)

        self.assertFalse(observer.thread.is_alive())
        self.assertIsNone(bridge.observed_revision(
            self.path, store.database_id, datetime.now(timezone.utc)))

    def test_close_stays_bounded_when_close_marker_write_stalls(self):
        import threading
        import time
        from market_intelligence import bridge
        from market_intelligence.store import IntelligenceStore

        store = IntelligenceStore(self.path)
        replacement_entered = threading.Event()
        release_replacement = threading.Event()
        marker_entered = threading.Event()
        release_marker = threading.Event()
        marker_finished = threading.Event()
        original_replace = bridge.os.replace
        target = self.path.with_suffix(".bot-observation.json")
        observer = bridge.BotIntelligenceObserver(self.path)

        def blocked_replace(source, destination):
            if Path(destination) == target:
                replacement_entered.set()
                release_replacement.wait(timeout=3)
            elif Path(destination) == observer.closed_path:
                marker_entered.set()
                release_marker.wait(timeout=3)
                marker_finished.set()
            return original_replace(source, destination)

        observer.record("SPY", "hold")
        with patch.object(bridge.os, "replace", side_effect=blocked_replace):
            self.assertTrue(replacement_entered.wait(timeout=2))
            started = time.monotonic()
            observer.close()
            self.assertTrue(marker_entered.is_set())
            self.assertLess(time.monotonic() - started, 1.5)
            self.assertFalse(observer.status_path.exists())
            self.assertIsNone(bridge.observed_revision(
                self.path, store.database_id, datetime.now(timezone.utc)))
            release_marker.set()
            release_replacement.set()
            self.assertTrue(marker_finished.wait(timeout=2))
            observer.thread.join(timeout=2)

        self.assertFalse(observer.thread.is_alive())
        self.assertIsNone(bridge.observed_revision(
            self.path, store.database_id, datetime.now(timezone.utc)))

    def test_close_reports_unreadable_status_and_rejects_late_snapshot(self):
        import threading
        from market_intelligence import bridge
        from market_intelligence.store import IntelligenceStore

        store = IntelligenceStore(self.path)
        replacement_entered = threading.Event()
        release_replacement = threading.Event()
        original_replace = bridge.os.replace
        original_open = Path.open
        target = self.path.with_suffix(".bot-observation.json")
        observer = bridge.BotIntelligenceObserver(self.path)

        def blocked_replace(source, destination):
            if Path(destination) == target:
                replacement_entered.set()
                release_replacement.wait(timeout=3)
            return original_replace(source, destination)

        def refuse_status_read(path, *args, **kwargs):
            if path == observer.status_path:
                raise PermissionError("synthetic status read failure")
            return original_open(path, *args, **kwargs)

        observer.record("SPY", "hold")
        with patch.object(bridge.os, "replace", side_effect=blocked_replace), \
                patch.object(observer, "_write_closed_marker",
                             side_effect=OSError("synthetic close marker failure")), \
                patch.object(Path, "open", refuse_status_read), \
                self.assertLogs("bot.intelligence", level="CRITICAL"):
            self.assertTrue(replacement_entered.wait(timeout=2))
            with self.assertRaisesRegex(OSError, "could not be invalidated"):
                observer.close()
            self.assertIsNone(bridge.observed_revision(
                self.path, store.database_id, datetime.now(timezone.utc)))
            release_replacement.set()
            observer.thread.join(timeout=2)

        self.assertFalse(observer.thread.is_alive())
        self.assertTrue(target.exists())
        self.assertIsNone(bridge.observed_revision(
            self.path, store.database_id, datetime.now(timezone.utc)))

    def test_close_rejects_non_object_status_when_marker_write_fails(self):
        from market_intelligence import bridge

        observer = bridge.BotIntelligenceObserver(self.path)
        observer.status_path.write_text("[]", encoding="utf-8")
        with patch.object(observer, "_write_closed_marker",
                          side_effect=OSError("synthetic close marker failure")), \
                self.assertLogs("bot.intelligence", level="CRITICAL"):
            with self.assertRaisesRegex(OSError, "could not be invalidated"):
                observer.close()

    def test_close_reports_stalled_status_invalidation(self):
        import threading
        import time
        from market_intelligence import bridge

        entered = threading.Event()
        release = threading.Event()
        unlink_finished = threading.Event()
        original_unlink = Path.unlink
        observer = bridge.BotIntelligenceObserver(self.path)

        def blocked_status_unlink(path, *args, **kwargs):
            if path == observer.status_path:
                entered.set()
                release.wait(timeout=3)
                result = original_unlink(path, *args, **kwargs)
                unlink_finished.set()
                return result
            return original_unlink(path, *args, **kwargs)

        with patch.object(observer, "_write_closed_marker",
                          side_effect=OSError("synthetic close marker failure")), \
                patch.object(Path, "unlink", blocked_status_unlink), \
                self.assertLogs("bot.intelligence", level="CRITICAL"):
            started = time.monotonic()
            with self.assertRaisesRegex(OSError, "could not be invalidated"):
                observer.close()
            self.assertTrue(entered.is_set())
            self.assertLess(time.monotonic() - started, 1.5)
            self.assertTrue(observer.status_path.exists())
            release.set()
            self.assertTrue(unlink_finished.wait(timeout=2))
            observer.thread.join(timeout=2)

        self.assertFalse(observer.thread.is_alive())
        self.assertFalse(observer.status_path.exists())