# Decision: Separate long-term GBP investment path

Status: accepted; v1 accounting and v2 manual/role accounting implemented,
research and execution gated

Date: 2026-11-15

Product direction updated on 2026-11-16 by the
[core-plus-dynamic portfolio decision](0004-core-dynamic-portfolio-manager.md).
It replaces the small-capital/passive-only product emphasis with substantial
designated investment capital, automated intelligence and future capped dynamic
management. The original v1 accounting/review safeguards remain relevant to v1
stores. V2 adds opening-account and role-aware accounting. Public investment
proposals are currently blocked; standing-policy execution remains a later gated
capability.

## Context

The investor's long-term GBP objective differs from the legacy Alpaca engine's
ATR-sized short-horizon trades. Legacy state requires hard stops and configured
ownership; its submission recovery assumes Alpaca client IDs. Neither nullable
stops nor cosmetic GBP conversion would implement a funded multi-account portfolio.
Public Trading 212 documentation explicitly warns of non-idempotent submissions.

## Decision

Introduce a separate offline investment package and versioned investment store,
with exact cash/quantity accounting and human-chosen policy boundaries. The
original v1 planner/export design is not an available public proposal capability:
proposal routes are blocked until approved role-aware policy exists. V2 supports
evidence-backed opening records, core/dynamic/unassigned attribution, limited
evidence-backed corrections and read-only local awareness. Start GBP-only and
label unsupported FX, accrual, entitlement, historical research and broker
execution explicitly. Preserve the existing trading ledger, state model, stops,
settings, commands and recovery tests.

Dashboard investment responses use a distinct schema. Reporting is read-only by
default; explicitly enabled, authenticated v2 manual recording uses previews and
revision-bound confirmation. No browser path approves or executes trades.
Eventual execution requires a separate implementation, task-specific operator
approval and verified venue recovery semantics.

Rebalancing was specified as contribution-first; revision-bound v1 monthly and
annual review acknowledgements are recorded offline, but do not approve trades.
The historical planner implementation is not an available public proposal or
execution path. No AI/news, leverage, crypto or active forecasting dependency
enters the core.

## Consequences

Some abstractions are deliberately not reused because their semantics differ.
Small shared helpers can be extracted later with two proven compatible consumers.
The first release remains useful if licensed data or safe execution cannot be
established. No operational ledger migration, automatic account adoption or
liquidation is part of application refactoring.

The [investment guide](../investment.md) records current evidence, provider/product
screens and remaining gates. Historical campaign records and holdouts are retained.
Changes to safety hooks, credentials, CI and instruction policies are not authorised
by this decision.

## Verification

Focused accounting, planning and read-only API tests must use synthetic input and
temporary storage. Full offline regressions and an independent safety review are
required for the shared accounting/configuration integration. Frontend build,
schema/asset checks and desktop/mobile browser checks cover new views. Actual
broker semantics, eligibility and profitability are not established by these tests.
