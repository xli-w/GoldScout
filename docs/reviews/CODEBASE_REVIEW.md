# Trading Bot Review

Review date: 2026-11-12. Scope: all application modules, configuration, dependency declarations, and README; selected public broker documentation and research.

## Implementation Update: Findings 1-5

Implemented on 2026-11-12 after this review. The numbered findings below describe the original snapshot; their old
line anchors are historical and may have moved.

1. Partial exit accounting retains and re-protects residual quantity and requires broker flatness before returning
	full-close success. Exit fill quantities/notionals are accumulated to avoid repeated accounting during recovery.
2. Exit intent is persisted, cancellation waits for terminal broker states, and failed exits attempt safe recovery.
	Unresolved order/protection states disable new entries. Recovery resolves client IDs separately from broker IDs.
3. Monitoring verifies protective order status, type, side, quantity and price; follows replacement IDs; repairs
	canceled/missing coverage outside regular trading hours; and reconciles partial stop executions. Restart clears
	persisted verification until the order is checked again.
4. Simulation now separates chronological open execution events from completed-candle information releases,
	batches shared-timestamp marks, and rejects future portfolio marks. OHLC range-stop executions are recorded at
	candle close, not retrospectively at candle start; this remains coarse simulation rather than tick replay.
5. Simulation deducts crypto asset-denominated entry fees and cash-denominated exit fees, reports net P&L and fee
	totals, and preserves activated-but-unfilled crypto stop limits. Configurable software fallback is checked at
	candle close; broker-only mode can model downtime. Live trade logs include allocated fee estimates, labeled as
	estimates rather than settled broker fees. Actual activity/fee reconciliation remains a follow-up requirement.

Thirteen focused regression checks passed, plus legacy CSV header migration validation. No authenticated broker
calls or orders were made. Data libraries were installed through a process-local proxy without storing credentials
in the repository. Broker SDK installation remains blocked by its pinned PyYAML build requiring Microsoft C++
Build Tools in the current Python 3.14 environment. Live-order tests therefore use a stub for SDK import only;
simulation checks use real pandas/NumPy. Use the documented Python 3.10/3.11 setup for broker integration validation.

These repairs do not certify live readiness: findings 6-15, full broker integration, delayed fee reconciliation,
transactional fill-ledger persistence, and fine-resolution execution modeling remain outstanding.

## Findings First

### Implementation Update: Findings 11-15 (2026-11-12)

Remaining review findings now have implemented safeguards and reporting paths:

- **11:** Exact HTTPS endpoint allowlist, finite/positive risk inputs, strategy ownership/threshold checks,
	broker-metadata crypto increments and limits, protective tick rounding, and explicit refusal of unsupported
	fractional GTC protection. Fractional market exits no longer silently truncate quantity.
- **12:** Day roll before entry gates/realization, broker opening-equity baseline, deduplicated cash transfers,
	and a loss latch that resets only on a new configured calendar day. Failed cash-flow verification fails closed.
- **13:** SQLite atomically commits state and exit rows; CSV is a derived atomic export with one-time legacy import.
	Durable exit/cumulative-fill identities and broker audit fields survive restart. Unknown exit prices remain unknown
	and block entries. Process locking prevents duplicate instances. JSON snapshots are compatibility exports.
- **14:** Default tuning disabled; explicit two-year-minimum research uses three expanding validation folds and
	records every candidate. A multiple-trial-adjusted block-bootstrap gate supplements trade-count and fold gates.
	Final 20% is reporting-only after parameters are locked; full-window reruns remain exploratory. Flat-start fold
	boundary policy is explicit. This is not formal proof against all overfitting or regime instability.
- **15:** Excess-return and downside metrics, CAGR/Calmar, expected shortfall, drawdown duration, turnover/exposure,
	fee/funding assumptions and passive comparisons exported in JSON. Forced liquidations excluded from natural trade
	statistics. Risk-matched passive comparison is ex-post/descriptive and gross of costs, not an investable strategy.

Residual limits: broker fee/activity settlement is still not fully reconciled, prior-day realized totals may need
historical restatement, cash-flow types beyond deposits/withdrawals require explicit support, broker session equity
is not exact midnight crypto equity, and fractional GTC stops are intentionally unsupported. Fine-resolution market
execution, authenticated integration, complete corporate-action history, tax modeling and broad factor attribution
remain unverified/not implemented. The original research recommendations are experiments, not automatically adopted
trading strategies. These changes must not be interpreted as a profitability or live-readiness certification.

### Implementation Update: Findings 6-10 (2026-11-12)

The following changes address the next review findings; the original numbered findings below remain historical:

- **6:** Removed SDK total-history limits; added cache provenance/checksums, rolling-request freshness and coverage
	checks, common test end dates, required warm-up, and execution-session/gap validation. Legacy or stale caches refresh.
