"""
Results management shared by the CLI (`extractor-eval runs ...`) and the web UI.

Two kinds of runs are managed:

- `extractor`: a directory under results/runs/<id>/ (see runner.py);
- `claims`:    a claims-suite results file claims/results/<stem>.json, addressed as
               `claims:<stem>`; its editable metadata lives in sidecar files
               `<stem>.meta.json` and `<stem>.comments.json`.

Editable state is plain JSON next to the results (no database): `meta.json`
{name, groups} and `comments.json` [{id, text, author, target, created_at, ...}].
A comment's `target` pins it to the run (null) or to {case, tier, unit}.
"""

import shutil
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

from . import CLAIMS_RESULTS_DIR, RUNS_DIR
from .config import read_json, write_json

CLAIMS_PREFIX = "claims:"
HEADLINE = (
    "text_similarity",
    "judge_overall",
    "table_cell_accuracy",
    "false_native_rate",
    "duration_seconds",
)
CLAIMS_HEADLINE = (
    "claim_f1",
    "entity_f1",
    "mutation_accuracy",
    "noise_clean_rate",
    "latency_p50_ms",
)

_lock = threading.RLock()


class RunNotFound(KeyError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# ---- locating runs ---------------------------------------------------------------


def _claims_paths(run_id: str) -> tuple[Path, Path, Path]:
    stem = run_id[len(CLAIMS_PREFIX) :]
    if "/" in stem or "\\" in stem or stem.startswith("."):
        raise RunNotFound(run_id)
    base = CLAIMS_RESULTS_DIR / stem
    return base.with_suffix(".json"), Path(f"{base}.meta.json"), Path(f"{base}.comments.json")


def _extractor_dir(run_id: str) -> Path:
    if "/" in run_id or "\\" in run_id or run_id.startswith("."):
        raise RunNotFound(run_id)
    return RUNS_DIR / run_id


def kind_of(run_id: str) -> str:
    return "claims" if run_id.startswith(CLAIMS_PREFIX) else "extractor"


def _paths(run_id: str) -> tuple[Path, Path, Path]:
    """(results file or run.json, meta.json, comments.json)."""
    if kind_of(run_id) == "claims":
        paths = _claims_paths(run_id)
    else:
        d = _extractor_dir(run_id)
        paths = (d / "run.json", d / "meta.json", d / "comments.json")
    if not paths[0].exists():
        raise RunNotFound(run_id)
    return paths


def run_dir(run_id: str) -> Path:
    if kind_of(run_id) != "extractor":
        raise ValueError("only extractor runs have a run directory")
    _paths(run_id)
    return _extractor_dir(run_id)


def all_ids() -> list[str]:
    ids = []
    if RUNS_DIR.exists():
        ids += [p.name for p in RUNS_DIR.iterdir() if (p / "run.json").exists()]
    if CLAIMS_RESULTS_DIR.exists():
        ids += [
            CLAIMS_PREFIX + p.stem
            for p in CLAIMS_RESULTS_DIR.glob("*.json")
            if not p.name.endswith((".meta.json", ".comments.json"))
        ]
    return ids


def resolve(ref: str) -> str:
    """Accepts a full id, a unique id prefix, or a unique display name."""
    ids = all_ids()
    if ref in ids:
        return ref
    matches = [i for i in ids if i.startswith(ref) or i.removeprefix(CLAIMS_PREFIX).startswith(ref)]
    if len(matches) == 1:
        return matches[0]
    named = [i for i in ids if meta(i)["name"] == ref]
    if len(named) == 1:
        return named[0]
    if len(matches) > 1 or len(named) > 1:
        raise RunNotFound(f"'{ref}' is ambiguous: {', '.join(sorted(set(matches + named)))}")
    raise RunNotFound(f"no run matches '{ref}'")


# ---- reading ---------------------------------------------------------------------


def meta(run_id: str) -> dict:
    results, meta_path, _ = _paths(run_id)
    data = read_json(meta_path) or {}
    if "name" not in data:
        source = read_json(results) or {}
        data["name"] = source.get("label") or run_id
    data.setdefault("groups", [])
    return data


def comments(run_id: str) -> list[dict]:
    return read_json(_paths(run_id)[2], [])


def summary(run_id: str) -> dict:
    """The compact record used by run lists."""
    results, _, _ = _paths(run_id)
    m, c = meta(run_id), comments(run_id)
    if kind_of(run_id) == "claims":
        data = read_json(results)
        return {
            "id": run_id,
            "kind": "claims",
            "name": m["name"],
            "groups": m["groups"],
            "label": data.get("label"),
            "created_at": _claims_time(data.get("timestamp")),
            "status": "complete",
            "model": data.get("model"),
            "commit": data.get("gitSha"),
            "tiers": [],
            "cases": len(data.get("episodes") or {}),
            "runs": data.get("runs"),
            "headline": {
                "all": {
                    k: (data.get("summary", {}).get(k) or {}).get("mean") for k in CLAIMS_HEADLINE
                }
            },
            "comments": len(c),
            "draft_gold": False,
        }
    run = read_json(results)
    summ = read_json(results.parent / "summary.json", {}) or {}
    return {
        "id": run_id,
        "kind": "extractor",
        "name": m["name"],
        "groups": m["groups"],
        "label": run.get("label"),
        "created_at": run.get("created_at"),
        "status": run.get("status"),
        "model": (summ.get(next(iter(summ), ""), {}).get("fingerprint") or {}).get("model")
        or run["config"]["extraction"].get("gemini_model"),
        "judge_model": run["config"].get("judge_model"),
        "commit": run["image"]["tag"],
        "tiers": run["config"]["tiers"],
        "cases": len(run["config"]["cases"]),
        "headline": {t: {k: s["metrics"].get(k) for k in HEADLINE} for t, s in summ.items()},
        "comments": len(c),
        "draft_gold": any(
            v.get("gold_status") != "verified"
            for v in run.get("dataset", {}).get("cases", {}).values()
        ),
        "errors": len(run.get("errors", [])),
    }


def _claims_time(stamp: str | None) -> str | None:
    if not stamp:
        return None
    try:
        return (
            datetime.strptime(stamp, "%Y%m%dT%H%M%SZ")
            .replace(tzinfo=UTC)
            .isoformat(timespec="seconds")
        )
    except ValueError:
        return stamp


def list_runs(group: str | None = None, kind: str | None = None) -> list[dict]:
    runs = []
    for run_id in all_ids():
        try:
            record = summary(run_id)
        except (RunNotFound, KeyError, TypeError, ValueError):
            continue  # unreadable or half-written run: skip, don't break the list
        if group and group not in record["groups"]:
            continue
        if kind and record["kind"] != kind:
            continue
        runs.append(record)
    return sorted(runs, key=lambda r: r.get("created_at") or "", reverse=True)


def groups() -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in list_runs():
        for g in record["groups"]:
            counts[g] = counts.get(g, 0) + 1
    return dict(sorted(counts.items()))


# ---- editing ---------------------------------------------------------------------


def _write_meta(run_id: str, data: dict) -> dict:
    write_json(_paths(run_id)[1], {"name": data["name"], "groups": sorted(set(data["groups"]))})
    return meta(run_id)


def rename(run_id: str, name: str) -> dict:
    name = name.strip()
    if not name:
        raise ValueError("name must not be empty")
    with _lock:
        data = meta(run_id)
        data["name"] = name
        return _write_meta(run_id, data)


def set_groups(run_id: str, groups_: list[str]) -> dict:
    with _lock:
        data = meta(run_id)
        data["groups"] = [g.strip() for g in groups_ if g.strip()]
        return _write_meta(run_id, data)


def add_groups(run_id: str, groups_: list[str]) -> dict:
    with _lock:
        return set_groups(run_id, [*meta(run_id)["groups"], *groups_])


def remove_groups(run_id: str, groups_: list[str]) -> dict:
    with _lock:
        return set_groups(run_id, [g for g in meta(run_id)["groups"] if g not in set(groups_)])


def rename_group(old: str, new: str) -> int:
    """Renames a group on every run that has it; returns how many runs changed."""
    changed = 0
    with _lock:
        for run_id in all_ids():
            current = meta(run_id)["groups"]
            if old in current:
                set_groups(run_id, [new if g == old else g for g in current])
                changed += 1
    return changed


def _clean_target(target: dict | None) -> dict | None:
    if not target:
        return None
    clean = {
        k: str(v)
        for k, v in target.items()
        if k in ("case", "tier", "unit") and v not in (None, "")
    }
    return clean or None


def add_comment(run_id: str, text: str, author: str = "", target: dict | None = None) -> dict:
    text = text.strip()
    if not text:
        raise ValueError("comment must not be empty")
    comment = {
        "id": uuid.uuid4().hex[:10],
        "text": text,
        "author": author.strip() or "anonymous",
        "target": _clean_target(target),
        "created_at": _now(),
        "updated_at": None,
    }
    with _lock:
        items = comments(run_id)
        items.append(comment)
        write_json(_paths(run_id)[2], items)
    return comment


def update_comment(run_id: str, comment_id: str, text: str) -> dict:
    text = text.strip()
    if not text:
        raise ValueError("comment must not be empty")
    with _lock:
        items = comments(run_id)
        for item in items:
            if item["id"] == comment_id:
                item["text"] = text
                item["updated_at"] = _now()
                write_json(_paths(run_id)[2], items)
                return item
    raise KeyError(f"comment {comment_id} not found")


def delete_comment(run_id: str, comment_id: str) -> None:
    with _lock:
        items = comments(run_id)
        kept = [c for c in items if c["id"] != comment_id]
        if len(kept) == len(items):
            raise KeyError(f"comment {comment_id} not found")
        write_json(_paths(run_id)[2], kept)


def delete_run(run_id: str) -> None:
    """Permanently deletes a run's results (and, for claims, its sidecar files)."""
    with _lock:
        results, meta_path, comments_path = _paths(run_id)
        if kind_of(run_id) == "claims":
            for path in (results, meta_path, comments_path, results.with_suffix(".html")):
                path.unlink(missing_ok=True)
        else:
            shutil.rmtree(results.parent)
