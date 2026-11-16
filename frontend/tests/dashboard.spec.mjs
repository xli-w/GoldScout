import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";

const fixtures = JSON.parse(readFileSync(new URL("../../dashboard/fixtures.json", import.meta.url), "utf8"));

test("investment demo separates contributions, GBP valuation and export-only proposals", async ({ page }) => {
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto("/investment");
  await expect(page.getByRole("heading", { name: "Long-term investment", level: 1 })).toBeVisible();
  await expect(page.getByText("SYNTHETIC INVESTMENT DEMO")).toBeVisible();
  await expect(page.getByText("Deposits are not profit")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Allocation and drift" })).toBeVisible();
  await expect(page.getByText(/External savings are not broker buying power/)).toBeVisible();
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
  await expect(page.getByText(/Proposed sale proceeds never fund purchases/)).toBeVisible();
  await expect(page.getByText(/Review acknowledgements are recorded through the offline CLI/)).toBeVisible();
  await expect(page.getByText(/five-point drift remains a trigger/)).toBeVisible();
});

test("investment view displays a non-executable sell proposal", async ({ page }) => {
  const response = await page.request.get("/api/investment/v1/snapshot");
  expect(response.ok()).toBeTruthy();
  const report = await response.json();
  report.proposal.status = "review";
  report.proposal.reason = "Proposed sale proceeds do not fund purchases.";
  report.proposal.orders = [{ account_id: "broker", product_id: "equity", side: "sell",
    quantity: "20", estimated_notional_gbp: "200" }];
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
  await page.goto("/investment");
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

test("trading guide works from the footer and without account data", async ({ page }) => {
  await page.route("**/api/v1/**", route => route.abort());
  await page.goto("/");
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
  await page.goto("/");
  await expect(page).toHaveTitle("GoldScout | Portfolio Monitor");
  await expect(page.getByRole("link", { name: "GoldScout Portfolio monitor" })).toBeVisible();
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

  await page.goto("/");
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

test("small screens retain runtime and expose scrollable navigation and tables", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "mobile", "Small-screen layout");
  await page.setViewportSize({ width: 320, height: 800 });
  await page.goto("/");
  await expect(page.locator(".connection")).toContainText("Runtime");
  await expect(page.locator(".connection")).toBeVisible();
  await expect(page.getByText("Scroll for more sections")).toBeVisible();
  const nav = page.locator(".nav-groups");
  expect(await nav.evaluate(element => element.scrollWidth > element.clientWidth)).toBeTruthy();
  await page.getByRole("link", { name: "Intelligence", exact: true }).focus();
  expect(await nav.evaluate(element => element.scrollLeft > 0)).toBeTruthy();
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

  await page.goto("/");
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

  await page.goto("/");
  await expect(page.locator(".gate-band")).toContainText("Blocked in snapshot");
  await expect(page.locator(".gate-band").getByRole("link", { name: "Review affected positions" })).toHaveAttribute("href", "/positions");
});

test("unavailable storage does not render an empty account", async ({ page }) => {
  await page.route("**/api/v1/overview", route => route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({
    api_version: "1", resource: "error", meta: fixtures.meta,
    data: { code: "ledger_missing", message: "Recorded account data is unavailable.", retryable: true },
  }) }));
  await page.goto("/");
  await expect(page.getByRole("alert")).toContainText("No account snapshot");
  await expect(page.getByText("No recorded holdings")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();
});

test("refresh failure hides previously loaded totals", async ({ page }) => {
  await page.goto("/");
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
  await page.goto("/");
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

  await page.goto("/");
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
  await page.goto("/");
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