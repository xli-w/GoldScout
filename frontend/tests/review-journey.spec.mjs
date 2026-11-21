import { expect, test } from "@playwright/test";

test("a saved account update preserves other checks and a quiet account can finish", async ({ page }) => {
  const report = await (await page.request.get("/api/investment/v1/snapshot")).json();
  report.source_mode = "recorded";
  const accounts = report.accounts.filter(item => item.status === "active");
  expect(accounts.length).toBeGreaterThanOrEqual(2);
  const [first, second] = accounts;
  const session = {
    enabled: true, csrf: "synthetic-csrf", opened: true,
    portfolio_id: report.portfolio_id, revision: report.revision,
    accounts: accounts.map(({ id, name, kind }) => ({ id, name, kind })),
    products: [...new Map(report.holdings.map(item => [item.product_id, {
      id: item.product_id, name: item.name, sleeve: item.sleeve,
      quantity_increment: "1", minimum_notional_gbp: "1",
    }])).values()],
    current_balances: Object.fromEntries(accounts.map(item => [item.id, item.cash_gbp])),
    current_holdings: report.holdings.map(({ account_id, product_id, quantity }) => ({ account_id, product_id, quantity })),
    latest_correctable_event: null, correctable_events: [],
  };
  let event;
  await page.route("**/api/v1/settings", route => route.fulfill({
    json: { mode: "recorded", workspace: "investment" },
  }));
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
  await page.route("**/api/investment/v1/recording/session", route => route.fulfill({ json: session }));
  await page.route("**/api/investment/v1/recording/preview", route => {
    event = route.request().postDataJSON().event;
    return route.fulfill({ json: {
      preview_id: "synthetic-preview", revision: report.revision, event,
      before: report, after: { ...report, revision: report.revision + 1 },
      expires_at: "2030-01-01T12:05:00Z",
    } });
  });
  await page.route("**/api/investment/v1/recording/confirm", route => {
    report.revision++;
    session.revision = report.revision;
    return route.fulfill({ json: { id: event.id, revision: report.revision } });
  });
  await page.clock.setFixedTime(new Date("2030-01-01T12:00:00Z"));
  await page.goto("/investment");
  const guided = page.getByRole("region", { name: "Guided portfolio review" });
  await guided.getByRole("button", { name: "Start account review" }).click();
  await expect(page.getByRole("heading", { name: first.name, exact: true })).toBeVisible();
  await page.getByRole("button", { name: "I checked this account — continue review" }).click();
  await expect(guided).toContainText(`1 of ${accounts.length} accounts checked`);
  await guided.getByRole("link", { name: "Continue review" }).click();
  await expect(page.getByRole("heading", { name: second.name, exact: true })).toBeVisible();
  await page.getByRole("link", { name: "Check this account's cash" }).click();
  await page.getByLabel("Observed GBP balance").fill("99");
  await page.getByLabel("Statement/source description").fill("Synthetic statement");
  await page.getByLabel("Applies at (local time)").fill("2030-01-01T12:00");
  await page.getByLabel("Source available from (local time)").fill("2030-01-01T12:00");
  await page.getByLabel("Source description / correction reason").fill("Synthetic cash check");
  await page.getByRole("button", { name: "Preview record" }).click();
  await page.getByRole("button", { name: "Confirm and append record" }).click();
  await expect(page).toHaveURL(new RegExp(`/investment/update\\?account_id=${second.id}`));
  await page.getByRole("button", { name: "I checked this account — continue review" }).click();
  await expect(page.getByRole("region", { name: "Guided portfolio review" }))
    .toContainText(`2 of ${accounts.length} accounts checked`);
  expect(event.type).toBe("cash_observation");
});

test("an outside journal change prevents a stale account check", async ({ page }) => {
  const report = await (await page.request.get("/api/investment/v1/snapshot")).json();
  report.source_mode = "recorded";
  await page.route("**/api/v1/settings", route => route.fulfill({
    json: { mode: "recorded", workspace: "investment" },
  }));
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
  await page.goto("/investment");
  await page.getByRole("button", { name: "Start account review" }).click();
  await expect(page.getByRole("button", { name: "I checked this account — continue review" })).toBeVisible();
  report.revision++;
  await page.getByRole("button", { name: "I checked this account — continue review" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "The journal changed" })).toBeVisible();
  await page.getByRole("link", { name: "Back to your portfolio" }).click();
  await expect(page.getByRole("region", { name: "Guided portfolio review" }))
    .toContainText("The journal changed after this review began");
  expect(await page.evaluate(portfolioId => JSON.parse(localStorage.getItem(
    `goldscout.guided-review.v1.${encodeURIComponent(portfolioId)}`)).checkedAccountIds, report.portfolio_id))
    .toEqual([]);
});

