# Second Codebase Review

Date: 2026-11-13. Fresh review of current application code, audit/ledger additions, configuration, documentation and tests after the preceding repairs. This report supersedes assumptions about the current implementation in the first review; that document remains a historical record.

## Verdict

### Repair Pass 6: Cancellation and Entry Recovery

- Cancellation follows replacements that appear while cancellation is pending, within one deadline and a
	bounded, cycle-checked chain. All snapshot orders are ownership-checked before any batch cancellation.
- Known zero-baseline sibling successors retain independent cumulative fill counters across ledger restart.
	Nonzero unfamiliar successor totals and exit replacements remain quarantined rather than inferred.
- Normal entry completion and recovery retain positive predecessor-fill evidence and filled-but-flat intents.
	Recovery refuses opposite-side exposure before adoption; unresolved accounting blocks entry recovery and
	configured adoption, including after restart with a stale broker snapshot.
- Full protective closes with replacement/group/history ambiguity commit quarantine with the exit accounting.
	Cancellation and monitoring do not follow successors after ownership is removed. Entry protection failure
	does not resurrect a closed zero-quantity position.

Validation: Python 3.11.9; `scripts/offline_tests.py --pattern test_review2_next.py` passed 28 tests;
`scripts/offline_tests.py` passed 132 tests. New fixtures include real temporary-ledger restart, inspection at
the post-commit export boundary, normal entry with flat/nonflat snapshots, and restart followed by reconciliation.
Independent read-only Safety Reviewer findings drove the additional combined-path checks; the final stale-snapshot
entry-recovery bypass was reviewed as closed. This is not an exhaustive correctness or broker-readiness certification.
Grouped full closes can intentionally require operator reconciliation even if no competing order ultimately remains.
No authenticated broker calls, operational-state writes, or automatic evidence-free accounting repairs occurred.

### Repair Pass 5 and Full-Cycle Review: 2026-11-13

- **R2-12 implemented:** Draw counts now scale to resolve adjusted bootstrap tails; insufficient usable tail
	samples fail closed. Research uses append-only SQLite run/trial/holdout records, unique manifests/results,
	code/config/data/action/environment fingerprints, and final-period claims made before reports. Ordinary
	nonsynthetic reports register exposure without preventing descriptive reruns. Research-mode base reports
	exclude the reserved final period; prior exposure in the same registry prevents a new untouched claim.
- **R2-13 implemented:** Weekly canonical minute partitions bound downloads, reuse completed history and extend
	tails; correction refreshes retain prior coverage. Research equity/crypto bars share their execution dataset.
	Live equity workers use chunk-built feature-only warm-up and short tail refresh; actions/calendar instances
	are cached. Simulation merges chronological generators instead of sorting a full event list. Storage and
	computation still scale with requested history; no unmeasured throughput or broker-latency guarantee is made.
- **R2-14 implemented:** Optional prices, flags, IDs, timestamps, configured ownership, integer counters, signed
	cash flows, unique IDs and fill quantity/notional relationships are validated. Known legacy optional defaults
	remain accepted; unknown position fields require migration. Invalid broker observations fail closed.
- **Residual cleanup:** Removed obsolete Sharpe/optimizer branches and negative-trigger naming, corrected missing
	SPY benchmark labels, standardized finite/null JSON exports, and updated funding/cache/research documentation.

The combined review caught additional conflicts, which were repaired: flat-after-entry phantom protection,
heuristic split counter/history disagreement, invalid cash-flow mutation, report exposure before holdout selection,
stale-partition correction skipping/coverage truncation, missing live feature endpoints, and negligible-variance
cash Sharpe inflation. Focused tests cover these alongside earlier R2 repairs.

Final offline suite: 110 tests passed with real SDK/pandas dependencies. A funding-aware synthetic CLI smoke run
and strict-JSON parse check are included in the final cycle validation. See CODEBASE_CYCLE_VALIDATION.md for
scope, independent-review findings and residual integration risks. No authenticated broker calls, live orders,
or production account-ledger writes were made. Local safeguards for all R2 findings are now implemented;
ambiguous broker replacement/adoption/split/fee provenance remains intentionally quarantined, not guessed.

