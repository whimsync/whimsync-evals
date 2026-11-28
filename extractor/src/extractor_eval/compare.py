"""
Side-by-side comparison of runs (any number), shared by the CLI and the web UI.

Extractor runs compare column-wise: one column per (run, tier), so two tiers of one
run, one tier across runs, or everything at once all work. Each metric row carries a
verdict per column against the first (base) column. `consistency` lists every setting
that differs between columns (commit, image, models, prompts, render scale, dataset
and gold hashes): a score change should be explainable by one of them.

Claims runs compare one column per run using their embedded metric catalog.
"""

from . import metrics as catalog
from . import store
from .config import read_json

UNIT_METRICS = (
    "text_similarity",
    "numeric_recall",
    "table_cell_accuracy",
    "heading_f1",
    "judge_overall",
)


def _flatten(prefix: str, value, out: dict) -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            _flatten(f"{prefix}.{k}" if prefix else k, v, out)
    else:
        out[prefix] = value


def _differences(columns: list[dict]) -> list[dict]:
    flat = []
    for col in columns:
        out: dict = {}
        _flatten("", col["settings"], out)
        flat.append(out)
    keys = sorted({k for f in flat for k in f})
    return [
        {"key": k, "values": [f.get(k) for f in flat]}
        for k in keys
        if len({repr(f.get(k)) for f in flat}) > 1
    ]


def _extractor_columns(run_ids: list[str], tiers: list[str] | None, scope: str) -> list[dict]:
    columns = []
    for run_id in run_ids:
        run_dir = store.run_dir(run_id)
        run = read_json(run_dir / "run.json")
        summary = read_json(run_dir / "summary.json", {}) or {}
        name = store.meta(run_id)["name"]
        for tier, tier_summary in summary.items():
            if tiers and tier not in tiers:
                continue
            values = dict(tier_summary["metrics"])
            if scope == "verified":
                values.update(
                    tier_summary.get("metrics_verified")
                    or {
                        k: None
                        for k in values
                        if catalog.get(k).group in ("quality", "links", "judge")
                    }
                )
            scores = read_json(run_dir / f"tier-{tier}.json", []) or []
            columns.append(
                {
                    "run": run_id,
                    "name": name,
                    "tier": tier,
                    "label": f"{name} · {tier}",
                    "metrics": values,
                    "units": {
                        f"{s['case']}/{u['id']}": {k: u["metrics"].get(k) for k in UNIT_METRICS}
                        | {"case": s["case"], "unit": u["id"], "gold_status": s["gold_status"]}
                        for s in scores
                        for u in s["units"]
                    },
                    "settings": {
                        "run": {
                            "image": run["image"],
                            "judge_model": run["config"].get("judge_model"),
                            "judge_prompt_version": run["config"].get("judge_prompt_version"),
                            "judge_temperature": run["config"].get("judge_temperature"),
                            "extraction": run["config"].get("extraction"),
                            "dataset_sha256": run["dataset"]["sha256"],
                        },
                        "parser": tier_summary.get("fingerprint", {}),
                        "inputs": run["dataset"]["cases"],
                    },
                }
            )
    return columns


def compare(run_ids: list[str], tiers: list[str] | None = None, scope: str = "all") -> dict:
    run_ids = [store.resolve(r) for r in run_ids]
    kinds = {store.kind_of(r) for r in run_ids}
    if len(kinds) > 1:
        raise ValueError("cannot compare extractor runs with claims runs")
    if kinds == {"claims"}:
        return _compare_claims(run_ids)

    columns = _extractor_columns(run_ids, tiers, scope)
    if not columns:
        raise ValueError("nothing to compare (no completed tiers in these runs)")
    keys = [
        m.key for m in catalog.CATALOG if any(c["metrics"].get(m.key) is not None for c in columns)
    ]
    rows = []
    for key in keys:
        values = [c["metrics"].get(key) for c in columns]
        rows.append(
            {
                "key": key,
                **catalog.get(key).to_json(),
                "values": values,
                "verdicts": [catalog.verdict(key, values[0], v) for v in values],
            }
        )
    unit_keys = sorted({k for c in columns for k in c["units"]})
    units = [
        {
            "key": k,
            "case": k.split("/", 1)[0],
            "unit": k.split("/", 1)[1],
            "values": {m: [c["units"].get(k, {}).get(m) for c in columns] for m in UNIT_METRICS},
        }
        for k in unit_keys
    ]
    return {
        "kind": "extractor",
        "scope": scope,
        "columns": [{k: c[k] for k in ("run", "name", "tier", "label")} for c in columns],
        "rows": rows,
        "units": units,
        "unit_metrics": list(UNIT_METRICS),
        "consistency": _differences(columns),
    }


