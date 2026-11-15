export function TradingGuide() {
  return <article className="trading-guide">
    <p className="guide-intro">GoldScout follows preset price-based trading rules through Alpaca. These are the default settings, not a guarantee of profit; actual settings may differ.</p>

    <section>
      <h2>Default trading strategies</h2>
      <div className="guide-strategies">
        <div><h3>SPY &amp; QQQ <span>Mean reversion</span></h3><p>These US stock funds are checked using completed 15-minute candles. The strategy looks for prices unusually far from their recent average, expecting they may return toward it.</p></div>
        <div><h3>BTC/USD <span>Momentum breakout</span></h3><p>Bitcoin is checked using completed hourly candles. The strategy looks for a move beyond a recent high or low with unusually high volume. Alpaca does not allow shorting Bitcoin: a downward signal can close a long position, not open a short.</p></div>
        <div><h3>GLD &amp; USO <span>Trend following</span></h3><p>These funds provide exposure to gold and oil. The strategy compares faster and slower price averages using completed four-hour candles to look for changes in trend.</p></div>
      </div>
      <p>A candle summarizes prices and volume over a period. Equity strategies pause when the market is closed; Bitcoin is monitored around the clock.</p>
    </section>

    <section>
      <h2>Before a trade</h2>
      <p>A strategy signal is not necessarily an order. The bot checks account and risk limits, recent monitoring, market data, and existing positions and orders before opening a trade. Uncertain or unresolved conditions can block new entries.</p>
      <p>By default, position size uses recent price movement (ATR) and targets a planned loss of up to 1% of equity at the hard-stop price. Per-position and total exposure caps can reduce the size. Actual losses can exceed the planned amount.</p>
    </section>

    <section>
      <h2>Stops and daily limits</h2>
      <p>A hard stop is intended to limit loss if price moves against a position. Some strategies also use a trailing stop that can tighten as price moves favorably. Stops do not guarantee an exit price; Bitcoin stop-limit orders may not fill during a fast move or gap.</p>
      <p>The default 3% daily-loss safeguard blocks new entries when triggered; it does not automatically close existing positions or guarantee a maximum daily loss. The halt remains in effect until the next New York calendar day.</p>
    </section>

    <section>
      <h2>Reading the dashboard</h2>
      <p>The dashboard shows recorded snapshots, not a live confirmation of broker positions or protection. Unknown or stale information is not permission to trade. Trading and account views cannot place orders or change risk settings.</p>
      <p>Stopping the bot does not close positions. Broker-held stops remain at their last set prices, while software monitoring and trailing-stop updates pause. Optional market intelligence is advisory and does not influence trading decisions.</p>
    </section>

    <div className="guide-warning"><strong>Trading involves risk.</strong> Paper trading uses simulated money, and backtests approximate historical behavior. Neither establishes real-world execution or future profitability.</div>
  </article>;
}
