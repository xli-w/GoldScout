import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { label, timestamp } from "./api";
import { CalendarDays, Newspaper, ShieldAlert, RefreshCw } from "lucide-react";
import "./intelligence.css";

type Settings={enabled:boolean;calendar_enabled:boolean;news_enabled:boolean;ai_enabled:boolean;mode:string;ai_profile:string|null;revision:number};
type ServiceState="unknown"|"unsupported"|"future"|"superseded"|"stale"|"recent";
type ServiceStatus={settings_revision:number;observed_at:string;network_permitted:boolean;calendar_sources:string[];news_permitted:boolean;ai_permitted:boolean;failures:{source:string;error_code:string;observed_at:string}[]};
type Data={source_mode:string;database_id:string;generated_at:string;settings:Settings;profiles:string[];controls_enabled:boolean;
  service_status_capable:boolean;service_status_state:ServiceState;service_status:ServiceStatus|null;
  source_status:{status:string;available_at:string;error_code:string|null}|null;
  calendar_sources:{source:string;checked_at:string|null}[];calendar:{event_id:string;title:string;scheduled_at:string|null;symbols:string[]}[];
  news_last_success_at:string|null;news:{evidence_id:string;title:string;snippet:string;source_url:string;published_at:string|null}[];
  briefs:{brief_id:string;available_at:string;interpretation:string;uncertainties:string[];claims:{text:string;evidence_ids:string[]}[];recommendation:string;verification:string;expires_at:string;provider:string;model:string}[];
  controls:{request_id:string;status:string;reason:string;effective_revision:number|null}[];budget:{calls:number;input_bytes:number;output_allowance:number}|null};
type Session={enabled:boolean;csrf:string|null};
function validTimestamp(value:unknown):value is string{return typeof value==='string'&&Number.isFinite(Date.parse(value));}
async function read<T>(path:string):Promise<T>{
  const response=await fetch(path,{credentials:"same-origin",cache:"no-store"});
  if(!response.ok)throw new Error("Intelligence unavailable");
  const value=await response.json();
  if(!value||typeof value!=='object')throw new Error('Invalid response');
  if(path.endsWith('/session')){
    if(typeof value.enabled!=='boolean'||(value.csrf!==null&&typeof value.csrf!=='string'))throw new Error('Invalid session');
  }else{
    if(typeof value.database_id!=='string'||!validTimestamp(value.generated_at)||!value.settings||!Number.isInteger(value.settings.revision)
      ||!['calendar','calendar_sources','news','briefs','controls','profiles'].every(key=>Array.isArray(value[key])))throw new Error('Invalid intelligence');
    for(const flag of ['enabled','calendar_enabled','news_enabled','ai_enabled'])if(typeof value.settings[flag]!=='boolean')throw new Error('Invalid settings');
    if(typeof value.service_status_capable!=='boolean'||!['unknown','unsupported','future','superseded','stale','recent'].includes(value.service_status_state)
      ||(value.news_last_success_at!==null&&!validTimestamp(value.news_last_success_at))
      ||(value.service_status_capable?value.service_status_state==='unsupported':value.service_status_state!=='unsupported'))throw new Error('Invalid service status');
    if(value.service_status===null){
      if(!['unknown','unsupported'].includes(value.service_status_state))throw new Error('Missing service observation');
    }else{
      const observed=value.service_status;
      if(!observed||['unknown','unsupported'].includes(value.service_status_state)||!Number.isInteger(observed.settings_revision)||!validTimestamp(observed.observed_at)
        ||!['network_permitted','news_permitted','ai_permitted'].every(key=>typeof observed[key]==='boolean')
        ||!Array.isArray(observed.calendar_sources)||!observed.calendar_sources.every((source:unknown)=>typeof source==='string')
        ||!Array.isArray(observed.failures)
        ||observed.failures.some((item:{source:unknown;error_code:unknown;observed_at:unknown})=>!item||typeof item.source!=='string'||typeof item.error_code!=='string'||!validTimestamp(item.observed_at))
        ||(value.service_status_state==='recent'&&(observed.settings_revision!==value.settings.revision
          ||Date.parse(observed.observed_at)>Date.parse(value.generated_at))))throw new Error('Invalid service observation');
    }
    if(value.calendar_sources.some((item:{source:unknown;checked_at:unknown})=>!item||typeof item.source!=='string'||(item.checked_at!==null&&!validTimestamp(item.checked_at))))throw new Error('Invalid calendar sources');
  }
  return value;
}
const serviceStateText:Record<ServiceState,string>={
  unknown:"No service observation is recorded; activity is unknown.",
  unsupported:"Service observations require an explicit intelligence database upgrade.",
  future:"The observation is future-dated; activity is unknown.",
  superseded:"The observation predates applied settings; activity is unknown.",
  stale:"The last observation is stale; activity is unknown.",
  recent:"A recent service observation is recorded.",
};
function sourceName(source:string){
  if(source==="analysis")return "AI summaries";
  if(source==="fed_monetary")return "Federal Reserve news";
  return source.toUpperCase();
}
export function useIntelligence(enabled:boolean){
  return useQuery({queryKey:["intelligence"],queryFn:()=>read<Data>("/api/v1/intelligence"),refetchInterval:5000,enabled});
}

