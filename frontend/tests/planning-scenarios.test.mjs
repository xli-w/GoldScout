import assert from "node:assert/strict";
import test from "node:test";
import { goalScenario } from "../src/planning-scenarios.ts";

test("goal scenarios use explicit contributions and assume no growth or fees", () => {
  const result = goalScenario({
    target_amount_gbp: "1000.00", target_date: "2030-04-15",
    current_amount_gbp: "200.00", monthly_contribution_gbp: "100.00",
  }, new Date("2030-01-20T12:00:00Z"));
  assert.deepEqual(result, {
    months: 3,
    current_amount_gbp: "200.00",
    target_amount_gbp: "1000.00",
    with_no_more_contributions_gbp: "200.00",
    with_monthly_contributions_gbp: "500.00",
    remaining_gap_with_no_more_contributions_gbp: "800.00",
    remaining_gap_with_monthly_contributions_gbp: "500.00",
  });
});

test("goal scenarios remain unavailable when user inputs are missing and do not invent growth", () => {
  assert.equal(goalScenario({
    target_amount_gbp: "1000", target_date: "2030-04-15",
    current_amount_gbp: null, monthly_contribution_gbp: "100",
  }, new Date("2030-01-01T00:00:00Z")), null);
  const result = goalScenario({
    target_amount_gbp: "1000", target_date: "2030-02-01",
    current_amount_gbp: "100", monthly_contribution_gbp: null,
  }, new Date("2030-01-01T00:00:00Z"));
  assert.equal(result?.months, 1);
  assert.equal(result?.with_monthly_contributions_gbp, null);
});
