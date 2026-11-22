# GBP portfolio evidence journal

GoldScout records selected savings, first-home LISA and longer-term investment
evidence in GBP. It does not independently verify account truth, select products,
execute trades or implement an approved allocation policy. The
[investor mandate](investment-research-context.md) governs direction; the
[research protocol](backtesting-protocol.md) governs any future comparison.

## Guided first-run setup

```powershell
python -m dashboard
```

Source-checkout startup selects the ignored `local-investment/` workspace.
Installed use requires `--workspace <directory>`. Missing/empty workspaces show
a wizard; startup, status and preview never write the journal. Review accounts,
products, cash and long holdings with explicit opening evidence, then confirm
the bound preview. Setup form drafts save in this browser on this computer;
full previews and acknowledgement checks are not restored. An exact pending
reference for an uncertain recording save is retained to retry only that save;
the interface blocks a new entry until it is resolved. Recovery re-entry is not
saved. No login or hand-written profile is required.

## Optional personal goals

The Goals page is a separate, optional local profile for purpose, target and
access dates, liquidity needs, comfort with losses, and amounts the user chooses
to earmark or contribute. A goal may associate one or more whole active accounts
from one portfolio, with each account assigned to at most one goal. The
association is a label, not a transfer or reservation. A linked amount is shown
only when the recorded portfolio value is complete and reconciles to its
account records; it replaces, never adds to, a manually entered amount. Linked
values are dated recorded evidence, not current provider balances. A simple
contribution illustration assumes no growth, interest, fees, tax or withdrawals;
it is not a forecast or investment recommendation. Editing saves in one action,
using an internal revision-bound preview; viewing does not create
the profile. The profile is local plaintext outside the journal workspace, is
not included in journal backups, and is not sent to hosted AI. Source-checkout
and `--workspace` launches choose a sibling `.goldscout-planning/<workspace-name>.json`
path. With `--investment-database`, use `--planning-profile <path>` to opt in.
Existing v1 and v2 goal profiles are read with new fields defaulted to unknown
or unlinked, and upgraded only after a confirmed save. Protect and back up this
file separately using storage you control.

The portfolio shows one next action per account and lets the user set a local
browser-only review interval (weekly through yearly). A complete value is
complete only for its recorded date; an old complete value is overdue under the
selected interval. Cash, units and price dates remain operator-entered evidence.
Review intervals and review dates are reminders, not journal records or provider
checks. A guided review walks across active accounts and resumes in the same
browser. A confirmed supported account record in the same browser advances the
review and clears the affected account's check (both accounts for a transfer).
Other journal changes, revision gaps, or an uncertain retry without its preview
leave progress stale and require a restart. Checking a quiet account needs no
new journal record. Finish the review to save an available complete value for
the next comparison; an incomplete value is not saved. Its completion summary keeps missing
values, cash differences and correction-related checks visible; completion is
not an all-clear. A cash difference or unavailable full value is shown before
the request to choose a review interval. Earlier differing cash checks remain
attention items even if the latest check matches.
Review progress is bound to portfolio ID and revision, not a journal history
hash: after restoring or switching to a divergent journal with the same ID and
revision, start a new review. Snapshot checks and browser storage are not atomic
with external writers; a later refresh can reveal a stale review. Browser
coordination does not prevent outside journal writes.
Savings accounts can surface missing or overdue rate, access, notice and maturity
terms using a separate browser-only recheck interval, and an approaching
recorded maturity date. These are prompts to verify current provider
information, not rate comparisons or claims about obtainable returns. Explicit
account/platform charges and buy/sell fees are recorded costs; fund, subscription,
FX, tax, spread, slippage and other costs may be missing. Direct concentration
shows each named holding's share of complete recorded portfolio value, without
look-through or a suitability assessment. Net money added is distinct from gain
since tracking began, which reflects only recorded activity and charges.
Wrapper contribution limits, LISA eligibility/bonus entitlement,
fund overlap and suitability are not inferred.

