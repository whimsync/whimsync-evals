import asyncio

import pytest
from src.schemas.parsing import ParseTier

from bench import runner
from bench.augment import DIGITAL
from bench.cases import get_cases
from bench.generate import render_case


class FakeVlm:
    """Stands in for VLMService.process_image: fails for paths containing 'bad'."""

    async def process_image(self, path, prompt_type="full_page_scan") -> str:
        await asyncio.sleep(0.01)
        return "" if "bad" in str(path) else "# text"


@pytest.mark.asyncio
async def test_vlm_usage_is_attributed_per_job_under_concurrency():
    svc = FakeVlm()
    runner.instrument_vlm(svc)
    runner.instrument_vlm(svc)  # idempotent

    async def job(paths):
        usage = runner.VlmUsage()
        runner._usage.set(usage)
        for p in paths:
            await svc.process_image(p)
        return usage

    a, b = await asyncio.gather(
        asyncio.create_task(job(["ok1", "bad1", "bad2"])), asyncio.create_task(job(["ok2"]))
    )
    assert (a.calls, a.failures) == (3, 2)
    assert (b.calls, b.failures) == (1, 0)


def test_aggregate_groups_and_ignores_errors():
    rec = lambda tier, mode, sim, **kw: {  # noqa: E731
        "tier": tier,
        "mode": mode,
        "seconds": 2.0,
        "pages": 2,
        "vlm_pages": 1,
        "metrics": {"text_similarity": sim, "heading_f1": None},
        **kw,
    }
    summary = runner.aggregate(
        [
            rec("smart", "scanned", 1.0),
            rec("smart", "scanned", 0.5, vlm_failures=1),
            rec("smart", "digital", 0.0),
            {"tier": "smart", "mode": "scanned", "error": "boom"},
        ]
    )
    assert set(summary) == {"smart/scanned", "smart/digital", "smart/all"}
    s = summary["smart/scanned"]
    assert (s["n"], s["text_similarity"], s["heading_f1"]) == (2, 0.75, None)
    assert (s["seconds_per_page"], s["vlm_pages"], s["vlm_failures"]) == (1.0, 2, 1)
    assert summary["smart/all"]["n"] == 3


@pytest.mark.asyncio
async def test_run_jobs_scores_fast_tier(tmp_path):
    case = get_cases(["simple_table"])[0]
    doc = render_case(case, DIGITAL, tmp_path / "corpus")
    records: list[dict] = []
    options = runner.RunOptions(work_root=tmp_path / "work", concurrency=2)
    await runner.run_jobs([runner.Job(doc, ParseTier.FAST)], options, records.append)
    (r,) = records
    assert r["doc_id"] == "simple_table__digital" and r["tier"] == "fast"
    assert r["metrics"]["word_recall"] > 0.9
    assert r["metrics"]["table_cell_accuracy"] > 0.9
    assert r["output"] and r["vlm_calls"] == 0