- **7:** Added `exchange-calendars` XNYS regular-session minute aggregation with open-anchored candles and early-close
	end times. Live and fetched historical features share this policy. Historical minute execution data supports
	intermediate stops, sampled trailing extremes and mean-reversion exits. Synthetic sessions exclude holidays and
	include overnight gaps. Minute OHLC is still an approximation, not exact live polling/tick execution.
- **8:** Added paginated action retrieval, validated ordinary USD dividends/splits, causal feature adjustment,
	split-adjusted holdings/stops, dividend receivables and short liabilities, payment-date settlement, and split/dividend
	benchmarks. Unsupported actions fail rather than silently bias results. Provider delays, due-bill edge cases,
	cash-in-lieu, and historical completeness are explicitly not certified.
- **9:** Persisted entry intent before submission; attached equity OTO stops; added ongoing entry recovery and
	unmanaged-position detection. Adoption is opt-in for configured symbols, independent of history availability,
	durable before stop operations, and preserves valid existing broker stops. Side mismatches retain existing orders
	and block entries. Recognizable quantity/cost-basis split changes adjust local risk levels. Crypto/partial entry
	crash windows cannot be eliminated entirely by this broker API.
- **10:** Added explicit SDK connect/read timeouts, bounded retries/pagination deadlines, disabled SDK-internal retries,
	shortened order waits, incremental live history cache, and separate read-only worker/API sessions. The main loop
	remains the sole order writer, monitors first, rechecks monitoring between strategies, and reports symbol failures.
	Request deadlines are checked between operations; a slowly streaming response is not an absolute process deadline.

Validation: 24 focused offline tests passed with real SDK/pandas dependencies in the currently selected Python 3.11.9
environment, plus one synthetic CLI smoke run. No authenticated data queries or broker orders were made. The active
environment is now `.venv311`, so the earlier Python 3.14 dependency-build blocker no longer describes this test run.
Findings 11-15, actual broker integration, settled fee/activity reconciliation and transactional execution-ledger
accounting remain outstanding. See README for ownership settings and simulation limitations.

**Verdict: keep this system in research/paper mode. It has material execution defects and a mixed-timeframe backtest chronology bug. Its current results cannot establish a profitable live edge.**

Evidence labels below distinguish **reproduced** (actual project methods exercised with isolated mocks), **source-confirmed** (directly visible control flow), **documentation-confirmed** (external API/SDK behavior), and **research hypothesis** (requires new experiments). Severity reflects either risk to capital or risk of making investment decisions from invalid results.

### 1. Critical: partial exits are treated as complete liquidation

