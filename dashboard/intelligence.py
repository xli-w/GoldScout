"""Independent advisory intelligence workspace; never opens investment records."""

from datetime import datetime, timedelta, timezone
import json
import secrets
import tempfile
import threading
from pathlib import Path

from market_intelligence.analysis import AdvisoryAnalyzer, AnalysisLimits, JSONSummaryProvider, ProviderResponse
from market_intelligence.contracts import CalendarEvent, CalendarSnapshot, ControlRequest, IntelligenceSettings, SourceCoverage
from market_intelligence.news import parse_fed_news
from market_intelligence.runtime import PersistentAnalysisBudget
from market_intelligence.store import IntelligenceStore, StoreError, connection, bounded_column, encode, initialize


class ControlSessions:
    def __init__(self):
        self.sessions = {}
        self.lock = threading.Lock()

    def session(self, token, now, create=False):
        with self.lock:
            self.sessions = {key: value for key, value in self.sessions.items() if value["expires"] > now}
            record = self.sessions.get(token)
            if record is None and create:
                if len(self.sessions) >= 128:
                    raise StoreError("session_unavailable")
                token = secrets.token_urlsafe(32)
                record = {"csrf": secrets.token_urlsafe(32), "expires": now + timedelta(minutes=15)}
                self.sessions[token] = record
            return token, record

    def authorize(self, token, csrf, now):
        _, record = self.session(token, now)
        good=bool(record and isinstance(csrf, str) and secrets.compare_digest(record["csrf"], csrf))
        if good:
            with self.lock:
                commands=[stamp for stamp in record.get("commands",[]) if now-stamp<timedelta(minutes=1)]
                if len(commands)>=30:
                    return False
                commands.append(now)
                record["commands"]=commands
        return good

