# GoldScout

GoldScout is being refocused on **long-term, diversified GBP investing**:
accurate portfolio measurement, optional contributions and explainable allocation
proposals, rather than frequent price prediction. The first investment release is
offline and export-only. It cannot place orders or guarantee profitability.

See the [investment guide](docs/investment.md) for the current workflow, supported
accounting and explicit research/broker blockers.

## Investment first

- A human chooses the allocation across global equities, high-quality bonds and
  GBP cash; there is no automatic "best backtest" selection.
- Contribution-first planning, monthly review and annual/five-percentage-point
  drift reviews aim to limit unnecessary turnover. Export-only sales can be
  suggested at annual/drift reviews; proposed proceeds do not fund purchases.
  Separate monthly/annual reviews can be acknowledged in the offline CLI;
  persistent five-point drift remains a trigger. No trade is a valid outcome.
- GBP contributions are separated from investment gains. External savings are
  not broker buying power; manual inputs are not broker verification.
- The read-only dashboard includes `/investment` and `/investment-guide`.
  No dashboard account or trading route approves or executes orders.
- Full FX, corporate-action entitlement/accrual, money-weighted/inflation returns,
  licensed historical comparisons and human-approved broker execution remain
  gated follow-ons. A valid import is not proof of investment suitability.

With the selected Python environment and existing dependencies:

```powershell
python -m investment demo
python -m dashboard --demo
```

The first command prints a synthetic GBP portfolio without creating a database.
After building the frontend as described in the dashboard guide, open
`http://127.0.0.1:8765/investment` for the synthetic investment view.
Use only explicitly selected, separately initialised investment storage for
recorded investment reporting; see the investment guide.

## Separate legacy trading capability

The original Alpaca trading engine and its accounting, stop protection and
restart recovery remain available, **unchanged and separate**. The investment
entry point does not invoke it, adopt its holdings or remove any protective orders.
The legacy strategy configuration below is not the new investment policy.

Economic-calendar and AI news context is available through the separate
[market intelligence service and dashboard](docs/intelligence.md). Collection and AI
are explicitly enabled by the operator; front-end analysis controls never place
orders or alter risk settings. The initial release supports advisory/shadow use only.

Trades SPY, QQQ, BTC/USD, GLD and USO with three strategy modules, ATR-based sizing and hard stops.
SPY/QQQ use 15-minute mean reversion, BTC/USD uses hourly momentum breakouts, and GLD/USO use
4-hour trend following.

## Legacy Alpaca trading setup
For repository AI agents, skills, safety boundaries, and isolated regression commands, see
[AI-assisted development](docs/development.md).

The validated runtime is Python 3.11.9. [requirements-tested.txt](requirements-tested.txt) records the exact
dependency versions used for the offline regression suite; install it with `python -m pip install -r requirements-tested.txt`
to reproduce that dependency set. This is a version snapshot, not a hash-verified package supply-chain lock or
certification of every available platform. The broader requirements file remains available for development.

Use Python 3.10 or 3.11 and an Alpaca paper-trading account with paper API credentials.
Open PowerShell in the project root and run:
```powershell
py -3.11 -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
notepad .env
```

In `.env`, add your Alpaca paper-trading credentials:
```dotenv
ALPACA_API_KEY=your_paper_api_key
ALPACA_SECRET_KEY=your_paper_secret_key
ALPACA_BASE_URL=https://paper-api.alpaca.markets
TRADER_PROXY_URL=
```

Optionally set `TRADER_PROXY_URL` to an HTTP proxy URL, for example
`http://USERNAME:PASSWORD@proxy.example:8080`. Enter real credentials locally, never in source or chat;
optional username/password authentication uses HTTP Basic. Percent-encode reserved characters and use
a literal URL rather than variable expansion. A username requires an explicit password (which may be
empty after the colon). A process environment value takes precedence over `.env`.
The setting applies to Alpaca REST, the intelligence calendar/Fed RSS transport (including direct
transport calls), and the hosted OpenAI adapter.
A configured proxy is required for those requests:
failure does not fall back to a direct connection. Feed and hosted AI requests still resolve, validate
and pin the destination address locally, so the proxy must allow CONNECT to that numeric address.
Malformed `.env` syntax is refused when the proxy setting is read from that file.

An empty setting preserves existing behavior: feeds connect directly, while Alpaca's Requests client
can still honor standard system or `HTTP_PROXY`/`HTTPS_PROXY` settings and `NO_PROXY`. The local
dashboard development proxy is separate; the explicitly configured local AI adapter uses loopback.
HTTP proxy authentication is not encrypted on the connection to the proxy; use a trusted network.
Keep TLS certificate verification enabled and configure the appropriate trusted CA if needed.
Authenticated Alpaca proxy requests refuse HTTP wire debugging to avoid exposing credentials.

Save `.env`, then start the bot from the project root:
```powershell
python -m bot.main
```

