# Alpaca vs Tiingo: Historical Market Data and Backtesting

**Purpose:** Evaluate Alpaca and Tiingo exclusively as historical
market-data sources for developing and backtesting a short-term trading
bot.

**Intended instruments** - SPY --- SPDR S&P 500 ETF Trust - QQQ ---
Invesco QQQ Trust - GLD --- SPDR Gold Shares - USO --- United States Oil
Fund - BTC/USD --- Bitcoin quoted in US dollars

**Intended strategy horizons:** 15-minute and 1-hour trading windows.

## Applicability to the GBP campaign

This comparison was written for the existing U.S. intraday bot strategies; it
is not a data-acquisition instruction or a GBP portfolio research approval.
The [current GBP campaign protocol](backtesting-protocol.md) is stricter:

- The supplied screening identifiers SPY (`US78462F1030`), QQQ
  (`US46090E1038`), GLD (`US78463V1070`) and USO (`US91232N2071`) are
  U.S. instruments with primary listings in USD; the ISINs alone do not
  establish a GBP-denominated trading line. They are now in the screening
  scope, but their UK retail and account eligibility must be verified before
  inclusion. Any GBP portfolio comparison needs point-in-time FX and its
  costs. USO is a futures-based commodity pool, not spot oil.
- The campaign does not permit authenticated Alpaca broker-data preparation
  or paid feeds. Tiingo's [terms of use](https://www.tiingo.com/tos/), updated
  2026-11-05, prohibit durable retention of Tiingo data and derived products on
  Starter/Trial plans. Its terms limit predictive-model backtesting rights to
  active Power/Power Commercial subscriptions, which are outside the campaign;
  those plans also require
  deletion of persisted data after the subscription ends. Thus no standard
  Tiingo plan is currently cleared for the campaign's durable offline
  snapshots. Reassess only if the license or campaign data policy changes.
- The older recommendation below to compare both providers or use their free
  access is superseded for the GBP campaign. No provider dataset has been
  downloaded or used for the campaign.
- Tiingo's [crypto API documentation](https://www.tiingo.com/documentation/crypto)
  says its top-of-book endpoint is deprecated because underlying exchange
  feeds can have unreliable ordering or timestamps. Historical OHLCV alone is
  not evidence of an executable quote, spread or broker-specific fill.
- The provider feature, price, coverage, and plan comparisons below are from
  the original research and have not been revalidated as current terms.

------------------------------------------------------------------------

## 1. Executive summary

Alpaca and Tiingo can both supply historical market data suitable for
researching strategies based on OHLCV candles (open, high, low, close
and volume). They differ in their product emphasis:

-   **Alpaca** provides a trading-oriented market-data API closely
    integrated with its brokerage platform. It offers historical bars as
    well as historical trades and quotes, which can be useful when
    investigating execution assumptions.
-   **Tiingo** is an independent market-data provider with equity and
    cryptocurrency datasets. Its consolidated equity intraday offering
    is worth evaluating as an alternative source for historical
    research.

For this project, a sensible starting approach is to test both providers
against the same instruments and dates, retain the raw datasets
separately, and choose a primary source only after checking coverage,
consistency, licensing and costs.

Do not assume that the providers' datasets are interchangeable. Their
venue coverage, timestamps, adjustment methods, session definitions and
aggregation rules may differ.

