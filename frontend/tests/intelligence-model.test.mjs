import assert from "node:assert/strict";
import test from "node:test";
import { calendarMatches, publicSourceURL, validPublicFoundation } from "../src/intelligence-model.ts";

const now = "2026-11-23T12:00:00Z";
function snapshot() {
  return {
    schema_capable: true, source_health: [], operation: null,
    briefing: {
      briefing_id: "briefing-1", generated_at: now, provider: "test", model: "synthetic",
      manifest: {
        evidence: [{ evidence_id: "source-1", title: "Public release", text: "Exact quote.",
          source_url: "https://example.test/release", received_at: now, published_at: null }],
        gaps: ["Selected source only."], omitted: 0, input_hash: "hash", cutoff: now,
      },
      output: { headline: "Public evidence briefing", developments: [{
        title: "Public release", topic: "inflation", geography: "UK",
        claims: [{ text: "Exact quote.", evidence_ids: ["source-1"] }],
        interpretation: { text: "Possible implication: a change may matter.", evidence_ids: ["source-1"] },
        uncertainties: ["Incomplete coverage."],
      }] },
    },
  };
}

test("briefing display retains its own manifest rather than the latest source list", () => {
  const value = snapshot();
  assert.equal(validPublicFoundation(value), true);
  assert.equal(validPublicFoundation({ ...value, briefing: null }), true);
  assert.equal(value.briefing.manifest.evidence[0].evidence_id,
    value.briefing.output.developments[0].claims[0].evidence_ids[0]);
});

test("malformed public briefing data is rejected before rendering nested fields", () => {
  for (const change of [
    value => { value.briefing.manifest.evidence = null; },
    value => { value.briefing.output.developments[0].interpretation = "text only"; },
    value => { value.briefing.output.developments[0].claims[0].evidence_ids = null; },
    value => { value.briefing.manifest.evidence[0].source_url = "javascript:alert(1)"; },
    value => { value.briefing.generated_at = "unknown"; },
    value => { value.source_health = [{}]; },
  ]) {
    const value = snapshot();
    change(value);
    assert.equal(validPublicFoundation(value), false);
  }
});

test("source URLs cannot become executable or credential-bearing links", () => {
  for (const value of ["javascript:alert(1)", "http://example.test", "https://u:p@example.test", "file:///C:/test"]) {
    assert.equal(publicSourceURL(value), undefined);
  }
  assert.equal(publicSourceURL("https://example.test/release"), "https://example.test/release");
});

test("calendar filters preserve unknown dates and distinguish selected UK/US sources", () => {
  const filters = { geography: "UK", category: "inflation", time: "upcoming", cutoff: now };
  const event = { source: "ons", category: "inflation", scheduled_at: null };
  assert.equal(calendarMatches(event, filters), true);
  assert.equal(calendarMatches({ ...event, source: "bls" }, filters), false);
  assert.equal(calendarMatches({ ...event, scheduled_at: "2026-11-22T12:00:00Z" }, filters), false);
  assert.equal(calendarMatches({ ...event, scheduled_at: "2026-11-22T12:00:00Z" }, { ...filters, time: "all" }), true);
});
