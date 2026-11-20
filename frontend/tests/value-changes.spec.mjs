import { expect, test } from "@playwright/test";

const first = "2026-01-01T12:00:00Z";
const second = "2026-01-02T12:00:00.000001Z";
const between = "2026-01-03T12:00:00Z";
const last = "2026-01-04T12:00:00Z";

function point(at, value, basis, net) {
  return {
    timestamp: at, source_available_at: at, as_of: at, complete: true,
    equity_gbp: value, capital_basis_gbp: basis, net_contributions_gbp: net,
    gain_since_baseline_gbp: "0", twr_pct: null, max_drawdown_pct: null, gap_reason: null,
  };
}

async function comparisonReport(page) {
  const report = await (await page.request.get("/api/investment/v1/snapshot")).json();
  const account = report.accounts[0];
  const destination = report.accounts[1];
  const holding = report.holdings[0];
  report.as_of = last;
  report.complete = true;
  report.equity_gbp = "279.012";
  report.net_contributions_gbp = "130.006";
  report.baseline_as_of = first;
  report.recorded_corrections = [];
  report.valuation_history = [
    point(first, "100", "100", "0"),
    point(second, "200.005", "200.005", "100.005"),
    { timestamp: between, source_available_at: between, as_of: null, complete: false,
      equity_gbp: null, capital_basis_gbp: null, net_contributions_gbp: "130.006",
      gain_since_baseline_gbp: null, twr_pct: null, max_drawdown_pct: null,
      gap_reason: "Incomplete synthetic valuation." },
    point(last, "279.012", "240.010", "130.006"),
  ];
  const flow = (id, type, amount, at, destinationId = null) => ({
    id, type, account_id: account.id, destination_account_id: destinationId,
    amount_gbp: amount, timestamp: at, available_at: at, evidence: `Evidence ${id}`, role: "unassigned",
  });
  report.recorded_flow_events = [
    flow("before-boundary", "contribution", "100.005", second),
    flow("deposit", "contribution", "50.001", "2026-01-02T12:00:00.000002Z"),
    flow("withdrawal", "withdrawal", "20", between),
    flow("transfer", "transfer", "7", between, destination.id),
  ];
  report.recorded_income_events = [{
    id: "interest", type: "interest", account_id: account.id, product_id: null,
    amount_gbp: "2.003", timestamp: between, available_at: between,
    evidence: "Evidence interest", role: "unassigned", role_evidence: null,
  }];
  report.recorded_fee_events = [{
    id: "buy", type: "buy", account_id: holding.account_id, product_id: holding.product_id,
    quantity: "1", price_gbp: "1", fee_gbp: "0.002", timestamp: last, available_at: last,
    evidence: "Evidence purchase", role: "unassigned",
  }];
  report.recorded_scope_additions = [{
    id: "scope", account_id: account.id, amount_gbp: "10.004", timestamp: between,
    available_at: between, evidence: "Evidence scope",
  }];
  return report;
}

test("explains the real synthetic replay without a mocked snapshot", async ({ page }) => {
  await page.goto("/investment");
  await expect(page.getByText("SYNTHETIC DEMO", { exact: true })).toBeVisible();
  const changes = page.getByRole("region", { name: "Changes between recorded values" });
  await expect(changes).toContainText("changed by £370.00");
  await expect(changes).toContainText("Money added from outside£250.00");
  await expect(changes).toContainText("Recorded interest and dividends£20.00");
  await expect(changes).toContainText("Unexplained remainder£100.00");
  await expect(changes).toContainText("2 incomplete valuation points");
  await changes.getByText("Supporting recorded activity (2)").click();
  await expect(changes).toContainText("Synthetic contribution receipt");
  await expect(changes).toContainText("Synthetic interest statement");
});

test("demo refresh keeps the selected relative comparison despite moving synthetic dates", async ({ page }) => {
  await page.goto("/investment");
  const changes = page.getByRole("region", { name: "Changes between recorded values" });
  const select = changes.getByLabel("Earlier recorded valuation");
  const firstOption = await select.locator("option").first().getAttribute("value");
  await select.selectOption(firstOption);
  await page.getByRole("button", { name: "Refresh investment" }).click();
  await expect.poll(() => select.inputValue()).not.toBe(firstOption);
  await expect(select).toHaveValue(await select.locator("option").first().getAttribute("value"));
  await expect(changes).toContainText("Demo dates move on refresh");
  await expect(changes).toContainText("changed by £550.00");
});

