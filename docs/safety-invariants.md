# Safety Invariants

These are development contracts, not a certification of live readiness. Current operating limits are documented in [README](../README.md). Historical reviews must be rechecked against current source.

## Execution

- Market-data workers fetch only; the main loop owns strategy evaluation and mutations ([main](../bot/main.py)).
- Stop requests skip remaining scheduler jobs and block entries still in preflight. An entry that passes its final guard after durable order intent is in flight; shutdown does not cancel it, and recovery reconciles it. Shutdown leaves open positions and their resting protection in place.
- New entries require a successful position-monitor pass started within `MONITOR_INTERVAL`. Failed, overdue, or never-completed monitoring blocks entries; retries and ordinary exits continue.
- Orders require known ownership, matching symbol/side/residual quantity, valid protective terms, and broker-confirmed state ([portfolio](../bot/portfolio.py)). Unknown provenance or direction blocks mutation.
- Fresh entries refresh same-symbol broker positions and open orders before durable intent. Existing exposure/orders block new entries; post-fill broker quantity must be conserved by the entry fills (crypto fee deductions are bounded by the configured estimate), and a protective OTO leg is attachable only with matching parent-group evidence. Foreign orders stay untracked and are not canceled or adopted.
- Durable intent precedes submission. Observed cumulative fills are not individual executions. Per-order baselines and repeated observations must conserve quantity without double accounting or resurrecting exited residuals.
- Cancellation must be confirmed before a competing exit or re-arm. Cancellation/re-arm has an unprotected interval. Stops and crypto stop-limit orders do not guarantee execution or price.
- Finite sizing inputs and risk caps remain mandatory ([risk manager](../bot/risk_manager.py)). The daily-loss halt blocks entries, not automatic liquidation. Live endpoint confirmation must not be bypassed.
- Local configuration imports do not initialize broker settings. Trading startup validates them before logging or locking, and broker clients still require an approved endpoint.
- When `TRADER_PROXY_URL` is configured, Alpaca REST, intelligence feed transport and the hosted AI adapter must use it without direct fallback. Proxy authentication must not appear in logs; external destination address validation, pinning and TLS hostname verification remain mandatory. The explicitly selected local AI adapter uses loopback.

## Accounting and Recovery

- [SQLite ledger](../bot/ledger.py) is the recovery/accounting authority; the shared persisted-state model and validator live in [state](../bot/state.py). CSVs are derived reports; JSON is a compatibility snapshot.
- Commit exit/state together before publishing in-memory quantities or counters. Persistence failure quarantines writes and stops unsafe scheduling, without silently resetting state.
- Broker calls and database commits cannot be atomic; do not hold transactions across requests. Recovery relies on intent, client IDs, observations, and reconciliation.
- Preserve account/environment binding, revision-linked snapshots, original evidence, append-only events, and idempotency. Conflicting duplicate IDs are refused.
- Unknown exit prices are not approximated. Evidence-linked corrections require operator verification of the relationship and values; settled fees and historical facts must not be invented.
- Maintenance uses the process lock and explicit operator approval. Backups use SQLite backup/integrity checks; copying only an active WAL database file is not a safe backup.

## Research and Data

