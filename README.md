# GoldScout

GoldScout is a local GBP portfolio evidence journal and dashboard for selected
savings, a first-home LISA and longer-term investments. It records
operator-supplied evidence; it does not verify custody, choose investments,
move money or execute trades.

## Start on Windows

GoldScout is currently distributed as a source checkout, not a desktop installer.
From the repository folder in PowerShell, install the dashboard dependencies and
build the frontend once:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dashboard.txt
npm --prefix frontend ci
npm --prefix frontend run build
```

After the one-time setup, double-click `Start-GoldScout.cmd` from the checkout.
Alternatively, start GoldScout from the same folder in PowerShell:

```powershell
.\.venv\Scripts\python.exe -m dashboard
```

In a source checkout, records are stored in the ignored `local-investment/`
folder. Installed use requires selecting the same private folder with
`--workspace <directory>` on each start. The dashboard has no sign-in and must
stay on this computer; keep the records and backups private.

## Set up

The first run guides you through accounts and starting figures. Have dated
sources for cash and all holdings at the same current point in time. For each
investment you also need its provider's GBP price, supported quantity step and
minimum purchase. A price may be left blank if unknown; do not guess the other
rules. Cash-only setup skips investment details. Review the preview before
confirming; nothing is saved to the journal before confirmation. Setup and
recording drafts save in this browser on this computer. Full previews and
acknowledgement checkboxes are never restored. If a recording save has an
uncertain outcome, its exact retry reference is retained so the same save can
be checked; the app blocks a new entry until it is resolved. Recovery re-entry
is deliberately not saved.

For investment accounts, list each different investment once, then enter its
quantity for each account that holds it. Use current figures for the same point
in time; setup records them when you request the preview and cannot be
backdated.

The optional eToro connection shows a separate provider snapshot and, after an
explicit manual refresh, up to 365 calendar days of closed-trade history. It
does not populate the journal and is live-unvalidated. Basic use does not
require it.
See [the connection guide](docs/investment.md#optional-etoro-connection).

## Review

On **Your portfolio**, check the recorded value and date, **What needs
attention**, and the comparison between recorded values. Choose **Review
accounts** to start a guided review across active accounts. Update each account
using dated sources: record transactions, compare cash, and check holdings,
prices and savings terms. Each record has its own preview and confirmation. A
cash check does not change the recorded balance. If nothing changed, mark the
account checked without adding a record. Confirmed account updates in this
browser retain other accounts' checks; unsupported or outside journal changes
make the review stale.

Choose an account review interval and, for savings accounts, a separate terms
recheck interval. Finish the review to save an available complete portfolio
value for the next comparison; outstanding items remain visible. Drafts, review
progress, and reminders are local browser data, are not included in the journal
backup, and can be lost if the browser is cleared.
They are not evidence that accounts are current. An unexplained remainder is
not a verified investment return. Missing historical performance does not stop
you from recording and reviewing your portfolio.

Manual recording is enabled for normal local launches. For viewing without
journal writes, add `--investment-read-only` to your usual startup command,
keeping the same workspace. This also prevents first-run setup from saving.

## Back up and recover

Use **Back up and practise recovery** on the portfolio page to request a verified
journal backup and rehearse checking a copy. The ZIP is unencrypted; store it
protected and off this computer. The backup does not include goals, browser
reminders, setup receipts or source documents. Save those separately. A
rehearsal does not install a backup or overwrite the active journal. See
[backup and recovery](docs/investment-journal.md#backup-and-restore).

See [setup and operations](docs/investment.md) and [dashboard development](docs/dashboard.md).

## Offline preview

```powershell
.\.venv\Scripts\python.exe -m investment demo
.\.venv\Scripts\python.exe -m dashboard --demo
```

These use synthetic evidence, not account access.

## Boundaries

- The journal supports GBP cash and GBP-priced long equity/bond holdings in
  deposit and investment accounts. It records contributions, withdrawals,
  transfers, buys, sells, splits, paid interest/dividends, prices and cash checks.
  It does not convert other currencies or infer missing transactions.
- Explicit GBP account/platform charges can be recorded as fees, not deposits
  or withdrawals. Tax, FX, fund charges, spread, slippage and other costs remain
  outside a complete cost history. Do not enter a balancing flow to disguise an
  unsupported charge or reconcile a difference.
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

Local source edits, tests, builds and synthetic previews are normal development.
Use temporary test data; credentials, operational stores, provider calls and
maintenance need task-specific approval. See the development guide for checks.
