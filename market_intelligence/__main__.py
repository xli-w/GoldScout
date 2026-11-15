"""Explicit local intelligence commands; calendar networking is opt-in."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import threading

from market_intelligence.service import IntelligenceService
from market_intelligence.providers import CALENDAR_SOURCES
from market_intelligence.store import IntelligenceStore, StoreError, encode, initialize, upgrade
from proxy_config import ProxyConfigurationError
from market_intelligence.runtime import load_profiles


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("initialize", "upgrade", "status", "once", "run"))
    parser.add_argument("--database", required=True, type=Path, help="Separate intelligence .sqlite3 path")
    parser.add_argument("--allow-network", action="store_true", help="Permit the run service to fetch its allowlisted public calendar")
    parser.add_argument("--collect-news", action="store_true", help="Enable scheduled collection of the fixed official RSS feed (requires network permission and schema 3)")
    parser.add_argument("--allow-ai", action="store_true", help="Permit registered advisory providers; requires news collection")
    parser.add_argument("--ai-profiles", type=Path, help="Operator-owned nonsecret provider profile JSON")
    parser.add_argument("--calendar-source", action="append", choices=tuple(CALENDAR_SOURCES),
                        help="Select a registered calendar source; repeat to select multiple (default: BLS and BEA)")
    args = parser.parse_args(argv)
    if args.allow_network and args.action != "run":
        parser.error("--allow-network is valid only with run")
    if args.collect_news and (args.action != "run" or not args.allow_network):
        parser.error("--collect-news requires run --allow-network")
    if args.allow_ai and (not args.collect_news or args.ai_profiles is None):
        parser.error("--allow-ai requires --collect-news and --ai-profiles")
    if args.ai_profiles is not None and not args.allow_ai:
        parser.error("--ai-profiles requires --allow-ai")
    if args.ai_profiles is not None and args.action != "run":
        parser.error("--ai-profiles is valid only with run --allow-ai")
    try:
        if args.action == "initialize":
            store = initialize(args.database, datetime.now(timezone.utc))
        elif args.action == "upgrade":
            store = upgrade(args.database)
        else:
            store = IntelligenceStore(args.database)
        processed = ()
        if args.action in ("once", "run"):
            with IntelligenceService(store, allow_network=args.allow_network,
                                     calendar_sources=tuple(args.calendar_source) if args.calendar_source else ("bls", "bea"),
                                     collect_news=args.collect_news, allow_ai=args.allow_ai,
                                     ai_providers=load_profiles(args.ai_profiles) if args.ai_profiles else None) as service:
                if args.action == "run":
                    service.run(threading.Event())
                else:
                    processed = service.step()
        observation = store.news_status() if args.action == "status" and store.schema_version >= 3 else None
        print(json.dumps({"database_id": store.database_id, "schema_version": store.schema_version,
                  "settings": json.loads(encode(store.settings())),
                  "last_news_observation": json.loads(encode(observation)) if observation is not None else None,
                          "processed": [json.loads(encode(receipt)) for receipt in processed],
                          "trading_authority": False, "providers_connected": None if args.action == "status" else False,
                          "network_permitted": args.allow_network}, sort_keys=True))
        return 0
    except KeyboardInterrupt:
        return 0
    except ProxyConfigurationError as exc:
        print(str(exc))
        return 1
    except (StoreError, ValueError):
        print("Intelligence command refused: storage, ownership or settings validation failed.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())