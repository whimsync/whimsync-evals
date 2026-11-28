"""Dataset editing, uploads, run/job submission (web API) and the matching CLI commands."""

import time

import pymupdf
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from extractor_eval import cli, jobs
from extractor_eval import dataset as ds
from extractor_eval.web import manage_api

GOLD = "# Title\n\nHello world.\n"


def _pdf(path, pages=2):
    doc = pymupdf.open()
    for i in range(pages):
        doc.new_page().insert_text((72, 72), f"Page {i + 1}")
    doc.save(path)


@pytest.fixture
def project(tmp_path, monkeypatch):
    project = tmp_path / "extractor"
    case_dir = project / "cases" / "doc"
    (case_dir / "expected" / "pages").mkdir(parents=True)
    _pdf(case_dir / "source.pdf")
    (case_dir / "expected" / "pages" / "001.md").write_text(GOLD)
    dataset_path = project / "dataset.json"
    monkeypatch.setattr(ds, "PROJECT_DIR", project)
    monkeypatch.setattr(ds, "DATASET_PATH", dataset_path)
    monkeypatch.setattr(manage_api, "UPLOAD_DIR", tmp_path / "uploads")
    case = ds.Case(
        id="doc",
        pdf="cases/doc/source.pdf",
        sha256=ds.sha256_file(case_dir / "source.pdf"),
        quality_mode="pages",
        suite="mine",
    )
    ds.save(ds.Dataset(cases=[case]), dataset_path)

    submitted = []

    class FakeManager:
        def submit(self, kind, title, args, *, links=None, cleanup=None):
            job = {"id": f"job{len(submitted)}", "kind": kind, "title": title, "argv": args}
            submitted.append(job | {"links": links or {}, "cleanup": cleanup or []})
            return job

        def active(self):
            return []

    monkeypatch.setattr(manage_api, "manager", lambda: FakeManager())
    monkeypatch.setattr(manage_api.docker_runner, "list_images", lambda: [object()])
    monkeypatch.setattr(manage_api, "gemini_api_key", lambda: "set")
    return {"dir": project, "submitted": submitted}


@pytest.fixture
def client(project):
    from extractor_eval.web.server import app

    return TestClient(app)


def test_edit_gold_labels_and_status(client, project):
    detail = client.get("/api/dataset/cases/doc").json()
    assert detail["page_count"] == 2 and detail["unit_list"][0]["gold"] == GOLD

    r = client.put("/api/dataset/cases/doc/gold/p001", json={"markdown": "# Title\n\nFixed."})
    assert r.status_code == 200
    assert (project["dir"] / "cases/doc/expected/pages/001.md").read_text() == "# Title\n\nFixed.\n"
    assert client.put("/api/dataset/cases/doc/gold/p009", json={"markdown": "x"}).status_code == 404

    label = client.put(
        "/api/dataset/cases/doc/labels/1",
        json={
            "expected_route": "vlm_required",
            "tags": ["scan", " "],
            "required_phrases": ["Fixed"],
        },
    ).json()
    assert label == {
        "expected_route": "vlm_required",
        "expected_classifier_reasons": [],
        "tags": ["scan"],
        "required_phrases": ["Fixed"],
    }
    # partial update keeps the other fields
    label = client.put(
        "/api/dataset/cases/doc/labels/1", json={"expected_classifier_reasons": ["scanned"]}
    ).json()
    assert label["expected_route"] == "vlm_required" and label["tags"] == ["scan"]
    bad = client.put(
        "/api/dataset/cases/doc/labels/1", json={"expected_classifier_reasons": ["chinese"]}
    )
    assert bad.status_code == 400
    assert client.put("/api/dataset/cases/doc/labels/7", json={}).status_code == 400

    patched = client.patch(
        "/api/dataset/cases/doc", json={"gold_status": "verified", "description": "d"}
    ).json()
    assert patched["gold_status"] == "verified" and patched["description"] == "d"
    assert client.patch("/api/dataset/cases/doc", json={"gold_status": "maybe"}).status_code == 400

    assert client.delete("/api/dataset/cases/doc").status_code == 200
    assert not (project["dir"] / "cases/doc").exists()
    assert client.get("/api/dataset").json()["cases"] == []


