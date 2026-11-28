"""
Everything the CLI can do besides reading results, exposed for the web UI:
dataset (upload PDF, edit gold and labels, mark verified, remove, synth, check),
images (list, build) and runs (start, resume, rescore), plus the job queue that
executes the long-running ones through the CLI itself (see jobs.py).
"""

import re
import uuid
from pathlib import Path
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import DEFAULT_EXTRACTOR_ROOT, RESULTS_DIR, TIERS, docker_runner, store
from .. import dataset as ds
from ..config import JUDGE_MODEL, gemini_api_key, read_json
from ..jobs import JobNotFound, manager

router = APIRouter(prefix="/api")
UPLOAD_DIR = RESULTS_DIR / "uploads"
MAX_UPLOAD_BYTES = 300 * 1024 * 1024
_ID = re.compile(ds.CASE_ID_RE)
_RANGE = re.compile(r"([a-z0-9][a-z0-9-]*):(\d+)-(\d+)")


def _case(case_id: str) -> ds.Case:
    try:
        return ds.load().get(case_id)
    except (KeyError, FileNotFoundError) as e:
        raise HTTPException(404, str(e)) from e


def _opt(name: str, value) -> str:
    """`--name=value`: a value starting with '-' can never be parsed as an option."""
    return f"--{name}={value}"


# ---- dataset ------------------------------------------------------------------------------


def _case_summary(case: ds.Case) -> dict:
    units = case.units()
    return {
        "id": case.id,
        "suite": case.suite,
        "description": case.description,
        "quality_mode": case.quality_mode,
        "gold_status": case.gold_status,
        "labels_status": case.labels_status,
        "units": len(units),
        "labelled_pages": len(case.page_labels),
        "vlm_required": sum(
            1 for lbl in case.page_labels.values() if lbl.expected_route == "vlm_required"
        ),
        "tags": sorted({t for lbl in case.page_labels.values() for t in lbl.tags}),
    }


@router.get("/dataset")
def get_dataset() -> dict:
    try:
        data = ds.load()
    except FileNotFoundError:
        return {"cases": [], "suites": {}, "reasons": sorted(ds.CLASSIFIER_REASONS)}
    suites: dict[str, int] = {}
    for c in data.cases:
        suites[c.suite] = suites.get(c.suite, 0) + 1
    return {
        "cases": [_case_summary(c) for c in data.cases],
        "suites": suites,
        "reasons": sorted(ds.CLASSIFIER_REASONS),
        "routes": ["vlm_required", "native_acceptable", "either_acceptable"],
    }


@router.get("/dataset/check")
def check_dataset() -> dict:
    data = ds.load()
    return {
        "problems": [p for c in data.cases for p in ds.problems(c)],
        "drafts": [c.id for c in data.cases if c.gold_status != "verified"],
        "cases": len(data.cases),
    }


@router.get("/dataset/cases/{case_id}")
def get_case(case_id: str) -> dict:
    case = _case(case_id)
    units = []
    for u in case.units():
        units.append(
            {
                "id": u.id,
                "pages": u.pages,
                "tags": u.tags,
                "required_phrases": u.required_phrases,
                "gold_file": str(u.gold_path.relative_to(case.dir.parent.parent)),
                "gold": u.gold_path.read_text(encoding="utf-8") if u.gold_path.exists() else "",
            }
        )
    return {
        **_case_summary(case),
        "pdf": case.pdf,
        "sha256": case.sha256,
        "page_count": ds.page_count(case),
        "page_labels": {k: v.model_dump() for k, v in case.page_labels.items()},
        "quality_ranges": [r.model_dump() for r in case.quality_ranges],
        "unit_list": units,
        "links": case.expected_links(),
        "problems": ds.problems(case),
    }


class CasePatch(BaseModel):
    suite: str | None = None
    description: str | None = None
    gold_status: str | None = None
    labels_status: str | None = None


@router.patch("/dataset/cases/{case_id}")
def patch_case(case_id: str, body: CasePatch) -> dict:
    _case(case_id)
    ds.update_case(case_id, **body.model_dump())
    return get_case(case_id)


class GoldIn(BaseModel):
    markdown: str


@router.put("/dataset/cases/{case_id}/gold/{unit_id}")
def put_gold(case_id: str, unit_id: str, body: GoldIn) -> dict:
    _case(case_id)
    try:
        path = ds.write_gold(case_id, unit_id, body.markdown)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    return {"saved": path.name, "sha256": ds.sha256_text(path.read_text(encoding="utf-8"))}


class LabelIn(BaseModel):
    expected_route: str | None = None
    expected_classifier_reasons: list[str] | None = None
    tags: list[str] | None = None
    required_phrases: list[str] | None = None


@router.put("/dataset/cases/{case_id}/labels/{page}")
def put_label(case_id: str, page: int, body: LabelIn) -> dict:
    _case(case_id)
    fields = body.model_dump()
    if fields["tags"] is not None:
        fields["tags"] = [t.strip() for t in fields["tags"] if t.strip()]
    if fields["required_phrases"] is not None:
        fields["required_phrases"] = [p.strip() for p in fields["required_phrases"] if p.strip()]
    return ds.set_label(case_id, page, **fields).model_dump()


