# Long-term GBP portfolio manager

GoldScout's investment path measures an operator-selected portfolio. Allocation
proposals are blocked pending approved and implemented role-aware rules. It
does not trade, transfer money, choose products or promise returns. The existing [Alpaca engine](legacy-systems.md#legacy-trading-guide)
remains separate with its original stops, ownership checks and recovery.

## Current mandate

The [17 November clarification](investment-research-context.md#current-clarification-17-november-2026)
supersedes conflicting scope/denominator assumptions below: selected savings,
first-home LISA and investments; approximately five years; irregular
contributions; illustrative experimental share of non-LISA investment holdings
only, excluding savings and LISA. No weights or loss limits are approved.
The [monitoring guide](monitoring.md) distinguishes local findings and the gated
eToro Read adapter from verified provider connectivity or investment policy.

The previous 2026-11-16 mandate described a central manager for the investor's substantial,
designated long-term investment capital across selected bank savings, bonds and
investments. Household spending accounts, emergency reserves and miscellaneous
small savings are outside the product scope. The earlier GBP 1,000 example is
not a limit or an assumption about today's capital. No new exact amount, target
weights or dynamic loss budget has been supplied.

Seek durable net compound GBP growth and better explained allocation decisions
within approved risk constraints, not a promised "most profitable" strategy.
Large losses can be permanent. Earlier willingness to tolerate a 40-50% fall
does not authorise that loss in an experimental sleeve.

The intended design is a **majority stable core plus a capped dynamic sleeve**.
An 80-90% core / 10-20% dynamic range is approved for illustration only, not
implementation, suitability approval or a default. The investor approves actual
weights, eligible products and risk budgets. The core can include diversified
global funds, high-quality bonds and designated GBP deposits; its bank allocation
is an investment choice, not an emergency-fund requirement. "Fixed" means stable
strategic policy, not fixed prices, guaranteed returns or mandatory term deposits.
Exact product/account/wrapper/protection eligibility must be verified.
Leverage, shorts, crypto and speculative options remain unapproved.

The end goal includes extensive automation: research, monitoring, reporting,
policy-bound core maintenance and admitted dynamic strategies. Existing v1
asset-class targets do not constitute a role-aware policy; v1 proposals are
blocked. V2 has explicit role accounting but no allocation policy. Neither
enables autonomous investment execution.
See the [mandate](investment-research-context.md) and
[architecture decision](decisions/0004-core-dynamic-portfolio-manager.md).

### Two allocation dimensions, not three new tickers

The model distinguishes **asset class** (equity/bond/designated savings)
from **management role** (core/dynamic). An equity fund may be core; a different
equity strategy may be dynamic. Account location alone does not establish role.
V2 records independent management roles without setting targets or risk budgets.
V1 targets cannot represent or enforce role isolation.

Core maintenance remains low-turnover and contribution-first, with periodic/drift
review; calendar headlines must not churn the core. The dynamic sleeve explores
a small predeclared set of explainable long-only daily/weekly strategies in
eligible products. Trend or relative-strength hypotheses are research candidates,
not admitted strategies. More frequent trading, thematic/individual holdings or
additional asset classes need evidence and explicit product/risk approval.

Before funding a dynamic strategy, specify capital and position limits,
overlapping/correlated exposure, turnover/cost limits, cumulative loss accounting,
review/retirement rules and transfers. Do not top up a losing experiment from
the core merely to restore its target percentage. Profit harvesting and any
replenishment need approved rules; the strategy cannot approve either itself.
Capital caps constrain planned exposure, not a guaranteed realised loss ceiling:
gaps, shared custody, outages and execution errors remain portfolio risks.
The dynamic sleeve is a controlled opportunity budget, not a requirement to
keep every experiment invested. Zero deployed dynamic risk is valid until a
candidate passes; rejected ideas must not be replaced merely to fill the budget.

The recommended future funding boundary is to prohibit core-to-dynamic
transfers and automatic loss replenishment. An external contribution may be
explicitly assigned to the dynamic sleeve only within an approved cap.
Dynamic-to-core profit transfers may be considered only under a separately
approved, human-confirmed rule. User-selectable preferences must default off
and may only choose among behaviors already authorized by a versioned policy;
a toggle cannot create authority. These are design constraints, not active
policy: role-to-role transfers and proposals remain disabled.

### Decision support and automation contract

The following describes the target capability, not a claim that every feature is
implemented. Current local portfolio awareness is limited to the read-only
projection documented in the [automation guide](portfolio-automation.md).

- Centralise only selected investment-capital accounts. Record balances, fills,
  actual interest and statement dates with provenance. Savings rate/AER, tiers,
  term/maturity and protection are account-specific facts, not inferred yields.
  Bond funds and individual bonds need distinct duration/credit and coupon/maturity
  treatment; neither is equivalent to protected savings.
- Provide dated portfolio/core/dynamic growth curves and net contributions,
  flow-neutral and money-weighted returns, fees, income, FX/inflation where
  supported, drawdown depth/duration, concentration and benchmark attribution.
  Missing evidence stays unavailable. Forecasts are labelled scenarios, not facts.
- Explain each decision's policy, known evidence, exposure change, funding,
  estimated total costs, uncertainty and alternatives including no trade.
  Dynamic value is judged against investing that same capital under core policy,
  not against zero-return cash or the core's gains.
- Extend source collection, dated economic-calendar awareness, AI briefs,
  thesis tracking and experiment proposals using separate intelligence storage.
  Existing public evidence can already be matched locally to configured
  exposures; this does not start collection or AI. Verify coverage before
  extending today's US-focused feeds to UK/global exposures. AI text cannot
  become an order, change targets or relax limits.
- Introduce revocable, versioned standing authority only after separate approved
  execution development: account/product allowlists, strategy/policy hashes,
  exposure/loss/turnover/cost limits, expiry, evidence freshness and suspension
  conditions. Policy approval should then permit compliant orders without
  repeated manual clicks; out-of-policy changes require approval.
- Keep durable intent, uniquely evidenced ambiguous-submission recovery,
  ownership/partial-fill reconciliation, settlement-aware funding and audit
  history between proposal and execution. A pause blocks new risk according to
  policy; it must not blindly cancel protection or liquidate the passive core.
  Never retry an uncertain non-idempotent submission as if it failed.

### Comparison policy

Use actual applicable designated savings terms for personal reporting; compare
the combined portfolio and dynamic contribution with a matched all-core passive
counterfactual after costs and consistent tax assumptions. The former fixed
passive recipes (100% savings/equities, 80/20 or 60/40 equity/cash or equity/bonds,
80/10/10 equity/bonds/cash) remain a small comparison family, not optima.
Freeze the relevant subset and exact capital/policy before viewing results.

The previously selected BoE instant-access quoted rate for GBP 1,000 balances,
excluding guaranteed bonuses with bonus-inclusive sensitivity, is a research
proxy only. It is not the investor's larger-account yield. NatWest 0% remains
historical context, not sufficient justification for active risk. Neither public
pages nor the illustrative split establish suitability or future outperformance.

## First offline release

Use the selected Python 3.11 environment; documented Python 3.10 compatibility
is retained. No broker account or credentials are needed.

```powershell
python -m investment --help
python -m investment demo
python -m investment demo --contribution
```

`demo` prints a synthetic report without creating a store or contacting a broker.
The local CLI supports explicit profile/store initialisation, event imports,
reports and a blocked proposal-envelope export:

```powershell
python -m investment init --profile .\profile.json --database .\investment.sqlite3
python -m investment import --events .\events.json --database .\investment.sqlite3
python -m investment report --database .\investment.sqlite3
python -m investment propose --database .\investment.sqlite3
python -m investment export --database .\investment.sqlite3
python -m investment review-status --database .\investment.sqlite3
```

For v1, `propose` and `export` print the same bound, execution-disabled envelope,
currently blocked with no orders. V2 refuses proposal export. Neither changes
the database. `review-status` is also read-only. Profile/event paths must
contain operator-prepared JSON, not broker credentials or operational exports
from the legacy engine.
Never select the trading/intelligence ledger as investment storage. Store creation
is explicit; reporting a missing/invalid store does not initialise or repair it.
The store must retain its original v1 tables, implicit index and immutable
triggers. Altered definitions or extra objects, including manually added
indexes, are refused; preserve the evidence rather than editing the schema.
Explicit backup and restore, capacity monitoring, trust boundaries and recovery
steps are documented in the [investment journal lifecycle](investment-journal.md).
Operational maintenance requires separate approval.

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

### Guided first-run setup

The normal source-checkout launch is now:

```powershell
.\venv\Scripts\python.exe -m dashboard
```

It selects `local-investment` beneath the source checkout, independent of the
terminal's current directory. Open the displayed local URL. If the directory is
absent or empty, the wizard is available without a login. An installed package outside a checkout requires an
explicit `--workspace <directory>`; no installation directory is used as storage.

The wizard guides accounts, products and opening evidence, then shows a
server-validated preview and requires a separate final confirmation. Use local
account aliases rather than account numbers. Verify product currency, equity/bond
classification, quantity increment and minimum-notional metadata; nothing is
looked up or guessed. Include every selected account's cash and its supported
holdings at one defensible effective time. Unknown prices remain unknown, and
late evidence may leave the baseline unavailable. Time inputs use the computer's
local timezone and are stored as UTC.

**Accounts and products cannot currently be added to an existing journal.**
Review the initial scope carefully. The policy stays null and roles start
unassigned. There is no provider connection, allocation approval or trading.
Monitoring and its separate stores can be configured later.

Unsubmitted drafts exist only in browser memory and disappear on reload or
navigation away. Preview state is held only by the local server and expires after
five minutes; temporary local sessions last fifteen minutes and provide CSRF
protection, not user authentication. No workspace files or
directories are created until the operator confirms. After confirmation,
operation-owned recovery artifacts may contain private evidence and must be
protected with the journal.

Creation publishes a validated journal containing the reviewed opening event,
without overwriting an existing file. Interrupted confirmed operations have a
guarded recovery path; existing unrelated files or manually initialized empty
journals are not adopted or reset. A missing or invalid journal explicitly
selected with `--investment-database` remains an error, not a new setup.

After completion the investment view is read-only. To record later events, stop
the server with Ctrl+C and restart:

```powershell
.\venv\Scripts\python.exe -m dashboard --investment-recording
```

Open `http://127.0.0.1:8765/investment/record`. Setup does not enable recording;
the separate launch flag is still required. See the [journal lifecycle guide](investment-journal.md) for private
storage, partial-completion behavior, backups and recovery.

### Starting a local v2 journal manually

The following remains an advanced CLI alternative to the default wizard.

These are operator steps; they do not contact a bank or broker. The repository
ignores the entire root `local-investment/` directory, including profiles,
journals and SQLite sidecars. Git ignore is not encryption or access control:
check the directory's Windows permissions and any backup or cloud-sync settings.
Git can still force-add ignored files, and `git clean -fdx` can delete them. Do
not place credentials or passwords there or share private records in chat.

1. From the repository root in PowerShell, create the private working directory
   if it does not already exist. Do not overwrite any existing files.

   ```powershell
   New-Item -ItemType Directory -Force .\local-investment | Out-Null
   ```

   Choose a folder that only authorized users can access; on Windows inspect
   inherited ACLs. The repository may itself be in a shared or synchronized
   location: ignoring a file does not prevent other software from copying it.
   Keep protected backups elsewhere as described in the
   [journal lifecycle guide](investment-journal.md).

2. Before creating the journal, identify all accounts to include and their
   supported products. The v2 profile is stored in the journal at initialization;
   editing the JSON file afterwards does not change it. There is currently no
   supported way to add accounts or products to an existing journal. A smaller
   scope is possible, but it does not represent excluded accounts or holdings.
   Do not omit an unsupported holding from an account and treat the result as a
   complete account snapshot. Foreign currency and unsupported holdings require
   separate handling; no FX conversion is inferred.

3. Create `local-investment\profile.json` in a local text editor, saved as
   UTF-8 without a BOM. The following is a **fictional schema example**, not real evidence or
   a ready-to-use profile. Replace its accounts, products and timestamp with
   your own supported scope before running the initialization command:

   ```json
   {
     "schema_version": 2,
     "portfolio_id": "my-portfolio",
     "currency": "GBP",
     "created_at": "2026-01-01T00:00:00Z",
     "accounts": [
       {
         "id": "savings",
         "name": "Selected savings",
         "kind": "deposit",
         "binding": "local-savings",
         "currency": "GBP"
       },
       {
         "id": "broker",
         "name": "Selected investment account",
         "kind": "broker",
         "binding": "local-broker",
         "currency": "GBP"
       }
     ],
     "products": [
       {
         "id": "fund",
         "name": "Selected fund",
         "sleeve": "equity",
         "currency": "GBP",
         "quantity_increment": "1",
         "minimum_notional_gbp": "0"
       }
     ],
     "policy": null
   }
   ```

   Use unique local aliases for account IDs and bindings, not account numbers.
   A savings account is `deposit`; a holdings account is `broker`. Set
   `created_at` to an explicit UTC time no later than the intended opening
   evidence clock and not in the future. Enter every intended supported product
   with its correct `equity` or `bond` classification and explicitly verified
   quantity increment and minimum notional; the sample values are not defaults.
   A deposit-only profile can use an empty `products` array. The policy must be
   `null`; no targets or roles are approved by this profile.

4. With the verified profile saved, initialize **once** at a new path:

   ```powershell
   .\venv\Scripts\python.exe -m investment init --profile .\local-investment\profile.json --database .\local-investment\investment.sqlite3
   ```

   Use the selected Python environment if its path differs. This creates an
   empty v2 journal, not an opening balance. It refuses to overwrite an existing
   store. The command prints a local report; treat terminal output as private.
   If it fails, read the error and do not delete or overwrite a pre-existing
   journal to retry.

5. Start the dashboard and opt in to recording:

   ```powershell
   .\venv\Scripts\python.exe -m dashboard --investment-database .\local-investment\investment.sqlite3 --investment-recording
   ```

   Open `http://127.0.0.1:8765/investment/record`. No login is required; the
   explicit flag enables local write actions. The server is loopback-only; do
   not expose or tunnel it. If the frontend bundle is missing, follow the
   [dashboard setup instructions](dashboard.md#setup-and-demo).

6. Prepare one evidenced opening snapshot at a **common effective clock**:
   exact GBP cash for every selected account and all supported existing long
   holdings, with quantities and, where evidenced at that clock, GBP prices
   per unit. If records from different dates cannot support a common snapshot,
   obtain suitable evidence before opening; do not invent a common clock or
   balances. The dashboard's effective and availability fields use your
   computer's local timezone. Enter when the evidence actually became available;
   it cannot precede the effective time. A later availability time or an
   unknown price may leave valuation and the performance baseline unavailable.

7. Enter every account's cash, including an evidenced zero, and add each
   holding under its account and product. Leave a holding's price blank if it
   is unknown; do not invent one. Add a brief source description without
   account numbers or secrets. Select **Preview record**, review the full
   event, times, amounts and before/after values against the evidence, then
   confirm only if correct. The opening event cannot be corrected through the
   browser. Roles initially remain unassigned; leave them so unless you have
   explicitly decided their attribution.

8. Review the investment page, then record later actual transactions and marks
   chronologically using the same preview/confirm flow. A cash observation
   displays a difference but does not correct a balance. Stop the dashboard with
   Ctrl+C; restart without `--investment-recording` for read-only access.
   Back up the journal and supporting profile using the approved procedure in
   the [journal lifecycle guide](investment-journal.md). Monitoring and its
   separate cache are optional and do not populate accounting.

### Version 2 opening evidence

The explicit `init`, `import` and `report` CLI commands also accept a new
version-2 profile for evidenced opening balances and existing long holdings.
This is a **separate newly created store**: it does not migrate an existing v1
store, change valid v1 accounting, import broker data or trade. The local
dashboard can display a selected v2 store read-only by default. V2 has no
configured valuation-freshness threshold, so check the displayed observation
dates rather than treating completeness as freshness.
V2 allocation policies, review acknowledgements and proposal exports are not
implemented; policy-dependent commands explicitly refuse them.

Manual browser recording is an explicit opt-in for an existing v2 store. Start
the recorded dashboard with `--investment-recording` in addition to
`--investment-database`. The recording page does not select or initialize a
store. Without that flag the dashboard remains read-only. Enabled sessions are
local and temporary; there is no user login. Each entry is previewed against the current
revision, then separately confirmed; the preview expires after five minutes,
and changed or stale journal state is refused. Repeating a committed request
within the session is safe, but a fresh preview is required after expiry or
conflict. This workflow records operator evidence; it does not independently
verify that evidence, contact a bank or broker, move money, authorize an order,
or provide an execution path.

The browser can record the opening balances and existing long holdings, later
contributions, withdrawals and transfers, paid interest and dividends, buy/sell
fills with recorded fees, price marks, and cash-balance observations. The
opening balance must cover every included account; a holding may have an
unknown opening price, which leaves its valuation incomplete rather than
inventing a value. A cash observation remains separate evidence: a discrepancy
is shown, not applied as a balance adjustment.

The only browser correction is an append-only replacement of the amount in the
immediately latest contribution, withdrawal or interest event. The original
event remains in the audit history; the correction retains its effective time
and records when the correction became available. Opening entries, fills,
marks, observations, older events and event identity cannot be corrected this
way. Corrections do not reconcile statement discrepancies automatically.
Review acknowledgements and cash-only valuation confirmations remain CLI-only.

### Version 2 management-role accounting

V2 reports cash and holdings by `core`, `dynamic` and `unassigned`, independent
of account and equity/bond class. Existing records stay unassigned until the
operator appends an evidenced `role_assignment` event. For cash, `product_id`
is null and `amount` is GBP; for a holding, `product_id` identifies the product
and `amount` is a quantity aligned to its increment. For example:

```json
[
  {"id":"attribute-example","type":"role_assignment",
   "timestamp":"2026-11-15T12:00:00Z","available_at":"2026-11-15T12:00:00Z",
   "evidence":"Operator-verified role allocation","account_id":"broker",
   "product_id":null,"role":"core","amount":"100"}
]
```

Attribution must be contemporaneous, after prior evidence is available, and
cannot exceed unassigned balances. It does not change aggregate cash, holdings,
valuation or historical performance. It cannot move balances from one role to
another, including via unassigned. After attribution, v2 contributions,
withdrawals, transfers between included accounts, interest, dividends and fills
can include `"role": "core"`, `"dynamic"` or `"unassigned"`; omitted roles are
unassigned. Debits and sales cannot consume a different role's balance. Account
transfers preserve role; splits preserve each role's attributed quantity.
The opt-in dashboard provides role selection and attribution using the normal
preview and confirmation flow. Corrections retain the original event's role.
Existing v2 stores remain readable with no migration or invented role history.
Once role accounting is active, recorded interest requires positive cash in the
selected role, dividends require a holding of the product in that role, and
both require a separate nonempty `role_evidence` describing the operator's
allocation of the payment to that role. Splits must preserve the declared
quantity increment for every role; activation is refused if an existing
holding is already off-increment. Dashboard records marked unassigned do not
activate role accounting, so pre-role records can still be entered in such a
store; explicit attribution remains blocked until its quantities are valid.
These checks do not prove entitlement or source completeness; the operator remains
responsible for the evidence and timing. A separately recorded withdrawal and
contribution are not independently verified as external transactions and could
misdescribe a role transfer; do not use them to bypass the transfer prohibition.

Role values require a complete common-clock portfolio valuation at or after the
latest attribution; a later assignment does not rewrite an earlier valuation.
Role-level returns, budgets, historical allocation, role-to-role movements,
policy approval and replenishment are not implemented. No allocation is inferred
from an account or product, and no loss is automatically refilled from the core.

Use the v1 account and product fields described below with `schema_version: 2`
and `"policy": null`. Import one first event with type `opening`, a unique
`id`, UTC `timestamp`, `available_at`, nonempty `evidence`, `balances_gbp`
mapping every included account ID to its exact decimal-string balance, and
`holdings` containing `{account_id, product_id, quantity, price_gbp}` entries.
Each nonzero holding must be in a broker account, uniquely identified and
increment-aligned; `price_gbp` is a positive decimal string or null when unknown.
Opening amounts are evidenced positions, not invented buys or contributions.
Pre-inception cost basis and lifetime gains are not reconstructed.

The first fully evidenced, common-clock valuation establishes the performance
baseline. If evidence was available later than its effective clock, or required
marks are missing, the valuation and baseline remain unavailable. A later
supported common-clock valuation can start measurement from that later clock;
the report distinguishes `since_baseline_gain_gbp` from unavailable
`lifetime_gain_gbp`. `net_contributions_gbp` records subsequent supported
external funding, including any events before a later baseline. Actual
interest, deposits, withdrawals, fills and marks still require explicit supported
events; a mismatching balance is not automatically corrected or labelled as
interest. The v2 report remains an offline manual record, not a verified bank
statement, broker buying power or proof of investability.

V2 also exposes a bounded `valuation_history` derived during journal replay.
It contains one point for each relevant event clock after all same-clock
evidence has been applied. Each point retains its source-availability clock;
late evidence or an incomplete valuation is an explicit gap with no value or
return. No values are interpolated between observations. Savings-term updates
and cash observations do not create valuation points, and cash observations
never refresh valuation. The dashboard charts at most the latest 500 points
and lists the latest 20; the report retains up to 10,000 points. This history
is evidence for recorded growth attribution, not proof of provider balances,
product performance, suitability or future returns.
The dashboard also charts the recorded time-weighted return since the baseline
and lists maximum drawdown at each displayed event clock. A complete valuation
can still lack a supported return; such points break the return line rather than
being treated as zero. Nearby points may be masked to make gaps visible; the
table retains their event clocks, including fractional seconds. The chart is
not a benchmark or a projection.
The history table also shows the flow-neutral return between consecutive
recorded event clocks, calculated from their cumulative unitized returns.
It is unavailable if either return is missing or the starting unit value is
zero; it never bridges a gap or implies calendar-period or benchmark coverage.
The starting UTC clock is shown even if it is outside the 20-row table.
After a baseline exists, an external flow without a complete contemporaneous
valuation invalidates the linked TWR/drawdown series; later marks do not
reconstruct the missing flow-time value.
The v2 snapshot also totals explicitly journaled fill fees, interest and
dividend payments, and exposes the underlying fee-bearing fills and income
events with effective/availability timestamps and evidence. The report retains
the bounded journal history; the dashboard shows the latest 100 journal entries
of each kind in original order. These are recorded-event details only: they do
not establish complete fee, income, entitlement, tax or cash-accrual coverage.
V2 also exposes contribution, withdrawal and included-account transfer events
with their evidence clocks. The dashboard shows the latest 100 in original
journal order; transfers are identified separately because they do not change
net external contributions. These operator records are not independently
verified account movements.

Readers verify every stored commit's replay hash for both versions; these
unkeyed hashes detect inconsistencies, not malicious tampering by a party who
can rewrite the entire database.

After opening, a v2 `cash_observation` event records one included account's
`balance_gbp`, effective `timestamp`, `available_at`, `source` and `evidence`.
Its reported difference is observed minus ledger-recorded cash at that event
clock. Append all other same-clock events first; multiple account observations
may then share the clock. Observations never change cash, contributions, return
or the baseline. Nonzero differences stay visible in warnings for explicit
investigation; a later matching observation does not resolve or hide an earlier
difference. No automatic repair or resolution workflow is implemented. Late
evidence does not refresh an earlier complete valuation, and a later
observation does not stand in for fresh holding marks. The v2 report retains
all observations in timestamp order in `cash_observations`; each account also
shows its latest observation. The dashboard lists the latest 100 while older
records remain available in the bounded report. These records are reconciliation
evidence, not balance corrections.

V2 preserves journal order for events sharing an effective timestamp. For
unambiguous performance measurement, record the required existing-holding
marks before same-clock contributions or withdrawals, and establish the first
complete valuation before recording same-clock interest or dividends. An
import that would make these clocks ambiguous is refused rather than assigning
a return by guesswork. Because journal events are append-only, a flow already
committed before its required same-clock marks cannot be reordered; record a
later complete valuation instead.
For example:

```json
{
  "id": "bank-statement-2026-11",
  "type": "cash_observation",
  "timestamp": "2026-11-15T12:00:00Z",
  "available_at": "2026-11-15T12:00:00Z",
  "evidence": "November statement balance",
  "source": "operator-entered bank statement",
  "account_id": "savings",
  "balance_gbp": "1000.25"
}
```

A v2 deposit account may include an optional profile `terms` object with
`annual_rate_pct`, `rate_basis` (`"AER"` or `"gross"`), `effective_from`
(date), `observed_at`, `available_at`, `source`, and optional
`maturity_date`/`notice_days`. Rate, source and dates are operator-entered facts
bound to the explicit profile, not verified provider data. The read-only
dashboard shows the reported basis and evidence clocks. Append a `savings_terms`
event to record a later observation; the latest terms are displayed while the
prior event remains in the journal. This is not a correction or deletion
workflow. Terms do not change cash, valuation or returns, and do not accrue
interest automatically. Do not edit immutable profile evidence or reinitialise
a portfolio merely to change terms; preserving an existing portfolio's opening
evidence and event history remains an explicit offline operation, not an
automatic migration.

A `savings_terms` event identifies an included deposit account and carries the
same rate, basis, effective date, source, maturity and notice fields as the
profile terms. Its `timestamp` is when the new terms were observed;
`available_at` records when that evidence became available; `evidence` records
the supporting notice/statement. For example:

```json
{
  "id": "savings-rate-2026-11",
  "type": "savings_terms",
  "timestamp": "2026-11-16T12:00:00Z",
  "available_at": "2026-11-16T12:00:00Z",
  "evidence": "Provider rate-change notice",
  "account_id": "savings",
  "annual_rate_pct": "3.75",
  "rate_basis": "AER",
  "effective_from": "2026-12-01",
  "source": "Operator-entered bank notice",
  "maturity_date": null,
  "notice_days": null
}
```

### Version 1 recorded input formats

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
tracking. These fields do not approve a role-aware policy.

Event input is a JSON array. Every account event contains `id`, `type`, `timestamp`,
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

`review_acknowledgement` and `cash_confirmation` are portfolio-wide journal
records, not account events. A review has `id`, `type`, `timestamp`,
`available_at`, `evidence`, `review_kind`, `reviewed_revision` and
`valuation_as_of`; the CLI creates it. A cash confirmation has `id`, `type`,
`timestamp`, `available_at`, `evidence` and `balances_gbp`. For both records,
`available_at` must equal `timestamp`. They are not broker statements or proof
of a trade.

Account-event monetary values, quantities and ratios use plain decimal strings,
not JSON numbers or exponent notation (at most 15 integer and 8 fractional digits).
Cash-confirmation balances accept up to 25 integer and 16 fractional digits to
preserve the exact recorded result of multiplying quantities and prices.
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

All v1 and v2 investment proposals are blocked until a role-aware policy,
actual weights, products and loss/transfer rules are approved and implemented.
V1 retains its existing asset-class target data for reporting and review; no
allocation planner is exposed. Review clocks and drift do not grant trade
authority. Scheduled contributions are not spendable cash, and deposit cash is
not broker buying power.

### Acknowledging a review

After personally reviewing the current recorded portfolio, obtain the current
revision and review status with `review-status`. Then record the review in the
offline CLI, replacing `5` with that exact revision and choosing a unique ID:

```powershell
python -m investment acknowledge-review --database .\investment.sqlite3 --kind monthly --reviewed-revision 5 --id review-2026-11 --evidence "Manual portfolio review completed"
```

Choose `annual` instead of `monthly` only when an annual review was actually
performed. A monthly acknowledgement clears the monthly calendar reminder until
the next UTC calendar month but does not reset the annual clock. An annual
acknowledgement resets the annual clock for one year and also counts as the
monthly review. An anniversary of 29 February falls on 28 February in a
non-leap year. A drift of at least five percentage points continues to trigger
review and eligible sales even after acknowledgement.

Acknowledgement is refused unless the reviewed revision is unchanged and the
recorded common-clock valuation is complete and strictly younger than the
policy's maximum mark age. The revision must already be committed; an import
cannot acknowledge an intermediate revision in the same batch. The check is
performed atomically with the journal append, so a concurrent change is refused.
Every prior journal event must also have been available by the acknowledgement
clock. `review-status` reports drift as unavailable when valuation is stale or
incomplete, or portfolio value is zero.
Fresh recorded evidence is not independent proof of current broker balances,
settlement or prices.

Acknowledgements do not change balances, holdings, valuation clocks or returns,
and do not approve or execute trades. They advance the append-only revision;
older software that does not understand the new event cannot read that journal.
Import any earlier-dated account evidence before acknowledging: later journal
entries cannot predate the acknowledgement. Repeating the same ID, kind,
reviewed revision and evidence returns the original acknowledgement, including
after an output failure; reusing the ID for different evidence is refused.
If the acknowledgement commits but its subsequent status read fails, the CLI
exits with an error and returns the receipt in stdout JSON with `review_status`
set to null and a `review_status_error`. A successful status may include a later
concurrent revision; it does not change the receipt's reviewed revision.
The dashboard stays read-only and review status/acknowledgement are CLI-only.
Previously recorded acknowledgements based on a stale cash-only valuation or an
uncommitted revision are refused during replay. There is no automatic correction:
preserve the original store and evidence and seek an operator-led recovery.

### Cash-only valuation confirmation

When there are no holdings, an operator can import a `cash_confirmation` event
using the ordinary `import` command to refresh the recorded valuation clock
without changing any money. `balances_gbp` must name **every** included account,
including zero balances, and every value must exactly match the recorded cash.
Discrepancies, missing accounts, holdings, delayed evidence and unsupported
decimal values are refused; this is not a correction or a reconciliation.

For example, after replacing the IDs, amounts, clocks and evidence with actual
operator-verified facts, a cash-only event file could contain:

```json
[
  {
    "id": "cash-check-example",
    "type": "cash_confirmation",
    "timestamp": "2026-11-15T12:00:00Z",
    "available_at": "2026-11-15T12:00:00Z",
    "evidence": "Contemporaneous manually checked evidence for both accounts",
    "balances_gbp": {"broker": "800.00", "savings": "200.00"}
  }
]
```

The common UTC clock must genuinely describe when all the evidenced balances
were valid and available, and every prior journal event must have been available
by that clock; do not invent current observations. The confirmation
is a manual attestation, not independent proof of account balances, protection,
settlement or undisclosed transactions. If a balance differs, stop rather than
guessing a correction. With holdings, fresh common-clock price marks remain
necessary; this cash-only confirmation cannot refresh them. A price mark for an
unheld product does not refresh the cash-only valuation.

No tight ATR stops are imposed on the new passive core. This is not a change to
legacy stops or a guarantee against losses. Market drawdowns, missing evidence
and operational failures are different concerns.

### Synthetic simulator mechanics (engineering only)

`investment.simulation.simulate_synthetic_matched_pair` runs two fixed target
recipes against one shared synthetic fixture: clocks, flows, prices, rates,
actions and cost assumptions are identical by construction. Either recipe's
failure aborts the pair. It does not model role budgets or loss-refill isolation,
approve policy, or establish dynamic alpha or suitability. Never use real
product history with this engineering-only harness.

`investment.simulation.simulate_synthetic_fixture` is a pure, non-CLI test
harness intended only for caller-supplied synthetic fixtures; it does not verify
their provenance. It requires synchronized GBP open/close observations whose
availability equals the event time. All fixture clocks must be explicit UTC
timestamps with at most six fractional digits; missing or more precise clocks
are rejected, not filled from the system clock or truncated. Inputs must be ISO
timestamp strings or standard Python `datetime` objects; subclasses such as
pandas `Timestamp` are rejected because their precision may be hidden. A caller
can provide a full-precision ISO string instead, which is still checked. It models fixed human-selected product
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

The investment responses have separate strict v1/v2 schemas and compiled
browser validators. Production assets are bound to both investment schemas and
the legacy schema checksums. Missing, incomplete or invalid values stay unavailable in the UI.
Refreshes only read reports unless the operator explicitly enabled manual
recording as described in [Version 2 opening evidence](investment.md#version-2-opening-evidence).
There is no browser trade-approval or execution route.

## Research and provider screen

### Design evidence reviewed on 2026-11-16

These public documents inform architecture and research priorities, not product
selection, account eligibility, trading approval or this portfolio's performance.
No market-history dataset was acquired.

| Verified source | Design implication and limitation |
| --- | --- |
| [Vanguard, Why, how and when multi-asset investors should rebalance](https://www.vanguard.co.uk/professional/vanguard-365/investment-knowledge/portfolio-construction/when-multi-asset-investors-should-rebalance), 4 January 2026 | Strategic rebalancing primarily maintains intended risk, not maximum returns. Retain low-turnover core maintenance; no universal optimal frequency or guarantee follows from its illustrations. |
| [Bank of England/FCA, Artificial intelligence in UK financial services](https://www.bankofengland.co.uk/report/2024/artificial-intelligence-in-uk-financial-services-2024), 21 November 2024 | Survey respondents identify analytical/operational benefits alongside data, third-party and model risks. Only 2% of reported use cases were fully autonomous. Adoption is not evidence of trading alpha; automate useful research and operations with explicit governance. |
| [ESMA, Guidance on AI in investment services](https://www.esma.europa.eu/press-news/esma-news/esma-provides-guidance-firms-using-artificial-intelligence-investment-services), 30 May 2024 | Highlights data quality/bias, opaque decisions, overreliance, privacy and security. Keep accountable policy ownership, evidence-linked explanations and deterministic execution constraints. EU firm guidance is design evidence, not a legal ruling on this UK personal tool. |
| [Hurst, Ooi and Pedersen, A Century of Evidence on Trend-Following Investing](https://www.aqr.com/Insights/Research/Journal-Article/A-Century-of-Evidence-on-Trend-Following-Investing), 31 October 2017, Journal of Portfolio Management | Motivates a small trend hypothesis family only. Its long/short futures/forward research does not establish returns for an unleveraged GBP long-only fund sleeve, nor authorise those instruments. |

Prefer verified primary evidence over an AI-generated research summary. Public
fund-manager research may have commercial interests; regulator surveys/guidance
do not measure this strategy's returns. The S&P SPIVA pages refused direct retrieval
in this review, so no purported latest report date or underperformance percentage
is asserted here. No source establishes an optimal 80-90% / 10-20% allocation.

Public-document review on 2026-11-15; no product dataset was acquired and no
performance run or account verification occurred.
The following initial screen retains its dated GBP 1,000 cost examples and
candidate products; it is not a current larger-capital fee calculation or selection.

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

### Read-only synchronisation discovery

The [automation guide](portfolio-automation.md#read-only-sync-provider-discovery)
compares Trading 212, IBKR Flex, bank aggregation and operator records. It
recommends a conditional combination based on existing accounts, not a platform
migration or a claim that one provider covers everything. Discovery has
verified public documentation only, not a credential's actual permissions.
Moneybox LISA and NatWest Digital Saver were named for evaluation;
no sync provider has passed account-level coverage/permission checks or been
connected. Future sync stages observations/discrepancies;
it must never automatically rewrite the accounting journal.

### Local portfolio awareness

[Portfolio awareness](portfolio-automation.md) implements explicit local
exposure-to-source mappings, existing source-dated research briefs and configurable
review reminders through CLI and the local dashboard. Optional operator-owned
profile entries also show dated thesis/watch history and manually noted calendar
events; they are editable notes, not an immutable audit log, automated feed or
assessment. It starts no collector,
calls no model or broker, and has no trading/accounting authority. Existing
US-focused source coverage and AI launch gates are unchanged. Calendar absence,
unknown coverage, stale briefs and unmapped exposures remain explicit.

### Offline observation preview

An operator-prepared local JSON observation file can be reconciled against an
already-opened v2 investment store with `reconcile-preview`. It compares explicit
GBP cash and long-holding quantities using evidenced local mappings; missing,
stale, incomplete, unmapped and time-incomparable evidence stays explicit.
The [observation contract and example](portfolio-automation.md#offline-observation-staging-and-reconciliation)
describe required timestamps, coverage and scope. The file is the staging
artifact; the preview writes neither it nor the accounting journal and cannot
correct discrepancies, infer transactions or access a provider.

## Explicit follow-on gates

This release includes only a synthetic mechanics harness, not a continuous
historical GBP product-performance simulator or a broker execution adapter. The
following remain unsupported/unverified:

- Historical source rights/vintages and full product/action/FX/cash-rate coverage.
- Executed FX, non-GBP account balances, settlement-specific broker funding.
- Distribution entitlement/payment reconciliation, automatic cash interest/tier/
  term accrual, corrections beyond the immediately latest contribution,
  withdrawal or interest amount, and operational migrations.
- Money-weighted return, inflation, tax scenarios, matched savings/passive curves
  and dated look-through concentration.
- Validated spread/slippage/quote/cost models for executable proposals.
- Exact venue permissions and uniquely evidenced ambiguous-order recovery.
- Licensed historical portfolio comparisons and the optional active challenger.
- Core/dynamic risk/transfer budgets, matched
  all-core attribution and revocable standing-policy execution.
- New brief-generation workflows beyond the existing public-evidence pipeline,
  immutable thesis/watch history or automatic thesis updates, UK/global
  calendar coverage and prospectively admitted intelligence-derived trading
  features. Local matching, reminders and editable thesis notes confer no approval.

### Trading 212 pilot: offline recovery engineering only

On 2026-11-17 the operator selected Trading 212 for an **offline-only** pilot:
one separately approved, cash-funded GBP long buy. No account, product, order
type, amount, budget or execution policy has been selected. This selection is
not permission to access an account, submit even a paper order, or enable
standing authority. Both investment proposal routes remain blocked.

The pure `investment.pilot.simulate_pilot_trace` replays caller-supplied
synthetic intent, approval, submission-attempt, acknowledgement, restart and
cumulative-fill observations. It tests that a lost response or restart before
an acknowledgement is quarantined rather than retried or matched to an
unowned order. Repeated cumulative observations count only new quantity;
conflicting declared order scope, decreasing fills and changed terminal
outcomes fail. Without an observation, filled and unfilled quantities are
unknown. Observed unfilled quantity is not an executable or active-order
quantity. It models neither Trading 212's wire schema nor actual venue responses
or source availability. It cannot
verify that input is synthetic and must not be used with private account data.

This is **not a broker mock adapter or runnable pilot**: there is no venue
client, authenticated approval, locking across a broker call, cash reservation,
settlement, fill-price/fee accounting, or connection to the investment journal.
`investment.pilot_store` separately persists these **synthetic test events**
in a dedicated, explicitly initialized append-only SQLite journal. Initialization
builds and validates a temporary copy before non-overwriting publication; failed
initialization does not leave a partial destination. Each append validates the
complete prior chain and replay in a transaction. Its v2 format encodes event
identity and payload as a canonical pair; development-only v1 journals using a
different hash encoding are rejected, with no migration path. Fresh connections
exercise close/reopen persistence and rollback, not recovery of real broker
state after process or machine failure. This internal test utility has no CLI or
integration with any operational store. The supplied policy and
approval IDs are declarations, not checked approvals. Ownership, funding and
spend-limit checks are explicitly unavailable; the limit in the fixture cannot
constrain a real fill. Outputs do not establish broker semantics, readiness or
permission to submit.

On 2026-11-17 a follow-up fetch of Trading 212's official [market-order
documentation](https://docs.trading212.com/api/orders/placemarketorder)
succeeded after an earlier transient transport-level fetch failure. It says
market orders execute at the next available price, warns of slippage, says the
endpoint is not idempotent and repeated requests may duplicate orders, and
returns a system-generated order ID. Its [historical orders
endpoint](https://docs.trading212.com/api/historical-events/orders_1) documents
cursor pagination (up to 50 items) and order/fill/wallet-impact fields. The
pages do **not** document a client-assigned idempotency key or a uniquely
verified recovery procedure after an ambiguous submit. A market order also
does not provide the spend/price cap required for a bounded GBP pilot. These
facts rule out blind retry; they do not prove that a matching history record
uniquely identifies the submission, that history is immediately complete, or
that a limit/other order type would resolve the same recovery gaps. Exact
account scope, response timing, fill revisions and settlement semantics still
need verification.

Before any real or paper pilot, separately establish and approve the actual
account/wrapper and product eligibility, order type and executable price/cost
bound, available settled GBP and fees, role-aware policy and per-order approval,
expiry/revocation, fresh independent reconciliation, and loss/position limits.
Implement durable account-bound intent, single-owner concurrency, unknown-outcome
quarantine, uniquely evidenced ownership/recovery, exactly-once partial-fill and
fee accounting, settlement handling and restart procedures. Prove cancellation
and protection semantics where applicable. Independent review, explicit
task-specific broker authorization and venue-specific recovery evidence are
required; otherwise execution stays manual outside GoldScout. No research or AI
output supplies any of these approvals.

### Continuation order

1. **Manual investment-capital management — implemented:** V1 supports
   evidenced empty inception; V2 can record opening cash and long holdings,
   subsequent activity, valuations, reconciliation observations and narrowly
   bounded evidence-backed corrections. V2 role attribution and reports preserve
   aggregate totals without inventing prior cost basis or lifetime return.
   Remaining work is any explicitly scoped accounting extension such as
   FX/action/entitlement support or verified imports. Do not import household
   cash or infer unrecorded transactions.
2. **Core/dynamic accounting — role attribution implemented; policy gated:**
   core/dynamic/unassigned balances are independent of asset class and account,
   with no cross-role debits or role-to-role transfers. Approve exact weights,
   products, risk budgets and loss/replenishment/profit-transfer rules before
   implementing policy-dependent proposals or funding experiments. Generic
   equity/bond/cash drift is not sufficient.
3. **Portfolio awareness and observation — local projection and offline preview
   implemented; provider sync discovery only:** explicitly configured local
   exposure matching, existing dated briefs/calendar alerts, review reminders,
   operator-maintained thesis history/manual calendar watch and
   operator-prepared GBP observation previews are read-only. Choose and verify a
   genuinely read-only provider, account, permission scope and data terms before
   implementing or enabling any sync adapter. No account data is sent to hosted
   AI. Further scheduled brief generation, immutable thesis tracking and broader
   calendar coverage need separate design and validation.
4. **Evidence and simulator engineering, in parallel with useful tracking:** close
   product/account, cash-terms, persistent source rights/vintages, action/FX,
   cost/tax and frozen-protocol gates before product-performance runs. Synthetic
   tests validate engineering only. Build continuous combined/core/dynamic
   accounting, not stitched USD windows. Compare a finite locked strategy family
   with matched all-core and savings alternatives. Stop at unavailable evidence.
5. **Execution progression:** first a separately approved per-proposal execution
   pilot, then revocable standing authority for approved core maintenance and
   admitted dynamic strategies, after broker trials/recovery tests and independent
   review. Trading 212 is selected for offline pilot investigation only; no
   adapter or standing authority is implemented. Reuse legacy safety concepts,
   not unverified Alpaca-specific retry semantics.
6. **Experimental admission and retirement:** require selection/locked
   confirmation and prospective shadow evidence before separately approved
   deployment. Track incremental net benefit and risk; retire or suspend failed
   hypotheses under approved rules rather than chase losses or force a winner.
   AI/calendar-derived trading features need their own prospective admission.

This roadmap authorises no account access, orders, secret access, operational
migrations or guard changes. Until execution is separately approved and recovery
is validated, proposals remain blocked and any investment decisions/execution
remain outside the application. No favourable report is approval to invest.
[Market intelligence](intelligence.md) remains advisory-only in the current
runtime. Local awareness reads explicitly selected existing public evidence; it
does not authorize AI-derived trading signals or change the intelligence
service's opt-in collection and analysis controls.
Preserve all earlier trials and consumed holdouts under the
[context](investment-research-context.md) and [protocol](backtesting-protocol.md).
