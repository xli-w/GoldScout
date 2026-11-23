import { expect, test } from "./fixtures.mjs";

async function fixture(page) {
  const report = await (await page.request.get("/api/investment/v1/snapshot")).json();
  report.source_mode = "recorded";
  report.complete = true;
  report.twr_pct = "0";
  report.max_drawdown_pct = "0";
  const account = report.accounts.find(item => item.kind === "broker" && item.status === "active");
  const holding = report.holdings.find(item => item.account_id === account.id);
  account.cash_observation = {
    balance_gbp: "99", difference_gbp: "-1",
    timestamp: "2026-01-01T12:00:00.000001Z", available_at: "2026-01-01T12:00:00.000002Z",
    source: "Synthetic dated statement", evidence: "Synthetic cash evidence",
  };
  const session = {
    enabled: true, csrf: "synthetic-csrf", opened: true,
    portfolio_id: report.portfolio_id, revision: report.revision,
    accounts: report.accounts.filter(item => item.status === "active").map(({ id, name, kind }) => ({ id, name, kind })),
    products: [...new Map(report.holdings.map(item => [item.product_id, {
      id: item.product_id, name: item.name, sleeve: item.sleeve,
      quantity_increment: "1", minimum_notional_gbp: "1",
    }])).values()],
    current_balances: Object.fromEntries(report.accounts.map(item => [item.id, item.cash_gbp])),
    current_holdings: report.holdings.map(({ account_id, product_id, quantity }) => ({ account_id, product_id, quantity })),
    latest_correctable_event: null, correctable_events: [],
  };
  await page.route("**/api/v1/settings", route => route.fulfill({
    json: { mode: "recorded", workspace: "investment" },
  }));
  await page.route("**/api/investment/v1/snapshot", route => route.fulfill({ json: report }));
  await page.route("**/api/investment/v1/recording/session", route => route.fulfill({ json: session }));
  await page.clock.setFixedTime(new Date("2030-01-01T12:00:00Z"));
  return { report, session, account, holding };
}

test("recording drafts resume only for the same journal revision and never reuse a preview", async ({ page }) => {
  const { account, session } = await fixture(page);
  let previews = 0;
  await page.route("**/api/investment/v1/recording/preview", route => {
    previews++;
    return route.abort();
  });
  const url = `/investment/record?record=account-update&account_id=${account.id}&kind=cash_observation`;
  await page.goto(url);
  await page.getByLabel("Observed GBP balance").fill("99.50");
  await page.getByLabel("Statement/source description").fill("Synthetic monthly statement");
  await page.getByLabel("Applies at (local time)").fill("2030-01-01T11:00");
  await page.getByLabel("Source available from (local time)").fill("2030-01-01T11:00");
  await page.getByLabel("Source description / correction reason").fill("Synthetic comparison");
  await expect.poll(() => page.evaluate(() => {
    const key = Object.keys(localStorage).find(item => item.includes("recording-draft"));
    return key ? JSON.parse(localStorage.getItem(key)).values.amount : null;
  })).toBe("99.50");

  await page.reload();
  await expect(page.getByText("A draft was restored from this browser. Its preview and confirmation were not saved; check all details and request a fresh preview."))
    .toBeVisible();
  await expect(page.getByLabel("Observed GBP balance")).toHaveValue("99.50");
  await expect(page.getByLabel("Statement/source description")).toHaveValue("Synthetic monthly statement");
  expect(previews).toBe(0);
  await page.getByRole("button", { name: "Preview record" }).click();
  await expect.poll(() => previews).toBe(1);

  session.revision++;
  await page.reload();
  await expect(page.getByRole("alert")).toContainText("no longer matches the current journal revision");
  await expect(page.getByLabel("Observed GBP balance")).toHaveValue("");
  await expect(page.getByRole("button", { name: "Discard saved draft" })).toBeVisible();
  expect(previews).toBe(1);
});

