# Local portfolio dashboard

The dashboard presents the GBP investment journal, optional local monitoring
and advisory intelligence. It has no trading runtime or account/order mutation
routes. Investment reads are nonmutating and never fall back to fabricated data.

## Start and build

For a source checkout on Windows, run these once from the repository folder in
PowerShell to install dependencies and build the frontend:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dashboard.txt
npm --prefix frontend ci
npm --prefix frontend run build
```

Then start the dashboard from that folder:

```powershell
.\.venv\Scripts\python.exe -m dashboard
```

To preview with synthetic data instead of a real workspace:

```powershell
.\.venv\Scripts\python.exe -m dashboard --demo
```

Dependency installation needs network. A packaged wheel includes built assets;
do not assume stale assets represent current source. Use
`.\.venv\Scripts\python.exe -m dashboard --help` for current options.

Normal `python -m dashboard` uses the source checkout's ignored
`local-investment/` workspace. Installed use needs `--workspace <directory>`.
The [guided setup](investment.md#guided-first-run-setup) confirms exact opening
evidence before creation. Explicit `--investment-database <path>` selects an
existing selected journal. Older formats fail explicitly with no migration.
Synthetic `--demo` uses temporary evidence, not a broker or real workspace.
For a selected workspace, the optional Goals page uses a separate local profile
outside the journal workspace. Source-checkout and `--workspace` launches select
`.goldscout-planning/<workspace-name>.json` beside the workspace by default;
when selecting a journal directly, opt in with `--planning-profile <path>`.
Viewing does not create that file. Changes require a revision-bound preview and
explicit confirmation. A companion lock file serializes writes from concurrent
dashboard instances. The profile stores purpose, target/access dates, liquidity
needs, comfort with losses, and user-entered current/monthly amounts as local
plaintext. Simple illustrations assume no growth, interest, fees, tax or
withdrawals; they do not read account balances and are not forecasts or
recommendations. Existing v1 goal profiles are read with new constraints marked
unknown and upgraded only after a confirmed save. It is not part of journal
backups or sent to hosted AI. Choose a device and backup location you control.

Normal launches enable manual recording for the selected journal, including
immediately after completed setup/recovery. Add `--investment-read-only` to
disable journal setup and recording. The older `--investment-recording` option
remains accepted, but cannot be combined with read-only mode. Embedded
`create_app` callers still default to recording disabled.
Same-origin/CSRF checks, bound previews and exact revision confirmation remain.
Partial or uncertain setup does not enable recording.
Monitoring cache/workflow writes have separate gates; a page refresh never
collects provider data or updates accounting.

The portfolio view leads with recorded balances and holdings; optional checks,
advisory context and detailed evidence remain available without being required
for basic use. Recording previews show a human-readable summary with the exact
event retained in expandable details. Monitoring observation entry uses
separate holding and explicit mapping fields; comparison results are displayed
separately from append-only review-inbox actions. Neither workflow status nor a
reconciliation match establishes current custody or approves a trade.
Each account has one status and next action, using a user-selected weekly-to-
yearly cadence stored only in this browser. The status shows shared portfolio
valuation dates, account cash and holding evidence; it does not invent an
account-specific value history or provider agreement. A complete value remains
complete only for its recorded date, and an older complete value can be overdue.
Savings-term reminders use only dated entered facts and direct users to verify
rates, access, notice and maturity with the provider; they do not compare
obtainable returns. Recorded fill fees are not a full cost assessment. Cash
observations do not update journal cash. Recent recorded activity lists dated
flows, income, fills and corrections, not all changes or a complete attribution
of returns.
**What changed between recorded values?** compares the latest complete
valuation with a selectable earlier complete valuation (the latest 100 earlier
choices, plus a still-available older selection). It displays exact recorded
external deposits, withdrawals, newly included account value, income and
purchase/sale fees, alongside an
unexplained remainder and source-linked activity. Internal transfers are not
external flows. The later endpoint must match the current complete portfolio
value; contradictory or late evidence and historical corrections make the
comparison unavailable. Incomplete intermediate points are flagged, not
reconstructed. The remainder is not a verified return or a complete explanation
of price movements. A browser-local review marker can anchor this comparison to
the complete valuation date last reviewed; it stores only a date and journal
revision, not a journal snapshot. It does not establish current balances,
complete activity, external fees, or custody.
For recorded portfolios, a selected comparison date remains selected across
refreshes while still available; otherwise the comparison asks for a new date.
Demo dates move with its synthetic clock, so its selection follows the same
relative history position instead.

The portfolio's recovery section downloads a verified journal copy with its
receipt after explicit user action. The selected database path is shown, and
the browser controls where the ZIP is saved. The archive is not encrypted.
Restore rehearsal checks and copies a selected backup to a temporary new path,
then removes that copy; it never overwrites or installs the active journal.
This does not prove that a backup is newest or durable, so keep protected
off-device copies and supporting setup evidence separately.
An active account's cash-check link starts a reviewed observation draft only if
the current recording session still contains that account; it never updates cash.
Each active recorded account also has an **Update** link. Its guided review
brings together recorded cash, the last cash check and discrepancy, held
quantities and exact price dates. It walks through source-supported
transactions, a cash comparison and price checks without requiring monitoring
configuration. Each action opens the existing revision-bound recording flow;
account and price targets are rechecked against the current recording session.
An unavailable target never selects another account or holding. A verified
save returns to the account review with reloaded evidence, not an "up to date"
certification. No provider read or journal import is started by this flow.

An eligible eToro read can be reviewed beside an explicitly selected and
confirmed journal account. Temporary one-to-one holding pairings are local to
the view, reset when either source changes, and exclude instrument IDs repeated
across direct/copy groups. No provider rows, currencies or cash figures are
converted, reconciled, imported or saved by this view. The ordinary account
update link still requires independent source evidence and reviewed recording.

Recording previews validate both complete report shapes and show valuation,
return and drawdown before and after the proposed record, together with new
evidence warnings. Losing a previously available value or performance metric
requires explicit acknowledgement before a new confirmation. Uncertain-save
retries retain the same existing confirmation reference. Deposits/withdrawals
also show a preflight reminder to record source-supported flow-time prices
across the whole portfolio first. Cash-check previews show their replayed
observed balance and difference; a match is not independent verification.
Reported savings terms, where recorded, are shown with their effective and
source dates and any maturity or notice detail. They are not verified current
rates, eligibility checks or accrued interest.
Snapshot comparisons use your configured account and investment names. Enter
source dates in your computer's local timezone; the preview shows the converted
UTC times while keeping the snapshot, source-availability and match-evidence
clocks separate.

The server has no user login and must remain loopback-only: host/origin and
CSRF defenses are not authentication or protection against another local user.
Do not expose or tunnel it. Intelligence analysis controls require their own
explicit enablement, but no login; see [intelligence](intelligence.md).

## Contracts and validation

The investment response retains its technical wire `schema_version=2`.
Routes may retain stable technical API version prefixes; these are not product
editions. Unsupported journals, incomplete value, stale/conflicting evidence,
unconfigured monitoring and unavailable intelligence must remain explicit.
No display, acknowledgement, allocation illustration or AI brief grants trading
authority. Private account evidence must never enter hosted AI.

```powershell
python scripts/offline_tests.py --pattern test_investment_dashboard.py
python scripts/offline_tests.py --pattern test_dashboard_planning.py
python scripts/offline_tests.py --pattern test_dashboard_api.py
npm --prefix frontend test
```

Tests use temporary stores and in-process transport. Browser/build/packaging
checks do not establish provider behavior, custody or profitability.
See [development](development.md), [monitoring](monitoring.md) and
[journal lifecycle](investment-journal.md).
