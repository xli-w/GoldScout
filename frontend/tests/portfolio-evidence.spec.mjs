import { expect, test } from "./fixtures.mjs";

test("current account differences take priority over review settings", async ({ page }) => {
  const report = await (await page.request.get("/api/investment/v1/snapshot")).json();
  report.source_mode = "recorded";
  const account = report.accounts.find(item => item.status === "active");
  account.cash_observation = {
    balance_gbp: "100", difference_gbp: "-1", timestamp: "2030-01-01T00:00:00Z",
    available_at: "2030-01-01T00:00:00Z", source: "Synthetic statement", evidence: "Synthetic check",
  };
  report.cash_observations = [{ ...account.cash_observation, account_id: account.id }];
  await page.route("**/api/v1/settings", route => route.fulfill({
    json: { mode: "recorded", workspace: "investment" },
  }));
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
  await page.clock.setFixedTime(new Date("2030-01-02T00:00:00Z"));
  await page.goto("/investment");

  const card = page.locator(".account-maintenance-card").filter({ hasText: account.name }).first();
  await expect(card).toContainText("Action needed");
  await expect(card).toContainText("Review the recorded cash difference");
  await expect(card).not.toContainText("Freshness unknown");
  await card.getByRole("link", { name: `Update ${account.name}` }).click();
  const accountCard = page.locator(".account-maintenance-card").filter({ hasText: account.name }).first();
  const details = accountCard.locator("details");
  await expect(details).not.toHaveAttribute("open", "");
  await details.getByText("Recorded account details").click();
  await expect(details).toContainText("difference -£1.00");
  account.cash_observation.difference_gbp = "0";
  report.cash_observations.push({ ...account.cash_observation, account_id: account.id });
  await page.getByRole("link", { name: "Back to your portfolio" }).click();
  await page.getByRole("button", { name: "Refresh investment" }).click();
  await expect(card).toContainText("Action needed");
  await expect(card).toContainText("a later match does not resolve them");
});

test("workspace review defaults are explicit and unavailable defaults fail closed", async ({ page }) => {
  const report = await (await page.request.get("/api/investment/v1/snapshot")).json();
  report.source_mode = "recorded";
  report.warnings = [];
  report.cash_observations = [];
  report.accounts.forEach(item => { item.cash_observation = null; });
  report.accounts.filter(item => item.kind === "deposit")
    .forEach(item => { item.terms = null; });
  const account = report.accounts.find(item => item.kind === "broker" && item.status === "active");
  await page.route("**/api/v1/settings", route => route.fulfill({
    json: { mode: "recorded", workspace: "investment" },
  }));
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
  let settingsUnavailable = false;
  await page.route("**/api/v1/settings/session", route => settingsUnavailable
    ? route.fulfill({ status: 503, json: { error: "settings_unavailable" } })
    : route.fulfill({ json: {
      enabled: true, csrf: "synthetic-settings-csrf", revision: 1,
      preferences: { compact_mode: false, review_interval_days: 30, savings_terms_interval_days: 90 },
      connection_revision: 0, etoro_credential_revision: "0",
      connection_settings_enabled: false, connection_overrides: {},
    } }));
  await page.goto(`/investment/update?account_id=${encodeURIComponent(account.id)}`);

  let card = page.locator(".account-maintenance-card").filter({ has: page.getByRole("heading", {
    name: `Review status for ${account.name}`, exact: true,
  }) });
  const interval = card.getByRole("combobox", { name: `Expected review interval for ${account.name}` });
  await expect(interval).toHaveValue("30");
  await expect(card).toContainText("Using the workspace default.");
  await card.getByRole("button", { name: "Mark account checked today" }).click();
  await expect(card).toContainText("Next reminder:");
  await expect(card).toContainText("Using the workspace default.");
  await expect(card).not.toContainText("The browser could not save this local review setting");
  await interval.selectOption("14");
  await expect(card).toContainText("Saved for this account in this browser.");
  await page.reload();
  card = page.locator(".account-maintenance-card").filter({ has: page.getByRole("heading", {
    name: `Review status for ${account.name}`, exact: true,
  }) });
  await expect(card.getByRole("combobox", { name: `Expected review interval for ${account.name}` }))
    .toHaveValue("14");
  await expect(card).toContainText("Saved for this account in this browser.");

  await page.evaluate(portfolioId => {
    localStorage.removeItem(`goldscout.account-maintenance.v1.${portfolioId}`);
    for (let index = localStorage.length - 1; index >= 0; index--) {
      const key = localStorage.key(index);
      if (key?.startsWith(`goldscout.account-maintenance.v1.${encodeURIComponent(portfolioId)}.account.`))
        localStorage.removeItem(key);
    }
  }, report.portfolio_id);
  settingsUnavailable = true;
  await page.reload();
  card = page.locator(".account-maintenance-card").filter({ has: page.getByRole("heading", {
    name: `Review status for ${account.name}`, exact: true,
  }) });
  await expect(page.getByRole("alert")).toContainText("Workspace review defaults are unavailable");
  await expect(card.getByRole("combobox", { name: `Expected review interval for ${account.name}` }))
    .toHaveValue("");
  await expect(card).toContainText("Freshness unknown");
  await expect(card).toContainText("No interval is set");
  await expect(card.getByRole("button", { name: "Mark account checked today" })).toHaveCount(0);
  const deposit = report.accounts.find(item => item.kind === "deposit" && item.status === "active");
  expect(deposit).toBeDefined();
  await page.goto(`/investment/update?account_id=${encodeURIComponent(deposit.id)}`);
  const depositCard = page.locator(".account-maintenance-card").filter({ has: page.getByRole("heading", {
    name: `Review status for ${deposit.name}`, exact: true,
  }) });
  await depositCard.getByRole("combobox", {
    name: `Savings terms recheck interval for ${deposit.name}`,
  }).selectOption("90");
  await expect(depositCard).toContainText("Saved for this account in this browser.");
  await expect(depositCard.getByRole("button", { name: "Mark account checked today" })).toHaveCount(0);
});

