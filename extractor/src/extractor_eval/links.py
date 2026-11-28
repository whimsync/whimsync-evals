"""
Link-injection checks: exact (anchor, URL) comparison, never an LLM judge.

Expectations live in `cases/<id>/expected/links.json`:

    {"links": [{"page": 1, "anchor": "PostgreSQL documentation", "url": "https://..."}]}

A case without that file is not link-scored.
"""

import re
from collections import Counter

from .deterministic import normalize

# Markdown links, excluding images. The anchor may contain nested emphasis.
_LINK_RE = re.compile(
    r"(?<!!)\[((?:[^\[\]]|\[[^\]]*\])*)\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)"
)


def _anchor(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[*_`]+", "", text)
    return normalize(text)


def extract(md: str) -> list[tuple[str, str]]:
    return [(_anchor(a), url.strip()) for a, url in _LINK_RE.findall(md)]


def score(expected: list[dict], candidate_md: str) -> dict:
    gold = Counter((_anchor(e["anchor"]), e["url"].strip()) for e in expected)
    pred = Counter(extract(candidate_md))
    gold_urls = Counter(url for _, url in gold.elements())
    pred_urls = Counter(url for _, url in pred.elements())
    tp = sum((gold & pred).values())
    n_gold, n_pred = sum(gold.values()), sum(pred.values())
    metrics = {
        "link_precision": tp / n_pred if n_pred else (None if not n_gold else 0.0),
        "link_recall": tp / n_gold if n_gold else None,
        "link_target_recall": sum((gold_urls & pred_urls).values()) / n_gold if n_gold else None,
    }
    if not n_gold and not n_pred:
        metrics["link_precision"] = None
    details = {
        "expected": n_gold,
        "found": n_pred,
        "missing": [{"anchor": a, "url": u} for a, u in (gold - pred).elements()],
        "unexpected": [{"anchor": a, "url": u} for a, u in (pred - gold).elements()],
    }
    return {"metrics": metrics, "details": details}
