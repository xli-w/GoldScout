import argparse
import json
from pathlib import Path

import config
from bot.ledger import Ledger, PersistenceError
from bot.process_lock import ProcessLock
from bot.state import validate_state
from bot.trade_logger import DAILY_FIELDS, TRADE_FIELDS


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline portfolio ledger inspection and maintenance")
    parser.add_argument("--database", type=Path, default=config.LEDGER_FILE)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("summary")
    history = commands.add_parser("events")
    history.add_argument("--after", type=int, default=0)
    history.add_argument("--limit", type=int, default=100)
    commands.add_parser("replay")
    backup = commands.add_parser("backup")
    backup.add_argument("destination", type=Path)
    export = commands.add_parser("export")
    export.add_argument("directory", type=Path)
    ingest = commands.add_parser("import-activities")
    ingest.add_argument("source", type=Path)
    correction = commands.add_parser("correct")
    correction.add_argument("source", type=Path)
    resolution = commands.add_parser("resolve-accounting")
    resolution.add_argument("--reason", required=True)
    args = parser.parse_args(argv)
    if not args.database.exists():
        parser.error("Database does not exist; maintenance will not create an empty ledger")
    lock_path = config.LOCK_FILE if args.database.resolve() == config.LEDGER_FILE.resolve() else args.database.with_suffix(".lock")
    lock = ProcessLock(lock_path)
    try:
        ledger = Ledger(args.database)
        state = ledger.load()
        if state is not None:
            validate_state(state)
        if args.command == "summary":
            result = ledger.summary()
        elif args.command == "events":
            result = ledger.history(args.after, args.limit)
        elif args.command == "replay":
            result = ledger.replay()
            if result != state:
                raise PersistenceError("Journal replay disagrees with snapshot")
        elif args.command == "backup":
            ledger.backup(args.destination)
            result = {"backup": str(args.destination)}
        elif args.command == "export":
            args.directory.mkdir(parents=True, exist_ok=True)
            ledger.export(args.directory / "trades.csv", TRADE_FIELDS)
            ledger.export(args.directory / "daily_pnl.csv", DAILY_FIELDS, daily=True)
            ledger.export_corrections(args.directory / "accounting_corrections.csv")
            result = {"export_directory": str(args.directory)}
        elif args.command == "import-activities":
            activities = Ledger.decode(args.source.read_text())
            if not isinstance(activities, list):
                raise ValueError("Activity import must be a JSON array of broker activity objects")
            for activity in activities:
                ledger.ingest_activity(activity)
            result = {"activities_processed": len(activities)}
        elif args.command == "resolve-accounting":
            ledger.resolve_accounting(args.reason)
            result = {"accounting_review_resolved": True, "reason": args.reason}
        else:
            payload = Ledger.decode(args.source.read_text())
            ledger.apply_correction(**payload)
            result = {"correction_id": payload["correction_id"], "applied": True}
        print(json.dumps(result, indent=2, allow_nan=False))
    finally:
        lock.close()


if __name__ == "__main__":
    main()