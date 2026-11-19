from datetime import timedelta
from dataclasses import replace
import unittest
from market_intelligence.analysis import AnalysisBrief, AnalysisLimits, EvidenceClaim, analysis_input
from market_intelligence.store import IntelligenceStore, StoreError, initialize, upgrade
from unittest.mock import Mock,patch
import io
import json
import threading
import socket
import ssl
import tempfile
import traceback
import proxy_config
from market_intelligence.runtime import ProviderProfile, load_profiles
from market_intelligence.analysis import SummaryRequest,AnalysisError
from proxy_config import ProxyConfigurationError
from test_intelligence_providers import FakeResponse
from test_intelligence_controls import IntelligenceStoreFixture
from market_intelligence.news import parse_fed_news
from test_intelligence_news import feed
from market_intelligence.contracts import ControlRequest,IntelligenceSettings
from pathlib import Path


class DurableCompletionTests(IntelligenceStoreFixture,unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.store.enqueue(self.request(enabled=True),self.now)
        self.store.apply_pending(self.now)
        self.batch=parse_fed_news(feed(),self.now)

    def success(self):
        return self.store.complete_news_attempt("attempt-1",1,self.now,self.now,self.now,batch=self.batch,raw=feed())
    def test_reservations_survive_restart_and_do_not_refund(self):
        status = self.success()
        settings = replace(self.store.settings(), ai_enabled=True, ai_profile="fixture", revision=2)
        from market_intelligence.contracts import ControlRequest
        request = ControlRequest("ai",self.store.database_id,1,self.now,self.now+timedelta(minutes=5),settings)
        self.store.enqueue(request,self.now)
        self.store.apply_pending(self.now,profiles=("fixture",))
        context=self.context(status,2)
        _,size,_=analysis_input(self.store.news_acquisition(status.acquisition_id),context["cluster_id"])
        limits=AnalysisLimits(daily_calls=1)
        self.store.reserve_analysis(self.now,size,limits,context)
        from market_intelligence.store import IntelligenceStore
        reopened=IntelligenceStore(self.path)
        with self.assertRaises(StoreError):
            reopened.reserve_analysis(self.now,size,limits,context)
        self.assertEqual(reopened.analysis_usage(self.now)["calls"],1)

    def context(self,status,revision):
        _,_,checksum=analysis_input(self.store.news_acquisition(status.acquisition_id),self.batch.items[0].cluster_id)
        return {"acquisition_id":status.acquisition_id,"source_attempt_id":status.attempt_id,
                "settings_revision":revision,"profile":"fixture","model":"model",
                "cluster_id":self.batch.items[0].cluster_id,"input_hash":checksum,
                "prompt_version":"news-extractive-v1"}

    def enable_ai(self):
        status=self.success()
        settings=replace(self.store.settings(),ai_enabled=True,ai_profile="fixture",revision=2)
        self.store.enqueue(ControlRequest("ai",self.store.database_id,1,self.now,self.now+timedelta(minutes=5),settings),self.now)
        self.store.apply_pending(self.now,profiles=("fixture",))
        return status

    def brief_for(self,reservation_id,context,brief_id="brief-test"):
        return AnalysisBrief(brief_id,context["cluster_id"],
            (EvidenceClaim(self.batch.items[0].snippet,(self.batch.items[0].evidence_id,)),),
            "Review possible rate-sensitive volatility.",("The price reaction is unknown.",),
            ("SPY",),"review","unverified",self.now,self.now+timedelta(minutes=10),
            "fixture","model","news-extractive-v1","1",context["settings_revision"],context["input_hash"],10,reservation_id)

    def test_analysis_rejects_input_hash_byte_profile_and_daily_limit_mismatch(self):
        status=self.enable_ai();context=self.context(status,2)
        _,size,_=analysis_input(self.store.news_acquisition(status.acquisition_id),context["cluster_id"])
        limits=AnalysisLimits(daily_calls=1)
        wrong=dict(context,input_hash="a"*64)
        with self.assertRaises(StoreError):self.store.reserve_analysis(self.now,size,limits,wrong)
        with self.assertRaises(StoreError):self.store.reserve_analysis(self.now,size+1,limits,context)
        self.store.reserve_analysis(self.now,size,limits,context)
        with self.assertRaises(StoreError):self.store.reserve_analysis(self.now,size,limits,context)
        other=dict(context,profile="unregistered")
        with self.assertRaises(StoreError):self.store.reserve_analysis(self.now,size,limits,other)

    def test_brief_persistence_checks_reservation_quotes_retry_and_conflicts(self):
        status=self.enable_ai();context=self.context(status,2)
        _,size,_=analysis_input(self.store.news_acquisition(status.acquisition_id),context["cluster_id"])
        reservation=self.store.reserve_analysis(self.now,size,AnalysisLimits(),context)
        brief=self.brief_for(reservation,context)
        committed=self.store.publish_brief(brief,self.now)
        self.assertEqual(self.store.recent_briefs(),(committed,))
        portfolio = self.store.portfolio_evidence()
        self.assertEqual(portfolio["settings"], self.store.settings())
        self.assertEqual(portfolio["briefs"], ((committed, (self.batch.items[0],), status.attempt_id),))
        self.assertEqual(portfolio["news_status"], status)
        self.assertEqual(self.store.publish_brief(brief,self.now),committed)
        invalid=replace(brief,interpretation="A different result with the same ID.")
        with self.assertRaises(StoreError):self.store.publish_brief(invalid,self.now)
        unquoted=replace(brief,claims=(EvidenceClaim("The central bank increased rates.",(self.batch.items[0].evidence_id,)),),brief_id="brief-unquoted")
        with self.assertRaises(StoreError):self.store.publish_brief(unquoted,self.now)

    def test_cached_brief_retry_rejects_a_clock_before_its_commit(self):
        status = self.enable_ai()
        context = self.context(status, 2)
        _, size, _ = analysis_input(self.store.news_acquisition(status.acquisition_id), context["cluster_id"])
        reservation = self.store.reserve_analysis(self.now, size, AnalysisLimits(), context)
        brief = self.brief_for(reservation, context)
        committed = self.store.publish_brief(brief, self.now + timedelta(seconds=10))
        with self.assertRaises(StoreError) as result:
            self.store.publish_brief(brief, self.now + timedelta(seconds=5))
        self.assertEqual(result.exception.code, "clock_invalid")
        self.assertEqual(self.store.recent_briefs(), (committed,))

    def test_schema_three_news_status_migrates_to_schema_five(self):
        path=Path(self.directory.name)/"v3.sqlite3"
        old=initialize(path,self.now,schema_version=3)
        old.enqueue(ControlRequest("enable",old.database_id,0,self.now,self.now+timedelta(minutes=5),IntelligenceSettings(enabled=True,revision=1)),self.now)
        old.apply_pending(self.now)
        batch=parse_fed_news(feed(),self.now)
        status=old.complete_news_attempt("attempt-v3",1,self.now,self.now,self.now,batch=batch,raw=feed())
        self.assertIsNone(old.analysis_usage(self.now))
        upgrade(path)
        migrated=IntelligenceStore(path)
        self.assertEqual(migrated.schema_version,5)
        self.assertEqual(migrated.news_status(),status)
        self.assertEqual(migrated.analysis_usage(self.now),{"day":self.now.date().isoformat(),"calls":0,"input_bytes":0,"output_allowance":0})


class RuntimeProviderTests(unittest.TestCase):
    def test_hosted_key_from_dotenv_overrides_environment_without_mutation(self):
        addresses = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        connection = Mock(sock=None)
        payload = {"choices": [{"finish_reason": "stop",
                                "message": {"role": "assistant", "content": "{}"}}]}
        connection.getresponse.return_value = FakeResponse(json.dumps(payload).encode())
        with patch.dict("os.environ", {"TRADER_AI_TEST": "environment-key"}), \
             patch("proxy_config._dotenv_loading_disabled", return_value=False), \
             patch("proxy_config._read_proxy_dotenv",
                   return_value={"TRADER_AI_TEST": "file-key"}) as read, \
             patch("market_intelligence.runtime.socket.getaddrinfo", return_value=addresses), \
             patch("market_intelligence.runtime.configured_proxy_url", return_value=""), \
             patch("market_intelligence.runtime.PinnedHTTPSConnection", return_value=connection):
            ProviderProfile("test", "openai", "model", "TRADER_AI_TEST").complete(
                SummaryRequest("system", "{}", 64), threading.Event())
            import os
            self.assertEqual(os.environ["TRADER_AI_TEST"], "environment-key")
        self.assertEqual(connection.request.call_args.args[3]["Authorization"], "Bearer file-key")
        read.assert_called_once_with(("TRADER_AI_TEST",))

    def test_blank_or_malformed_dotenv_key_never_dispatches(self):
        for result in ({"TRADER_AI_TEST": ""}, ProxyConfigurationError("Invalid connection settings")):
            with self.subTest(result=type(result)), \
                 patch.dict("os.environ", {"TRADER_AI_TEST": "environment-key"}), \
                 patch("proxy_config._dotenv_loading_disabled", return_value=False), \
                 patch("proxy_config._read_proxy_dotenv") as read, \
                 patch("market_intelligence.runtime.socket.getaddrinfo",
                       side_effect=AssertionError("DNS must not be used")) as resolve:
                if isinstance(result, Exception):
                    read.side_effect = result
                else:
                    read.return_value = result
                with self.assertRaises(AnalysisError) as caught:
                    ProviderProfile("test", "openai", "model", "TRADER_AI_TEST").complete(
                        SummaryRequest("system", "{}", 64), threading.Event())
                self.assertEqual(caught.exception.code, "credential_unavailable")
                resolve.assert_not_called()

    def test_windows_case_insensitive_blank_key_blocks_environment_fallback(self):
        contents = "TRADER_AI_TEST=initial\ntrader_ai_test=\n"
        with patch.dict("os.environ", {"TRADER_AI_test": "environment-key"}), \
             patch.object(proxy_config.os, "name", "nt"), \
             patch("proxy_config._dotenv_loading_disabled", return_value=False), \
             patch("proxy_config._read_proxy_dotenv",
                   side_effect=lambda names: proxy_config._parse_proxy_dotenv(io.StringIO(contents), names)), \
             patch("market_intelligence.runtime.socket.getaddrinfo",
                   side_effect=AssertionError("DNS must not be used")) as resolve:
            with self.assertRaises(AnalysisError) as caught:
                ProviderProfile("test", "openai", "model", "TRADER_AI_test").complete(
                    SummaryRequest("system", "{}", 64), threading.Event())
            self.assertEqual(caught.exception.code, "credential_unavailable")
            resolve.assert_not_called()

    def test_anthropic_and_gemini_use_provider_specific_requests(self):
        examples = (
            ("anthropic", "claude-test", "api.anthropic.com", "/v1/messages",
             {"type": "message", "role": "assistant", "stop_reason": "end_turn",
              "content": [{"type": "text", "text": "{}"}],
              "usage": {"input_tokens": 10, "output_tokens": 5}}),
            ("gemini", "gemini-test", "generativelanguage.googleapis.com",
             "/v1beta/models/gemini-test:generateContent",
             {"candidates": [{"content": {"role": "model", "parts": [{"text": "{}"}]},
                              "finishReason": "STOP"}],
              "usageMetadata": {"totalTokenCount": 15}}),
            ("openai", "gpt-test", "api.openai.com", "/v1/chat/completions",
             {"choices": [{"finish_reason": "stop",
                           "message": {"role": "assistant", "content": "{}", "refusal": None}}],
              "usage": {"total_tokens": 15}}),
        )
        addresses = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        for kind, model, host, path, payload in examples:
            with self.subTest(kind=kind):
                connection = Mock(sock=None)
                encoded_response = json.dumps(payload).encode()
                connection.getresponse.return_value = FakeResponse(
                    encoded_response, headers={"Content-Length": str(len(encoded_response))})
                with patch.dict("os.environ", {"TRADER_AI_TEST": "synthetic-key"}), \
                     patch("market_intelligence.runtime.socket.getaddrinfo", return_value=addresses) as resolve, \
                     patch("market_intelligence.runtime.configured_proxy_url", return_value=""), \
                     patch("market_intelligence.runtime.PinnedHTTPSConnection", return_value=connection) as factory:
                    result = ProviderProfile("test", kind, model, "TRADER_AI_TEST").complete(
                        SummaryRequest("system", '{"evidence":[]}', 64), threading.Event())
                resolve.assert_called_once_with(host, 443, type=socket.SOCK_STREAM)
                factory.assert_called_once_with(host, "93.184.216.34", 3.0, proxy=None)
                method, actual_path, encoded, headers = connection.request.call_args.args
                self.assertEqual((method, actual_path), ("POST", path))
                self.assertNotIn("synthetic-key", actual_path)
                self.assertNotIn("synthetic-key", encoded.decode())
                body = json.loads(encoded)
                if kind == "anthropic":
                    self.assertEqual(headers["x-api-key"], "synthetic-key")
                    self.assertEqual(headers["anthropic-version"], "2023-06-01")
                    self.assertEqual(body["system"], "system")
                    self.assertEqual(body["messages"][0]["content"], '{"evidence":[]}')
                    self.assertEqual(body["max_tokens"], 64)
                elif kind == "gemini":
                    self.assertEqual(headers["x-goog-api-key"], "synthetic-key")
                    self.assertEqual(body["system_instruction"]["parts"][0]["text"], "system")
                    self.assertEqual(body["contents"][0]["parts"][0]["text"], '{"evidence":[]}')
                    self.assertEqual(body["generationConfig"]["maxOutputTokens"], 64)
                    self.assertEqual(body["generationConfig"]["responseMimeType"], "application/json")
                else:
                    self.assertEqual(headers["Authorization"], "Bearer synthetic-key")
                    self.assertEqual(body["messages"][0]["content"], "system")
                    self.assertEqual(body["messages"][1]["content"], '{"evidence":[]}')
                    self.assertEqual(body["max_tokens"], 64)
                self.assertNotIn("tools", body)
                self.assertEqual((result.text, result.tokens_used), ("{}", 15))

    def test_gemini_model_cannot_escape_the_fixed_endpoint(self):
        for model in ("../model", "models/model", "model?key=secret", "model%2fother", "https://example.com"):
            with self.subTest(model=model), self.assertRaises(ValueError):
                ProviderProfile("test", "gemini", model, "TRADER_AI_TEST")

    def test_new_provider_profiles_load_without_reading_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.json"
            path.write_text(json.dumps([
                {"name": "claude", "kind": "anthropic", "model": "claude-test",
                 "key_env": "TRADER_AI_CLAUDE"},
                {"name": "gemini", "kind": "gemini", "model": "gemini-test",
                 "key_env": "TRADER_AI_GEMINI"},
            ]), encoding="utf-8")
            with patch("proxy_config.os.getenv", side_effect=AssertionError("unexpected credential read")), \
                 patch("market_intelligence.runtime.configured_connection_values",
                       side_effect=AssertionError("unexpected connection configuration read")):
                profiles = load_profiles(path)
            self.assertEqual(set(profiles), {"claude", "gemini"})

    def test_profile_file_rejects_ambiguous_or_incomplete_provider_selection(self):
        examples = (
            '[{"name":"test","kind":"local","kind":"gemini","model":"model",'
            '"key_env":"TRADER_AI_TEST"}]',
            '[{"name":"test","kind":"anthropic"}]',
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.json"
            for content in examples:
                with self.subTest(content=content):
                    path.write_text(content, encoding="utf-8")
                    with patch("proxy_config.os.getenv", side_effect=AssertionError("unexpected credential read")), \
                         patch("market_intelligence.runtime.configured_connection_values",
                               side_effect=AssertionError("unexpected connection configuration read")):
                        with self.assertRaises(ValueError):
                            load_profiles(path)

    def test_incomplete_or_tool_provider_output_fails_closed(self):
        examples = (
            ("anthropic", {"type": "message", "role": "assistant", "stop_reason": "max_tokens",
                           "content": [{"type": "text", "text": "{}"}]}),
            ("gemini", {"candidates": [{"finishReason": "STOP",
                                        "content": {"parts": [{"functionCall": {"name": "tool"}}]}}]}),
            ("openai", {"choices": [{"finish_reason": "length",
                                     "message": {"role": "assistant", "content": "{}"}}]}),
            ("openai", {"choices": [{"finish_reason": "stop",
                                     "message": {"role": "assistant", "content": "{}",
                                                 "refusal": "refused"}}]}),
            ("openai", {"choices": [{"finish_reason": "stop",
                                     "message": {"role": "assistant", "content": "{}",
                                                 "tool_calls": [{"id": "tool"}]}}]}),
            ("openai", {"choices": [{"message": {"role": "assistant", "content": "{}"}}]}),
        )
        addresses = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        for kind, payload in examples:
            with self.subTest(kind=kind):
                connection = Mock(sock=None)
                connection.getresponse.return_value = FakeResponse(json.dumps(payload).encode())
                with patch.dict("os.environ", {"TRADER_AI_TEST": "synthetic-key"}), \
                     patch("market_intelligence.runtime.socket.getaddrinfo", return_value=addresses), \
                     patch("market_intelligence.runtime.configured_proxy_url", return_value=""), \
                     patch("market_intelligence.runtime.PinnedHTTPSConnection", return_value=connection):
                    with self.assertRaises(AnalysisError):
                        ProviderProfile("test", kind, "model", "TRADER_AI_TEST").complete(
                            SummaryRequest("system", "{}", 64), threading.Event())

    def test_truncated_provider_http_body_is_rejected(self):
        body = json.dumps({"type": "message", "role": "assistant", "stop_reason": "end_turn",
                           "content": [{"type": "text", "text": "{}"}]}).encode()
        addresses = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        for declared in (str(len(body) + 10), "-1", "131073"):
            with self.subTest(declared=declared):
                connection = Mock(sock=None)
                connection.getresponse.return_value = FakeResponse(body, headers={"Content-Length": declared})
                with patch.dict("os.environ", {"TRADER_AI_TEST": "synthetic-key"}), \
                     patch("market_intelligence.runtime.socket.getaddrinfo", return_value=addresses), \
                     patch("market_intelligence.runtime.configured_proxy_url", return_value=""), \
                     patch("market_intelligence.runtime.PinnedHTTPSConnection", return_value=connection):
                    with self.assertRaises(AnalysisError):
                        ProviderProfile("test", "anthropic", "model", "TRADER_AI_TEST").complete(
                            SummaryRequest("system", "{}", 64), threading.Event())

    def test_ambiguous_provider_envelope_is_rejected(self):
        body = (b'{"candidates":[],"candidates":[{"finishReason":"STOP",'
                b'"content":{"parts":[{"text":"{}"}]}}]}')
        addresses = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        connection = Mock(sock=None)
        connection.getresponse.return_value = FakeResponse(body)
        with patch.dict("os.environ", {"TRADER_AI_TEST": "synthetic-key"}), \
             patch("market_intelligence.runtime.socket.getaddrinfo", return_value=addresses), \
             patch("market_intelligence.runtime.configured_proxy_url", return_value=""), \
             patch("market_intelligence.runtime.PinnedHTTPSConnection", return_value=connection):
            with self.assertRaises(AnalysisError):
                ProviderProfile("test", "gemini", "model", "TRADER_AI_TEST").complete(
                    SummaryRequest("system", "{}", 64), threading.Event())

    def test_new_provider_refuses_malformed_or_private_dns_results(self):
        records = (
            [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))],
            [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:4700:4700::1111%1", 443, 0, 0))],
            [(99999, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
            [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
             (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))],
        )
        for addresses in records:
            with self.subTest(addresses=addresses), \
                 patch.dict("os.environ", {"TRADER_AI_TEST": "synthetic-key"}), \
                 patch("market_intelligence.runtime.socket.getaddrinfo", return_value=addresses), \
                 patch("market_intelligence.runtime.configured_proxy_url", return_value=""), \
                 patch("market_intelligence.runtime.PinnedHTTPSConnection") as factory:
                with self.assertRaises(AnalysisError):
                    ProviderProfile("test", "anthropic", "model", "TRADER_AI_TEST").complete(
                        SummaryRequest("system", "{}", 64), threading.Event())
                factory.assert_not_called()

    def test_new_provider_never_bypasses_a_failed_configured_proxy(self):
        addresses = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        with patch.dict("os.environ", {"TRADER_AI_TEST": "synthetic-key"}), \
             patch("market_intelligence.runtime.socket.getaddrinfo", return_value=addresses), \
             patch("market_intelligence.runtime.configured_proxy_url", return_value="http://127.0.0.1:8080"), \
             patch("market_intelligence.runtime.PinnedHTTPSConnection", side_effect=OSError("proxy failed")) as factory, \
             patch("market_intelligence.runtime.http.client.HTTPConnection") as direct:
            with self.assertRaises(AnalysisError):
                ProviderProfile("test", "gemini", "model", "TRADER_AI_TEST").complete(
                    SummaryRequest("system", "{}", 64), threading.Event())
        factory.assert_called_once()
        self.assertEqual(factory.call_args.kwargs["proxy"].port, 8080)
        direct.assert_not_called()

    def test_provider_transport_failure_never_exposes_key_in_traceback(self):
        addresses = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        connection = Mock(sock=None)
        connection.request.side_effect = OSError("synthetic-key should not be exposed")
        with patch.dict("os.environ", {"TRADER_AI_TEST": "synthetic-key"}), \
             patch("market_intelligence.runtime.socket.getaddrinfo", return_value=addresses), \
             patch("market_intelligence.runtime.configured_proxy_url", return_value=""), \
             patch("market_intelligence.runtime.PinnedHTTPSConnection", return_value=connection):
            try:
                ProviderProfile("test", "openai", "model", "TRADER_AI_TEST").complete(
                    SummaryRequest("system", "{}", 64), threading.Event())
            except AnalysisError as exc:
                rendered = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            else:
                self.fail("A failed provider request must not succeed")
        self.assertNotIn("synthetic-key", rendered)

    def test_local_provider_uses_fixed_loopback_and_output_limit(self):
        profile=ProviderProfile("local","local","model")
        connection=Mock(sock=None)
        connection.getresponse.return_value=FakeResponse(json.dumps({"choices":[{"finish_reason":"stop","message":{"role":"assistant","content":"{}"}}],"usage":{"total_tokens":10}}).encode(),headers={"Content-Type":"application/json"})
        with patch("market_intelligence.runtime.http.client.HTTPConnection",return_value=connection) as factory:
            response=profile.complete(SummaryRequest("system","{}",64),threading.Event())
        factory.assert_called_once_with("127.0.0.1",11434,timeout=3.0)
        self.assertEqual(response.text,"{}")
        self.assertEqual(json.loads(connection.request.call_args.args[2])["max_tokens"],64)

    def test_local_provider_rejects_an_explicit_truncated_completion(self):
        connection = Mock(sock=None)
        connection.getresponse.return_value = FakeResponse(json.dumps({
            "choices": [{"finish_reason": "length", "message": {"role": "assistant", "content": "{}"}}],
        }).encode())
        with patch("market_intelligence.runtime.http.client.HTTPConnection", return_value=connection):
            with self.assertRaises(AnalysisError):
                ProviderProfile("local", "local", "model").complete(
                    SummaryRequest("system", "{}", 64), threading.Event())

    def test_cancelled_hosted_dns_never_connects_or_posts(self):
        cancelled=threading.Event()
        def resolve(*args,**kwargs):
            cancelled.set()
            return [(socket.AF_INET,socket.SOCK_STREAM,6,"",("93.184.216.34",443))]
        with patch.dict("os.environ",{"TRADER_AI_TEST":"synthetic-key"}),patch("market_intelligence.runtime.socket.getaddrinfo",side_effect=resolve),patch("market_intelligence.runtime.PinnedHTTPSConnection") as factory:
            with self.assertRaises(AnalysisError):
                ProviderProfile("hosted","openai","model","TRADER_AI_TEST").complete(SummaryRequest("system","{}",64),cancelled)
        factory.assert_not_called()

    def test_hosted_cancellation_keeps_inflight_credentials_under_tls(self):
        entered, released, sent = threading.Event(), threading.Event(), threading.Event()
        state = {"protected": True, "connected": True}
        plaintext, errors, timers = [], [], []
        tls_socket = Mock(spec=ssl.SSLSocket)
        connection = Mock(sock=tls_socket)
        cancelled = threading.Event()

        def request(*args, **kwargs):
            entered.set()
            released.wait(2.0)
            if state["connected"] and not state["protected"]:
                plaintext.append(kwargs["headers"]["Authorization"])
            sent.set()
            raise OSError("synthetic cancelled request")

        def unsafe_shutdown(*args):
            state["protected"] = False
            released.set()
            sent.wait(2.0)
            state["connected"] = False

        def native_shutdown(*args):
            state["connected"] = False
            released.set()
            sent.wait(2.0)

        def close_connection():
            state["protected"] = False
            released.set()
            sent.wait(2.0)

        def timer_factory(delay, callback):
            timers.append(callback)
            return Mock()

        def complete():
            try:
                ProviderProfile("hosted", "openai", "model", "TRADER_AI_TEST").complete(
                    SummaryRequest("system", "{}", 64), cancelled)
            except AnalysisError as exc:
                errors.append(exc.code)

        tls_socket.shutdown.side_effect = unsafe_shutdown
        connection.request.side_effect = request
        connection.close.side_effect = close_connection
        records = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", 443))]
        with patch.dict("os.environ", {"TRADER_AI_TEST": "synthetic-key"}), \
             patch("market_intelligence.runtime.socket.getaddrinfo", return_value=records), \
             patch("market_intelligence.runtime.PinnedHTTPSConnection", return_value=connection), \
             patch("market_intelligence.runtime.threading.Timer", side_effect=timer_factory), \
             patch.object(socket.socket, "shutdown", side_effect=native_shutdown):
            worker = threading.Thread(target=complete)
            try:
                worker.start()
                self.assertTrue(entered.wait(2.0))
                timers[0]()
            finally:
                released.set()
                worker.join(2.0)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, ["provider_unavailable"])
        self.assertEqual(plaintext, [])

    def test_cancelled_local_connection_setup_does_not_dispatch(self):
        cancelled = threading.Event()
        connection = Mock(sock=None)

        def create(*args, **kwargs):
            cancelled.set()
            return connection

        with patch("market_intelligence.runtime.http.client.HTTPConnection", side_effect=create):
            with self.assertRaises(AnalysisError) as result:
                ProviderProfile("local", "local", "model").complete(SummaryRequest("system", "{}", 64), cancelled)
        self.assertEqual(result.exception.code, "analysis_cancelled")
        connection.request.assert_not_called()
        connection.close.assert_called_once()