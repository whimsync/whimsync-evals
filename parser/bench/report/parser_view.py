"""Report view for `document-parser` results: summary, deltas, degradation plots, documents."""

import json
from pathlib import Path

from ..catalog import Catalog
from ..kinds import ResultKind
from .html import (
    delta_cell,
    esc,
    legend,
    scatter_svg,
    score_cell,
    score_style,
    table,
    thumbnail,
    word_diff,
)


def _metric_keys(runs: list[dict], kind: ResultKind, catalog: Catalog) -> list[str]:
    keys = {k for run in runs for g in kind.groups(run).values() for k in g}
    return catalog.ordered(keys)


def _summary(run: dict, kind: ResultKind, catalog: Catalog, keys: list[str]) -> str:
    header = ["tier / mode", "n", *(esc(catalog.get(k).label) for k in keys)]
    rows = []
    for group, vals in run["summary"].items():
        cells = "".join(score_cell(catalog.get(k), vals.get(k)) for k in keys)
        rows.append(f"<tr><td>{esc(group)}</td><td>{vals.get('n', '')}</td>{cells}</tr>")
    return table(header, rows)


def _changes(a: dict, b: dict, kind: ResultKind, catalog: Catalog, keys: list[str]) -> str:
    ga, gb = kind.groups(a), kind.groups(b)
    common = [g for g in ga if g in gb]
    if not common:
        return "<p class='meta'>No common groups to compare.</p>"
    rows = [
        f"<tr><td>{esc(g)}</td>"
        + "".join(delta_cell(catalog, k, ga[g].get(k), gb[g].get(k)) for k in keys)
        + "</tr>"
        for g in common
    ]
    return table(["group (B − A)", *(esc(catalog.get(k).label) for k in keys)], rows)


def _degradation(run: dict) -> str:
    angle, jpeg = [], []
    for r in run["results"]:
        aug = r.get("augment")
        if r.get("error") or not aug:
            continue
        sim = r["metrics"]["text_similarity"]
        angle.append((abs(aug.get("angle_deg", 0.0)), sim, r["tier"]))
        jpeg.append((100 - aug.get("jpeg_quality", 100), sim, r["tier"]))
    return (
        legend(sorted({p[2] for p in angle}))
        + '<div style="display:flex;gap:24px;flex-wrap:wrap">'
        + f"<div>{scatter_svg(angle, '|rotation| (degrees)', 'text similarity')}</div>"
        + f"<div>{scatter_svg(jpeg, 'JPEG compression (100 − quality)', 'text similarity')}</div>"
        + "</div>"
    )


def _augment_text(aug: dict) -> str:
    if not aug:
        return "digital (no augmentation)"
    return (
        f"angle {aug.get('angle_deg', 0):+.1f}° · persp {aug.get('perspective', 0):.3f} · "
        f"jpeg {aug.get('jpeg_quality', '–')} · blur {aug.get('blur_sigma', 0)}"
        + (" · fold" if aug.get("fold_line") else "")
    )


def _card(dlg_id: str, doc_id: str, rows: list[dict], catalog: Catalog) -> str:
    first = rows[0]
    sim_spec = catalog.get("text_similarity")
    thumb = thumbnail(first["pdf_path"]) if first.get("pdf_path") else None
    img = f'<img loading="lazy" src="{thumb}" alt="page 1 of {esc(doc_id)}">' if thumb else ""
    chips, sims = [], []
    for r in rows:
        if r.get("error"):
            chips.append(f'<span class="chip err">{esc(r["tier"])}: error</span>')
            continue
        v = r["metrics"]["text_similarity"]
        sims.append(v)
        chips.append(
            f'<span class="chip" style="{score_style(sim_spec, v)}">{esc(r["tier"])} {v:.2f}</span>'
        )
    return (
        f'<div class="card" data-mode="{esc(first["mode"])}" data-case="{esc(first["case"])}" '
        f'data-score="{min(sims) if sims else 0:.4f}" '
        f"onclick=\"document.getElementById('{dlg_id}').showModal()\">"
        f'{img}<div class="body"><div class="title">{esc(doc_id)}</div>'
        f'<div class="aug">{esc(_augment_text(first.get("augment") or {}))}</div>'
        f'<div class="chips">{"".join(chips)}</div></div></div>'
    )


