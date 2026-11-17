import argparse
from pathlib import Path

from dashboard.assets import FrontendBuildError, build_guidance, verify_frontend


def main(argv=None):
    parser = argparse.ArgumentParser(description="GoldScout local dashboard for read-only accounts and investment records")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--demo", action="store_true", help="Use synthetic data only")
    source.add_argument("--database", type=Path, help="Read an operator-selected existing ledger")
    source.add_argument("--investment-database", type=Path, help="Read an explicitly initialised investment store")
    source.add_argument("--workspace", type=Path,
                        help="Local investment workspace; defaults to local-investment in a source checkout")
    parser.add_argument("--scenario", default="healthy_recorded", help="Synthetic fixture scenario")
    parser.add_argument("--telemetry", type=Path, help="Optional operator-selected health snapshot")
    parser.add_argument("--api-only", action="store_true", help="Development API without production frontend assets")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--intelligence-database",type=Path,help="Existing separate intelligence database")
    parser.add_argument("--intelligence-demo",action="store_true",help="Synthetic temporary intelligence with demo-only controls")
    parser.add_argument("--analysis-controls",action="store_true",help="Enable local analysis settings only")
    parser.add_argument("--analysis-profiles",nargs="*",default=[],help="Registered nonsecret AI profile names")
    parser.add_argument("--investment-recording", action="store_true",
                        help="Enable manual edits to the selected v2 investment store")
    parser.add_argument("--awareness-profile", type=Path,
                        help="Operator-owned local exposure/reminder JSON; requires both investment and intelligence stores")
    parser.add_argument("--monitoring-profile", type=Path, help="Local nonsecret monitoring JSON for the selected investment store")
    parser.add_argument("--monitoring-cache", type=Path, help="Existing separately initialized observation cache; never collects")
    parser.add_argument("--monitoring-reviews", type=Path,
                        help="Existing explicitly initialized local monitoring review store")
    parser.add_argument("--monitoring-workflow", action="store_true",
                        help="Enable local review actions and observation staging")
    args = parser.parse_args(argv)
    setup_options = {}
    setup_state = None
    if not args.demo and args.database is None and args.investment_database is None:
        from dashboard.workspace import resolve_workspace
        from investment.bootstrap import workspace_status
        try:
            args.workspace = resolve_workspace(args.workspace)
            status = workspace_status(args.workspace)
        except (ValueError, OSError) as exc:
            parser.error(str(exc))
        setup_state = status["state"]
        args.investment_database = args.workspace / "investment.sqlite3"
        if setup_state != "ready" and (
                args.investment_recording or args.monitoring_profile or args.awareness_profile
                or args.monitoring_cache or args.monitoring_reviews or args.monitoring_workflow):
            parser.error("Complete workspace setup first; recording, monitoring and awareness are separate opt-ins.")
        setup_options = {
            "setup_workspace": args.workspace,
            "setup_enabled": setup_state in ("empty", "recoverable"),
        }
    if args.intelligence_demo and (not args.demo or args.intelligence_database):
        parser.error("--intelligence-demo requires --demo without an intelligence database")
    if args.analysis_controls and args.intelligence_database is None:
        parser.error("--analysis-controls requires an existing intelligence database")
    if args.investment_recording and args.investment_database is None:
        parser.error("--investment-recording requires --investment-database")
    if args.awareness_profile is not None and (args.investment_database is None or args.intelligence_database is None):
        parser.error("--awareness-profile requires --investment-database and --intelligence-database")
    if args.monitoring_profile is not None and args.investment_database is None:
        parser.error("--monitoring-profile requires --investment-database")
    if args.monitoring_cache is not None and args.monitoring_profile is None:
        parser.error("--monitoring-cache requires --monitoring-profile")
    if args.monitoring_reviews is not None and (
            args.investment_database is None or args.monitoring_profile is None):
        parser.error("--monitoring-reviews requires --investment-database and --monitoring-profile")
    if args.monitoring_workflow and args.monitoring_reviews is None:
        parser.error("--monitoring-workflow requires an existing --monitoring-reviews store")
    if not 1024 <= args.port <= 65535:
        parser.error("Port must be between 1024 and 65535")
    if args.database is None and args.telemetry is not None:
        parser.error("--telemetry is only valid with --database")
    if not args.demo and args.scenario != "healthy_recorded":
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
                         analysis_controls_enabled=args.analysis_controls,
                         analysis_profiles=tuple(args.analysis_profiles), investment_database=args.investment_database,
                         investment_recording_enabled=args.investment_recording,
                         awareness_profile=args.awareness_profile,
                         monitoring_profile=args.monitoring_profile, monitoring_cache=args.monitoring_cache,
                         monitoring_reviews=args.monitoring_reviews,
                         monitoring_workflow_enabled=args.monitoring_workflow, **setup_options)
    except FrontendBuildError as exc:
        parser.error(f"{exc}. {build_guidance()}")
    except ValueError as exc:
        parser.error(str(exc))
    landing = ("setup" if setup_state is not None and setup_state != "ready"
               else "investment" if args.investment_database is not None else "")
    mode = ("investment setup" if landing == "setup"
            else "synthetic demo" if args.demo else "recorded data")
    if args.investment_recording:
        mode += " with opt-in investment recording"
    if args.monitoring_workflow:
        mode += " with opt-in monitoring workflow actions"
    print(f"GoldScout local dashboard ({mode}): http://127.0.0.1:{args.port}/{landing}")
    uvicorn.run(app, host="127.0.0.1", port=args.port, proxy_headers=False, access_log=False)


if __name__ == "__main__":
    main()