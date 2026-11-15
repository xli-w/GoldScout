import argparse
import getpass
from pathlib import Path

from dashboard.assets import FrontendBuildError, build_guidance, verify_frontend


def main(argv=None):
    parser = argparse.ArgumentParser(description="GoldScout local read-only trading dashboard")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--demo", action="store_true", help="Use synthetic data only")
    source.add_argument("--database", type=Path, help="Read an operator-selected existing ledger")
    parser.add_argument("--scenario", default="healthy_recorded", help="Synthetic fixture scenario")
    parser.add_argument("--telemetry", type=Path, help="Optional operator-selected health snapshot")
    parser.add_argument("--api-only", action="store_true", help="Development API without production frontend assets")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--intelligence-database",type=Path,help="Existing separate intelligence database")
    parser.add_argument("--intelligence-demo",action="store_true",help="Synthetic temporary intelligence with demo-only controls")
    parser.add_argument("--analysis-controls",action="store_true",help="Enable authenticated local analysis settings only")
    parser.add_argument("--analysis-profiles",nargs="*",default=[],help="Registered nonsecret AI profile names")
    args = parser.parse_args(argv)
    if args.intelligence_demo and (not args.demo or args.intelligence_database):
        parser.error("--intelligence-demo requires --demo without an intelligence database")
    if args.analysis_controls and args.intelligence_database is None:
        parser.error("--analysis-controls requires an existing intelligence database")
    if not 1024 <= args.port <= 65535:
        parser.error("Port must be between 1024 and 65535")
    if args.demo and args.telemetry is not None:
        parser.error("--telemetry is only valid with --database")
    if args.database is not None and args.scenario != "healthy_recorded":
        parser.error("--scenario is only valid with --demo")
    static_directory = None
    if not args.api_only:
        try:
            static_directory = verify_frontend()
        except FrontendBuildError as exc:
            parser.error(f"{exc}. {build_guidance()}")
    try:
        import uvicorn
        from dashboard.app import create_app
    except ModuleNotFoundError:
        parser.error("Dashboard dependencies missing. Install trader-local-dashboard with its dependencies; checkout users can install requirements-dashboard.txt.")
    try:
        app = create_app(database=args.database, scenario=args.scenario, telemetry_path=args.telemetry,
                         static_directory=static_directory, serve_frontend=not args.api_only,
                         intelligence_database=args.intelligence_database,intelligence_demo=args.intelligence_demo,
                         analysis_password=getpass.getpass("Local analysis-control password: ") if args.analysis_controls else None,
                         analysis_profiles=tuple(args.analysis_profiles))
    except FrontendBuildError as exc:
        parser.error(f"{exc}. {build_guidance()}")
    except ValueError:
        parser.error("Unknown synthetic scenario")
    print(f"GoldScout read-only dashboard ({'synthetic demo' if args.demo else 'recorded data'}): http://127.0.0.1:{args.port}/")
    uvicorn.run(app, host="127.0.0.1", port=args.port, proxy_headers=False, access_log=False)


if __name__ == "__main__":
    main()