test("malformed saved recording drafts are surfaced and can be explicitly discarded", async ({ page }) => {
  const { account, report } = await fixture(page);
  const route = `?record=account-update&account_id=${account.id}&kind=cash_observation`;
  const key = `goldscout.recording-draft.v1.${encodeURIComponent(report.portfolio_id)}.${encodeURIComponent(route)}`;
  await page.goto("/");
  await page.evaluate(savedKey => localStorage.setItem(savedKey, "{"), key);
  await page.goto(`/investment/record${route}`);
  await expect(page.getByRole("alert")).toContainText("saved recording draft could not be read");
  await expect(page.getByLabel("Observed GBP balance")).toHaveValue("");
  await page.getByRole("button", { name: "Discard saved draft" }).click();
  await expect(page.getByText(/saved recording draft could not be read/)).toHaveCount(0);
  expect(await page.evaluate(savedKey => localStorage.getItem(savedKey), key)).toBeNull();
});

test("guided account review links evidence to safe drafts without making updates", async ({ page }) => {
  const { account, holding } = await fixture(page);
  let writes = 0;
  page.on("request", request => { if (request.method() === "POST") writes++; });
  await page.goto("/investment");
  await page.getByRole("link", { name: `Update ${account.name}`, exact: true }).click();
  await expect(page.getByRole("heading", { name: "Update an account" })).toBeVisible();
  const evidence = page.getByRole("region", { name: "Account evidence to review" });
  await expect(evidence).toContainText("difference at that time -£1.00");
  await expect(evidence).toContainText("do not enter a balancing deposit or withdrawal");
  await expect(page.getByRole("region", { name: "Guided account update" }))
    .toContainText("every holding in the portfolio, not just this account");
  await evidence.getByText("Cash-check source", { exact: true }).click();
  await expect(evidence).toContainText("2026-01-01T12:00:00.000002Z");
  await expect(evidence).toContainText("Synthetic dated statement");
  await page.getByRole("link", { name: "Check this account's cash" }).click();
  await expect(page.getByLabel("What are you recording?")).toHaveValue("cash_observation");
  await expect(page.getByRole("combobox", { name: "Account", exact: true })).toHaveValue(account.id);
  await page.getByRole("link", { name: "Return to the account review" }).click();
  await page.getByRole("link", { name: `Update price for ${holding.name}`, exact: true }).click();
  await expect(page.getByLabel("What are you recording?")).toHaveValue("mark");
  await expect(page.getByRole("combobox", { name: "Account", exact: true })).toHaveValue(account.id);
  await expect(page.getByRole("combobox", { name: "Investment", exact: true })).toHaveValue(holding.product_id);
  expect(writes).toBe(0);
});

test("unavailable account and stale holding targets never fall back to another selection", async ({ page }) => {
  const { session, account, holding } = await fixture(page);
  await page.goto("/investment/update?account_id=not-included");
  await expect(page.getByRole("alert")).toContainText("No other account has been selected");
  await expect(page.getByRole("link", { name: "Check this account's cash" })).toHaveCount(0);
  session.current_holdings = [];
  await page.goto(`/investment/record?record=account-update&account_id=${account.id}&kind=mark&product_id=${holding.product_id}`);
  await expect(page.getByRole("alert")).toContainText("No alternative was selected");
  await expect(page.getByRole("button", { name: "Preview record" })).toHaveCount(0);
  await page.goto(`/investment/record?record=account-update&account_id=${account.id}&kind=unexpected`);
  await expect(page.getByRole("alert")).toContainText("No alternative was selected");
});

