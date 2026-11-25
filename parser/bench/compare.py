"""
Compare two results files of the same suite in the terminal (B - A).

    uv run python -m bench.compare results/A.json results/B.json [--per-case]
    uv run python -m bench.compare X.json X.json --group-a fast/scanned --group-b smart/scanned

Works for every suite (document parser and claim extractor) via `kinds`. Metric
directions and tolerances come from the run's catalog. Exit code 1 if anything
regresses, so it can gate CI.
"""

import argparse
import sys
from pathlib import Path

from .catalog import Catalog
from .console import table
from .diff import DEFAULT_THRESHOLD, Delta, count_regressions, diff_metrics
from .kinds import kind_of
from .store import load_run

HEADER = ["metric", "A", "B", "delta", ""]
FLAGS = {"regression": "REGRESSION", "improved": "improved"}


def _rows(deltas: list[Delta], catalog: Catalog, spread_a=None, spread_b=None) -> list[list[str]]:
    rows = []
    for d in deltas:
        spec = catalog.get(d.metric)

        def cell(v, sd):
            shown = sd is not None and abs(sd) >= 0.0005
            return spec.format(v) + (f"±{spec.format(sd)}" if shown else "")

        delta = "n/a" if d.delta is None else f"{d.delta:+.3f}"
        if d.delta is not None and spec.unit == "ms":
            delta = f"{d.delta:+.0f}"
        rows.append(
            [
                d.metric,
                cell(d.a, (spread_a or {}).get(d.metric)),
                cell(d.b, (spread_b or {}).get(d.metric)),
                delta,
                FLAGS.get(d.verdict, ""),
            ]
        )
    return rows


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Diff two benchmark results files (B - A).")
    p.add_argument("a", type=Path)
    p.add_argument("b", type=Path)
    p.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD, help="Score tolerance")
    p.add_argument("--per-case", action="store_true", help="Also list per-case changes")
    p.add_argument("--group-a", help="Compare this group of A (e.g. fast/scanned) ...")
    p.add_argument("--group-b", help="... against this group of B (default: --group-a)")
    args = p.parse_args(argv)

    a, b = load_run(args.a), load_run(args.b)
    kind = kind_of(a)
    if kind_of(b) is not kind:
        print(f"Cannot compare {a['kind']} with {b['kind']}", file=sys.stderr)
        return 2
    catalog = kind.catalog(b).merged(kind.catalog(a))
    for tag, path, run in (("A", args.a, a), ("B", args.b, b)):
        print(
            f"{tag}: {path.name}  label={run['label']}  "
            f"sha={run.get('git_sha') or run.get('gitSha')}  model={kind.model(run)}"
        )

    conc_a, conc_b = ((run.get("config") or {}).get("concurrency") for run in (a, b))
    if conc_a != conc_b and None not in (conc_a, conc_b):
        print(f"note: concurrency differs (A={conc_a}, B={conc_b}); timing is not comparable")

    ga, gb = kind.groups(a), kind.groups(b)
    sa, sb = kind.spread(a), kind.spread(b)
    if args.group_a:
        pairs = [(args.group_a, args.group_b or args.group_a)]
    else:
        pairs = [(g, g) for g in sorted(set(ga) & set(gb))]
        if only := sorted(set(ga) ^ set(gb)):
            print(f"(groups present in only one file: {only})")

    regressions = 0
    for x, y in pairs:
        if x not in ga or y not in gb:
            print(f"\nGroup missing: A[{x}] or B[{y}]")
            continue
        deltas = diff_metrics(ga[x], gb[y], catalog, args.threshold)
        regressions += count_regressions(deltas)
        print(f"\n== {x if x == y else f'A[{x}] vs B[{y}]'} ==")
        print(table(HEADER, _rows(deltas, catalog, sa.get(x), sb.get(y)), indent="  "))

    if args.per_case:
        ca, cb = kind.cases(a), kind.cases(b)
        for key in sorted(set(ca) & set(cb)):
            changed = [
                d
                for d in diff_metrics(ca[key], cb[key], catalog, args.threshold)
                if d.verdict in FLAGS
            ]
            if changed:
                print(f"\n-- {key} --")
                print(table(HEADER, _rows(changed, catalog), indent="  "))

    print(f"\n{regressions} regression(s) beyond threshold {args.threshold}")
    return 1 if regressions else 0


if __name__ == "__main__":
    sys.exit(main())
