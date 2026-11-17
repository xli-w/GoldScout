---
name: order-recovery-review
description: "Use prospectively to review a separately approved future execution design; no runtime exists."
---
# Future order recovery boundary

Read [invariants](../../../docs/safety-invariants.md) and
[investment-system-development](../investment-system-development/SKILL.md).
GoldScout has no execution runtime. Proposals are blocked. This checklist is
not authorization to build an adapter, contact a venue or access operational data.

For a separately approved future design:

1. Establish venue-specific durable intent, ownership, idempotency and ambiguous
   outcome semantics before any integration. Never reuse another venue's retry
   assumptions. Non-idempotent uncertain submits cannot be retried based on
   approximate historical matches.
2. Trace intent -> submit -> acknowledgement/uncertainty -> observations ->
   confirmed fills/accounting -> settlement/protection -> restart. Check
   symbol/side/residual quantity and exact cumulative-fill conservation.
3. Require explicit failure/suspension/revocation rules, cancellation confirmation
   before competing actions and honest exposure/protection-gap disclosure.
4. Define synthetic mocked timeout/partial-fill/cancel-race/stale/foreign-order/
   restart/commit-failure checks before implementation. No real account calls.
5. Require independent read-only review and separately approved venue validation.
   Synthetic traces, persistence exercises and public history schemas cannot
   establish unique recovery, real approval or live readiness.

Reviewer mode inspects source/evidence only; never execute or mutate.
