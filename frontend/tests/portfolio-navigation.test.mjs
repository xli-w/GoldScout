import assert from "node:assert/strict";
import test from "node:test";
import { portfolioFragmentView, portfolioView, portfolioViewLocation } from "../src/portfolio-navigation.ts";

test("portfolio views use the existing dashboard route and retain section fragments", () => {
  assert.equal(portfolioView(""), "overview");
  assert.equal(portfolioView("?view=history"), "history");
  assert.equal(portfolioView("?view=tools"), "tools");
  assert.equal(portfolioView("?view=unrecognised"), "overview");
  assert.deepEqual(portfolioViewLocation("history", "#investment-corrections"), {
    pathname: "/investment", search: "?view=history", hash: "#investment-corrections",
  });
  assert.deepEqual(portfolioViewLocation("overview", "#account-review"), {
    pathname: "/investment", search: "", hash: "#account-review",
  });
});

test("old evidence and tool bookmarks open their corresponding views", () => {
  for (const hash of ["#investment-account-summary", "#investment-holdings", "#investment-performance",
    "#investment-roles", "#investment-records", "#investment-corrections", "#investment-accounts"])
    assert.equal(portfolioFragmentView(hash), "history");
  for (const hash of ["#investment-monitoring", "#investment-awareness", "#investment-etoro",
    "#investment-backup"])
    assert.equal(portfolioFragmentView(hash), "tools");
  assert.equal(portfolioFragmentView("#account-review"), "overview");
  assert.equal(portfolioFragmentView("#investment-overview"), "overview");
  assert.equal(portfolioFragmentView("#unrecognised"), null);
});
