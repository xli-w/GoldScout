import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { label, timestamp } from "./api";
import { CalendarDays, Newspaper, ShieldAlert, RefreshCw } from "lucide-react";
import { calendarMatches, publicSourceURL as safeSourceURL, validPublicFoundation } from "./intelligence-model";
import "./intelligence.css";

type Settings={enabled:boolean;calendar_enabled:boolean;news_enabled:boolean;ai_enabled:boolean;mode:string;ai_profile:string|null;revision:number};
type PublicProfile={name:string;kind:string;model:string;key_env?:string;port?:number};
type ManagedConfig={enabled:boolean;calendar_sources:string[];content_sources:string[];profile:PublicProfile|null};
type Setup={available:boolean;state:string;config:ManagedConfig|null;worker_running:boolean;error_code:string|null;
  applied_settings?:Settings|null;control?:{status:string;effective_revision:number|null;reason:string}|null;
  catalog:{calendar:{source:string;geography:string}[];content:{source:string;institution:string;kind:string;topic:string;geography:string;supported:boolean;reason:string|null}[]}};
type Evidence={evidence_id:string;source?:string;source_url?:string;url?:string;title:string;snippet?:string;excerpt?:string;text?:string;published_at?:string|null;available_at?:string;received_at?:string;content_hash?:string};
type Foundation={schema_capable:boolean;briefing:null|{briefing_id:string;generated_at:string;provider:string;model:string;freshness?:string;profile_current?:boolean;verification?:string;
  manifest:{evidence:Evidence[];gaps:unknown[];omitted:number;input_hash:string;cutoff:string};
  output:{headline:string;developments:{title:string;topic:string;geography:string;claims:{text:string;evidence_ids:string[]}[];
    interpretation:{text:string;evidence_ids:string[]};uncertainties:string[]}[]}};
  source_health:{source:string;attempted_at:string|null;succeeded_at:string|null;error_code:string|null}[];
  operation:null|{operation_id:string;state:string;observed_at:string;error_code:string|null};
  refreshes?:{request_id:string;action:string;requested_at:string;state:string;error_code?:string|null}[]};
type Relevance={state:string;mapping_revision?:number;journal_revision?:string;matches?:{development_index:number;account_id:string;instrument_id?:string;reasons:string[];match_kind:string;review_question:string}[];unmapped?:unknown[];
  valuation?:{state:string;observed_at:string|null;total_gbp?:string};
  editor?:{exposures:{account_id:string;instrument_id?:string;label:string;classification:string;look_through:string}[];
    mappings:{account_id:string;instrument_id?:string;topics:string[];geographies:string[]}[];topics:string[];geographies:string[]}};