The user may mark a complete recorded value as reviewed. GoldScout stores only
its date and journal revision in browser storage, then compares that dated point
with later recorded values using the existing evidence-linked attribution,
including an unexplained remainder. This marker is not a snapshot of the
journal; if the valuation point is unavailable or history was corrected, the
comparison fails closed. Saving a new comparison point replaces the old one.
It is separate from marking an account checked for reminder purposes; neither
action verifies the accounts or resolves discrepancies. Neither browser-only
setting is included in a journal backup.
Incomplete setup and recording form drafts are also browser-local and excluded
from journal backups. A restored recording draft is bound to the exact
portfolio, journal revision and form route. After a journal change it must be
discarded rather than reused; no full preview or acknowledgement is restored.
The separate uncertain-save reference only supports retrying the same
confirmation, never composing or confirming a different record.

The portfolio's **Tools > Backup and recovery tools** section downloads an
explicitly requested verified SQLite copy with its receipt. It displays the selected
journal path; the browser controls the download destination. The archive is not
encrypted. Restore rehearsal verifies and copies a selected backup only to a
temporary new location and removes that copy; it never installs or overwrites
the active journal. Keep the goals profile, source documents and setup receipts
separately, and choose a protected off-device destination yourself.

## Optional eToro connection

The startup wizard offers an optional eToro connection, and the portfolio shows
the provider's latest in-memory read alongside the recorded accounts. Choose
the environment and explicitly enable the connection: it reads immediately,
binds the returned account and enables one read per server startup until
disabled. Either key scope is accepted; transport is fixed to GET and has no
order runtime. The eToro snapshot is displayed separately; it
does not create or populate a GBP journal account, balance, holding or mark.
The provider figures are not included in portfolio totals. On the portfolio
screen, connection controls remain accessible if its journal snapshot is
unavailable. During interrupted or blocked setup, or after a setup receipt,
status and disabling are available when the local eToro session is available,
but new reads are blocked there. If activation
or a refresh has an uncertain outcome, the current dashboard tab blocks further
reads, even after revocation; status and revocation remain available when
possible. Interrupting a connection read or leaving the panel during a read or
revocation also requires this check. A status or snapshot is not a request-specific
receipt. The same tab can check a process-local activation receipt without
another provider read; an unknown receipt does not prove that no activation
committed. The receipt and server-observed read status are process-local; the
receipt lookup reference and UI uncertainty block are tab-local and are lost on
reload. Other tabs may observe the same process's uncertain status, but that
status can change or be associated with a different connection. Neither other
tabs nor other dashboard processes are guaranteed to block further reads after
an uncertain outcome. Reloading does not resolve the uncertainty. Check the dashboard's logs and
verify the outcome before another read. Keys remain in private server-side
configuration, never browser fields; status checks use the effective credential
revision without making a provider request.

