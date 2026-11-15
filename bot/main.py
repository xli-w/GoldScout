"""
Entry point.  Run from the project root:   python -m bot.main

One scheduler loop runs:
  - each strategy at its own interval (entries on newly closed candles only)
  - a position monitor (trailing stops, reconciliation) every MONITOR_INTERVAL seconds
  - a heartbeat that snapshots equity into daily_pnl.csv
Equity strategies pause when the market is closed; BTC/USD runs 24/7.
"""
import logging
import signal
import sys
import time
from datetime import datetime, timezone
import pandas as pd
from concurrent.futures import ThreadPoolExecutor
from logging.handlers import RotatingFileHandler

import alpaca_trade_api as tradeapi

import config
from bot import indicators
from bot.corporate_actions import adjusted_window
from bot.data import MarketData
from bot.portfolio import Portfolio
from bot.ledger import PersistenceError
from bot.process_lock import ProcessLock
from bot.telemetry import TelemetryPublisher
from bot.risk_manager import RiskManager
from bot.strategies.base import Action, Signal, Strategy
from bot.strategies.mean_reversion import MeanReversionStrategy
from bot.strategies.momentum_breakout import MomentumBreakoutStrategy
from bot.strategies.trend_following import TrendFollowingStrategy
from bot.utils import norm_symbol, create_api

log = logging.getLogger("bot")


def setup_logging() -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logfile = RotatingFileHandler(config.LOG_FILE, maxBytes=5_000_000, backupCount=3)
    logfile.setFormatter(fmt)
    root.addHandler(console)
    root.addHandler(logfile)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


class Task:
    def __init__(self, name: str, interval: float, fn):
        self.name, self.interval, self.fn = name, interval, fn
        self.next_run = 0.0
        self.failures = 0


