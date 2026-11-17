---
name: investment-system-development
description: "Use for GBP journal accounting, opening evidence, replay, local monitoring, CLI and dashboard changes."
---
# Investment system development

Read [investment](../../../docs/investment.md),
[invariants](../../../docs/safety-invariants.md) and
[development](../../../docs/development.md). For simulation/selection also use
[backtest-integrity-review](../backtest-integrity-review/SKILL.md),
[mandate](../../../docs/investment-research-context.md) and
[protocol](../../../docs/backtesting-protocol.md). Stop at unmet feasibility gates.

## Current boundary

- One operator-recorded GBP opening-evidence journal, wire `schema_version=2`.
  Explicitly reject older profiles/stores with no migration or compatibility mode.
  Refuse journals containing retired policy-review acknowledgement events;
  no event migration is supported.
  Readers never initialize/repair or discover operational data.
- Opening cash and long holdings do not invent history, cost basis or custody.
  Exact decimal cash/quantity/role replay is accounting, not an approved policy.
  Core/dynamic/unassigned is independent of account/asset class; conserve totals,
  isolate debits, preserve roles across transfers and forbid role-to-role moves.
- Proposals are blocked; there is no proposal export, broker pilot or execution
  runtime. Illustrative weights approve no budgets, products or loss refill.
  AI cannot rewrite policy, promote experiments or receive private account evidence.
- Setup requires reviewed bound preview/confirmation; reads create no files.
  Journal publication and supporting profile/receipt writes are not atomic.
  Preserve/report a committed journal if later operations fail. Recording needs
  separate opt-in, exact revision and same-origin/CSRF. No user login exists.
- Profile amendments preserve immutable identity/binding/currency/classification/
  quantity rules; retirement needs zero exposure. Funded scope additions need
  complete same-clock balances/held marks and a valid baseline, and are not flows.
- Corrections append original/new evidence, replay downstream and reject broken
  accounting. Historical corrections invalidate performance without automatic
  baseline reset. Opening/profile/scope/roles/observations/reviews are not correctable.
- Cash observations never change cash or refresh valuation. Historical
  correction replay can change or hide previously reported differences;
  reconciliation and monitoring surface attention when corrections and
  observations coexist. Retain prior reports and review correction evidence.
  Savings terms are dated display facts, not accrual/eligibility.
  Explicit fee/income/flow histories are not full tax/entitlement verification.
- Effective and available time differ. Late marks cannot establish earlier
  completeness; incomplete held prices mean incomplete value. Preserve same-clock
  order, marks-before-flow and baseline-before-income requirements.
- Linked unitization is external-flow neutral through full redemption/refunding.
  Missing flow-time value invalidates performance, never later reconstruction.
  Future contributions and proposed sales are not settled buying power.
- Exact cash-only confirmations remain causal recorded evidence, not
  independent custody, corrections or trade approval. Policy review
  acknowledgements are not implemented.
- Monitoring caches/review inbox/intelligence are separate authorities. Reads
  collect nothing; provider collection and workflow writes need separate gates.
  Mapping/freshness/source revisions remain explicit. Unknown is never zero.
- Awareness locally projects public evidence against explicit exposures, starts
  no service/AI and sends no account data. Editable notes are not source truth.
- Synthetic simulation is disconnected engineering only: explicit microsecond
  clocks, next-open buys, cash-constrained withdrawals, exact increments, causal
  splits/receivables/rates and unitization. Preserve synthetic labels; no real
  product histories, provenance claims, performance runs or external calls.

## Procedure

1. Locate owner and nearest `test_investment*.py` check. Use controlled UTC,
   exact decimals and temporary SQLite; verify interpreter.
2. Validate schema objects before integrity/data queries. Pin read snapshot via
   schema catalog before implicit current clock. Commit before publishing state;
   preserve original evidence, commit boundaries, revision/idempotency and replay.
3. Cover exact/missing/future/late evidence, cash/quantity/role boundaries,
   repeat/conflicting IDs, rollback/restart and post-commit reporting failures.
   Never suggest a committed action rolled back because a status read failed.
4. Run focused `python scripts/offline_tests.py --pattern test_investment_opening.py`
   and relevant adjacent checks. Full offline suite and independent read-only
   Safety Reviewer are required for shared accounting/data contracts.
5. Update docs/invariants only where changed; validate customization and diff.
   Report exact checks, assumptions, review status and absent broker validation.

Never read operational journals/workspaces, acquire histories, inspect secrets
or bypass offline guards with subprocess/native network clients.
