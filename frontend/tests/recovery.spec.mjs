import { expect, test } from "./fixtures.mjs";

test("backup download and restore rehearsal require explicit choices and report verification", async ({ page }) => {
  const report = await (await page.request.get("/api/investment/v1/snapshot")).json();
  report.source_mode = "recorded";
  await page.route("**/api/v1/settings", route => route.fulfill({
    json: { mode: "recorded", workspace: "investment" },
  }));
  let snapshotFails = false;
  await page.route("**/api/investment/v1/snapshot", route => snapshotFails
    ? route.fulfill({ status: 503, json: { error: "synthetic_snapshot_failure" } })
    : route.fulfill({ json: report }));
  let sessionRequests = 0;
  await page.route("**/api/investment/v1/backup/session", route => {
    sessionRequests++;
    return route.fulfill({ json: {
      enabled: true, csrf: "synthetic-backup-csrf", archive_limit_bytes: 33558528,
      database_location: "C:\\synthetic\\investment.sqlite3",
    } });
  });
  let downloadRequests = 0;
  let rehearsalRequests = 0;
  let signalRehearsalStarted;
  let releaseRehearsal;
  const rehearsalStarted = new Promise(resolve => { signalRehearsalStarted = resolve; });
  const rehearsalReleased = new Promise(resolve => { releaseRehearsal = resolve; });
  await page.route("**/api/investment/v1/backup/download", async route => {
    downloadRequests++;
    expect(route.request().method()).toBe("POST");
    expect(route.request().headers()["x-csrf-token"]).toBe("synthetic-backup-csrf");
    expect(route.request().postDataJSON()).toEqual({});
    await route.fulfill({
      status: 200, contentType: "application/zip",
      headers: { "content-disposition": 'attachment; filename="goldscout-verified-backup-test.zip"' },
      body: "synthetic archive bytes",
    });
  });
  await page.route("**/api/investment/v1/backup/rehearsal", async route => {
    rehearsalRequests++;
    expect(route.request().method()).toBe("POST");
    expect(route.request().headers()["content-type"]).toBe("application/zip");
    expect(route.request().headers()["x-csrf-token"]).toBe("synthetic-backup-csrf");
    expect(route.request().postDataBuffer()).toEqual(Buffer.from("synthetic archive bytes"));
    signalRehearsalStarted();
    await rehearsalReleased;
    await route.fulfill({ json: {
      status: "verified", schema_version: 2, portfolio_id: "synthetic-portfolio",
      revision: 3, copy_size_bytes: 2048, rehearsal_size_bytes: 2048,
      event_count: 7, event_limit: 10000, evidence_bytes: 4096,
      evidence_limit_bytes: 8388608, database_limit_bytes: 33554432,
    } });
  });

  await page.goto("/investment");
  expect(sessionRequests).toBe(0);
  await page.getByRole("link", { name: "Tools", exact: true }).click();
  expect(sessionRequests).toBe(0);
  expect(downloadRequests).toBe(0);
  expect(rehearsalRequests).toBe(0);
  await page.getByText("Backup and recovery tools", { exact: true }).click();
  const recovery = page.getByRole("region", { name: "Verified backup and recovery" });
  await expect(recovery).toContainText("C:\\synthetic\\investment.sqlite3");
  const downloadWait = page.waitForEvent("download");
  await recovery.getByRole("button", { name: "Create and download verified backup" }).click();
  const download = await downloadWait;
  await expect(recovery).toContainText("a download was requested as goldscout-verified-backup-test.zip");
  await expect(recovery).toContainText("Confirm it appears in your browser's download location");
  expect(download.suggestedFilename()).toBe("goldscout-verified-backup-test.zip");
  await recovery.getByLabel("Verified backup ZIP").setInputFiles({
    name: "backup.zip", mimeType: "application/zip", buffer: Buffer.from("synthetic archive bytes"),
  });
  await recovery.getByRole("button", { name: "Run restore rehearsal" }).click();
  await rehearsalStarted;
  snapshotFails = true;
  await page.getByRole("button", { name: "Refresh investment" }).click();
  await expect(page.getByRole("heading", { name: "Investment data unavailable" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Verified backup and recovery" })).toHaveCount(0);
  releaseRehearsal();
  snapshotFails = false;
  await page.getByRole("button", { name: "Refresh investment" }).click();
  await expect(recovery.getByRole("status")).toContainText("Restore rehearsal passed");
  await expect(recovery).toContainText("7 of 10000");
  await page.getByText("Backup and recovery tools", { exact: true }).click();
  await page.getByText("Backup and recovery tools", { exact: true }).click();
  await expect(recovery.getByRole("status")).toContainText("Restore rehearsal passed");
  expect(await recovery.getByLabel("Verified backup ZIP").evaluate(input => input.files.length)).toBe(1);
  await page.getByRole("link", { name: "Overview", exact: true }).click();
  await page.getByRole("link", { name: "Tools", exact: true }).click();
  await expect(recovery.getByRole("status")).toContainText("Restore rehearsal passed");
  expect(sessionRequests).toBe(1);
  expect(downloadRequests).toBe(1);
  expect(rehearsalRequests).toBe(1);
});
