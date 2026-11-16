# Local portfolio awareness and observation preview

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
UTC. A profile cannot be used before its observation time, and recorded review
dates cannot follow that time. The profile is local configuration, not an
immutable review journal or investment-policy approval.

### Read locally

After the operator has explicitly initialized the separate stores:

```powershell
python -m investment awareness --database .\investment.sqlite3 --intelligence-database .\intelligence.sqlite3 --awareness-profile .\awareness.json
python -m dashboard --investment-database .\investment.sqlite3 --intelligence-database .\intelligence.sqlite3 --awareness-profile .\awareness.json
```

The dashboard adds `GET /api/investment/v1/awareness`. Without an awareness
profile it reports disabled. Missing/foreign stores, invalid bindings or invalid
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

Discovery only, reviewed against public primary documentation on 2026-11-16.
No provider/account has been selected, no credential inspected, no authenticated
request made and no broker-enforced permission tested. Sending only GET requests
does **not** make a broadly privileged key read-only.

| Route | Documented fit | Read-only evidence and unresolved gates |
| --- | --- | --- |
| [Trading 212 Public API](https://docs.trading212.com/api) | Self-service Invest/Stocks ISA account summary, positions and history. Beta; primary-account-currency reporting, not API multi-currency or SIPP support. | [Key guidance](https://helpcentre.trading212.com/hc/en-us/articles/14584770928157-Trading-212-API-key) explicitly permits read-only viewing permissions, optional trusted-IP restrictions and immediate key-pair deletion. Exact configured scopes, actual server enforcement, history completeness, account/product fit and acceptance of its [API terms](https://www.trading212.com/legal-documentation/API-Terms_EN.pdf) remain unverified. |
| [IBKR Flex Web Service](https://www.ibkrguides.com/clientportal/performanceandstatements/flex3.htm) / [Flex reports](https://www.ibkrguides.com/clientportal/performanceandstatements/flex.htm) | Token and saved query IDs retrieve activity/trade-confirmation reports; suitable for periodic reporting rather than real-time sync. Documented optional IP restriction and token replacement. | A dedicated reporting route is a promising narrower credential surface than trading APIs, but these sources do not establish tested inability to trade with the credential. Confirm exact UK account/wrapper/report fields, delay, token/permission isolation, rate limits, revocation and permitted retention before selection. |
| [TrueLayer Data API](https://docs.truelayer.com/docs/data-api-basics) | Bank-data/consented connection route rather than a broker feed; potentially complements broker reporting. | Data scopes and bank consent are distinct from a payments product. Individual/personal production access, commercial terms, exact designated savings/bond-provider coverage and current consent/revocation requirements are not established here. Do not equate current-account coverage with savings, NS&I or investment coverage. |
| Operator-owned statements/exports | Broad fallback where a particular institution has no approved read-only API. | No online credential is needed. The offline preview accepts an operator-prepared normalized JSON observation file, **not** automatic CSV/PDF ingestion or independent verification of statements. Preserve source evidence and operator reconciliation. |

### Recommended combination, not platform migration

Use existing accounts rather than move capital merely for API convenience.
Retain manual evidenced bank/bond records as the dependable baseline. If the
investor already uses Trading 212 Invest/ISA, its explicitly documented
read-only-key option is the strongest first broker-sync candidate in this bounded
comparison. IBKR Flex is a complementary periodic-report candidate for existing
IBKR holdings, not a reason to run its trading API. Defer bank aggregation until
personal access, exact account coverage, costs and data terms are evidenced.
There is no verified universal provider for all three portfolio locations.

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