## 2. High-level comparison

  -----------------------------------------------------------------------
  Area                    Alpaca                  Tiingo
  ----------------------- ----------------------- -----------------------
  Historical US equities  Yes                     Yes

  Historical              Yes                     Yes
  cryptocurrency                                  

  OHLCV bars              Yes                     Yes

  Minute-level data       Yes                     Intraday minute data;
                                                  check product-specific
                                                  granularity

  15-minute and hourly    Supported through bar   Supported through
  data                    timeframes or           resampling options,
                          aggregation             subject to dataset

  Historical trades and   Available through       Product- and
  quotes                  relevant market-data    endpoint-dependent; do
                          endpoints and           not assume equivalent
                          entitlements            depth

  Consolidated US equity  SIP feed                Consolidated intraday
  data                                            offering; verify
                                                  historical coverage for
                                                  each symbol/date

  Corporate-action        Supported options for   Adjustment/reference
  adjustments             equity bars             datasets and
                                                  endpoint-specific
                                                  options

  API access              REST and streaming      REST APIs;
                                                  product-dependent
                                                  streaming/real-time
                                                  options

  Free access             Basic plan              Starter access

  Paid access             Algo Trader Plus listed Power listed at
                          at \$99/month           \$30/month

  Best fit                Trading-oriented data   Independent historical
                          and execution research  research and
                                                  cross-checking
  -----------------------------------------------------------------------

Pricing, quotas and entitlements change. Confirm current details
directly with each provider before subscribing.

## 3. Historical depth and candle granularity

### Alpaca

Alpaca's historical market-data API provides equity and crypto bars,
with supported timeframes including minute-based and larger intervals.
Its equity historical dataset is documented as extending back to 2016,
although the actual start date and availability can vary by symbol, feed
and endpoint.

Relevant capabilities include: - OHLCV bars. - Minute and larger bar
intervals. - Historical trades and quotes, subject to feed and
subscription access. - Pagination for retrieving larger datasets. -
Equity adjustment options, including split/dividend-related adjustment
choices where supported.

For this bot, Alpaca can provide 1-minute data that can be aggregated
into 15-minute and hourly candles. Alternatively, request supported
larger intervals directly. Building the 15-minute and hourly bars from
raw 1-minute data gives the project greater control over candle
boundaries and validation.

### Tiingo

Tiingo provides historical equity and cryptocurrency data. Its
advertised long-term historical coverage should not be interpreted as
meaning that every instrument has decades of intraday minute-level
history: daily and intraday coverage are separate, and availability
varies by product and instrument.

Relevant capabilities include: - Historical equity intraday prices. -
OHLCV data and configurable resampling, subject to endpoint and
subscription. - Historical cryptocurrency data. - Separate equity
reference/corporate-action datasets. - A consolidated equity intraday
offering whose exact historical coverage should be verified for each
symbol and date range.

For both providers, test the exact data you need for SPY, QQQ, GLD and
USO rather than relying on headline statements about historical depth.

### Recommended granularity

Collect **1-minute bars where economically and technically practical**,
then derive 15-minute and hourly bars locally.

Benefits: - One canonical raw dataset supports multiple strategy
timeframes. - You can change candle construction without downloading
data again. - You can inspect price movement within a 15-minute
candle. - You can test alternative entry/exit timing assumptions.

A 1-minute OHLCV dataset still does not reveal the full sequence of
trades or the bid/ask spread. It is not sufficient by itself for precise
execution simulation.

## 4. Equity data quality: IEX versus consolidated feeds

This distinction matters for SPY, QQQ, GLD and USO.

### Alpaca IEX and SIP

Alpaca offers different US equity data feeds, notably: - **IEX:** data
from the Investors Exchange only. - **SIP:** consolidated US market data
distributed through the Securities Information Processors.

IEX is one venue and represents only a portion of overall US equity
trading. Its prices and volumes can therefore differ from consolidated
market activity. This can matter for strategies that use: - Volume
spikes and relative volume. - VWAP. - Breakout confirmation. - Momentum
and liquidity filters.

Alpaca's Basic subscription is associated with IEX real-time data and
has restrictions on access to recent SIP data. Historical SIP data older
than the applicable delay may be available without paying for real-time
SIP access. Confirm current plan entitlements and endpoint behaviour
before designing the ingestion process.

The paid Algo Trader Plus subscription has been listed at **\$99/month**
and includes broader real-time consolidated data access and higher API
limits. The subscription is not automatically necessary for a
historical-only project.

