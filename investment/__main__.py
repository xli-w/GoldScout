"""Investment records and local monitoring. Only explicit approved monitor-collect may make provider reads."""

import argparse
import json
import sys
import threading

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
    init = commands.add_parser("init", help="create a new explicitly bound investment store")
    init.add_argument("--profile", required=True)
    init.add_argument("--database", required=True)
    events = commands.add_parser("import", help="atomically import explicit local event JSON array")
    events.add_argument("--events", required=True)
    events.add_argument("--database", required=True)
    for name in ("backup", "restore"):
        command = commands.add_parser(name, help="copy a validated investment store to a new path")
        command.add_argument("--database", required=True, help="existing source store")
        command.add_argument("--destination", required=True, help="new destination; never overwrites")
    for name in ("report", "propose", "export"):
        command = commands.add_parser(name, help="read-only stdout JSON; never executes")
        command.add_argument("--database", required=True)
    status = commands.add_parser("review-status", help="read-only review clocks and valuation status")
    status.add_argument("--database", required=True)
    awareness = commands.add_parser("awareness", help="local advisory alerts, briefs and reminder JSON; no collection")
    awareness.add_argument("--database", required=True)
    awareness.add_argument("--intelligence-database", required=True)
    awareness.add_argument("--awareness-profile", required=True)
    awareness.add_argument("--schema-version", type=int, choices=(1, 2), default=1,
                           help="report version 2 includes operator thesis and calendar notes")
    reconcile = commands.add_parser("reconcile-preview", help="preview local staged observations; never writes")
    reconcile.add_argument("--database", required=True)
    reconcile.add_argument("--observations", required=True)
    reconcile.add_argument("--max-age-seconds", type=int, required=True)
    template = commands.add_parser("monitor-template", help="print an unconfigured local monitoring profile; never collects")
    template.add_argument("--database", required=True)
    template.add_argument("--max-age-seconds", type=int, required=True)
    template.add_argument("--review-interval-days", type=int, required=True)
    for name in ("monitor", "monitor-init", "monitor-stage", "monitor-purge", "monitor-collect"):
        command = commands.add_parser(name, help="explicit monitoring; only monitor-collect can use network")
        command.add_argument("--database", required=True)
        command.add_argument("--monitoring-profile", required=True)
        command.add_argument("--cache", required=name != "monitor")
        if name == "monitor-init":
            command.add_argument("--retention-seconds", type=int, required=True)
        if name == "monitor-stage":
            command.add_argument("--attempt", required=True, help="operator-normalized attempt JSON, not provider exports")
        if name == "monitor-collect":
            command.add_argument("--account-id", required=True)
            command.add_argument("--approval", required=True, help="nonsecret time-bounded operator attestation")
            command.add_argument("--allow-network", action="store_true")
            command.add_argument("--watch", action="store_true", help="explicit daily loop; stops on errors or approval expiry")
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
        elif args.command in ("backup", "restore"):
            from .lifecycle import copy_verified
            value = copy_verified(args.database, args.destination, requested_now)
        elif args.command == "report":
            value = snapshot(args.database, now)
        elif args.command == "review-status":
            value = review_status(args.database, requested_now)
        elif args.command == "awareness":
            from .awareness import awareness_snapshot
            value = awareness_snapshot(args.database, args.intelligence_database,
                                       args.awareness_profile, requested_now, args.schema_version)
        elif args.command == "reconcile-preview":
            from .reconciliation import preview_observations
            value = preview_observations(args.database, load_json(args.observations),
                                         args.max_age_seconds, requested_now)
        elif args.command == "monitor-template":
            from .store import canonical, digest, read_with_clock
            from .core import iso
            ledger, observed = read_with_clock(args.database, requested_now)
            if (ledger.spec["schema_version"] != 2 or not ledger.opened
                    or not 1 <= args.max_age_seconds <= 365 * 86400
                    or not 1 <= args.review_interval_days <= 3660):
                raise InvestmentError("Template requires an opened v2 journal and bounded explicit age/review intervals")
            value = {
                "schema_version": 1, "portfolio_id": ledger.spec["portfolio_id"],
                "investment_profile_sha256": digest(canonical(ledger.spec)),
                "observed_at": iso(observed), "concentration_threshold_pct": None,
                "accounts": [
                    {"account_id": a["id"], "provider": "manual", "wrapper": "unknown",
                     "denominator": None, "max_age_seconds": args.max_age_seconds,
                     "last_reviewed_at": None, "review_interval_days": args.review_interval_days,
                     "evidence": "Unconfigured; operator must verify wrapper, provider and denominator locally"}
                    for a in ledger.spec["accounts"]],
            }
        elif args.command.startswith("monitor"):
            from .monitoring import monitoring_snapshot, validate_profile
            from .monitoring_store import initialize_cache, append_attempt, purge_expired
            from .store import read_with_clock
            ledger, observed = read_with_clock(args.database, requested_now)
            monitoring = load_json(args.monitoring_profile)
            validate_profile(monitoring, ledger, observed)
            if args.command == "monitor-init":
                initialize_cache(args.cache, monitoring, args.retention_seconds)
                value = {"initialized": True, "accounting_writes": False}
            elif args.command == "monitor-stage":
                value = {"revision": append_attempt(args.cache, monitoring, load_json(args.attempt), observed),
                         "accounting_writes": False}
            elif args.command == "monitor-purge":
                value = purge_expired(args.cache, monitoring, observed)
            elif args.command == "monitor-collect":
                from .monitoring_runtime import collect_once
                while True:
                    value = collect_once(args.database, monitoring, args.cache, args.account_id,
                                         load_json(args.approval), allow_network=args.allow_network,
                                         now=requested_now)
                    if not args.watch:
                        break
                    print(json.dumps(value, sort_keys=True, allow_nan=False), file=stdout, flush=True)
                    threading.Event().wait(86400)
            else:
                value = monitoring_snapshot(args.database, args.monitoring_profile, args.cache, observed)
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
    except KeyboardInterrupt:
        print("Command interrupted. Preserve any earlier commit receipts; monitoring never changes accounts.", file=stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
