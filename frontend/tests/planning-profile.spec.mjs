import { expect, test } from "@playwright/test";

async function recordedPortfolio(page) {
  const report = await (await page.request.get("/api/investment/v1/snapshot")).json();
  report.source_mode = "recorded";
  return report;
}

test("local goals save in one action and remain separate from portfolio records", async ({ page }) => {
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
    expect(body.goals.every(goal => !("id" in goal))).toBe(true);
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
  await page.getByRole("button", { name: "Save goals" }).click();
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
  await page.getByRole("button", { name: "Save goals" }).click();
  await expect(page.getByRole("status")).toContainText("Saved as local planning revision 1");
  expect(writes).toBe(2);
  await expect(page.getByRole("heading", { name: "First home" })).toBeVisible();
  await expect(page.getByText("first home · Target £25000.00 · Target date 2035-05-01")).toBeVisible();
  await expect(page.getByText("User-entered amount set aside: £5000.00.")).toBeVisible();
  await expect(page.getByText(/remaining target gap £20000.00/)).toBeVisible();
  await expect(page.getByText(/remaining target gap £4000.00/)).toBeVisible();
  await expect(page.getByText(/assumes no investment growth/)).toBeVisible();
  await expect(page.getByLabel("Goal name")).toHaveCount(0);
  await page.getByRole("button", { name: "Edit First home" }).click();
  await expect(page.getByLabel("Goal name")).toHaveValue("First home");
  await expect(page.getByLabel("Target amount (GBP, optional)")).toHaveValue("25000.00");
  await expect(page.getByLabel("Purpose")).toHaveValue("first_home");
  await expect(page.getByLabel("Manual amount set aside (GBP, used only without linked accounts)")).toHaveValue("5000.00");
  await page.getByLabel("Goal name").fill("Updated home goal");
  await page.getByLabel("Earliest access date (optional)").fill("");
  await page.getByLabel("Manual amount set aside (GBP, used only without linked accounts)").fill("");
  await page.getByLabel("Expected monthly contribution (GBP, optional)").fill("");
  await page.getByRole("button", { name: "Save goals" }).click();
  await expect(page.getByRole("status")).toContainText("Saved as local planning revision 2");
  expect(goals[0].access_date).toBeNull();
  expect(goals[0].current_amount_gbp).toBeNull();
  expect(goals[0].monthly_contribution_gbp).toBeNull();
  await expect(page.getByRole("heading", { name: "Updated home goal" })).toBeVisible();
  await expect(page.getByLabel("Goal name")).toHaveCount(0);
});

test("an uncertain goal save retries the same pending request", async ({ page }) => {
  let previewCalls = 0;
  let confirmCalls = 0;
  let revision = 0;
  let goals = [];
  await page.route("**/api/v1/settings", route => route.fulfill({
    json: { mode: "recorded", workspace: "investment" },
  }));
  await page.route("**/api/investment/v1/planning/session", route => route.fulfill({
    json: { enabled: true, csrf: "synthetic-csrf", revision,
      profile_hash: "a".repeat(64), goals },
  }));
  await page.route("**/api/investment/v1/planning/preview", route => {
    previewCalls++;
    goals = route.request().postDataJSON().goals.map((goal, index) => ({
      id: `${index}`.padStart(32, "0"), ...goal,
    }));
    return route.fulfill({ json: { preview_id: "same-request", revision: 1,
      expires_at: "2030-01-01T00:05:00Z", goals } });
  });
  await page.route("**/api/investment/v1/planning/confirm", route => {
    confirmCalls++;
    expect(route.request().postDataJSON()).toEqual({ preview_id: "same-request" });
    if (confirmCalls === 1) return route.abort("failed");
    revision = 1;
    return route.fulfill({ json: { status: "saved", revision } });
  });

  await page.goto("/planning");
  await page.getByRole("button", { name: "Add a goal" }).click();
  await page.getByLabel("Goal name").fill("Reserve");
  await page.getByRole("button", { name: "Save goals" }).click();
  await expect(page.getByRole("alert")).toContainText("The save may have completed");
  await expect(page.getByLabel("Goal name")).toBeDisabled();
  await page.getByRole("button", { name: "Retry save" }).click();
  await expect(page.getByRole("status")).toContainText("Saved as local planning revision 1");
  expect(previewCalls).toBe(1);
  expect(confirmCalls).toBe(2);
});

