# Decision: Separate advisory intelligence

Status: advisory service retained; trading observer/order evaluator retired

Dates: original 2026-11-14; retirement 2026-11-17

Intelligence stores public calendar/news evidence, revisions, coverage and
dated advisory briefs separately from portfolio accounting. Master and AI
default off. Existing persisted `advisory`/`shadow` settings remain valid
immutable evidence labels; `shadow` has no runtime behavior or hypothetical
order evaluation. No broker-observed revision, trading bridge or entry gate
exists. See [operations](../intelligence.md).

## Retained contracts

- Source observation/publication, receipt and validated committed AI completion
  have distinct causal meanings. Preserve revisions, archive hashes, parser and
  provider/model/prompt/input provenance. Empty healthy coverage is not unknown.
- Requests, service-applied receipts and active collection capabilities differ.
  Settings require exact revision/expiry/identity, append-only evidence and
  transactional application. Missing/foreign stores are never repaired by reads.
- Explicit service/provider capabilities, endpoint allowlists, address
  validation/pinning, TLS, configured proxy with no direct fallback and bounded
  requests remain mandatory. Local AI is explicit loopback, not a remote fallback.
- Durable AI spending reservations commit before dispatch and are not refunded
  on failure. Caps bound calls/projected bytes/output allowance, not a verified
  currency bill. External text/model output is untrusted data, never authority.
- Recorded analysis controls retain separate opt-in and same-origin/CSRF
  protections, not user authentication, and analysis-only bounded fields. No arbitrary URL/prompt/
  secret/order/risk input. Synthetic demos use temporary evidence.
- Portfolio awareness reads explicit mappings and existing public context
  locally. Private account evidence must never enter hosted AI. Reads start no
  collection/AI, change no accounting and grant no permission or verified coverage.

Provider compatibility, data rights and UK/global source coverage need separate
evidence. Existing campaign news/LLM signals remain excluded; future structured
features require separate prospective research approval and failure policy.
An AI result or outage never changes policy, moves money or forces liquidation.
