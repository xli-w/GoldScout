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
a wizard; startup, status and preview never write files. Review accounts,
products, cash and long holdings with explicit opening evidence, then confirm
the bound preview. No login or hand-written profile is required.

The wizard walks through **Accounts**, **Investments**, **Starting balances**,
and **Review and save**. Cash-only users can skip adding investments. Expand
the help panels for privacy/storage, account types and quantity rules; required
fields remain visible. Missing prices remain unknown, not zero. Enter current
cash and holdings; the opening is timestamped when you request its preview, and
the review shows that one time in local and UTC formats before confirmation.
Leaving or reloading loses an unsaved draft.

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

On **Your portfolio**, start with **What needs attention** and the recorded date.
These are not live bank balances. Optional roles and detailed history are
collapsed; section shortcuts open the relevant panel. Performance help
explains the starting point (baseline), cash-flow-neutral return and largest
fall (drawdown). **Add a record** first asks for a category and then the record
type; it still requires a reviewed preview. Recording dates and purchase/sale
fees start blank: enter source-supported values, including zero only when
verified. Corrections reject a source-availability time earlier than the
correction clock instead of silently changing it. Monitoring snapshots remain separate comparisons,
not transactions or repairs. Technical identities and provider details remain
available in expandable panels; errors and missing-data warnings stay visible.

Read-only recording pages give visible restart instructions: stop the existing
dashboard and add `--investment-recording` to the original startup command,
retaining its workspace/journal selection. The page never enables writing.
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
Its date inputs still explicitly require UTC. Monitoring and awareness are
optional and require separate local configuration; the investment guide links
to the relevant instructions. Recovery starts with safe actions:
keep the whole setup folder unchanged, check/re-enter the exact original figures
when offered, and review before confirmation. Committed or uncertain save outcomes
must never be treated as permission to recreate records.

Creation stages and replay-validates the new journal before no-overwrite
publication. The journal's embedded profile is authoritative; profile export
and receipts are separate filesystem operations. A later failure must not
delete a committed journal or claim rollback. Preserve conflicting artifacts;
only provenance-bound setup recovery is permitted.

Existing journals open read-only. Damaged, foreign or older-format journals
are rejected, not migrated, reset or repaired. Journals containing retired
policy-review acknowledgement events are also refused, even if they carry the
current wire version; there is no event migration. `--investment-database <path>`
selects an existing journal and never falls back to setup.
Later manual writes require separate `--investment-recording` opt-in.
Same-origin, CSRF, revision and preview checks are mandatory but are not user
authentication. Keep the dashboard on loopback.

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
