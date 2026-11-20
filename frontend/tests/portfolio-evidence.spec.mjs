import { expect, test } from "@playwright/test";

test("portfolio shows evidence age and dated activity without claiming live balances", async ({ page }) => {
  const report = await (await page.request.get("/api/investment/v1/snapshot")).json();
  const account = report.accounts[0];
  const holding = report.holdings[0];
  const at = "2026-01-01T12:00:00Z";
  report.as_of = at;
  report.accounts.forEach(item => { item.cash_observation = null; });
  account.cash_observation = {
    balance_gbp: "100", difference_gbp: "-1", timestamp: at,
    available_at: "2026-01-01T12:00:00.000001Z",
    source: "Synthetic statement", evidence: "Synthetic cash check",
  };
  report.cash_observations = [{ ...account.cash_observation, account_id: account.id }];
  account.terms = {
    annual_rate_pct: "4.25", rate_basis: "AER", effective_from: "2025-12-01",
    observed_at: "2025-12-02T09:00:00.000001Z",
    available_at: "2025-12-02T09:00:00.000002Z",
    source: "Synthetic account terms", maturity_date: "2027-01-01", notice_days: 90,
  };
  holding.mark_at = "2026-01-01T11:59:59.000001Z";
  holding.mark_available_at = "2026-01-01T11:59:59.000002Z";
  report.recorded_flow_events = [{
    id: "synthetic-contribution", type: "contribution", account_id: account.id,
    destination_account_id: null, role: "unassigned", amount_gbp: "10",
    timestamp: "2026-01-02T12:00:00.000001Z", available_at: "2026-01-02T12:00:00.000001Z",
    evidence: "Synthetic deposit",
  }];
  report.recorded_income_events = [{
    id: "synthetic-interest", type: "interest", account_id: account.id, product_id: null,
    role: "unassigned", role_evidence: null, amount_gbp: "2",
    timestamp: "2026-01-02T12:00:00.000002Z", available_at: "2026-01-02T12:00:00.000002Z",
    evidence: "Synthetic interest",
  }];
  report.recorded_fee_events = [];
  let writes = 0;
  page.on("request", request => { if (!["GET", "HEAD"].includes(request.method())) writes++; });
  await page.clock.setFixedTime(new Date("2026-01-03T12:00:00Z"));
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
  await page.goto("/investment");

  await expect(page.getByRole("region", { name: "Valuation evidence" })).toContainText("age: 2 days");
  await expect(page.getByRole("region", { name: "Valuation evidence" }))
    .toContainText("Set an expected review interval for each account below");
  const summary = page.getByRole("region", { name: "Accounts and investments" });
  await expect(summary).toContainText("Last cash check:");
  await expect(summary).toContainText("difference at that time: -£1.00");
  await expect(summary).toContainText("Cash checks are separate observations");
  await expect(summary).toContainText("Reported savings terms: 4.25% AER");
  await expect(summary).toContainText("Maturity 2027-01-01 · Notice 90 days");
  await summary.getByText("Term source and dates").click();
  await expect(summary).toContainText("Synthetic account terms");
  await expect(summary).toContainText("2025-12-02T09:00:00.000001Z");
  await expect(summary).toContainText("2025-12-02T09:00:00.000002Z");
  await expect(summary).toContainText("not current provider verification");
  await expect(summary.locator('[aria-label="Recorded investment holdings"]')).toContainText("age: 2 days");
  await summary.getByText("Price dates and source availability").click();
  await expect(summary).toContainText("2026-01-01T11:59:59.000001Z");
  await expect(summary).toContainText("2026-01-01T11:59:59.000002Z");
  await page.getByText("Cash observations and savings details", { exact: true }).click();
  await expect(page.getByLabel("Cash reconciliation history").getByRole("table"))
    .toContainText("2026-01-01T12:00:00.000001Z");

  const activity = page.getByRole("region", { name: "Recent recorded activity" });
  await expect(activity).toContainText("not a complete explanation");
  await expect(activity.getByRole("listitem").first()).toContainText("Interest received");
  await expect(activity.getByRole("listitem").nth(1)).toContainText("Money added");
  await activity.getByText("Source details").first().click();
  await expect(activity).toContainText("Synthetic interest");
  await activity.getByRole("link", { name: "View record history" }).click();
  await expect(page.locator("#investment-records")).toHaveAttribute("open", "");
  expect(writes).toBe(0);
});

