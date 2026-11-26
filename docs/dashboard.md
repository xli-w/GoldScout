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

On Windows, `Start-GoldScout.cmd` runs this command from the checkout using its
default `local-investment/` workspace. It does not install dependencies or open
a browser; follow the one-time setup above first, then open the printed local
URL. Close the console window to stop the dashboard.

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
Viewing does not create that file. Saving requests a revision-bound preview and
commits it in one action; an uncertain save can be retried using the same request.
A companion lock file serializes writes from concurrent dashboard instances.
The profile stores purpose, target/access dates, liquidity
needs, comfort with losses, user-entered current/monthly amounts, and optional
whole-account associations as local plaintext. An account can be assigned to
only one goal and associations do not move or reserve money. A linked amount
replaces, rather than adds to, the manual amount and is shown only when the
complete recorded portfolio value reconciles to its account records. The
portfolio identity and journal revision are checked at preview and confirmation;
values are dated records, not live provider balances. Simple illustrations
assume no growth, interest, fees, tax or withdrawals and are not forecasts or
recommendations. Existing v1 and v2 goal profiles are read with new fields
defaulted to unknown or unlinked and upgraded only after a save. It is not part of journal
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

### Optional managed intelligence

**Intelligence** offers explicit initialization/upgrade, source selection,
optional local/hosted model configuration and persistent enablement. Settings
links to this same setup; it is not a second service. The selected workspace
determines a separate sibling `.goldscout-intelligence/<workspace-name>/`
directory containing `settings.json`, `intelligence.sqlite3` and local relevance
mappings. Merely opening the dashboard/page does not create these files.

Once enabled, collection runs for the dashboard's lifetime and restarts with it.
There is no background OS service while the dashboard is closed. Source refresh
and briefing refresh have separate durable requests. The existing ownership
lock coordinates CLI/dashboard use; queued settings, applied controls and actual
worker/source state remain distinct. Disabling persists a control even if
another owner must apply it.

For the same saved selection in the standalone CLI, stop the dashboard owner and
use `python -m market_intelligence run --managed-config <settings.json>
--allow-network`. Saved enablement/source/provider restrictions still apply;
command-line overrides cannot silently expand them. A `status` read makes no
provider calls; managed `once` processes controls only.

The page leads with retained public quotations, provenance and constrained
interpretation templates, then locally explained exposure matches, upcoming
events and source diagnostics. Private mapping/journal evidence never enters
hosted synthesis. Missing valuation prevents exposure weights, not qualitative
context. Source completeness, dated adapters, unsupported BLS outcomes and
unvalidated external compatibility remain explicit in the
[intelligence guide](intelligence.md); this is a partial foundation, not complete
live coverage.

