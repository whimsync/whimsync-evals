"""
Builds the synthetic suites into `cases/` and returns their dataset entries.

- `synthetic`: each Markdown template rendered as a digital PDF plus one seeded
  `scanned_noisy` and one `photo` variant. Gold is the template itself (exact ground
  truth, so gold_status is `verified`); routing labels follow from construction
  (digital -> native_acceptable, scans -> vlm_required).
- `links`: small digital PDFs exercising the link injector: duplicate anchors,
  internal (GoTo) links, multi-word and line-wrapped anchors, adjacent links with
  different targets, and text that already contains Markdown link syntax.
"""

import re
import shutil
import tempfile
from pathlib import Path

import pymupdf

from .. import PROJECT_DIR
from ..dataset import Case, PageLabel, QualityRange, sha256_file
from .cases import BenchCase, get_cases
from .render import render_case

SCAN_MODES = ("scanned_noisy", "photo")
_MD_LINK_RE = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)\s]+)\)")


def _write_gold_pages(case_dir: Path, pages: list[str]) -> None:
    pages_dir = case_dir / "expected" / "pages"
    if pages_dir.exists():
        shutil.rmtree(pages_dir)
    pages_dir.mkdir(parents=True)
    for i, md in enumerate(pages, start=1):
        (pages_dir / f"{i:03d}.md").write_text(md.strip() + "\n", encoding="utf-8")


def _write_links(case_dir: Path, links: list[dict]) -> None:
    import json

    path = case_dir / "expected" / "links.json"
    if links:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"links": links}, indent=2) + "\n", encoding="utf-8")
    elif path.exists():
        path.unlink()


def _template_tags(template: BenchCase) -> list[str]:
    md = template.markdown
    tags = []
    if "|" in md:
        tags.append("table")
    if _MD_LINK_RE.search(md):
        tags.append("links")
    if template.columns > 1:
        tags.append("multi_column")
    if re.search(r"^#{2,}", md, re.M):
        tags.append("headings")
    return tags


def build_synthetic(templates: list[str] | None = None) -> list[Case]:
    cases: list[Case] = []
    with tempfile.TemporaryDirectory() as tmp:
        for template in get_cases(templates):
            for mode in ("digital", *SCAN_MODES):
                doc = render_case(template, mode, Path(tmp))
                case_id = f"syn-{template.name.replace('_', '-')}-{mode.replace('_', '-')}"
                case_dir = PROJECT_DIR / "cases" / case_id
                case_dir.mkdir(parents=True, exist_ok=True)
                pdf = case_dir / "source.pdf"
                shutil.copyfile(doc.pdf_path, pdf)
                with pymupdf.open(pdf) as rendered:
                    n_pages = len(rendered)

                route = "native_acceptable" if mode == "digital" else "vlm_required"
                tags = [mode, *_template_tags(template)]
                labels = {
                    str(p): PageLabel(expected_route=route, tags=tags)
                    for p in range(1, n_pages + 1)
                }
                links = (
                    [
                        {"page": 1, "anchor": a, "url": u}
                        for a, u in _MD_LINK_RE.findall(template.markdown)
                    ]
                    if mode == "digital" and n_pages == 1
                    else []
                )
                _write_links(case_dir, links)

                common = dict(
                    id=case_id,
                    pdf=str(pdf.relative_to(PROJECT_DIR)),
                    sha256=sha256_file(pdf),
                    suite="synthetic",
                    description=f"{template.description or template.name} ({mode})".strip(),
                    gold_status="verified",
                    labels_status="verified",
                    page_labels=labels,
                )
                if n_pages == 1:
                    _write_gold_pages(case_dir, [template.markdown])
                    cases.append(Case(quality_mode="pages", **common))
                else:
                    # Text flows across pages, so the gold is one range over the document.
                    ranges_dir = case_dir / "expected" / "ranges"
                    ranges_dir.mkdir(parents=True, exist_ok=True)
                    gold = ranges_dir / f"document-001-{n_pages:03d}.md"
                    gold.write_text(template.markdown.strip() + "\n", encoding="utf-8")
                    cases.append(
                        Case(
                            quality_mode="ranges",
                            quality_ranges=[
                                QualityRange(
                                    id="document",
                                    start_page=1,
                                    end_page=n_pages,
                                    gold=str(gold.relative_to(PROJECT_DIR)),
                                )
                            ],
                            **common,
                        )
                    )
    return cases


