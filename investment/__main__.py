"""Investment records and local monitoring. Only explicit monitor-collect may make provider reads."""

import argparse
import json
import sys
import threading

from . import InvestmentError
from .service import demo_snapshot, snapshot
from .store import import_events, initialize, load_json


def main(argv=None, stdout=None, stderr=None, now=None):
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("demo", help="synthetic opening-evidence report; no persistence")
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
    command = commands.add_parser("report", help="read-only stdout JSON; never executes")
    command.add_argument("--database", required=True)
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
            command.add_argument("--allow-network", action="store_true")
            command.add_argument("--watch", action="store_true", help="explicit daily loop; stops when interrupted or on error")
    review_init = commands.add_parser(
        "review-init", help="explicitly initialize a separate local monitoring review store")
    review_init.add_argument("--database", required=True)
    review_init.add_argument("--monitoring-profile", required=True)
    review_init.add_argument("--reviews", required=True)
    review_init.add_argument("--cache")
    review_sync = commands.add_parser(
        "review-sync", help="sync current recorded monitoring findings into the local review inbox")
    review_sync.add_argument("--database", required=True)
    review_sync.add_argument("--monitoring-profile", required=True)
    review_sync.add_argument("--reviews", required=True)
    review_sync.add_argument("--cache")
    review_list = commands.add_parser("review-inbox", help="read the separate local monitoring review inbox")
    review_list.add_argument("--reviews", required=True)
    review_update = commands.add_parser(
        "review-update", help="append an acknowledgement/defer/review workflow action; never resolves a finding")
    review_update.add_argument("--reviews", required=True)
    review_update.add_argument("--case-id", required=True)
    review_update.add_argument("--request-id", required=True, help="unique idempotency key for this action")
    review_update.add_argument("--expected-revision", type=int, required=True)
    review_update.add_argument("--action", choices=("acknowledged", "deferred", "reviewed"), required=True)
    review_update.add_argument("--note", default="")
    args = parser.parse_args(argv)
    requested_now = now
    committed = None
    reported = False
    try:
        if args.command == "demo":
            value = demo_snapshot(now)
        elif args.command == "init":
            initialize(args.database, load_json(args.profile), now)
            committed = ("initialized", 0)
            value = snapshot(args.database, now)
        elif args.command == "import":
            revision = import_events(args.database, load_json(args.events), now)
            committed = ("import completed", revision)
            value = snapshot(args.database, now)
        elif args.command in ("backup", "restore"):
            from .lifecycle import copy_verified
            value = copy_verified(args.database, args.destination, requested_now)
        elif args.command == "report":
            value = snapshot(args.database, now)
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
            if (not ledger.opened
                    or not 1 <= args.max_age_seconds <= 365 * 86400
                    or not 1 <= args.review_interval_days <= 3660):
                raise InvestmentError("Template requires an opened journal and bounded explicit age/review intervals")
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
        elif args.command == "monitor":
            from .monitoring import monitoring_snapshot
            value = monitoring_snapshot(
                args.database, args.monitoring_profile, args.cache, requested_now)
        elif args.command.startswith("monitor"):
            from .monitoring import validate_profile
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
                from .monitoring_runtime import WatchCredentialRevision, collect_once
                watch_revision = WatchCredentialRevision() if args.watch else None
                while True:
                    value = collect_once(args.database, monitoring, args.cache, args.account_id,
                                         allow_network=args.allow_network, now=requested_now,
                                         watch_credential_revision=watch_revision)
                    if not args.watch:
                        break
                    print(json.dumps(value, sort_keys=True, allow_nan=False), file=stdout, flush=True)
                    threading.Event().wait(86400)
        elif args.command in ("review-init", "review-sync"):
            from pathlib import Path
            from .monitoring import monitoring_snapshot, validate_profile
            from .monitoring_reviews import (
                initialize_review_store, monitoring_cache_binding, sync_review_findings,
            )
            from .store import canonical, digest, read_with_clock

            database_path = Path(args.database).resolve()
            review_path = Path(args.reviews).resolve()
            cache_path = Path(args.cache).resolve() if args.cache else None
            if review_path == database_path or cache_path is not None and review_path == cache_path:
                raise InvestmentError("Review store must be separate from journal and observation cache")
            monitoring_profile = load_json(args.monitoring_profile)
            if args.command == "review-init":
                ledger, observed = read_with_clock(args.database, requested_now)
                validate_profile(monitoring_profile, ledger, observed)
                profile_hash = digest(canonical(monitoring_profile))
                cache_binding = None
                if args.cache:
                    from .monitoring_store import read_cache
                    cache = read_cache(args.cache, monitoring_profile, observed)
                    cache_binding = monitoring_cache_binding(args.cache, cache["instance_id"])
                initialize_review_store(
                    args.reviews, ledger.spec["portfolio_id"], profile_hash, cache_binding)
                value = {"initialized": True, "portfolio_id": ledger.spec["portfolio_id"],
                         "profile_hash": profile_hash, "cache_bound": args.cache is not None,
                         "accounting_writes": False}
            else:
                report_value = monitoring_snapshot(
                    args.database, args.monitoring_profile, args.cache, requested_now)
                cache_binding = None
                if args.cache:
                    from .monitoring_store import read_cache
                    cache = read_cache(args.cache, monitoring_profile,
                                       report_value["generated_at"])
                    cache_binding = monitoring_cache_binding(args.cache, cache["instance_id"])
                    if cache["revision"] != report_value["cache_revision"]:
                        raise InvestmentError("Monitoring cache changed during review sync; refresh and retry")
                value = sync_review_findings(
                    args.reviews, report_value, report_value["portfolio_id"],
                    report_value["profile_hash"], report_value["generated_at"],
                    cache_binding)
                value["accounting_writes"] = False
        elif args.command == "review-inbox":
            from .monitoring_reviews import review_inbox
            value = review_inbox(args.reviews)
        elif args.command == "review-update":
            from .monitoring_reviews import update_review
            value = update_review(args.reviews, args.case_id, args.request_id,
                                  args.expected_revision, args.action, args.note, requested_now)
        print(json.dumps(value, sort_keys=True, allow_nan=False), file=stdout)
        reported = True
        return 0
    except InvestmentError as exc:
        if committed is not None:
            action, revision = committed
            print(f"{type(exc).__name__}: Journal {action} at revision {revision}; "
                  f"report failed: {exc}. Preserve the journal and review it before retrying.",
                  file=stderr)
        else:
            print(f"InvestmentError: {exc}", file=stderr)
        reported = True
        return 2
    except KeyboardInterrupt:
        if committed is not None:
            action, revision = committed
            print(f"Command interrupted after journal {action} at revision {revision}. "
                  "Preserve the journal and review it before retrying.", file=stderr)
        else:
            print("Command interrupted. Preserve any earlier commit receipts; monitoring never changes accounts.", file=stderr)
        reported = True
        return 130
    finally:
        if committed is not None and not reported:
            action, revision = committed
            print(f"Journal {action} at revision {revision}; report failed or did not complete. "
                  "Preserve the journal and review it before retrying.", file=stderr)


if __name__ == "__main__":
    sys.exit(main())
