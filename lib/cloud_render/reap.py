"""CLI entry point for the cloud-render reaper.

    python -m lib.cloud_render.reap [--dry-run] [--adopt-unknown]

The mechanics live in `ledger.reap()`; this module is only argument parsing
and the printed report, so a Makefile target or CI hook can shell out to it
and check the exit code (non-zero means it had to destroy something).
"""

from __future__ import annotations

import argparse
import sys

from lib.cloud_render.ledger import ReapReport, reap


def _print_report(report: ReapReport) -> None:
    print(f"{report.total} rentals, {len(report.destroyed)} destroyed")
    if report.closed:
        print(f"  {len(report.closed)} closed (already gone on the account)")
    if report.left_alone:
        print(f"  {len(report.left_alone)} left alone (still in-deadline)")
    for warning in report.warnings:
        print(f"WARNING: {warning}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m lib.cloud_render.reap",
        description="Sweep the Vast.ai account for leaked or expired openmontage-* rentals.")
    parser.add_argument("--dry-run", action="store_true",
                        help="report only, destroy nothing")
    parser.add_argument("--adopt-unknown", action="store_true",
                        help="leave in-deadline openmontage-labelled instances with no local "
                             "ledger record alone, instead of destroying them")
    args = parser.parse_args(argv)

    report = reap(dry_run=args.dry_run, adopt_unknown=args.adopt_unknown)
    _print_report(report)
    return 1 if report.destroyed else 0


if __name__ == "__main__":
    sys.exit(main())
