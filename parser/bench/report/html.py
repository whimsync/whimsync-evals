"""
Reusable HTML building blocks for report views.

Cell colours and delta signs are derived from the metric catalog and
`diff.judge`, so the HTML report and `bench.compare` always agree.
"""

import base64
import difflib
import html
from pathlib import Path

import pymupdf

from ..catalog import Catalog, MetricSpec
from ..diff import DEFAULT_THRESHOLD, judge

TIER_COLOURS = {"fast": "#e07b39", "smart": "#2f8fd0", "max": "#8a4fd0"}


def esc(v) -> str:
    return html.escape(str(v))


def score_style(spec: MetricSpec, v: float | None) -> str:
    """Red→green background for 0..1 scores (inverted when lower is better)."""
    if v is None or spec.unit != "ratio" or spec.better == "none":
        return ""
    x = max(0.0, min(1.0, v))
    if spec.better == "lower":
        x = 1 - x
    return f"background:hsla({int(x * 120)},70%,50%,.22)"


def score_cell(spec: MetricSpec, v: float | None, sd: float | None = None) -> str:
    shown = sd is not None and abs(sd) >= 0.0005
    extra = f" <span class='meta'>±{esc(spec.format(sd))}</span>" if shown else ""
    return f'<td class="score" style="{score_style(spec, v)}">{esc(spec.format(v))}{extra}</td>'


def delta_cell(
    catalog: Catalog,
    key: str,
    a: float | None,
    b: float | None,
    threshold: float = DEFAULT_THRESHOLD,
) -> str:
    if a is None or b is None:
        return "<td>–</td>"
    verdict = judge(key, a, b, catalog, threshold)
    cls = {"improved": "delta-pos", "regression": "delta-neg"}.get(verdict, "")
    d = b - a
    text = f"{d:+.0f}" if catalog.get(key).unit == "ms" else f"{d:+.3f}"
    return f'<td class="{cls}">{text}</td>'


def table(header_cells: list[str], rows: list[str], cls: str = "") -> str:
    head = "".join(f"<th>{h}</th>" for h in header_cells)
    return f'<div class="scroll"><table class="{cls}"><tr>{head}</tr>{"".join(rows)}</table></div>'


def thumbnail(pdf_path: str, width: int = 360) -> str | None:
    """Page 1 of a PDF as an inline JPEG data URI (None if the file is gone)."""
    p = Path(pdf_path)
    if not p.exists():
        return None
    doc = pymupdf.open(str(p))
    try:
        page = doc[0]
        z = width / page.rect.width
        data = page.get_pixmap(matrix=pymupdf.Matrix(z, z), alpha=False).tobytes(
            "jpeg", jpg_quality=72
        )
    finally:
        doc.close()
    return "data:image/jpeg;base64," + base64.b64encode(data).decode()


def word_diff(ref: str, pred: str) -> str:
    """Word-level inline diff: green = only in output, struck red = missing from output."""
    a, b = ref.split(), pred.split()
    out: list[str] = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            out.append(esc(" ".join(a[i1:i2])))
        if op in ("delete", "replace"):
            out.append(f'<span class="del">{esc(" ".join(a[i1:i2]))}</span>')
        if op in ("insert", "replace"):
            out.append(f'<span class="add">{esc(" ".join(b[j1:j2]))}</span>')
    return " ".join(out)


def scatter_svg(points: list[tuple[float, float, str]], x_label: str, y_label: str) -> str:
    """Small inline scatter plot: x >= 0, y in [0, 1], dots coloured by series (tier)."""
    if not points:
        return "<p class='meta'>No scanned/photo results to plot.</p>"
    w, h, pad_l, pad_b, pad_t, pad_r = 560, 260, 44, 34, 10, 12
    x_max = max(max(p[0] for p in points), 1.0)
    pw, ph = w - pad_l - pad_r, h - pad_t - pad_b

    def sx(x: float) -> float:
        return pad_l + x / x_max * pw

    def sy(y: float) -> float:
        return pad_t + (1 - y) * ph

    parts = [
        f'<svg width="{w}" height="{h}" role="img" aria-label="{esc(y_label)} by {esc(x_label)}">'
    ]
    for t in (0, 0.25, 0.5, 0.75, 1.0):
        parts.append(
            f'<line x1="{pad_l}" x2="{w - pad_r}" y1="{sy(t)}" y2="{sy(t)}" '
            'stroke="currentColor" stroke-opacity=".12"/>'
            f'<text x="{pad_l - 6}" y="{sy(t) + 4}" text-anchor="end">{t:.2f}</text>'
        )
    step = max(1, round(x_max / 5))
    x = 0
    while x <= x_max + 1e-9:
        parts.append(f'<text x="{sx(x)}" y="{h - pad_b + 16}" text-anchor="middle">{x:g}</text>')
        x += step
    parts.append(
        f'<text x="{pad_l + pw / 2}" y="{h - 4}" text-anchor="middle">{esc(x_label)}</text>'
    )
    for px, py, series in points:
        c = TIER_COLOURS.get(series, "#888")
        parts.append(
            f'<circle cx="{sx(px):.1f}" cy="{sy(py):.1f}" r="4" fill="{c}" fill-opacity=".75">'
            f"<title>{esc(series)}: {px:.2f} → {py:.3f}</title></circle>"
        )
    parts.append("</svg>")
    return "".join(parts)


def legend(series: list[str]) -> str:
    items = "".join(
        f'<span><i style="background:{TIER_COLOURS.get(s, "#888")}"></i>{esc(s)}</span>'
        for s in series
    )
    return f'<div class="legend">{items}</div>'
