import { expect, test } from "@playwright/test";

test("settings persist preferences and keep entered credentials write-only", async ({ page }) => {
  let session = {
    enabled: true, csrf: "synthetic-settings-csrf", revision: 0,
    preferences: { compact_mode: false, review_interval_days: 30, savings_terms_interval_days: 90 },
    connection_revision: 0, etoro_credential_revision: "12",
    connection_settings_enabled: true,
    connection_overrides: {
      GOLDSCOUT_ETORO_API_KEY: false, GOLDSCOUT_ETORO_USER_KEY: false,
      TRADER_AI_OPENAI_KEY: false, TRADER_AI_ANTHROPIC_KEY: false,
      TRADER_AI_GEMINI_KEY: false, TRADER_PROXY_URL: false, TRADER_DNS_IPV4: false,
    },
  };
  const savedCredentials = [];
  await page.route("**/api/v1/settings", route => route.fulfill({
    json: { workspace: "investment", settings_enabled: true },
  }));
  await page.route("**/api/v1/settings/session", route => route.fulfill({ json: session }));
  await page.route("**/api/v1/settings/preferences", async route => {
    const body = route.request().postDataJSON();
    session = { ...session, revision: session.revision + 1, preferences: body.preferences };
    await route.fulfill({ json: { revision: session.revision, preferences: session.preferences } });
  });
  await page.route("**/api/v1/settings/connections", async route => {
    const body = route.request().postDataJSON();
    savedCredentials.push(body);
    session = {
      ...session, connection_revision: session.connection_revision + 1,
      etoro_credential_revision: "13",
      connection_overrides: {
        ...session.connection_overrides, GOLDSCOUT_ETORO_API_KEY: true,
      },
    };
    await route.fulfill({ json: {
      connection_revision: session.connection_revision,
      connection_overrides: session.connection_overrides,
      reauthorization_required: true,
    } });
  });

  await page.goto("/settings");
  await expect(page.getByRole("navigation", { name: "Settings and help navigation" })
    .getByRole("link")).toHaveText(["Settings", "Help"]);
  await expect(page.getByRole("navigation", { name: "Portfolio navigation" })
    .getByRole("link")).toHaveText(["Your portfolio", "Goals", "Market Intelligence"]);
  await expect(page.getByRole("link", { name: "Review checks", exact: true })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Private connections" })).toBeVisible();
  await page.getByLabel("Compact display").check();
  await page.getByLabel("Default account review interval").selectOption("14");
  await page.getByRole("button", { name: "Save preferences" }).click();
  await expect(page.locator(".shell")).toHaveClass(/compact-mode/);
  await expect(page.getByRole("status").filter({ hasText: "Preferences saved." })).toBeVisible();

  const secret = "synthetic-key-for-settings-test";
  await page.getByLabel("eToro API key").fill(secret);
  await page.getByLabel("IPv4 DNS server").fill("8.8.8.8");
  await page.getByRole("button", { name: "Save connection settings" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Re-enable eToro before another read" }))
    .toBeVisible();
  expect(savedCredentials).toHaveLength(1);
  expect(savedCredentials[0].updates.GOLDSCOUT_ETORO_API_KEY).toBe(secret);
  expect(savedCredentials[0].updates.TRADER_DNS_IPV4).toBe("8.8.8.8");
  await expect(page.getByLabel("eToro API key")).toHaveValue("");
  expect(await page.locator("body").innerText()).not.toContain(secret);
});
