# GoldScout Local Dashboard

## Long-term investment view

The default dashboard and its account views are read-only. The separate
[investment path](investment.md) adds `/investment` and
`/investment-guide`, showing GBP contributions and recorded valuations. V1 adds
sleeve drift; v2 shows since-baseline growth, management roles, dated
marks and unavailable lifetime gain, with no allocation policy or freshness
threshold. It can show evidence-dated savings terms and statement cash
observations; neither accrues interest nor adjusts recorded balances. An
explicitly enabled, password-authenticated v2 recording mode can append
operator-entered events after a revision-bound preview and confirmation. It
does not contact banks or brokers, verify entered evidence, approve trades or
move money. The legacy USD trading screens below remain available and unchanged
in authority. Review status and acknowledgement remain available only in the
offline investment CLI. Proposals are blocked for both store versions until
role-aware policy, weights, products and loss/transfer rules are approved and
implemented.

Use `python -m dashboard --demo` for synthetic investment and legacy previews.
The root opens the GBP investment view using synthetic v2 opening evidence;
`/legacy` opens the separate trading demo. The investment demo's role labels
are illustrative accounting evidence, not targets or recommendations, and it
does not imply investment performance. The root choice does not change the
legacy account routes or the configured source.
Use `python -m dashboard --investment-database .\investment.sqlite3` only for an
explicitly initialised, operator-selected investment store. It is mutually
exclusive with the legacy `--database` and `--demo` sources; recorded investment
mode never substitutes synthetic legacy accounts. No store is created or repaired
by reporting. The frontend build binds the legacy, investment v1/v2 and awareness response schemas.
To enable manual recording for an existing v2 store, add
`--investment-recording`; the operator enters a 12-character-minimum password
at the terminal, not in command-line arguments. The server remains bound to
loopback. Sessions and pending previews are process-local and expire; each
change must be reviewed and confirmed, and stale journal revisions are refused.
See the [investment guide](investment.md#version-2-opening-evidence) for
supported records and correction limits. Without this flag, all dashboard
investment routes remain read-only.

For [local portfolio awareness](portfolio-automation.md), additionally select an
existing `--intelligence-database` and `--awareness-profile` JSON file. The
investment page shows explicitly mapped calendar alerts, dated existing briefs
and configured review reminders. Its local-read minute polling is opt-in; the
page starts no collector/model, sends no account data to AI and cannot trade.
Without that profile awareness is disabled. Read-only sync remains discovery,
not an active broker/bank connection.

Recorded investment mode opens the investment page at the root and hides legacy
account/record navigation; a direct legacy URL explains that those records are
not selected. In demo mode, `/legacy` preserves the legacy trading overview and
account pages. The investment page presents valuation, roles, and performance
before progressively disclosed account evidence and capability limits. The
separate `/investment/record` page explains its opt-in gate and retains
authentication, revision-bound previews, and explicit confirmation. Workspace
selection identifies the configured source, not its availability or
verification; an unknown workspace is not replaced by a demo.

The investment page shows the age of the portfolio valuation and, for v2, each
holding mark using the browser clock. These are elapsed times, not a freshness
classification or a claim that the valuation is current. Missing or future
timestamps have unavailable ages. The v2 cash evidence overview lists every
account, including accounts without observations, the latest observed difference,
and the count plus earliest/latest differing observations across recorded
history. Later matching observations do not resolve prior differences. The
separate history table remains limited to the latest 100; these displays do not
reconcile balances, verify providers or change the journal.

This is an offline measurement/planning release, not full multi-currency research
or a human-approved broker integration. See the investment guide's capability
limits before interpreting any return or proposal.

The dashboard includes a local FastAPI service and React overview, positions,
health, activity, history, and intelligence screens. The dashboard cannot start, stop, pause, or change trading;
it reads no broker credentials and makes no broker requests. Optional analysis-only
intelligence controls are described in the [intelligence guide](intelligence.md). Step 3 adds optional
non-authoritative scheduler telemetry. Live prices and unrealized P&L remain
unavailable until a separately timestamped price observation source exists.
The interface uses a persistent dark theme and a left navigation sidebar; on
small screens the sidebar becomes a compact, vertically scrollable rail.
The footer links to a trading guide covering default strategies, risk rules, and
important limitations; it remains available when account data is unavailable.
Its defaults are descriptive and may differ from the operator's settings.

## Setup and Demo

Use the selected Python 3.11 environment (retain Python 3.10 compatibility) and a
current Node.js LTS installation. From the project root:

```powershell
python -m pip install -r requirements-dashboard-dev.txt
npm --prefix frontend ci
npm --prefix frontend run build
python -m pip install --no-deps --no-build-isolation -e .
trader-dashboard --demo
```

After setup, `trader-dashboard --demo` is the single launch command. The equivalent
source-checkout command is `python -m dashboard --demo`. Open
`http://127.0.0.1:8765/`. The demo uses
synthetic fixtures only and labels each response and screen as synthetic. It does
not create a ledger. `--scenario partial_exit`, `--scenario stale_telemetry`,
`--scenario stopped_empty_account`, and `--scenario ledger_missing` exercise the
screens. Focused fixtures lacking overview/positions return unavailable rather
than inventing a successful response. To use the API without a frontend build,
start with `--api-only`; endpoints include `/api/v1/overview`, `/api/v1/positions`,
and `/api/v1/health`.

The GoldScout name does not change the existing `trader-dashboard` command,
`trader-local-dashboard` distribution, environment variables, or storage paths.

If the port is occupied, use `--port 8766` and the corresponding browser URL. The
dashboard binds only to `127.0.0.1`, disables proxy-header trust, and rejects
foreign hosts/origins. Account and trading routes reject mutations. Recorded-mode
intelligence controls require explicit enablement and local authentication; the
synthetic demo uses a temporary workspace and no real credential. It is not a
remotely hosted service; do not tunnel or expose it.

For separate development, start `trader-dashboard --demo --api-only`, then run
`npm --prefix frontend run dev` in another terminal. Vite uses port 5173 and
proxies API requests to 8765. Adjust the development proxy explicitly if the API
uses another port. Both processes are independent of trading.

## Recorded Data

An operator may explicitly select an existing local ledger:

```powershell
python -m dashboard --database .\bot_ledger.sqlite3
```

The installed equivalent is `trader-dashboard --database <existing-ledger-path>`.
Relative paths resolve against the operator's working directory. The command
does not start trading. `--demo` and `--database` are mutually exclusive and one
is mandatory; `--telemetry` is recorded-mode only, and nondefault `--scenario`
is demo-only. No implicit account selection, bot startup, ledger creation,
account adoption, or repair is performed.

## Packaging and Launch

The frontend build now creates `dashboard-build.json` and copies the generated
web bundle into the ignored `dashboard/static/` directory. Its manifest records
the response-schema SHA-256 and hashes of `index.html` and static assets.
Production CLI startup validates those hashes before importing the optional web
runtime or starting a server; direct app-factory production startup is guarded
as well. Manifest membership must exactly match the served files, and symlinks
or unlisted assets are refused. A missing, incomplete, changed, or schema-mismatched
bundle fails with rebuild guidance instead of serving a blank page. Checksums
detect accidental mismatches, not coordinated tampering or a trusted malicious
build. A build does not contact the broker or change trading configuration.

`--api-only` explicitly skips production asset loading for API/Vite development;
it does not serve even an existing frontend bundle. CLI `--help` needs only the
standard library. Missing dashboard dependencies produce installation guidance.
Dependencies and build tools live in the dashboard requirements files, separate
from the trading snapshot. Node/pip package access must use the operator's locally
configured proxy where required; credentials must not be written to source files
or passed through chat. No launcher runs automatic installs, builds, or updates.

To create an installable distribution, build the frontend first, then run:

```powershell
if (Test-Path .\build\lib\dashboard\static) {
    Remove-Item -LiteralPath .\build\lib\dashboard\static -Recurse -Force
}
python -m pip wheel --no-deps --no-build-isolation --wheel-dir dist .
python -m pip install --no-deps --force-reinstall .\dist\trader_local_dashboard-0.1.0-py3-none-any.whl
trader-dashboard --demo
```

Install `requirements-dashboard.txt` into a clean runtime environment before
using `--no-deps`; the build commands require the pinned development build tools.
The wheel contains the `dashboard`, `investment` and `market_intelligence` Python packages,
`proxy_config`, their packaged JSON, the generated frontend, and distribution
metadata. Investment reporting is offline; intelligence dependencies are optional. It excludes `bot`, `config`,
tests, credentials, ledgers, snapshots, reports, and logs. Console launch works
outside the checkout; there is no packaged trading entry point. Rebuild and
reinstall the wheel after changing frontend/schema sources. Editable installs
use the source package's generated assets and need a frontend rebuild after
frontend/schema edits.

The cleanup above targets only setuptools' generated frontend staging directory,
not source assets or account data. Reusing old staging after a frontend rebuild
can package obsolete hashed assets; exact manifest verification correctly refuses
that wheel. Verify the installed bundle rather than relaxing the manifest check.

Windows source setup uses the selected Python 3.11 environment; Python 3.10 is
retained in package metadata but not newly certified here. Production and
development servers bind loopback only. To stop the dashboard, press Ctrl+C;
this has no effect on a separately running trading process or broker positions.
If a port is occupied, the launch fails without stopping the other process;
choose another port with `--port`. Do not expose or tunnel this local service.

Historical Step-5 validation on Python 3.11.9 (before intelligence was packaged): the frontend build, dashboard-only wheel
build/install, isolated installed-package help, installed schema/asset checks,
and all eight focused launch tests passed. Wheel inspection found 66 members,
all dashboard files or distribution metadata; no trading/operational files were
included. The installed `trader-dashboard --demo` command served all five pages
with synthetic labels and no page errors in the integrated browser. Final full
offline suite: 213 tests, 212 passed, with only the existing broken-skill-link
customization check failing. Independent final source review confirmed packaging
findings closed without performing its own runtime validation. The documented
limitations recorded at that stage are historical; the subsequent repair pass
below resolves the runner and dependency advisories. Packaging does not certify
live trading readiness.

The service never imports credential-loading `config` or instantiates `Ledger`.
It opens the selected database with SQLite `mode=ro` and `query_only`, verifies
schema v1 and the latest snapshot/journal revision inside one short read
transaction, then projects allowlisted fields. It never creates a missing
database, migrates schema, submits orders, writes accounting, or acquires the
bot's exclusive process lock. Missing storage is HTTP 503; invalid/revision-
inconsistent storage is HTTP 409, not an empty account.

Read-only here means **no logical database/accounting writes**, not filesystem
immutability. SQLite WAL readers can create or update WAL shared-memory
coordination files. Keep the database and its sidecars on a local filesystem
with appropriate operator permissions. Do not delete sidecars, copy only an
active main database, use `immutable=1` on a changing ledger, or relax permissions
to bypass an unavailable read. Temporary WAL tests verify that an active writer's
uncommitted state is not published. Restricted-filesystem and operator-ledger
integration remain unverified. No operational ledger was accessed in development.

Projection validation is not certification of configured trading ownership or
broker reality; restart validation and trading decisions remain the bot's job.
Recorded `protection_verified` flags lack observation timestamps, so protection
is displayed as unknown (or explicitly unverified), never current verification.
Existing entry/exit/accounting/daily-halt blocks remain visible. A client-ID-only
pending exit is retained as `exit_pending` even without a broker order ID.
Without valid optional telemetry, runtime health remains unknown. Unrealized P&L
remains unavailable. Telemetry never supplies or changes accounting values.

## Scheduler Health Feed

Installing the dashboard Python dependencies also installs `psutil`. On a future
operator-approved bot start, the scheduler creates a new run ID and binds it to
the ledger's database ID, environment, process ID, and process creation time.
The optional publisher writes `bot_telemetry.json` beside the configured state
snapshot. If ledger/state directories differ, select the path explicitly using
`python -m dashboard --database ... --telemetry ...`. No bot was started during
implementation or validation.

The scheduler submits only allowlisted in-memory facts to a latest-only queue of
size one. A daemon writer creates a bounded (65,536-byte maximum) temporary file
and atomically replaces the health snapshot. It makes no broker requests or
ledger writes. Disk failures and bad optional observations are contained; no
writer join delays monitoring or shutdown. Shutdown/fault publication is best
effort, not a guaranteed final record. Atomic snapshots are not a crash-recovery
store or protection guarantee. These files are ignored by Git.

Monitor completion advances only after a successful monitor task. Per-position
protection samples use monitor **start** time as a conservative lower bound on
verification freshness, not completion time: a slow monitor cannot make an early
stop observation look newly verified. The existing monitor performs protection
checks; telemetry adds no new broker requests. Samples must match durable
position identity, residual quantity, and recorded broker stop, remain fresh,
and not override an unverified or pending-exit position.

The reader accepts only bounded payloads matching ledger/environment and a live
process instance. Process checks can fail under restricted permissions, causing
unknown health rather than assumed liveness. Missing, corrupt, future-dated,
stale, or mismatched telemetry does not invalidate otherwise valid accounting.
Fresh monitor failures degrade runtime; stale faults remain historical warnings.
Durable accounting/daily-halt blocks take precedence over telemetry flags.
Fresh reported runtime is observational, not proof that the next operation will
succeed. Fresh, process-verified telemetry reports `monitor_not_ready` as an
entry block when no successful monitor pass started within the configured
interval; stale telemetry cannot establish that block. The frontend drops a
cached monitor-only block when that telemetry ages out, while retaining known
ledger-backed restrictions.
Entry permission remains unknown unless actual restrictions are known; telemetry
is never authorization to place an order.

Health reports monitor completion, persistence/accounting/daily-loss flags,
unmanaged symbols, task failures, and warnings. Activity checkpoints come from
the existing committed ledger checkpoint, not a newly invented publication time.
Activity sync remains a bounded overlap, not full settled-account coverage.

## Contracts and Frontend

- The shared [schema](../dashboard/response.schema.json) and
  [synthetic fixtures](../dashboard/fixtures.json) include `source_mode`,
  `database_id`, and per-position `exit_pending` added for step 2.
- Each request reads a consistent ledger snapshot. The frontend pairs overview
  and positions using both database identity and journal revision; a changed
  identity/revision rejects the pair instead of mixing snapshots.
- The frontend uses React Router, TanStack Query, local IBM Plex fonts, Lucide
  icons, and Decimal.js formatting/sorting. Search is stored in the URL.
- Queries refresh every five seconds, individual fetches time out after ten
  seconds, and observations age locally once per second. Failed refreshes hide
  old balances rather than leave them presented as current.
- API responses are validated in Python and in the browser. The frontend build
  generates a standalone Ajv validator, avoiding dynamic compilation under the
  production CSP. Do not add `unsafe-eval` to make validation work.
- Production serves the Vite build and API from one origin with `no-store`,
  restrictive CSP, no permissive CORS, and sanitized error bodies.
- Collection history/orders/events endpoints and cursor rules in the
  [contract decision](decisions/0001-dashboard-contracts.md) are future work;
  overview/positions/health/activity/performance and non-sensitive mode metadata are implemented now.

## Activity and History

Step 4 adds `/activity` (journal and orders) and `/history` (daily projections,
original exit slices, separate evidence-linked corrections). Date filters are
inclusive New York accounting dates; symbol filtering applies to exit slices
and their corrections, not portfolio-wide daily totals. Filters and activity
tabs are retained in the URL. Legacy dates, quantities, fee provenance, and
timestamps without recorded timezones stay unknown rather than reconstructed.
History groups portfolio totals separately from original exits and their
evidence-linked corrections, and displays the symbol filter's scope. Accounting
dates are identified as New York dates; displayed timestamps include the year
and use UTC. Section links and pagination controls above and below the records
make long histories easier to navigate; each control advances the shared totals,
exit, and correction collections.

Position protection status shows its observation time when available. The hard
stop, trailing stop, and broker-stop fields are separate persisted values; none
by itself confirms current broker protection.

The read-only endpoints `/api/v1/events`, `/api/v1/orders`, and
`/api/v1/performance` default to 100 rows, reject limits outside 1..1000, and
return continuation cursors. Collection queries run inside the same validated
snapshot transaction, using short SQLite read-only connections. No migrations,
accounting mutations, broker requests, or operational maintenance are added.
Event summaries project event kinds rather than forwarding raw payloads.
Signal reasons not journalled by the bot are not invented by the dashboard.

Recorded cursors bind resource, filters, page size, database ID, state revision,
and the entire event-journal head. New observations or corrections invalidate
older cursors with HTTP 409 `snapshot_changed`; reload starts a new snapshot,
without concatenating histories. Performance cursors track daily, exit, and
correction collections independently so unequal counts do not discard rows.
Client caches also bind database identity. These unsigned cursors are pagination
state, not authorization or privileged access; requests never select arbitrary
files or SQL. Synthetic demo collections support continuation as well.

Daily totals come directly from stored projections and can already include
corrections. The UI never adds correction amounts again. An uncorrected unknown-
price exit makes that day's realized result unavailable. Original exits remain
unchanged after corrections; corrected prices and total allocated fees appear
in a separate table. Estimated fees stay labelled. Negative quantities/fees,
nonfinite values, and impossible cumulative fills are refused; legitimate
negative P&L remains supported. Decimal accounting strings are retained or
serialized to fixed-point form without claiming additional historical precision.

The accessible chart shows at most the latest 60 loaded daily portfolio records,
with recorded calendar spacing and unknown P&L left as gaps. It is not a live
equity curve or a performance certification. Tables provide exact values; the
chart uses bounded normalized display coordinates. History date and symbol
filters are grouped, show their active count, and can be cleared together without
dropping unrelated URL state. Responsive date controls,
scrollable tables, loading/empty/error states, pagination, and snapshot-specific
read timestamps are included. On narrow screens the navigation is horizontally
scrollable, tables show a scroll cue, and account runtime status remains visible.
The Activity view uses a labelled button group with an exposed pressed state;
scrollable tables include assistive-technology instructions, and pagination
status changes are announced. No maintenance or trading buttons were added.

The sidebar separates account views, historical records, and advisory
intelligence. Activity, History, and Intelligence pages explain their scope.
Intelligence keeps the last successful source label visible during refresh and
reports a failed source as unknown. Brief availability and expiry are shown in
UTC; claims link to cited news only when that evidence is in the current
snapshot, and otherwise say that the citation is not present there. Source
observation status is displayed with the news evidence.

Step-4 validation: production build and TypeScript checks passed; all 11 focused
history regressions passed. The final full offline suite ran 205 tests, with 204
passing and only the existing broken-skill-link customization check failing.
Independent read-only review findings were repaired and confirmed closed in
source. Integrated browser checks confirmed five demo daily rows/chart bars,
unknown original prices with separate corrections, symbol filtering without
changing portfolio totals, the order/journal switcher, and complete limit-one pagination
across five days, two exits, and one correction. The embedded browser sometimes
overrides requested viewport sizes, limiting exact screenshot-size evidence;
narrow-layout overflow checks and actual rendered captures were inspected.
The runner and dependency advisories recorded at this stage were resolved by the
subsequent repair pass below. No operational ledger or authenticated broker
validation was performed.

## Validation

The commands below are current starting points. The step-specific counts and
limitations that follow are historical records, not the status of the current
checkout; rerun the relevant checks before making current validation claims.

```powershell
python scripts/offline_tests.py --pattern "test_dashboard*.py"
python scripts/offline_tests.py --pattern test_telemetry.py
python scripts/offline_tests.py
node --check frontend/scripts/generate-validator.mjs
npm --prefix frontend run build
npm --prefix frontend exec playwright install chromium
npm --prefix frontend test
```

For installed Edge on Windows, set `$env:DASHBOARD_BROWSER_CHANNEL = 'msedge'`
before the browser command. Native ESM browser specs/config avoid TypeScript
test-loader requirements; application TypeScript remains checked by the build.

The browser suite requires a running synthetic demo service with a built frontend;
it covers desktop/mobile layouts, search, unavailable storage, and failed refresh.
Python API tests use in-memory ASGI scheduling with a threading-event wakeup,
not sockets, so the existing offline network guard remains unchanged. Without
dashboard dependencies, optional API/schema tests are explicitly skipped.

At an earlier dashboard milestone on Python 3.11.9: 31 focused dashboard tests
passed. The full isolated suite ran 174 tests: 173 passed; the customization test failed because
two skill links point to the wrong safety-invariants path. Generator syntax and
editor diagnostics passed. Independent source review identified and prompted
fixes for CSP compilation, WAL documentation, identity pairing, source labels,
freshness, and client-ID-only exits.

Step 3 resolved package installation using an operator-entered session proxy;
credentials were not written to files. The npm lockfile, TypeScript check, and
production build now succeed. Mixed CommonJS helpers in the generated validator
are transformed by Vite; actual production browser startup was checked under the
unchanged CSP and no `unsafe-eval` was added.

Integrated Playwright checks verified desktop (1440px) and mobile (390px)
rendering, local font loading, no page-level horizontal overflow, positions
search/URL clearing, health/fault displays, missing-storage errors, old-holdings
removal after a failed refresh, and Running-to-Unknown expiry after 90 seconds.
Desktop and mobile screenshots were inspected. At that stage the Playwright CLI
stalled before test discovery in this machine's Node environment, including
native ESM configuration; those attempts were stopped. Its committed regression
suite was not claimed to have passed at that stage. Synthetic route interception uses
static payloads rather than secondary proxy-dependent request fetching.

Focused telemetry and in-process API tests cover blocked/failing writers,
quarantine, monitor failures, stale/mismatched processes, binding, protection
identity/quantity/stop/freshness mismatches, invalid projected task names, and
accounting continuity. Exact final counts are reported in the implementation
handoff. Final Python 3.11.9 checks: 18 telemetry tests and 21 API tests passed;
the full isolated suite ran 193 tests, with 192 passing and only the existing
broken-skill-link customization check failing. Independent final source review
found no remaining actionable safety defect, without independently executing
the reported checks. The broken-skill-link customization failure remained outside
that milestone. npm installation reported seven dependency advisories (one moderate,
six high); no forced upgrade was applied, and dependency security remediation
was outstanding at that stage. No authenticated broker or paper-account validation occurred.

## Review and Repair Pass

The subsequent review repaired these dashboard issues without changing trading:

- Price and accounting-table formatting retains sub-cent decimal values;
  overview totals intentionally remain rounded to cents. A neutral zero marker
  replaces a misleading positive bar for zero daily P&L.
- The primary Refresh action refreshes the account snapshot and resets active
  collection snapshots, so Activity/History data is actually reloaded.
- Snapshot identity comparison is shared across positions and health and now
  checks environment consistently. Request failures/timeouts have actionable,
  sanitized messages while caller cancellation remains cancellation.
- Stale false health flags are unknown; stale true flags remain explicitly
  last-reported facts. Nullable legacy exit quantities match the frontend type.
- Cursor parsing is shared between demo and recorded collections. Invalid or
  boolean page sizes and offsets beyond SQLite's integer range are refused as
  invalid requests, not misreported as ledger corruption.

Audited dependency versions are Vite 6.4.6, Ajv 8.20.0, React Router 7.18.5,
and Playwright 1.65.0. The install audit reports zero known vulnerabilities;
this is an advisory snapshot, not a security certification. Router retains the
existing BrowserRouter/NavLink workflow. Use Node 20+ or a current Node LTS.
The production build and all 18 standalone browser regressions passed using
installed Edge across desktop and mobile projects, including exact prices and
the primary history-refresh regression. No browser download was required.

Final repair validation ran 225 offline tests: 224 passed, with only the existing
broken-skill-link customization failure. All 14 history/cursor regressions passed;
the independent source review confirmed its final cursor finding closed. The
repaired wheel was rebuilt and reinstalled without changing trading dependencies.
Validation previews and stalled prompts were stopped afterward. Launch the updated
local demo with `trader-dashboard --demo` when needed. No authenticated broker
or operational-ledger validation was performed.