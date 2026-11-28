"""
Deterministic comparison of candidate Markdown against gold Markdown.

Runs before (and independently of) the Gemini judge, and catches what exact checks
catch best: OCR errors, dropped or altered numbers, malformed tables, missing footer
text, empty output. Every metric is in [0, 1] (higher is better) unless noted;
`None` means "not applicable" (e.g. table metrics when the gold has no table).
"""

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field

from markdown_it import MarkdownIt
from rapidfuzz import fuzz
from rapidfuzz.distance import LCSseq

_MD = MarkdownIt("commonmark", {"html": True}).enable("table").enable("strikethrough")

_CJK = r"぀-ヿ㐀-䶿一-鿿豈-﫿가-힯"
_TOKEN_RE = re.compile(rf"[{_CJK}]|[^\W_]+(?:[.,'][^\W_]+)*", re.UNICODE)
_CJK_RE = re.compile(rf"[{_CJK}]")
_NUMBER_RE = re.compile(r"\(?[-−–]?[$€£¥]?\d[\d,]*(?:\.\d+)?%?\)?")
_TAG_RE = re.compile(r"<[^>]+>")
_HTML_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.I | re.S)
_HTML_CELL_RE = re.compile(r"<t[hd][^>]*>(.*?)</t[hd]>", re.I | re.S)
_HTML_TABLE_RE = re.compile(r"<table[^>]*>(.*?)</table>", re.I | re.S)
_FILLER_RE = re.compile(r"^\s*(here is|here's|below is|sure[,!])\b", re.I | re.M)


def normalize(text: str) -> str:
    """NFKC, lowercase, collapsed whitespace."""
    text = unicodedata.normalize("NFKC", text).replace(" ", " ")
    return re.sub(r"\s+", " ", text).strip().lower()


def plain_text(md: str) -> str:
    """Markdown -> visible text (markup, link targets, image refs and HTML tags removed)."""
    parts: list[str] = []
    for token in _MD.parse(md):
        if token.type == "inline":
            for child in token.children or []:
                if child.type in ("text", "code_inline"):
                    parts.append(child.content)
                elif child.type in ("softbreak", "hardbreak"):
                    parts.append(" ")
                elif child.type == "html_inline":
                    parts.append(" ")
            parts.append("\n")
        elif token.type in ("code_block", "fence"):
            parts.append(token.content + "\n")
        elif token.type == "html_block":
            parts.append(_TAG_RE.sub(" ", token.content) + "\n")
    return "".join(parts)


def tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(normalize(text))


def _chars(text: str) -> str:
    return "".join(ch for ch in normalize(text) if ch.isalnum())


def _prf(gold: Counter, pred: Counter) -> tuple[float | None, float | None, float | None]:
    n_gold, n_pred = sum(gold.values()), sum(pred.values())
    if n_gold == 0 and n_pred == 0:
        return None, None, None
    tp = sum((gold & pred).values())
    precision = tp / n_pred if n_pred else 0.0
    recall = tp / n_gold if n_gold else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


# ---- headings ------------------------------------------------------------------


def headings(md: str) -> list[tuple[int, str]]:
    out, tokens_ = [], _MD.parse(md)
    for i, token in enumerate(tokens_):
        if token.type == "heading_open" and i + 1 < len(tokens_):
            text = normalize(plain_text(tokens_[i + 1].content))
            if text:
                out.append((int(token.tag[1]), text))
    return out


# ---- tables --------------------------------------------------------------------


def tables(md: str) -> list[list[list[str]]]:
    """GFM and HTML tables as grids of normalized cell text."""
    grids: list[list[list[str]]] = []
    row: list[str] | None = None
    grid: list[list[str]] | None = None
    tokens_ = _MD.parse(md)
    for i, token in enumerate(tokens_):
        if token.type == "table_open":
            grid = []
        elif token.type == "table_close" and grid is not None:
            grids.append(grid)
            grid = None
        elif token.type == "tr_open":
            row = []
        elif token.type == "tr_close" and grid is not None and row is not None:
            grid.append(row)
            row = None
        elif token.type in ("th_open", "td_open") and row is not None:
            content = tokens_[i + 1].content if i + 1 < len(tokens_) else ""
            row.append(normalize(plain_text(content)))
    for html_table in _HTML_TABLE_RE.findall(md):
        rows = [
            [normalize(_TAG_RE.sub(" ", c)) for c in _HTML_CELL_RE.findall(r)]
            for r in _HTML_ROW_RE.findall(html_table)
        ]
        if rows:
            grids.append(rows)
    return [g for g in grids if g]


