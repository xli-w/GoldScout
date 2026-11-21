import { expect, test } from "@playwright/test";

test.use({ timezoneId: "Europe/London" });

const originalProfile = {
  schema_version: 2, portfolio_id: "original-synthetic-portfolio", currency: "GBP",
  created_at: "2025-12-30T12:00:00Z", policy: null,
  accounts: [
    { id: "original-deposit", binding: "original-bank-binding", name: "Synthetic savings", kind: "deposit", currency: "GBP" },
    { id: "original-broker", binding: "original-broker-binding", name: "Synthetic broker", kind: "broker", currency: "GBP" },
  ],
  products: [
    { id: "original-fund", name: "Synthetic fixed fund", sleeve: "equity", currency: "GBP",
      quantity_increment: "0.001", minimum_notional_gbp: "1.00" },
  ],
};
const originalOpening = {
  id: "original-synthetic-opening", type: "opening",
  timestamp: "2026-01-01T10:00:00.000Z", available_at: "2026-01-01T11:00:00.000Z",
  evidence: "Synthetic dated statement, selected scope only.",
  balances_gbp: { "original-deposit": "1234.56", "original-broker": "0.00" },
  holdings: [{ account_id: "original-broker", product_id: "original-fund", quantity: "2.345", price_gbp: null }],
};
const recovery = (requiresOpening = true) => ({
  can_preview: !requiresOpening, requires_opening: requiresOpening,
  profile: originalProfile, opening_id: originalOpening.id,
});

// Synthetic routes only. Run against the same isolated demo helper as dashboard.spec.mjs.
async function setupRoutes(page, options = {}) {
  const report = await (await page.request.get("/api/investment/v1/snapshot")).json();
  const calls = [];
  const sessions = [];
  let etoroSessionReads = 0;
  let etoroPortfolioReads = 0;
  let completed = false;
  let previewRequest;
  let confirmationCount = 0;
  await page.route("**/api/v1/settings", route => route.fulfill({
    json: { mode: "recorded", scenario: null, workspace: completed ? "investment" : "setup" },
  }));
  await page.route("**/api/investment/v1/etoro/session", route => {
    if (!options.startupRefresh) return route.fulfill({ json: {
      enabled: false, csrf: null, state: "not_selected", last_status: "not_selected",
      snapshot_available: false,
    } });
    etoroSessionReads++;
    const refreshed = etoroSessionReads > 2;
    return route.fulfill({ json: {
      enabled: true, csrf: "synthetic-etoro-csrf",
      state: "startup_refresh_enabled", last_status: refreshed ? "success" : "not_collected",
      snapshot_available: refreshed,
      connection: { environment: "real", account_id: 12345,
        expires_at: "2099-01-01T00:00:00Z", revoked_at: null, revision: 1 },
    } });
  });
  await page.route("**/api/investment/v1/etoro/portfolio", route => {
    etoroPortfolioReads++;
    const snapshot = options.startupRefresh && etoroPortfolioReads > 2 ? {
      schema_version: 1, environment: "real", account_id: 12345,
      source_as_of: "2026-01-01T10:00:00.000000Z", account_currency: "USD",
      account_totals: { accountAvailableCash: "100", accountFrozenCash: "0",
        accountCurrentPnl: "0", accountTotalValue: "100", accountTotalUsedMargin: "0",
        accountBalance: "100" },
      direct_positions: [], mirrors: [],
      closed_trade_history: { status: "not_requested", from_date: null, through_date: null,
        received_at: null, trades: [] },
      coverage: {}, gbp_balance: null,
    } : null;
    return route.fulfill({ json: {
      state: options.startupRefresh ? "startup_refresh_enabled" : "not_selected",
      snapshot, last_status: snapshot ? "success" : "not_collected",
      ...(options.startupRefresh ? { connection: { environment: "real", account_id: 12345,
        expires_at: "2099-01-01T00:00:00Z", revoked_at: null, revision: 1 } } : {}),
    } });
  });
  await page.route("**/api/investment/v1/setup/**", async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname.split("/").at(-1);
    if (request.method() === "GET") {
      const session = {
        enabled: options.enabled ?? true,
        csrf: options.enabled === false ? null : "synthetic-csrf",
        state: options.state ?? "empty", workspace: "synthetic-temporary-workspace",
        ...(options.recovery ? { recovery: options.recovery } : {}),
      };
      sessions.push(session);
      return route.fulfill({ json: session });
    }
    expect(request.headers()["x-csrf-token"]).toBe("synthetic-csrf");
    expect(request.headers()["content-type"]).toBe("application/json");
    const body = request.postDataJSON();
    calls.push({ path, body });
    if (path === "preview") {
      previewRequest = body;
      if (options.refusePreview) return route.fulfill({ status: 409, json: { detail: "Original opening hash does not match." } });
      if (options.invalidPreview) return route.fulfill({ json: { preview_id: "bad" } });
      return route.fulfill({ json: {
        preview_id: "synthetic-preview", expires_at: options.previewExpiry ?? "2099-01-01T00:00:00Z",
        profile: body.profile, opening: body.opening, report,
      } });
    }
    if (path === "confirm") {
      expect(body).toEqual({ preview_id: "synthetic-preview" });
      confirmationCount++;
      if (options.loseConfirmation && confirmationCount === 1) return route.abort();
      completed = !options.partial;
      return route.fulfill({ json: {
        status: options.partial ? "partial" : "completed",
        committed: !options.uncommitted, revision: options.uncommitted ? null : 1,
        message: options.receiptMessage ?? (options.partial ? "Synthetic ancillary export incomplete." : "Synthetic committed receipt."),
      } });
    }
    if (path === "recovery-preview") {
      if (!options.recoveryOpening) return route.fulfill({ status: 409, json: { detail: "Opening evidence is required." } });
      return route.fulfill({ json: {
        preview_id: "synthetic-preview", expires_at: "2099-01-01T00:00:00Z",
        profile: options.recovery.profile, opening: options.recoveryOpening, report,
      } });
    }
    return route.fulfill({ status: 404, json: {} });
  });
  return { calls, sessions, preview: () => previewRequest };
}

