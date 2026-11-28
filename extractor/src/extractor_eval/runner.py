"""
Executes an evaluation run and writes its results directory:

    results/runs/<run_id>/
      run.json                       configuration, image, dataset hashes, status
      meta.json                      display name + groups (editable; see store.py)
      comments.json                  review comments (editable)
      summary.json                   per-tier aggregates (report.summarize)
      tier-<tier>.json               every case score for that tier
      cases/<case>/<tier>/output/    container artifacts (document.md, pages/, trace, result)
      cases/<case>/<tier>/output.log container log
      cases/<case>/<tier>/scores.json
      cases/<case>/<tier>/judge/     judge requests, responses and rendered page images
      cases/<case>/<tier>/perf/<i>/  performance repeats (no scoring, no judging)

`--resume <run_id>` skips (case, tier) pairs that already have scores.json.
"""

import os
import re
import subprocess
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from . import DATASET_PATH, REPO_DIR, RUNS_DIR, report, scoring
from . import judge as judge_mod
from .config import CONTAINER_ENV, JUDGE_CONCURRENCY, JUDGE_TEMPERATURE, read_json, write_json
from .dataset import Case, problems, sha256_file, sha256_text
from .docker_runner import Image, run_container

Log = Callable[[str], None]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_run_id(label: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", label).strip("-")[:40] or "run"
    return f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{slug}"


def _evals_commit() -> str | None:
    try:
        return subprocess.run(
            ["git", "-C", str(REPO_DIR), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def _container_settings(model: str | None) -> dict:
    """Non-secret extraction settings that go into the container."""
    settings = {
        k.lower(): os.environ[k] for k in CONTAINER_ENV if k in os.environ and k != "GEMINI_API_KEY"
    }
    if model:
        settings["gemini_model"] = model
    return settings


def _dataset_fingerprint(cases: list[Case]) -> dict:
    return {
        "sha256": sha256_file(DATASET_PATH),
        "cases": {
            c.id: {
                "pdf_sha256": c.sha256,
                "gold_status": c.gold_status,
                "gold": {
                    u.id: sha256_text(u.gold_path.read_text(encoding="utf-8")) for u in c.units()
                },
            }
            for c in cases
        },
    }


def _judge_all(case: Case, tier_dir: Path, output_dir: Path, judge_model: str) -> dict[str, dict]:
    """Judges every unit of one (case, tier) in parallel; returns unit id -> record."""
    jobs = {}
    with ThreadPoolExecutor(max_workers=max(1, JUDGE_CONCURRENCY)) as pool:
        for unit in case.units():
            gold = unit.gold_path.read_text(encoding="utf-8")
            candidate = scoring.candidate_for(unit, output_dir)
            jobs[unit.id] = pool.submit(
                judge_mod.judge_unit,
                case.pdf_path,
                unit.pages,
                gold,
                candidate,
                tier_dir / "judge",
                unit.id,
                judge_model,
            )
    return {unit_id: job.result() for unit_id, job in jobs.items()}


def execute(
    cases: list[Case],
    tiers: list[str],
    image: Image,
    *,
    label: str,
    groups: list[str] | None = None,
    judge: bool = True,
    judge_model: str = judge_mod.JUDGE_MODEL,
    perf_repeats: int = 0,
    model: str | None = None,
    resume: str | None = None,
    log: Log = print,
) -> Path:
    issues = [p for c in cases for p in problems(c)]
    if issues:
        raise ValueError("dataset problems:\n  " + "\n  ".join(issues))

    run_id = resume or new_run_id(label)
    run_dir = RUNS_DIR / run_id
    run_path = run_dir / "run.json"
    run = read_json(run_path) if resume else None
    if resume and run is None:
        raise ValueError(f"cannot resume: {run_path} not found")
    if run is None:
        run = {
            "id": run_id,
            "kind": "extractor",
            "label": label,
            "created_at": _now(),
            "finished_at": None,
            "status": "running",
            "evals_commit": _evals_commit(),
            "image": {
                "tag": image.tag,
                "commit": image.commit,
                "dirty": image.dirty,
                "digest": image.digest,
            },
            "config": {
                "tiers": tiers,
                "cases": [c.id for c in cases],
                "judge": judge,
                "judge_model": judge_model if judge else None,
                "judge_prompt_version": judge_mod.PROMPT_VERSION if judge else None,
                "judge_temperature": JUDGE_TEMPERATURE if judge else None,
                "perf_repeats": perf_repeats,
                "extraction": _container_settings(model),
            },
            "dataset": _dataset_fingerprint(cases),
            "errors": [],
        }
        write_json(run_path, run)
        write_json(run_dir / "meta.json", {"name": label, "groups": sorted(set(groups or []))})
        write_json(run_dir / "comments.json", [])
    else:
        run["status"] = "running"
        write_json(run_path, run)

    log(f"[run] {run_id}: {len(cases)} cases x {tiers} on {image.ref}")
    scores_by_tier: dict[str, list[dict]] = {t: [] for t in tiers}
    perf_by_tier: dict[str, dict[str, list[float]]] = {t: {} for t in tiers}

    try:
        for case in cases:
            for tier in tiers:
                tier_dir = run_dir / "cases" / case.id / tier
                scores_path = tier_dir / "scores.json"
                if scores_path.exists():
                    log(f"[skip] {case.id} [{tier}] already scored")
                    scores_by_tier[tier].append(read_json(scores_path))
                else:
                    log(f"[parse] {case.id} [{tier}]")
                    output_dir = tier_dir / "output"
                    proc = run_container(image, case.pdf_path, tier, output_dir, model=model)
                    container = {
                        "exit_code": proc.exit_code,
                        "wall_seconds": round(proc.wall_seconds, 3),
                        "timed_out": proc.timed_out,
                    }
                    if proc.exit_code != 0:
                        run["errors"].append(
                            f"{case.id} [{tier}]: container exit {proc.exit_code} (see {proc.log_path.name})"
                        )
                    records = {}
                    if judge and (output_dir / "result.json").exists():
                        log(f"[judge] {case.id} [{tier}] {len(case.units())} unit(s)")
                        records = _judge_all(case, tier_dir, output_dir, judge_model)
                    scores = scoring.score_case_tier(
                        case,
                        tier,
                        output_dir,
                        container,
                        judge=(lambda unit, _g, _c, r=records: r.get(unit.id)) if records else None,
                    )
                    write_json(scores_path, scores)
                    scores_by_tier[tier].append(scores)

                if perf_repeats and case.small:
                    durations = []
                    for i in range(perf_repeats):
                        perf_dir = tier_dir / "perf" / str(i)
                        existing = read_json(perf_dir / "result.json")
                        if existing is None:
                            log(f"[perf] {case.id} [{tier}] {i + 1}/{perf_repeats}")
                            run_container(image, case.pdf_path, tier, perf_dir, model=model)
                            existing = read_json(perf_dir / "result.json")
                        if existing and existing.get("status") == "success":
                            durations.append(existing["duration_seconds"])
                    perf_by_tier[tier][case.id] = durations
                write_json(run_path, run)
    except KeyboardInterrupt:
        run["status"] = "interrupted"
        write_json(run_path, run)
        log(f"[run] interrupted; resume with --resume {run_id}")
        raise

    for tier, scores in scores_by_tier.items():
        write_json(run_dir / f"tier-{tier}.json", scores)
    summary = report.summarize(scores_by_tier, perf_by_tier)
    write_json(run_dir / "summary.json", summary)
    run["status"] = "complete" if not run["errors"] else "complete_with_errors"
    run["finished_at"] = _now()
    write_json(run_path, run)
    log(report.console_table(summary))
    log(f"[run] results: {run_dir}")
    return run_dir


def rescore(run_dir: Path, cases_by_id: dict[str, Case], log: Log = print) -> None:
    """Recomputes deterministic scores and summaries from saved artifacts (no Docker,
    no new judge calls: saved judge verdicts are reused)."""
    run = read_json(run_dir / "run.json")
    tiers = run["config"]["tiers"]
    scores_by_tier: dict[str, list[dict]] = {t: [] for t in tiers}
    perf_by_tier: dict[str, dict[str, list[float]]] = {t: {} for t in tiers}
    for case_id in run["config"]["cases"]:
        case = cases_by_id[case_id]
        for tier in tiers:
            tier_dir = run_dir / "cases" / case_id / tier
            old = read_json(tier_dir / "scores.json", {})
            container = {
                "exit_code": old.get("operational", {}).get("exit_code"),
                "wall_seconds": old.get("operational", {}).get("wall_seconds"),
            }
            saved = {p.stem: read_json(p) for p in (tier_dir / "judge").glob("*.json")}
            scores = scoring.score_case_tier(
                case,
                tier,
                tier_dir / "output",
                container,
                judge=(lambda unit, _g, _c, r=saved: r.get(unit.id)) if saved else None,
            )
            write_json(tier_dir / "scores.json", scores)
            scores_by_tier[tier].append(scores)
            perf = [read_json(p / "result.json") for p in sorted((tier_dir / "perf").glob("*"))]
            durations = [p["duration_seconds"] for p in perf if p and p.get("status") == "success"]
            if durations:
                perf_by_tier[tier][case_id] = durations
    for tier, scores in scores_by_tier.items():
        write_json(run_dir / f"tier-{tier}.json", scores)
    write_json(run_dir / "summary.json", report.summarize(scores_by_tier, perf_by_tier))
    log(f"[rescore] {run_dir.name} updated")