### Repair Pass 4: 2026-11-13

- **R2-10 implemented:** A stale opposite-side entry/reversal signal becomes EXIT-only when a position exists.
	Stale flat/same-side entries become HOLD and are consumed without creating exposure. Broker-side/quantity
	guards still apply to the close, so preserving exit intent cannot bypass reconciliation safety checks.
- **R2-11 implemented:** Minute execution is two-phase. The elapsed minute's range is checked using the stop
	active before publication of newly completed features. All completed strategy features/ATR at that timestamp
	are then published; the monitor updates sampled extremes/trails, recomputes effective stops, and enforces them
	at the current observed price before deciding new strategy entries. No new stop is retroactively applied to
	the previous minute's high/low. The crypto broker-only option continues to disable software fallback.
	Strategy open/close boundary prices and complete OHLCV minute aggregates are compared; mismatches raise
	instead of silently overwriting inconsistent portfolio observations.

Validation: 90 offline tests passed, including nine new regressions for stale breakdown exits/flat entries,
long/short/crypto tightened stops, completed-ATR publication, prior-range safety, and inconsistent boundary
prices/volume. Editor diagnostics were clean. No authenticated broker calls or production-ledger edits occurred.
This defines a deterministic minute-based simulation policy, not exact 30-second polling or intraminute order
execution. Partial execution intervals still rely on the separate coverage validator. R2-12 onward remain open.

### Repair Pass 3: 2026-11-13

- **R2-07 implemented:** Equity minute aggregation retains only candles with an observation at the true nominal
	start, an observation in the final minute, and no interior gap over five minutes. Partial boundary candles are
	dropped instead of borrowing a later price as their open. Execution validation checks minute alignment and
	opening/closing boundaries over explicit intervals, including early closes. Live equity data validates between
	completed strategy candles; research validates its requested test interval. A partial warm-up session outside
	that interval is not falsely required to cover a full session. Sparse interior observations remain a declared
	bounded-gap policy, not proof that every trade was captured. Missing prices are not fabricated.
- **R2-08 implemented:** Corporate actions are globally ordered and deduplicated after all pages, with conflicts
	rejected. Features, continuous ATR history, simulation events and benchmarks share the same stable ordering:
	effective timestamp, splits before dividends, then action ID. Repeated identical records do not double accrual.
- **R2-09 implemented:** No-RTH, no-completed-candle and empty-API results retain a UTC index and timezone-aware
	`bar_end` schema for equities. An empty incremental response can reuse the existing cache without inventing
	fresh candles. Single-day calendar queries now use a padded construction interval, fixing the library boundary
	restriction exposed by the new coverage tests.

Cache provenance version was raised to 4; older histories refresh rather than silently reuse pre-repair candle
semantics. Synthetic equity bars remain explicitly generated strategy bars with session-clipped ends; they are
not passed through strict minute aggregation or represented as real minute execution observations.

Validation: 81 offline tests passed, including 12 new data regressions, and one short synthetic CLI smoke test
completed. Cross-page same-day split/dividend fixtures run through the actual simulator and benchmark. Editor
diagnostics were clean; no authenticated broker/data calls or production-account writes were made. Provider
late corporate-action records, intraminute price ordering and research/statistical limits remain unresolved.
R2-10 onward remain outstanding.

### Repair Pass 2: 2026-11-13

- **R2-05 implemented:** Initial crypto requests are rounded down to timeframe boundaries; incremental requests
	overlap a full preceding candle and reject off-grid timestamps. A returned candle beginning before the requested
	interval cannot overwrite a complete cached candle.
- **R2-06 implemented:** Equity OTO entries request GTC, retain group ownership and convert inadequate attached
	coverage only after confirmed cancellation and residual checks. Protection requires the matching symbol, side,
	quantity, GTC lifetime and non-imminent expiry. Crypto limits must be tick-aligned and within the configured adverse
	buffer, with one rounding tick permitted. Legacy DAY stops are not treated as durable and are converted safely.
