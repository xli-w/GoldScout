# Investment Research Context

## Current long-term investment mandate

On 2026-11-16 the investor clarified that GoldScout should manage substantial,
designated long-term investment capital, not emergency reserves, day-to-day
liquidity, miscellaneous savings or household budgeting. Include only selected
investment bank deposits, bonds and investment accounts. GBP remains the reporting
currency; the capital amount is to be supplied, not assumed to be GBP 1,000.
Earlier drawdown tolerance is context, not approval of a dynamic loss budget.

**Objective:** seek durable net compound GBP growth over years, explain its
sources and costs, and improve allocation decisions within investor-approved risk
constraints. "Most profitable" is an aspiration, not a knowable optimum or a
mandate to maximise leverage, turnover or risk. Operational funding/settlement
constraints still apply even though personal liquidity planning is out of scope.

**Structure:** a majority stable core plus a separately capped experimental,
dynamic sleeve. The investor approved documenting 80-90% core / 10-20% dynamic
as an illustrative design range only. Exact weights, allowed products, per-position
and aggregate risk, loss budget, replenishment and profit-transfer rules await
approval. The core is diversified unleveraged global funds, high-quality bonds
and selected GBP deposits. "Fixed" means stable strategic holdings/policy, not
guaranteed value or a ban on disciplined rebalancing. Leverage, shorts, crypto and
speculative options are not newly authorised in either sleeve.

**Automation:** maximise reliable automatic measurement, monitoring, alerts,
source-linked AI research and calendar awareness first; progress to execution
under revocable standing policy after venue/recovery validation. Human approval
of policy should eventually replace repetitive approval of every compliant order.
The system must not rewrite its own mandate, promote experiments, refill losses
from the core or increase risk because of AI output. These are future requirements,
not permissions implemented by the current blocked-proposal release. The local
awareness projection is implemented: it matches operator-configured exposures to
existing public calendar and dated-brief evidence and displays configured
reminders. It does not start collection or AI, send account data to AI, or
influence trades.

**Research priorities:** maintain a low-cost, low-turnover core; evaluate a small
set of interpretable daily/weekly long-only dynamic hypotheses against keeping
that capital in the core. AI should automate evidence gathering, dated briefs,
exposure explanations and experiment proposals. Calendar-aware execution or
structured AI-derived features may become separate prospective experiments only
after explicit signal/failure-policy approval. No news/LLM trading signal is
admitted to the existing campaign by this mandate. Do not revive the legacy
intraday strategies as presumed winners or attach unrestricted AI to orders.

**Comparisons:** actual designated savings terms for personal reporting, a
matched all-core passive portfolio for judging dynamic value, and full-portfolio
as well as sleeve-level net GBP attribution. The previously selected BoE
GBP 1,000 instant-access quoted-rate series (excluding guaranteed bonuses, with
bonus-inclusive sensitivity) remains a separately labelled research proxy, not
this larger investor's available yield. Its series/vintage still needs verification.
NatWest 0% and the original GBP 1,000 scenario remain historical/sensitivity
context, not the current primary benchmark or capital assumption.

The earlier six-month aspiration, stop-based profiles, leverage/crypto challengers
and +5-point NatWest gate below are historical. Preserve their provenance, every
exposed sample and consumed holdout. Retain the 60-candidate total ceiling,
predeclaration, causal accounting, adverse costs and single-use report-only
confirmation. Do not reset the search history because the mandate changed.

The current separate GBP investment path supports v1 empty-inception and v2
evidence-backed opening accounting. V2 records core/dynamic/unassigned roles
independently of asset class and account, local read-only portfolio awareness,
and narrow evidence-backed corrections; it does not implement approved role
budgets, transfers, investment proposals, a historical product simulator or a
broker adapter. Provider sync is discovery only. The [investment guide](investment.md)
defines current capabilities and gates; the [protocol](backtesting-protocol.md)
defines research admission. Public-document research may inform design, but
market-data acquisition/performance runs remain blocked at unmet product,
source-rights, vintage, cash-terms, action/FX, cost/tax and frozen-protocol gates.
No paid feed, authenticated broker data, account access, orders or
operational-state work is authorised. These implementations do not change
legacy trading strategies/defaults or grant execution authority.

