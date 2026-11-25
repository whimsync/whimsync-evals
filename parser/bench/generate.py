"""
Renders cases into PDFs: one digital PDF per case plus N randomized OpenCV-augmented
variants per scan mode (one mode per profile in `augment.PROFILES`).

Output is cached and deterministic: (case, mode, variant, seed) always yields the
same file, so repeated runs reuse the corpus and stay comparable.

    uv run python -m bench.generate --variants 3 --previews   # writes parser/results/corpus/
"""

import argparse
import html
import json
import re
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import pymupdf

from . import CORPUS_DIR
from .augment import DIGITAL, augment, params_summary, parse_modes, sample_params
from .cases import BenchCase, get_cases

PAGE_RECT = pymupdf.paper_rect("a4")
MARGIN = 54
GUTTER = 24
SCAN_DPI = 150

CSS = """
body { font-family: sans-serif; font-size: 11pt; line-height: 1.35; }
h1 { font-size: 20pt; font-weight: bold; margin-bottom: 8pt; }
h2 { font-size: 15pt; font-weight: bold; margin-top: 10pt; margin-bottom: 6pt; }
h3 { font-size: 12.5pt; font-weight: bold; margin-top: 8pt; margin-bottom: 4pt; }
p { margin-bottom: 6pt; }
table { border-collapse: collapse; margin-bottom: 8pt; }
th, td { border: 1px solid #444; padding: 3pt 5pt; }
th { font-weight: bold; background-color: #e8e8e8; }
a { color: #1a4fb4; text-decoration: underline; }
"""

_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def _inline(text: str) -> str:
    out, last = [], 0
    for m in _LINK_RE.finditer(text):
        out.append(html.escape(text[last : m.start()]))
        out.append(f'<a href="{html.escape(m.group(2))}">{html.escape(m.group(1))}</a>')
        last = m.end()
    out.append(html.escape(text[last:]))
    return "".join(out)


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def markdown_to_html(md: str) -> str:
    """Minimal Markdown -> HTML covering the constructs used in bench cases."""
    lines = md.splitlines()
    parts: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        if m := re.match(r"^(#{1,6})\s+(.*)$", stripped):
            level = len(m.group(1))
            parts.append(f"<h{level}>{_inline(m.group(2))}</h{level}>")
            i += 1
        elif stripped.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(lines[i])
                i += 1
            header, body = _cells(rows[0]), [r for r in rows[1:] if not _TABLE_SEP_RE.match(r)]
            t = ["<table><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in header) + "</tr>"]
            for r in body:
                t.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in _cells(r)) + "</tr>")
            t.append("</table>")
            parts.append("".join(t))
        elif re.match(r"^([-*+]|\d+[.)])\s+", stripped):
            ordered = bool(re.match(r"^\d", stripped))
            tag = "ol" if ordered else "ul"
            items = []
            while i < len(lines) and re.match(r"^\s*([-*+]|\d+[.)])\s+", lines[i]):
                items.append(re.sub(r"^\s*([-*+]|\d+[.)])\s+", "", lines[i]).strip())
                i += 1
            parts.append(
                f"<{tag}>" + "".join(f"<li>{_inline(x)}</li>" for x in items) + f"</{tag}>"
            )
        else:
            para = []
            while (
                i < len(lines)
                and lines[i].strip()
                and not re.match(r"^(#{1,6}\s|\||([-*+]|\d+[.)])\s)", lines[i].strip())
            ):
                para.append(lines[i].strip())
                i += 1
            parts.append(f"<p>{_inline(' '.join(para))}</p>")
    return "\n".join(parts)


def _column_rects(columns: int) -> list[pymupdf.Rect]:
    inner = PAGE_RECT + (MARGIN, MARGIN, -MARGIN, -MARGIN)
    if columns <= 1:
        return [inner]
    width = (inner.width - GUTTER * (columns - 1)) / columns
    return [
        pymupdf.Rect(
            inner.x0 + k * (width + GUTTER),
            inner.y0,
            inner.x0 + k * (width + GUTTER) + width,
            inner.y1,
        )
        for k in range(columns)
    ]


def render_digital(case: BenchCase, out_path: Path) -> None:
    story = pymupdf.Story(html=markdown_to_html(case.markdown), user_css=CSS)
    rects = _column_rects(case.columns)

    def rectfn(rect_num: int, _filled: pymupdf.Rect):
        # A non-None mediabox starts a new page, so only return it for the first column.
        col = rect_num % len(rects)
        return (PAGE_RECT if col == 0 else None), rects[col], None

    # write_with_links (unlike DocumentWriter) preserves <a href> as PDF link annotations
    doc = story.write_with_links(rectfn)
    try:
        doc.save(str(out_path))
    finally:
        doc.close()