async function unlock(page) {
  await page.goto("/setup");
  await expect(page.getByRole("heading", { name: "Accounts", exact: true })).toBeVisible();
}
async function accountStep(page, broker = false) {
  await page.getByLabel("Account 1 name").fill("Synthetic savings");
  await page.getByLabel("Account 1 type").selectOption(broker ? "broker" : "deposit");
  await page.getByRole("checkbox", { name: /These are the GBP accounts I want to track/ }).check();
  await page.getByRole("button", { name: "Continue", exact: true }).click();
}
async function confirmOpeningContext(page, recoveryEntry = false) {
  const label = recoveryEntry
    ? /re-entered figures, source and dates match the original opening record/
    : /I checked that every starting figure is current/;
  await page.getByRole("checkbox", { name: label }).check();
}
async function evidenceStep(page, recoveryEntry = false) {
  if (recoveryEntry) {
    await page.getByLabel("Balances as of (local time)").fill("2026-01-01T10:00");
    await page.getByLabel("Source available from (local time)").fill("2026-01-01T11:00");
  }
  await page.getByLabel("Cash for Synthetic savings (GBP)").fill("1234.56");
  await page.getByLabel("Where did these figures come from?").fill("Synthetic dated statement, selected scope only.");
  await confirmOpeningContext(page, recoveryEntry);
}
async function depositPreview(page) {
  await accountStep(page);
  await expect(page.getByRole("heading", { name: "Starting balances", exact: true })).toBeVisible();
  await expect(page.locator(".setup-steps")).toContainText("2. Starting balances");
  await expect(page.getByText(/only savings \/ deposit accounts; no investment holdings are needed/)).toBeVisible();
  await evidenceStep(page);
  await page.getByRole("button", { name: "Preview setup", exact: true }).click();
}

test("startup refresh result appears without reloading the setup wizard", async ({ page }) => {
  await setupRoutes(page, { startupRefresh: true });
  const mutations = [];
  page.on("request", request => {
    if (request.url().includes("/api/investment/v1/etoro/") && request.method() !== "GET")
      mutations.push(request.url());
  });
  await unlock(page);
  const etoro = page.locator("#setup-etoro");
  await expect(etoro).not.toHaveAttribute("open", "");
  await etoro.locator("summary").first().click();
  await expect(page.getByText("Waiting for the one authorized startup read. This screen does not initiate a request."))
    .toBeVisible();
  await expect(page.getByRole("heading", { name: "Latest eToro read" }))
    .toBeVisible({ timeout: 5000 });
  await expect(etoro.getByText(/Account ID 12345/).first()).toBeVisible();
  await etoro.locator("summary").first().click();
  await etoro.locator("summary").first().click();
  await expect(etoro.getByRole("heading", { name: "Latest eToro read" })).toBeVisible();
  expect(mutations).toEqual([]);
});