- **R2-04 implementation extended:** Nested grouped orders are flattened/deduplicated. Known related legs have
	durable per-order quantity/notional baselines, so fills arriving during cancellation are accounted exactly once.
	Predecessor executions are consumed before following replacement IDs. Bot-managed stop movement now uses
	cancel/account/re-arm rather than PATCH, avoiding uncertain OTO replacement support and inherited-fill semantics.
	Unknown order ownership, partially filled adoption without provenance and nonzero unfamiliar replacement
	carryover fail closed for explicit reconciliation; the bot does not guess or auto-clear those exceptions.

Validation: 69 offline tests passed, including 11 additional checks for whole-candle refresh, grouped legs,
predecessor fills, ambiguous carryover, legacy DAY conversion, expiry, tick rounding, unknown ownership and residual
stop movement. Editor diagnostics were clean. No authenticated broker requests or production-ledger changes occurred.
These tests establish local behavior, not Alpaca venue/group integration. Confirmed cancel/re-arm still has an
unprotected interval, and GTC/stop-limit orders cannot guarantee gap execution. R2-07 onward remain outstanding.

### Repair Pass 1: 2026-11-13

The review findings below describe the pre-repair snapshot; line anchors may have moved.

- **R2-01 repaired:** Broker-side guards now run before close cancellation, again before submission, at exit recovery,
	and during monitoring. Side mismatches block software liquidation and new entries while retaining existing orders.
- **R2-02 repaired locally:** ATR constructs metadata-free numeric Series, so execution DataFrames in history attrs
	cannot trigger ambiguous pandas comparisons in true-range concat. Execution/action metadata remains available to
	the caller and backtester. A dedicated history container remains a possible follow-up, not a prerequisite for this fix.
- **R2-03 repaired for observed cumulative fills:** Reconciliation refreshes broker positions after accounting stop
	executions and retains accounted residual quantity if snapshots still disagree. Unexplained quantities block order
	changes instead of silently resizing local exposure. Stop repair, close confirmation, recovery and monitoring respect
	that quantity guard. The existing recognizable split-adjustment policy remains and still needs explicit action provenance.
- **R2-04 partially repaired:** Exit and tracked protective-stop fills share a cumulative-quantity/notional accounting
	helper. Cancellation responses for the tracked stop are consumed before a market residual exit; repeated observations
	do not log duplicate fills. Full-fill cancellation avoids a second close. Replacement-chain identity/counter semantics,
	untracked grouped orders and historical adoption fills remain outstanding and must not be treated as resolved.

Validation: 58 offline tests passed, including nine new regression checks for wrong-side order paths, realistic
execution-bearing history construction, interleaved stop/position snapshots, cancellation fills, and residual guards.
Existing fixtures were updated to include the new pre-cancellation broker-side read. No broker requests/orders were
made; tests use mocks or temporary files. No production ledger/state files were changed.

Broad simplification was deferred. The only new shared abstractions are the broker consistency guards and cumulative
fill helper, which directly support these repairs. R2-05 onward and the remaining portions of R2-04 are not implemented
by this batch. The original live-readiness warning still applies.

**Do not enable live trading yet.** The audit ledger is materially stronger, and all 49 existing tests pass. Nevertheless, additional offline probes reproduced a wrong-side liquidation path, a real-history ATR crash, stale-snapshot position accounting, and lost cancellation fills. The test suite currently covers individual helpers better than complete broker/data workflows.

This was a review, not a repair pass. Trading code, production account files and the user's README edits were left unchanged. Only this report was added. No authenticated broker/data requests, orders, package installations or existing ledger modifications were made.

Evidence labels: **Reproduced** means actual project methods exercised with controlled inputs and mocked broker/persistence operations. **Source-confirmed** means the control flow directly establishes the problem. **API-dependent** means public documentation supports the concern, but authenticated integration has not been exercised. Severity includes both capital safety and invalid investment inference.

## Prioritized Findings

### R2-01. Critical: Side mismatch can still trigger an order that increases exposure