def rasterize(digital_pdf: Path, out_path: Path, profile: str, seed: int) -> dict:
    """
    Renders every page of `digital_pdf` to pixels, applies OpenCV scan effects and
    writes an image-only PDF. Returns the per-page augmentation parameters.
    """
    src = pymupdf.open(str(digital_pdf))
    dst = pymupdf.open()
    pages: list[dict] = []
    try:
        for i, page in enumerate(src):
            pix = page.get_pixmap(dpi=SCAN_DPI, colorspace=pymupdf.csRGB, alpha=False)
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
            params = sample_params(profile, seed + i * 7919)
            out = augment(img, params)
            ok, buf = cv2.imencode(
                ".jpg", cv2.cvtColor(out, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 92]
            )
            if not ok:
                raise RuntimeError(f"Failed to encode augmented page {i + 1}")
            # Keep the image aspect (borders may have been added) while staying page-sized.
            oh, ow = out.shape[:2]
            new_page = dst.new_page(width=page.rect.width, height=page.rect.width * oh / ow)
            new_page.insert_image(new_page.rect, stream=buf.tobytes())
            pages.append(params)
        dst.save(str(out_path), deflate=True)
    finally:
        src.close()
        dst.close()
    return {"pages": pages, **params_summary(pages[0] if pages else {})}


@dataclass(frozen=True)
class RenderedDoc:
    case: str
    mode: str
    variant: int
    pdf_path: Path
    md_path: Path
    augment: dict | None

    @property
    def doc_id(self) -> str:
        return self.pdf_path.stem


def variant_seed(case_name: str, mode: str, variant: int, base_seed: int = 0) -> int:
    return zlib.crc32(f"{case_name}:{mode}:{variant}".encode()) + base_seed


def render_case(
    case: BenchCase,
    mode: str,
    out_dir: Path,
    variant: int = 0,
    base_seed: int = 0,
) -> RenderedDoc:
    """
    Writes the ground truth `<case>.md` and the PDF for (case, mode, variant), reusing
    files that already exist. Digital PDFs have a single variant; each scan variant
    gets its own seeded random draw, stored next to the PDF as `<pdf>.json`.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"{case.name}.md"
    md_path.write_text(case.markdown, encoding="utf-8")

    digital_path = out_dir / f"{case.name}__{DIGITAL}.pdf"
    if not digital_path.exists():
        render_digital(case, digital_path)
    if mode == DIGITAL:
        return RenderedDoc(case.name, mode, 0, digital_path, md_path, None)

    pdf_path = out_dir / f"{case.name}__{mode}__v{variant}.pdf"
    params_path = pdf_path.with_suffix(".json")
    if pdf_path.exists() and params_path.exists():
        params = json.loads(params_path.read_text(encoding="utf-8"))
    else:
        params = rasterize(
            digital_path, pdf_path, mode, variant_seed(case.name, mode, variant, base_seed)
        )
        params_path.write_text(json.dumps(params, indent=2), encoding="utf-8")
    return RenderedDoc(case.name, mode, variant, pdf_path, md_path, params)


def generate_corpus(
    cases: list[BenchCase],
    modes: list[str],
    out_dir: Path,
    variants: int = 1,
    base_seed: int = 0,
) -> list[RenderedDoc]:
    docs: list[RenderedDoc] = []
    for case in cases:
        for mode in modes:
            n = 1 if mode == DIGITAL else variants
            docs.extend(render_case(case, mode, out_dir, v, base_seed) for v in range(n))
    return docs


def write_preview(pdf_path: Path, out_png: Path, width: int = 900) -> Path:
    """Renders page 1 of a PDF to a PNG for eyeballing the generated documents."""
    doc = pymupdf.open(str(pdf_path))
    try:
        page = doc[0]
        pix = page.get_pixmap(
            matrix=pymupdf.Matrix(width / page.rect.width, width / page.rect.width)
        )
        pix.save(str(out_png))
    finally:
        doc.close()
    return out_png


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate the synthetic benchmark document corpus."
    )
    parser.add_argument("--out", type=Path, default=CORPUS_DIR)
    parser.add_argument("--cases", default="", help="Comma list of case names (default: all)")
    parser.add_argument("--modes", default="", help="Comma list of render modes (default: all)")
    parser.add_argument(
        "--variants", type=int, default=3, help="Random variants per scanned/photo mode"
    )
    parser.add_argument(
        "--seed", type=int, default=0, help="Base seed (change to draw a new corpus)"
    )
    parser.add_argument("--previews", action="store_true", help="Also write page-1 PNG previews")
    args = parser.parse_args(argv)

    cases = get_cases([c for c in args.cases.split(",") if c])
    modes = parse_modes([m for m in args.modes.split(",") if m])
    docs = generate_corpus(cases, modes, args.out, args.variants, args.seed)
    for d in docs:
        extra = ""
        if d.augment:
            a = d.augment
            extra = f"  angle={a['angle_deg']:+.1f}  persp={a['perspective']:.3f}  jpeg={a['jpeg_quality']}  blur={a['blur_sigma']}"
        if args.previews:
            write_preview(d.pdf_path, d.pdf_path.with_suffix(".png"))
        print(f"{d.doc_id:<45}{extra}")
    print(f"\n{len(docs)} documents written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