# ---- link-injection suite --------------------------------------------------------

_FONT_SIZE = 11
_LINE = 16


def _place_lines(page: pymupdf.Page, lines: list[str], top: float = 72) -> None:
    for i, line in enumerate(lines):
        page.insert_text((72, top + i * _LINE), line, fontsize=_FONT_SIZE)


def _word_rects(page: pymupdf.Page, words: list[str], occurrence: int = 0) -> list[pymupdf.Rect]:
    """Rects of a consecutive word sequence (the `occurrence`-th match) on the page."""
    page_words = page.get_text("words", sort=True)
    texts = [w[4] for w in page_words]
    found = -1
    for i in range(len(texts) - len(words) + 1):
        if texts[i : i + len(words)] == words:
            found += 1
            if found == occurrence:
                return [pymupdf.Rect(w[:4]) for w in page_words[i : i + len(words)]]
    raise ValueError(f"{words} not found on page")


def _link(
    page: pymupdf.Page, rects: list[pymupdf.Rect], target: str | int, per_word: bool = False
) -> None:
    """Adds a URI link (str target) or GoTo link (int page index) over word rects.

    One annotation per line, as producers emit for wrapped anchors; per_word=True
    emits one per word instead, mimicking exporters that split every word.
    """
    if per_word:
        groups = [[r] for r in rects]
    else:
        lines: dict[int, list[pymupdf.Rect]] = {}
        for r in rects:
            lines.setdefault(round(r.y0), []).append(r)
        groups = [lines[y] for y in sorted(lines)]
    for group in groups:
        rect = pymupdf.Rect(group[0])
        for r in group[1:]:
            rect |= r
        if isinstance(target, int):
            page.insert_link(
                {
                    "kind": pymupdf.LINK_GOTO,
                    "from": rect,
                    "page": target,
                    "to": pymupdf.Point(72, 72),
                }
            )
        else:
            page.insert_link({"kind": pymupdf.LINK_URI, "from": rect, "uri": target})


def _reload(doc: pymupdf.Document) -> pymupdf.Document:
    data = doc.tobytes()
    doc.close()
    return pymupdf.open(stream=data, filetype="pdf")