test("a conflicting goal save keeps the draft until explicitly discarded", async ({ page }) => {
  let sessionCalls = 0;
  let finishReload;
  const reloadWait = new Promise(resolve => { finishReload = resolve; });
  await page.route("**/api/v1/settings", route => route.fulfill({
    json: { mode: "recorded", workspace: "investment" },
  }));
  await page.route("**/api/investment/v1/planning/session", async route => {
    sessionCalls++;
    if (sessionCalls > 1) await reloadWait;
    await route.fulfill({ json: { enabled: true, csrf: "synthetic-csrf", revision: 1,
      profile_hash: "a".repeat(64), goals: [] } });
  });
  await page.route("**/api/investment/v1/planning/preview", route => route.fulfill({
    status: 409, json: { error: "profile_changed" },
  }));

  await page.goto("/planning");
  await page.getByRole("button", { name: "Add a goal" }).click();
  await page.getByLabel("Goal name").fill("Unsaved reserve");
  await page.getByRole("button", { name: "Save goals" }).click();
  await expect(page.getByRole("alert")).toContainText("profile changed");
  await expect(page.getByLabel("Goal name")).toHaveValue("Unsaved reserve");
  await page.getByRole("button", { name: "Discard draft and reload profile" }).click();
  await expect(page.getByLabel("Goal name")).toBeDisabled();
  finishReload();
  await expect(page.getByLabel("Goal name")).toHaveCount(0);
});

