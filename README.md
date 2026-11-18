# GoldScout

GoldScout is a local GBP portfolio evidence journal and dashboard for selected
savings, a first-home LISA and longer-term investments. It records
operator-supplied evidence; it does not verify custody, choose investments,
move money or execute trades.

## Set up and start on Windows

GoldScout is currently distributed as a source checkout, not a desktop installer.
From the repository folder in PowerShell, install the dashboard dependencies and
build the frontend once:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dashboard.txt
npm --prefix frontend ci
npm --prefix frontend run build
```

Start GoldScout from the same folder whenever you want to use it:

```powershell
.\.venv\Scripts\python.exe -m dashboard
```

In a source checkout, records are stored in the ignored `local-investment/`
folder. The first run guides you through choosing accounts and entering starting
figures. Review the preview before confirming; nothing is saved before that
confirmation. Cash-only setup skips investment details. If an investment
account has holdings, include them rather than omitting them.

Existing valid journals open read-only. To record changes, stop the dashboard
with Ctrl+C and restart with `--investment-recording`, keeping the same folder:

```powershell
.\.venv\Scripts\python.exe -m dashboard --investment-recording
```

Installed use requires selecting the same private folder with
`--workspace <directory>` on each start. The dashboard has no sign-in and must
stay on this computer; keep the records and backups private.

See [setup and operations](docs/investment.md) and [dashboard development](docs/dashboard.md).

## Offline preview

```powershell
.\.venv\Scripts\python.exe -m investment demo
.\.venv\Scripts\python.exe -m dashboard --demo
```

These use synthetic evidence, not account access.

## Boundaries

- One opening-evidence journal format, wire `schema_version=2`. Older journals
  are explicitly rejected, with no migration or compatibility mode.
- Exact GBP accounting, replay, valuation, cash observations and
  core/dynamic/unassigned attribution. Attribution is not an approved risk budget.
- Optional [monitoring](docs/monitoring.md), a separate observation cache and
  review inbox. Provider collection has separate explicit approval gates and
  is live-unvalidated.
- Optional [intelligence](docs/intelligence.md) supplies public advisory context;
  private account evidence never enters hosted AI. It has no trading authority.
- Investment proposals remain blocked. There is no execution runtime.
- [`research_data/`](research_data/) snapshots local provenance-checked inputs
  and audits declared readiness. The separate synthetic portfolio simulator is
  engineering-only, not a product-performance result. See the
  [research gates and preserved history](docs/backtesting-protocol.md).

## Project and guides

- [`investment/`](investment/) — accounting, replay, local monitoring and CLI.
- [`dashboard/`](dashboard/), [`frontend/`](frontend/) — local evidence views.
- [`market_intelligence/`](market_intelligence/) — separate optional advisory service.
- [`research_data/`](research_data/) — offline ingestion, readiness and pure fill checks.
- [Journal backup, restore and capacity](docs/investment-journal.md)
- [Portfolio awareness and observation preview](docs/portfolio-automation.md)
- [Investor mandate](docs/investment-research-context.md), [provider assessment](docs/provider-feasibility.md)
- [Development](docs/development.md), [safety invariants](docs/safety-invariants.md)

Default to offline development. No credentials, operational stores, provider
calls or maintenance without task-specific approval. Offline tests establish
neither suitability nor profitability.