def _claims_verdict(spec: dict, base, other) -> str:
    if base is None or other is None:
        return "n/a"
    if spec.get("better", "none") == "none":
        return "same"
    delta = other - base
    if spec.get("tolerance") == "relative" or spec.get("unit") in ("ms", "s"):
        significant = abs(delta) > catalog.RELATIVE_TOLERANCE * max(abs(base), 1e-9)
    else:
        significant = abs(delta) > 0.02
    if not significant:
        return "same"
    return "better" if (delta > 0) == (spec["better"] == "higher") else "worse"


def _compare_claims(run_ids: list[str]) -> dict:
    data = [read_json(store._paths(r)[0]) for r in run_ids]
    specs: dict[str, dict] = {}
    for d in data:
        for spec in d.get("catalog", []):
            specs.setdefault(spec["key"], spec)
    keys = [k for k in specs if any(k in (d.get("summary") or {}) for d in data)]
    keys += sorted({k for d in data for k in (d.get("summary") or {}) if k not in specs})
    rows = []
    for key in keys:
        spec = specs.get(key, {"key": key, "label": key, "better": "none", "unit": "ratio"})
        values = [((d.get("summary") or {}).get(key) or {}).get("mean") for d in data]
        stds = [((d.get("summary") or {}).get(key) or {}).get("std") for d in data]
        rows.append(
            {
                "key": key,
                "label": spec.get("label", key),
                "group": "claims",
                "better": spec.get("better", "none"),
                "unit": spec.get("unit", "ratio"),
                "description": spec.get("description", ""),
                "values": values,
                "stds": stds,
                "verdicts": [_claims_verdict(spec, values[0], v) for v in values],
            }
        )
    episodes = sorted({e for d in data for e in (d.get("episodes") or {})})
    units = []
    for episode in episodes:
        f1 = []
        for d in data:
            vals = [
                r["metrics"]["claims"]["f1"]
                for r in d.get("results", [])
                if r["episodeId"] == episode and r.get("metrics")
            ]
            f1.append(sum(vals) / len(vals) if vals else None)
        units.append({"key": episode, "case": episode, "unit": "", "values": {"claim_f1": f1}})
    settings = [
        {
            k: d.get(k)
            for k in ("model", "embeddingModel", "simThreshold", "runs", "gitSha", "evalsSha")
        }
        for d in data
    ]
    return {
        "kind": "claims",
        "scope": "all",
        "columns": [
            {"run": r, "name": store.meta(r)["name"], "tier": "", "label": store.meta(r)["name"]}
            for r in run_ids
        ],
        "rows": rows,
        "units": units,
        "unit_metrics": ["claim_f1"],
        "consistency": _differences([{"settings": s} for s in settings]),
    }


def console(result: dict) -> str:
    cols = result["columns"]
    width = max([len(r["label"]) for r in result["rows"]] + [10]) + 2
    colw = max([len(c["label"]) for c in cols] + [12]) + 2
    marks = {"better": "+", "worse": "!", "same": " ", "n/a": " "}
    lines = [f"{'metric':<{width}}" + "".join(f"{c['label']:>{colw}}" for c in cols)]
    lines.append("-" * len(lines[0]))
    for row in result["rows"]:
        cells = []
        for v, verdict in zip(row["values"], row["verdicts"], strict=True):
            text = (
                catalog.fmt(row["key"], v)
                if result["kind"] == "extractor"
                else ("-" if v is None else f"{v:.3f}")
            )
            cells.append(f"{text}{marks[verdict]}".rjust(colw))
        lines.append(f"{row['label']:<{width}}" + "".join(cells))
    lines.append("\n(+ better / ! worse than the first column, beyond tolerance)")
    if result["consistency"]:
        lines.append("\nSettings that differ between columns:")
        for diff in result["consistency"][:40]:
            lines.append(f"  {diff['key']}: " + " | ".join(str(v) for v in diff["values"]))
    return "\n".join(lines)