test("saved goals start as dated summaries and edit independently without changing other goals", async ({ page }) => {
  const hash = "d".repeat(64);
  let revision = 3;
  let savedGoals = [
    { id: "1".padStart(32, "0"), name: "Home", target_amount_gbp: "999999999999999.99",
      target_date: "2038-06-30", notes: "Private note", purpose: "first_home",
      access_date: null, liquidity: "unknown", loss_tolerance: "not_set",
      current_amount_gbp: "0.01", monthly_contribution_gbp: null,
      linked_portfolio_id: null, linked_account_ids: [] },
    { id: "2".padStart(32, "0"), name: "Later", target_amount_gbp: null,
      target_date: null, notes: "", purpose: "not_set", access_date: null,
      liquidity: "unknown", loss_tolerance: "not_set", current_amount_gbp: null,
      monthly_contribution_gbp: null, linked_portfolio_id: null, linked_account_ids: [] },
  ];
  const originalLater = { ...savedGoals[1] };
  const { id: _laterId, ...originalLaterInput } = originalLater;
  await page.route("**/api/v1/settings", route => route.fulfill({
    json: { mode: "recorded", workspace: "investment" },
  }));
  await page.route("**/api/investment/v1/planning/session", route => route.fulfill({
    json: { enabled: true, csrf: "synthetic-csrf", revision, profile_hash: hash, goals: savedGoals },
  }));
  await page.route("**/api/investment/v1/planning/preview", async route => {
    const body = route.request().postDataJSON();
    expect(body.expected_revision).toBe(3);
    expect(body.expected_hash).toBe(hash);
    expect(body.goals[0]).toMatchObject({
      name: "Home updated", target_amount_gbp: "999999999999999.99",
      target_date: "2038-06-30", current_amount_gbp: "0.01", notes: "Private note",
    });
    expect(body.goals[1]).toEqual(originalLaterInput);
    savedGoals = body.goals.map((goal, index) => ({ id: savedGoals[index].id, ...goal }));
    await route.fulfill({ json: { preview_id: "summary-preview", revision: 4,
      expires_at: "2030-01-01T00:05:00Z", goals: savedGoals } });
  });
  await page.route("**/api/investment/v1/planning/confirm", async route => {
    expect(route.request().postDataJSON()).toEqual({ preview_id: "summary-preview" });
    revision = 4;
    await route.fulfill({ json: { status: "saved", revision } });
  });

  await page.clock.setFixedTime(new Date("2030-01-01T00:00:00Z"));
  await page.goto("/planning");
  await expect(page.getByText("first home · Target £999999999999999.99 · Target date 2038-06-30")).toBeVisible();
  await expect(page.getByText("Purpose not set · Target amount not set · Target date not set")).toBeVisible();
  await expect(page.getByText("User-entered amount set aside: £0.01.")).toBeVisible();
  await expect(page.getByText("User-entered amount not set.")).toBeVisible();
  await expect(page.getByText(/remaining target gap £999999999999999.98/)).toBeVisible();
  await expect(page.getByText("Private note")).toHaveCount(0);
  await expect(page.getByLabel("Goal name")).toHaveCount(0);
  await page.getByRole("button", { name: "Edit Home" }).click();
  await expect(page.getByLabel("Goal name")).toHaveCount(1);
  await expect(page.getByLabel("Target amount (GBP, optional)")).toHaveValue("999999999999999.99");
  await page.getByLabel("Goal name").fill("Home updated");
  await page.getByRole("button", { name: "Save goals" }).click();
  await expect(page.getByRole("status")).toContainText("Saved as local planning revision 4");
  expect(savedGoals[1]).toEqual(originalLater);
  await expect(page.getByRole("heading", { name: "Home updated" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Later" })).toBeVisible();
  await expect(page.getByLabel("Goal name")).toHaveCount(0);
});

test("saved linked summaries use exact recorded amounts and suppress duplicated links everywhere", async ({ page }) => {
  const report = await recordedPortfolio(page);
  const holding = report.holdings[0];
  const account = report.accounts.find(item => item.id === holding.account_id);
  report.accounts.forEach(item => { item.cash_gbp = "0"; });
  report.holdings.forEach(item => { item.value_gbp = "0"; });
  holding.value_gbp = "100000.0000110000000001";
  report.equity_gbp = holding.value_gbp;
  report.as_of = "2029-12-31T12:34:56.123456Z";
  const base = {
    target_amount_gbp: "200000.00", target_date: "2035-01-01", notes: "",
    purpose: "other", access_date: null, liquidity: "unknown", loss_tolerance: "not_set",
    current_amount_gbp: null, monthly_contribution_gbp: "100.00",
    linked_portfolio_id: report.portfolio_id, linked_account_ids: [account.id],
  };
  const goals = [
    { ...base, id: "1".padStart(32, "0"), name: "First" },
    { ...base, id: "2".padStart(32, "0"), name: "Second" },
  ];
  await page.route("**/api/v1/settings", route => route.fulfill({
    json: { mode: "recorded", workspace: "investment" },
  }));
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
  await page.route("**/api/investment/v1/planning/session", route => route.fulfill({
    json: { enabled: true, csrf: "synthetic-csrf", revision: 1,
      profile_hash: "e".repeat(64), goals },
  }));
  await page.route("**/api/investment/v1/planning/preview", route => {
    const body = route.request().postDataJSON();
    return route.fulfill({ json: { preview_id: "duplicate-preview", revision: 2,
      expires_at: "2030-01-01T00:05:00Z",
      goals: body.goals.map((goal, index) => ({ id: goals[index].id, ...goal })) } });
  });
  await page.clock.setFixedTime(new Date("2030-01-01T00:00:00Z"));
  await page.goto("/planning");
  await expect(page.getByText("Linked account amount unavailable.")).toHaveCount(2);
  await expect(page.getByText(/Linked recorded amount:/)).toHaveCount(0);
  await expect(page.getByText("Simple contribution illustration")).toHaveCount(0);
  await page.getByRole("button", { name: "Edit First" }).click();
  await expect(page.getByText(/Linked account amount unavailable/)).toHaveCount(2);
  await expect(page.getByText("Simple contribution illustration")).toHaveCount(0);
  await page.getByRole("checkbox", { name: `${account.name} (${account.id})` }).uncheck();
  await expect(page.getByText(
    "Linked recorded amount: £100,000.0000110000000001 as of 2029-12-31T12:34:56.123456Z.",
  )).toBeVisible();
  await expect(page.getByText(/remaining target gap £100000.00/)).toBeVisible();
  await expect(page.getByText(/Illustration amounts are rounded to the nearest penny/)).toBeVisible();
  await expect(page.getByText(/assumes no investment growth/)).toBeVisible();
  await page.getByRole("button", { name: "Edit Second" }).click();
  await expect(page.getByText(
    "Linked recorded amount: £100,000.0000110000000001 as of 2029-12-31T12:34:56.123456Z.",
  )).toBeVisible();
  await expect(page.getByText(
    "Linked recorded amount: £100,000.0000110000000001 as of 2029-12-31T12:34:56.123456Z.",
  )).toBeVisible();
});

test("saved goals with past targets or incomplete inputs do not show contribution illustrations", async ({ page }) => {
  const base = {
    notes: "", purpose: "other", access_date: null, liquidity: "unknown",
    loss_tolerance: "not_set", current_amount_gbp: "10.00", monthly_contribution_gbp: null,
    linked_portfolio_id: null, linked_account_ids: [],
  };
  const goals = [
    { ...base, id: "3".padStart(32, "0"), name: "Past", target_amount_gbp: "100.00", target_date: "2029-12-31" },
    { ...base, id: "4".padStart(32, "0"), name: "No date", target_amount_gbp: "100.00", target_date: null },
    { ...base, id: "5".padStart(32, "0"), name: "No amount", target_amount_gbp: "100.00",
      target_date: "2035-01-01", current_amount_gbp: null },
  ];
  await page.route("**/api/v1/settings", route => route.fulfill({
    json: { mode: "recorded", workspace: "investment" },
  }));
  await page.route("**/api/investment/v1/planning/session", route => route.fulfill({
    json: { enabled: true, csrf: "synthetic-csrf", revision: 1,
      profile_hash: "f".repeat(64), goals },
  }));
  await page.clock.setFixedTime(new Date("2030-01-01T00:00:00Z"));
  await page.goto("/planning");
  await expect(page.getByRole("heading", { name: "Past" })).toBeVisible();
  await expect(page.getByText("Simple contribution illustration")).toHaveCount(0);
  await page.getByRole("button", { name: "Edit Past" }).click();
  await expect(page.getByText("Simple contribution illustration")).toHaveCount(0);
});

test("linked goal amounts retain exact fractional recorded values", async ({ page }) => {
  const report = await recordedPortfolio(page);
  const holding = report.holdings[0];
  const account = report.accounts.find(item => item.id === holding.account_id);
  report.accounts.forEach(item => { item.cash_gbp = "0"; });
  report.holdings.forEach(item => { item.value_gbp = "0"; });
  holding.value_gbp = "100000.0000110000000001";
  report.equity_gbp = holding.value_gbp;
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
  await page.getByRole("checkbox", { name: `${account.name} (${account.id})` }).check();
  await expect(page.getByText(/Linked recorded amount:/)).toBeVisible();
  await expect(page.getByText(/Linked account amount unavailable/)).toHaveCount(0);

  holding.value_gbp = "0.0000000000000001";
  report.equity_gbp = holding.value_gbp;
  await page.reload();
  await page.getByRole("button", { name: "Add a goal" }).click();
  await page.getByRole("checkbox", { name: `${account.name} (${account.id})` }).check();
  await page.getByLabel("Target amount (GBP, optional)").fill("1000");
  await page.getByLabel("Target date (optional)").fill("2035-01-01");
  await expect(page.getByText("Simple contribution illustration")).toBeVisible();
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

  await page.getByRole("button", { name: "Save goals" }).click();
  await expect(page.getByRole("status")).toContainText("Saved as local planning revision 1");
  expect(previewBody.expected_portfolio_id).toBe(report.portfolio_id);
  expect(previewBody.expected_journal_revision).toBe(report.revision);
  expect(previewBody.goals[0]).toMatchObject({
    current_amount_gbp: null,
    linked_portfolio_id: report.portfolio_id,
    linked_account_ids: [account.id],
  });
  expect(previewBody.goals[1].linked_account_ids).toEqual([]);
  await expect(page.getByText(/Target date 2035-01-01 · 1 linked account/)).toBeVisible();
  await page.getByRole("button", { name: "Edit Emergency reserve" }).click();
  await expect(page.getByRole("checkbox", { name: `${account.name} (${account.id})` })).toBeChecked();
  await expect(page.getByLabel("Manual amount set aside (GBP, used only without linked accounts)")).toBeDisabled();
  await page.getByRole("button", { name: "Save goals" }).click();
  await expect(page.getByRole("status")).toContainText("Saved as local planning revision 2");
  expect(previewBody.expected_portfolio_id).toBe(report.portfolio_id);
  expect(previewBody.expected_journal_revision).toBe(report.revision);
  expect(previewBody.goals[0].linked_account_ids).toEqual([account.id]);
  expect(previewBody.goals[0].current_amount_gbp).toBeNull();
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
      profile_hash: "c".repeat(64), goals: [{
        id: "6".padStart(32, "0"), name: "Emergency reserve",
        target_amount_gbp: "1000", target_date: "2035-01-01", notes: "",
        purpose: "not_set", access_date: null, liquidity: "unknown", loss_tolerance: "not_set",
        current_amount_gbp: null, monthly_contribution_gbp: null,
        linked_portfolio_id: report.portfolio_id, linked_account_ids: [account.id],
      }] },
  }));

  await page.goto("/planning");
  await expect(page.getByText("Linked account amount unavailable.")).toBeVisible();
  await expect(page.getByText("Simple contribution illustration")).toHaveCount(0);
  await page.getByRole("button", { name: "Edit Emergency reserve" }).click();
  await expect(page.getByText(/Linked account amount unavailable/)).toBeVisible();
  await expect(page.getByText(/Goal progress is unavailable/)).toBeVisible();
  await expect(page.getByText("Simple contribution illustration")).toHaveCount(0);
});