- Decisions use completed candles and features known at the event time. UTC, session calendars, early closes, crypto boundaries, and `bar_end` are part of the data contract ([sessions](../bot/sessions.py), [data](../bot/data.py)).
- Reject incomplete, sparse, off-grid, or conflicting execution history instead of silently synthesizing validity. Keep cache provenance and validation ([history cache](../bot/history_cache.py)).
- Corporate actions are globally ordered across pages; same-day split/dividend ordering and adjustments must agree across features, simulation, and benchmarks ([corporate actions](../bot/corporate_actions.py)).
- Newly published ATR/stops cannot act on an earlier range. Mixed-timeframe sizing uses only contemporaneously available marks ([backtest](../bot/backtest.py)).
- Shared strategy rules do not make simulated fills equivalent to broker behavior. Document OHLC ambiguity, slippage, liquidity, fees/estimates, and gaps.
- Record experiment provenance, costs, seeds, parameters, and trials. Lock selection before the final report-only holdout; no repeated holdout tuning or implicit risk optimization ([research](../bot/research.py), [statistics](../bot/statistics.py)).
- `bot.experiments` is offline research only: network access solely via `prepare --allow-network`, runs read immutable checksummed datasets, parameters are locked in the registry before any holdout is simulated, and research-only strategy pairings (`validate_strategy(research=True)`) never apply to live code ([experiments](../bot/experiments/)).
- For the agreed GBP campaign, [investor context](investment-research-context.md) and the [protocol](backtesting-protocol.md) add stricter controls than the generic runner: public/user-supplied data only, no Alpaca preparation, fixed research-only risk/candidate budgets, feasibility before performance, comparable net GBP cash/passive evidence, and no holdout opening when selection fails. Existing USD/window outputs cannot substitute for unsupported GBP portfolio accounting.
- The read-only GBP normalized daily view requires a complete scoped readiness audit and declared availability by its cutoff. It preserves source provenance but does not verify historical data vintages or authorize a performance run; future consumers must enforce availability at each decision and independently establish point-in-time data.
- The read-only FX-readiness audit and complete observation view check declared availability and age only at caller-supplied clocks. A scoped pass or complete view does not establish complete event coverage, source vintages or executable FX, and must not be used as currency conversion, portfolio accounting or authorization to trade.
- `bot.campaign_data.account_fills` is only a pure synthetic GBP cash/quantity conservation contract for ordered long-only fills. It does not value positions, accrue cash, apply actions, model product increments/costs, calculate risk or returns, persist state, or authorize performance claims. It is not a trading ledger or an execution adapter.

## Separate GBP Investment Path

- Product scope is designated substantial long-term investment capital, not
  household cash/emergency budgeting; see the [current mandate](investment-research-context.md).
  The [core-plus-dynamic roadmap](decisions/0004-core-dynamic-portfolio-manager.md)
  is planned, not an implemented accounting or execution contract. The
  illustrative 80-90% / 10-20% split does not approve weights, risk or replenishment.
- Future role budgets must be distinct from asset-class targets. No experiment
  may automatically draw core funding to replace losses, promote itself, alter
  allocation/risk limits or treat core gains as alpha. Approved capital caps do
  not guarantee realised loss bounds. Exact transfer and loss rules require
  separate implementation and approval before experimental funding.
- Future standing authority must be explicit, versioned, revocable, scoped to
  accounts/products/strategies and bounded by funding, cost/risk, freshness and
  expiry/failure rules. It is not implemented by a proposal export, research
  result, browser setting or this documentation change. Current per-task broker
  approval, durable intent, reconciliation and read-only route safeguards remain.