class TradingBot:
    def __init__(self):
        if not config.ALPACA_API_KEY or not config.ALPACA_SECRET_KEY:
            sys.exit("Missing ALPACA_API_KEY / ALPACA_SECRET_KEY in .env")
        if not config.IS_PAPER and not config.LIVE_TRADING_CONFIRMED:
            sys.exit("ALPACA_BASE_URL is a LIVE endpoint. Set CONFIRM_LIVE_TRADING=yes in .env "
                     "if you really intend to trade real money.")

        self.api = create_api()
        self.data = MarketData(self.api)
        self.risk = RiskManager()
        self.portfolio = Portfolio(self.api, self.risk)
        self.strategies = [MeanReversionStrategy(), MomentumBreakoutStrategy(), TrendFollowingStrategy()]
        self.running = True
        self._monitor_ready = False
        self._last_monitor_started_monotonic = None
        self.data_workers = ThreadPoolExecutor(max_workers=3, thread_name_prefix="market-data")
        self.data_jobs = {}
        self.symbol_failures = {}
        self.telemetry = None
        self._telemetry_monitor_at = None
        self._telemetry_protection = {}
        self._telemetry_failures = {}
        self._telemetry_sync_at = None
        self._telemetry_sync_status = "unknown"
        self._telemetry_fault = False
        self.intelligence_observer = None
        try:
            from market_intelligence.bridge import BotIntelligenceObserver
            self.intelligence_observer = BotIntelligenceObserver(config.STATE_FILE.with_name("intelligence.sqlite3"))
        except Exception:
            log.warning("Intelligence observation unavailable; trading unchanged")
        self.strategy_data = {strategy.name: MarketData(create_api()) for strategy in self.strategies}

    # ------------------------------------------------------------------ startup
    def startup(self) -> None:
        acct = self.portfolio.get_account()
        self.portfolio.bind_account(acct.id)
        self._start_telemetry()
        if acct.trading_blocked or acct.account_blocked:
            sys.exit("Account is blocked from trading - check the Alpaca dashboard.")
        log.info("Connected (%s). Equity $%s, buying power $%s",
                 "PAPER" if config.IS_PAPER else "LIVE", acct.equity, acct.buying_power)

        self.portfolio.update_daily(float(acct.equity))
        monitor_started = time.monotonic()
        try:
            self.portfolio.manage_positions()
        except Exception as exc:
            if isinstance(exc, PersistenceError) or self.portfolio.persistence_fault:
                raise
            log.error("Initial position monitoring failed; entries remain blocked until monitoring recovers: %s", exc)
            return
        self._monitor_ready = True
        self._last_monitor_started_monotonic = monitor_started

    def _monitor_is_fresh(self) -> bool:
        last_started = getattr(self, "_last_monitor_started_monotonic", None)
        if not getattr(self, "_monitor_ready", False) or last_started is None:
            return False
        age = time.monotonic() - last_started
        return 0 <= age <= config.MONITOR_INTERVAL

    def _start_telemetry(self):
        try:
            import psutil
            database_id = self.portfolio.ledger_database_id
            self.telemetry = TelemetryPublisher(
                config.STATE_FILE.with_name("bot_telemetry.json"), database_id,
                "paper" if config.IS_PAPER else "live", psutil.Process().create_time())
            self._publish_telemetry("starting")
        except Exception:
            self.telemetry = None
            log.warning("Dashboard telemetry unavailable; trading behavior unchanged")

    def _publish_telemetry(self, runtime="running"):
        publisher = getattr(self, "telemetry", None)
        if publisher is None:
            return
        try:
            portfolio = self.portfolio
            reasons = []
            for flag, code in (("persistence_fault", "persistence_fault"),
                               ("accounting_unresolved", "accounting_unresolved"),
                               ("daily_halted", "daily_halted")):
                if getattr(portfolio, flag):
                    reasons.append(code)
            if not self._monitor_is_fresh():
                reasons.append("monitor_not_ready")
            if portfolio.unmanaged_symbols:
                reasons.append("unmanaged_positions")
            if portfolio.entry_intents:
                reasons.append("entry_intent_pending")
            if any(pos.exit_order_id or pos.exit_client_order_id for pos in portfolio.positions.values()):
                reasons.append("exit_pending")
            if any(not pos.protection_verified for pos in portfolio.positions.values()):
                reasons.append("protection_unverified")
            publisher.submit({
                "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "runtime": runtime, "last_monitor_completed_at": self._telemetry_monitor_at,
                "persistence_fault": bool(portfolio.persistence_fault or getattr(self, "_telemetry_fault", False)),
                "accounting_unresolved": bool(portfolio.accounting_unresolved),
                "daily_halted": bool(portfolio.daily_halted),
                "unmanaged_symbols": sorted(portfolio.unmanaged_symbols)[:32],
                "block_reasons": reasons, "task_failures": dict(self._telemetry_failures),
                "activity_sync": {"checkpoint_at": self._telemetry_sync_at, "status": self._telemetry_sync_status},
                "protection": self._telemetry_protection,
            })
        except Exception:
            log.warning("Dashboard telemetry snapshot failed; trading behavior unchanged")

    def _observe_task(self, task, successful, started_at=None):
        if getattr(self, "telemetry", None) is None:
            return
        try:
            if successful:
                self._telemetry_failures.pop(task.name, None)
                if task.name == "monitor":
                    self._telemetry_monitor_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                    self._telemetry_protection = {
                        symbol: {"position_id": pos.identity, "quantity": str(pos.qty),
                                 "broker_stop": str(pos.server_stop) if pos.server_stop is not None else None,
                                 "verified": bool(pos.protection_verified), "observed_at": started_at or self._telemetry_monitor_at}
                        for symbol, pos in list(self.portfolio.positions.items())[:32] if pos.identity}
            else:
                self._telemetry_failures[task.name] = task.failures + 1
                if task.name == "heartbeat":
                    self._telemetry_sync_status = "incomplete"
        except Exception:
            log.warning("Dashboard task observation failed; scheduler behavior unchanged")

    # --------------------------------------------------------------- strategy run
    def _fetch_strategy(self, strat: Strategy):
        results = {}
        for symbol in strat.symbols:
            inst = config.INSTRUMENTS[symbol]
            try:
                market = self.strategy_data[strat.name]
                results[symbol] = market.equity_features(symbol, strat.timeframe, strat.lookback_days) \
                    if inst.asset_class == "equity" else market.get_completed_bars(
                        symbol, inst.asset_class, strat.timeframe, strat.lookback_days)
            except Exception as exc:
                results[symbol] = exc
        return results

    def run_strategy(self, strat: Strategy, data=None) -> None:
        failed = []
        for symbol in strat.symbols:
            inst = config.INSTRUMENTS[symbol]
            if inst.asset_class == "equity" and not self.portfolio.market_is_open():
                log.debug("%s skipped: market closed", symbol)
                continue
            try:
                bars = data[symbol] if data is not None else None
                if isinstance(bars, Exception):
                    raise bars
                self._process_symbol(strat, inst, bars)
                self.symbol_failures[symbol] = 0
            except Exception:  # one symbol failing must not stop the others
                self.symbol_failures[symbol] = self.symbol_failures.get(symbol, 0) + 1
                failed.append(symbol)
                log.exception("%s/%s evaluation failed", strat.name, symbol)
        if failed:
            raise RuntimeError(f"{strat.name}: evaluation failed for {', '.join(failed)}")

    def _process_symbol(self, strat: Strategy, inst: config.Instrument, bars=None) -> None:
        symbol = inst.symbol
        if bars is None:
            bars = self.data.equity_features(symbol, strat.timeframe, strat.lookback_days) \
                if inst.asset_class == "equity" else self.data.get_completed_bars(
                    symbol, inst.asset_class, strat.timeframe, strat.lookback_days)
        bars = adjusted_window(bars, bars.attrs.get("corporate_actions", []),
                               pd.Timestamp.now(tz="UTC"))
        if len(bars) < strat.min_bars:
            log.warning("%s: only %d/%d bars available - skipping", symbol, len(bars), strat.min_bars)
            return

        last_ts = bars.index[-1].isoformat()
        new_bar = self.portfolio.last_bar_ts.get(symbol) != last_ts
        pos = self.portfolio.positions.get(symbol)
        atr = float(indicators.atr(bars, self.risk.atr_period).iloc[-1])

        live_price = self.portfolio.current_price(symbol) if pos else None
        if pos:
            pos.atr = atr                      # trailing stop follows current volatility
            if strat.name == "mean_reversion":
                pos.exit_target = float(indicators.sma(bars["close"], strat.period).iloc[-1])

        signal_ = strat.evaluate(symbol, bars, pos.side if pos else None, live_price, new_bar)
        last_end = pd.Timestamp(bars["bar_end"].iloc[-1]) if "bar_end" in bars else bars.index[-1] + pd.Timedelta(
            minutes=strat.timeframe[0] * (1 if strat.timeframe[1] == "Min" else 60))
        if signal_.action in (Action.ENTER_LONG, Action.ENTER_SHORT) and pd.Timestamp.now(tz="UTC") - last_end > pd.Timedelta(minutes=5):
            want = "long" if signal_.action == Action.ENTER_LONG else "short"
            if pos is not None and pos.side != want:
                signal_ = Signal(Action.EXIT, symbol, f"stale reversal exit only: {signal_.reason}")
            else:
                signal_ = Signal(Action.HOLD, symbol, "stale candle: new exposure blocked")
            log.warning("%s stale candle: entry blocked, exit intent preserved", symbol)
        if signal_.action != Action.HOLD:
            log.info("SIGNAL %s %s: %s", symbol, signal_.action.value, signal_.reason)

        self._execute(signal_, strat, inst, atr, float(bars["close"].iloc[-1]))
        if new_bar:
            self.portfolio.mark_bar(symbol, last_ts)

    def _execute(self, sig: Signal, strat: Strategy, inst: config.Instrument,
                 atr: float, price: float) -> None:
        try:
            observer = getattr(self, "intelligence_observer", None)
            if observer is not None:
                observer.record(inst.symbol, sig.action.value)
        except Exception:
            pass
        symbol = inst.symbol
        pos = self.portfolio.positions.get(symbol)

        if sig.action == Action.HOLD:
            return
        if sig.action == Action.EXIT:
            if pos:
                self.portfolio.close_position(symbol, sig.reason)
            return

        want = "long" if sig.action == Action.ENTER_LONG else "short"
        if pos and pos.side == want:
            return
        if pos and pos.side != want:           # opposite signal: always leave the old position
            if not self.portfolio.close_position(symbol, f"reversal signal: {sig.reason}"):
                return
        if not self._monitor_is_fresh():
            log.warning("%s entry blocked: position monitoring is stale or has not succeeded", symbol)
            return
        if want == "short" and not inst.allow_short:
            log.info("%s: shorting not available for this instrument - exit only", symbol)
            return

        equity = float(self.portfolio.get_account().equity)
        if self.portfolio.daily_loss_exceeded(equity):
            log.warning("%s entry blocked: daily loss limit (%.0f%%) reached",
                        symbol, 100 * self.risk.max_daily_loss_pct)
            return
        allowed, why = self.risk.correlation_filter(symbol, want, self.portfolio.side_map())
        if not allowed:
            log.info(why)
            return

        if not getattr(self, "running", True):
            log.info("%s entry blocked: scheduler is stopping", symbol)
            return
        self.portfolio.open_position(inst, want, atr, price, strat.name,
                                     strat.trailing_atr_mult, sig.reason,
                                     entry_guard=lambda: self.running and self._monitor_is_fresh())

    # ----------------------------------------------------------------- scheduler
    def heartbeat(self) -> None:
        self.portfolio.update_daily()
        self.portfolio.sync_activities()
        if getattr(self, "telemetry", None) is not None:
            self._telemetry_sync_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            self._telemetry_sync_status = "last_observed_complete"
        log.info("Heartbeat | equity $%.2f | open: %s", self.portfolio.last_equity,
                 {s: p.side for s, p in self.portfolio.positions.items()} or "none")

    def _run_task(self, task: Task) -> None:
        started_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        started_monotonic = time.monotonic()
        try:
            task.fn()
            if task.name == "monitor":
                self._monitor_ready = True
                self._last_monitor_started_monotonic = started_monotonic
            self._observe_task(task, True, started_at)
            task.failures = 0
            task.next_run = (started_monotonic + task.interval if task.name == "monitor"
                             else time.monotonic() + task.interval)
        except Exception as exc:  # noqa: BLE001  (API outage, bad response, anything)
            if task.name == "monitor":
                self._monitor_ready = False
            self._observe_task(task, False)
            if isinstance(exc, PersistenceError) or self.portfolio.persistence_fault:
                self._telemetry_fault = True
                self.running = False
                raise PersistenceError("Persistence failed; scheduler stopped, resting stops left unchanged") from exc
            task.failures += 1
            backoff = min(task.interval * (2 ** task.failures), config.MONITOR_INTERVAL if task.name == "monitor" else 600)
            log.error("Task '%s' failed (%s). Attempt %d, retrying in %.0fs",
                      task.name, exc, task.failures, backoff, exc_info=task.failures == 1)
            task.next_run = time.monotonic() + backoff
        finally:
            self._publish_telemetry("faulted" if self.portfolio.persistence_fault or getattr(self, "_telemetry_fault", False) else "running")

    def run(self) -> None:
        self.startup()
        tasks = [Task("monitor", config.MONITOR_INTERVAL, self.portfolio.manage_positions),
                  Task("heartbeat", config.HEARTBEAT_INTERVAL, self.heartbeat)]
        strategy_tasks = {strategy.name: Task(strategy.name, strategy.check_interval, None)
                         for strategy in self.strategies}

        log.info("Bot running. Ctrl+C to stop (open positions keep their resting stop orders).")
        while self.running:
            for task in tasks:
                if not self.running:
                    break
                if time.monotonic() >= task.next_run:
                    started = time.monotonic()
                    self._run_task(task)
                    if time.monotonic() - started > config.MONITOR_INTERVAL:
                        log.critical("Task %s exceeded monitor cadence", task.name)
            if not self.running:
                break
            for strat in self.strategies:
                if not self.running:
                    break
                if time.monotonic() >= tasks[0].next_run:
                    self._run_task(tasks[0])
                if not self.running:
                    break
                task = strategy_tasks[strat.name]
                job = self.data_jobs.get(strat.name)
                if job is not None and job.done():
                    if not self.running:
                        break
                    def apply_result(strat=strat, job=job):
                        self.run_strategy(strat, job.result())
                    task.fn = apply_result
                    self._run_task(task)
                    del self.data_jobs[strat.name]
                elif job is None and time.monotonic() >= task.next_run:
                    self.data_jobs[strat.name] = self.data_workers.submit(self._fetch_strategy, strat)
            if not self.running:
                break
            self._publish_telemetry()
            time.sleep(1)
        self.shutdown()

    def shutdown(self) -> None:
        log.info("Shutting down...")
        self.data_workers.shutdown(wait=True, cancel_futures=True)
        try:
            self.portfolio.update_daily()
        except Exception as exc:  # noqa: BLE001
            log.warning("final daily P&L write failed: %s", exc)
        log.info("State saved. %d position(s) remain open with resting stops.",
                 len(self.portfolio.positions))


def main() -> None:
    config.initialize_broker_settings()
    setup_logging()
    lock = ProcessLock(config.LOCK_FILE)
    try:
        bot = TradingBot()
    except BaseException:
        lock.close()
        raise

    def _stop(signum, frame):
        log.info("Signal %s received", signum)
        bot.running = False

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    try:
        bot.run()
    finally:
        bot._publish_telemetry("faulted" if bot.portfolio.persistence_fault or getattr(bot, "_telemetry_fault", False) else "stopped")
        if getattr(bot, "telemetry", None) is not None:
            bot.telemetry.close()
        intelligence_observer = getattr(bot, "intelligence_observer", None)
        if intelligence_observer is not None:
            try:
                intelligence_observer.close()
            except Exception:
                log.critical("Intelligence observer close was not confirmed", exc_info=True)
        try:
            bot.data_workers.shutdown(wait=True, cancel_futures=True)
        finally:
            lock.close()


if __name__ == "__main__":
    main()