Locations: [bot/portfolio.py](bot/portfolio.py#L741), [bot/portfolio.py](bot/portfolio.py#L810), [bot/portfolio.py](bot/portfolio.py#L576). Evidence: reproduced monitor path; source-confirmed order direction.

`reconcile()` notices that the broker position side differs from the local side and marks protection unverified. `_ensure_protection()` also detects the mismatch. Neither removes the position from the remainder of `manage_positions()`. Software target/stop checks can then call `close_position()` anyway.

The close method obtains broker quantity but chooses buy/sell from **local** `pos.side`. For a locally recorded long SPY but actual broker short SPY, a price below the local stop reaches a market **sell**, adding to the short rather than buying to cover. Direct strategy EXIT/reversal paths can enter the same close method. Cancellation of broker orders also happens before the close method checks this mismatch.

Observed probe: local long 100, broker short 100, price 97, local stop 98. The monitor logged the mismatch and then called liquidation.

Repair: require broker/local side agreement immediately before every closing submission; return an explicit quarantined-symbol result on mismatch. Monitoring and strategy execution must respect that symbol status, not merely block new entries globally. Do not cancel another side's protection while the mismatch is unresolved.

### R2-02. High: Real historical data fails in ATR because execution DataFrames are stored in attrs

Locations: [bot/data.py](bot/data.py#L98), [bot/backtest.py](bot/backtest.py#L156), [bot/indicators.py](bot/indicators.py#L17), [bot/corporate_actions.py](bot/corporate_actions.py#L39). Evidence: reproduced with installed pandas.

Fetched equity histories and fetched backtest histories attach a DataFrame under `frame.attrs['execution']`. Pandas propagates/copies this metadata into derived Series. ATR uses `pd.concat()` to assemble true-range components; concat compares attrs, which now contain independently copied DataFrames. That comparison produces a DataFrame rather than a boolean and raises:

```text
ValueError: The truth value of a DataFrame is ambiguous.
```

Both a minimal attributed OHLCV frame passed to `indicators.atr()` and actual `Backtester` construction reproduced the error. Live `_process_symbol()` adjusts a frame and then computes ATR, so it also reaches the affected pipeline. Metadata-free synthetic fixtures do not establish that fetched data works.

Repair: use an explicit market-history container for strategy bars, execution bars, actions and provenance. As a small interim repair, detach complex attrs before numerical operations and carry execution/actions separately. Add one integration test from `MarketData` output through live evaluation and `Backtester` construction, with realistic attrs.

### R2-03. High: A stale broker snapshot can undo a correctly accounted stop fill

Location: [bot/portfolio.py](bot/portfolio.py#L741). Evidence: reproduced.

Reconciliation gets `broker_positions()` first, then reads the stop order. If the stop fills between these calls, a newer order response reports 70 executed shares while the earlier position snapshot still reports 100. The exit accounting correctly reduces local quantity to 30, but the later `pos.qty = abs(float(bp.qty))` assignment restores it to 100. Fill counters remain at 70, so the next reconciliation cannot simply replay the fill to repair it.

Observed probe: one 70-share fill recorded, local `stop_filled_qty=70`, but local `qty=100` after reconciliation. Subsequent stop placement or exit P&L can then use incorrect remaining exposure.

Related problem: quantity changes without matched executions are silently adopted by assignment. A manual reduction or increase can make the execution ledger incomplete or the entry basis wrong without raising an accounting exception.

Repair: refresh positions after newly observed executions; use a consistent execution-first reconciliation protocol and assert expected residual quantities. Treat unexplained quantity changes as explicit reconciliation exceptions. Do not overwrite newer local accounting from an older position snapshot.

### R2-04. High: Cancel/fill races discard known stop executions and can corrupt split accounting

Locations: [bot/portfolio.py](bot/portfolio.py#L217), [bot/portfolio.py](bot/portfolio.py#L576), [bot/portfolio.py](bot/portfolio.py#L288), [bot/portfolio.py](bot/portfolio.py#L332). Evidence: reproduced close path; related paths source-confirmed.

`_cancel_and_confirm()` returns a terminal order, including filled quantity and price. `_cancel_orders_for()` discards that result. If a stop partially fills during cancellation, `close_position()` submits an exit for the smaller broker residual without first accounting for the protective fill. `_exit_price_from_orders()` only accepts status `filled`; it ignores known fills on status `canceled`.

Observed probe: stop sold 70 at 98 and was canceled; remaining 30 sold at 101. The close path recorded the market exit and then requested unknown-price accounting for the residual local quantity, despite the known 98 stop execution.

The same dropped observation matters when repairing/replacing stops. Resetting stop counters to zero is safe only if old-order fills have already been accounted and new-order cumulative quantities are understood. Adoption similarly seeds counters from an existing stop without necessarily attributing its previous partial fills.

Repair: feed every cancellation/replacement terminal response into one cumulative-execution accounting path before switching order identity or resizing protection. Test fill-during-cancel, partial-fill-then-replace, and cancel-completes-after-another-fill. Raw observation storage alone does not update the position/accounting projection.

### R2-05. High: Incremental crypto refresh starts inside a candle and can overwrite complete OHLCV

Location: [bot/data.py](bot/data.py#L72). Evidence: reproduced request boundary and overwrite; provider clipping behavior needs integration confirmation.

All timeframes reuse `last_cached_timestamp - 2 minutes`. For hourly crypto this starts inside the preceding hour. A clipped/partially aggregated API response can share a timestamp with a complete cached candle; duplicate removal keeps the new response without establishing that it covers the whole candle.

Observed controlled response: request starting 10:58 replaced the cached 11:00 hourly candle's volume 300 with 5, and replaced its full high/low range. This directly affects the breakout volume filter and ATR. The exact clipping behavior of Alpaca's aggregation for a nonaligned request should be checked, but the current code has no defense against it.

Repair: refetch from a full timeframe-aligned boundary, with sufficient overlap to rebuild the current and previous candle. Track completeness and never replace a complete candle with a partial interval. One-minute canonical data plus local crypto aggregation would remove this ambiguity.

### R2-06. High: DAY/OTO orders are accepted as durable stop coverage

Locations: [bot/portfolio.py](bot/portfolio.py#L412), [bot/portfolio.py](bot/portfolio.py#L272), [bot/portfolio.py](bot/portfolio.py#L332). Evidence: reproduced coverage check; source-confirmed DAY entry; API-dependent lifetime.

Equity entry is submitted as an OTO group with `time_in_force='day'`. `_attach_existing_stop()` can adopt its stop leg, and `_check_stop()` does not inspect time-in-force or expiry. A DAY order with the correct quantity/price was accepted as verified coverage in the probe. That does not support the README promise that stopping the bot leaves durable GTC protection.

Public Alpaca documentation states DAY orders expire after the session and describes group cancellation propagation. The current code does not retain group identity or define how a DAY attached leg becomes persistent protection. OTO replacement support also needs specific integration verification rather than being assumed from single-order replacement behavior.

Additionally, crypto `_check_stop()` never checks `limit_price`. A sell stop at 98 with limit 200 passed the probe, although that limit cannot execute near its trigger. Sub-dollar/asset tick checks are also weakened by `round(effective_stop, 2)` in validation despite tick-aware placement.

Repair: verify TIF/expiry, expected stop/limit relationships and tick precision. Define and test OTO lifecycle and protection conversion. Preserve order-group identity so canceling/replacing a child cannot silently invalidate other coverage.

### R2-07. High: Sparse sessions and partial candles are labeled complete

Locations: [bot/sessions.py](bot/sessions.py#L32), [bot/sessions.py](bot/sessions.py#L55), [bot/data.py](bot/data.py#L61), [bot/backtest.py](bot/backtest.py#L766). Evidence: reproduced.

`validate_execution()` checks missing dates and interior gaps, not coverage from each session open through session close. Two consecutive sessions containing only one noon minute each passed validation. Aggregation then labeled the first noon price with the 09:30 four-hour candle start. The resulting simulated candle-open execution can use a price that did not exist until hours later.

The initial rolling history request commonly starts inside a session/candle. Aggregation does not distinguish that partial first candle from a complete one. This can also cause `backtest_window()` to reject otherwise usable history because execution starts later than the invented aggregate start. Crypto validation likewise permits off-grid timestamps and incomplete boundary coverage.

Repair: distinguish partial boundary intervals, drop or explicitly label incomplete strategy candles, and verify expected session boundaries/coverage before simulations. Missing-market-data policies must not invent a tradable open from a later observed trade. Separate legitimate sparse prints from missing observations using a declared coverage policy.

### R2-08. High for research: Corporate-action ordering is lost across pages

Locations: [bot/data.py](bot/data.py#L48), [bot/corporate_actions.py](bot/corporate_actions.py#L39), [bot/corporate_actions.py](bot/corporate_actions.py#L55), [bot/backtest.py](bot/backtest.py#L201), [bot/backtest.py](bot/backtest.py#L652). Evidence: reproduced through actual paginated retrieval and simulator.

`normalize_actions()` sorts each response page, but `get_corporate_actions()` concatenates pages, deduplicates by dictionary insertion order and returns without a final global sort. Subsequent feature adjustments consume that order. Simulation events with identical timestamps are then ordered by the original action index, not an explicit split-before-dividend priority. Benchmark ordering sorts only by effective time and preserves any wrong same-time order.

Observed probe: a dividend record on page one and same-day 2:1 split on page two caused the simulator to accrue 100 rather than 200 for a 100-share pre-split holding and $1 post-split dividend. Continuous close became 99 instead of 100.

Repair: define one stable ordering key, globally normalize/deduplicate all pages, reject conflicting duplicate action IDs, and explicitly prioritize event semantics in the simulator and benchmark. Add same-day cross-page fixtures, not just helper sorting tests.

### R2-09. Medium: Empty regular-session results raise instead of returning a valid empty history

Locations: [bot/data.py](bot/data.py#L93), [bot/sessions.py](bot/sessions.py#L55). Evidence: reproduced.

If the API returns nonempty premarket/after-hours bars but no RTH data, `aggregate_equities()` returns an empty OHLCV frame without `bar_end`. The caller indexes `df['bar_end']` and raises `KeyError` instead of returning a valid empty completed-candle result.

Observed premarket-only fixture raised `KeyError: 'bar_end'`. This is an operational error, not a capital-loss event by itself, but produces unnecessary strategy failure/backoff and complicates cold-start recovery.

Repair: preserve a stable empty-frame schema, including a timezone-aware `bar_end`, and handle no-completed-session data explicitly.

### R2-10. Medium: Freshness gate can suppress a risk-reducing reversal exit

Location: [bot/main.py](bot/main.py#L143). Evidence: source-confirmed.

The five-minute stale-candle check rejects all ENTER_LONG/ENTER_SHORT signals before `_execute()`. Those actions can mean exit/reverse for an existing opposite-side position, including a BTC breakdown that should only close a long. A delayed fetch therefore suppresses the close as well as the stale new entry.

Repair: distinguish exit intent from entry intent before freshness gating. Stale features should prevent increasing exposure, not automatically prevent risk reduction. Use quote/feature timestamps and explicit stale-exit policy rather than relying on action names alone.

### R2-11. Medium: Minute-level simulation still has live trailing-stop and feature-boundary mismatches

Locations: [bot/backtest.py](bot/backtest.py#L366), [bot/backtest.py](bot/backtest.py#L233), [bot/portfolio.py](bot/portfolio.py#L838). Evidence: source-confirmed; not asserted as exact intrabar execution.

The minute monitor computes `stop` before ratcheting the trail. It does not then enforce the newly tightened stop for equities; the crypto fallback uses the old `stop` variable. Live monitoring ratchets and then recomputes/enforces the effective stop. A volatility-driven tightening or a retained extreme can therefore leave a simulated position alive for an extra minute even if its newly computed stop is already breached.

At a shared minute/strategy-close timestamp, monitoring runs with the previous feature index/ATR before publishing the newly closed strategy candle. The real scheduler can run these in a different order. Different execution and strategy datasets are not cross-checked for consistent boundary closes; a coarse strategy mark can overwrite a minute execution mark.

Repair: explicitly specify observed-price, feature-publication and monitor ordering; enforce the resulting stop after updates; cross-validate strategy aggregation against execution data. This is separate from unavoidable uncertainty about high/low ordering within one minute.

### R2-12. Medium: Selection-adjusted bootstrap bounds are poorly resolved and the trial registry is not persistent

Locations: [bot/statistics.py](bot/statistics.py#L15), [bot/backtest.py](bot/backtest.py#L712), [bot/backtest.py](bot/backtest.py#L1061). Evidence: source-confirmed.

The optimizer uses only 250 bootstrap draws but requests Bonferroni-adjusted tail quantiles. For the 28 momentum candidates, the lower quantile is approximately 0.00089, below one draw out of 250. The gate is effectively extrapolated near the sampled minimum, not a well-resolved extreme confidence bound. This method should not be described as statistically certifying the selected strategy.

The JSON trial output is overwritten on every research run. It is not an append-only experiment registry and does not identify dataset hashes or code/config revisions. Repeated manual runs and reuse of the same holdout are therefore not controlled by the implementation. Two years and 20 aggregate validation exits are useful filters, not proof of sufficient independent evidence.

Repair: choose a defensible selection/uncertainty method with enough simulation resolution; persist immutable experiment IDs, data/config/code fingerprints, every attempted variant and holdout-use history. Keep the current flat-start fold boundary limitation explicit.

### R2-13. Medium: History volume and unconditional rebuilds threaten practical runtime

Locations: [bot/data.py](bot/data.py#L73), [bot/backtest.py](bot/backtest.py#L786), [bot/main.py](bot/main.py#L224), [bot/utils.py](bot/utils.py#L19). Evidence: source-confirmed cost structure; no measured latency claim.

The 450-day trend lookback now downloads minute bars, including extended-hours observations before filtering. Multi-year BTC research downloads every minute as well. Cold fetches are paginated under fixed operation budgets, and caches older than five minutes refresh the entire requested historical range instead of incrementally extending a verified immutable cache.

The calendar helper is cached by exact date range; moving worker ranges may recreate calendars. Live data validation/aggregation repeatedly handles large frames, and action histories are fetched each evaluation even when unchanged. Ledger audit/order/state/export operations also add many synchronous disk actions to the main monitor. This increases the chance that documented request/monitor budgets are exhausted in ordinary startup, not just outages.

Repair: separate live warm-up data from research execution data, partition/cache minute histories, incrementally update recent ranges and corporate actions, and profile monitor latency. Keep a single authoritative execution writer; do not fix the issue by unsafely making broker mutations concurrent.

### R2-14. Medium: Recovery-state validation leaves optional control fields unchecked

Location: [bot/portfolio.py](bot/portfolio.py#L1013). Evidence: source-confirmed validation omissions.

Core quantities and fill counters are checked, but optional stops/targets, `daily_controls.cash_flow`, strategy/configured-symbol ownership, protection flags, counter integrality and several order identity fields are not comprehensively validated. JSON encoding rejects nonfinite numeric tokens but permits wrong-type values such as strings or negative optional prices.

A structurally intact but semantically invalid snapshot can pass journal equality and then fail inside arithmetic, compare an invalid target, or restore a position whose configured instrument contract differs. Snapshot equality establishes integrity against accidental projection edits, not semantic validity.

Repair: validate the entire typed state at load/commit with explicit migration rules for older schemas. Separate configuration changes from genuine broker ownership changes and reject unsupported combinations predictably.


## Simplification and Residual Code

1. **Use a market-history data object instead of heavy DataFrame attrs.** It resolves R2-02, avoids accidental large metadata copies, and makes strategy/execution/action/provenance ownership explicit.
2. **Use one execution-observation reconciler.** Exit fills, stop fills, cancel responses, replacements and recovered intents currently follow separate paths. One producer of accounted deltas would remove duplicated logic underlying R2-03/R2-04.
3. **Return explicit per-symbol reconciliation states.** A boolean `protection_verified` is used for entry gating but is not sufficient to prevent wrong-side monitoring. States should distinguish ready, closing, missing protection, side mismatch and accounting/persistence quarantine.
4. **Remove obsolete optimizer branches.** [bot/backtest.py](bot/backtest.py#L1065) still checks `oos_new`/`oos_spec`, which the new optimizer no longer returns. `negative` contains all strategy names, and comments still describe negative-Sharpe-triggered adjustment. These are dead/misleading remnants.
5. **Consolidate Sharpe code.** The standalone `sharpe_ratio()` helper is no longer the metric path; `compute_metrics()` uses `daily_returns()` directly. Keep one excess-return policy to prevent later divergence.
6. **Tighten output schemas.** Research and metric exports use default `json.dumps()` behavior, which can emit `NaN`/`Infinity`; consumers expecting standard JSON may reject them. Convert undefined metrics to null and write with `allow_nan=False`.
7. **Update stale funding documentation.** Backtest module text and some README prose still say margin interest/borrow fees are not modeled even though annual-rate accrual exists. The correct claim is simplified rates modeled, broker-specific fees/settlements not fully reproduced.
8. **Separate benchmark labels and intent.** If SPY is absent, `portfolio_benchmarks()` labels a fallback passive curve as SPY. Ex-post risk matching is already caveated; retain that caveat and avoid presenting it as a cost-aware deployable benchmark.
9. **Make invalid input handling consistent.** Periods and timeframes require integer/range checks, fees should be bounded below one, metadata increments should be positive, and simulation/live crypto quantization should share a contract. Current live sizing changes `SizeResult.qty` after metadata rounding without recomputing the logged notional/risk values.
10. **Minimize duplicated export attempts and session logs.** State saves and post-exit logging can request redundant exports. Keep committed projections authoritative and rate-limit retry/log traffic without hiding failures.

No broad abstraction rewrite is recommended before fixing the reproducible safety failures. New types/helpers should directly remove those duplicated responsibilities, not become another layer around unchanged broken logic.

## Checks and Limits

- Existing offline suite: **49 tests passed** in Python 3.11.9 with installed SDK and pandas. Tests were not altered.
- Editor diagnostics: no errors reported in the reviewed portfolio, ledger, data and backtest modules. This does not establish runtime correctness.
- Additional controlled actual-method probes reproduced R2-01 through R2-04, R2-06 coverage validation, R2-07 sparse-session acceptance, R2-08 cross-page ordering, and R2-09 empty-RTH schema failure.
- R2-05 reproduced an unsafe fetch boundary and duplicate overwrite with a controlled clipped response; live endpoint aggregation behavior remains to be verified.
- The actual attributed ATR/Backtester probe failed as expected; this is evidence of a production-path defect, not a failing test accidentally introduced by this review.
- Side-mismatch tests intercepted liquidation requests rather than submitting orders. Persistence and CSV writes were mocked or confined to the existing temporary-directory test suite.
- No full multi-year optimizer run, real-data download, live/paper integration run, or runtime profiling was performed. Statistical/resource claims are labeled accordingly.
- Public [Alpaca order documentation](https://docs.alpaca.markets/docs/orders-at-alpaca) was checked for DAY/GTC lifetime, grouped-order behavior and cancellation/order states. The old order-list reference URL returned 404; it was not used as evidence that child orders are invisible.

## Recommended Repair Order

1. Quarantine side mismatches at every order boundary; fix real-history ATR metadata handling.
2. Account executions observed during cancellation/replacement and repair execution-first broker reconciliation.
3. Define durable DAY/OTO-to-GTC protection lifecycle and validate complete stop-limit coverage.
4. Fix timeframe-aligned refreshes, session/candle completeness and globally ordered corporate actions.
5. Align stale-exit and minute-monitor semantics; consolidate execution/data contracts.
6. Improve immutable research provenance, statistically resolved uncertainty, bounded history storage and residual documentation.

The immediate next verification should use full-path fixtures: real `MarketData`-shaped histories into live/backtest evaluation, OTO group responses with cancellation fills, and interleaved broker position/order observations. Passing standalone helpers and random synthetic runs is not enough to validate those contracts.