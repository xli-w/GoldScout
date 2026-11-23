"""Explicit local intelligence commands; calendar networking is opt-in."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import threading

from market_intelligence.service import IntelligenceService
from market_intelligence.providers import CALENDAR_SOURCES
from market_intelligence.contracts import service_status_state
from market_intelligence.store import IntelligenceStore, StoreError, connection, encode, initialize, upgrade
from proxy_config import ProxyConfigurationError
from market_intelligence.runtime import load_profiles
from market_intelligence.briefing import FoundationService


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("initialize", "upgrade", "status", "once", "run", "check"))
    parser.add_argument("--database", type=Path, help="Separate intelligence .sqlite3 path (optional with --managed-config)")
    parser.add_argument("--managed-config", type=Path,
                        help="Dashboard settings.json; use its sibling store and exact saved source/provider selection")
    parser.add_argument("--allow-network", action="store_true", help="Permit explicitly selected public provider requests")
    parser.add_argument("--collect-news", action="store_true", help="Enable the fixed official RSS capability (requires network permission and schema 3)")
    parser.add_argument("--allow-ai", action="store_true", help="Permit registered advisory providers; requires news collection")
    parser.add_argument("--ai-profiles", type=Path, help="Operator-owned nonsecret provider profile JSON")
    parser.add_argument("--calendar-source", action="append", choices=tuple(CALENDAR_SOURCES),
                        help="Select a registered calendar source; repeat to select multiple (default: BLS and BEA)")
    parser.add_argument("--check-source", action="append", choices=(*CALENDAR_SOURCES, "fed_monetary", "analysis"),
                        help="Select exactly one source for a supervised single-attempt check")
    args = parser.parse_args(argv)
    if args.managed_config is not None:
        if args.action not in ("run", "status", "once"):
            parser.error("--managed-config is valid only with run, status or once")
        if args.managed_config.name != "settings.json":
            parser.error("--managed-config must select the dashboard settings.json")
        if (args.calendar_source is not None or args.ai_profiles is not None
                or args.collect_news or args.allow_ai or args.check_source is not None):
            parser.error("--managed-config cannot be combined with source, profile, news, AI or check overrides")
        sibling = args.managed_config.absolute().parent / "intelligence.sqlite3"
        if args.database is not None and args.database.absolute() != sibling:
            parser.error("--database must be the managed settings.json sibling intelligence.sqlite3")
        args.database = sibling
        if args.action == "run" and not args.allow_network:
            parser.error("managed run requires --allow-network; saved settings control actual collection")
    elif args.database is None:
        parser.error("--database is required without --managed-config")
    if args.allow_network and args.action not in ("run", "check"):
        parser.error("--allow-network is valid only with run or check")
    if args.collect_news and (args.action not in ("run", "check") or not args.allow_network):
        parser.error("--collect-news requires run or check with --allow-network")
    if args.allow_ai and (not args.collect_news or args.ai_profiles is None):
        parser.error("--allow-ai requires --collect-news and --ai-profiles")
    if args.ai_profiles is not None and not args.allow_ai:
        parser.error("--ai-profiles requires --allow-ai")
    if args.ai_profiles is not None and args.action not in ("run", "check"):
        parser.error("--ai-profiles is valid only with run or check --allow-ai")
    if args.action == "check":
        if (not args.allow_network or args.check_source is None or len(args.check_source) != 1
                or args.calendar_source is not None):
            parser.error("check requires --allow-network and exactly one --check-source")
        check_source = args.check_source[0]
        if check_source in CALENDAR_SOURCES and (args.collect_news or args.allow_ai):
            parser.error("a calendar check does not enable news or AI")
        if check_source == "fed_monetary" and (not args.collect_news or args.allow_ai):
            parser.error("a news check requires --collect-news without --allow-ai")
        if check_source == "analysis" and not args.allow_ai:
            parser.error("an analysis check requires --allow-ai and --collect-news")
    else:
        if args.check_source is not None:
            parser.error("--check-source is valid only with check")
        check_source = None
    try:
        managed_config = None
        configuration_loader = None
        if args.managed_config is not None:
            # This bounded shared loader reads only nonsecret configuration.
            # It imports neither FastAPI nor a selected portfolio workspace.
            from dashboard.intelligence_setup import load_managed_config, queue_managed_configuration
            configuration_loader = lambda: load_managed_config(args.managed_config)
            managed_config = configuration_loader()
        if args.action == "initialize":
            store = initialize(args.database, datetime.now(timezone.utc))
        elif args.action == "upgrade":
            store = upgrade(args.database)
        else:
            store = IntelligenceStore(args.database)
        processed = ()
        check = None
        if managed_config is not None and args.action == "run":
            from market_intelligence.runtime import ProviderProfile
            profile = ProviderProfile(**managed_config["profile"]) if managed_config["profile"] else None
            providers = {profile.name: profile.provider()} if profile else {}
            with FoundationService(
                    store, calendar_sources=tuple(managed_config["calendar_sources"]),
                    content_sources=tuple(managed_config["content_sources"]), providers=providers,
                    configuration_loader=configuration_loader) as service:
                # Settle stale commands under this sole owner without starting
                # provider work, then atomically bind the current saved selection.
                bootstrap = configuration_loader()
                names = (bootstrap["profile"]["name"],) if bootstrap["profile"] else ()
                now = datetime.now(timezone.utc)
                store.apply_pending(now, profiles=names)
                queue_managed_configuration(store, bootstrap, now)
                # The shared service stays alive while disabled, processes
                # controls without collecting, and reloads selection in-place.
                service.run(threading.Event())
        elif managed_config is not None and args.action == "once":
            profile = managed_config["profile"]
            names = (profile["name"],) if profile else ()
            # Existing once semantics are control-only. It grants no network or
            # provider capability, including for an enabled managed selection.
            with IntelligenceService(
                    store, profiles=names, allow_network=False,
                    calendar_sources=tuple(managed_config["calendar_sources"])) as service:
                processed = service.step()
                queue_managed_configuration(store, configuration_loader(), datetime.now(timezone.utc))
                processed += service.step()
        elif args.action in ("once", "run", "check"):
            calendar_sources = (tuple(args.calendar_source) if args.calendar_source else
                                (check_source,) if check_source in CALENDAR_SOURCES else ("bls", "bea"))
            with IntelligenceService(store, allow_network=args.allow_network,
                                     calendar_sources=calendar_sources,
                                     collect_news=args.collect_news, allow_ai=args.allow_ai,
                                     ai_providers=load_profiles(args.ai_profiles) if args.ai_profiles else None) as service:
                if args.action == "run":
                    service.run(threading.Event())
                elif args.action == "check":
                    check, processed = service.check_once(check_source)
                else:
                    processed = service.step()
        with connection(store.path, expected_id=store.database_id) as handle:
            version = handle.execute("PRAGMA user_version").fetchone()[0]
            observation = store._news_status(handle) if args.action in ("status", "check") and version >= 3 else None
            settings, _ = store._settings(handle)
            service_status = store._latest_service_observation(handle) if version >= 5 else None
            status_state = service_status_state(service_status, settings, datetime.now(timezone.utc)) if version >= 5 else "unsupported"
        output = {"database_id": store.database_id, "schema_version": version,
                  "settings": json.loads(encode(settings)),
                  "last_news_observation": json.loads(encode(observation)) if observation is not None else None,
                  "service_status_capable": version >= 5,
                  "service_status_state": status_state,
                  "service_status": json.loads(encode(service_status)) if service_status is not None else None,
                          "processed": [json.loads(encode(receipt)) for receipt in processed],
                          "trading_authority": False, "providers_connected": None if args.action in ("status", "check") else False,
                          "network_permitted": args.allow_network}
        if check is not None:
            output["check"] = check
        if managed_config is not None:
            output["managed_configuration"] = configuration_loader()
            output["foundation"] = store.foundation_snapshot(datetime.now(timezone.utc))
        print(json.dumps(output, sort_keys=True))
        return 0 if check is None or check["status"] == "succeeded" else 1
    except KeyboardInterrupt:
        if args.action == "check":
            print("Intelligence check interrupted; provider or storage outcomes may be uncertain.")
            return 1
        return 0
    except ProxyConfigurationError as exc:
        print(str(exc))
        return 1
    except (StoreError, ValueError, OSError):
        if args.action in ("once", "run", "check"):
            print("Intelligence service stopped: settings or evidence may already be committed. Check recorded state.")
        else:
            print("Intelligence command refused: storage, ownership or settings validation failed.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())