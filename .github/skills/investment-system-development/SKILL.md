---
name: investment-system-development
description: "Use when changing GoldScout's separate GBP investment accounting, proposals, CLI, dashboard, or future portfolio simulator."
---

# Investment System Development

Read [investment documentation](../../../docs/investment.md),
[safety invariants](../../../docs/safety-invariants.md), and
[development guidance](../../../docs/development.md) before editing. For
historical simulation/selection, also use
[`backtest-integrity-review`](../backtest-integrity-review/SKILL.md) and read the
[current mandate](../../../docs/investment-research-context.md) plus
[backtesting protocol](../../../docs/backtesting-protocol.md).

## Current release boundary

- `investment/` is separate from `bot/`, `config.py`, the trading ledger, and
  market intelligence. Keep that separation; never reuse an operational store.
- The released contract is GBP-only and operator-recorded, with explicit empty
  inception. Corrections, FX, automatic accrual/entitlement, MWR, inflation,
  historical portfolio research, and broker execution are not implied capabilities.
- Allocation proposals are buy-only and export-only. Dashboard routes remain
  read-only. Proposed notionals do not prove fees, settlement, or executable
  funding. Do not add a broker path as a convenience to an accounting change.
- Human-selected targets and the fixed comparison set are not optimized by
  historical returns. Do not promise profit, safety, maximum drawdown, or a
  winning allocation.
- `investment.simulation.simulate_synthetic_fixture` is only an engineering
  harness: synchronized GBP observations, next-session-open buy-only fills,
  positive contributions, cash-funded close-time withdrawals (no liquidation),
  an assumed proportional fee, and synthetic
  split events known by modeled session opens. Split quantities must preserve
  the declared increment. It accepts explicit synthetic rate events at modeled
  opens/closes using simple ACT/365 accrual only; the first open needs a rate,
  overnight uses the prior rate, and changes apply after accrual at their
  effective boundary. Cash shortfalls on withdrawals fail closed. Unit value
  remains linked through complete cash redemption and later contributions.
  Initial capital enters at the first modeled close and earns no earlier
  interest. This does not establish cash provider terms. It does not verify
  input provenance and is not connected to the campaign dataset, store, CLI, or
  product research. Preserve its
  synthetic-only output label and do not use it to imply a performance result;
  callers must not provide real product history.

## Change procedure

1. Locate the state transition/valuation/planner that owns the behavior and its
   nearest `tests/test_investment*.py` regression. State one falsifiable
   hypothesis and reproduce it with a controlled UTC clock and temporary SQLite
   store. The verified baseline is Python 3.11.9; preserve Python 3.10 support.
2. Keep accounting transitions deterministic with `Decimal`; validate event
   schema, currency, effective timestamp, source availability, cash and quantity
   conservation before commit. Append evidence atomically and publish memory
   state only after commit. Readers must not initialize or repair a missing store.
3. Keep effective time separate from evidence availability. A mark effective at
   T but published after T cannot establish contemporaneous completeness at T.
   Never let a latest retrospective mark leak into an earlier valuation/proposal.
4. Preserve unitized return continuity across deposits, withdrawals and periods
   with zero invested units. Complete redemption followed by new funding must
   not reset prior gains or create a synthetic drawdown; if linkage cannot be
   supported, report performance unavailable rather than a false result.
5. In planning, distinguish missing holdings valuation from a missing purchase
   reference. Incomplete held-asset valuation blocks portfolio proposals.
   Unsupported purchase references exclude those buys; any supported partial
   proposal must disclose that it is partial. Ignore references for zero-target,
   zero-need or unfunded products. Missing product selection for a funded,
   positively targeted sleeve blocks purchases with an explicit reason.
6. Preserve external-deposit versus broker buying-power separation. Declared
   future contributions are not current funds; do not assume future fees,
   settlement, venue permissions, execution prices, or fractional-order rules
   beyond explicit profile evidence.
7. Add a few behavior-level regressions for the changed contract (including
   restart/failure when persistence changes), run
   `python scripts/offline_tests.py --pattern test_investment.py` and the
   dashboard API slice as applicable. Shared accounting/data contracts require
   the full offline suite and an independent read-only Safety Reviewer.
8. For simulator or performance work, synthetic tests validate engineering only.
   Do not acquire datasets or run product comparisons until every external
   feasibility gate and the finite locked protocol are satisfied. Never use
   `prepare --allow-network` for this campaign or inspect credentials,
   operational databases, snapshots, logs, or broker state.
9. After implementation, update the investment guide, research protocol, safety
   invariants, and this skill only where behavior/constraints changed. Validate
   customization with `python scripts/validate_ai_setup.py` and inspect the
   final diff. Report exact checks, unresolved assumptions, review status, and
   explicitly whether broker validation occurred (normally: no).

Offline tests are defense in depth, not an OS sandbox. Do not bypass the runner
with subprocesses, native network clients, or direct operational file access.