def _cells(grid: list[list[str]]) -> Counter:
    return Counter(c for row in grid for c in row if c)


def _overlap(a: list[list[str]], b: list[list[str]]) -> float:
    ca, cb = _cells(a), _cells(b)
    union = sum((ca | cb).values())
    return sum((ca & cb).values()) / union if union else 0.0


def _align_rows(gold: list[list[str]], pred: list[list[str]]) -> list[tuple[int, int]]:
    """Monotone row alignment maximising total row similarity (DP, gaps cost 0)."""
    g = [" | ".join(r) for r in gold]
    p = [" | ".join(r) for r in pred]
    n, m = len(g), len(p)
    if n * m > 250_000:  # very large tables: positional alignment keeps this linear
        return [(i, i) for i in range(min(n, m))]
    sims = [[fuzz.ratio(a, b) / 100 for b in p] for a in g]
    score = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            sim = sims[i - 1][j - 1]
            score[i][j] = max(score[i - 1][j], score[i][j - 1], score[i - 1][j - 1] + sim)
    pairs, i, j = [], n, m
    while i > 0 and j > 0:
        sim = sims[i - 1][j - 1]
        if score[i][j] == score[i - 1][j - 1] + sim and sim > 0.3:
            pairs.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif score[i][j] == score[i - 1][j]:
            i -= 1
        else:
            j -= 1
    return pairs[::-1]


@dataclass
class TableScore:
    gold_tables: int
    pred_tables: int
    matched: int = 0
    shape_matches: int = 0
    row_matches: int = 0
    col_matches: int = 0
    gold_cells: int = 0
    matched_cells: int = 0
    details: list[dict] = field(default_factory=list)


def score_tables(gold_md: str, pred_md: str) -> TableScore:
    gold, pred = tables(gold_md), tables(pred_md)
    result = TableScore(len(gold), len(pred))
    result.gold_cells = sum(len(r) for g in gold for r in g)
    candidates = sorted(
        ((_overlap(g, p), gi, pi) for gi, g in enumerate(gold) for pi, p in enumerate(pred)),
        reverse=True,
    )
    used_g, used_p = set(), set()
    for overlap, gi, pi in candidates:
        if overlap <= 0 or gi in used_g or pi in used_p:
            continue
        used_g.add(gi)
        used_p.add(pi)
        g, p = gold[gi], pred[pi]
        g_cols = max(len(r) for r in g)
        p_cols = max(len(r) for r in p)
        matched_cells = sum(
            1
            for gr, pr in _align_rows(g, p)
            for c, cell in enumerate(g[gr])
            if c < len(p[pr]) and p[pr][c] == cell
        )
        result.matched += 1
        result.row_matches += len(g) == len(p)
        result.col_matches += g_cols == p_cols
        result.shape_matches += len(g) == len(p) and g_cols == p_cols
        result.matched_cells += matched_cells
        result.details.append(
            {
                "gold_table": gi,
                "pred_table": pi,
                "gold_shape": [len(g), g_cols],
                "pred_shape": [len(p), p_cols],
                "cell_accuracy": round(matched_cells / max(1, sum(len(r) for r in g)), 4),
            }
        )
    return result


# ---- numbers -------------------------------------------------------------------


def numbers(text: str) -> Counter:
    out: Counter = Counter()
    for raw in _NUMBER_RE.findall(unicodedata.normalize("NFKC", text)):
        negative = raw.startswith("(") and raw.endswith(")") or raw.lstrip("(")[:1] in "-−–"
        value = re.sub(r"[^\d.]", "", raw).rstrip(".")
        if not value or not any(ch.isdigit() for ch in value):
            continue
        out[("-" if negative else "") + value] += 1
    return out


# ---- Markdown validity -----------------------------------------------------------


def markdown_issues(md: str) -> list[str]:
    issues: list[str] = []
    if sum(1 for line in md.splitlines() if line.lstrip().startswith("```")) % 2:
        issues.append("unclosed_code_fence")
    block: list[str] = []
    for line in [*md.splitlines(), ""]:
        if line.strip().startswith("|"):
            block.append(line.strip())
            continue
        if len(block) >= 2:
            sep = re.fullmatch(r"\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?", block[1])
            if not sep:
                issues.append("table_without_separator_row")
            widths = {len(r.strip("|").split("|")) for r in block}
            if len(widths) > 1:
                issues.append("table_column_mismatch")
        block = []
    if _FILLER_RE.search(md):
        issues.append("conversational_filler")
    return sorted(set(issues))