### Tiingo consolidated equity data

Tiingo offers a consolidated equity intraday product. It is potentially
useful for obtaining a broader market view than a single-venue feed.

Before relying on it, verify: - Whether historical consolidated bars are
available for every required symbol. - The earliest available intraday
date per symbol. - Whether pre-market and after-hours data are
included. - How bars are constructed and timestamped. - Whether volume
and price fields are consolidated consistently. - Whether the relevant
subscription permits the intended use.

**Practical conclusion:** For volume-sensitive strategies, prefer a
consolidated US equity feed over IEX-only data where possible. Compare
the actual datasets rather than assuming that two feeds labelled
"consolidated" have identical methodology.

## 5. Cryptocurrency data: BTC/USD

Bitcoin trades continuously, unlike US-listed equities, which have
defined exchange sessions.

Both Alpaca and Tiingo offer cryptocurrency historical data, but their
market coverage and aggregation methodology may differ.

### Alpaca

-   Historical crypto bars are available through its crypto market-data
    API.
-   OHLCV data can be used for minute-based and larger timeframes.
-   Broker and data-provider integration may simplify development if
    Alpaca is also the execution broker.

### Tiingo

-   Provides historical cryptocurrency data.
-   Its crypto offering can aggregate market information from multiple
    exchanges, depending on the endpoint and parameters.
-   Check the selected exchange set, quote currency, aggregation method
    and historical coverage.

Do not assume a multi-exchange aggregate is identical to the price feed
used by the eventual execution venue. For realistic backtesting, use the
same venue or a carefully modelled proxy for execution prices.

Also note that Tiingo has documented changes/deprecations affecting some
crypto top-of-book functionality. Verify current endpoint status before
depending on historical bid/ask data. Historical OHLCV availability
should be assessed separately from top-of-book availability.

## 6. Pricing and access

The following are indicative published individual-plan details discussed
during the comparison. They are not a guarantee of current pricing or
entitlements.

### Alpaca

  ------------------------------------------------------------------------
  Plan                              Indicative price Relevant features
  --------------------- ---------------------------- ---------------------
  Basic                                    \$0/month IEX real-time
                                                     equities; historical
                                                     access subject to
                                                     feed/delay
                                                     entitlements; 200
                                                     REST requests/minute
                                                     and 30 WebSocket
                                                     symbols have been
                                                     listed

  Algo Trader Plus                        \$99/month Consolidated
                                                     real-time US equity
                                                     data; higher limits,
                                                     including 10,000 REST
                                                     requests/minute and
                                                     unlimited WebSocket
                                                     symbols have been
                                                     listed
  ------------------------------------------------------------------------

For historical-only work, the free plan may be sufficient to start,
particularly where older SIP data is available after the applicable
delay. Check the exact historical endpoint, feed parameter and plan
restrictions.

### Tiingo

  ------------------------------------------------------------------------
  Plan                              Indicative price Relevant features
  --------------------- ---------------------------- ---------------------
  Starter                                  \$0/month Limited request and
                                                     bandwidth quotas

  Power                                   \$30/month Higher request and
                                                     bandwidth quotas;
                                                     broader access to
                                                     datasets
  ------------------------------------------------------------------------

Previously published Starter limits included 50 requests/hour, 1,000
requests/day and 1 GB monthly bandwidth. Previously published Power
limits included 10,000 requests/hour, 100,000 requests/day and 40 GB
monthly bandwidth. Verify current limits and which specific
equity/crypto products are included.

### Cost considerations

For five instruments, historical data can often be downloaded in batches
and stored locally. The recurring cost may therefore be less important
than: - Whether the required intraday history is included. - API request
and bandwidth limits. - Whether data is consolidated. - Whether quotes
and trades are available. - Whether the licence permits the intended
use.

Tiingo's individual plans have been described as intended for
personal/internal use, not redistribution or display to other people or
organisations. Review the current licence before using data in a
client-facing product or sharing datasets.

