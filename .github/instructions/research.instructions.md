---
description: "Use when changing offline research data, simulation, selection or research documentation."
applyTo: "research_data/**/*.py,investment/simulation.py,tests/test_campaign*.py,tests/test_investment.py,docs/backtesting-protocol.md,docs/campaign-dataset.md,docs/investment-research-context.md"
---
# Data and research

Use `backtest-integrity-review`, [invariants](../../docs/safety-invariants.md),
[mandate](../../docs/investment-research-context.md) and
[protocol](../../docs/backtesting-protocol.md). For investment also use
`investment-system-development`.

- Stop before product histories/comparative returns at unmet eligibility,
  persistent data-rights/vintage, cash-terms, actions/FX, costs/tax or frozen-spec
  gates. No data acquisition, account access or paid/authenticated feeds.
- Preserve all prior/manual/failed/descriptive trials and exposed/reserved/
  consumed holdouts. The 60-candidate total ceiling never resets on retirement,
  registry/dataset changes or mandate revision. Lock before single-use report-only
  confirmation; no passing candidate means no holdout opening.
- Keep exact timestamps, completed-information availability, price basis,
  sessions/early closes and global action ordering. Reject missing/conflicting
  evidence; never infer continuity, entitlement or executable prices.
- `research_data` is offline ingestion/provenance/readiness, not a simulator,
  FX converter or feasibility approval. Complete cutoff views are not causal
  knowledge at every earlier decision. Fill accounting is pure synthetic
  cash/quantity conservation, not performance/accounting authority.
- The retained investment simulator is disconnected synthetic engineering only.
  Preserve explicit clocks, next-open timing, cash constraints, exact increments,
  receivable/payment timing and flow-neutral unitization; no real histories.
- Actual core/dynamic policy and matched all-core comparisons require approval;
  illustrative weights approve no funding, loss budget or replenishment.
  No core-funded loss refill or counting core gains as alpha.
- Existing news/LLM signals remain excluded. New structured/calendar features
  need prospective separate approval, source/model/prompt/input provenance,
  receipt/validated completion, complete trial accounting and failure policy.
  Historical text analyzed by today's model is not causal holdout evidence.
- Run temporary synthetic fixtures through the offline runner, full suite for
  shared data and independent read-only review. Report gaps without profit claims.
