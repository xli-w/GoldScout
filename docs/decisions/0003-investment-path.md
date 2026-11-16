# Decision: Separate long-term GBP investment path

Status: accepted; first offline slice implemented, research/execution gated

Date: 2026-11-15

## Context

The investor's long-term GBP objective differs from the legacy Alpaca engine's
ATR-sized short-horizon trades. Legacy state requires hard stops and configured
ownership; its submission recovery assumes Alpaca client IDs. Neither nullable
stops nor cosmetic GBP conversion would implement a funded multi-account portfolio.
Public Trading 212 documentation explicitly warns of non-idempotent submissions.

## Decision

Introduce a separate offline investment package and versioned investment store,
with exact cash/quantity accounting, human-chosen sleeve allocation and export-only
proposals. Start GBP-only and label unsupported FX, accrual, entitlement, historical
research and broker execution explicitly. Preserve the existing trading ledger,
state model, stops, settings, commands and recovery tests.

Dashboard investment responses use a distinct schema and read-only route. Recorded
investment, legacy and demo modes are explicit. No browser account/trading approval
or mutation is added. Eventual execution requires a separate local CLI,
task-specific operator approval and verified venue recovery semantics.

Rebalancing is contribution-first; monthly review and annual/five-point drift rules
implement the investor's chosen policy, not an optimality claim. The first slice
generated buys only. The current planner also suggests bounded sales at annual or
drift reviews, without using hypothetical proceeds to fund purchases; review
acknowledgement/reset remains deferred.
Unsupported purchase references are excluded, while independently supported
purchases can produce a labelled partial correction. No AI/news, leverage,
crypto or active forecasting dependency enters the core.

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
