"""End-to-end run with a fake container and judge, then results management, compare
and the HTTP API, all inside a temporary results tree (no Docker, no API calls)."""

import json

import pymupdf
import pytest
from fastapi.testclient import TestClient

from extractor_eval import compare, runner, store
from extractor_eval import dataset as ds
from extractor_eval import judge as judge_mod
from extractor_eval.docker_runner import ContainerRun, Image

GOLD = "# Notice\n\nThe fee is $250 per year.\n\nJoint Secretary to the Government of India\n"
CANDIDATES = {
    "balanced": "# Notice\n\nThe fee is $250 per year.\n\nJoint Secretary to the Government of lndia\n",
    "max": GOLD,
}


@pytest.fixture
def env(tmp_path, monkeypatch):
    project = tmp_path / "extractor"
    runs_dir = project / "results" / "runs"
    claims_dir = tmp_path / "claims"
    claims_dir.mkdir(parents=True)
    case_dir = project / "cases" / "notice"
    (case_dir / "expected" / "pages").mkdir(parents=True)
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "The fee is $250 per year.")
    doc.save(case_dir / "source.pdf")
    (case_dir / "expected" / "pages" / "001.md").write_text(GOLD)
    dataset_path = project / "dataset.json"

    monkeypatch.setattr(ds, "PROJECT_DIR", project)
    monkeypatch.setattr(ds, "DATASET_PATH", dataset_path)
    monkeypatch.setattr(runner, "DATASET_PATH", dataset_path)
    monkeypatch.setattr(runner, "RUNS_DIR", runs_dir)
    monkeypatch.setattr(store, "RUNS_DIR", runs_dir)
    monkeypatch.setattr(store, "CLAIMS_RESULTS_DIR", claims_dir)

    case = ds.Case(
        id="notice",
        pdf="cases/notice/source.pdf",
        sha256=ds.sha256_file(case_dir / "source.pdf"),
        quality_mode="pages",
        gold_status="verified",
        page_labels={
            "1": ds.PageLabel(
                expected_route="vlm_required",
                tags=["scanned"],
                required_phrases=["Government of India"],
            )
        },
    )
    ds.save(ds.Dataset(cases=[case]), dataset_path)

    def fake_container(image, pdf, tier, output, model=None, env_overrides=None, timeout=0):
        (output / "pages").mkdir(parents=True, exist_ok=True)
        (output / "pages" / "001.md").write_text(CANDIDATES[tier])
        (output / "document.md").write_text(CANDIDATES[tier])
        route = "vlm" if tier == "max" else "native"
        (output / "trace.json").write_text(
            json.dumps(
                {
                    "pages": [
                        {
                            "page_number": 1,
                            "route": route,
                            "vlm_failed": False,
                            "complexity": {"reasons": []},
                        }
                    ],
                    "vlm_calls": [],
                }
            )
        )
        (output / "result.json").write_text(
            json.dumps(
                {
                    "status": "success",
                    "model": "gemini-test",
                    "total_pages": 1,
                    "vlm_page_count": int(route == "vlm"),
                    "duration_seconds": 2.0 if tier == "max" else 0.5,
                    "vlm_calls": int(route == "vlm"),
                    "vlm_failures": 0,
                    "retries": 0,
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "output_chars": 60,
                    "extractor_commit": "abc",
                    "prompt_versions": {"full_page_scan": "p1"},
                    "render_scale": {"max": 2.5},
                    "settings": {},
                }
            )
        )
        return ContainerRun(0, 1.0, output.parent / "output.log")

    def fake_judge(pdf_path, pages, gold_md, candidate_md, out_dir, unit_id, model="m"):
        score = 5 if candidate_md.strip() == gold_md.strip() else 3
        verdict = {
            "overall_score": score,
            "text_fidelity": score,
            "reading_order": 5,
            "table_fidelity": None,
            "visual_fidelity": None,
            "critical_errors": [],
            "summary": "ok",
        }
        record = {
            "unit": unit_id,
            "model": model,
            "verdict": verdict,
            "error": None,
            "synthetic": False,
        }
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{unit_id}.json").write_text(json.dumps(record))
        return record

    monkeypatch.setattr(runner, "run_container", fake_container)
    monkeypatch.setattr(judge_mod, "judge_unit", fake_judge)
    image = Image(
        tag="abc", commit="abc", dirty=False, digest="sha256:x", built_at="", extractor_root=""
    )
    return {"case": case, "image": image, "runs_dir": runs_dir, "claims_dir": claims_dir}


def _run(env, label, **kw):
    return runner.execute(
        [env["case"]], ["balanced", "max"], env["image"], label=label, log=lambda _m: None, **kw
    )


def test_run_writes_scores_summary_and_metadata(env):
    run_dir = _run(env, "first", groups=["baseline"], perf_repeats=2)
    run = json.loads((run_dir / "run.json").read_text())
    assert run["status"] == "complete"
    summary = json.loads((run_dir / "summary.json").read_text())
    bal, mx = summary["balanced"]["metrics"], summary["max"]["metrics"]
    assert mx["text_similarity"] == pytest.approx(1.0)
    assert bal["required_phrase_recall"] == 0.0  # the OCR typo misses the footer phrase
    assert mx["judge_overall"] == 5 and bal["judge_overall"] == 3
    assert bal["false_native_rate"] == 1.0 and mx["false_native_rate"] == 0.0
    assert summary["max"]["perf"]["notice"]["n"] == 2
    assert (run_dir / "tier-max.json").exists()
    assert store.meta(run_dir.name) == {"name": "first", "groups": ["baseline"]}


