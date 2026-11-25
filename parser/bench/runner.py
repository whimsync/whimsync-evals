"""
Executes parser jobs, one (document, tier) pair each, and aggregates their records.

Jobs run concurrently up to `concurrency`. Gemini pacing (RPM, retries, quota
fail-fast) stays inside whimsync's `vlm_service`, so concurrency never exceeds
the configured rate. VLM calls are counted per job through a ContextVar, so
counts stay correct while jobs overlap.
"""

import asyncio
import statistics
import time
from collections.abc import Awaitable, Callable, Iterable
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path

from src.parsing.engine import parsing_engine
from src.schemas.parsing import ParseTier
from src.services.vlm_service import VLMService

from .generate import RenderedDoc
from .metrics import score

Record = dict


@dataclass(frozen=True)
class Job:
    doc: RenderedDoc
    tier: ParseTier

    @property
    def key(self) -> tuple[str, str]:
        return (self.doc.doc_id, self.tier.value)


def record_key(record: Record) -> tuple[str, str]:
    return (record["doc_id"], record["tier"])


def is_reusable(record: Record) -> bool:
    """Records worth keeping on resume; errors and failed VLM pages are re-run."""
    return not record.get("error") and not record.get("vlm_failures")


# --------------------------------------------------------------------------- VLM accounting


@dataclass
class VlmUsage:
    calls: int = 0
    failures: int = 0


_usage: ContextVar[VlmUsage | None] = ContextVar("vlm_usage", default=None)


def instrument_vlm(service: VLMService) -> None:
    """
    Wraps `service.process_image` to count calls and empty results for the job
    running in the current context. Idempotent. An empty result means the call
    failed after retries (or produced nothing), so it counts as a failure.
    """
    if getattr(service.process_image, "_bench_instrumented", False):
        return
    original = service.process_image

    async def tracked(*args, **kwargs) -> str:
        text = await original(*args, **kwargs)
        if (usage := _usage.get()) is not None:
            usage.calls += 1
            usage.failures += not text
        return text

    tracked._bench_instrumented = True  # type: ignore[attr-defined]
    service.process_image = tracked  # type: ignore[method-assign]


# --------------------------------------------------------------------------- execution


@dataclass
class RunOptions:
    work_root: Path
    keep_output: bool = True
    concurrency: int = 1
    on_start: Callable[[Job, int, int], None] = field(default=lambda job, i, n: None)


async def run_job(job: Job, options: RunOptions) -> Record:
    """Parses one document with one tier and scores it. Never raises."""
    doc = job.doc
    work_dir = options.work_root / f"{doc.doc_id}__{job.tier.value}"
    work_dir.mkdir(parents=True, exist_ok=True)
    usage = VlmUsage()
    _usage.set(usage)
    record: Record = {
        "case": doc.case,
        "mode": doc.mode,
        "variant": doc.variant,
        "doc_id": doc.doc_id,
        "tier": job.tier.value,
        "pdf_path": str(doc.pdf_path.resolve()),
        "gt_path": str(doc.md_path.resolve()),
        "augment": {k: v for k, v in (doc.augment or {}).items() if k != "pages"} or None,
    }
    start = time.perf_counter()
    try:
        parsed = await parsing_engine.parse(doc.pdf_path, work_dir, job.tier)
    except Exception as exc:  # a benchmark records failures instead of aborting
        return {**record, "error": f"{type(exc).__name__}: {exc}", "seconds": _since(start)}
    ground_truth = doc.md_path.read_text(encoding="utf-8")
    record |= {
        "seconds": _since(start),
        "pages": parsed.total_pages,
        "vlm_pages": int(parsed.metadata.get("vlm_page_count", 0) or 0),
        "vlm_calls": usage.calls,
        "vlm_failures": usage.failures,
        "metrics": score(parsed.markdown, ground_truth),
    }
    if options.keep_output:
        record["output"] = parsed.markdown
    return record


async def run_jobs(
    jobs: list[Job],
    options: RunOptions,
    on_result: Callable[[Record], None] | Callable[[Record], Awaitable[None]],
) -> None:
    """Runs jobs with bounded concurrency; `on_result` sees each record as it finishes."""
    semaphore = asyncio.Semaphore(max(1, options.concurrency))
    total = len(jobs)

    async def one(index: int, job: Job) -> None:
        async with semaphore:
            options.on_start(job, index + 1, total)
            # Each task gets its own context, so the VLM usage counter is per job.
            record = await run_job(job, options)
        maybe = on_result(record)
        if asyncio.iscoroutine(maybe):
            await maybe

    await asyncio.gather(*(asyncio.create_task(one(i, job)) for i, job in enumerate(jobs)))


def _since(start: float) -> float:
    return time.perf_counter() - start


# --------------------------------------------------------------------------- aggregation


def group_keys(record: Record) -> Iterable[str]:
    """Groups a record contributes to. Extend here to add new slices (e.g. by case)."""
    yield f"{record['tier']}/{record['mode']}"
    yield f"{record['tier']}/all"


def aggregate(records: list[Record]) -> dict[str, dict[str, float | int | None]]:
    """Per group: mean of each score (N/A ignored) plus summed/averaged cost metrics."""
    groups: dict[str, list[Record]] = {}
    for r in records:
        if r.get("error"):
            continue
        for key in group_keys(r):
            groups.setdefault(key, []).append(r)

    out: dict[str, dict[str, float | int | None]] = {}
    for key, rows in sorted(groups.items()):
        agg: dict[str, float | int | None] = {"n": len(rows)}
        for metric in rows[0]["metrics"]:
            vals = [
                row["metrics"][metric] for row in rows if row["metrics"].get(metric) is not None
            ]
            agg[metric] = statistics.fmean(vals) if vals else None
        agg["seconds_per_page"] = statistics.fmean(r["seconds"] / max(r["pages"], 1) for r in rows)
        agg["vlm_pages"] = sum(r["vlm_pages"] for r in rows)
        agg["vlm_failures"] = sum(r.get("vlm_failures", 0) for r in rows)
        out[key] = agg
    return out
