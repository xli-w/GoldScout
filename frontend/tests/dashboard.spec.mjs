import { expect, test } from "@playwright/test";

const etoroSnapshot = {
  schema_version: 1, environment: "demo", account_id: 12345,
  source_as_of: "2026-07-01T10:00:00Z", account_currency: "USD",
  account_totals: { accountAvailableCash: "100", accountFrozenCash: "0",
    accountCurrentPnl: "5", accountTotalValue: "999999", accountTotalUsedMargin: "0",
    accountBalance: "100" },
  direct_positions: [
    { instrument_id: 101, asset_currency: "USD", netUnits: "2", netContracts: "2",
      netCurrentExposureAssetCurrency: "42", netCurrentExposureAccountCurrency: "42", avgLeverage: "1" },
    { instrument_id: 102, asset_currency: "GBP", netUnits: "3", netContracts: "3",
      netCurrentExposureAssetCurrency: "12", netCurrentExposureAccountCurrency: "16", avgLeverage: "1" },
  ],
  mirrors: [{ mirror_id: 7, positions: [
    { instrument_id: 103, asset_currency: "JPY", netUnits: "1", netContracts: "1",
      netCurrentExposureAssetCurrency: "500", netCurrentExposureAccountCurrency: "4", avgLeverage: "1" },
  ] }],
  closed_trade_history: { status: "not_requested", from_date: null, through_date: null,
    received_at: null, trades: [] },
  coverage: {}, gbp_balance: { status: "provider_reported", amount_gbp: "77",
    source_balance: "100", source_currency: "USD", display_currency: "GBP",
    exchange_rate: "0.77", source_as_of: null, received_at: "2026-07-01T10:02:00Z" },
};
const etoroConnection = (expiresAt = "2099-01-01T00:00:00Z", revoked = false) => ({
  environment: "demo", account_id: 12345, expires_at: expiresAt,
  revoked_at: revoked ? "2026-07-01T10:00:00Z" : null, revision: 1,
});