## Historical trading campaign (retained context)

All investor objectives, capital assumptions, strategy priorities and research
risk profiles in the remainder are historical, not the current mandate above.
Their trial/exposure history remains binding provenance; changing the product
direction cannot make previously seen samples unexposed.

This document records the investor's stated goals and the agreed boundaries for offline strategy research. It is context for research and decision-making, not investment, legal, or tax advice. It does not change the live bot, its configuration, or its operating limits.

The [backtesting protocol](backtesting-protocol.md) operationalizes this context and records current simulator/data-import gaps. The separate [market-intelligence development](decisions/0002-market-intelligence.md) is advisory/shadow only; its existing bot observer does not influence orders, and it does not lift this campaign's exclusion of news/LLM trading signals or authorize trading integration.

## Investor objectives and constraints

- **Primary objective:** seek durable, diversified, net compound growth in GBP over years, with a meaningful margin over the selected GBP cash alternative: NatWest Select, whose public terms currently state 0% in-credit interest. It is a current-account cash counterfactual, not an interest-bearing savings product; verify the applicable terms before using its rate.
- **Starting capital:** approximately GBP 1,000. The main comparison starts with one initial amount and assumes no later deposits or withdrawals. Compounding is from retained investment proceeds only.
- **Return aspiration:** 30% net growth over roughly six months is an aspiration, not a forecast, promise, pass condition, or reason to increase risk. Repeating 30% every six months would be about 69% annualized and is an exceptionally demanding objective.
- **Cash-relative hurdle:** assess against NatWest Select as the selected 0% in-credit cash counterfactual, subject to confirming applicable terms. The research hurdle is at least 5 percentage points of annualized net GBP return above that comparator, after consistently modeled costs and tax assumptions. This is a decision threshold, not an expected return or savings yield.
- **Risk preference:** moderately tolerant of losses and willing to accept that the full starting amount could be lost, while preferring diversified, repeatable evidence over a high-risk bet. Acceptance of total-loss risk is not a target allocation or a reason to ignore drawdowns.
- **Drawdown review:** a 20% peak-to-trough drawdown is an alert for human review, not a guaranteed maximum-loss limit, automatic trading halt, or stop-loss promise.
- **Turnover and friction:** frequent small trades can lose money to spread, slippage, fees, FX, product charges, and taxes even when headline trade returns are positive. Net GBP account growth and turnover-adjusted cost attribution matter more than win rate.
- **Jurisdiction and currency:** the investor is UK-resident and begins with GBP. Product eligibility, broker permissions, currency conversion, tax treatment, and any tax wrapper must be checked for the actual investor and instrument. US symbols already present in the bot are not presumed eligible or suitable.

## Agreed research controls

- Keep the candidate search bounded to **60 distinct candidate configurations for the entire campaign**, including baselines. A distinct strategy, instrument universe, timeframe, parameter set, or portfolio-weight recipe counts as a candidate. Record every attempted candidate; do not add candidates after seeing results without registering a new research cycle.
- Use a **core, unlevered profile** with research-only limits of 0.50% planned risk per active trade, 2.00% aggregate planned stop risk, and 1.00x gross exposure. These limits are study assumptions, not current live settings.
- Test leverage only as a **separate challenger**: no more than 1.10x gross exposure and no more than 0.25% planned risk per leveraged trade. Never mix its result into the unlevered core result or present leverage as necessary to meet the return aspiration.
- Keep any crypto allocation in a separate sleeve capped at 5% of portfolio value. Crypto is not a core diversification substitute; the research must be allowed to reject it entirely.
- Use public or user-supplied historical data only. Do not use paid feeds or authenticated broker data for this campaign. No broker account access, orders, deposits, operational database access, or live configuration changes are authorized by this research context.
- Predeclare hypotheses, data, candidate budget, costs, metrics, and decision rules. Use chronological selection windows and one locked, single-use, report-only final holdout. The holdout can confirm, reject, or leave the locked candidate inconclusive; it cannot select a replacement or be tuned against.
- A valid outcome can be **no strategy passes** or **inconclusive**. Do not force a winner.

