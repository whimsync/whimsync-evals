"""CLI: `python -m bench.report [results.json ...] [--out page.html] [--open]`."""

import argparse
import sys
from pathlib import Path

from . import RESULTS_DIR, build_report, latest_result


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Render benchmark results JSON into an HTML report.")
    p.add_argument("results", nargs="*", type=Path, help="Results files (default: latest)")
    p.add_argument("--out", type=Path, help="Output HTML path (default: next to last JSON)")
    p.add_argument("--open", action="store_true", help="Open the report in a browser")
    args = p.parse_args(argv)

    paths = args.results
    if not paths:
        latest = latest_result()
        if not latest:
            print(f"No results in {RESULTS_DIR}. Run `bun run parser` first.", file=sys.stderr)
            return 1
        paths = [latest]
    print(f"Report written to {build_report(paths, args.out, args.open)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