test("savings terms and fees link directly to the selected account without writes", async ({ page }) => {
  const { session, account } = await fixture(page);
  const savings = session.accounts.find(item => item.kind === "deposit");
  expect(savings).toBeTruthy();
  let posts = 0;
  page.on("request", request => { if (request.method() === "POST") posts++; });
  await page.goto(`/investment/update?account_id=${savings.id}`);
  await page.getByRole("link", { name: "Record this account's savings terms" }).click();
  await expect(page.getByLabel("What are you recording?")).toHaveValue("savings_terms");
  await expect(page.getByRole("combobox", { name: "Account", exact: true })).toHaveValue(savings.id);
  await expect(page.getByRole("combobox", { name: "Account", exact: true }).locator(`option[value="${account.id}"]`)).toHaveCount(0);
  await page.getByRole("link", { name: "Return to the account review" }).click();
  await page.getByRole("link", { name: "An account or platform charge was paid" }).click();
  await expect(page.getByLabel("What are you recording?")).toHaveValue("account_fee");
  await expect(page.getByRole("combobox", { name: "Account", exact: true })).toHaveValue(savings.id);
  await page.goto(`/investment/update?account_id=${account.id}`);
  await expect(page.getByRole("link", { name: "Record this account's savings terms" })).toHaveCount(0);
  await page.getByRole("link", { name: "An account or platform charge was paid" }).click();
  await expect(page.getByLabel("What are you recording?")).toHaveValue("account_fee");
  await expect(page.getByRole("combobox", { name: "Account", exact: true })).toHaveValue(account.id);
  await page.goto(`/investment/record?record=account-update&account_id=${account.id}&kind=savings_terms`);
  await expect(page.getByRole("alert")).toContainText("No alternative was selected");
  expect(posts).toBe(0);
});

