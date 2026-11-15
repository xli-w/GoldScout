"""CSV logging: trades.csv (one row per closed trade) and daily_pnl.csv (one row per day)."""
import csv
import logging
from pathlib import Path

import config

log = logging.getLogger(__name__)

TRADE_FIELDS = ["timestamp", "instrument", "direction", "entry_price",
                "exit_price", "profit_loss", "position_size", "gross_profit_loss",
                "fees", "fees_estimated", "execution_id", "reason", "broker_order_id", "entry_time", "price_verified"]
DAILY_FIELDS = ["date", "starting_equity", "ending_equity", "pnl", "pnl_pct",
                "realized_pnl", "trades_closed"]


def log_trade(row: dict, path: Path = None) -> None:
    if path is None and config.LEDGER_FILE.exists():
        from bot.ledger import Ledger
        Ledger(config.LEDGER_FILE).export(config.TRADES_CSV, TRADE_FIELDS)
        return
    path = path or config.TRADES_CSV
    new_file = not path.exists() or path.stat().st_size == 0
    if not new_file:
        rows = None
        with open(path, newline="") as existing:
            reader = csv.DictReader(existing)
            old_fields = reader.fieldnames
            if old_fields != TRADE_FIELDS:
                if old_fields is None or not set(old_fields).issubset(TRADE_FIELDS):
                    raise ValueError("Unrecognized trade CSV schema")
                rows = list(reader)
        if rows is not None:
            temporary = path.with_suffix(".tmp")
            with open(temporary, "w", newline="") as migrated:
                writer = csv.DictWriter(migrated, fieldnames=TRADE_FIELDS)
                writer.writeheader()
                writer.writerows(rows)
            temporary.replace(path)
    with open(path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=TRADE_FIELDS)
        if new_file:
            writer.writeheader()
        writer.writerow({key: row.get(key, "") for key in TRADE_FIELDS})


def upsert_daily_pnl(row: dict, path: Path = None) -> None:
    """Insert or replace the row for row['date'] (file is tiny: one row per day)."""
    path = path or config.DAILY_PNL_CSV
    rows = []
    if path.exists():
        with open(path, newline="") as f:
            rows = [r for r in csv.DictReader(f) if r["date"] != row["date"]]
    rows.append({k: row[k] for k in DAILY_FIELDS})
    rows.sort(key=lambda r: r["date"])
    temporary = path.with_suffix(".tmp")
    with open(temporary, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=DAILY_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)
