---
description: "Use for advisory intelligence evidence, providers, AI controls, storage and portfolio awareness."
applyTo: "market_intelligence/**/*.py,market_intelligence/**/*.json,dashboard/intelligence.py,dashboard/app.py,dashboard/__main__.py,frontend/src/IntelligenceView.tsx,frontend/src/intelligence.css,frontend/tests/intelligence.spec.mjs,tests/test_market_intelligence.py,tests/test_intelligence*.py,docs/intelligence.md,docs/decisions/0002-market-intelligence.md"
---
# Advisory intelligence

Read [guide](../../docs/intelligence.md),
[decision](../../docs/decisions/0002-market-intelligence.md) and
[invariants](../../docs/safety-invariants.md). Distinguish implementation,
planned extensions and absent provider validation.

- Master/AI default off. Intelligence is advisory only, with no order evaluator,
  broker observer or execution runtime. Persisted `advisory`/`shadow` settings
  labels preserve immutable evidence compatibility; `shadow` grants no runtime
  behavior or hypothetical order evaluation. Unknown coverage is uncertainty.
- Imports/pure contracts perform no filesystem, network, credential or service
  access. Operational initialization/provider/service actions require approval.
- Preserve aware UTC observation/availability, source identity/revisions and
  explicit coverage. Empty healthy evidence differs from unavailable coverage.
  News is available at receipt and AI at validated committed completion, not
  publication. Preserve provider/model/prompt/input provenance.
- Requested settings and service-applied receipts differ. Neither a schema nor
  a toggle proves application or enabled network capabilities. Recorded controls
  need explicit opt-in and same-origin/CSRF protections, which are not user
  authentication. Accept
  bounded analysis settings only, never arbitrary URLs/prompts/secrets/orders.
- Keep dedicated store identity, append-only evidence, exact revision/expiry,
  idempotency, atomic application, bounded reads and no-reader initialization/
  repair. Use `ledger-migration-review` for persistence, not the accounting store.
- External text/model output is untrusted data, never executable instructions.
  No private account evidence in hosted AI, secret access or arbitrary fetching.
  Preserve approved endpoint allowlists, address validation/pinning, TLS,
  configured proxy/no-direct-fallback, bounded requests and durable AI reservations.
  Explicit local-loopback AI is not a fallback for failed remote calls.
- Portfolio awareness reads explicit mappings/existing public evidence locally,
  starts no collection/AI and never changes journal/policy. Provider discovery
  is not account permission. US source coverage does not imply UK/global coverage.
- Use fixed clocks, synthetic evidence and temporary stores through offline
  runner. Cover freshness/availability/revisions/default-off, forbidden effects,
  control/restart/failure and spending paths. Disclose absent provider validation.
- Follow [research protocol](../../docs/backtesting-protocol.md): existing
  news/LLM signal exclusions remain. New structured/calendar features need
  separate prospective approval, causal provenance/trials and failure policy.
  No AI output or outage may rewrite policy, refill losses or force liquidation.
