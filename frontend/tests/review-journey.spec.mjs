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
  await page.locator(".account-maintenance-card").filter({ hasText: first.name }).first()
    .getByRole("button", { name: "Mark reviewed in this session" }).click();
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
  report.revision++;
  await page.locator(".account-maintenance-card").first()
    .getByRole("button", { name: "Mark reviewed in this session" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "The journal changed" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Guided portfolio review" })).toContainText("0 of");
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
  await page.getByLabel(`Expected review interval for ${account.name}`).selectOption("7");
  const guided = page.getByRole("region", { name: "Guided portfolio review" });
  await guided.getByRole("button", { name: "Start account review" }).click();
  await guided.getByRole("link", { name: "Continue review" }).click();
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
