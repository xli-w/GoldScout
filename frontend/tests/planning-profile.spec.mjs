import { expect, test } from "@playwright/test";

async function recordedPortfolio(page) {
  const report = await (await page.request.get("/api/investment/v1/snapshot")).json();
  report.source_mode = "recorded";
  return report;
}

test("local goals require review and confirmation and remain separate from portfolio records", async ({ page }) => {
  const hash = "a".repeat(64);
  let revision = 0;
  let goals = [];
  let writes = 0;
  await page.route("**/api/v1/settings", route => route.fulfill({
    json: { mode: "recorded", workspace: "investment" },
  }));
  await page.route("**/api/investment/v1/planning/session", route => route.fulfill({
    json: {
      enabled: true, csrf: "synthetic-csrf", revision,
      profile_hash: hash, goals,
    },
  }));
  await page.route("**/api/investment/v1/planning/preview", async route => {
    writes++;
    const body = route.request().postDataJSON();
    expect(body.expected_revision).toBe(revision);
    expect(body.expected_hash).toBe(hash);
    expect(body.expected_portfolio_id).toBeNull();
    expect(body.expected_journal_revision).toBeNull();
    goals = body.goals.map((goal, index) => ({ id: `${index}`.padStart(32, "0"), ...goal }));
    await route.fulfill({ json: {
      preview_id: "synthetic-preview", revision: revision + 1,
      expires_at: "2030-01-01T00:05:00Z", goals,
    } });
  });
  await page.route("**/api/investment/v1/planning/confirm", async route => {
    writes++;
    expect(route.request().postDataJSON()).toEqual({ preview_id: "synthetic-preview" });
    revision++;
    await route.fulfill({ json: { status: "saved", revision } });
  });

  await page.clock.setFixedTime(new Date("2030-01-01T00:00:00Z"));
  await page.goto("/planning");
  await expect(page.getByRole("heading", { name: "Goals" })).toBeVisible();
  await expect(page.getByText(/not sent to hosted AI/)).toBeVisible();
  await page.getByRole("button", { name: "Add a goal" }).click();
  await page.getByLabel("Goal name").fill("First home");
  const amount = page.getByLabel("Target amount (GBP, optional)");
  await amount.fill("0.00");
  expect(await amount.evaluate(input => input.checkValidity())).toBe(false);
  await page.getByRole("button", { name: "Preview changes" }).click();
  expect(writes).toBe(0);
  await amount.fill("25000.00");
  await page.getByLabel("Target date (optional)").fill("2035-05-01");
  await page.getByLabel("Purpose").selectOption("first_home");
  await page.getByLabel("Earliest access date (optional)").fill("2032-01-01");
  await page.getByLabel("How soon might you need access?").selectOption("within_5_years");
  await page.getByLabel("Comfort with possible losses").selectOption("small_fluctuations");
  await page.getByLabel("Manual amount set aside (GBP, used only without linked accounts)").fill("5000.00");
  await page.getByLabel("Expected monthly contribution (GBP, optional)").fill("250.00");
  await page.getByLabel("Notes (optional)").fill("Synthetic goal note");
  await expect(page.getByText("Simple contribution illustration")).toBeVisible();
  await expect(page.getByText(/assumes no investment growth/)).toBeVisible();
  await page.getByRole("button", { name: "Preview changes" }).click();
  await expect(page.getByRole("region", { name: "Review goals before saving" }))
    .toContainText("This will replace the separate planning profile");
  await expect(page.getByRole("region", { name: "Review goals before saving" }))
    .toContainText("First home");
  await expect(page.getByRole("region", { name: "Review goals before saving" }))
    .toContainText("first home");
  await expect(page.getByRole("region", { name: "Review goals before saving" }))
    .toContainText("£250.00/month expected");
  await expect(page.getByRole("button", { name: "Confirm and save locally" })).toBeVisible();
  expect(writes).toBe(1);

  await page.getByRole("button", { name: "Confirm and save locally" }).click();
  await expect(page.getByRole("status")).toContainText("Saved as local planning revision 1");
  expect(writes).toBe(2);
  await expect(page.getByLabel("Goal name")).toHaveValue("First home");
  await expect(page.getByLabel("Target amount (GBP, optional)")).toHaveValue("25000.00");
  await expect(page.getByLabel("Purpose")).toHaveValue("first_home");
  await expect(page.getByLabel("Manual amount set aside (GBP, used only without linked accounts)")).toHaveValue("5000.00");
});

