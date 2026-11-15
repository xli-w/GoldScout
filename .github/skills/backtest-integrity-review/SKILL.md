---
name: backtest-integrity-review
description: "Use when changing candles, strategy indicators, experiment specs, backtest timing, portfolio research protocols, selection, or holdout statistics."
---
# Backtest Integrity Review

Read [research contracts](../../../docs/safety-invariants.md). Anchor at the function producing bars/features/fills or selecting trials, not a reporting wrapper.

1. Establish event timing: source timestamps, session/timeframe boundaries, `bar_end`, feature availability, decision, fill, and stop publication. Identify what was actually known at each event.
2. Select a minimal deterministic fixture: early close/holiday, sparse or off-grid bars, incomplete last candle, mixed timeframes, or same-day split/dividend across pages.
3. Assert no future feature/price use, no retrospective new-stop application, completed execution coverage, globally ordered actions, and preserved cache provenance/validation.
4. Review costs and simulation assumptions: slippage, fees versus estimates, dividend liability, liquidity, gaps, and OHLC event ambiguity. Shared strategy code does not imply execution parity.
5. For research selection, record data range/provenance, parameters, seeds, trial count, benchmark, and costs. Verify locked parameters and report-only final holdout; do not optimize risk controls.
6. Run scoped backtest/data/research regressions through the offline runner, then full tests for shared changes. Use synthetic/local fixtures, not broker downloads.

## GBP campaign feasibility and interpretation

For strategy comparisons or suitability claims, first read [investor context](../../../docs/investment-research-context.md) and the [protocol](../../../docs/backtesting-protocol.md).

1. Verify product/UK access, applicable terms for the selected GBP cash comparator, licensed data, total-return coverage and point-in-time FX. Record unresolved facts; bot ticker support or a policy rate is not eligibility/product evidence.
2. Confirm the question can be represented: one initial GBP 1,000-equivalent contribution, one chronological portfolio cash/equity clock, simultaneous allocation/risk rules, FX/distributions and comparable costs/tax scenarios. Independent reset USD windows are not continuous GBP portfolio growth.
3. Check the predeclared 60-candidate campaign ledger, baselines and failed/abandoned/manual variants included. Verify the fixed unlevered 0.50% per-trade / 2.00% aggregate planned stop risk / 1.00x gross profile, separate 1.10x / 0.25% leverage challenger, and 5% crypto sleeve. These are research assumptions, not live settings or guaranteed loss limits.
4. Verify matched net GBP cash/passive comparisons, the 5-percentage-point annualized cash-relative hurdle, base and predeclared 1.5x/2.0x variable-cost stresses, observation/uncertainty gates, and the 20% drawdown review flag. The six-month return aspiration is not a pass criterion.
5. Require selection evidence before one locked report-only holdout; no passing candidate means do not open it. Do not treat finalist-only stresses or baseline-only holdout behavior as full campaign compliance. Never reuse a consumed holdout by deleting/changing the registry.
6. Stop at capability gaps or obtain explicit scope narrowing. For example, `prepare` without `--allow-network` only prints coverage; adding the flag fetches Alpaca data and is prohibited for this campaign. The separate `bot.campaign_data` importer and read-only normalized daily view are approved offline ingestion/readiness tools; neither feeds a combined GBP portfolio simulator. The current CLI still does not implement that simulator.

Reviewer mode inspects evidence without execution. Output: timing contract, reproduction/check results, bias/coverage findings, and assumptions. Never claim profitability, live readiness, or significance unsupported by the experiment design.