- The [investment store](../investment/store.py) is distinct from trading and intelligence stores. Initialization is explicit; dashboard/read-only commands never create, migrate, repair, or mutate it. Evidence is append-only, duplicate-identical imports are idempotent, conflicts fail, and committed cash/quantity effects must replay after restart.
- The supported first-release history begins at an explicitly evidenced empty inception. Do not infer opening holdings, cost basis, FX, correction, corporate-action entitlement, unrecorded interest, or distribution completeness.
- Every event preserves effective time and evidence `available_at`. A mark published after its effective valuation clock cannot establish contemporaneous completeness or support an earlier decision. Incomplete held-product marks make the portfolio valuation incomplete; never present a complete subtotal or zero-filled return as complete.
- Time-weighted unitization is flow-neutral and remains linked across complete withdrawals and later refunding. A zero-equity interval does not erase prior unit price/history or manufacture a drawdown from new capital.
- External-deposit cash is part of recorded mandate value only when included by policy; it is never broker buying power. Scheduled contributions are not settled cash. Transfers between included accounts do not count as external investment performance flows.
- The allocation planner is pure and human-policy-driven. Proposals are exported with expiry/revision binding and execution disabled. Sales are limited to recorded overweight holdings at annual/drift reviews, bounded by sleeve excess, holdings and declared increments/minimums; proposed sale proceeds never fund buys. Purchases use only recorded per-account broker cash, not external deposits or hypothetical proceeds. Missing targeted products block funded orders; unavailable purchase references exclude affected buys and any supported partial proposal must disclose partial coverage. Future costs and settlement assurance are not implemented.
- Investment v1 store schemas are validated against the original tables, implicit index and immutable triggers before integrity checks or data queries can execute untrusted schema expressions. Altered or extra objects are refused; readers never repair them.
- Manual review acknowledgements are append-only, bound to an unchanged, previously committed revision and a complete, fresh recorded valuation; they are not trade approval. Monthly and annual clocks are separate; an annual acknowledgement also counts as monthly, while five-point drift remains a trigger. Acknowledgements do not alter cash, holdings, valuation clocks or performance; later evidence cannot be backdated before them. Invalid historical attestations fail replay rather than being silently repaired.
- A cash-only confirmation may refresh the recorded valuation clock only when every included account has an exact matching GBP balance and evidence available at that clock; an unheld-product price mark cannot refresh that clock. It never changes money or repairs discrepancies; it is an operator attestation, not independent proof of balances or settlement. Prior journal evidence must have been available by either an acknowledgement or confirmation clock.
- `investment.simulation.simulate_synthetic_fixture` is a pure engineering harness intended only for caller-supplied synthetic GBP bars, next-session-open buy-only allocations, contribution/withdrawal unitization, explicit synthetic distribution events, and assumed proportional costs. Synthetic withdrawals are paid only from available cash; they cannot trigger sales, and insufficient cash fails closed. Preserve unit-value continuity through complete cash redemptions and later contributions. Synthetic distributions use holdings immediately before modeled ex-open fills, create a receivable included in portfolio and contribution-allocation target valuation until their later modeled pay-close, and convert that receivable to spendable cash before close-time external flows. Reject distributions unavailable by ex-open, unheld ex-open products, and a same-product split at ex-open. Caller prices must exclude the distribution adjustment; the harness cannot verify this basis or the source/entitlement. It supports only synthetic splits effective at modeled opens and available no later than their effective time; unsupported fractional-increment outcomes fail. Explicit synthetic cash-rate events (annual decimal 0-1) require a first-open rate, may change only at modeled opens/closes, and accrue by simple ACT/365 only after being available. Overnight interest uses the prior rate; open-effective rates apply after that accrual. This is not a cash-product terms or rate model. The harness does not verify input provenance and is not connected to product datasets, the investment store, or a CLI; its output cannot support product-performance claims. It omits sales or liquidation, taxes/withholding, actual cash-account conventions, FX, fixed costs, spreads/slippage, and actual calendars/execution. Product selection, legal/access eligibility, licensed data, savings terms, historical comparisons, broker behavior, and profitability require separate evidence and approval.

## Advisory and Shadow Intelligence

- Portfolio intelligence may support automated awareness and hypothesis creation
  under approved collection capabilities. Future calendar/AI-derived dynamic
  trading features require separate prospective signal/failure-policy approval;
  existing news/LLM exclusions and advisory-only runtime remain unchanged.
  External text is data, not executable authority; private account evidence must
  not be sent to hosted AI. No intelligence outage forces core liquidation or
  changes independent protective recovery.
- [Market intelligence](decisions/0002-market-intelligence.md) is a separate advisory/shadow development stream. Master enablement and AI default off; hypothetical decisions never change trading (`affects_trading=False`). Unknown/stale coverage is uncertainty, not an order permission, halt, or evidence of protection.
- Preserve source revisions, contemporaneous availability, provenance and explicit coverage. News receipt and validated AI completion determine availability; publication time alone does not. Today's model on historical headlines is not causal out-of-sample evidence.
- Dedicated intelligence storage/control history is separate from the trading ledger and lock. Preserve identity, append-only evidence, bounded reads, exact revision/expiry checks and atomic application. No implicit initialization/repair by readers or development access to operational stores.
- Requested settings, service-applied receipts, and bot-observed revisions are distinct. Local application or a schema does not establish active collection or bot acknowledgement. Trading/account APIs remain read-only; recorded-mode intelligence controls require explicit enablement and authentication. Controls are limited to CSRF-protected analysis settings; synthetic demo sessions use isolated temporary storage.
- Implemented opt-in feeds, providers, controls and the best-effort observer do not authorize operational service startup, external calls or changes to trading. Preserve broker-call parity, exits, stops, risk gates, quantities and causal candle consumption. News/LLM trading signals remain excluded from the GBP campaign.

## AI Development Boundary

- No secret access, operational-state mutation, broker calls, or trading by default. Authenticated paper work requires explicit task-specific approval and operator-owned credentials.
- Offline checks isolate and restore cached broker settings and use temporary state, mocked broker boundaries, and blocked Python socket access. This is not an OS sandbox and does not contain malicious/native/subprocess code.
- Read-only review cannot prove runtime behavior. Report hypotheses as hypotheses and executed evidence as executed evidence.