test.describe("eToro within the portfolio", () => {
  test.use({ timezoneId: "Europe/London" });

  test("provider evidence stays separate, shows UTC and each asset currency, and preserves old links", async ({ page }) => {
    const writes = [];
    await page.route("**/api/investment/v1/etoro/**", route => {
      const request = route.request();
      if (request.method() !== "GET") { writes.push(request.url()); return route.abort(); }
      if (request.url().endsWith("/session")) return route.fulfill({ json: {
        enabled: true, csrf: "synthetic-csrf", state: "connected",
        last_status: "success", snapshot_available: true,
        connection: { environment: "demo", account_id: 12345, expires_at: null, revoked_at: null, revision: 1 },
      } });
      return route.fulfill({ json: { state: "connected", last_status: "success", snapshot: etoroSnapshot,
        connection: etoroConnection(null) } });
    });

    await page.goto("/investment?view=tools");
    const navigation = page.getByRole("navigation", { name: "Portfolio navigation" });
    await expect(navigation.getByRole("link", { name: /eToro/ })).toHaveCount(0);
    const etoro = page.getByRole("region", { name: "eToro portfolio" });
    await expect(etoro).toBeVisible();
    await expect(etoro.getByRole("heading", { name: "Latest eToro read" })).toBeVisible();
    await expect(etoro).toContainText("separate from your journal");
    await expect(etoro).toContainText("Jul 1, 2026, 10:00:00 AM UTC");
    await expect(etoro).not.toContainText("Jul 1, 2026, 10:02:00 AM UTC");
    await expect(etoro).toContainText("balance coverage and source time are unverified");
    const direct = etoro.getByRole("region", { name: "Direct instrument aggregates" });
    await expect(direct.getByRole("columnheader", { name: "Exposure (asset currency)" })).toBeVisible();
    await expect(direct.getByRole("row").filter({ hasText: "101" })).toContainText("42 USD");
    await expect(direct.getByRole("row").filter({ hasText: "102" })).toContainText("12 GBP");
    await expect(etoro.getByRole("region", { name: "Instrument aggregates for copy relationship 7" })
      .getByRole("row").filter({ hasText: "103" })).toContainText("500 JPY");
    await page.getByRole("link", { name: "Overview", exact: true }).click();
    await expect(page.getByRole("region", { name: "Investment totals" })).not.toContainText("999999");
    await page.getByRole("link", { name: "Tools", exact: true }).click();
    await expect(etoro.getByRole("button", { name: "Connect and retrieve once" })).toBeHidden();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();

    await page.goto("/etoro-portfolio");
    await expect(page).toHaveURL(/\/investment\?view=tools#investment-etoro$/);
    await expect(etoro).toBeVisible();
    expect(writes).toEqual([]);
  });

  test("connection status and controls remain available when journal data fails", async ({ page }) => {
    let session = { enabled: false, csrf: null, state: "not_selected", last_status: "not_selected", snapshot_available: false };
    let portfolio = { state: "not_selected", last_status: "not_selected", snapshot: null };
    const writes = [];
    await page.route("**/api/investment/v1/etoro/**", route => {
      if (route.request().method() !== "GET") { writes.push(route.request().url()); return route.abort(); }
      return route.fulfill({ json: route.request().url().endsWith("/session") ? session : portfolio });
    });
    await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ status: 503, json: { error: "unavailable" } }));
    await page.goto("/investment?view=tools");
    await expect(page.getByRole("heading", { name: "Investment data unavailable" })).toBeVisible();
    const etoro = page.getByRole("region", { name: "eToro portfolio" });
    await expect(etoro).toContainText("eToro is not enabled for this session");
    await expect(etoro.getByRole("button", { name: "Connect and retrieve once" })).toHaveCount(0);

    session = { enabled: true, csrf: "synthetic-csrf", state: "expired", last_status: "permission_expired",
      snapshot_available: true, connection: { environment: "demo", account_id: 12345,
        expires_at: "2026-06-30T10:00:00Z", revoked_at: null, revision: 1 } };
    portfolio = { state: "expired", last_status: "permission_expired", snapshot: etoroSnapshot,
      connection: session.connection };
    await page.reload();
    await expect(etoro).toContainText("Startup permission expired");
    await expect(etoro).toContainText("The displayed source snapshot is not refreshed");
    await expect(etoro.getByRole("button", { name: "Refresh once now" })).toBeEnabled();
    session.state = "revoked";
    session.last_status = "startup_refresh_revoked";
    session.connection.revoked_at = "2026-07-01T10:00:00Z";
    portfolio.state = "revoked";
    portfolio.last_status = "startup_refresh_revoked";
    await page.reload();
    await expect(etoro).toContainText("Startup permission is revoked");
    session = { enabled: true, csrf: null, state: "startup_refresh_enabled", last_status: "success",
      snapshot_available: true, connection: { environment: "demo", account_id: 12345,
        expires_at: "2099-01-01T00:00:00Z", revoked_at: null, revision: 1 } };
    portfolio = { state: "startup_refresh_enabled", last_status: "success", snapshot: etoroSnapshot,
      connection: session.connection };
    await page.reload();
    await expect(etoro.getByRole("alert")).toContainText("local session could not be verified");
    await expect(etoro.getByRole("button", { name: "Refresh once now" })).toBeDisabled();
    await expect(etoro.getByRole("button", { name: "Revoke startup refresh" })).toBeDisabled();
    await expect(etoro.getByRole("button", { name: "Connect and retrieve once" })).toHaveCount(0);
    expect(writes).toEqual([]);
  });

  test("partial status failures preserve known revocation access but block new reads", async ({ page }) => {
    let revoked = false;
    let sessionUnavailable = false;
    let portfolioUnavailable = true;
    const writes = [];
    await page.route("**/api/investment/v1/etoro/**", route => {
      const request = route.request();
      const path = new URL(request.url()).pathname.split("/").at(-1);
      if (request.method() === "POST") {
        writes.push(path);
        expect(path).toBe("revoke");
        revoked = true;
        return route.fulfill({ json: { state: "revoked" } });
      }
      if (path === "session") {
        if (sessionUnavailable) return route.fulfill({ status: 503, json: {} });
        return route.fulfill({ json: { enabled: true, csrf: "synthetic-csrf",
          state: revoked ? "revoked" : "startup_refresh_enabled",
          last_status: revoked ? "startup_refresh_revoked" : "success", snapshot_available: true,
          connection: { environment: "demo", account_id: 12345, expires_at: "2099-01-01T00:00:00Z",
            revoked_at: revoked ? "2026-07-01T10:00:00Z" : null, revision: 1 } } });
      }
      if (portfolioUnavailable) return route.fulfill({ status: 503, json: {} });
      return route.fulfill({ json: { state: "revoked", last_status: "startup_refresh_revoked",
        snapshot: etoroSnapshot, connection: etoroConnection(undefined, true) } });
    });

    await page.goto("/investment?view=tools");
    const etoro = page.getByRole("region", { name: "eToro portfolio" });
    await expect(etoro).toContainText("eToro portfolio status is unavailable");
    const revoke = etoro.getByRole("button", { name: "Revoke startup refresh" });
    await expect(revoke).toBeEnabled();
    expect(writes).toEqual([]);
    page.once("dialog", dialog => dialog.accept());
    await revoke.click();
    await expect(etoro).toContainText("Connection status reports revocation");
    expect(writes).toEqual(["revoke"]);

    sessionUnavailable = true;
    portfolioUnavailable = false;
    await etoro.getByRole("button", { name: "Retry status" }).click();
    await expect(etoro).toContainText("eToro connection status is unavailable");
    await expect(etoro.getByRole("heading", { name: "Latest eToro read" })).toBeVisible();
    await expect(revoke).toBeEnabled();
    await expect(etoro.getByRole("button", { name: "Refresh once now" })).toHaveCount(0);
    await expect(etoro.getByRole("button", { name: "Connect and retrieve once" })).toHaveCount(0);
    expect(writes).toEqual(["revoke"]);
    await page.reload();
    await expect(etoro).toContainText("eToro connection status is unavailable");
    await expect(revoke).toHaveCount(0);
    expect(writes).toEqual(["revoke"]);
  });

  test("disagreeing status reads block collection but keep revocation available", async ({ page }) => {
    let sessionState = "startup_refresh_enabled";
    let portfolioState = "revoked";
    let portfolioRevision = 1;
    let includeSessionConnection = true;
    let includePortfolioConnection = true;
    const writes = [];
    await page.route("**/api/investment/v1/etoro/**", route => {
      const request = route.request();
      if (request.method() !== "GET") { writes.push(request.url()); return route.abort(); }
      return route.fulfill({ json: request.url().endsWith("/session") ? {
        enabled: true, csrf: "synthetic-csrf", state: sessionState,
        last_status: "success", snapshot_available: true,
        ...(includeSessionConnection ? { connection: {
          environment: "demo", account_id: 12345, expires_at: "2099-01-01T00:00:00Z",
          revoked_at: sessionState === "revoked" ? "2026-07-01T10:00:00Z" : null, revision: 1,
        } } : {}),
      } : { state: portfolioState, last_status: "success", snapshot: etoroSnapshot,
        ...(includePortfolioConnection ? { connection: {
          environment: "demo", account_id: 12345, expires_at: "2099-01-01T00:00:00Z",
          revoked_at: portfolioState === "revoked" ? "2026-07-01T10:00:00Z" : null, revision: portfolioRevision,
        } } : {}) } });
    });
    await page.goto("/investment?view=tools");
    const etoro = page.getByRole("region", { name: "eToro portfolio" });
    await expect(etoro).toContainText("eToro status reads disagree");
    await expect(etoro.getByRole("button", { name: "Refresh once now" })).toBeDisabled();
    await expect(etoro.getByRole("button", { name: "Revoke startup refresh" })).toBeEnabled();
    await expect(etoro.getByRole("button", { name: "Connect and retrieve once" })).toHaveCount(0);
    sessionState = "revoked";
    portfolioState = "startup_refresh_enabled";
    await etoro.getByRole("button", { name: "Retry status" }).click();
    await expect(etoro).toContainText("eToro status reads disagree");
    await expect(etoro).not.toContainText("Startup permission is revoked.");
    await expect(etoro.getByRole("button", { name: "Revoke startup refresh" })).toBeEnabled();
    sessionState = "startup_refresh_enabled";
    portfolioRevision = 2;
    await etoro.getByRole("button", { name: "Retry status" }).click();
    await expect(etoro).toContainText("eToro status reads disagree");
    await expect(etoro.getByRole("button", { name: "Refresh once now" })).toBeDisabled();
    portfolioRevision = 1;
    includePortfolioConnection = false;
    await etoro.getByRole("button", { name: "Retry status" }).click();
    await expect(etoro).toContainText("eToro status reads disagree");
    await expect(etoro.getByRole("button", { name: "Refresh once now" })).toBeDisabled();
    sessionState = "not_connected";
    portfolioState = "not_connected";
    includeSessionConnection = false;
    await etoro.getByRole("button", { name: "Retry status" }).click();
    await expect(etoro).toContainText("eToro connection details are incomplete");
    await expect(etoro.getByRole("button", { name: "Connect and retrieve once" })).toHaveCount(0);
    sessionState = "startup_refresh_enabled";
    portfolioState = "startup_refresh_enabled";
    includeSessionConnection = true;
    includePortfolioConnection = true;
    await etoro.getByRole("button", { name: "Retry status" }).click();
    await expect(etoro.getByRole("button", { name: "Refresh once now" })).toBeEnabled();
    expect(writes).toEqual([]);
  });

  test("unavailable status payloads retain revocation and allow status recovery", async ({ page }) => {
    let unavailable = true;
    const writes = [];
    await page.route("**/api/investment/v1/etoro/**", route => {
      const request = route.request();
      if (request.method() !== "GET") { writes.push(request.url()); return route.abort(); }
      const state = unavailable ? "unavailable" : "startup_refresh_enabled";
      const connection = unavailable ? null : etoroConnection();
      return route.fulfill({ json: request.url().endsWith("/session") ? {
        enabled: true, csrf: "synthetic-csrf", state, last_status: "not_collected",
        snapshot_available: false, connection,
      } : { state, last_status: "not_collected", snapshot: null, connection } });
    });
    await page.goto("/investment?view=tools");
    const etoro = page.getByRole("region", { name: "eToro portfolio" });
    await expect(etoro).toContainText("eToro connection status is unavailable");
    await expect(etoro.getByRole("button", { name: "Revoke startup refresh" })).toBeEnabled();
    await expect(etoro.getByRole("button", { name: "Connect and retrieve once" })).toHaveCount(0);
    unavailable = false;
    await etoro.getByRole("button", { name: "Retry status" }).click();
    await expect(etoro).toContainText("Waiting for the one authorized startup read");
    await expect(etoro).not.toContainText("eToro connection status is unavailable");
    expect(writes).toEqual([]);
  });

  test("workspace failure retains eToro revocation without enabling a provider read", async ({ page }) => {
    let revoked = false;
    const writes = [];
    await page.route("**/api/v1/settings", route => route.fulfill({ status: 503, json: {} }));
    await page.route("**/api/investment/v1/etoro/**", route => {
      const request = route.request();
      const path = new URL(request.url()).pathname.split("/").at(-1);
      if (request.method() === "GET") return route.fulfill({ json: path === "session" ? {
        enabled: true, csrf: "synthetic-csrf", state: revoked ? "revoked" : "startup_refresh_enabled",
        last_status: revoked ? "startup_refresh_revoked" : "success", snapshot_available: true,
        connection: { environment: "demo", account_id: 12345, expires_at: "2099-01-01T00:00:00Z",
          revoked_at: revoked ? "2026-07-01T10:00:00Z" : null, revision: 1 },
      } : { state: revoked ? "revoked" : "startup_refresh_enabled",
        last_status: revoked ? "startup_refresh_revoked" : "success", snapshot: etoroSnapshot,
        connection: etoroConnection(undefined, revoked) } });
      writes.push(path);
      if (path === "revoke") { revoked = true; return route.fulfill({ json: { state: "revoked" } }); }
      return route.abort();
    });
    await page.goto("/investment?view=tools");
    await expect(page.getByText("Workspace settings are unavailable; portfolio source is unknown.")).toBeVisible();
    const fallback = page.locator("#workspace-etoro");
    await fallback.locator(":scope > summary").click();
    await expect(fallback.getByRole("button", { name: "Refresh once now" })).toHaveCount(0);
    await expect(fallback.getByRole("button", { name: "Connect and retrieve once" })).toHaveCount(0);
    await expect(fallback.getByRole("button", { name: "Revoke startup refresh" })).toBeEnabled();
    expect(writes).toEqual([]);
    page.once("dialog", dialog => dialog.accept());
    await fallback.getByRole("button", { name: "Revoke startup refresh" }).click();
    await expect(fallback).toContainText("Startup permission is revoked");
    expect(writes).toEqual(["revoke"]);
  });

  test("workspace failure and recovery preserve an uncertain eToro result and cached revocation", async ({ page }) => {
    let workspaceUnavailable = false;
    let sessionUnavailable = false;
    const writes = [];
    await page.route("**/api/v1/settings", route => workspaceUnavailable
      ? route.fulfill({ status: 503, json: {} })
      : route.fulfill({ json: { mode: "demo", workspace: "demo" } }));
    await page.route("**/api/investment/v1/etoro/**", route => {
      const request = route.request();
      const path = new URL(request.url()).pathname.split("/").at(-1);
      if (request.method() === "GET") {
        if (path === "session" && sessionUnavailable) return route.fulfill({ status: 503, json: {} });
        return route.fulfill({ json: path === "session" ? {
          enabled: true, read_generation: 0, csrf: "synthetic-csrf", state: "startup_refresh_enabled",
          last_status: "success", snapshot_available: true, connection: etoroConnection(),
        } : { state: "startup_refresh_enabled", last_status: "success",
          snapshot: etoroSnapshot, connection: etoroConnection() } });
      }
      writes.push(path);
      return route.abort();
    });
    await page.goto("/investment?view=tools");
    let etoro = page.getByRole("region", { name: "eToro portfolio" });
    page.once("dialog", dialog => dialog.accept());
    await etoro.getByRole("button", { name: "Refresh once now" }).click();
    await expect(etoro).toContainText("Refresh is still unverified");
    workspaceUnavailable = true;
    sessionUnavailable = true;
    await page.evaluate(() => window.dispatchEvent(new Event("visibilitychange")));
    await expect(page.getByText("Workspace settings are unavailable; portfolio source is unknown.")).toBeVisible();
    const fallback = page.locator("#workspace-etoro");
    await fallback.locator(":scope > summary").click();
    await expect(fallback).toContainText("Refresh is still unverified");
    await expect(fallback.getByRole("button", { name: "Revoke startup refresh" })).toBeEnabled();
    workspaceUnavailable = false;
    await page.getByRole("button", { name: "Retry", exact: true }).click();
    etoro = page.getByRole("region", { name: "eToro portfolio" });
    await expect(etoro).toContainText("Refresh is still unverified");
    await expect(etoro.getByRole("button", { name: "Revoke startup refresh" })).toBeEnabled();
    sessionUnavailable = false;
    await etoro.getByRole("button", { name: "Retry status" }).click();
    await expect(etoro.getByRole("button", { name: "Refresh once now" })).toBeDisabled();
    await expect(etoro).toContainText("Refresh is still unverified");
    expect(writes).toEqual(["refresh"]);
  });

  test("manual refresh works without startup permission and confirms the account", async ({ page }) => {
    const writes = [];
    await page.route("**/api/investment/v1/etoro/**", route => {
      const request = route.request();
      const path = new URL(request.url()).pathname.split("/").at(-1);
      if (request.method() === "GET") return route.fulfill({ json: path === "session" ? {
        enabled: true, read_generation: 0, csrf: "synthetic-csrf", state: "connected",
        last_status: "success", snapshot_available: true, connection: etoroConnection(null),
      } : { state: "connected", last_status: "success", snapshot: etoroSnapshot,
        connection: etoroConnection(null) } });
      writes.push(request.postDataJSON());
      return route.fulfill({ json: { state: "refreshed" } });
    });
    await page.goto("/investment?view=tools");
    const etoro = page.getByRole("region", { name: "eToro portfolio" });
    await expect(etoro).toContainText("Account ID 12345 · demo environment");
    page.once("dialog", dialog => {
      expect(dialog.message()).toContain("demo account 12345");
      void dialog.accept();
    });
    await etoro.getByRole("button", { name: "Refresh once now" }).click();
    await expect.poll(() => writes.length).toBe(1);
    expect(writes[0]).toEqual({
      confirm_read: true, confirm_closed_trade_history: true,
      history_retention_rights_verified: true, read_generation: 0, revision: 1,
      account_id: 12345, environment: "demo",
    });
  });

  test("a failed refresh leaves the previous read dated and its missing GBP balance explicit", async ({ page }) => {
    let lastStatus = "success";
    const writes = [];
    const snapshot = { ...etoroSnapshot, gbp_balance: { status: "unavailable", error_code: "balance_unavailable",
      source_as_of: null, received_at: "2026-07-01T10:02:00Z" } };
    await page.route("**/api/investment/v1/etoro/**", route => {
      const request = route.request();
      const path = new URL(request.url()).pathname.split("/").at(-1);
      if (request.method() === "GET") return route.fulfill({ json: path === "session" ? {
        enabled: true, read_generation: 0, csrf: "synthetic-csrf", state: "startup_refresh_enabled",
        last_status: lastStatus, snapshot_available: true,
        connection: { environment: "demo", account_id: 12345,
          expires_at: "2099-01-01T00:00:00Z", revoked_at: null, revision: 1 },
      } : { state: "startup_refresh_enabled", last_status: lastStatus, snapshot, connection: etoroConnection() } });
      writes.push(path);
      if (path === "refresh") {
        lastStatus = "rate_limited";
        return route.fulfill({ json: { state: "refresh_failed", error_code: "rate_limited" } });
      }
      return route.abort();
    });
    await page.goto("/investment?view=tools");
    const etoro = page.getByRole("region", { name: "eToro portfolio" });
    await expect(etoro.getByRole("region", { name: "GBP balance unavailable" }))
      .toContainText("No conversion was estimated. Reason: balance_unavailable.");
    page.once("dialog", dialog => dialog.accept());
    await etoro.getByRole("button", { name: "Refresh once now" }).click();
    await expect(etoro).toContainText("Last refresh status: rate_limited");
    await expect(etoro).toContainText("The displayed source snapshot is not refreshed");
    await expect(etoro).toContainText("Jul 1, 2026, 10:00:00 AM UTC");
    expect(writes).toEqual(["refresh"]);
  });

  test("an uncertain refresh blocks another read while allowing revocation after a partial status", async ({ page }) => {
    let revoked = false;
    let portfolioUnavailable = false;
    const writes = [];
    await page.route("**/api/investment/v1/etoro/**", route => {
      const request = route.request();
      const path = new URL(request.url()).pathname.split("/").at(-1);
      if (request.method() === "GET") {
        if (path === "portfolio" && portfolioUnavailable) return route.fulfill({ status: 503, json: {} });
        return route.fulfill({ json: path === "session" ? {
          enabled: true, read_generation: 0, csrf: "synthetic-csrf", state: revoked ? "revoked" : "startup_refresh_enabled",
          last_status: revoked ? "startup_refresh_revoked" : "success", snapshot_available: true,
          connection: { environment: "demo", account_id: 12345, expires_at: "2099-01-01T00:00:00Z",
            revoked_at: revoked ? "2026-07-01T10:00:00Z" : null, revision: 1 },
        } : { state: revoked ? "revoked" : "startup_refresh_enabled",
          last_status: revoked ? "startup_refresh_revoked" : "success", snapshot: etoroSnapshot,
          connection: etoroConnection(undefined, revoked) } });
      }
      writes.push(path);
      if (path === "refresh") {
        portfolioUnavailable = true;
        return route.fulfill({ status: 503, json: {} });
      }
      if (path === "revoke") {
        revoked = true;
        portfolioUnavailable = false;
        return route.fulfill({ json: { state: "revoked" } });
      }
      return route.abort();
    });
    await page.goto("/investment?view=tools");
    const etoro = page.getByRole("region", { name: "eToro portfolio" });
    page.once("dialog", dialog => dialog.accept());
    await etoro.getByRole("button", { name: "Refresh once now" }).click();
    await expect(etoro).toContainText("refresh result could not be verified");
    await etoro.getByRole("button", { name: "Retry status" }).click();
    await expect(etoro).toContainText("Refresh is still unverified");
    await expect(etoro.getByRole("button", { name: "Refresh once now" })).toBeDisabled();
    await expect(etoro.getByRole("button", { name: "Revoke startup refresh" })).toBeEnabled();
    page.once("dialog", dialog => dialog.accept());
    await etoro.getByRole("button", { name: "Revoke startup refresh" }).click();
    await expect(etoro).toContainText("Startup permission is revoked");
    await expect(etoro).toContainText("Refresh is still unverified");
    await expect(etoro.getByRole("button", { name: "Connect and retrieve once" })).toHaveCount(0);
    expect(writes).toEqual(["refresh", "revoke"]);
  });

  test("unchanged status does not resolve a refresh that may still be in flight", async ({ page }) => {
    let snapshot = etoroSnapshot;
    const writes = [];
    await page.route("**/api/investment/v1/etoro/**", route => {
      const request = route.request();
      const path = new URL(request.url()).pathname.split("/").at(-1);
      if (request.method() === "GET") return route.fulfill({ json: path === "session" ? {
        enabled: true, read_generation: 0, csrf: "synthetic-csrf", state: "startup_refresh_enabled",
        last_status: "success", snapshot_available: true,
        connection: { environment: "demo", account_id: 12345, expires_at: "2099-01-01T00:00:00Z",
          revoked_at: null, revision: 1 },
      } : { state: "startup_refresh_enabled", last_status: "success", snapshot, connection: etoroConnection() } });
      writes.push(path);
      return route.abort();
    });
    await page.goto("/investment?view=tools");
    const etoro = page.getByRole("region", { name: "eToro portfolio" });
    page.once("dialog", dialog => dialog.accept());
    await etoro.getByRole("button", { name: "Refresh once now" }).click();
    await expect(etoro).toContainText("refresh result could not be verified");
    await etoro.getByRole("button", { name: "Retry status" }).click();
    await expect(etoro).toContainText("Refresh is still unverified");
    await expect(etoro.getByRole("button", { name: "Refresh once now" })).toBeDisabled();
    expect(writes).toEqual(["refresh"]);
    snapshot = { ...etoroSnapshot, source_as_of: "2026-07-01T10:05:00Z" };
    await etoro.getByRole("button", { name: "Retry status" }).click();
    await expect(etoro).toContainText("Refresh is still unverified");
    await expect(etoro.getByRole("button", { name: "Refresh once now" })).toBeDisabled();
    expect(writes).toEqual(["refresh"]);
  });

  test("an uncertain revocation checks status instead of repeating the request", async ({ page }) => {
    let revoked = false;
    const writes = [];
    await page.route("**/api/investment/v1/etoro/**", route => {
      const request = route.request();
      const path = new URL(request.url()).pathname.split("/").at(-1);
      if (request.method() === "GET") return route.fulfill({ json: path === "session" ? {
        enabled: true, csrf: "synthetic-csrf", state: revoked ? "revoked" : "startup_refresh_enabled",
        last_status: revoked ? "startup_refresh_revoked" : "success", snapshot_available: true,
        connection: { environment: "demo", account_id: 12345, expires_at: "2099-01-01T00:00:00Z",
          revoked_at: revoked ? "2026-07-01T10:00:00Z" : null, revision: 1 },
      } : { state: revoked ? "revoked" : "startup_refresh_enabled",
        last_status: revoked ? "startup_refresh_revoked" : "success", snapshot: etoroSnapshot,
        connection: etoroConnection(undefined, revoked) } });
      writes.push(path);
      if (path === "revoke") {
        revoked = true;
        return route.fulfill({ status: 503, json: {} });
      }
      return route.abort();
    });
    await page.goto("/investment?view=tools");
    const etoro = page.getByRole("region", { name: "eToro portfolio" });
    page.once("dialog", dialog => dialog.accept());
    await etoro.getByRole("button", { name: "Revoke startup refresh" }).click();
    await expect(etoro).toContainText("Revocation could not be verified and may already have taken effect");
    await expect(etoro.getByRole("button", { name: "Revoke startup refresh" })).toBeEnabled();
    await etoro.getByRole("button", { name: "Retry status" }).click();
    await expect(etoro).toContainText("Startup permission is revoked");
    expect(writes).toEqual(["revoke"]);
  });

  for (const exchangeRate of ["0", "-0.5"]) {
    test(`a provider GBP balance with exchange rate ${exchangeRate} is unavailable`, async ({ page }) => {
      const snapshot = { ...etoroSnapshot,
        gbp_balance: { ...etoroSnapshot.gbp_balance, exchange_rate: exchangeRate } };
      await page.route("**/api/investment/v1/etoro/**", route => {
        if (route.request().method() !== "GET") return route.abort();
        return route.fulfill({ json: route.request().url().endsWith("/session") ? {
          enabled: true, csrf: "synthetic-csrf", state: "connected",
          last_status: "success", snapshot_available: true, connection: etoroConnection(null),
        } : { state: "connected", last_status: "success", snapshot, connection: etoroConnection(null) } });
      });
      await page.goto("/investment?view=tools");
      const etoro = page.getByRole("region", { name: "eToro portfolio" });
      await expect(etoro.getByRole("region", { name: "GBP balance unavailable" }))
        .toContainText("invalid GBP balance or exchange rate");
      await expect(etoro).not.toContainText("GBP 77");
    });
  }

  test("a startup read becoming available between status requests is eventually shown", async ({ page }) => {
    let portfolioReads = 0;
    const writes = [];
    await page.route("**/api/investment/v1/etoro/**", route => {
      if (route.request().method() !== "GET") { writes.push(route.request().url()); return route.abort(); }
      if (route.request().url().endsWith("/session")) return route.fulfill({ json: {
        enabled: true, csrf: "synthetic-csrf", state: "startup_refresh_enabled",
        last_status: "success", snapshot_available: true,
        connection: { environment: "demo", account_id: 12345,
          expires_at: "2099-01-01T00:00:00Z", revoked_at: null, revision: 1 },
      } });
      portfolioReads++;
      const available = portfolioReads > 2;
      return route.fulfill({ json: { state: "startup_refresh_enabled",
        last_status: available ? "success" : "not_collected", snapshot: available ? etoroSnapshot : null,
        connection: etoroConnection() } });
    });
    await page.goto("/investment?view=tools");
    const etoro = page.getByRole("region", { name: "eToro portfolio" });
    await expect(etoro.getByRole("heading", { name: "Latest eToro read" })).toBeVisible({ timeout: 5000 });
    await expect(etoro).not.toContainText("did not complete (success)");
    expect(writes).toEqual([]);
  });

  test("a delayed startup status cannot undo a revocation", async ({ page }) => {
    let revoked = false;
    let sessionReads = 0;
    let releaseOldStatus;
    let oldStatusStarted;
    let oldStatusFinished;
    const started = new Promise(resolve => { oldStatusStarted = resolve; });
    const released = new Promise(resolve => { releaseOldStatus = resolve; });
    const finished = new Promise(resolve => { oldStatusFinished = resolve; });
    await page.route("**/api/investment/v1/etoro/**", async route => {
      const request = route.request();
      const path = new URL(request.url()).pathname.split("/").at(-1);
      if (request.method() === "POST") {
        expect(path).toBe("revoke");
        revoked = true;
        return route.fulfill({ json: { state: "revoked" } });
      }
      const state = revoked ? "revoked" : "startup_refresh_enabled";
      const lastStatus = revoked ? "startup_refresh_revoked" : "not_collected";
      if (path === "session") {
        const status = { enabled: true, csrf: "synthetic-csrf", state, last_status: lastStatus,
          snapshot_available: false, connection: { environment: "demo", account_id: 12345,
            expires_at: "2099-01-01T00:00:00Z", revoked_at: revoked ? "2026-07-01T10:00:00Z" : null, revision: 1 } };
        sessionReads++;
        if (sessionReads === 3 && !revoked) {
          oldStatusStarted();
          await released;
          await route.fulfill({ json: status });
          oldStatusFinished();
          return;
        }
        return route.fulfill({ json: status });
      }
      return route.fulfill({ json: { state, last_status: lastStatus, snapshot: null,
        connection: etoroConnection(undefined, revoked) } });
    });
    await page.goto("/investment?view=tools");
    const etoro = page.getByRole("region", { name: "eToro portfolio" });
    await started;
    const refreshWasDisabled = await etoro.getByRole("button", { name: "Refresh once now" }).isDisabled();
    page.once("dialog", dialog => dialog.accept());
    await etoro.getByRole("button", { name: "Revoke startup refresh" }).click();
    await expect(etoro).toContainText("Startup permission is revoked");
    releaseOldStatus();
    await finished;
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await expect(etoro).toContainText("Startup permission is revoked");
    await expect(etoro.getByRole("button", { name: "Refresh once now" })).toHaveCount(0);
    expect(refreshWasDisabled).toBeTruthy();
  });

  for (const remount of [false, true]) test(
    `revocation remains available during refresh${remount ? " after remount" : ""} and ignores its late result`, async ({ page }) => {
    let revoked = false;
    let startRefresh;
    let releaseRefresh;
    let finishRefresh;
    const started = new Promise(resolve => { startRefresh = resolve; });
    const released = new Promise(resolve => { releaseRefresh = resolve; });
    const finished = new Promise(resolve => { finishRefresh = resolve; });
    const writes = [];
    await page.route("**/api/investment/v1/etoro/**", async route => {
      const request = route.request();
      const path = new URL(request.url()).pathname.split("/").at(-1);
      if (request.method() === "POST") {
        writes.push(path);
        if (path === "refresh") {
          startRefresh();
          await released;
          await route.fulfill({ json: { state: "refreshed" } });
          finishRefresh();
          return;
        }
        expect(path).toBe("revoke");
        revoked = true;
        return route.fulfill({ json: { state: "revoked" } });
      }
      const state = revoked ? "revoked" : "startup_refresh_enabled";
      const lastStatus = revoked ? "startup_refresh_revoked" : "success";
      const connection = etoroConnection(undefined, revoked);
      return route.fulfill({ json: path === "session" ? {
        enabled: true, read_generation: 0, csrf: "synthetic-csrf", state, last_status: lastStatus,
        snapshot_available: true, connection,
      } : { state, last_status: lastStatus, snapshot: etoroSnapshot, connection } });
    });

    await page.goto("/investment?view=tools");
    const etoro = page.getByRole("region", { name: "eToro portfolio" });
    const details = etoro.locator("details").filter({ has: page.getByText("About eToro data and permissions") });
    await expect(etoro).toContainText("separate from your journal and its totals");
    await expect(details).not.toHaveAttribute("open");
    await expect(details.getByText(/Instrument types and individual position legs/)).toBeHidden();
    await details.locator("summary").click();
    await expect(details.getByText(/Instrument types and individual position legs/)).toBeVisible();
    page.once("dialog", dialog => dialog.accept());
    await etoro.getByRole("button", { name: "Refresh once now" }).click();
    await started;
    if (remount) {
      await page.getByRole("navigation", { name: "Settings and help navigation" }).getByRole("link", { name: "Help" }).click();
      await page.getByRole("navigation", { name: "Portfolio navigation" }).getByRole("link", { name: "Your portfolio" }).click();
      await page.getByRole("link", { name: "Tools", exact: true }).click();
      await expect(etoro.getByRole("heading", { name: "Latest eToro read" })).toBeVisible();
    }
    const revoke = etoro.getByRole("button", { name: "Revoke startup refresh" });
    await expect(revoke).toBeEnabled();
    page.once("dialog", dialog => dialog.accept());
    await revoke.click();
    await expect(etoro).toContainText("Startup permission is revoked");
    releaseRefresh();
    await finished;
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await expect(etoro).toContainText("Startup permission is revoked");
    await expect(etoro).toContainText("Refresh is still unverified");
    await expect(etoro.getByRole("button", { name: "Refresh once now" })).toHaveCount(0);
    await expect(etoro.getByRole("button", { name: "Connect and retrieve once" })).toHaveCount(0);
    expect(writes).toEqual(["refresh", "revoke"]);
  });

  for (const { connected, failRevoke } of [
    { connected: true, failRevoke: false },
    { connected: false, failRevoke: false },
    { connected: true, failRevoke: true },
  ]) test(
    `stopping a connection read ignores a late preview (${connected ? "connected" : "new connection"}${failRevoke ? ", revoke failed" : ""})`, async ({ page }) => {
    let revoked = false;
    let startRead;
    let releaseRead;
    let finishRead;
    const started = new Promise(resolve => { startRead = resolve; });
    const released = new Promise(resolve => { releaseRead = resolve; });
    const finished = new Promise(resolve => { finishRead = resolve; });
    const writes = [];
    await page.route("**/api/investment/v1/etoro/**", async route => {
      const request = route.request();
      const path = new URL(request.url()).pathname.split("/").at(-1);
      if (request.method() === "POST") {
        writes.push(path);
        if (path === "connect") {
          startRead();
          await released;
          await route.fulfill({ json: { preview_id: "late-preview",
            expires_at: "2099-01-01T00:00:00Z", snapshot: etoroSnapshot } });
          finishRead();
          return;
        }
        expect(path).toBe("revoke");
        if (failRevoke) return route.fulfill({ status: 403, json: { error: "authorization_required" } });
        revoked = true;
        return route.fulfill({ json: { state: connected ? "revoked" : "not_connected" } });
      }
      const state = connected ? revoked ? "revoked" : "startup_refresh_enabled" : "not_connected";
      const lastStatus = connected ? revoked ? "startup_refresh_revoked" : "success" : "not_collected";
      const connection = connected ? etoroConnection(undefined, revoked) : null;
      return route.fulfill({ json: path === "session" ? {
        enabled: true, read_generation: 0, csrf: "synthetic-csrf", state, last_status: lastStatus,
        snapshot_available: connected, connection,
      } : { state, last_status: lastStatus, snapshot: connected ? etoroSnapshot : null, connection } });
    });
    await page.goto("/investment?view=tools");
    const etoro = page.getByRole("region", { name: "eToro portfolio" });
    await etoro.getByText(connected ? "Update eToro connection" : "Connect eToro", { exact: true }).click();
    await etoro.getByLabel("eToro environment").selectOption("demo");
    await etoro.getByLabel("I verified this key has Read-only permission.").check();
    await etoro.getByLabel("I verified my account is eligible for this API use.").check();
    await etoro.getByLabel(/I verified rights for local display/).check();
    await etoro.getByRole("button", { name: "Connect and retrieve once" }).click();
    await started;
    const revoke = etoro.getByRole("button", { name: connected ? "Revoke startup refresh" : "Stop current read" });
    await expect(revoke).toBeEnabled();
    page.once("dialog", dialog => dialog.accept());
    await revoke.click();
    const expectedStatus = failRevoke ? "Revocation could not be verified"
      : connected ? "Startup permission is revoked" : "Not connected";
    await expect(etoro).toContainText(expectedStatus);
    releaseRead();
    await finished;
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await expect(etoro).toContainText(expectedStatus);
    await expect(etoro).toContainText("The connection read is still unverified");
    await expect(etoro.getByRole("button", { name: "Confirm connection and account" })).toHaveCount(0);
    expect(writes).toEqual(["connect", "revoke"]);
  });

  test("remounting during revocation keeps its outcome unverified", async ({ page }) => {
    let revoked = false;
    let startRevoke;
    let releaseRevoke;
    let finishRevoke;
    const started = new Promise(resolve => { startRevoke = resolve; });
    const released = new Promise(resolve => { releaseRevoke = resolve; });
    const finished = new Promise(resolve => { finishRevoke = resolve; });
    await page.route("**/api/investment/v1/etoro/**", async route => {
      const request = route.request();
      const path = new URL(request.url()).pathname.split("/").at(-1);
      if (request.method() === "POST") {
        expect(path).toBe("revoke");
        startRevoke();
        await released;
        revoked = true;
        await route.fulfill({ json: { state: "revoked" } });
        finishRevoke();
        return;
      }
      const state = revoked ? "revoked" : "startup_refresh_enabled";
      const lastStatus = revoked ? "startup_refresh_revoked" : "success";
      const connection = etoroConnection(undefined, revoked);
      return route.fulfill({ json: path === "session" ? {
        enabled: true, csrf: "synthetic-csrf", state, last_status: lastStatus,
        snapshot_available: true, connection,
      } : { state, last_status: lastStatus, snapshot: etoroSnapshot, connection } });
    });
    await page.goto("/investment?view=tools");
    const etoro = page.getByRole("region", { name: "eToro portfolio" });
    page.once("dialog", dialog => dialog.accept());
    await etoro.getByRole("button", { name: "Revoke startup refresh" }).click();
    await started;
    await page.getByRole("navigation", { name: "Settings and help navigation" })
      .getByRole("link", { name: "Help" }).click();
    await page.getByRole("navigation", { name: "Portfolio navigation" })
      .getByRole("link", { name: "Your portfolio" }).click();
    await page.getByRole("link", { name: "Tools", exact: true }).click();
    await expect(etoro).toContainText("Revocation or a read is still unverified");
    await expect(etoro.getByRole("button", { name: "Refresh once now" })).toBeDisabled();
    releaseRevoke();
    await finished;
    await expect(etoro).toContainText("Revocation or a read is still unverified");
  });

  test("connecting requires explicit consent and account confirmation", async ({ page }) => {
    let connected = false;
    let revoked = false;
    const writes = [];
    await page.route("**/api/investment/v1/etoro/**", route => {
      const request = route.request();
      const path = new URL(request.url()).pathname.split("/").at(-1);
      if (request.method() === "GET") return route.fulfill({ json: path === "session" ? {
        enabled: true, read_generation: 0, csrf: "synthetic-csrf", state: revoked ? "revoked" : connected ? "startup_refresh_enabled" : "not_connected",
        last_status: revoked ? "startup_refresh_revoked" : connected ? "success" : "not_collected", snapshot_available: connected,
        connection: connected ? etoroConnection(undefined, revoked) : null,
      } : { state: revoked ? "revoked" : connected ? "startup_refresh_enabled" : "not_connected",
        last_status: revoked ? "startup_refresh_revoked" : connected ? "success" : "not_collected",
        snapshot: connected ? etoroSnapshot : null, connection: connected ? etoroConnection(undefined, revoked) : null } });
      writes.push({ path, body: request.postDataJSON(), csrf: request.headers()["x-csrf-token"] });
      if (path === "connect") return route.fulfill({ json: { preview_id: "synthetic-preview",
        expires_at: "2099-01-01T00:00:00Z", snapshot: etoroSnapshot } });
      if (path === "confirm") { connected = true; return route.fulfill({ json: { state: "startup_refresh_enabled" } }); }
      if (path === "revoke") { revoked = true; return route.fulfill({ json: { state: "revoked" } }); }
      return route.abort();
    });
    await page.goto("/investment?view=tools");
    const etoro = page.getByRole("region", { name: "eToro portfolio" });
    await expect(etoro).toContainText("Not connected");
    await etoro.getByText("Connect eToro", { exact: true }).click();
    await etoro.getByLabel("eToro environment").selectOption("demo");
    await etoro.getByLabel("I verified this key has Read-only permission.").check();
    await etoro.getByLabel("I verified my account is eligible for this API use.").check();
    await etoro.getByLabel(/I verified rights for local display/).check();
    expect(writes).toEqual([]);
    await etoro.getByRole("button", { name: "Connect and retrieve once" }).click();
    const confirm = etoro.getByRole("button", { name: "Confirm connection and account" });
    await expect(confirm).toBeDisabled();
    await etoro.getByLabel("I verified account ID 12345 is the eToro account I selected.").check();
    await etoro.getByLabel("Allow one automatic read at each server startup while permission is active.").check();
    await etoro.getByLabel("Permission expires in").selectOption("7");
    await confirm.click();
    await expect(etoro.getByRole("heading", { name: "Latest eToro read" })).toBeVisible();
    expect(writes.map(item => item.path)).toEqual(["connect", "confirm"]);
    expect(writes[0].csrf).toBe("synthetic-csrf");
    expect(writes[0].body).toMatchObject({ environment: "demo", read_scope_verified: true,
      trade_capable_key_risk_accepted: false, eligibility_verified: true, retention_rights_verified: true });
    expect(writes[1].body).toEqual({ preview_id: "synthetic-preview", account_binding_confirmed: true,
      startup_refresh: true, refresh_days: 7 });
    page.once("dialog", dialog => dialog.accept());
    await etoro.getByRole("button", { name: "Revoke startup refresh" }).click();
    await expect(etoro).toContainText("Startup permission is revoked");
    await expect(etoro).toContainText("The displayed source snapshot is not refreshed");
    await expect(etoro.getByRole("button", { name: "Refresh once now" })).toHaveCount(0);
    expect(writes.map(item => item.path)).toEqual(["connect", "confirm", "revoke"]);
  });

  test("discarding a preview resets startup consent before using a broader key", async ({ page }) => {
    let connected = false;
    const writes = [];
    await page.route("**/api/investment/v1/etoro/**", route => {
      const request = route.request();
      const path = new URL(request.url()).pathname.split("/").at(-1);
      if (request.method() === "GET") return route.fulfill({ json: path === "session" ? {
        enabled: true, read_generation: 0, csrf: "synthetic-csrf", state: connected ? "connected" : "not_connected",
        last_status: connected ? "success" : "not_collected", snapshot_available: connected,
        connection: connected ? etoroConnection(null) : null,
      } : { state: connected ? "connected" : "not_connected",
        last_status: connected ? "success" : "not_collected", snapshot: connected ? etoroSnapshot : null,
        connection: connected ? etoroConnection(null) : null } });
      writes.push({ path, body: request.postDataJSON() });
      if (path === "connect") return route.fulfill({ json: { preview_id: "synthetic-preview",
        expires_at: "2099-01-01T00:00:00Z", snapshot: etoroSnapshot } });
      if (path === "discard") return route.fulfill({ json: { discarded: true } });
      if (path === "confirm") { connected = true; return route.fulfill({ json: { state: "connected" } }); }
      return route.abort();
    });
    await page.goto("/investment?view=tools");
    const etoro = page.getByRole("region", { name: "eToro portfolio" });
    await etoro.getByText("Connect eToro", { exact: true }).click();
    await etoro.getByLabel("eToro environment").selectOption("demo");
    await etoro.getByLabel("I verified this key has Read-only permission.").check();
    await etoro.getByLabel("I verified my account is eligible for this API use.").check();
    await etoro.getByLabel(/I verified rights for local display/).check();
    await etoro.getByRole("button", { name: "Connect and retrieve once" }).click();
    await etoro.getByLabel("Allow one automatic read at each server startup while permission is active.").check();
    await etoro.getByRole("button", { name: "Discard preview" }).click();
    await etoro.getByText("Connect eToro", { exact: true }).click();
    await etoro.getByLabel(/I accept the risk if this credential/).check();
    await etoro.getByRole("button", { name: "Connect and retrieve once" }).click();
    const startup = etoro.getByLabel("Allow one automatic read at each server startup while permission is active.");
    await expect(startup).toBeDisabled();
    await expect(startup).not.toBeChecked();
    await etoro.getByLabel("I verified account ID 12345 is the eToro account I selected.").check();
    await etoro.getByRole("button", { name: "Confirm connection and account" }).click();
    await expect(etoro.getByRole("heading", { name: "Latest eToro read" })).toBeVisible();
    expect(writes.map(item => item.path)).toEqual(["connect", "discard", "connect", "confirm"]);
    expect(writes[2].body).toMatchObject({ read_scope_verified: false, trade_capable_key_risk_accepted: true });
    expect(writes[3].body).toMatchObject({ startup_refresh: false, refresh_days: null });
  });

  test("revocation does not resolve an uncertain connection confirmation", async ({ page }) => {
    let revoked = false;
    const writes = [];
    await page.route("**/api/investment/v1/etoro/**", route => {
      const request = route.request();
      const path = new URL(request.url()).pathname.split("/").at(-1);
      if (request.method() === "GET") return route.fulfill({ json: path === "session" ? {
        enabled: true, read_generation: 0, csrf: "synthetic-csrf", state: revoked ? "revoked" : "startup_refresh_enabled",
        last_status: revoked ? "startup_refresh_revoked" : "success", snapshot_available: true,
        connection: etoroConnection(undefined, revoked),
      } : { state: revoked ? "revoked" : "startup_refresh_enabled",
        last_status: revoked ? "startup_refresh_revoked" : "success", snapshot: etoroSnapshot,
        connection: etoroConnection(undefined, revoked) } });
      writes.push(path);
      if (path === "connect") return route.fulfill({ json: { preview_id: "synthetic-preview",
        expires_at: "2099-01-01T00:00:00Z", snapshot: etoroSnapshot } });
      if (path === "revoke") { revoked = true; return route.fulfill({ json: { state: "revoked" } }); }
      return route.abort();
    });
    await page.goto("/investment?view=tools");
    const etoro = page.getByRole("region", { name: "eToro portfolio" });
    await etoro.getByText("Update eToro connection", { exact: true }).click();
    await etoro.getByLabel("eToro environment").selectOption("demo");
    await etoro.getByLabel("I verified this key has Read-only permission.").check();
    await etoro.getByLabel("I verified my account is eligible for this API use.").check();
    await etoro.getByLabel(/I verified rights for local display/).check();
    await etoro.getByRole("button", { name: "Connect and retrieve once" }).click();
    await etoro.getByLabel("I verified account ID 12345 is the eToro account I selected.").check();
    await etoro.getByRole("button", { name: "Confirm connection and account" }).click();
    await expect(etoro).toContainText("Confirmation is still unverified");
    page.once("dialog", dialog => dialog.accept());
    await etoro.getByRole("button", { name: "Revoke startup refresh" }).click();
    await expect(etoro).toContainText("Startup permission is revoked");
    await expect(etoro).toContainText("Confirmation is still unverified");
    await expect(etoro.getByRole("button", { name: "Connect and retrieve once" })).toHaveCount(0);
    expect(writes).toEqual(["connect", "confirm", "revoke"]);
  });

  test("unchanged status does not resolve a confirmation that may still be in flight", async ({ page }) => {
    let connected = false;
    const writes = [];
    await page.route("**/api/investment/v1/etoro/**", route => {
      const request = route.request();
      const path = new URL(request.url()).pathname.split("/").at(-1);
      if (request.method() === "GET") return route.fulfill({ json: path === "session" ? {
        enabled: true, read_generation: 0, csrf: "synthetic-csrf", state: connected ? "connected" : "not_connected",
        last_status: connected ? "success" : "not_collected", snapshot_available: connected,
        connection: connected ? { environment: "demo", account_id: 12345,
          expires_at: null, revoked_at: null, revision: 1 } : null,
      } : { state: connected ? "connected" : "not_connected",
        last_status: connected ? "success" : "not_collected", snapshot: connected ? etoroSnapshot : null,
        connection: connected ? etoroConnection(null) : null } });
      writes.push(path);
      if (path === "connect") return route.fulfill({ json: { preview_id: "synthetic-preview",
        expires_at: "2099-01-01T00:00:00Z", snapshot: etoroSnapshot } });
      if (path === "confirm") return route.abort();
      return route.abort();
    });
    await page.goto("/investment?view=tools");
    const etoro = page.getByRole("region", { name: "eToro portfolio" });
    await etoro.getByText("Connect eToro", { exact: true }).click();
    await etoro.getByLabel("eToro environment").selectOption("demo");
    await etoro.getByLabel(/I accept the risk if this credential/).check();
    await etoro.getByLabel("I verified my account is eligible for this API use.").check();
    await etoro.getByLabel(/I verified rights for local display/).check();
    await etoro.getByRole("button", { name: "Connect and retrieve once" }).click();
    await etoro.getByLabel("I verified account ID 12345 is the eToro account I selected.").check();
    await etoro.getByRole("button", { name: "Confirm connection and account" }).click();
    await expect(etoro).toContainText("may already be saved");
    await etoro.getByRole("button", { name: "Retry status" }).click();
    await expect(etoro).toContainText("Confirmation is still unverified");
    await expect(etoro.getByRole("button", { name: "Connect and retrieve once" })).toHaveCount(0);
    expect(writes).toEqual(["connect", "confirm"]);
    connected = true;
    await etoro.getByRole("button", { name: "Retry status" }).click();
    await expect(etoro.getByRole("heading", { name: "Latest eToro read" })).toBeVisible();
    await expect(etoro).toContainText("Confirmation is still unverified");
    await expect(etoro.getByRole("button", { name: "Connect and retrieve once" })).toHaveCount(0);
    expect(writes).toEqual(["connect", "confirm"]);
  });

  for (const failure of ["lost confirmation response", "server error after confirmation",
    "invalid confirmation response", "unavailable status after confirmation"]) {
    test(`one-time broader-key consent handles ${failure} without repeating a read`, async ({ page }) => {
      let connected = false;
      let statusUnavailable = false;
      let portfolioUnavailable = false;
      const writes = [];
      await page.route("**/api/investment/v1/etoro/**", route => {
        const request = route.request();
        const path = new URL(request.url()).pathname.split("/").at(-1);
        if (request.method() === "GET") {
          if (statusUnavailable || (portfolioUnavailable && path === "portfolio"))
            return route.fulfill({ status: 503, json: { error: "unavailable" } });
          return route.fulfill({ json: path === "session" ? {
            enabled: true, read_generation: 0, csrf: "synthetic-csrf", state: connected ? "connected" : "not_connected",
            last_status: connected ? "success" : "not_collected", snapshot_available: connected,
            connection: connected ? { environment: "demo", account_id: 12345,
              expires_at: null, revoked_at: null, revision: 1 } : null,
          } : { state: connected ? "connected" : "not_connected",
            last_status: connected ? "success" : "not_collected", snapshot: connected ? etoroSnapshot : null,
            connection: connected ? etoroConnection(null) : null } });
        }
        writes.push({ path, body: request.postDataJSON() });
        if (path === "connect") return route.fulfill({ json: { preview_id: "synthetic-preview",
          expires_at: "2099-01-01T00:00:00Z", snapshot: etoroSnapshot } });
        if (path === "confirm") {
          connected = true;
          if (failure === "lost confirmation response") return route.abort();
          if (failure === "server error after confirmation") {
            portfolioUnavailable = true;
            return route.fulfill({ status: 503, json: { error: "unavailable" } });
          }
          if (failure === "invalid confirmation response") return route.fulfill({ json: { state: "not_connected" } });
          statusUnavailable = true;
          return route.fulfill({ json: { state: "connected" } });
        }
        return route.abort();
      });
      await page.goto("/investment?view=tools");
      const etoro = page.getByRole("region", { name: "eToro portfolio" });
      await etoro.getByText("Connect eToro", { exact: true }).click();
      await etoro.getByLabel("eToro environment").selectOption("demo");
      await etoro.getByLabel(/I accept the risk if this credential/).check();
      await etoro.getByLabel("I verified my account is eligible for this API use.").check();
      await etoro.getByLabel(/I verified rights for local display/).check();
      await etoro.getByRole("button", { name: "Connect and retrieve once" }).click();
      await expect(etoro.getByLabel("Allow one automatic read at each server startup while permission is active.")).toBeDisabled();
      await expect(etoro).toContainText("Automatic refresh is unavailable unless Read-only key scope is verified");
      await etoro.getByLabel("I verified account ID 12345 is the eToro account I selected.").check();
      await etoro.getByRole("button", { name: "Confirm connection and account" }).click();
      await expect(etoro.locator("[role=alert]").first()).toContainText(failure !== "unavailable status after confirmation"
        ? "may already be saved" : "The connection was saved");
      await expect(etoro.getByRole("button", { name: "Connect and retrieve once" })).toHaveCount(0);
      expect(writes.map(item => item.path)).toEqual(["connect", "confirm"]);
      expect(writes[1].body).toEqual({ preview_id: "synthetic-preview", account_binding_confirmed: true,
        startup_refresh: false, refresh_days: null });
      statusUnavailable = false;
      if (portfolioUnavailable) {
        await etoro.getByRole("button", { name: "Retry status" }).click();
        await expect(etoro).toContainText("Confirmation is still unverified");
        await expect(etoro.getByRole("button", { name: "Connect and retrieve once" })).toHaveCount(0);
        expect(writes.map(item => item.path)).toEqual(["connect", "confirm"]);
        portfolioUnavailable = false;
      }
      await etoro.getByRole("button", { name: "Retry status" }).click();
      await expect(etoro.getByRole("heading", { name: "Latest eToro read" })).toBeVisible();
      expect(writes.map(item => item.path)).toEqual(["connect", "confirm"]);
    });
  }
});

