---
name: order-recovery-review
description: "Use when changing order submission, partial fills, stop protection, cancellation, replacement, broker reconciliation, or restart recovery."
---
# Order Recovery Review

Read [execution contracts](../../../docs/safety-invariants.md). Anchor at the order-mutating method in `bot/portfolio.py`, or entry gates in `bot/main.py`/`bot/risk_manager.py`.

1. Trace one lifecycle: durable intent -> submit -> acknowledgement/uncertain outcome -> observations -> accounting -> protection -> restart.
2. Check known ownership, symbol/side, remaining quantity, GTC/expiry, tick/limit rules, client IDs, and per-order cumulative baselines. Never infer unfamiliar replacement fill provenance.
3. Build a focused broker-mocked regression for the affected failure: timeout after broker acceptance, partial then canceled fill, stale snapshot, missing protection, side mismatch, duplicate observation, or restart.
4. Assert confirmed fills are applied exactly once, residual quantity is conserved, and forbidden cancel/submit calls do not occur. Include storage failure before in-memory publication where relevant.
5. Run relevant portfolio/recovery tests via the offline runner, followed by the full suite for production changes. Review cancellation/re-arm gaps honestly.

Reviewer mode: inspect existing evidence only; request checks rather than execute them. Report lifecycle, confirmed findings with file/line, regression coverage, and unresolved broker semantics. Authenticated paper testing requires separate approval and does not prove live behavior.

The current GBP investment path exports buy-only proposals and has no execution
adapter. For any future investment order path, also use
[`investment-system-development`](../investment-system-development/SKILL.md);
do not transfer Alpaca client-ID/retry assumptions to another venue. Require a
separately approved adapter and venue-specific evidence for unique recovery
after an ambiguous non-idempotent submit. Otherwise keep execution manual.