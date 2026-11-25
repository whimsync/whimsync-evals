"""
Document parser benchmark CLI.

    uv run python -m bench.run --tiers fast,smart,max --modes digital,scanned --label baseline

Pipeline: cases (`parser/cases/*.md`) -> corpus (`generate`) -> jobs (doc x tier)
-> `runner` (parse + score) -> `store` (checkpoint + results JSON) -> summary.
"""

import argparse
import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

from src.schemas.parsing import ParseTier
from src.services.vlm_service import vlm_service

from . import CORPUS_DIR, RESULTS_DIR
from .augment import parse_modes
from .cases import get_cases
from .console import log, table
from .generate import generate_corpus
from .meta import provenance, utc_timestamp
from .metrics import CATALOG
from .runner import Job, RunOptions, aggregate, instrument_vlm, is_reusable, record_key, run_jobs
from .store import ResultStore, final_path

KIND = "document-parser"


def _csv(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Benchmark document parsing tiers on synthetic PDFs.")
    sel = p.add_argument_group("what to run")
    sel.add_argument("--tiers", default="fast,smart,max", help="Comma list of fast,smart,max")
    sel.add_argument("--modes", default="", help="Comma list of render modes (default: all)")
    sel.add_argument("--cases", default="", help="Comma list of case names (default: all)")
    sel.add_argument("--variants", type=int, default=2, help="Random variants per scan mode")
    sel.add_argument("--seed", type=int, default=0, help="Base seed for scan augmentation")

    exe = p.add_argument_group("execution")
    exe.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="Documents parsed at once. >1 speeds up VLM-bound runs but inflates "
        "seconds_per_page (jobs share the event loop); compare runs at equal values",
    )
    exe.add_argument("--rpm", type=float, help="Cap VLM requests/minute (default: VLM_RPM)")
    exe.add_argument("--model", help="Override GEMINI_MODEL for the VLM tiers")
    exe.add_argument("--keep-workdir", action="store_true", help="Keep extracted images/scans")

    out = p.add_argument_group("output")
    out.add_argument("--label", default="run", help="Stored in results and used in the filename")
    out.add_argument("--out", type=Path, help="Results path (default: results/<ts>-<label>.json)")
    out.add_argument(
        "--fixtures-dir", type=Path, default=CORPUS_DIR, help="Generated corpus (reused)"
    )
    out.add_argument("--no-output", action="store_true", help="Don't store parsed Markdown")
    out.add_argument(
        "--resume",
        type=Path,
        help="Continue from a .partial.jsonl checkpoint (or results JSON); finished doc/tier "
        "pairs are skipped and errors / VLM failures re-run",
    )
    out.add_argument("--report", action="store_true", help="Build the HTML report afterwards")
    out.add_argument("--open", action="store_true", help="Build the HTML report and open it")
    return p


def select_tiers(names: list[str]) -> list[ParseTier]:
    tiers = [ParseTier(t) for t in names]
    if vlm_service.is_available():
        return tiers
    skipped = [t.value for t in tiers if t != ParseTier.FAST]
    if skipped:
        log(f"GEMINI_API_KEY not set: skipping tiers {skipped}")
    return [t for t in tiers if t == ParseTier.FAST]


def print_summary(summary: dict, records: list[dict]) -> None:
    specs = CATALOG.summary_specs()
    rows = [
        [group, str(vals["n"]), *(s.format(vals.get(s.key)) for s in specs)]
        for group, vals in summary.items()
    ]
    print("\n" + table(["group", "n", *(s.column for s in specs)], rows))
    errors = [r for r in records if r.get("error")]
    if errors:
        print(f"\n{len(errors)} run(s) failed:")
        for e in errors:
            print(f"  {e['doc_id']} [{e['tier']}]: {e['error']}")


async def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.rpm is not None:
        vlm_service.rate_limiter.set_rpm(args.rpm)
    if args.model:
        vlm_service.model = args.model
    instrument_vlm(vlm_service)

    tiers = select_tiers(_csv(args.tiers))
    if not tiers:
        log("No tiers left to run.")
        return 1
    modes = parse_modes(_csv(args.modes))
    log(f"Generating corpus in {args.fixtures_dir}")
    docs = generate_corpus(
        get_cases(_csv(args.cases)), modes, args.fixtures_dir, args.variants, args.seed
    )

    timestamp = utc_timestamp()
    out_path = args.out or (
        final_path(args.resume) if args.resume else RESULTS_DIR / f"{timestamp}-{args.label}.json"
    )
    store = ResultStore(out_path, record_key, resume_from=args.resume, keep=is_reusable)
    if args.resume:
        log(f"Resuming: {len(store.records)} results carried over from {args.resume}")
    log(f"Checkpointing each result to {store.checkpoint}")

    jobs = [Job(doc, t) for doc in docs for t in tiers]
    jobs = [job for job in jobs if not store.is_done(job.key)]
    work_root = Path(tempfile.mkdtemp(prefix="whimsync-bench-"))
    options = RunOptions(
        work_root=work_root,
        keep_output=not args.no_output,
        concurrency=args.concurrency,
        on_start=lambda job, i, n: log(f"({i}/{n}) {job.doc.doc_id} [{job.tier.value}]"),
    )
    try:
        await run_jobs(jobs, options, store.add)
    finally:
        if args.keep_workdir:
            log(f"Work dir kept at {work_root}")
        else:
            shutil.rmtree(work_root, ignore_errors=True)

    summary = aggregate(store.records)
    print_summary(summary, store.records)
    path = store.finalize(
        {
            "kind": KIND,
            "label": args.label,
            "timestamp": timestamp,
            **provenance(),
            "gemini_model": vlm_service.model if vlm_service.is_available() else None,
            "vlm_rpm": vlm_service.rate_limiter.rpm or None,
            "tiers": [t.value for t in tiers],
            "modes": modes,
            "variants": args.variants,
            "seed": args.seed,
            "config": {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()},
            "catalog": CATALOG.to_json(),
            "summary": summary,
        },
        sort_key=record_key,
    )
    print(f"\n[bench] Results written to {path}")
    if args.report or args.open:
        from .report import build_report

        print(f"[bench] Report written to {build_report([path], open_browser=args.open)}")
    return 1 if any(r.get("error") for r in store.records) else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
