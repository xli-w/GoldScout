"""Bounded collection projections within the validated read-only transaction."""

import base64
import json
from datetime import date
from decimal import Decimal

from dashboard.reader import LedgerReader, ReadError, decode, decimal_text, identity, utc_text

MAX_OFFSET = (1 << 63) - 1


def valid_offset(value):
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= MAX_OFFSET


def parse_cursor(cursor, resource, limit, filters, demo=False):
    try:
        if not isinstance(cursor, str) or len(cursor) > 2048:
            raise ValueError("cursor")
        token = json.loads(base64.b64decode(cursor.encode("ascii"), altchars=b"-_", validate=True))
        if (not isinstance(token, dict) or token.get("resource") != resource
                or token.get("filters") != filters or not valid_offset(token.get("limit")) or token.get("limit") != limit
                or token.get("demo", False) is not demo or not isinstance(token.get("last"), list)
                or len(token["last"]) != 3 or not all(valid_offset(value) for value in token["last"])):
            raise ValueError("cursor")
        return token
    except (ValueError, TypeError, UnicodeError) as exc:
        raise ReadError("invalid_request") from exc


def accounting_day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ReadError("ledger_invalid")
    return value


def optional_number(value, nonnegative=False):
    if value in (None, ""):
        return None
    result = decimal_text(value)
    if nonnegative and Decimal(result) < 0:
        raise ReadError("ledger_invalid")
    if nonnegative and Decimal(result).is_zero():
        result = format(Decimal(result).copy_abs(), "f")
    return result


def optional_flag(value):
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in ("true", "false"):
        return value.lower() == "true"
    raise ReadError("ledger_invalid")