## 7. Suitability for backtesting

The right dataset depends on how sophisticated the backtest needs to be.

### Signal research using candles

For strategies driven by indicators such as RSI, moving averages, MACD,
ATR, candle patterns or basic volume filters, OHLCV data may be
sufficient for an initial research phase.

Both providers can support this type of work, provided the relevant
historical coverage and candle quality are adequate.

### More realistic execution simulation

For short-term strategies, candle-only backtesting can materially
overstate performance. A candle's close is not necessarily a price at
which an order could have executed.

More rigorous testing should account for: - Bid/ask spreads. -
Slippage. - Commissions and regulatory fees where applicable. - Partial
fills and liquidity. - Order type and fill assumptions. - Market opening
and closing conditions. - Trading halts and missing data. - Latency
between signal generation and order submission.

Alpaca's historical trades and quotes can be useful for
execution-oriented research, subject to the relevant data access and
coverage. Do not assume Tiingo's OHLCV offering supplies equivalent
trade/quote depth; check its specific products and endpoints.

### Backtesting pitfalls to avoid

1.  **Look-ahead bias:** Never use information that would not have been
    available at the simulated decision time.
2.  **Survivorship bias:** A universe made only from today's surviving
    securities can distort historical results. This is less prominent
    when testing a fixed ETF list, but matters if the bot later scans
    individual stocks.
3.  **Corporate actions:** Handle splits and distributions consistently.
    Avoid mixing adjusted prices with executable historical prices.
4.  **Session alignment:** Respect US exchange hours, holidays, daylight
    saving changes and extended sessions.
5.  **Crypto session conventions:** BTC/USD trades 24/7. Define UTC or
    another explicit timezone and consistent candle boundaries.
6.  **Intrabar ambiguity:** OHLC candles do not reveal whether a
    stop-loss or take-profit was reached first when both fall within the
    same candle.
7.  **Data leakage:** Fit parameters and select strategies using
    training periods, then evaluate on genuinely unseen periods.
8.  **Overfitting:** Use walk-forward testing and out-of-sample
    evaluation rather than optimising repeatedly on the full history.

## 8. Suggested source allocation for the five instruments

A sensible initial test matrix is:

  -----------------------------------------------------------------------
  Instrument              Primary dataset to      Independent comparison
                          evaluate                
  ----------------------- ----------------------- -----------------------
  SPY                     Tiingo consolidated     Alpaca SIP historical
                          equity intraday         bars

  QQQ                     Tiingo consolidated     Alpaca SIP historical
                          equity intraday         bars

  GLD                     Tiingo consolidated     Alpaca SIP historical
                          equity intraday         bars

  USO                     Tiingo consolidated     Alpaca SIP historical
                          equity intraday         bars

  BTC/USD                 Tiingo historical       Alpaca historical
                          crypto                  crypto
  -----------------------------------------------------------------------

This is a proposed evaluation plan, not a claim that either provider has
complete, identical coverage for every instrument and date.

Keep each provider's original data in separate tables or files. Do not
blend the sources into one candle series until discrepancies have been
understood.

## 9. Recommended data validation exercise

Before selecting a provider or paying for a subscription, run a
controlled comparison.

### Test design

1.  Select the same date range for all five instruments.
2.  Request 1-minute OHLCV data from both providers where available.
3.  Use the same session definition for equities.
4.  Use a consistent timezone and candle-boundary convention.
5.  Compare overlapping timestamps.
6.  Compare OHLC values, volume and missing-candle rates.
7.  Check corporate-action treatment around known splits and
    distributions.
8.  Compare BTC/USD venue/aggregation definitions.
9.  Record API response times, pagination behaviour, request limits and
    failures.
10. Repeat a smaller download to confirm that results are reproducible.

