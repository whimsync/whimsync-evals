"""
Results UI backend: a small JSON API over store.py / compare.py plus the static
single-page app in ./static. Local tool: binds to 127.0.0.1 by default.

Candidate and gold Markdown are untrusted text (parser output), so rendered views
use markdown-it with raw HTML disabled, and the frontend escapes everything else.
"""

import difflib
import re
import threading
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path

import pymupdf
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from markdown_it import MarkdownIt
from pydantic import BaseModel

from .. import RESULTS_DIR, jobs, store
from .. import compare as compare_mod
from .. import dataset as ds
from .. import metrics as catalog
from ..config import read_json
from .manage_api import router as manage_router

STATIC_DIR = Path(__file__).parent / "static"
PAGE_CACHE = RESULTS_DIR / ".page-cache"
_SAFE_MD = (
    MarkdownIt("commonmark", {"html": False, "linkify": False})
    .enable("table")
    .enable("strikethrough")
)
_render_lock = threading.Lock()
_BIND_HOST = "127.0.0.1"


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    jobs.manager().recover()
    yield


app = FastAPI(
    title="extractor-eval",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    lifespan=_lifespan,
)
app.include_router(manage_router)
_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]", "testserver"}


@app.middleware("http")
async def _local_only(request: Request, call_next):
    """The UI can start Docker jobs, so refuse requests whose Host isn't local
    (blocks DNS-rebinding pages from driving it through the browser)."""
    host = (request.headers.get("host") or "").rsplit(":", 1)[0].lower()
    if host not in _ALLOWED_HOSTS and host != _BIND_HOST:
        return JSONResponse({"detail": "forbidden host"}, status_code=403)
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.startswith("/static/"):
        # Revalidate every load (cheap: ETag -> 304) so an updated UI is never
        # hidden behind a heuristically cached old copy.
        response.headers["Cache-Control"] = "no-cache"
    return response


def _run_id(run_id: str) -> str:
    try:
        return store.resolve(run_id)
    except store.RunNotFound as e:
        raise HTTPException(404, str(e.args[0])) from e


@app.exception_handler(ValueError)
async def _value_error(_, exc: ValueError):
    return JSONResponse({"detail": str(exc)}, status_code=400)


# ---- catalog / runs ------------------------------------------------------------------


@app.get("/api/catalog")
def get_catalog() -> list[dict]:
    return [m.to_json() for m in catalog.CATALOG]


@app.get("/api/runs")
def list_runs(group: str | None = None, kind: str | None = None) -> list[dict]:
    return store.list_runs(group, kind)


@app.get("/api/groups")
def list_groups() -> dict[str, int]:
    return store.groups()


class GroupRename(BaseModel):
    old: str
    new: str


@app.post("/api/groups/rename")
def rename_group(body: GroupRename) -> dict:
    if not body.new.strip():
        raise HTTPException(400, "group name must not be empty")
    return {"changed": store.rename_group(body.old, body.new.strip())}


def _slim_unit(unit: dict) -> dict:
    return {k: unit[k] for k in ("id", "pages", "tags", "metrics", "candidate_chars", "judge")} | {
        "issues": unit["details"].get("markdown_issues", []),
        "missing_phrases": unit["details"].get("missing_phrases", []),
    }


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    run_id = _run_id(run_id)
    record = store.summary(run_id)
    out = {"record": record, "meta": store.meta(run_id), "comments": store.comments(run_id)}
    if record["kind"] == "claims":
        data = read_json(store._paths(run_id)[0])
        out["claims"] = data
        return out
    run_dir = store.run_dir(run_id)
    out["run"] = read_json(run_dir / "run.json")
    out["summary"] = read_json(run_dir / "summary.json", {})
    out["scores"] = {
        tier: [
            {k: v for k, v in s.items() if k != "units"}
            | {"units": [_slim_unit(u) for u in s["units"]]}
            for s in read_json(run_dir / f"tier-{tier}.json", []) or []
        ]
        for tier in out["run"]["config"]["tiers"]
    }
    return out


class RunPatch(BaseModel):
    name: str | None = None
    groups: list[str] | None = None
    add_groups: list[str] | None = None
    remove_groups: list[str] | None = None


@app.patch("/api/runs/{run_id}")
def patch_run(run_id: str, body: RunPatch) -> dict:
    run_id = _run_id(run_id)
    if body.name is not None:
        store.rename(run_id, body.name)
    if body.groups is not None:
        store.set_groups(run_id, body.groups)
    if body.add_groups:
        store.add_groups(run_id, body.add_groups)
    if body.remove_groups:
        store.remove_groups(run_id, body.remove_groups)
    return store.summary(run_id)


@app.delete("/api/runs/{run_id}")
def delete_run(run_id: str) -> dict:
    store.delete_run(_run_id(run_id))
    return {"deleted": run_id}


# ---- comments ---------------------------------------------------------------------------


class CommentIn(BaseModel):
    text: str
    author: str = ""
    target: dict | None = None


class CommentEdit(BaseModel):
    text: str


@app.post("/api/runs/{run_id}/comments")
def add_comment(run_id: str, body: CommentIn) -> dict:
    return store.add_comment(_run_id(run_id), body.text, body.author, body.target)


@app.patch("/api/runs/{run_id}/comments/{comment_id}")
def edit_comment(run_id: str, comment_id: str, body: CommentEdit) -> dict:
    try:
        return store.update_comment(_run_id(run_id), comment_id, body.text)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e