The portfolio **Overview** shows the recorded value and its date, portfolio-wide
evidence needing review, prominent guided account review, account-specific next
actions and up to three recent activity entries. Missing portfolio valuation is
kept separate from an account's own action. Refresh reloads the locally recorded
portfolio and does not fetch provider balances; if the snapshot is unavailable,
the page offers a retry. Historical performance limitations are summarized
with their details in **History and insights**, alongside the value comparison,
money-added and gain figures, holdings, performance, cash evidence and journal
records. Saved goals appear only when configured. **Tools** contains optional
checks, public context, backup and the separate eToro view. Existing portfolio
section bookmarks open their corresponding view. Opening Tools does not collect
provider data or start a backup. Backup controls load only when opened; closing
them or switching between these portfolio views retains their selected file and
result. A failed snapshot refresh hides the controls until the same recorded
portfolio is available again; leaving or reloading the page, or changing
portfolio, clears them. Recording previews show a human-readable summary with
the exact event retained in expandable details. Monitoring observation entry
uses separate holding and explicit mapping fields; comparison results are
displayed separately from append-only review-inbox actions. Neither workflow
status nor a reconciliation match establishes current custody or approves a
trade.
Each account has one status and next action, using an account-specific
weekly-to-yearly cadence saved in this browser or the configured workspace
default; the source is stated in the account's review reminders. Savings-term rechecks have
their own independent cadence. An unknown rate, future-effective terms or an
old observation remains a recheck, even if the source became available
recently. Unrecorded notice and maturity details are shown without assuming
they apply. If workspace settings cannot be loaded, no
replacement cadence is assumed: age-based freshness stays unknown until an
interval is chosen, while account-specific choices remain available. The
status shows shared portfolio valuation dates, account cash and holding
evidence; it does not invent an account-specific value history or provider
agreement. A complete value remains complete only for its recorded date, and
an older complete value can be overdue. Current missing values and recorded
cash differences take priority over choosing a cadence; a later matching cash
check does not resolve an earlier difference. Historical performance
limitations are presented separately from current attention, without hiding
correction-related cash replay warnings. Account cards show their next action
first; open an account for its evidence, actions and reminder settings.
**Guided account review** keeps a local checklist across active accounts. It
starts with the first account and resumes after a browser restart when the
journal revision is unchanged. Supported account updates confirmed in that
review preserve unaffected checks; other or outside journal changes make the
saved checklist stale. Starting over leaves the saved
comparison point untouched. Completion counts accounts reviewed, not issues resolved:
remaining missing values, cash differences, overdue reminders and checks
affected by corrections stay visible in the completion summary and account actions. This progress is
browser-local, is not included in journal backups, and does not change the
journal. Reminder changes and account checks require browser Web Locks so
concurrent tabs cannot overwrite one another's settings; unsupported browsers
show an error instead of saving. Comparison-point saves use the same tab
coordination, check the current journal revision and refuse to replace a point
that changed unseen. If a point cannot be saved, retry reading it before
attempting to finish again; unreadable saved data is not repaired or
overwritten.
History and insights also shows the largest direct holding exposures against complete
recorded value. It does not look through funds or assess suitability or risk.
Explicit account/platform charges can be recorded as fees and reduce recorded
cash without being treated as an external withdrawal. The fee total still omits
many possible costs, including fund charges, taxes, FX, spread and slippage.
Setup and recording forms also save incomplete drafts in the same browser and
workspace context. Only form fields are restored; a full preview is not resumed.
The separate uncertain-save safeguard
retains an exact pending confirmation reference so only that same save can be
retried; it blocks new entries until resolved. Recovery re-entry is not saved.
If browser storage is unavailable or malformed, the page surfaces that state
instead of claiming the draft was saved. Drafts are local plaintext browser
data and are not part of journal backups.
Savings-term reminders use only dated entered facts and direct users to verify
rates, access, notice and maturity with the provider; they do not compare
obtainable returns. Recorded fill fees are not a full cost assessment. Cash
observations do not update journal cash. Recent recorded activity lists dated
flows, income, fills and corrections, not all changes or a complete attribution
of returns. The overview preview can omit entries sharing the same time;
the fuller activity list is in History and insights.
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
revision, not a journal snapshot. Later evidence effective at that same time
can change the replayed starting value; compare retained earlier reports. It
does not establish current balances, complete activity, external fees, or
custody.
Saving a comparison point replaces the previous browser-local point. Marking an
account checked saves a separate reminder date and does not save that comparison
point or resolve a discrepancy. Neither is included in a journal backup.
Changes to a saved comparison point are reflected in other tabs on the same
browser origin. A save in progress remains visible if you leave and return to
the portfolio; a failed save is reported and can be retried. If the point
cannot be read, comparison is unavailable rather than silently choosing another
date; the saved data is not changed.
For recorded portfolios, a selected comparison date remains selected across
refreshes while still available; otherwise the comparison asks for a new date.
Demo dates move with its synthetic clock, so its selection follows the same
relative history position instead.

The Tools recovery section downloads a verified journal copy with its
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
evidence warnings. The impact remains visible when a value or performance
metric becomes unavailable. Uncertain-save retries retain the same existing
confirmation reference. Deposits/withdrawals
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
npm --prefix frontend run test:unit
npm --prefix frontend run test:browser
```

The Node tests require Node 24. Browser tests require a current packaged
frontend and task-specific approval; they start their own synthetic
intelligence/demo server on an OS-selected loopback port and do not reuse an
existing dashboard. Python tests use temporary stores and in-process transport.
Browser/build/packaging checks do not establish provider behavior, custody or
profitability.
See [development](development.md), [monitoring](monitoring.md) and
[journal lifecycle](investment-journal.md).
