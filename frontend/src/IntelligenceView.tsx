import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { label, timestamp } from "./api";
import { CalendarDays, Newspaper, ShieldAlert, RefreshCw } from "lucide-react";
import "./intelligence.css";

type Settings={enabled:boolean;calendar_enabled:boolean;news_enabled:boolean;ai_enabled:boolean;mode:string;ai_profile:string|null;revision:number};
type Data={source_mode:string;database_id:string;generated_at:string;settings:Settings;profiles:string[];controls_enabled:boolean;source_status:{status:string;available_at:string;error_code:string|null}|null;calendar:{event_id:string;title:string;scheduled_at:string|null;symbols:string[]}[];news:{evidence_id:string;title:string;snippet:string;source_url:string;published_at:string|null}[];briefs:{brief_id:string;available_at:string;interpretation:string;uncertainties:string[];claims:{text:string;evidence_ids:string[]}[];recommendation:string;verification:string;expires_at:string;provider:string;model:string}[];controls:{request_id:string;status:string;reason:string;effective_revision:number|null}[];budget:{calls:number;input_bytes:number;output_allowance:number}|null};
type Session={enabled:boolean;csrf:string|null};
async function read<T>(path:string):Promise<T>{const response=await fetch(path,{credentials:"same-origin",cache:"no-store"});if(!response.ok)throw new Error("Intelligence unavailable");const value=await response.json();if(!value||typeof value!=='object')throw new Error('Invalid response');if(path.endsWith('/session')){if(typeof value.enabled!=='boolean'||(value.csrf!==null&&typeof value.csrf!=='string'))throw new Error('Invalid session');}else{if(typeof value.database_id!=='string'||!value.settings||!Number.isInteger(value.settings.revision)||!['calendar','news','briefs','controls','profiles'].every(key=>Array.isArray(value[key])))throw new Error('Invalid intelligence');for(const flag of ['enabled','calendar_enabled','news_enabled','ai_enabled'])if(typeof value.settings[flag]!=='boolean')throw new Error('Invalid settings');}return value;}
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
      <div><h2>Optional public information — not investment advice</h2><p>Calendar, news and AI information may be incomplete or unverified. It cannot change your records or make trades.
        Portfolio awareness is a separate local view that matches saved public information to your selected investments. Private account evidence must not be sent to hosted AI.</p></div>
    </section>
    <section className="intelligence-coverage" aria-label="Current source coverage">
      <h2>Current source coverage</h2>
      <p>U.S.-focused: BLS/BEA calendars and Federal Reserve monetary-policy RSS. UK/global calendars and issuer coverage are not established; coverage remains incomplete and unverified.</p>
    </section>
    <nav className="intelligence-sections" aria-label="Intelligence sections">
      <div><span>Evidence</span><a href="#intelligence-calendar">Calendar</a><a href="#intelligence-briefs">AI briefs</a>
        <a href="#intelligence-news">Source evidence</a></div>
      <div><span>Settings</span><a href="#intelligence-controls">Analysis controls</a></div>
    </nav>
    <section className="position-section" id="intelligence-calendar">
      <div className="section-heading"><h2><CalendarDays size={18}/> Economic calendar</h2><span className="status caution">Coverage unverified</span></div>
      <div className="table-scroll" tabIndex={0} aria-label="Economic calendar" aria-describedby="table-scroll-instructions"><table><thead><tr><th>Event</th><th>Scheduled (UTC)</th><th>Instruments</th></tr></thead><tbody>{snapshot.calendar.map(event=><tr key={event.event_id}><td>{event.title}</td><td>{timestamp(event.scheduled_at,"Unknown")}</td><td>{event.symbols.join(', ')}</td></tr>)}</tbody></table>{snapshot.calendar.length===0&&<div className="intelligence-empty"><h3>No calendar events recorded</h3><p>{calendarCollectionActive?"No events are recorded in this snapshot. Calendar coverage remains unverified; this does not establish that no events are scheduled.":"Calendar collection is off in the applied settings. This empty view does not establish that no events are scheduled."}</p></div>}</div>
    </section>
    <section className="position-section" id="intelligence-briefs">
      <div className="section-heading"><h2>AI briefs</h2><span className="subtle">{snapshot.budget?.calls??0} reserved calls today</span></div>
      {snapshot.briefs.length===0?<div className="intelligence-empty"><h3>No AI briefs recorded</h3><p>{briefGenerationActive?"No briefs are recorded in this snapshot. Enabled settings do not guarantee a completed analysis.":"Brief generation requires Market intelligence, News collection, and AI summaries in the applied settings. This empty list is not evidence that no relevant events exist."}</p></div>:snapshot.briefs.map(brief=><article className="intelligence-brief" key={brief.brief_id}>
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
      <div className="snapshot-strip" role="status"><span>Last news-source observation: {snapshot.source_status?label(snapshot.source_status.status):"Unknown"}</span><span>{timestamp(snapshot.source_status?.available_at||null)}</span></div>
      {snapshot.news.map(item=><article id={`evidence-${item.evidence_id}`} className="intelligence-news" key={item.evidence_id}><a href={item.source_url} target="_blank" rel="noreferrer">{item.title}</a><time>Published {timestamp(item.published_at,"Unknown")}</time><p>{item.snippet}</p></article>)}
      {snapshot.news.length===0&&<div className="intelligence-empty"><h3>No news evidence recorded</h3><p>{newsCollectionActive?`No news items are recorded in this snapshot. ${sourceObservation} News coverage may be incomplete.`:"News collection is off in the applied settings. No evidence here does not establish that no relevant news exists."}</p></div>}
    </section>
    <section className="intelligence-controls" id="intelligence-controls">
      <div className="section-heading"><div><h2>Analysis controls</h2><span className="subtle">Optional settings for collection and analysis</span></div><span className="status caution">No trading authority</span></div>
      <div className="intelligence-revisions" aria-label="Analysis settings status">
        <div><span>Latest request</span><strong>{requested?"Waiting for the service":latestControl?`${label(latestControl.status)} / ${latestControl.reason}`:"No recorded request"}</strong></div>
        <div><span>Applied settings</span><strong>{snapshot.settings.enabled?"Market intelligence enabled":"Market intelligence off"}</strong></div>
      </div>
      <p className="subtle">A request is not applied until the service confirms it. Applied settings do not prove that collection or analysis is running.</p>
      <details className="help-disclosure"><summary>Settings revisions</summary>
        <p>Applied revision {snapshot.settings.revision}.{requested && ` Requested revision ${requested.settings.revision}.`}</p>
      </details>
      <div className="intelligence-toggle-row">{([['enabled','Market intelligence'],['calendar_enabled','Calendar collection'],['news_enabled','News collection'],['ai_enabled','AI summaries']] as const).map(([key,label])=><label key={key}><input type="checkbox" aria-label={label} aria-describedby={displayed.ai_enabled&&(key==='enabled'||key==='ai_enabled')?'ai-shutdown-note':undefined} checked={displayed[key]} disabled={locked||(key==='ai_enabled'&&!displayed.ai_enabled&&!profileForEnable)} onChange={event=>change(key,event.target.checked)}/>{label}</label>)}</div>
      {displayed.ai_enabled&&<p className="subtle" id="ai-shutdown-note">Turning off AI summaries clears the saved AI profile. Turning off Market intelligence also turns off AI summaries.</p>}
      {!displayed.ai_enabled&&!profileForEnable&&<p className="subtle">AI summaries are unavailable. Configure one dashboard AI profile or register the saved profile.</p>}
      {!displayed.ai_enabled&&displayed.ai_profile&&!snapshot.profiles.includes(displayed.ai_profile)&&<p className="subtle">The saved AI profile is not registered and will be cleared when other settings change.</p>}
      {displayed.ai_enabled&&(!displayed.ai_profile||!snapshot.profiles.includes(displayed.ai_profile))&&<p className="subtle">The saved AI profile is not registered. Turn off AI summaries or Market intelligence before changing other settings.</p>}
      {!displayed.ai_enabled&&displayed.ai_profile&&latestControl?.status==='rejected'&&latestControl.reason==='AI profile is not registered'&&<p className="subtle">The service rejected the saved AI profile. <button disabled={locked} onClick={()=>change('ai_profile',null)}>Clear saved AI profile</button> and retry the setting.</p>}
      {requested&&<p role="status">Requested revision {requested.settings.revision}: awaiting application</p>}
      {locked&&<p className="control-lock-reason" role="status">{controlLockReason}</p>}
      {!snapshot.controls_enabled?<span className="status caution">Controls disabled</span>
        :session.isPending||session.isError?null
        :<span className="status observed">Enabled for this local session</span>}
      {error&&<p role="alert">{error}</p>}
      {latestControl&&<div className="subtle" role="status">Last request: {latestControl.status} / {latestControl.reason}</div>}
    </section>
  </>;
}