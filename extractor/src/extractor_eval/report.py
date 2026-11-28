"""
Aggregates per-(case, tier) scores into `summary.json` and prints console tables.

Quality is the macro mean over units. It is reported twice: over all units, and over
units whose gold is human-verified (`gold_status: verified`), because draft gold
(bootstrapped from a parser run) flatters the tier that produced it.
"""

from collections import defaultdict
from statistics import fmean, median

from . import metrics as catalog
from . import routing

QUALITY_GROUPS = ("quality", "links", "judge")
_QUALITY_KEYS = [m.key for m in catalog.CATALOG if m.group in QUALITY_GROUPS]


def _mean(values: list) -> float | None:
    values = [v for v in values if v is not None]
    return fmean(values) if values else None


def _quality(units: list[dict]) -> dict:
    return {k: _mean([u["metrics"].get(k) for u in units]) for k in _QUALITY_KEYS}


def _sum(values: list) -> float | None:
    values = [v for v in values if v is not None]
    return sum(values) if values else None


def summarize_tier(scores: list[dict], perf: dict[str, list[float]]) -> dict:
    units_all = [u | {"case": s["case"]} for s in scores for u in s["units"]]
    units_verified = [
        u | {"case": s["case"]}
        for s in scores
        if s["gold_status"] == "verified"
        for u in s["units"]
    ]

    by_tag: dict[str, list[dict]] = defaultdict(list)
    for unit in units_all:
        for tag in unit["tags"]:
            by_tag[tag].append(unit)
    by_suite: dict[str, list[dict]] = defaultdict(list)
    for s in scores:
        by_suite[s["suite"]].extend(s["units"])

    ops = [s["operational"] for s in scores]
    total_pages = _sum([o["total_pages"] for o in ops]) or 0
    total_duration = _sum([o["duration_seconds"] for o in ops])
    operational = {
        "success_rate": sum(o["status"] == "success" for o in ops) / len(ops) if ops else None,
        "duration_seconds": total_duration,
        "seconds_per_page": total_duration / total_pages
        if total_duration is not None and total_pages
        else None,
        "vlm_page_ratio": (_sum([o["vlm_page_count"] for o in ops]) or 0) / total_pages
        if total_pages
        else None,
        "vlm_failures": _sum([o["vlm_failures"] for o in ops]),
        "retries": _sum([o["retries"] for o in ops]),
        "input_tokens": _sum([o["input_tokens"] for o in ops]),
        "output_tokens": _sum([o["output_tokens"] for o in ops]),
        "cost_usd": _sum([o["cost_usd"] for o in ops]),
        "output_chars": _sum([o["output_chars"] for o in ops]),
    }

    perf_by_case = {
        case: {"n": len(d), "median": median(d), "min": min(d), "max": max(d)}
        for case, d in perf.items()
        if d
    }
    perf_totals = {
        "perf_median_seconds": _sum([p["median"] for p in perf_by_case.values()]),
        "perf_min_seconds": _sum([p["min"] for p in perf_by_case.values()]),
        "perf_max_seconds": _sum([p["max"] for p in perf_by_case.values()]),
    }

    route = routing.aggregate([o for s in scores for o in s["routing"]])
    judged = [u["judge"] for u in units_all if u.get("judge")]
    quality_all = _quality(units_all)
    return {
        "cases": len(scores),
        "units": {"all": len(units_all), "verified": len(units_verified)},
        "metrics": {
            **quality_all,
            **{
                k: route[k]
                for k in (
                    "routing_precision",
                    "routing_recall",
                    "false_native_rate",
                    "false_vlm_rate",
                    "reason_agreement",
                )
            },
            **operational,
            **perf_totals,
        },
        "metrics_verified": _quality(units_verified) if units_verified else {},
        "by_case": {
            s["case"]: {
                "gold_status": s["gold_status"],
                "suite": s["suite"],
                "status": s["operational"]["status"],
                **_quality(s["units"]),
                "duration_seconds": s["operational"]["duration_seconds"],
                "vlm_page_ratio": s["operational"]["vlm_page_ratio"],
            }
            for s in scores
        },
        "by_tag": {
            tag: _quality(units) | {"units": len(units)} for tag, units in sorted(by_tag.items())
        },
        "by_suite": {
            suite: _quality(units) | {"units": len(units)}
            for suite, units in sorted(by_suite.items())
        },
        "routing": route,
        "operational_by_case": {s["case"]: s["operational"] for s in scores},
        "perf": perf_by_case,
        "judge": {
            "judged": len(judged),
            "errors": sum(bool(j.get("error")) for j in judged),
            "synthetic": sum(bool(j.get("synthetic")) for j in judged),
        },
        "fingerprint": next(
            (s["fingerprint"] for s in scores if s["fingerprint"].get("model")), {}
        ),
    }


def summarize(
    scores_by_tier: dict[str, list[dict]], perf_by_tier: dict[str, dict[str, list[float]]]
) -> dict:
    return {
        tier: summarize_tier(scores, perf_by_tier.get(tier, {}))
        for tier, scores in scores_by_tier.items()
    }


def console_table(summary: dict, keys: list[str] | None = None) -> str:
    keys = keys or [m.key for m in catalog.CATALOG if m.headline]
    tiers = list(summary)
    width = max(len(catalog.get(k).label) for k in keys) + 2
    lines = [f"{'metric':<{width}}" + "".join(f"{t:>14}" for t in tiers)]
    lines.append("-" * len(lines[0]))
    for key in keys:
        row = f"{catalog.get(key).label:<{width}}"
        for tier in tiers:
            row += f"{catalog.fmt(key, summary[tier]['metrics'].get(key)):>14}"
        lines.append(row)
    return "\n".join(lines)