export function IntelligenceView({data}:{data:ReturnType<typeof useIntelligence>}){
  const client=useQueryClient();
  const session=useQuery({queryKey:["analysis-session"],queryFn:()=>read<Session>("/api/v1/intelligence/session"),refetchInterval:60000});
  const [error,setError]=useState("");const [pending,setPending]=useState(false);
  const [requested,setRequested]=useState<{id:string;settings:Settings}|null>(null);
  const snapshot=data.data;
  const profileForEnable=snapshot
    ? snapshot.settings.ai_profile&&snapshot.profiles.includes(snapshot.settings.ai_profile)
      ? snapshot.settings.ai_profile
      : snapshot.profiles.length===1?snapshot.profiles[0]:null
    : null;
  useEffect(()=>{if(!requested||!snapshot)return;const receipt=snapshot.controls.find(item=>item.request_id===requested.id);if(snapshot.settings.revision>=requested.settings.revision||(receipt&&['rejected','expired'].includes(receipt.status)))setRequested(null);},[snapshot,requested]);
  async function send(path:string,payload:unknown){setError("");setPending(true);try{const response=await fetch(path,{method:"POST",credentials:"same-origin",headers:{"Content-Type":"application/json","X-CSRF-Token":session.data?.csrf||""},body:JSON.stringify(payload)});if(!response.ok)throw new Error("Request rejected");await client.invalidateQueries({queryKey:["intelligence"]});await client.invalidateQueries({queryKey:["analysis-session"]});}catch{setRequested(null);setError("Request rejected. Refresh the recorded state.");}finally{setPending(false);}}
  function change(key:keyof Settings,value:unknown){
    if(!snapshot)return;
    const {revision,...current}=snapshot.settings;
    const settings={...current,[key]:value};
    if(key==='ai_enabled'&&value===true){
      if(!profileForEnable){setError("Configure one dashboard AI profile or register the saved profile.");return;}
      settings.ai_profile=profileForEnable;
    }else if((key==='ai_enabled'||key==='enabled')&&value===false){
      if(key==='enabled')settings.ai_enabled=false;
      settings.ai_profile=null;
    }else if(!settings.ai_enabled&&settings.ai_profile&&!snapshot.profiles.includes(settings.ai_profile)){
      settings.ai_profile=null;
    }
    const id=crypto.randomUUID();
    setRequested({id,settings:{...settings,revision:revision+1}});
    void send("/api/v1/intelligence/controls",{request_id:id,database_id:snapshot.database_id,base_revision:revision,settings});
  }
  if(data.isPending)return <div className="loading" role="status"><RefreshCw className="spin"/>Loading intelligence</div>;
  if(!snapshot||data.isError)return <div className="error-state" role="alert"><ShieldAlert/><h2>Intelligence unavailable</h2><button onClick={()=>void data.refetch()}><RefreshCw size={16}/>Retry</button></div>;
  const latestControl=snapshot.controls[0];
  const calendarCollectionActive=snapshot.settings.enabled&&snapshot.settings.calendar_enabled;
  const newsCollectionActive=snapshot.settings.enabled&&snapshot.settings.news_enabled;
  const briefGenerationActive=snapshot.settings.enabled&&snapshot.settings.news_enabled&&snapshot.settings.ai_enabled;
  const sourceObservation=snapshot.source_status
    ? `Last source observation: ${label(snapshot.source_status.status)}.`
    : "No source observation has been recorded.";
  const evidenceById=new Map(snapshot.news.map(item=>[item.evidence_id,item]));
  const controlLockReason=pending?"Submitting the settings request."
    :requested?`Waiting for revision ${requested.settings.revision} to be applied.`
    :!snapshot.controls_enabled?"Analysis controls are disabled by the dashboard configuration."
    :session.isPending?"Checking analysis-control access."
    :session.isError?"Analysis-control access could not be verified. Refresh and retry."
    :!session.data?.csrf?"Local control session is unavailable."
    :null;
  const locked=controlLockReason!==null;
  const displayed=requested?.settings||snapshot.settings;
  return <>
    {snapshot.source_mode==="demo"&&<div className="demo-band">Synthetic intelligence</div>}
    <section className="intelligence-advisory" aria-label="Advisory-only boundary">
      <ShieldAlert size={20}/>
      <div><h2>Optional public information — not investment advice</h2><p>Calendar, news and analysis may be incomplete or unverified; they cannot change records or make trades.
        Portfolio awareness is separate and local. Never send private account evidence to hosted AI.</p></div>
    </section>
    <section className="intelligence-coverage" aria-label="Current source coverage">
      <h2>Current source coverage</h2>
      <p>U.S.-focused calendars and Federal Reserve policy news. UK/global calendars and issuer coverage are unverified.</p>
    </section>
    <section className="intelligence-coverage" aria-label="Source status">
      <h2>Source status</h2>
      <p>{snapshot.source_mode==="demo"&&snapshot.service_status_state==="unknown"
        ?"Synthetic demo: service activity is unknown.":serviceStateText[snapshot.service_status_state]}</p>
      {snapshot.service_status&&<>
        {snapshot.service_status.failures.length>0&&<ul aria-label="Last reported failures">{snapshot.service_status.failures.map(failure=>
          <li key={failure.source}>{sourceName(failure.source)}: {label(failure.error_code)}; reported {timestamp(failure.observed_at)}. This may be historical.</li>)}</ul>}
        <details className="help-disclosure"><summary>Observation details</summary>
          <p>Observed {timestamp(snapshot.service_status.observed_at)} · Applied settings revision {snapshot.service_status.settings_revision}.</p>
          <p>Reported capabilities: network {snapshot.service_status.network_permitted?"permitted":"not permitted"};
            news {snapshot.service_status.news_permitted?"permitted":"not permitted"};
            AI {snapshot.service_status.ai_permitted?"permitted":"not permitted"}.
            Calendar sources: {snapshot.service_status.calendar_sources.map(sourceName).join(", ")}.</p>
        </details>
      </>}
      <p>This does not prove that the service is running now or that a provider is connected.</p>
    </section>
    <nav className="intelligence-sections" aria-label="Intelligence sections">
      <div><span>Evidence</span><a href="#intelligence-calendar">Calendar</a><a href="#intelligence-briefs">AI briefs</a>
        <a href="#intelligence-news">Source evidence</a></div>
      <div><span>Settings</span><a href="#intelligence-controls">Analysis controls</a></div>
    </nav>
    <section className="position-section" id="intelligence-calendar">
      <div className="section-heading"><h2><CalendarDays size={18}/> Economic calendar</h2><span className="status caution">Coverage unverified</span></div>
      {snapshot.calendar_sources.length>0&&<div className="snapshot-strip" aria-label="Calendar source observations">
        {snapshot.calendar_sources.map(source=><span key={source.source}>{sourceName(source.source)} last saved source check: {timestamp(source.checked_at)}</span>)}
        <span>A later failure may appear in Source status.</span>
      </div>}
      <div className="table-scroll" tabIndex={0} aria-label="Economic calendar" aria-describedby="table-scroll-instructions"><table><thead><tr><th>Event</th><th>Scheduled (UTC)</th><th>Instruments</th></tr></thead><tbody>{snapshot.calendar.map(event=><tr key={event.event_id}><td>{event.title}</td><td>{timestamp(event.scheduled_at,"Unknown")}</td><td>{event.symbols.join(', ')}</td></tr>)}</tbody></table>{snapshot.calendar.length===0&&<div className="intelligence-empty"><h3>No calendar events recorded</h3><p>{calendarCollectionActive?"No events in this snapshot; coverage may be incomplete.":"Calendar collection is off in applied settings. This does not show whether events are scheduled."}</p></div>}</div>
    </section>
    <section className="position-section" id="intelligence-briefs">
      <div className="section-heading"><h2>AI briefs</h2><span className="subtle">{snapshot.budget===null?"Usage unavailable":`${snapshot.budget.calls} reserved calls today`}</span></div>
      {snapshot.briefs.length===0?<div className="intelligence-empty"><h3>No AI briefs recorded</h3><p>{briefGenerationActive?"No briefs in this snapshot. Enabled settings do not guarantee analysis completed.":"Brief generation is off in applied settings. No briefs does not show whether relevant events exist."}</p></div>:snapshot.briefs.map(brief=><article className="intelligence-brief" key={brief.brief_id}>
        <div><span className="status caution">{Date.parse(brief.expires_at)<=Date.now()?'Expired':'Unverified'} / {brief.recommendation}</span><span className="subtle">{brief.provider} / {brief.model}</span></div>
        <p className="intelligence-brief-time">Available {timestamp(brief.available_at)} · Expires {timestamp(brief.expires_at)}</p>
        {brief.claims.map((claim,index)=><div className="intelligence-claim" key={index}>
          <blockquote>{claim.text}</blockquote>
          <ul className="claim-evidence" aria-label="Claim evidence">{claim.evidence_ids.map(evidenceId=>{
            const evidence=evidenceById.get(evidenceId);
            return <li key={evidenceId}>{evidence
              ? <a href={`#evidence-${evidenceId}`}>Evidence: {evidence.title}</a>
              : <span>Not in this snapshot: {evidenceId}</span>}</li>;
          })}</ul>
        </div>)}
        <p>{brief.interpretation}</p><ul>{brief.uncertainties.map(value=><li key={value}>{value}</li>)}</ul>
      </article>)}
    </section>
    <section className="position-section" id="intelligence-news">
      <div className="section-heading"><h2><Newspaper size={18}/> Source evidence</h2></div>
      <div className="snapshot-strip" role="status"><span>Last news-source observation: {snapshot.source_status?label(snapshot.source_status.status):"Unknown"}</span><span>{timestamp(snapshot.source_status?.available_at||null)}</span>
        {snapshot.source_status?.error_code&&<span>Last error: {label(snapshot.source_status.error_code)}</span>}
        <span>Last saved news evidence: {timestamp(snapshot.news_last_success_at)}</span></div>
      {snapshot.news.map(item=><article id={`evidence-${item.evidence_id}`} className="intelligence-news" key={item.evidence_id}><a href={item.source_url} target="_blank" rel="noreferrer">{item.title}</a><time>Published {timestamp(item.published_at,"Unknown")}</time><p>{item.snippet}</p></article>)}
      {snapshot.news.length===0&&<div className="intelligence-empty"><h3>No news evidence recorded</h3><p>{newsCollectionActive?`No news in this snapshot. ${sourceObservation} Coverage may be incomplete.`:"News collection is off in applied settings. This does not show whether relevant news exists."}</p></div>}
    </section>
    <section className="intelligence-controls" id="intelligence-controls">
      <div className="section-heading"><div><h2>Analysis controls</h2><span className="subtle">Optional settings for collection and analysis</span></div><span className="status caution">No trading authority</span></div>
      <div className="intelligence-revisions" aria-label="Analysis settings status">
        <div><span>Latest request</span><strong>{requested?`Awaiting revision ${requested.settings.revision}`:latestControl?`${label(latestControl.status)} / ${latestControl.reason}`:"No recorded request"}</strong></div>
        <div><span>Applied settings</span><strong>Revision {snapshot.settings.revision} · Market intelligence {snapshot.settings.enabled?"on":"off"};
          calendar {snapshot.settings.calendar_enabled?"on":"off"}; news {snapshot.settings.news_enabled?"on":"off"};
          AI {snapshot.settings.ai_enabled?"on":"off"}</strong></div>
        <div><span>Service</span><strong>{label(snapshot.service_status_state)}</strong></div>
      </div>
      <p className="subtle">Requested settings, applied settings and service observations are separate. Applied settings do not prove that collection or analysis is running.</p>
      <details className="help-disclosure"><summary>Settings revisions</summary>
        <p>Applied revision {snapshot.settings.revision}.{requested && ` Requested revision ${requested.settings.revision}.`}</p>
      </details>
      <div className="intelligence-toggle-row">{([['enabled','Market intelligence'],['calendar_enabled','Calendar collection'],['news_enabled','News collection'],['ai_enabled','AI summaries']] as const).map(([key,label])=><label key={key}><input type="checkbox" aria-label={label} aria-describedby={displayed.ai_enabled&&(key==='enabled'||key==='ai_enabled')?'ai-shutdown-note':undefined} checked={displayed[key]} disabled={locked||(key==='ai_enabled'&&!displayed.ai_enabled&&!profileForEnable)} onChange={event=>change(key,event.target.checked)}/>{label}</label>)}</div>
      {displayed.ai_enabled&&<p className="subtle" id="ai-shutdown-note">Turning off AI summaries clears the saved AI profile. Turning off Market intelligence also turns off AI summaries.</p>}
      {!displayed.ai_enabled&&!profileForEnable&&<p className="subtle">AI summaries are unavailable. Configure one dashboard AI profile or register the saved profile.</p>}
      {!displayed.ai_enabled&&displayed.ai_profile&&!snapshot.profiles.includes(displayed.ai_profile)&&<p className="subtle">The saved AI profile is not registered and will be cleared when other settings change.</p>}
      {displayed.ai_enabled&&(!displayed.ai_profile||!snapshot.profiles.includes(displayed.ai_profile))&&<p className="subtle">The saved AI profile is not registered. Turn off AI summaries or Market intelligence before changing other settings.</p>}
      {!displayed.ai_enabled&&displayed.ai_profile&&latestControl?.status==='rejected'&&latestControl.reason==='AI profile is not registered'&&<p className="subtle">The service rejected the saved AI profile. <button disabled={locked} onClick={()=>change('ai_profile',null)}>Clear saved AI profile</button> and retry the setting.</p>}
      {locked&&<p className="control-lock-reason" role="status">{controlLockReason}</p>}
      {!snapshot.controls_enabled?<span className="status caution">Controls disabled</span>
        :session.isPending||session.isError?null
        :<span className="status observed">Settings controls enabled for this session</span>}
      {error&&<p role="alert">{error}</p>}
    </section>
  </>;
}