The panel shows provider account totals in their account currency, direct
instrument aggregates with each asset's currency, and separate copy groups.
Provider timestamps are displayed in UTC. It also shows a separate
provider-reported GBP display balance when usable. eToro documents a Trading
account's balance as total account value, not spendable cash; the balance read
has no source timestamp and is not synchronized to the portfolio snapshot.
A missing, zero or negative exchange rate makes that balance unavailable in the view.
See eToro's
[balance endpoint documentation](https://api-portal.etoro.com/api-reference/balances/get-balance-for-a-specific-account).
The documented
portfolio response does not identify every instrument class or individual
position leg, and pending-order details are unavailable beyond the provider's
reserved-cash total. No FX, holdings valuation, or journal import is inferred.
After a manual refresh, the panel also displays up to 365
calendar days of closed-trade history, bounded to 1,000 rows. That history is
kept in dashboard memory only; it is not fetched at startup or saved as history.
The API response does not identify row-level currency, instrument type or
account ID. It is not a complete transaction ledger or journal evidence.
`.etoro/connection.json` holds activation, environment and account identity, not keys or
portfolio/history data.

For an available real-account read and a recorded journal, **Review this eToro
account against your records** lets you explicitly choose and confirm a journal
account, then pair distinct provider rows with recorded holdings for a
side-by-side review. Pairings are temporary and disappear when the view,
provider snapshot or journal revision changes. Rows sharing an instrument ID
across direct/copy groups are not combined or paired. Journal cash and provider
available cash are shown separately in their own currencies, not reconciled.
Provider net units and recorded units may refer to different clocks or exposure
types; neither equality nor a difference establishes a match, transaction or
error. The account-update link uses the normal manual review flow and does not
copy provider data. No provider data or mapping is saved by this review.

Activation has no arbitrary expiry. Legacy consent or permission records,
including expired and revoked records, do not enable startup reads: explicitly
enable again. Corrupt records are not overwritten or guessed. Startup reads
collect the current portfolio only, at most once per server startup; status and
page loads never collect. The visible panel rechecks local status on focus and
periodically; a cached snapshot is hidden if its activation no longer matches.
A change in another process may remain visible until the next status check.
Manual refresh reads the bound portfolio and history
immediately. Disable stops future reads until explicitly enabled again.
Credentials with trading scope can place, modify or cancel orders if exposed or
misused, even though this collector uses GET only. Disable remains available while
a manual read is waiting for the provider. If revoked during a read,
the tab treats the read outcome as unverified and blocks further reads pending
the checks described above. Failed startup reads are not retried automatically.
Known limitations: revocation cannot guarantee cancellation of an already sent
request or eliminate races across dashboard instances; an expired local session
or its shared command rate limit can also prevent revocation. Each read pins
one credential snapshot and checks it before requests and publication. Resolving
separate configuration sources and checking before dispatch are not one atomic
transaction with external changes or revocation; an already dispatched GET
cannot be recalled. Refresh checks
the account binding before requesting the separate balance and history. A
history failure is reported separately; it does not turn the current portfolio
snapshot into history or journal evidence. Do not
rely on the collector when those guarantees are required. The provider
integration is engineering-only and has not been live-validated; eligibility,
demo balance environment association, account-specific response behavior and data-retention rights remain the
operator's responsibility.

The wizard walks through **Accounts**, **Investments held (if any)**, **Starting
balances**, and **Review and save**. Setups with only savings / deposit accounts
go directly to starting balances. In investment setups, list each different
share or fund once, then enter its quantity separately for each account that
holds it. Cash-only investment accounts can continue without adding investments,
but each account still needs an explicit completeness confirmation.

Before starting, have current cash and holding quantities for a consistent
point in time, a brief description of the dated sources, and the provider's
GBP price, smallest supported unit amount and minimum purchase value for each
investment. Check the provider's dealing rules or product documents. Those
details validate the record; if you cannot verify them, pause rather than
guessing. The opening is timestamped when you request
its preview; setup does not backdate. Check that the figures still reflect the
same current point in time rather than treating an old statement as current.
Confirm the current-point check again after returning from a preview before
requesting another one. Removing an account or investment removes its linked
holding rows. Changing a holdings account to savings/deposit keeps those rows,
which must be reassigned or removed before saving; changing a holding's account
or investment requires the affected completeness checks again. The review shows the preview time in local and UTC
formats before confirmation. Form values can be resumed in the same browser;
after returning, recheck all figures and completeness confirmations. Previews
are always requested afresh.

Fictional setup examples distinguish an investment from the units held in an
account. Quantity steps and minimum purchases must come from the provider's
rules for that investment/account; unknown values are not prefilled in the
manual investment-addition form. Pause if a required fact cannot be verified.
Setup and recording reviews show local time with its timezone alongside the
unchanged UTC timestamp. New setup records both opening clocks at preview
submission because the figures are entered as current; setup does not backdate
them. Interrupted-setup recovery still requires the original opening clocks to
reconstruct and verify the exact record. Ambiguous recovery times when clocks
move back are refused rather than silently choosing an occurrence.

On **Your portfolio**, start with **What needs attention**, the valuation
date and each account's next action, then choose **Start account review**.
The comparison and recorded holdings are in **History and insights**. Historical
performance limitations are shown separately from immediate value and cash
issues; they do not prevent normal recording. This is not a live bank balance;
latest cash and unit counts can be from
different journal dates. Each holding's price-effective and source-available
dates are available in a collapsed detail. Optional roles and detailed history
are collapsed in History and insights. Performance help
explains the starting point (baseline), cash-flow-neutral return and largest
fall (drawdown). A missing-price link identifies the affected account and
investment and opens a mark-record draft with those fields selected only when
that holding still appears in the current saved records. Verify the source,
price and dates; a later price cannot complete an earlier valuation. **Add a
record** presents record types in one grouped selection; it still requires a
reviewed preview. Recording dates and purchase/sale
fees start blank: enter source-supported values, including zero only when
verified. Corrections reject a source-availability time earlier than the
correction clock instead of silently changing it. Monitoring snapshots remain separate comparisons,
not transactions or repairs. Technical identities and provider details remain
available in expandable panels; errors and missing-data warnings stay visible.

Under **History and insights**, **What changed between recorded values?** compares the latest complete
portfolio valuation with an earlier complete recorded valuation you select.
It separates recorded external deposits and withdrawals, newly included
account value, interest and dividends, and recorded purchase/sale fees.
The unexplained remainder may include price movements or missing records; it
is not a verified return. Expand supporting activity to inspect the effective
and available times and evidence. Internal transfers are not external money
flows. An incomplete latest value, contradictory or late evidence, or a
historical correction prevents the comparison; intermediate gaps are shown
without filling them. An optional browser-local comparison point records a
valuation date, not proof of an account review. GoldScout does not independently
verify the records or know that all activity and fees have been captured.

For a regular account review, choose **Open account** beside that account. The guided
page combines recorded cash, the last cash-check difference and holding price
dates, then offers plain-language actions for transactions, cash checks and
price updates, account charges and savings terms. No monitoring configuration is needed. Each action still needs
its own preview and confirmation; saving returns to the account review, not
an automatic all-clear. A missing or retired account/holding is never replaced
with another selection. Provider snapshots are not imported by this workflow.

Before entering a deposit or withdrawal, obtain source-supported prices for
every portfolio holding at the transaction time, available by that time, and
record them before the flow. Do not invent prices or alter true dates to keep
performance available. The preview shows the valuation and performance
consequences before saving. If a previously available value, return or
drawdown would disappear, the preview shows a warning; the recording-impact
checkbox is no longer required, while the bound record preview remains.
Cash-check previews show the historical difference
without changing cash; neither a match nor acknowledgement resolves earlier
differences or verifies an account.

After completed setup or recovery, choose **Add a record** for future updates;
no restart is needed. Normal local launches permit manual recording in the
selected journal. Add `--investment-read-only` to your original command for
viewing without journal writes; it also prevents setup from saving. Keep the
same workspace/journal selection. The older `--investment-recording` option
remains accepted, but cannot be combined with read-only mode.
Each save still needs a bound preview and explicit confirmation. The final
confirmation button is the setup review acknowledgement; no extra review
checkbox is required. Source dates and holdings-completeness checks remain.
Missing-price and cash-review links open the relevant records or guidance.
Later prices cannot repair earlier performance gaps; historical corrections
do not automatically establish a replacement baseline.
If a recording confirmation fails or its response is lost, the record may
already be committed. Browser-local storage keeps only the pending preview and
event references and expected revision, blocks new recordings in that browser
origin and offers the same confirmation retry. A browser lock coordinates
previews and confirmations across tabs on that origin. If the session or preview
has expired after an uncertain result, recording remains blocked there; do not
enter the record again. Check the journal and terminal before further action;
there is no automatic resolution. This does not coordinate separate browser
profiles or origins (including `localhost` and `127.0.0.1`) or verify the journal.

Monitoring uses "snapshot", "investment match" and "save request reference" in
its forms; exact payloads, revisions and retry references remain available.
Required matching/source facts remain visible and are never inferred from names.
Its date inputs use your computer's local timezone and are converted to UTC for
review; snapshot, source-availability and matching-evidence clocks remain
separate. Monitoring and awareness are optional and require separate local
configuration; the investment guide points to the local setup instructions.
Snapshot comparison needs a separately selected monitoring cache and review
store; opening the page never enables writing or contacts a provider.
Recovery starts with safe actions:
keep the whole setup folder unchanged, check/re-enter the exact original figures
when offered, and review before confirmation. Committed or uncertain save outcomes
must never be treated as permission to recreate records.

Creation stages and replay-validates the new journal before no-overwrite
publication. The journal's embedded profile is authoritative; profile export
and receipts are separate filesystem operations. A later failure must not
delete a committed journal or claim rollback. Preserve conflicting artifacts;
only provenance-bound setup recovery is permitted.

Existing valid journals allow manual recording on a normal launch. Damaged, foreign or older-format journals
are rejected, not migrated, reset or repaired. Journals containing retired
policy-review acknowledgement events are also refused, even if they carry the
current wire version; there is no event migration. `--investment-database <path>`
selects an existing journal and never falls back to setup.
Same-origin, CSRF, revision and preview checks are mandatory but are not user
authentication. Keep the dashboard on loopback.

Reassess provider scope semantics, eligibility, retention terms and response
coverage before live validation. Manual refresh does not add automatic retries
or persistent provider history. The separate CLI collector uses no approval
file; its watch pins a credential revision and stops on a change.

## Manual CLI and input contract

```powershell
python -m investment demo
python -m investment init --profile .\profile.json --database .\investment.sqlite3
python -m investment import --events .\events.json --database .\investment.sqlite3
python -m investment report --database .\investment.sqlite3
```

These are explicit operator actions, not permission to access operational files
while developing. Tests must use temporary synthetic files. The demo has no
persistence or network calls. `init` creates an empty journal; a complete,
explicit opening event must precede financial events.

Only wire `schema_version=2` is supported. It is an immutable wire identifier,
not a product edition. Older profiles and journals are explicitly rejected;
there is no migration or compatibility alias.

Profiles contain exactly `schema_version`, `portfolio_id`, `currency`,
`created_at`, `accounts`, `products`, `policy`. Currency is GBP and policy is
null. There are 1-50 accounts and at most 100 products:

- Account: `id`, `name`, `kind` (`broker` or `deposit`), `binding`, `currency`;
  deposit accounts may additionally record evidenced `terms`.
- Product: `id`, `name`, `sleeve` (`equity` or `bond`), `currency`,
  `quantity_increment`, `minimum_notional_gbp`.
- Monetary/quantity inputs use bounded exact nonnegative decimal strings,
  never binary floats. Clocks are explicit aware UTC evidence.

Opening records contain `id`, `type: "opening"`, `timestamp`, `available_at`,
`evidence`, `balances_gbp` for all included accounts, and `holdings`.
They establish cash and long holdings, not past transactions, cost basis or
lifetime gains. Use the guided preview rather than guessing event payloads;
[`investment/core.py`](../investment/core.py) owns strict validation.

Financial events include contributions, withdrawals, account transfers,
buy/sell fills with explicit fees, marks, splits, paid interest and dividends.
No FX, inferred settlement, automatic interest or distribution entitlement is
implemented. Missing or unknown facts are not zero.
The dashboard's recorded-fee total sums explicitly entered buy/sell fees and
account/platform charges; it is not a full cost-of-ownership measure. Fund
charges, tax and FX may be missing, and spread or slippage are not separately
measured.

## Accounting and reporting

- Core/dynamic/unassigned attribution is independent of account and asset
  class. Roles conserve aggregate balances, isolate debits and survive account
  transfers. Role-to-role movements are disabled; unassigned does not imply core.
- Profile amendments are append-only. Identity, binding/currency and product
  classification/quantity rules remain fixed. Retirement requires zero exposure.
  Funded account-scope additions require complete contemporaneous evidence and
  an existing valid baseline; they are unitization-neutral, not contributions.
- Effective time and evidence availability remain distinct. Late marks cannot
  establish earlier complete value. Missing held-product prices make value
  incomplete, not a complete subtotal. There is no journal freshness policy.
- A complete common valuation establishes the performance baseline. Marks must
  precede same-clock external flows, and baseline must precede same-clock income.
  Missing flow-time value invalidates linked performance; later prices cannot
  reconstruct it. Unitization remains linked through full redemption/refunding.
- Cash observations never change balances or refresh valuation. Historical
  corrections replay observations against corrected cash, so earlier reported
  differences can change or disappear. Reports surface an attention warning
  when corrections and observations coexist; preserve prior reports for review.
  Savings terms are display facts, not accrual/eligibility. Valuation history
  preserves incomplete clocks as gaps, never interpolation.
- Supported financial corrections append evidence and replay downstream events,
  refusing invalid cash, quantity, role or chronology. Original evidence and
  availability remain. Historical corrections invalidate performance without
  automatically establishing a new baseline. Opening, profile/scope, role,
  observation and review events are not correctable.
- Fee, income and capital-flow histories show explicitly journaled evidence,
  not complete tax, expense or entitlement coverage. Included-account transfers
  are internal flows, not returns or new contributions.
- Exact cash-only confirmations preserve their causal accounting constraints;
  they are not trade approval or independent custody evidence. Policy review
  acknowledgements and the retired scheduler are not product features.

Investment proposals remain blocked: no proposal export or order path exists.
Exact targets, products, role budgets, loss/replenishment and transfer rules
need separate approval and implementation. An illustrative allocation, role
assignment, reminder or monitoring review action grants no authority.

## Local supporting tools

- [Journal lifecycle](investment-journal.md): explicit backup/restore, receipts,
  limits and incident preservation.
- [Monitoring](monitoring.md): local evidence-quality/concentration findings,
  separate purgeable observations and separate review workflow.
- [Awareness and reconciliation](portfolio-automation.md): local public-context
  projection and staged observations, never automatic accounting repair.
- [Intelligence](intelligence.md): opt-in advisory collection/briefs, never private
  account inputs to hosted AI or trading authority.
- [Provider feasibility](provider-feasibility.md): public evidence is not verified
  account permission, source rights or production validation.

## Synthetic simulator mechanics (engineering only)

`investment.simulation.simulate_synthetic_fixture` is a pure, disconnected
engineering harness, not a consumer of product histories or `research_data`.
It checks synchronized synthetic GBP sessions, next-open buy-only fills,
positive contributions, cash-funded withdrawals without liquidation, explicit
proportional fees and increment-preserving splits known by modeled opens.
Explicit distributions use pre-ex-open holdings, create unspendable receivables
included in allocation/portfolio value, then pay at a later modeled close before
external flows. Same-product ex-open splits are rejected. Prices must not already
include distribution adjustments; the harness cannot verify their basis.

Synthetic annual rates require a first-open rate and use simple ACT/365 at
modeled boundaries; overnight uses the prior rate, and changes apply after
accrual at their boundary. Initial capital arrives at the first close and earns
no earlier interest. Missing clocks, sub-microsecond values and datetime
subclasses are rejected, not silently truncated. Unit value remains linked
through complete cash redemption and later funding.

It omits sales/liquidation, actual cash-product terms, source entitlement, tax,
FX, inflation, fixed fees, spreads/slippage and actual execution/calendar behavior.
Inputs and output must remain synthetic-only. These checks do not close any
external feasibility gate, establish returns or permit data acquisition.
There is no broker pilot or execution runtime.
