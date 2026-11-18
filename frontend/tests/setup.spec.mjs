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
  let completed = false;
  let previewRequest;
  let confirmationCount = 0;
  await page.route("**/api/v1/settings", route => route.fulfill({
    json: { mode: "recorded", scenario: null, workspace: completed ? "investment" : "setup" },
  }));
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
        preview_id: "synthetic-preview", expires_at: "2099-01-01T00:00:00Z",
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
        message: options.partial ? "Synthetic ancillary export incomplete." : "Synthetic committed receipt.",
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
async function evidenceStep(page, recoveryEntry = false) {
  if (recoveryEntry) {
    await page.getByLabel("Balances as of (local time)").fill("2026-01-01T10:00");
    await page.getByLabel("Source available from (local time)").fill("2026-01-01T11:00");
  }
  await page.getByLabel("Cash for Synthetic savings (GBP)").fill("1234.56");
  await page.getByLabel("Where did these figures come from?").fill("Synthetic dated statement, selected scope only.");
}
async function depositPreview(page) {
  await accountStep(page);
  await expect(page.getByRole("heading", { name: "Starting balances", exact: true })).toBeVisible();
  await expect(page.locator(".setup-steps")).toContainText("2. Starting balances");
  await evidenceStep(page);
  await page.getByRole("button", { name: "Preview setup", exact: true }).click();
}

test("guided deposit setup requires review and creates only on explicit confirmation", async ({ page }) => {
  const api = await setupRoutes(page);
  await page.clock.setFixedTime(new Date("2026-01-01T10:00:00.000Z"));
  await unlock(page);
  await expect(page.getByText("Keep a record of your savings and investments in pounds. You'll add your accounts, enter starting balances, then check everything before saving.", { exact: true })).toBeVisible();
  await expect(page.getByText(/Manual records only/)).toHaveCount(0);
  await expect(page.getByLabel("Balances as of (local time)")).toHaveCount(0);
  await expect(page.getByLabel("Source available from (local time)")).toHaveCount(0);
  const help = page.getByText("Before you start: privacy, storage and later changes", { exact: true });
  const disclosure = help.locator("..");
  await expect(disclosure).not.toHaveAttribute("open", "");
  await help.focus();
  await page.keyboard.press("Enter");
  await expect(disclosure).toHaveAttribute("open", "");
  await expect(disclosure).toContainText("synthetic-temporary-workspace");
  await expect(disclosure).toContainText("make separate backups");
  await expect(disclosure).toContainText("Starting accounts and figures cannot be edited in place");
  await help.click();
  await depositPreview(page);
  const preview = page.getByRole("group", { name: "Review setup before confirmation" });
  await expect(preview).toContainText("1234.56");
  await expect(preview.getByRole("heading", { name: "Accounts and cash (GBP)" })).toBeVisible();
  await expect(preview.getByText(/Where these figures came from/)).toBeVisible();
  await expect(preview.locator(".review-dates")).toContainText("Europe/London");
  await expect(preview.locator(".review-dates")).toContainText("UTC: 2026-01-01T10:00:00.000Z");
  await expect(preview.locator(".review-dates")).toContainText("Starting figures recorded at");
  await expect(preview.locator(".review-dates")).not.toContainText("Source available from");
  await preview.getByText("How starting values and performance are recorded", { exact: true }).click();
  await expect(preview.getByText(/All opening roles are unassigned/)).toBeVisible();
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
  await expect(confirm).toBeDisabled();
  await page.getByRole("checkbox", { name: /I reviewed every account/ }).check();
  await confirm.click();
  await expect.poll(() => api.calls.filter(call => call.path === "confirm").length).toBe(1);
  expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0]);
});

