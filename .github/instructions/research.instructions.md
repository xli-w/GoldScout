---
description: "Use when changing historical data, strategies, experiment specifications, simulation, or research protocols and statistics."
applyTo: "bot/data.py,bot/history_cache.py,bot/sessions.py,bot/corporate_actions.py,bot/indicators.py,bot/backtest.py,bot/research.py,bot/statistics.py,bot/strategies/**/*.py,bot/experiments/**/*.py,bot/campaign_data/**/*.py,investment/**/*.py,experiments/**/*.json,tests/test_backtest.py,tests/test_experiments.py,tests/test_campaign_dataset.py,tests/test_research_safety.py,tests/test_remaining_findings.py,tests/test_review2_data.py,tests/test_review2_monitor.py,tests/test_review2_final.py,tests/test_investment.py,tests/test_investment_dashboard.py,docs/backtesting-protocol.md,docs/campaign-dataset.md,docs/investment-research-context.md,docs/investment.md"
---
# Data and Research

Use `backtest-integrity-review` and [research invariants](../../docs/safety-invariants.md).
For the implemented GBP record-keeping and proposal path, also use
[`investment-system-development`](../skills/investment-system-development/SKILL.md).

- All decisions use completed bars and information available at decision time. Validate UTC timestamps, `bar_end`, exact execution boundaries, exchange sessions, early closes, and crypto timeframe alignment.
- Missing/sparse/off-grid history must not silently become executable complete candles. Preserve source metadata and cache validation.
- Sort corporate actions globally across pages and apply same-day split/dividend ordering consistently. Reject conflicting or unsupported actions rather than infer terms.
- Publish ATR/features only after the relevant bar completes. A newly calculated stop must not be applied retrospectively to an earlier range. Mixed-timeframe sizing must use contemporaneously known marks.
- Share strategy rules with live operation but document simulation differences: fill timing, OHLC ambiguity, slippage, liquidity, estimated fees, and protective order gaps.
- Record data provenance, date ranges, parameters, seeds, trial count, costs, and benchmark assumptions. Keep risk settings outside automatic optimization.
- Lock parameters before final holdout evaluation; holdout is report-only. Do not repeatedly tune against it or claim statistical significance/profitability from a favorable backtest.

## Campaign alignment

- For the GBP portfolio campaign, apply [investor context](../../docs/investment-research-context.md) and the [protocol](../../docs/backtesting-protocol.md), not generic runner defaults. Verify eligibility, applicable terms for the selected cash comparator, licensed data, point-in-time GBP FX/accounting, costs, and tax assumptions before performance evaluation. Return fail/inconclusive where evidence cannot support the question.
- Do not use `prepare --allow-network` for this campaign: it uses the Alpaca history client. The separate `bot.campaign_data` importer is offline ingestion/validation only; it is not consumed by the USD minute runner, does not implement combined GBP portfolio accounting, and does not close the campaign's feasibility gates. Independent USD strategy windows cannot answer the combined GBP portfolio question. Obtain explicit scope narrowing or implement approved prerequisites; never relabel existing outputs as portfolio suitability.
- Keep attempted candidates and earlier manual searches visible. The campaign's 60-candidate total includes baselines; CLI limits of 125 per strategy/500 total are implementation ceilings, not permission to expand that search. Fixed cost cases/windows are not additional candidates. Preserve research-only risk profiles without changing live configuration.
- Report capability gaps as gaps. The runner stresses finalists/baselines and can evaluate a baseline holdout when no candidate locks; the campaign instead requires adverse-cost selection evidence and no holdout opening when selection fails. Documentation or a valid spec does not enforce those extra gates.
- The separate [`investment` package](../../investment/) is GBP-only and operator-recorded: it is not the historical portfolio simulator. Preserve its causal availability contract (including delayed marks), linked unitized returns through zero holdings, and buy-only/export-only planning boundary. Synthetic tests may validate new simulation mechanics, but product performance evaluation remains blocked until source rights, product/account eligibility, comparable cash terms, actions/FX, costs/tax and the frozen research protocol are evidenced.