test("legacy eToro setup link opens the optional connection inside setup", async ({ page }) => {
  await setupRoutes(page);
  await page.goto("/etoro-portfolio");
  await expect(page).toHaveURL(/\/setup#setup-etoro$/);
  await expect(page.locator("#setup-etoro")).toHaveAttribute("open", "");
  await expect(page.getByRole("heading", { name: "Accounts", exact: true })).toBeVisible();
});

test("eToro consent preview survives collapsing and reviewing portfolio setup", async ({ page }) => {
  await page.clock.install({ time: new Date("2026-01-01T10:00:00Z") });
  await setupRoutes(page);
  const writes = [];
  await page.route("**/api/investment/v1/etoro/session", route => route.fulfill({ json: {
    enabled: true, read_generation: 0, csrf: "synthetic-etoro-csrf", state: "not_connected", last_status: "not_collected",
    snapshot_available: false, connection: null,
  } }));
  await page.route("**/api/investment/v1/etoro/portfolio", route => route.fulfill({ json: {
    state: "not_connected", last_status: "not_collected", snapshot: null, connection: null,
  } }));
  await page.route("**/api/investment/v1/etoro/connect", route => {
    writes.push(route.request().postDataJSON());
    return route.fulfill({ json: { preview_id: "synthetic-etoro-preview", expires_at: "2026-01-01T10:00:10Z",
      snapshot: { schema_version: 1, environment: "demo", account_id: 12345,
        source_as_of: "2026-01-01T10:00:00Z", account_currency: "USD",
        account_totals: { accountAvailableCash: "100", accountFrozenCash: "0",
          accountCurrentPnl: "0", accountTotalValue: "100", accountTotalUsedMargin: "0", accountBalance: "100" },
        direct_positions: [], mirrors: [],
        closed_trade_history: { status: "not_requested", from_date: null, through_date: null,
          received_at: null, trades: [] },
        coverage: {}, gbp_balance: null } } });
  });
  await unlock(page);
  const optional = page.locator("#setup-etoro");
  await optional.locator(":scope > summary").click();
  await optional.getByText("Connect eToro", { exact: true }).click();
  await optional.getByLabel("eToro environment").selectOption("demo");
  await optional.getByLabel("I verified this key has Read-only permission.").check();
  await optional.getByLabel("I verified my account is eligible for this API use.").check();
  await optional.getByLabel(/I verified rights for local display/).check();
  await optional.getByRole("button", { name: "Connect and retrieve once" }).click();
  const binding = optional.getByLabel("I verified account ID 12345 is the eToro account I selected.");
  await binding.check();
  await optional.locator(":scope > summary").click();
  await optional.locator(":scope > summary").click();
  await expect(binding).toBeChecked();
  await depositPreview(page);
  await expect(binding).toBeChecked();
  const confirm = optional.getByRole("button", { name: "Confirm connection and account" });
  await expect(confirm).toBeEnabled();
  await page.clock.fastForward(11000);
  await expect(confirm).toBeDisabled();
  await expect(optional).toContainText("This eToro preview expired. Discard it and request a new read.");
  expect(writes).toHaveLength(1);
});

async function activeEToroRoutes(page) {
  let revoked = false;
  const writes = [];
  const snapshot = { schema_version: 1, environment: "demo", account_id: 12345,
    source_as_of: "2026-01-01T10:00:00Z", account_currency: "USD",
    account_totals: { accountAvailableCash: "100", accountFrozenCash: "0",
      accountCurrentPnl: "0", accountTotalValue: "100", accountTotalUsedMargin: "0", accountBalance: "100" },
    direct_positions: [], mirrors: [],
    closed_trade_history: { status: "not_requested", from_date: null, through_date: null,
      received_at: null, trades: [] },
    coverage: {}, gbp_balance: null };
  await page.route("**/api/investment/v1/etoro/**", route => {
    const request = route.request();
    const path = new URL(request.url()).pathname.split("/").at(-1);
    if (request.method() === "GET") return route.fulfill({ json: path === "session" ? {
      enabled: true, read_generation: 0, csrf: "synthetic-etoro-csrf",
      state: revoked ? "revoked" : "startup_refresh_enabled",
      last_status: revoked ? "startup_refresh_revoked" : "rate_limited", snapshot_available: false,
      connection: { environment: "demo", account_id: 12345, expires_at: "2099-01-01T00:00:00Z",
        revoked_at: revoked ? "2026-01-01T10:00:00Z" : null, revision: 1 },
    } : { state: revoked ? "revoked" : "startup_refresh_enabled",
      last_status: revoked ? "startup_refresh_revoked" : "rate_limited", snapshot: null,
      connection: { environment: "demo", account_id: 12345, expires_at: "2099-01-01T00:00:00Z",
        revoked_at: revoked ? "2026-01-01T10:00:00Z" : null, revision: 1 } } });
    writes.push(path);
    if (path === "connect") return route.fulfill({ json: {
      preview_id: "synthetic-etoro-preview", expires_at: "2099-01-01T00:00:00Z", snapshot,
    } });
    if (path === "revoke") { revoked = true; return route.fulfill({ json: { state: "revoked" } }); }
    return route.abort();
  });
  return writes;
}

for (const state of ["blocked", "manual_unopened", "recoverable"]) {
  test(`eToro revocation remains available while setup is ${state}`, async ({ page }) => {
    await setupRoutes(page, { state, ...(state === "recoverable" ? { recovery: recovery(false) } : {}) });
    const writes = await activeEToroRoutes(page);
    await page.goto("/setup");
    const etoro = page.locator("#setup-etoro");
    await expect(etoro).toBeVisible();
    await etoro.locator(":scope > summary").click();
    await expect(etoro).toContainText("cannot start or confirm new reads");
    await expect(etoro.getByRole("button", { name: "Refresh once now" })).toHaveCount(0);
    await expect(etoro.getByRole("button", { name: "Connect and retrieve once" })).toHaveCount(0);
    const revoke = etoro.getByRole("button", { name: "Revoke startup refresh" });
    await expect(revoke).toBeEnabled();
    expect(writes).toEqual([]);
    page.once("dialog", dialog => dialog.accept());
    await revoke.click();
    await expect(etoro).toContainText("Startup permission is revoked");
    expect(writes).toEqual(["revoke"]);
  });
}

test("a partial setup receipt keeps revocation available and blocks an existing eToro preview", async ({ page }) => {
  await setupRoutes(page, { partial: true });
  const writes = await activeEToroRoutes(page);
  await unlock(page);
  const etoro = page.locator("#setup-etoro");
  await etoro.locator(":scope > summary").click();
  await etoro.getByText("Update eToro connection", { exact: true }).click();
  await etoro.getByLabel("eToro environment").selectOption("demo");
  await etoro.getByLabel("I verified this key has Read-only permission.").check();
  await etoro.getByLabel("I verified my account is eligible for this API use.").check();
  await etoro.getByLabel(/I verified rights for local display/).check();
  await etoro.getByRole("button", { name: "Connect and retrieve once" }).click();
  await etoro.getByLabel("I verified account ID 12345 is the eToro account I selected.").check();
  await depositPreview(page);
  await page.getByRole("button", { name: "Confirm and create local records" }).click();
  await expect(page.getByRole("heading", { name: /Setup partially completed/ })).toBeVisible();
  await expect(etoro).toContainText("cannot start or confirm new reads");
  await expect(etoro.getByRole("button", { name: "Confirm connection and account" })).toBeDisabled();
  await expect(etoro.getByRole("button", { name: "Discard preview" })).toBeEnabled();
  await expect(etoro.getByRole("button", { name: "Refresh once now" })).toHaveCount(0);
  page.once("dialog", dialog => dialog.accept());
  await etoro.getByRole("button", { name: "Revoke startup refresh" }).click();
  await expect(etoro).toContainText("Startup permission is revoked");
  await expect(etoro.getByRole("button", { name: "Confirm connection and account" })).toHaveCount(0);
  expect(writes).toEqual(["connect", "revoke"]);
});

test("cash-only setup skips investments in both directions", async ({ page }) => {
  await setupRoutes(page);
  await unlock(page);
  await accountStep(page);
  await expect(page.getByRole("heading", { name: "Starting balances", exact: true })).toBeFocused();
  await page.getByRole("button", { name: "Back", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Accounts", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Accounts", exact: true })).toBeFocused();
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Starting balances", exact: true })).toBeVisible();
  await expect(page.locator(".setup-steps")).toContainText("2. Starting balances");
  await expect(page.locator(".setup-steps")).not.toContainText("Investments");
  await expect(page.getByRole("button", { name: "Add holding", exact: true })).toHaveCount(0);
});