test("unreadable browser review settings never produce a reassuring account status", async ({ page }) => {
  const report = await (await page.request.get("/api/investment/v1/snapshot")).json();
  report.source_mode = "recorded";
  report.complete = true;
  report.cash_observations = [];
  report.accounts.forEach(account => { account.cash_observation = null; });
  const account = report.accounts.find(item => item.kind === "broker" && item.status === "active");
  await page.goto("/");
  await page.evaluate(portfolioId => {
    localStorage.setItem(`goldscout.account-maintenance.v1.${portfolioId}`, "{");
  }, report.portfolio_id);
  await page.route("**/api/v1/settings", route => route.fulfill({
    json: { mode: "recorded", workspace: "investment" },
  }));
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
  await page.goto("/investment");
  const card = page.locator(".account-overview-row").filter({ hasText: account.name }).first();
  await expect(page.getByRole("alert").filter({ hasText: "Saved account review settings are invalid" })).toBeVisible();
  await expect(card).toContainText("Review settings unavailable");
  await expect(card).not.toContainText("Within workspace default");
  await expect(page.getByRole("button", { name: "Start account review" })).toBeDisabled();
  await card.getByRole("link", { name: `Update ${account.name}` }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Saved account review settings are invalid" })).toBeVisible();
  await expect(page.locator(".account-maintenance-card")).toContainText("Review settings unavailable");
});

test("a later review-setting read failure makes cached reminders unavailable", async ({ page }) => {
  const report = await (await page.request.get("/api/investment/v1/snapshot")).json();
  report.source_mode = "recorded";
  report.complete = true;
  report.holdings = [];
  report.cash_observations = [];
  const account = report.accounts.find(item => item.kind === "broker" && item.status === "active");
  account.cash_observation = null;
  const key = `goldscout.account-maintenance.v1.${encodeURIComponent(report.portfolio_id)}.account.${encodeURIComponent(account.id)}`;
  await page.goto("/");
  await page.evaluate(storageKey => localStorage.setItem(storageKey, JSON.stringify({
    intervalDays: 30, termsIntervalDays: null, reviewedAt: null,
  })), key);
  await page.route("**/api/v1/settings", route => route.fulfill({
    json: { mode: "recorded", workspace: "investment" },
  }));
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
  await page.goto(`/investment/update?account_id=${encodeURIComponent(account.id)}`);
  const card = page.locator(".account-maintenance-card");
  const interval = card.getByLabel(`Expected review interval for ${account.name}`);
  await expect(interval).toHaveValue("30");
  await page.evaluate(storageKey => {
    const original = Storage.prototype.getItem;
    window.restoreSyntheticStorage = () => { Storage.prototype.getItem = original; };
    Storage.prototype.getItem = function (item) {
      if (item === storageKey) throw new Error("Synthetic storage read failure");
      return original.call(this, item);
    };
  }, key);
  await interval.selectOption("7");
  await expect(card).toContainText("Review settings unavailable");
  await expect(card.getByRole("button", { name: "Mark account checked today" })).toBeDisabled();
  await page.evaluate(() => window.restoreSyntheticStorage());
  await interval.selectOption("7");
  await expect.poll(() => page.evaluate(storageKey => JSON.parse(localStorage.getItem(storageKey)).intervalDays, key))
    .toBe(7);
  await expect(card).not.toContainText("Review settings unavailable");
});

test("many accounts remain compact and usable on a narrow overview", async ({ page }) => {
  const report = await (await page.request.get("/api/investment/v1/snapshot")).json();
  report.source_mode = "recorded";
  const template = report.accounts.find(account => account.status === "active");
  for (let index = 0; index < 20; index++) report.accounts.push({
    ...template, id: `synthetic-extra-${index}`, name: `Synthetic account ${index}`,
  });
  await page.route("**/api/v1/settings", route => route.fulfill({
    json: { mode: "recorded", workspace: "investment" },
  }));
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
  await page.setViewportSize({ width: 320, height: 844 });
  await page.goto("/investment");
  const accounts = page.getByRole("region", { name: "Account freshness and next actions" });
  await expect(accounts.locator(".account-overview-row"))
    .toHaveCount(report.accounts.filter(account => account.status === "active").length);
  await expect(accounts.getByRole("combobox")).toHaveCount(0);
  await expect(accounts.getByRole("button", { name: "Start account review" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("duplicate account names include their journal IDs in review cards", async ({ page }) => {
  const report = await (await page.request.get("/api/investment/v1/snapshot")).json();
  report.source_mode = "recorded";
  const active = report.accounts.filter(item => item.status === "active");
  expect(active.length).toBeGreaterThan(1);
  active[1].name = active[0].name;
  await page.route("**/api/v1/settings", route => route.fulfill({
    json: { mode: "recorded", workspace: "investment" },
  }));
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
  await page.goto("/investment");

  for (const account of active.slice(0, 2))
    await expect(page.getByRole("heading", { name: `${account.name} (${account.id})` })).toBeVisible();
});

test("historical performance limitations remain distinct from current actions", async ({ page }) => {
  const report = await (await page.request.get("/api/investment/v1/snapshot")).json();
  report.source_mode = "recorded";
  report.twr_pct = null;
  report.max_drawdown_pct = null;
  report.recorded_corrections = [];
  report.cash_observations = [];
  report.warnings = [];
  report.accounts.forEach(account => { account.cash_observation = null; });
  await page.route("**/api/v1/settings", route => route.fulfill({
    json: { mode: "recorded", workspace: "investment" },
  }));
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
  await page.goto("/investment");

  const actions = page.getByRole("region", { name: "What needs attention" });
  await expect(actions).not.toContainText("Some performance figures are unavailable");
  const history = page.getByRole("region", { name: "Historical performance limitations" });
  await expect(history).toContainText("Some performance figures are unavailable");
  await expect(history).toContainText("You can continue to record and review your portfolio");
});

test("a correction keeps cash review actionable when replay shows a match", async ({ page }) => {
  const report = await (await page.request.get("/api/investment/v1/snapshot")).json();
  report.source_mode = "recorded";
  const account = report.accounts.find(item => item.status === "active");
  account.cash_observation = {
    balance_gbp: "100", difference_gbp: "0", timestamp: "2030-01-01T00:00:00Z",
    available_at: "2030-01-01T00:00:00Z", source: "Synthetic statement", evidence: "Synthetic check",
  };
  report.cash_observations = [{ ...account.cash_observation, account_id: account.id }];
  report.recorded_corrections = [{ id: "synthetic-correction", original_event_id: "original",
    replacement_event_id: "replacement", replaced_type: "interest", account_id: account.id,
    role: "unassigned", role_evidence: null, original_amount_gbp: "1", replacement_amount_gbp: "2",
    timestamp: "2030-01-01T00:00:00Z", available_at: "2030-01-01T00:00:00Z", evidence: "Synthetic correction" }];
  report.warnings.push("Historical correction replay may have changed past cash-observation differences. Preserve prior reports and review the original observations and correction evidence.");
  await page.route("**/api/v1/settings", route => route.fulfill({
    json: { mode: "recorded", workspace: "investment" },
  }));
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
  await page.clock.setFixedTime(new Date("2030-01-02T00:00:00Z"));
  await page.goto(`/investment/update?account_id=${encodeURIComponent(account.id)}`);

  const card = page.locator(".account-maintenance-card").filter({ hasText: account.name }).first();
  await card.getByRole("combobox", { name: `Expected review interval for ${account.name}` }).selectOption("7");
  await card.getByRole("button", { name: "Mark account checked today" }).click();
  await expect(card).toContainText("Action needed");
  await expect(card).toContainText("replay may have changed past differences");
  await expect(card).not.toContainText("No review is due");
  await card.getByRole("link", { name: "Review cash evidence" }).click();
  await expect(page.getByRole("region", { name: "Cash evidence overview" })).toBeVisible();
});

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
    .toContainText("Valuation recorded");
  await page.goto("/investment?view=history#investment-account-summary");
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

  await page.locator(".investment-detail > summary").filter({ hasText: "Recent recorded activity" }).click();
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
  report.cash_observations = report.accounts.map(item => ({
    ...item.cash_observation, account_id: item.id,
  }));
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
  await page.goto(`/investment/update?account_id=${encodeURIComponent(account.id)}`);

  const status = page.getByRole("region", { name: "Account review reminders" });
  const card = page.locator(".account-maintenance-card").filter({ hasText: account.name }).first();
  await expect(card).toContainText("Choose how often you expect to review this account.");
  const interval = card.getByRole("combobox", { name: `Expected review interval for ${account.name}` });
  await interval.selectOption("7");
  await expect(card).toContainText("Check due");
  if (account.kind === "deposit")
    await card.getByRole("combobox", { name: `Savings terms recheck interval for ${account.name}` }).selectOption("7");
  await page.clock.setFixedTime(new Date("2030-01-02T00:00:30Z"));
  await card.getByRole("button", { name: "Mark account checked today" }).click();
  await expect(card).toContainText("No review is due under your chosen interval.");
  await page.reload();
  const reloaded = page.locator(".account-maintenance-card").filter({ hasText: account.name }).first();
  await expect(reloaded.getByRole("combobox", { name: `Expected review interval for ${account.name}` })).toHaveValue("7");
  await expect(reloaded).toContainText("No review is due under your chosen interval.");
  expect(writes).toBe(0);
  await expect(status).toContainText("saved in this browser only");

  await page.clock.setFixedTime(new Date("2030-01-01T23:30:00Z"));
  await page.reload();
  await expect(page.locator(".account-maintenance-card").filter({ hasText: account.name }).first())
    .toContainText("Review this account's records");
});

test("local review actions use the action time rather than the last display refresh", async ({ page }) => {
  const report = await (await page.request.get("/api/investment/v1/snapshot")).json();
  report.source_mode = "recorded";
  const account = report.accounts.find(item => item.status === "active");
  await page.route("**/api/v1/settings", route => route.fulfill({
    json: { mode: "recorded", workspace: "investment" },
  }));
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
  await page.clock.setFixedTime(new Date("2030-01-02T00:00:00Z"));
  await page.goto(`/investment/update?account_id=${encodeURIComponent(account.id)}`);
  const card = page.locator(".account-maintenance-card").filter({ hasText: account.name }).first();
  await card.getByRole("combobox", { name: `Expected review interval for ${account.name}` }).selectOption("7");

  await page.clock.setFixedTime(new Date("2030-01-02T00:00:30Z"));
  await card.getByRole("button", { name: "Mark account checked today" }).click();
  const preferenceKey = `goldscout.account-maintenance.v1.${encodeURIComponent(report.portfolio_id)}.account.${encodeURIComponent(account.id)}`;
  await expect.poll(() => page.evaluate(key => JSON.parse(localStorage.getItem(key)).reviewedAt, preferenceKey))
    .toBe("2030-01-02T00:00:30.000Z");
  await page.clock.setFixedTime(new Date("2030-01-02T00:00:45Z"));
  await page.getByRole("link", { name: "Back to your portfolio" }).click();
  const guided = page.getByRole("region", { name: "Guided portfolio review" });
  await guided.getByRole("button", { name: "Start account review" }).click();
  await page.getByRole("button", { name: "I checked this account — continue review" }).click();
  for (const other of report.accounts.filter(item => item.status === "active" && item.id !== account.id)) {
    await guided.getByRole("link", { name: "Continue review" }).click();
    await expect(page.getByRole("heading", { name: other.name, exact: true })).toBeVisible();
    await page.getByRole("button", { name: "I checked this account — continue review" }).click();
  }
  await expect(guided).toContainText("Accounts checked.");
  await expect(guided).not.toContainText(`${account.name} reminder is due`);
  await guided.getByRole("button", { name: "Finish review and save this value for next time" }).click();
  await expect(guided).toContainText("Review complete.");

  const saved = await page.evaluate(({ portfolioId, accountId }) => ({
    cycle: JSON.parse(localStorage.getItem(`goldscout.guided-review.v1.${encodeURIComponent(portfolioId)}`)),
    preference: JSON.parse(localStorage.getItem(`goldscout.account-maintenance.v1.${encodeURIComponent(portfolioId)}.account.${encodeURIComponent(accountId)}`)),
    checkpoint: JSON.parse(localStorage.getItem(`goldscout.review-checkpoint.v1.${portfolioId}`)),
  }), { portfolioId: report.portfolio_id, accountId: account.id });
  expect(saved.cycle.startedAt).toBe("2030-01-02T00:00:45.000Z");
  expect(saved.preference.reviewedAt).toBe("2030-01-02T00:00:45.000Z");
  expect(saved.checkpoint.savedAt).toBe("2030-01-02T00:00:45.000Z");
});

test("guided review resumes, records completion, and restarts when journal revision changes", async ({ page }) => {
  const report = await (await page.request.get("/api/investment/v1/snapshot")).json();
  report.source_mode = "recorded";
  report.complete = true;
  const active = report.accounts.filter(item => item.status === "active");
  await page.route("**/api/v1/settings", route => route.fulfill({
    json: { mode: "recorded", workspace: "investment" },
  }));
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
  await page.goto("/investment");

  const guided = page.getByRole("region", { name: "Guided portfolio review" });
  await guided.getByRole("button", { name: "Start account review" }).click();
  for (let index = 0; index < active.length; index++) {
    const account = active[index];
    await expect(page.getByRole("heading", { name: account.name, exact: true })).toBeVisible();
    await page.getByRole("button", { name: "I checked this account — continue review" }).click();
    if (index === 0) {
      await page.reload();
      await expect(page.getByRole("region", { name: "Guided portfolio review" }))
        .toContainText(`1 of ${active.length} accounts checked`);
    }
    if (index < active.length - 1) await guided.getByRole("link", { name: "Continue review" }).click();
  }
  await expect(guided).toContainText("Accounts checked.");
  await guided.getByRole("button", { name: "Finish review and save this value for next time" }).click();
  await expect(guided).toContainText("Review complete.");

  report.revision++;
  await page.reload();
  await expect(page.getByRole("region", { name: "Guided portfolio review" }))
    .toContainText("The journal changed after this review began");
  await expect(page.getByRole("button", { name: "Start a new review" })).toBeVisible();
  const checkpointKey = `goldscout.review-checkpoint.v1.${report.portfolio_id}`;
  const originalCheckpoint = await page.evaluate(key => localStorage.getItem(key), checkpointKey);
  await guided.getByRole("button", { name: "Start a new review" }).click();
  await expect(page.getByRole("heading", { name: active[0].name, exact: true })).toBeVisible();
  expect(await page.evaluate(key => localStorage.getItem(key), checkpointKey)).toBe(originalCheckpoint);
});

test("guided review completion retains cash differences on retired accounts", async ({ page }) => {
  const report = await (await page.request.get("/api/investment/v1/snapshot")).json();
  report.source_mode = "recorded";
  report.complete = true;
  const retired = report.accounts[0];
  report.accounts.forEach(item => { item.status = item.id === retired.id ? "retired" : "active"; });
  const observation = {
    balance_gbp: "99", difference_gbp: "-1",
    timestamp: "2030-01-01T00:00:00Z", available_at: "2030-01-01T00:00:00Z",
    source: "Synthetic statement", evidence: "Synthetic cash comparison",
  };
  retired.cash_observation = observation;
  report.cash_observations = [...report.cash_observations,
    { ...observation, account_id: retired.id }];
  const active = report.accounts.filter(item => item.status === "active");
  expect(active.length).toBeGreaterThan(0);
  await page.route("**/api/v1/settings", route => route.fulfill({
    json: { mode: "recorded", workspace: "investment" },
  }));
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
  await page.goto("/investment");

  const guided = page.getByRole("region", { name: "Guided portfolio review" });
  await guided.getByRole("button", { name: "Start account review" }).click();
  for (let index = 0; index < active.length; index++) {
    await expect(page.getByRole("heading", { name: active[index].name, exact: true })).toBeVisible();
    await page.getByRole("button", { name: "I checked this account — continue review" }).click();
    if (index < active.length - 1) await guided.getByRole("link", { name: "Continue review" }).click();
  }
  await expect(guided).toContainText("Accounts checked.");
  await guided.getByRole("button", { name: "Finish review and save this value for next time" }).click();
  await expect(guided).toContainText("Review complete.");
  await expect(guided.getByRole("list", { name: "Unresolved review items" }))
    .toContainText(`${retired.name} (retired) has a recorded cash difference`);
  await expect(guided).not.toContainText("No open evidence issues");
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
  await page.goto(`/investment/update?account_id=${encodeURIComponent(accounts[0].id)}`);
  const secondTab = await context.newPage();
  await secondTab.clock.setFixedTime(new Date("2030-01-02T00:00:00Z"));
  await secondTab.goto(`/investment/update?account_id=${encodeURIComponent(accounts[1].id)}`);

  const firstCard = page.locator(".account-maintenance-card").filter({ hasText: accounts[0].name }).first();
  const secondCard = secondTab.locator(".account-maintenance-card").filter({ hasText: accounts[1].name }).first();
  await firstCard.getByRole("combobox", {
    name: `Expected review interval for ${accounts[0].name}`,
  }).selectOption("7");
  await secondCard.getByRole("combobox", {
    name: `Expected review interval for ${accounts[1].name}`,
  }).selectOption("30");
  await page.reload();
  await secondTab.reload();
  await expect(page.locator(".account-maintenance-card")
    .filter({ hasText: accounts[0].name }).first().getByRole("combobox", {
      name: `Expected review interval for ${accounts[0].name}`,
    })).toHaveValue("7");
  await expect(secondTab.locator(".account-maintenance-card")
    .filter({ hasText: accounts[1].name }).first().getByRole("combobox", {
      name: `Expected review interval for ${accounts[1].name}`,
    })).toHaveValue("30");
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
  await page.goto(`/investment/update?account_id=${encodeURIComponent(account.id)}`);

  const card = page.locator(".account-maintenance-card").filter({ hasText: account.name }).first();
  const interval = card.getByRole("combobox", {
    name: `Expected review interval for ${account.name}`,
  });
  await expect(interval).toHaveValue("30");
  await expect(card.getByRole("combobox", {
    name: `Savings terms recheck interval for ${account.name}`,
  })).toHaveValue("");
  await interval.selectOption("14");
  await page.reload();
  await expect(page.locator(".account-maintenance-card").filter({ hasText: account.name })
    .first().getByRole("combobox", {
      name: `Expected review interval for ${account.name}`,
    })).toHaveValue("14");
});

test("savings reminders flag an unknown rate even when the observation is recent", async ({ page }) => {
  const report = await (await page.request.get("/api/investment/v1/snapshot")).json();
  report.source_mode = "recorded";
  report.complete = true;
  report.as_of = "2030-01-02T00:00:00Z";
  report.recorded_corrections = [];
  report.cash_observations = [];
  const account = report.accounts.find(item => item.kind === "deposit" && item.status === "active");
  account.cash_observation = {
    balance_gbp: "100", difference_gbp: "0", timestamp: report.as_of,
    available_at: report.as_of, source: "Synthetic check", evidence: "Synthetic evidence",
  };
  account.terms = {
    annual_rate_pct: null, rate_basis: null, effective_from: "2030-01-01",
    observed_at: report.as_of, available_at: report.as_of, source: "Synthetic terms",
    maturity_date: null, notice_days: null,
  };
  report.holdings = [];
  const key = `goldscout.account-maintenance.v1.${encodeURIComponent(report.portfolio_id)}.account.${encodeURIComponent(account.id)}`;
  await page.goto("/");
  await page.evaluate(storageKey => localStorage.setItem(storageKey, JSON.stringify({
    intervalDays: 30, termsIntervalDays: 30, reviewedAt: "2030-01-02T00:00:00Z",
  })), key);
  await page.clock.setFixedTime(new Date("2030-01-02T00:00:00Z"));
  await page.route("**/api/v1/settings", route => route.fulfill({
    json: { mode: "recorded", workspace: "investment" },
  }));
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
  await page.goto(`/investment/update?account_id=${encodeURIComponent(account.id)}`);
  const card = page.locator(".account-maintenance-card");
  await expect(card).toContainText("Check due");
  await expect(card).toContainText("Recheck the savings rate");
  await expect(card).toContainText("notice period and maturity date are not recorded; check whether they apply");
  await expect(card).not.toContainText("Within account interval");
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
  report.cash_observations = report.accounts.map(item => ({
    ...item.cash_observation, account_id: item.id,
  }));
  report.holdings.forEach(item => {
    if (item.mark_at !== null) item.mark_at = report.as_of;
  });
  await page.clock.setFixedTime(new Date("2030-01-02T00:00:00Z"));
  await page.route("**/api/v1/settings", route => route.fulfill({
    json: { mode: "recorded", workspace: "investment" },
  }));
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
  const account = report.accounts.find(item => item.kind === "deposit" && item.status === "active");
  await page.goto(`/investment/update?account_id=${encodeURIComponent(account.id)}`);
  const card = page.locator(".account-maintenance-card").filter({ hasText: account.name }).first();
  await card.getByRole("combobox", { name: `Expected review interval for ${account.name}` }).selectOption("365");
  await card.getByRole("combobox", {
    name: `Savings terms recheck interval for ${account.name}`,
  }).selectOption("30");
  await expect(card).toContainText("Recheck the savings rate, access, notice and maturity terms");
  await expect(card).toContainText("maturity 2030-03-01");
  await expect(page.getByRole("link", { name: "Record this account's savings terms" }))
    .toHaveAttribute("href", new RegExp(`account_id=${encodeURIComponent(account.id)}.*kind=savings_terms`));
  await expect(page.getByRole("region", { name: "Account review reminders" }))
    .toContainText("fund, subscription, FX, tax and other costs may be missing");
});

test("direct concentration is bounded to complete recorded holdings", async ({ page }) => {
  const report = await (await page.request.get("/api/investment/v1/snapshot")).json();
  report.source_mode = "recorded";
  await page.route("**/api/v1/settings", route => route.fulfill({
    json: { mode: "recorded", workspace: "investment" },
  }));
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
  await page.goto("/investment?view=history#investment-holdings");

  const concentration = page.getByRole("region", { name: "Direct investment concentration" });
  await expect(concentration).toBeVisible();
  await expect(concentration).toContainText("Share of the complete recorded portfolio value");
  await expect(concentration).toContainText("does not look through funds");
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
  await expect(page.getByRole("region", { name: "Valuation evidence" })).toContainText("Incomplete");
  await expect(page.getByRole("region", { name: "Investment totals" })).toContainText("Unavailable");
  await expect(page.getByRole("region", { name: "Recent recorded activity" }))
    .toContainText("No such activity has been recorded since setup");
  await page.goto("/investment?view=history#investment-account-summary");
  const summary = page.getByRole("region", { name: "Accounts and investments" });
  await expect(summary).toContainText("No cash check recorded");
  await expect(summary).toContainText("No recorded price");
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
  const preview = page.getByRole("region", { name: "Recent recorded activity" });
  await expect(preview.getByRole("listitem")).toHaveCount(3);
  await expect(preview).toContainText("Same-time entries may be omitted");
  await page.getByRole("link", { name: "History and insights" }).click();
  await page.locator(".investment-detail > summary").filter({ hasText: "Recent recorded activity" }).click();
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

test("a bookmark opens its evidence when a later journal revision adds it", async ({ page }) => {
  const report = await (await page.request.get("/api/investment/v1/snapshot")).json();
  report.recorded_corrections = [];
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
  await page.goto("/investment?view=history#investment-corrections");
  await expect(page.locator("#investment-corrections")).toHaveCount(0);
  const at = "2026-01-02T12:00:00Z";
  report.recorded_corrections = [{
    id: "synthetic-correction", original_event_id: "old-purchase",
    replacement_event_id: "new-purchase", replaced_type: "buy", account_id: report.accounts[0].id,
    role: "unassigned", role_evidence: null, original_amount_gbp: null,
    replacement_amount_gbp: null, timestamp: at, available_at: at, evidence: "Synthetic correction",
  }];
  report.revision++;
  await page.getByRole("button", { name: "Refresh investment" }).click();
  await expect(page.locator("#investment-records")).toHaveAttribute("open", "");
  await expect(page.getByRole("region", { name: "Evidence-backed corrections" })).toBeVisible();
});

test("the compact activity preview does not claim to show 100 entries", async ({ page }) => {
  const report = await (await page.request.get("/api/investment/v1/snapshot")).json();
  const at = "2026-01-02T12:00:00Z";
  report.recorded_flow_events = Array.from({ length: 101 }, (_, index) => ({
    id: `synthetic-contribution-${index}`, type: "contribution", account_id: report.accounts[0].id,
    destination_account_id: null, role: "unassigned", amount_gbp: "1",
    timestamp: at, available_at: at, evidence: "Synthetic deposit",
  }));
  report.recorded_income_events = [];
  report.recorded_fee_events = [];
  report.recorded_account_fees = [];
  report.recorded_corrections = [];
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
  await page.goto("/investment");
  const preview = page.getByRole("region", { name: "Recent recorded activity" });
  await expect(preview.getByRole("listitem")).toHaveCount(3);
  await expect(preview).not.toContainText("only the first 100 are shown here");
  await page.getByRole("link", { name: "History and insights" }).click();
  await page.locator(".investment-detail > summary").filter({ hasText: "Recent recorded activity" }).click();
  const history = page.getByRole("region", { name: "Recent recorded activity" });
  await expect(history.getByRole("listitem")).toHaveCount(100);
  await expect(history).toContainText("only the first 100 are shown here");
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
  await page.goto("/investment?view=history#investment-account-summary");
  await page.getByRole("link", { name: `Check cash for ${selected.name}` }).click();
  await expect(page).toHaveURL(/\/investment\/record\?record=cash-check/);
  await expect(page.getByRole("region", { name: "Manual recording" }).getByRole("status"))
    .toContainText("this observation does not update recorded cash");
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
