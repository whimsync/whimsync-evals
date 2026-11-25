"""
Pure scoring functions comparing parsed Markdown against ground-truth Markdown.

`score()` runs every function registered with `@scorer`. Each scorer declares the
MetricSpecs for the keys it returns, which together form `CATALOG`, so adding a
metric is one decorated function (tests check that declared and returned keys
match). A scorer returns None for metrics that don't apply to a document (e.g.
tables in a letter); those are left out of averages.
"""

import re
from collections import Counter
from collections.abc import Callable
from difflib import SequenceMatcher

from .catalog import Catalog, MetricSpec

Scores = dict[str, float | None]

_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK_RE = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)\s]+)\)")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_EMPHASIS_RE = re.compile(r"(\*\*|__|\*|_|`|~~)")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_WORD_RE = re.compile(r"[a-z0-9]+(?:[.,'][a-z0-9]+)*")


def _clean_inline(text: str) -> str:
    text = _IMAGE_RE.sub(" ", text)
    text = _LINK_RE.sub(r"\1", text)
    text = _EMPHASIS_RE.sub("", text)
    text = text.replace("<br>", " ")
    return re.sub(r"\s+", " ", text).strip().lower()


def normalize(md: str) -> str:
    """Strips Markdown syntax noise so text comparisons focus on content."""
    lines = []
    for line in md.splitlines():
        if _TABLE_SEP_RE.match(line) and "-" in line:
            continue
        line = re.sub(r"^\s*#{1,6}\s+", "", line)
        line = re.sub(r"^\s*([-*+]|\d+[.)])\s+", "", line)
        line = line.replace("|", " ")
        lines.append(_clean_inline(line))
    return re.sub(r"\s+", " ", " ".join(lines)).strip()


def words(md: str) -> list[str]:
    return _WORD_RE.findall(normalize(md))


def _prf(tp: int, n_pred: int, n_ref: int) -> dict[str, float]:
    precision = tp / n_pred if n_pred else (1.0 if n_ref == 0 else 0.0)
    recall = tp / n_ref if n_ref else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": precision, "recall": recall, "f1": f1}


def text_similarity(pred: str, ref: str) -> float:
    """Normalized character-level similarity in [0, 1] (difflib ratio on normalized text)."""
    a, b = normalize(pred), normalize(ref)
    if not a and not b:
        return 1.0
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def word_prf(pred: str, ref: str) -> dict[str, float]:
    """Bag-of-words overlap: recall = content kept, precision = 1 - hallucinated share."""
    p, r = Counter(words(pred)), Counter(words(ref))
    tp = sum((p & r).values())
    return _prf(tp, sum(p.values()), sum(r.values()))


def _lcs_len(a: list[str], b: list[str]) -> int:
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0] * (len(b) + 1)
        for j, y in enumerate(b, 1):
            cur[j] = prev[j - 1] + 1 if x == y else max(prev[j], cur[j - 1])
        prev = cur
    return prev[-1]


def reading_order(pred: str, ref: str) -> float:
    """LCS of word sequences / reference length: penalizes shuffled or dropped content."""
    r = words(ref)
    if not r:
        return 1.0
    return _lcs_len(words(pred), r) / len(r)


def extract_headings(md: str) -> list[tuple[int, str]]:
    out = []
    for line in md.splitlines():
        m = _HEADING_RE.match(line.strip())
        if m:
            out.append((len(m.group(1)), _clean_inline(m.group(2))))
    return out


def _multiset_prf(pred: list, ref: list) -> dict[str, float]:
    tp = sum((Counter(pred) & Counter(ref)).values())
    return _prf(tp, len(pred), len(ref))


def _rank_levels(headings: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Maps absolute levels to dense ranks (e.g. ##/#### -> 1/2): parsers infer levels from relative font size."""
    ranks = {lvl: i + 1 for i, lvl in enumerate(sorted({lvl for lvl, _ in headings}))}
    return [(ranks[lvl], text) for lvl, text in headings]


def heading_prf(pred: str, ref: str) -> dict[str, float]:
    """Heading match on (relative level rank, text) plus a level-agnostic text-only variant."""
    p, r = _rank_levels(extract_headings(pred)), _rank_levels(extract_headings(ref))
    strict = _multiset_prf(p, r)
    loose = _multiset_prf([t for _, t in p], [t for _, t in r])
    return {
        "precision": strict["precision"],
        "recall": strict["recall"],
        "f1": strict["f1"],
        "text_only_f1": loose["f1"],
    }


def extract_tables(md: str) -> list[list[list[str]]]:
    """Parses GFM tables into grids of normalized cell strings (separator rows removed)."""
    tables: list[list[list[str]]] = []
    current: list[list[str]] = []
    for line in md.splitlines() + [""]:
        stripped = line.strip()
        if stripped.startswith("|") and stripped.count("|") >= 2:
            if _TABLE_SEP_RE.match(stripped):
                continue
            cells = [_clean_inline(c) for c in stripped.strip("|").split("|")]
            current.append(cells)
        else:
            if current:
                tables.append(current)
            current = []
    return tables


def _cell_matches(pred: list[list[str]], ref: list[list[str]]) -> int:
    hits = 0
    for i, row in enumerate(ref):
        if i >= len(pred):
            break
        for j, cell in enumerate(row):
            if j < len(pred[i]) and pred[i][j] == cell:
                hits += 1
    return hits


def table_metrics(pred: str, ref: str) -> dict[str, float] | None:
    """
    Pairs each reference table with its best unused predicted table and scores
    exact cell agreement. Returns None if the reference contains no tables.
    """
    ref_tables, pred_tables = extract_tables(ref), extract_tables(pred)
    if not ref_tables:
        return None

    used: set[int] = set()
    total_cells = matched_cells = detected = shape_ok = 0
    for rt in ref_tables:
        n_cells = sum(len(row) for row in rt)
        total_cells += n_cells
        best_idx, best_hits = -1, -1
        for k, pt in enumerate(pred_tables):
            if k in used:
                continue
            hits = _cell_matches(pt, rt)
            if hits > best_hits:
                best_idx, best_hits = k, hits
        if best_idx < 0:
            continue
        used.add(best_idx)
        detected += 1
        matched_cells += best_hits
        pt = pred_tables[best_idx]
        if len(pt) == len(rt) and all(len(a) == len(b) for a, b in zip(pt, rt, strict=True)):
            shape_ok += 1

    n = len(ref_tables)
    return {
        "cell_accuracy": matched_cells / total_cells if total_cells else 1.0,
        "detection_recall": detected / n,
        "shape_match": shape_ok / n,
    }


def extract_links(md: str) -> list[str]:
    return [url.rstrip("/") for _, url in _LINK_RE.findall(md)]


def link_recall(pred: str, ref: str) -> float | None:
    """Share of reference link URLs present in the prediction. None if reference has no links."""
    r = extract_links(ref)
    if not r:
        return None
    p = Counter(extract_links(pred))
    return sum((p & Counter(r)).values()) / len(r)


def link_exact(pred: str, ref: str) -> float | None:
    """Share of reference (anchor text, URL) pairs reproduced exactly. Catches split or truncated anchors."""
    r = [(_clean_inline(t), u.rstrip("/")) for t, u in _LINK_RE.findall(ref)]
    if not r:
        return None
    p = Counter((_clean_inline(t), u.rstrip("/")) for t, u in _LINK_RE.findall(pred))
    return sum((p & Counter(r)).values()) / len(r)


# --------------------------------------------------------------------------- registry

_SCORERS: list[Callable[[str, str], Scores]] = []
_SCORE_SPECS: list[MetricSpec] = []


def scorer(*specs: MetricSpec) -> Callable:
    """Registers a `(pred, ref) -> Scores` function and the metrics it returns."""

    def register(fn: Callable[[str, str], Scores]) -> Callable[[str, str], Scores]:
        _SCORERS.append(fn)
        _SCORE_SPECS.extend(specs)
        return fn

    return register


@scorer(
    MetricSpec(
        "text_similarity",
        "Text similarity",
        "text_sim",
        summary=True,
        description="Character-level similarity of normalised text",
    ),
)
def _text(pred: str, ref: str) -> Scores:
    return {"text_similarity": text_similarity(pred, ref)}


@scorer(
    MetricSpec(
        "word_precision",
        "Word precision",
        "w_prec",
        summary=True,
        description="Share of output words found in the ground truth (hallucination)",
    ),
    MetricSpec(
        "word_recall",
        "Word recall",
        "w_rec",
        summary=True,
        description="Share of ground-truth words kept (dropped content)",
    ),
    MetricSpec("word_f1", "Word F1", "w_f1"),
)
def _words(pred: str, ref: str) -> Scores:
    w = word_prf(pred, ref)
    return {"word_precision": w["precision"], "word_recall": w["recall"], "word_f1": w["f1"]}


@scorer(
    MetricSpec(
        "reading_order",
        "Reading order",
        "order",
        summary=True,
        description="Longest common word subsequence / reference length",
    ),
)
def _order(pred: str, ref: str) -> Scores:
    return {"reading_order": reading_order(pred, ref)}


@scorer(
    MetricSpec(
        "heading_f1",
        "Heading F1",
        "head_f1",
        summary=True,
        description="Headings matched on relative level and text",
    ),
    MetricSpec(
        "heading_text_f1",
        "Heading text F1",
        "head_txt",
        description="Headings matched on text only",
    ),
)
def _headings(pred: str, ref: str) -> Scores:
    if not extract_headings(ref):
        return {"heading_f1": None, "heading_text_f1": None}
    h = heading_prf(pred, ref)
    return {"heading_f1": h["f1"], "heading_text_f1": h["text_only_f1"]}


@scorer(
    MetricSpec(
        "table_cell_accuracy",
        "Table cells",
        "tbl_cell",
        summary=True,
        description="Exact cell matches in GFM tables",
    ),
    MetricSpec(
        "table_detection",
        "Table detection",
        "tbl_det",
        description="Share of reference tables found",
    ),
    MetricSpec(
        "table_shape_match",
        "Table shape",
        "tbl_shape",
        description="Share of tables with the right rows/columns",
    ),
)
def _tables(pred: str, ref: str) -> Scores:
    t = table_metrics(pred, ref)
    return {
        "table_cell_accuracy": t["cell_accuracy"] if t else None,
        "table_detection": t["detection_recall"] if t else None,
        "table_shape_match": t["shape_match"] if t else None,
    }


@scorer(
    MetricSpec(
        "link_recall",
        "Link recall",
        "links",
        summary=True,
        description="Share of reference link URLs present",
    ),
    MetricSpec(
        "link_exact",
        "Link exact",
        "link_ex",
        summary=True,
        description="Share of exact [anchor](url) pairs; catches split anchors",
    ),
)
def _links(pred: str, ref: str) -> Scores:
    return {"link_recall": link_recall(pred, ref), "link_exact": link_exact(pred, ref)}


def score(pred: str, ref: str) -> Scores:
    """Every registered metric for one (prediction, reference) pair."""
    out: Scores = {}
    for fn in _SCORERS:
        out.update(fn(pred, ref))
    return out


SCORE_SPECS: tuple[MetricSpec, ...] = tuple(_SCORE_SPECS)

# Per-group cost metrics computed by the runner's aggregation, not by scorers.
RUN_SPECS: tuple[MetricSpec, ...] = (
    MetricSpec(
        "seconds_per_page",
        "s / page",
        "s/page",
        better="lower",
        unit="s",
        tolerance="relative",
        summary=True,
        description="Wall time per page",
    ),
    MetricSpec(
        "vlm_pages",
        "VLM pages",
        "vlm_pg",
        better="none",
        unit="count",
        summary=True,
        description="Pages sent to the vision model",
    ),
    MetricSpec(
        "vlm_failures",
        "VLM failures",
        "vlm_fail",
        better="lower",
        unit="count",
        summary=True,
        description="VLM calls that returned no text; above 0 means scores are understated",
    ),
)

CATALOG = Catalog([*SCORE_SPECS, *RUN_SPECS])