test("starting figures must be confirmed current before a preview can be requested", async ({ page }) => {
  const api = await setupRoutes(page);
  await unlock(page);
  await accountStep(page);
  await page.getByLabel("Cash for Synthetic savings (GBP)").fill("1234.56");
  await page.getByLabel("Where did these figures come from?").fill("Synthetic current statement.");
  const currentCheck = page.getByRole("checkbox", { name: /I checked that every starting figure is current/ });
  await expect(currentCheck).not.toBeChecked();
  await page.getByRole("button", { name: "Preview setup", exact: true }).click();
  expect(api.calls.filter(call => call.path === "preview")).toHaveLength(0);
  await currentCheck.check();
  await page.getByRole("button", { name: "Preview setup", exact: true }).click();
  await expect(page.getByRole("group", { name: "Review setup before confirmation" })).toBeVisible();
  expect(api.calls.filter(call => call.path === "preview")).toHaveLength(1);
  await page.getByRole("button", { name: "Edit evidence" }).click();
  await expect(currentCheck).not.toBeChecked();
  await page.getByRole("button", { name: "Preview setup", exact: true }).click();
  await expect(currentCheck).toBeFocused();
  expect(api.calls.filter(call => call.path === "preview")).toHaveLength(1);
  await currentCheck.check();
  await page.getByRole("button", { name: "Preview setup", exact: true }).click();
  await expect(page.getByRole("group", { name: "Review setup before confirmation" })).toBeVisible();
  expect(api.calls.filter(call => call.path === "preview")).toHaveLength(2);
});

test("guided deposit setup requires review and creates only on explicit confirmation", async ({ page }) => {
  const api = await setupRoutes(page);
  await page.clock.setFixedTime(new Date("2026-01-01T10:00:00.000Z"));
  await unlock(page);
  await expect(page.locator(".setup-intro")).toContainText("Add the GBP accounts you want to track");
  const preflight = page.getByRole("region", { name: "Have these ready" });
  await expect(preflight).toContainText("same date and time");
  await expect(preflight).toContainText("supported quantity step and minimum purchase");
  await expect(preflight).toContainText("checked with your provider");
  await expect(page.getByText(/Manual records only/)).toHaveCount(0);
  await expect(page.getByLabel("Balances as of (local time)")).toHaveCount(0);
  await expect(page.getByLabel("Source available from (local time)")).toHaveCount(0);
  const help = page.getByText("Before you start", { exact: true });
  const disclosure = help.locator("..");
  await expect(disclosure).not.toHaveAttribute("open", "");
  await help.focus();
  await page.keyboard.press("Enter");
  await expect(disclosure).toHaveAttribute("open", "");
  await expect(disclosure).toContainText("synthetic-temporary-workspace");
  await expect(disclosure).toContainText("arrange separate backups");
  await expect(disclosure).toContainText("Starting accounts and figures cannot be edited in place");
  await help.click();
  await accountStep(page);
  await expect(page.locator(".setup-intro")).toHaveCount(0);
  await expect(preflight).toHaveCount(0);
  await expect(page.getByText(/Your private setup draft is saved in this browser/)).toBeVisible();
  await evidenceStep(page);
  await page.getByRole("button", { name: "Preview setup", exact: true }).click();
  const preview = page.getByRole("group", { name: "Review setup before confirmation" });
  await expect(preview).toContainText("1234.56");
  await expect(preview.getByRole("heading", { name: "Accounts, cash and holdings (GBP)" })).toBeVisible();
  await expect(preview).toContainText("Synthetic savings");
  await expect(preview).toContainText("cash 1234.56");
  await expect(preview.getByText(/Where these figures came from/)).toBeVisible();
  await expect(preview.locator(".review-dates")).toContainText("Europe/London");
  await expect(preview.locator(".review-dates")).toContainText("UTC: 2026-01-01T10:00:00.000Z");
  await expect(preview.locator(".review-dates")).toContainText("Starting figures recorded at");
  await expect(preview.locator(".review-dates")).not.toContainText("Source available from");
  await expect(preview.getByText("Starting funds are unassigned; you can add optional roles later.")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  expect(api.calls.map(call => call.path)).toEqual(["preview"]);
  const request = api.preview();
  expect(request.profile.policy).toBeNull();
  expect(request.profile.accounts[0].binding).toMatch(/^local-/);
  expect(request.profile.products).toEqual([]);
  expect(Object.values(request.opening.balances_gbp)).toEqual(["1234.56"]);
  expect(request.opening.holdings).toEqual([]);
  expect(request.opening.timestamp).toMatch(/Z$/);
  expect(request.opening.timestamp).toBe("2026-01-01T10:00:00.000Z");
  expect(request.opening.available_at).toBe(request.opening.timestamp);
  expect(request.profile.created_at).toBe(request.opening.timestamp);
  const confirm = page.getByRole("button", { name: "Confirm and create local records" });
  await expect(confirm).toBeEnabled();
  await confirm.click();
  await expect(page.getByRole("heading", { name: "Setup completed" })).toBeVisible();
  await expect(page.getByText("Open your portfolio to choose review intervals, review your accounts and optionally add a goal.")).toBeVisible();
  await expect(page.getByText(/Restart it with/)).toHaveCount(0);
  await expect(page.getByText(/Your draft is not saved until you confirm/)).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Have these ready" })).toHaveCount(0);
  await page.getByRole("button", { name: "Open portfolio" }).click();
  await expect(page).toHaveURL(/\/investment$/);
  await expect(page.getByRole("heading", { name: "Your portfolio", exact: true })).toBeVisible();
  await expect.poll(() => api.calls.filter(call => call.path === "confirm").length).toBe(1);
  expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0]);
});

test("setup draft resumes after reload but clears review confirmations and previews", async ({ page }) => {
  const api = await setupRoutes(page);
  await page.goto("/setup");
  await page.getByLabel("Account 1 name").fill("Synthetic savings");
  await page.getByLabel("Account 1 type").selectOption("deposit");
  await page.getByRole("checkbox", { name: /These are the GBP accounts I want to track/ }).check();
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await page.getByLabel("Cash for Synthetic savings (GBP)").fill("1234.56");
  await page.getByLabel("Where did these figures come from?").fill("Synthetic statement.");
  const confirmation = page.getByRole("checkbox", { name: /I checked that every starting figure is current/ });
  await confirmation.check();
  await expect.poll(() => page.evaluate(() => localStorage.length)).toBe(1);

  await page.reload();
  await expect(page.getByLabel("Cash for Synthetic savings (GBP)")).toBeVisible();
  await expect(page.getByLabel("Cash for Synthetic savings (GBP)")).toHaveValue("1234.56");
  await expect(page.getByLabel("Where did these figures come from?")).toHaveValue("Synthetic statement.");
  await expect(confirmation).not.toBeChecked();
  await page.getByRole("button", { name: "Preview setup", exact: true }).click();
  expect(api.calls.filter(call => call.path === "preview")).toHaveLength(0);
  await expect(confirmation).not.toBeChecked();
});