To use Python 3.10, replace `-3.11` with `-3.10` in the virtual-environment command. If PowerShell blocks virtual-environment activation, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned` in that PowerShell window and activate again. Keep the `.env` file private; it is excluded from Git.

## Layout
```
config.py                       static settings; broker environment initialized on demand
.env                            API keys (git-ignored)
bot/main.py                     scheduler loop, signal execution
bot/portfolio.py                broker orders, protection, reconciliation and recovery coordination
bot/state.py                     persisted position model and shared state validation
bot/ledger.py                    SQLite recovery state, accounting journal and derived reports
bot/audit.py                     offline ledger inspection and maintenance
bot/risk_manager.py             ATR sizing, stops, correlation filter, daily-loss breaker
bot/data.py, bot/indicators.py  completed-candle fetching, SMA/EMA/ATR
bot/sessions.py                exchange calendar and regular-session candle construction
bot/corporate_actions.py       split/dividend feature adjustments and validation
bot/strategies/                 mean_reversion.py, momentum_breakout.py, trend_following.py
```

## Local Dashboard
The read-only dashboard adds overview, positions, health, activity, and history screens, with an
explicit synthetic demo, an operator-selected ledger reader, and optional
non-authoritative scheduler telemetry. It cannot place orders or change trading. See the
[dashboard guide](docs/dashboard.md) for setup, safety boundaries, and current
validation evidence and remaining limitations. The production build and integrated
desktop/mobile browser checks now work. The subsequent repair pass also resolved
the standalone browser-runner and known dependency advisories; remaining safety
and broker-integration limitations are documented there.

After the dashboard setup/build steps in that guide, launch the synthetic UI with
`trader-dashboard --demo` (or `python -m dashboard --demo` from this checkout).
Production startup requires a matching built frontend; `--api-only` is explicit
development mode. This command never starts the trading bot.

## Architecture
```mermaid
flowchart TD
  Config["config.py<br/>symbols, timeframes, strategy and risk settings"]

  subgraph Live["Trading: scheduled operation"]
    direction LR
    Main["bot.main: TradingBot.run()<br/>schedules monitoring and strategies"]
    Workers["TradingBot._fetch_strategy()<br/>workers fetch data only"]
    Data["bot.data: MarketData.get_completed_bars()<br/>fetches completed candles"]
    Sessions["bot.sessions: regular_hours()<br/>aligns equity candles to exchange hours"]
    Adjust["bot.corporate_actions: adjusted_window()<br/>adjusts features for splits and dividends"]
    Evaluate["TradingBot._process_symbol()<br/>calls Strategy.evaluate() on the main loop"]
    Gates["bot.main: TradingBot._execute()<br/>bot.risk_manager: RiskManager.correlation_filter()<br/>checks entry limits and direction"]
    Portfolio["bot.portfolio: open_position(), close_position(), manage_positions()<br/>sizes entries, places orders and monitors positions"]
    Alpaca["Alpaca API<br/>market data, account and orders"]
    State["bot.state<br/>persisted position model and validation"]
    Ledger["bot.ledger<br/>SQLite recovery state and accounting"]
    Reports["CSV exports and recovery snapshot"]
    Audit["bot.audit<br/>offline ledger review and maintenance"]

    Main -->|submits fetch jobs| Workers
    Workers --> Data
    Data <--> |candle requests and responses| Alpaca
    Data --> Sessions --> Adjust --> Evaluate -->|signal| Gates --> Portfolio
    Main -->|periodic position monitoring| Portfolio
    Portfolio <--> |account and orders| Alpaca
    Portfolio -. "validates persisted state" .-> State
    Audit -. "validates recovered state" .-> State
    Portfolio --> Ledger --> Reports
    Audit <--> Ledger
  end

  subgraph Research["Research: offline simulation, no live orders"]
    direction LR
    History["Historical data access<br/>broker history, cache or synthetic input"]
    BacktestCLI["bot.backtest CLI"]
    Dataset["bot.experiments.dataset<br/>validated, checksummed snapshots"]
    Simulator["bot.backtest: Backtester.run()<br/>simulates decisions and fills over time"]
    ResearchStrategy["bot.strategies: Strategy.evaluate()<br/>same strategy rules as live trading"]
    Runner["bot.experiments<br/>selection, parameter lock and holdout"]
    Registry["Research registry and reports<br/>separate from trading ledger"]
    Results["Metrics, trades, charts and research reports"]

    History --> BacktestCLI --> Simulator
    History --> Dataset --> Runner --> Simulator
    Simulator --> ResearchStrategy --> Results
    Runner --> Registry
  end

  subgraph Intelligence["Advisory intelligence: separate store, no trading authority"]
    direction LR
    Providers["Allowlisted calendar/news providers<br/>and optional AI adapters"]
    Service["market_intelligence.service<br/>opt-in collection and analysis"]
    IntelligenceStore["Separate intelligence SQLite store"]
    Controls["Optional authenticated<br/>analysis-only dashboard controls"]
    Observer["Best-effort bot observer<br/>records shadow context only"]
    Observation["Non-authoritative observation file"]

    Providers --> Service --> IntelligenceStore
    Controls --> IntelligenceStore
    IntelligenceStore --> Observer --> Observation
  end

  subgraph Monitoring["Read-only dashboard backend"]
    direction LR
    Reader["dashboard readers<br/>ledger projection and history"]
    Telemetry["Optional scheduler telemetry"]
    DashboardAPI["Local dashboard API"]

    Ledger --> Reader --> DashboardAPI
    Telemetry --> DashboardAPI
    Observation --> DashboardAPI
    IntelligenceStore --> DashboardAPI
    DashboardAPI -. "analysis settings only" .-> Controls
  end

  Config --> Main
  Config --> BacktestCLI
  Main -. "publishes optional telemetry" .-> Telemetry
  Evaluate -. "same strategy implementation" .-> ResearchStrategy
  Main -. "asynchronous observation only" .-> Observer
