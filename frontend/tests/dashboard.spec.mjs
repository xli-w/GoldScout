import { expect, test } from "@playwright/test";

test("monitoring shows synthetic findings and invalidates them on failed refresh", async ({ page }) => {
  let unavailable = false;
  await page.route("**/api/investment/v1/monitoring", async route => {
    if (unavailable) return route.fulfill({ status: 503, json: { error: "unavailable" } });
    return route.continue();
  });

  await page.goto("/investment");
  await page.locator("#investment-monitoring > summary").click();
  const section = page.getByRole("region", { name: "Portfolio monitoring", exact: true });
  await expect(section.getByText(/Synthetic monitoring demonstration/)).toBeVisible();
  await expect(section.getByRole("link", { name: "Open the separate review inbox" })).toHaveAttribute("href", "/investment/reviews");
  await expect(section.getByText("Provider connections are not verified. These checks use saved evidence only.")).toBeVisible();
  await expect(section.getByRole("list", { name: "Provider evidence status" })).toContainText("freshness");
  await expect(section.getByRole("list", { name: "Provider evidence status" })).toContainText("coverage");
  await section.getByText("Provider status and source details", { exact: true }).click();
  await expect(section.getByText(/blocked_unvalidated/)).toBeVisible();
  await expect(section.getByText(/Nonzero replayed cash-observation differences remain/)).toBeVisible();
  await expect(section.getByRole("checkbox")).not.toBeChecked();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBeTruthy();
  unavailable = true;
  await section.getByRole("button", { name: "Refresh checks", exact: true }).click();
  await expect(section.getByRole("alert")).toContainText("unavailable");
  await expect(section.getByText(/Nonzero replayed cash-observation differences remain/)).toHaveCount(0);
});

test("monitoring refuses mismatched revisions and malformed responses", async ({ page }) => {
  const report = await (await page.request.get("/api/investment/v1/monitoring")).json();
  let malformed = false;
  await page.route("**/api/investment/v1/monitoring", route => route.fulfill({
    json: malformed ? { ...report, dynamic_pct: "NaN" } : { ...report, portfolio_revision: report.portfolio_revision + 1 },
  }));
  await page.goto("/investment");
  await page.locator("#investment-monitoring > summary").click();
  const section = page.getByRole("region", { name: "Portfolio monitoring", exact: true });
  await expect(section.getByRole("alert")).toContainText("revision changed");
  await expect(section.getByText(/Synthetic monitoring demonstration/)).toHaveCount(0);
  malformed = true;
  await section.getByRole("button", { name: "Try again", exact: true }).click();
  await expect(section.getByRole("alert")).toContainText("unavailable");
  await expect(section.getByText(/Synthetic monitoring demonstration/)).toHaveCount(0);
});

