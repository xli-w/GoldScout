---
description: "Use when a change could introduce order execution or future trading authority."
applyTo: "investment/**/*.py,dashboard/app.py,docs/decisions/*.md"
---
# No execution runtime

Read [safety invariants](../../docs/safety-invariants.md).
The application records GBP portfolio evidence; proposals are blocked and it
has no execution adapter, scheduler or order path. Do not revive removed runtime
code, add compatibility aliases or infer authority from a profile/report/AI brief.

Any future execution needs separate explicit scope/approval and venue-specific
evidence. Use `order-recovery-review` prospectively: durable intent before submit,
unique ownership, cumulative-fill conservation, ambiguous-outcome quarantine,
confirmed cancellation, settlement and restart are distinct contracts. A
non-idempotent uncertain submit cannot be retried from approximate history.
No authenticated/paper calls or operational state access are ordinary development.
