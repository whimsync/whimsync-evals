"""CLI-level tests: run -> results JSON -> report / resume / compare (fast tier, no API)."""

import json
from pathlib import Path

import pytest

from bench import compare
from bench import run as bench_run
from bench.report import build_report


def _args(tmp: Path, *extra: str) -> list[str]:
    return [
        "--tiers", "fast", "--cases", "simple_table", "--modes", "digital,scanned",
        "--variants", "1", "--fixtures-dir", str(tmp / "corpus"), *extra,
    ]  # fmt: skip


@pytest.fixture
async def parser_run(tmp_path) -> Path:
    out = tmp_path / "r.json"
    assert await bench_run.main(_args(tmp_path, "--out", str(out))) == 0
    return out


async def test_run_writes_self_describing_results(parser_run):
    data = json.loads(parser_run.read_text())
    assert data["schema_version"] == 2 and data["kind"] == "document-parser"
    assert {r["doc_id"] for r in data["results"]} == {
        "simple_table__digital",
        "simple_table__scanned__v0",
    }
    assert {s["key"] for s in data["catalog"]} >= {"text_similarity", "vlm_failures"}
    assert data["config"]["tiers"] == "fast"
    assert not parser_run.with_name("r.partial.jsonl").exists(), "checkpoint removed on success"


async def test_report_renders_cards_and_plots(parser_run):
    page = build_report([parser_run]).read_text()
    assert page.count('class="card"') == 2
    assert "<svg" in page and "Text similarity" in page


async def test_resume_skips_finished_work(tmp_path, parser_run):
    full = json.loads(parser_run.read_text())["results"]
    partial = tmp_path / "killed.partial.jsonl"
    partial.write_text(json.dumps(full[0]) + "\n")  # simulate a run killed after one result
    assert await bench_run.main(_args(tmp_path, "--resume", str(partial))) == 0
    resumed = json.loads((tmp_path / "killed.json").read_text())["results"]
    assert [r["doc_id"] for r in resumed] == [r["doc_id"] for r in full]
    assert resumed[0]["seconds"] == full[0]["seconds"], "finished work is not re-run"
    assert not partial.exists()


async def test_compare_flags_regressions(tmp_path, parser_run, capsys):
    assert compare.main([str(parser_run), str(parser_run)]) == 0
    worse = json.loads(parser_run.read_text())
    worse["summary"]["fast/all"]["text_similarity"] -= 0.2
    worse_path = tmp_path / "worse.json"
    worse_path.write_text(json.dumps(worse))
    assert compare.main([str(parser_run), str(worse_path)]) == 1
    assert "REGRESSION" in capsys.readouterr().out


def _claims_run(path: Path, f1: float, legacy: bool = False) -> Path:
    """A minimal claim-extractor results file (legacy = pre-catalog format)."""
    run = {
        "kind": "claim-extractor", "label": path.stem, "model": "m", "gitSha": "abc",
        "summary": {
            "claim_f1": {"mean": f1, "std": 0.01},
            "failure_rate": {"mean": 0.0, "std": 0.0},
            "latency_p50_ms": {"mean": 1000.0, "std": 0.0},
            "tag:noise:claim_f1": {"mean": 1.0, "std": 0.0},
        },
        "results": [{
            "episodeId": "e1", "run": 0, "latencyMs": 900,
            "result": {"claims": [{"content": "User likes tea.", "kind": "preference"}]},
            "metrics": {"claims": {"f1": f1}, "claimMatches": [{"predIndex": 0, "refIndex": 0, "similarity": 0.93}],
                        "mutationAccuracy": None, "evidenceValidity": 1.0},
        }],
    }  # fmt: skip
    if not legacy:
        run["schema_version"] = 2
        run["catalog"] = [{"key": "claim_f1", "label": "Claim F1"}]
        run["episodes"] = {
            "e1": {
                "rawText": "I like tea.",
                "expected": {"claims": [{"content": "User likes tea.", "kind": "preference"}]},
            }
        }
    path.write_text(json.dumps(run))
    return path


def test_claims_compare_and_report_work_for_old_and_new_files(tmp_path, capsys):
    old = _claims_run(tmp_path / "old.json", 0.9, legacy=True)
    new = _claims_run(tmp_path / "new.json", 0.7)
    assert compare.main([str(old), str(new), "--per-case"]) == 1
    out = capsys.readouterr().out
    assert "claim_f1" in out and "REGRESSION" in out and "-- e1 --" in out
    page = build_report([old, new]).read_text()
    assert "Claim F1" in page and "I like tea." in page and "sim 0.93" in page


def test_report_refuses_mixed_kinds(tmp_path, parser_run):
    with pytest.raises(ValueError, match="mix"):
        build_report([parser_run, _claims_run(tmp_path / "c.json", 0.5)])
