---
name: review-change
description: "Read-only safety review of a change, ranked findings first."
disable-model-invocation: true
argument-hint: "Change or files to review"
---
Review the requested change against [safety invariants](../../../docs/safety-invariants.md) and nearby tests. Lead with ranked findings and concrete file/line evidence. Distinguish confirmed defects, unverified hypotheses, and missing tests. Do not edit or execute commands.

Use `backtest-integrity-review` for research/spec changes and check [campaign requirements](../../../docs/backtesting-protocol.md) separately from implemented runner behavior. For intelligence changes, use the [scoped checklist](../../instructions/market-intelligence.instructions.md); schema validity and hypothetical decisions are not evidence of effective controls or trading authority.