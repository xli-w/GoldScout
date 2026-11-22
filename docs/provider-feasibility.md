# Portfolio providers: feasibility and activation gates

## Provenance and decision

Recovered from the uncommitted provider assessment in the older Trader checkout
and its 17 November 2026 discussion. The source review is historical evidence,
not a fresh verification, account entitlement or permission to connect.
GoldScout already has investment accounting and local awareness; the older
report's claim that the entire portfolio system is absent does not apply here.

Keep the current accounts while establishing reliable evidence. Do not move
money to obtain an API. Providers, wrappers, products and account bindings are
configurable; no account values or personal identifiers belong in these docs.

| Route | Release-1 approach | Unresolved gates |
| --- | --- | --- |
| NatWest Digital Regular Saver | Manual normalized GBP evidence | Exact saver's Excel/PDF export, fields, balance clock, history, applicable terms |
| Moneybox S&S LISA | Manual normalized evidence and separately evidenced wrapper notes | Statement format/fields/history; actual funds, fee waivers and transfer terms |
| eToro | Fixed GET adapter; explicitly activated current-portfolio startup reads and manual closed-trade preview | UK/account eligibility, key scope, credentials, data retention/deletion, schema/completeness, account association and currencies |
| Trading 212 | Comparator only; not selected | Actual account fit, beta API limitations, terms and total migration costs |
| AJ Bell Dodl | LISA cost comparator only | Matched funds, complete charges, transfer effects; no verified API/export schema |
| TrueLayer / other bank aggregators | Deferred | Personal production eligibility, pricing, exact saver coverage and permitted retention |

No universal approved connector for all three locations has been established.
New integration code is offline engineering, not verified account connectivity.

## Connection and scheduling status

The eToro collector is separately gated and live-unvalidated; the wizard keeps
a partial provider portfolio observation in memory and shows a separate
provider-reported GBP balance when available. A manual
refresh can also show a bounded closed-trade history preview. Neither is a GBP
journal sync.
NatWest and Moneybox remain operator-normalized manual evidence until a
supported route, the exact account fields and permitted retention are verified.
A manual or partial route must not be labelled a complete live connection.

Once activated, the dashboard attempts one current-portfolio eToro read set per
server startup. It does not impose a separate approval or expiry. It accepts
Read-only or trade-capable credentials, but cannot verify their scope; a
trade-capable key can place, modify or cancel orders if exposed or misused.
Manual closed-trade history is never collected at startup. Activation can be
disabled; failed reads are not retried.
It is not a periodic watch loop or OS schedule, and the feature is not live
validated or blanket approval to connect an actual account. The separate
`monitor-collect --watch` CLI requires explicit network activation and runs
until stopped or an error, without a time-limited approval file. See
[monitoring](monitoring.md) for its constraints and failure behavior.

## What the recovered public research supports

### NatWest

