"""
Portfolio: everything that touches the broker or persistent state.

- order entry/exit (with duplicate-safe submission)
- server-side hard stops (so a crashed bot is still protected)
- trailing-stop ratcheting and a software stop as a second safety net
- reconciliation with the broker (detects stops that filled while we were busy)
- trades.csv / daily_pnl.csv logging and crash-safe state persistence
"""
import json
import logging
import math
import time
import uuid
from types import SimpleNamespace
from dataclasses import asdict, fields, replace
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, Optional
from zoneinfo import ZoneInfo

from alpaca_trade_api.rest import APIError

import config
from bot import trade_logger
from bot.risk_manager import RiskManager
from bot.ledger import Ledger, PersistenceError
from bot.state import Position, validate_state
from bot.utils import is_retryable, norm_symbol, retry

log = logging.getLogger(__name__)


class _EntrySubmissionStopped(Exception):
    pass


class Portfolio:
    def __init__(self, api, risk: RiskManager):
        self.api = api
        self.risk = risk
        self.positions: Dict[str, Position] = {}
        self.entry_intents = {}
        self.unmanaged_symbols = set()
        self.last_bar_ts: Dict[str, str] = {}
        self.day: Optional[str] = None
        self.day_start_equity: Optional[float] = None
        self.last_equity: Optional[float] = None
        self.realized_today = 0.0
        self.trades_today = 0
        self.daily_halted = False
        self.daily_cash_flow = 0.0
        self.cash_flow_ids = []
        self.accounting_unresolved = False
        self._ledger = None
        self._pending_exit = None
        self._pending_daily = []
        self._reports_dirty = True
        self.persistence_fault = False
        self._clock_cache = (0.0, False)
        self._load_state()

    # ================================================================ broker reads
    @retry()
    def get_account(self):
        return self.api.get_account()

    @retry()
    def _list_positions(self):
        return self.api.list_positions()

    @retry()
    def _get_order(self, order_id):
        order = self.api.get_order(order_id)
        self._observe_order(order)
        return order

    @retry()
    def _list_open_orders(self):
        orders = self.api.list_orders(status="open", limit=500, nested=True)
        if len(orders) >= 500:
            raise RuntimeError("Open-order response reached limit; complete coverage cannot be verified")
        flattened = {}

        def visit(order, parent=None):
            flattened[order.id] = order
            if parent is not None:
                self._order_groups[order.id] = parent
            for leg in getattr(order, "legs", None) or []:
                visit(leg, parent or order.id)

        self._order_groups = {}
        for order in orders:
            visit(order)
        return list(flattened.values())

    @retry()
    def _get_clock(self):
        return self.api.get_clock()

    def broker_positions(self) -> Dict[str, object]:
        return {norm_symbol(p.symbol): p for p in self._list_positions()}

    def market_is_open(self) -> bool:
        """US equity market status, cached 30s. If the clock is unreachable, assume closed."""
        ts, value = self._clock_cache
        if time.monotonic() - ts < 30:
            return value
        try:
            value = bool(self._get_clock().is_open)
        except Exception as exc:  # noqa: BLE001
            log.warning("Market clock unavailable (%s) - treating equities as closed", exc)
            value = False
        self._clock_cache = (time.monotonic(), value)
        return value

    def current_price(self, symbol: str) -> Optional[float]:
        bp = self.broker_positions().get(norm_symbol(symbol))
        return float(bp.current_price) if bp is not None else None

    def gross_exposure(self) -> float:
        return sum(abs(float(p.market_value)) for p in self._list_positions())

    def side_map(self) -> Dict[str, str]:
        return {s: p.side for s, p in self.positions.items()}

    # ================================================================ order helpers
    @retry(attempts=1)
    def _submit(self, *, submission_guard=None, **kwargs):
        """
        Submit an order exactly once. A client_order_id lets us detect that an earlier
        attempt actually reached Alpaca even though the response was lost.
        """
        coid = kwargs.setdefault("client_order_id", f"bot-{uuid.uuid4().hex[:24]}")
        self._save_state(order=self._canonical_order(kwargs))
        last_exc = None
        for attempt in range(1, 4):
            if attempt > 1:
                try:  # did the previous attempt go through?
                    order = self.api.get_order_by_client_order_id(coid)
                    self._observe_order(order)
                    return order
                except PersistenceError:
                    raise
                except Exception:  # noqa: BLE001
                    pass
            if submission_guard is not None and not submission_guard():
                if last_exc is not None:
                    raise last_exc
                raise _EntrySubmissionStopped
            try:
                order = self.api.submit_order(**kwargs)
                self._observe_order(order)
                return order
            except APIError as exc:
                if "client_order_id" in str(exc).lower():
                    order = self.api.get_order_by_client_order_id(coid)
                    self._observe_order(order)
                    return order
                if not is_retryable(exc):
                    raise
                last_exc = exc
            except Exception as exc:  # noqa: BLE001
                if not is_retryable(exc):
                    raise
                last_exc = exc
            log.warning("submit_order failed (%s); retrying", last_exc)
            time.sleep(0.5 * attempt)
        raise last_exc

    @retry(attempts=1)
    def _wait_for_fill(self, order_id: str, timeout: float = config.ORDER_FILL_WAIT, observations=None):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            order = self._get_order(order_id)
            if observations is not None:
                observations.append(order)
            if order.status == "filled" or order.status in ("canceled", "expired", "rejected"):
                return order
            time.sleep(1)
        return self._cancel_and_confirm(order_id, observations=observations)

    def _cancel_and_confirm(self, order_id: str, timeout: float = config.ORDER_CANCEL_WAIT,
                            observations=None):
        self._require_persistence()
        order = self._get_order(order_id)
        deadline = time.monotonic() + timeout
        visited = {order_id}
        requested = set()
        while True:
            if observations is not None:
                observations.append(order)
            if order.status == "replaced":
                owners = [pos for pos in self.positions.values()
                          if order_id == pos.stop_order_id or order_id in pos.stop_order_fills]
                self._account_cancel_observation(order)
                if any(pos.symbol not in self.positions for pos in owners):
                    self.accounting_unresolved = True
                    self._save_state()
                    raise RuntimeError("Fully closed predecessor requires replacement reconciliation")
                if any(pos.exit_order_id == order_id for pos in self.positions.values()):
                    self.accounting_unresolved = True
                    self._save_state()
                    raise RuntimeError("Exit replacement requires cumulative-fill provenance reconciliation")
                replacement = getattr(order, "replaced_by", None)
                if not replacement or replacement in visited or len(visited) >= 10:
                    raise RuntimeError("Cannot cancel unresolved or cyclic replacement chain")
                predecessor_id = order_id
                visited.add(replacement)
                order_id = replacement
                order = self._get_order(order_id)
                for pos in list(self.positions.values()):
                    if pos.stop_order_id == predecessor_id:
                        self._switch_stop(pos, order)
                    elif predecessor_id in pos.stop_order_fills:
                        if order_id not in pos.stop_order_fills:
                            if float(order.filled_qty or 0) != 0:
                                self.accounting_unresolved = True
                                self._save_state()
                                raise RuntimeError("Sibling replacement has ambiguous inherited fills")
                            pos.stop_order_fills = dict(pos.stop_order_fills)
                            pos.stop_order_fills[order_id] = {"qty": 0.0, "notional": 0.0}
                            self._save_state()
                continue
            if order.status in ("filled", "canceled", "expired", "rejected"):
                self._account_cancel_observation(order)
                return order
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Order {order_id} cancellation remains unresolved")
            if order_id not in requested:
                self._audit("cancel_intent", {"broker_order_id": str(order_id)})
                requested.add(order_id)
                try:
                    self.api.cancel_order(order_id)
                except Exception:
                    order = self._get_order(order_id)
                    if order.status == "replaced" or order.status in ("filled", "canceled", "expired", "rejected"):
                        continue
                    raise
            else:
                time.sleep(0.25)
            order = self._get_order(order_id)

    def _account_cancel_observation(self, order):
        for pos in list(self.positions.values()):
            if order.id == pos.stop_order_id or order.id in pos.stop_order_fills:
                self._account_stop_fill(pos, order, "protective fill during cancellation")
                return

    def _cancel_orders_for(self, symbol: str) -> None:
        target = norm_symbol(symbol)
        orders = [order for order in self._list_open_orders() if norm_symbol(order.symbol) == target]
        pos = self.positions.get(symbol)
        if pos is None and orders:
            raise RuntimeError("Cannot cancel orders without managed position ownership")
        related_ids = []
        for order in orders:
            if order.id not in (pos.stop_order_id, pos.stop_group_id, pos.exit_order_id) and order.id not in pos.stop_order_fills:
                related = pos.stop_group_id is not None and getattr(self, "_order_groups", {}).get(order.id) == pos.stop_group_id
                exit_side = "sell" if pos.side == "long" else "buy"
                if not related or float(order.filled_qty or 0) != 0 or getattr(order, "side", None) != exit_side:
                    self.accounting_unresolved = True
                    self._save_state()
                    raise RuntimeError("Untracked order requires reconciliation before cancellation")
                related_ids.append(order.id)
        if related_ids:
            pos.stop_order_fills = dict(pos.stop_order_fills)
            for order_id in related_ids:
                pos.stop_order_fills[order_id] = {"qty": 0.0, "notional": 0.0}
            self._save_state()
        confirmed = set()
        for order in orders:
            if order.id in confirmed:
                continue
            if symbol not in self.positions:
                self.accounting_unresolved = True
                self._save_state()
                raise RuntimeError("Remaining orders after full close require reconciliation")
            terminal = self._cancel_and_confirm(order.id)
            confirmed.update((order.id, terminal.id))
            pos = self.positions.get(symbol)
            if pos is not None and terminal.id == pos.stop_order_id:
                self._account_stop_fill(pos, terminal, "protective fill during cancellation")

    @staticmethod
    def _fmt_qty(asset_class: str, qty: float):
        return int(qty) if asset_class == "equity" and float(qty).is_integer() else qty

    def _stop_price(self, pos, stop):
        asset = self.api.get_asset(pos.symbol)
        increment = getattr(asset, "price_increment", None) if pos.asset_class == "crypto" else None
        tick = Decimal(str(increment or (0.01 if stop >= 1 else 0.0001)))
        value = Decimal(str(stop))
        rounding = ROUND_CEILING if pos.side == "long" else ROUND_FLOOR
        rounded = (value / tick).to_integral_value(rounding=rounding) * tick
        if rounded <= 0 or not math.isfinite(float(rounded)):
            raise ValueError("Invalid rounded stop price")
        return float(rounded)

    def _crypto_limit(self, side: str, stop: float, symbol: Optional[str] = None) -> float:
        buf = self.risk.crypto_buffer
        limit = stop * (1 - buf) if side == "long" else stop * (1 + buf)
        if symbol is None:
            return round(limit, 2)
        asset = self.api.get_asset(symbol)
        tick = Decimal(str(asset.price_increment))
        rounding = ROUND_FLOOR if side == "long" else ROUND_CEILING
        return float((Decimal(str(limit)) / tick).to_integral_value(rounding=rounding) * tick)

    # ================================================================ server-side stops
    def _place_server_stop(self, pos: Position, stop_price: float) -> None:
        """Resting protective stop. Equities: stop order. Crypto: stop-limit (stop orders unsupported)."""
        broker = self.broker_positions().get(norm_symbol(pos.symbol))
        if broker is None or not self._side_matches(pos, broker) or not self._quantity_matches(pos, broker):
            raise RuntimeError("Protective order requires confirmed matching broker exposure")
        exit_side = "sell" if pos.side == "long" else "buy"
        if pos.asset_class == "equity" and not float(pos.qty).is_integer():
            raise ValueError("Fractional equity requires explicit day-stop policy; GTC coverage unavailable")
        sp = self._stop_price(pos, stop_price)
        params = dict(symbol=pos.symbol, qty=self._fmt_qty(pos.asset_class, pos.qty),
                      side=exit_side, time_in_force="gtc")
        if pos.asset_class == "crypto":
            params.update(type="stop_limit", stop_price=sp, limit_price=self._crypto_limit(pos.side, sp, pos.symbol))
        else:
            params.update(type="stop", stop_price=sp)
        order = self._submit(**params)
        order = self._get_order(order.id)
        if order.status not in ("new", "accepted", "pending_new", "partially_filled"):
            raise RuntimeError(f"Protective order {order.id} is {order.status}")
        pos.stop_order_id = order.id
        pos.stop_group_id = None
        pos.server_stop = sp
        pos.stop_filled_qty = pos.stop_filled_notional = 0.0
        pos.protection_verified = False
        if not self._check_stop(pos, order):
            raise RuntimeError(f"Protective order {order.id} coverage is not verified")

    def _check_stop(self, pos: Position, order) -> bool:
        remaining = float(order.qty) - float(order.filled_qty or 0)
        correct_side = order.side == ("sell" if pos.side == "long" else "buy")
        correct_type = order.type == ("stop_limit" if pos.asset_class == "crypto" else "stop")
        price = float(order.stop_price or 0)
        required = self.effective_stop(pos)
        tight_enough = price >= required if pos.side == "long" else price <= required
        durable = getattr(order, "time_in_force", None) == "gtc"
        expiry = getattr(order, "expires_at", None)
        if expiry is not None:
            expires = datetime.fromisoformat(str(expiry).replace("Z", "+00:00"))
            durable = durable and expires.tzinfo is not None and expires > datetime.now(timezone.utc) + timedelta(minutes=5)
        limit_valid = True
        if pos.asset_class == "crypto":
            limit = float(getattr(order, "limit_price", None) or 0)
            asset = self.api.get_asset(pos.symbol)
            tick = float(asset.price_increment)
            if not math.isfinite(tick) or tick <= 0:
                raise ValueError("Invalid crypto price increment")
            aligned = abs(limit / tick - round(limit / tick)) <= 1e-6 and abs(price / tick - round(price / tick)) <= 1e-6
            limit_valid = math.isfinite(limit) and limit > 0 and (
                price * (1 - self.risk.crypto_buffer) - tick < limit <= price if pos.side == "long" else
                price <= limit < price * (1 + self.risk.crypto_buffer) + tick)
            limit_valid = limit_valid and aligned
        symbol_valid = norm_symbol(getattr(order, "symbol", "")) == norm_symbol(pos.symbol)
        pos.protection_verified = (
            order.status in ("new", "accepted", "partially_filled")
            and correct_side and correct_type and durable and limit_valid and symbol_valid
            and math.isfinite(remaining) and abs(remaining - pos.qty) <= 1e-8
            and math.isfinite(price) and price > 0 and tight_enough
        )
        if pos.protection_verified:
            pos.server_stop = price
        return pos.protection_verified

    def _ensure_protection(self, pos: Position) -> None:
        self._require_persistence()
        pos.protection_verified = False
        if pos.exit_order_id or pos.exit_client_order_id:
            self._recover_exit(pos)
            return
        try:
            broker = self.broker_positions().get(norm_symbol(pos.symbol))
            if broker is None:
                return
            if ("long" if float(broker.qty) > 0 else "short") != pos.side:
                raise RuntimeError("Broker side mismatch; existing orders left untouched")
            if not self._quantity_matches(pos, broker):
                return
            try:
                order = self._get_order(pos.stop_order_id) if pos.stop_order_id else None
            except APIError as exc:
                if exc.status_code != 404:
                    raise
                order = None
            for _ in range(10):
                if order is None or order.status != "replaced":
                    break
                replacement = getattr(order, "replaced_by", None)
                if not replacement:
                    raise RuntimeError("Stop replacement ID unavailable")
                self._account_stop_fill(pos, order, "predecessor stop fill")
                next_order = self._get_order(replacement)
                self._switch_stop(pos, next_order)
                order = next_order
            if order is not None and self._check_stop(pos, order):
                return
            if order is not None and order.status in ("pending_replace", "pending_cancel", "pending_new"):
                raise RuntimeError(f"Protective order is {order.status}")
            self._cancel_orders_for(pos.symbol)
            if pos.symbol not in self.positions:
                return
            broker = self.broker_positions().get(norm_symbol(pos.symbol))
            if broker is None:
                return
            side = "long" if float(broker.qty) > 0 else "short"
            if side != pos.side:
                raise RuntimeError("Broker position side differs from managed side")
            if not self._quantity_matches(pos, broker):
                return
            self._place_server_stop(pos, self.effective_stop(pos))
        except Exception as exc:
            log.critical("%s stop coverage unverified; entries disabled: %s", pos.symbol, exc)
        self._save_state()

    def _move_server_stop(self, pos: Position, new_stop: float) -> None:
        self._require_persistence()
        broker = self.broker_positions().get(norm_symbol(pos.symbol))
        if broker is None or not self._side_matches(pos, broker) or not self._quantity_matches(pos, broker):
            return
        pos.protection_verified = False
        self._save_state()
        self._cancel_orders_for(pos.symbol)
        if pos.symbol not in self.positions:
            return
        broker = self.broker_positions().get(norm_symbol(pos.symbol))
        if broker is None or not self._side_matches(pos, broker) or not self._quantity_matches(pos, broker):
            return
        self._place_server_stop(pos, new_stop)
        self._save_state()

    # ================================================================ open / close
    def open_position(self, inst: config.Instrument, side: str, atr: float, price: float,
                      strategy: str, trailing_mult: Optional[float], reason: str,
                      entry_guard: Optional[Callable[[], bool]] = None) -> Optional[Position]:
        if not self.entries_safe():
            log.error("Entry blocked: unresolved exit or unverified protection")
            return None
        symbol = inst.symbol
        acct = self.get_account()
        equity = float(acct.equity)
        if not math.isfinite(atr) or atr <= 0 or not math.isfinite(price) or price <= 0:
            return None
        asset = self.api.get_asset(symbol)
        if not asset.tradable or (side == "short" and not inst.allow_short):
            return None

        if side == "short":
            if not getattr(acct, "shorting_enabled", True):
                log.warning("%s short skipped: shorting not enabled on this account", symbol)
                return None
            asset = self.api.get_asset(symbol)
            if not (asset.shortable and asset.easy_to_borrow):
                log.warning("%s short skipped: not shortable / easy to borrow", symbol)
                return None

        if inst.asset_class == "crypto":
            buying_power = float(getattr(acct, "non_marginable_buying_power", None) or acct.cash)
        else:
            buying_power = float(acct.buying_power)

        size = self.risk.size_position(equity, price, atr, inst.asset_class,
                                       buying_power, self.gross_exposure())
        if size.qty <= 0:
            log.info("%s %s skipped: position size rounds to zero (ATR %.4f, price %.2f). %s",
                     symbol, side, atr, price, size.note)
            return None
        if inst.asset_class == "crypto":
            increment = Decimal(str(asset.min_trade_increment))
            qty = (Decimal(str(size.qty)) / increment).to_integral_value(rounding=ROUND_FLOOR) * increment
            size.qty = float(qty)
            if size.qty < float(asset.min_order_size) or size.qty * price < self.risk.min_crypto_notional:
                return None
            if size.qty * price > 200000:
                raise ValueError("Crypto order exceeds broker notional maximum")

        ownership = self._entry_preflight(symbol)
        if ownership != "clear":
            log.error("Entry blocked for %s: broker ownership preflight is %s", symbol, ownership)
            return None
        if entry_guard is not None and not entry_guard():
            log.info("%s entry canceled before intent: scheduler is stopping", symbol)
            return None

        client_id = f"bot-entry-{uuid.uuid4().hex[:24]}"
        self.entry_intents[client_id] = dict(symbol=symbol, side=side, atr=atr, price=price,
                                           strategy=strategy, trailing_mult=trailing_mult)
        self._save_state()
        params = dict(
            symbol=symbol, qty=self._fmt_qty(inst.asset_class, size.qty),
            side="buy" if side == "long" else "sell", type="market",
            time_in_force="gtc",
            client_order_id=client_id,
        )
        if inst.asset_class == "equity":
            stop = self.risk.hard_stop_price(side, price, atr, size.qty, equity)
            params.update(order_class="oto", stop_loss={"stop_price": self._stop_price(
                SimpleNamespace(symbol=symbol, side=side, asset_class=inst.asset_class), stop)})
        if entry_guard is not None and not entry_guard():
            self.entry_intents.pop(client_id, None)
            self._save_state()
            log.info("%s entry canceled before submission: scheduler is stopping", symbol)
            return None
        try:
            order = self._submit(**params) if entry_guard is None else self._submit(
                submission_guard=entry_guard, **params)
        except _EntrySubmissionStopped:
            self.entry_intents.pop(client_id, None)
            self._save_state()
            log.info("%s entry canceled before broker submission: scheduler is stopping", symbol)
            return None
        except APIError as exc:
            if exc.status_code in (400, 401, 403, 422):
                self.entry_intents.pop(client_id, None)
                self._save_state()
            raise
        observations = [order]
        order = self._wait_for_fill(order.id, observations=observations)
        if any(observation.id != order.id and float(observation.filled_qty or 0) > 0
               for observation in observations):
            self.accounting_unresolved = True
            self._save_state()
            log.critical("%s entry replacement has predecessor fills; retaining intent", symbol)
            return None
        filled = float(order.filled_qty or 0)
        if filled <= 0:
            if order.status in ("canceled", "rejected", "expired", "filled"):
                self.entry_intents.pop(client_id, None)
                self._save_state()
            log.warning("%s entry order did not fill (status=%s)", symbol, order.status)
            return None
        entry = float(order.filled_avg_price)

        # Crypto fees are taken in the asset, so trust the broker's actual quantity.
        bp = self.broker_positions().get(norm_symbol(symbol))
        if bp is None:
            self.accounting_unresolved = True
            self._save_state()
            log.critical("%s entry filled but broker is flat; retaining intent for execution reconciliation", symbol)
            return None
        qty = abs(float(bp.qty))
        if ("long" if float(bp.qty) > 0 else "short") != side:
            self.accounting_unresolved = True
            self._save_state()
            raise RuntimeError("Entry fill and broker side disagree")
        if not self._entry_fill_explains_position(inst.asset_class, filled, qty):
            self.accounting_unresolved = True
            self._save_state()
            log.critical("%s entry fill quantity does not explain broker exposure; retaining intent", symbol)
            return None

        pos = Position(
            symbol=symbol, asset_class=inst.asset_class, strategy=strategy, side=side, qty=qty,
            entry_price=entry, entry_time=datetime.now(timezone.utc).isoformat(), atr=atr,
            hard_stop=self.risk.hard_stop_price(side, entry, atr, qty, equity),
            extreme_price=entry, trailing_mult=trailing_mult,
            entry_fees=(max(0.0, filled - qty) * entry if qty < filled else
                        filled * entry * config.RISK["crypto_taker_fee"])
                       if inst.asset_class == "crypto" else 0.0,
            fees_estimated=inst.asset_class == "crypto",
            identity=client_id,
            stop_group_id=order.id if inst.asset_class == "equity" else None,
        )
        if trailing_mult:
            pos.trailing_stop = self.risk.trailing_stop(side, entry, atr, trailing_mult)

        self.positions[symbol] = pos
        self._save_state()

        # Hard stop is mandatory. If we cannot place it, we do not keep the position.
        try:
            if not self._attach_existing_stop(pos):
                self._cancel_orders_for(symbol)
                if symbol not in self.positions:
                    self.entry_intents.pop(client_id, None)
                    self._save_state()
                    return None
                broker = self.broker_positions().get(norm_symbol(symbol))
                if broker is None or not self._side_matches(pos, broker) or not self._quantity_matches(pos, broker):
                    raise RuntimeError("Entry protection conversion could not confirm residual position")
                self._place_server_stop(pos, self.effective_stop(pos))
        except Exception as exc:  # noqa: BLE001
            if symbol not in self.positions:
                log.critical("%s protection conversion closed exposure; recovery quarantine retained: %s", symbol, exc)
                return None
            log.critical("%s: could not place hard stop (%s). Flattening immediately.", symbol, exc)
            self.positions[symbol] = pos
            self.close_position(symbol, "failed to place hard stop")
            return None

        self.entry_intents.pop(client_id, None)
        self.positions[symbol] = pos
        self._save_state()
        log.info("OPEN %s %s qty=%s @ %.2f | hard stop %.2f | risk@1ATR $%.2f (%.2f%% equity) | %s %s",
                 side.upper(), symbol, pos.qty, entry, pos.hard_stop, size.risk_at_1atr,
                 100 * size.risk_at_1atr / equity, reason, size.note)
        return pos

    def close_position(self, symbol: str, reason: str) -> bool:
        self._require_persistence()
        pos = self.positions.get(symbol)
        if pos is None:
            return False
        pos.protection_verified = False
        self._save_state()
        try:
            return self._close_position(pos, reason)
        except PersistenceError:
            raise
        except Exception as exc:
            log.error("%s exit failed: %s; recovering protection", symbol, exc)
            self._recover_exit(pos)
            return False

    def entries_safe(self) -> bool:
        return not self.persistence_fault and not self.accounting_unresolved and not self.daily_halted and not self.entry_intents and not self.unmanaged_symbols and all(pos.protection_verified and not pos.exit_order_id and not pos.exit_client_order_id
                   for pos in self.positions.values())

    def _entry_preflight(self, symbol: str) -> str:
        """Classify fresh broker ownership before creating an entry intent."""
        target = norm_symbol(symbol)
        try:
            broker = self.broker_positions()
            orders = self._list_open_orders()
        except Exception as exc:  # noqa: BLE001
            self.unmanaged_symbols.add(target)
            log.error("Entry ownership for %s is unknown: %s", symbol, exc)
            return "unknown"

        managed_name = next((name for name in self.positions if norm_symbol(name) == target), None)
        broker_position = broker.get(target)
        if managed_name is not None:
            managed = self.positions[managed_name]
            if broker_position is not None and self._side_matches(managed, broker_position) \
                    and self._quantity_matches(managed, broker_position):
                return "owned"
            self.unmanaged_symbols.add(target)
            return "unknown"
        if broker_position is not None:
            self.unmanaged_symbols.add(target)
            return "foreign"
        if any(norm_symbol(getattr(order, "symbol", "")) == target for order in orders):
            self.unmanaged_symbols.add(target)
            return "unknown"
        return "clear"

    @staticmethod
    def _entry_fill_explains_position(asset_class: str, filled: float, position_qty: float) -> bool:
        if not all(math.isfinite(value) and value > 0 for value in (filled, position_qty)):
            return False
        if asset_class == "crypto":
            fee_rate = float(config.RISK["crypto_taker_fee"])
            if not math.isfinite(fee_rate) or not 0 <= fee_rate < 1:
                return False
            return (position_qty <= filled + 1e-8
                    and filled - position_qty <= filled * fee_rate + 1e-8)
        return math.isclose(position_qty, filled, rel_tol=0, abs_tol=1e-8)

    def _attach_existing_stop(self, pos: Position) -> bool:
        orders = [order for order in self._list_open_orders() if norm_symbol(order.symbol) == norm_symbol(pos.symbol)
                  and order.type in ("stop", "stop_limit")]
        orders.sort(key=lambda order: float(order.stop_price or 0), reverse=pos.side == "long")
        for order in orders:
            if pos.stop_group_id is None or getattr(self, "_order_groups", {}).get(order.id) != pos.stop_group_id:
                continue
            if self._check_stop(pos, order):
                if float(order.filled_qty or 0) > 0 and order.id not in pos.stop_order_fills:
                    self.accounting_unresolved = True
                    self._save_state()
                    raise RuntimeError("Existing partially filled stop requires historical execution reconciliation")
                pos.stop_order_id = order.id
                pos.stop_group_id = getattr(self, "_order_groups", {}).get(order.id)
                pos.server_stop = float(order.stop_price)
                pos.hard_stop = pos.server_stop
                pos.stop_filled_qty = float(order.filled_qty or 0)
                pos.stop_filled_notional = pos.stop_filled_qty * float(order.filled_avg_price or 0)
                return True
        return False

    def _recover_entries(self) -> None:
        if self.accounting_unresolved:
            return
        for client_id, intent in list(self.entry_intents.items()):
            try:
                order = self.api.get_order_by_client_order_id(client_id)
                self._observe_order(order)
                observations = [order]
                order = self._cancel_and_confirm(order.id, observations=observations)
                symbol = intent["symbol"]
                if order.status not in ("filled", "canceled", "expired", "rejected"):
                    raise RuntimeError("Entry recovery requires a terminal order observation")
                filled = float(order.filled_qty or 0)
                if not math.isfinite(filled) or filled < 0:
                    raise ValueError("Invalid recovered entry fill quantity")
                chain_quantities = [float(observation.filled_qty or 0) for observation in observations]
                if not all(math.isfinite(value) and value >= 0 for value in chain_quantities):
                    self.accounting_unresolved = True
                    raise ValueError("Invalid entry replacement-chain fills")
                predecessor_filled = any(value > 0 for value in chain_quantities)
                broker = self.broker_positions().get(norm_symbol(symbol))
                if broker is None:
                    if filled > 0 or predecessor_filled:
                        self.accounting_unresolved = True
                        log.critical("%s recovered entry filled but broker is flat; intent retained for execution reconciliation", symbol)
                    else:
                        self.entry_intents.pop(client_id, None)
                    continue
                if filled <= 0:
                    self.accounting_unresolved = True
                    raise RuntimeError("Zero-fill entry cannot establish broker position ownership")
                if ("long" if float(broker.qty) > 0 else "short") != intent["side"]:
                    self.accounting_unresolved = True
                    raise RuntimeError("Recovered entry intent and broker side disagree")
                instrument = config.INSTRUMENTS[symbol]
                if not self._entry_fill_explains_position(instrument.asset_class, filled, abs(float(broker.qty))):
                    self.accounting_unresolved = True
                    raise RuntimeError("Recovered entry fills do not explain broker position quantity")
                if any(observation.id != order.id and float(observation.filled_qty or 0) > 0
                       for observation in observations):
                    self.accounting_unresolved = True
                    raise RuntimeError("Entry replacement fills require provenance reconciliation")
                if broker is not None and symbol not in self.positions:
                    self.adopt_position(instrument, intent["strategy"], intent["trailing_mult"], intent["atr"],
                                        broker, stop_group_id=order.id if instrument.asset_class == "equity" else None)
                pos = self.positions.get(symbol)
                if pos is not None and pos.protection_verified and self._side_matches(pos, broker) \
                        and self._quantity_matches(pos, broker):
                    self.entry_intents.pop(client_id, None)
            except APIError as exc:
                if exc.status_code == 404:
                    log.critical("Entry %s is not yet visible; retaining intent and blocking entries", client_id)
                else:
                    log.critical("Entry recovery unresolved for %s: %s", client_id, exc)
            except Exception as exc:
                log.critical("Entry recovery unresolved for %s: %s", client_id, exc)
        self._save_state()

    def _recover_exit(self, pos: Position) -> None:
        try:
            if not self._side_matches(pos, self.broker_positions().get(norm_symbol(pos.symbol))):
                return
            if pos.exit_client_order_id and not pos.exit_order_id:
                try:
                    pos.exit_order_id = self.api.get_order_by_client_order_id(pos.exit_client_order_id).id
                except APIError as exc:
                    if exc.status_code != 404:
                        raise
                    raise RuntimeError("Exit intent not visible at broker; retaining identity and blocking resubmission") from exc
            if pos.exit_order_id:
                order = self._cancel_and_confirm(pos.exit_order_id)
                self._account_exit_fill(pos, order, "exit recovery")
                pos.exit_order_id = None
                pos.exit_client_order_id = None
            self._cancel_orders_for(pos.symbol)
            remaining = self.broker_positions().get(norm_symbol(pos.symbol))
            if remaining is None:
                if pos.symbol in self.positions:
                    self._record_close(pos, self._exit_price_from_orders(pos), "flat during recovery")
                return
            if not self._side_matches(pos, remaining) or not self._quantity_matches(pos, remaining):
                return
            self.positions[pos.symbol] = pos
            self._place_server_stop(pos, self.effective_stop(pos))
        except Exception as exc:
            pos.protection_verified = False
            log.critical("%s protection unresolved; entries disabled: %s", pos.symbol, exc)
        finally:
            self._save_state()

    def _account_exit_fill(self, pos: Position, order, reason: str) -> None:
        self._account_order_fill(pos, order, reason, "exit")

    def _account_stop_fill(self, pos: Position, order, reason: str) -> None:
        order_id = str(getattr(order, "id", None) or pos.stop_order_id)
        if order_id != pos.stop_order_id and order_id not in pos.stop_order_fills:
            self._switch_stop(pos, order)
        self._account_order_fill(pos, order, reason, "stop")
        if pos.symbol not in self.positions and self.accounting_unresolved:
            raise RuntimeError("Fully closed protective order requires replacement/group reconciliation")

    def _switch_stop(self, pos, order):
        order_id = str(order.id)
        filled = float(order.filled_qty or 0)
        if filled > 0 and order_id not in pos.stop_order_fills:
            self.accounting_unresolved = True
            self._save_state()
            raise RuntimeError("Replacement stop has ambiguous inherited fills; reconciliation required")
        pos.stop_order_id = order_id
        known = pos.stop_order_fills.get(order_id, {"qty": 0.0, "notional": 0.0})
        pos.stop_filled_qty = known["qty"]
        pos.stop_filled_notional = known["notional"]
        pos.protection_verified = False
        self._save_state()

    def _account_order_fill(self, pos: Position, order, reason: str, kind: str) -> None:
        filled = float(order.filled_qty or 0)
        quantity_field = f"{kind}_filled_qty"
        notional_field = f"{kind}_filled_notional"
        order_id = str(getattr(order, "id", None) or pos.stop_order_id)
        prior_qty, prior_notional = getattr(pos, quantity_field), getattr(pos, notional_field)
        if kind == "stop" and order_id in pos.stop_order_fills:
            prior_qty = pos.stop_order_fills[order_id]["qty"]
            prior_notional = pos.stop_order_fills[order_id]["notional"]
        delta = filled - prior_qty
        if not math.isfinite(filled) or delta < -1e-9 or delta > pos.qty + 1e-8:
            self.accounting_unresolved = True
            self._save_state()
            raise RuntimeError(f"{pos.symbol}: inconsistent cumulative {kind} fills")
        if delta > 1e-9:
            candidate = replace(pos)
            candidate.last_fill_order_id = str(getattr(order, "id", None) or
                                               (pos.exit_order_id if kind == "exit" else pos.stop_order_id) or "")
            candidate.last_fill_time = str(getattr(order, "filled_at", None) or datetime.now(timezone.utc).isoformat())
            notional = filled * float(order.filled_avg_price)
            price = (notional - prior_notional) / delta
            if not math.isfinite(price) or price <= 0:
                raise ValueError("Invalid cumulative execution price")
            if kind != "stop" or order_id == pos.stop_order_id:
                setattr(candidate, quantity_field, filled)
                setattr(candidate, notional_field, notional)
            if kind == "stop":
                candidate.stop_order_fills = dict(pos.stop_order_fills)
                candidate.stop_order_fills[candidate.last_fill_order_id] = {"qty": filled, "notional": notional}
            quarantine_close = kind == "stop" and (
                getattr(order, "status", None) == "replaced" or pos.stop_group_id is not None
                or any(known_id != order_id for known_id in pos.stop_order_fills))
            self._record_close(candidate, price, reason, delta, quarantine_close=quarantine_close)
            pos.__dict__.update(candidate.__dict__)
            if pos.symbol in self.positions:
                self.positions[pos.symbol] = pos

    def _close_position(self, pos: Position, reason: str) -> bool:
        symbol = pos.symbol
        bp = self.broker_positions().get(norm_symbol(symbol))
        if not self._side_matches(pos, bp):
            return False
        if pos.exit_order_id or pos.exit_client_order_id:
            self._recover_exit(pos)
            return symbol not in self.positions
        self._cancel_orders_for(symbol)             # frees the shares held by the stop order
        if symbol not in self.positions:
            return True
        bp = self.broker_positions().get(norm_symbol(symbol))
        if not self._side_matches(pos, bp):
            return False
        if bp is None:                              # already gone (stop filled)
            self._record_close(pos, self._exit_price_from_orders(pos), reason + " (already flat at broker)")
            return True
        if not self._quantity_matches(pos, bp):
            return False

        qty = abs(float(bp.qty))
        pos.exit_client_order_id = f"bot-exit-{uuid.uuid4().hex[:24]}"
        pos.exit_filled_qty = pos.exit_filled_notional = 0.0
        self._save_state()
        order = self._submit(
            symbol=symbol, qty=self._fmt_qty(pos.asset_class, qty),
            side="sell" if pos.side == "long" else "buy", type="market",
            time_in_force="gtc" if pos.asset_class == "crypto" else "day",
            client_order_id=pos.exit_client_order_id,
        )
        pos.exit_order_id = order.id
        self._save_state()
        order = self._wait_for_fill(order.id)
        if order.status not in ("filled", "canceled", "expired", "rejected"):
            raise RuntimeError(f"Exit order {order.id} remains {order.status}")
        remaining = self.broker_positions().get(norm_symbol(symbol))
        self._account_exit_fill(pos, order, reason)
        pos.exit_order_id = None
        pos.exit_client_order_id = None
        if remaining is not None:
            if not self._side_matches(pos, remaining) or not self._quantity_matches(pos, remaining):
                return False
            self.positions[symbol] = pos
            log.warning("%s exit left qty=%s (status=%s) - re-arming stop", symbol, pos.qty, order.status)
            try:
                self._place_server_stop(pos, self.effective_stop(pos))
            except Exception as exc:  # noqa: BLE001
                log.critical("%s: could not re-arm stop: %s", symbol, exc)
            self._save_state()
            return False
        if symbol in self.positions:
            self._record_close(pos, self._exit_price_from_orders(pos), reason + " (flat at broker)")
        return True

    def _side_matches(self, pos: Position, broker_position) -> bool:
        if broker_position is None:
            return True
        qty = float(broker_position.qty)
        if not math.isfinite(qty) or qty == 0 or ("long" if qty > 0 else "short") != pos.side:
            pos.protection_verified = False
            self.accounting_unresolved = True
            self._save_state()
            log.critical("%s broker side mismatch: orders and software liquidation disabled", pos.symbol)
            return False
        return True

    def _quantity_matches(self, pos: Position, broker_position) -> bool:
        if broker_position is None:
            return True
        qty = abs(float(broker_position.qty))
        if not math.isfinite(qty) or not math.isclose(qty, pos.qty, rel_tol=0, abs_tol=1e-8):
            pos.protection_verified = False
            self.accounting_unresolved = True
            self._save_state()
            log.critical("%s quantity mismatch: broker=%s accounted=%s; orders disabled", pos.symbol, qty, pos.qty)
            return False
        return True

    def _exit_price_from_orders(self, pos: Position) -> Optional[float]:
        if pos.stop_order_id:
            try:
                order = self._get_order(pos.stop_order_id)
                if order.status == "filled" and order.filled_avg_price:
                    pos.last_fill_order_id = pos.stop_order_id
                    pos.last_fill_time = str(getattr(order, "filled_at", None) or datetime.now(timezone.utc).isoformat())
                    return float(order.filled_avg_price)
            except Exception as exc:  # noqa: BLE001
                log.warning("could not fetch stop order for %s: %s", pos.symbol, exc)
        log.critical("%s exit price unknown; accounting unresolved and entries blocked", pos.symbol)
        self.accounting_unresolved = True
        return None

    def _record_close(self, pos: Position, exit_price: Optional[float], reason: str,
                      closed_qty: Optional[float] = None, quarantine_close: bool = False) -> None:
        self._require_persistence()
        original = pos
        pos = replace(pos)
        qty = pos.qty if closed_qty is None else min(closed_qty, pos.qty)
        if qty <= 0:
            return
        if self.day is not None:
            self.update_daily(persist=False)
        if exit_price is None:
            self.accounting_unresolved = True
        if quarantine_close and qty >= pos.qty - 1e-9:
            self.accounting_unresolved = True
        sign = 1 if pos.side == "long" else -1
        gross_pnl = sign * (exit_price - pos.entry_price) * qty if exit_price is not None else None
        entry_fees = pos.entry_fees * qty / pos.qty
        exit_fees = qty * exit_price * config.RISK["crypto_taker_fee"] if pos.asset_class == "crypto" and exit_price is not None else 0.0
        fees = entry_fees + exit_fees
        pnl = gross_pnl - fees if gross_pnl is not None else None
        pos.exit_sequence += 1
        if not pos.identity:
            pos.identity = f"{pos.symbol}-{pos.entry_time}"
        row = {
            "execution_id": f"{pos.identity}:{pos.exit_sequence}", "reason": reason,
            "broker_order_id": pos.last_fill_order_id, "entry_time": pos.entry_time,
            "price_verified": exit_price is not None,
            "timestamp": pos.last_fill_time or datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "instrument": pos.symbol, "direction": pos.side,
            "entry_price": round(pos.entry_price, 4), "exit_price": round(exit_price, 4) if exit_price is not None else None,
            "profit_loss": round(pnl, 2) if pnl is not None else None, "position_size": qty,
            "gross_profit_loss": round(gross_pnl, 2) if gross_pnl is not None else None, "fees": round(fees, 2),
            "fees_estimated": pos.fees_estimated or pos.asset_class == "crypto",
            "accounting": {"qty": str(Decimal(str(qty))), "entry_price": str(Decimal(str(pos.entry_price))),
                           "exit_price": str(Decimal(str(exit_price))) if exit_price is not None else None,
                           "profit_loss": str(Decimal(str(pnl))) if pnl is not None else None,
                           "fees": str(Decimal(str(fees))), "currency": "USD"},
        }
        fill_date = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00")).astimezone(ZoneInfo(config.MARKET_TZ)).date().isoformat()
        row["accounting_date"] = fill_date
        data = self._state_data()
        if self.day is None or fill_date == self.day:
            data["daily"]["realized"] += pnl or 0.0
            data["daily"]["trades"] += 1
        pos.qty = max(0.0, pos.qty - qty)
        pos.entry_fees = max(0.0, pos.entry_fees - entry_fees)
        if pos.qty <= 1e-9:
            data["positions"].pop(pos.symbol, None)
        else:
            data["positions"][pos.symbol] = asdict(pos)
        data["daily_controls"]["accounting_unresolved"] = self.accounting_unresolved
        if self.day is not None:
            self._pending_daily.append(self._daily_row(self.last_equity or self.day_start_equity,
                                                     data["daily"]["realized"], data["daily"]["trades"]))
        self._pending_exit = row
        self._save_state(data)
        original.__dict__.update(pos.__dict__)
        if pos.qty <= 1e-9:
            self.positions.pop(pos.symbol, None)
        else:
            self.positions[pos.symbol] = original
        self.realized_today = data["daily"]["realized"]
        self.trades_today = data["daily"]["trades"]
        log.info("CLOSE %s %s qty=%s entry=%.2f exit=%s P&L=%s | %s",
                 pos.side.upper(), pos.symbol, qty, pos.entry_price, exit_price, pnl, reason)

    def adopt_position(self, inst: config.Instrument, strategy: str, trailing_mult: Optional[float],
                       atr: float, bp, stop_group_id: Optional[str] = None) -> None:
        """Take over a broker position the bot has no record of (e.g. after a crash)."""
        self._require_persistence()
        entry = float(bp.avg_entry_price)
        signed_qty = float(bp.qty)
        side = "long" if signed_qty > 0 else "short"
        qty = abs(signed_qty)
        equity = float(self.get_account().equity)
        current = float(bp.current_price)
        if not all(math.isfinite(value) and value > 0 for value in (equity, current, qty, entry)):
            raise ValueError("Cannot adopt invalid broker position values")
        if not math.isfinite(atr) or atr <= 0:
            atr = equity * self.risk.risk_pct / qty
        pos = Position(
            symbol=inst.symbol, asset_class=inst.asset_class, strategy=strategy, side=side, qty=qty,
            entry_price=entry, entry_time=datetime.now(timezone.utc).isoformat(), atr=atr,
            hard_stop=self.risk.hard_stop_price(side, current, atr, qty, equity),
            extreme_price=max(entry, current) if side == "long" else min(entry, current),
            trailing_mult=trailing_mult,
            identity=f"adopt-{uuid.uuid4().hex}",
            stop_group_id=stop_group_id,
        )
        if trailing_mult:
            pos.trailing_stop = self.risk.trailing_stop(side, pos.extreme_price, atr, trailing_mult)
        self.positions[inst.symbol] = pos
        self._save_state()
        if not self._attach_existing_stop(pos):
            try:
                if any(norm_symbol(getattr(order, "symbol", "")) == norm_symbol(pos.symbol)
                       for order in self._list_open_orders()):
                    self.accounting_unresolved = True
                    self._save_state()
                    raise RuntimeError("Unowned open order prevents safe protection during adoption")
                self._place_server_stop(pos, self.effective_stop(pos))
            except Exception as exc:
                log.critical("%s adoption protection failed: %s; retaining recovery state", inst.symbol, exc)
        self._save_state()
        if pos.protection_verified:
            log.warning("ADOPTED unmanaged %s position in %s qty=%s entry=%.2f stop=%.2f",
                        side, inst.symbol, qty, entry, pos.server_stop)
        else:
            log.critical("Retained adopted %s position in %s without verified protection", side, inst.symbol)

    # ================================================================ monitoring
    def effective_stop(self, pos: Position) -> float:
        return self.risk.effective_stop(pos.side, pos.hard_stop, pos.trailing_stop)

    def reconcile(self) -> Dict[str, object]:
        """Detect positions that vanished at the broker (e.g. the server-side stop filled)."""
        self._require_persistence()
        if self.entry_intents:
            self._recover_entries()
        broker = self.broker_positions()
        self.unmanaged_symbols = {symbol for symbol in broker
                                  if symbol not in {norm_symbol(name) for name in self.positions}}
        for normalized in list(self.unmanaged_symbols):
            inst = next((inst for inst in config.INSTRUMENTS.values()
                         if norm_symbol(inst.symbol) == normalized), None)
            if inst is not None and config.ADOPT_CONFIGURED_POSITIONS and not self.entry_intents \
                    and not self.accounting_unresolved:
                try:
                    self.adopt_position(inst, inst.strategy, None, float("nan"), broker[normalized])
                    self.unmanaged_symbols.discard(normalized)
                except Exception as exc:
                    log.critical("Cannot adopt %s: %s", normalized, exc)
            else:
                log.critical("Unmanaged position %s: entries blocked; explicit ownership approval required", normalized)
        for symbol, pos in list(self.positions.items()):
            if pos.exit_order_id or pos.exit_client_order_id:
                self._recover_exit(pos)
                continue
            bp = broker.get(norm_symbol(symbol))
            if pos.stop_order_id:
                try:
                    order = self._get_order(pos.stop_order_id)
                    filled = float(order.filled_qty or 0)
                    delta = filled - pos.stop_filled_qty
                    self._account_stop_fill(pos, order, "protective stop fill")
                    if delta > 1e-9:
                        refreshed = self.broker_positions()
                        broker = refreshed
                        bp = refreshed.get(norm_symbol(symbol))
                except Exception as exc:
                    pos.protection_verified = False
                    self.accounting_unresolved = True
                    log.error("%s stop reconciliation failed: %s", symbol, exc)
                    continue
            if symbol not in self.positions:
                continue
            if norm_symbol(symbol) not in broker:
                self._record_close(pos, self._exit_price_from_orders(pos), "closed at broker (stop hit)")
            elif bp is not None:
                broker_side = "long" if float(bp.qty) > 0 else "short"
                if broker_side != pos.side:
                    pos.protection_verified = False
                    log.critical("%s broker side differs; manual reconciliation required", symbol)
                    continue
                broker_entry = float(getattr(bp, "avg_entry_price", pos.entry_price))
                broker_qty = abs(float(bp.qty))
                ratio = broker_qty / pos.qty if pos.qty else 1.0
                if not math.isclose(ratio, 1.0) and math.isclose(broker_entry * ratio, pos.entry_price, rel_tol=1e-4):
                    pos.protection_verified = False
                    self.accounting_unresolved = True
                    log.critical("%s split-like broker change requires action/fill-unit reconciliation; state retained", symbol)
                elif not math.isclose(broker_qty, pos.qty, abs_tol=1e-8):
                    pos.protection_verified = False
                    self.accounting_unresolved = True
                    log.critical("%s unexplained broker quantity %s versus accounted %s; residual retained",
                                 symbol, broker_qty, pos.qty)
        self._save_state()
        return broker

    @retry(attempts=1, budget=config.MONITOR_INTERVAL)
    def manage_positions(self) -> None:
        """Ratchet trailing stops, move resting stops, and enforce stops in software."""
        broker = self.reconcile()
        market_open = self.market_is_open()
        for symbol, pos in list(self.positions.items()):
            bp = broker.get(norm_symbol(symbol))
            if not self._side_matches(pos, bp):
                continue
            self._ensure_protection(pos)
            if symbol not in self.positions:
                continue
            if pos.asset_class == "equity" and not market_open:
                continue
            bp = self.broker_positions().get(norm_symbol(symbol))
            if not self._side_matches(pos, bp):
                continue
            if not self._quantity_matches(pos, bp):
                continue
            if bp is None:
                continue
            price = float(bp.current_price)
            if pos.exit_target is not None and (
                    (pos.side == "long" and price >= pos.exit_target) or
                    (pos.side == "short" and price <= pos.exit_target)):
                self.close_position(symbol, "mean reversion target reached during monitoring")
                continue

            pos.extreme_price = max(pos.extreme_price, price) if pos.side == "long" \
                else min(pos.extreme_price, price)
            if pos.trailing_mult:
                new_trail = self.risk.trailing_stop(pos.side, pos.extreme_price, pos.atr, pos.trailing_mult)
                if pos.trailing_stop is None or \
                        (pos.side == "long" and new_trail > pos.trailing_stop) or \
                        (pos.side == "short" and new_trail < pos.trailing_stop):
                    pos.trailing_stop = new_trail

            stop = self.effective_stop(pos)
            breached = price <= stop if pos.side == "long" else price >= stop
            if breached:
                log.warning("%s price %.2f breached stop %.2f - closing at market", symbol, price, stop)
                self.close_position(symbol, f"software stop at {stop:.2f}")
                continue

            # Only touch the resting order when the stop moved meaningfully (saves API calls).
            if pos.server_stop is None:
                improvement = float("inf")
            elif pos.side == "long":
                improvement = stop - pos.server_stop
            else:
                improvement = pos.server_stop - stop
            if improvement >= 0.25 * pos.atr:
                try:
                    self._move_server_stop(pos, stop)
                    log.info("%s stop moved to %.2f", symbol, pos.server_stop)
                except Exception as exc:  # noqa: BLE001
                    log.error("could not move stop for %s: %s (software stop still active)", symbol, exc)
        self._save_state()

    # ================================================================ bar bookkeeping
    def mark_bar(self, symbol: str, bar_ts: str) -> None:
        self._require_persistence()
        self.last_bar_ts[symbol] = bar_ts
        self._save_state()

    # ================================================================ daily P&L
    def update_daily(self, equity: Optional[float] = None, persist: bool = True) -> None:
        self._require_persistence()
        account = None
        if equity is None:
            account = self.get_account()
            equity = float(account.equity)
        if not math.isfinite(equity) or equity <= 0:
            self.daily_halted = True
            raise ValueError("Daily equity must be finite and positive")
        today = datetime.now(ZoneInfo(config.MARKET_TZ)).date().isoformat()
        if self.day != today:
            if self.day is not None:
                self._write_daily_row(self.last_equity or equity)
            if account is None:
                account = self.get_account()
            baseline = float(getattr(account, "last_equity", equity))
            if not math.isfinite(baseline) or baseline <= 0:
                raise ValueError("Broker opening equity unavailable")
            self.day, self.day_start_equity = today, baseline
            self.realized_today, self.trades_today = 0.0, 0
            self.daily_halted = False
            self.daily_cash_flow = 0.0
            self.cash_flow_ids = []
        try:
            token = None
            cash_updates = {}
            while True:
                activities = self.api.get_activities(activity_types=["CSD", "CSW"], date=today,
                                                     direction="asc", page_size=100, page_token=token)
                for activity in activities:
                    amount = float(activity.net_amount)
                    if not isinstance(activity.id, str) or not activity.id or not math.isfinite(amount):
                        raise ValueError("Invalid cash-flow activity")
                    if activity.id not in self.cash_flow_ids:
                        if activity.id in cash_updates and cash_updates[activity.id] != amount:
                            raise ValueError("Conflicting cash-flow activity")
                        cash_updates[activity.id] = amount
                if len(activities) < 100:
                    break
                next_token = activities[-1].id
                if next_token == token:
                    raise ValueError("Cash-flow pagination repeated token")
                token = next_token
            self.daily_cash_flow += sum(cash_updates.values())
            self.cash_flow_ids.extend(cash_updates)
        except Exception:
            self.daily_halted = True
            if persist:
                self._save_state()
            raise
        adjusted = equity - self.daily_cash_flow
        if self.risk.daily_loss_exceeded(self.day_start_equity, adjusted):
            self.daily_halted = True
        self.last_equity = equity
        if persist:
            self._write_daily_row(equity)
            self._save_state()

    def _write_daily_row(self, ending_equity: float) -> None:
        self._pending_daily.append(self._daily_row(ending_equity, self.realized_today, self.trades_today))

    def _daily_row(self, ending_equity, realized, trades):
        start = self.day_start_equity or ending_equity
        pnl = ending_equity - start - self.daily_cash_flow
        return {
            "date": self.day, "starting_equity": round(start, 2), "ending_equity": round(ending_equity, 2),
            "pnl": round(pnl, 2), "pnl_pct": round(100 * pnl / start, 3) if start else 0.0,
            "realized_pnl": round(realized, 2), "trades_closed": trades,
        }

    def daily_loss_exceeded(self, equity: float) -> bool:
        self.update_daily(equity)
        return self.daily_halted

    # ================================================================ persistence
    def _state_data(self):
        return {
            "entry_intents": self.entry_intents,
            "positions": {s: asdict(p) for s, p in self.positions.items()},
            "last_bar_ts": self.last_bar_ts,
            "daily": {"day": self.day, "start_equity": self.day_start_equity,
                      "last_equity": self.last_equity, "realized": self.realized_today,
                      "trades": self.trades_today},
            "daily_controls": {"halted": self.daily_halted, "cash_flow": self.daily_cash_flow,
                               "cash_flow_ids": self.cash_flow_ids, "accounting_unresolved": self.accounting_unresolved},
        }

    def _require_persistence(self):
        if self.persistence_fault:
            raise PersistenceError("Persistence quarantined; stop trading and repair/restart explicitly")

    def _save_state(self, data=None, order=None) -> None:
        self._require_persistence()
        data = self._state_data() if data is None else data
        try:
            self._validate_state(data)
            if self._ledger is None:
                self._ledger = Ledger(config.LEDGER_FILE)
                self._ledger.import_legacy(config.TRADES_CSV, config.DAILY_PNL_CSV)
            self._reports_dirty = self._reports_dirty or bool(self._pending_exit or self._pending_daily)
            self._ledger.commit(data, self._pending_exit, self._pending_daily, order)
        except Exception as exc:
            self.accounting_unresolved = True
            self.persistence_fault = True
            raise PersistenceError("Portfolio commit failed; trading quarantined") from exc
        self._pending_exit = None
        self._pending_daily.clear()
        if self._reports_dirty:
            self._export_reports()
        tmp = config.STATE_FILE.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(data, indent=2, allow_nan=False))
            tmp.replace(config.STATE_FILE)
        except OSError as exc:
            log.error("JSON compatibility snapshot failed; SQLite state remains authoritative: %s", exc)

    def _load_state(self) -> None:
        if not config.STATE_FILE.exists() and not config.LEDGER_FILE.exists():
            return
        try:
            self._ledger = Ledger(config.LEDGER_FILE)
            data = self._ledger.load()
            if data is None:
                data = Ledger.decode(config.STATE_FILE.read_text()) if config.STATE_FILE.exists() else {}
            self._validate_state(data)
            self.entry_intents = data.get("entry_intents", {})
            names = {f.name for f in fields(Position)}
            self.positions = {s: Position(**{k: v for k, v in d.items() if k in names})
                              for s, d in data.get("positions", {}).items()}
            for pos in self.positions.values():
                pos.protection_verified = False
            self.last_bar_ts = data.get("last_bar_ts", {})
            daily = data.get("daily", {})
            self.day = daily.get("day")
            self.day_start_equity = daily.get("start_equity")
            self.last_equity = daily.get("last_equity")
            self.realized_today = daily.get("realized", 0.0)
            self.trades_today = daily.get("trades", 0)
            controls = data.get("daily_controls", {})
            self.daily_halted = controls.get("halted", False)
            self.daily_cash_flow = controls.get("cash_flow", 0.0)
            self.cash_flow_ids = controls.get("cash_flow_ids", [])
            self.accounting_unresolved = controls.get("accounting_unresolved", False)
            self._ledger.import_legacy(config.TRADES_CSV, config.DAILY_PNL_CSV)
            self._export_reports()
            log.info("Restored state: %d open position(s)", len(self.positions))
        except Exception as exc:  # noqa: BLE001
            self.accounting_unresolved = True
            self.persistence_fault = True
            log.critical("Recovery quarantined; database will not be overwritten: %s", exc)

    @staticmethod
    def _validate_state(data):
        validate_state(data)

    def bind_account(self, account_id):
        self._require_persistence()
        try:
            if self._ledger is None:
                self._ledger = Ledger(config.LEDGER_FILE)
            self._ledger.bind(account_id, "paper" if config.IS_PAPER else "live")
            self._ledger.record("session_started", {"risk": config.RISK, "mean_reversion": config.MEAN_REVERSION,
                                                   "momentum_breakout": config.MOMENTUM_BREAKOUT,
                                                   "trend_following": config.TREND_FOLLOWING})
        except Exception as exc:
            self.persistence_fault = True
            raise PersistenceError("Account binding failed") from exc

    @property
    def ledger_database_id(self) -> str:
        if self._ledger is None:
            raise PersistenceError("Ledger is not initialized")
        database_id = self._ledger.get_metadata("database_id")
        if not isinstance(database_id, str) or not database_id:
            raise PersistenceError("Ledger identity unavailable")
        return database_id

    @staticmethod
    def _canonical_order(parameters):
        numeric = {"qty", "notional", "stop_price", "limit_price", "filled_qty", "filled_avg_price"}
        return {key: Portfolio._canonical_order(value) if isinstance(value, dict) else
                str(Decimal(str(value))) if key in numeric and value is not None else value
                for key, value in parameters.items()}

    def _audit(self, kind, payload):
        self._require_persistence()
        self._save_state()
        if self._ledger is not None:
            try:
                self._ledger.record(kind, payload)
            except Exception as exc:
                self.persistence_fault = True
                raise PersistenceError("Audit write failed") from exc

    def _observe_order(self, order):
        self._require_persistence()
        if self._ledger is None:
            return
        payload = {}
        for name in ("id", "client_order_id", "symbol", "side", "type", "status", "qty", "filled_qty",
                     "filled_avg_price", "stop_price", "limit_price", "filled_at", "replaced_by", "replaces"):
            value = getattr(order, name, None)
            if value is None or isinstance(value, (str, int, float, bool)):
                payload[name] = value
            elif isinstance(value, datetime):
                payload[name] = value.isoformat()
        payload = self._canonical_order(payload)
        try:
            self._ledger.observe_order(payload)
        except Exception as exc:
            self.persistence_fault = True
            raise PersistenceError("Order observation could not be persisted") from exc

    def _export_reports(self):
        self._reports_dirty = False
        for path, columns, daily in ((config.TRADES_CSV, trade_logger.TRADE_FIELDS, False),
                                     (config.DAILY_PNL_CSV, trade_logger.DAILY_FIELDS, True)):
            try:
                self._ledger.export(path, columns, daily=daily)
            except Exception as exc:
                self._reports_dirty = True
                log.error("Derived report %s unavailable; committed state unchanged: %s", path, exc)
                try:
                    self._ledger.record("export_failed", {"path": str(path), "error": str(exc)},
                                        "export:" + str(path) + ":" + str(exc))
                except Exception:
                    log.error("Export failure could not be recorded in audit journal")

    def sync_activities(self, max_pages=20):
        self._require_persistence()
        self._save_state()
        now = datetime.now(timezone.utc)
        checkpoint = self._ledger.get_metadata("activities_synced_at")
        start = datetime.fromisoformat(checkpoint) if checkpoint else now
        after = (start - timedelta(days=7)).isoformat()
        token = None
        for _ in range(max_pages):
            activities = self.api.get_activities(after=after, direction="asc", page_size=100, page_token=token)
            for activity in activities:
                raw = getattr(activity, "_raw", None)
                if not isinstance(raw, dict) or not raw.get("id"):
                    raise ValueError("Broker activity payload or identity unavailable")
                try:
                    self._ledger.ingest_activity(raw)
                except Exception as exc:
                    self.persistence_fault = True
                    raise PersistenceError("Broker activity audit write failed") from exc
            if len(activities) < 100:
                try:
                    self._ledger.set_metadata("activities_synced_at", now.isoformat())
                except Exception as exc:
                    self.persistence_fault = True
                    raise PersistenceError("Activity checkpoint commit failed") from exc
                return
            next_token = str(activities[-1].id)
            if next_token == token:
                raise ValueError("Broker activity pagination repeated token")
            token = next_token
        raise TimeoutError("Activity sync page budget exhausted; checkpoint retained")