@router.delete("/dataset/cases/{case_id}")
def delete_case(case_id: str) -> dict:
    _case(case_id)
    ds.remove_case(case_id)
    return {"deleted": case_id}


@router.post("/uploads")
async def upload(request: Request) -> dict:
    """Raw PDF body. Requires the X-Filename header (a custom header, so a cross-site
    page can't post here without a CORS preflight, which this server never grants)."""
    filename = unquote(request.headers.get("x-filename") or "")
    if not filename:
        raise HTTPException(400, "X-Filename header required")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    path = UPLOAD_DIR / f"{token}.pdf"
    size = 0
    with path.open("wb") as f:
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                f.close()
                path.unlink(missing_ok=True)
                raise HTTPException(413, f"file is larger than {MAX_UPLOAD_BYTES >> 20} MB")
            f.write(chunk)
    with path.open("rb") as f:
        if f.read(5) != b"%PDF-":
            path.unlink(missing_ok=True)
            raise HTTPException(400, "not a PDF file")
    try:
        import pymupdf

        with pymupdf.open(path) as doc:
            pages = len(doc)
    except Exception as e:  # noqa: BLE001 - any parse failure means an unusable upload
        path.unlink(missing_ok=True)
        raise HTTPException(400, f"cannot open PDF: {e}") from e
    suggested = re.sub(r"[^a-z0-9]+", "-", Path(filename).stem.lower()).strip("-")[:48]
    return {"upload": token, "pages": pages, "size": size, "suggested_id": suggested or "case"}


class AddCase(BaseModel):
    upload: str
    id: str
    suite: str = "core"
    description: str = ""
    ranges: list[str] = Field(default_factory=list)
    draft_tier: str = "max"
    image: str | None = None
    force: bool = False


@router.post("/dataset/add")
def add_case(body: AddCase) -> dict:
    if not re.fullmatch(r"[0-9a-f]{32}", body.upload):
        raise HTTPException(400, "bad upload token")
    path = UPLOAD_DIR / f"{body.upload}.pdf"
    if not path.exists():
        raise HTTPException(404, "upload not found (upload the PDF again)")
    if not _ID.fullmatch(body.id):
        raise HTTPException(400, "id must be lowercase letters, digits and dashes")
    for spec in body.ranges:
        if not _RANGE.fullmatch(spec.strip()):
            raise HTTPException(400, f"bad range '{spec}', expected id:START-END")
    if body.draft_tier not in (*TIERS, "none"):
        raise HTTPException(400, f"draft tier must be one of {[*TIERS, 'none']}")
    try:
        exists = any(c.id == body.id for c in ds.load().cases)
    except FileNotFoundError:
        exists = False
    if exists and not body.force:
        raise HTTPException(409, f"case {body.id} exists (tick 'replace existing')")
    args = [
        "dataset",
        "add",
        str(path),
        _opt("id", body.id),
        _opt("suite", body.suite.strip() or "core"),
    ]
    args += [_opt("description", body.description)] if body.description else []
    args += [_opt("range", r.strip()) for r in body.ranges]
    args += [_opt("draft-tier", body.draft_tier)]
    args += [_opt("image", body.image)] if body.image else []
    args += ["--force"] if body.force else []
    job = manager().submit(
        "dataset-add",
        f"Add case {body.id}",
        args,
        links={"case": body.id},
        cleanup=[str(path)],
    )
    return job


class SynthIn(BaseModel):
    templates: list[str] = Field(default_factory=list)
    links: bool = True


@router.post("/dataset/synth")
def synth(body: SynthIn) -> dict:
    args = ["dataset", "synth"]
    if body.templates:
        args.append(_opt("templates", ",".join(body.templates)))
    args.append("--links" if body.links else "--no-links")
    return manager().submit("dataset-synth", "Rebuild synthetic and link suites", args)


# ---- images ----------------------------------------------------------------------------------


@router.get("/images")
def images() -> dict:
    imgs = sorted(docker_runner.list_images(), key=lambda i: i.built_at, reverse=True)
    return {
        "images": [i.__dict__ | {"ref": i.ref} for i in imgs],
        "default_extractor_root": str(DEFAULT_EXTRACTOR_ROOT),
    }


class BuildIn(BaseModel):
    extractor_root: str | None = None
    tag: str | None = None


@router.post("/images/build")
def build(body: BuildIn) -> dict:
    args = ["image", "build"]
    if body.extractor_root:
        root = Path(body.extractor_root).expanduser()
        if not (root / "apps" / "extractor" / "Dockerfile.eval").exists():
            raise HTTPException(400, f"{root} has no apps/extractor/Dockerfile.eval")
        args.append(_opt("extractor-root", root))
    if body.tag:
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", body.tag):
            raise HTTPException(400, "invalid Docker tag")
        args.append(_opt("tag", body.tag))
    return manager().submit("image-build", "Build extractor image", args)


