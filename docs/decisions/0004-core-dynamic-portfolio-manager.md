# Decision: Core-plus-dynamic investment-capital manager

Status: accepted product/documentation direction; implementation and execution gated

Date: 2026-11-16

## Context

The investor clarified that the managed money is substantial long-term investment
capital, not household cash, emergency reserves or miscellaneous small savings.
They want selected bank savings, bonds and investments centralised; a large
stable core and a smaller experimental/dynamic portion; AI market research,
economic-calendar awareness and as much useful automation as possible.

The investor explicitly chose documentation/context/guidance changes only.
An 80-90% core / 10-20% dynamic range is illustrative, not approved weights or
live risk settings. No exact capital, loss budget or replenishment rule is known.
This supersedes the product scope of the earlier
[investment-path decision](0003-investment-path.md), not its accounting or
operational safeguards.

## Decision

- Optimise the product for explainable net compound GBP growth within approved
  constraints, not a promised maximum-return strategy or busy trading interface.
  Only explicitly designated investment-capital accounts belong in its view.
- Maintain separate asset-class and management-role dimensions. Core/dynamic
  budgets, internal transfers and overlapping exposures require explicit future
  accounting; today's equity/bond/cash schema does not implement them.
- Keep a low-turnover, diversified core. Research a finite interpretable long-only
  dynamic family with costs, uncertainty and a matched all-core counterfactual.
  No automatic risk expansion, experiment promotion or core-funded loss refill.
  Leverage, shorts, crypto and speculative options remain unapproved.
- Reuse legacy intelligence provenance, calendars, archival briefs, providers and
  bounded analysis infrastructure. Reuse execution safety concepts only where
  venue semantics actually match. Do not share operational stores, adopt legacy
  holdings/strategies or copy nonportable retry logic.
- Build observation/research automation first; separately approved per-proposal
  execution may progress to revocable standing-policy execution. Standing
  authority binds account, products, strategy/policy version, funding, cost/risk
  limits, expiry, failure/suspension rules and auditable decisions. The human owns
  policy; neither AI text nor a strategy can grant itself authority.
- A calendar restriction or AI-derived feature can enter a future dynamic
  experiment only under explicit separately approved prospective research,
  coverage/failure rules and admission evidence. Existing campaign exclusions,
  advisory-only runtime, default-off collection and read-only trading routes
  remain unchanged.

## Consequences and verification

Prioritise onboarding/reconciliation, growth history/attribution, role/risk
accounting and automated awareness alongside gated research engineering.
Public-document research informs design but does not satisfy dataset rights,
product suitability or execution feasibility. No source establishes the
illustrative split as optimal.

The [investment guide](../investment.md) owns the roadmap and verified design
sources; the [mandate](../investment-research-context.md) owns investor intent;
the [protocol](../backtesting-protocol.md) owns research admission. Prior campaign
trials and exposed/consumed holdouts are retained.

This change modifies documentation and matching repository guidance only.
Validate metadata/links and inspect consistency; it is not broker or return
validation. Later execution/accounting/data changes require focused and full
offline tests plus independent review under existing safeguards. No operational
database, guard, credential policy or executable default is changed here.
