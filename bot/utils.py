"""Small helpers: symbol normalisation and a retry decorator for flaky connections."""
import functools
import logging
import time
from contextvars import ContextVar
from urllib.parse import urlsplit

import requests
from urllib3.connection import HTTPConnection, HTTPSConnection
import config
import alpaca_trade_api as tradeapi
from alpaca_trade_api.rest import APIError
from proxy_config import ProxyConfigurationError, parse_proxy_url

log = logging.getLogger(__name__)

RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}
NETWORK_ERRORS = (requests.exceptions.RequestException, ConnectionError, TimeoutError)
REQUEST_DEADLINE = ContextVar("request_deadline", default=None)


def create_api():
    proxy = parse_proxy_url(config.TRADER_PROXY_URL)
    api = tradeapi.REST(config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY,
                        config.ALPACA_BASE_URL, api_version="v2")
    api._retry = 0
    original = api._session.request

    def bounded_request(method, url, **kwargs):
        deadline = REQUEST_DEADLINE.get()
        remaining = deadline - time.monotonic() if deadline is not None else config.API_RETRY_BUDGET
        if remaining <= 0:
            raise TimeoutError("API operation exceeded its retry deadline")
        kwargs["timeout"] = (min(config.API_CONNECT_TIMEOUT, remaining),
                             min(config.API_READ_TIMEOUT, remaining))
        if proxy is not None:
            target = urlsplit(url)
            connection_type = HTTPSConnection if target.scheme == "https" else HTTPConnection
            if proxy.authorization is not None and connection_type.debuglevel:
                raise ProxyConfigurationError("Disable HTTP wire debugging when using an authenticated proxy")
            proxies = {"http": proxy.url, "https": proxy.url}
            if target.scheme in ("http", "https") and target.hostname is not None:
                proxies[f"{target.scheme}://{target.hostname}"] = proxy.url
            kwargs["proxies"] = proxies
        try:
            return original(method, url, **kwargs)
        except requests.exceptions.RequestException as exc:
            if proxy is None:
                raise
            # Requests exceptions can include proxy credentials; retain retryable types.
            message = f"Proxy-routed Alpaca request failed ({type(exc).__name__})"
            exc.args = (message,)
            if str(exc) != message:
                raise requests.exceptions.RequestException(message) from None
            raise exc from None

    api._session.request = bounded_request
    return api


def norm_symbol(symbol: str) -> str:
    """'BTC/USD' and 'BTCUSD' both -> 'BTCUSD' (Alpaca uses both forms)."""
    return symbol.replace("/", "").upper()


def is_retryable(exc: Exception) -> bool:
    if isinstance(exc, APIError):
        return getattr(exc, "status_code", None) in RETRYABLE_STATUS
    return isinstance(exc, NETWORK_ERRORS)


def retry(attempts: int = 2, base_delay: float = 0.5, max_delay: float = 2.0,
          budget: float = None):
    """Retry read-style API calls on network errors, rate limits and 5xx responses."""
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            delay = base_delay
            deadline = time.monotonic() + (budget if budget is not None else config.API_RETRY_BUDGET)
            parent = REQUEST_DEADLINE.get()
            if parent is not None:
                deadline = min(deadline, parent)
            token = REQUEST_DEADLINE.set(deadline)
            try:
                for attempt in range(1, attempts + 1):
                    try:
                        if time.monotonic() >= deadline:
                            raise TimeoutError("API retry budget exhausted")
                        return fn(*args, **kwargs)
                    except Exception as exc:
                        if not is_retryable(exc) or attempt == attempts or time.monotonic() + delay >= deadline:
                            raise
                        log.warning("%s retry %d/%d after %s", fn.__name__, attempt, attempts, exc)
                        time.sleep(delay)
                        delay = min(delay * 2, max_delay)
            finally:
                REQUEST_DEADLINE.reset(token)
        return wrapper
    return decorator
