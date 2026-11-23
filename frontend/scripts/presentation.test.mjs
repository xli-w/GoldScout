import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";
import { reviewTimestamp } from "../src/api.ts";

async function loadTypeScript(path) {
  const source = readFileSync(new URL(path, import.meta.url), "utf8");
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  });
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);
}
const { confirmRecording, localClock, pendingRecording, recordingClocks, recordingVersion, RecordingPreviewExpiredError,
  RecordingRequestError, RecordingSavedStorageError, standardRecord } =
  await loadTypeScript("../src/recording.ts");

test("review dates pair the selected local timezone with unchanged UTC evidence", () => {
  const summer = reviewTimestamp("2026-07-01T09:00:00.123456Z", "Europe/London");
  assert.match(summer, /Local: 1 Jul 2026, 10:00:00 \(Europe\/London\)/);
  assert.ok(summer.endsWith("UTC: 2026-07-01T09:00:00.123456Z"));
  const winter = reviewTimestamp("2026-01-01T09:00:00Z", "Europe/London");
  assert.match(winter, /Local: 1 Jan 2026, 09:00:00/);
  const previousDay = reviewTimestamp("2026-07-01T01:00:00Z", "America/New_York");
  assert.match(previousDay, /30 Jun 2026, 21:00:00/);
  assert.ok(previousDay.endsWith("UTC: 2026-07-01T01:00:00Z"));
});

test("repeated daylight-saving local times remain distinguishable by exact UTC", () => {
  const before = reviewTimestamp("2026-10-25T00:30:00.000001Z", "Europe/London");
  const after = reviewTimestamp("2026-10-25T01:30:00.000002Z", "Europe/London");
  assert.match(before, /Local: 25 Oct 2026, 01:30:00/);
  assert.match(after, /Local: 25 Oct 2026, 01:30:00/);
  assert.notEqual(before, after);
  assert.ok(before.endsWith("UTC: 2026-10-25T00:30:00.000001Z"));
  assert.ok(after.endsWith("UTC: 2026-10-25T01:30:00.000002Z"));
});

test("missing and invalid review dates do not become plausible timestamps", () => {
  assert.equal(reviewTimestamp(null, "Europe/London"), "Not observed");
  assert.throws(() => reviewTimestamp("not-a-date", "Europe/London"), RangeError);
});

test("recording clocks require evidence and never silently move availability forward", () => {
  assert.throws(() => recordingClocks("", "", false), /valid local date/);
  assert.throws(() => recordingClocks("2026-01-02T10:00", "2026-01-02T09:00", false), /cannot be earlier/);
  const clocks = recordingClocks("2026-01-02T10:00", "2026-01-02T11:00", false);
  assert.equal(Date.parse(clocks.available_at) - Date.parse(clocks.timestamp), 3600000);
  const same = recordingClocks("2026-01-02T10:00", "", true);
  assert.equal(same.available_at, same.timestamp);
});

test("split records include the selected investment and purchases require an explicit fee", () => {
  const common = { id: "event", timestamp: "2026-01-02T10:00:00Z",
    available_at: "2026-01-02T10:00:00Z", evidence: "notice" };
  const input = { accountId: "account", destinationAccountId: "", productId: "fund",
    role: "unassigned", roleEvidence: "", amount: "", quantity: "2", price: "10", fee: "", ratio: "2" };
  assert.deepEqual(standardRecord("split", common, input),
    { ...common, type: "split", account_id: "account", currency: "GBP", product_id: "fund", ratio: "2" });
  assert.throws(() => standardRecord("split", common, { ...input, productId: "" }), /Choose the investment/);
  assert.throws(() => standardRecord("buy", common, input), /recorded fee/);
  assert.equal(standardRecord("buy", common, { ...input, fee: "0" }).fee_gbp, "0");
});

test("ambiguous and nonexistent local clock times are rejected", () => {
  const previous = process.env.TZ;
  process.env.TZ = "Europe/London";
  try {
    assert.throws(() => localClock("2026-10-25T01:30"), /occurs twice/);
    assert.throws(() => localClock("2026-03-29T01:30"), /valid local date/);
    assert.equal(localClock("2026-10-25T02:30"), "2026-10-25T02:30:00.000Z");
  } finally {
    if (previous === undefined) delete process.env.TZ;
    else process.env.TZ = previous;
  }
});