test("monitoring shows synthetic findings and invalidates them on failed refresh", async ({ page }) => {
  let unavailable = false;
  await page.route("**/api/investment/v1/monitoring", async route => {
    if (unavailable) return route.fulfill({ status: 503, json: { error: "unavailable" } });
    return route.continue();
  });

  await page.goto("/investment?view=tools");
  await page.locator("#investment-monitoring > summary").click();
  const section = page.getByRole("region", { name: "Portfolio monitoring", exact: true });
  await expect(section.getByText(/Synthetic monitoring demonstration/)).toBeVisible();
  await expect(section.getByRole("link", { name: "Review checks and compare account figures" })).toHaveAttribute("href", "/investment/reviews");
  await expect(section.getByText("Provider connections are not verified. These checks use saved evidence only.")).toBeVisible();
  await expect(section.getByRole("list", { name: "Provider evidence status" })).toHaveCount(0);
  await section.getByText("Source status", { exact: true }).click();
  await expect(section.getByRole("list", { name: "Provider evidence status" })).toContainText("freshness");
  await expect(section.getByRole("list", { name: "Provider evidence status" })).toContainText("coverage");
  await expect(section.getByText(/Blocked Unvalidated/)).toBeVisible();
  await expect(section.getByText(/Nonzero replayed cash-observation differences remain/)).toBeVisible();
  await expect(section.getByRole("checkbox")).not.toBeChecked();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBeTruthy();
  unavailable = true;
  await section.getByRole("button", { name: "Refresh checks", exact: true }).click();
  await expect(section.getByRole("alert")).toContainText("unavailable");
  await expect(section.getByText(/Nonzero replayed cash-observation differences remain/)).toHaveCount(0);
});

