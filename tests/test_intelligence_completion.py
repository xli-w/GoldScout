from datetime import timedelta
from dataclasses import replace
import unittest
from market_intelligence.analysis import AnalysisBrief, AnalysisLimits, EvidenceClaim, analysis_input
from market_intelligence.store import IntelligenceStore, StoreError, initialize, upgrade
from unittest.mock import Mock,patch
import json
import threading
import socket
from market_intelligence.runtime import ProviderProfile
from market_intelligence.analysis import SummaryRequest,AnalysisError
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
    def test_local_provider_uses_fixed_loopback_and_output_limit(self):
        profile=ProviderProfile("local","local","model")
        connection=Mock(sock=None)
        connection.getresponse.return_value=FakeResponse(json.dumps({"choices":[{"message":{"content":"{}"}}],"usage":{"total_tokens":10}}).encode(),headers={"Content-Type":"application/json"})
        with patch("market_intelligence.runtime.http.client.HTTPConnection",return_value=connection) as factory:
            response=profile.complete(SummaryRequest("system","{}",64),threading.Event())
        factory.assert_called_once_with("127.0.0.1",11434,timeout=3.0)
        self.assertEqual(response.text,"{}")
        self.assertEqual(json.loads(connection.request.call_args.args[2])["max_tokens"],64)

    def test_cancelled_hosted_dns_never_connects_or_posts(self):
        cancelled=threading.Event()
        def resolve(*args,**kwargs):
            cancelled.set()
            return [(socket.AF_INET,socket.SOCK_STREAM,6,"",("93.184.216.34",443))]
        with patch.dict("os.environ",{"TRADER_AI_TEST":"synthetic-key"}),patch("market_intelligence.runtime.socket.getaddrinfo",side_effect=resolve),patch("market_intelligence.runtime.PinnedHTTPSConnection") as factory:
            with self.assertRaises(AnalysisError):
                ProviderProfile("hosted","openai","model","TRADER_AI_TEST").complete(SummaryRequest("system","{}",64),cancelled)
        factory.assert_not_called()

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