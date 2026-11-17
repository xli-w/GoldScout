import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";

const fixtures = JSON.parse(readFileSync(new URL("../../dashboard/fixtures.json", import.meta.url), "utf8"));

test("monitoring shows synthetic findings and invalidates them on failed refresh", async ({ page }) => {
  let unavailable = false;
  await page.route("**/api/investment/v1/monitoring", async route => {
    if (unavailable) return route.fulfill({ status: 503, json: { error: "unavailable" } });
    return route.continue();
  });

  await page.goto("/investment");
  const section = page.getByRole("region", { name: "Portfolio monitoring", exact: true });
  await expect(section.getByText(/Synthetic monitoring demonstration/)).toBeVisible();
  await expect(section.getByText(/blocked_unvalidated/)).toBeVisible();
  await expect(section.getByText(/Unresolved recorded cash differences/)).toBeVisible();
  await expect(section.getByRole("checkbox")).not.toBeChecked();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBeTruthy();
  unavailable = true;
  await section.getByRole("button", { name: "Refresh monitoring", exact: true }).click();
  await expect(section.getByRole("alert")).toContainText("unavailable");
  await expect(section.getByText(/Unresolved recorded cash differences/)).toHaveCount(0);
});

test("monitoring refuses mismatched revisions and malformed responses", async ({ page }) => {
  const report = await (await page.request.get("/api/investment/v1/monitoring")).json();
  let malformed = false;
  await page.route("**/api/investment/v1/monitoring", route => route.fulfill({
    json: malformed ? { ...report, dynamic_pct: "NaN" } : { ...report, portfolio_revision: report.portfolio_revision + 1 },
  }));
  await page.goto("/investment");
  const section = page.getByRole("region", { name: "Portfolio monitoring", exact: true });
  await expect(section.getByRole("alert")).toContainText("revision changed");
  await expect(section.getByText(/Synthetic monitoring demonstration/)).toHaveCount(0);
  malformed = true;
  await section.getByRole("button", { name: "Refresh monitoring", exact: true }).click();
  await expect(section.getByRole("alert")).toContainText("unavailable");
  await expect(section.getByText(/Synthetic monitoring demonstration/)).toHaveCount(0);
});