def _tier_pane(r: dict, ground_truth: str, catalog: Catalog) -> str:
    if r.get("error"):
        return f'<p class="err">{esc(r["error"])}</p>'
    m = r["metrics"]
    metric_rows = "".join(
        f"<tr><td>{esc(catalog.get(k).label)}</td>{score_cell(catalog.get(k), m.get(k))}</tr>"
        for k in catalog.ordered(m)
    )
    out = r.get("output")
    if out is None:
        body = "<p class='meta'>Output not stored (run used --no-output).</p>"
    else:
        body = (
            f'<div class="cols"><div><h3>Ground truth</h3><pre>{esc(ground_truth)}</pre></div>'
            f"<div><h3>Parser output</h3><pre>{esc(out)}</pre></div></div>"
            '<h3>Word diff (<span class="del">missing</span> / <span class="add">extra</span>)</h3>'
            f'<pre class="diff">{word_diff(ground_truth, out)}</pre>'
        )
    return (
        f'<p class="meta">{r["seconds"]:.2f}s · {r.get("pages", "?")} page(s) · '
        f"VLM pages {r.get('vlm_pages', 0)} · VLM failures {r.get('vlm_failures', 0)}</p>"
        f"<table>{metric_rows}</table>{body}"
    )


def _dialog(dlg_id: str, doc_id: str, rows: list[dict], catalog: Catalog) -> str:
    gt_path = rows[0].get("gt_path")
    gt = Path(gt_path).read_text(encoding="utf-8") if gt_path and Path(gt_path).exists() else ""
    tabs, panes = [], []
    for j, r in enumerate(rows):
        pane = f"{dlg_id}_t{j}"
        tabs.append(
            f'<button class="{"on" if j == 0 else ""}" '
            f"onclick=\"showPane(this,'{pane}')\">{esc(r['tier'])}</button>"
        )
        hidden = "" if j == 0 else "display:none"
        panes.append(
            f'<div id="{pane}" class="pane" style="{hidden}">{_tier_pane(r, gt, catalog)}</div>'
        )
    aug = rows[0].get("augment") or {}
    pdf = rows[0].get("pdf_path")
    pdf_link = f'<a href="file://{esc(pdf)}" target="_blank">open PDF</a>' if pdf else ""
    aug_json = (
        "<details><summary class='meta'>augmentation parameters</summary>"
        f"<pre>{esc(json.dumps(aug, indent=2))}</pre></details>"
        if aug
        else ""
    )
    return (
        f'<dialog id="{dlg_id}"><div class="dlg-head"><div><strong>{esc(doc_id)}</strong> '
        f'<span class="meta">{pdf_link}</span></div>'
        "<button onclick=\"this.closest('dialog').close()\">Close</button></div>"
        f'<div class="dlg-body">{aug_json}<div class="tabs">{"".join(tabs)}</div>'
        f"{''.join(panes)}</div></dialog>"
    )


def _documents(run: dict, run_idx: int, catalog: Catalog) -> str:
    by_doc: dict[str, list[dict]] = {}
    for r in run["results"]:
        by_doc.setdefault(r["doc_id"], []).append(r)
    cards, dialogs = [], []
    for i, (doc_id, rows) in enumerate(sorted(by_doc.items())):
        rows = sorted(rows, key=lambda x: x["tier"])
        dlg_id = f"d{run_idx}_{i}"
        cards.append(_card(dlg_id, doc_id, rows, catalog))
        dialogs.append(_dialog(dlg_id, doc_id, rows, catalog))

    def options(values: set[str]) -> str:
        return "".join(f'<option value="{esc(v)}">{esc(v)}</option>' for v in sorted(values))

    return (
        '<div class="filters">'
        '<select id="f-mode" onchange="filterCards()"><option value="">all modes</option>'
        f"{options({r['mode'] for r in run['results']})}</select>"
        '<select id="f-case" onchange="filterCards()"><option value="">all cases</option>'
        f"{options({r['case'] for r in run['results']})}</select>"
        '<select id="f-sort" onchange="sortCards()"><option value="name">sort: name</option>'
        '<option value="worst">sort: worst score first</option></select>'
        '<span class="meta">Chips show text similarity per tier. '
        "Click a card for output, diff and metrics.</span>"
        f'</div><div class="grid" id="cards">{"".join(cards)}</div>{"".join(dialogs)}'
    )


def render(runs: list[dict], kind: ResultKind) -> str:
    catalog = kind.catalog(runs[-1])
    keys = _metric_keys(runs, kind, catalog)
    parts = [
        f"<h2>Summary · {esc(r['label'])}</h2>{_summary(r, kind, catalog, keys)}" for r in runs
    ]
    if len(runs) >= 2:
        a, b = runs[0], runs[-1]
        parts.append(f"<h2>Change: {esc(a['label'])} → {esc(b['label'])}</h2>")
        parts.append(_changes(a, b, kind, catalog, keys))
    run = runs[-1]
    parts += [
        f"<h2>Accuracy vs. scan degradation · {esc(run['label'])}</h2>",
        "<p class='meta'>Each dot is one parsed document. Digital documents are excluded.</p>",
        _degradation(run),
        f"<h2>Documents · {esc(run['label'])}</h2>",
        _documents(run, len(runs) - 1, catalog),
    ]
    return "".join(parts)