test("an interval edit preserves a concurrent account check", async ({ page, context }) => {
  const report = await (await page.request.get("/api/investment/v1/snapshot")).json();
  report.source_mode = "recorded";
  const account = report.accounts.find(item => item.status === "active");
  await context.route("**/api/v1/settings", route => route.fulfill({
    json: { mode: "recorded", workspace: "investment" },
  }));
  await context.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
  await page.goto("/investment");
  const key = `goldscout.account-maintenance.v1.${encodeURIComponent(report.portfolio_id)}.account.${encodeURIComponent(account.id)}`;
  await page.evaluate(storageKey => localStorage.setItem(storageKey, JSON.stringify({
    intervalDays: 30, termsIntervalDays: null, reviewedAt: null,
  })), key);
  const other = await context.newPage();
  await other.goto(`/investment/update?account_id=${encodeURIComponent(account.id)}`);
  const interval = other.getByLabel(`Expected review interval for ${account.name}`);
  await expect(interval).toHaveValue("30");
  await page.evaluate(storageKey => {
    window.syntheticLockAcquired = false;
    void navigator.locks.request("goldscout-recording-confirmation", async () => {
      const current = JSON.parse(localStorage.getItem(storageKey));
      window.syntheticLockAcquired = true;
      await new Promise(resolve => { window.releaseSyntheticLock = resolve; });
      current.reviewedAt = "2030-01-01T12:00:00Z";
      localStorage.setItem(storageKey, JSON.stringify(current));
    });
  }, key);
  await expect.poll(() => page.evaluate(() => window.syntheticLockAcquired)).toBe(true);
  await interval.selectOption("7");
  await page.evaluate(() => window.releaseSyntheticLock());
  await expect.poll(() => page.evaluate(storageKey => JSON.parse(localStorage.getItem(storageKey)), key))
    .toEqual({ intervalDays: 7, termsIntervalDays: null, reviewedAt: "2030-01-01T12:00:00Z" });
});

test("an account-check read failure makes reminder status unavailable", async ({ page }) => {
  const report = await (await page.request.get("/api/investment/v1/snapshot")).json();
  report.source_mode = "recorded";
  report.complete = true;
  report.holdings = [];
  report.cash_observations = [];
  report.recorded_corrections = [];
  const account = report.accounts.find(item => item.status === "active");
  account.cash_observation = null;
  await page.route("**/api/v1/settings", route => route.fulfill({
    json: { mode: "recorded", workspace: "investment" },
  }));
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
  await page.goto("/investment");
  const key = `goldscout.account-maintenance.v1.${encodeURIComponent(report.portfolio_id)}.account.${encodeURIComponent(account.id)}`;
  await page.evaluate(storageKey => localStorage.setItem(storageKey, JSON.stringify({
    intervalDays: 30, termsIntervalDays: null, reviewedAt: null,
  })), key);
  await page.getByRole("button", { name: "Start account review" }).click();
  await expect(page.getByLabel(`Expected review interval for ${account.name}`)).toHaveValue("30");
  await page.evaluate(storageKey => {
    const original = Storage.prototype.getItem;
    Storage.prototype.getItem = function (item) {
      if (item === storageKey) throw new Error("Synthetic storage read failure");
      return original.call(this, item);
    };
  }, key);
  await page.getByRole("button", { name: "I checked this account — continue review" }).click();
  await expect(page.locator(".account-maintenance-card")).toContainText("Review settings unavailable");
  await expect(page.getByRole("alert").filter({ hasText: "A reminder date may have been saved" })).toBeVisible();
  expect(await page.evaluate(portfolioId => JSON.parse(localStorage.getItem(
    `goldscout.guided-review.v1.${encodeURIComponent(portfolioId)}`)).checkedAccountIds, report.portfolio_id))
    .toEqual([]);
});

test("finishing rereads review settings before saving a comparison point", async ({ page }) => {
  const report = await (await page.request.get("/api/investment/v1/snapshot")).json();
  report.source_mode = "recorded";
  const account = report.accounts.find(item => item.status === "active");
  report.accounts.forEach(item => { item.status = item.id === account.id ? "active" : "retired"; });
  await page.route("**/api/v1/settings", route => route.fulfill({
    json: { mode: "recorded", workspace: "investment" },
  }));
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
  await page.goto("/investment");
  await page.getByRole("button", { name: "Start account review" }).click();
  await page.getByRole("button", { name: "I checked this account — continue review" }).click();
  const guided = page.getByRole("region", { name: "Guided portfolio review" });
  await expect(guided).toContainText("Accounts checked.");
  const key = `goldscout.account-maintenance.v1.${encodeURIComponent(report.portfolio_id)}.account.${encodeURIComponent(account.id)}`;
  await page.evaluate(storageKey => {
    const original = Storage.prototype.getItem;
    window.restoreSyntheticStorage = () => { Storage.prototype.getItem = original; };
    Storage.prototype.getItem = function (item) {
      if (item === storageKey) throw new Error("Synthetic storage read failure");
      return original.call(this, item);
    };
  }, key);
  const finish = guided.getByRole("button", { name: "Finish review and save this value for next time" });
  await finish.click();
  await expect(finish).toBeDisabled();
  expect(await page.evaluate(portfolioId =>
    localStorage.getItem(`goldscout.review-checkpoint.v1.${portfolioId}`), report.portfolio_id)).toBeNull();
  await page.evaluate(() => window.restoreSyntheticStorage());
  await page.reload();
  await guided.getByRole("button", { name: "Finish review and save this value for next time" }).click();
  await expect(guided).toContainText("Review complete.");
});