def _links_cases() -> list[tuple]:
    """(id, description, page lines, linker, expected links, gold pages)."""

    def duplicate(doc):
        p = doc[0]
        _link(p, _word_rects(p, ["Docs"], 0), "https://docs.example.com/v1")
        _link(p, _word_rects(p, ["Docs"], 2), "https://docs.example.com/v2")

    def internal(doc):
        p = doc[0]
        _link(p, _word_rects(p, ["the", "appendix"]), 1)

    def wrapped(doc):
        p = doc[0]
        _link(
            p,
            _word_rects(p, ["PostgreSQL", "documentation"]),
            "https://www.postgresql.org/docs/",
            per_word=True,
        )
        _link(
            p,
            _word_rects(p, ["pgvector", "extension", "guide"]),
            "https://github.com/pgvector/pgvector",
        )
        _link(p, _word_rects(p, ["Redis"]), "https://redis.io")
        _link(p, _word_rects(p, ["BullMQ"]), "https://docs.bullmq.io")

    def preformatted(doc):
        p = doc[0]
        _link(p, _word_rects(p, ["release", "notes"]), "https://example.com/releases")

    return [
        (
            "links-duplicate-anchors",
            "Same anchor text three times: two links to different URLs, one plain",
            [
                [
                    "Docs for version one are archived.",
                    "Docs are also mirrored offline.",
                    "Docs for version two are current.",
                ]
            ],
            duplicate,
            [
                {"page": 1, "anchor": "Docs", "url": "https://docs.example.com/v1"},
                {"page": 1, "anchor": "Docs", "url": "https://docs.example.com/v2"},
            ],
            [
                "[Docs](https://docs.example.com/v1) for version one are archived. Docs are also mirrored offline. [Docs](https://docs.example.com/v2) for version two are current."
            ],
        ),
        (
            "links-internal",
            "Internal GoTo link to page 2",
            [
                ["Retention rules are listed in the appendix at the end."],
                ["Appendix", "Records are kept for seven years."],
            ],
            internal,
            [{"page": 1, "anchor": "the appendix", "url": "#page-2"}],
            [
                "Retention rules are listed in [the appendix](#page-2) at the end.",
                "Appendix Records are kept for seven years.",
            ],
        ),
        (
            "links-multiword-wrapped",
            "Multi-word anchors (per-word annotations), an anchor wrapped across lines, adjacent links",
            [
                [
                    "Storage follows the PostgreSQL documentation closely. Search uses the pgvector",
                    "extension guide for HNSW tuning. Queues run on Redis BullMQ workers.",
                ]
            ],
            wrapped,
            [
                {
                    "page": 1,
                    "anchor": "PostgreSQL documentation",
                    "url": "https://www.postgresql.org/docs/",
                },
                {
                    "page": 1,
                    "anchor": "pgvector extension guide",
                    "url": "https://github.com/pgvector/pgvector",
                },
                {"page": 1, "anchor": "Redis", "url": "https://redis.io"},
                {"page": 1, "anchor": "BullMQ", "url": "https://docs.bullmq.io"},
            ],
            [
                "Storage follows the [PostgreSQL documentation](https://www.postgresql.org/docs/) closely. "
                "Search uses the [pgvector extension guide](https://github.com/pgvector/pgvector) for HNSW tuning. "
                "Queues run on [Redis](https://redis.io) [BullMQ](https://docs.bullmq.io) workers."
            ],
        ),
        (
            "links-preformatted",
            "Page text already contains Markdown link syntax; only the annotated words get a link",
            [
                [
                    "The README says [Whimsync](https://whimsync.io) is a memory engine.",
                    "See the release notes for changes.",
                ]
            ],
            preformatted,
            [
                {"page": 1, "anchor": "Whimsync", "url": "https://whimsync.io"},
                {"page": 1, "anchor": "release notes", "url": "https://example.com/releases"},
            ],
            [
                "The README says [Whimsync](https://whimsync.io) is a memory engine. See the [release notes](https://example.com/releases) for changes."
            ],
        ),
    ]


def build_links() -> list[Case]:
    cases = []
    for case_id, description, pages, linker, links, gold_pages in _links_cases():
        doc = pymupdf.open()
        for lines in pages:
            _place_lines(doc.new_page(width=595, height=842), lines)
        doc = _reload(doc)
        linker(doc)
        case_dir = PROJECT_DIR / "cases" / case_id
        case_dir.mkdir(parents=True, exist_ok=True)
        pdf = case_dir / "source.pdf"
        doc.save(pdf, garbage=3, deflate=True)
        doc.close()
        _write_gold_pages(case_dir, gold_pages)
        _write_links(case_dir, links)
        cases.append(
            Case(
                id=case_id,
                pdf=str(pdf.relative_to(PROJECT_DIR)),
                sha256=sha256_file(pdf),
                quality_mode="pages",
                suite="links",
                description=description,
                gold_status="verified",
                labels_status="verified",
                page_labels={
                    str(i): PageLabel(expected_route="native_acceptable", tags=["links"])
                    for i in range(1, len(pages) + 1)
                },
            )
        )
    return cases
