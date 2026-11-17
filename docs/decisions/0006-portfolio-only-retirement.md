# Decision: Retire trading runtime, retain grounded research contracts

Status: approved retirement, 2026-11-17

## Assessment

The candle day-trader's broker client, scheduler, strategies, risk/stop machinery,
ledger, state snapshots, telemetry, plotting, minute backtests and experiment
runner do not implement a funded long-term GBP evidence portfolio. Keeping them
behind a second product/mode would preserve coupling and confusing authority.
Remove them and their exclusively dedicated tests, configuration and dashboard
surfaces. No compatibility aliases, old operational commands or old-journal
migration remain. No operational data is read, converted or deleted.

Retain only demonstrated independent contracts:

| Component | Rationale and limit |
| --- | --- |
| Opening-evidence investment journal | Exact GBP multi-account accounting, roles, causal marks, external-flow unitization and replay are current product behavior. Preserve wire `schema_version=2`; explicitly reject older formats without migration. Retired policy-review acknowledgement events are not supported, even in otherwise current-version journals. |
| `research_data` importer | Bounded local raw-byte snapshots, hashes, strict provenance, atomic publication/reopen and race/failure tests are substantial reusable ingestion behavior, not broker plumbing. Move out of the removed runtime with no aliases. |
| `research_data` readiness | Complete scoped daily/FX views, explicit units and declared availability have independent tested semantics. XLON session checks genuinely require `exchange-calendars` and its scientific dependencies; importer/fill helper remain standard-library-only. A readiness pass is not source truth, executable FX or performance authorization. |
| Pure `account_fills` | Exact ordered long-only GBP cash/quantity/fee conservation is useful independently, but is neither journal authority nor a valuation/risk/performance model. |
| Synthetic investment simulator | Existing causal portfolio mechanics include next-open buys, cash constraints, exact increments, external-flow unitization, split handling, distribution receivables/payment and timed cash-rate accrual. Retain engineering tests/labels, disconnected from product histories, datasets and journal. Source rights/eligibility and the full research/execution model remain unmet gates. |
| Advisory intelligence | Public provenance, calendar/news archive, dated briefs and bounded AI controls support current awareness. Remove its broker observer, hypothetical order evaluator and bot-observed display; stored `shadow` labels remain historical evidence only. |

The disconnected single-order synthetic pilot/pilot store is retired: a bespoke
trace does not establish reusable venue approval, ownership, cash reservation,
real fill accounting or ambiguous-submit recovery. Retaining it would imply more
execution readiness than it supplies. Future venue execution must be designed
from exact venue semantics under separate approval, not rebuilt from old
Alpaca client-ID assumptions. Non-idempotent uncertain submissions must not be
retried from approximate historical matches.

## Preserved boundaries

There is one portfolio product and no execution runtime. Technical schema/API
numbers are immutable wire identifiers, not product editions. Keep journal,
observation cache, review workflow and intelligence stores distinct. Preserve
accounting/provenance safety in [invariants](../safety-invariants.md), hooks,
CI/offline guards and read-only review permissions.

No dependency installation, market-history acquisition, performance run,
broker/account call or operational-store access is part of retirement.
Removing machinery does not reset earlier trials, exposed samples or reserved/
consumed holdouts; their factual constraints remain in
[mandate](../investment-research-context.md) and [protocol](../backtesting-protocol.md).
Offline regressions establish engineering only; full integration validation and
independent Safety Reviewer findings are reported by the coordinating change.