test("investment accounts require an explicit no-holdings confirmation when left empty", async ({ page }) => {
  const api = await setupRoutes(page);
  await unlock(page);
  await accountStep(page, true);
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await evidenceStep(page);
  await page.getByRole("button", { name: "Preview setup", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Confirm that the included investment accounts have no shares");
  expect(api.calls.filter(call => call.path === "preview")).toHaveLength(0);
  await page.getByRole("checkbox", { name: /do not currently hold any shares/ }).check();
  await page.getByRole("button", { name: "Preview setup", exact: true }).click();
  expect(api.preview().opening.holdings).toEqual([]);
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
  await page.getByRole("button", { name: "Preview setup", exact: true }).click();
  await expect(page.getByText("Unknown — not zero", { exact: false })).toBeVisible();
  expect(api.preview().opening.holdings[0]).toMatchObject({ quantity: "2.345", price_gbp: null });
  expect(api.preview().profile.products[0]).toMatchObject({ quantity_increment: "0.001", minimum_notional_gbp: "1.00" });
  await page.getByRole("button", { name: "Edit evidence" }).click();
  await expect(page.getByRole("button", { name: "Confirm and create local records" })).toHaveCount(0);
  await page.getByLabel("Cash for Synthetic savings (GBP)").fill("NaN");
  await page.getByRole("button", { name: "Preview setup", exact: true }).click();
  await expect(page.getByRole("alert")).toBeFocused();
  await expect(page.getByRole("alert")).toContainText("number of zero or more");
  expect(api.calls.filter(call => call.path === "preview")).toHaveLength(1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
});

test("malformed preview fails closed without offering confirmation", async ({ page }) => {
  await setupRoutes(page, { invalidPreview: true });
  await unlock(page);
  await depositPreview(page);
  await expect(page.getByRole("alert")).toContainText("failed validation");
  await expect(page.getByRole("button", { name: "Confirm and create local records" })).toHaveCount(0);
});

test("reload loses in-memory draft and asks before abandoning it", async ({ page }) => {
  await setupRoutes(page);
  await unlock(page);
  await page.getByLabel("Account 1 name").fill("Synthetic unsaved alias");
  let warned = false;
  page.on("dialog", async dialog => {
    expect(dialog.type()).toBe("beforeunload");
    warned = true;
    await dialog.accept();
  });
  await page.reload();
  await expect(page.getByLabel("Account 1 name")).toHaveValue("");
  expect(warned).toBe(true);
});

test("uncertain confirmation retries only the identical preview; partial commit is not success", async ({ page }) => {
  const api = await setupRoutes(page, { loseConfirmation: true, partial: true });
  await unlock(page);
  await depositPreview(page);
  await page.getByRole("checkbox", { name: /I reviewed every account/ }).check();
  await page.getByRole("button", { name: "Confirm and create local records" }).click();
  await expect(page.getByRole("alert")).toContainText("outcome is unknown");
  await expect(page.getByRole("button", { name: "Edit evidence" })).toBeDisabled();
  await page.getByRole("button", { name: "Retry same confirmation" }).click();
  await expect(page.getByRole("heading", { name: /Setup partially completed/ })).toBeVisible();
  await expect(page.getByRole("button", { name: "Open read-only investment view" })).toHaveCount(0);
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
  await page.getByRole("button", { name: "Preview setup", exact: true }).click();
  await expect(page.getByRole("group", { name: "Review setup before confirmation" })).toBeVisible();
  expect(api.preview().profile).toEqual(originalProfile);
  expect(api.preview().opening).toEqual(originalOpening);
  await page.getByRole("checkbox", { name: /I reviewed every account/ }).check();
  await page.getByRole("button", { name: "Confirm and create local records" }).click();
  await expect(page.getByRole("heading", { name: /Setup partially completed/ })).toBeVisible();
  await expect(page.getByRole("status")).toContainText("No committed journal revision");
  await expect(page.getByRole("status")).not.toContainText("Journal revision: 0");
  await expect(page.getByRole("status")).toContainText("Journal committed: no");
  await expect(page.getByRole("button", { name: "Open read-only investment view" })).toHaveCount(0);
  expect(api.calls.map(call => call.path)).toEqual(["preview", "confirm"]);
});

test("reconstructed opening hash refusal cannot advance to confirmation or edit original identities", async ({ page }) => {
  const api = await setupRoutes(page, { state: "recoverable", recovery: recovery(), refusePreview: true });
  await page.goto("/setup");
  await page.getByRole("button", { name: "Re-enter original opening evidence" }).click();
  await evidenceStep(page, true);
  await page.getByLabel("Cash for Synthetic broker (GBP)").fill("1");
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
  await expect(preview).toContainText("Unknown — not zero");
  await expect(page.getByRole("button", { name: "Confirm and create local records" })).toBeDisabled();
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
    await expect(setup.getByRole("button", { name: "Open read-only investment view" })).toHaveCount(state === "ready" ? 1 : 0);
    expect(api.calls).toEqual([]);
  });
}