### Suggested quality metrics

  Metric                         What it reveals
  ------------------------------ --------------------------------------------
  Coverage start/end             Actual historical depth
  Missing-bar percentage         Data completeness
  Duplicate timestamps           Ingestion or aggregation problems
  OHLC discrepancy               Differences in feed or methodology
  Volume discrepancy             Venue coverage and aggregation differences
  Timestamp alignment            Session and timezone consistency
  Corporate-action consistency   Adjustment correctness
  API failure/retry rate         Operational reliability

Do not automatically treat one provider as the truth. Investigate
discrepancies against feed definitions and, where appropriate, a third
authoritative source.

## 10. Recommended architecture

Keep the data provider separate from the strategy and backtesting code.

``` text
             Tiingo API              Alpaca API
          historical data       historical/validation data
                 \                    /
                  \                  /
                   v                v
              Data ingestion adapters
                       |
                       v
             Normalisation and QA
                       |
                       v
              Raw historical storage
             (separate source datasets)
                       |
                       v
             Canonical research dataset
             1-minute OHLCV + metadata
                       |
              +--------+--------+
              |                 |
              v                 v
        15-minute bars      Hourly bars
              |                 |
              +--------+--------+
                       |
                       v
              Strategy/backtest engine
                       |
                       v
        Performance, risk and execution analysis
```

### Suggested storage

-   **PostgreSQL:** instrument metadata, strategy runs, signals, trades,
    results and data-quality records.
-   **Parquet:** efficient storage of larger historical OHLCV datasets
    for research and backtesting.
-   **Raw source retention:** preserve the unmodified provider response
    or a faithful raw representation.
-   **Normalised layer:** standardise timestamps, symbols, field names
    and units without discarding source provenance.

Store at least: - Instrument identifier and provider symbol. - Provider
and feed name. - Timestamp and timezone convention. - Open, high, low,
close and volume. - Adjustment status. - Retrieval time. - Data version
or ingestion batch identifier.

## 11. Final recommendation

### Start with both providers' free access

For the initial build: - Test Tiingo's equity intraday/consolidated
dataset and crypto history. - Test Alpaca's historical equity and crypto
endpoints, paying attention to feed selection and historical SIP
access. - Download overlapping periods for SPY, QQQ, GLD, USO and
BTC/USD. - Compare coverage, prices, volume, timestamps and missing
candles. - Store the datasets separately.

### When to pay

-   Consider **Tiingo Power (\$30/month as previously listed)** if its
    specific intraday products and quotas meet the project's needs and
    the free tier is too restrictive.
-   Consider **Alpaca Algo Trader Plus (\$99/month as previously
    listed)** if you need its consolidated real-time feed, higher limits
    or other paid entitlements. It may not be necessary for
    historical-only backtesting.

### Bottom line

**Tiingo is a compelling candidate for independent historical research;
Alpaca is particularly useful for broker-integrated data and
execution-oriented investigation.** The better choice for this bot
should be decided by a measured comparison of the actual five
instruments and date ranges, not by headline historical coverage or
subscription price alone.

For the first version, retain 1-minute raw data, derive 15-minute and
hourly candles locally, and keep the strategy engine independent of
either provider. That preserves flexibility as the bot and its
backtesting requirements become more sophisticated.

------------------------------------------------------------------------

## Provider documentation and pricing

Check current documentation and plan entitlements before implementation:

-   [Alpaca Market Data
    documentation](https://docs.alpaca.markets/docs/about-market-data-api)
-   [Alpaca Historical Data
    API](https://docs.alpaca.markets/docs/historical-api)
-   [Alpaca market-data pricing](https://alpaca.markets/data)
-   [Tiingo website and data products](https://www.tiingo.com/)
-   [Tiingo documentation](https://www.tiingo.com/documentation/)
-   [Tiingo pricing](https://www.tiingo.com/pricing)

**Note:** Provider capabilities, historical coverage, API limits, prices
and data licences can change. Verify current terms and endpoint-specific
entitlements before relying on any feature or cost stated in this
document.