def test_upload_and_add_case_submits_cli_job(client, project, tmp_path):
    src = tmp_path / "Annual Report 2024.pdf"
    _pdf(src, pages=3)
    up = client.post(
        "/api/uploads",
        content=src.read_bytes(),
        headers={"X-Filename": "Annual%20Report%202024.pdf"},
    ).json()
    assert up["pages"] == 3 and up["suggested_id"] == "annual-report-2024"

    assert (
        client.post("/api/uploads", content=b"hello", headers={"X-Filename": "x.pdf"}).status_code
        == 400
    )
    assert client.post("/api/uploads", content=src.read_bytes()).status_code == 400  # no header

    body = {
        "upload": up["upload"],
        "id": "annual",
        "suite": "mine",
        "ranges": ["income:2-3"],
        "description": "--force",
    }
    job = client.post("/api/dataset/add", json=body).json()
    argv = project["submitted"][0]["argv"]
    assert argv[:2] == ["dataset", "add"] and argv[2].endswith(f"{up['upload']}.pdf")
    assert "--id=annual" in argv and "--range=income:2-3" in argv
    assert "--description=--force" in argv and "--force" not in argv  # values can't become options
    assert project["submitted"][0]["cleanup"] == [argv[2]]
    assert job["kind"] == "dataset-add"

    assert client.post("/api/dataset/add", json=body | {"id": "doc"}).status_code == 409
    assert client.post("/api/dataset/add", json=body | {"id": "Bad Id"}).status_code == 400
    assert client.post("/api/dataset/add", json=body | {"ranges": ["2-3"]}).status_code == 400


def test_start_run_builds_cli_arguments(client, project):
    job = client.post(
        "/api/runs/start",
        json={
            "cases": ["doc"],
            "tiers": ["max"],
            "label": "try",
            "groups": ["g"],
            "judge": False,
            "perf_repeats": 2,
        },
    ).json()
    assert job["argv"] == [
        "run",
        "--tiers=max",
        "--case=doc",
        "--label=try",
        "--group=g",
        "--no-judge",
        "--perf-repeats=2",
    ]
    assert client.post("/api/runs/start", json={"cases": ["nope"]}).status_code == 400
    assert (
        client.post("/api/runs/start", json={"cases": ["doc"], "tiers": ["fast"]}).status_code
        == 400
    )
    assert client.post("/api/runs/start", json={}).status_code == 400


def test_foreign_host_is_rejected(client):
    assert client.get("/api/dataset", headers={"host": "evil.example"}).status_code == 403
    assert client.get("/api/dataset", headers={"host": "127.0.0.1:8765"}).status_code == 200


def test_job_manager_runs_cli_and_records_status(tmp_path):
    manager = jobs.JobManager(tmp_path / "jobs")
    job = manager.submit("dataset", "help", ["dataset", "--help"])
    deadline = time.time() + 60
    while manager.get(job["id"])["status"] in jobs.ACTIVE and time.time() < deadline:
        time.sleep(0.2)
    done = manager.get(job["id"])
    assert done["status"] == "succeeded" and done["exit_code"] == 0
    assert "Inspect and grow dataset.json" in manager.log(job["id"])["text"]

    bad = manager.submit("x", "bad", ["no-such-command"])
    while manager.get(bad["id"])["status"] in jobs.ACTIVE and time.time() < deadline:
        time.sleep(0.2)
    assert manager.get(bad["id"])["status"] == "failed"

    # a fresh server marks jobs it can't drive as lost instead of live forever
    stale = manager.get(job["id"]) | {"id": "stale-1", "status": "running", "pid": None}
    manager._save(stale)
    jobs.JobManager(tmp_path / "jobs").recover()
    assert manager.get("stale-1")["status"] == "lost"
    with pytest.raises(jobs.JobNotFound):
        manager.get("../etc")


def test_cli_dataset_commands(project, tmp_path):
    runner = CliRunner()
    r = runner.invoke(
        cli.app,
        [
            "dataset",
            "label",
            "doc",
            "2",
            "--route",
            "native_acceptable",
            "--tags",
            "a,b",
            "--phrase",
            "Page 2",
        ],
    )
    assert r.exit_code == 0, r.output
    assert ds.load().get("doc").page_labels["2"].tags == ["a", "b"]
    assert (
        runner.invoke(cli.app, ["dataset", "label", "doc", "2", "--reasons", "nope"]).exit_code == 1
    )

    new_gold = tmp_path / "gold.md"
    new_gold.write_text("# New\n")
    assert (
        runner.invoke(
            cli.app, ["dataset", "gold", "doc", "p001", "--from", str(new_gold)]
        ).exit_code
        == 0
    )
    assert runner.invoke(cli.app, ["dataset", "gold", "doc", "p001"]).output == "# New\n"

    assert runner.invoke(cli.app, ["dataset", "edit", "doc", "--suite", "other"]).exit_code == 0
    shown = runner.invoke(cli.app, ["dataset", "show", "doc"]).output
    assert "suite=other" in shown and "native_acceptable" in shown

    assert runner.invoke(cli.app, ["dataset", "remove", "doc", "--yes"]).exit_code == 0
    assert ds.load().cases == []