test("recorded comparison keeps the selected date after a new revision", async ({ page }) => {
  const report = await comparisonReport(page);
  report.source_mode = "recorded";
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
  await page.goto("/investment");
  const changes = page.getByRole("region", { name: "Changes between recorded values" });
  const select = changes.getByLabel("Earlier recorded valuation");
  await select.selectOption(first);
  await expect(changes).toContainText("changed by £179.012");
  report.revision++;
  await Promise.all([
    page.waitForResponse(response => response.url().endsWith("/api/investment/v1/snapshot")),
    page.getByRole("button", { name: "Refresh investment" }).click(),
  ]);
  await expect(page.getByRole("button", { name: "Refresh investment" })).toBeEnabled();
  await expect(select).toHaveValue(first);
  await expect(changes).toContainText("changed by £179.012");
  report.revision++;
  report.valuation_history.shift();
  await Promise.all([
    page.waitForResponse(response => response.url().endsWith("/api/investment/v1/snapshot")),
    page.getByRole("button", { name: "Refresh investment" }).click(),
  ]);
  await expect(changes.getByRole("status")).toContainText("selected earlier valuation is no longer available");
  await expect(changes).not.toContainText("Unexplained remainder");
  await select.selectOption(second);
  await expect(changes).toContainText("changed by £79.007");
});

test("saved review checkpoint anchors the next evidence-linked change comparison", async ({ page }) => {
  const report = await comparisonReport(page);
  report.source_mode = "recorded";
  await page.clock.setFixedTime(new Date("2026-01-05T12:00:00Z"));
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
  await page.goto("/investment");

  const review = page.getByRole("region", { name: "Review continuity" });
  await expect(review).toContainText("No comparison point is saved in this browser yet");
  await review.getByRole("button", { name: "Save this value for the next comparison" }).click();
  await expect(review).toContainText("journal revision");
  await expect(page.getByRole("region", { name: "What changed since your saved value?" }))
    .toContainText("No newer complete recorded value is available yet");
  await expect(page.getByLabel("Earlier recorded valuation")).toHaveValue("");
  await page.getByLabel("Earlier recorded valuation").selectOption(first);
  await expect(page.getByRole("region", { name: "Changes between recorded values" }))
    .toContainText("Selecting an earlier value does not save it");
  await review.getByRole("button", { name: "Save this value for the next comparison" }).click();

  const next = "2026-01-05T12:00:00.000001Z";
  report.revision++;
  report.as_of = next;
  report.equity_gbp = "280";
  report.valuation_history.push(point(next, "280", "240.010", "130.006"));
  await Promise.all([
    page.waitForResponse(response => response.url().endsWith("/api/investment/v1/snapshot")),
    page.getByRole("button", { name: "Refresh investment" }).click(),
  ]);
  const changes = page.getByRole("region", { name: "What changed since your saved value?" });
  await expect(changes).toContainText("From 2026-01-04T12:00:00Z");
  await expect(changes).toContainText("Unexplained remainder£0.988");
});

test("saved comparison points update in other tabs without changing the journal", async ({ page, context }) => {
  const report = await comparisonReport(page);
  report.source_mode = "recorded";
  const later = structuredClone(report);
  later.revision++;
  later.as_of = "2026-01-05T12:00:00Z";
  later.equity_gbp = "280";
  later.valuation_history.push(point(later.as_of, "280", "240.010", "130.006"));
  let writes = 0;
  context.on("request", request => { if (!["GET", "HEAD"].includes(request.method())) writes++; });
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
  await page.goto("/investment");
  const secondTab = await context.newPage();
  await secondTab.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: later }));
  await secondTab.goto("/investment");

  await page.getByRole("button", { name: "Save this value for the next comparison" }).click();
  const otherReview = secondTab.getByRole("region", { name: "Review continuity" });
  await expect(otherReview).toContainText(`journal revision ${report.revision}`);
  await otherReview.getByRole("button", { name: "Save this value for the next comparison" }).click();
  await expect(page.getByRole("region", { name: "Review continuity" }))
    .toContainText(`journal revision ${later.revision}`);
  const key = `goldscout.review-checkpoint.v1.${report.portfolio_id}`;
  await secondTab.evaluate(key => localStorage.setItem(key, "invalid checkpoint"), key);
  const review = page.getByRole("region", { name: "Review continuity" });
  await expect(review.getByRole("alert")).toContainText("could not be read");
  await expect(review).not.toContainText("No comparison point is saved");
  await expect(page.getByRole("region", { name: "Changes between recorded values" })).toHaveCount(0);
  expect(await secondTab.evaluate(key => localStorage.getItem(key), key)).toBe("invalid checkpoint");
  await secondTab.evaluate(key => localStorage.removeItem(key), key);
  await expect(review).toContainText("No comparison point is saved");
  await expect(page.getByRole("region", { name: "Changes between recorded values" })).toBeVisible();
  expect(writes).toBe(0);
  await secondTab.close();
});