test("an uncertain save retains its exact confirmation reference for retry", async () => {
  const values = new Map();
  const storage = {
    getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: key => values.delete(key),
  };
  const pending = { previewId: "preview-1", eventId: "event-1", expectedRevision: 1 };
  const attempts = [];
  await assert.rejects(confirmRecording(storage, pending, async id => {
    attempts.push(id);
    throw new Error("response lost after commit");
  }), /response lost/);
  assert.deepEqual(pendingRecording(storage), pending);
  assert.deepEqual(JSON.parse([...values.values()][0]), pending);
  let otherSent = false;
  assert.equal(recordingVersion(storage), "preview-1");
  await assert.rejects(confirmRecording(storage, { previewId: "preview-2", eventId: "event-2", expectedRevision: 1 },
    async () => { otherSent = true; }), /unfinished save/);
  assert.equal(otherSent, false);
  await confirmRecording(storage, pendingRecording(storage), async id => {
    attempts.push(id); return { id: "event-1", revision: 1 };
  });
  assert.deepEqual(attempts, ["preview-1", "preview-1"]);
  assert.equal(pendingRecording(storage), null);
  let sent = false;
  await assert.rejects(confirmRecording({ ...storage, setItem() { throw new Error("storage unavailable"); } },
    pending, async () => { sent = true; }), /storage unavailable/);
  assert.equal(sent, false);
  await assert.rejects(confirmRecording(storage, pending, async () => ({ id: "different", revision: 1 })),
    /could not be verified/);
  assert.deepEqual(pendingRecording(storage), pending);
  await assert.rejects(confirmRecording(storage, pending, async () => ({ id: "event-1", revision: 2 })),
    /could not be verified/);
  assert.deepEqual(pendingRecording(storage), pending);
  await assert.rejects(confirmRecording(storage, pending, async () => ({})), /could not be verified/);
  assert.deepEqual(pendingRecording(storage), pending);
  await assert.rejects(confirmRecording(storage, pending, async () => {
    throw new RecordingRequestError("preview_expired");
  }), /preview_expired/);
  assert.deepEqual(pendingRecording(storage), pending);
  storage.removeItem("goldscout-pending-recording");
  await assert.rejects(confirmRecording(storage, pending, async () => {
    throw new RecordingRequestError("preview_expired");
  }), RecordingPreviewExpiredError);
  assert.equal(pendingRecording(storage), null);
  await assert.rejects(confirmRecording({ ...storage, removeItem() { throw new Error("cleanup failed"); } },
    pending, async () => ({ id: "event-1", revision: 1 })), RecordingSavedStorageError);
  assert.deepEqual(pendingRecording(storage), pending);
});

test("a retry cannot send until the shared preview version is recorded", async () => {
  const values = new Map();
  const storage = {
    getItem: key => values.get(key) ?? null,
    setItem: (key, value) => {
      if (key === "goldscout-recording-version") throw new Error("version storage failed");
      values.set(key, value);
    },
    removeItem: key => values.delete(key),
  };
  const pending = { previewId: "preview", eventId: "event", expectedRevision: 1 };
  let sent = false;
  for (let attempt = 0; attempt < 2; attempt++) {
    await assert.rejects(confirmRecording(storage, pending, async () => { sent = true; }),
      /version storage failed/);
    assert.deepEqual(pendingRecording(storage), pending);
    assert.equal(sent, false);
  }
});

test("an older pending reference remains visible but cannot be verified automatically", async () => {
  const raw = JSON.stringify({ previewId: "old-preview", eventId: "old-event" });
  const storage = { getItem: () => raw, setItem() {}, removeItem() {} };
  const pending = pendingRecording(storage);
  assert.deepEqual(pending, { previewId: "old-preview", eventId: "old-event", expectedRevision: null });
  let sent = false;
  await assert.rejects(confirmRecording(storage, pending, async () => { sent = true; }), /no expected revision/);
  assert.equal(sent, false);
});
