---
name: backtest-integrity-review
description: "Use for causal research data, synthetic portfolio simulation, selection and holdout integrity."
---
# Research integrity

Read [invariants](../../../docs/safety-invariants.md),
[mandate](../../../docs/investment-research-context.md) and
[protocol](../../../docs/backtesting-protocol.md). Anchor at source/availability,
valuation/fill or selection owner, not a reporting wrapper.

Apply feasibility gates to real-data research and performance claims, not to
local implementation or synthetic engineering checks. Do not introduce extra
operator-protection policies or repeat approvals for already scoped local work.
Keep causal evidence, trial history and holdout integrity intact.

1. Stop before product histories/comparative returns at missing product/account
   eligibility, persistent rights/vintage, cash terms, actions/FX, costs/tax or
   frozen-policy/specification gates. Public documentation and synthetic fixtures
   do not close them. No network, broker, paid feed or operational-file access.
2. Trace effective/available/decision/fill/settlement clocks, completed data,
   sessions and action order. Late evidence is not earlier knowledge. A final
   cutoff view cannot establish causal coverage at every prior decision.
3. Use minimal synthetic fixtures for missing/off-grid/late marks, early closes,
   exact increments, simultaneous events, fees, distributions and cash rates.
   Reject unsupported basis/entitlement rather than inventing continuity.
4. Preserve cash/quantity/role conservation and external-flow-neutral unit value,
   including complete redemption/refunding. Distinguish incomplete held valuation
   from unsupported purchase references; proposed sales are not current funds.
5. `research_data` is offline provenance/readiness and pure fill conservation,
   not a product simulator or eligibility approval. `investment.simulation` is
   disconnected synthetic-only mechanics, not licensed history or return evidence.
6. Preserve every prior/manual/failed/descriptive trial, exposed/reserved sample
   and consumed holdout. The 60-candidate total ceiling never resets on registry,
   dataset, mandate or code changes. Fixed stress cases/windows are not candidates.
7. Freeze actual scope/policy/hypotheses/parameters, base and 1.5x/2.0x variable
   costs, contribution/tax cases, metrics and uncertainty before return viewing.
   Compare dynamic and combined results with matched all-core, not core gains
   called alpha. Historical GBP 1,000/NatWest/stop-risk assumptions are provenance,
   not current policy defaults.
8. Select only on selection evidence; lock before one genuinely unexposed,
   single-use report-only confirmation. No passing candidate means no holdout
   opening, including baseline fallback. No repeated tuning or forced winner.
9. Existing news/LLM signals remain excluded. New structured/calendar features
   require separate prospective signal/failure policy, receipt/validated
   completion/model/prompt/input provenance and full trial accounting. Today's
   model on old text cannot establish causal holdout evidence.
10. Run focused offline data/simulation checks, full suite for shared contracts
    and independent read-only review. Report assumptions, gaps and actual checks,
    never profitability, suitability or broker-readiness claims.

For journal changes also use
[investment-system-development](../investment-system-development/SKILL.md).
Reviewer mode inspects without execution and labels unexecuted hypotheses.