test("a failed refresh does not silently change the selected recorded date", async ({ page }) => {
  const report = await comparisonReport(page);
  report.source_mode = "recorded";
  let unavailable = false;
  await page.route("**/api/investment/v1/snapshot", route => unavailable
    ? route.fulfill({ status: 503, json: { detail: "Synthetic read failure" } })
    : route.fulfill({ json: report }));
  await page.goto("/investment");
  let changes = page.getByRole("region", { name: "Changes between recorded values" });
  await changes.getByLabel("Earlier recorded valuation").selectOption(first);
  unavailable = true;
  await page.getByRole("button", { name: "Refresh investment" }).click();
  await expect(page.getByRole("heading", { name: "Investment data unavailable" })).toBeVisible();
  unavailable = false;
  await page.getByRole("button", { name: "Refresh investment" }).click();
  changes = page.getByRole("region", { name: "Changes between recorded values" });
  await expect(changes.getByLabel("Earlier recorded valuation")).toHaveValue(first);
  await expect(changes).toContainText("changed by £179.012");
});

test("a workspace refresh failure does not silently change the selected date", async ({ page }) => {
  const report = await comparisonReport(page);
  report.source_mode = "recorded";
  const settings = await (await page.request.get("/api/v1/settings")).json();
  settings.workspace = "investment";
  let unavailable = false;
  await page.route("**/api/v1/settings", route => unavailable
    ? route.fulfill({ status: 503, json: { detail: "Synthetic workspace failure" } })
    : route.fulfill({ json: settings }));
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
  await page.goto("/investment");
  await page.getByLabel("Earlier recorded valuation").selectOption(first);
  unavailable = true;
  await Promise.all([
    page.waitForResponse(response => response.url().endsWith("/api/v1/settings") && response.status() === 503),
    page.evaluate(() => window.dispatchEvent(new Event("visibilitychange"))),
  ]);
  await expect(page.getByRole("alert")).toContainText("Workspace settings are unavailable");
  unavailable = false;
  await page.getByRole("button", { name: "Retry", exact: true }).click();
  const changes = page.getByRole("region", { name: "Changes between recorded values" });
  await expect(changes.getByLabel("Earlier recorded valuation")).toHaveValue(first);
  await expect(changes).toContainText("changed by £179.012");
});

test("a selected older date remains available when newer history exceeds the display limit", async ({ page }) => {
  const report = await comparisonReport(page);
  report.source_mode = "recorded";
  const dates = Array.from({ length: 102 }, (_, index) =>
    new Date(Date.UTC(2026, 0, 1, index)).toISOString());
  report.valuation_history = dates.slice(0, 101).map(at => point(at, "100", "100", "0"));
  report.as_of = dates[100];
  report.equity_gbp = "100";
  report.net_contributions_gbp = "0";
  report.recorded_flow_events = [];
  report.recorded_income_events = [];
  report.recorded_fee_events = [];
  report.recorded_scope_additions = [];
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
  await page.goto("/investment");
  const changes = page.getByRole("region", { name: "Changes between recorded values" });
  const select = changes.getByLabel("Earlier recorded valuation");
  await select.selectOption(dates[0]);
  report.revision++;
  report.valuation_history.push(point(dates[101], "100", "100", "0"));
  report.as_of = dates[101];
  await page.getByRole("button", { name: "Refresh investment" }).click();
  await expect(changes).toContainText(dates[101]);
  await expect(select).toHaveValue(dates[0]);
  await expect(select.locator("option")).toHaveCount(101);
  await expect(changes).toContainText("your selected date if older");
  await expect(changes).toContainText("Unexplained remainder£0.00");
});