type ServiceState="unknown"|"unsupported"|"future"|"superseded"|"stale"|"recent";
type ServiceStatus={settings_revision:number;observed_at:string;network_permitted:boolean;calendar_sources:string[];news_permitted:boolean;ai_permitted:boolean;failures:{source:string;error_code:string;observed_at:string}[]};
type Data={source_mode:string;database_id:string;generated_at:string;settings:Settings;profiles:string[];controls_enabled:boolean;
  setup?:Setup;foundation?:Foundation;relevance?:Relevance;
  service_status_capable:boolean;service_status_state:ServiceState;service_status:ServiceStatus|null;
  source_status:{status:string;available_at:string;error_code:string|null}|null;
  calendar_sources:{source:string;checked_at:string|null;source_url:string|null;source_urls:string[]}[];
  calendar:{event_id:string;source:string;source_url:string;category:string;title:string;scheduled_at:string|null;
    status:string;source_timezone:string;source_notes:string[];symbols:string[]}[];
  calendar_total:number;calendar_truncated:boolean;
  upcoming_calendar?:Data["calendar"];upcoming_calendar_total?:number;upcoming_calendar_truncated?:boolean;
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
    if(value.setup){
      if(typeof value.setup.available!=='boolean'||typeof value.setup.state!=='string'
        ||!value.setup.catalog||!Array.isArray(value.setup.catalog.calendar)||!Array.isArray(value.setup.catalog.content)
        ||!validTimestamp(value.generated_at))throw new Error('Invalid setup status');
      if(value.setup.state!=='ready'&&!value.database_id)return value;
    }
    validateSnapshot(value);
  }
  return value;
}
const EMPTY_CONFIG:ManagedConfig={enabled:false,calendar_sources:["bls","bea"],content_sources:[],profile:null};
    function Citations({ids,evidence}:{ids:string[];evidence:Map<string,Evidence>}){
      return <ul className="claim-evidence" aria-label="Citations">{ids.map(id=><li key={id}>{evidence.has(id)
        ?<a href={`#public-evidence-${encodeURIComponent(id)}`}>{evidence.get(id)!.title}</a>
        :<span>Evidence unavailable: {id}</span>}</li>)}</ul>;
    }
    type Send=(path:string,payload:unknown)=>Promise<boolean>;
    function IntelligenceSetupPanel({setup,disabled,send}:{setup:Setup;disabled:boolean;send:Send}){
      const [draft,setDraft]=useState<ManagedConfig>(setup.config||EMPTY_CONFIG);
      const [saved,setSaved]=useState(false);
      useEffect(()=>{setDraft(setup.config||EMPTY_CONFIG);},[setup.config]);
      function sources(key:"calendar_sources"|"content_sources",source:string,checked:boolean){
        setSaved(false);setDraft(current=>({...current,[key]:checked?[...current[key],source]:current[key].filter(item=>item!==source)}));
      }
      const profile=draft.profile;
      function provider(kind:string){
        const key=kind==="anthropic"?"TRADER_AI_ANTHROPIC_KEY":kind==="gemini"?"TRADER_AI_GEMINI_KEY":"TRADER_AI_OPENAI_KEY";
        setDraft(current=>({...current,profile:kind?{name:"public-briefing",kind,model:"",
          ...(kind==="local"?{port:11434}:{key_env:key})}:null}));setSaved(false);
      }
      return <section className="intelligence-controls" id="intelligence-setup">
        <h2>Intelligence setup</h2>
        <p>Save a source selection and optionally an AI profile. Enabled settings are retained and start with this dashboard; disabling stops its worker.</p>
        {setup.applied_settings&&<p role="status">Saved selection: {setup.config?.enabled?"enabled":"disabled"} · Applied service settings: {setup.applied_settings.enabled?"enabled":"disabled"} at revision {setup.applied_settings.revision}
          {setup.control&&` · Latest settings request ${label(setup.control.status)}`}. Applied settings do not prove a running collector or complete coverage.</p>}
        {!setup.available?<p>Select a local portfolio workspace when launching the dashboard to set up separate public intelligence.</p>
          :setup.state==="not_initialized"?<><p>Create a separate public-evidence store; portfolio records stay separate.</p>
            <button disabled={disabled} onClick={()=>void send("setup",{action:"initialize"})}>Initialize intelligence</button></>
          :setup.state==="upgrade_required"?<><p>This public-evidence store needs an explicit schema upgrade. Existing evidence is retained.</p>
            <button disabled={disabled} onClick={()=>void send("setup",{action:"upgrade"})}>Upgrade intelligence store</button></>
          :setup.state!=="ready"?<p role="status">Intelligence setup could not be read. Check diagnostics and retry; no automatic repair is performed.</p>
          :<form onSubmit={event=>{event.preventDefault();void send("settings",draft).then(ok=>setSaved(ok));}}>
            <label className="intelligence-enable"><input type="checkbox" checked={draft.enabled} disabled={disabled}
              onChange={event=>{setDraft({...draft,enabled:event.target.checked});setSaved(false);}}/> Enable intelligence</label>
            <fieldset disabled={disabled}><legend>Calendar sources</legend><div className="intelligence-toggle-row">
              {setup.catalog.calendar.map(item=><label key={item.source}><input type="checkbox" checked={draft.calendar_sources.includes(item.source)}
                onChange={event=>sources("calendar_sources",item.source,event.target.checked)}/>{sourceName(item.source)} · {item.geography}</label>)}
            </div></fieldset>
            <fieldset disabled={disabled}><legend>Public release and policy sources</legend><div className="intelligence-source-options">
              {setup.catalog.content.map(item=><label key={item.source}><input type="checkbox" disabled={!item.supported}
                checked={draft.content_sources.includes(item.source)} onChange={event=>sources("content_sources",item.source,event.target.checked)}/>
                <span>{item.institution} · {label(item.source)} · {item.geography}<small>{item.reason||`${label(item.topic)} ${item.kind}; selected public content only.`}</small>
                  {!item.supported&&<small>Not supported; not collected.</small>}</span></label>)}
            </div></fieldset>
            <div className="intelligence-profile">
              <label>AI provider<select aria-label="AI provider" disabled={disabled} value={profile?.kind||""} onChange={event=>provider(event.target.value)}>
                <option value="">No AI — sources and calendar only</option><option value="openai">OpenAI</option><option value="anthropic">Anthropic</option>
                <option value="gemini">Gemini</option><option value="local">Local loopback</option></select></label>
              {profile&&<><label>Profile name<input required maxLength={128} value={profile.name} disabled={disabled}
                onChange={event=>setDraft({...draft,profile:{...profile,name:event.target.value}})}/></label>
                <label>Model<input required maxLength={128} value={profile.model} disabled={disabled}
                  onChange={event=>setDraft({...draft,profile:{...profile,model:event.target.value}})}/></label>
                {profile.kind==="local"?<label>Local port<input type="number" min={1024} max={65535} value={profile.port||11434} disabled={disabled}
                  onChange={event=>setDraft({...draft,profile:{...profile,port:Number(event.target.value)}})}/></label>
                  :<label>Credential reference<select value={profile.key_env} disabled={disabled}
                    onChange={event=>setDraft({...draft,profile:{...profile,key_env:event.target.value}})}>
                    {["TRADER_AI_OPENAI_KEY","TRADER_AI_ANTHROPIC_KEY","TRADER_AI_GEMINI_KEY"].map(name=><option key={name}>{name}</option>)}</select></label>}
              </>}
            </div>
            <p className="subtle">Credential names only. Enter or update key values in <a href="/settings">Settings</a>. Private portfolio evidence never goes to the briefing provider.</p>
            <button type="submit" disabled={disabled}>Save intelligence settings</button>{saved&&<p role="status">Intelligence settings saved.</p>}
          </form>}
      </section>;
    }

    function LocalRelevance({value,disabled,send,briefing}:{value:Relevance|undefined;disabled:boolean;send:Send;briefing:Foundation["briefing"]|undefined}){
      const [mappings,setMappings]=useState<NonNullable<Relevance["editor"]>["mappings"]>([]);
      useEffect(()=>{setMappings(value?.editor?.mappings||[]);},[value?.editor]);
      const editor=value?.editor;
      return <section className="position-section" id="intelligence-relevance">
        <h2>Locally relevant context</h2><p>Explicit topic/geography mappings are local context, not issuer coverage or a recommendation. Fund look-through is unknown.</p>
        {!value?<p>No local portfolio projection is configured. Public evidence is unchanged.</p>:<>
          <p>Projection: {label(value.state)} · Recorded valuation {label(value.valuation?.state||"unavailable")}
            {value.valuation?.observed_at&&` as at ${timestamp(value.valuation.observed_at)}`}</p>
          {(value.matches||[]).length===0?<p>No mapped context is available in this snapshot. This does not establish that holdings are unaffected.</p>
            :<div className="intelligence-local-matches">{(value.matches||[]).map((match,index)=>{
              const exposure=editor?.exposures.find(item=>item.account_id===match.account_id&&item.instrument_id===match.instrument_id);
              return <article className="intelligence-brief" key={index}><h3>{exposure?.label||"Recorded exposure"}</h3>
                <p>{briefing?.output.developments[match.development_index]?.title||"Public context"} · {label(match.match_kind)} context</p>
                <ul>{match.reasons.map((reason,i)=><li key={i}>{reason}</li>)}</ul><p>{match.review_question}</p></article>;
            })}</div>}
          {(value.unmapped||[]).length>0&&<p>{value.unmapped!.length} exposures have no matching explicit public-context mapping.</p>}
          {editor&&<details className="help-disclosure"><summary>Edit local relevance mappings</summary>
            <form onSubmit={event=>{event.preventDefault();void send("relevance",{expected_revision:value.mapping_revision,
              expected_journal_revision:value.journal_revision,mappings});}}>
              {editor.exposures.map(exposure=>{
                const index=mappings.findIndex(item=>item.account_id===exposure.account_id&&item.instrument_id===exposure.instrument_id);
                const current=index>=0?mappings[index]:{account_id:exposure.account_id,...(exposure.instrument_id?{instrument_id:exposure.instrument_id}:{}),topics:[],geographies:[]};
                function toggle(key:"topics"|"geographies",item:string,checked:boolean){
                  const next={...current,[key]:checked?[...current[key],item]:current[key].filter(value=>value!==item)};
                  setMappings(items=>index<0?[...items,next]:items.map((old,i)=>i===index?next:old));
                }
                return <fieldset key={`${exposure.account_id}-${exposure.instrument_id||""}`} disabled={disabled}><legend>{exposure.label} · {label(exposure.classification)}</legend>
                  <p className="subtle">Fund look-through: {exposure.look_through}</p><div className="intelligence-toggle-row">
                    {editor.topics.map(topic=><label key={topic}><input type="checkbox" checked={current.topics.includes(topic)}
                      onChange={event=>toggle("topics",topic,event.target.checked)}/>{label(topic)}</label>)}
                    {editor.geographies.map(geography=><label key={geography}><input type="checkbox" checked={current.geographies.includes(geography)}
                      onChange={event=>toggle("geographies",geography,event.target.checked)}/>{geography}</label>)}</div>
                  {index>=0&&<button type="button" onClick={()=>setMappings(items=>items.filter((_,i)=>i!==index))}>Remove declared mapping</button>}</fieldset>;
              })}
              <p className="subtle">Each declared mapping needs at least one topic and geography. Remove a mapping to clear it; class-based macro context may still be shown.</p>
              <button disabled={disabled||!editor.exposures.length||mappings.some(item=>!item.topics.length||!item.geographies.length)}>Save local mappings</button>
            </form></details>}
        </>}
      </section>;
    }

    export function IntelligenceView({data}:{data:ReturnType<typeof useIntelligence>}){
      const client=useQueryClient();
      const [error,setError]=useState("");
      const [pending,setPending]=useState(false);
      const [geography,setGeography]=useState("all");
      const [category,setCategory]=useState("all");
      const [timeFilter,setTimeFilter]=useState("upcoming");
      const session=useQuery({queryKey:["analysis-session"],queryFn:()=>read<Session>("/api/v1/intelligence/session"),refetchInterval:60000});
      const snapshot=data.data;
      async function send(path:string,payload:unknown){
        setPending(true);setError("");
        try{
          const response=await fetch(`/api/v1/intelligence/${path}`,{method:"POST",credentials:"same-origin",
            headers:{"Content-Type":"application/json","X-CSRF-Token":session.data?.csrf||""},body:JSON.stringify(payload)});
          if(!response.ok){
            const result=await response.json();
            throw new Error(result.error==="calendar_sources_required"?"Select at least one calendar source."
              :result.error==="briefing_content_source_required"?"Select at least one supported release or policy source for AI briefing."
              :"Request could not be completed. Refresh the saved state and retry.");
          }
          await client.invalidateQueries({queryKey:["intelligence"]});
          return true;
        }catch(failure){setError(failure instanceof Error?failure.message:"Request could not be completed. Refresh and retry.");return false;}
        finally{setPending(false);}
      }
      if(data.isPending)return <div className="loading" role="status">Loading intelligence</div>;
      if(!snapshot||data.isError)return <div className="error-state" role="alert"><h2>Intelligence unavailable</h2><button onClick={()=>void data.refetch()}>Retry</button></div>;
      // Keep the existing standalone CLI/demo controls without changing their gates.
      if(!snapshot.setup&&(snapshot.source_mode==="demo"||!snapshot.foundation?.schema_capable))return <LegacyIntelligenceView data={data}/>;
      const foundation=snapshot.foundation;
      const briefing=foundation?.briefing;
      const evidence=new Map((briefing?.manifest.evidence||[]).map(item=>[item.evidence_id,item]));
      const disabled=pending||!session.data?.csrf;
      const workerReady=!!snapshot.setup?.config?.enabled&&snapshot.setup?.state==="ready";
      const selectedCalendar=(timeFilter==="upcoming"?snapshot.upcoming_calendar:undefined)||snapshot.calendar||[];
      const events=selectedCalendar.filter(event=>calendarMatches(event,{geography,category,time:timeFilter,cutoff:snapshot.generated_at}));
      const calendarTotal=timeFilter==="upcoming"?(snapshot.upcoming_calendar_total??snapshot.calendar_total):snapshot.calendar_total;
      const calendarTruncated=timeFilter==="upcoming"?(snapshot.upcoming_calendar_truncated??snapshot.calendar_truncated):snapshot.calendar_truncated;
      return <>
        {snapshot.source_mode==="demo"&&<div className="demo-band">Synthetic intelligence</div>}
        <p className="intelligence-boundary">Public information and local context · advisory only · no portfolio writes or trading authority.</p>
        <nav className="intelligence-sections" aria-label="Intelligence sections"><div>
          <a href="#intelligence-briefing">Briefing</a><a href="#intelligence-relevance">Local relevance</a><a href="#intelligence-calendar">Upcoming</a>
          <a href="#intelligence-evidence">Evidence</a><a href="#intelligence-setup">Setup</a></div></nav>
        {error&&<p role="alert">{error}</p>}
        <section className="position-section intelligence-lead" id="intelligence-briefing">
          <div className="section-heading"><h2>Sourced briefing</h2>{snapshot.setup&&<div className="intelligence-actions">
            <button disabled={disabled||!workerReady} onClick={()=>void send("refresh",{action:"sources",request_id:crypto.randomUUID()})}>Refresh sources</button>
            <button disabled={disabled||!workerReady||!snapshot.setup.config?.profile} onClick={()=>void send("refresh",{action:"briefing",request_id:crypto.randomUUID()})}>Generate briefing</button>
          </div>}</div>
          {foundation?.operation&&<p role="status">Latest operation: {label(foundation.operation.state)} · {timestamp(foundation.operation.observed_at)}
            {foundation.operation.error_code&&` · ${label(foundation.operation.error_code)}`}</p>}
          {foundation?.refreshes?.[0]&&<p role="status">Latest refresh request: {label(foundation.refreshes[0].action)} · {label(foundation.refreshes[0].state)}
            {foundation.refreshes[0].error_code&&` · ${label(foundation.refreshes[0].error_code)}`}. Queued requests need a running intelligence service.</p>}
          {!briefing?<div className="intelligence-empty"><h3>No sourced briefing recorded</h3>
            <p>{snapshot.setup?.state!=="ready"?"Complete intelligence setup below.":!snapshot.setup?.config?.enabled?"Enable intelligence below to collect selected public sources.":"Refresh selected sources, then generate a briefing with a configured AI profile."} Missing evidence is not evidence that nothing changed.</p></div>
            :<>
              <h3>{briefing.output.headline}</h3>
              <p className="status caution">{label(briefing.verification||"unverified")} · {label(briefing.freshness||"freshness_unknown")}
                {briefing.profile_current===false&&" · Generated with a previous profile"}</p>
              <p className="subtle">Generated {timestamp(briefing.generated_at)} · {briefing.provider} / {briefing.model} · Cutoff {timestamp(briefing.manifest.cutoff)}</p>
              {briefing.output.developments.map((development,index)=><article className="intelligence-brief" key={index}>
                <h3>{development.title}</h3><p className="subtle">{development.geography} · {label(development.topic)}</p>
                {development.claims.map((claim,i)=><div className="intelligence-claim" key={i}><blockquote>{claim.text}</blockquote><Citations ids={claim.evidence_ids} evidence={evidence}/></div>)}
                <h4>Interpretation</h4><p className="subtle">Selected from grounded topic templates, not free-form validated facts.</p>
                <p>{development.interpretation.text}</p><Citations ids={development.interpretation.evidence_ids} evidence={evidence}/>
                <ul aria-label="Uncertainties">{development.uncertainties.map((text,i)=><li key={i}>{text}</li>)}</ul>
              </article>)}
              <details className="help-disclosure"><summary>Coverage gaps and provenance</summary>
                <p>{briefing.manifest.omitted} documents omitted. Source selection and source checks do not establish complete UK, US, global or issuer coverage.</p>
                {briefing.manifest.gaps.map((gap,index)=><p key={index}>{typeof gap==="string"?gap:JSON.stringify(gap)}</p>)}
                <p className="subtle">Briefing {briefing.briefing_id} · Input {briefing.manifest.input_hash}</p></details>
            </>}
        </section>
        <LocalRelevance value={snapshot.relevance} briefing={briefing} disabled={disabled||!snapshot.setup} send={send}/>
        <section className="position-section" id="intelligence-calendar">
          <h2><CalendarDays size={18}/> Upcoming calendar</h2>
          <div className="intelligence-filters">
            <label>Geography<select value={geography} onChange={event=>setGeography(event.target.value)}><option value="all">UK and US</option><option>UK</option><option>US</option></select></label>
            <label>Topic<select value={category} onChange={event=>setCategory(event.target.value)}><option value="all">All topics</option>
              {[...new Set((snapshot.calendar||[]).map(event=>event.category))].map(topic=><option key={topic} value={topic}>{label(topic)}</option>)}</select></label>
            <label>Dates<select value={timeFilter} onChange={event=>setTimeFilter(event.target.value)}><option value="upcoming">Upcoming / date unknown</option><option value="all">All recorded</option></select></label>
          </div>
          <p className="subtle">Recorded selected calendars only. Unknown dates remain visible. Missing events do not prove an empty schedule.</p>
          {calendarTruncated&&<p>Snapshot limited to {selectedCalendar.length} of {calendarTotal} recorded {timeFilter==="upcoming"?"upcoming ":""}events, before geography/topic filters.</p>}
          {events.length===0?<p>No recorded events match these filters.</p>:<div className="table-scroll" tabIndex={0} aria-label="Economic calendar"><table><thead><tr><th>Event</th><th>Scheduled (UTC)</th><th>Source / topic</th></tr></thead>
            <tbody>{events.map(event=><tr key={event.event_id}><td><a href={safeSourceURL(event.source_url)} target="_blank" rel="noreferrer">{event.title}</a>
              {event.source_notes.map((note,index)=><p key={index} className="subtle">{note}</p>)}</td><td>{timestamp(event.scheduled_at,"Unknown")}
              <small>{label(event.status)} · {event.source_timezone}</small></td><td>{sourceName(event.source)} · {label(event.category)}</td></tr>)}</tbody></table></div>}
        </section>
        <section className="position-section" id="intelligence-evidence"><h2><Newspaper size={18}/> Briefing evidence</h2>
          <p>The retained manifest belongs to this briefing. Later source refreshes do not replace its citations. Calendar schedules are not release outcomes.</p>
          {[...evidence.values()].map(item=><article className="intelligence-news" id={`public-evidence-${encodeURIComponent(item.evidence_id)}`} key={item.evidence_id}>
            <a href={safeSourceURL(item.source_url||item.url)} target="_blank" rel="noreferrer">{item.title}</a>
            <time>Published {timestamp(item.published_at||null,"Unknown")} · Received {timestamp(item.received_at||item.available_at||null,"Unknown")}</time>
            <p>{item.text||item.excerpt||item.snippet}</p><small>{item.source} · Evidence {item.evidence_id}</small>
          </article>)}
          {!evidence.size&&<p>No durable briefing citations are recorded yet.</p>}
        </section>
        {snapshot.setup?<IntelligenceSetupPanel setup={snapshot.setup} disabled={disabled} send={send}/>
          :<section className="position-section" id="intelligence-setup"><h2>Standalone intelligence store</h2>
            <p>Source and provider configuration are managed by the CLI for this explicitly selected store. Existing analysis controls are in diagnostics below.</p></section>}
        <details className="intelligence-diagnostics"><summary>Diagnostics and source checks</summary>
          <p>Supported source adapters are capabilities, not acquired coverage. A running dashboard worker is not proof of provider connectivity.</p>
          {snapshot.setup&&<p>Setup: {label(snapshot.setup.state)} · Dashboard worker {snapshot.setup.worker_running?"running":"stopped"}
            {snapshot.setup.error_code&&` · ${label(snapshot.setup.error_code)}`}</p>}
          {(foundation?.source_health||[]).map(source=><p key={source.source}>{sourceName(source.source)} · Attempt {timestamp(source.attempted_at)} · Success {timestamp(source.succeeded_at)}
            {source.error_code&&` · ${label(source.error_code)}`}</p>)}
          {!snapshot.setup&&<LegacyIntelligenceView data={data}/>}
        </details>
      </>;
    }
