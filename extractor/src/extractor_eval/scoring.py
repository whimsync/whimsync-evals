"""
Scores one (case, tier) container output: deterministic checks, links, routing and
(optionally) the Gemini judge per unit, plus operational facts from result.json.
"""

from collections.abc import Callable
from pathlib import Path

from . import deterministic, links, routing
from .config import cost_usd, read_json
from .dataset import Case, Unit, sha256_text


def candidate_for(unit: Unit, output_dir: Path) -> str:
    """The candidate Markdown for a unit: its page file, or the range's pages joined."""
    parts = []
    for page in unit.pages:
        path = output_dir / "pages" / f"{page:03d}.md"
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        if text.strip():
            parts.append(text.strip())
    return "\n\n".join(parts)


def judge_metrics(record: dict | None) -> dict:
    verdict = (record or {}).get("verdict")
    if not verdict:
        return {}
    return {
        "judge_overall": verdict["overall_score"],
        "judge_text": verdict["text_fidelity"],
        "judge_reading_order": verdict["reading_order"],
        "judge_table": verdict.get("table_fidelity"),
        "judge_visual": verdict.get("visual_fidelity"),
        "judge_critical_errors": len(verdict.get("critical_errors", [])),
    }


def operational(result: dict | None, container: dict) -> dict:
    result = result or {}
    pages = result.get("total_pages") or 0
    duration = result.get("duration_seconds")
    return {
        "status": result.get("status") or ("timeout" if container.get("timed_out") else "failed"),
        "error": result.get("error"),
        "exit_code": container.get("exit_code"),
        "wall_seconds": container.get("wall_seconds"),
        "duration_seconds": duration,
        "seconds_per_page": duration / pages if duration is not None and pages else None,
        "total_pages": pages,
        "vlm_page_count": result.get("vlm_page_count"),
        "vlm_page_ratio": (result.get("vlm_page_count") or 0) / pages if pages else None,
        "vlm_calls": result.get("vlm_calls"),
        "vlm_failures": result.get("vlm_failures"),
        "retries": result.get("retries"),
        "input_tokens": result.get("input_tokens"),
        "output_tokens": result.get("output_tokens"),
        "cost_usd": cost_usd(
            result.get("model"), result.get("input_tokens"), result.get("output_tokens")
        ),
        "output_chars": result.get("output_chars"),
        "vlm_available": result.get("vlm_available"),
    }


def score_case_tier(
    case: Case,
    tier: str,
    output_dir: Path,
    container: dict,
    judge: Callable[[Unit, str, str], dict] | None = None,
) -> dict:
    result = read_json(output_dir / "result.json")
    trace = read_json(output_dir / "trace.json")
    has_links = case.links_path.exists()
    expected_links = case.expected_links() if has_links else []

    units = []
    for unit in case.units():
        gold_md = unit.gold_path.read_text(encoding="utf-8")
        candidate_md = candidate_for(unit, output_dir) if result else ""
        det = deterministic.score(gold_md, candidate_md, unit.required_phrases)
        metrics = dict(det["metrics"])
        details = dict(det["details"])
        if has_links:
            unit_links = [link for link in expected_links if link["page"] in unit.pages]
            link_score = links.score(unit_links, candidate_md)
            metrics.update(link_score["metrics"])
            details["links"] = link_score["details"]
        judge_record = judge(unit, gold_md, candidate_md) if judge and result else None
        metrics.update(judge_metrics(judge_record))
        units.append(
            {
                "id": unit.id,
                "pages": unit.pages,
                "tags": unit.tags,
                "gold_sha256": sha256_text(gold_md),
                "candidate_sha256": sha256_text(candidate_md),
                "candidate_chars": len(candidate_md),
                "metrics": metrics,
                "details": details,
                "judge": (
                    {
                        "verdict": judge_record.get("verdict"),
                        "error": judge_record.get("error"),
                        "synthetic": judge_record.get("synthetic"),
                    }
                    if judge_record
                    else None
                ),
            }
        )

    return {
        "case": case.id,
        "tier": tier,
        "suite": case.suite,
        "quality_mode": case.quality_mode,
        "gold_status": case.gold_status,
        "labels_status": case.labels_status,
        "pdf_sha256": case.sha256,
        "operational": operational(result, container),
        "units": units,
        "routing": routing.page_outcomes(case, trace),
        "fingerprint": fingerprint(result),
    }


def fingerprint(result: dict | None) -> dict:
    """What must stay constant for two results to be comparable."""
    result = result or {}
    return {
        "extractor_commit": result.get("extractor_commit"),
        "image_digest": result.get("image_digest"),
        "model": result.get("model"),
        "prompt_versions": result.get("prompt_versions"),
        "render_scale": result.get("render_scale"),
        "settings": result.get("settings"),
        "versions": result.get("versions"),
    }
