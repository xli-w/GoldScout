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
proven optima or product recommendations. Use the investor-selected Bank of
England quoted instant-access rate for a GBP 1,000 balance, excluding guaranteed
bonuses, as the primary cash proxy; show the bonus-inclusive series as a
sensitivity. This is a market average, not a rate the investor necessarily
receives. Retain NatWest's published 0% current-account counterfactual for
context, but verify applicable terms.

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
sleeve drift flags review. At those annual/drift reviews, the planner can also
suggest selling overweight holdings, bounded by recorded holdings and sleeve
excess, using declared increments and minimum notionals. It processes holdings
in account/product ID order; this is not tax optimisation. Proposed sale
proceeds never fund buys in the same proposal. Record actual proceeds before
considering another purchase and independently verify settlement. Retained
cash/partial correction and no trade are legitimate outcomes. Review clocks are
relative to profile inception; persistent reminders have no acknowledgement/reset
workflow yet. Scheduled contributions are metadata, not spendable cash.

An external deposit account cannot fund a broker purchase without an actual,
recorded internal transfer. A transfer suggestion is not an instruction to a bank.
Incomplete held-product valuation blocks proposals. Missing, stale or
late-available purchase references exclude the affected purchases; valid
alternatives can produce a labelled partial correction. With no eligible
purchase remaining, the proposal is blocked. Unused zero-target products do not
need purchase references. Exported proposals have expiry/revision information
and **execution disabled**. They are not blanket authorisation for future trades.

Purchase funding covers estimated notionals only. Sale quantities and notionals
are estimates based on recorded holdings and marks, not broker-verified
availability. Future fees, slippage, settlement and venue permissions are not
verified; a funded proposal is not proof of executable funding. A human must
recheck those conditions and prices.

No tight ATR stops are imposed on the new passive core. This is not a change to
legacy stops or a guarantee against losses. Market drawdowns, missing evidence
and operational failures are different concerns.

### Synthetic simulator mechanics (engineering only)

`investment.simulation.simulate_synthetic_fixture` is a pure, non-CLI test
harness intended only for caller-supplied synthetic fixtures; it does not verify
their provenance. It requires synchronized GBP open/close observations whose
availability equals the event time. It models fixed human-selected product
weights, positive contributions and cash-funded withdrawals at a close,
buy-only allocation at the next session open, quantity increments, minimum
notionals, an explicit proportional cost assumption, cash conservation, and a
flow-neutral unitized curve. Withdrawals cannot trigger sales; a cash shortfall
fails closed. Synthetic split actions are accepted only at modeled session
opens, with availability no later than effective time; a split must preserve
the declared quantity increment.

Synthetic distributions specify a product, ex-open, later modeled pay-close,
availability no later than the ex-open, and a positive GBP amount per unit.
Entitlement uses the holding before any fill at the ex-open. A same-product
split at that open is rejected. The gross amount is recognized as a receivable
from ex-open and converted to cash at the pay-close, before close-time external
flows. An unpaid receivable contributes to portfolio and contribution-allocation
target valuation, but is not spendable cash until payment. Inputs must use
unadjusted ex-distribution prices; the harness cannot verify that basis. This is
not a tax, withholding, settlement, or historical entitlement model.

Synthetic annual cash rates must be explicitly supplied at modeled open/close
clocks, available by their effective time, and accrue by simple ACT/365 between
clocks. This convention tests causal mechanics only; it is not a
savings-account rate, AER, tier, withdrawal, tax, or provider-terms model. The
first open needs an explicit rate (annual decimal from 0 to 1); a rate persists
until the next declared change. Overnight accrual uses the prior effective rate
before an open rate change; intraday accrual uses the rate effective at that
open. Interest is credited at modeled boundaries and can compound in later
intervals. Initial capital enters as a close-time flow at the first modeled
close, so it earns no interest before that inception clock.

This validates mechanics, not actual returns. The harness is not connected to
the campaign dataset, investment store, dashboard, broker, or product research.
Its synthetic label is not evidence that an input's provenance is synthetic.
It does not model sales or liquidating holdings to fund a withdrawal,
distribution declarations or price-basis validation, account-specific cash
terms/interest conventions, FX, taxes, inflation, fixed fees, spreads, slippage,
venue calendars, or real execution. Apart from the narrow synthetic split and
distribution rules, other corporate actions remain unsupported. Its output is
explicitly marked as synthetic-only and unsupported for product-performance
claims. External research gates remain open.

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