test("account freshness intervals and local review dates persist without journal writes", async ({ page }) => {
  const report = await (await page.request.get("/api/investment/v1/snapshot")).json();
  report.source_mode = "recorded";
  report.as_of = "2030-01-01T00:00:00Z";
  report.accounts.forEach(item => {
    item.cash_observation = {
      balance_gbp: "100", difference_gbp: "0", timestamp: report.as_of,
      available_at: report.as_of, source: "Synthetic check", evidence: "Synthetic evidence",
    };
    if (item.kind === "deposit") item.terms = {
      annual_rate_pct: "4.00", rate_basis: "AER", effective_from: "2029-12-01",
      observed_at: report.as_of, available_at: report.as_of,
      source: "Synthetic terms", maturity_date: null, notice_days: null,
    };
  });
  report.holdings.forEach(item => {
    if (item.mark_at !== null) item.mark_at = report.as_of;
  });
  const account = report.accounts.find(item => item.status === "active");
  await page.clock.setFixedTime(new Date("2030-01-02T00:00:00Z"));
  await page.route("**/api/v1/settings", route => route.fulfill({
    json: { mode: "recorded", workspace: "investment" },
  }));
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
  let writes = 0;
  page.on("request", request => { if (!["GET", "HEAD"].includes(request.method())) writes++; });
  await page.goto("/investment");

  const status = page.getByRole("region", { name: "Account freshness and next actions" });
  const card = page.locator(".account-maintenance-card").filter({ hasText: account.name }).first();
  await expect(card).toContainText("Freshness unknown");
  const interval = card.getByRole("combobox", { name: `Expected review interval for ${account.name}` });
  await interval.selectOption("7");
  await expect(card).toContainText("Check due");
  await card.getByRole("button", { name: "Record local review date" }).click();
  await expect(card).toContainText("No review is due under your chosen interval.");
  await page.reload();
  const reloaded = page.locator(".account-maintenance-card").filter({ hasText: account.name }).first();
  await expect(reloaded.getByRole("combobox")).toHaveValue("7");
  await expect(reloaded).toContainText("No review is due under your chosen interval.");
  expect(writes).toBe(0);
  await expect(status).toContainText("saved in this browser only");
});

test("review interval updates in separate tabs retain each account's setting", async ({ page, context }) => {
  const report = await (await page.request.get("/api/investment/v1/snapshot")).json();
  report.source_mode = "recorded";
  report.as_of = "2030-01-01T00:00:00Z";
  report.holdings.forEach(item => {
    if (item.mark_at !== null) item.mark_at = report.as_of;
  });
  const accounts = report.accounts.filter(item => item.status === "active");
  expect(accounts.length).toBeGreaterThanOrEqual(2);
  await context.route("**/api/v1/settings", route => route.fulfill({
    json: { mode: "recorded", workspace: "investment" },
  }));
  await context.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
  await page.clock.setFixedTime(new Date("2030-01-02T00:00:00Z"));
  await page.goto("/investment");
  const secondTab = await context.newPage();
  await secondTab.clock.setFixedTime(new Date("2030-01-02T00:00:00Z"));
  await secondTab.goto("/investment");

  const firstCard = page.locator(".account-maintenance-card").filter({ hasText: accounts[0].name }).first();
  const secondCard = secondTab.locator(".account-maintenance-card").filter({ hasText: accounts[1].name }).first();
  await firstCard.getByRole("combobox").selectOption("7");
  await secondCard.getByRole("combobox").selectOption("30");
  await page.reload();
  await secondTab.reload();
  await expect(page.locator(".account-maintenance-card")
    .filter({ hasText: accounts[0].name }).first().getByRole("combobox")).toHaveValue("7");
  await expect(secondTab.locator(".account-maintenance-card")
    .filter({ hasText: accounts[1].name }).first().getByRole("combobox")).toHaveValue("30");
  await secondTab.close();
});

test("existing browser review settings remain readable and are overridden per account", async ({ page }) => {
  const report = await (await page.request.get("/api/investment/v1/snapshot")).json();
  report.source_mode = "recorded";
  const account = report.accounts.find(item => item.status === "active");
  await page.goto("/");
  await page.evaluate(({ portfolioId, accountId }) => {
    localStorage.setItem(`goldscout.account-maintenance.v1.${portfolioId}`, JSON.stringify({
      [accountId]: { intervalDays: 30, reviewedAt: null },
    }));
  }, { portfolioId: report.portfolio_id, accountId: account.id });
  await page.route("**/api/v1/settings", route => route.fulfill({
    json: { mode: "recorded", workspace: "investment" },
  }));
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
  await page.goto("/investment");

  const card = page.locator(".account-maintenance-card").filter({ hasText: account.name }).first();
  const interval = card.getByRole("combobox");
  await expect(interval).toHaveValue("30");
  await interval.selectOption("14");
  await page.reload();
  await expect(page.locator(".account-maintenance-card").filter({ hasText: account.name })
    .first().getByRole("combobox")).toHaveValue("14");
});