test("compares chosen recorded values with exact sourced activity and an unexplained remainder", async ({ page }) => {
  const report = await comparisonReport(page);
  let writes = 0;
  page.on("request", request => { if (!["GET", "HEAD"].includes(request.method())) writes++; });
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
  await page.goto("/investment");
  const changes = page.getByRole("region", { name: "Changes between recorded values" });
  await expect(changes).toContainText("Selecting an earlier value does not save it");
  await expect(changes).toContainText("changed by £79.007");
  await expect(changes).toContainText("Money added from outside£50.001");
  await expect(changes).toContainText("Money withdrawn to outside£20.00");
  await expect(changes).toContainText("Value added by including accounts£10.004");
  await expect(changes).toContainText("Recorded interest and dividends£2.003");
  await expect(changes).toContainText("Recorded purchase and sale fees£0.002");
  await expect(changes).toContainText("Unexplained remainder£37.001");
  await expect(changes).toContainText("1 incomplete valuation point");
  await expect(changes).toContainText("not a verified investment return");
  await changes.getByText("Supporting recorded activity (6)").click();
  await expect(changes).toContainText("Evidence deposit");
  await expect(changes).toContainText("2026-01-02T12:00:00.000002Z");
  await expect(changes).toContainText("Evidence transfer");
  await expect(changes).not.toContainText("Evidence before-boundary");
  await changes.getByLabel("Earlier recorded valuation").selectOption(first);
  await expect(changes).toContainText("changed by £179.012");
  await expect(changes).toContainText("Money added from outside£150.006");
  await expect(changes).toContainText("Unexplained remainder£37.001");
  await expect(changes).toContainText("Evidence before-boundary");
  expect(writes).toBe(0);
});

test("withdrawal-only movement can leave a zero remainder without inventing a loss", async ({ page }) => {
  const report = await comparisonReport(page);
  report.equity_gbp = "150.004";
  report.net_contributions_gbp = "50.004";
  report.valuation_history[3] = point(last, "150.004", "150.004", "50.004");
  report.recorded_flow_events = [report.recorded_flow_events[0], {
    ...report.recorded_flow_events[2], amount_gbp: "50.001",
  }];
  report.recorded_income_events = [];
  report.recorded_fee_events = [];
  report.recorded_scope_additions = [];
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
  await page.goto("/investment");
  const changes = page.getByRole("region", { name: "Changes between recorded values" });
  await expect(changes).toContainText("changed by -£50.001");
  await expect(changes).toContainText("Money withdrawn to outside£50.001");
  await expect(changes).toContainText("Unexplained remainder£0.00");
});

for (const reason of ["incomplete latest", "correction", "late activity", "inconsistent flows", "inconsistent scope", "out of order"]) {
  test(`does not present a comparison with ${reason}`, async ({ page }) => {
    const report = await comparisonReport(page);
    if (reason === "incomplete latest") {
      report.complete = false;
      report.as_of = null;
      report.equity_gbp = null;
      report.since_baseline_gain_gbp = null;
      report.twr_pct = null;
      report.max_drawdown_pct = null;
    } else if (reason === "correction") {
      report.recorded_corrections = [{
        id: "correction", original_event_id: "original", replacement_event_id: "replacement",
        replaced_type: "contribution", account_id: report.accounts[0].id, role: "unassigned",
        role_evidence: null, original_amount_gbp: "1", replacement_amount_gbp: "2",
        timestamp: last, available_at: last, evidence: "Correction evidence",
      }];
    } else if (reason === "late activity") {
      report.recorded_income_events[0].available_at = "2026-01-04T12:00:00.000001Z";
    } else if (reason === "inconsistent flows") {
      report.recorded_flow_events[1].amount_gbp = "60";
    } else if (reason === "inconsistent scope") {
      report.recorded_scope_additions[0].amount_gbp = "20";
    } else {
      [report.valuation_history[0], report.valuation_history[1]] =
        [report.valuation_history[1], report.valuation_history[0]];
    }
    await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
    await page.goto("/investment");
    const changes = page.getByRole("region", { name: "Changes between recorded values" });
    await expect(changes.getByRole("status")).toBeVisible();
    await expect(changes).not.toContainText("Unexplained remainder");
    if (reason === "correction") await expect(changes).toContainText("historical correction");
    if (reason === "late activity" || reason === "inconsistent flows" || reason === "inconsistent scope")
      await expect(changes).toContainText("do not agree");
  });
}

test("one complete valuation does not invent a previous value", async ({ page }) => {
  const report = await comparisonReport(page);
  report.valuation_history = [report.valuation_history.at(-1)];
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
  await page.goto("/investment");
  const changes = page.getByRole("region", { name: "Changes between recorded values" });
  await expect(changes).toContainText("At least two complete recorded valuations are needed");
  await expect(changes).not.toContainText("Unexplained remainder");
});