test("portfolio shortcuts prioritize accounts and keep optional details available", async ({ page }) => {
  const snapshot = await (await page.request.get("/api/investment/v1/snapshot")).json();
  snapshot.source_mode = "recorded";
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: snapshot }));
  await page.goto("/investment");
  const sections = page.getByRole("navigation", { name: "Investment sections" });
  await expect(sections.getByText("Portfolio")).toBeVisible();
  await expect(sections.getByText("Records and checks")).toBeVisible();
  await expect(sections.getByRole("link", { name: "Accounts and investments" })).toHaveAttribute("href", "#investment-account-summary");
  await expect(page.getByRole("region", { name: "Accounts and investments" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Add a record", exact: true })).toBeVisible();
  await expect(page.locator("#investment-roles")).not.toHaveAttribute("open", "");
  await page.locator("#investment-roles > summary").focus();
  await page.keyboard.press("Enter");
  await expect(page.locator("#investment-roles")).toHaveAttribute("open", "");
  await expect(page.getByRole("heading", { name: "Management roles" })).toBeVisible();
  await sections.getByRole("link", { name: "Optional checks" }).click();
  await expect(page).toHaveURL(/#investment-monitoring$/);
  await expect(page.locator("#investment-monitoring")).toHaveAttribute("open", "");
  await sections.getByRole("link", { name: "Record history" }).click();
  await expect(page.locator("#investment-records")).toHaveAttribute("open", "");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBeTruthy();
  await expect(page.getByRole("link", { name: "Add a record", exact: true })).toBeVisible();
});

test("monitoring observation staging uses structured fields and readable reconciliation", async ({ page }) => {
  let submitted;
  await page.route("**/api/investment/v1/monitoring/reviews", route =>
    route.fulfill({ json: { enabled: false } }));
  await page.route("**/api/investment/v1/monitoring-workflow/session", route =>
    route.fulfill({ json: { enabled: true, csrf: "synthetic-csrf", cache_enabled: true,
      accounts: [{ account_id: "local-broker", name: "My brokerage", provider: "manual" }],
      products: [{ product_id: "fund", name: "Synthetic fund" }] } }));
  await page.route("**/api/investment/v1/monitoring-workflow/observation/preview", route => {
    submitted = route.request().postDataJSON();
    return route.fulfill({ json: {
      preview_id: "synthetic-preview", request_id: submitted.request_id,
      journal_revision: 4, cache_revision: 2, observation: submitted.observation,
      reconciliation: { status: "attention", revision: 4, generated_at: "2026-01-03T12:06:00Z",
        accounts: [], missing_recorded_accounts: ["bank"], limitations: ["Synthetic limitation."] },
      expires_at: "2026-01-03T12:10:00Z", investment_journal_changed: false,
    } });
  });
  await page.goto("/investment/reviews");
  await expect(page.getByRole("navigation", { name: "Monitoring workflow sections" })).toBeVisible();
  await expect(page.getByLabel("Holdings JSON list (source_product and quantity)")).toHaveCount(0);
  await expect(page.getByLabel("Account to compare")).toContainText("My brokerage");
  await page.getByLabel("Snapshot reference (source observation ID)", { exact: true }).fill("observation-1");
  await page.getByLabel("When do these figures apply? (leave blank if unknown)").fill("2026-01-03T12:00");
  await page.getByLabel("When was this source available?").fill("2026-01-03T12:05");
  await page.getByLabel("When was the account or investment match established?").fill("2026-01-03T11:00");
  await page.getByLabel("Where did these figures come from?").fill("Synthetic operator statement");
  await page.getByRole("button", { name: "Add observed holding" }).click();
  await page.getByLabel("Investment name in source").fill("Synthetic fund");
  await page.getByLabel("Number of units in source").fill("2");
  await page.getByRole("button", { name: "Add investment match" }).click();
  await page.getByLabel("Investment name to match").fill("Synthetic fund");
  await page.getByLabel("Recorded investment").selectOption("fund");
  await page.getByLabel("Source supporting this match").fill("Synthetic mapping note");
  await page.getByRole("button", { name: "Preview comparison" }).click();
  await expect.poll(() => submitted?.observation?.holdings?.length).toBe(1);
  expect(submitted.observation.holdings).toEqual([{ source_product: "Synthetic fund", quantity: "2" }]);
  expect(submitted.observation.observed_at).toMatch(/Z$/);
  expect(submitted.observation.available_at).toMatch(/Z$/);
  expect(submitted.mapping_observed_at).toMatch(/Z$/);
  expect(submitted.observation.product_mappings).toEqual([{
    source_product: "Synthetic fund", product_id: "fund", evidence: "Synthetic mapping note",
  }]);
  await expect(page.getByRole("region", { name: "Reconciliation results" })).toContainText("Attention");
  await expect(page.getByRole("region", { name: "Reconciliation results" })).toContainText("bank");
});

test("investment workspace opens the portfolio without trading requests", async ({ page }) => {
  let legacyRequests = 0;
  await page.route("**/api/v1/settings", route => route.fulfill({
    json: { mode: "recorded", scenario: null, workspace: "investment" },
  }));
  await page.route("**/api/v1/overview", route => {
    legacyRequests++;
    return route.fulfill({ status: 503, json: {} });
  });
  await page.goto("/");
  await expect(page).toHaveURL(/\/investment$/);
  await expect(page.getByRole("heading", { name: "Your portfolio", level: 1 })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Account navigation" })).toHaveCount(0);
  await expect(page.getByRole("navigation", { name: "Records navigation" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Trading demo", exact: true })).toHaveCount(0);
  expect((await page.request.get("/positions")).status()).toBe(404);
  expect(legacyRequests).toBe(0);
});

test("demo defaults to investment without a second trading product", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/investment$/);
  await page.getByText("Optional roles: core, dynamic and unassigned", { exact: true }).click();
  await expect(page.getByRole("heading", { name: "Management roles" })).toBeVisible();
  await expect(page.getByText(/Synthetic role attribution is shown only to demonstrate accounting/)).toBeVisible();
  await expect(page.getByRole("heading", { name: "Allocation and drift" })).toHaveCount(0);
  await expect(page.getByRole("navigation", { name: "Legacy navigation" })).toHaveCount(0);
  expect((await page.request.get("/legacy")).status()).toBe(404);
});

test("recording route clearly reports when local opt-in is disabled", async ({ page }) => {
  await page.goto("/investment/record");
  await expect(page.getByRole("heading", { name: "Add a record", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Recording is disabled" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "How to enable manual recording" })).toBeVisible();
  await expect(page.getByText(/Run your original startup command again/)).toBeVisible();
  await expect(page.getByText(/Retain your original/)).toBeVisible();
  await page.getByText("Why a restart is required", { exact: true }).click();
  await expect(page.getByText(/explicitly selected journal and local opt-in/)).toBeVisible();
  await expect(page.getByText(/--investment-recording/, { exact: false }).first()).toBeVisible();
  await expect(page.getByText(/Retain your original/)).toContainText("--workspace or --investment-database");
  await expect(page.getByRole("button", { name: "Preview record" })).toHaveCount(0);
});

test("next steps reflect incomplete evidence and historical differences without writes", async ({ page }) => {
  const report = await (await page.request.get("/api/investment/v1/snapshot")).json();
  const replay = structuredClone(report);
  expect(replay.complete).toBe(true);
  expect(replay.baseline_as_of).not.toBeNull();
  expect(replay.twr_pct).toBeNull();
  const originalHolding = { ...report.holdings[0] };
  report.source_mode = "recorded";
  report.complete = false;
  report.as_of = null;
  report.equity_gbp = null;
  report.baseline_as_of = null;
  report.since_baseline_gain_gbp = null;
  report.twr_pct = null;
  report.max_drawdown_pct = null;
  report.holdings[0] = { ...originalHolding, value_gbp: null, mark_at: null, mark_available_at: null };
  report.cash_observations = [];
  for (const account of report.accounts) account.cash_observation = null;
  let mutations = 0;
  page.on("request", request => { if (!["GET", "HEAD"].includes(request.method())) mutations++; });

  await page.route("**/api/v1/settings", route => route.fulfill({
    json: { mode: "recorded", scenario: null, workspace: "investment" },
  }));
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
  await page.goto("/investment");
  const steps = page.getByRole("region", { name: "What needs attention" });
  await expect(steps).toContainText("A complete total needs prices for every holding");
  await expect(steps).toContainText(originalHolding.name);
  await expect(steps.getByRole("link", { name: "Review holdings" })).toHaveAttribute("href", "#investment-holdings");
  await expect(steps.getByRole("link", { name: "Add a price record" })).toHaveAttribute("href", "/investment/record");
  await expect(steps).toContainText("A later price cannot fill an earlier gap");
  await expect(steps).toContainText("Performance tracking has no starting point yet");
  await expect(steps).not.toContainText("No cash observations have been recorded");
  await expect(steps).not.toContainText("Backup status is unknown");

  report.holdings[0] = { ...originalHolding, mark_available_at: report.generated_at };
  report.cash_observations = [
    { account_id: report.accounts[0].id, balance_gbp: "100", difference_gbp: "0.001",
      timestamp: "2026-01-01T12:00:00Z", available_at: "2026-01-01T12:00:00Z",
      source: "Synthetic statement", evidence: "Synthetic earlier difference" },
    { account_id: report.accounts[0].id, balance_gbp: "100", difference_gbp: "0",
      timestamp: "2026-01-02T12:00:00Z", available_at: "2026-01-02T12:00:00Z",
      source: "Synthetic statement", evidence: "Synthetic later match" },
  ];
  report.accounts[0].cash_observation = { ...report.cash_observations[1] };
  delete report.accounts[0].cash_observation.account_id;
  await page.getByRole("button", { name: "Refresh investment" }).click();
  await expect(steps).not.toContainText("A complete total needs prices for every holding");
  await expect(steps).toContainText("Review earlier cash differences");
  await expect(steps).toContainText("A later matching balance does not resolve them");
  await steps.getByRole("link", { name: "Review cash evidence" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.locator("#investment-accounts")).toHaveAttribute("open", "");
  await expect(page.getByRole("region", { name: "Cash evidence overview" })).toBeVisible();

  report.complete = replay.complete;
  report.as_of = replay.as_of;
  report.equity_gbp = replay.equity_gbp;
  report.baseline_as_of = replay.baseline_as_of;
  report.since_baseline_gain_gbp = replay.since_baseline_gain_gbp;
  report.twr_pct = replay.twr_pct;
  report.max_drawdown_pct = replay.max_drawdown_pct;
  report.holdings = replay.holdings;
  await page.getByRole("button", { name: "Refresh investment" }).click();
  await expect(steps).not.toContainText("complete portfolio total is unavailable");
  await expect(steps).toContainText("Return or largest fall is unavailable");
  await expect(steps).not.toContainText("Performance tracking has no starting point yet");
  await expect(steps).not.toContainText("Backup verified");
  expect(mutations).toBe(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await page.locator(".record-toolbar").getByRole("link", { name: "Help" }).click();
  await expect(page.getByRole("heading", { name: "Backup and recovery" })).toBeVisible();
  await expect(page.getByText(/GoldScout does not create or check backups/)).toBeVisible();
});

test("cash evidence overview labels replay history and surfaces correction limits", async ({ page }) => {
  const report = await (await page.request.get("/api/investment/v1/snapshot")).json();
  const correctionWarning =
    "Historical correction replay may have changed past cash-observation differences. Preserve prior reports and review the original observations and correction evidence.";
  report.warnings.push(correctionWarning);
  report.cash_observations = report.cash_observations.map(observation => ({
    ...observation, difference_gbp: "0",
  }));
  for (const account of report.accounts) {
    const latest = report.cash_observations.filter(observation =>
      observation.account_id === account.id).at(-1);
    account.cash_observation = latest ? { ...latest } : null;
    if (account.cash_observation) delete account.cash_observation.account_id;
  }
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
  await page.goto("/investment");
  await page.getByText("Cash observations and savings details", { exact: true }).click();
  const overview = page.getByRole("region", { name: "Cash evidence overview" });
  await expect(overview).toContainText("0 differing observations in current replayed history");
  await expect(overview).toContainText(correctionWarning);
});

test("demo attention panel stays focused while optional details remain accessible", async ({ page }) => {
  await page.goto("/investment");
  const steps = page.getByRole("region", { name: "What needs attention" });
  await expect(steps).toContainText("Synthetic demonstration");
  await expect(steps).not.toContainText("Backup status is unknown");
  await expect(steps).not.toContainText("Backup verified");
  await page.locator("#investment-roles > summary").click();
  await expect(page.getByText(/Unassigned means you have not chosen a role/)).toBeVisible();
  const contrast = await steps.evaluate(element => {
    const luminance = value => {
      const channels = value.match(/\d+/g).slice(0, 3).map(channel => {
        const n = Number(channel) / 255;
        return n <= 0.04045 ? n / 12.92 : ((n + 0.055) / 1.055) ** 2.4;
      });
      return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
    };
    const background = luminance(getComputedStyle(element).backgroundColor);
    const color = luminance(getComputedStyle(element).color);
    const link = luminance(getComputedStyle(element.querySelector("a")).color);
    return [color, link].map(value =>
      (Math.max(value, background) + 0.05) / (Math.min(value, background) + 0.05));
  });
  expect(contrast[0]).toBeGreaterThanOrEqual(4.5);
  expect(contrast[1]).toBeGreaterThanOrEqual(4.5);
});

test("unknown workspace does not guess an account source", async ({ page }) => {
  let legacyRequests = 0;
  await page.route("**/api/v1/settings", route => route.fulfill({ json: { workspace: ["demo"] } }));
  await page.route("**/api/v1/overview", route => { legacyRequests++; return route.abort(); });
  await page.goto("/");
  await expect(page.getByRole("alert")).toContainText("portfolio source is unknown");
  await expect(page.getByRole("navigation", { name: "Account navigation" })).toHaveCount(0);
  expect(legacyRequests).toBe(0);
});

test("portfolio awareness keeps matching local, shows evidence and fails closed on refresh", async ({ page }) => {
  await page.clock.install();
  const investment = await (await page.request.get("/api/investment/v1/snapshot")).json();
  const at = "2026-01-01T12:00:00Z";
  const matches = [{ account_id: "broker", product_id: "global-fund", roles: ["core"],
    mapping_evidence: "Operator-declared macro proxy; not fund look-through" }];
  const awareness = {
    enabled: true, schema_version: 2, source_mode: "recorded", portfolio_id: investment.portfolio_id,
    portfolio_revision: investment.revision, intelligence_database_id: "synthetic-intelligence",
    settings_revision: 1, profile_hash: "a".repeat(64), profile_observed_at: at, generated_at: at,
    calendar_status: "available", calendar_available_at: at, calendar_revision: 1,
    alerts: [{ event_id: "cpi", revision: 1, source: "bls", source_url: "https://www.bls.gov/schedule/",
      category: "inflation", title: "Synthetic inflation release", scheduled_at: null,
      available_at: at, status: "tentative", evidence_status: "unknown", matches }],
    briefs: [{
      brief_id: "brief", available_at: at, expires_at: "2026-01-01T12:30:00Z", status: "expired",
      verification: "unverified", provider: "local", model: "synthetic", prompt_version: "news-extractive-v1",
      input_hash: "b".repeat(64), interpretation: "Unverified synthetic interpretation.",
      uncertainties: ["Limited source coverage."], recommendation: "review", symbols: ["SPY"], matches,
      claims: [{ text: "Synthetic public statement.", evidence_ids: ["source-1"] }],
      sources: [{ evidence_id: "source-1", source_url: "https://www.federalreserve.gov/",
        title: "Public statement", published_at: at, received_at: at }],
    }],
    reminders: [{ id: "review", title: "Portfolio mandate check", last_reviewed_at: null,
      interval_days: 30, evidence: "Operator-selected reminder only", due_at: null, status: "never_reviewed" }],
    theses: [{ id: "case", title: "Synthetic long-term case", account_id: "broker", product_id: "global-fund",
      history: [
        { observed_at: "2025-12-31T12:00:00Z", status: "watch", thesis: "Initial hypothesis",
          invalidation: "Evidence contradicts premise", evidence: "Operator note one" },
        { observed_at: at, status: "active", thesis: "Updated hypothesis",
          invalidation: "Evidence contradicts premise", evidence: "Operator note two" },
      ] }],
    calendar_watch: [{ id: "uk", title: "Synthetic UK release", account_id: "broker", product_id: "global-fund",
      scheduled_on: "2026-12-17", time_note: "Time not stated", status: "tentative",
      observed_at: at, source: "Operator-checked calendar" }],
    warnings: ["No complete UK/global source coverage is established."],
    affects_trading: false, accounting_writes: false,
  };
  let requests = 0;
  let unavailable = false;
  await page.route("**/api/investment/v2/awareness", route => {
    requests++;
    return route.fulfill(unavailable ? { status: 503, json: { error: "unavailable" } } : { json: awareness });
  });
  await page.goto("/investment");
  await page.getByText("Optional context: news, calendar and reminders", { exact: true }).click();
  const section = page.getByRole("region", { name: "Portfolio awareness", exact: true });
  await expect(section.getByText("Synthetic inflation release", { exact: false })).toBeVisible();
  await expect(section.getByText(/Time unknown/)).toBeVisible();
  await expect(section.getByText(/No complete UK\/global/)).toBeVisible();
  await expect(section.getByRole("checkbox")).not.toBeChecked();
  await page.clock.fastForward(61000);
  expect(requests).toBe(1);
  await section.getByRole("checkbox").check();
  await page.clock.fastForward(61000);
  await expect.poll(() => requests).toBeGreaterThan(1);
  await section.getByRole("checkbox").uncheck();
  await section.getByText(/Expired \/ unverified/).click();
  await expect(section.getByText("Unverified synthetic interpretation.")).toBeVisible();
  await expect(section.getByRole("link", { name: "Public statement" })).toHaveAttribute("href", "https://www.federalreserve.gov/");
  await expect(section.getByText(/Portfolio mandate check/)).toBeVisible();
  await section.getByText("Synthetic long-term case - Active").click();
  await expect(section.getByText("Thesis: Updated hypothesis")).toBeVisible();
  await expect(section.getByText("Thesis: Initial hypothesis")).toBeVisible();
  await expect(section.getByText("Operator evidence note: Operator note two")).toBeVisible();
  const manualEvent = section.getByRole("listitem").filter({ hasText: "Synthetic UK release" });
  await expect(manualEvent).toContainText("2026-12-17");
  await expect(manualEvent).toContainText("Time not stated");
  const history = structuredClone(awareness.theses[0].history);
  for (const invalid of [
    [history[1], history[0]],
    [history[0], history[0]],
    [history[0], { ...history[1], observed_at: "2026-01-01T12:00:00.000001Z" }],
  ]) {
    awareness.theses[0].history = invalid;
    await section.getByRole("button", { name: "Refresh awareness" }).click();
    await expect(section.getByRole("alert")).toHaveText("Portfolio awareness response failed validation.");
    await expect(section.getByText("Updated hypothesis", { exact: false })).toHaveCount(0);
  }
  awareness.theses[0].history = history;
  awareness.theses = [awareness.theses[0], { ...awareness.theses[0], history }];
  await section.getByRole("button", { name: "Refresh awareness" }).click();
  await expect(section.getByRole("alert")).toHaveText("Portfolio awareness response failed validation.");
  awareness.theses = [awareness.theses[0]];
  awareness.calendar_watch.push({ ...awareness.calendar_watch[0] });
  await section.getByRole("button", { name: "Refresh awareness" }).click();
  await expect(section.getByRole("alert")).toHaveText("Portfolio awareness response failed validation.");
  awareness.calendar_watch.pop();
  awareness.profile_observed_at = "2026-01-01T12:00:00.000002Z";
  awareness.generated_at = awareness.profile_observed_at;
  awareness.theses[0].history = [
    { ...history[0], observed_at: "2026-01-01T12:00:00.000001Z" },
    { ...history[1], observed_at: "2026-01-01T12:00:00.000002Z" },
  ];
  await section.getByRole("button", { name: "Refresh awareness" }).click();
  await section.getByText("Synthetic long-term case - Active").click();
  await expect(section.getByText(/2026-01-01T12:00:00.000001Z - Watch/)).toBeVisible();
  awareness.profile_observed_at = at;
  awareness.generated_at = at;
  awareness.theses[0].history = history;
  await section.getByRole("button", { name: "Refresh awareness" }).click();
  await expect(section.getByText("Synthetic UK release", { exact: false })).toBeVisible();
  awareness.portfolio_revision++;
  await section.getByRole("button", { name: "Refresh awareness" }).click();
  await expect(section.getByRole("alert")).toContainText("Portfolio revision changed");
  await expect(section.getByText("Synthetic inflation release", { exact: false })).toHaveCount(0);
  await expect(section.getByText("Updated hypothesis", { exact: false })).toHaveCount(0);
  awareness.portfolio_revision--;
  awareness.affects_trading = true;
  await section.getByRole("button", { name: "Refresh awareness" }).click();
  await expect(section.getByRole("alert")).toHaveText("Portfolio awareness response failed validation.");
  await expect(section.getByText("Synthetic inflation release", { exact: false })).toHaveCount(0);
  unavailable = true;
  await section.getByRole("button", { name: "Refresh awareness" }).click();
  await expect(section.getByRole("alert")).toContainText("Portfolio awareness unavailable");
});

test("investment demo separates contributions, GBP valuation and blocked proposals", async ({ page }) => {
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto("/");
  await expect(page).toHaveURL(/\/investment$/);
  await expect(page.getByRole("heading", { name: "Your portfolio", level: 1 })).toBeVisible();
  await expect(page.getByText("SYNTHETIC INVESTMENT DEMO")).toBeVisible();
  await expect(page.getByText("Deposits are not profit")).toBeVisible();
  await page.getByText("Optional roles: core, dynamic and unassigned", { exact: true }).click();
  await expect(page.getByRole("heading", { name: "Management roles" })).toBeVisible();
  await expect(page.getByText("Example data, not your accounts or an investment recommendation.")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Allocation and drift" })).toHaveCount(0);
  await page.getByText("Cash observations and savings details", { exact: true }).click();
  await expect(page.getByText(/External savings are not broker buying power/)).toBeVisible();
  await page.getByText("Proposal status and capability limits", { exact: true }).click();
  await expect(page.getByText(/No order approval or execution is available here/)).toBeVisible();
  await page.getByRole("button", { name: "Refresh investment" }).click();
  await expect(page.locator(".metrics")).toContainText("GBP");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  expect(errors).toEqual([]);
});

test("help remains usable when recorded investment data is unavailable", async ({ page }) => {
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ status: 503, json: { error: "investment_unavailable" } }));
  await page.goto("/investment");
  await expect(page.getByRole("heading", { name: "Investment data unavailable" })).toBeVisible();
  await page.locator(".record-toolbar").getByRole("link", { name: "Help" }).click();
  await expect(page.getByRole("heading", { name: "About your records" })).toBeVisible();
  await expect(page.getByText(/Investment values can fall, and no return is guaranteed/)).toBeVisible();
});

test("investment browser rejects the retired journal report format", async ({ page }) => {
  const report = await (await page.request.get("/api/investment/v1/snapshot")).json();
  report.schema_version = 1;
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
  await page.goto("/investment");
  await expect(page.getByRole("heading", { name: "Investment data unavailable" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Investment totals" })).toHaveCount(0);
});

test("investment browser rejects invalid money instead of displaying an invented total", async ({ page }) => {
  const response = await page.request.get("/api/investment/v1/snapshot");
  expect(response.ok()).toBeTruthy();
  const report = await response.json();
  report.equity_gbp = "NaN";
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
  await page.goto("/investment");
  await expect(page.getByRole("heading", { name: "Investment data unavailable" })).toBeVisible();
  await expect(page.getByText("Investment response failed validation.")).toBeVisible();
  await expect(page.getByText("Portfolio value", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Next steps" })).toHaveCount(0);
});

test("opening records show since-baseline value without inventing lifetime gain", async ({ page }) => {
  await page.clock.setFixedTime(new Date("2026-01-05T12:00:00Z"));
  const at = "2026-01-01T12:00:00Z";
  const report = {
    schema_version: 2, source_mode: "recorded", portfolio_id: "synthetic", revision: 1,
    generated_at: at, as_of: at, baseline_as_of: at, currency: "GBP", complete: true,
    equity_gbp: "2000", net_contributions_gbp: "0", since_baseline_gain_gbp: "0",
    lifetime_gain_gbp: null, twr_pct: "0", max_drawdown_pct: "0",
    recorded_fees_gbp: "1.5", recorded_interest_gbp: "2",
    recorded_dividends_gbp: "3", recorded_income_gbp: "5",
    recorded_fee_events: [{
      id: "synthetic-buy", type: "buy", account_id: "broker", product_id: "fund",
      quantity: "1", price_gbp: "10", fee_gbp: "0.5",
      timestamp: at, available_at: at, evidence: "synthetic fill evidence", role: "unassigned",
    }, {
      id: "synthetic-sell", type: "sell", account_id: "broker", product_id: "fund",
      quantity: "1", price_gbp: "11", fee_gbp: "1",
      timestamp: at, available_at: at, evidence: "synthetic later fill evidence", role: "unassigned",
    }],
    recorded_income_events: [{
      id: "synthetic-interest", type: "interest", account_id: "bank",
      product_id: null, amount_gbp: "2", timestamp: at, available_at: at,
      evidence: "synthetic payment evidence", role: "unassigned", role_evidence: null,
    }, {
      id: "synthetic-dividend", type: "dividend", account_id: "broker",
      product_id: "fund", amount_gbp: "3", timestamp: at, available_at: at,
      evidence: "synthetic later payment evidence", role: "unassigned", role_evidence: null,
    }],
    recorded_flow_events: [{
      id: "synthetic-contribution", type: "contribution", account_id: "bank",
      destination_account_id: null, amount_gbp: "10", timestamp: at,
      available_at: at, evidence: "synthetic contribution evidence", role: "unassigned",
    }, {
      id: "synthetic-transfer", type: "transfer", account_id: "bank",
      destination_account_id: "broker", amount_gbp: "5", timestamp: at,
      available_at: at, evidence: "synthetic transfer evidence", role: "unassigned",
    }, {
      id: "synthetic-withdrawal", type: "withdrawal", account_id: "bank",
      destination_account_id: null, amount_gbp: "10", timestamp: at,
      available_at: at, evidence: "synthetic withdrawal evidence", role: "unassigned",
    }],
    recorded_corrections: [], recorded_profile_changes: [],
    recorded_scope_additions_gbp: "0", recorded_scope_additions: [],
    cash_observations: [
      { account_id: "bank", balance_gbp: "990", difference_gbp: "-10",
        timestamp: "2025-12-01T12:00:00Z", available_at: "2025-12-01T12:00:00Z",
        source: "prior synthetic statement", evidence: "prior synthetic evidence" },
      { account_id: "bank", balance_gbp: "999", difference_gbp: "-1",
        timestamp: at, available_at: at, source: "synthetic statement",
        evidence: "synthetic evidence" },
    ],
    valuation_history: [
      { timestamp: at, source_available_at: at, as_of: at, complete: true,
        equity_gbp: "2000", capital_basis_gbp: "2000", net_contributions_gbp: "0",
        gain_since_baseline_gbp: "0", twr_pct: "0", max_drawdown_pct: "0",
        gap_reason: null },
      { timestamp: "2026-01-01T18:00:00Z", source_available_at: "2026-01-01T18:00:00Z",
        as_of: "2026-01-01T18:00:00Z", complete: true, equity_gbp: "2100",
        capital_basis_gbp: "2000", net_contributions_gbp: "0",
        gain_since_baseline_gbp: "100", twr_pct: "5", max_drawdown_pct: "0",
        gap_reason: null },
      { timestamp: "2026-01-02T12:00:00Z", source_available_at: "2026-01-02T12:00:00Z",
        as_of: null, complete: false, equity_gbp: null, capital_basis_gbp: null,
        net_contributions_gbp: null, gain_since_baseline_gbp: null, twr_pct: null,
        max_drawdown_pct: null, gap_reason: "Incomplete synthetic valuation." },
      { timestamp: "2026-01-03T12:00:00Z", source_available_at: "2026-01-03T12:00:00Z",
        as_of: "2026-01-03T12:00:00Z", complete: true, equity_gbp: "1920",
        capital_basis_gbp: "2000", net_contributions_gbp: "0",
        gain_since_baseline_gbp: "-80", twr_pct: "-4", max_drawdown_pct: "8.57",
        gap_reason: null },
      { timestamp: "2026-01-04T12:00:00Z", source_available_at: "2026-01-04T12:00:00Z",
        as_of: "2026-01-04T12:00:00Z", complete: true, equity_gbp: "2020",
        capital_basis_gbp: "2100", net_contributions_gbp: "100",
        gain_since_baseline_gbp: "-80", twr_pct: null, max_drawdown_pct: null,
        gap_reason: null },
    ],
    accounts: [{ id: "bank", name: "Savings", kind: "deposit", status: "active", cash_gbp: "1000",
      cash_observation: { balance_gbp: "999", difference_gbp: "-1", timestamp: at,
        available_at: at, source: "synthetic statement", evidence: "synthetic evidence" },
      terms: { annual_rate_pct: "4.25", rate_basis: "AER", effective_from: "2026-01-01",
        observed_at: at, available_at: at, source: "synthetic statement",
        maturity_date: "2027-01-01", notice_days: 90 } },
      { id: "broker", name: "Broker", kind: "broker", status: "active", cash_gbp: "500",
        cash_observation: null, terms: null }],
    holdings: [{ account_id: "broker", product_id: "fund", name: "Fund", sleeve: "equity",
      quantity: "50", value_gbp: "500", mark_at: at, mark_available_at: at }],
    role_accounting: [
      { role: "core", cash_gbp: "0", value_gbp: "0", accounts: [], holdings: [] },
      { role: "dynamic", cash_gbp: "0", value_gbp: "0", accounts: [], holdings: [] },
      { role: "unassigned", cash_gbp: "1500", value_gbp: "2000", accounts: [], holdings: [] },
    ],
    role_accounting_at: null, role_assignments: [],
    proposal: { status: "blocked", reason: "Policy is not implemented", expires_at: null,
      orders: [], manual_transfers: [], execution_enabled: false },
    warnings: [], limitations: ["Synthetic test evidence."],
  };
  let opened = true;
  let latestCorrectable = {
    id: "synthetic-interest", type: "interest", account_id: "bank",
    currency: "GBP", amount_gbp: "2", timestamp: at, available_at: at,
    evidence: "synthetic payment evidence",
  };
  let previewEvent;
  await page.route("**/api/investment/v1/recording/session", route => route.fulfill({ json: {
    enabled: true, csrf: "synthetic-csrf", opened,
    accounts: [{ id: "bank", name: "Savings", kind: "deposit" },
      { id: "broker", name: "Broker", kind: "broker" }],
    products: [{ id: "fund", name: "Fund", sleeve: "equity", quantity_increment: "1",
      minimum_notional_gbp: "1" }],
    latest_correctable_event: latestCorrectable,
    correctable_events: latestCorrectable ? [latestCorrectable] : [],
    current_balances: { bank: "1000", broker: "500" }, current_holdings: [],
  } }));
  await page.route("**/api/investment/v1/recording/preview", async route => {
    const request = route.request().postDataJSON();
    previewEvent = request.event;
    await route.fulfill({ json: {
      preview_id: `preview-${request.event.id}`, revision: report.revision,
      event: request.event, before: { equity_gbp: "2000" },
      after: { equity_gbp: "2000" }, expires_at: "2026-01-05T12:05:00Z",
    } });
  });
  await page.route("**/api/investment/v1/recording/confirm", async route => {
    if (previewEvent.type === "correction") latestCorrectable = null;
    else latestCorrectable = {
      id: previewEvent.id, type: previewEvent.type, account_id: previewEvent.account_id,
      currency: previewEvent.currency, amount_gbp: previewEvent.amount_gbp, timestamp: previewEvent.timestamp,
      available_at: previewEvent.available_at, evidence: previewEvent.evidence, role: previewEvent.role,
    };
    await route.fulfill({ json: { id: previewEvent.id, revision: report.revision + 1 } });
  });
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
  await page.goto("/investment");
  const totals = page.getByRole("region", { name: "Investment totals" });
  await expect(totals.getByText("Gain since tracking began")).toBeVisible();
  await expect(totals.getByText("£2,000.00")).toBeVisible();
  await page.locator("#investment-performance > summary").click();
  await expect(page.getByRole("heading", { name: "Recorded growth history" })).toBeVisible();
  await expect(page.getByText("These are recorded values, not live prices. Lines join recorded points; missing information stays as a gap.")).toBeVisible();
  const returnChart = page.getByRole("img", { name: "Time-weighted return since baseline; unavailable returns are gaps" });
  await expect(returnChart).toBeVisible();
  await expect(returnChart.locator("circle")).toHaveCount(3);
  await expect(returnChart.locator("polyline")).toHaveCount(1);
  await expect(returnChart.locator(".return-chart-gap")).toHaveCount(2);
  const historyDisclosure = page.getByText("Detailed history and calculation notes", { exact: true });
  await expect(historyDisclosure.locator("..")).not.toHaveAttribute("open", "");
  await historyDisclosure.click();
  await expect(page.getByText("Gap: Incomplete synthetic valuation.")).toBeVisible();
  await expect(page.getByLabel("Recorded growth history").getByRole("columnheader", { name: "Maximum drawdown" })).toBeVisible();
  await expect(page.getByLabel("Recorded growth history").getByRole("row").nth(2)).toContainText("8.57%");
  const historyTable = page.getByLabel("Recorded growth history");
  await expect(historyTable.getByRole("columnheader", { name: "Return since previous clock" })).toBeVisible();
  await expect(historyTable.getByRole("row").nth(4).getByRole("cell").nth(7)).toHaveText("5.00%From 2026-01-01T12:00:00Z");
  await expect(historyTable.getByRole("row").nth(2).getByRole("cell").nth(7)).toHaveText("Unavailable");
  await expect(historyTable.getByRole("row").nth(5).getByRole("cell").nth(7)).toHaveText("Unavailable");
  const history = report.valuation_history;
  report.valuation_history = history.map((point, index) => ({
    ...point, twr_pct: index === 0 ? "100" : index === 1 ? "110" : point.twr_pct,
  }));
  await page.getByRole("button", { name: "Refresh investment" }).click();
  await expect(historyTable.getByRole("row").nth(4).getByRole("cell").nth(7)).toHaveText("5.00%From 2026-01-01T12:00:00Z");
  report.valuation_history = history.map((point, index) => ({
    ...point, twr_pct: index === 0 ? "-100" : index === 1 ? "0" : point.twr_pct,
  }));
  await page.getByRole("button", { name: "Refresh investment" }).click();
  await expect(historyTable.getByRole("row").nth(4).getByRole("cell").nth(7)).toHaveText("Unavailable");
  report.valuation_history = history.map((point, index) => ({
    ...point, twr_pct: index === 0 ? "0" : index === 1 ? "-100" : point.twr_pct,
  }));
  await page.getByRole("button", { name: "Refresh investment" }).click();
  await expect(historyTable.getByRole("row").nth(4).getByRole("cell").nth(7)).toHaveText("-100.00%From 2026-01-01T12:00:00Z");
  report.valuation_history = ["0", "10", null, "10"].map((twr, index) => {
    const time = new Date(Date.UTC(2026, 0, index + 1, 12)).toISOString();
    return { ...history[0], timestamp: time, source_available_at: time, as_of: time,
      equity_gbp: index === 2 ? "0" : "1000", twr_pct: twr,
      max_drawdown_pct: twr === null ? null : "0" };
  });
  await page.getByRole("button", { name: "Refresh investment" }).click();
  await expect(historyTable.getByRole("row").nth(1).getByRole("cell").nth(7)).toHaveText("Unavailable");
  await expect(historyTable.getByRole("row").nth(2).getByRole("cell").nth(7)).toHaveText("Unavailable");
  await expect(historyTable.getByRole("row").nth(3).getByRole("cell").nth(7))
    .toHaveText("10.00%From 2026-01-01T12:00:00.000Z");
  report.valuation_history = Array.from({ length: 21 }, (_, index) => {
    const time = new Date(Date.UTC(2026, 0, 1, index)).toISOString();
    return { ...history[0], timestamp: time, source_available_at: time, as_of: time,
      twr_pct: String(index) };
  });
  await page.getByRole("button", { name: "Refresh investment" }).click();
  await expect(historyTable.getByRole("row")).toHaveCount(21);
  await expect(historyTable.getByRole("row").nth(20).getByRole("cell").nth(7))
    .toHaveText("1.00%From 2026-01-01T00:00:00.000Z");
  const preciseReturns = ["1", "1.00000000000000000001", null,
    "1.00000000000000000002", "1.00000000000000000003"];
  report.valuation_history = history.map((point, index) => {
    const time = `2026-01-03T12:00:00.10000${index + 1}Z`;
    return { ...point, timestamp: time, source_available_at: time,
      as_of: point.complete ? time : null, twr_pct: preciseReturns[index] };
  });
  await page.getByRole("button", { name: "Refresh investment" }).click();
  await expect(returnChart.locator("polyline")).toHaveCount(2);
  const preciseChart = await returnChart.evaluate(svg => ({
    circles: [...svg.querySelectorAll("circle")].map(circle => [Number(circle.getAttribute("cx")), Number(circle.getAttribute("cy"))]),
    gap: Number(svg.querySelector(".return-chart-gap").getAttribute("x1")),
    lines: [...svg.querySelectorAll("polyline")].map(line => line.getAttribute("points")),
  }));
  expect(preciseChart.circles.every(([x, y]) => Number.isFinite(x) && Number.isFinite(y))).toBeTruthy();
  expect(new Set(preciseChart.circles.map(([x]) => x)).size).toBe(4);
  expect(preciseChart.circles[1][0]).toBeLessThan(preciseChart.gap);
  expect(preciseChart.circles[2][0]).toBeGreaterThan(preciseChart.gap);
  expect(preciseChart.lines.every(points => !points.includes("NaN") && !points.includes("Infinity"))).toBeTruthy();
  await expect(page.getByLabel("Recorded growth history")).toContainText("2026-01-03T12:00:00.100001Z");
  const closeTimes = ["2026-01-01T00:00:00Z", "2026-01-01T23:59:59Z",
    "2026-01-02T00:00:00Z", "2026-01-02T00:00:01Z", "2026-01-03T00:00:00Z"];
  report.valuation_history = history.map((point, index) => {
    const time = closeTimes[index];
    return { ...point, timestamp: time, source_available_at: time,
      as_of: point.complete ? time : null, twr_pct: point.complete ? "1" : null };
  });
  await page.getByRole("button", { name: "Refresh investment" }).click();
  await expect(returnChart.locator("polyline")).toHaveCount(2);
  const renderedGap = await returnChart.evaluate(async svg => {
    const clone = svg.cloneNode(true);
    clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
    clone.querySelectorAll(".return-chart-gap").forEach(line => line.remove());
    const point = clone.querySelectorAll("circle")[1];
    const x = Math.round(Number(point.getAttribute("cx")));
    const y = Math.round(Number(point.getAttribute("cy")));
    const sample = async () => {
      const image = new Image();
      const source = new XMLSerializer().serializeToString(clone);
      await new Promise((resolve, reject) => {
        image.onload = resolve; image.onerror = reject;
        image.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(source)}`;
      });
      const canvas = document.createElement("canvas");
      canvas.width = 800; canvas.height = 260;
      const context = canvas.getContext("2d");
      context.drawImage(image, 0, 0);
      return [...context.getImageData(x, y, 1, 1).data];
    };
    const masked = await sample();
    clone.querySelector("g[mask]").removeAttribute("mask");
    return { masked, unmasked: await sample() };
  });
  expect(renderedGap.unmasked.slice(0, 3)).toEqual([33, 116, 95]);
  expect(renderedGap.masked).not.toEqual(renderedGap.unmasked);
  report.valuation_history = history.map(point => ({ ...point, twr_pct: null, max_drawdown_pct: null }));
  await page.getByRole("button", { name: "Refresh investment" }).click();
  await expect(page.getByText("No time-weighted return is available in the recent history.")).toBeVisible();
  await expect(returnChart).toHaveCount(0);
  report.valuation_history = history;
  await page.getByRole("button", { name: "Refresh investment" }).click();
  await expect(returnChart).toBeVisible();
  await page.locator("#investment-records > summary").click();
  await expect(page.getByText(/Lifetime gain and prior cost basis are unavailable/)).toBeVisible();
  const attribution = page.getByRole("region", { name: "Recorded fees and income" });
  await expect(attribution.getByText("Recorded transaction fees")).toBeVisible();
  await expect(attribution.getByText("Recorded interest")).toBeVisible();
  await expect(attribution.getByText("Recorded dividends")).toBeVisible();
  await expect(attribution.getByText("Recorded income total")).toBeVisible();
  const feeHistory = page.getByLabel("Recorded fill fee history").getByRole("table");
  await expect(feeHistory.getByRole("columnheader", { name: "Effective at (UTC)" })).toBeVisible();
  await expect(feeHistory.getByRole("row").nth(1)).toContainText("synthetic fill evidence");
  await expect(feeHistory.getByRole("row").nth(2)).toContainText("synthetic later fill evidence");
  const incomeHistory = page.getByLabel("Recorded income history").getByRole("table");
  await expect(incomeHistory.getByRole("columnheader", { name: "Effective at (UTC)" })).toBeVisible();
  await expect(incomeHistory.getByRole("row").nth(1)).toContainText("synthetic payment evidence");
  await expect(incomeHistory.getByRole("row").nth(2)).toContainText("synthetic later payment evidence");
  const flowHistory = page.getByLabel("Recorded capital flow history").getByRole("table");
  await expect(flowHistory.getByRole("columnheader", { name: "Effective at (UTC)" })).toBeVisible();
  await expect(flowHistory.getByRole("row").nth(1)).toContainText("synthetic contribution evidence");
  await expect(flowHistory.getByRole("row").nth(1)).toContainText("External source");
  await expect(flowHistory.getByRole("row").nth(2)).toContainText("synthetic transfer evidence");
  await expect(flowHistory.getByRole("row").nth(2)).toContainText("broker");
  await expect(flowHistory.getByRole("row").nth(3)).toContainText("synthetic withdrawal evidence");
  await expect(flowHistory.getByRole("row").nth(3)).toContainText("External destination");
  await expect(page.getByText(/Transfers between included accounts do not change net external contributions/)).toBeVisible();
  await page.getByText("Cash observations and savings details", { exact: true }).click();
  await expect(page.getByRole("heading", { name: "Reported savings-account terms" })).toBeVisible();
  await expect(page.getByText(/4.25% AER/)).toBeVisible();
  await expect(page.getByText(/not current provider verification or an interest-accrual input/)).toBeVisible();
  await expect(page.getByRole("heading", { name: "Observed cash balances" })).toBeVisible();
  const cashHistory = page.getByLabel("Cash reconciliation history").getByRole("table");
  await expect(cashHistory.getByRole("row").nth(1)).toContainText("-£1.00");
  await expect(cashHistory.getByRole("row").nth(1)).toContainText("synthetic evidence");
  await expect(cashHistory.getByRole("row").nth(2)).toContainText("-£10.00");
  await expect(cashHistory.getByRole("row").nth(2)).toContainText("prior synthetic evidence");
  const reconciliation = page.getByRole("region", { name: "Cash evidence overview" });
  await expect(reconciliation).toContainText("2 differing observations");
  await expect(reconciliation).toContainText("No cash observation");
  await expect(page.getByRole("region", { name: "Valuation evidence" })).toContainText("4 days");
  const latest = { account_id: "bank", balance_gbp: "1000", difference_gbp: "0",
    timestamp: "2026-01-02T12:00:00Z", available_at: "2026-01-02T12:00:00Z",
    source: "later synthetic statement", evidence: "later matching evidence" };
  report.cash_observations.push(latest);
  report.accounts[0].cash_observation = { ...latest };
  delete report.accounts[0].cash_observation.account_id;
  await page.getByRole("button", { name: "Refresh investment" }).click();
  await expect(reconciliation).toContainText("2 differing observations");
  await expect(reconciliation).toContainText("synthetic evidence");
  await expect(reconciliation).toContainText("prior synthetic evidence");
  await expect(reconciliation).toContainText("Later matching observations do not resolve");
  report.cash_observations.pop();
  report.accounts[0].cash_observation = { ...report.cash_observations[1] };
  delete report.accounts[0].cash_observation.account_id;
  for (const [day, difference] of [[2, "0.001"], [3, "-0.001"]]) {
    report.cash_observations.push({ account_id: "bank", balance_gbp: "1000",
      difference_gbp: difference, timestamp: `2026-01-0${day}T12:00:00Z`,
      available_at: `2026-01-0${day}T12:00:00Z`, source: "precise statement", evidence: "precise evidence" });
  }
  report.accounts[0].cash_observation = { ...report.cash_observations.at(-1) };
  delete report.accounts[0].cash_observation.account_id;
  await page.getByRole("button", { name: "Refresh investment" }).click();
  await expect(reconciliation).toContainText("-£0.001");
  await expect(cashHistory.getByRole("row").nth(1)).toContainText("-£0.001");
  await expect(cashHistory.getByRole("row").nth(2)).toContainText("£0.001");
  report.cash_observations.splice(-2);
  report.accounts[0].cash_observation = { ...report.cash_observations[1] };
  delete report.accounts[0].cash_observation.account_id;
  const holding = { ...report.holdings[0] };
  report.as_of = null;
  report.complete = false;
  report.equity_gbp = null;
  report.since_baseline_gain_gbp = null;
  report.twr_pct = null;
  report.max_drawdown_pct = null;
  report.holdings[0] = { ...holding, value_gbp: null, mark_at: null, mark_available_at: null };
  await page.getByRole("button", { name: "Refresh investment" }).click();
  await expect(page.getByRole("region", { name: "Valuation evidence" })).toContainText("Age unavailable");
  await expect(page.getByLabel("Investment holdings")).toContainText("Age unavailable");
  report.as_of = at;
  report.complete = true;
  report.equity_gbp = "2000";
  report.since_baseline_gain_gbp = "0";
  report.twr_pct = "0";
  report.max_drawdown_pct = "0";
  report.holdings[0] = holding;
  await page.getByRole("button", { name: "Refresh investment" }).click();
  await page.clock.setFixedTime(new Date("2025-01-01T12:00:00Z"));
  await expect(page.getByRole("region", { name: "Valuation evidence" })).toContainText("Age unavailable (future or invalid clock)");
  await page.clock.setFixedTime(new Date("2026-01-05T12:00:00Z"));
  await expect(page.locator("#investment-accounts")).toHaveAttribute("open", "");
  await expect(page.getByRole("columnheader", { name: "Price date" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Allocation and drift" })).toHaveCount(0);
  await page.getByText("Proposal status and capability limits", { exact: true }).click();
  await expect(page.getByText(/No order approval or execution is available here/)).toBeVisible();
  await page.getByRole("link", { name: "Add a record", exact: true }).click();
  await expect(page).toHaveURL(/\/investment\/record$/);
  await expect(page.getByLabel("What do you want to record?")).toBeVisible();
  await page.getByLabel("What do you want to record?").selectOption("investment");
  await page.getByLabel("Choose a record").selectOption("buy");
  await expect(page.getByLabel("Recorded fee in GBP")).toHaveValue("");
  await expect(page.getByLabel("Applies at (local time)")).toHaveValue("");
  await expect(page.getByLabel("Source available from (local time)")).toHaveValue("");
  await page.getByLabel("Choose a record").selectOption("split");
  await page.getByLabel("Account", { exact: true }).selectOption("broker");
  await page.getByLabel("Investment", { exact: true }).selectOption("fund");
  await page.getByLabel("Split ratio").fill("2");
  await page.getByLabel("Applies at (local time)").fill("2026-01-05T12:00");
  await page.getByLabel("Source available from (local time)").fill("2026-01-05T12:00");
  await page.getByLabel("Source description / correction reason").fill("Synthetic split notice");
  await page.getByRole("button", { name: "Preview record" }).click();
  expect(previewEvent).toMatchObject({ type: "split", account_id: "broker", product_id: "fund", ratio: "2" });
  await page.getByRole("button", { name: "Cancel preview" }).click();
  await page.getByLabel("What do you want to record?").selectOption("money");
  await page.getByLabel("Choose a record").selectOption("contribution");
  await page.getByLabel("GBP amount").fill("25");
  await page.getByLabel("Applies at (local time)").fill("2026-01-05T12:00");
  await page.getByLabel("Source available from (local time)").fill("2026-01-05T12:00");
  await page.getByLabel("Source description / correction reason").fill("Synthetic contribution receipt");
  await page.getByRole("button", { name: "Preview record" }).click();
  expect(previewEvent).not.toHaveProperty("role");
  await expect(page.getByRole("region", { name: "Record summary" })).toContainText("Unassigned — no role chosen");
  await page.getByRole("button", { name: "Cancel preview" }).click();
  await page.getByRole("region", { name: "Manual recording" }).getByLabel("Optional role", { exact: true }).selectOption("dynamic");
  await page.getByRole("button", { name: "Preview record" }).click();
  await expect(page.getByRole("heading", { name: "Review before recording" })).toBeVisible();
  const confirmationEvent = page.locator(".recording-preview details.recording-event");
  await expect(page.getByText("£25.00", { exact: true })).toBeVisible();
  await expect(confirmationEvent.locator("summary")).toHaveText("Exact event and evidence being appended");
  await expect(confirmationEvent).not.toHaveAttribute("open", "");
  await expect(confirmationEvent).toContainText('"account_id": "bank"');
  await expect(confirmationEvent).toContainText('"amount_gbp": "25"');
  await expect(confirmationEvent).toContainText('"role": "dynamic"');
  await expect(confirmationEvent).toContainText("Synthetic contribution receipt");
  await page.getByRole("button", { name: "Confirm and append record" }).click();
  await expect.poll(() => previewEvent?.type).toBe("contribution");
  await page.getByLabel("What do you want to record?").selectOption("other");
  await page.getByLabel("Choose a record").selectOption("correction");
  await page.getByLabel("Event to correct").selectOption({ index: 1 });
  await page.getByLabel("Corrected GBP amount").fill("20");
  await page.getByLabel("Correction recorded at (local time)").fill("2026-01-05T12:00");
  await page.getByLabel("Source available from (local time)").fill("2026-01-05T12:00");
  await page.getByLabel("Source description / correction reason").fill("Synthetic corrected receipt");
  await expect(page.getByText(/Correcting Contribution/)).toBeVisible();
  await page.getByRole("button", { name: "Preview record" }).click();
  await expect(confirmationEvent).toContainText('"replacement_event"');
  await expect(confirmationEvent).toContainText('"amount_gbp": "20"');
  await expect(confirmationEvent).toContainText("Synthetic corrected receipt");
  await page.getByRole("button", { name: "Confirm and append record" }).click();
  await expect.poll(() => previewEvent?.type).toBe("correction");
  expect(previewEvent.replacement_event.amount_gbp).toBe("20");
  expect(previewEvent.replacement_event.currency).toBe("GBP");
  expect(previewEvent.replacement_event.role).toBe("dynamic");
  await page.getByLabel("What do you want to record?").selectOption("other");
  await page.getByLabel("Choose a record").selectOption("profile_change");
  await page.getByLabel("What do you want to change?").selectOption("rename");
  await page.getByLabel("Account or investment?", { exact: true }).selectOption("product");
  await page.getByLabel("Which account or investment?").selectOption("fund");
  await page.getByLabel("New name").fill("Renamed fund");
  await page.getByLabel("Applies and available at (local time)")
    .fill("2026-01-02T12:00");
  await page.getByLabel("Source description / correction reason").fill("Synthetic product documentation");
  await page.getByRole("button", { name: "Preview record" }).click();
  await expect(confirmationEvent).toContainText('"action": "rename"');
  await expect(confirmationEvent).toContainText('"entity_id": "fund"');
  await expect(confirmationEvent).toContainText('"name": "Renamed fund"');
  await page.getByRole("button", { name: "Cancel preview" }).click();
  await page.getByLabel("Choose a record").selectOption("scope_addition");
  await page.getByLabel("Account reference (unique ID)", { exact: true }).fill("scope");
  await page.getByLabel("Account name").fill("Additional broker");
  await page.getByLabel("Account link nickname", { exact: true }).fill("synthetic-binding");
  const scopeBalances = page.getByRole("group", { name: "Cash in every account at the same date and time (GBP)" });
  await scopeBalances.getByRole("textbox").nth(0).fill("1000");
  await scopeBalances.getByRole("textbox").nth(1).fill("500");
  await scopeBalances.getByRole("textbox").nth(2).fill("10");
  await page.getByRole("button", { name: "Add new-account holding" }).click();
  const scopeHoldings = page.getByRole("group", {
    name: "All existing and new holdings with prices at the same time",
  });
  await scopeHoldings.getByLabel("Account").selectOption("scope");
  await scopeHoldings.getByLabel("Investment").selectOption("fund");
  await scopeHoldings.getByLabel("Quantity").fill("2");
  await scopeHoldings.getByLabel("GBP price per unit").fill("5");
  await page.getByLabel("Applies and available at (local time)")
    .fill("2026-01-03T12:00");
  await page.getByLabel("Source description / correction reason").fill("Synthetic common-clock statement");
  await page.getByRole("button", { name: "Preview record" }).click();
  expect(previewEvent.type).toBe("scope_addition");
  expect(previewEvent.account).toMatchObject({
    id: "scope", name: "Additional broker", binding: "synthetic-binding", kind: "broker",
  });
  expect(previewEvent.balances_gbp).toEqual({ bank: "1000", broker: "500", scope: "10" });
  expect(previewEvent.holdings).toEqual([{
    account_id: "scope", product_id: "fund", quantity: "2", price_gbp: "5",
  }]);
  const recordSummary = page.getByRole("region", { name: "Record summary" });
  await expect(recordSummary).toContainText("Account added to scope");
  await expect(recordSummary).toContainText("Additional broker");
  await expect(recordSummary).toContainText("Cash included in this record");
  await expect(recordSummary).toContainText("£10.00");
  await expect(recordSummary).toContainText("Holdings included");
  await page.getByRole("button", { name: "Cancel preview" }).click();
  await expect(page.getByLabel("What do you want to record?")).toBeVisible();
  await page.getByLabel("Choose a record").selectOption("role_assignment");
  await page.getByRole("region", { name: "Manual recording" }).getByLabel("Optional role", { exact: true }).selectOption("core");
  await page.getByLabel("GBP amount").fill("100");
  await page.getByLabel("Applies at (local time)").fill("2026-01-05T12:00");
  await page.getByLabel("Source available from (local time)").fill("2026-01-05T12:00");
  await page.getByLabel("Source description / correction reason").fill("Synthetic attribution evidence");
  await page.getByRole("button", { name: "Preview record" }).click();
  await expect(page.getByRole("region", { name: "Record summary" })).toContainText("£100.00");
  await expect(page.getByRole("region", { name: "Record summary" })).toContainText("Cash");
  await expect(confirmationEvent).toContainText('"role": "core"');
  await expect(confirmationEvent).toContainText('"product_id": null');
  await page.getByRole("button", { name: "Cancel preview" }).click();
  opened = false;
  await page.reload();
  await expect(page.getByText("Starting cash balances at the same date and time")).toBeVisible();
  await expect(page.getByRole("button", { name: "Add existing holding" })).toBeVisible();
  const openingCash = page.getByRole("group", { name: "Starting cash balances at the same date and time" });
  await openingCash.getByRole("textbox").nth(0).fill("1000");
  await openingCash.getByRole("textbox").nth(1).fill("0");
  await page.getByRole("button", { name: "Add existing holding" }).click();
  const holdingInput = page.getByRole("group", { name: "Investments you already own (add every holding)" });
  await holdingInput.getByLabel("Account").selectOption("broker");
  await holdingInput.getByLabel("Investment").selectOption("fund");
  await page.getByLabel("Quantity").fill("1");
  await page.getByLabel("Applies at (local time)").fill("2026-01-05T12:00");
  await page.getByLabel("Source available from (local time)").fill("2026-01-05T12:00");
  await page.getByLabel("Source description / correction reason").fill("Synthetic existing holding evidence");
  await page.getByRole("button", { name: "Preview record" }).click();
  await expect(page.getByRole("heading", { name: "Review before recording" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Record summary" })).toContainText("unknown — not zero");
  expect(previewEvent.holdings[0].price_gbp).toBeNull();
  await page.getByRole("button", { name: "Cancel preview" }).click();
  await page.goto("/investment");
  report.lifetime_gain_gbp = "0";
  await page.getByRole("button", { name: "Refresh investment" }).click();
  await expect(page.getByText("Investment response failed validation.")).toBeVisible();
});
