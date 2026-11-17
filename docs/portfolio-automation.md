# Local portfolio awareness and observation preview

The newer [monitoring slice](monitoring.md) adds deterministic local findings and
a separate observation cache without changing awareness wire contracts.
The [recovered provider assessment](provider-feasibility.md) supplements the
historical discovery below; no provider has passed account-level activation.
Follow the [current context](investment-research-context.md#current-clarification-17-november-2026)
where its selected savings/LISA scope supersedes earlier investment-capital wording.

## Implemented boundary

Portfolio awareness is a read-only local projection of an existing investment
journal, existing intelligence evidence, and an operator-owned JSON profile.
It adds calendar watch alerts, dated source-linked research briefs and review
reminders to the investment dashboard and CLI. It starts no collector, model,
broker connection or scheduled OS task. Accounting and trading are unchanged.

Matching occurs **after** public evidence/AI briefs have been collected. Account
IDs, holdings, balances, management roles, mapping evidence and reminder data are
never inputs to the AI pipeline. AI still sees only the public evidence allowed
by the [existing intelligence service](intelligence.md); it cannot trade, move
money, amend policy or replenish experimental losses.

### Configure explicitly

Use only selected investment-capital accounts, not household cash. Create a local
nonsecret awareness JSON file; identifiers must match the selected stores:

```json
{
  "schema_version": 1,
  "portfolio_id": "operator-selected-portfolio",
  "intelligence_database_id": "copy-the-local-intelligence-database-id",
  "observed_at": "2026-11-16T12:00:00Z",
  "calendar_window_days": 7,
  "max_source_age_seconds": 7200,
  "exposures": [
    {
      "account_id": "broker",
      "product_id": "global-equity",
      "calendar": [{"source": "bls", "category": "inflation"}],
      "research_symbols": ["SPY"],
      "evidence": "Operator-selected US macro context proxy; not verified fund look-through"
    }
  ],
  "reviews": [
    {
      "id": "portfolio-review",
      "title": "Review portfolio records and mandate",
      "last_reviewed_at": null,
      "interval_days": 30,
      "evidence": "Operator-selected reminder cadence, not policy approval"
    }
  ]
}
```

This example does not approve any product, proxy, cadence or risk limit. For
account cash set `product_id` to null; an account mapping does not implicitly
cover its holdings. Map each desired account/product pair explicitly. Only
currently positive recorded balances/holdings participate. Management roles are
read locally, never inferred from account or asset class. Missing mappings are
reported rather than guessed. Changing the profile should update its observation
clock and evidence; the output retains its SHA256 and observation clock.

Sources/categories are explicit pairs. Existing sources remain BLS/BEA calendars
and Fed monetary-policy news, **not UK/global/issuer coverage**. Brief matching is
limited to the existing public research symbols (SPY, QQQ, BTC/USD, GLD, USO).
They are context proxies, not permission to hold/trade those instruments or proof
of an underlying fund exposure. A brief's matches describe **current recorded
holdings and current mappings**, not holdings at the brief's publication time.

The profile accepts up to 100 unique exposure pairs, 20 calendar pairs per
exposure, and 20 unique reminders. Calendar horizon is 1-30 days; maximum source
age is 60-86,400 seconds. Reminder intervals are 1-3,660 days. All timestamps are
UTC with at most six fractional digits. A profile cannot be used before its observation time, and recorded review
dates cannot follow that time. The profile is local configuration, not an
immutable review journal or investment-policy approval.

### Operator thesis watch

The same profile can optionally contain a `theses` array. Each thesis has an
explicit account and optional product association and an operator-maintained,
chronological history:

```json
"theses": [
  {
    "id": "example-case",
    "title": "Example long-term case",
    "account_id": "broker",
    "product_id": "global-equity",
    "history": [
      {
        "observed_at": "2026-11-15T12:00:00Z",
        "status": "watch",
        "thesis": "Example hypothesis",
        "invalidation": "Example reason to reconsider",
        "evidence": "Operator's source or review note"
      }
    ]
  }
]
```

Add the property to the profile object, not as a separate file. Status is an
operator label: `watch`, `active`, `invalidated` or `closed`. Include a dated
entry for each change and update the profile's `observed_at` to at least the
latest entry. Entries must be strictly increasing in UTC and no later than the
profile observation. Note and profile clocks require ISO UTC seconds, optionally
with up to six fractional digits. The profile allows 20 theses and 1-50 entries per thesis;
the existing 64 KiB total profile limit still applies. Accounts and products
must exist in the investment profile; a product association requires a broker
account. An association does not establish a current holding, role or look-through.

The CLI and dashboard show these notes locally, including earlier entries.
They do not automatically assess or update a thesis, fetch its evidence, link it
to calendar events, acknowledge a review, change accounting or authorize a
trade. Entries are not sent to AI. The JSON is editable operator-owned
configuration: the app cannot enforce append-only history or prove dates and
notes, so preserve independent evidence if an audit trail is needed. It may
contain sensitive portfolio context; keep it private and do not put credentials
or secrets in it.

### Operator calendar watch

The optional `calendar_watch` array is a manual way to note relevant UK, global
or issuer events alongside recorded exposures while automated coverage remains
US-focused. Add entries to the same profile:

```json
"calendar_watch": [
  {
    "id": "example-event",
    "title": "Example scheduled release",
    "account_id": "broker",
    "product_id": "global-equity",
    "scheduled_on": "2026-12-17",
    "time_note": "Time not stated; source date is local",
    "status": "tentative",
    "observed_at": "2026-11-16T12:00:00Z",
    "source": "Operator's source reference"
  }
]
```

An entry's source-local date is `YYYY-MM-DD` or null if unknown; `time_note` is
free text, not a parsed time or time zone. The app never fabricates an instant
or converts a date to UTC. Status is `scheduled`, `tentative` or `cancelled`;
the observation must be no later than the profile's `observed_at`. Up to 100
unique entries are allowed within the total profile size bound. Account and
product associations follow the same rules as thesis notes. The CLI and
dashboard display the operator's entire list, including past and cancelled
entries and entries for no-longer-held products; they do not infer holdings or
filter by the automated calendar horizon.

These are editable, unverified and potentially stale operator notes, not a
feed, source-coverage claim, automatic alert or evidence of event absence.
Review the cited source and update notes manually; source text is not fetched
by the application or sent to AI. For example, the
[Bank of England MPC dates](https://www.bankofengland.co.uk/monetary-policy/upcoming-mpc-dates)
and [ONS release calendar](https://www.ons.gov.uk/releasecalendar) provide
official pages to check, but this feature does not subscribe to either.
The ONS page links to an [iCalendar feed](https://www.ons.gov.uk/calendar/releasecalendar);
an observed public response contained confirmed, provisional and postponed
entries. That single response does not establish a complete time horizon,
revision semantics, or the feed-specific reuse and retention terms. These must
be verified before an automated adapter can claim coverage; no ONS feed has
been added or activated.

ONS also documents a [release-search API](https://developer.ons.gov.uk/search/search-releases/)
with date filters, offset pagination, release-type filters and example
postponement, cancellation and previous-date fields. The documented example
does not establish a per-change revision or publication time, a consistent
snapshot across pages, a complete future horizon, or the applicable retention
terms. These are discovery leads, not verified coverage or permission to
collect. Do not equate previous dates with the times changes became available.

### Read locally

After the operator has explicitly initialized the separate stores:

```powershell
python -m investment awareness --database .\investment.sqlite3 --intelligence-database .\intelligence.sqlite3 --awareness-profile .\awareness.json --schema-version 2
python -m dashboard --investment-database .\investment.sqlite3 --intelligence-database .\intelligence.sqlite3 --awareness-profile .\awareness.json
```

The dashboard uses `GET /api/investment/v2/awareness` for the additional notes.
The existing v1 endpoint and default CLI report retain their original wire
shape and omit those notes; opt in to CLI version 2 as shown above. Both versions
validate the complete profile. Without an awareness profile they report disabled.
Missing/foreign stores, invalid bindings or invalid
profiles are errors, never fallback demo data or automatic store creation.
Read failures hide previously displayed matching context. The frontend is bound
to the awareness wire schema when packaged. Rebuild existing frontend bundles
after upgrading.

The page reads once and offers explicit refresh. Its minute-by-minute local
polling checkbox defaults off and operates only while the page is open. It does
not enable external collection. Network collection and AI still require the
existing explicit service launch capabilities and applied settings; setting
preferences alone is not proof of an active service. No email/push delivery or
new feed is implemented.

### Evidence and time semantics

- Calendar windows include now and exclude the horizon end. Unknown-time and
  cancelled relevant events remain visible; no announcement time is invented.
  Source freshness, originating source/category and coverage are checked
  separately. Incomplete/stale coverage stays unknown, including when a
  different source is healthy. An empty list is never an all-clear.
- Alerts retain event/source revision, source link, schedule and availability.
  A maximum of 100 matching events is displayed with explicit truncation.
- Briefs come only from the latest 20 stored, validated briefs. Exact source
  quotations, source publication/receipt times, validated completion/expiry,
  provider/model/prompt/input hash, uncertainty and watch/review/wait wording
  are preserved. Future evidence is withheld. Expired, disabled, superseded or
  source-unknown briefs are labelled historical/noncurrent, not silently renewed.
  "Current" is as of the displayed snapshot and requires unexpired evidence,
  matching settings and a successful matching news observation no older than
  900 seconds; it does not verify truth, prediction or current market conditions.
- Reminder due times equal the explicitly recorded review time plus the
  configured number of days; equality is due. A null review date is
  `never_reviewed`, not an invented starting date. No reminder acknowledges a
  journal revision, approves a proposal, or sets an allocation policy.
- Each intelligence read pins settings, calendar, news status, citations and
  briefs in one validated read-only SQLite transaction. The investment revision
  is read separately; there is no cross-database atomic snapshot or historical
  exposure-reconstruction claim. The UI refuses a mismatched investment revision.
  Refresh both views if records change.

## Read-only sync provider discovery

Discovery only, reviewed against public documentation on 2026-11-16.
Moneybox LISA and NatWest Digital Saver were named as accounts to evaluate, but
no account/provider has passed the integration gate. No credential was inspected,
no authenticated request made and no broker-enforced permission tested. Sending
only GET requests does **not** make a broadly privileged key read-only.

| Route | Documented fit | Read-only evidence and unresolved gates |
| --- | --- | --- |
| Moneybox LISA | Moneybox [statement help](https://www.moneyboxapp.com/faqs/category/your-account/your-account/how-can-i-get-a-statement-of-my-account) describes generating a statement for a selected account/date range in the app or website. Its separate [Round Ups help](https://www.moneyboxapp.com/faqs/what-data-do-you-access-and-how-do-you-keep-it-safe) says it uses Open Banking to read transactions from linked external accounts; that is Moneybox consuming bank data, not an API exposing the investor's Moneybox LISA to GoldScout. | No supported LISA account-data API, CSV export, machine-readable statement contract or third-party LISA connection was verified. The indexed official help pages were discoverable, but direct retrieval was denied (HTTP 403); verify the actual export locally. Coverage, point-in-time balance, transactions, revisions, retention rights and file format remain unverified. A local normalizer is not justified until the operator confirms the real statement structure and permitted local processing. No credential scraping or undocumented mobile endpoint. |
| NatWest Digital Saver (official product page: [Digital Regular Saver](https://www.natwest.com/savings/digital-regular-saver.html)) | NatWest Online Banking officially supports exporting selected transaction searches as Excel or PDF; its [transaction-search guidance](https://www.natwest.com/support-centre/help-with-your-card/transactions/how-do-i-search-for-transactions-online.html) says up to seven years are searchable, subject to the account having existed that long. [Midata](https://www.natwest.com/support-centre/payments/general/do-you-offer-the-ability-to-obtain-midata.html) is no longer offered; NatWest points customers to these standard exports. NatWest separately publishes an [Account & Transaction API](https://www.bankofapis.com/products/natwest-group-open-banking/accounts) in its Bank of APIs ecosystem. | The operator-generated Excel/PDF route is an actionable local evidence candidate, but the actual file structure, balance/as-of field, account selection and completeness must be checked locally; an activity export alone is not a contemporaneous account-balance observation. The general API route does not establish that this exact savings product is exposed to AIS. NatWest's consumer [Open Banking FAQ](https://www.natwest.com/mobile-app-content/mobile-app-content/aggregation-faqs.html) covers NatWest fetching selected other-bank accounts, with a stated 90-day consent period and four daily refreshes; this is the reverse direction and must not be mistaken for third-party access to NatWest. Its API portal is a developer/partner ecosystem; [NatWest Group](https://www.natwestgroup.com/who-we-are/contact-us/open-banking-and-third-party-providers-api-portal-page.html) invites customers/partners to discuss API use. The [Open Banking Directory](https://www.openbanking.org.uk/directory/) describes enrolment and checking appropriate PSD2 permissions; FCA [consumer guidance](https://www.fca.org.uk/consumers/account-information-payment-initiation-services) requires an authorised/registered provider and explicit consent. Exact account eligibility in a chosen provider's consent journey, accessible balance/transactions, latency/completeness, retention, revocation and provider terms remain unverified. Do not build direct GoldScout access or infer that app aggregation proves outbound AIS coverage. |
| [Trading 212 Public API](https://docs.trading212.com/api) | Beta; official docs limit it to Invest and Stocks & Shares ISA, exclude SIPP, and document primary-currency reporting rather than multi-currency account data. | Trading 212's [key guidance](https://helpcentre.trading212.com/hc/en-us/articles/14584770928157-Trading-212-API-key) says the customer selects permissions, which may include viewing or placing orders; it also documents IP restrictions and key deletion. The API itself supports live orders, so only a locally verified key with view-only scopes could be considered. Account fit, exact scopes, completeness and [API terms](https://www.trading212.com/legal-documentation/API-Terms_EN.pdf) still require verification. |
| [IBKR Flex Web Service](https://www.ibkrguides.com/clientportal/performanceandstatements/flex3.htm) / [Flex reports](https://www.ibkrguides.com/clientportal/performanceandstatements/flex.htm) | Token and saved query IDs retrieve activity/trade-confirmation reports; suitable for periodic reporting rather than real-time sync. Documented optional IP restriction and token replacement. | A dedicated reporting route is a promising narrower credential surface than trading APIs, but these sources do not establish tested inability to trade with the credential. Confirm exact UK account/wrapper/report fields, delay, token/permission isolation, rate limits, revocation and permitted retention before selection. |
| [TrueLayer Data API](https://docs.truelayer.com/docs/enable-your-users-to-connect-their-bank-account) | Its current Data API v3 documentation describes account and transaction information with explicit `info`, `accounts` and `transactions` scopes, one-time or recurring access, and an end-user bank-consent flow. Non-regulated merchants must use TrueLayer's hosted journey; custom consent UI requires AISP regulation. | TrueLayer's [official help article](https://support.truelayer.com/hc/en-us/articles/51026317780881-Can-I-get-production-access-for-a-personal-project-I-built-in-Sandbox) says personal projects cannot obtain production access. This makes it a poor current fit for a single-user local portfolio tool unless a genuine commercial arrangement is approved. Production access, price/retention terms, and NatWest Digital Regular Saver coverage were not verified. Do not integrate sandbox or treat a supported-bank list as proof that this savings product is selectable. |
| [GoCardless Bank Account Data](https://docs.gocardless.com/docs/bank-account-data) | Its published [API overview](https://docs.gocardless.com/docs/bank-account-data) describes JSON account, balance and transaction data, up to 24 months' transaction history and up to 90 days' continuous access. The [quickstart](https://docs.gocardless.com/docs/bank-account-data/quickstart-guide) specifies default agreement terms of 90 days' transaction history, 90 days' access and full details/balance/transaction scope; it shows user secrets exchanged for a refresh token. Institution endpoints disclose bank-specific transaction history/access periods; bank rate limits may be as low as four calls daily per account/resource. | The public docs describe a usable API and say account verification is offered to paying customers, but this does not establish suitable commercial terms or account-level coverage. NatWest Digital Regular Saver inclusion, institution-specific data limitations, retention rights and whether this personal use is permitted need direct verification. Do not create an account, obtain a secret or test a live link during discovery. Not selected. |
| [Hargreaves Lansdown investment reports](https://www.hl.co.uk/help/profile-settings/investment-reports-tax-certificates/find-investment-reports) | The help page says online investment reports are downloadable four times a year and show account valuation and recent transactions. | This establishes a periodic human-download route, not a machine-readable export schema or public investment-account API. A report's frequency and scope may not support timely reconciliation; check the selected account's actual report before considering a local normalizer. |
| [Vanguard UK documents and report generator](https://www.vanguardinvestor.co.uk/need-help/answer/how-do-i-generate-a-document) | The help page describes periodic statements and a report generator for statements, valuations and other reports, plus contract notes in the document area. | The page does not establish a machine-readable format or public account-data API. Treat it as a possible manual evidence source only; confirm actual document fields, coverage and permitted local use for an existing account. |
| Operator-owned statements/exports | Broad fallback where a particular institution has no approved read-only API. | No online credential is needed. The offline preview accepts an operator-prepared normalized JSON observation file, **not** automatic CSV/PDF ingestion or independent verification of statements. Preserve source evidence and operator reconciliation. |

### Selected Moneybox statement route: verification status

On 2026-11-17 the operator selected Moneybox LISA statements for further
investigation, not a connected provider. Public search indexed Moneybox's
[app statement help](https://www.moneyboxapp.com/faqs/my-account/will-i-receive-statements/)
and [website statement help](https://www.moneyboxapp.com/faqs/category/moneybox-online/managing-your-account/how-can-i-view-and-generate-statements-on-the-moneybox-website),
which describe requesting statements for a selected account and date range.
Direct retrieval of those pages was denied (HTTP 403), so the current full-page
wording could not be independently checked. Website access to balances and recent
transactions does not establish that the generated statement contains either
field. The statement format, account-specific coverage, data dates, holdings,
transactions, retention and permitted processing are **unverified**; do not infer
PDF or CSV availability from the word "download". The public screen also did
not establish a supported LISA read-only API; that is not proof none exists.

The operator may check the official help and their own statement outside chat;
do not submit statements, identifiers, credentials or account data to hosted AI.
An operator-prepared normalized observation remains manual evidence, not
provider verification. No parser, permission scope, account access or
integration is approved by selecting this candidate.

### Recommended combination, not platform migration

Use existing accounts rather than move capital merely for API convenience.
For Moneybox LISA, an operator-generated statement is the only official
evidence path found in this review; its actual format is not verified. Continue with manual
operator-normalized evidence until the actual statement and its coverage are
checked locally. NatWest's official Excel/PDF transaction export is a more
promising offline candidate than direct API work, but the operator still needs
to confirm that the selected export and a separately observed balance support
the desired observation clock. GoldScout currently accepts normalized JSON,
not either provider's export format; do not send statements into chat or hosted
AI. For NatWest Digital Regular Saver, the general Open Banking route is
plausible but this exact product's third-party visibility is unproven; verify
only through an operator-selected regulated provider's normal consent journey.
GoldScout has no implemented AISP enrollment or provider integration. Never
request banking credentials or attempt an account connection as a coverage test.
TrueLayer is not currently a personal-project production route according to
its published help; GoCardless's API shape is documented, but live account
eligibility, current access availability and terms remain unverified. Neither
is selected. For investments, compare
providers only against the operator's actual existing accounts: Trading 212
view-only scopes or IBKR Flex reporting may be evaluated if the investor already
holds those accounts. Neither is a reason to migrate. There is no verified
universal provider for all three portfolio locations. Public help documentation
also shows periodic downloadable reports at Hargreaves Lansdown and Vanguard UK,
but it does not establish a supported machine-readable investment-data contract.
These are documentation-screen candidates, not account recommendations. No
provider-specific export parser should be added until an existing account and a
permitted, concrete export format are selected and checked locally.

Before any adapter activation, the operator must confirm intended accounts,
provider terms/retention rights, source completeness/latency and a **separate
read-only credential** with no order/payment/account-setting authority. Never
test read-only status by attempting an order or cancellation. A future validation
must use provider permission inspection/attestation and approved harmless reads,
with revocation checked independently. Do not put credentials in the awareness
profile or chat.

## Offline observation staging and reconciliation

The operator may prepare and retain a separate local JSON file containing
normalized observations from a synthetic fixture or an export they are permitted
to use. It is the staging artifact: the preview reads it without modifying it
or creating a staging database. It also reads an explicitly selected,
already-opened v2 investment store in a pinned read-only transaction. It neither
appends journal evidence nor repairs, imports or changes balances, holdings,
roles, valuations, buying power or policy. It never contacts a provider.

Example format (identifiers and values are synthetic; use local aliases rather
than account numbers, credentials or sensitive statement details):

```json
{
  "schema_version": 1,
  "portfolio_id": "selected-portfolio",
  "currency": "GBP",
  "source": "operator-reviewed statement",
  "evidence": "local export checked by operator",
  "mapping_observed_at": "2026-11-16T12:00:00Z",
  "account_mappings": [
    {
      "source_account": "broker-export",
      "account_id": "broker",
      "account_binding": "selected-broker-binding",
      "evidence": "operator checked account identity"
    }
  ],
  "product_mappings": [
    {
      "source_product": "fund-export",
      "product_id": "global-fund",
      "evidence": "operator checked product identity"
    }
  ],
  "accounts": [
    {
      "source_account": "broker-export",
      "observed_at": "2026-11-16T12:01:00Z",
      "available_at": "2026-11-16T12:02:00Z",
      "cash_coverage": "complete",
      "holdings_coverage": "partial",
      "coverage_evidence": "cash checked; holdings export missing a page",
      "cash_gbp": "100",
      "holdings": [
        {"source_product": "fund-export", "quantity": "10"}
      ]
    }
  ]
}
```

```powershell
python -m investment reconcile-preview --database .\investment.sqlite3 --observations .\observations.json --max-age-seconds 86400
```

Use the portfolio ID and local account bindings from the selected profile.
Mappings are explicit, one-to-one and operator-evidenced; unknown source accounts
and products are displayed as unmapped, not guessed. The local profile hash,
revision and normalized input SHA256 identify what was compared, but are not
proof of custody or independently verified identity. Mapping evidence must have
been available by the observation clock for a numerical comparison; a later
mapping is reported as unavailable. `observed_at` is the snapshot's
effective clock; `available_at` is when its evidence became available. Both
must be explicit UTC timestamps, ordered and no later than preview time.

The operator declares `complete`, `partial` or `unknown` coverage for cash and
holdings separately, with evidence describing source scope, missing pages or
failed attempts as applicable. These are declarations, not independently
verified pagination or retention rights. A missing cash value is `null`, not
zero. An omitted holding is shown as missing when it exists in the journal,
even if coverage is declared complete; it is never assumed to be zero. Every
included account and recorded holding must be represented before the overall
status can be `matched`. Previously recorded cash-observation discrepancies
remain visible and prevent `matched`; a later match does not resolve them.
Stale observations, incomplete coverage, unmapped items, missing records,
unavailable comparisons and differences yield
`attention`. Freshness is measured from observation time with the required
operator-supplied maximum age (1 to 31,536,000 seconds); equality is stale.

For a comparison, the observation must be strictly later than the journal's
latest event clock and all balance evidence must have been available by that
observation clock. Otherwise the result is `unavailable`, with no numerical
difference: this first preview does not reconstruct historical journal states
or guess the order of same-clock events. An observed row can show an exact
`difference` or `match` even when its coverage is partial or stale; check the
separate coverage and freshness fields. A match is a dated comparison of
operator-provided numbers, not confirmation of current custody, settlement,
completeness, role attribution or buying power. Differences are observed minus
recorded. The output is a read-only stdout JSON report; protect both it and the
source file as private financial data. The application does not persist preview
history, deduplicate future imports, retry providers or automatically resolve
any discrepancy.

No adapter has been selected or implemented. Before doing so, the operator
must identify an existing account and separately verify provider-enforced
read-only permissions, exact account coverage, completeness/latency and
data-use/retention terms. Do not send credentials or statements through chat.
Any future sync must preserve this separation and must not import guessed fills,
alter role attribution, turn observed balances into spendable funds, or repair
the journal automatically.
