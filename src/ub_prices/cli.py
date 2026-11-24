"""Command-line interface for ub-prices."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from ub_prices.config import load_config
from ub_prices.matching import ComplexMatcher
from ub_prices.models import DealType
from ub_prices.report import generate_report
from ub_prices.scanner import run_scan
from ub_prices.storage.sqlite_builder import build_sqlite_db


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ub-prices",
        description="Ulaanbaatar residential property price & rental tracker",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to complexes.yaml (defaults to config/complexes.yaml)",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data"),
        help="Directory for canonical data files and SQLite DB (default: data)",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable debug logging"
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # scan
    p_scan = sub.add_parser("scan", help="Run a polite incremental scan")
    p_scan.add_argument("--source", default="unegui", choices=["unegui"])
    p_scan.add_argument("--deal", default="both", choices=["sale", "rent", "both"])
    p_scan.add_argument(
        "--complex",
        dest="complexes",
        action="append",
        help="Limit scan to specific complex slug (can be repeated)",
    )
    p_scan.add_argument("--max-pages", type=int, default=3, help="Max pages per search query")
    p_scan.add_argument(
        "--max-details",
        type=int,
        default=120,
        help="Max new/changed detail pages to fetch per run (0 = unlimited)",
    )
    p_scan.add_argument("--min-delay", type=float, default=3.0, help="Min polite delay (seconds)")
    p_scan.add_argument("--max-delay", type=float, default=6.0, help="Max polite delay (seconds)")
    p_scan.add_argument("--dry-run", action="store_true", help="Scan without writing data files")

    # build-db
    p_db = sub.add_parser("build-db", help="Compile canonical JSONL/CSV files into SQLite DB")
    p_db.add_argument("--db-path", type=Path, default=None, help="Output SQLite file path")

    # report
    p_rep = sub.add_parser("report", help="Generate Markdown daily digest report")
    p_rep.add_argument(
        "--output", type=Path, default=Path("reports/latest.md"), help="Output Markdown path"
    )
    p_rep.add_argument("--lookback-hours", type=int, default=24)

    # inspect-match
    p_match = sub.add_parser("inspect-match", help="Test complex matching on a sample title/text")
    p_match.add_argument("title", help="Listing title to test")
    p_match.add_argument("--location", default="", help="Optional location text")
    p_match.add_argument("--description", default="", help="Optional description text")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    cfg = load_config(args.config)

    if args.command == "scan":
        deals = (
            [DealType.SALE, DealType.RENT]
            if args.deal == "both"
            else [DealType(args.deal)]
        )
        run = run_scan(
            config=cfg,
            data_dir=args.data_dir,
            source_name=args.source,
            deal_types=deals,
            complex_slugs=args.complexes,
            max_pages=args.max_pages,
            max_details=args.max_details,
            min_delay=args.min_delay,
            max_delay=args.max_delay,
            dry_run=args.dry_run,
        )
        print(
            f"Scan finished: status={run.status} requests={run.requests_made} "
            f"matched={run.matched_candidates} details={run.details_fetched} "
            f"new={run.new_listings} price_changes={run.price_changes} "
            f"delisted={run.delisted_count}"
        )
        if run.notes:
            print(f"Notes: {run.notes}")
        return 0 if run.status in ("ok", "partial", "dry_run") else 2

    if args.command == "build-db":
        db_path = build_sqlite_db(args.data_dir, args.db_path)
        print(f"SQLite database built: {db_path}")
        return 0

    if args.command == "report":
        build_sqlite_db(args.data_dir)
        generate_report(
            config=cfg,
            data_dir=args.data_dir,
            output_path=args.output,
            lookback_hours=args.lookback_hours,
        )
        print(f"Report written: {args.output}")
        return 0

    if args.command == "inspect-match":
        matcher = ComplexMatcher(cfg)
        res = matcher.match(args.title, args.location, args.description)
        print(res.model_dump_json(indent=2))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
