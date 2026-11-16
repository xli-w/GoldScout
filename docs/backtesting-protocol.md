# Backtesting Protocol

## Current investment protocol boundary

The [current mandate](investment-research-context.md#current-long-term-investment-mandate)
defines a substantial-capital core-plus-dynamic portfolio, not the original
GBP 1,000 short-horizon trading question. It supersedes that campaign's objectives
and passive-core risk assumptions. The remainder retains the historical
design and runner limitations; it does not authorise crypto, leverage, stop-sized
passive holdings or the former six-month return aspiration in the new core.

The first [investment release](investment.md) provides offline GBP accounting,
recorded valuation and export-only contribution/allocation proposals. A separate
synthetic-only mechanics harness tests a narrow buy-only portfolio clock,
including unitized cash-funded withdrawals without liquidation and explicit
distribution receivables across ex-open/pay-close boundaries; it is not an
integrated historical product-performance simulator, research authorisation or
broker adapter. Existing minute experiments still cannot answer this new
portfolio question.

Continuation order: complete the external product/account, licensed-history and
vintage, cash-terms, action/FX, cost/tax and frozen-protocol feasibility gates
before product-performance work. Synthetic-only simulator engineering and
regressions are permissible as engineering evidence, but are not return research
and do not satisfy any external gate. If any gate remains unmet, stop before
loading product histories or producing comparative returns.

Before any portfolio performance comparison:

1. Verify exact product/UK/account/wrapper eligibility, licensed persistent data
   use, historical price basis/actions, cash terms and point-in-time FX where needed.
2. Validate one chronological funded portfolio clock and causal valuation/action/
   cash-flow conventions. A final-cutoff normalized view must not expose future
   data at earlier decisions. Actual fund prices often embed expenses/income:
   prevent double-counting OCF and accumulating distributions.
3. Register the small fixed passive comparison set in the investment guide,
   benchmarks, base and 1.5x/2.0x variable-cost cases, contribution scenarios,
   calendar, five-percentage-point drift rule and metrics before looking at returns.
   Keep 60 as the total ceiling, not a search target; record failed/manual variants.
4. Distinguish risk-preference-based passive allocation choice from active-model
   selection. Preserve every previously exposed sample and consumed holdout.
   Use genuinely unexposed/reserved confirmation intervals only.
5. For any active challenger, approve the exact signal and cash-funded exposure,
   passive-relative improvement, observation and dependence-aware uncertainty
   criteria first. The historical +5-point NatWest rule is not the new universal
   passive admission gate. No passing active candidate means no holdout simulation,
   including baseline-only fallback. Locked confirmation is report-only.

Primary future comparison: the approved core-plus-dynamic policy versus deploying
the same actual starting capital entirely under the approved passive-core policy,
with matched dated contributions, costs, tax/wrapper assumptions and event clocks.
The starting amount and exact policy are not yet supplied. Include a no-later-flow
case and separately labelled contribution cases; deposits are not performance.
Also compare applicable designated savings terms where supported. The previously
chosen BoE GBP 1,000 excluding-bonus rate remains a market-average proxy/sensitivity,
with bonus-inclusive sensitivity, not the larger account's realised yield.
Retain NatWest 0% and GBP 1,000 only as labelled historical/sensitivity context.
Short histories or missing regimes remain limited/inconclusive, never repaired
with synthetic or unlicensed proxy data. No comparison guarantees profitability.

### Core-plus-dynamic admission requirements

- Freeze core policy and the dynamic sleeve's capital cap, position/aggregate
  exposure, turnover/cost budget, loss accounting, stop/review conditions and
  replenishment/profit-transfer rules before selection. The illustrative
  80-90% / 10-20% range is not an approved allocation. A falling sleeve must not
  be automatically refilled to its target by a generic rebalance.
- Evaluate dynamic results and combined results against a matched all-core
  counterfactual. Attribute beta, overlapping core exposure, FX, distributions,
  costs and active decisions; do not count core gains as experimental alpha.
  Sleeve transfers are internal flows, not strategy profit. Include dependence,
  drawdown duration, tail scenarios, concentration and operational complexity.
- Start with a small interpretable daily/weekly long-only hypothesis family,
  not a broad indicator/LLM search. Exact products and parameters require a frozen
  specification. The existing 60-candidate ceiling includes prior/manual/failed
  variants and baselines; it is not a target to fill.
- Predeclare whether a dynamic hypothesis seeks net excess growth, risk reduction
  or diversification, its passive-relative admission threshold, sufficient
  observations, dependence-aware uncertainty and adverse-cost failure rules.
  Compare at the approved funding/risk constraints and disclose risk mismatch.
  Neither cash outperformance alone nor a positive backtest justifies admission.
- Existing news/LLM exclusions remain in force for existing runs. A proposed
  calendar gate or AI-derived feature needs separate explicit approval, defined
  coverage/failure behaviour and a new prospectively recorded protocol before
  affecting decisions. Preserve source revisions, receipt/validated-completion
  time, model/prompt/input hashes and every model/manual trial. Today's LLM on old
  headlines is contaminated retrospective analysis, not causal holdout evidence.
- Missing intelligence may invalidate a dependent experimental decision under
  its approved failure policy; it must not force liquidation or change the core.
  Nondependent accounting, monitoring and protective recovery remain separate.
  No strategy can edit risk limits or admit its own replacement.
- Selection, one genuinely unexposed locked report-only confirmation, prospective
  shadow observation and separately approved broker trials are distinct gates.
  No passing candidate means no holdout opening and no experimental deployment.
  Manual execution/export-only operation remains valid if safe recovery cannot
  be established. This documentation enables none of these operational steps.

## Historical trading protocol and runner evidence

The remaining campaign objectives, capital, profiles and dated feasibility
screens retain historical provenance. They do not override the current
core-plus-dynamic protocol above. Technical runner/data limitations still matter;
the earlier GBP 1,000 and BoE-primary screen is not the new capital/benchmark policy.

This protocol operationalizes [investment research context](investment-research-context.md). It tests whether a bounded set of approaches merits further consideration; it cannot guarantee returns, certify live readiness, or force a winner.

## 1. Non-negotiable boundaries

- Research is offline. Use public or operator-supplied historical data only; no paid or authenticated broker data, credential access, broker order/account calls, deposits, or operational database/snapshot/log access.
- Do not start `bot.main`, place or cancel orders, change account settings, or alter live strategy/risk configuration.
- Keep the main comparison to one initial GBP 1,000-equivalent contribution and no later deposits/withdrawals. Record the exact initial amount and treatment of cash and distributions.
- The agreed unlevered research profile is 0.50% planned risk per active trade, 2.00% aggregate planned stop risk, and 1.00x gross exposure. The separate leverage challenger is capped at 1.10x gross exposure and 0.25% planned risk per leveraged trade. Crypto is separately capped at 5% of portfolio value. Do not optimize these risk limits.
- Treat 20% peak-to-trough drawdown as a review flag, not a guaranteed loss cap or automatic halt.
- The total predeclared candidate budget is 60 distinct configurations, baselines included. Record invalid and abandoned trials. Cost stresses and walk-forward windows are not extra candidates, but they must be fixed in advance and reported.
- Do not repeatedly query historical windows to choose parameters. The final holdout is evaluated once, after locking; it cannot be used to select a replacement.

## 2. Feasibility gates before any performance run

### Investor and product eligibility

For each instrument, record exact product/share class, ISIN, exchange, trading currency, domicile, distribution policy, annual product charge, broker availability, minimum order/fractional rules, and the source/date of each fact. Verify current UK retail access, relevant disclosure requirements, and whether the proposed account or tax wrapper can hold it. A US ticker supported by the bot is not presumed available or suitable.

Use NatWest Select as the chosen GBP cash counterfactual. Its public current-account terms state 0% in-credit interest; verify applicable terms, account eligibility, access restrictions, fees, and tax treatment before assigning the cash rate. It is not an interest-bearing savings product. Do not substitute a policy rate or promotional headline.

### Data sufficiency and provenance

Use public or user-supplied data with documented license/terms. Freeze raw inputs and record provider, retrieval date, currency, timezone, resolution, corporate-action treatment, missing-data policy, and cryptographic checksums. Maintain a separate, timestamped GBP FX series for every non-GBP asset and transaction.

Prefer at least five years of trustworthy data spanning materially different regimes where product history permits. If history is shorter, misses relevant stress regimes, or cannot support the chosen bars and costs, label evidence underpowered/inconclusive rather than extending it with synthetic prices. Do not substitute spot prices for an ETF, futures pool, or other product with different return mechanics.

Validate exchange sessions, holidays/early closes, daylight-saving changes, completed bars, split/dividend/other corporate actions, distribution reinvestment, instrument inception, missing intervals, and currency timestamps. Reject incomplete, sparse, off-grid, conflicting, or survivorship-biased data; never invent continuity to make a run pass.

### Simulator and accounting capability

Before a result can answer the portfolio question, the simulator must maintain one chronological, account-level GBP cash/equity clock across instruments and strategies, preserve cash constraints, apply simultaneous-event rules deterministically, and account for FX and distributions without future information. It must model the agreed portfolio-level risk/exposure limits and distinguish planned stop risk from gap and execution loss.

Signals may use only completed information available at decision time. Entries and exits must use the next executable observation under a predeclared fill model. Model stop gaps, intrabar OHLC ambiguity, unfilled stop-limit orders, partial/missed execution where relevant, and costs on both entry and exit. Document what the data cannot resolve; shared strategy code does not imply live execution parity.

## 3. Candidate universe and hypotheses

Choose exact eligible instruments and strategy definitions only after feasibility checks. Count every distinct configuration toward the 60-candidate budget.

1. **Benchmarks:** NatWest Select as the selected 0% in-credit GBP cash counterfactual (subject to verified terms); a broad diversified passive equity holding; and a predeclared balanced passive allocation where reliable total-return and GBP conversion data exist. Include product charges, distributions, FX, and a stated tax scenario. A single US equity ETF is not a sufficient diversified benchmark.
2. **Reference strategies:** where eligible data exists, include current bot rules as references: SPY/QQQ 15-minute mean reversion, BTC/USD hourly breakout, and GLD/USO four-hour trend. Do not presume investability or profitability. Keep crypto separate and enforce its 5% sleeve cap; account for USO's futures-roll/product structure.
3. **Primary challengers:** a small, fixed family of lower-turnover trend rules across eligible equity, bond, gold, and broad-commodity exposures. Compare one horizon with a small predeclared ensemble. Do not count overlapping products as independent diversification.
4. **Limited ablations:** only a few one-hypothesis changes, such as trend-state re-entry versus crossover-only, slower horizons, cost-aware entry thresholds, or stop distance with quantity adjusted to preserve the same planned dollar risk.
5. **Separate leverage challenger:** test only as a separate portfolio under the 1.10x/0.25% caps. Report leveraged and unlevered results side by side; leverage cannot rescue a failing unlevered case.

Do not tune risk caps, search every indicator, vary many correlated parameters, or add candidates after seeing selection results. A new idea requires a new registered search and accounting for prior search history.

## 4. GBP accounting, costs, and tax scenarios

Report a full GBP-denominated account ledger and equity curve. Convert foreign-currency cash flows and valuations using point-in-time FX; show FX contribution separately. Apply realistic conversion costs on initial and later conversions. Do not treat GBP conversion as free or translate only the final balance.

Itemize by instrument and side where possible: commissions and exchange/regulatory fees; bid-ask spread, market impact, slippage, missed/partial fills; crypto maker/taker fees using a verified tier plus venue spread/liquidity and unfilled stop-limit behavior; FX fees; product expense/tracking difference; custody/platform charges and minimum-order costs; cash yield, financing, borrow, distributions, withholding, and other modeled cash flows.

Run a base case using documented plausible estimates and predeclared adverse cost cases (at minimum 1.5x and 2.0x variable execution costs). Keep fixed charges and product expenses explicit instead of multiplying them blindly. Show gross return, each cost component, total cost drag, and cost drag in GBP and as a percentage of starting capital and traded notional.

Show pre-tax results and clearly labeled tax scenarios only after confirming investor and product facts. Do not assume an ISA, tax rate, foreign-tax credit, reporting-fund status, or exemption. For an offshore fund, verify the exact share class against the current HMRC reporting-funds list. Separate taxes actually modeled from unresolved tax advice.

The cash-relative hurdle is **5 percentage points of annualized net GBP return above the NatWest Select 0% in-credit counterfactual**, using consistent periods and tax treatment. Reconfirm its terms and rate over the tested period; do not treat it as an interest-bearing savings yield. Also show a pre-tax comparison if individual tax facts are unavailable.

## 5. Chronological design and selection

- Pre-register hypotheses, candidate list/count, parameters, risk profiles, instruments, benchmark, data sources, costs, tax scenarios, metrics, exclusions, window dates, and decision gates. Save the canonical specification and hash before simulation.
- Use multiple non-overlapping chronological selection windows, with warm-up outside each scored interval. Windows should cover different regimes where reliable history permits. Set split and lengths before viewing results.
- Select/rank using selection windows only; distinguish natural exits from forced boundary closes. Require enough observations and uncertainty reporting for strategy frequency. Sparse trades mean inconclusive, not a reason to relax the gate.
- Adjust for all registered/manual variants that informed the decision. Preserve all tried candidates; do not report only the winner.
- Lock at most one candidate per predeclared decision unit before opening the final holdout. Evaluate that locked candidate and fixed baselines once on the final report-only holdout, at base and adverse costs. If no candidate passes selection, do not open the holdout to hunt for a better one.
- Where continuous portfolio-level walk-forward is supported, start with actual initial capital, carry cash/equity through time, and apply only decisions made from prior data. Do not stitch independently reset windows into a purported continuous compounded return.

## 6. Required metrics and decision rules

For every candidate, benchmark, window, and cost case, report: gross/net GBP total and annualized return, ending equity and sample length; excess versus the selected cash and passive benchmarks including the 5-point hurdle; maximum drawdown and duration, worst day/month/year, tail loss/expected shortfall, volatility, Sharpe/Sortino with stated GBP cash/risk-free series, and uncertainty intervals; turnover, natural exits, holding period, time invested, average/peak gross exposure, planned stop risk, concentration, and blocked/skipped orders; and cost components in GBP, as a fraction of starting capital and traded notional. Attribute market exposure, strategy, FX, leverage, and distributions where supportable.

A candidate is eligible for final confirmation only if product, data, accounting, and execution gates pass; it beats the cash-relative hurdle on net GBP selection results; results are not dominated by one window, instrument, or a few trades; and the case survives predeclared adverse costs. Predeclare minimum independent observations and uncertainty rules appropriate to each strategy before the run. A positive point estimate alone is not robust evidence.

The locked holdout confirms, rejects, or leaves the candidate inconclusive; it is not a second selection set. A result below the hurdle, materially fragile to costs/regimes, or supported by too few observations fails or is inconclusive. Any 20% drawdown crossing triggers a documented review. No result is a forecast, guaranteed maximum loss, or proof of live profitability.

## 7. Execution sequence

### Phase 0: establish feasibility (no backtest)

1. Verify product/account eligibility, the selected cash comparator and its applicable terms, tax/wrapper assumptions, and source/licensing conditions for intended public/user-supplied datasets.
2. Confirm common point-in-time GBP FX and total-return/corporate-action coverage over all selection and holdout periods.
3. Close the runner gaps below or explicitly narrow the question to a per-strategy USD simulation that cannot answer the GBP portfolio objective. Do not describe a narrower output as portfolio suitability.
4. Write and hash the finite specification. Confirm at most 60 candidates and complete data/cost/risk assumptions.

### Current CLI boundary

From the repository root, `python -m bot.experiments prepare .\experiment.json` is a coverage dry-run only unless `--allow-network` is supplied. Do not supply that flag: it fetches through the Alpaca history client and is outside this campaign's approved data boundary. The separate offline importer is `python -m bot.campaign_data import .\manifest.json --dataset-dir .\campaign_data` followed by `python -m bot.campaign_data validate DATASET_ID --dataset-dir .\campaign_data`; see the [campaign dataset contract](campaign-dataset.md). It snapshots and validates user-supplied daily/weekly product histories, GBP-per-unit FX, cash rates, and corporate actions only. It is not connected to the USD minute runner, does not create a runner-compatible Dataset v1, and does not provide simulation or GBP portfolio accounting. Existing `bot.experiments validate/run` commands still operate only on their minute datasets, and do not answer the portfolio objective until the documented simulator gaps are closed.

### Recommended continuation (no data acquisition or performance run)

1. **Importer integrity:** retain focused regressions for a handled write failure and simultaneous identical imports. They verify staging cleanup and atomic publication, but do not simulate abrupt process termination or establish cross-process filesystem behavior.
2. **Feasibility evidence:** before building product-specific checks, identify the intended eligible products, public/user-supplied licensed sources, and applicable terms for the selected GBP cash comparator. These are external facts; stop if they cannot be substantiated.
3. **Data-readiness audit:** the separate importer offers scoped `readiness` checks for XLON daily GBP units, sessions and declared bar/cash-rate availability, plus a read-only `normalize` view. The separate `fx-readiness` audit checks declared FX publication and maximum age only at supplied clocks; `fx-view` returns the complete selected observations at those clocks or rejects the view. Generic synthetic fixtures validate these engineering contracts; they do not prove complete event clocks or historical vintages, provide executable FX prices, or close external gates. See the [GBP](campaign-dataset.md#scoped-gbp-daily-readiness-audit) and [FX](campaign-dataset.md#complete-fx-observation-view) contracts. Price-basis truth/action reconciliation and foreign-currency conversion remain unsupported/unverified; never fill gaps or infer source facts.
4. **Synthetic simulator mechanics:** `investment.simulation.simulate_synthetic_fixture` checks synchronized GBP observations, event-time availability, next-session-open buy-only fills, contribution/withdrawal unitization, quantity/minimum handling, an explicit proportional cost assumption, increment-preserving splits, per-unit distributions based on ex-open holdings and paid at a later modeled close, and explicit synthetic annual rates (0-1) accruing by simple ACT/365 between modeled boundaries. Withdrawals use available cash only; they do not liquidate holdings, and a cash shortfall fails closed. Distribution receivables contribute to portfolio and contribution-allocation target value from the ex-open until payment, but are not spendable cash. Same-product splits at the ex-open are rejected. Price bars must be unadjusted for the supplied distribution, but provenance and basis cannot be verified. Unit value remains linked through full cash redemption and later contribution. The first open requires a rate; rate changes take effect only at modeled opens/closes, after interest through that clock is accrued. It is not connected to the campaign importer or any product history. It omits sales/liquidation, distribution source/entitlement verification, taxes/withholding, actual cash-product terms/accrual conventions, FX, inflation, fixed fees, spreads, slippage and real calendar/execution behavior. Its output is engineering-only and cannot support product returns or close a feasibility gate.
5. **Accounting contract:** the separate pure fill helper checks ordered GBP-normalized long-only fill events, cash conservation and quantity conservation. It is not a portfolio valuation/accounting system; cash accrual, market marks/equity, distributions/actions, FX, risk, performance and costs beyond supplied fill fees remain unsupported. Any further accounting implementation must retain explicit action entitlement/payment and cash-rate conventions; no feasibility or performance claim follows from the helper.
6. **Campaign integration:** only after those prerequisites are independently validated, assess integration and enforcement of the registered candidate budget, adverse-cost selection, and closed-unless-selected holdout. This order does not authorize a campaign run.

### Current public-feasibility screen (2026-11-15)

This is a desk review of public documentation only. It does not inspect the
investor's broker account, API entitlements, product availability, savings
eligibility, or data-license terms, and it authorizes no account/API calls.

- **Trading 212:** Its [Public API documentation](https://docs.trading212.com/api)
  currently describes a beta API for Invest and Stocks ISA accounts, including
  demo/live environments, tradable-instrument metadata (including ISIN and
  currency), and market, limit, stop, and stop-limit orders. API operations use
  the account's primary currency; multi-currency API trading is not documented.
  The [stop](https://docs.trading212.com/api/orders/placestoporder_1) and
  [stop-limit](https://docs.trading212.com/api/orders/placestoporder) endpoints
  warn that submissions are not idempotent. The reviewed public API reference
  does not establish a client-assigned durable order ID, linked/OCO protection,
  or a generic historical OHLCV feed. Therefore, GBP API access, exact product
  eligibility, licensed historical prices, and safe recovery after an
  ambiguous submission remain unverified. Trading 212 is a research candidate,
  not a selected or safe unattended adapter.
- **Alpaca:** The public [non-US account guidance](https://alpaca.markets/learn/live-trading-account-non-us)
  and [US funding documentation](https://docs.alpaca.markets/us/docs/funding-accounts)
  concern the existing US Trading API account path. Alpaca's separate
  [Europe Broker API](https://docs.alpaca.markets/eu/docs/about-broker-api) is
  designed for fintech partners and European investment-services firms; its
  documentation describes local-currency trading in EUR for U.S. equities.
  This separate partner product does not establish that the investor's
  existing account can hold GBP cash or trade the specified instruments.
  Account-specific venue, asset, currency, funding, and API entitlements
  remain unverified. Do not query or change the account to verify them without
  separate task-specific approval.
- **Candidate instruments:** Four ISINs were supplied for screening:
  [US78462F1030 / SPY](https://www.ssga.com/us/en/individual/etfs/funds/spdr-sp-500-etf-trust-spy),
  [US46090E1038 / QQQ](https://www.invesco.com/us/en/financial-products/etfs/invesco-qqq-trust-series-1.html),
  [US78463V1070 / GLD](https://www.ssga.com/us/en/individual/etfs/funds/spdr-gold-shares-gld), and
  [US91232N2071 / USO](https://www.uscfinvestments.com/uso). These identify
  U.S. instruments whose primary listings are in USD; the ISINs alone do not
  establish a GBP-denominated trading line. USO is a commodity pool using oil
  futures, not spot oil. Their exact UK retail/account eligibility, any
  alternative listing, share-class terms, and treatment of FX, actions and tax
  have not been established. The screening scope is explicitly broadened to
  include these USD primary-listing candidates. The campaign objective remains
  net GBP growth, so any future comparison must include causally available FX
  conversion and associated costs; the scope change does not establish that
  these instruments are accessible or ready for simulation.
- **Cash comparator:** The Bank of England's [quoted household interest
  rates](https://www.bankofengland.co.uk/statistics/quoted-household-interest-rates)
  are published rate series for defined product categories, not the
  selected product's account terms. They are not used in place of the chosen
  NatWest counterfactual. [NatWest's current-account terms](https://www.natwest.com/current-accounts/rates-and-charges.html)
  identify Select as a current account with no in-credit interest. It is the
  selected 0% cash counterfactual, not an interest-bearing savings product.
  Its applicable account terms, access restrictions, rate history, charges,
  eligibility, and tax treatment have not been independently verified.
- **Historical market data:** The [London Stock Exchange historical-data
  products](https://www.londonstockexchange.com/equities-trading/market-data/historical-analytics-data-products)
  are a possible source to investigate, not evidence that a suitable dataset
  is publicly available or licensed for this campaign. No product-specific
  daily history, total-return/action coverage, or permissible-use terms have
  been confirmed. The existing [Alpaca/Tiingo comparison](alpaca_vs_tiingo_historical_data.md)
  covers older U.S. intraday strategy research and is not authorization to
  fetch data for this campaign. Authenticated Alpaca broker data is outside
  the approved campaign boundary. Tiingo's [current terms](https://www.tiingo.com/tos/)
  (updated 2026-11-05) prohibit persistent storage of data and derived
  products on Starter/Trial plans and limit predictive-model backtesting
  rights to active Power/Power Commercial subscriptions; persistent data must
  also be deleted when such a subscription ends. Since this campaign excludes
  paid feeds and requires durable, checksummed offline datasets, no standard
  Tiingo plan is currently cleared. No provider dataset was downloaded. Use
  only a different public or operator-supplied source whose current license
  permits the needed persistent research use, or obtain explicit approval to
  revise those constraints.
- **FX reference:** The [Federal Reserve H.10 release](https://www.federalreserve.gov/releases/h10/about.htm)
  publishes the previous week's daily noon reference rates in a weekly release,
  not contemporaneously on the observation day. Past releases may differ from
  revised history. The [DEXUSUK series](https://fred.stlouisfed.org/series/DEXUSUK)
  is USD per GBP, the opposite direction from this importer's GBP per USD.
  Publication/vintage timing, rights, direction and conversion costs need
  explicit evidence; do not treat these reference rates as executable prices
  or assign them earlier availability.

**Feasibility outcome: blocked.** The screening scope now includes the
identified USD instruments and the cash counterfactual is NatWest Select, but
these choices do not establish product/account eligibility, usable FX and
market histories, data rights, or action coverage. The 30% six-month return
aspiration remains an aspiration, not a pass criterion. Public API
documentation does not clear these gates or establish safe order recovery.

### GBP passive-product feasibility follow-up (2026-11-16)

This is a public-source follow-up for the new long-term GBP portfolio question;
it does not replace the historical campaign's NatWest counterfactual or change
its candidate ledger. No account, credentials, data feed or broker API was
accessed. Issuer pages returned product facts and limited displayed price rows;
these were not downloaded, retained or analyzed as return inputs.

- **User-set cash benchmark and data boundary:** the investor selected the Bank
  of England's monthly quoted household instant-access deposit rate for a
  £1,000 balance, excluding guaranteed bonuses as the primary cash proxy and
  showing the bonus-inclusive series as a sensitivity. The [BoE methodology](https://www.bankofengland.co.uk/statistics/details/further-details-about-quoted-household-interest-rates-data)
  says these are weighted advertised rates, gross of tax, with fees omitted; the
  product sample changes and values can be revised. This is not an account the
  investor necessarily has or the rate they would have earned. Instant-access
  rates are selected for £1,000 balances; both bonus treatments have series
  from January 2011. The BoE [Database terms](https://www.bankofengland.co.uk/legal)
  state that statistical data may be reused under the UK Open Government
  Licence, subject to its terms/attribution. No rate series was downloaded; the
  exact series identifier and vintage convention must be frozen before data
  use. The investor confirmed the existing public/user-supplied-only market-data
  policy; paid-data licensing research or acquisition is not authorized.
- **Fund candidates (not selections):** issuer pages identify VWRP
  (IE00BK5BQT80; accumulating, Irish UCITS, global developed/emerging large/mid
  caps, USD base, GBP listing, 0.14% displayed ongoing charge, share-class
  inception 2019-07-23) and VAGS (IE00BG47K971; accumulating, Irish UCITS,
  global investment-grade/government bonds, GBP-hedged, 0.08% displayed ongoing
  charge, share-class inception 2019-06-18). Both have only post-2019 live
  share-class history. A GBP trading line does not hedge equity currency
  exposure; bond hedging does not remove duration/credit risk. Issuer factsheets
  and KIDs are linked from the [VWRP](https://www.vanguard.co.uk/professional/product/etf/equity/9679/ftse-all-world-ucits-etf-usd-accumulating)
  and [VAGS](https://www.vanguard.co.uk/professional/product/etf/bond/9685/global-aggregate-bond-ucits-etf-gbp-hedged-accumulating)
  pages. Confirm exact ISIN/share-class terms before use.
- **Account/provider economics:** Trading 212's published
  [Invest/ISA/SIPP tariff](https://helpcentre.trading212.com/hc/en-us/articles/11471996799517-What-are-the-fees-in-the-Invest-ISAs-and-SIPP)
  lists free dealing commission/custody and a 0.15% FX fee. Its public API is
  beta, supports Invest/Stocks ISA only, uses the account's primary currency
  and does not support API multi-currency. Trading 212 says most instruments
  can be fractional, with permitted precision varying by instrument/position;
  that does not establish access to either fund or its fractional terms in the
  investor's account. API market-order submissions are explicitly
  non-idempotent, so this is not cleared for unattended execution. Vanguard's
  [self-managed fee page](https://www.vanguardinvestor.co.uk/what-we-offer/fees-explained)
  lists £4 monthly below £32,000 invested, before fund costs: £48 is 4.8% of
  £1,000 in year one. This is a platform-cost finding, not a judgement on
  Vanguard funds held elsewhere. The investor also requested an Alpaca screen:
  [its public non-US account guide](https://alpaca.markets/learn/live-trading-account-non-us)
  includes UK applicants, but the existing Alpaca API path is USD-oriented and
  does not establish GBP cash, ISA access or access to VWRP/VAGS. The separate
  [Europe Broker API](https://docs.alpaca.markets/eu/docs/about-broker-api)
  is intended for fintech/investment-service partners; its documented initial
  account types are taxable and margin, not a direct retail ISA. Neither
  provider's exact account/product access was checked.
- **Cash and protection:** NatWest Select remains a 0%-interest current-account
  counterfactual, not the primary interest-bearing savings benchmark. Marcus's
  [official page](https://www.marcus.co.uk/uk/en/savings/online-savings-account)
  displays a variable 3.75% AER including a first-12-month bonus, but explicitly
  dates its information 2025-08-13; it is not a verified current rate or a
  historical account-rate series. No personal savings product is selected: the
  BoE proxy above is used instead. FSCS confirms eligible bank deposits
  are protected up to £120,000 per person per authorised firm from 2025-12-01;
  investment protection is distinct and does not reimburse market losses.
- **Tax:** GOV.UK lists 2026/27 ISA allowance/eligibility rules, but the
  investor's eligibility and provider access are unconfirmed. HMRC's
  [reporting-fund guidance](https://www.gov.uk/hmrc-internal-manuals/investment-funds/ifm13100)
  explains UK tax on distributions and reported income, including undistributed
  amounts. The current monthly [HMRC list](https://www.gov.uk/government/publications/approved-offshore-reporting-funds)
  was updated 2026-11-04, but exact VWRP/VAGS ISIN status and periods have not
  been independently checked in the published ODS. Do not assume an ISA or
  taxable-account scenario.
- **Data rights and history:** Vanguard's
  [website terms](https://www.vanguard.co.uk/uk-fund-directory/terms-and-conditions)
  permit only personal, informational, non-commercial use of site materials
  (unless otherwise authorised) and prohibit repeated automated access and
  copying/exploitation without permission. This is not a licence for scraping
  or maintaining the research dataset. No persistent-use rights have been
  confirmed for daily prices/NAV or total return, actions/distributions, GBP
  FX, historical vintages or derived research series. A vendor/exchange source
  may require a separate agreement; no dataset or agreement has been acquired.
- **Portfolio-data and modelling gap:** a live accumulating ETF history cannot
  provide pre-inception returns for those share classes. Any total-return input
  must not double-count retained fund income/charges. Actions, price basis,
  distribution treatment, FX availability, spreads and comparable costs must
  be evidenced together on a chronological GBP clock; no proxy splicing is
  approved.

**Outcome: public feasibility screen complete; performance gate remains
blocked.** The user-set BoE benchmark convention and public/user-supplied-only
data boundary are recorded. Trading 212 has the stronger public fit for a GBP
ISA-shaped screen; Alpaca remains a USD-oriented alternative with no established
access to the selected funds. Neither account nor instrument access has been
confirmed. Verify exact HMRC entries and an expressly permitted retained
product-history source before loading any market history; if unavailable within
the approved data boundary, stop. Do not run comparative returns, acquire
product prices, or infer suitability from public tariffs. See the [investment
guide](investment.md#feasibility-follow-up-2026-11-16) for the corresponding
provider matrix.

### Phase 1: offline data and simulator validation

1. The campaign importer can snapshot and validate its documented local file contract offline. Its FX envelope check and schema validation do not establish calendar completeness, point-in-time transaction conversion, product total returns, or campaign feasibility.
2. Validate calendars, missing bars, sessions, declared price basis, total-return treatment, cash accrual, and point-in-time accounting separately before any performance evaluation; fail closed on gaps or conflicting provenance. The importer preserves source-specific provenance and declared price basis, but does not independently verify them or synthesize data.
3. Use small deterministic fixtures to check signal availability, next-fill timing, stop-gap ordering, simultaneous portfolio events, fees, FX, cash conservation, and GBP benchmarks.
4. Run `scripts/offline_tests.py` for simulator/data changes. No broker downloads, `bot.main`, operational storage, or order calls.

### Phase 2: frozen selection and cost stress

Run every registered candidate across every selection window using the fixed base and predeclared cost model. Preserve exclusions, invalid combinations, trades, and equity series. Do not change the spec in response to results.

### Phase 3: lock and one-use holdout

Select by the predeclared selection rules, write a lock record containing candidate ID, parameters, spec/dataset hashes, and timestamp, then evaluate only that candidate and fixed baselines on the holdout. If no candidate passes selection, do not open the holdout to hunt for a better one.

### Phase 4: independent interpretation

Have a reviewer who did not tune candidates inspect data provenance, timing, costs, benchmark comparability, selection leakage, and decision tables. Separate verified results from assumptions and unmodeled risks. A material change starts a new registered search; a consumed holdout cannot be reused for tuning.

## 8. Expected output package

1. **Pre-registration:** canonical spec/hash, candidate/trial ledger, selected universe/product facts, costs/tax/risk assumptions, date windows, and decision rules.
2. **Data evidence:** manifests/checksums, provider and license details, date coverage, corporate actions, FX source, validation results, exclusions, and known gaps.
3. **Machine-readable results:** one row per candidate/window/cost case with gross/net metrics, benchmarks, trial-adjusted uncertainty, natural/forced trade counts, exposure, drawdown, turnover, and costs; include failed/invalid candidates.
4. **Audit detail:** trade/fill ledger, daily GBP equity and cash curves, FX contribution, cost ledger, portfolio exposure/risk series, software/dependency versions, and random seeds.
5. **Human report:** goals/scope; pass, fail, or inconclusive answer; selection versus holdout results; net GBP cash/passive comparisons; costs/tax assumptions; drawdown/regime analysis; limitations; and decisions not supported by evidence.

The current runner writes `results.csv`, `results.json`, `summary.md`, `locked.json`, trade/breakdown CSVs, and equity curves. Preserve these where applicable, but they do not by themselves meet the GBP portfolio requirements.

## 9. Current runner readiness and blockers

The existing [`bot.experiments`](../bot/experiments/) runner supports strict JSON specs, bounded parameter grids, minute-data snapshots with checksums/coverage validation, preflight checks, trial history, parameter locking before holdout simulation, report-only holdout windows, cost stresses, and machine-readable reports. It does not yet implement this full protocol:

- `prepare --allow-network` fetches through the Alpaca history client. That authenticated/broker data path is outside this campaign's approved boundary.
- The `bot.campaign_data` CLI validates/snapshots user-supplied daily/weekly product OHLCV, declared price basis and source-specific provenance, explicitly directed GBP-per-unit FX, effective-dated GBP cash rates, and point-in-time corporate-action records. A separate read-only `readiness` profile checks scoped XLON daily GBP units, full requested sessions and declared bar/cash-rate availability without changing importer v1. Readiness reports examples; `normalize` can return a complete declared-as-of GBP daily view. `fx-readiness` checks declared publication and freshness at supplied clocks; `fx-view` returns their complete selected observations without conversion. Neither verifies historical vintages, complete transaction clocks, source facts or executable FX. Units and their evidence are never inferred. A pure synthetic fill helper checks GBP cash and quantity conservation, but does not calculate equity or returns. Synthetic inputs and missing external evidence remain unresolved; adjusted prices, recorded actions, foreign-currency accounting and weekly-only execution are unsupported. The USD minute runner still does not accept or consume these datasets. Source facts, total returns/eligibility, FX transaction-time coverage, costs/tax, cash accrual and combined GBP portfolio accounting remain unverified/unimplemented.
- The separate `investment.simulation` module is a synthetic-only mechanics harness, not a consumer of the campaign importer. It uses synchronized synthetic GBP opens/closes, buy-only contribution allocation, and an assumed proportional variable cost; it does not implement the complete accounting, data, benchmark, action, cash-interest, tax, or execution model required for product research.
- Each strategy entry is simulated on flat independent windows. There is no simultaneous combined multi-strategy portfolio/account allocation with one GBP cash ledger, cross-asset risk budget, and continuous compounded equity curve.
- The runner is USD-capital based and does not model GBP accounting/FX conversion, UK product access, ISA or other wrapper eligibility, investor-specific taxes, product-level charges, or the selected cash comparator.
- Current implementation limits differ from this campaign's limits: up to 125 trials per strategy and 500 total are allowed by the spec, while this protocol is stricter at 60 total. Selection settings and research risk profiles must be explicit; do not rely on defaults or mutate live configuration to imitate them.
- Cost stresses are evaluated for finalists/baselines, not every candidate during selection. This does not implement the campaign's requirement for adverse-cost evidence before final confirmation.
- The runner reserves holdouts before simulation and can evaluate a fixed baseline on holdout when no candidate passes selection. This campaign instead says not to open the holdout if selection fails; the current CLI does not enforce that additional gate.

**Therefore, no performance run can yet answer the agreed GBP portfolio question using the current runner alone.** The campaign-data tools provide ingestion, validation and offline views only; they do not close any outstanding GBP feasibility gate or authorize campaign runs. Further approved data and integrated simulator capabilities or an explicitly narrowed question are required. No backtest or campaign run is authorized by this protocol update.

## References and operating guidance

- Project [research invariants](safety-invariants.md), [development guide](development.md), and [README backtesting assumptions](../README.md#backtesting).
- HMRC: [foreign income](https://www.gov.uk/tax-foreign-income), [ISAs](https://www.gov.uk/individual-savings-accounts), and [approved offshore reporting funds](https://www.gov.uk/government/publications/offshore-funds-list-of-reporting-funds). Recheck current rules and the exact instrument/share class.
- FCA: [PRIIPs disclosure](https://www.fca.org.uk/markets/priips-disclosure). Verify current retail disclosure and product-access rules; this is not a legal interpretation.
- Alpaca: [non-US account guidance](https://alpaca.markets/learn/live-trading-account-non-us) and the [Europe Broker API](https://docs.alpaca.markets/eu/docs/about-broker-api) describe separate public programs. Neither verifies this investor's account-specific permissions; no authenticated access is authorized here.
- See the dated [public-feasibility screen](#current-public-feasibility-screen-2026-11-15) for current Trading 212 API, Alpaca account-program, savings-comparator, and market-data gates. These are public-document findings, not account verification or trading authorization.
- Research motivation: [Time Series Momentum](https://www.aqr.com/Insights/Research/Journal-Article/Time-Series-Momentum), [A Century of Evidence on Trend-Following Investing](https://www.aqr.com/Insights/Research/Journal-Article/A-Century-of-Evidence-on-Trend-Following-Investing), and [Volatility Managed Portfolios](https://www.nber.org/papers/w22208). These motivate tests and do not establish expected returns for this account.