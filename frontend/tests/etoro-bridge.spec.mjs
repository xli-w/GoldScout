import { expect, test } from "@playwright/test";

const position = (id, units = "2") => ({
  instrument_id: id, asset_currency: "USD", netUnits: units, netContracts: units,
  netCurrentExposureAssetCurrency: "42", netCurrentExposureAccountCurrency: "42", avgLeverage: "1",
});

async function fixture(page) {
  const report = await (await page.request.get("/api/investment/v1/snapshot")).json();
  report.source_mode = "recorded";
  const account = report.accounts.find(item => item.kind === "broker" && item.status === "active");
  const holdings = report.holdings.filter(item => item.account_id === account.id);
  const snapshot = {
    schema_version: 1, environment: "real", account_id: 12345,
    source_as_of: "2026-07-01T10:00:00Z", account_currency: "USD",
    account_totals: { accountAvailableCash: "100", accountFrozenCash: "5",
      accountCurrentPnl: "0", accountTotalValue: "999999", accountTotalUsedMargin: "0",
      accountBalance: "105" },
    direct_positions: [position(101, "2.000001"), position(102), position(103)],
    mirrors: [{ mirror_id: 7, positions: [position(103, "-1"), position(104)] }],
    closed_trade_history: { status: "not_requested", from_date: null, through_date: null,
      received_at: null, trades: [] },
    coverage: {}, gbp_balance: { status: "provider_reported", amount_gbp: "77",
      source_balance: "105", source_currency: "USD", display_currency: "GBP",
      exchange_rate: "0.77", source_as_of: null, received_at: "2026-07-01T10:02:00Z" },
  };
  const connection = { environment: "real", account_id: 12345, expires_at: null, revoked_at: null, revision: 1 };
  const session = { enabled: true, csrf: "synthetic-csrf", state: "connected",
    last_status: "success", snapshot_available: true, connection };
  const portfolio = { state: "connected", last_status: "success", snapshot, connection };
  await page.route("**/api/v1/settings", route => route.fulfill({
    json: { mode: "recorded", workspace: "investment" },
  }));
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
  await page.route("**/api/investment/v1/etoro/**", route => {
    if (route.request().method() !== "GET") return route.abort();
    return route.fulfill({ json: route.request().url().endsWith("/session") ? session : portfolio });
  });
  await page.clock.setFixedTime(new Date("2030-01-01T12:00:00Z"));
  return { report, account, holdings, snapshot, connection, session, portfolio };
}

test("operator pairs distinct rows temporarily and follows the existing account update", async ({ page }) => {
  const { report, account, holdings } = await fixture(page);
  let writes = 0;
  page.on("request", request => { if (!["GET", "HEAD"].includes(request.method())) writes++; });
  await page.goto("/investment?view=tools");
  const bridge = page.getByRole("region", { name: "Review eToro against journal" });
  await expect(bridge).toContainText("Nothing is imported");
  await bridge.getByLabel("Journal account").selectOption(account.id);
  await expect(bridge.getByLabel("Provider row")).toHaveCount(0);
  await bridge.getByRole("checkbox", { name: /I checked that eToro account/ }).check();
  await expect(bridge).toContainText("Journal cash: GBP");
  await expect(bridge).toContainText("eToro available cash: 100 USD");
  await expect(bridge).toContainText("not a GBP cash reconciliation");
  await expect(bridge).toContainText("2 provider rows share an instrument ID");
  await expect(bridge).toContainText("An equal number is not a match");
  const source = bridge.getByLabel("Provider row");
  const holding = bridge.getByLabel("Recorded holding");
  await expect(source.locator('option[value="direct:103"]')).toHaveCount(0);
  await expect(source.locator('option[value="copy:7:103"]')).toHaveCount(0);
  await source.selectOption("direct:101");
  await holding.selectOption(holdings[0].product_id);
  await bridge.getByRole("button", { name: "Add to this review" }).click();
  const pairs = bridge.getByRole("list", { name: "Temporary holding pairings" });
  await expect(pairs).toContainText("instrument 101: 2.000001 provider net units");
  await expect(pairs).toContainText(`${holdings[0].quantity} recorded units`);
  await expect(source.locator('option[value="direct:101"]')).toHaveCount(0);
  await expect(holding.locator(`option[value="${holdings[0].product_id}"]`)).toHaveCount(0);
  await source.selectOption("direct:102");
  await holding.selectOption(holdings[1].product_id);
  await bridge.getByRole("button", { name: "Add to this review" }).click();
  await expect(bridge).toContainText("3 provider rows are not paired");
  await expect(bridge).toContainText("0 recorded holdings are not paired");
  await bridge.getByRole("button", { name: "Remove pairing for instrument 101" }).click();
  await expect(source.locator('option[value="direct:101"]')).toHaveCount(1);
  await expect(bridge).toContainText("Unpaired does not mean absent or zero");
  await page.getByRole("link", { name: "Overview", exact: true }).click();
  await expect(page.getByRole("region", { name: "Investment totals" })).not.toContainText("999999");
  await page.getByRole("link", { name: "Tools", exact: true }).click();
  await expect(bridge.getByLabel("Journal account")).toHaveValue("");
  await expect(pairs).toHaveCount(0);
  await bridge.getByLabel("Journal account").selectOption(account.id);
  await bridge.getByRole("checkbox", { name: /I checked that eToro account/ }).check();
  await bridge.getByRole("link", { name: `Update ${account.name} using independent evidence` }).click();
  await expect(page).toHaveURL(new RegExp(`/investment/update\\?account_id=${account.id}$`));
  await expect(page.getByRole("heading", { name: account.name, exact: true })).toBeVisible();
  expect(writes).toBe(0);
  expect(await page.evaluate(() => Object.keys(localStorage))).toEqual([]);
  expect(report.source_mode).toBe("recorded");
});