@app.delete("/api/runs/{run_id}/comments/{comment_id}")
def delete_comment(run_id: str, comment_id: str) -> dict:
    try:
        store.delete_comment(_run_id(run_id), comment_id)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    return {"deleted": comment_id}


# ---- unit detail -------------------------------------------------------------------------


def _unit_candidate(tier_dir: Path, pages: list[int]) -> str:
    parts = []
    for page in pages:
        path = tier_dir / "output" / "pages" / f"{page:03d}.md"
        if path.exists() and path.read_text(encoding="utf-8").strip():
            parts.append(path.read_text(encoding="utf-8").strip())
    return "\n\n".join(parts)


@app.get("/api/runs/{run_id}/units/{case_id}/{unit_id}")
def get_unit(run_id: str, case_id: str, unit_id: str) -> dict:
    run_id = _run_id(run_id)
    run_dir = store.run_dir(run_id)
    run = read_json(run_dir / "run.json")
    if case_id not in run["config"]["cases"]:
        raise HTTPException(404, f"case {case_id} not in run")
    tiers = {}
    pages: list[int] = []
    for tier in run["config"]["tiers"]:
        tier_dir = run_dir / "cases" / case_id / tier
        scores = read_json(tier_dir / "scores.json")
        if not scores:
            continue
        unit = next((u for u in scores["units"] if u["id"] == unit_id), None)
        if unit is None:
            continue
        pages = unit["pages"]
        trace = read_json(tier_dir / "output" / "trace.json", {}) or {}
        tiers[tier] = {
            "candidate": _unit_candidate(tier_dir, pages),
            "unit": unit,
            "judge": read_json(tier_dir / "judge" / f"{unit_id}.json"),
            "trace_pages": [p for p in trace.get("pages", []) if p["page_number"] in pages],
            "vlm_calls": [c for c in trace.get("vlm_calls", []) if c.get("page_number") in pages],
            "routing": [r for r in scores["routing"] if r["page"] in pages],
            "operational": scores["operational"],
        }
    if not tiers:
        raise HTTPException(404, f"unit {unit_id} not found")
    gold, gold_note = "", None
    try:
        case = ds.load().get(case_id)
        unit_def = next((u for u in case.units() if u.id == unit_id), None)
        if unit_def and unit_def.gold_path.exists():
            gold = unit_def.gold_path.read_text(encoding="utf-8")
            recorded = next(iter(tiers.values()))["unit"]["gold_sha256"]
            if ds.sha256_text(gold) != recorded:
                gold_note = "Gold has changed since this run was scored (showing the current file)."
        pdf_changed = case.sha256 != run["dataset"]["cases"].get(case_id, {}).get("pdf_sha256")
    except KeyError:
        gold_note, pdf_changed = "Case is no longer in dataset.json.", True
    return {
        "run": run_id,
        "case": case_id,
        "unit": unit_id,
        "pages": pages,
        "gold": gold,
        "gold_note": gold_note,
        "gold_status": run["dataset"]["cases"].get(case_id, {}).get("gold_status"),
        "pdf_changed": pdf_changed,
        "tiers": tiers,
    }


@app.get("/api/cases/{case_id}/pages/{page}.png")
def page_image(case_id: str, page: int, scale: float = 1.3) -> FileResponse:
    try:
        case = ds.load().get(case_id)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    scale = min(max(scale, 0.5), 3.0)
    path = PAGE_CACHE / f"{case.sha256[:16]}-{page}-{scale:.1f}.png"
    with _render_lock:
        if not path.exists():
            PAGE_CACHE.mkdir(parents=True, exist_ok=True)
            with pymupdf.open(case.pdf_path) as doc:
                if not 1 <= page <= len(doc):
                    raise HTTPException(404, "page out of range")
                doc[page - 1].get_pixmap(matrix=pymupdf.Matrix(scale, scale)).save(path)
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "max-age=86400"})


# ---- compare / diff / render ---------------------------------------------------------------


@app.get("/api/compare")
def get_compare(ids: str, tiers: str | None = None, scope: str = "all") -> dict:
    try:
        return compare_mod.compare(
            [i for i in ids.split(",") if i],
            [t for t in (tiers or "").split(",") if t] or None,
            scope,
        )
    except store.RunNotFound as e:
        raise HTTPException(404, str(e.args[0])) from e


class DiffIn(BaseModel):
    a: str
    b: str


_DIFF_TOKEN = re.compile(r"\s+|[^\s]+")


@app.post("/api/diff")
def diff(body: DiffIn) -> list[list[str]]:
    """Word-level diff of b against a: [[op, text], ...] with op in = + -."""
    a, b = _DIFF_TOKEN.findall(body.a), _DIFF_TOKEN.findall(body.b)
    ops: list[list[str]] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            ops.append(["=", "".join(a[i1:i2])])
        else:
            if i2 > i1:
                ops.append(["-", "".join(a[i1:i2])])
            if j2 > j1:
                ops.append(["+", "".join(b[j1:j2])])
    return ops


class RenderIn(BaseModel):
    markdown: str


@app.post("/api/render")
def render(body: RenderIn) -> dict:
    return {"html": _SAFE_MD.render(body.markdown)}


# ---- static app -----------------------------------------------------------------------------

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


def serve(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = False) -> None:
    import uvicorn

    global _BIND_HOST
    _BIND_HOST = host.lower()
    url = f"http://{host}:{port}/"
    print(f"extractor-eval UI: {url}")
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=host, port=port, log_level="warning")