test("savings terms preview and confirm preserve sourced fields and unknowns", async ({ page }) => {
  const { report, session } = await fixture(page);
  const savings = session.accounts.find(item => item.kind === "deposit");
  report.accounts.find(item => item.id === savings.id).terms = {
    annual_rate_pct: "3.5", rate_basis: "gross", effective_from: "2026-01-01",
    observed_at: "2026-01-01T10:00:00Z", available_at: "2026-01-01T10:00:00Z",
    source: "Synthetic previous notice", maturity_date: "2031-01-01", notice_days: 90,
  };
  let submitted;
  let confirmations = 0;
  await page.route("**/api/investment/v1/recording/preview", async route => {
    submitted = route.request().postDataJSON().event;
    await route.fulfill({ json: {
      preview_id: "terms-preview", revision: report.revision, event: submitted,
      before: report, after: { ...report, revision: report.revision + 1 },
      expires_at: "2030-01-01T12:05:00Z",
    } });
  });
  await page.route("**/api/investment/v1/recording/confirm", async route => {
    confirmations++;
    await route.fulfill({ json: { id: submitted.id, revision: report.revision + 1 } });
  });
  await page.goto(`/investment/update?account_id=${savings.id}`);
  await page.getByRole("link", { name: "Record this account's savings terms" }).click();
  await page.getByLabel("Annual rate (%) (optional)").fill("4.25");
  await page.getByLabel("Rate basis").selectOption("AER");
  await page.getByLabel("Terms effective from").fill("2030-02-01");
  await page.getByLabel("Notice days (optional)").fill("30");
  await page.getByLabel("Terms source", { exact: true }).fill("Synthetic rate notice");
  await page.getByLabel("Terms observed at (local time)").fill("2030-01-01T11:00");
  await page.getByLabel("Source available from (local time)").fill("2030-01-01T11:30");
  await page.getByLabel("Source description / correction reason").fill("Synthetic announced terms");
  await page.reload();
  await expect(page.getByLabel("Annual rate (%) (optional)")).toHaveValue("4.25");
  await expect(page.getByLabel("Terms effective from")).toHaveValue("2030-02-01");
  await expect(page.getByRole("combobox", { name: "Account", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Preview record" }).click();
  await expect(page.getByRole("region", { name: "Previous savings terms" })).toContainText("2031-01-01");
  await expect(page.getByRole("region", { name: "Previous savings terms" })).toContainText("Blank optional fields become unknown");
  await expect(page.getByRole("region", { name: "Record summary" })).toContainText(/Maturity date\s*Not recorded/);
  expect(submitted).toMatchObject({
    type: "savings_terms", account_id: savings.id, annual_rate_pct: "4.25", rate_basis: "AER",
    effective_from: "2030-02-01", maturity_date: null, notice_days: 30,
    source: "Synthetic rate notice", evidence: "Synthetic announced terms",
  });
  expect(confirmations).toBe(0);
  await page.getByRole("button", { name: "Confirm and append record" }).click();
  await expect(page).toHaveURL(new RegExp(`/investment/update\\?account_id=${savings.id}$`));
  expect(confirmations).toBe(1);
});

test("savings terms reject invalid dates, source, rate and notice before preview", async ({ page }) => {
  const { session } = await fixture(page);
  const savings = session.accounts.find(item => item.kind === "deposit");
  let previews = 0;
  await page.route("**/api/investment/v1/recording/preview", route => { previews++; return route.abort(); });
  await page.goto(`/investment/record?record=account-update&account_id=${savings.id}&kind=savings_terms`);
  await page.getByLabel("Terms effective from").fill("2030-02-01");
  await page.getByLabel("Maturity date (optional)").fill("2030-01-01");
  await page.getByLabel("Terms source", { exact: true }).fill("Synthetic notice");
  await page.getByLabel("Terms observed at (local time)").fill("2030-01-01T11:00");
  await page.getByLabel("Source available from (local time)").fill("2030-01-01T11:30");
  await page.getByLabel("Source description / correction reason").fill("Synthetic source");
  await page.getByRole("button", { name: "Preview record" }).click();
  await expect(page.getByRole("status")).toContainText("maturity date on or after");
  await page.getByLabel("Maturity date (optional)").fill("");
  await page.getByLabel("Terms source", { exact: true }).fill(" ");
  await page.getByRole("button", { name: "Preview record" }).click();
  await expect(page.getByRole("status")).toContainText("Enter a source");
  await page.getByLabel("Terms source", { exact: true }).fill("Synthetic notice");
  await page.getByLabel("Annual rate (%) (optional)").fill("101");
  await page.getByLabel("Rate basis").selectOption("AER");
  await page.getByRole("button", { name: "Preview record" }).click();
  await expect(page.getByRole("status")).toContainText("valid annual rate");
  await page.getByLabel("Annual rate (%) (optional)").fill("");
  await page.getByLabel("Rate basis").selectOption("");
  await page.getByLabel("Notice days (optional)").fill("3651");
  await page.getByRole("button", { name: "Preview record" }).click();
  await expect(page.getByRole("status")).toContainText("Notice days");
  await page.getByLabel("Notice days (optional)").fill("");
  await page.getByLabel("Source available from (local time)").fill("2030-01-01T10:00");
  await page.getByRole("button", { name: "Preview record" }).click();
  await expect(page.getByRole("status")).toContainText("cannot be earlier");
  expect(previews).toBe(0);
});

test("guided terms rejects a restored draft targeting a different account", async ({ page }) => {
  const { report, session } = await fixture(page);
  const savings = session.accounts.find(item => item.kind === "deposit");
  const route = `?record=account-update&account_id=${savings.id}&kind=savings_terms`;
  const key = `goldscout.recording-draft.v1.${encodeURIComponent(report.portfolio_id)}.${encodeURIComponent(route)}`;
  let previews = 0;
  await page.route("**/api/investment/v1/recording/preview", route => { previews++; return route.abort(); });
  await page.goto(`/investment/record${route}`);
  await page.getByLabel("Terms effective from").fill("2030-02-01");
  await page.getByLabel("Terms source", { exact: true }).fill("Synthetic notice");
  await page.getByLabel("Terms observed at (local time)").fill("2030-01-01T11:00");
  await page.getByLabel("Source available from (local time)").fill("2030-01-01T11:30");
  await page.getByLabel("Source description / correction reason").fill("Synthetic source");
  await expect.poll(() => page.evaluate(savedKey => {
    const raw = localStorage.getItem(savedKey);
    return raw ? JSON.parse(raw).values.evidence : null;
  }, key)).toBe("Synthetic source");
  await page.evaluate(savedKey => {
    const saved = JSON.parse(localStorage.getItem(savedKey));
    saved.values.accountId = "different-account";
    localStorage.setItem(savedKey, JSON.stringify(saved));
  }, key);
  await page.reload();
  await page.getByRole("button", { name: "Preview record" }).click();
  await expect(page.getByRole("status")).toContainText("draft does not match the guided account");
  expect(previews).toBe(0);
});

test("performance loss is explained and requires acknowledgement before a verified save", async ({ page }) => {
  const { report, account } = await fixture(page);
  let previewEvent;
  let confirmations = 0;
  await page.route("**/api/investment/v1/recording/preview", async route => {
    previewEvent = route.request().postDataJSON().event;
    await route.fulfill({ json: {
      preview_id: "synthetic-preview", revision: report.revision, event: previewEvent,
      before: report,
      after: { ...report, revision: report.revision + 1, twr_pct: null, max_drawdown_pct: null,
        warnings: ["Synthetic missing flow-time valuation."] },
      expires_at: "2030-01-01T12:05:00Z",
    } });
  });
  await page.route("**/api/investment/v1/recording/confirm", async route => {
    confirmations++;
    await route.fulfill({ json: { id: previewEvent.id, revision: report.revision + 1 } });
  });
  await page.goto(`/investment/update?account_id=${account.id}`);
  await page.getByRole("link", { name: "Money came in from outside my tracked accounts" }).click();
  await expect(page.getByText(/Before previewing: a deposit or withdrawal/)).toBeVisible();
  await page.getByLabel("GBP amount").fill("100");
  await page.getByLabel("Applies at (local time)").fill("2030-01-01T12:00");
  await page.getByLabel("Source available from (local time)").fill("2030-01-01T12:00");
  await page.getByLabel("Source description / correction reason").fill("Synthetic deposit receipt");
  await page.getByRole("button", { name: "Preview record" }).click();
  const impact = page.getByRole("region", { name: "Valuation and performance impact" });
  await expect(impact).toContainText("0.00% → Unavailable");
  await expect(impact.getByRole("alert")).toContainText("previously available figures unavailable");
  await expect(impact).toContainText("Synthetic missing flow-time valuation.");
  const confirm = page.getByRole("button", { name: "Confirm and append record" });
  expect(confirmations).toBe(0);
  await expect(confirm).toBeEnabled();
  await confirm.click();
  await expect(page).toHaveURL(new RegExp(`/investment/update\\?account_id=${account.id}$`));
  expect(confirmations).toBe(1);
  expect(previewEvent).toMatchObject({ type: "contribution", account_id: account.id, amount_gbp: "100" });
  await expect(page.getByRole("region", { name: "Account evidence to review" })).toBeVisible();
});

test("malformed performance evidence cannot produce a confirmable preview", async ({ page }) => {
  const { account } = await fixture(page);
  await page.route("**/api/investment/v1/recording/preview", async route => route.fulfill({ json: {
    preview_id: "malformed-preview", revision: 1, event: route.request().postDataJSON().event,
    before: { equity_gbp: "100" }, after: { equity_gbp: "100" }, expires_at: "2030-01-01T12:05:00Z",
  } }));
  await page.goto(`/investment/record?record=account-update&account_id=${account.id}&kind=interest`);
  await page.getByLabel("GBP amount").fill("1");
  await page.getByLabel("Why does this income belong to this role?").fill("Synthetic account interest");
  await page.getByLabel("Applies at (local time)").fill("2030-01-01T12:00");
  await page.getByLabel("Source available from (local time)").fill("2030-01-01T12:00");
  await page.getByLabel("Source description / correction reason").fill("Synthetic interest receipt");
  await page.getByRole("button", { name: "Preview record" }).click();
  await expect(page.getByRole("status")).toContainText("performance evidence failed validation");
  await expect(page.getByRole("button", { name: "Confirm and append record" })).toHaveCount(0);
});

test("a failed refresh after a save keeps the committed outcome visible", async ({ page }) => {
  const { account } = await fixture(page);
  await page.goto(`/investment/update?account_id=${account.id}`);
  await expect(page.getByRole("region", { name: "Account evidence to review" })).toBeVisible();
  await page.evaluate(() => {
    history.replaceState({ ...history.state, usr: { recordSaved: true,
      reviewNotice: "Review progress could not be updated." } }, "");
  });
  let releaseRefresh;
  let signalStarted;
  const refreshStarted = new Promise(resolve => { signalStarted = resolve; });
  const refreshAllowed = new Promise(resolve => { releaseRefresh = resolve; });
  await page.route("**/api/investment/v1/snapshot", async route => {
    signalStarted();
    await refreshAllowed;
    await route.fulfill({ status: 503, json: { error: "synthetic_unavailable" } });
  });
  await page.reload({ waitUntil: "domcontentloaded" });
  try {
    await refreshStarted;
    await expect(page.getByText("Your record was saved.", { exact: false })).toBeVisible();
    await expect(page.getByRole("region", { name: "Account evidence to review" })).toHaveCount(0);
  } finally {
    releaseRefresh();
  }
  await expect(page.getByText("Your record was saved.", { exact: false })).toBeVisible();
  await expect(page.getByRole("region", { name: "Account evidence to review" })).toHaveCount(0);
  await expect(page.getByRole("alert")).toContainText("latest account evidence could not be refreshed");
  await expect(page.getByRole("alert")).not.toContainText("No update has been started");
  await expect(page.getByText(/Review progress could not be updated/)).toBeVisible();
});

test("a generic save shows its outcome after the recording page reloads", async ({ page }) => {
  const { report, session, account } = await fixture(page);
  let event;
  await page.route("**/api/investment/v1/recording/preview", route => {
    event = route.request().postDataJSON().event;
    return route.fulfill({ json: { preview_id: "generic-preview", revision: report.revision,
      event, before: report, after: { ...report, revision: report.revision + 1 },
      expires_at: "2030-01-01T12:05:00Z" } });
  });
  await page.route("**/api/investment/v1/recording/confirm", route => {
    report.revision++;
    session.revision = report.revision;
    return route.fulfill({ json: { id: event.id, revision: report.revision } });
  });
  await page.goto("/investment/record");
  await page.getByLabel("What are you recording?").selectOption("interest");
  await page.getByRole("combobox", { name: "Account", exact: true }).selectOption(account.id);
  await page.getByLabel("GBP amount").fill("1");
  await page.getByLabel("Why does this income belong to this role?").fill("Synthetic interest");
  await page.getByLabel("Applies at (local time)").fill("2030-01-01T12:00");
  await page.getByLabel("Source available from (local time)").fill("2030-01-01T12:00");
  await page.getByLabel("Source description / correction reason").fill("Synthetic interest");
  await page.getByRole("button", { name: "Preview record" }).click();
  await page.getByRole("button", { name: "Confirm and append record" }).click();
  await expect(page).toHaveURL(/\/investment\/record$/);
  await expect(page.getByText(`Previous record ${event.id} was saved.`, { exact: false })).toBeVisible();
  await expect(page.getByLabel("What are you recording?")).toHaveValue("");
});

test("cash check preview explains the difference without claiming to update cash", async ({ page }) => {
  const { report, account } = await fixture(page);
  let confirmations = 0;
  await page.route("**/api/investment/v1/recording/confirm", route => {
    confirmations++;
    return route.abort();
  });
  await page.route("**/api/investment/v1/recording/preview", async route => {
    const event = route.request().postDataJSON().event;
    const observation = {
      account_id: account.id, balance_gbp: event.balance_gbp, difference_gbp: "-0.001",
      timestamp: event.timestamp, available_at: event.available_at,
      source: event.source, evidence: event.evidence,
    };
    await route.fulfill({ json: {
      preview_id: "cash-preview", revision: report.revision, event, before: report,
      after: { ...report, revision: report.revision + 1,
        cash_observations: [...report.cash_observations, observation] },
      expires_at: "2030-01-01T12:05:00Z",
    } });
  });
  await page.goto(`/investment/record?record=account-update&account_id=${account.id}&kind=cash_observation`);
  await page.getByLabel("Observed GBP balance").fill("99.999");
  await page.getByLabel("Statement/source description").fill("Synthetic statement");
  await page.getByLabel("Applies at (local time)").fill("2030-01-01T12:00");
  await page.getByLabel("Source available from (local time)").fill("2030-01-01T12:00");
  await page.getByLabel("Source description / correction reason").fill("Synthetic cash check");
  await page.getByRole("button", { name: "Preview record" }).click();
  const comparison = page.getByRole("region", { name: "Preview cash comparison" });
  await expect(comparison).toContainText("£99.999");
  await expect(comparison).toContainText("-£0.001");
  await expect(comparison).toContainText("does not adjust cash or resolve the difference");
  await expect(page.getByRole("button", { name: "Confirm and append record" })).toBeEnabled();
  await expect(page.getByRole("checkbox", { name: /I understand that these previously available figures/ })).toHaveCount(0);
  expect(confirmations).toBe(0);
});

test("guided transfer from the second account submits the displayed other account", async ({ page }) => {
  const { report, session } = await fixture(page);
  const source = session.accounts[1];
  const destination = session.accounts[0];
  let submitted;
  await page.route("**/api/investment/v1/recording/preview", async route => {
    submitted = route.request().postDataJSON().event;
    await route.fulfill({ json: {
      preview_id: "transfer-preview", revision: report.revision, event: submitted,
      before: report, after: { ...report, revision: report.revision + 1 },
      expires_at: "2030-01-01T12:05:00Z",
    } });
  });
  await page.goto(`/investment/record?record=account-update&account_id=${source.id}&kind=transfer`);
  await expect(page.getByRole("combobox", { name: "Account", exact: true })).toHaveValue(source.id);
  await expect(page.getByLabel("Destination account")).toHaveValue(destination.id);
  await page.getByLabel("GBP amount").fill("1");
  await page.getByLabel("Applies at (local time)").fill("2030-01-01T12:00");
  await page.getByLabel("Source available from (local time)").fill("2030-01-01T12:00");
  await page.getByLabel("Source description / correction reason").fill("Synthetic transfer receipt");
  await page.getByRole("button", { name: "Preview record" }).click();
  await expect(page.getByRole("button", { name: "Confirm and append record" })).toBeVisible();
  expect(submitted).toMatchObject({
    type: "transfer", account_id: source.id, destination_account_id: destination.id,
  });
});

for (const loss of ["value", "drawdown", "already unavailable"]) {
  test(`preview reports ${loss} independently without an acknowledgement gate`, async ({ page }) => {
    const { report, account } = await fixture(page);
    if (loss === "already unavailable" || loss === "value") {
      report.twr_pct = null;
      report.max_drawdown_pct = null;
    }
    await page.route("**/api/investment/v1/recording/preview", async route => {
      const after = { ...report, revision: report.revision + 1 };
      if (loss === "value") {
        after.complete = false;
        after.equity_gbp = null;
        after.as_of = null;
        after.since_baseline_gain_gbp = null;
      }
      if (loss === "drawdown") after.max_drawdown_pct = null;
      await route.fulfill({ json: {
        preview_id: "impact-preview", revision: report.revision,
        event: route.request().postDataJSON().event, before: report, after,
        expires_at: "2030-01-01T12:05:00Z",
      } });
    });
    await page.goto(`/investment/record?record=account-update&account_id=${account.id}&kind=contribution`);
    await page.getByLabel("GBP amount").fill("1");
    await page.getByLabel("Applies at (local time)").fill("2030-01-01T12:00");
    await page.getByLabel("Source available from (local time)").fill("2030-01-01T12:00");
    await page.getByLabel("Source description / correction reason").fill("Synthetic evidence");
    await page.getByRole("button", { name: "Preview record" }).click();
    const confirm = page.getByRole("button", { name: "Confirm and append record" });
    await expect(confirm).toBeEnabled();
    if (loss === "already unavailable") {
      await expect(page.getByRole("region", { name: "Valuation and performance impact" }))
        .toContainText("Return reporting is unavailable after this record");
    } else {
      await expect(page.getByRole("region", { name: "Valuation and performance impact" }).getByRole("alert"))
        .toContainText("previously available figures unavailable");
      await page.getByRole("button", { name: "Cancel preview" }).click();
      await page.getByRole("button", { name: "Preview record" }).click();
      await expect(confirm).toBeEnabled();
    }
  });
}

test("lost confirmation after performance impact retries the same reference after reload", async ({ page }) => {
  const { report, account } = await fixture(page);
  let previewEvent;
  const previewId = "uncertain-impact-preview";
  const references = [];
  let appends = 0;
  await page.route("**/api/investment/v1/recording/preview", async route => {
    previewEvent = route.request().postDataJSON().event;
    await route.fulfill({ json: {
      preview_id: previewId, revision: report.revision, event: previewEvent, before: report,
      after: { ...report, revision: report.revision + 1, twr_pct: null, max_drawdown_pct: null },
      expires_at: "2030-01-01T12:05:00Z",
    } });
  });
  await page.route("**/api/investment/v1/recording/confirm", async route => {
    references.push(route.request().postDataJSON().preview_id);
    if (references.length === 1) {
      appends++;
      return route.abort("failed");
    }
    await route.fulfill({ json: { id: previewEvent.id, revision: report.revision + 1 } });
  });
  await page.goto(`/investment/record?record=account-update&account_id=${account.id}&kind=contribution`);
  await page.getByLabel("GBP amount").fill("1");
  await page.getByLabel("Applies at (local time)").fill("2030-01-01T12:00");
  await page.getByLabel("Source available from (local time)").fill("2030-01-01T12:00");
  await page.getByLabel("Source description / correction reason").fill("Synthetic receipt");
  await page.getByRole("button", { name: "Preview record" }).click();
  await page.getByRole("button", { name: "Confirm and append record" }).click();
  await expect(page.getByRole("status")).toContainText("save outcome is unknown");
  expect(await page.evaluate(() => {
    const event = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(event);
    return event.defaultPrevented;
  })).toBe(false);
  await page.reload();
  await expect(page.getByRole("heading", { name: "Check an unfinished save" })).toBeVisible();
  await page.getByRole("button", { name: "Retry same confirmation" }).click();
  await expect(page).toHaveURL(new RegExp(`/investment/update\\?account_id=${account.id}$`));
  expect(references).toEqual([previewId, previewId]);
  expect(appends).toBe(1);
});

test("navigating during a confirmation retains the pending save reference", async ({ page }) => {
  const { report, account } = await fixture(page);
  let previewEvent;
  const previewId = "in-flight-preview";
  const references = [];
  let firstSent;
  let releaseFirst;
  const firstStarted = new Promise(resolve => { firstSent = resolve; });
  const firstResponse = new Promise(resolve => { releaseFirst = resolve; });
  await page.route("**/api/investment/v1/recording/preview", route => {
    previewEvent = route.request().postDataJSON().event;
    return route.fulfill({ json: {
      preview_id: previewId, revision: report.revision, event: previewEvent, before: report,
      after: { ...report, revision: report.revision + 1 },
      expires_at: "2030-01-01T12:05:00Z",
    } });
  });
  await page.route("**/api/investment/v1/recording/confirm", async route => {
    references.push(route.request().postDataJSON().preview_id);
    if (references.length === 1) {
      firstSent();
      await firstResponse;
      await route.abort("failed").catch(() => {});
      return;
    }
    await route.fulfill({ json: { id: previewEvent.id, revision: report.revision + 1 } });
  });
  await page.goto(`/investment/record?record=account-update&account_id=${account.id}&kind=contribution`);
  await page.getByLabel("GBP amount").fill("1");
  await page.getByLabel("Applies at (local time)").fill("2030-01-01T12:00");
  await page.getByLabel("Source available from (local time)").fill("2030-01-01T12:00");
  await page.getByLabel("Source description / correction reason").fill("Synthetic receipt");
  await page.getByRole("button", { name: "Preview record" }).click();
  await page.getByRole("button", { name: "Confirm and append record" }).click();
  await firstStarted;
  await page.reload();
  releaseFirst();
  await expect(page.getByRole("heading", { name: "Check an unfinished save" })).toBeVisible();
  await page.getByRole("button", { name: "Retry same confirmation" }).click();
  await expect(page).toHaveURL(new RegExp(`/investment/update\\?account_id=${account.id}$`));
  expect(references).toEqual([previewId, previewId]);
});
