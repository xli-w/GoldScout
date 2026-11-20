import { expect, test } from "@playwright/test";

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
  await page.getByLabel("Amount set aside for this goal (GBP, optional)").fill("5000.00");
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
  await expect(page.getByLabel("Amount set aside for this goal (GBP, optional)")).toHaveValue("5000.00");
});