test("setup confirmation expires even without a redundant review checkbox", async ({ page }) => {
  const api = await setupRoutes(page, { previewExpiry: "2026-01-01T10:00:10Z" });
  await page.clock.install({ time: new Date("2026-01-01T10:00:00Z") });
  await unlock(page);
  await depositPreview(page);
  const confirm = page.getByRole("button", { name: "Confirm and create local records" });
  await expect(confirm).toBeEnabled();
  await page.clock.fastForward(11000);
  await expect(confirm).toBeDisabled();
  await expect(page.getByRole("alert")).toContainText("Preview expired");
  expect(api.calls.map(call => call.path)).toEqual(["preview"]);
});

test("completed setup surfaces unavailable recording without suggesting another setup", async ({ page }) => {
  const message = "Setup completed, but manual recording is unavailable. Restart GoldScout to retry recording initialization.";
  const api = await setupRoutes(page, { receiptMessage: message });
  await unlock(page);
  await depositPreview(page);
  await page.getByRole("button", { name: "Confirm and create local records" }).click();
  await expect(page.getByRole("heading", { name: "Setup completed" })).toBeVisible();
  await expect(page.getByText(message, { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Open portfolio" })).toBeEnabled();
  await expect(page.getByRole("button", { name: "Confirm and create local records" })).toHaveCount(0);
  expect(api.calls.map(call => call.path)).toEqual(["preview", "confirm"]);
});

test("investment accounts require an explicit completeness confirmation when left empty", async ({ page }) => {
  const api = await setupRoutes(page);
  await unlock(page);
  await accountStep(page, true);
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await evidenceStep(page);
  await page.getByRole("button", { name: "Preview setup", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Confirm that you have listed all holdings");
  expect(api.calls.filter(call => call.path === "preview")).toHaveLength(0);
  await page.getByRole("checkbox", { name: /Synthetic savings: I have listed all holdings/ }).check();
  await page.getByRole("button", { name: "Preview setup", exact: true }).click();
  expect(api.preview().opening.holdings).toEqual([]);
  const preview = page.getByRole("group", { name: "Review setup before confirmation" });
  await expect(preview).toContainText("Synthetic savings");
  await expect(preview).toContainText("holdings confirmed none");
});

test("investment rules are validated on their step and explain why a holding cannot yet be added", async ({ page }) => {
  await setupRoutes(page);
  await unlock(page);
  await accountStep(page, true);
  await page.getByRole("button", { name: "Add investment", exact: true }).click();
  await page.getByLabel("Investment 1 name").fill("Synthetic equity");
  await page.getByLabel("Investment 1 type").selectOption("equity");
  await page.getByLabel("Investment 1 pricing currency").selectOption("GBP");
  await page.getByLabel("Investment 1 smallest quantity step").fill("0");
  await page.getByLabel("Investment 1 minimum purchase (GBP)").fill("1.00");
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Check the highlighted investment details");
  await expect(page.getByLabel("Investment 1 smallest quantity step")).toHaveAttribute("aria-invalid", "true");
  await page.getByLabel("Investment 1 smallest quantity step").fill("0.001");
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Starting balances", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Add holding", exact: true })).toBeEnabled();

  await page.getByRole("button", { name: "Back", exact: true }).click();
  await page.getByRole("button", { name: "Remove investment 1", exact: true }).click();
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await expect(page.getByRole("button", { name: "Add holding", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Add holding", exact: true })).toHaveAttribute("aria-describedby", "setup-no-investments");
  await expect(page.getByText(/go back and add its investment first/)).toBeVisible();
});

test("every investment account needs its own holdings completeness confirmation", async ({ page }) => {
  const api = await setupRoutes(page);
  await unlock(page);
  await page.getByLabel("Account 1 name").fill("First brokerage");
  await page.getByLabel("Account 1 type").selectOption("broker");
  await page.getByRole("button", { name: "Add account", exact: true }).click();
  await page.getByLabel("Account 2 name").fill("Second brokerage");
  await page.getByLabel("Account 2 type").selectOption("broker");
  await page.getByRole("checkbox", { name: /These are the GBP accounts I want to track/ }).check();
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await page.getByLabel("Cash for First brokerage (GBP)").fill("0");
  await page.getByLabel("Cash for Second brokerage (GBP)").fill("0");
  await page.getByLabel("Where did these figures come from?").fill("Two synthetic brokerage statements.");
  await page.getByRole("checkbox", { name: /First brokerage: I have listed all holdings/ }).check();
  await confirmOpeningContext(page);
  await page.getByRole("button", { name: "Preview setup", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("each investment account");
  expect(api.calls.filter(call => call.path === "preview")).toHaveLength(0);
  await page.getByRole("checkbox", { name: /Second brokerage: I have listed all holdings/ }).check();
  await page.getByRole("button", { name: "Preview setup", exact: true }).click();
  expect(api.preview().opening.holdings).toEqual([]);
  const preview = page.getByRole("group", { name: "Review setup before confirmation" });
  await expect(preview).toContainText("First brokerage");
  await expect(preview).toContainText("Second brokerage");
  await expect(preview).toContainText("holdings confirmed none");
});

test("holding completeness checks survive amount edits but clear when a holding moves accounts", async ({ page }) => {
  const api = await setupRoutes(page);
  await unlock(page);
  await page.getByLabel("Account 1 name").fill("First brokerage");
  await page.getByLabel("Account 1 type").selectOption("broker");
  await page.getByRole("button", { name: "Add account", exact: true }).click();
  await page.getByLabel("Account 2 name").fill("Second brokerage");
  await page.getByLabel("Account 2 type").selectOption("broker");
  await page.getByRole("checkbox", { name: /These are the GBP accounts I want to track/ }).check();
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await page.getByRole("button", { name: "Add investment", exact: true }).click();
  await page.getByLabel("Investment 1 name").fill("Synthetic equity");
  await page.getByLabel("Investment 1 type").selectOption("equity");
  await page.getByLabel("Investment 1 pricing currency").selectOption("GBP");
  await page.getByLabel("Investment 1 smallest quantity step").fill("0.001");
  await page.getByLabel("Investment 1 minimum purchase (GBP)").fill("1.00");
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await page.getByLabel("Cash for First brokerage (GBP)").fill("0");
  await page.getByLabel("Cash for Second brokerage (GBP)").fill("0");
  await page.getByLabel("Where did these figures come from?").fill("Two synthetic brokerage statements.");
  await page.getByRole("button", { name: "Add holding", exact: true }).click();
  await page.getByLabel("Holding 1 account").selectOption({ label: "First brokerage" });
  await page.getByLabel("Holding 1 investment").selectOption({ label: "Synthetic equity" });
  await page.getByLabel("Holding 1 quantity", { exact: true }).fill("2.345");
  const firstCheck = page.getByRole("checkbox", { name: /First brokerage: I have listed all holdings/ });
  const secondCheck = page.getByRole("checkbox", { name: /Second brokerage: I have listed all holdings/ });
  await firstCheck.check();
  await secondCheck.check();
  await confirmOpeningContext(page);
  await page.getByRole("button", { name: "Preview setup", exact: true }).click();
  expect(api.preview().opening.holdings).toHaveLength(1);
  await page.getByRole("button", { name: "Edit evidence" }).click();
  await page.getByLabel("Holding 1 quantity", { exact: true }).fill("2.500");
  await expect(firstCheck).toBeChecked();
  await expect(secondCheck).toBeChecked();
  await page.getByLabel("Holding 1 investment").selectOption("");
  await expect(firstCheck).not.toBeChecked();
  await expect(secondCheck).toBeChecked();
  await page.getByLabel("Holding 1 investment").selectOption({ label: "Synthetic equity" });
  await expect(firstCheck).not.toBeChecked();
  await firstCheck.check();
  await page.getByLabel("Holding 1 account").selectOption({ label: "Second brokerage" });
  await expect(firstCheck).not.toBeChecked();
  await expect(secondCheck).not.toBeChecked();
});

test("removing an account warns before removing its linked holdings", async ({ page }) => {
  await setupRoutes(page);
  await unlock(page);
  await page.getByLabel("Account 1 name").fill("Synthetic brokerage");
  await page.getByLabel("Account 1 type").selectOption("broker");
  await page.getByRole("button", { name: "Add account", exact: true }).click();
  await page.getByLabel("Account 2 name").fill("Synthetic savings");
  await page.getByLabel("Account 2 type").selectOption("deposit");
  await page.getByRole("checkbox", { name: /These are the GBP accounts I want to track/ }).check();
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await page.getByRole("button", { name: "Add investment", exact: true }).click();
  await page.getByLabel("Investment 1 name").fill("Synthetic equity");
  await page.getByLabel("Investment 1 type").selectOption("equity");
  await page.getByLabel("Investment 1 pricing currency").selectOption("GBP");
  await page.getByLabel("Investment 1 smallest quantity step").fill("0.001");
  await page.getByLabel("Investment 1 minimum purchase (GBP)").fill("1.00");
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await page.getByRole("button", { name: "Add holding", exact: true }).click();
  await page.getByLabel("Holding 1 account").selectOption({ label: "Synthetic brokerage" });
  await page.getByLabel("Holding 1 investment").selectOption({ label: "Synthetic equity" });
  await page.getByLabel("Holding 1 quantity", { exact: true }).fill("1");
  await page.getByRole("button", { name: "Back", exact: true }).click();
  await page.getByRole("button", { name: "Back", exact: true }).click();

  let warning = "";
  page.once("dialog", async dialog => {
    warning = dialog.message();
    await dialog.dismiss();
  });
  await page.getByRole("button", { name: "Remove account 1" }).click();
  expect(warning).toContain("Synthetic brokerage");
  expect(warning).toContain("1 linked holding row");
  await expect(page.getByLabel("Account 1 name")).toHaveValue("Synthetic brokerage");

  page.once("dialog", dialog => dialog.accept());
  await page.getByRole("button", { name: "Remove account 1" }).click();
  await expect(page.getByLabel("Account 1 name")).toHaveValue("Synthetic savings");
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Starting balances", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Add holding", exact: true })).toHaveCount(0);
});

test("broker holdings preserve exact decimal strings and unknown unit prices", async ({ page }) => {
  const api = await setupRoutes(page);
  await unlock(page);
  await accountStep(page, true);
  await page.getByRole("button", { name: "Add investment", exact: true }).click();
  await expect(page.getByLabel("Investment 1 smallest quantity step")).toHaveValue("");
  await expect(page.getByLabel("Investment 1 minimum purchase (GBP)")).toHaveValue("");
  await page.getByLabel("Investment 1 name").fill("Synthetic equity");
  await page.getByLabel("Investment 1 type").selectOption("equity");
  await page.getByLabel("Investment 1 pricing currency").selectOption("GBP");
  await page.getByLabel("Investment 1 smallest quantity step").fill("0.001");
  await page.getByLabel("Investment 1 minimum purchase (GBP)").fill("1.00");
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await evidenceStep(page);
  await page.getByRole("button", { name: "Add holding", exact: true }).click();
  await page.getByLabel("Holding 1 account").selectOption({ label: "Synthetic savings" });
  await page.getByLabel("Holding 1 investment").selectOption({ label: "Synthetic equity" });
  await page.getByLabel("Holding 1 quantity", { exact: true }).fill("2.345");
  await page.getByRole("checkbox", { name: /Synthetic savings: I have listed all holdings/ }).check();
  await confirmOpeningContext(page);
  await page.getByRole("button", { name: "Preview setup", exact: true }).click();
  const groupedAccount = page.getByRole("group", { name: "Review setup before confirmation" })
    .locator("li").filter({ hasText: "Synthetic savings" });
  await expect(groupedAccount).toContainText("Synthetic equity");
  await expect(groupedAccount).toContainText("2.345 units");
  await expect(page.getByRole("group", { name: "Review setup before confirmation" }))
    .toContainText("smallest quantity step 0.001");
  await expect(page.getByRole("group", { name: "Review setup before confirmation" }))
    .toContainText("minimum purchase GBP 1.00");
  await expect(page.getByText("unknown (not zero)", { exact: false })).toBeVisible();
  expect(api.preview().opening.holdings[0]).toMatchObject({ quantity: "2.345", price_gbp: null });
  expect(api.preview().profile.products[0]).toMatchObject({ quantity_increment: "0.001", minimum_notional_gbp: "1.00" });
  await page.getByRole("button", { name: "Edit evidence" }).click();
  await expect(page.getByRole("button", { name: "Confirm and create local records" })).toHaveCount(0);
  await page.getByLabel("Cash for Synthetic savings (GBP)").fill("NaN");
  await confirmOpeningContext(page);
  await page.getByRole("button", { name: "Preview setup", exact: true }).click();
  await expect(page.getByRole("alert")).toBeFocused();
  await expect(page.getByRole("alert")).toContainText("number of zero or more");
  const amountError = page.getByRole("alert").getByRole("link", { name: /Synthetic savings cash/ });
  await expect(amountError).toBeVisible();
  await amountError.click();
  await expect(page.getByLabel("Cash for Synthetic savings (GBP)")).toBeFocused();
  expect(api.calls.filter(call => call.path === "preview")).toHaveLength(1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);

  await page.getByRole("button", { name: "Back", exact: true }).click();
  await expect(page.getByText(/Removing this investment will also remove its 1 linked holding row/)).toBeVisible();
  let removalWarning = "";
  page.once("dialog", async dialog => {
    removalWarning = dialog.message();
    await dialog.dismiss();
  });
  await page.getByRole("button", { name: "Remove investment 1", exact: true }).click();
  expect(removalWarning).toContain("1 linked holding row");
  await expect(page.getByLabel("Investment 1 name")).toHaveValue("Synthetic equity");
  page.once("dialog", dialog => dialog.accept());
  await page.getByRole("button", { name: "Remove investment 1", exact: true }).click();
  await expect(page.getByLabel("Investment 1 name")).toHaveCount(0);
});

test("malformed preview fails closed without offering confirmation", async ({ page }) => {
  await setupRoutes(page, { invalidPreview: true });
  await unlock(page);
  await depositPreview(page);
  await expect(page.getByRole("alert")).toContainText("failed validation");
  await expect(page.getByRole("button", { name: "Confirm and create local records" })).toHaveCount(0);
});

test("reload restores the browser draft without requiring a technical warning", async ({ page }) => {
  await setupRoutes(page);
  await unlock(page);
  await page.getByLabel("Account 1 name").fill("Synthetic unsaved alias");
  let warned = false;
  page.on("dialog", async dialog => {
    warned = true;
    await dialog.accept();
  });
  await expect.poll(() => page.evaluate(() => localStorage.length)).toBeGreaterThan(0);
  await page.reload();
  await expect(page.getByLabel("Account 1 name")).toHaveValue("Synthetic unsaved alias");
  expect(warned).toBe(false);
});

test("uncertain confirmation retries only the identical preview; partial commit is not success", async ({ page }) => {
  const api = await setupRoutes(page, { loseConfirmation: true, partial: true });
  await unlock(page);
  await depositPreview(page);
  await page.getByRole("button", { name: "Confirm and create local records" }).click();
  await expect(page.getByRole("alert")).toContainText("outcome is unknown");
  await expect(page.getByRole("button", { name: "Edit evidence" })).toBeDisabled();
  await page.getByRole("button", { name: "Retry same confirmation" }).click();
  await expect(page.getByRole("heading", { name: /Setup partially completed/ })).toBeVisible();
  await expect(page.getByRole("button", { name: "Open portfolio" })).toHaveCount(0);
  expect(api.calls.filter(call => call.path === "confirm")).toHaveLength(2);
  expect(api.calls.filter(call => call.path === "preview")).toHaveLength(1);
});

test("existing unopened journal cannot be adopted or overwritten", async ({ page }) => {
  const api = await setupRoutes(page, { state: "manual_unopened" });
  await page.goto("/setup");
  await expect(page.getByText(/already initialized/)).toBeVisible();
  expect(api.calls).toEqual([]);
});

test("recovery requires explicit preview; missing evidence is never guessed", async ({ page }) => {
  const api = await setupRoutes(page, { state: "recoverable", recovery: recovery(false) });
  await page.goto("/setup");
  await page.getByRole("button", { name: "Preview verified recovery" }).click();
  await expect(page.getByRole("alert")).toContainText("re-entering and explicitly reviewing");
  await expect(page.getByRole("button", { name: "Confirm and create local records" })).toHaveCount(0);
  expect(api.calls.map(call => call.path)).toEqual(["recovery-preview"]);
});

test("revision-zero recovery prefills only the original locked profile and reconstructs its opening", async ({ page }) => {
  const api = await setupRoutes(page, {
    state: "recoverable", recovery: recovery(), partial: true, uncommitted: true,
  });

  await page.goto("/setup");
  await expect(page.getByRole("button", { name: "Preview verified recovery" })).toHaveCount(0);
  await page.getByRole("button", { name: "Re-enter original opening evidence" }).click();
  const profile = page.getByRole("region", { name: "Recovered fixed profile" });
  await expect(profile).toContainText("original-bank-binding");
  await expect(profile).toContainText("original-broker-binding");
  await expect(profile).toContainText("original-fund");
  await expect(profile).toContainText("0.001");
  await expect(page.getByLabel("Account 1 name")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Add investment", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Back", exact: true })).toHaveCount(0);
  await expect(page.getByLabel("Cash for Synthetic savings (GBP)")).toHaveValue("");
  await expect(page.getByLabel("Cash for Synthetic broker (GBP)")).toHaveValue("");
  await expect(page.getByLabel("Balances as of (local time)")).toHaveValue("");
  await expect(page.getByLabel("Where did these figures come from?")).toHaveValue("");
  await evidenceStep(page, true);
  await page.getByLabel("Cash for Synthetic broker (GBP)").fill("0.00");
  await page.getByRole("button", { name: "Add holding", exact: true }).click();
  await page.getByLabel("Holding 1 account").selectOption("original-broker");
  await page.getByLabel("Holding 1 investment").selectOption("original-fund");
  await page.getByLabel("Holding 1 quantity", { exact: true }).fill("2.345");
  await page.getByRole("checkbox", { name: /Synthetic broker: I have listed all holdings/ }).check();
  await confirmOpeningContext(page, true);
  await page.getByRole("button", { name: "Preview setup", exact: true }).click();
  await expect(page.getByRole("group", { name: "Review setup before confirmation" })).toBeVisible();
  expect(api.preview().profile).toEqual(originalProfile);
  expect(api.preview().opening).toEqual(originalOpening);
  await page.getByRole("button", { name: "Confirm and create local records" }).click();
  await expect(page.getByRole("heading", { name: /Setup partially completed/ })).toBeVisible();
  await expect(page.getByRole("status")).toContainText("No committed journal revision");
  await expect(page.getByRole("status")).not.toContainText("Journal revision: 0");
  await expect(page.getByRole("status")).toContainText("Journal committed: no");
  await expect(page.getByRole("button", { name: "Open portfolio" })).toHaveCount(0);
  expect(api.calls.map(call => call.path)).toEqual(["preview", "confirm"]);
});

test("re-entered recovery evidence warns before unloading, including from guidance", async ({ page }) => {
  await setupRoutes(page, { state: "recoverable", recovery: recovery() });
  await page.goto("/setup");
  await page.getByRole("button", { name: "Re-enter original opening evidence" }).click();
  await page.getByLabel("Cash for Synthetic savings (GBP)").fill("1234.56");
  const isUnloadBlocked = () => page.evaluate(() => {
    const event = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(event);
    return event.defaultPrevented;
  });
  expect(await isUnloadBlocked()).toBe(true);
  await page.getByRole("button", { name: "Back to recovery guidance" }).click();
  expect(await isUnloadBlocked()).toBe(true);
  await page.getByRole("button", { name: "Re-enter original opening evidence" }).click();
  await expect(page.getByLabel("Cash for Synthetic savings (GBP)")).toHaveValue("1234.56");
});

test("reconstructed opening hash refusal cannot advance to confirmation or edit original identities", async ({ page }) => {
  const api = await setupRoutes(page, { state: "recoverable", recovery: recovery(), refusePreview: true });
  await page.goto("/setup");
  await page.getByRole("button", { name: "Re-enter original opening evidence" }).click();
  await evidenceStep(page, true);
  await page.getByLabel("Cash for Synthetic broker (GBP)").fill("1");
  await page.getByRole("checkbox", { name: /Synthetic broker: I have listed all holdings/ }).check();
  await confirmOpeningContext(page, true);
  await page.getByRole("button", { name: "Preview setup", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("refused");
  await expect(page.getByRole("button", { name: "Confirm and create local records" })).toHaveCount(0);
  expect(api.preview().profile).toEqual(originalProfile);
  expect(api.preview().opening.id).toBe(originalOpening.id);
  expect(api.calls.some(call => call.path === "confirm")).toBe(false);
});

test("retained recovery evidence is reviewed without reconstructing or replacing its original IDs", async ({ page }) => {
  const api = await setupRoutes(page, {
    state: "recoverable", recovery: recovery(false), recoveryOpening: originalOpening,
  });
  await page.goto("/setup");
  await expect(page.getByRole("button", { name: "Re-enter original opening evidence" })).toHaveCount(0);
  await page.getByRole("button", { name: "Preview verified recovery" }).click();
  const preview = page.getByRole("group", { name: "Review setup before confirmation" });
  await expect(preview.getByText(/Starting records may already be saved/)).toBeVisible();
  await expect(preview.getByText(/Nothing has been saved yet/)).toHaveCount(0);
  await preview.getByText("Technical details: exact profile and starting record", { exact: true }).click();
  await expect(preview.getByText(/Opening record:/)).toContainText(originalOpening.id);
  await expect(preview.getByText(/Opening record:/)).toContainText(originalProfile.portfolio_id);
  await expect(preview).toContainText("unknown (not zero)");
  await expect(page.getByRole("button", { name: "Confirm and create local records" })).toBeEnabled();
  expect(api.calls.map(call => call.path)).toEqual(["recovery-preview"]);
});

for (const requiresOpening of [false, true]) {
  test(`local recovery session exposes the verified metadata (${requiresOpening ? "re-entry" : "retained evidence"})`, async ({ page }) => {
    const metadata = recovery(requiresOpening);
    const api = await setupRoutes(page, { state: "recoverable", recovery: metadata });
    await page.goto("/setup");
    await expect(page.getByRole("heading", { name: "Recovery", exact: true })).toBeVisible();
    expect(api.sessions[0]).toMatchObject({ enabled: true, state: "recoverable", recovery: metadata });
    await expect(page.getByRole("button", {
      name: requiresOpening ? "Re-enter original opening evidence" : "Preview verified recovery",
    })).toBeEnabled();
    expect(api.calls).toEqual([]);
  });
}

for (const state of ["ready", "blocked", "manual_unopened"]) {
  test(`disabled ${state} session accepts null CSRF without offering setup authority`, async ({ page }) => {
    const api = await setupRoutes(page, { state, enabled: false });
    await page.goto("/setup");
    const setup = page.getByRole("region", { name: "Investment setup", exact: true });
    await expect(setup.getByRole("heading", {
      name: state === "ready" ? "Existing investment records" : "Setup cannot create this workspace",
    })).toBeVisible();
    expect(api.sessions[0]).toMatchObject({ enabled: false, csrf: null, state });
    await expect(setup.getByRole("alert")).toHaveCount(0);
    await expect(setup.getByRole("button", { name: "Preview setup", exact: true })).toHaveCount(0);
    await expect(setup.getByRole("button", { name: "Confirm and create local records" })).toHaveCount(0);
    await expect(setup.getByRole("button", { name: "Open portfolio" })).toHaveCount(state === "ready" ? 1 : 0);
    expect(api.calls).toEqual([]);
  });
}
