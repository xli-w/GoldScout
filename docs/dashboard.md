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
evidence before creation. Explicit `--investment-database <path>` reads only an
existing selected journal. Older formats fail explicitly with no migration.
Synthetic `--demo` uses temporary evidence, not a broker or real workspace.

The default is read-only. Later manual recording needs
`--investment-recording`, same-origin/CSRF checks, bound previews and exact
revision confirmation. Setup does not grant recording authority.
Monitoring cache/workflow writes have separate gates; a page refresh never
collects provider data or updates accounting.

The portfolio view leads with recorded balances and holdings; optional checks,
advisory context and detailed evidence remain available without being required
for basic use. Recording previews show a human-readable summary with the exact
event retained in expandable details. Monitoring observation entry uses
separate holding and explicit mapping fields; comparison results are displayed
separately from append-only review-inbox actions. Neither workflow status nor a
reconciliation match establishes current custody or approves a trade.
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
python scripts/offline_tests.py --pattern test_dashboard_api.py
npm --prefix frontend test
```

Tests use temporary stores and in-process transport. Browser/build/packaging
checks do not establish provider behavior, custody or profitability.
See [development](development.md), [monitoring](monitoring.md) and
[journal lifecycle](investment-journal.md).