test("changing the account or journal revision clears temporary pairings and account confirmation", async ({ page }) => {
  const { report, account, holdings } = await fixture(page);
  report.accounts.push({ ...account, id: "another-broker", name: "Another synthetic broker" });
  await page.goto("/investment?view=tools");
  const bridge = page.getByRole("region", { name: "Review eToro against journal" });
  const selectAccount = bridge.getByLabel("Journal account");
  await selectAccount.selectOption(account.id);
  await bridge.getByRole("checkbox", { name: /I checked that eToro account/ }).check();
  await bridge.getByLabel("Provider row").selectOption("direct:101");
  await bridge.getByLabel("Recorded holding").selectOption(holdings[0].product_id);
  await bridge.getByRole("button", { name: "Add to this review" }).click();
  await selectAccount.selectOption("another-broker");
  await expect(bridge.getByRole("checkbox", { name: /I checked that eToro account/ })).not.toBeChecked();
  await expect(bridge.getByRole("list", { name: "Temporary holding pairings" })).toHaveCount(0);
  await selectAccount.selectOption(account.id);
  await bridge.getByRole("checkbox", { name: /I checked that eToro account/ }).check();
  await bridge.getByLabel("Provider row").selectOption("direct:101");
  await bridge.getByLabel("Recorded holding").selectOption(holdings[0].product_id);
  await bridge.getByRole("button", { name: "Add to this review" }).click();
  report.revision++;
  await page.getByRole("button", { name: "Refresh investment" }).click();
  await expect(bridge.getByLabel("Journal account")).toHaveValue("");
  await expect(bridge.getByRole("list", { name: "Temporary holding pairings" })).toHaveCount(0);
});

for (const reason of ["demo", "wrong account", "failed read", "conflicting session",
  "missing session snapshot", "future clock", "duplicate source row", "unavailable journal"]) {
  test(`does not open a bridge with ${reason}`, async ({ page }) => {
    const { report, snapshot, session, portfolio, connection } = await fixture(page);
    if (reason === "demo") {
      snapshot.environment = "demo"; connection.environment = "demo";
    } else if (reason === "wrong account") {
      snapshot.account_id = 99999;
    } else if (reason === "failed read") {
      portfolio.last_status = "refresh_failed";
    } else if (reason === "conflicting session") {
      session.last_status = "refresh_failed";
    } else if (reason === "missing session snapshot") {
      session.snapshot_available = false;
    } else if (reason === "future clock") {
      snapshot.source_as_of = "2031-01-01T00:00:00Z";
    } else if (reason === "duplicate source row") {
      snapshot.direct_positions.push(position(101));
    } else {
      report.source_mode = "demo";
    }
    await page.goto("/investment?view=tools");
    await expect(page.getByRole("heading", { name: "Latest eToro read" })).toBeVisible();
    await expect(page.getByRole("region", { name: "Review eToro against journal" })).toHaveCount(0);
    if (reason !== "unavailable journal") {
      await expect(page.getByRole("region", { name: "eToro portfolio" })).toContainText("Side-by-side review is unavailable");
    }
    expect(session.state).toBe("connected");
  });
}
