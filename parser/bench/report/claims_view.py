"""Report view for `claim-extractor` results: metrics per run (mean ± std) and episodes."""

import json

from .. import CLAIMS_FIXTURES_DIR
from ..catalog import Catalog
from ..kinds import ClaimsKind, ResultKind
from .html import delta_cell, esc, score_cell, score_style, table


def _episodes(run: dict) -> dict[str, dict]:
    """Episode inputs/expectations: embedded in the results file, else read from fixtures."""
    if run.get("episodes"):
        return run["episodes"]
    out: dict[str, dict] = {}
    for path in sorted(CLAIMS_FIXTURES_DIR.glob("*.json")):
        for e in json.loads(path.read_text(encoding="utf-8")):
            out[e["id"]] = e
    return out


def _summary(runs: list[dict], kind: ResultKind, catalog: Catalog) -> str:
    means = [kind.groups(r)[ClaimsKind.GROUP] for r in runs]
    stds = [kind.spread(r).get(ClaimsKind.GROUP, {}) for r in runs]
    keys = catalog.ordered({k for m in means for k in m})
    keys.sort(key=lambda k: k.startswith("tag:"))  # stable sort: per-tag slices last
    rows = []
    for k in keys:
        spec = catalog.get(k)
        cells = "".join(
            score_cell(spec, m.get(k), s.get(k)) for m, s in zip(means, stds, strict=True)
        )
        if len(runs) >= 2:
            cells += delta_cell(catalog, k, means[0].get(k), means[-1].get(k))
        rows.append(f"<tr><td>{esc(spec.label)}</td>{cells}</tr>")
    header = [
        "metric",
        *(f"{esc(r['label'])}<br><span class='meta'>{esc(kind.model(r))}</span>" for r in runs),
        *(["Δ last − first"] if len(runs) >= 2 else []),
    ]
    return table(header, rows)


def _claim_list(
    claims: list[dict],
    similarity: dict[int, float | None],
    empty: str,
    missed: str,
    missed_cls: str,
) -> str:
    """Claims with ✓ for matched ones (plus similarity when known)."""
    if not claims:
        return f"<span class='meta'>{empty}</span>"
    out = []
    for i, c in enumerate(claims):
        hit = i in similarity
        sim = f" · sim {similarity[i]:.2f}" if hit and similarity[i] is not None else ""
        out.append(
            f"<div class='{'' if hit else missed_cls}'>{'✓' if hit else missed} "
            f"{esc(c['content'])} <span class='meta'>[{esc(c['kind'])}{sim}]</span></div>"
        )
    return "".join(out)


def _episode_rows(run: dict, catalog: Catalog) -> list[str]:
    episodes = _episodes(run)
    fmt = lambda key, v: catalog.get(key).format(v)  # noqa: E731
    rows = []
    for r in run["results"]:
        if r.get("run", 0) != 0:
            continue
        ep = episodes.get(r["episodeId"], {})
        title = (
            f"<td><strong>{esc(r['episodeId'])}</strong>"
            f"<div class='meta'>{esc(ep.get('rawText', ''))}</div></td>"
        )
        if r.get("error"):
            rows.append(f"<tr>{title}<td colspan=3 class='err'>{esc(r['error'])}</td></tr>")
            continue
        m = r["metrics"]
        matches = m.get("claimMatches", [])
        expected = _claim_list(
            ep.get("expected", {}).get("claims", []),
            {x["refIndex"]: None for x in matches},
            "none expected",
            missed="✗",
            missed_cls="err",
        )
        predicted = _claim_list(
            (r.get("result") or {}).get("claims", []),
            {x["predIndex"]: x["similarity"] for x in matches},
            "none",
            missed="•",
            missed_cls="",
        )
        f1 = m["claims"]["f1"]
        stats = (
            f"F1 {f1:.2f}<br>mut {fmt('mutation_accuracy', m.get('mutationAccuracy'))}"
            f"<br>evid {fmt('evidence_validity', m.get('evidenceValidity'))}"
            f"<br>{r['latencyMs']:.0f} ms"
        )
        style = score_style(catalog.get("claim_f1"), f1)
        rows.append(
            f"<tr>{title}<td>{expected}</td><td>{predicted}</td><td style='{style}'>{stats}</td></tr>"
        )
    return rows


def render(runs: list[dict], kind: ResultKind) -> str:
    catalog = kind.catalog(runs[-1])
    run = runs[-1]
    return (
        "<h2>Summary (mean ± std over runs)</h2>"
        + _summary(runs, kind, catalog)
        + f"<h2>Episodes · {esc(run['label'])} (run 0)</h2>"
        + "<p class='meta'>✓ matched · ✗ expected but missed · • predicted, no match</p>"
        + table(
            ["episode", "expected claims", "predicted claims", "scores"],
            _episode_rows(run, catalog),
            cls="claims",
        )
    )
