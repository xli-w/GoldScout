"""Import, validate, or audit a user-supplied campaign dataset without network access."""

import argparse
import sys
from pathlib import Path

from research_data.dataset import (
    CampaignDataset,
    CampaignDatasetError,
    _json_dumps,
    import_campaign_dataset,
)


def _parser():
    parser = argparse.ArgumentParser(
        prog="python -m research_data",
        description="Offline import/validation for daily/weekly GBP campaign data.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    importer = subparsers.add_parser("import", help="validate and snapshot a local JSON manifest and CSV files")
    importer.add_argument("manifest", type=Path)
    importer.add_argument("--dataset-dir", type=Path, default=Path("campaign_datasets"))
    validator = subparsers.add_parser("validate", help="reopen and verify an immutable campaign dataset")
    validator.add_argument("dataset_id")
    validator.add_argument("--dataset-dir", type=Path, default=Path("campaign_datasets"))
    readiness = subparsers.add_parser("readiness", help="audit scoped GBP daily data readiness without modifying it")
    readiness.add_argument("dataset_id")
    readiness.add_argument("--dataset-dir", type=Path, default=Path("campaign_datasets"))
    readiness.add_argument("--profile", type=Path, required=True)
    fx_readiness = subparsers.add_parser("fx-readiness", help="audit FX availability at explicitly supplied clocks")
    fx_readiness.add_argument("dataset_id")
    fx_readiness.add_argument("--dataset-dir", type=Path, default=Path("campaign_datasets"))
    fx_readiness.add_argument("--profile", type=Path, required=True)
    fx_view = subparsers.add_parser("fx-view", help="show complete FX observations at explicitly supplied clocks")
    fx_view.add_argument("dataset_id")
    fx_view.add_argument("--dataset-dir", type=Path, default=Path("campaign_datasets"))
    fx_view.add_argument("--profile", type=Path, required=True)
    normalize = subparsers.add_parser("normalize", help="show a complete, bounded offline GBP daily price view")
    normalize.add_argument("dataset_id")
    normalize.add_argument("product_id")
    normalize.add_argument("--dataset-dir", type=Path, default=Path("campaign_datasets"))
    normalize.add_argument("--profile", type=Path, required=True)
    normalize.add_argument("--as-of", required=True, help="declared availability cutoff, with explicit UTC offset")
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        if args.command == "fx-view":
            from research_data.readiness import fx_observation_view

            view = fx_observation_view(args.dataset_dir, args.dataset_id, args.profile)
            print(_json_dumps(view, sort_keys=True, separators=(",", ":")))
            if view["readiness_status"] != "pass":
                print("FX observation view has unresolved readiness; it does not authorize performance or trading.",
                      file=sys.stderr)
                return 3
            return 0
        if args.command == "fx-readiness":
            from research_data.readiness import audit_fx_readiness

            report = audit_fx_readiness(args.dataset_dir, args.dataset_id, args.profile)
            print(_json_dumps(report, sort_keys=True, separators=(",", ":")))
            if report["status"] != "pass":
                print(f"FX readiness {report['status']}; inspect coverage and unresolved campaign gates.", file=sys.stderr)
                return 3
            return 0
        if args.command == "normalize":
            from research_data.readiness import normalized_product_view

            view = normalized_product_view(
                args.dataset_dir, args.dataset_id, args.profile, args.product_id, args.as_of,
            )
            print(_json_dumps(view, sort_keys=True, separators=(",", ":")))
            if view["readiness_status"] != "pass":
                print("GBP normalized view has unresolved readiness; it does not authorize performance or trading.",
                      file=sys.stderr)
                return 3
            return 0
        if args.command == "readiness":
            from research_data.readiness import audit_readiness

            report = audit_readiness(args.dataset_dir, args.dataset_id, args.profile)
            print(_json_dumps(report, sort_keys=True, indent=2))
            if report["status"] != "pass":
                print(f"GBP data readiness {report['status']}; inspect checks and unresolved campaign gates.", file=sys.stderr)
                return 3
            return 0
        if args.command == "import":
            dataset_id = import_campaign_dataset(args.manifest, args.dataset_dir)
            dataset = CampaignDataset.open(args.dataset_dir, dataset_id)
            print(f"Campaign dataset imported and validated: {dataset_id}")
        else:
            dataset = CampaignDataset.open(args.dataset_dir, args.dataset_id)
            print(f"Campaign dataset valid: {dataset.dataset_id}")
        print(
            f"Products: {len(dataset.manifest['series']['products'])}; "
            f"FX series: {len(dataset.manifest['series']['fx'])}; "
            f"cash-rate series: {len(dataset.manifest['series']['cash_rates'])}; "
            f"corporate actions: {len(dataset.manifest['source_manifest']['corporate_actions'])}"
        )
        print("Coverage is timestamp-envelope only; calendar completeness and campaign feasibility remain unresolved.")
        return 0
    except (CampaignDatasetError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