# ---- runs: start / resume / rescore ----------------------------------------------------------


class StartRun(BaseModel):
    cases: list[str] = Field(default_factory=list)
    suites: list[str] = Field(default_factory=list)
    tiers: list[str] = Field(default_factory=lambda: list(TIERS))
    image: str | None = None
    label: str | None = None
    groups: list[str] = Field(default_factory=list)
    judge: bool = True
    judge_model: str | None = None
    perf_repeats: int = Field(0, ge=0, le=10)
    model: str | None = None


@router.post("/runs/start")
def start_run(body: StartRun) -> dict:
    data = ds.load()
    known = {c.id for c in data.cases}
    unknown = [c for c in body.cases if c not in known]
    if unknown:
        raise HTTPException(400, f"unknown cases {unknown}")
    if not body.cases and not body.suites:
        raise HTTPException(400, "pick at least one case or suite")
    bad_tiers = set(body.tiers) - set(TIERS)
    if not body.tiers or bad_tiers:
        raise HTTPException(400, f"tiers must be a subset of {list(TIERS)}")
    if body.judge and not gemini_api_key():
        raise HTTPException(400, "GEMINI_API_KEY is not set in .env: turn the judge off")
    if not docker_runner.list_images() and not body.image:
        raise HTTPException(400, "no extractor image built yet: build one on the Setup page")
    args = ["run", _opt("tiers", ",".join(body.tiers))]
    args += [_opt("case", c) for c in body.cases]
    args += [_opt("suite", ",".join(body.suites))] if body.suites and not body.cases else []
    args += [_opt("image", body.image)] if body.image else []
    label = (body.label or "").strip() or "run"
    args += [_opt("label", label)]
    args += [_opt("group", g.strip()) for g in body.groups if g.strip()]
    args += ["--judge" if body.judge else "--no-judge"]
    if body.judge and body.judge_model:
        args.append(_opt("judge-model", body.judge_model))
    if body.perf_repeats:
        args.append(_opt("perf-repeats", body.perf_repeats))
    if body.model:
        args.append(_opt("model", body.model))
    scope = f"{len(body.cases)} case(s)" if body.cases else f"suite {','.join(body.suites)}"
    return manager().submit("run", f"Run '{label}' ({scope}, {'+'.join(body.tiers)})", args)


def _resolved(run_id: str) -> str:
    try:
        return store.resolve(run_id)
    except store.RunNotFound as e:
        raise HTTPException(404, str(e.args[0])) from e


def _busy(run_id: str) -> None:
    for job in manager().active():
        if job["links"].get("run") == run_id or run_id in " ".join(job["argv"]):
            raise HTTPException(409, f"job {job['id']} is already working on this run")


@router.post("/runs/{run_id}/resume")
def resume_run(run_id: str) -> dict:
    run_id = _resolved(run_id)
    if run_id.startswith("claims:"):
        raise HTTPException(400, "claims runs are started from the claims suite (bun run claims)")
    _busy(run_id)
    run = read_json(store.run_dir(run_id) / "run.json")
    args = ["run", _opt("resume", run_id)]
    args.append("--judge" if run["config"].get("judge") else "--no-judge")
    if run["config"].get("judge_model"):
        args.append(_opt("judge-model", run["config"]["judge_model"]))
    if run["config"].get("perf_repeats"):
        args.append(_opt("perf-repeats", run["config"]["perf_repeats"]))
    if run.get("image", {}).get("tag"):
        args.append(_opt("image", run["image"]["tag"]))
    return manager().submit(
        "run", f"Resume {store.meta(run_id)['name']}", args, links={"run": run_id}
    )


@router.post("/runs/{run_id}/rescore")
def rescore_run(run_id: str) -> dict:
    run_id = _resolved(run_id)
    if run_id.startswith("claims:"):
        raise HTTPException(400, "claims runs can't be rescored here")
    _busy(run_id)
    return manager().submit(
        "rescore",
        f"Rescore {store.meta(run_id)['name']}",
        ["rescore", run_id],
        links={"run": run_id},
    )


# ---- jobs --------------------------------------------------------------------------------------


@router.get("/jobs")
def list_jobs() -> list[dict]:
    return manager().all()


@router.get("/jobs/{job_id}")
def get_job(job_id: str, offset: int = 0) -> dict:
    try:
        return {"job": manager().get(job_id), "log": manager().log(job_id, offset)}
    except JobNotFound as e:
        raise HTTPException(404, f"no job {job_id}") from e


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict:
    try:
        return manager().cancel(job_id)
    except JobNotFound as e:
        raise HTTPException(404, f"no job {job_id}") from e


@router.get("/status")
def status() -> dict:
    active = manager().active()
    return {
        "active_jobs": len(active),
        "running": next((j for j in active if j["status"] == "running"), None),
        "gemini_key": bool(gemini_api_key()),
        "judge_model": JUDGE_MODEL,
        "tiers": list(TIERS),
        "images": len(docker_runner.list_images()),
    }