The [product page](https://www.natwest.com/savings/digital-regular-saver.html)
and [export guidance](https://www.natwest.com/support-centre/payments/general/do-you-offer-the-ability-to-obtain-midata.html)
were reported as read. General Excel/PDF transaction export does not prove that
this saver has a contemporaneous balance export or machine-readable schema.
Transaction history alone cannot establish a balance observation.

The inherited assessment quoted tiered variable interest and regular funding
conditions. They are not runtime defaults or verified personal terms. Recheck
current full terms, tiers, exceptions, payment dates and actual credited interest.
Do not apply a headline AER to every balance or silently accrue interest.

### Moneybox

The inherited review reported reading the [fee schedule](https://www.moneyboxapp.com/fees-and-charges)
and official [statement help](https://www.moneyboxapp.com/faqs/category/your-account/your-account/how-can-i-get-a-statement-of-my-account)
in a browser after page-fetch failures. Statement generation/download is not
proof of CSV/PDF format, unit holdings, bonus/fee coverage or export history.
No supported public LISA account-data API was established; this is not proof
that none exists. Do not use private mobile endpoints or credential scraping.

The assessment described an applicable subscription, waivers, differing service
fees for Moneybox versus other funds, and additional underlying fund costs.
Confirm the actual share classes, KIIDs, current tariff, cash/invested fee bases,
waiver eligibility and transfer terms before making a comparison. Unknown
charges are not zero; fees already reflected in NAV must not be debited twice.

Record a credited LISA bonus only from evidence. Do not infer eligibility or
payment from contributions. [GOV.UK LISA guidance](https://www.gov.uk/lifetime-isa)
and [withdrawal guidance](https://www.gov.uk/lifetime-isa/withdrawing-money-from-your-lifetime-isa)
must be rechecked for applicable effective dates before any first-home action.
The inherited report's particular higher-property withdrawal-charge claim is
not adopted as a legal rule or implemented calculation.

### eToro

The inherited assessment identified the [official API portal](https://api-portal.etoro.com/),
[authentication](https://api-portal.etoro.com/core/getting-started/authentication.md),
[personal-use guidance](https://builders.etoro.com/use-cases/personal-use.md),
[FAQ](https://builders.etoro.com/faq) and
[terms index](https://www.etoro.com/customer-service/terms-conditions/).
Public Read permissions and eligible personal monitoring use do not establish
this investor's account entitlement or inability of their key to trade.

The report identified material restrictions concerning Licensed Content,
systematic caching, attribution, deletion/termination and AI use in the
[Builders terms PDF](https://www.etoro.com/wp-content/uploads/2026/03/Master_eToro_Builders_Economy_Terms_17-Feb-2026-clean_R.pdf).
The scope for account-history/derived records and the apparent one-credential-set
versus Demo/Real-key tension remain unresolved. Obtain clarification before
activation or persistence. No eToro data is sent to hosted AI.

Portfolio snapshots, cash-account transactions, closed trades and balance
history have different coverage; none alone establishes a complete GBP ledger.
Keep cash/investment/subaccounts separate. USD equity is not GBP equity, and
CFDs, copy positions, shorts and financing are not ordinary unleveraged funds.
Only documented, bounded Read routes belong in the adapter. Missing fields,
unknown product classification and uncertain snapshot clocks stay unavailable.

The official [List trading history API reference](https://api-portal.etoro.com/api-reference/trading--real/list-trading-history)
documents a paginated, `minDate`-bounded closed-trade feed. GoldScout can request
the trailing 365 calendar days only during a manual refresh,
with a 1,000-row cap; startup refresh remains limited to the current portfolio.
The projection keeps documented trade fields but does not infer a row's
currency, instrument type, account identity or complete transaction coverage.
It is a temporary provider view, not a journal import. Entitlement, exact
account association, live response behavior and rights for this history use
remain unvalidated; a refused or malformed history response is shown as
unavailable without suppressing a valid current portfolio snapshot.

The inherited FAQ review reported no separate API subscription for eligible
users, not universal eligibility or zero product/trading/data costs.
[Trading fees](https://www.etoro.com/trading/fees/) and
[conversion fees](https://www.etoro.com/trading/fees/conversion/) require actual
entity, funding, currency, instrument and waiver checks. No tariff is hard-coded.

## Comparators and conflicting evidence

[Trading 212's beta API](https://docs.trading212.com/api) and CSV history are
research leads, not a reason to migrate. Primary-currency coverage and account
type restrictions matter. [Dodl charges](https://dodl.co.uk/charges) need matched
fund and minimum-charge comparisons, including migration and wrapper continuity.

The inherited Trader report left TrueLayer personal-project eligibility
unresolved after retrieval failed; GoldScout's earlier
[provider discovery](portfolio-automation.md#read-only-sync-provider-discovery)
cites an official help article excluding personal production projects.
Retain the discrepancy as evidence to recheck, never assume a free hobbyist route.

Compare total recurring/platform, underlying fund, dealing, FX, funding,
withdrawal, data and one-off transfer costs on the same capital/period.
Trading turnover is not necessarily FX turnover. No all-in ranking or provider
switch is established by the component examples in the source review.

## Operator-only activation checklist

1. Privately verify the existing account/wrapper and understand the key's
   permissions; never test permission by submitting an order.
2. Resolve retention, caching, deletion, attribution and permitted use for the
   exact fields to be stored. Define a permitted retention duration.
3. Confirm account/subaccount identity, currencies, timestamps, pagination,
   source coverage and applicable charges; no guessed GBP conversion.
4. Provide only non-sensitive capability/schema answers, not statements or keys.
   The operator manages secrets outside chat; the browser never receives them.
5. Development-time authenticated calls still require task-specific approval;
   API code, documentation or a saved setting does not grant it.

See [monitoring operations](monitoring.md). Offline tests do not establish live
provider behavior, complete accounting, suitability or profitability.