test("savings terms surface source checks without comparing advertised rates", async ({ page }) => {
  const report = await (await page.request.get("/api/investment/v1/snapshot")).json();
  report.source_mode = "recorded";
  report.as_of = "2030-01-01T00:00:00Z";
  report.accounts.forEach(item => {
    item.cash_observation = {
      balance_gbp: "100", difference_gbp: "0", timestamp: report.as_of,
      available_at: report.as_of, source: "Synthetic check", evidence: "Synthetic evidence",
    };
    if (item.kind === "deposit") item.terms = {
      annual_rate_pct: "4.00", rate_basis: "AER", effective_from: "2029-01-01",
      observed_at: "2029-01-01T00:00:00Z", available_at: "2029-01-01T00:00:00Z",
      source: "Synthetic terms", maturity_date: "2030-03-01", notice_days: 30,
    };
  });
  report.holdings.forEach(item => {
    if (item.mark_at !== null) item.mark_at = report.as_of;
  });
  await page.clock.setFixedTime(new Date("2030-01-02T00:00:00Z"));
  await page.route("**/api/v1/settings", route => route.fulfill({
    json: { mode: "recorded", workspace: "investment" },
  }));
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
  await page.goto("/investment");

  const account = report.accounts.find(item => item.kind === "deposit" && item.status === "active");
  const card = page.locator(".account-maintenance-card").filter({ hasText: account.name }).first();
  await card.getByRole("combobox", { name: `Expected review interval for ${account.name}` }).selectOption("30");
  await expect(card).toContainText("Recheck the savings rate, access, notice and maturity terms");
  await expect(card).toContainText("maturity 2030-03-01");
  await expect(card.getByRole("link", { name: "Update this account" }))
    .toHaveAttribute("href", `/investment/update?account_id=${encodeURIComponent(account.id)}`);
  await expect(page.getByRole("region", { name: "Account freshness and next actions" }))
    .toContainText("platform, fund, subscription, FX, tax and other costs may be missing");
});

test("unknown cash checks and prices remain explicitly unknown", async ({ page }) => {
  const report = await (await page.request.get("/api/investment/v1/snapshot")).json();
  report.accounts.forEach(item => { item.cash_observation = null; });
  report.holdings[0].mark_at = null;
  report.holdings[0].mark_available_at = null;
  report.holdings[0].value_gbp = null;
  report.complete = false;
  report.as_of = null;
  report.equity_gbp = null;
  report.since_baseline_gain_gbp = null;
  report.twr_pct = null;
  report.max_drawdown_pct = null;
  report.recorded_flow_events = [];
  report.recorded_income_events = [];
  report.recorded_fee_events = [];
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
  await page.goto("/investment");
  const summary = page.getByRole("region", { name: "Accounts and investments" });
  await expect(summary).toContainText("No cash check recorded");
  await expect(summary).toContainText("No recorded price");
  await expect(page.getByRole("region", { name: "Valuation evidence" })).toContainText("Incomplete");
  await expect(page.getByRole("region", { name: "Investment totals" })).toContainText("Unavailable");
  await expect(page.getByRole("region", { name: "Recent recorded activity" }))
    .toContainText("No such activity has been recorded since setup");
});