### Feasibility follow-up (2026-11-16)

This follow-up verifies public product pages and published fee/terms pages only.
It does not access an account, inspect the HMRC reporting-funds spreadsheet by
ISIN, obtain a data licence, download a price-history dataset or establish
eligibility. Issuer pages were not used as research return inputs.
The investor selected the Bank of England's GBP 1,000 instant-access quoted-rate
proxy, excluding guaranteed bonuses as the primary series and showing the
bonus-inclusive series only as a sensitivity. Paid-data research is not
authorized; the existing public/user-supplied-only boundary remains.

| Gate | Public evidence | Status and consequence |
| --- | --- | --- |
| Equity candidate | Vanguard identifies VWRP, ISIN IE00BK5BQT80, as an accumulating Irish UCITS tracking developed/emerging large- and mid-cap equities; the page displays 0.14%, USD base currency, a GBP listing, and a 23 July 2019 share-class inception. | Candidate only. Its USD base means a GBP dealing line does not remove underlying currency risk. The short product life cannot support a long-period product-specific comparison. |
| Bond candidate | Vanguard identifies VAGS, ISIN IE00BG47K971, as an accumulating GBP-hedged Irish UCITS global investment-grade/government bond fund; the page displays 0.08% and an 18 June 2019 share-class inception. | Candidate only. GBP hedging does not remove duration or credit risk; no product-specific performance run is authorized. |
| Provider fit | [Trading 212's public API](https://docs.trading212.com/api) is beta, supports Invest and Stocks ISA accounts, and operates in the primary account currency without API multi-currency support. Its [fractional-share guidance](https://helpcentre.trading212.com/hc/en-us/articles/9511997937437-Can-I-trade-with-fractional-shares) says most Invest/ISA instruments support fractional dealing, with precision depending on instrument and position. [Alpaca's non-US onboarding guide](https://alpaca.markets/learn/live-trading-account-non-us) includes UK applicants, but its separate [Europe Broker API](https://docs.alpaca.markets/eu/docs/about-broker-api) is a partner service; its [account-opening documentation](https://docs.alpaca.markets/eu/docs/account-opening) says initial European account types are taxable and margin, not tax-advantaged registered accounts. | Trading 212 is the more plausible public candidate for a GBP/ISA-shaped review, not a selection. Alpaca's existing API path is USD-oriented and public sources do not establish GBP cash, ISA access, or access to VWRP/VAGS. Exact account approval, product listings, fractional precision and terms remain operator-confirmation items. Trading 212's [market-order API](https://docs.trading212.com/api/orders/placemarketorder) is non-idempotent, a separate blocker for automated execution. |
| Platform costs | Trading 212's published tariff lists free commission/custody and a 0.15% FX fee. Vanguard's self-managed service lists £4 monthly below £32,000 invested, plus fund costs. | At £1,000, Vanguard's £48 annual account fee is 4.8% of initial capital before fund costs. Exact Trading 212 instrument availability and applicable account terms require operator confirmation. |
| Cash comparator | NatWest Select remains a 0% current-account counterfactual, not an interest-bearing savings product. Marcus publicly displays 3.75% AER with a 0.49% first-year bonus, but its own page says the rates are correct as of 13 August 2025 and variable. The [Bank of England's quoted-rate methodology](https://www.bankofengland.co.uk/statistics/details/further-details-about-quoted-household-interest-rates-data) describes monthly quoted household rates, including instant-access deposits selected for £1,000 balances. | The selected primary proxy is the instant-access series excluding guaranteed bonuses; show the bonus-inclusive series as a sensitivity. Rates are advertised, gross of tax, do not include account fees, and reflect changing products/institutions—not the investor's own account. Do not use Marcus's stale-dated headline as a current or historical rate. Freeze the exact BoE series identifier and vintage convention before any data use. |
| Deposit protection | FSCS states the deposit limit is £120,000 per eligible person, per authorised firm from 1 December 2025; investment protection is separate and does not cover poor market performance. | Protection is conditional on eligible deposit/account/institution status; it is not inflation protection or a fund-value guarantee. |
| Tax and wrapper | GOV.UK confirms the 2026/27 ISA allowance and general age/residency eligibility. HMRC says reporting-fund income can include undistributed excess reported income. Its reporting-funds list is monthly; the exact candidate ISIN entries and periods have not been independently checked in the ODS list. | Do not assume the investor's ISA eligibility/allowance or a taxable-account treatment. Keep pre-tax comparisons distinct; exact share-class status, tax assumptions and personal circumstances remain unverified. |
| Historical data rights | The [Bank of England Database terms](https://www.bankofengland.co.uk/legal) state that its statistical data may be reused under the UK Open Government Licence, subject to attribution; its quoted-rate methodology describes monthly weighted advertised rates and revisions. Vanguard's website terms limit use of site material to personal, informational, non-commercial use (or written authorisation) and prohibit repeated automated access and copying/exploitation absent authorisation. | The BoE cash-rate source is a public-data candidate under OGL; no rate data was acquired, and the precise series/vintage still needs to be frozen. No source is cleared for a retained, checksummed product-history dataset with prices/NAV, total-return or distribution treatment, actions, GBP FX and point-in-time vintages. Do not scrape issuer pages or treat public display as a research licence. Paid licensing remains outside the approved boundary. |

**Gate result: blocked.** The cash benchmark convention and public-data boundary
are now specified, but the proxy is not a personal savings product and neither
fund is selected. Trading 212 and Alpaca are public-screen candidates only; the
operator must confirm the intended provider/account and product availability
outside this work. Exact HMRC share-class reporting status remains unchecked.
Most importantly, no public or operator-supplied source has been established
with permitted persistent use of complete product price/total-return/action/FX
history and vintages. If that cannot be evidenced within the approved
public/user-supplied-only scope, stop rather than acquire paid data. No product
price-history dataset was downloaded or analyzed as return evidence, no account
was accessed and no return comparison ran.

## Explicit follow-on gates

This release includes only a synthetic mechanics harness, not a continuous
historical GBP product-performance simulator or a broker execution adapter. The
following remain unsupported/unverified:

- Historical source rights/vintages and full product/action/FX/cash-rate coverage.
- Executed FX, non-GBP account balances, settlement-specific broker funding.
- Distribution entitlement/payment reconciliation, automatic cash interest/tier/
  term accrual, corrections and operational migrations.
- Money-weighted return, inflation, tax scenarios, matched savings/passive curves
  and dated look-through concentration.
- Validated spread/slippage/quote/cost models for executable proposals.
- Exact venue permissions and uniquely evidenced ambiguous-order recovery.
- Licensed historical portfolio comparisons and the optional active challenger.

### Continuation order

The bounded recorded-accounting release is complete. The next portfolio-research
phase is **feasibility and simulator engineering**, not a return run: first close
the product/account, cash-terms, historical-data-rights/vintage, action/FX,
cost/tax, and selection-protocol gates in the
[research protocol](backtesting-protocol.md). Synthetic-only tests may validate a
future continuous simulator's event ordering and conservation, but cannot close
those external gates or support a portfolio-performance conclusion. Stop if a
required source or permission is unavailable; retain the useful tracking workflow.

Broker execution is a separate later phase. It requires exact venue/product/
settlement evidence, durable per-proposal human approval, and uniquely evidenced
recovery from ambiguous submissions. This plan does not authorize account access,
orders, credentials, operational migrations, or changes to safety hooks/policies.
The optional trend challenger follows passive comparison and remains nonessential.

No favourable report is approval to invest. A separately enabled local execution
CLI would require per-proposal human approval, durable intent, confirmed partial
fills/ownership, evidence-backed recovery and independent validation. If safe
recovery is unavailable, retain manual execution outside GoldScout.

[Market intelligence](intelligence.md) stays advisory-only and is not a dependency
of allocation or a news/AI signal in research. The
[investment research context](investment-research-context.md) and
[protocol](backtesting-protocol.md) distinguish this mandate from the historical
trading campaign; preserve all old trials and consumed holdouts.