test("monitoring refuses mismatched revisions and malformed responses", async ({ page }) => {
  const report = await (await page.request.get("/api/investment/v1/monitoring")).json();
  let malformed = false;
  await page.route("**/api/investment/v1/monitoring", route => route.fulfill({
    json: malformed ? { ...report, dynamic_pct: "NaN" } : { ...report, portfolio_revision: report.portfolio_revision + 1 },
  }));
  await page.goto("/investment?view=tools");
  await page.locator("#investment-monitoring > summary").click();
  const section = page.getByRole("region", { name: "Portfolio monitoring", exact: true });
  await expect(section.getByRole("alert")).toContainText("revision changed");
  await expect(section.getByText(/Synthetic monitoring demonstration/)).toHaveCount(0);
  malformed = true;
  await section.getByRole("button", { name: "Try again", exact: true }).click();
  await expect(section.getByRole("alert")).toContainText("unavailable");
  await expect(section.getByText(/Synthetic monitoring demonstration/)).toHaveCount(0);
  await expect(section.getByRole("link", { name: "Review checks and compare account figures" }))
    .toHaveAttribute("href", "/investment/reviews");
});

test("the overview separates account review, history and optional tools", async ({ page }) => {
  const snapshot = await (await page.request.get("/api/investment/v1/snapshot")).json();
  snapshot.source_mode = "recorded";
  let recoverySessionRequests = 0;
  await page.route("**/api/investment/v1/backup/session", route => {
    recoverySessionRequests++;
    return route.fulfill({ json: {
      enabled: false, csrf: null, archive_limit_bytes: 33558528, database_location: null,
    } });
  });
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: snapshot }));
  await page.goto("/investment");
  await expect(page.locator(".record-toolbar").getByRole("link", { name: "Help" })).toHaveCount(0);
  await expect(page.getByRole("navigation", { name: "Settings and help navigation" })
    .getByRole("link", { name: "Help" })).toBeVisible();
  await expect(page.getByRole("region", { name: "What needs attention" })).toBeVisible();
  const views = page.getByRole("navigation", { name: "Portfolio views" });
  await expect(views.getByRole("link", { name: "Overview" })).toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("region", { name: "Account freshness and next actions" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Changes between recorded values" })).toHaveCount(0);
  await expect(page.getByRole("region", { name: "eToro portfolio" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Add a record", exact: true })).toBeVisible();
  expect(recoverySessionRequests).toBe(0);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBeTruthy();
  const portfolioValue = page.getByRole("region", { name: "Investment totals" }).locator("strong");
  await expect(portfolioValue).toHaveCSS("white-space", "nowrap");
  expect(await portfolioValue.evaluate(element => element.scrollWidth <= element.clientWidth)).toBeTruthy();

  await page.goto("/investment#investment-account-summary");
  await expect(page).toHaveURL(/\/investment\?view=history#investment-account-summary$/);
  await expect(page.getByRole("region", { name: "Recorded investments" })).toBeVisible();
  await expect(page.getByText(/Latest journal entries, not live balances/)).toBeVisible();
  await expect(page.locator(".portfolio-summary")).not.toContainText("Accounts and cash");
  const priceDates = page.locator(".portfolio-summary details").filter({
    has: page.getByText("Price dates and source availability", { exact: true }),
  });
  await priceDates.locator("summary").click();
  await expect(priceDates.getByRole("table")).toContainText("Price applies at");
  await expect(priceDates.getByRole("table")).toContainText("Source available from");
  await expect(page.locator("#investment-roles")).not.toHaveAttribute("open", "");
  await page.locator("#investment-roles > summary").focus();
  await page.keyboard.press("Enter");
  await expect(page.locator("#investment-roles")).toHaveAttribute("open", "");
  await expect(page.getByRole("heading", { name: "Management roles" })).toBeVisible();
  await page.goto("/investment#investment-monitoring");
  await expect(page).toHaveURL(/\/investment\?view=tools#investment-monitoring$/);
  await expect(page.locator("#investment-monitoring")).toHaveAttribute("open", "");
  const recoveryTools = page.locator(".recovery-tools");
  await expect(recoveryTools.locator(".recovery-panel")).toHaveCount(0);
  expect(recoverySessionRequests).toBe(0);
  await recoveryTools.locator("summary").click();
  await expect(recoveryTools.getByRole("region", { name: "Verified backup and recovery" })).toBeVisible();
  await expect.poll(() => recoverySessionRequests).toBe(1);
  await page.goto("/investment#investment-records");
  await expect(page).toHaveURL(/\/investment\?view=history#investment-records$/);
  await expect(page.locator("#investment-records")).toHaveAttribute("open", "");
  await page.setViewportSize({ width: 850, height: 844 });
  expect(await page.getByRole("region", { name: "Recorded contribution and gain" }).evaluate(element =>
    getComputedStyle(element).gridTemplateColumns.split(" ").length)).toBe(2);
  expect(await page.getByRole("region", { name: "Recorded contribution and gain" }).locator("strong").evaluateAll(elements =>
    elements.filter(element => element.textContent.length <= 12).every(element => {
      const range = document.createRange();
      range.selectNodeContents(element);
      return range.getClientRects().length === 1;
    }))).toBe(true);
});

test("mobile navigation keeps every destination visible without sideways scrolling", async ({ page }) => {
  let workspace = "demo";
  await page.route("**/api/v1/settings", route => route.fulfill({ json: { workspace } }));
  for (const width of [320, 390]) {
    await page.setViewportSize({ width, height: 844 });
    for (workspace of ["demo", "investment"]) {
      await page.goto("/investment");
      const sidebar = page.locator(".sidebar");
      const links = sidebar.locator("nav a");
      await expect(links).toHaveCount(workspace === "investment" ? 5 : 4);
      await expect(page.getByRole("region", { name: "Investment totals" })).toBeVisible();
      expect(await links.evaluateAll(elements => elements.every(element => {
        const bounds = element.getBoundingClientRect();
        return bounds.left >= 0 && bounds.right <= window.innerWidth;
      }))).toBe(true);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await page.evaluate(() => window.scrollTo(0, 500));
      expect(await sidebar.evaluate(element => element.getBoundingClientRect().bottom)).toBeLessThanOrEqual(0);
    }
  }
});

test("monitoring observation staging uses structured fields and readable reconciliation", async ({ page }) => {
  let submitted;
  await page.route("**/api/investment/v1/monitoring/reviews", route =>
    route.fulfill({ json: { enabled: false } }));
  await page.route("**/api/investment/v1/monitoring-workflow/session", route =>
    route.fulfill({ json: { enabled: true, csrf: "synthetic-csrf", cache_enabled: true,
      accounts: [{ account_id: "local-broker", name: "My brokerage", provider: "manual" }],
      products: [{ product_id: "fund", name: "Synthetic fund" }] } }));
  await page.route("**/api/investment/v1/monitoring-workflow/observation/preview", route => {
    submitted = route.request().postDataJSON();
    return route.fulfill({ json: {
      preview_id: "synthetic-preview", request_id: submitted.request_id,
      journal_revision: 4, cache_revision: 2, observation: submitted.observation,
      mapping_observed_at: "2026-01-03T11:00:00Z",
      reconciliation: { status: "attention", revision: 4, generated_at: "2026-01-03T12:06:00Z",
        accounts: [{
          source_account: "local-broker", account_id: "local-broker",
          observed_at: "2026-01-03T12:00:00Z", available_at: "2026-01-03T12:05:00Z",
          freshness: "fresh", comparison_unavailable_reason: null,
          cash_coverage: "complete", holdings_coverage: "complete",
          coverage_evidence: "Synthetic evidence",
          cash: { observed: "10", recorded: "10", difference: "0", status: "match" },
          holdings: [{ source_product: "Synthetic fund", product_id: "fund",
            mapping_evidence: "Synthetic mapping", observed: "2", recorded: "2",
            difference: "0", status: "match" }],
          missing_recorded_products: [],
        }],
        missing_recorded_accounts: ["bank"], limitations: ["Synthetic limitation."] },
      expires_at: "2026-01-03T12:10:00Z", investment_journal_changed: false,
    } });
  });
  await page.goto("/investment/reviews");
  await expect(page.getByRole("navigation", { name: "Monitoring workflow sections" })).toBeVisible();
  await expect(page.getByLabel("Holdings JSON list (source_product and quantity)")).toHaveCount(0);
  await expect(page.getByRole("combobox").first()).toContainText("My brokerage");
  await page.getByLabel("Source snapshot reference").fill("observation-1");
  await page.getByLabel("When did the figures apply? (optional)").fill("2026-01-03T12:00");
  await page.getByLabel("When was the source available?").fill("2026-01-03T12:05");
  await page.getByLabel("When was this account or investment match established?").fill("2026-01-03T11:00");
  await page.getByLabel("Source and coverage evidence").fill("Synthetic operator statement");
  await page.getByRole("button", { name: "Add observed holding" }).click();
  await page.getByLabel("Investment name in source").fill("Synthetic fund");
  await page.getByLabel("Number of units in source").fill("2");
  await page.getByRole("button", { name: "Add investment match" }).click();
  await page.getByLabel("Investment name to match").fill("Synthetic fund");
  await page.getByLabel("Recorded investment").selectOption("fund");
  await page.getByLabel("Source supporting this match").fill("Synthetic mapping note");
  await page.getByRole("button", { name: "Preview comparison" }).click();
  await expect.poll(() => submitted?.observation?.holdings?.length).toBe(1);
  expect(submitted.observation.holdings).toEqual([{ source_product: "Synthetic fund", quantity: "2" }]);
  expect(submitted.observation.observed_at).toMatch(/Z$/);
  expect(submitted.observation.available_at).toMatch(/Z$/);
  expect(submitted.mapping_observed_at).toMatch(/Z$/);
  expect(submitted.observation.product_mappings).toEqual([{
    source_product: "Synthetic fund", product_id: "fund", evidence: "Synthetic mapping note",
  }]);
  const preview = page.getByRole("region", { name: "Observation reconciliation preview" });
  await expect(preview).toContainText("Account and investment match known from");
  const reconciliation = preview.getByRole("region", { name: "Reconciliation results" });
  await expect(reconciliation).toContainText("Attention");
  await expect(reconciliation).toContainText("My brokerage (local-broker)");
  await expect(reconciliation).toContainText("Synthetic fund (fund)");
  await expect(reconciliation).toContainText("Comparison generated");
  await expect(reconciliation).toContainText("2026-01-03T12:06:00Z");
  await expect(reconciliation).toContainText("Match evidence: Synthetic mapping");
  await expect(reconciliation).toContainText("bank");
  await expect(reconciliation).toContainText("Snapshot applies at");
  await expect(reconciliation).toContainText("Source available from");
  await expect(preview).toContainText("Account and investment match known from");
  await expect(reconciliation).toContainText("A match does not verify today's account balance or money available to spend.");
  await preview.getByRole("button", { name: "Edit comparison" }).click();
  await expect(preview).toHaveCount(0);
  await expect(page.getByLabel("Source snapshot reference")).toHaveValue("observation-1");
  await expect(page.getByRole("region", { name: "Reconciliation results" })).toHaveCount(0);
});

test("review inbox separates setup states and keeps history out of active checks", async ({ page }) => {
  let inbox = { enabled: false };
  let session = { enabled: false, csrf: null, cache_enabled: false, accounts: [], products: [] };
  await page.route("**/api/investment/v1/monitoring/reviews", route => route.fulfill({ json: inbox }));
  await page.route("**/api/investment/v1/monitoring-workflow/session", route => route.fulfill({ json: session }));
  await page.goto("/investment/reviews");
  await expect(page.getByText("Review inbox: Not set up")).toBeVisible();
  await expect(page.getByText("Compare account figures: Not set up")).toBeVisible();
  await expect(page.getByRole("link", { name: "About optional setup" })).toHaveCount(1);

  inbox = {
    enabled: true, portfolio_id: "synthetic", profile_hash: "a".repeat(64), revision: 3,
    sync_current: true, source_available: true, limitations: [],
    cases: [
      { case_id: "active", finding_id: "active-finding", finding: {
        rule: "synthetic_active", subject: "Active finding", status: "review", explanation: "Synthetic evidence needs review.",
        recommendation: "Check the saved evidence.", evidence_sha256: "b".repeat(64),
      }, lifecycle: "active", first_seen_at: "2026-01-03T12:00:00Z", last_seen_at: "2026-01-03T12:00:00Z",
      source_portfolio_revision: 4, source_cache_revision: 2, review_status: "pending", reviewed_at: null, note: null },
      { case_id: "historical", finding_id: "old-finding", finding: {
        rule: "synthetic_old", subject: "Historical finding", status: "review", explanation: "Old synthetic evidence.",
        recommendation: "Keep the original evidence.", evidence_sha256: "c".repeat(64),
      }, lifecycle: "superseded", first_seen_at: "2026-01-02T12:00:00Z", last_seen_at: "2026-01-02T12:00:00Z",
      source_portfolio_revision: 3, source_cache_revision: 1, review_status: "reviewed",
      reviewed_at: "2026-01-02T13:00:00Z", note: "Reviewed offline." },
    ],
  };
  await page.reload();
  await expect(page.getByText("Review inbox: Up to date")).toBeVisible();
  await expect(page.getByText("Compare account figures: Not set up")).toBeVisible();
  const history = page.locator("#review-inbox details").filter({ hasText: "History (1)" });
  await expect(history).not.toHaveAttribute("open", "");
  await expect(page.getByText("Historical finding", { exact: false })).toHaveCount(1);
  await history.locator(":scope > summary").click();
  await expect(history).toContainText("Superseded");
  await expect(history).toContainText("Reviewed offline.");
  await expect(page.getByText("Active finding", { exact: false })).toHaveCount(1);
  await page.getByRole("link", { name: "About optional setup" }).first().click();
  await expect(page).toHaveURL(/\/investment-guide#monitoring-setup$/);
  await expect(page.locator("#monitoring-setup")).toHaveAttribute("open", "");
  await expect(page.locator("#monitoring-setup")).toContainText("Ask the person managing your workspace");
});

test("investment workspace opens the portfolio without trading requests", async ({ page }) => {
  let legacyRequests = 0;
  await page.route("**/api/v1/settings", route => route.fulfill({
    json: { mode: "recorded", scenario: null, workspace: "investment" },
  }));
  await page.route("**/api/v1/overview", route => {
    legacyRequests++;
    return route.fulfill({ status: 503, json: {} });
  });
  await page.goto("/");
  await expect(page).toHaveURL(/\/investment$/);
  await expect(page.getByRole("heading", { name: "Your Portfolio", level: 1 })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Account navigation" })).toHaveCount(0);
  await expect(page.getByRole("navigation", { name: "Records navigation" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Trading demo", exact: true })).toHaveCount(0);
  expect((await page.request.get("/positions")).status()).toBe(404);
  expect(legacyRequests).toBe(0);
});

test("demo defaults to investment without a second trading product", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/investment$/);
  await page.getByRole("link", { name: "History and insights" }).click();
  await page.getByText("Optional fund labels", { exact: true }).click();
  await expect(page.getByRole("heading", { name: "Management roles" })).toBeVisible();
  await expect(page.getByText(/Synthetic role attribution is shown only to demonstrate accounting/)).toBeVisible();
  await expect(page.getByRole("heading", { name: "Allocation and drift" })).toHaveCount(0);
  await expect(page.getByRole("navigation", { name: "Legacy navigation" })).toHaveCount(0);
  expect((await page.request.get("/legacy")).status()).toBe(404);
});

test("view-only recording route offers workspace guidance without a normal recording opt-in", async ({ page }) => {
  await page.goto("/investment/record");
  await expect(page.getByRole("heading", { name: "Add a record", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Viewing only" })).toBeVisible();
  await expect(page.getByText(/Normal local launches allow recording after setup/)).toBeVisible();
  await page.getByText("Use a recorded workspace", { exact: true }).click();
  await expect(page.getByText(/restart without that option/)).toContainText("--investment-read-only");
  await expect(page.getByText(/Keep the same/)).toContainText("--workspace or --investment-database");
  await expect(page.getByText("How to enable manual recording", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Preview record" })).toHaveCount(0);
});

test("next steps reflect incomplete evidence and historical differences without writes", async ({ page }) => {
  const report = await (await page.request.get("/api/investment/v1/snapshot")).json();
  const replay = structuredClone(report);
  expect(replay.complete).toBe(true);
  expect(replay.baseline_as_of).not.toBeNull();
  expect(replay.twr_pct).toBeNull();
  const originalHolding = { ...report.holdings[0] };
  const recordingSession = {
    enabled: true, csrf: "synthetic-csrf", opened: true,
    accounts: report.accounts.map(account => ({ id: account.id, name: account.name, kind: account.kind })),
    products: [...new Map(report.holdings.map(holding => [holding.product_id, {
      id: holding.product_id, name: holding.name, sleeve: holding.sleeve,
      quantity_increment: "0.001", minimum_notional_gbp: "1",
    }])).values()],
    latest_correctable_event: null, correctable_events: [],
    current_balances: Object.fromEntries(report.accounts.map(account => [account.id, account.cash_gbp ?? "0"])),
    current_holdings: report.holdings.map(holding => ({
      account_id: holding.account_id, product_id: holding.product_id, quantity: holding.quantity,
    })),
  };
  report.source_mode = "recorded";
  report.complete = false;
  report.as_of = null;
  report.equity_gbp = null;
  report.baseline_as_of = null;
  report.since_baseline_gain_gbp = null;
  report.twr_pct = null;
  report.max_drawdown_pct = null;
  report.holdings[0] = { ...originalHolding, value_gbp: null, mark_at: null, mark_available_at: null };
  report.holdings.push(...Array.from({ length: 6 }, (_, index) => ({
    ...originalHolding,
    product_id: `missing-price-${index}`,
    name: `Synthetic missing-price holding ${index + 1}`,
    value_gbp: null,
    mark_at: null,
    mark_available_at: null,
  })));
  report.cash_observations = [];
  for (const account of report.accounts) account.cash_observation = null;
  let mutations = 0;
  page.on("request", request => { if (!["GET", "HEAD"].includes(request.method())) mutations++; });

  await page.route("**/api/v1/settings", route => route.fulfill({
    json: { mode: "recorded", scenario: null, workspace: "investment" },
  }));
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
  await page.route("**/api/investment/v1/recording/session", route =>
    route.fulfill({ json: recordingSession }));
  await page.goto("/investment");
  const steps = page.getByRole("region", { name: "What needs attention" });
  await expect(steps).toContainText("holdings have no recorded price");
  await expect(steps).toContainText(originalHolding.name);
  await expect(steps.getByRole("link", { name: "Review holdings" }))
    .toHaveAttribute("href", "/investment?view=history#investment-holdings");
  const priceLink = steps.getByRole("link", { name: `Add a checked price for ${originalHolding.name} in ${report.accounts.find(account => account.id === originalHolding.account_id).name}` });
  await expect(priceLink).toHaveAttribute("href", new RegExp(
    `record=missing-price.*account_id=${encodeURIComponent(originalHolding.account_id)}.*product_id=${encodeURIComponent(originalHolding.product_id)}`,
  ));
  await expect(steps).toContainText("Later information cannot fill an earlier valuation.");
  await expect(page.getByRole("region", { name: "Historical performance limitations" }))
    .toContainText("Performance has no complete starting value.");
  const moreMissingPrices = steps.locator("details");
  await expect(moreMissingPrices.locator("summary")).toHaveText("Show 4 more missing prices");
  await moreMissingPrices.locator("summary").click();
  await expect(moreMissingPrices.getByRole("link", { name: /Add a checked price for/ })).toHaveCount(4);
  await expect(steps).not.toContainText("No cash observations have been recorded");
  await expect(steps).not.toContainText("Backup status is unknown");
  await priceLink.click();
  await expect(page).toHaveURL(/\/investment\/record\?record=missing-price/);
  await expect(page.getByRole("status")).toContainText("Started from a missing-price reminder");
  await expect(page.getByLabel("What are you recording?")).toHaveValue("mark");
  const accountSelect = page.getByRole("combobox", { name: "Account" });
  const investmentSelect = page.getByRole("combobox", { name: "Investment" });
  await expect(accountSelect).toHaveValue(originalHolding.account_id);
  await expect(investmentSelect).toHaveValue(originalHolding.product_id);
  await page.goBack();
  await expect(steps).toBeVisible();
  recordingSession.current_holdings = [];
  await steps.getByRole("link", { name: /Add a checked price for/ }).first().click();
  await expect(page.getByRole("status")).toContainText("no longer matches a held investment");
  await expect(page.getByRole("combobox", { name: "Account" })).toHaveValue("");
  await expect(page.getByRole("combobox", { name: "Investment" })).toHaveValue("");
  await page.goBack();
  await expect(steps).toBeVisible();

  report.holdings = report.holdings.map(holding => ({
    ...holding, mark_at: report.generated_at, mark_available_at: report.generated_at,
  }));
  report.cash_observations = [
    { account_id: report.accounts[0].id, balance_gbp: "100", difference_gbp: "0.001",
      timestamp: "2026-01-01T12:00:00Z", available_at: "2026-01-01T12:00:00Z",
      source: "Synthetic statement", evidence: "Synthetic earlier difference" },
    { account_id: report.accounts[0].id, balance_gbp: "100", difference_gbp: "0",
      timestamp: "2026-01-02T12:00:00Z", available_at: "2026-01-02T12:00:00Z",
      source: "Synthetic statement", evidence: "Synthetic later match" },
  ];
  report.accounts[0].cash_observation = { ...report.cash_observations[1] };
  delete report.accounts[0].cash_observation.account_id;
  await page.getByRole("button", { name: "Refresh investment" }).click();
  await expect(steps).not.toContainText("holdings have no recorded price");
  await expect(steps).toContainText("Recorded cash observations differ from the journal");
  const cashDetails = steps.getByText("Cash evidence details", { exact: true });
  await cashDetails.click();
  await expect(steps).toContainText("later matching observations do not resolve it.");
  await steps.getByRole("link", { name: "Review cash evidence" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.locator("#investment-accounts")).toHaveAttribute("open", "");
  await expect(page.getByRole("region", { name: "Cash evidence overview" })).toBeVisible();

  report.complete = replay.complete;
  report.as_of = replay.as_of;
  report.equity_gbp = replay.equity_gbp;
  report.baseline_as_of = replay.baseline_as_of;
  report.since_baseline_gain_gbp = replay.since_baseline_gain_gbp;
  report.twr_pct = replay.twr_pct;
  report.max_drawdown_pct = replay.max_drawdown_pct;
  report.holdings = replay.holdings;
  await page.getByRole("link", { name: "Overview", exact: true }).click();
  await page.getByRole("button", { name: "Refresh investment" }).click();
  await expect(steps).not.toContainText("A complete total is unavailable");
  const performanceLimits = page.getByRole("region", { name: "Historical performance limitations" });
  await expect(performanceLimits).toContainText("Some performance figures are unavailable");
  await expect(performanceLimits).not.toContainText("Performance has no complete starting value.");
  await expect(steps).not.toContainText("Backup verified");
  await performanceLimits.getByRole("link", { name: "View performance history", exact: true }).click();
  await expect(page.locator("#investment-performance")).toHaveAttribute("open", "");
  expect(mutations).toBe(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await page.getByRole("navigation", { name: "Settings and help navigation" })
    .getByRole("link", { name: "Help" }).click();
  await page.locator("#backup-recovery > summary").click();
  await expect(page.locator("#backup-recovery")).toHaveAttribute("open", "");
  await expect(page.getByText(/Backups are not automatic/)).toBeVisible();
  await page.getByText("Steps for a source checkout", { exact: true }).click();
  await expect(page.locator("#backup-recovery pre"))
    .toContainText("--database .\\local-investment\\investment.sqlite3");
  await expect(page.getByText(/assumes .*backups.* already exists/)).toBeVisible();
});

test("cash evidence overview labels replay history and surfaces correction limits", async ({ page }) => {
  const report = await (await page.request.get("/api/investment/v1/snapshot")).json();
  const correctionWarning =
    "Historical correction replay may have changed past cash-observation differences. Preserve prior reports and review the original observations and correction evidence.";
  report.warnings.push(correctionWarning);
  report.recorded_corrections = [{ id: "synthetic-correction", original_event_id: "original",
    replacement_event_id: "replacement", replaced_type: "mark", account_id: null,
    role: "unassigned", role_evidence: null, original_amount_gbp: null,
    replacement_amount_gbp: null, timestamp: "2026-01-01T12:00:00Z",
    available_at: "2026-01-01T12:00:00Z", evidence: "Synthetic correction evidence" }];
  report.cash_observations = report.cash_observations.map(observation => ({
    ...observation, difference_gbp: "0",
  }));
  for (const account of report.accounts) {
    const latest = report.cash_observations.filter(observation =>
      observation.account_id === account.id).at(-1);
    account.cash_observation = latest ? { ...latest } : null;
    if (account.cash_observation) delete account.cash_observation.account_id;
  }
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
  await page.goto("/investment");
  await expect(page.getByRole("region", { name: "What needs attention" }))
    .toContainText("A correction may have changed earlier reported cash differences");
  await page.getByRole("link", { name: "Review correction history" }).click();
  await expect(page.locator("#investment-records")).toHaveAttribute("open", "");
  await expect(page.getByRole("region", { name: "Evidence-backed corrections" })).toContainText("Synthetic correction evidence");
  await page.getByText("Cash observations and savings details", { exact: true }).click();
  const overview = page.getByRole("region", { name: "Cash evidence overview" });
  await expect(overview).toContainText("0 differing observations in current replayed history");
  await expect(overview).toContainText(correctionWarning);
});

test("demo attention panel stays focused while optional details remain accessible", async ({ page }) => {
  await page.goto("/investment");
  const steps = page.getByRole("region", { name: "What needs attention" });
  await expect(page.locator(".demo-band")).toContainText("SYNTHETIC DEMO");
  await expect(steps).toBeVisible();
  await expect(steps).not.toContainText("Backup status is unknown");
  await expect(steps).not.toContainText("Backup verified");
  const contrast = await steps.evaluate(element => {
    const luminance = value => {
      const channels = value.match(/\d+/g).slice(0, 3).map(channel => {
        const n = Number(channel) / 255;
        return n <= 0.04045 ? n / 12.92 : ((n + 0.055) / 1.055) ** 2.4;
      });
      return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
    };
    const background = luminance(getComputedStyle(element).backgroundColor);
    const color = luminance(getComputedStyle(element).color);
    const link = luminance(getComputedStyle(element.querySelector("a")).color);
    return [color, link].map(value =>
      (Math.max(value, background) + 0.05) / (Math.min(value, background) + 0.05));
  });
  expect(contrast[0]).toBeGreaterThanOrEqual(4.5);
  expect(contrast[1]).toBeGreaterThanOrEqual(4.5);
  await page.getByRole("link", { name: "History and insights" }).click();
  await page.locator("#investment-roles > summary").click();
  await expect(page.getByText(/Unassigned means you have not chosen a role/)).toBeVisible();
});

test("unknown workspace does not guess an account source", async ({ page }) => {
  let legacyRequests = 0;
  await page.route("**/api/v1/settings", route => route.fulfill({ json: { workspace: ["demo"] } }));
  await page.route("**/api/v1/overview", route => { legacyRequests++; return route.abort(); });
  await page.goto("/");
  await expect(page.getByRole("alert")).toContainText("portfolio source is unknown");
  await expect(page.getByRole("navigation", { name: "Account navigation" })).toHaveCount(0);
  expect(legacyRequests).toBe(0);
});

test("portfolio awareness keeps matching local, shows evidence and fails closed on refresh", async ({ page }) => {
  await page.clock.install();
  const investment = await (await page.request.get("/api/investment/v1/snapshot")).json();
  const at = "2026-01-01T12:00:00Z";
  const matches = [{ account_id: "broker", product_id: "global-fund", roles: ["core"],
    mapping_evidence: "Operator-declared macro proxy; not fund look-through" }];
  const awareness = {
    enabled: true, schema_version: 2, source_mode: "recorded", portfolio_id: investment.portfolio_id,
    portfolio_revision: investment.revision, intelligence_database_id: "synthetic-intelligence",
    settings_revision: 1, profile_hash: "a".repeat(64), profile_observed_at: at, generated_at: at,
    calendar_status: "available", calendar_available_at: at, calendar_revision: 1,
    alerts: [{ event_id: "cpi", revision: 1, source: "bls", source_url: "https://www.bls.gov/schedule/",
      category: "inflation", title: "Synthetic inflation release", scheduled_at: null,
      available_at: at, status: "tentative", evidence_status: "unknown", matches }],
    briefs: [{
      brief_id: "brief", available_at: at, expires_at: "2026-01-01T12:30:00Z", status: "expired",
      verification: "unverified", provider: "local", model: "synthetic", prompt_version: "news-extractive-v1",
      input_hash: "b".repeat(64), interpretation: "Unverified synthetic interpretation.",
      uncertainties: ["Limited source coverage."], recommendation: "review", symbols: ["SPY"], matches,
      claims: [{ text: "Synthetic public statement.", evidence_ids: ["source-1"] }],
      sources: [{ evidence_id: "source-1", source_url: "https://www.federalreserve.gov/",
        title: "Public statement", published_at: at, received_at: at }],
    }],
    reminders: [{ id: "review", title: "Portfolio mandate check", last_reviewed_at: null,
      interval_days: 30, evidence: "Operator-selected reminder only", due_at: null, status: "never_reviewed" }],
    theses: [{ id: "case", title: "Synthetic long-term case", account_id: "broker", product_id: "global-fund",
      history: [
        { observed_at: "2025-12-31T12:00:00Z", status: "watch", thesis: "Initial hypothesis",
          invalidation: "Evidence contradicts premise", evidence: "Operator note one" },
        { observed_at: at, status: "active", thesis: "Updated hypothesis",
          invalidation: "Evidence contradicts premise", evidence: "Operator note two" },
      ] }],
    calendar_watch: [{ id: "uk", title: "Synthetic UK release", account_id: "broker", product_id: "global-fund",
      scheduled_on: "2026-12-17", time_note: "Time not stated", status: "tentative",
      observed_at: at, source: "Operator-checked calendar" }],
    warnings: ["No complete UK/global source coverage is established."],
    affects_trading: false, accounting_writes: false,
  };
  let requests = 0;
  let unavailable = false;
  await page.route("**/api/investment/v2/awareness", route => {
    requests++;
    return route.fulfill(unavailable ? { status: 503, json: { error: "unavailable" } } : { json: awareness });
  });
  await page.goto("/investment?view=tools");
  await page.getByText("Optional public context", { exact: true }).click();
  const section = page.getByRole("region", { name: "Portfolio awareness", exact: true });
  await expect(section.getByText("Synthetic inflation release", { exact: false })).toBeVisible();
  await section.getByText(/Synthetic inflation release - Tentative \/ Unknown/).click();
  await expect(section.getByText(/Time unknown/)).toBeVisible();
  await expect(section.getByText(/No complete UK\/global/)).toBeVisible();
  await expect(section.getByRole("checkbox")).not.toBeChecked();
  await page.clock.fastForward(61000);
  expect(requests).toBe(1);
  await section.getByRole("checkbox").check();
  await page.clock.fastForward(61000);
  await expect.poll(() => requests).toBeGreaterThan(1);
  await section.getByRole("checkbox").uncheck();
  await section.getByText(/Expired \/ Unverified/).click();
  await expect(section.getByText("Unverified synthetic interpretation.")).toBeVisible();
  await expect(section.getByRole("link", { name: "Public statement" })).toHaveAttribute("href", "https://www.federalreserve.gov/");
  await expect(section.getByText(/Portfolio mandate check/)).toBeVisible();
  await section.getByText("Synthetic long-term case - Active").click();
  await expect(section.getByText("Thesis: Updated hypothesis")).toBeVisible();
  await expect(section.getByText("Thesis: Initial hypothesis")).toBeVisible();
  await expect(section.getByText("Operator evidence note: Operator note two")).toBeVisible();
  const manualEvent = section.getByRole("listitem").filter({ hasText: "Synthetic UK release" });
  await expect(manualEvent).toContainText("2026-12-17");
  await expect(manualEvent).toContainText("Time not stated");
  const history = structuredClone(awareness.theses[0].history);
  for (const invalid of [
    [history[1], history[0]],
    [history[0], history[0]],
    [history[0], { ...history[1], observed_at: "2026-01-01T12:00:00.000001Z" }],
  ]) {
    awareness.theses[0].history = invalid;
    await section.getByRole("button", { name: "Refresh awareness" }).click();
    await expect(section.getByRole("alert")).toHaveText("Portfolio awareness response failed validation.");
    await expect(section.getByText("Updated hypothesis", { exact: false })).toHaveCount(0);
  }
  awareness.theses[0].history = history;
  awareness.theses = [awareness.theses[0], { ...awareness.theses[0], history }];
  await section.getByRole("button", { name: "Refresh awareness" }).click();
  await expect(section.getByRole("alert")).toHaveText("Portfolio awareness response failed validation.");
  awareness.theses = [awareness.theses[0]];
  awareness.calendar_watch.push({ ...awareness.calendar_watch[0] });
  await section.getByRole("button", { name: "Refresh awareness" }).click();
  await expect(section.getByRole("alert")).toHaveText("Portfolio awareness response failed validation.");
  awareness.calendar_watch.pop();
  awareness.profile_observed_at = "2026-01-01T12:00:00.000002Z";
  awareness.generated_at = awareness.profile_observed_at;
  awareness.theses[0].history = [
    { ...history[0], observed_at: "2026-01-01T12:00:00.000001Z" },
    { ...history[1], observed_at: "2026-01-01T12:00:00.000002Z" },
  ];
  await section.getByRole("button", { name: "Refresh awareness" }).click();
  await section.getByText("Synthetic long-term case - Active").click();
  await expect(section.getByText(/2026-01-01T12:00:00.000001Z - Watch/)).toBeVisible();
  awareness.profile_observed_at = at;
  awareness.generated_at = at;
  awareness.theses[0].history = history;
  await section.getByRole("button", { name: "Refresh awareness" }).click();
  await expect(section.getByText("Synthetic UK release", { exact: false })).toBeVisible();
  const saved = {
    alerts: awareness.alerts, briefs: awareness.briefs, calendar_watch: awareness.calendar_watch,
    reminders: awareness.reminders, theses: awareness.theses,
  };
  awareness.alerts = [];
  awareness.briefs = [];
  awareness.calendar_watch = [];
  awareness.reminders = [];
  awareness.theses = [];
  await section.getByRole("button", { name: "Refresh awareness" }).click();
  await expect(section.getByText(/No matched events in this snapshot/)).toBeVisible();
  await expect(section.getByText(/No matched briefs among the latest 20 saved/)).toBeVisible();
  await expect(section.getByText("No notes or reminders saved.", { exact: true })).toBeVisible();
  awareness.alerts = saved.alerts;
  awareness.briefs = saved.briefs;
  awareness.calendar_watch = saved.calendar_watch;
  awareness.reminders = saved.reminders;
  awareness.theses = saved.theses;
  await section.getByRole("button", { name: "Refresh awareness" }).click();
  await expect(section.getByText("Synthetic UK release", { exact: false })).toBeVisible();
  awareness.portfolio_revision++;
  await section.getByRole("button", { name: "Refresh awareness" }).click();
  await expect(section.getByRole("alert")).toContainText("Portfolio revision changed");
  await expect(section.getByText("Synthetic inflation release", { exact: false })).toHaveCount(0);
  await expect(section.getByText("Updated hypothesis", { exact: false })).toHaveCount(0);
  awareness.portfolio_revision--;
  awareness.affects_trading = true;
  await section.getByRole("button", { name: "Refresh awareness" }).click();
  await expect(section.getByRole("alert")).toHaveText("Portfolio awareness response failed validation.");
  await expect(section.getByText("Synthetic inflation release", { exact: false })).toHaveCount(0);
  unavailable = true;
  await section.getByRole("button", { name: "Refresh awareness" }).click();
  await expect(section.getByRole("alert")).toContainText("Portfolio awareness unavailable");
});

test("investment demo keeps key portfolio figures concise and puts capability details in help", async ({ page }) => {
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto("/");
  await expect(page).toHaveURL(/\/investment$/);
  await expect(page.getByRole("heading", { name: "Your Portfolio", level: 1 })).toBeVisible();
  await expect(page.getByText("SYNTHETIC DEMO")).toBeVisible();
  await page.getByRole("link", { name: "History and insights" }).click();
  await expect(page.getByText("Deposits less withdrawals", { exact: true })).toBeVisible();
  await page.getByText("Optional fund labels", { exact: true }).click();
  await expect(page.getByRole("heading", { name: "Management roles" })).toBeVisible();
  await expect(page.getByText("Examples, not your accounts.")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Allocation and drift" })).toHaveCount(0);
  await page.getByText("Cash observations and savings details", { exact: true }).click();
  await expect(page.getByText(/Savings are separate from investment-account buying power/)).toBeVisible();
  await expect(page.getByText(/No order approval or execution is available here/)).toHaveCount(0);
  await page.getByRole("button", { name: "Refresh investment" }).click();
  await page.getByRole("link", { name: "Overview", exact: true }).click();
  await expect(page.getByRole("region", { name: "Investment totals" })).toContainText("GBP");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  expect(errors).toEqual([]);
  await page.getByRole("navigation", { name: "Settings and help navigation" })
    .getByRole("link", { name: "Help" }).click();
  await page.getByText("About your records", { exact: true }).click();
  await expect(page.getByText(/does not recommend allocations or approve orders/)).toBeVisible();
});

test("help remains usable when recorded investment data is unavailable", async ({ page }) => {
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ status: 503, json: { error: "investment_unavailable" } }));
  await page.goto("/investment");
  await expect(page.getByRole("heading", { name: "Investment data unavailable" })).toBeVisible();
  await page.getByRole("navigation", { name: "Settings and help navigation" })
    .getByRole("link", { name: "Help" }).click();
  await page.getByRole("link", { name: "back up your records" }).click();
  await expect(page.locator("#backup-recovery")).toHaveAttribute("open", "");
  await page.getByText("About your records", { exact: true }).click();
  await expect(page.getByText(/About your records/)).toBeVisible();
  await expect(page.getByText(/Investment values can fall, and no return is guaranteed/)).toBeVisible();
  await expect(page.getByText(/Backups are not automatic/)).toBeVisible();
  await page.getByText("Steps for a source checkout", { exact: true }).click();
  await expect(page.getByText(/Never restore over the active journal/)).toBeVisible();
});

test("investment browser rejects the retired journal report format", async ({ page }) => {
  const report = await (await page.request.get("/api/investment/v1/snapshot")).json();
  report.schema_version = 1;
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
  await page.goto("/investment");
  await expect(page.getByRole("heading", { name: "Investment data unavailable" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Investment totals" })).toHaveCount(0);
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
  await expect(page.getByRole("region", { name: "Next steps" })).toHaveCount(0);
});

test("opening records show since-baseline value without inventing lifetime gain", async ({ page }) => {
  await page.clock.setFixedTime(new Date("2026-01-05T12:00:00Z"));
  const at = "2026-01-01T12:00:00Z";
  const report = {
    schema_version: 2, source_mode: "recorded", portfolio_id: "synthetic", revision: 1,
    generated_at: at, as_of: at, baseline_as_of: at, currency: "GBP", complete: true,
    equity_gbp: "2000", net_contributions_gbp: "0", since_baseline_gain_gbp: "0",
    lifetime_gain_gbp: null, twr_pct: "0", max_drawdown_pct: "0",
    recorded_fees_gbp: "3.5", recorded_interest_gbp: "2",
    recorded_dividends_gbp: "3", recorded_income_gbp: "5",
    recorded_fee_events: [{
      id: "synthetic-buy", type: "buy", account_id: "broker", product_id: "fund",
      quantity: "1", price_gbp: "10", fee_gbp: "0.5",
      timestamp: at, available_at: at, evidence: "synthetic fill evidence", role: "unassigned",
    }, {
      id: "synthetic-sell", type: "sell", account_id: "broker", product_id: "fund",
      quantity: "1", price_gbp: "11", fee_gbp: "1",
      timestamp: at, available_at: at, evidence: "synthetic later fill evidence", role: "unassigned",
    }],
    recorded_account_fees: [{
      id: "synthetic-account-charge", type: "account_fee", account_id: "bank",
      role: "unassigned", amount_gbp: "2", timestamp: at, available_at: at,
      evidence: "synthetic platform statement",
    }],
    recorded_income_events: [{
      id: "synthetic-interest", type: "interest", account_id: "bank",
      product_id: null, amount_gbp: "2", timestamp: at, available_at: at,
      evidence: "synthetic payment evidence", role: "unassigned", role_evidence: null,
    }, {
      id: "synthetic-dividend", type: "dividend", account_id: "broker",
      product_id: "fund", amount_gbp: "3", timestamp: at, available_at: at,
      evidence: "synthetic later payment evidence", role: "unassigned", role_evidence: null,
    }],
    recorded_flow_events: [{
      id: "synthetic-contribution", type: "contribution", account_id: "bank",
      destination_account_id: null, amount_gbp: "10", timestamp: at,
      available_at: at, evidence: "synthetic contribution evidence", role: "unassigned",
    }, {
      id: "synthetic-transfer", type: "transfer", account_id: "bank",
      destination_account_id: "broker", amount_gbp: "5", timestamp: at,
      available_at: at, evidence: "synthetic transfer evidence", role: "unassigned",
    }, {
      id: "synthetic-withdrawal", type: "withdrawal", account_id: "bank",
      destination_account_id: null, amount_gbp: "10", timestamp: at,
      available_at: at, evidence: "synthetic withdrawal evidence", role: "unassigned",
    }],
    recorded_corrections: [], recorded_profile_changes: [],
    recorded_scope_additions_gbp: "0", recorded_scope_additions: [],
    cash_observations: [
      { account_id: "bank", balance_gbp: "990", difference_gbp: "-10",
        timestamp: "2025-12-01T12:00:00Z", available_at: "2025-12-01T12:00:00Z",
        source: "prior synthetic statement", evidence: "prior synthetic evidence" },
      { account_id: "bank", balance_gbp: "999", difference_gbp: "-1",
        timestamp: at, available_at: at, source: "synthetic statement",
        evidence: "synthetic evidence" },
    ],
    valuation_history: [
      { timestamp: at, source_available_at: at, as_of: at, complete: true,
        equity_gbp: "2000", capital_basis_gbp: "2000", net_contributions_gbp: "0",
        gain_since_baseline_gbp: "0", twr_pct: "0", max_drawdown_pct: "0",
        gap_reason: null },
      { timestamp: "2026-01-01T18:00:00Z", source_available_at: "2026-01-01T18:00:00Z",
        as_of: "2026-01-01T18:00:00Z", complete: true, equity_gbp: "2100",
        capital_basis_gbp: "2000", net_contributions_gbp: "0",
        gain_since_baseline_gbp: "100", twr_pct: "5", max_drawdown_pct: "0",
        gap_reason: null },
      { timestamp: "2026-01-02T12:00:00Z", source_available_at: "2026-01-02T12:00:00Z",
        as_of: null, complete: false, equity_gbp: null, capital_basis_gbp: null,
        net_contributions_gbp: null, gain_since_baseline_gbp: null, twr_pct: null,
        max_drawdown_pct: null, gap_reason: "Incomplete synthetic valuation." },
      { timestamp: "2026-01-03T12:00:00Z", source_available_at: "2026-01-03T12:00:00Z",
        as_of: "2026-01-03T12:00:00Z", complete: true, equity_gbp: "1920",
        capital_basis_gbp: "2000", net_contributions_gbp: "0",
        gain_since_baseline_gbp: "-80", twr_pct: "-4", max_drawdown_pct: "8.57",
        gap_reason: null },
      { timestamp: "2026-01-04T12:00:00Z", source_available_at: "2026-01-04T12:00:00Z",
        as_of: "2026-01-04T12:00:00Z", complete: true, equity_gbp: "2020",
        capital_basis_gbp: "2100", net_contributions_gbp: "100",
        gain_since_baseline_gbp: "-80", twr_pct: null, max_drawdown_pct: null,
        gap_reason: null },
    ],
    accounts: [{ id: "bank", name: "Savings", kind: "deposit", status: "active", cash_gbp: "1000",
      cash_observation: { balance_gbp: "999", difference_gbp: "-1", timestamp: at,
        available_at: at, source: "synthetic statement", evidence: "synthetic evidence" },
      terms: { annual_rate_pct: "4.25", rate_basis: "AER", effective_from: "2026-01-01",
        observed_at: at, available_at: at, source: "synthetic statement",
        maturity_date: "2027-01-01", notice_days: 90 } },
      { id: "broker", name: "Broker", kind: "broker", status: "active", cash_gbp: "500",
        cash_observation: null, terms: null }],
    holdings: [{ account_id: "broker", product_id: "fund", name: "Fund", sleeve: "equity",
      quantity: "50", value_gbp: "500", mark_at: at, mark_available_at: at }],
    role_accounting: [
      { role: "core", cash_gbp: "0", value_gbp: "0", accounts: [], holdings: [] },
      { role: "dynamic", cash_gbp: "0", value_gbp: "0", accounts: [], holdings: [] },
      { role: "unassigned", cash_gbp: "1500", value_gbp: "2000", accounts: [], holdings: [] },
    ],
    role_accounting_at: null, role_assignments: [],
    proposal: { status: "blocked", reason: "Policy is not implemented", expires_at: null,
      orders: [], manual_transfers: [], execution_enabled: false },
    warnings: [], limitations: ["Synthetic test evidence."],
  };
  let opened = true;
  let latestCorrectable = {
    id: "synthetic-interest", type: "interest", account_id: "bank",
    currency: "GBP", amount_gbp: "2", timestamp: at, available_at: at,
    evidence: "synthetic payment evidence",
  };
  let previewEvent;
  await page.route("**/api/investment/v1/recording/session", route => route.fulfill({ json: {
    enabled: true, csrf: "synthetic-csrf", opened,
    accounts: [{ id: "bank", name: "Savings", kind: "deposit" },
      { id: "broker", name: "Broker", kind: "broker" }],
    products: [{ id: "fund", name: "Fund", sleeve: "equity", quantity_increment: "1",
      minimum_notional_gbp: "1" }],
    latest_correctable_event: latestCorrectable,
    correctable_events: latestCorrectable ? [latestCorrectable] : [],
    current_balances: { bank: "1000", broker: "500" }, current_holdings: [],
  } }));
  await page.route("**/api/investment/v1/recording/preview", async route => {
    const request = route.request().postDataJSON();
    previewEvent = request.event;
    await route.fulfill({ json: {
      preview_id: `preview-${request.event.id}`, revision: report.revision,
      event: request.event, before: { ...report, equity_gbp: "2000" },
      after: { ...report, equity_gbp: "2000", revision: report.revision + 1 }, expires_at: "2026-01-05T12:05:00Z",
    } });
  });
  await page.route("**/api/investment/v1/recording/confirm", async route => {
    if (previewEvent.type === "correction") latestCorrectable = null;
    else latestCorrectable = {
      id: previewEvent.id, type: previewEvent.type, account_id: previewEvent.account_id,
      currency: previewEvent.currency, amount_gbp: previewEvent.amount_gbp, timestamp: previewEvent.timestamp,
      available_at: previewEvent.available_at, evidence: previewEvent.evidence, role: previewEvent.role,
    };
    await route.fulfill({ json: { id: previewEvent.id, revision: report.revision + 1 } });
  });
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
  await page.goto("/investment");
  const totals = page.getByRole("region", { name: "Investment totals" });
  await expect(totals.getByText("£2,000.00")).toBeVisible();
  await page.getByRole("link", { name: "History and insights" }).click();
  await expect(page.getByRole("region", { name: "Recorded contribution and gain" })
    .getByText("Gain since tracking began")).toBeVisible();
  await expect(page.getByText("Net money added is not investment gain. Missing history is unavailable, not zero."))
    .toBeVisible();
  const metricHelp = page.locator("details").filter({ has: page.getByText("About these figures and dates", { exact: true }) });
  await expect(metricHelp).not.toHaveAttribute("open", "");
  await metricHelp.locator("summary").click();
  await expect(metricHelp).toContainText("Gain since tracking began subtracts recorded net flows");
  await expect(metricHelp).toContainText("Percentage return is separate and needs complete evidence");
  await page.locator("#investment-performance > summary").click();
  await expect(page.getByRole("heading", { name: "Recorded growth history" })).toBeVisible();
  await expect(page.getByText("These are recorded values, not live prices. Lines join recorded points; missing information stays as a gap.")).toBeVisible();
  const returnChart = page.getByRole("img", { name: "Time-weighted return since baseline; unavailable returns are gaps" });
  await expect(returnChart).toBeVisible();
  await expect(returnChart.locator("circle")).toHaveCount(3);
  await expect(returnChart.locator("polyline")).toHaveCount(1);
  await expect(returnChart.locator(".return-chart-gap")).toHaveCount(2);
  const historyDisclosure = page.getByText("Detailed history and calculation notes", { exact: true });
  await expect(historyDisclosure.locator("..")).not.toHaveAttribute("open", "");
  await historyDisclosure.click();
  await expect(page.getByText("Gap: Incomplete synthetic valuation.")).toBeVisible();
  await expect(page.getByLabel("Recorded growth history").getByRole("columnheader", { name: "Maximum drawdown" })).toBeVisible();
  await expect(page.getByLabel("Recorded growth history").getByRole("row").nth(2)).toContainText("8.57%");
  const historyTable = page.getByLabel("Recorded growth history");
  await expect(historyTable.getByRole("columnheader", { name: "Return since previous clock" })).toBeVisible();
  await expect(historyTable.getByRole("row").nth(4).getByRole("cell").nth(7)).toHaveText("5.00%From 2026-01-01T12:00:00Z");
  await expect(historyTable.getByRole("row").nth(2).getByRole("cell").nth(7)).toHaveText("Unavailable");
  await expect(historyTable.getByRole("row").nth(5).getByRole("cell").nth(7)).toHaveText("Unavailable");
  const history = report.valuation_history;
  report.valuation_history = history.map((point, index) => ({
    ...point, twr_pct: index === 0 ? "100" : index === 1 ? "110" : point.twr_pct,
  }));
  await page.getByRole("button", { name: "Refresh investment" }).click();
  await expect(historyTable.getByRole("row").nth(4).getByRole("cell").nth(7)).toHaveText("5.00%From 2026-01-01T12:00:00Z");
  report.valuation_history = history.map((point, index) => ({
    ...point, twr_pct: index === 0 ? "-100" : index === 1 ? "0" : point.twr_pct,
  }));
  await page.getByRole("button", { name: "Refresh investment" }).click();
  await expect(historyTable.getByRole("row").nth(4).getByRole("cell").nth(7)).toHaveText("Unavailable");
  report.valuation_history = history.map((point, index) => ({
    ...point, twr_pct: index === 0 ? "0" : index === 1 ? "-100" : point.twr_pct,
  }));
  await page.getByRole("button", { name: "Refresh investment" }).click();
  await expect(historyTable.getByRole("row").nth(4).getByRole("cell").nth(7)).toHaveText("-100.00%From 2026-01-01T12:00:00Z");
  report.valuation_history = ["0", "10", null, "10"].map((twr, index) => {
    const time = new Date(Date.UTC(2026, 0, index + 1, 12)).toISOString();
    return { ...history[0], timestamp: time, source_available_at: time, as_of: time,
      equity_gbp: index === 2 ? "0" : "1000", twr_pct: twr,
      max_drawdown_pct: twr === null ? null : "0" };
  });
  await page.getByRole("button", { name: "Refresh investment" }).click();
  await expect(historyTable.getByRole("row").nth(1).getByRole("cell").nth(7)).toHaveText("Unavailable");
  await expect(historyTable.getByRole("row").nth(2).getByRole("cell").nth(7)).toHaveText("Unavailable");
  await expect(historyTable.getByRole("row").nth(3).getByRole("cell").nth(7))
    .toHaveText("10.00%From 2026-01-01T12:00:00.000Z");
  report.valuation_history = Array.from({ length: 21 }, (_, index) => {
    const time = new Date(Date.UTC(2026, 0, 1, index)).toISOString();
    return { ...history[0], timestamp: time, source_available_at: time, as_of: time,
      twr_pct: String(index) };
  });
  await page.getByRole("button", { name: "Refresh investment" }).click();
  await expect(historyTable.getByRole("row")).toHaveCount(21);
  await expect(historyTable.getByRole("row").nth(20).getByRole("cell").nth(7))
    .toHaveText("1.00%From 2026-01-01T00:00:00.000Z");
  const preciseReturns = ["1", "1.00000000000000000001", null,
    "1.00000000000000000002", "1.00000000000000000003"];
  report.valuation_history = history.map((point, index) => {
    const time = `2026-01-03T12:00:00.10000${index + 1}Z`;
    return { ...point, timestamp: time, source_available_at: time,
      as_of: point.complete ? time : null, twr_pct: preciseReturns[index] };
  });
  await page.getByRole("button", { name: "Refresh investment" }).click();
  await expect(returnChart.locator("polyline")).toHaveCount(2);
  const preciseChart = await returnChart.evaluate(svg => ({
    circles: [...svg.querySelectorAll("circle")].map(circle => [Number(circle.getAttribute("cx")), Number(circle.getAttribute("cy"))]),
    gap: Number(svg.querySelector(".return-chart-gap").getAttribute("x1")),
    lines: [...svg.querySelectorAll("polyline")].map(line => line.getAttribute("points")),
  }));
  expect(preciseChart.circles.every(([x, y]) => Number.isFinite(x) && Number.isFinite(y))).toBeTruthy();
  expect(new Set(preciseChart.circles.map(([x]) => x)).size).toBe(4);
  expect(preciseChart.circles[1][0]).toBeLessThan(preciseChart.gap);
  expect(preciseChart.circles[2][0]).toBeGreaterThan(preciseChart.gap);
  expect(preciseChart.lines.every(points => !points.includes("NaN") && !points.includes("Infinity"))).toBeTruthy();
  await expect(page.getByLabel("Recorded growth history")).toContainText("2026-01-03T12:00:00.100001Z");
  const closeTimes = ["2026-01-01T00:00:00Z", "2026-01-01T23:59:59Z",
    "2026-01-02T00:00:00Z", "2026-01-02T00:00:01Z", "2026-01-03T00:00:00Z"];
  report.valuation_history = history.map((point, index) => {
    const time = closeTimes[index];
    return { ...point, timestamp: time, source_available_at: time,
      as_of: point.complete ? time : null, twr_pct: point.complete ? "1" : null };
  });
  await page.getByRole("button", { name: "Refresh investment" }).click();
  await expect(returnChart.locator("polyline")).toHaveCount(2);
  const renderedGap = await returnChart.evaluate(async svg => {
    const clone = svg.cloneNode(true);
    clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
    clone.querySelectorAll(".return-chart-gap").forEach(line => line.remove());
    const point = clone.querySelectorAll("circle")[1];
    const x = Math.round(Number(point.getAttribute("cx")));
    const y = Math.round(Number(point.getAttribute("cy")));
    const sample = async () => {
      const image = new Image();
      const source = new XMLSerializer().serializeToString(clone);
      await new Promise((resolve, reject) => {
        image.onload = resolve; image.onerror = reject;
        image.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(source)}`;
      });
      const canvas = document.createElement("canvas");
      canvas.width = 800; canvas.height = 260;
      const context = canvas.getContext("2d");
      context.drawImage(image, 0, 0);
      return [...context.getImageData(x, y, 1, 1).data];
    };
    const masked = await sample();
    clone.querySelector("g[mask]").removeAttribute("mask");
    return { masked, unmasked: await sample() };
  });
  expect(renderedGap.unmasked.slice(0, 3)).toEqual([33, 116, 95]);
  expect(renderedGap.masked).not.toEqual(renderedGap.unmasked);
  report.valuation_history = history.map(point => ({ ...point, twr_pct: null, max_drawdown_pct: null }));
  await page.getByRole("button", { name: "Refresh investment" }).click();
  await expect(page.getByText("No time-weighted return is available in the recent history.")).toBeVisible();
  await expect(returnChart).toHaveCount(0);
  report.valuation_history = history;
  await page.getByRole("button", { name: "Refresh investment" }).click();
  await expect(returnChart).toBeVisible();
  await page.locator("#investment-records > summary").click();
  await expect(page.getByText(/Lifetime gain and prior cost basis are unavailable/)).toBeVisible();
  const attribution = page.getByRole("region", { name: "Recorded fees and income" });
  await expect(attribution.getByText("Recorded trade and account charges")).toBeVisible();
  await expect(attribution).toContainText("£3.50");
  await expect(attribution).toContainText("not an all-in cost total");
  await expect(attribution).toContainText("spread or slippage are not separately measured");
  const accountChargeHistory = page.getByLabel("Recorded account charge history");
  await expect(accountChargeHistory.getByRole("row").nth(1))
    .toContainText("synthetic platform statement");
  await expect(accountChargeHistory.getByRole("row").nth(1)).toContainText("£2.00");
  await expect(attribution.getByText("Recorded interest")).toBeVisible();
  await expect(attribution.getByText("Recorded dividends")).toBeVisible();
  await expect(attribution.getByText("Recorded income total")).toBeVisible();
  const feeHistory = page.getByLabel("Recorded fill fee history").getByRole("table");
  await expect(feeHistory.getByRole("columnheader", { name: "Effective at (UTC)" })).toBeVisible();
  await expect(feeHistory.getByRole("row").nth(1)).toContainText("synthetic fill evidence");
  await expect(feeHistory.getByRole("row").nth(2)).toContainText("synthetic later fill evidence");
  const incomeHistory = page.getByLabel("Recorded income history").getByRole("table");
  await expect(incomeHistory.getByRole("columnheader", { name: "Effective at (UTC)" })).toBeVisible();
  await expect(incomeHistory.getByRole("row").nth(1)).toContainText("synthetic payment evidence");
  await expect(incomeHistory.getByRole("row").nth(2)).toContainText("synthetic later payment evidence");
  const flowHistory = page.getByLabel("Recorded capital flow history").getByRole("table");
  await expect(flowHistory.getByRole("columnheader", { name: "Effective at (UTC)" })).toBeVisible();
  await expect(flowHistory.getByRole("row").nth(1)).toContainText("synthetic contribution evidence");
  await expect(flowHistory.getByRole("row").nth(1)).toContainText("External source");
  await expect(flowHistory.getByRole("row").nth(2)).toContainText("synthetic transfer evidence");
  await expect(flowHistory.getByRole("row").nth(2)).toContainText("broker");
  await expect(flowHistory.getByRole("row").nth(3)).toContainText("synthetic withdrawal evidence");
  await expect(flowHistory.getByRole("row").nth(3)).toContainText("External destination");
  await expect(page.getByText(/Transfers between included accounts do not change net external contributions/)).toBeVisible();
  await page.getByText("Cash observations and savings details", { exact: true }).click();
  await expect(page.getByRole("heading", { name: "Reported savings-account terms" })).toBeVisible();
  await expect(page.locator("#investment-accounts").getByText(/4.25% AER/)).toBeVisible();
  await expect(page.getByText(/not current provider verification or an interest-accrual input/)).toBeVisible();
  await expect(page.getByRole("heading", { name: "Observed cash balances" })).toBeVisible();
  const cashHistory = page.getByLabel("Cash reconciliation history").getByRole("table");
  await expect(cashHistory.getByRole("row").nth(1)).toContainText("-£1.00");
  await expect(cashHistory.getByRole("row").nth(1)).toContainText("synthetic evidence");
  await expect(cashHistory.getByRole("row").nth(2)).toContainText("-£10.00");
  await expect(cashHistory.getByRole("row").nth(2)).toContainText("prior synthetic evidence");
  const reconciliation = page.getByRole("region", { name: "Cash evidence overview" });
  await expect(reconciliation).toContainText("2 differing observations");
  await expect(reconciliation).toContainText("No cash observation");
  await expect(page.getByRole("region", { name: "Valuation evidence" })).toContainText("Valuation recorded");
  const latest = { account_id: "bank", balance_gbp: "1000", difference_gbp: "0",
    timestamp: "2026-01-02T12:00:00Z", available_at: "2026-01-02T12:00:00Z",
    source: "later synthetic statement", evidence: "later matching evidence" };
  report.cash_observations.push(latest);
  report.accounts[0].cash_observation = { ...latest };
  delete report.accounts[0].cash_observation.account_id;
  await page.getByRole("button", { name: "Refresh investment" }).click();
  await expect(reconciliation).toContainText("2 differing observations");
  await expect(reconciliation).toContainText("synthetic evidence");
  await expect(reconciliation).toContainText("prior synthetic evidence");
  await expect(reconciliation).toContainText("Later matching observations do not resolve");
  report.cash_observations.pop();
  report.accounts[0].cash_observation = { ...report.cash_observations[1] };
  delete report.accounts[0].cash_observation.account_id;
  for (const [day, difference] of [[2, "0.001"], [3, "-0.001"]]) {
    report.cash_observations.push({ account_id: "bank", balance_gbp: "1000",
      difference_gbp: difference, timestamp: `2026-01-0${day}T12:00:00Z`,
      available_at: `2026-01-0${day}T12:00:00Z`, source: "precise statement", evidence: "precise evidence" });
  }
  report.accounts[0].cash_observation = { ...report.cash_observations.at(-1) };
  delete report.accounts[0].cash_observation.account_id;
  await page.getByRole("button", { name: "Refresh investment" }).click();
  await expect(reconciliation).toContainText("-£0.001");
  await expect(cashHistory.getByRole("row").nth(1)).toContainText("-£0.001");
  await expect(cashHistory.getByRole("row").nth(2)).toContainText("£0.001");
  report.cash_observations.splice(-2);
  report.accounts[0].cash_observation = { ...report.cash_observations[1] };
  delete report.accounts[0].cash_observation.account_id;
  const holding = { ...report.holdings[0] };
  report.as_of = null;
  report.complete = false;
  report.equity_gbp = null;
  report.since_baseline_gain_gbp = null;
  report.twr_pct = null;
  report.max_drawdown_pct = null;
  report.holdings[0] = { ...holding, value_gbp: null, mark_at: null, mark_available_at: null };
  await page.getByRole("button", { name: "Refresh investment" }).click();
  await expect(page.getByRole("region", { name: "Valuation evidence" })).toContainText("Incomplete; full total unavailable.");
  await page.getByText("Accounts and recorded holdings", { exact: true }).click();
  await expect(page.getByLabel("Recorded investment holdings", { exact: true })).toContainText("Unavailable");
  const priceDates = page.locator(".portfolio-summary details").filter({
    has: page.getByText("Price dates and source availability", { exact: true }),
  });
  await priceDates.locator("summary").click();
  await expect(priceDates).toContainText("No recorded price");
  await expect(priceDates).toContainText("Not available");
  report.as_of = at;
  report.complete = true;
  report.equity_gbp = "2000";
  report.since_baseline_gain_gbp = "0";
  report.twr_pct = "0";
  report.max_drawdown_pct = "0";
  report.holdings[0] = holding;
  await page.getByRole("button", { name: "Refresh investment" }).click();
  await page.clock.setFixedTime(new Date("2025-01-01T12:00:00Z"));
  await expect(page.getByRole("region", { name: "Valuation evidence" })).toContainText("Valuation recorded");
  await page.clock.setFixedTime(new Date("2026-01-05T12:00:00Z"));
  await expect(page.locator("#investment-accounts")).toHaveAttribute("open", "");
  await expect(priceDates.getByRole("columnheader", { name: "Price applies at" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Allocation and drift" })).toHaveCount(0);
  await expect(page.getByText(/No order approval or execution is available here/)).toHaveCount(0);
  await page.getByRole("link", { name: "Add a record", exact: true }).click();
  await expect(page).toHaveURL(/\/investment\/record$/);
  await expect(page.getByLabel("What are you recording?")).toBeVisible();
  await expect(page.getByLabel("What are you recording?").locator("optgroup[label='Investments']"))
    .toContainText("Investment bought");
  await expect(page.getByRole("combobox", { name: "Choose a record", exact: true })).toHaveCount(0);
  await page.getByLabel("What are you recording?").selectOption("buy");
  await expect(page.getByLabel("Recorded fee in GBP")).toHaveValue("");
  await expect(page.getByLabel("Applies at (local time)")).toHaveValue("");
  await expect(page.getByLabel("Source available from (local time)")).toHaveValue("");
  await page.getByLabel("What are you recording?").selectOption("split");
  await page.getByRole("combobox", { name: "Account", exact: true }).selectOption("broker");
  await page.getByRole("combobox", { name: "Investment", exact: true }).selectOption("fund");
  await page.getByLabel("Split ratio").fill("2");
  await page.getByLabel("Applies at (local time)").fill("2026-01-05T12:00");
  await page.getByLabel("Source available from (local time)").fill("2026-01-05T12:00");
  await page.getByLabel("Source description / correction reason").fill("Synthetic split notice");
  await page.getByRole("button", { name: "Preview record" }).click();
  await expect.poll(() => previewEvent).toMatchObject({ type: "split", account_id: "broker", product_id: "fund", ratio: "2" });
  await page.getByRole("button", { name: "Cancel preview" }).click();
  await page.getByLabel("What are you recording?").selectOption("contribution");
  await page.getByRole("combobox", { name: "Account", exact: true }).selectOption("bank");
  await page.getByLabel("GBP amount").fill("25");
  await page.getByLabel("Applies at (local time)").fill("2026-01-05T12:00");
  await page.getByLabel("Source available from (local time)").fill("2026-01-05T12:00");
  await page.getByLabel("Source description / correction reason").fill("Synthetic contribution receipt");
  await page.getByRole("button", { name: "Preview record" }).click();
  await expect.poll(() => previewEvent?.type).toBe("contribution");
  expect(previewEvent).not.toHaveProperty("role");
  await expect(page.getByRole("region", { name: "Record summary" })).toContainText("Unassigned — no role chosen");
  await page.getByRole("button", { name: "Cancel preview" }).click();
  await page.getByRole("region", { name: "Manual recording" }).getByLabel("Optional role", { exact: true }).selectOption("dynamic");
  await page.getByRole("button", { name: "Preview record" }).click();
  await expect(page.getByRole("heading", { name: "Review before recording" })).toBeVisible();
  const confirmationEvent = page.locator(".recording-preview details.recording-event");
  await expect(page.getByText("£25.00", { exact: true })).toBeVisible();
  await expect(confirmationEvent.locator("summary")).toHaveText("Exact event and evidence being appended");
  await expect(confirmationEvent).not.toHaveAttribute("open", "");
  await expect(confirmationEvent).toContainText('"account_id": "bank"');
  await expect(confirmationEvent).toContainText('"amount_gbp": "25"');
  await expect(confirmationEvent).toContainText('"role": "dynamic"');
  await expect(confirmationEvent).toContainText("Synthetic contribution receipt");
  await page.getByRole("button", { name: "Confirm and append record" }).click();
  await expect.poll(() => previewEvent?.type).toBe("contribution");
  await page.getByLabel("What are you recording?").selectOption("account_fee");
  await page.getByRole("combobox", { name: "Account", exact: true }).selectOption("bank");
  await page.getByLabel("Charge amount (GBP)").fill("3.50");
  await page.getByLabel("Applies at (local time)").fill("2026-01-05T12:00");
  await page.getByLabel("Source available from (local time)").fill("2026-01-05T12:00");
  await page.getByLabel("Source description / correction reason").fill("Synthetic account charge");
  await page.getByRole("button", { name: "Preview record" }).click();
  await expect.poll(() => previewEvent?.type).toBe("account_fee");
  expect(previewEvent).toMatchObject({
    account_id: "bank", amount_gbp: "3.50", type: "account_fee",
  });
  const chargeSummary = page.locator(".recording-preview details.recording-event");
  await expect(chargeSummary).toContainText('"amount_gbp": "3.50"');
  await expect(chargeSummary).toContainText("Synthetic account charge");
  await page.getByRole("button", { name: "Cancel preview" }).click();
  await page.getByLabel("What are you recording?").selectOption("correction");
  await page.getByLabel("Event to correct").selectOption({ index: 1 });
  await page.getByLabel("Corrected GBP amount").fill("20");
  await page.getByLabel("Correction recorded at (local time)").fill("2026-01-05T12:00");
  await page.getByLabel("Source available from (local time)").fill("2026-01-05T12:00");
  await page.getByLabel("Source description / correction reason").fill("Synthetic corrected receipt");
  await expect(page.getByText(/Correcting Contribution/)).toBeVisible();
  await page.getByRole("button", { name: "Preview record" }).click();
  await expect(confirmationEvent).toContainText('"replacement_event"');
  await expect(confirmationEvent).toContainText('"amount_gbp": "20"');
  await expect(confirmationEvent).toContainText("Synthetic corrected receipt");
  await page.getByRole("button", { name: "Confirm and append record" }).click();
  await expect.poll(() => previewEvent?.type).toBe("correction");
  expect(previewEvent.replacement_event.amount_gbp).toBe("20");
  expect(previewEvent.replacement_event.currency).toBe("GBP");
  expect(previewEvent.replacement_event.role).toBe("dynamic");
  await page.getByLabel("What are you recording?").selectOption("profile_change");
  await page.getByLabel("What do you want to change?").selectOption("rename");
  await page.getByRole("combobox", { name: "Account or investment?", exact: true }).selectOption("product");
  await page.getByLabel("Which account or investment?").selectOption("fund");
  await page.getByLabel("New name").fill("Renamed fund");
  await page.getByLabel("Applies and available at (local time)")
    .fill("2026-01-02T12:00");
  await page.getByLabel("Source description / correction reason").fill("Synthetic product documentation");
  await page.getByRole("button", { name: "Preview record" }).click();
  await expect(confirmationEvent).toContainText('"action": "rename"');
  await expect(confirmationEvent).toContainText('"entity_id": "fund"');
  await expect(confirmationEvent).toContainText('"name": "Renamed fund"');
  await page.getByRole("button", { name: "Cancel preview" }).click();
  await page.getByLabel("What are you recording?").selectOption("scope_addition");
  await page.getByLabel("Account reference (unique ID)", { exact: true }).fill("scope");
  await page.getByLabel("Account name").fill("Additional broker");
  await page.getByLabel("Account link nickname", { exact: true }).fill("synthetic-binding");
  const scopeBalances = page.getByRole("group", { name: "Cash in every account at the same date and time (GBP)" });
  await scopeBalances.getByRole("textbox").nth(0).fill("1000");
  await scopeBalances.getByRole("textbox").nth(1).fill("500");
  await scopeBalances.getByRole("textbox").nth(2).fill("10");
  await page.getByRole("button", { name: "Add new-account holding" }).click();
  const scopeHoldings = page.getByRole("group", {
    name: "All existing and new holdings with prices at the same time",
  });
  await scopeHoldings.getByLabel("Account").selectOption("scope");
  await scopeHoldings.getByLabel("Investment").selectOption("fund");
  await scopeHoldings.getByLabel("Quantity").fill("2");
  await scopeHoldings.getByLabel("GBP price per unit").fill("5");
  await page.getByLabel("Applies and available at (local time)")
    .fill("2026-01-03T12:00");
  await page.getByLabel("Source description / correction reason").fill("Synthetic common-clock statement");
  await page.getByRole("button", { name: "Preview record" }).click();
  await expect.poll(() => previewEvent?.type).toBe("scope_addition");
  expect(previewEvent.account).toMatchObject({
    id: "scope", name: "Additional broker", binding: "synthetic-binding", kind: "broker",
  });
  expect(previewEvent.balances_gbp).toEqual({ bank: "1000", broker: "500", scope: "10" });
  expect(previewEvent.holdings).toEqual([{
    account_id: "scope", product_id: "fund", quantity: "2", price_gbp: "5",
  }]);
  const recordSummary = page.getByRole("region", { name: "Record summary" });
  await expect(recordSummary).toContainText("Account added to scope");
  await expect(recordSummary).toContainText("Additional broker");
  await expect(recordSummary).toContainText("Cash included in this record");
  await expect(recordSummary).toContainText("£10.00");
  await expect(recordSummary).toContainText("Holdings included");
  await page.getByRole("button", { name: "Cancel preview" }).click();
  await expect(page.getByLabel("What are you recording?")).toBeVisible();
  await page.getByLabel("What are you recording?").selectOption("role_assignment");
  await page.getByRole("region", { name: "Manual recording" }).getByLabel("Optional role", { exact: true }).selectOption("core");
  await page.getByLabel("GBP amount").fill("100");
  await page.getByLabel("Applies at (local time)").fill("2026-01-05T12:00");
  await page.getByLabel("Source available from (local time)").fill("2026-01-05T12:00");
  await page.getByLabel("Source description / correction reason").fill("Synthetic attribution evidence");
  await page.getByRole("button", { name: "Preview record" }).click();
  await expect(page.getByRole("region", { name: "Record summary" })).toContainText("£100.00");
  await expect(page.getByRole("region", { name: "Record summary" })).toContainText("Cash");
  await expect(confirmationEvent).toContainText('"role": "core"');
  await expect(confirmationEvent).toContainText('"product_id": null');
  await page.getByRole("button", { name: "Cancel preview" }).click();
  opened = false;
  await page.reload();
  await expect(page.getByText("Starting cash balances at the same date and time")).toBeVisible();
  await expect(page.getByRole("button", { name: "Add existing holding" })).toBeVisible();
  const openingCash = page.getByRole("group", { name: "Starting cash balances at the same date and time" });
  await openingCash.getByRole("textbox").nth(0).fill("1000");
  await openingCash.getByRole("textbox").nth(1).fill("0");
  await page.getByRole("button", { name: "Add existing holding" }).click();
  const holdingInput = page.getByRole("group", { name: "Investments you already own (add every holding)" });
  await holdingInput.getByLabel("Account").selectOption("broker");
  await holdingInput.getByLabel("Investment").selectOption("fund");
  await page.getByLabel("Quantity").fill("1");
  await page.getByLabel("Applies at (local time)").fill("2026-01-05T12:00");
  await page.getByLabel("Source available from (local time)").fill("2026-01-05T12:00");
  await page.getByLabel("Source description / correction reason").fill("Synthetic existing holding evidence");
  await page.getByRole("button", { name: "Preview record" }).click();
  await expect(page.getByRole("heading", { name: "Review before recording" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Record summary" })).toContainText("unknown — not zero");
  await expect.poll(() => previewEvent?.type).toBe("opening");
  expect(previewEvent.holdings[0].price_gbp).toBeNull();
  await page.getByRole("button", { name: "Cancel preview" }).click();
  await page.goto("/investment");
  report.lifetime_gain_gbp = "0";
  await page.getByRole("button", { name: "Refresh investment" }).click();
  await expect(page.getByText("Investment response failed validation.")).toBeVisible();
});
