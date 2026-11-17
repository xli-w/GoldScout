# GoldScout

GoldScout is a local GBP portfolio evidence journal and dashboard for selected
savings, a first-home LISA and longer-term investments. It records
operator-supplied evidence; it does not verify custody, choose investments,
move money or execute trades.

## Start locally

With the documented dependencies and frontend build available:

```powershell
python -m dashboard
```

In a source checkout this selects the ignored `local-investment/` workspace.
An absent or empty workspace shows guided setup: review accounts, products and
dated opening balances, preview, then explicitly confirm creation. Startup and
preview create no files. Existing valid journals open read-only; invalid or
conflicting files are never reset. Installed use requires `--workspace <directory>`.
Subsequent manual recording requires `--investment-recording`.

The dashboard has no user login. Keep it on loopback; do not expose or tunnel it.
See [setup and operations](docs/investment.md) and [dashboard development](docs/dashboard.md).

## Offline preview

```powershell
python -m investment demo
python -m dashboard --demo
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
