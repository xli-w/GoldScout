import assert from "node:assert/strict";
import test from "node:test";
import { addBridgePair, bridgeRows, canReviewEToro, unambiguousBridgeRows } from "../src/etoro-bridge-model.ts";

const position = (id, units = "2") => ({ instrument_id: id, netUnits: units, asset_currency: "USD" });

function context() {
  const snapshot = {
    environment: "real", account_id: 12345, source_as_of: "2026-07-01T10:00:00Z",
    account_currency: "USD", account_totals: { accountAvailableCash: "100" },
    direct_positions: [position(101, "2.000001"), position(102), position(103)],
    mirrors: [{ mirror_id: 7, positions: [position(103, "-1"), position(104)] }],
  };
  return {
    revokeOnly: false, preview: false, busy: false, revoking: false, statusError: "",
    reconciliationRequired: false,
    session: { enabled: true, state: "connected", last_status: "success",
      snapshot_available: true, connection: { environment: "real", account_id: 12345 } },
    portfolioStatus: "success", snapshot, journal: { source_mode: "recorded" },
    now: Date.parse("2030-01-01T00:00:00Z"),
  };
}

test("requires a bound real snapshot, a recorded journal, and a valid current status", () => {
  assert.equal(canReviewEToro(context()), true);
  const cases = [
    value => { value.snapshot.environment = "demo"; },
    value => { value.session.connection.account_id = 99999; },
    value => { value.session.enabled = false; },
    value => { value.session.state = "revoked"; },
    value => { value.session.last_status = "refresh_failed"; },
    value => { value.session.snapshot_available = false; },
    value => { value.snapshot = null; },
    value => { value.portfolioStatus = "refresh_failed"; },
    value => { value.reconciliationRequired = true; },
    value => { value.busy = true; },
    value => { value.preview = true; },
    value => { value.journal.source_mode = "demo"; },
    value => { value.snapshot.source_as_of = "2031-01-01T00:00:00Z"; },
    value => { value.snapshot.source_as_of = "2030-01-01T00:00:00.000001Z"; },
    value => { value.snapshot.source_as_of = "not-a-clock"; },
    value => { value.snapshot.account_currency = "usd"; },
    value => { value.snapshot.account_totals.accountAvailableCash = "NaN"; },
    value => { value.snapshot.direct_positions[0].netUnits = "1e3"; },
    value => { value.snapshot.direct_positions.push(position(101)); },
    value => { value.snapshot.mirrors.push({ mirror_id: 7, positions: [] }); },
  ];
  for (const change of cases) {
    const value = context();
    change(value);
    assert.equal(canReviewEToro(value), false);
  }
});

test("pairings remain one-to-one and exclude instrument IDs repeated across copy groups", () => {
  const rows = bridgeRows(context().snapshot);
  const holdings = [{ product_id: "fund-one" }, { product_id: "fund-two" }];
  assert.deepEqual(unambiguousBridgeRows(rows).map(row => row.key), [
    "direct:101", "direct:102", "copy:7:104",
  ]);
  assert.equal(rows[0].units, "2.000001");
  assert.equal(addBridgePair(rows, holdings, [], "direct:103", "fund-one"), null);
  assert.equal(addBridgePair(rows, holdings, [], "copy:7:103", "fund-one"), null);
  const first = addBridgePair(rows, holdings, [], "direct:101", "fund-one");
  assert.deepEqual(first, [{ sourceKey: "direct:101", productId: "fund-one" }]);
  assert.equal(addBridgePair(rows, holdings, first, "direct:101", "fund-two"), null);
  assert.equal(addBridgePair(rows, holdings, first, "direct:102", "fund-one"), null);
  assert.equal(addBridgePair(rows, holdings, first, "direct:102", "unknown"), null);
  assert.deepEqual(addBridgePair(rows, holdings, first, "direct:102", "fund-two"), [
    { sourceKey: "direct:101", productId: "fund-one" },
    { sourceKey: "direct:102", productId: "fund-two" },
  ]);
  assert.equal(first.length, 1);
});