test("investment workspace opens investment and does not present legacy-only pages", async ({ page }) => {
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
  await expect(page.getByRole("heading", { name: "Long-term investment", level: 1 })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Account navigation" })).toHaveCount(0);
  await expect(page.getByRole("navigation", { name: "Records navigation" })).toHaveCount(0);
  await page.goto("/positions");
  await expect(page.getByText("Legacy trading records are not selected in this workspace.")).toBeVisible();
  expect(legacyRequests).toBe(0);
});

test("demo defaults to investment and retains a separate legacy trading route", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/investment$/);
  await expect(page.getByRole("heading", { name: "Management roles" })).toBeVisible();
  await expect(page.getByText(/Synthetic role attribution is shown only to demonstrate accounting/)).toBeVisible();
  await expect(page.getByRole("heading", { name: "Allocation and drift" })).toHaveCount(0);
  await page.getByRole("link", { name: "Trading demo", exact: true }).click();
  await expect(page).toHaveURL(/\/legacy$/);
  await expect(page.getByRole("heading", { name: "Portfolio overview" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Account navigation" })).toBeVisible();
  await page.getByRole("link", { name: "Positions", exact: true }).click();
  await expect(page.getByRole("cell", { name: /SPY/ })).toBeVisible();
});

test("recording route clearly reports when local opt-in is disabled", async ({ page }) => {
  await page.goto("/investment/record");
  await expect(page.getByRole("heading", { name: "Record evidence" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Recording is disabled" })).toBeVisible();
  await expect(page.getByText(/explicitly selected version-2 store and local opt-in/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Preview record" })).toHaveCount(0);
});

test("unknown workspace does not guess an account source", async ({ page }) => {
  let legacyRequests = 0;
  await page.route("**/api/v1/settings", route => route.fulfill({ json: { workspace: ["demo"] } }));
  await page.route("**/api/v1/overview", route => { legacyRequests++; return route.abort(); });
  await page.goto("/");
  await expect(page.getByRole("alert")).toContainText("account source is unknown");
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
  await page.getByText("Optional local portfolio awareness", { exact: true }).click();
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

test("investment demo separates contributions, GBP valuation and export-only proposals", async ({ page }) => {
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto("/");
  await expect(page).toHaveURL(/\/investment$/);
  await expect(page.getByRole("heading", { name: "Long-term investment", level: 1 })).toBeVisible();
  await expect(page.getByText("SYNTHETIC INVESTMENT DEMO")).toBeVisible();
  await expect(page.getByText("Deposits are not profit")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Management roles" })).toBeVisible();
  await expect(page.getByText(/no targets, policy, or investment recommendation/)).toBeVisible();
  await expect(page.getByRole("heading", { name: "Allocation and drift" })).toHaveCount(0);
  await page.getByText("Cash accounts, holdings and reconciliation evidence", { exact: true }).click();
  await expect(page.getByText(/External savings are not broker buying power/)).toBeVisible();
  await page.getByText("Proposal status and capability limits", { exact: true }).click();
  await expect(page.getByText(/No order approval or execution is available here/)).toBeVisible();
  await page.getByRole("button", { name: "Refresh investment" }).click();
  await expect(page.locator(".metrics")).toContainText("GBP");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  expect(errors).toEqual([]);
});

test("investment guide remains usable when recorded investment data is unavailable", async ({ page }) => {
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ status: 503, json: { error: "investment_unavailable" } }));
  await page.goto("/investment");
  await expect(page.getByRole("heading", { name: "Investment data unavailable" })).toBeVisible();
  await page.locator(".record-toolbar").getByRole("link", { name: "Investment guide" }).click();
  await expect(page.getByRole("heading", { name: "Ownership, not short-term prediction" })).toBeVisible();
  await expect(page.getByText(/Losses can be large, prolonged or permanent/)).toBeVisible();
  await expect(page.getByText(/Proposals are blocked until role-aware policy/)).toBeVisible();
  await expect(page.getByText(/Review acknowledgements are recorded through the offline CLI/)).toBeVisible();
  await expect(page.getByText(/five-point drift remains a trigger/)).toBeVisible();
});

test("investment view displays a non-executable sell proposal", async ({ page }) => {
  const report = {
    schema_version: 1, source_mode: "demo", portfolio_id: "synthetic-v1", revision: 1,
    generated_at: "2026-01-01T12:00:00Z", as_of: "2026-01-01T12:00:00Z",
    currency: "GBP", complete: true, equity_gbp: "1000", net_contributions_gbp: "900",
    gain_gbp: "100", twr_pct: "11.11", max_drawdown_pct: "0",
    accounts: [{ id: "broker", name: "Synthetic broker", kind: "broker", cash_gbp: "0" }],
    holdings: [], allocation: [
      { sleeve: "equity", target_pct: "80", actual_pct: "100", drift_pp: "20" },
      { sleeve: "bond", target_pct: "0", actual_pct: "0", drift_pp: "0" },
      { sleeve: "cash", target_pct: "20", actual_pct: "0", drift_pp: "-20" },
    ],
    proposal: { status: "review", reason: "Proposed sale proceeds do not fund purchases.",
      expires_at: "2026-01-01T13:00:00Z", orders: [{ account_id: "broker",
        product_id: "equity", side: "sell", quantity: "20", estimated_notional_gbp: "200" }],
      manual_transfers: [], execution_enabled: false },
    warnings: [], limitations: ["Synthetic v1 test evidence."],
  };
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
  await page.goto("/investment");
  await expect(page.getByText("Legacy v1 asset-class target view", { exact: true })).toBeVisible();
  await page.getByText("Legacy v1 asset-class target view", { exact: true }).click();
  await expect(page.getByRole("heading", { name: "Legacy v1 allocation and drift" })).toBeVisible();
  await expect(page.getByText(/not the current role-based mandate, approved targets/)).toBeVisible();
  await page.getByText("Proposal status and capability limits", { exact: true }).click();
  const row = page.getByLabel("Proposed investment changes").getByRole("row").nth(1);
  await expect(row).toContainText("Sell");
  await expect(row).toContainText("20");
  await expect(page.getByText(/No order approval or execution is available here/)).toBeVisible();
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
    recorded_corrections: [],
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
    accounts: [{ id: "bank", name: "Savings", kind: "deposit", cash_gbp: "1000",
      cash_observation: { balance_gbp: "999", difference_gbp: "-1", timestamp: at,
        available_at: at, source: "synthetic statement", evidence: "synthetic evidence" },
      terms: { annual_rate_pct: "4.25", rate_basis: "AER", effective_from: "2026-01-01",
        observed_at: at, available_at: at, source: "synthetic statement",
        maturity_date: "2027-01-01", notice_days: 90 } },
      { id: "broker", name: "Broker", kind: "broker", cash_gbp: "500",
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
  let authenticated = false;
  let opened = true;
  let latestCorrectable = {
    id: "synthetic-interest", type: "interest", account_id: "bank",
    currency: "GBP", amount_gbp: "2", timestamp: at, available_at: at,
    evidence: "synthetic payment evidence",
  };
  let previewEvent;
  await page.route("**/api/investment/v1/recording/session", route => route.fulfill({ json: {
    enabled: true, authenticated, csrf: "synthetic-csrf", opened,
    accounts: [{ id: "bank", name: "Savings", kind: "deposit" },
      { id: "broker", name: "Broker", kind: "broker" }],
    products: [{ id: "fund", name: "Fund", sleeve: "equity", quantity_increment: "1" }],
    latest_correctable_event: latestCorrectable,
  } }));
  await page.route("**/api/investment/v1/recording/login", async route => {
    authenticated = true;
    await route.fulfill({ json: { authenticated: true } });
  });
  await page.route("**/api/investment/v1/recording/preview", async route => {
    const request = route.request().postDataJSON();
    previewEvent = request.event;
    await route.fulfill({ json: {
      preview_id: `preview-${request.event.id}`, revision: report.revision,
      event: request.event, before: { equity_gbp: "2000" },
      after: { equity_gbp: "2000" }, expires_at: "2026-01-01T12:05:00Z",
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
  await expect(totals.getByText("Gain since baseline")).toBeVisible();
  await expect(totals.getByText("£2,000.00")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Recorded growth history" })).toBeVisible();
  await expect(page.getByText("Gap: Incomplete synthetic valuation.")).toBeVisible();
  await expect(page.getByText("Only recorded valuation event clocks are shown. Lines connect observations; no intermediate values are inferred. Incomplete clocks remain gaps.")).toBeVisible();
  const returnChart = page.getByRole("img", { name: "Time-weighted return since baseline; unavailable returns are gaps" });
  await expect(returnChart).toBeVisible();
  await expect(returnChart.locator("circle")).toHaveCount(3);
  await expect(returnChart.locator("polyline")).toHaveCount(1);
  await expect(returnChart.locator(".return-chart-gap")).toHaveCount(2);
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
  await page.getByText("Cash accounts, holdings and reconciliation evidence", { exact: true }).click();
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
  await expect(page.getByRole("columnheader", { name: "Mark as of" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Allocation and drift" })).toHaveCount(0);
  await page.getByText("Proposal status and capability limits", { exact: true }).click();
  await expect(page.getByText(/No order approval or execution is available here/)).toBeVisible();
  await page.getByRole("link", { name: "Record evidence", exact: true }).click();
  await expect(page).toHaveURL(/\/investment\/record$/);
  await page.getByLabel("Local recording password").fill("synthetic-local-passphrase");
  await page.getByRole("button", { name: "Unlock recording" }).click();
  await expect(page.getByLabel("Record type")).toBeVisible();
  await page.getByLabel("Record type").selectOption("contribution");
  await page.getByLabel("GBP amount").fill("25");
  await page.getByLabel("Source evidence / correction reason").fill("Synthetic contribution receipt");
  await page.getByRole("button", { name: "Preview record" }).click();
  expect(previewEvent).not.toHaveProperty("role");
  await page.getByRole("button", { name: "Cancel preview" }).click();
  await page.getByRole("region", { name: "Manual recording" }).getByLabel("Management role").selectOption("dynamic");
  await page.getByRole("button", { name: "Preview record" }).click();
  await expect(page.getByRole("heading", { name: "Review before recording" })).toBeVisible();
  const confirmationEvent = page.getByLabel("Full event being appended");
  await expect(confirmationEvent).toContainText('"account_id": "bank"');
  await expect(confirmationEvent).toContainText('"amount_gbp": "25"');
  await expect(confirmationEvent).toContainText('"role": "dynamic"');
  await expect(confirmationEvent).toContainText("Synthetic contribution receipt");
  await page.getByRole("button", { name: "Confirm and append record" }).click();
  await expect.poll(() => previewEvent?.type).toBe("contribution");
  await page.getByLabel("Record type").selectOption("correction");
  await page.getByLabel("Corrected GBP amount").fill("20");
  await page.getByLabel("Source evidence / correction reason").fill("Synthetic corrected receipt");
  await expect(page.getByText(/Correcting latest Contribution/)).toBeVisible();
  await page.getByRole("button", { name: "Preview record" }).click();
  await expect(confirmationEvent).toContainText('"replacement_event"');
  await expect(confirmationEvent).toContainText('"amount_gbp": "20"');
  await expect(confirmationEvent).toContainText("Synthetic corrected receipt");
  await page.getByRole("button", { name: "Confirm and append record" }).click();
  await expect.poll(() => previewEvent?.type).toBe("correction");
  expect(previewEvent.replacement_event.amount_gbp).toBe("20");
  expect(previewEvent.replacement_event.currency).toBe("GBP");
  expect(previewEvent.replacement_event.role).toBe("dynamic");
  await expect(page.getByLabel("Record type")).toBeVisible();
  await page.getByLabel("Record type").selectOption("role_assignment");
  await page.getByRole("region", { name: "Manual recording" }).getByLabel("Management role").selectOption("core");
  await page.getByLabel("GBP amount").fill("100");
  await page.getByLabel("Source evidence / correction reason").fill("Synthetic attribution evidence");
  await page.getByRole("button", { name: "Preview record" }).click();
  await expect(confirmationEvent).toContainText('"role": "core"');
  await expect(confirmationEvent).toContainText('"product_id": null');
  await page.getByRole("button", { name: "Cancel preview" }).click();
  opened = false;
  await page.reload();
  await expect(page.getByText("Opening cash balance at the same evidence clock")).toBeVisible();
  await expect(page.getByRole("button", { name: "Add existing holding" })).toBeVisible();
  const openingCash = page.getByRole("group", { name: "Opening cash balance at the same evidence clock" });
  await openingCash.getByRole("textbox").nth(0).fill("1000");
  await openingCash.getByRole("textbox").nth(1).fill("0");
  await page.getByRole("button", { name: "Add existing holding" }).click();
  const holdingInput = page.getByRole("group", { name: "Existing long fund holdings (optional)" });
  await holdingInput.getByLabel("Account").selectOption("broker");
  await holdingInput.getByLabel("Fund").selectOption("fund");
  await page.getByLabel("Quantity").fill("1");
  await page.getByLabel("Source evidence / correction reason").fill("Synthetic existing holding evidence");
  await page.getByRole("button", { name: "Preview record" }).click();
  await expect(page.getByRole("heading", { name: "Review before recording" })).toBeVisible();
  expect(previewEvent.holdings[0].price_gbp).toBeNull();
  await page.getByRole("button", { name: "Cancel preview" }).click();
  await page.goto("/investment");
  report.lifetime_gain_gbp = "0";
  await page.getByRole("button", { name: "Refresh investment" }).click();
  await expect(page.getByText("Investment response failed validation.")).toBeVisible();
});

test("trading guide works from the footer and without account data", async ({ page }) => {
  await page.route("**/api/v1/**", route => route.abort());
  await page.goto("/legacy");
  await page.locator("footer").getByRole("link", { name: "Trading guide" }).click();
  await expect(page).toHaveURL(/\/guide$/);
  await expect(page.getByRole("heading", { name: "Trading guide", level: 1 })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Default trading strategies" })).toBeVisible();
  await expect(page.getByText(/daily-loss safeguard blocks new entries/)).toBeVisible();
  await expect(page.getByText(/Bitcoin stop-limit orders may not fill/)).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: "Trading guide", level: 1 })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
});

test("overview loads and position search preserves URL", async ({ page }) => {
  await page.goto("/legacy");
  await expect(page).toHaveTitle("GoldScout | Portfolio Monitor");
  await expect(page.getByRole("link", { name: "GoldScout Legacy trading" })).toBeVisible();
  await expect(page.locator("footer")).toContainText("GoldScout");
  await expect(page.getByRole("heading", { name: "Portfolio overview" })).toBeVisible();
  await expect(page.getByText("SYNTHETIC DEMO")).toBeVisible();
  await expect(page.getByRole("cell", { name: /SPY/ })).toBeVisible();
  await page.getByRole("link", { name: "Positions", exact: true }).click();
  await page.getByRole("textbox", { name: "Search positions" }).fill("BTC");
  await expect(page.getByRole("heading", { name: "No matching positions" })).toBeVisible();
  await expect(page).toHaveURL(/q=BTC/);
  await page.getByRole("button", { name: "Clear search" }).click();
  await expect(page.getByRole("cell", { name: /SPY/ })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
});

test("position protection shows its observation time and stop definitions", async ({ page }) => {
  await page.clock.install({ time: new Date("2026-11-13T15:00:00Z") });
  const healthy = fixtures.scenarios.find(scenario => scenario.name === "healthy_recorded");
  await page.route("**/api/v1/overview", route => route.fulfill({ json: {
    api_version: "1", resource: "overview", meta: fixtures.meta, data: healthy.responses.overview,
  } }));
  await page.route("**/api/v1/positions?*", route => route.fulfill({ json: {
    api_version: "1", resource: "positions", meta: fixtures.meta, data: healthy.responses.positions,
  } }));
  await page.route("**/api/v1/health?*", route => route.fulfill({ json: {
    api_version: "1", resource: "health", meta: fixtures.meta, data: healthy.responses.health,
  } }));

  await page.goto("/legacy");
  const positionRow = page.getByRole("row").filter({ hasText: "SPY" });
  await expect(positionRow).toContainText("Last verified");
  await expect(positionRow).toContainText("Observed Nov 13, 2026");
  await expect(positionRow).toContainText("UTC");
  await expect(page.getByText(/Hard stop is the locally recorded fixed stop/)).toContainText("do not confirm current broker protection");

  const staleMeta = structuredClone(fixtures.meta);
  staleMeta.telemetry.observed_at = "2026-11-13T14:00:00Z";
  await page.route("**/api/v1/overview", route => route.fulfill({ json: {
    api_version: "1", resource: "overview", meta: staleMeta, data: healthy.responses.overview,
  } }));
  await page.route("**/api/v1/positions?*", route => route.fulfill({ json: {
    api_version: "1", resource: "positions", meta: staleMeta, data: healthy.responses.positions,
  } }));
  await page.route("**/api/v1/health?*", route => route.fulfill({ json: {
    api_version: "1", resource: "health", meta: staleMeta, data: healthy.responses.health,
  } }));
  await page.getByRole("button", { name: "Refresh data" }).click();
  await expect(positionRow).toContainText("Not verified");
  await expect(positionRow).toContainText("Observed Nov 13, 2026");
});

test("dark theme and sidebar persist across navigation and reload", async ({ page }) => {
  await page.goto("/legacy");
  const sidebar = page.locator(".sidebar");
  await expect(sidebar).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Account navigation" })).toBeVisible();
  const theme = () => page.evaluate(() => ({
    background: getComputedStyle(document.documentElement).backgroundColor,
    scheme: getComputedStyle(document.documentElement).colorScheme,
  }));
  expect(await theme()).toEqual({ background: "rgb(8, 13, 12)", scheme: "dark" });
  await page.getByRole("link", { name: "Positions", exact: true }).click();
  await expect(page).toHaveURL(/\/positions$/);
  await page.reload();
  await expect(page.getByRole("heading", { name: "Positions", level: 1 })).toBeVisible();
  await expect(sidebar).toBeVisible();
  expect(await theme()).toEqual({ background: "rgb(8, 13, 12)", scheme: "dark" });
});

test("small screens retain sidebar navigation, runtime, and scrollable tables", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "mobile", "Small-screen layout");
  await page.setViewportSize({ width: 320, height: 800 });
  await page.goto("/legacy");
  await expect(page.locator(".connection")).toContainText("Runtime");
  await expect(page.locator(".connection")).toBeVisible();
  const sidebar = page.locator(".sidebar");
  await expect(sidebar).toBeVisible();
  await expect(page.getByRole("link", { name: "Intelligence", exact: true })).toBeVisible();
  const bounds = await sidebar.boundingBox();
  expect(bounds.x).toBe(0);
  expect(bounds.width).toBeLessThan(100);
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  expect((await sidebar.boundingBox()).y).toBe(0);
  const table = page.getByLabel("Position table");
  await expect(table).toHaveAttribute("aria-describedby", "table-scroll-instructions");
  expect(await table.evaluate(element => getComputedStyle(element, "::before").content)).toContain("Scroll");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
});

test("unknown entry status explains its meaning and leads with fresh observations", async ({ page }) => {
  const healthy = fixtures.scenarios.find(scenario => scenario.name === "healthy_recorded");
  await page.route("**/api/v1/overview", route => route.fulfill({ json: {
    api_version: "1", resource: "overview", meta: fixtures.meta,
    data: { ...healthy.responses.overview, entries: "unknown" },
  } }));
  await page.route("**/api/v1/positions?*", route => route.fulfill({ json: {
    api_version: "1", resource: "positions", meta: fixtures.meta, data: healthy.responses.positions,
  } }));
  await page.route("**/api/v1/health?*", route => route.fulfill({ json: {
    api_version: "1", resource: "health", meta: fixtures.meta, data: healthy.responses.health,
  } }));

  await page.goto("/legacy");
  await expect(page.locator(".gate-band")).toContainText("Unknown");
  await expect(page.locator(".gate-band")).toContainText("Unknown is not approval to trade.");
  await expect(page.locator(".gate-band").getByRole("link", { name: "Review system health" })).toHaveAttribute("href", "/health");
  expect(await page.evaluate(() => {
    const gate = document.querySelector(".gate-band");
    const freshness = document.querySelector(".observation-band");
    const metrics = document.querySelector(".metrics");
    return Boolean(gate && freshness && metrics
      && (gate.compareDocumentPosition(freshness) & Node.DOCUMENT_POSITION_FOLLOWING)
      && (freshness.compareDocumentPosition(metrics) & Node.DOCUMENT_POSITION_FOLLOWING));
  })).toBeTruthy();
});

test("blocked position issues link directly to positions", async ({ page }) => {
  const healthy = fixtures.scenarios.find(scenario => scenario.name === "healthy_recorded");
  const position = { ...healthy.responses.positions[0], protection: "unverified", exit_pending: true };
  await page.route("**/api/v1/overview", route => route.fulfill({ json: {
    api_version: "1", resource: "overview", meta: fixtures.meta,
    data: { ...healthy.responses.overview, entries: "blocked", block_reasons: ["exit_pending", "protection_unverified"] },
  } }));
  await page.route("**/api/v1/positions?*", route => route.fulfill({ json: {
    api_version: "1", resource: "positions", meta: fixtures.meta, data: [position],
  } }));
  await page.route("**/api/v1/health?*", route => route.fulfill({ json: {
    api_version: "1", resource: "health", meta: fixtures.meta, data: healthy.responses.health,
  } }));

  await page.goto("/legacy");
  await expect(page.locator(".gate-band")).toContainText("Blocked in snapshot");
  await expect(page.locator(".gate-band").getByRole("link", { name: "Review affected positions" })).toHaveAttribute("href", "/positions");
});

test("unavailable storage does not render an empty account", async ({ page }) => {
  await page.route("**/api/v1/overview", route => route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({
    api_version: "1", resource: "error", meta: fixtures.meta,
    data: { code: "ledger_missing", message: "Recorded account data is unavailable.", retryable: true },
  }) }));
  await page.goto("/legacy");
  await expect(page.getByRole("alert")).toContainText("No account snapshot");
  await expect(page.getByText("No recorded holdings")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();
});

test("refresh failure hides previously loaded totals", async ({ page }) => {
  await page.goto("/legacy");
  await expect(page.getByRole("cell", { name: /SPY/ })).toBeVisible();
  await page.route("**/api/v1/overview", route => route.abort());
  await page.getByRole("button", { name: "Refresh data" }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page.getByRole("cell", { name: /SPY/ })).toHaveCount(0);
});

test("monitoring observation ages without another response", async ({ page }) => {
  const now = new Date();
  await page.clock.install({ time: now });
  const meta = structuredClone(fixtures.meta);
  meta.generated_at = now.toISOString();
  meta.ledger.observed_at = now.toISOString();
  meta.telemetry.observed_at = new Date(now.getTime() - 89000).toISOString();
  const healthy = fixtures.scenarios.find(scenario => scenario.name === "healthy_recorded");
  await page.route("**/api/v1/overview", route => route.fulfill({ contentType: "application/json", body: JSON.stringify({
    api_version: "1", resource: "overview", meta, data: healthy.responses.overview,
  }) }));
  await page.route("**/api/v1/positions?*", route => route.fulfill({ contentType: "application/json", body: JSON.stringify({
    api_version: "1", resource: "positions", meta, data: healthy.responses.positions,
  }) }));
  await page.goto("/legacy");
  const monitoring = page.locator(".observation-band > div").nth(1);
  await expect(monitoring.getByText("Fresh", { exact: true })).toBeVisible();
  await page.clock.fastForward(2000);
  await expect(monitoring.getByText("Stale", { exact: true })).toBeVisible();
});

test("monitor-only entry block becomes unknown when telemetry ages out", async ({ page }) => {
  const now = new Date();
  await page.clock.install({ time: now });
  const meta = structuredClone(fixtures.meta);
  meta.generated_at = now.toISOString();
  meta.ledger.observed_at = now.toISOString();
  meta.telemetry.observed_at = now.toISOString();
  const healthy = fixtures.scenarios.find(scenario => scenario.name === "healthy_recorded");
  const overview = structuredClone(healthy.responses.overview);
  overview.entries = "blocked";
  overview.block_reasons = ["monitor_not_ready"];
  await page.route("**/api/v1/overview", route => route.fulfill({ contentType: "application/json", body: JSON.stringify({
    api_version: "1", resource: "overview", meta, data: overview,
  }) }));
  await page.route("**/api/v1/positions?*", route => route.fulfill({ contentType: "application/json", body: JSON.stringify({
    api_version: "1", resource: "positions", meta, data: healthy.responses.positions,
  }) }));
  await page.route("**/api/v1/health?*", route => route.fulfill({ contentType: "application/json", body: JSON.stringify({
    api_version: "1", resource: "health", meta, data: healthy.responses.health,
  }) }));

  await page.goto("/legacy");
  await expect(page.locator(".gate-band h2")).toContainText("Blocked in snapshot");
  await page.clock.runFor(91000);
  await expect(page.locator(".gate-band h2")).toContainText("Unknown");
  await expect(page.getByText("Monitor Not Ready", { exact: true })).toHaveCount(0);
});

test("health view renders monitor, blocks and activity coverage", async ({ page }) => {
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto("/health");
  await expect(page.getByRole("heading", { name: "System health" })).toBeVisible();
  await expect(page.getByText("Bounded overlap; not full history")).toBeVisible();
  await expect(page.locator(".health-facts > div").first()).toContainText("Running");
  expect(errors).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
});

test("fault observation is visible on health page", async ({ page }) => {
  await page.goto("/legacy");
  const health = await page.evaluate(async () => await (await fetch("/api/v1/health")).json());
  await page.route("**/api/v1/health?*", async route => {
    const body = structuredClone(health);
    body.data.runtime = "faulted";
    body.data.persistence_fault = true;
    body.data.task_failures = { monitor: 2 };
    body.data.warnings = ["software_monitoring_stopped"];
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.goto("/health");
  await expect(page.locator(".health-facts > div").first()).toContainText("Faulted");
  await expect(page.getByText("Software Monitoring Stopped")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Task failures" })).toBeVisible();
});

test("history symbol filter preserves portfolio totals and original unknown price", async ({ page }) => {
  await page.goto("/history");
  await expect(page.getByRole("heading", { name: "Original exit slices" })).toBeVisible();
  await expect(page.getByLabel("From accounting date (New York)")).toBeVisible();
  await expect(page.getByLabel("To accounting date (New York)")).toBeVisible();
  await expect(page.getByText(/Accounting dates use New York time; timestamps are shown in UTC/)).toBeVisible();
  await expect(page.getByRole("region", { name: "Portfolio totals" })).toContainText("Daily accounting");
  await expect(page.getByRole("region", { name: "Exit details" })).toContainText("Evidence-linked corrections");
  await expect(page.getByLabel("Filter exit symbol")).toHaveAccessibleDescription("Symbol filters exit slices and their corrections, not portfolio totals.");
  await page.getByLabel("Filter exit symbol").fill("QQQ");
  await expect(page.getByLabel("Original exit slices").locator("tbody tr")).toHaveCount(1);
  await expect(page.getByLabel("Daily accounting table").locator("tbody tr")).toHaveCount(5);
  await expect(page.getByText("Price unknown", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Accounting corrections").locator("tbody tr")).toHaveCount(1);
  await expect(page).toHaveURL(/symbol=QQQ/);
  await expect(page.getByText("1 active filter", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Clear filters" }).click();
  await expect(page).not.toHaveURL(/symbol=/);
  await expect(page.getByText("All history", { exact: true })).toBeVisible();
});

test("history sections and shared pagination are reachable before and after the records", async ({ page }) => {
  await page.route("**/api/v1/performance?*", async route => {
    const response = await route.fetch();
    const body = await response.json();
    body.data.next_cursor = "next-page";
    await route.fulfill({ json: body });
  });
  await page.goto("/history");

  const sections = page.getByRole("navigation", { name: "History sections" });
  await expect(sections.getByRole("link", { name: "Portfolio totals" })).toHaveAttribute("href", "#history-totals");
  await expect(sections.getByRole("link", { name: "Exit details" })).toHaveAttribute("href", "#history-exits");
  const pagination = page.getByRole("navigation", { name: "History pagination" });
  await expect(pagination).toHaveCount(2);
  for (const control of await pagination.all()) {
    await expect(control).toContainText("More portfolio totals, exits, and corrections are available");
    await expect(control.getByRole("button", { name: "Load more history" })).toBeVisible();
  }
  await sections.getByRole("link", { name: "Exit details" }).click();
  await expect(page).toHaveURL(/#history-exits$/);
});

test("primary navigation groups account, records, and advisory destinations", async ({ page }) => {
  await page.goto("/activity");
  await expect(page.getByRole("navigation", { name: "Account navigation" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Records navigation" })).toContainText("History");
  await expect(page.getByRole("navigation", { name: "Advisory navigation" })).toContainText("Intelligence");
  await expect(page.locator(".page-description")).toHaveText("Recorded journal events and observed orders; not live order control.");
  await page.getByRole("link", { name: "History", exact: true }).click();
  await expect(page.locator(".page-description")).toHaveText("Portfolio totals, original exit records, and separate evidence-linked corrections.");
});

test("history filters can be reset together without discarding other URL state", async ({ page }) => {
  await page.goto("/history?view=history");
  await expect(page.getByText("All history", { exact: true })).toBeVisible();
  await page.getByLabel("From accounting date (New York)").fill("2026-01-01");
  await page.getByLabel("To accounting date (New York)").fill("2026-01-31");
  await page.getByLabel("Filter exit symbol").fill("spy");
  await expect(page.getByLabel("Filter exit symbol")).toHaveValue("SPY");
  await expect(page.getByText("3 active filters", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Clear filters" }).click();
  await expect(page.getByLabel("From accounting date (New York)")).toHaveValue("");
  await expect(page.getByLabel("To accounting date (New York)")).toHaveValue("");
  await expect(page.getByLabel("Filter exit symbol")).toHaveValue("");
  await expect(page).toHaveURL(/view=history/);
  await expect(page).not.toHaveURL(/(?:from|to|symbol)=/);
});

test("activity switches between journal and observed orders", async ({ page }) => {
  await page.goto("/activity");
  await expect(page.getByRole("group", { name: "Activity view" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Journal" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("button", { name: "Orders" })).toHaveAttribute("aria-pressed", "false");
  await expect(page.getByLabel("Event journal")).toBeVisible();
  await page.getByRole("button", { name: "Orders", exact: true }).click();
  await expect(page.getByRole("button", { name: "Orders" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByLabel("Order observations").locator("tbody tr")).toHaveCount(1);
  await expect(page.getByLabel("Order observations")).toHaveAttribute("aria-describedby", "table-scroll-instructions");
  await expect(page).toHaveURL(/view=orders/);
});

test("history preserves precise price and the primary refresh reloads records", async ({ page }) => {
  let requests = 0;
  await page.route("**/api/v1/performance?*", async route => {
    requests += 1;
    await route.continue();
  });
  await page.goto("/history");
  await expect(page.getByLabel("Original exit slices").getByText("$103.9375", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Refresh data", exact: true }).click();
  await expect.poll(() => requests).toBeGreaterThan(1);
});