test("linked goals use a whole recorded account once and bind preview to its journal revision", async ({ page }) => {
  const report = await recordedPortfolio(page);
  const account = report.accounts.find(item => item.status === "active");
  const hash = "b".repeat(64);
  let revision = 0;
  let savedGoals = [];
  let previewBody;
  await page.route("**/api/v1/settings", route => route.fulfill({
    json: { mode: "recorded", workspace: "investment" },
  }));
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
  await page.route("**/api/investment/v1/planning/session", route => route.fulfill({
    json: { enabled: true, csrf: "synthetic-csrf", revision,
      profile_hash: hash, goals: savedGoals },
  }));
  await page.route("**/api/investment/v1/planning/preview", async route => {
    previewBody = route.request().postDataJSON();
    savedGoals = previewBody.goals.map((goal, index) => ({
      id: `${index + 1}`.toString(16).padStart(32, "0"), ...goal,
    }));
    await route.fulfill({ json: {
      preview_id: "linked-goal-preview", revision: revision + 1,
      expires_at: "2030-01-01T00:05:00Z", goals: savedGoals,
    } });
  });
  await page.route("**/api/investment/v1/planning/confirm", async route => {
    revision++;
    await route.fulfill({ json: { status: "saved", revision } });
  });

  await page.clock.setFixedTime(new Date("2030-01-01T00:00:00Z"));
  await page.goto("/planning");
  await page.getByRole("button", { name: "Add a goal" }).click();
  await page.getByLabel("Goal name").fill("Emergency reserve");
  await page.getByLabel("Target amount (GBP, optional)").fill("1000");
  await page.getByLabel("Target date (optional)").fill("2035-01-01");
  const manualAmount = page.getByLabel("Manual amount set aside (GBP, used only without linked accounts)");
  await manualAmount.fill("50");
  const accountCheck = page.getByRole("checkbox", {
    name: `${account.name} (${account.id})`,
  });
  await accountCheck.check();
  await expect(manualAmount).toHaveValue("");
  await expect(manualAmount).toBeDisabled();
  await expect(page.getByText(/Linked recorded amount:/)).toBeVisible();
  await expect(page.getByText(/Simple contribution illustration/)).toBeVisible();

  await page.getByRole("button", { name: "Add a goal" }).click();
  await page.getByLabel("Goal name").nth(1).fill("Second goal");
  await expect(page.getByRole("checkbox", {
    name: `${account.name} (${account.id})`,
  }).nth(1)).toBeDisabled();

  await page.getByRole("button", { name: "Preview changes" }).click();
  expect(previewBody.expected_portfolio_id).toBe(report.portfolio_id);
  expect(previewBody.expected_journal_revision).toBe(report.revision);
  expect(previewBody.goals[0]).toMatchObject({
    current_amount_gbp: null,
    linked_portfolio_id: report.portfolio_id,
    linked_account_ids: [account.id],
  });
  expect(previewBody.goals[1].linked_account_ids).toEqual([]);
  await expect(page.getByRole("region", { name: "Review goals before saving" }))
    .toContainText(`${account.name}`);
  await expect(page.getByRole("region", { name: "Review goals before saving" }))
    .toContainText("recorded amount:");
});

test("incomplete linked account values do not create goal progress", async ({ page }) => {
  const report = await recordedPortfolio(page);
  report.complete = false;
  report.equity_gbp = null;
  report.as_of = null;
  report.since_baseline_gain_gbp = null;
  report.twr_pct = null;
  report.max_drawdown_pct = null;
  report.holdings.forEach(item => {
    item.mark_at = null;
    item.mark_available_at = null;
    item.value_gbp = null;
  });
  const account = report.accounts.find(item => item.status === "active");
  await page.route("**/api/v1/settings", route => route.fulfill({
    json: { mode: "recorded", workspace: "investment" },
  }));
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
  await page.route("**/api/investment/v1/planning/session", route => route.fulfill({
    json: { enabled: true, csrf: "synthetic-csrf", revision: 0,
      profile_hash: "c".repeat(64), goals: [] },
  }));

  await page.goto("/planning");
  await page.getByRole("button", { name: "Add a goal" }).click();
  await page.getByLabel("Goal name").fill("Emergency reserve");
  await page.getByLabel("Target amount (GBP, optional)").fill("1000");
  await page.getByLabel("Target date (optional)").fill("2035-01-01");
  await page.getByRole("checkbox", { name: `${account.name} (${account.id})` }).check();
  await expect(page.getByText(/Linked account amount unavailable/)).toBeVisible();
  await expect(page.getByText(/Goal progress is unavailable/)).toBeVisible();
  await expect(page.getByText("Simple contribution illustration")).toHaveCount(0);
});
