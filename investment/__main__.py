"""Explicit local CLI: python -m investment. Never invokes a broker."""

import argparse
import json
import sys

from . import InvestmentError
from .core import clock, replay
from .service import (demo_events, demo_profile, demo_snapshot, proposal_export, report,
                      review_status, snapshot)
from .store import acknowledge_review, import_events, initialize, load_json


def main(argv=None, stdout=None, stderr=None, now=None):
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="pure synthetic GBP1000 report; no persistence")
    demo.add_argument("--contribution", action="store_true", help="demonstrate an extra GBP100")
    init = commands.add_parser("init", help="create new explicitly bound v1 investment store")
    init.add_argument("--profile", required=True)
    init.add_argument("--database", required=True)
    events = commands.add_parser("import", help="atomically import explicit local event JSON array")
    events.add_argument("--events", required=True)
    events.add_argument("--database", required=True)
    for name in ("report", "propose", "export"):
        command = commands.add_parser(name, help="read-only stdout JSON; never executes")
        command.add_argument("--database", required=True)
    status = commands.add_parser("review-status", help="read-only review clocks and valuation status")
    status.add_argument("--database", required=True)
    acknowledge = commands.add_parser("acknowledge-review", help="record a manual review, not an order approval")
    acknowledge.add_argument("--database", required=True)
    acknowledge.add_argument("--kind", choices=("monthly", "annual"), required=True)
    acknowledge.add_argument("--reviewed-revision", type=int, required=True)
    acknowledge.add_argument("--id", required=True, help="unique, reusable acknowledgement ID")
    acknowledge.add_argument("--evidence", required=True, help="brief manual review evidence")
    args = parser.parse_args(argv)
    requested_now = now
    try:
        now = clock(now)
        if args.command == "demo":
            value = (report(replay(demo_profile(now), demo_events(now, True), now), now, "demo")
                     if args.contribution else demo_snapshot(now))
        elif args.command == "init":
            initialize(args.database, load_json(args.profile), now)
            value = snapshot(args.database, now)
        elif args.command == "import":
            import_events(args.database, load_json(args.events), now)
            value = snapshot(args.database, now)
        elif args.command == "report":
            value = snapshot(args.database, now)
        elif args.command == "review-status":
            value = review_status(args.database, requested_now)
        elif args.command == "acknowledge-review":
            acknowledgement = acknowledge_review(args.database, args.id, args.kind,
                                                  args.reviewed_revision, args.evidence, requested_now)
            try:
                status = review_status(args.database, requested_now)
            except InvestmentError as exc:
                value = {"acknowledgement": acknowledgement, "review_status": None,
                         "review_status_error": str(exc)}
                print(json.dumps(value, sort_keys=True, allow_nan=False), file=stdout)
                print(f"InvestmentError: Review recorded, but status unavailable: {exc}",
                      file=stderr)
                return 2
            value = {"acknowledgement": acknowledgement, "review_status": status}
        else:
            value = proposal_export(args.database, now)
        print(json.dumps(value, sort_keys=True, allow_nan=False), file=stdout)
        return 0
    except InvestmentError as exc:
        print(f"InvestmentError: {exc}", file=stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
