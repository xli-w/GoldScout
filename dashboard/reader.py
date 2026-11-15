"""Credential-free, read-only projection of committed ledger state."""

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from dashboard.contracts import freshness


class ReadError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def decode(payload):
    def invalid(value):
        raise ReadError("ledger_invalid")

    return json.loads(payload, parse_float=Decimal, parse_constant=invalid)


def identity(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise ReadError("ledger_invalid")
    return value


def decimal_text(value, positive=False):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal, str)):
        raise ReadError("ledger_invalid")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ReadError("ledger_invalid") from exc
    if not number.is_finite() or positive and number <= 0:
        raise ReadError("ledger_invalid")
    return format(number, "f")


def utc_text(value):
    if not isinstance(value, str):
        raise ReadError("ledger_invalid")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ReadError("ledger_invalid")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def metadata(now, revision=None, observed_at=None, environment="unknown", database_id=None):
    return {
        "generated_at": now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source_mode": "recorded", "database_id": database_id,
        "environment": environment, "ledger_revision": revision, "telemetry_run_id": None,
        "ledger": {"observed_at": observed_at, "freshness": freshness(observed_at, now, 600), "max_age_seconds": 600},
        "telemetry": {"observed_at": None, "freshness": "missing", "max_age_seconds": 90},
        "broker": {"observed_at": None, "freshness": "missing", "max_age_seconds": 90},
    }


class LedgerReader:
    def __init__(self, database):
        self.database = Path(database).resolve()

    def snapshot(self, now=None, collection=None):
        now = now or datetime.now(timezone.utc)
        if not self.database.is_file():
            raise ReadError("ledger_missing")
        try:
            with closing(sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True, timeout=0.25)) as connection:
                connection.execute("PRAGMA query_only=ON")
                connection.execute("BEGIN")
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                if version != 1:
                    raise ReadError("schema_unsupported")
                row = connection.execute("SELECT payload FROM state WHERE id=1").fetchone()
                revision = connection.execute("SELECT value FROM metadata WHERE key='state_sequence'").fetchone()
                if not row or not revision:
                    raise ReadError("ledger_invalid")
                sequence = int(revision[0])
                latest = connection.execute("SELECT MAX(sequence) FROM events WHERE kind='state_committed'").fetchone()[0]
                if sequence < 1 or sequence != latest:
                    raise ReadError("ledger_invalid")
                event = connection.execute(
                    "SELECT payload, observed_at FROM events WHERE sequence=? AND kind='state_committed'", (sequence,)).fetchone()
                state = decode(row[0])
                if not event or state != decode(event[0]):
                    raise ReadError("ledger_invalid")
                binding_row = connection.execute("SELECT value FROM metadata WHERE key='account_binding'").fetchone()
                database_row = connection.execute("SELECT value FROM metadata WHERE key='database_id'").fetchone()
                if not database_row:
                    raise ReadError("ledger_invalid")
                database_id = identity(database_row[0])
                checkpoint = connection.execute("SELECT value FROM metadata WHERE key='activities_synced_at'").fetchone()
                environment = "unknown"
                if binding_row:
                    binding = decode(binding_row[0])
                    if not isinstance(binding, dict) or binding.get("environment") not in ("paper", "live"):
                        raise ReadError("ledger_invalid")
                    identity(binding.get("account_id"))
                    environment = binding["environment"]
                observed = utc_text(event[1])
                if freshness(observed, now, 600) == "invalid":
                    raise ReadError("ledger_invalid")
                overview, positions = self.project(state)
                result = {"meta": metadata(now, sequence, observed, environment, database_id),
                    "overview": overview, "positions": positions,
                    "activity_checkpoint": utc_text(checkpoint[0]) if checkpoint else None}
                if collection is not None:
                    result["collection"] = collection(connection, result["meta"])
                return result
        except ReadError:
            raise
        except sqlite3.OperationalError as exc:
            code = "source_unavailable" if "locked" in str(exc).lower() or "unable to open" in str(exc).lower() else "ledger_invalid"
            raise ReadError(code) from exc
        except (sqlite3.DatabaseError, ValueError, TypeError, KeyError, OverflowError) as exc:
            raise ReadError("ledger_invalid") from exc

    @staticmethod
    def project(state):
        if not isinstance(state, dict) or not isinstance(state.get("positions"), dict):
            raise ReadError("ledger_invalid")
        for section in ("daily", "daily_controls", "entry_intents"):
            if section in state and not isinstance(state[section], dict):
                raise ReadError("ledger_invalid")
        daily = state.get("daily", {})
        day = daily.get("day")
        if day is not None:
            if not isinstance(day, str) or datetime.strptime(day, "%Y-%m-%d").strftime("%Y-%m-%d") != day:
                raise ReadError("ledger_invalid")
        reasons = []
        controls = state.get("daily_controls", {})
        for flag, reason in (("halted", "daily_halted"), ("accounting_unresolved", "accounting_unresolved")):
            if flag in controls and not isinstance(controls[flag], bool):
                raise ReadError("ledger_invalid")
            if controls.get(flag):
                reasons.append(reason)
        if state.get("entry_intents"):
            reasons.append("entry_intent_pending")
        positions = []
        for symbol, payload in sorted(state["positions"].items()):
            if not isinstance(payload, dict) or payload.get("symbol") != symbol or payload.get("side") not in ("long", "short"):
                raise ReadError("ledger_invalid")
            identity(symbol)
            entry_time = utc_text(payload.get("entry_time"))
            position_id = payload.get("identity") or f"{symbol}-{entry_time}"
            verified = payload.get("protection_verified")
            if verified is not None and not isinstance(verified, bool):
                raise ReadError("ledger_invalid")
            pending = payload.get("exit_order_id")
            pending_client = payload.get("exit_client_order_id")
            for order_id in (pending, pending_client):
                if order_id is not None:
                    identity(order_id)
            if pending or pending_client:
                reasons.append("exit_pending")
            if verified is False:
                reasons.append("protection_unverified")
            position = {
                "position_id": identity(position_id), "symbol": symbol, "strategy": identity(payload.get("strategy")),
                "side": payload["side"], "quantity": decimal_text(payload.get("qty"), positive=True),
                "entry_price": decimal_text(payload.get("entry_price"), positive=True),
                "hard_stop": decimal_text(payload.get("hard_stop"), positive=True),
                "trailing_stop": decimal_text(payload.get("trailing_stop"), positive=True),
                "broker_stop": decimal_text(payload.get("server_stop"), positive=True),
                "protection": "unverified" if verified is False else "unknown",
                "protection_observed_at": None, "pending_exit_order_id": pending,
                "exit_pending": bool(pending or pending_client),
            }
            if any(position[field] is None for field in ("quantity", "entry_price", "hard_stop")):
                raise ReadError("ledger_invalid")
            positions.append(position)
        reasons = sorted(set(reasons))
        overview = {
            "currency": "USD", "accounting_day": day,
            "recorded_equity": decimal_text(daily.get("last_equity"), positive=True),
            "realized_pnl": None if controls.get("accounting_unresolved") else decimal_text(daily.get("realized")),
            "unrealized_pnl": None, "entries": "blocked" if reasons else "unknown", "block_reasons": reasons,
        }
        return overview, positions