class IntelligenceWorkspace:
    def __init__(self, path=None, profiles=(), controls_enabled=False, demo=False):
        self.demo = demo
        self.temporary = tempfile.TemporaryDirectory(prefix="trader-intelligence-demo-") if demo else None
        self.store = initialize(Path(self.temporary.name)/"demo.sqlite3", datetime.now(timezone.utc)) if demo else IntelligenceStore(path)
        self.profiles = tuple(profiles) if not demo else ("demo",)
        self.security = ControlSessions()
        self.controls_enabled = demo or controls_enabled
        self.demo_analyzer = None
        self.demo_raw = None
        self.lock = threading.Lock()
        if demo:
            self._seed()

    def _seed(self):
        now = datetime.now(timezone.utc)
        self.store.enqueue(ControlRequest("seed-on",self.store.database_id,0,now,now+timedelta(minutes=5),IntelligenceSettings(enabled=True,revision=1)),now)
        self.store.apply_pending(now)
        raw = ("<rss version='2.0'><channel><item><title>FOMC policy statement</title>"
               "<link>https://www.federalreserve.gov/newsevents/pressreleases/monetary20261114a.htm</link>"
               "<description>The policy rate is unchanged.</description></item></channel></rss>").encode()
        self.demo_raw=raw
        self.store.complete_news_attempt("seed-news",1,now,now,now,batch=parse_fed_news(raw,now),raw=raw)
        source = SourceCoverage("bls",("inflation",),now,None,None,"unavailable")
        event = CalendarEvent("demo-cpi","bls","https://www.bls.gov/schedule/","inflation",("SPY","QQQ","GLD"),now+timedelta(hours=2),now,"Consumer Price Index")
        self.store.publish_calendar(CalendarSnapshot(1,now,(source,),(event,)),1,now)
        self.store.enqueue(ControlRequest("seed-off",self.store.database_id,1,now,now+timedelta(minutes=5),IntelligenceSettings(revision=2)),now)
        self.store.apply_pending(now)
        def complete(request, cancelled):
            item = json.loads(request.evidence_json)["evidence"][0]
            return ProviderResponse(json.dumps({"claims":[{"text":item["snippet"],"evidence_ids":[item["evidence_id"]]}],
                "interpretation":"Rate-sensitive instruments may experience volatility around policy communication.",
                "uncertainties":["Synthetic evidence; no market reaction is established."],"symbols":["SPY","QQQ","GLD"],"recommendation":"review"}))
        limits=AnalysisLimits()
        self.demo_analyzer=AdvisoryAnalyzer({"demo":JSONSummaryProvider("demo","synthetic",complete)},limits=limits,budget=PersistentAnalysisBudget(self.store,limits))

    def snapshot(self):
        with self.lock:
            now=datetime.now(timezone.utc)
            if self.demo:
                self.store.apply_pending(now,profiles=self.profiles)
            settings=self.store.settings()
            status=self.store.news_status() if self.store.schema_version>=3 else None
            if self.demo and settings.enabled and settings.news_enabled and status and (now-status.available_at).total_seconds()>=300:
                attempt="demo-news-"+secrets.token_hex(12)
                self.store.complete_news_attempt(attempt,settings.revision,now,now,now,
                                                  batch=parse_fed_news(self.demo_raw,now),raw=self.demo_raw)
                status=self.store.news_status()
            if self.demo and settings.enabled and settings.ai_enabled and settings.news_enabled and status:
                archive=self.store.news_acquisition(status.acquisition_id)
                for cluster in {item.cluster_id for item in archive.batch.items}:
                    brief=self.demo_analyzer.analyze(archive,settings,cluster,self.store.settings,self.store.news_status)
                    if brief:
                        self.store.publish_brief(brief,datetime.now(timezone.utc))
            calendar=self.store.calendar()
            archive=self.store.news_acquisition(status.last_success_id) if status and status.last_success_id else None
            briefs=self.store.recent_briefs()
            with connection(self.store.path,expected_id=self.store.database_id) as handle:
                rows=handle.execute(f"SELECT {bounded_column('request_id',128)} FROM commands ORDER BY sequence DESC LIMIT 20").fetchall()
                controls=[self.store._command(handle,row[0])[1] for row in rows]
            if self.store.settings()!=settings:
                raise StoreError("snapshot_changed")
            return {"source_mode":"demo" if self.demo else "recorded","database_id":self.store.database_id,"generated_at":now.isoformat().replace("+00:00","Z"),
                    "settings":json.loads(encode(settings)),"profiles":list(self.profiles),"controls_enabled":self.controls_enabled,
                    "news_capable":self.store.schema_version>=3,"ai_capable":self.store.schema_version>=4 and bool(self.profiles),
                    "source_status":json.loads(encode(status)) if status else None,
                    "calendar":[json.loads(encode(item)) for item in calendar.events[:100]] if calendar else [],
                    "news":[json.loads(encode(item)) for item in archive.batch.items[:50]] if archive else [],
                    "briefs":[json.loads(encode(item)) for item in briefs],"controls":[json.loads(encode(item)) for item in controls],
                    "budget":self.store.analysis_usage(now)}

    def enqueue(self,payload,now):
        if type(payload) is not dict or set(payload)!={"request_id","database_id","base_revision","settings"}:
            raise ValueError("Invalid command fields")
        settings=payload["settings"]
        if type(settings) is not dict or set(settings)!={"enabled","calendar_enabled","news_enabled","ai_enabled","mode","ai_profile"}:
            raise ValueError("Analysis settings only")
        if type(payload["base_revision"]) is not int:
            raise ValueError("Invalid revision")
        requested=IntelligenceSettings(**settings,revision=payload["base_revision"]+1)
        if requested.ai_profile is not None and requested.ai_profile not in self.profiles:
            raise ValueError("Unregistered AI profile")
        if requested.ai_enabled and (self.store.schema_version<4 or not self.profiles):
            raise ValueError("AI analysis is not configured for this store")
        request=ControlRequest(payload["request_id"],payload["database_id"],payload["base_revision"],now,now+timedelta(minutes=5),requested)
        with connection(self.store.path,expected_id=self.store.database_id) as handle:
            original=self.store._command(handle,request.request_id)
            if original is not None:
                if original[0].settings!=requested or original[0].database_id!=request.database_id or original[0].base_revision!=request.base_revision:
                    raise ValueError("Conflicting request ID")
                return original[1]
        return self.store.enqueue(request,now)