function validateSnapshot(value:Data){
    if(value.foundation&&!validPublicFoundation(value.foundation))throw new Error('Invalid public briefing');
    if(typeof value.database_id!=='string'||!validTimestamp(value.generated_at)||!value.settings||!Number.isInteger(value.settings.revision)
      ||!(['calendar','calendar_sources','news','briefs','controls','profiles'] as const).every(key=>Array.isArray(value[key])))throw new Error('Invalid intelligence');
    for(const flag of ['enabled','calendar_enabled','news_enabled','ai_enabled'] as const)if(typeof value.settings[flag]!=='boolean')throw new Error('Invalid settings');
    if(typeof value.service_status_capable!=='boolean'||!['unknown','unsupported','future','superseded','stale','recent'].includes(value.service_status_state)
      ||(value.news_last_success_at!==null&&!validTimestamp(value.news_last_success_at))
      ||!Number.isInteger(value.calendar_total)||value.calendar_total<value.calendar.length
      ||typeof value.calendar_truncated!=='boolean'||value.calendar_truncated!==(value.calendar_total>value.calendar.length)
      ||(value.service_status_capable?value.service_status_state==='unsupported':value.service_status_state!=='unsupported'))throw new Error('Invalid service status');
    if(value.service_status===null){
      if(!['unknown','unsupported'].includes(value.service_status_state))throw new Error('Missing service observation');
    }else{
      const observed=value.service_status;
      if(!observed||['unknown','unsupported'].includes(value.service_status_state)||!Number.isInteger(observed.settings_revision)||!validTimestamp(observed.observed_at)
        ||!(['network_permitted','news_permitted','ai_permitted'] as const).every(key=>typeof observed[key]==='boolean')
        ||!Array.isArray(observed.calendar_sources)||!observed.calendar_sources.every((source:unknown)=>typeof source==='string')
        ||!Array.isArray(observed.failures)
        ||observed.failures.some((item:{source:unknown;error_code:unknown;observed_at:unknown})=>!item||typeof item.source!=='string'||typeof item.error_code!=='string'||!validTimestamp(item.observed_at))
        ||(value.service_status_state==='recent'&&(observed.settings_revision!==value.settings.revision
          ||Date.parse(observed.observed_at)>Date.parse(value.generated_at))))throw new Error('Invalid service observation');
    }
    if(value.calendar_sources.some((item:{source:unknown;checked_at:unknown;source_url:unknown;source_urls:unknown})=>
      !item||typeof item.source!=='string'||(item.checked_at!==null&&!validTimestamp(item.checked_at))
      ||(item.source_url!==null&&typeof item.source_url!=='string')||!Array.isArray(item.source_urls)
      ||!item.source_urls.every((url:unknown)=>typeof url==='string')))throw new Error('Invalid calendar sources');
    if(value.calendar.some((item:{event_id:unknown;source:unknown;source_url:unknown;category:unknown;title:unknown;
      scheduled_at:unknown;status:unknown;source_timezone:unknown;source_notes:unknown;symbols:unknown})=>
      !item||typeof item.event_id!=='string'||typeof item.source!=='string'||typeof item.source_url!=='string'
      ||typeof item.category!=='string'||typeof item.title!=='string'
      ||(item.scheduled_at!==null&&!validTimestamp(item.scheduled_at))||typeof item.status!=='string'
      ||typeof item.source_timezone!=='string'||!Array.isArray(item.source_notes)
      ||!item.source_notes.every((note:unknown)=>typeof note==='string')
      ||!Array.isArray(item.symbols)||!item.symbols.every((symbol:unknown)=>typeof symbol==='string')))throw new Error('Invalid calendar event');
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
  if(source==="ons")return "ONS selected releases";
  if(source==="boe_mpc")return "Bank of England MPC dates";
  return source.toUpperCase();
}
export function useIntelligence(enabled:boolean){
  return useQuery({queryKey:["intelligence"],queryFn:()=>read<Data>("/api/v1/intelligence"),refetchInterval:5000,enabled});
}

