# Long-term GBP investment path

GoldScout's new investment path measures an operator-selected portfolio and
produces export-only allocation proposals. It does not trade, transfer money,
choose products or promise returns. The existing [Alpaca engine](../README.md#separate-legacy-trading-capability)
remains separate with its original stops, ownership checks and recovery.

## Current mandate

The investor confirmed GBP 1,000, separate emergency savings, no expensive debt,
no fixed withdrawal date and willingness to tolerate a 40-50% fall. Large losses
can be permanent; that tolerance is not a loss limit or a reason to take maximum
risk. Optional regular contributions are supported without a fixed schedule.

The intended universe is UK-accessible, unleveraged global equity funds,
high-quality bonds and protected GBP deposits. Exclude portfolio leverage,
shorts, crypto and speculative options. Exact product, account, wrapper and
protection eligibility must be verified separately.

The investor chooses the target allocation. Compare a small fixed set such as
100% savings, 100% global equities, 80/20 and 60/40 equity/cash, 80/20 and 60/40
equity/bonds, and 80/10/10 equity/bonds/cash. These are research recipes, not
proven optima or product recommendations. Keep NatWest's verified current-account
counterfactual for context, but use a genuinely eligible interest-bearing GBP
savings alternative in the primary comparison.

## First offline release

Use the selected Python 3.11 environment; documented Python 3.10 compatibility
is retained. No broker account or credentials are needed.

```powershell
python -m investment --help
python -m investment demo
python -m investment demo --contribution
```

`demo` prints a synthetic report without creating a store or contacting a broker.
The local CLI supports explicit profile/store initialisation, event imports and
report/proposal export:

```powershell
python -m investment init --profile .\profile.json --database .\investment.sqlite3
python -m investment import --events .\events.json --database .\investment.sqlite3
python -m investment report --database .\investment.sqlite3
python -m investment propose --database .\investment.sqlite3
python -m investment export --database .\investment.sqlite3
```

`propose` and `export` print the same bound, execution-disabled proposal envelope
to stdout; they do not write an order or change the database. A human may save
that output locally. Profile/event paths must contain operator-prepared JSON,
not broker credentials or operational exports from the legacy engine.
Never select the trading/intelligence ledger as investment storage. Store creation
is explicit; reporting a missing/invalid store does not initialise or repair it.

The first accounting contract is **GBP-only, operator-recorded evidence**. It
supports per-account funded cash and long holdings, actual fill fees, recorded
contributions/withdrawals/internal transfers, splits and actual income payments.
Income payment is not proof of full ex-date/record-date entitlement or source
completeness. The software does not download prices or infer historical facts.

Reports separate contributed capital from gain and show valuation coverage.
Time-weighted return/drawdown require a complete causal series of supported
valuations; incomplete history is unavailable, not a zero return. Decimal
quantities and GBP prices are explicit. Foreign currency and unsupported events
are refused rather than converted by assumption.

### Recorded input formats

The profile is one JSON object containing exactly `schema_version` (integer 1),
`portfolio_id`, `currency` (`"GBP"`), `created_at`, `accounts`, `products` and
`policy`. Account objects contain `id`, `name`, `kind` (`"broker"` or `"deposit"`),
`binding` (a unique local evidence binding) and `currency`. Product objects contain
`id`, `name`, `sleeve` (`"equity"` or `"bond"`), `currency`,
`quantity_increment` and `minimum_notional_gbp`.

Policy contains `id`, `targets` (decimal-text percentages for exactly `equity`,
`bond`, `cash`, summing to 100), integer `max_mark_age_seconds` (1-2,678,400),
integer `proposal_ttl_seconds` (1-604,800) and `monthly_contribution_gbp`
(decimal text; planning metadata only). Product selection may be incomplete for
tracking, but a missing targeted product blocks funded purchase proposals.

Event input is a JSON array. Every event contains `id`, `type`, `timestamp`,
`available_at`, `account_id`, `currency` and nonempty `evidence`. Both clocks must
be timezone-aware; use UTC timestamps such as `"2026-11-15T12:00:00Z"` and record
when evidence actually became available, not when it would have been convenient.
Events cannot predate profile inception or be available before their effective
timestamp. IDs are immutable: identical retries are harmless, conflicting reuse
is rejected.

| Type | Additional fields |
| --- | --- |
| `contribution`, `withdrawal`, `interest` | `amount_gbp` |
| `transfer` | `amount_gbp`, `destination_account_id` |
| `buy`, `sell` | `product_id`, `quantity`, `price_gbp`, `fee_gbp` |
| `split` | `product_id`, `ratio` |
| `dividend` | `product_id`, `amount_gbp` |
| `mark` | `product_id`, `price_gbp` |

Monetary values, quantities and ratios use plain decimal strings, not JSON
numbers or exponent notation (at most 15 integer and 8 fractional digits).
Funding, quantities, prices and split ratios must be positive; fees may be zero.
Unknown fields/types and negative cash or short holdings are refused.
Initial holdings/cash are not profile fields: start empty and import evidenced
funding and fills chronologically. This release cannot adopt an existing position
without its supported inception history, edit evidence, or guess a correction.
Delayed marks cannot establish contemporaneous valuation coverage.

Manual product/price information is not independently verified broker access,
protection, ownership or investability. No authenticated import/synchronisation is
implemented. Do not put credentials or private identifiers in shared example files.

### Allocation proposals

Monthly reviews use the human's fixed sleeve targets. Direct settled new cash to
underweights first. Annual review or at least five absolute percentage points of
sleeve drift flags review. This first release generates **buy-only** proposals,
subject to recorded per-account cash and declared product increments/minimums.
It does not generate sales to fund rebalancing. Retained cash/partial correction
and no trade are legitimate outcomes. Review clocks are relative to profile
inception; persistent reminders have no acknowledgement/reset workflow yet.
Scheduled contributions are metadata, not spendable cash.

An external deposit account cannot fund a broker purchase without an actual,
recorded internal transfer. A transfer suggestion is not an instruction to a bank.
Incomplete held-product valuation blocks proposals. Missing, stale or
late-available purchase references exclude the affected purchases; valid
alternatives can produce a labelled partial correction. With no eligible
purchase remaining, the proposal is blocked. Unused zero-target products do not
need purchase references. Exported proposals have expiry/revision information
and **execution disabled**. They are not blanket authorisation for future trades.

Purchase funding covers estimated notionals only. Future fees, slippage,
settlement and venue permissions are not verified; a funded proposal is not proof
of executable funding. A human must recheck those conditions and prices.

No tight ATR stops are imposed on the new passive core. This is not a change to
legacy stops or a guarantee against losses. Market drawdowns, missing evidence
and operational failures are different concerns.

### Read-only dashboard

Build the existing frontend using the [dashboard setup](dashboard.md), then:

```powershell
python -m dashboard --demo
python -m dashboard --investment-database .\investment.sqlite3
```

The recorded command requires an existing, separately initialised investment
store selected by the operator. It does not start trading or modify the store.
Open `/investment` or `/investment-guide`. Investment, legacy trading and demo
sources are explicitly selected and are not combined into a fictional account.
Recorded investment mode leaves legacy-account endpoints unavailable, not
synthetic. A selected legacy ledger does not implicitly become an investment store.

The investment response has a separate strict schema and compiled browser
validator. Production assets are bound to both legacy and investment schema
checksums. Missing, incomplete or invalid values stay unavailable in the UI.
Refreshes only read reports; there is no browser approval or execution route.

## Research and provider screen

Public-document review on 2026-11-15; no product dataset was acquired and no
performance run or account verification occurred.

| Evidence | Implication |
| --- | --- |
| [FCA guidance](https://www.fca.org.uk/investsmart/should-you-invest) supports emergency reserves, diversified mainstream funds and longer-term lower-turnover habits, without guarantees. | Make discipline/costs the core, not a prediction requirement. |
| [UBS 2026 historical research](https://www.ubs.com/global/en/media/display-page-ndp/en-20260303-global-investment-returns-yearbook-2026.html) discusses equity ownership, diversification and currency risk. | Long-run historical findings do not forecast this GBP account; global equity exposure remains concentrated. |
| [Vanguard rebalancing research](https://www.vanguard.co.uk/professional/vanguard-365/investment-knowledge/portfolio-construction/when-multi-asset-investors-should-rebalance) explains drift control and directing cash flows to underweights. | Rebalancing primarily maintains risk allocation; the chosen calendar/threshold is not an optimal-return claim. |
| [Vanguard self-managed tariff](https://www.vanguardinvestor.co.uk/what-we-offer/fees-explained) lists GBP 48 annually below GBP 32,000 invested, before fund costs. | At GBP 1,000, fixed platform charges can dominate hypothetical gains. This does not preclude holding Vanguard funds elsewhere. |
| [Trading 212 fees](https://helpcentre.trading212.com/hc/en-us/articles/11471996799517-What-are-the-fees-in-the-Invest-ISAs-and-SIPP) list free custody/dealing and 0.15% FX. | A provider to screen, not a selected or fully verified venue; spreads, funding, product and tax costs still matter. |
| [Trading 212 market-order API](https://docs.trading212.com/api/orders/placemarketorder) explicitly warns of non-idempotent submissions. [History](https://docs.trading212.com/api/historical-events/orders_1) documents cursor-paged fills and wallet effects. | Never transfer Alpaca's client-ID retries to this API. History shape does not prove unique recovery after a lost submission response. |
| [FSCS deposits](https://www.fscs.org.uk/what-we-cover/banks-building-societies-credit-unions/deposit-limit/) and [investments](https://www.fscs.org.uk/what-we-cover/investments/) have different coverage. | Eligible deposits have a GBP 120,000 per-person/authorised-firm limit; shared licences aggregate. Investment protection does not cover poor market performance. Broker cash, bond funds and money-market funds are not automatically protected deposits. |
| [Current ISA guidance](https://www.gov.uk/individual-savings-accounts) describes eligibility and the GBP 20,000 2026/27 allowance. | Do not assume the investor's wrapper, age eligibility, unused allowance or exemption from every cost/tax. |

Issuer screening examples, **not selections**:

- [VWRP accumulating global equities](https://www.vanguard.co.uk/professional/product/etf/equity/9679/ftse-all-world-ucits-etf-usd-accumulating):
  issuer-linked KIID IE00BK5BQT80; Irish UCITS, developed/emerging large/mid-cap
  exposure, USD base and GBP trading line; page displays 0.14%.
- [VAGS GBP-hedged accumulating global bonds](https://www.vanguard.co.uk/professional/product/etf/bond/9685/global-aggregate-bond-ucits-etf-gbp-hedged-accumulating):
  issuer-linked KIID IE00BG47K971; investment-grade/government exposure; page
  displays 0.08%. GBP hedging is not protection from interest-rate/credit losses.

Read current product documents and verify exact broker availability, increments,
wrapper/tax status and data rights before approval. The share classes began in
2019: they do not supply direct 2008 product evidence. Do not splice proxy history
into an allegedly executable series. A public chart/CSV is not a research licence.

## Explicit follow-on gates

This release is not the continuous historical GBP research simulator or a broker
execution adapter. The following remain unsupported/unverified:

- Historical source rights/vintages and full product/action/FX/cash-rate coverage.
- Executed FX, non-GBP account balances, settlement-specific broker funding.
- Distribution entitlement/payment reconciliation, automatic cash interest/tier/
  term accrual, corrections and operational migrations.
- Money-weighted return, inflation, tax scenarios, matched savings/passive curves
  and dated look-through concentration.
- Validated spread/slippage/quote/cost models for executable proposals.
- Exact venue permissions and uniquely evidenced ambiguous-order recovery.
- Licensed historical portfolio comparisons and the optional active challenger.

No favourable report is approval to invest. A separately enabled local execution
CLI would require per-proposal human approval, durable intent, confirmed partial
fills/ownership, evidence-backed recovery and independent validation. If safe
recovery is unavailable, retain manual execution outside GoldScout.

[Market intelligence](intelligence.md) stays advisory-only and is not a dependency
of allocation or a news/AI signal in research. The
[investment research context](investment-research-context.md) and
[protocol](backtesting-protocol.md) distinguish this mandate from the historical
trading campaign; preserve all old trials and consumed holdouts.