test("same-time activity is not silently discarded and small amounts remain exact", async ({ page }) => {
  const report = await (await page.request.get("/api/investment/v1/snapshot")).json();
  const account = report.accounts[0];
  const holding = report.holdings[0];
  const at = "2026-01-02T12:00:00.000001Z";
  report.recorded_flow_events = Array.from({ length: 5 }, (_, index) => ({
    id: `synthetic-contribution-${index}`, type: "contribution", account_id: account.id,
    destination_account_id: null, role: "unassigned", amount_gbp: "0.001",
    timestamp: at, available_at: at, evidence: "Synthetic deposit",
  }));
  report.recorded_income_events = [];
  report.recorded_fee_events = [{
    id: "synthetic-purchase", type: "buy", account_id: holding.account_id,
    product_id: holding.product_id, role: "unassigned", quantity: "1", price_gbp: "1",
    fee_gbp: "0.001", timestamp: at, available_at: "2026-01-02T12:00:00.000002Z",
    evidence: "Synthetic fill",
  }];
  report.recorded_corrections = [{
    id: "synthetic-correction", original_event_id: "old-purchase",
    replacement_event_id: "synthetic-purchase", replaced_type: "buy", account_id: holding.account_id,
    role: "unassigned", role_evidence: null, original_amount_gbp: null,
    replacement_amount_gbp: null, timestamp: "2026-01-02T12:00:00.000001Z",
    available_at: "2026-01-02T12:00:00.000002Z", evidence: "Synthetic correction",
  }];
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
  await page.goto("/investment");
  const activity = page.getByRole("region", { name: "Recent recorded activity" });
  await expect(activity.getByRole("listitem")).toHaveCount(7);
  await expect(activity).toContainText("Same-time entries are not shown in journal order");
  await expect(activity).toContainText("Correction recorded for Buy");
  await expect(activity).toContainText("prices and other records are not listed");
  await expect(activity).toContainText("Money added: £0.001");
  await expect(activity).toContainText("recorded fee £0.001");
  await activity.getByText("Source details").last().click();
  await expect(activity).toContainText("Correction recorded at 2026-01-02T12:00:00.000001Z");
  await expect(activity).toContainText("2026-01-02T12:00:00.000001Z");
  await expect(activity).toContainText("2026-01-02T12:00:00.000002Z");
  await activity.getByRole("link", { name: "View record history" }).click();
  const corrections = page.getByRole("region", { name: "Evidence-backed corrections" });
  await expect(corrections.getByRole("columnheader", { name: "Correction recorded at (UTC)" })).toBeVisible();
  await expect(corrections.getByRole("columnheader", { name: "Effective at (UTC)" })).toHaveCount(0);
  const correctionRow = corrections.getByRole("row").nth(1);
  await expect(correctionRow).toContainText("2026-01-02T12:00:00.000001Z");
  await expect(correctionRow).toContainText("2026-01-02T12:00:00.000002Z");
});

test("account cash check preselects only an account still available for recording", async ({ page }) => {
  const report = await (await page.request.get("/api/investment/v1/snapshot")).json();
  report.source_mode = "recorded";
  const selected = report.accounts.find(account => account.status === "active");
  let accounts = report.accounts.filter(account => account.status === "active").map(account => ({
    id: account.id, name: account.name, kind: account.kind,
  }));
  const duplicate = { id: "synthetic-duplicate", name: selected.name, kind: selected.kind };
  accounts.push(duplicate);
  let writes = 0;
  page.on("request", request => { if (!["GET", "HEAD"].includes(request.method())) writes++; });
  await page.route("**/api/v1/settings", route => route.fulfill({
    json: { mode: "recorded", scenario: null, workspace: "investment" },
  }));
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
  await page.route("**/api/investment/v1/recording/session", route => route.fulfill({ json: {
    enabled: true, csrf: "synthetic-csrf", opened: true, accounts, products: [],
    latest_correctable_event: null, correctable_events: [], current_balances: {}, current_holdings: [],
  } }));
  await page.goto("/investment");
  await page.getByRole("link", { name: `Check cash for ${selected.name}` }).click();
  await expect(page.getByRole("status")).toContainText("this observation does not update recorded cash");
  await expect(page.getByLabel("What are you recording?")).toHaveValue("cash_observation");
  const accountSelect = page.getByRole("combobox", { name: "Account" });
  await expect(accountSelect).toHaveValue(selected.id);
  await expect(accountSelect.locator(`option[value="${selected.id}"]`)).toContainText(selected.id);
  await expect(accountSelect.locator(`option[value="${duplicate.id}"]`)).toContainText(duplicate.id);
  expect(writes).toBe(0);

  await page.goBack();
  accounts = accounts.filter(account => account.id !== selected.id);
  await page.getByRole("link", { name: `Check cash for ${selected.name}` }).click();
  await expect(page.getByRole("status")).toContainText("account is no longer available");
  await expect(accountSelect).toHaveValue("");
  await accountSelect.selectOption(duplicate.id);
  await expect(page.getByText("This account is no longer available for a cash check.")).toHaveCount(0);
  await page.goBack();
  await page.getByRole("link", { name: `Check cash for ${selected.name}` }).click();
  await expect(page.getByRole("status")).toContainText("account is no longer available");
  await page.getByLabel("What are you recording?").selectOption("interest");
  await expect(page.getByText("This account is no longer available for a cash check.")).toHaveCount(0);
  expect(writes).toBe(0);
});