# ---- unit score ------------------------------------------------------------------


def score(gold_md: str, pred_md: str, required_phrases: list[str] | None = None) -> dict:
    """All deterministic metrics for one unit. Returns {"metrics": {...}, "details": {...}}."""
    gold_text, pred_text = plain_text(gold_md), plain_text(pred_md)
    gold_norm, pred_norm = normalize(gold_text), normalize(pred_text)
    gold_tokens, pred_tokens = tokens(gold_text), tokens(pred_text)
    metrics: dict[str, float | None] = {}
    details: dict = {}

    metrics["empty_output"] = float(not pred_norm and bool(gold_norm))
    metrics["text_similarity"] = (
        fuzz.ratio(gold_norm, pred_norm) / 100 if gold_norm or pred_norm else None
    )
    gold_chars, pred_chars = _chars(gold_text), _chars(pred_text)
    metrics["char_similarity"] = (
        fuzz.ratio(gold_chars, pred_chars) / 100 if gold_chars or pred_chars else None
    )
    p, r, f = _prf(Counter(gold_tokens), Counter(pred_tokens))
    metrics.update(word_precision=p, word_recall=r, word_f1=f)
    metrics["reading_order"] = (
        LCSseq.similarity(gold_tokens, pred_tokens) / len(gold_tokens) if gold_tokens else None
    )
    metrics["length_ratio"] = len(pred_norm) / len(gold_norm) if gold_norm else None
    details["cjk_share"] = round(len(_CJK_RE.findall(gold_text)) / max(1, len(gold_chars)), 3)

    gold_h, pred_h = headings(gold_md), headings(pred_md)
    if gold_h or pred_h:
        _, _, metrics["heading_f1"] = _prf(Counter(gold_h), Counter(pred_h))
        _, _, metrics["heading_text_f1"] = _prf(
            Counter(t for _, t in gold_h), Counter(t for _, t in pred_h)
        )
        gold_seq, pred_seq = [t for _, t in gold_h], [t for _, t in pred_h]
        metrics["heading_sequence"] = LCSseq.similarity(gold_seq, pred_seq) / max(
            len(gold_seq), len(pred_seq)
        )
    else:
        metrics.update(heading_f1=None, heading_text_f1=None, heading_sequence=None)
    details["headings"] = {"gold": gold_h, "candidate": pred_h}

    issues = markdown_issues(pred_md)
    metrics["markdown_valid"] = 0.0 if issues else 1.0
    details["markdown_issues"] = issues

    t = score_tables(gold_md, pred_md)
    if t.gold_tables:
        metrics["table_detection"] = t.matched / t.gold_tables
        metrics["table_shape_match"] = t.shape_matches / t.gold_tables
        metrics["table_row_count_match"] = t.row_matches / t.gold_tables
        metrics["table_col_count_match"] = t.col_matches / t.gold_tables
        metrics["table_cell_accuracy"] = t.matched_cells / t.gold_cells if t.gold_cells else None
        gold_table_numbers = numbers(
            "\n".join(c for g in tables(gold_md) for row in g for c in row)
        )
        pred_table_numbers = numbers(
            "\n".join(c for g in tables(pred_md) for row in g for c in row)
        )
        _, metrics["table_numeric_recall"], _ = _prf(gold_table_numbers, pred_table_numbers)
    else:
        for key in (
            "table_detection",
            "table_shape_match",
            "table_row_count_match",
            "table_col_count_match",
            "table_cell_accuracy",
            "table_numeric_recall",
        ):
            metrics[key] = None
    details["tables"] = {
        "gold": t.gold_tables,
        "candidate": t.pred_tables,
        "spurious": max(0, t.pred_tables - t.matched),
        "matches": t.details,
    }

    p, r, _ = _prf(numbers(gold_text), numbers(pred_text))
    metrics["numeric_precision"], metrics["numeric_recall"] = p, r
    gold_nums, pred_nums = numbers(gold_text), numbers(pred_text)
    details["missing_numbers"] = sorted((gold_nums - pred_nums).elements())[:50]
    details["unexpected_numbers"] = sorted((pred_nums - gold_nums).elements())[:50]

    phrases = required_phrases or []
    if phrases:
        missing = [ph for ph in phrases if normalize(ph) not in pred_norm]
        metrics["required_phrase_recall"] = 1 - len(missing) / len(phrases)
        details["missing_phrases"] = missing
    else:
        metrics["required_phrase_recall"] = None

    return {"metrics": metrics, "details": details}
