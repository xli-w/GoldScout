import assert from "node:assert/strict";
import test from "node:test";
import { activationReceiptMessage, hasUnrevokedEToroConnection, hasUncertainEToroRead,
  isActivationReceipt, recordEToroUncertainty, retainActivationReceipt,
  waitingForEToroStartup } from "../src/etoro-controls-model.ts";

test("disable remains available for an unrevoked connection needing reauthorization", () => {
  for (const state of ["startup_refresh_enabled", "reauthorization_required", "unavailable"]) {
    assert.equal(hasUnrevokedEToroConnection({ state, connection: { revoked_at: null } }), true);
  }
  assert.equal(hasUnrevokedEToroConnection({ state: "revoked",
    connection: { revoked_at: "2026-01-01T00:00:00Z" } }), false);
  assert.equal(hasUnrevokedEToroConnection({ state: "not_connected", connection: null }), false);
  assert.equal(hasUnrevokedEToroConnection(null), false);
});

test("uncertain startup or another tab's status requires reconciliation", () => {
  assert.equal(hasUncertainEToroRead("connection_read_outcome_uncertain", "success"), true);
  assert.equal(hasUncertainEToroRead("success", "cancellation_unconfirmed"), true);
  assert.equal(hasUncertainEToroRead("not_collected", "success"), false);
});

test("only an actual or unknown startup wait blocks manual refresh", () => {
  const session = { enabled: true, state: "startup_refresh_enabled",
    last_status: "not_collected", startup_pending: false };
  const portfolio = { state: "startup_refresh_enabled", last_status: "not_collected",
    snapshot: null, startup_pending: false };
  assert.equal(waitingForEToroStartup(session, portfolio), false);
  assert.equal(waitingForEToroStartup({ ...session, startup_pending: true }, portfolio), true);
  assert.equal(waitingForEToroStartup(session, { ...portfolio, startup_pending: true }), true);
  assert.equal(waitingForEToroStartup({ ...session, startup_pending: undefined }, portfolio), true);
  assert.equal(waitingForEToroStartup(session, { ...portfolio, snapshot: {} }), false);
});

test("activation receipt proves the original commit independently of current state", () => {
  const receipt = { committed: true, revision: 1, environment: "real", account_id: 12345 };
  assert.equal(isActivationReceipt(receipt, "real"), true);
  assert.equal(isActivationReceipt(receipt, "demo"), false);
  assert.equal(isActivationReceipt({ ...receipt, committed: false }, "real"), false);
  assert.equal(isActivationReceipt({ ...receipt, revision: 0 }, "real"), false);
});

test("receipt keeps commit evidence while reflecting current or unavailable status", () => {
  const receipt = { committed: true, revision: 1, environment: "demo", account_id: 12345 };
  const original = "An earlier activation committed for demo account 12345, revision 1.";
  assert.equal(activationReceiptMessage(receipt, { state: "startup_refresh_enabled" }, false),
    `${original} Current connection state: startup_refresh_enabled.`);
  assert.equal(activationReceiptMessage(receipt, { state: "revoked" }, false),
    `${original} Current connection state: revoked.`);
  assert.equal(activationReceiptMessage(receipt, { state: "reauthorization_required" }, false),
    `${original} Current connection state: reauthorization_required.`);
  assert.equal(activationReceiptMessage(receipt, { state: "startup_refresh_enabled" }, true),
    `${original} Current connection state: unavailable.`);
  assert.equal(activationReceiptMessage(receipt, null, false),
    `${original} Current connection state: unavailable.`);
});

test("late activation failure cannot replace an unverified disable", () => {
  let outcome = recordEToroUncertainty("connection", "revocation");
  outcome = recordEToroUncertainty(outcome, "connection");
  assert.equal(outcome, "revocation");
});

test("unmounted or superseded activation retains its receipt lookup", () => {
  assert.equal(retainActivationReceipt(false, 0, 0), true);
  assert.equal(retainActivationReceipt(true, 0, 1), true);
  assert.equal(retainActivationReceipt(true, 0, 0), false);
});