Location: [bot/portfolio.py](bot/portfolio.py#L271). Evidence: reproduced.

`close_position()` accepts any positive `filled_qty`, records P&L for the entire local `pos.qty`, removes the position, and returns success. A canceled exit that sold 70 of 100 shares is logged as closing 100 shares. The remaining 30 shares are no longer monitored; runtime reconciliation does not adopt missing local positions. A reversal can consequently proceed while the old position still exists.

Remedy: process fill deltas, retain residual quantity, account for partial realized P&L, re-protect the residual, and return full-close success only after broker-confirmed flatness. Reconcile both order executions and position quantities.

### 2. Critical: failures during exit can remove stop protection

Location: [bot/portfolio.py](bot/portfolio.py#L271), [bot/portfolio.py](bot/portfolio.py#L152). Evidence: reproduced exception path; source-confirmed cancellation protocol.

The protective order is canceled before the market exit is submitted. If submission or fill polling raises, execution bypasses the stop re-arm branch entirely. The caller logs an exception, but the position can remain without its resting stop. Cancellation uses a 0.5-second sleep rather than confirmed terminal order state, so reserved quantity and cancel/fill races are also unresolved.

Remedy: explicit cancellation/closing/protection states, confirmed cancellation, broker refresh after every ambiguous outcome, and recovery on every failure path. Block new entries and raise an operational alert whenever protection cannot be verified. Do not assume a blind `finally` stop submission is safe while a closing order can still fill.

### 3. Critical: persisted stop identifiers are mistaken for live protection

Location: [bot/portfolio.py](bot/portfolio.py#L356), [bot/portfolio.py](bot/portfolio.py#L364). Evidence: reproduced.

Reconciliation only checks symbol presence. The monitor never verifies that a stored stop is still active, correctly sided, or sized to the remaining position. If price is unchanged, it does not even attempt replacement. A canceled, expired, rejected, partially filled, or externally modified stop can therefore go unnoticed indefinitely. Alpaca documents automatic aging of GTC orders after 90 days [S1].

Remedy: periodically assert that each managed position has confirmed protective coverage, reconcile replacement chains, inspect order status/remaining quantity, and repair or escalate missing protection. Continue these health checks outside equity trading hours even though execution eligibility differs.

### 4. Critical for research: mixed-timeframe simulation uses future account information

Location: [bot/backtest.py](bot/backtest.py#L138), [bot/backtest.py](bot/backtest.py#L181), [bot/backtest.py](bot/backtest.py#L271). Evidence: reproduced.

Events are ordered by candle **end**, but pending orders execute at that candle's **start**. A 14:30-14:45 SPY candle is processed before the 14:00-15:00 BTC candle. The BTC entry timestamped 14:00 can use equity already reflecting SPY's 14:45 close. The isolated reproduction observed $102,000 instead of the $100,000 implied by the earlier SPY mark.

This contaminates sizing, exposure headroom, and event ordering; daily boundaries and correlation decisions are also vulnerable to inconsistent timing. This is not the usual indicator-window lookahead: it is a portfolio-clock defect.

Remedy: a single chronological execution clock with distinct open, execution/monitor, and completed-bar events. Prefer fine-resolution execution data shared across instruments, with higher-timeframe features released only at their true availability time. Same-time events need a deterministic policy that does not accidentally privilege the symbol list order. Add a regression test that rejects any mark/feature timestamp later than the execution timestamp.

### 5. High: crypto economics and stop-limit execution are misrepresented

Location: [bot/backtest.py](bot/backtest.py#L69), [bot/backtest.py](bot/backtest.py#L181), [bot/backtest.py](bot/backtest.py#L271), [bot/portfolio.py](bot/portfolio.py#L311). Evidence: source- and documentation-confirmed.

Simulation defaults to zero commission and 0.05% slippage per fill. Alpaca's published entry-tier crypto fees are 0.15% maker and 0.25% taker per side [S2]. Market entry plus market exit therefore costs approximately **0.50% of traded notional in fees alone**, before spread/slippage. The default simulated slippage adds another approximately 0.10% round trip; it does not replace those fees. Tier and actual order liquidity classification must be modeled.

Crypto stop-limit orders are modeled as guaranteed stop-market fills, including gaps. In reality, a sell stop at 100 with a limit at 99 cannot execute against a market wholly below 99. Software liquidation helps only when the bot and API are functioning. Narrowing the limit buffer would increase, not reduce, the likelihood of non-execution.

Live trade P&L also excludes explicit fees. Trusting post-fee quantity is useful, but does not substitute for accounting for gross entry cost, asset-denominated fees, and exit proceeds.

Remedy: asset-specific maker/taker fees, bid/ask execution, fee-denominated quantity handling, activated-but-unfilled stop-limit states, and net account-activity reconciliation. Evaluate crypto viability only after these changes.

### 6. High: historical requests are capped at 10,000 total bars

Location: [bot/data.py](bot/data.py#L32), [bot/data.py](bot/data.py#L35), [bot/backtest.py](bot/backtest.py#L446). Evidence: source- and SDK-confirmed [S5].

In SDK v3.2.0, `_data_get()` tracks `total_items` and stops when the supplied limit is exhausted. `limit=10000` is not merely a page-size setting. Longer hourly BTC histories exceed this cap at about 417 days; longer intraday equity histories can also exceed it. The requested research horizon is then not necessarily the returned horizon.

`backtest_window()` uses the latest ending instrument rather than verifying common coverage. An instrument ending early can appear inactive while other instruments continue, invalidating comparisons. Cache files do not record feed, adjustment, coverage, or freshness, and a longer requested window can silently reuse a shorter cache.

Remedy: unrestricted SDK pagination or explicit time chunks; validate start/end coverage, warm-up sufficiency, gaps, monotonicity, and overlap. Store cache provenance and reject insufficient data rather than silently continuing.

### 7. High: live and simulated sessions/monitoring do not match

Location: [bot/data.py](bot/data.py#L40), [bot/main.py](bot/main.py#L91), [bot/backtest.py](bot/backtest.py#L181), [bot/backtest.py](bot/backtest.py#L476). Evidence: source-confirmed.

Live equity entries and software monitoring are gated by the broker market clock. Historical bars are not filtered by an exchange calendar, and the backtester executes on every supplied bar, potentially including extended hours [S3]. Broker-native four-hour aggregation also has no explicit session convention in this project.

Synthetic four-hour bars start at 09:30 and 13:30 but are both treated as four hours long; the second is assigned a 17:30 end despite the regular session ending at 16:00. Business-day generation includes exchange holidays. Synthetic opens equal previous closes, so the smoke generator does not meaningfully test overnight gaps.

Live mean-reversion exits are polled roughly once per minute; simulation checks only candle-close price and exits at the next open. Live trailing extremes use sampled broker prices; simulation uses full candle highs/lows but only updates stops after the candle. Neither is equivalent, and the resulting bias need not have one consistent direction.

Remedy: define RTH/extended-hours policy and four-hour bar boundaries, use an exchange calendar with holidays/early closes/DST, and simulate the actual monitor/exit policy on finer data. Treat synthetic history strictly as smoke input.

### 8. High: raw equity histories have no corporate-action accounting

Location: [bot/data.py](bot/data.py#L35), [bot/backtest.py](bot/backtest.py#L271), [bot/backtest.py](bot/backtest.py#L353). Evidence: source-confirmed.

Raw bars across splits distort EMAs, ATR, and returns; simulated holdings are not split-adjusted. Dividends and short dividend liabilities are absent. USO's issuer documents a 1-for-8 reverse split in April 2020 [S9], making this concrete for a longer crisis-period test.

Remedy: separate point-in-time adjusted feature data from tradable execution prices and process split/dividend events in the account ledger. Do not simply replace raw bars with fully adjusted prices and assume that fills and share accounting are solved. Expense/roll effects already embedded in USO's share-price history should not be deducted twice.

### 9. High: recovery has unprotected crash windows and incomplete adoption

Location: [bot/portfolio.py](bot/portfolio.py#L203), [bot/portfolio.py](bot/portfolio.py#L327), [bot/portfolio.py](bot/portfolio.py#L452), [bot/main.py](bot/main.py#L77). Evidence: source-confirmed.

Entry fills before stop placement and durable position persistence. A crash in that interval leaves exposure without confirmed protection. Recovery fetches historical data and computes ATR before adopting an unknown position; empty data, invalid ATR, or failed stop placement can abort startup before management resumes. Adoption cancels existing orders before successfully replacing protection, and can overwrite tighter existing protection with a reconstructed stop.

Only configured symbols are adopted, only at startup. Unknown broker positions are not comprehensively handled during ongoing reconciliation. Existing local positions are not updated for broker quantity or side changes.

Remedy: persist order intent before submission, recover using execution IDs, retain confirmed valid protection during adoption, validate data independently, and run bidirectional ongoing reconciliation. Use a dedicated account or an explicit ownership policy; do not silently take over manual investments. Evaluate broker-supported attached protection, with tests for partial entry fills and replacement limitations rather than assuming a bracket/OTO removes every risk.

### 10. High: long broker calls delay all other monitoring

Location: [bot/main.py](bot/main.py#L167), [bot/main.py](bot/main.py#L179), [bot/utils.py](bot/utils.py#L25), [bot/portfolio.py](bot/portfolio.py#L138). Evidence: source-confirmed; actual delays depend on API behavior.

The scheduler is single-threaded. Fill waits, repeated API retries, and long history fetches block the monitor and heartbeat. The advertised 30-second monitoring interval is consequently not a maximum safety latency. SDK v3.2.0's request path does not supply an explicit network timeout [S5]. Per-symbol strategy errors are swallowed, so the task-level failure counter does not capture repeated symbol failures.

Remedy: bounded request deadlines, monitoring priority, separate data/execution health reporting, and supervised order-state updates. If concurrency is introduced, preserve a single authoritative execution writer or proper synchronization. There is no present simultaneous-strategy sizing race to fix: the current loop is serial.

### 11. Medium: numeric, configuration, and asset-rule validation are incomplete

Location: [bot/risk_manager.py](bot/risk_manager.py#L39), [bot/risk_manager.py](bot/risk_manager.py#L63), [bot/portfolio.py](bot/portfolio.py#L171), [bot/strategies/mean_reversion.py](bot/strategies/mean_reversion.py#L22), [config.py](config.py#L23). Evidence: NaN sizing reproduced; remaining items source-confirmed.

`min(equity, price, atr) <= 0` does not reject NaN; a NaN ATR reaches `math.floor()` and raises. Zero quantity can divide by zero in stop calculation. Negative buying power/configuration and nonfinite prices are not systematically validated. Stop rounding can slightly increase planned loss beyond the unrounded budget.

Crypto quantity is always floored to six decimals and stop prices to two, without consulting current asset increments, minimum size, and per-order maximums [S2]. Adopted fractional equities are rounded to whole-share exit quantities, potentially leaving a residual. Adding strategy symbols can fail through missing instrument or threshold keys. Paper detection uses substring matching rather than parsed exact HTTPS host validation.

Remedy: fail-fast typed configuration plus finite/positive input checks, asset metadata validation, decimal/tick-aware rounding with final dollar-risk verification, and an endpoint allowlist. These are defensive gaps, not evidence that the default endpoint guard actually submitted a live order.

### 12. Medium: the daily-loss breaker is not a reliable daily control

Location: [bot/portfolio.py](bot/portfolio.py#L410), [bot/portfolio.py](bot/portfolio.py#L436), [bot/main.py](bot/main.py#L140). Evidence: source-confirmed.

The daily baseline rolls during heartbeat/startup, not when the gate is checked. The first evaluation after midnight can use the previous day's baseline; closing a position before the heartbeat can also assign its realized P&L to the wrong day. Restart after a multi-day outage uses stale `last_equity` as the new daily baseline. Deposits/withdrawals are treated as performance.

The breaker is not latched: entries can resume if equity recovers. It blocks entries, not exits or all losses; this behavior is documented and is not itself a bug. A 3% setting is not a guaranteed maximum daily loss.

Remedy: authoritative timezone-aware baseline and cash-flow accounting, day-roll before gates/realization, a documented latch/reset policy, and separate drawdown/exposure actions. Do not automatically liquidate during an outage without a tested order protocol.

### 13. Medium: crash-safe state is not a crash-safe accounting ledger

Location: [bot/portfolio.py](bot/portfolio.py#L311), [bot/portfolio.py](bot/portfolio.py#L440), [bot/trade_logger.py](bot/trade_logger.py#L26). Evidence: source-confirmed.

Trade CSV append occurs before position removal/state replacement. A crash between them can cause duplicate trade logging after restart. CSV rows have no fill/order identity for deduplication, and daily CSV rewrites are not atomic. Atomic JSON rename helps, but does not make broker submission, CSV append, and local state one transaction.

Unknown exits are priced at an approximate stop and stamped with reconciliation time, rather than the actual fill price/time. Stop replacement chains, partial executions, external closes, and fees can therefore produce invented realized P&L or wrong daily attribution.

Remedy: a small SQLite execution ledger with unique broker fill IDs, durable intent/state transitions, and CSV as derived output. Record unknown data as unknown and retrieve account activities instead of treating the stop level as an actual execution.

### 14. Medium: parameter validation is statistically weak and reuses the holdout

Location: [bot/backtest.py](bot/backtest.py#L56), [bot/backtest.py](bot/backtest.py#L398), [bot/backtest.py](bot/backtest.py#L638). Evidence: source-confirmed.

Five in-sample and three validation trades are permitted. Six months normally provides too little regime diversity for slow trend strategies. Tuning is triggered using Sharpe on the full window, including the eventual holdout; the holdout then decides whether the selected candidate is retained. The adjusted full-window rerun is consequently not untouched out-of-sample evidence. The program warns that it is optimistic and does not automatically modify configuration, which is appropriate.

Each validation run starts flat and training trades are force-closed at the split, which changes a continuous strategy's behavior at boundaries. There is no trial registry, uncertainty estimate, or multiple-testing adjustment [S8]. Simply raising a minimum to 30 trades would not establish validity either: trades may be dependent and concentrated in one regime.

Remedy: predeclared rolling/expanding walk-forward protocol, separate validation and final untouched test, explicit boundary/position policy, dependent-return bootstrap confidence intervals, and a record of every tested variant. Use deflated/probabilistic Sharpe or comparable selection-adjusted analysis as supporting evidence, not a profit guarantee.

### 15. Medium: reporting is insufficient for investment comparison

Location: [bot/backtest.py](bot/backtest.py#L323), [bot/backtest.py](bot/backtest.py#L336), [bot/backtest.py](bot/backtest.py#L353). Evidence: source-confirmed.

Sharpe uses zero risk-free rate; cash yield, financing, and borrow/dividend costs are absent. End-of-test forced closes inflate the number of completed trades and must be labeled separately. Configured nonzero commissions affect equity but are not included in individual trade P&L. Drawdown uses sampled account points rather than guaranteed intraday extrema, and intermediate same-time symbol updates are not a simultaneous portfolio mark.

Per-instrument buy-and-hold percentages do not supply a comparable combined, cost-aware, risk-matched benchmark. Add CAGR, excess-return Sharpe, Sortino, Calmar, expected shortfall, drawdown duration, turnover, exposure, cost decomposition, and beta/factor attribution. Weekday-only daily equity returns are conventional; Friday-to-Monday returns are not by themselves a Sharpe calculation bug.

## What Is Already Sound

- Strategy and risk modules are small and separable; the simulator reuses those abstractions.
- BTC channel highs/lows and average volume use `shift(1)`, avoiding inclusion of the current candle in its prior breakout threshold.
- Ordinary single-symbol signals use completed candles and next-bar fills. The mixed-timeframe account clock still needs repair.
- Whole-share equity rounding, gross/notional caps, shortability checks, crypto cash buying power, and a live confirmation requirement are useful safeguards.
- The hard-stop distance clamp is valid for finite positive inputs. Fewer shares from a partial entry reduce planned dollar loss; they do not inherently increase risk.
- Software trailing stops ratchet rather than loosen. Gaps can nevertheless exceed planned loss, and broker protection must be verified separately.
- Synthetic results and adjusted parameter results are explicitly caveated in the documentation.

## Improvements and Simplifications

1. Keep the pure strategy interface. Introduce only a shared chronological decision/risk layer and broker-specific execution adapter, so backtest and live do not duplicate subtly different gating logic.
2. Separate ledger/reconciliation from order execution instead of expanding `Portfolio` indefinitely. SQLite is justified here by concrete idempotency and recovery requirements, not by scale.
3. Fetch incremental bars and cache completed features; repeatedly fetching 450 days every five minutes is unnecessary. Periodically rebuild/check the cache and preserve sufficient causal warm-up.
4. Replace arbitrary sleeps with bounded order-state waits/streamed trade updates. Stream market data where useful, but retain supervised REST reconciliation as a recovery mechanism.
5. Replace mutable, loosely checked configuration dictionaries with validated configuration objects. Validate strategy ownership, timeframe units, threshold coverage, and risk ranges at startup.
6. Lock a tested runtime and dependency set. The selected environment is Python 3.14.3 with only pip installed, whereas [README.md](README.md) specifies Python 3.10/3.11. Pylance reports missing imports and type issues; missing runtime libraries limit the reliability of some inferred diagnostics. Do not equate every editor type warning with an observed trading bug.
7. Verify Windows timezone support in a clean dependency install. The review's isolated backtest import failed with `ZoneInfoNotFoundError` and missing `tzdata`; direct timezone support is not declared in [requirements.txt](requirements.txt). Pandas may bring timezone data transitively, so the fully installed environment still needs checking.
8. Add focused tests and CI before refactoring: order state transitions, broker/ledger agreement, partial fills, stale data, crash restart, precision, calendar boundaries, and mixed-timeframe causality. No test suite was supplied in the reviewed project structure.

## Strengthening the Investment Strategy

### Define the objective before optimizing

"Highest return" without a risk constraint favors excessive leverage and eventual ruin. A defensible goal is robust **net compound growth**, subject to maximum acceptable drawdown, volatility, liquidity, leverage, and operational risk. The user's capital, loss tolerance, time horizon, tax jurisdiction, data budget, and permissible instruments have not been specified; there is no justified optimal allocation or risk setting yet.

The relevant quantity is not win rate or the prettiest backtest:

$$
E[\text{net trade P\&L}] = p E[W] - (1-p) E[L] - E[\text{all trading costs}].
$$

Diversification and slower turnover are research priorities, not promises of higher absolute returns. There is no credible way to certify this as the best possible investment bot.

### Priority A: broaden diversified, lower-turnover trend research

The stronger published evidence concerns trend across many markets and months, not one-hour BTC plus four-hour gold/oil alone. Moskowitz, Ooi, and Pedersen examine 58 futures/forward markets; AQR's century study extends the historical regime set [S6]. These are institutional-market studies, not evidence that the current ETF/crypto implementation has the same edge.

Test a small predeclared family of daily/weekly trend rules across liquid instruments representing equities, government bonds, gold, and broad commodities; international equities may help but can share common equity beta. Treat SPY/QQQ as overlapping exposures, not two independent sources of alpha. Consider cash/short-duration Treasury exposure as a tested defensive allocation, subject to instrument and account eligibility.

Compare a single trend horizon against a small fixed ensemble, such as short and long horizons, measuring incremental diversification and turnover. Do not assume more horizons always help. First use ETFs already supported by the account; managed-futures research requires additional futures/FX infrastructure and cannot be replicated merely by adding tickers.

**Specific current limitation:** [bot/strategies/trend_following.py](bot/strategies/trend_following.py#L31) only enters on a fresh crossover. After a stop-out it can remain flat throughout an otherwise persistent trend until another crossover. That is a strategy choice, not necessarily a bug. Test crossover-only against trend-state entries with cooldown/hysteresis and explicit re-entry criteria; unconstrained re-entry can create costly repeated stop-outs.

### Priority B: replace the binary correlation rule with portfolio risk allocation

The existing rule blocks BTC longs only when both SPY and QQQ are already long. It is order-dependent and ignores how large the positions are. It does not control same-factor exposure among other symbols or across opposite-side combinations.

Begin with transparent asset-class/strategy budgets and total planned stop risk. Then test bounded volatility targeting and a stable covariance estimate, with shrinkage and stress correlations. Portfolio volatility depends on position weights and covariance:

$$
\sigma_p = \sqrt{w^{\mathsf T}\Sigma w}.
$$

Use covariance estimates from aligned returns on a consistent clock, and validate their stability. ATR sizing on a 15-minute equity chart and on a four-hour commodity ETF chart does not equalize annualized volatility, overnight gap exposure, or portfolio risk. Moreira and Muir provide evidence motivating volatility management [S7], but their factor results are not a universal guarantee for these strategies.

Do not start with unconstrained mean-variance optimization or full Kelly sizing: expected-return estimates here are far too uncertain. Leverage and risk targets must be chosen from stress tests and user constraints, not assumed from one backtest.

### Priority C: make stop distance strategy-dependent and cost-aware

All current strategies start with a one-ATR hard stop. The two-/three-ATR trail only becomes tighter than that hard stop after a favorable move. A one-ATR adverse move can be normal noise for a trend system, and frequent stop-outs can eliminate the intended trend payoff.

Test a broader structural/volatility stop while reducing quantity so planned dollar risk remains unchanged:

$$
q = \frac{E r}{m\,ATR},
$$

where $E$ is equity, $r$ is the permitted trade risk fraction, and $m$ is the stop-distance multiplier. The existing hard-stop clamp must be updated consistently; merely changing the stop multiplier while retaining $q=Er/ATR$ would cause the clamp to undo the intended wider stop. Include fee/slippage and gap reserves in the risk budget and retain notional/exposure caps.

Evaluate wider stops against reduced turnover, drawdown, time-in-market, and tail loss, not just in-sample Sharpe. Wider stops are not automatically better.

### Priority D: treat intraday mean reversion as a smaller, conditional experiment

The current SPY/QQQ rule fades deviations of price level from a rolling SMA. A rolling price z-score is not a calibrated normal-distribution probability, and price is not generally stationary around that mean. The rule can repeatedly short strong rallies or buy persistent declines.

Predeclare a few comparisons: long-only versus symmetric long/short; no filter versus a simple lagged daily trend/volatility filter; rolling SMA versus session VWAP when consolidated data is available. Test maximum holding time, re-entry cooldown, and regular-session-only trading as separate ablations. A scheduled-event filter is a hypothesis requiring a reliable point-in-time event calendar.

For each prospective entry, compare estimated distance to the exit target with round-trip costs and stop distance. Record maximum favorable/adverse excursions, holding-time distributions, performance by time-of-day, and long/short attribution. Do not keep adding RSI, MACD, or other correlated indicators without incremental out-of-sample evidence.

### Priority E: make BTC survive a fee and liquidity hurdle

The 20-hour breakout and 1.5x volume filter have no established edge in this repository. Alpaca's crypto bars can use quote-midpoint prices when no trades occurred and volume is zero; its volume reflects its own venue, not the entire crypto market [S3].

Compare hourly breakout with slower four-hour/daily trend variants, and test whether the volume filter adds net value. The cost hurdle should use actual spread and fee tier. Add quote freshness, spread/impact limits, asset-size rules, and a tested software response to activated-but-unfilled stop limits. Consider another execution venue only after validating eligibility, custody, fees, market data, and stop behavior, not simply because a native stop is advertised.

It is entirely possible that the best decision is to omit BTC or omit intraday trading after realistic costs. A research system must be able to reject an unprofitable strategy.

### Priority F: assess USO as the actual product being traded

USO is a futures-based commodity pool, not spot crude oil. Its issuer describes rolling benchmark futures and warns it is not a direct oil-market proxy [S9]. Roll conditions, fund structure, historical allocation changes, and tax treatment can matter. Test USO itself with corporate actions, or compare a broader commodity instrument; do not substitute spot-oil backtests as proof of USO profitability. Consult appropriate tax advice for the investor's jurisdiction.

### Additions to defer

Neural networks, reinforcement learning, LLM news trading, and alternative-data stacks are not the next step. They increase data, latency, validation, and selection-bias requirements. A later supervised filter could be considered only with point-in-time features, chronological/purged validation where labels overlap, cost-aware labels, and measurable net improvement over simple baselines. Do not use martingale/grid doubling or unconditional averaging down to manufacture a high win rate.

## Research and Deployment Gates

1. **Execution gate:** every mocked partial-fill, rejection, lost-response, cancel/fill race, and restart case preserves broker/ledger consistency and either verified protection or a clearly escalated state. Add unique-intent/fill identities and a single-process lock so accidental duplicate bot instances cannot trade independently.
2. **Data gate:** common coverage, documented feeds/sessions, exchange calendars, fresh quotes, finite OHLCV, corporate actions, and multi-timeframe causality assertions all pass. Use a fully installed, locked runtime.
3. **Simulation gate:** realistic asset-specific costs, partial executions, non-executing stop limits, monitor cadence, financing, and account cash rules are modeled. Reconcile simulated P&L against hand-worked fixtures.
4. **Evidence gate:** use multiple years/regimes where trustworthy data exists, including crises and sideways markets, then rolling walk-forward and one final untouched test. Report uncertainty and all tried variants. Do not extrapolate synthetic results or publish adjusted training-window returns as expected returns.
5. **Benchmark gate:** compare net performance with cash/T-bills, SPY total-return buy-and-hold, a diversified passive allocation, and a risk-matched passive benchmark. Report whether returns come from alpha, market beta, leverage, or cash exposure. Include actual costs/yields and appropriately modeled tax scenarios.
6. **Robustness gate:** modest parameter changes, later fills, materially higher costs, missed bars, outages, adverse gaps, and higher correlations must not destroy the investment case. Avoid dependence on one instrument or a few trades.
7. **Forward gate:** record paper signals, executable quotes, order events, slippage, and state recovery under unchanged parameters. Paper trading validates integration but is not proof of live execution economics or profitability.
8. **Capital gate:** only after the prior gates, consider a constrained small live pilot with independently checked risk limits, alerts, and a tested kill switch. Scaling follows evidence of net behavior, not a calendar deadline or a good recent Sharpe.

## Verification Performed

- Actual `Portfolio.close_position()` reproduced the 70-of-100 partial-exit accounting/management defect.
- Actual `Portfolio.reconcile()` retained local quantity 100 when broker quantity was 30.
- Actual `Portfolio.manage_positions()` did not query/re-arm a canceled stop at unchanged price.
- Actual `Portfolio.close_position()` canceled protection and did not re-arm after a mocked submission exception.
- Actual `RiskManager.size_position()` raised `ValueError` for a NaN ATR.
- Actual `Backtester._on_bar()` and `_equity()` reproduced future SPY equity being used for an earlier BTC entry.
- A partial-entry risk-clamp check disproved the hypothesis that a smaller fill inherently increases planned loss.
- Pylance workspace diagnostics and configured-environment inspection were collected. The selected venv contains only pip, so full real-library backtesting and broker integration were not run. Mocked checks intentionally stubbed missing third-party modules; the chronology check also mocked timezone lookup after observing its import failure.
- Public API and SDK documentation was inspected. No broker orders, authenticated historical downloads, dependency installs, or production-code modifications were performed. This document is the review deliverable, not an implemented fix or a performance certification.

## Sources

Public pages accessed during the review; fee/asset rules and documentation can change. Research summaries below are paraphrases, not reproductions of the papers.

- **S1:** [Alpaca order documentation](https://docs.alpaca.markets/docs/orders-at-alpaca): order lifecycle, pending cancellation/replacement, stop-limit non-execution, GTC aging, and streaming order updates.
- **S2:** [Alpaca crypto trading](https://docs.alpaca.markets/docs/crypto-trading): maker/taker fee tiers, asset-denominated fees, supported orders, non-marginable buying power, precision metadata, and per-order limits.
- **S3:** [Alpaca market-data FAQ](https://docs.alpaca.markets/docs/market-data-faq): left-labeled bars, trade aggregation, extended-hours minute trades, missing bars, crypto quote-midpoint bars, and IEX/SIP differences.
- **S4:** [Alpaca historical stock data](https://docs.alpaca.markets/docs/historical-stock-data-1): IEX is a single-exchange testing-oriented feed; SIP consolidates US exchanges. An IEX-based edge needs separate validation before transferring to consolidated/executable prices.
- **S5:** [Alpaca Python SDK v3.2.0 REST implementation](https://raw.githubusercontent.com/alpacahq/alpaca-trade-api-python/v3.2.0/alpaca_trade_api/rest.py): total-history limit enforcement and request/order APIs. This is the declared minimum SDK version, not an installed-version claim.
- **S6:** [Moskowitz, Ooi, Pedersen: Time Series Momentum](https://www.aqr.com/Insights/Research/Journal-Article/Time-Series-Momentum), and [Hurst, Ooi, Pedersen: A Century of Evidence on Trend-Following Investing](https://www.aqr.com/Insights/Research/Journal-Article/A-Century-of-Evidence-on-Trend-Following-Investing): motivation for diversified, longer-horizon trend research, not a guarantee for this bot.
- **S7:** [Moreira, Muir: Volatility Managed Portfolios](https://www.nber.org/papers/w22208): historical evidence motivating volatility-aware risk allocation; adaptation to these strategies remains an experiment.
- **S8:** [Bailey: FAQs on backtest overfitting](https://mathinvestor.org/2014/04/faqs-on-backtest-overfitting/), [backtest-overfitting demonstration](https://mathinvestor.org/2014/08/new-online-tool-to-demonstrate-backtest-overfitting/), and [author publication index](https://www.davidhbailey.com/dhbpapers/). The index verifies the Probability of Backtest Overfitting and Deflated Sharpe publications. Direct PDF extraction failed and SSRN access returned 403, so no claim is made to have reviewed those full texts during this session.
- **S9:** [USCF: United States Oil Fund](https://www.uscfinvestments.com/uso): actual investment objective, futures roll exposure, and reverse split.
- **S10:** [Quantpedia: Time Series Momentum Effect](https://quantpedia.com/strategies/time-series-momentum-effect/): secondary literature map, including critiques about volatility scaling and predictability. Institutional backtest numbers are not return targets for this project.