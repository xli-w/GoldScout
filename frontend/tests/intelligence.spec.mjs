import { test, expect } from "@playwright/test";

const mockedFrontendURL=process.env.TRADER_INTELLIGENCE_TEST_URL||"http://127.0.0.1:8766";
test.use({baseURL:mockedFrontendURL});
test("synthetic intelligence controls and briefs work without account data",async({page},testInfo)=>{
  test.skip(testInfo.project.name!=="desktop","The synthetic demo has one mutable workspace");
  const errors=[];page.on("pageerror",error=>errors.push(error.message));
  await page.goto("/intelligence");
  const resetStatus=await page.evaluate(async()=>{const snapshot=await(await fetch('/api/v1/intelligence')).json();if(snapshot.source_mode!=='demo')throw new Error('Synthetic intelligence is required');const session=await(await fetch('/api/v1/intelligence/session')).json();const response=await fetch('/api/v1/intelligence/controls',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':session.csrf},body:JSON.stringify({request_id:crypto.randomUUID(),database_id:snapshot.database_id,base_revision:snapshot.settings.revision,settings:{enabled:false,calendar_enabled:true,news_enabled:true,ai_enabled:false,mode:'advisory',ai_profile:null}})});return response.status;});
  expect(resetStatus).toBe(202);
  await page.reload();
  await expect(page.getByRole("heading",{name:"Market intelligence",exact:true})).toBeVisible();
  await expect(page.getByText("Synthetic intelligence",{exact:true})).toBeVisible();
  await expect(page.getByRole("region",{name:"Current source coverage"})).toContainText("U.S.-focused: BLS/BEA calendars and Federal Reserve monetary-policy RSS.");
  await expect(page.getByRole("region",{name:"Current source coverage"})).toContainText("UK/global calendars and issuer coverage are not established");
  await expect(page.getByText("Last request: applied / Settings applied",{exact:true})).toBeVisible();
  const appliedChange=async action=>{
    const revisions=page.getByLabel("Analysis settings status");
    const revision=Number((await revisions.locator("> div").nth(1).locator("strong").innerText()).replace("Revision ",""));
    await action();
    await expect(revisions.locator("> div").nth(1)).toContainText(`Revision ${revision+1}`);
    await expect(page.getByText("Last request: applied / Settings applied",{exact:true})).toBeVisible();
  };
  let accountRequests=0;
  await page.route("**/api/v1/overview",route=>{accountRequests++;return route.abort();});
  await page.getByRole("button",{name:"Refresh data"}).click();
  await expect(page.getByRole("heading",{name:"Analysis controls"})).toBeVisible();
  expect(accountRequests).toBe(0);
  await expect(page.getByRole("combobox",{name:"AI provider"})).toHaveCount(0);
  const master=page.getByRole("checkbox",{name:"Market intelligence",exact:true});
  await expect(master).toBeEnabled();
  await appliedChange(()=>master.check());await expect(master).toBeChecked();
  const ai=page.getByRole("checkbox",{name:"AI summaries",exact:true});
  await expect(ai).toBeEnabled();
  await appliedChange(()=>ai.check());await expect(ai).toBeChecked();
  await expect(page.getByText("Rate-sensitive instruments may experience volatility around policy communication.").first()).toBeVisible();
  await appliedChange(()=>page.getByRole("radio",{name:"Shadow",exact:true}).check());
  await expect(page.getByRole("radio",{name:"Shadow",exact:true})).toBeChecked();
  await expect(master).toBeEnabled();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
  expect(errors).toEqual([]);
  await page.screenshot({path:testInfo.outputPath("intelligence.png"),fullPage:true});
  await appliedChange(()=>ai.uncheck());await expect(ai).not.toBeChecked();await expect(master).toBeEnabled();
  await appliedChange(()=>master.uncheck());await expect(master).not.toBeChecked();
});

async function mockIntelligence(page,profiles,serviceProfiles=null,authenticated=true){
  const snapshot={source_mode:"demo",database_id:"test",generated_at:"2026-03-08T09:30:00Z",settings:{enabled:false,calendar_enabled:true,news_enabled:true,ai_enabled:false,mode:"advisory",ai_profile:null,revision:0},profiles,controls_enabled:true,source_status:null,calendar:[],news:[],briefs:[],controls:[],budget:null,bot_observed_revision:null};
  const requests=[];
  await page.route("**/api/**",route=>route.abort());
  await page.route("**/api/v1/intelligence/session",route=>route.fulfill({json:{enabled:true,authenticated,csrf:"test"}}));
  await page.route("**/api/v1/intelligence",route=>route.fulfill({json:snapshot}));
  await page.route("**/api/v1/intelligence/controls",route=>{
    const request=route.request().postDataJSON();requests.push(request);
    const registered=serviceProfiles===null?snapshot.profiles:serviceProfiles;
    const accepted=request.settings.ai_profile===null||registered.includes(request.settings.ai_profile);
    if(accepted)snapshot.settings={...request.settings,revision:request.base_revision+1};
    snapshot.controls=[{request_id:request.request_id,status:accepted?"applied":"rejected",reason:accepted?"Settings applied":"AI profile is not registered",effective_revision:snapshot.settings.revision}];
    return route.fulfill({status:202,json:{status:"pending"}});
  });
  return {snapshot,requests};
}

test("intelligence header uses its own source instead of cached account status",async({page})=>{
  await page.goto("/legacy");
  await expect(page.getByRole("heading",{name:"Portfolio overview"})).toBeVisible();
  await expect(page.locator(".topbar-status")).toContainText("SYNTHETIC DEMO");
  const {snapshot}=await mockIntelligence(page,["demo"]);
  snapshot.source_mode="recorded";
  await page.getByRole("link",{name:"Intelligence",exact:true}).click();
  await expect(page.getByRole("heading",{name:"Economic calendar"})).toBeVisible();
  await expect(page.locator(".topbar-status")).toContainText("RECORDED INTELLIGENCE");
  await expect(page.locator(".topbar-status")).not.toContainText("Runtime");
  await expect(page.locator(".page-heading")).toContainText("INTELLIGENCE WORKSPACE");
  await page.route("**/api/v1/intelligence",route=>route.abort());
  await page.getByRole("button",{name:"Refresh data"}).click();
  await expect(page.locator(".topbar-status")).toContainText("Source unknown");
});

test("direct intelligence visit identifies synthetic source",async({page})=>{
  await mockIntelligence(page,["demo"]);
  await page.goto(new URL("/intelligence",mockedFrontendURL).href);
  await expect(page.locator(".topbar-status")).toContainText("SYNTHETIC DEMO");
  await expect(page.locator(".topbar-status")).not.toContainText("Runtime");
});

test("intelligence empty states distinguish disabled collection from missing evidence",async({page})=>{
  const {snapshot}=await mockIntelligence(page,["demo"]);
  await page.goto(new URL("/intelligence",mockedFrontendURL).href);
  const calendar=page.getByRole("heading",{name:"No calendar events recorded"}).locator("..");
  const briefs=page.getByRole("heading",{name:"No AI briefs recorded"}).locator("..");
  const news=page.getByRole("heading",{name:"No news evidence recorded"}).locator("..");
  await expect(calendar).toContainText("Calendar collection is off in the applied settings");
  await expect(briefs).toContainText("Brief generation requires Market intelligence, News collection, and AI summaries");
  await expect(news).toContainText("News collection is off in the applied settings");
  await expect(calendar).toContainText("does not establish that no events are scheduled");
  await expect(news).toContainText("does not establish that no relevant news exists");

  snapshot.settings={...snapshot.settings,enabled:true,ai_enabled:true,ai_profile:"demo"};
  snapshot.source_status={status:"unavailable",available_at:"2026-03-08T09:30:00Z",error_code:"feed_unavailable"};
  await page.reload();
  await expect(calendar).toContainText("No events are recorded in this snapshot");
  await expect(briefs).toContainText("Enabled settings do not guarantee a completed analysis");
  await expect(news).toContainText("Last source observation: Unavailable");
  await expect(news).toContainText("News coverage may be incomplete");
});

test("intelligence dates include the year and are explicitly UTC",async({page})=>{
  const {snapshot}=await mockIntelligence(page,["demo"]);
  snapshot.calendar=[{event_id:"event-1",title:"Synthetic event",scheduled_at:"2026-03-08T09:30:00Z",symbols:["SPY"]}];
  snapshot.news=[{evidence_id:"news-1",title:"Synthetic source",snippet:"Synthetic evidence",source_url:"https://example.test/source",published_at:"2026-03-08T09:30:00Z"}];
  await page.goto(new URL("/intelligence",mockedFrontendURL).href);
  await expect(page.getByRole("columnheader",{name:"Scheduled (UTC)"})).toBeVisible();
  await expect(page.getByRole("cell",{name:/Mar 8, 2026/})).toBeVisible();
  await expect(page.locator(".intelligence-news time")).toContainText("Published Mar 8, 2026");
  await expect(page.locator(".intelligence-news time")).toContainText("UTC");
});

test("intelligence briefs link only loaded evidence and show their validity window",async({page})=>{
  const {snapshot}=await mockIntelligence(page,["demo"],null,false);
  snapshot.source_status={status:"available",available_at:"2026-03-08T09:30:00Z",error_code:null};
  snapshot.news=[{evidence_id:"evidence-1",title:"Synthetic cited source",snippet:"Synthetic evidence",source_url:"https://example.test/source",published_at:"2026-03-08T09:00:00Z"}];
  snapshot.briefs=[{brief_id:"brief-1",available_at:"2026-03-08T09:31:00Z",expires_at:"2026-03-08T10:00:00Z",claims:[{text:"A cited synthetic claim.",evidence_ids:["evidence-1","missing-evidence"]}],interpretation:"Synthetic interpretation.",uncertainties:["Synthetic uncertainty."],recommendation:"review",verification:"unverified",provider:"synthetic",model:"test"}];
  await page.goto(new URL("/intelligence",mockedFrontendURL).href);

  await expect(page.locator(".page-description")).toHaveText("Optional calendar, news, and analysis. Advisory only and separate from account records.");
  await expect(page.getByRole("navigation",{name:"Advisory navigation"})).toContainText("Intelligence");
  await expect(page.locator(".intelligence-brief-time")).toContainText("Available Mar 8, 2026");
  await expect(page.locator(".intelligence-brief-time")).toContainText("Expires Mar 8, 2026");
  await expect(page.getByRole("link",{name:"Evidence: Synthetic cited source"})).toHaveAttribute("href","#evidence-evidence-1");
  await expect(page.getByText("Not in this snapshot: missing-evidence",{exact:true})).toBeVisible();
  const sourceSection=page.locator(".position-section").filter({has:page.getByRole("heading",{name:"Source evidence"})});
  await expect(sourceSection.locator(".snapshot-strip")).toContainText("Last news-source observation: Available");
  await expect(sourceSection.locator(".snapshot-strip")).toContainText("Mar 8, 2026");
  await page.getByRole("link",{name:"Evidence: Synthetic cited source"}).click();
  await expect(page).toHaveURL(/#evidence-evidence-1$/);
});

test("intelligence retains its last source during refresh and shows unknown after failure",async({page})=>{
  await mockIntelligence(page,["demo"]);
  await page.goto(new URL("/intelligence",mockedFrontendURL).href);
  await expect(page.locator(".topbar-status")).toContainText("SYNTHETIC DEMO");

  let markStarted;
  const started=new Promise(resolve=>{markStarted=resolve;});
  let releaseRequest;
  const held=new Promise(resolve=>{releaseRequest=resolve;});
  await page.route("**/api/v1/intelligence",async route=>{
    markStarted();
    await held;
    await route.abort();
  });
  await page.getByRole("button",{name:"Refresh data"}).click();
  await started;
  await expect(page.locator(".topbar-status")).toContainText("SYNTHETIC DEMO");
  await expect(page.locator(".topbar-status")).toContainText("Refreshing intelligence");
  releaseRequest();
  await expect(page.locator(".topbar-status")).toContainText("Source unknown");
  await expect(page.locator(".topbar-status")).toContainText("Source unavailable");
});

test("intelligence leads with evidence and separates request, application, and observation",async({page})=>{
  const {snapshot}=await mockIntelligence(page,["demo"],null,false);
  snapshot.settings.revision=7;
  snapshot.bot_observed_revision=5;
  snapshot.controls=[{request_id:"request-7",status:"applied",reason:"Settings applied",effective_revision:7}];
  await page.goto(new URL("/intelligence",mockedFrontendURL).href);

  await expect(page.getByRole("region",{name:"Advisory-only boundary"})).toContainText("never controls trading");
  expect(await page.evaluate(()=>Array.from(document.querySelectorAll(".intelligence-advisory, .position-section, .intelligence-controls")).map(section=>
    section.classList.contains("intelligence-advisory")?"advisory":section.classList.contains("intelligence-controls")?"controls":section.querySelector("h2")?.textContent?.trim()
  ))).toEqual(["advisory","Economic calendar","AI briefs","Source evidence","controls"]);
  const revisions=page.getByLabel("Analysis settings status");
  await expect(revisions).toContainText("Applied / Settings applied");
  await expect(revisions).toContainText("Revision 7");
  await expect(revisions).toContainText("Revision 5");
  await expect(page.getByText("Sign in to change analysis settings.",{exact:true})).toBeVisible();
  await expect(page.getByRole("textbox",{name:"Analysis-control password"})).toBeVisible();
});

test("disabled analysis controls explain the configuration lock",async({page})=>{
  const {snapshot}=await mockIntelligence(page,["demo"]);
  snapshot.controls_enabled=false;
  await page.goto(new URL("/intelligence",mockedFrontendURL).href);
  await expect(page.locator(".control-lock-reason")).toContainText("Analysis controls are disabled by the dashboard configuration.");
  await expect(page.getByRole("checkbox",{name:"Market intelligence",exact:true})).toBeDisabled();
});

test("configured AI profile is used without a provider dropdown",async({page})=>{
  const {requests}=await mockIntelligence(page,["demo"]);
  await page.goto(new URL("/intelligence",mockedFrontendURL).href);
  await expect(page.getByRole("combobox",{name:"AI provider"})).toHaveCount(0);
  const master=page.getByRole("checkbox",{name:"Market intelligence",exact:true});
  const ai=page.getByRole("checkbox",{name:"AI summaries",exact:true});
  await master.check();await expect(master).toBeEnabled();
  expect(requests[0].settings.ai_profile).toBeNull();
  await ai.check();await expect(ai).toBeEnabled();
  expect(requests[1].settings).toMatchObject({enabled:true,ai_enabled:true,ai_profile:"demo"});
  await ai.uncheck();await expect(ai).toBeEnabled();
  expect(requests[2].settings).toMatchObject({ai_enabled:false,ai_profile:null});
});

test("configured AI profile must be unambiguous and can be safely disabled",async({page})=>{
  const {snapshot,requests}=await mockIntelligence(page,[]);
  await page.goto(new URL("/intelligence",mockedFrontendURL).href);
  const ai=page.getByRole("checkbox",{name:"AI summaries",exact:true});
  await expect(ai).toBeDisabled();
  await expect(page.getByText("AI summaries are unavailable.",{exact:false})).toBeVisible();
  snapshot.profiles=["first","second"];
  await page.reload();await expect(ai).toBeDisabled();
  expect(requests).toHaveLength(0);
  snapshot.settings.ai_profile="retired";
  await page.reload();
  await expect(page.getByText("The saved AI profile is not registered and will be cleared",{exact:false})).toBeVisible();
  const calendar=page.getByRole("checkbox",{name:"Calendar collection",exact:true});
  await calendar.uncheck();await expect(calendar).toBeEnabled();
  expect(requests[0].settings).toMatchObject({calendar_enabled:false,ai_enabled:false,ai_profile:null});
  snapshot.settings.ai_profile="second";
  await page.reload();await expect(ai).toBeEnabled();
  await ai.check();await expect(ai).toBeEnabled();
  expect(requests[1].settings.ai_profile).toBe("second");
  snapshot.profiles=[];
  await page.reload();await expect(ai).toBeEnabled();
  await expect(page.getByText("The saved AI profile is not registered.",{exact:false})).toBeVisible();
  await ai.uncheck();await expect(ai).toBeDisabled();
  await expect.poll(()=>requests.length).toBe(3);
  expect(requests[2].settings).toMatchObject({ai_enabled:false,ai_profile:null});
  snapshot.profiles=["replacement"];
  await page.reload();await expect(ai).toBeEnabled();
  await ai.check();await expect(ai).toBeEnabled();
  expect(requests[3].settings).toMatchObject({ai_enabled:true,ai_profile:"replacement"});
});

for(const {scenario,label,profiles,masterEnabled} of [
  {scenario:"AI off with service profile missing",label:"AI summaries",profiles:["demo"],masterEnabled:true},
  {scenario:"master off with service profile missing",label:"Market intelligence",profiles:["demo"],masterEnabled:false},
  {scenario:"master off with dashboard profile missing",label:"Market intelligence",profiles:[],masterEnabled:false},
]){
  test(`safe shutdown: ${scenario}`,async({page})=>{
    const {snapshot,requests}=await mockIntelligence(page,profiles,[]);
    snapshot.settings={...snapshot.settings,enabled:true,ai_enabled:true,ai_profile:"demo",revision:1};
    await page.goto(new URL("/intelligence",mockedFrontendURL).href);
    const control=page.getByRole("checkbox",{name:label,exact:true});
    await expect(control).toBeChecked();
    await control.uncheck();
    await expect(page.getByLabel("Analysis settings status").locator("> div").nth(1)).toContainText("Revision 2");
    await expect(page.getByText("Last request: applied / Settings applied",{exact:true})).toBeVisible();
    expect(requests[0].settings).toMatchObject({enabled:masterEnabled,ai_enabled:false,ai_profile:null,calendar_enabled:true,news_enabled:true});
  });
}

test("safe recovery clears a rejected profile while both controls are off",async({page})=>{
  const {snapshot,requests}=await mockIntelligence(page,["demo"],[]);
  snapshot.settings={...snapshot.settings,enabled:false,ai_enabled:false,ai_profile:"demo",revision:1};
  await page.goto(new URL("/intelligence",mockedFrontendURL).href);
  const master=page.getByRole("checkbox",{name:"Market intelligence",exact:true});
  await master.click();
  await expect(page.getByText("Last request: rejected / AI profile is not registered",{exact:true})).toBeVisible();
  await expect(master).not.toBeChecked();
  expect(requests[0].settings.ai_profile).toBe("demo");
  const clear=page.getByRole("button",{name:"Clear saved AI profile",exact:true});
  await clear.click();
  await expect(page.getByLabel("Analysis settings status").locator("> div").nth(1)).toContainText("Revision 2");
  expect(requests[1].settings).toMatchObject({enabled:false,ai_enabled:false,ai_profile:null});
  await expect(clear).toHaveCount(0);
  await master.check();
  await expect(page.getByLabel("Analysis settings status").locator("> div").nth(1)).toContainText("Revision 3");
  expect(requests[2].settings).toMatchObject({enabled:true,ai_enabled:false,ai_profile:null});
});