def test_resume_skips_scored_pairs(env, monkeypatch):
    run_dir = _run(env, "resumable")
    calls = []
    monkeypatch.setattr(runner, "run_container", lambda *a, **k: calls.append(a))
    runner.execute(
        [env["case"]],
        ["balanced", "max"],
        env["image"],
        label="x",
        resume=run_dir.name,
        log=lambda _m: None,
    )
    assert calls == []


def test_store_rename_group_comment_delete(env):
    run_id = _run(env, "managed").name
    assert store.resolve("managed") == run_id
    store.rename(run_id, "Managed run")
    store.add_groups(run_id, ["prompt-v2", "baseline"])
    store.remove_groups(run_id, ["baseline"])
    assert store.meta(run_id) == {"name": "Managed run", "groups": ["prompt-v2"]}
    assert store.rename_group("prompt-v2", "prompt-v3") == 1
    assert store.groups() == {"prompt-v3": 1}

    c = store.add_comment(
        run_id, "footer typo", "ana", {"case": "notice", "unit": "p001", "tier": "balanced"}
    )
    store.update_comment(run_id, c["id"], "footer OCR typo")
    assert store.comments(run_id)[0]["text"] == "footer OCR typo"
    assert store.comments(run_id)[0]["target"] == {
        "case": "notice",
        "unit": "p001",
        "tier": "balanced",
    }
    store.delete_comment(run_id, c["id"])
    assert store.comments(run_id) == []

    [record] = store.list_runs(group="prompt-v3")
    assert record["name"] == "Managed run" and record["tiers"] == ["balanced", "max"]
    store.delete_run(run_id)
    assert store.list_runs() == []
    with pytest.raises(store.RunNotFound):
        store.resolve(run_id)


def test_store_rejects_path_traversal(env):
    with pytest.raises(store.RunNotFound):
        store.meta("../escape")
    with pytest.raises(store.RunNotFound):
        store.meta("claims:../../etc/passwd")


def test_compare_tiers_and_runs(env):
    a = _run(env, "a").name
    result = compare.compare([a])
    assert [c["tier"] for c in result["columns"]] == ["balanced", "max"]
    row = next(r for r in result["rows"] if r["key"] == "judge_overall")
    assert row["verdicts"] == ["same", "better"]
    assert result["units"][0]["key"] == "notice/p001"

    b = _run(env, "b").name
    both = compare.compare([a, b], tiers=["max"])
    assert len(both["columns"]) == 2
    assert all(v == "same" for r in both["rows"] for v in r["verdicts"])
    assert "Settings that differ" not in compare.console(both)  # identical setup


def _claims_file(env, stem, f1):
    data = {
        "kind": "claim-extractor",
        "label": stem,
        "timestamp": "20261125T150109Z",
        "model": "lite",
        "catalog": [{"key": "claim_f1", "label": "Claim F1", "better": "higher", "unit": "ratio"}],
        "summary": {"claim_f1": {"mean": f1, "std": 0.01}},
        "episodes": {
            "e1": {
                "tags": ["facts"],
                "rawText": "I live in Pune",
                "expected": {"claims": [], "entities": [], "mutations": []},
            }
        },
        "results": [
            {
                "episodeId": "e1",
                "run": 0,
                "latencyMs": 900,
                "result": {},
                "metrics": {"claims": {"f1": f1}, "entities": {"f1": 1}},
            }
        ],
    }
    (env["claims_dir"] / f"{stem}.json").write_text(json.dumps(data))
    return f"claims:{stem}"


def test_claims_runs_are_managed_and_compared(env):
    a, b = _claims_file(env, "old", 0.80), _claims_file(env, "new", 0.90)
    store.rename(a, "Lite baseline")
    store.add_comment(b, "better on facts")
    assert {r["name"] for r in store.list_runs(kind="claims")} == {"Lite baseline", "new"}
    result = compare.compare([a, b])
    assert result["kind"] == "claims"
    assert result["rows"][0]["verdicts"] == ["same", "better"]
    with pytest.raises(ValueError):
        compare.compare([a, _run(env, "x").name])


def test_http_api(env):
    from extractor_eval.web.server import app

    run_id = _run(env, "api").name
    client = TestClient(app)
    assert client.get("/api/runs").json()[0]["id"] == run_id
    assert client.patch(
        f"/api/runs/{run_id}", json={"name": "Renamed", "add_groups": ["g1"]}
    ).json()["groups"] == ["g1"]
    c = client.post(
        f"/api/runs/{run_id}/comments",
        json={"text": "hi", "author": "me", "target": {"case": "notice", "unit": "p001"}},
    ).json()
    assert client.get(f"/api/runs/{run_id}").json()["comments"][0]["id"] == c["id"]

    unit = client.get(f"/api/runs/{run_id}/units/notice/p001").json()
    assert unit["gold"] == GOLD
    assert set(unit["tiers"]) == {"balanced", "max"}
    assert "lndia" in unit["tiers"]["balanced"]["candidate"]
    assert unit["tiers"]["max"]["judge"]["verdict"]["overall_score"] == 5

    assert client.get("/api/cases/notice/pages/1.png").headers["content-type"] == "image/png"
    assert client.get(f"/api/compare?ids={run_id}").json()["columns"][1]["tier"] == "max"
    ops = client.post(
        "/api/diff", json={"a": "Government of India", "b": "Government of lndia"}
    ).json()
    assert ["-", "India"] in ops and ["+", "lndia"] in ops
    html = client.post(
        "/api/render", json={"markdown": "<script>alert(1)</script>\n\n**ok**"}
    ).json()["html"]
    assert "<script>" not in html and "<strong>ok</strong>" in html
    assert client.get("/api/runs/nope").status_code == 404
    assert client.delete(f"/api/runs/{run_id}").status_code == 200
    assert client.get("/").status_code == 200
