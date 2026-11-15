---
description: "Use when changing broker execution, position monitoring, sizing, risk gates, or scheduling."
applyTo: "bot/portfolio.py,bot/risk_manager.py,bot/main.py,bot/utils.py,config.py,tests/test_portfolio.py,tests/test_review2_repairs.py,tests/test_review2_next.py"
---
# Execution and Risk

Read [execution invariants](../../docs/safety-invariants.md). Trace the producer of each order mutation, not only scheduler wiring.

- Data workers fetch only; strategy evaluation, risk decisions, and portfolio mutation remain serialized.
- Confirm symbol, side, remaining quantity, ownership, time-in-force, expiry, tick alignment, and protective coverage against broker observations. Local restored IDs are not proof.
- Persist entry intent before submission. Preserve client IDs, acknowledgement evidence, per-order cumulative fill baselines, and restart reconciliation.
- Account confirmed partial fills exactly once, including canceled or replaced orders. Stale observations must not resurrect exited quantities. Unfamiliar fill provenance must block mutation, not invite inference.
- Confirm cancellation and residual quantity before placing a competing exit or re-arming protection. Do not claim cancellation/re-arm eliminates the unprotected interval.
- Side mismatches must not trigger an exit that increases broker exposure. Unknown prices remain unknown and block new entries until evidence-backed reconciliation.
- Finite values, risk caps, daily-loss latches, correlation gates, and live confirmation remain mandatory. Do not optimize risk settings implicitly.
- Use the `order-recovery-review` skill for lifecycle changes. Cover timeout-after-submit, partial fills, cancel races, unknown ownership, stale snapshots, and restart as applicable using broker mocks.