```

## Legacy trading guide
This project is a program that follows preset rules for trading through Alpaca, a brokerage. It watches a small
set of markets and decides when to seek a trade or exit one. The trading rules are not AI, and they do not
guarantee a profit. By default it is configured for **paper trading**: a practice account using simulated money,
not real money.

### What does it trade?

The default settings use three different approaches:

- **SPY and QQQ:** These are funds holding baskets of US stocks. The bot looks for a price that has moved unusually
  far from its recent average, expecting it might return toward that average. It checks 15-minute periods.
- **Bitcoin (BTC/USD):** The bot looks for a move beyond a recent high or low, backed by unusually high trading
  volume. It checks hourly periods. Alpaca does not allow the bot to short Bitcoin, so a downward signal can close
  a Bitcoin holding but cannot open a bet on its price falling.
- **GLD and USO:** These are funds providing exposure to gold and oil, respectively. The bot compares a
  faster-moving price average with a slower one to look for changes in trend. It checks four-hour periods.

The periods above are summarized as **candles**: each candle describes the prices and trading volume over one
finished period. The strategies use these completed periods for entry decisions.

### What happens during trading?

1. The bot obtains market data and applies the relevant strategy. A strategy may suggest buying, selling short
   (a bet on a falling price), closing a holding, or doing nothing.
2. Before opening a trade, the bot checks account and risk limits and whether the data and existing orders are
   safe to use. A suggestion does not necessarily become an order.
3. If permitted, the bot sends an order to Alpaca and monitors the result. It calculates position size using
   recent price movement and applies limits to reduce the amount at risk.
4. While a position is open, it monitors it and its protective orders. A **stop** is an order intended to limit
   loss if the price moves against the position; some strategies can also move the stop as the price moves in
   the position's favor.
5. The bot keeps a database ledger of orders, trades and recovery information. It uses this to help reconcile
   its records with the broker and recover after an interruption. CSV files are reports, not the main ledger.

### What else is included?

- **Backtesting** simulates the trading rules on historical prices to explore how they might have behaved. It is
  an approximation, not a replay of actual broker execution or proof of future results.
- The **dashboard** presents information about the bot and its history; its trading and account views cannot
  place orders or change trading settings.
- Optional **market intelligence** can collect economic-calendar and news information and provide AI summaries.
  It is separate from the trading rules: its advisory information and analysis controls cannot place orders,
  change risk settings, or influence the bot's trading decisions.

### Important limits

Trading can lose money. Stops cannot guarantee an exit price, and Bitcoin stop-limit orders may not fill at all.
The daily-loss safeguard blocks new entries when triggered; it does not automatically close existing positions.
Stopping the bot also does not close positions: broker-held stops remain at their last set prices, but software
monitoring and stop updates pause. Neither paper trading nor backtesting proves real-world performance.

For details, see [Behaviours worth knowing](#behaviours-worth-knowing),
[Persistent Audit Ledger](#persistent-audit-ledger), [Backtesting](#backtesting), and the
[market intelligence guide](docs/intelligence.md).

## Persistent Audit Ledger
The SQLite ledger uses versioned transactional schema upgrades, WAL mode, `synchronous=FULL`, a bounded
two-second lock wait, and one serialized writer. Keep it on a local disk, not a network share. Existing exit
payloads and legacy CSV values are retained; historical information that was never captured is not reconstructed.
Daily CSV history is imported once. Both trade and daily CSVs are now derived reports, not accounting inputs.

The database contains an append-only event journal, exact order intents and broker observations, current recovery
state, daily accounting, raw broker activities, and evidence-linked corrections. Changed state snapshots are linked
to their journal sequence; replay retrieves the latest committed state and verifies it against the recovery snapshot.
Position changes, entry intents, processed bars, risk controls, cancellations and replacements are audited.
Session events retain risk/strategy settings without API credentials. Broker decimal order values and unrounded exit
accounting inputs are retained as decimal strings; live sizing and portfolio arithmetic still use floats.
Observed cumulative order fills are not claimed to be individual broker executions. Broker timestamps remain in
payloads separately from the journal's UTC observation time.

Startup binds the database to the broker account ID and paper/live environment before trading. An older unbound
database is bound to the first account used after upgrade: independently verify that account before the first run.
Invalid recovery state or a persistence failure quarantines writes and stops the scheduler. Exit accounting is
prepared and committed before quantities and fill counters are published in memory. A failed commit cannot advance
those counters. Resting broker stops are left untouched during quarantine; software protection and trailing updates
stop. Repair storage and reconcile the broker before restarting. Do not delete the database or reset flags to bypass
a persistence failure. No database transaction is held open across a broker request, and broker/database atomicity
is not possible: recovery depends on durable intent, client IDs and broker reconciliation.

Heartbeat activity sync downloads all available activity types with a seven-day overlap around the last successful
checkpoint, at most 20 pages per heartbeat. First-run coverage is seven days, not full account history. Failed or
incomplete sync retains its checkpoint. Older history or postings outside the overlap need an explicit import.
Conflicting duplicate activity IDs are refused rather than silently overwritten. Raw activities are evidence, not
automatically matched settled fees or comprehensive broker cash accounting.

Stop the bot before running the offline maintenance commands; they acquire the same process lock and make no
broker calls. A database must already exist. Backups require a new destination and use SQLite's backup API plus
integrity/recovery checks; never copy only the main database file while WAL writes are active.
```powershell
python -m bot.audit summary
python -m bot.audit events --after 0 --limit 100
python -m bot.audit replay
python -m bot.audit backup .\ledger-backup.sqlite3
python -m bot.audit export .\audit_exports
python -m bot.audit import-activities .\broker-activities.json
python -m bot.audit correct .\correction.json
```
Activity imports are JSON arrays of original broker activity objects with nonempty `id` values. A correction file
has the following shape; `fees` is the verified total fee amount allocated to this exit slice, not an incremental fee:
```json
{
  "execution_id": "bot-entry-example:1",
  "correction_id": "review-example-1",
  "activity_id": "broker-activity-example",
  "exit_price": "101.25",
  "fees": "2.50",
  "reason": "Verified exit price and allocated settled fees against broker activity"
}
```
Corrections require an existing exit and stored activity evidence. The operator must verify their relationship,
quantity, currency and fee allocation; the presence of an activity ID alone does not prove the supplied correction.
Repeated identical correction IDs are harmless; conflicting reuse is rejected. Corrections append records and adjust
daily realized totals without modifying original exit records. Exports include `accounting_corrections.csv` separately;
`trades.csv` retains original reported values. Prior-day fills restate existing historical realized totals rather than
today's totals. Missing historical daily rows become audit exceptions; opening/closing equity and cash-flow-adjusted
performance are not invented or restated from order fills.

After independently reviewing holdings, activities and exceptions, an unknown-price accounting block can be cleared
explicitly. Every unverified exit must first have an evidence-linked correction. This does not clear the daily-loss halt:
```powershell
python -m bot.audit resolve-accounting --reason "Broker holdings and accounting exceptions reviewed"
```
Export failures are logged and recorded when storage is available; exports retry on subsequent saves or restart without
invalidating committed trades. Journal history grows with state changes and is not automatically pruned. Append-only
triggers and snapshot checks catch accidental edits, not privileged tampering, disk failure or coordinated modification
of the database. Broker statements remain authoritative for settled activity. Maintain verified off-machine backups.

Unknown exit prices are blank (`price_verified=False`), not approximated at a stop level. Unresolved accounting
blocks new entries. Broker-held exit fill times are retained when available; a prior-day fill is not counted as
today's realized result. Historical daily rows and delayed broker fees still require account-activity reconciliation.

Partial exits now log executed slices and retain the residual position. Stop cancellation is confirmed before an exit;
unresolved exit/protection states block new entries. The monitor verifies protective order status, side, quantity and
price, follows replacement IDs, and checks coverage even when the equity market is closed. Restored stop IDs are not
trusted until checked against the broker.

Protective coverage now requires GTC time-in-force, matching symbol/side/remaining quantity, and no imminent
expiration. Crypto stop-limit prices must be on the asset tick and respect the configured adverse buffer.
Equity OTO groups request GTC at entry; legacy DAY legs are canceled and converted to standalone GTC protection
only after broker-confirmed cancellation and residual checks. Nested legs and group IDs are retained.

Each new entry refreshes broker positions and open orders immediately before recording durable intent. Any existing
same-symbol position or open order blocks a fresh entry; adding to an existing position is not supported. After a fill,
the broker position quantity must be explained by that entry's fills. Crypto quantity may be lower only within the
configured estimated taker-fee allowance when fees are taken in the asset; this is a bound, not settled fee evidence.
Excess or inconsistent exposure retains the intent and quarantines accounting rather than assigning the
aggregate position to the latest fill. Restart recovery applies the same quantity check and carries the recovered
equity entry ID forward as the expected OTO parent. An OTO stop is attached only when its broker-reported parent
group matches the entry order. Foreign same-symbol orders remain untracked and are not canceled or adopted; when they
prevent safe protection or adoption, the position remains quarantined for reconciliation. Broker reads are not atomic
with order submission, so external account changes still require prompt reconciliation.

Stop movement uses confirmed cancel/account/re-arm, not PATCH replacement. This avoids relying on OTO replacement
support but still introduces an unprotected interval while cancellation and re-arm complete. Per-order cumulative
fill baselines persist across cancellation observations. Unfamiliar replacement orders with nonzero fills, partially
filled adopted stops without provenance, and unknown orders on a managed symbol block further order changes rather
than guessing ownership or inherited quantities. Independently reconcile these exceptions before resuming.

Crypto history refreshes start on whole-timeframe boundaries and overlap a complete preceding candle; off-grid
responses are refused. Broker aggregation and order-group behavior still require authenticated paper validation.

Trade CSVs include gross P&L, fees and a `fees_estimated` flag; crypto `profit_loss` is net of configured fee estimates.
Crypto entry fees inferred from credited quantity and exit taker-fee estimates are allocated across partial exits.
These are not settled broker activity totals: fee tiers and delayed fee postings must still be reconciled independently.
Existing trade CSV headers are migrated without changing historical values; older fee fields remain blank.

## Behaviours worth knowing
- **Paper by default.** A live URL is refused unless `CONFIRM_LIVE_TRADING=yes` is set in `.env`.
- **Sizing:** qty = 1% of equity / ATR(14) on each strategy's own timeframe. Notional is capped per position
  (1.5x equity) and in total (2x equity) in `config.RISK`; a cap can only reduce risk below 1%.
- **Hard stop = 1 ATR** (equals 1% of equity at full size, clamped so planned loss at the stop price
  does not exceed 1%; gaps and slippage can increase realized losses).
  It rests at the broker as a GTC order and is also enforced in software. The 2x / 3x ATR trailing stops
  take over once price has moved far enough in your favour (the tighter stop always wins).
- **BTC/USD cannot be shorted on Alpaca**, so a breakdown signal closes a long and nothing more.
  Crypto uses stop-limit orders (Alpaca does not offer plain stop orders for crypto); a fast move or gap
  can leave a stop-limit unfilled.
- Entries fire once per newly closed candle; mean-reversion exits are checked against the live price.
- Equity strategies pause when the market is closed; BTC/USD runs 24/7.
- The 3% daily-loss breaker blocks new entries; it does not close existing positions.
- A triggered daily-loss halt remains latched until the next New York calendar day, even if equity recovers.
  Day rollover happens before entry gates and realized accounting, using broker `last_equity` as the baseline.
  Cash deposit/withdrawal activities (`CSD`/`CSW`) are deduplicated and removed from measured performance.
  Failure to verify cash flows blocks entries. The baseline is broker session equity, not a guaranteed midnight
  crypto valuation, and the breaker cannot guarantee a maximum realized daily loss.
- Endpoint validation requires the exact approved Alpaca HTTPS host. Risk values and strategy ownership/thresholds
  are validated; nonfinite sizing inputs cannot produce an order. Crypto sizes/prices use asset metadata increments
  and order limits. Stops round toward protection so ticks cannot increase planned dollar loss. Fractional equity
  market exits preserve fractions, but fractional GTC protection is refused until an explicit day-stop policy exists.
- Stopping the bot leaves positions open with their resting stops. On restart it restores state,
  detects stops that filled while it was down, and recovers persisted entry intents.
- Unmanaged broker positions block new entries. `ADOPT_CONFIGURED_POSITIONS=False` is the default;
  set it to `True` only for a dedicated bot account when takeover of configured symbols is explicitly intended.
  Other symbols are never adopted automatically. Adoption preserves valid existing protection and uses a
  current-price dollar-risk fallback if ATR data is unavailable. Ambiguous entry intents remain blocked until resolved.
- Equity entries request broker OTO attached stops. Partial entry fills and crypto still require software
  reconciliation; attached stops are not a guarantee of protection during every crash/cancellation race.
- Historical downloads run in read-only worker threads with separate API sessions. All order/portfolio mutations
  remain serialized on the main loop. Monitoring runs first and is rechecked between ready strategy results.
  New entries require a successful monitoring pass started within `MONITOR_INTERVAL`; failed or overdue
  monitoring blocks entries until a recent pass succeeds, while normal exits continue. Slow synchronous work
  can delay monitoring and keep entries blocked.
  Requests use explicit connect/read timeouts, bounded retry budgets, and disabled SDK-internal retries.
  Fill/cancel waits are short and unresolved states are recovered on subsequent monitors. This reduces blocking;
  it does not guarantee protection-update latency during outages or slow HTTP responses.
- While stopped, broker-held stops remain at their last set prices; software checks and trailing-stop
  updates resume only when the bot is running.
- Equities use whole shares (rounded down) so GTC stop orders can be attached.

## Backtesting
For the agreed GBP strategy/portfolio campaign, first read [investor context](docs/investment-research-context.md)
and the [backtesting protocol](docs/backtesting-protocol.md). Those documents define research-only controls,
feasibility gates and current runner blockers; they do not change live settings. The Alpaca download commands
below are general operator workflows, not approved data acquisition for that campaign. Neither cached Alpaca
data nor synthetic smoke results establish compliance with its public/user-supplied-data and GBP portfolio requirements.

Run these commands from the project root in PowerShell:
```powershell
python -m bot.backtest                # pulls 6 months from Alpaca (needs keys in .env), saves CSVs to backtest_data/
python -m bot.backtest --use-cache    # reuse fresh caches; may request refreshes and corporate actions
python -m bot.backtest --synthetic    # offline smoke test on random data (results are meaningless)
```
Options: `--months`, `--capital`, `--slippage` (default 0.0005 = 0.05% per fill), `--no-adjust`.

`--use-cache` is not an offline mode: missing or stale minute data may be fetched, and equity corporate actions
are requested even when minute data is cached. Local research and ledger inspection do not initialize broker
environment settings merely by importing configuration; broker clients still validate their endpoint, and live
trading still requires confirmation.

Additional annual-rate assumptions: `--risk-free-rate`, `--cash-yield-rate`, `--margin-rate`,
`--short-borrow-rate` (fractions, default zero). Funding and cash accrual are reported separately; short sale
proceeds are excluded from modeled cash yield. These are simplified annual-rate assumptions, not broker statements.
Funding costs affect account return but are not allocated to individual trade P&L.

`backtest_metrics.json` includes CAGR, excess-return Sharpe and block-bootstrap confidence interval, Sortino,
Calmar, expected shortfall, sampled drawdown duration, turnover, sampled gross exposure, funding/fees, and passive
portfolio comparisons. Forced end-of-test exits remain in account return but are excluded from natural-exit trade
statistics. Cash, SPY total-return and equal-weight passive benchmarks are gross of execution costs. The reported
volatility-matched comparison uses full-sample realized volatility and is explicitly descriptive, not an investable
out-of-sample strategy. Taxes and comprehensive factor attribution are not modeled.

Crypto fees default to 0.25% taker / 0.15% maker per side, configured through `--crypto-taker-fee` and
`--crypto-maker-fee` as fractions (or `config.RISK` for live log estimates). Market fills and immediately executable
stop limits use taker fees; previously unfilled resting stop limits use the configured maker assumption. Select rates
appropriate to the account's actual tier. Entry fees reduce credited crypto quantity; exit fees reduce cash proceeds.
The summary reports fees separately from slippage, and simulated trade P&L includes both entry and exit fees.

The simulator processes separate candle-open and candle-close events chronologically. Completed closes are released
before opens at the same timestamp; shared-timestamp marks are batched, with alphabetical execution priority.
Gap stops are checked at the open. Candle-range stop executions are timestamped at the close, when their range becomes
known, rather than retroactively changing earlier portfolio cash. This is a coarse OHLC approximation, not tick replay.

With minute execution data, the elapsed minute is first checked against its prior protective stop. Completed
strategy features at the same timestamp are then published, followed by extreme/trail updates and enforcement
of the newly computed stop at the observed closing price. Mean-reversion targets use those completed features;
new entries are decided afterward. A newly tightened stop is never applied retrospectively to the previous
minute's range. Strategy boundary prices and complete OHLCV aggregates must agree with minute execution data.
This is deterministic minute sampling, not exact reproduction of live polling or the unknown intraminute path.

Live stale reversal/breakdown signals retain exit intent but cannot open the opposite side. Stale signals while
flat or already on the desired side create no orders. The completed bar is consumed, avoiding repeated stale
entry attempts. All exits remain subject to broker ownership and residual-quantity safety checks.

Historical downloads use SDK pagination in bounded weekly UTC minute partitions under `backtest_data/minute_partitions`.
Partitions retain symbol/feed/asset/coverage/checksum provenance. Recent partitions refresh corrections after five
minutes; extending an up-to-date partition fetches a short tail overlap. Refreshing a shorter request never truncates
existing coverage. Older completed partitions can be reused; delete only the relevant history-cache partition or run
without `--use-cache` to refetch older corrections. This cache is not an immutable truth source for late provider edits.
Strategy bars are rebuilt from the same canonical minutes used for execution, including crypto. Legacy monolithic
CSV caches are not used as inputs. Tampered caches are rejected. The test window ends at common
instrument coverage and requires indicator warm-up and execution coverage. Missing exchange sessions, equity
intraday gaps over five minutes, and crypto gaps over two minutes fail validation rather than silently simulate.
Sparse IEX data may fail these checks; use an appropriate consolidated feed rather than filling invented prices.

Minute inputs must lie on timezone-aware minute boundaries. Execution validation requires the first and final
observations of each session intersecting the requested test interval, respecting holidays and early closes.
The test interval is explicit: partial warm-up days outside it are not treated as full missing sessions.
Strategy candle aggregation requires an observed opening minute and final minute and rejects interior gaps over
five minutes. A first rolling-history candle clipped by the download start is dropped; a later observation is
never relabeled as its earlier open. Empty or extended-hours-only equity histories retain the same UTC/`bar_end`
schema. Generated strategy CSVs are reports with provenance version 5; canonical minute partitions use version 1.

Live equity workers retain aggregated features rather than full historical minute datasets. Cold warm-up is built
in chunks of at most seven days; subsequent evaluations refresh only the recent tail, with a daily rebuild and
five-minute corporate-action query cache. All requested completed candle boundaries must be present. Research
still retains minute execution data and can require substantial storage; streamed merged event iterators avoid
materializing and sorting all minute events at once. This is bounded work, not a measured maximum-latency guarantee.

Equity candles are constructed from regular-session one-minute data using the XNYS calendar, anchored at 09:30
New York time. Holidays, DST and early closes are respected; the final four-hour candle ends at session close,
even when shorter than four hours. Live features and historical features use the same convention. Retrieved
one-minute execution data drives intermediate stop/trailing checks and mean-reversion exits; the live monitor also
checks its most recent completed-candle mean target. One-minute OHLC remains coarser than live 30-second polling.
Synthetic history includes exchange sessions and overnight gaps but has no real execution microstructure.

Raw execution prices are retained. Corporate actions are downloaded with pagination and used to adjust feature
windows only after the ex-date. Splits change simulated shares, price basis, ATR and stops while preserving dollar
exposure. Ordinary USD cash dividends accrue as receivables (or liabilities for shorts) on the ex-date and settle
to cash on the payable date. Equity benchmarks include splits and dividend entitlement. Non-USD dividends,
missing payment dates, due-bill cases, ticker-changing splits and unsupported action types fail explicitly.
Alpaca warns that corporate-action records can arrive late; data availability and historical completeness still
need broker validation. Fractional split entitlements are retained in simulation; cash-in-lieu is not modeled.

Corporate-action IDs are deduplicated across all pages; conflicting reuse is refused. Global effective-time order
puts splits before same-day dividends and uses the ID as a deterministic tie-breaker. Live features, simulated
entitlements and benchmarks use that same ordering, regardless of the API page order.

Crypto stops are stop limits: a gap beyond the limit can leave an activated order unfilled. The default software
fallback checks the candle's closing price, which is coarser than live polling. `--broker-stops-only` disables that
crypto fallback to examine protection during bot downtime. Limit fills respect the limit price; ambiguous intrabar
trigger/rebound ordering is deferred conservatively rather than assumed filled.

When minute execution data is attached, the software fallback checks completed minute prices instead of waiting
for an hourly strategy close. Synthetic or externally supplied strategy-only histories retain the coarse fallback.

Outputs: summary table (printed last), `backtest_results.png`, `backtest_trades.csv`, and, only when explicit research
locks a different parameter set, `backtest_results_adjusted.png` and
`backtest_suggested_params.json`. `config.py` is never modified automatically.

Per-instrument results are standalone runs using the full starting capital; the combined result runs all five
instruments together in one portfolio.

Simulation rules: signals on a completed candle fill at the next candle's open; slippage hits every entry and exit;
stops are checked against each candle's high/low (equity gaps fill at the open; crypto limits may remain unfilled); sizing, stops, correlation filter and the
no-crypto-short rule are the live bot's own code. Crypto must be paid from positive cash, so a margin-financed equity
long can leave BTC entries skipped for buying power (counted in the summary). Annual margin/borrow rates are modeled
when configured; broker-specific settlements, partial fills and queue priority are not reproduced.

Offline regression checks (temporary storage, mocked broker boundaries and blocked Python sockets):
`python scripts/offline_tests.py`. This runner is not an OS sandbox; see [development guidance](docs/development.md).

Parameter search is disabled by default. `--research-optimize` explicitly enables research with at least two years
of data. Three expanding chronological validation folds occupy the first 80% of the sample; all candidates,
including unchanged parameters, are recorded in `backtest_research_trials.json`. A candidate needs sufficient
natural exits, positive Sharpe in every fold and a positive lower block-bootstrap confidence bound adjusted for
the number of trials. Parameters are locked before the final 20% holdout is evaluated; holdout results are
reporting-only and never used to choose or accept parameters. Each fold starts flat, with forced boundary exits
excluded from trade counts. The adjusted full-window rerun is exploratory and not untouched out-of-sample evidence.
These checks reduce selection bias but do not prove an edge. Additional manual experiments also count as trials;
never repeatedly tune against the same final holdout. Risk settings are not automatically optimized.

Research runs additionally persist append-only records in `research_runs/registry.sqlite3` (override with
`--research-dir`) and a new UUID directory for each run's manifest/results. Manifests fingerprint strategy/execution
data, corporate actions, code/config files, interpreter and dependency versions. Individual trials and locked
parameters are recorded before final holdout evaluation. Bootstrap draws scale to at least 20 expected usable
observations per requested adjusted tail (22,400 draws for 28 trials), within a 100,000-draw budget; insufficient
variance/sample evidence produces undefined bounds, not a passing gate. This is an approximate bootstrap diagnostic,
not formal protection against all selection bias.

Research-mode base reports stop at the first 80% selection window; final-period results are reported separately.
All nonsynthetic descriptive runs register their exposed intervals too. Repeating descriptive reports is allowed,
but later research cannot claim overlapping symbol/date intervals in the same registry as untouched holdouts.
Keeping one registry is essential; deleting it, switching directories, or external/manual viewing cannot be policed
by the program. Earlier reports created before the registry existed are not reconstructed. Full-window adjusted
charts remain exploratory. JSON reports use `null` for nonfinite metrics and standard strict JSON encoding.

### Experiment runner (offline strategy/parameter comparison)

`python -m bot.experiments` compares strategies and parameter grids across named, mixed historical windows using the
same `Backtester`, costs and next-open fill rules as above. Research-only: it never places orders.

1. Write a spec (see `experiments/example_mean_reversion.json`): named UTC windows (end exclusive; disjoint; role
   `selection` or `holdout`), strategies with `params` and a `grid` (dotted keys such as `entry_std.SPY` allowed),
   cost overrides, optional `stress` multipliers and selection gates. Strategy/instrument pairings outside live
   ownership are allowed for research only (symbols must still be in `INSTRUMENTS`); live validation is unchanged.
2. `prepare SPEC [--allow-network]` prints required coverage (including warm-up). Only with `--allow-network` does it
   download minutes and corporate actions, validate them, and write an immutable checksummed snapshot under
   `experiment_data/datasets/<id>`.
3. `validate SPEC --dataset ID|latest` is a local dry run: trial count, approximate estimated simulations (hard
   cap 250 per run; an estimate, not a runtime guarantee), coverage problems (missing data is listed per
   strategy/window), and a warning if a holdout overlaps earlier research exposure.
4. `run SPEC --dataset ID|latest [--allow-partial]` runs every trial on every selection window flat and independent,
   selects per strategy on pooled selection-window results (configurable gates include minimum natural exits,
   share of profitable windows, and, by default, trial-adjusted Sharpe lower bound > 0), records `parameters_locked`
   in the registry, and only then evaluates the locked trial and the baseline on holdout windows (report-only)
   and under cost stress.
   Outputs in `research_runs/<run>/`: `summary.md`, `results.csv/json`, `locked.json`, `trades.csv`,
   `breakdown.csv`, `curves/`, `manifest.json` (spec hash, dataset id, code/dependency fingerprints, trial history).
   Holdouts are single-use: re-running one or relabeling its reserved interval as selection fails in the same
   registry. Selection (including warm-up) must not overlap previously reserved holdouts; choose a previously
   unexposed holdout and do not change registries to bypass history.
   `list` shows datasets, `show RUN_ID` prints a summary.

Limitations: no combined multi-strategy portfolio run; risk settings are not optimised. Trial-adjusted intervals
include earlier registered trials on the same dataset in the conservative adjustment; unregistered/manual searches
and trials on other dataset IDs are not covered. Results do not establish live profitability.

The GBP campaign is stricter than this generic runner: 60 total candidates including baselines rather than
the CLI's 125-per-strategy/500-total ceilings; one GBP portfolio cash/equity clock with FX, product eligibility,
costs/tax scenarios and the selected cash comparator; adverse-cost selection evidence; and no holdout opening
if selection fails. Current stresses cover finalists/baselines, and a baseline holdout can run with no locked
candidate. A separate [offline campaign-data importer](docs/campaign-dataset.md) validates and snapshots
user-supplied daily/weekly product, FX, cash-rate and corporate-action files. Its read-only
`python -m bot.campaign_data readiness DATASET_ID --dataset-dir .\campaign_datasets --profile .\readiness.json`
audits selected daily GBP data against explicit XLON sessions, units and availability rules.
It shows exact GBP-normalized examples for explicitly declared pound or pence quotes, rejects ambiguous units,
and reports external/synthetic evidence separately. A separate `normalize` command can return the complete
selected daily series with an explicit declared-availability cutoff, without changing the source snapshot.
It does not verify historical data vintages or authorize performance research or trading.
See the [profile and view contracts](docs/campaign-dataset.md#scoped-gbp-daily-readiness-audit).
The separate [`fx-readiness` audit](docs/campaign-dataset.md#scoped-fx-publication-and-freshness-audit)
checks declared publication and maximum age at supplied clocks. The [`fx-view`](docs/campaign-dataset.md#complete-fx-observation-view)
returns every selected source observation at those clocks, or rejects the whole view if one is missing or stale.
Neither converts prices or establishes complete event coverage, executable FX rates or historical vintages.
The `account_fills` helper is only a synthetic GBP cash/quantity ledger; it does not value positions or report returns.
It is not connected to the USD minute runner and does not provide GBP portfolio accounting,
close remaining campaign feasibility gates, or authorize runs or trading.
See [readiness blockers](docs/backtesting-protocol.md#9-current-runner-readiness-and-blockers); these
requirements are not implemented simply by writing a valid spec.

### Advisory/shadow market intelligence

The separate [intelligence release](docs/intelligence.md) has no trading authority.
Opt-in BLS/BEA calendar and Fed RSS collection, AI adapters, analysis-only browser controls and a
best-effort shadow observer are implemented. Calendar coverage remains unverified; settings requests,
service application and bot observation are distinct. Trading/account dashboard APIs remain read-only.
The trading bot remains price-rule based; this work does not authorize news/LLM strategy signals in the
GBP campaign, provider calls, operational service startup, or trading mutations.

Recovery validation now checks configured ownership, aware timestamps, optional prices/flags/IDs, integral counters,
cash-flow identity uniqueness and matching positive fill quantity/notional pairs. Legacy optional defaults remain
supported; unknown position fields require explicit migration. Split-like live quantity/cost-basis changes now block
orders for action/fill-unit reconciliation rather than heuristically resetting audited counters. A filled entry that
is already flat cannot arm a phantom protective order. Protection placement rechecks broker side and residual size.
These safeguards do not implement automated split-unit or settled-fee reconciliation.

Historical validation at an earlier repair milestone: 110 regression tests passed, including research
provenance/holdout reuse, partition refresh, complete recovery validation, flat-entry protection and stale exit
preservation, minute feature/stop
ordering, complete-candle boundaries, cross-page corporate
actions, grouped protective orders, cancellation fills, SQLite migration, backup/replay, duplicate/conflicting
activities, evidence-linked corrections, partial-exit restart, disk/lock and acknowledgement failures, export recovery,
accounting resolution and scheduler quarantine. The earlier synthetic CLI smoke run used nonzero funding/risk-free
assumptions. No authenticated broker calls were used in these validations; activity sync still needs paper-account
integration validation before relying on broker coverage.
For current validation, run the isolated suite described in [AI-assisted development](docs/development.md);
historical counts do not certify the current checkout.
