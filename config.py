"""
Central configuration for the Alpaca multi-strategy trading bot.

Every tunable number lives here. Secrets come from .env (never hard-code keys).
"""
import os
import math
from urllib.parse import urlsplit
from dataclasses import dataclass
from pathlib import Path
from threading import Lock as _Lock

from dotenv import load_dotenv
from proxy_config import configured_proxy_url

BASE_DIR = Path(__file__).resolve().parent

# --------------------------------------------------------------------------
# Credentials / endpoints
# --------------------------------------------------------------------------
ALPACA_API_KEY: str
ALPACA_SECRET_KEY: str
ALPACA_BASE_URL: str
STOCK_DATA_FEED: str
LIVE_TRADING_CONFIRMED: bool
IS_PAPER: bool
TRADER_PROXY_URL: str

_RUNTIME_FIELDS = frozenset(("ALPACA_API_KEY", "ALPACA_SECRET_KEY", "ALPACA_BASE_URL", "STOCK_DATA_FEED",
                             "LIVE_TRADING_CONFIRMED", "IS_PAPER", "TRADER_PROXY_URL", "endpoint"))
_runtime_settings = None
_runtime_lock = _Lock()


def _broker_settings():
    """Load broker configuration only when a runtime setting is needed."""
    global _runtime_settings
    if _runtime_settings is None:
        with _runtime_lock:
            if _runtime_settings is None:
                proxy = configured_proxy_url()
                load_dotenv(BASE_DIR / ".env")
                api_key = os.getenv("ALPACA_API_KEY", "")
                secret_key = os.getenv("ALPACA_SECRET_KEY", "")
                base_url = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
                data_feed = os.getenv("ALPACA_DATA_FEED", "iex")
                live_confirmed = os.getenv("CONFIRM_LIVE_TRADING", "").strip().lower() == "yes"
                endpoint = urlsplit(base_url)
                if endpoint.scheme != "https" or endpoint.hostname not in ("paper-api.alpaca.markets", "api.alpaca.markets") \
                        or endpoint.username or endpoint.password or endpoint.port not in (None, 443) \
                        or endpoint.path not in ("", "/") or endpoint.query or endpoint.fragment:
                    raise ValueError("ALPACA_BASE_URL must be an approved Alpaca HTTPS trading endpoint")
                _runtime_settings = {
                    "TRADER_PROXY_URL": proxy, "ALPACA_API_KEY": api_key, "ALPACA_SECRET_KEY": secret_key,
                    "ALPACA_BASE_URL": base_url, "STOCK_DATA_FEED": data_feed,
                    "LIVE_TRADING_CONFIRMED": live_confirmed, "endpoint": endpoint,
                    "IS_PAPER": endpoint.hostname == "paper-api.alpaca.markets",
                }
    return _runtime_settings


def initialize_broker_settings() -> None:
    _broker_settings()


def __getattr__(name):
    if name not in _RUNTIME_FIELDS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return _broker_settings()[name]


def __dir__():
    return sorted(set(globals()) | _RUNTIME_FIELDS)

# --------------------------------------------------------------------------
# Instruments
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Instrument:
    symbol: str
    asset_class: str      # "equity" or "crypto"
    strategy: str         # which strategy module trades it
    allow_short: bool     # Alpaca does NOT support shorting crypto


INSTRUMENTS = {
    "SPY": Instrument("SPY", "equity", "mean_reversion", True),
    "QQQ": Instrument("QQQ", "equity", "mean_reversion", True),
    "BTC/USD": Instrument("BTC/USD", "crypto", "momentum_breakout", False),
    "GLD": Instrument("GLD", "equity", "trend_following", True),
    "USO": Instrument("USO", "equity", "trend_following", True),
}

# --------------------------------------------------------------------------
# Strategy parameters
# timeframe = (amount, unit) where unit is "Min" or "Hour"
# check_interval = seconds between evaluations of that strategy
# --------------------------------------------------------------------------
MEAN_REVERSION = {
    "symbols": ["SPY", "QQQ"],
    "timeframe": (15, "Min"),
    "lookback_days": 10,
    "period": 20,
    "entry_std": {"SPY": 1.5, "QQQ": 1.8},
    "check_interval": 60,
}

MOMENTUM_BREAKOUT = {
    "symbols": ["BTC/USD"],
    "timeframe": (1, "Hour"),
    "lookback_days": 14,
    "period": 20,
    "volume_mult": 1.5,
    "trailing_atr_mult": 2.0,
    "check_interval": 120,
}

TREND_FOLLOWING = {
    "symbols": ["GLD", "USO"],
    "timeframe": (4, "Hour"),
    "lookback_days": 450,        # 200-EMA needs a long warm-up on 4h bars
    "fast": 50,
    "slow": 200,
    "trailing_atr_mult": 3.0,
    "check_interval": 300,
}