## Strategy research priorities

1. **Diversified, lower-turnover trend:** prioritize a small set of transparent daily/weekly trend hypotheses across products verified as available and appropriate for a UK retail investor. Consider distinct equity, bond, gold, and broad-commodity exposures where suitable data and products exist. Compare one horizon with a small, predeclared ensemble; do not assume more markets or horizons improve results.
2. **Passive and cash alternatives:** compare with the selected NatWest Select cash counterfactual and simple diversified passive alternatives. Include product charges, distributions, currency exposure, and the same tax/wrapper assumptions used for the strategy. Recheck the actual cash-account terms/rate rather than assuming a stale or promotional rate.
3. **Current bot strategies as references, not presumed winners:** retain the current SPY/QQQ 15-minute mean reversion, BTC/USD hourly breakout, and GLD/USO four-hour trend rules only as reference candidates where data, product, and UK access gates permit. SPY and QQQ have overlapping equity exposure; USO is a futures-based product rather than spot oil; crypto has distinct venue, liquidity, fee, and stop-execution risks.
4. **Small, interpretable challengers:** if prerequisites are met, test only a few predeclared changes such as slower trend horizons, measured re-entry rules, cost-aware entry filters, or structurally wider stops with quantity reduced to keep planned dollar risk constant. Attribute each challenger to one hypothesis.
5. **Explicit exclusions:** do not use martingale/grid doubling, unconditional averaging down, unconstrained Kelly or mean-variance optimization, broad indicator mining, machine learning, news/LLM signals, or repeated parameter sweeps as a route to the return aspiration.

## Interpretation principles

- A backtest cannot establish the "most effective" approach or predict a six-month return. Academic evidence on diversified trend and volatility management motivates hypotheses; it does not transfer published results to this strategy, account, instrument, or period.
- Compare net GBP returns with cash and passive alternatives on matched periods and consistent tax assumptions. Separate market beta, currency effect, instrument exposure, leverage, and strategy contribution.
- Judge consistency across distinct regimes, drawdown depth/duration, tail losses, turnover, exposure concentration, natural trade counts, and cost drag—not just CAGR, Sharpe, win rate, or the best window.
- A stop does not guarantee its price or execution. Gaps, halts, outages, partial fills, unfilled stop-limits, and liquidity constraints can exceed planned stop risk.
- If instrument eligibility, data quality, trading costs, or sample size cannot be established, report the limitation and stop or mark the result inconclusive.

## Research references

- [FCA: PRIIPs disclosure](https://www.fca.org.uk/markets/priips-disclosure) — verify current disclosure and retail product rules before treating a product as accessible.
- [HMRC: Individual Savings Accounts](https://www.gov.uk/individual-savings-accounts) — verify current ISA eligibility and provider access for any proposed holding.
- [HMRC: Tax on foreign income](https://www.gov.uk/tax-foreign-income) — UK-resident foreign investment income may have UK reporting/tax consequences; confirm the investor's circumstances.
- [HMRC: Approved offshore reporting funds](https://www.gov.uk/government/publications/offshore-funds-list-of-reporting-funds) — check current status for the exact fund and share class; the list is updated regularly.
- [Alpaca: Trading for non-US residents](https://alpaca.markets/docs/trading-non-us-residents/) — broker availability and eligible assets must be independently confirmed; bot support is not proof of account eligibility.
- [Moskowitz, Ooi, and Pedersen: Time Series Momentum](https://www.aqr.com/Insights/Research/Journal-Article/Time-Series-Momentum) and [Hurst, Ooi, and Pedersen: A Century of Evidence on Trend-Following Investing](https://www.aqr.com/Insights/Research/Journal-Article/A-Century-of-Evidence-on-Trend-Following-Investing) — motivation for diversified trend hypotheses, not a return guarantee.
- [Moreira and Muir: Volatility Managed Portfolios](https://www.nber.org/papers/w22208) — motivation to test risk-aware sizing, not a universal rule.
- The project [historical codebase review](reviews/CODEBASE_REVIEW.md) is a source of hypotheses and limitations, not a current defect list or a substitute for checking current code.