test("a second finish cannot overwrite a checkpoint saved by the first", async ({ page }) => {
  const report = await (await page.request.get("/api/investment/v1/snapshot")).json();
  report.source_mode = "recorded";
  const account = report.accounts.find(item => item.status === "active");
  report.accounts.forEach(item => { item.status = item.id === account.id ? "active" : "retired"; });
  let blockFinish = false;
  let startBlockedRead;
  let releaseBlockedRead;
  const readStarted = new Promise(resolve => { startBlockedRead = resolve; });
  const readReleased = new Promise(resolve => { releaseBlockedRead = resolve; });
  await page.route("**/api/v1/settings", route => route.fulfill({
    json: { mode: "recorded", workspace: "investment" },
  }));
  await page.route("**/api/investment/v1/snapshot", async route => {
    if (blockFinish) {
      blockFinish = false;
      startBlockedRead();
      await readReleased;
    }
    return route.fulfill({ json: report });
  });
  await page.goto("/investment");
  await page.getByRole("button", { name: "Start account review" }).click();
  await page.getByRole("button", { name: "I checked this account — continue review" }).click();
  const guided = page.getByRole("region", { name: "Guided portfolio review" });
  await expect(guided).toContainText("Accounts checked.");
  await page.evaluate(() => {
    window.syntheticCheckpointWrites = 0;
    const original = Storage.prototype.setItem;
    Storage.prototype.setItem = function (key, value) {
      if (key.startsWith("goldscout.review-checkpoint.v1.")) window.syntheticCheckpointWrites++;
      return original.call(this, key, value);
    };
  });
  blockFinish = true;
  await guided.getByRole("button", { name: "Finish review and save this value for next time" }).dblclick();
  await readStarted;
  releaseBlockedRead();
  await expect(guided).toContainText("Review complete.");
  await expect(page.getByRole("alert").filter({ hasText: "The review changed" })).toBeVisible();
  expect(await page.evaluate(() => window.syntheticCheckpointWrites)).toBe(1);
});

test("a failed comparison-point write can be retried without losing review progress", async ({ page }) => {
  const report = await (await page.request.get("/api/investment/v1/snapshot")).json();
  report.source_mode = "recorded";
  const account = report.accounts.find(item => item.status === "active");
  report.accounts.forEach(item => { item.status = item.id === account.id ? "active" : "retired"; });
  await page.route("**/api/v1/settings", route => route.fulfill({
    json: { mode: "recorded", workspace: "investment" },
  }));
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
  await page.goto("/investment");
  await page.getByRole("button", { name: "Start account review" }).click();
  await page.getByRole("button", { name: "I checked this account — continue review" }).click();
  const guided = page.getByRole("region", { name: "Guided portfolio review" });
  await expect(guided).toContainText("Accounts checked.");
  await page.evaluate(() => {
    const original = Storage.prototype.setItem;
    let fail = true;
    Storage.prototype.setItem = function (key, value) {
      if (fail && key.startsWith("goldscout.review-checkpoint.v1.")) {
        fail = false;
        throw new Error("Synthetic storage failure");
      }
      return original.call(this, key, value);
    };
  });
  const finish = guided.getByRole("button", { name: "Finish review and save this value for next time" });
  await finish.click();
  await expect(page.getByRole("alert").filter({ hasText: "could not save this review checkpoint" })).toBeVisible();
  await page.getByRole("button", { name: "Retry comparison point" }).click();
  await finish.click();
  await expect(guided).toContainText("Review complete.");
});

