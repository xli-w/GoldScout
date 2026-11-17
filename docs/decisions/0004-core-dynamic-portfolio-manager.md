# Decision: Core-plus-dynamic investment-capital manager

Status: accepted product direction; role accounting and local awareness implemented,
policy-dependent proposals, research and execution gated

Date: 2026-11-16

Amendment, 2026-11-17: the confirmed
[current context](../investment-research-context.md#current-clarification-17-november-2026)
includes selected savings and a first-home LISA. The illustrative experimental
denominator is non-LISA investment holdings, excluding savings and LISA, not
all designated capital. Human approval of individual actions initially precedes
any separately approved standing authority. Historical wording below is retained
as provenance; no exact allocation or loss budget is approved by either version.

## Context

The investor clarified that the managed money is substantial long-term investment
capital, not household cash, emergency reserves or miscellaneous small savings.
They want selected bank savings, bonds and investments centralised; a large
stable core and a smaller experimental/dynamic portion; AI market research,
economic-calendar awareness and as much useful automation as possible.

The initial direction approval was for documentation/context/guidance only;
subsequent requests authorized the implemented role-accounting and local-awareness
work described below. An 80-90% core / 10-20% dynamic range is illustrative, not
approved weights or live risk settings. No exact capital, loss budget or
replenishment rule is known. This supersedes the product scope of the earlier
[investment-path decision](0003-investment-path.md), not its accounting or
operational safeguards.

## Decision

- Optimise the product for explainable net compound GBP growth within approved
  constraints, not a promised maximum-return strategy or busy trading interface.
  Only explicitly designated investment-capital accounts belong in its view.
- Maintain separate asset-class and management-role dimensions. V2 implements
  evidence-backed core/dynamic/unassigned attribution independently of asset
  class and account, with role-isolated debits and no role-to-role transfers.
  This is accounting, not an approved risk budget. Exact allocation/product
  targets, transfer/loss/profit rules and policy-dependent proposals remain gated.
- Keep a low-turnover, diversified core. Research a finite interpretable long-only
  dynamic family with costs, uncertainty and a matched all-core counterfactual.
  No automatic risk expansion, experiment promotion or core-funded loss refill.
  Leverage, shorts, crypto and speculative options remain unapproved.
- Recommended future funding boundary: do not transfer core capital to the
  dynamic sleeve or automatically replenish experimental losses. New external
  contributions may be explicitly assigned to the dynamic sleeve only within
  an approved cap. Dynamic-to-core profit transfers may be considered only
  under a separately approved, human-confirmed rule. Any user-selectable
  preference must default off and may only disable or choose among behaviors
  already authorized by the versioned policy; a toggle cannot grant authority.
  These are future design constraints, not an implemented policy or permission
  to transfer funds.
- Reuse legacy intelligence provenance, calendars, archival briefs, providers and
  bounded analysis infrastructure. Reuse execution safety concepts only where
  venue semantics actually match. Do not share operational stores, adopt legacy
  holdings/strategies or copy nonportable retry logic.
- Local portfolio awareness is implemented as an opt-in, read-only projection of
  explicitly mapped exposures against existing public calendar/brief evidence
  and configured review reminders. It does not activate collection or AI, share
  account data with AI, or create a review log. Provider sync remains discovery
  only; Moneybox LISA and NatWest Digital Saver are public-research candidates,
  but no sync provider has passed account-level permission/coverage checks or
  been connected.
- Build further observation/research automation; separately approved
  per-proposal execution may progress to revocable standing-policy execution. Standing
  authority binds account, products, strategy/policy version, funding, cost/risk
  limits, expiry, failure/suspension rules and auditable decisions. The human owns
  policy; neither AI text nor a strategy can grant itself authority.
- A calendar restriction or AI-derived feature can enter a future dynamic
  experiment only under explicit separately approved prospective research,
  coverage/failure rules and admission evidence. Existing campaign exclusions,
  advisory-only runtime, default-off collection and read-only trading routes
  remain unchanged.

## Requirements for a future approved policy

This section defines requirements for subsequent design and approval, not an
implemented policy or permission to propose or execute. Missing decisions have
no defaults. Until implemented and approved, both investment store versions
continue to block proposals; v2 has no proposal export or role transfers.

1. **Mandate and approval:** bind a unique policy version, scope, effective time,
   approver and revision to the selected investment portfolio. Record expiry,
   supersession, revocation and an auditable reason; policy changes require
   explicit investor approval, not AI or strategy edits.
2. **Scope and allocation:** explicitly identify eligible accounts, wrappers,
   products and currencies; separate asset-class targets from core/dynamic roles.
   Define targets, tolerances, valuation and review rules, and how unassigned
   assets are handled. The illustrative 80-90% / 10-20% range supplies no values.
3. **Dynamic isolation:** specify initial funding, capital and aggregate/position
   exposure limits, overlapping exposures, cumulative loss accounting, turnover
   and costs; separately approve suspension, retirement, profit-transfer and
   replenishment rules. The recommended boundary prohibits core-funded loss
   replenishment; external contributions must be explicitly assigned within
   the approved cap. Any dynamic-to-core profit transfer requires a separate
   human-confirmed rule. Optional settings may not broaden policy authority.
   Caps are not guaranteed realised-loss limits.
4. **Evidence and decisions:** bind each future decision to the approved policy
   version and unchanged portfolio revision, causal observation/availability
   times, valuation coverage, explicit freshness and reconciliation requirements,
   assumptions, costs, alternatives and a reason. Unknown or insufficient
   evidence must block the affected decision, not become zero or permission.
   Distinguish observed balances from settled buying power and do not assume
   proposed sales fund purchases.
5. **Authority boundary:** distinguish a policy-approved proposal from any
   execution authority. Standing authority, if separately implemented, must
   specify venue/account/product/strategy scope, funding, risk, expiry,
   suspension and revocation. Durable intent, ambiguous-submission recovery,
   partial fills, settlement, protective ownership and audit require
   venue-specific evidence before a pilot. Pausing new risk must not silently
   remove protection or liquidate the core.
6. **Verification before release:** cover absent/unassigned inputs, stale or
   conflicting evidence, policy revision/revocation, loss and transfer
   boundaries, restart and concurrent revisions with isolated tests and
   independent review. Define a matched all-core counterfactual and research
   admission separately under the [protocol](../backtesting-protocol.md);
   neither a research result nor a model output approves policy.

## Draft policy design and investor decisions

This design is **not an approved policy** and has no executable representation
or authority. The investor must decide the following before a policy can be
approved; unknown values are not defaults.

| Decision | Required investor input |
| --- | --- |
| Scope | Designated accounts and wrappers, eligible products and currencies, exclusions, and treatment of unassigned assets. Product and account eligibility need separate evidence. |
| Targets | Core/dynamic and asset-class targets, tolerances and valuation basis; confirm how each applies across accounts and wrappers. The illustrative range is not a target. |
| Dynamic risk | Capital cap, individual and aggregate exposure limits, concentration and overlap treatment, cost/turnover limits, loss measurement, and suspension and retirement triggers. A cap cannot guarantee losses. |
| Funding and transfers | Initial funding, treatment of new external contributions, replenishment and any human-confirmed profit-transfer rule. Automatic core-funded loss replenishment remains prohibited. |
| Review and evidence | Review cadence and triggers, freshness and reconciliation requirements, response to missing evidence, policy expiry and the process for approval and revocation. |

The current illustrative dynamic denominator is investment holdings outside the
LISA, excluding savings and cash. It is a monitoring basis, not an approved
target, limit or funding instruction. Any future policy must explicitly define
its numerator and denominator, including zero or incomplete valuations; it must
not silently reinterpret the monitoring figure as an approved risk measure.

### Version and lifecycle

A future implementation should bind an immutable policy version and its content
digest to a portfolio identity, explicit scope, effective time, expiry and
investor approval evidence. Approval must identify the exact version and record
when the evidence became available; neither a draft, a journal role assignment,
a review acknowledgement nor this document is approval. The mechanism for
authenticating and recording approval is not yet designed or implemented.

Changes require a new version and explicit approval. Record supersession and
revocation as auditable lifecycle evidence without rewriting earlier decisions.
At decision time, require an approved, effective, unexpired and unrevoked version
for the applicable scope; ambiguous, conflicting, unavailable or stale lifecycle
evidence blocks the decision. Revocation or expiry blocks new policy-dependent
decisions; it does not imply liquidation, cancellation or removal of protection.
Handling already-submitted orders requires separately approved venue-specific
recovery and execution rules.

Each future decision must bind the policy version and digest, portfolio revision,
relevant evidence and availability clocks, valuation and reconciliation status,
limits and assumptions. Revalidate these bindings before any separately approved
action; concurrent changes or revocation require a fresh decision rather than
reuse of a stale one. Do not treat proposed sale proceeds as available funding.
The persistence model, concurrency protocol and approval interface require
separate implementation design, failure tests and independent review.

## Consequences and verification

Manual opening/onboarding, subsequent recording, reconciliation evidence,
bounded corrections, role attribution and local awareness are implemented.
Prioritise approved role policy, provider/account selection before any sync
adapter, and gated research engineering. Role risk accounting and broad
automated research are not implied by role attribution or local awareness.
Public-document research informs design but does not satisfy dataset rights,
product suitability or execution feasibility. No source establishes the
illustrative split as optimal.

The [investment guide](../investment.md) owns the roadmap and verified design
sources; the [mandate](../investment-research-context.md) owns investor intent;
the [protocol](../backtesting-protocol.md) owns research admission. Prior campaign
trials and exposed/consumed holdouts are retained.

The original direction decision modified documentation only; subsequent
accounting and awareness implementations were separately requested and verified
offline. This decision still changes no operational database, guard, credential
policy or executable default. Documentation alignment is not broker or return
validation. Later execution/accounting/data changes require focused and full
offline tests plus independent review under existing safeguards.
