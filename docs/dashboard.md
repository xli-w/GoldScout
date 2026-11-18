# Local portfolio dashboard

The dashboard presents the GBP investment journal, optional local monitoring
and advisory intelligence. It has no trading runtime or account/order mutation
routes. Investment reads are nonmutating and never fall back to fabricated data.

## Start and build

```powershell
python -m pip install -r requirements-dashboard.txt
npm --prefix frontend ci
npm --prefix frontend run build
python -m dashboard --demo
```

Dependency installation needs network and separate approval in restricted
development. A packaged wheel includes built assets; do not assume stale assets
represent current source. Use `python -m dashboard --help` for current options.

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

Investment and advisory pages provide section shortcuts for their longer
evidence views. Recording previews show a human-readable summary with the exact
event retained in expandable details. Monitoring observation entry uses
separate holding and explicit mapping fields; comparison results are displayed
separately from append-only review-inbox actions. Neither workflow status nor a
reconciliation match establishes current custody or approves a trade.

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