test("a successful finish clears a transient review error", async ({ page }) => {
  const report = await (await page.request.get("/api/investment/v1/snapshot")).json();
  report.source_mode = "recorded";
  const account = report.accounts.find(item => item.status === "active");
  report.accounts.forEach(item => { item.status = item.id === account.id ? "active" : "retired"; });
  let failNextRead = false;
  await page.route("**/api/v1/settings", route => route.fulfill({
    json: { mode: "recorded", workspace: "investment" },
  }));
  await page.route("**/api/investment/v1/snapshot", route => {
    if (failNextRead) {
      failNextRead = false;
      return route.fulfill({ status: 503, json: { error: "synthetic_snapshot_failure" } });
    }
    return route.fulfill({ json: report });
  });
  await page.goto("/investment");
  await page.getByRole("button", { name: "Start account review" }).click();
  await page.getByRole("button", { name: "I checked this account — continue review" }).click();
  const guided = page.getByRole("region", { name: "Guided portfolio review" });
  await expect(guided).toContainText("Accounts checked.");
  const finish = guided.getByRole("button", { name: "Finish review and save this value for next time" });
  failNextRead = true;
  await finish.click();
  await expect(page.getByRole("alert").filter({ hasText: "A comparison value may already have been saved" })).toBeVisible();
  await finish.click();
  await expect(guided).toContainText("Review complete.");
  await expect(page.getByRole("alert").filter({ hasText: "A comparison value may already have been saved" })).toHaveCount(0);
});

test("a savings account shows its recorded terms before a quiet review", async ({ page }) => {
  const report = await (await page.request.get("/api/investment/v1/snapshot")).json();
  report.source_mode = "recorded";
  const account = report.accounts.find(item => item.status === "active");
  account.kind = "deposit";
  account.terms = {
    annual_rate_pct: "4.25", rate_basis: "AER", effective_from: "2029-01-01",
    observed_at: "2029-01-02T00:00:00Z", available_at: "2029-01-02T00:00:00Z",
    source: "Synthetic statement", maturity_date: "2030-01-01", notice_days: 30,
  };
  await page.route("**/api/v1/settings", route => route.fulfill({
    json: { mode: "recorded", workspace: "investment" },
  }));
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
  await page.goto(`/investment/update?account_id=${encodeURIComponent(account.id)}`);
  const terms = page.locator("section").filter({ has: page.getByRole("heading", { name: "Review savings terms" }) }).last();
  await expect(terms).toContainText("4.25% AER");
  await expect(terms).toContainText("Synthetic statement");
  await expect(terms).toContainText("30 days");
});

test("the portfolio shows saved goals without treating duplicate links as money twice", async ({ page }) => {
  const report = await (await page.request.get("/api/investment/v1/snapshot")).json();
  report.source_mode = "recorded";
  const account = report.accounts.find(item => item.status === "active");
  const goal = {
    target_amount_gbp: "1000.00", target_date: "2035-01-01", notes: "",
    purpose: "other", access_date: null, liquidity: "unknown", loss_tolerance: "not_set",
    current_amount_gbp: null, monthly_contribution_gbp: null,
    linked_portfolio_id: report.portfolio_id, linked_account_ids: [account.id],
  };
  await page.route("**/api/v1/settings", route => route.fulfill({
    json: { mode: "recorded", workspace: "investment" },
  }));
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
  await page.route("**/api/investment/v1/planning/session", route => route.fulfill({
    json: { enabled: true, csrf: "synthetic-csrf", revision: 1, profile_hash: "a".repeat(64),
      goals: [{ ...goal, id: "1".repeat(32), name: "First" }, { ...goal, id: "2".repeat(32), name: "Second" }] },
  }));
  await page.goto("/investment");
  const summary = page.getByRole("region", { name: "Your goals" });
  await expect(summary).toContainText("First");
  await expect(summary).toContainText("Second");
  await expect(summary.getByText("Linked amount unavailable", { exact: false })).toHaveCount(2);
});

test("a failed account check explains that its reminder may already have changed", async ({ page }) => {
  const report = await (await page.request.get("/api/investment/v1/snapshot")).json();
  report.source_mode = "recorded";
  const account = report.accounts.find(item => item.status === "active");
  await page.route("**/api/v1/settings", route => route.fulfill({
    json: { mode: "recorded", workspace: "investment" },
  }));
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
  await page.goto("/investment");
  const guided = page.getByRole("region", { name: "Guided portfolio review" });
  await guided.getByRole("button", { name: "Start account review" }).click();
  await page.getByLabel(`Expected review interval for ${account.name}`).selectOption("7");
  await page.evaluate(() => {
    const original = Storage.prototype.setItem;
    Storage.prototype.setItem = function (key, value) {
      if (key.startsWith("goldscout.guided-review.v1.")) throw new Error("Synthetic storage failure");
      return original.call(this, key, value);
    };
  });
  await page.getByRole("button", { name: "I checked this account — continue review" }).click();
  await expect(page.getByRole("alert")).toContainText("A reminder date may have been saved");
  await expect(page.getByRole("alert")).toContainText("No journal record was changed");
});