class HistoryReader(LedgerReader):
    def page(self, resource, limit=100, cursor=None, after=0, start=None, end=None, symbol=None, now=None):
        if (not valid_offset(limit) or not 1 <= limit <= 1000 or not valid_offset(after)
            or resource not in ("events", "orders", "performance")):
            raise ReadError("invalid_request")
        try:
            for value in (start, end):
                if value is not None:
                    accounting_day(value)
            if start and end and start > end:
                raise ValueError("dates")
            if symbol is not None:
                identity(symbol)
        except Exception as exc:
            raise ReadError("invalid_request") from exc
        token = parse_cursor(cursor, resource, limit, [start, end, symbol]) if cursor else None

        def collect(connection, meta):
            head = connection.execute("SELECT COALESCE(MAX(sequence),0) FROM events").fetchone()[0]
            meta["journal_head"] = head
            binding = [meta["database_id"], meta["ledger_revision"], head]
            if token and token.get("binding") != binding:
                raise ReadError("snapshot_changed")
            last = token["last"] if token else [after if resource == "events" else 0, 0, 0]
            next_last = list(last)
            more = False

            def page_rows(table, index, project, where="", parameters=()):
                nonlocal more
                rows = connection.execute(
                    f"SELECT rowid, payload{', id' if table == 'exits' else ''} FROM {table} WHERE rowid>? {where} ORDER BY rowid LIMIT ?",
                    (last[index], *parameters, limit + 1)).fetchall()
                more = more or len(rows) > limit
                if rows[:limit]:
                    next_last[index] = rows[min(len(rows), limit) - 1][0]
                items = []
                for row in rows[:limit]:
                    payload = decode(row[1])
                    if table == "exits":
                        payload["_ledger_id"] = row[2]
                    items.append(project(payload))
                return items

            if resource == "events":
                rows = connection.execute(
                    "SELECT sequence,kind,observed_at,payload FROM events WHERE sequence>? AND sequence<=? ORDER BY sequence LIMIT ?",
                    (last[0], head, limit + 1)).fetchall()
                items = []
                for sequence, kind, observed, payload in rows[:limit]:
                    raw = decode(payload)
                    name = raw.get("symbol") if isinstance(raw, dict) else None
                    items.append({"sequence": sequence, "kind": identity(kind), "observed_at": utc_text(observed),
                                  "symbol": identity(name) if name is not None else None,
                                  "summary": kind.replace("_", " ").capitalize()})
                more = len(rows) > limit
                next_last[0] = rows[min(len(rows), limit) - 1][0] if rows else last[0]
                data = {"items": items, "next_after": next_last[0], "has_more": more}
            elif resource == "orders":
                data = {"items": page_rows("orders", 0, self.order)}
            else:
                conditions, parameters = [], []
                if start:
                    conditions.append("day>=?")
                    parameters.append(start)
                if end:
                    conditions.append("day<=?")
                    parameters.append(end)
                where = " AND " + " AND ".join(conditions) if conditions else ""
                daily = page_rows("daily_accounts", 0, lambda row: self.daily(row, connection), where, parameters)
                exit_where, exit_params = self.exit_filter(start, end, symbol, "payload")
                exits = page_rows("exits", 1, self.exit, exit_where, exit_params)
                corrections = page_rows("corrections", 2, lambda row: self.correction(row["input"]),
                    " AND execution_id IN (SELECT id FROM exits WHERE 1=1 " + exit_where + ")", exit_params)
                data = {"currency": "USD", "daily": daily, "exits": exits, "corrections": corrections}
            encoded = None
            if more:
                encoded = base64.urlsafe_b64encode(json.dumps({"resource": resource, "binding": binding,
                    "filters": [start, end, symbol], "limit": limit, "last": next_last}, separators=(",", ":")).encode()).decode()
            data["next_cursor"] = encoded
            return data

        snapshot = self.snapshot(now, collection=collect)
        return {"api_version": "1", "resource": resource, "meta": snapshot["meta"], "data": snapshot["collection"]}

    @staticmethod
    def exit_filter(start, end, symbol, column):
        conditions, parameters = [], []
        if start:
            conditions.append(f"json_extract({column}, '$.accounting_date')>=?")
            parameters.append(start)
        if end:
            conditions.append(f"json_extract({column}, '$.accounting_date')<=?")
            parameters.append(end)
        if symbol:
            conditions.append(f"json_extract({column}, '$.instrument')=?")
            parameters.append(symbol)
        return (" AND " + " AND ".join(conditions) if conditions else ""), parameters

    @staticmethod
    def order(row):
        observation = row.get("observation") or {}
        raw = dict(row, **{key: value for key, value in observation.items() if value is not None})
        requested = optional_number(raw.get("qty"), nonnegative=True)
        filled = optional_number(raw.get("filled_qty"), nonnegative=True)
        if requested is not None and (Decimal(requested) <= 0 or filled is not None and Decimal(filled) > Decimal(requested)):
            raise ReadError("ledger_invalid")
        return {"client_id": identity(raw["client_order_id"]), "broker_order_id": raw.get("id"),
                "position_id": None, "symbol": identity(raw["symbol"]), "side": raw["side"],
                "status": identity(raw.get("status") or "unknown"),
                "requested_quantity": requested,
                "cumulative_filled_quantity": filled, "observed_at": None}

    @staticmethod
    def exit(row):
        accounting = row.get("accounting", {})
        reported_price = optional_number(accounting.get("exit_price", row.get("exit_price")), nonnegative=True)
        flag = optional_flag(row.get("price_verified"))
        verified = reported_price is not None if flag is None else flag
        if verified and (reported_price is None or Decimal(reported_price) <= 0):
            raise ReadError("ledger_invalid")
        price = accounting.get("exit_price", row.get("exit_price")) if verified else None
        execution_id = identity(row.get("execution_id") or row["_ledger_id"])
        amount = optional_number(accounting.get("qty", row.get("position_size")), nonnegative=True)
        if amount is not None and Decimal(amount) <= 0:
            raise ReadError("ledger_invalid")
        fill_at = None
        if row.get("timestamp"):
            try:
                fill_at = utc_text(row["timestamp"])
            except (ReadError, ValueError):
                if row.get("execution_id"):
                    raise
        return {"execution_id": execution_id, "position_id": execution_id.rsplit(":", 1)[0] if row.get("execution_id") and ":" in execution_id else None,
                "symbol": identity(row["instrument"]), "quantity": amount,
                "fill_at": fill_at,
                "accounting_day": accounting_day(row["accounting_date"]) if row.get("accounting_date") else None, "exit_price": decimal_text(price),
                "price_verified": verified, "gross_pnl": optional_number(row.get("gross_profit_loss")) if verified else None,
                "fees": optional_number(accounting.get("fees", row.get("fees")), nonnegative=True), "fees_estimated": optional_flag(row.get("fees_estimated")),
                "net_pnl": optional_number(accounting.get("profit_loss", row.get("profit_loss"))) if verified else None}

    @staticmethod
    def correction(row):
        result = {key: row[key] for key in ("correction_id", "execution_id", "activity_id", "exit_price", "fees", "reason")}
        result["exit_price"] = decimal_text(row["exit_price"], positive=True)
        result["fees"] = optional_number(row["fees"], nonnegative=True)
        return result

    @staticmethod
    def daily(row, connection):
        day = accounting_day(row["date"])
        corrected = connection.execute("SELECT 1 FROM corrections JOIN exits ON exits.id=corrections.execution_id WHERE json_extract(exits.payload,'$.accounting_date')=? LIMIT 1", (day,)).fetchone()
        unresolved = connection.execute("SELECT 1 FROM exits WHERE json_extract(payload,'$.accounting_date')=? AND (json_extract(payload,'$.price_verified')=0 OR lower(json_extract(payload,'$.price_verified'))='false' OR ((json_extract(payload,'$.price_verified') IS NULL OR json_extract(payload,'$.price_verified')='') AND (json_extract(payload,'$.exit_price') IS NULL OR json_extract(payload,'$.exit_price')=''))) AND NOT EXISTS (SELECT 1 FROM corrections WHERE corrections.execution_id=exits.id) LIMIT 1", (day,)).fetchone()
        return {"day": day, "recorded_equity": optional_number(row.get("ending_equity", row.get("closing_equity"))),
            "realized_pnl": None if unresolved else optional_number(row.get("realized_pnl")),
            "includes_corrections": bool(corrected), "accounting_complete": not bool(unresolved)}


def demo_page(data, resource, limit, cursor, filters, after=0):
    if not valid_offset(limit) or not 1 <= limit <= 1000 or not valid_offset(after):
        raise ReadError("invalid_request")
    last = [0, 0, 0]
    if cursor:
        last = parse_cursor(cursor, resource, limit, filters, demo=True)["last"]
    names = ["daily", "exits", "corrections"] if resource == "performance" else ["items"]
    more = False
    for index, name in enumerate(names):
        rows = data[name]
        if resource == "events":
            rows = [row for row in rows if row["sequence"] > after]
        data[name] = rows[last[index]:last[index] + limit]
        last[index] += len(data[name])
        more = more or last[index] < len(rows)
    data["next_cursor"] = base64.urlsafe_b64encode(json.dumps({"demo": True, "resource": resource, "limit": limit, "filters": filters, "last": last}).encode()).decode() if more else None
    if resource == "events":
        data["has_more"] = more
        data["next_after"] = data["items"][-1]["sequence"] if data["items"] else after
    return data