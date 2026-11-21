import assert from "node:assert/strict";
import test from "node:test";
import { advanceReviewAfterSave, readReviewCycle, reviewCanCheckAccount, reviewStorageKey, sameReview,
  savingsTermsNeedReview } from "../src/guided-review.ts";

class Storage {
  values = new Map();
  getItem(key) { return this.values.get(key) ?? null; }
  setItem(key, value) { this.values.set(key, String(value)); }
}

test("a confirmed account record retains unaffected checks", () => {
  globalThis.localStorage = new Storage();
  const key = reviewStorageKey("portfolio");
  localStorage.setItem(key, JSON.stringify({
    version: 1, portfolioId: "portfolio", revision: 3, startedAt: "2030-01-01T00:00:00Z",
    accountIds: ["a", "b", "c"], checkedAccountIds: ["a", "b", "c"],
    finishedAt: "2030-01-02T00:00:00Z",
  }));
  advanceReviewAfterSave("portfolio", 4, {
    type: "transfer", account_id: "a", destination_account_id: "b",
  });
  assert.equal(readReviewCycle("portfolio").revision, 4);
  assert.deepEqual(readReviewCycle("portfolio").checkedAccountIds, ["c"]);
  assert.equal(readReviewCycle("portfolio").finishedAt, undefined);
});

test("unrelated revisions and records never advance a review", () => {
  globalThis.localStorage = new Storage();
  const key = reviewStorageKey("portfolio");
  const cycle = {
    version: 1, portfolioId: "portfolio", revision: 3, startedAt: "2030-01-01T00:00:00Z",
    accountIds: ["a"], checkedAccountIds: ["a"],
  };
  localStorage.setItem(key, JSON.stringify(cycle));
  advanceReviewAfterSave("portfolio", 5, { type: "interest", account_id: "a" });
  advanceReviewAfterSave("portfolio", 4, { type: "correction", account_id: "a" });
  advanceReviewAfterSave("portfolio", 4, { type: "transfer", account_id: "a", destination_account_id: "other" });
  assert.deepEqual(readReviewCycle("portfolio"), cycle);
});

test("a split clears only its own account and a new review has a distinct identity", () => {
  globalThis.localStorage = new Storage();
  const cycle = {
    version: 1, portfolioId: "portfolio", revision: 3, startedAt: "2030-01-01T00:00:00Z",
    reviewId: "first", accountIds: ["a", "b"], checkedAccountIds: ["a", "b"],
  };
  localStorage.setItem(reviewStorageKey("portfolio"), JSON.stringify(cycle));
  advanceReviewAfterSave("portfolio", 4, { type: "split", account_id: "a" });
  assert.deepEqual(readReviewCycle("portfolio").checkedAccountIds, ["b"]);
  assert.equal(sameReview(cycle, { ...cycle, reviewId: "second" }), false);
  assert.equal(sameReview(cycle, { ...cycle }), true);
});

test("finished, stale or already checked review cycles cannot accept another account check", () => {
  const cycle = {
    version: 1, portfolioId: "portfolio", revision: 3, startedAt: "2030-01-01T00:00:00Z",
    accountIds: ["a"], checkedAccountIds: [],
  };
  assert.equal(reviewCanCheckAccount(cycle, 3, "a"), true);
  assert.equal(reviewCanCheckAccount(cycle, 4, "a"), false);
  assert.equal(reviewCanCheckAccount(cycle, 3, "other"), false);
  assert.equal(reviewCanCheckAccount({ ...cycle, checkedAccountIds: ["a"] }, 3, "a"), false);
  assert.equal(reviewCanCheckAccount({ ...cycle, finishedAt: "2030-01-02T00:00:00Z" }, 3, "a"), false);
  assert.equal(reviewCanCheckAccount(null, 3, "a"), false);
});

test("savings reminders do not treat unknown, future or old observations as current", () => {
  const now = Date.parse("2030-01-31T12:00:00Z");
  const terms = {
    annual_rate_pct: "4", rate_basis: "AER", effective_from: "2030-01-01",
    observed_at: "2030-01-01T12:00:00Z", available_at: "2030-01-31T12:00:00Z",
  };
  assert.equal(savingsTermsNeedReview(terms, 30, now), false);
  assert.equal(savingsTermsNeedReview({ ...terms, annual_rate_pct: null, rate_basis: null }, 30, now), true);
  assert.equal(savingsTermsNeedReview({ ...terms, effective_from: "2030-02-01" }, 30, now), true);
  assert.equal(savingsTermsNeedReview({ ...terms, observed_at: "2029-12-01T12:00:00Z" }, 30, now), true);
  assert.equal(savingsTermsNeedReview({ ...terms, available_at: "2030-02-01T12:00:00Z" }, 30, now), true);
  assert.equal(savingsTermsNeedReview(terms, null, now), true);
});