function LegacyIntelligenceView({data}:{data:ReturnType<typeof useIntelligence>}){
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
        Portfolio awareness is separate and local. Never send private account evidence to hosted AI.</p>
        <div className="intelligence-source-details" role="region" aria-label="Current source coverage">
          <h3>Current source coverage</h3>
          <p>Supported adapters include U.S. BLS/BEA calendars, selected ONS releases and Bank of England MPC dates. Supported adapters are not evidence of acquired coverage; use the recorded source checks below. UK/global and issuer coverage remain incomplete; events are not automatically linked to holdings.</p>
        </div>
        <div className="intelligence-source-details" role="region" aria-label="Source status">
          <h3>Source status</h3>
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
        </div>
      </div>
    </section>
    <nav className="intelligence-sections" aria-label="Intelligence sections">
      <div><span>Evidence</span><a href="#intelligence-calendar">Calendar</a><a href="#intelligence-briefs">AI briefs</a>
        <a href="#intelligence-news">Source evidence</a></div>
      <div><span>Settings</span><a href="#intelligence-controls">Analysis controls</a></div>
    </nav>
    <section className="position-section" id="intelligence-calendar">
      <div className="section-heading"><h2><CalendarDays size={18}/> Economic calendar</h2><span className="status caution">Coverage unverified</span></div>
      {snapshot.calendar_sources.length>0&&<div className="snapshot-strip" aria-label="Calendar source observations">
        {snapshot.calendar_sources.map(source=><span key={source.source}>{sourceName(source.source)} last saved source check: {timestamp(source.checked_at)}
          {(source.source_urls.length>0?source.source_urls:source.source_url?[source.source_url]:[]).map((url,index)=>
            <a key={url} href={url} target="_blank" rel="noreferrer"> Source {index+1}</a>)}</span>)}
        <span>A later failure may appear in Source status.</span>
      </div>}
      {snapshot.calendar_truncated&&<p className="subtle">Showing the next {snapshot.calendar.length} of {snapshot.calendar_total} recorded calendar events.</p>}
      <div className="table-scroll" tabIndex={0} aria-label="Economic calendar" aria-describedby="table-scroll-instructions"><table><thead><tr><th>Event</th><th>Scheduled (UTC)</th><th>Category / instruments</th><th>Status</th></tr></thead><tbody>{snapshot.calendar.map(event=><tr key={event.event_id}>
        <td><a href={event.source_url} target="_blank" rel="noreferrer">{event.title}</a>
          {event.source_notes.map((note,index)=><p className="subtle" key={index}>{note}</p>)}</td>
        <td>{timestamp(event.scheduled_at,"Unknown")}<div className="subtle">Source zone: {event.source_timezone}</div></td>
        <td>{label(event.category)}<div className="subtle">{sourceName(event.source)}{event.symbols.length?` · ${event.symbols.join(", ")}`:""}</div></td>
        <td><span className="status caution">{label(event.status)}</span></td></tr>)}</tbody></table>{snapshot.calendar.length===0&&<div className="intelligence-empty"><h3>No calendar events recorded</h3><p>{calendarCollectionActive?"No events in this snapshot; coverage may be incomplete.":"Calendar collection is off in applied settings. This does not show whether events are scheduled."}</p></div>}</div>
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
          calendar preference {snapshot.settings.calendar_enabled?"on":"off"}; news preference {snapshot.settings.news_enabled?"on":"off"};
          AI {snapshot.settings.ai_enabled?"on":"off"}</strong></div>
        <div><span>Service</span><strong>{label(snapshot.service_status_state)}</strong></div>
      </div>
      <p className="subtle">Requested settings, applied settings and service observations are separate. Applied settings do not prove that collection or analysis is running.</p>
      <details className="help-disclosure"><summary>Settings revisions</summary>
        <p>Applied revision {snapshot.settings.revision}.{requested && ` Requested revision ${requested.settings.revision}.`}</p>
      </details>
      <div className="intelligence-toggle-row">{([['enabled','Market intelligence'],['calendar_enabled','Calendar collection'],['news_enabled','News collection'],['ai_enabled','AI summaries']] as const).map(([key,label])=><label key={key}><input type="checkbox" aria-label={label} aria-describedby={(key==='calendar_enabled'||key==='news_enabled')&&!snapshot.settings.enabled?'intelligence-master-note':displayed.ai_enabled&&(key==='enabled'||key==='ai_enabled')?'ai-shutdown-note':undefined} checked={displayed[key]} disabled={locked||(key==='ai_enabled'&&!displayed.ai_enabled&&!profileForEnable)} onChange={event=>change(key,event.target.checked)}/>{label}</label>)}</div>
      {!snapshot.settings.enabled&&<p className="subtle" id="intelligence-master-note">Calendar and news selections are inactive while Market intelligence is off in applied settings.</p>}
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