# --------------------------------------------------------------------------
# Risk management
# --------------------------------------------------------------------------
RISK = {
    "risk_per_trade_pct": 0.01,      # a 1 ATR adverse move == 1% of equity
    "atr_period": 14,
    "hard_stop_atr_mult": 1.0,       # hard stop = 1 ATR (== 1% equity at full size)
    # --- practical safety caps (these can only REDUCE risk below 1%) ---
    # Quiet instruments (SPY on 15m bars) imply huge leverage under pure ATR
    # sizing, so notional is capped. Raise at your own risk.
    "max_position_pct_of_equity": 1.5,   # max notional per position
    "max_gross_exposure_pct": 2.0,       # max total notional across positions
    "max_daily_loss_pct": 0.03,          # stop opening new trades after -3% on the day
    "min_crypto_notional": 10.0,         # Alpaca minimum crypto order (USD)
    "crypto_stop_limit_buffer": 0.01,    # limit sits 1% beyond the stop for crypto
    "crypto_taker_fee": 0.0025,
    "crypto_maker_fee": 0.0015,
}

# Don't open new LONGS in `target` if every symbol in `blocking_symbols` is already long.
CORRELATION_FILTER = {
    "target": "BTC/USD",
    "blocking_symbols": ["SPY", "QQQ"],
    "blocked_side": "long",
}

# --------------------------------------------------------------------------
# Scheduling / files
# --------------------------------------------------------------------------
MONITOR_INTERVAL = 30          # seconds: stop management + reconciliation
ADOPT_CONFIGURED_POSITIONS = False
API_CONNECT_TIMEOUT = 3.0
API_READ_TIMEOUT = 5.0
API_RETRY_BUDGET = 10.0
ORDER_FILL_WAIT = 2.0
ORDER_CANCEL_WAIT = 3.0
HEARTBEAT_INTERVAL = 300       # seconds: equity snapshot + daily_pnl.csv update
MARKET_TZ = "America/New_York" # defines the "trading day" for daily P&L

TRADES_CSV = BASE_DIR / "trades.csv"
DAILY_PNL_CSV = BASE_DIR / "daily_pnl.csv"
STATE_FILE = BASE_DIR / "bot_state.json"
LOG_FILE = BASE_DIR / "bot.log"
LEDGER_FILE = BASE_DIR / "bot_ledger.sqlite3"
LOCK_FILE = BASE_DIR / "bot.lock"


def validate_strategy(name, settings, research=False):
    """`research` permits offline-only strategy/instrument pairings; live code never passes it."""
    symbols = settings["symbols"]
    if not symbols or len(symbols) != len(set(symbols)):
        raise ValueError(f"{name}: symbols must be nonempty and unique")
    if any(symbol not in INSTRUMENTS or (not research and INSTRUMENTS[symbol].strategy != name)
           for symbol in symbols):
        raise ValueError(f"{name}: invalid instrument ownership")
    amount, unit = settings["timeframe"]
    if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0 or unit not in ("Min", "Hour") \
            or amount > (59 if unit == "Min" else 23):
        raise ValueError(f"{name}: invalid timeframe")
    for key in ("lookback_days", "check_interval", "period", "fast", "slow", "volume_mult", "trailing_atr_mult"):
        if key in settings and (isinstance(settings[key], bool) or not isinstance(settings[key], (int, float))
                                or not math.isfinite(settings[key]) or settings[key] <= 0):
            raise ValueError(f"{name}: {key} must be finite and positive")
    for key in ("lookback_days", "period", "fast", "slow"):
        if key in settings and not isinstance(settings[key], int):
            raise ValueError(f"{name}: {key} must be an integer")
    if name == "trend_following" and settings["fast"] >= settings["slow"]:
        raise ValueError("Trend fast period must be below slow period")
    if name == "mean_reversion" and any(symbol not in settings["entry_std"] or
            isinstance(settings["entry_std"][symbol], bool) or not math.isfinite(settings["entry_std"][symbol]) or
            settings["entry_std"][symbol] <= 0 for symbol in symbols):
        raise ValueError("Mean-reversion thresholds must cover every symbol")


for strategy_name, settings in (("mean_reversion", MEAN_REVERSION), ("momentum_breakout", MOMENTUM_BREAKOUT),
                                ("trend_following", TREND_FOLLOWING)):
    validate_strategy(strategy_name, settings)

# Preserve wildcard imports of the lazy runtime settings.
__all__ = [name for name in globals() if not name.startswith("_")] + sorted(_RUNTIME_FIELDS)
