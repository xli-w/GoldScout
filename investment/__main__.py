"""Explicit local CLI: python -m investment. Never invokes a broker."""

import argparse
import json
import sys

from . import InvestmentError
from .core import clock, replay
from .service import demo_events, demo_profile, demo_snapshot, proposal_export, report, snapshot
from .store import import_events, initialize, load_json


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
    args = parser.parse_args(argv)
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
        else:
            value = proposal_export(args.database, now)
        print(json.dumps(value, sort_keys=True, allow_nan=False), file=stdout)
        return 0
    except InvestmentError as exc:
        print(f"InvestmentError: {exc}", file=stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
