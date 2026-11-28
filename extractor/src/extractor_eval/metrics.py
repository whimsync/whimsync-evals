"""
Metric catalog: one description of every metric, read by the console tables,
`compare` and the web UI (served at /api/catalog). Add a metric here once and every
view picks it up.
"""

from dataclasses import asdict, dataclass
from typing import Literal

Better = Literal["higher", "lower", "none"]
Unit = Literal["ratio", "score5", "seconds", "count", "usd", "flag"]

# A change beyond these marks a regression/improvement in compare views.
ABSOLUTE_TOLERANCE = {"ratio": 0.02, "score5": 0.1, "flag": 0.02}
RELATIVE_TOLERANCE = 0.25  # seconds, counts and cost are noisy; compare relatively


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    group: str
    better: Better = "higher"
    unit: Unit = "ratio"
    headline: bool = False
    description: str = ""

    def to_json(self) -> dict:
        return asdict(self)


CATALOG: list[Metric] = [
    # quality: deterministic
    Metric(
        "text_similarity",
        "Text similarity",
        "quality",
        headline=True,
        description="Normalized edit similarity of visible text (NFKC, markup removed)",
    ),
    Metric(
        "char_similarity",
        "Character similarity",
        "quality",
        description="Edit similarity over letters/digits only; the fair measure for CJK text",
    ),
    Metric(
        "word_precision",
        "Word precision",
        "quality",
        description="Candidate words found in gold (low = invented text)",
    ),
    Metric(
        "word_recall",
        "Word recall",
        "quality",
        description="Gold words found in candidate (low = dropped text)",
    ),
    Metric("word_f1", "Word F1", "quality"),
    Metric(
        "reading_order",
        "Reading order",
        "quality",
        headline=True,
        description="Longest common word subsequence / gold words",
    ),
    Metric(
        "heading_f1",
        "Heading F1",
        "quality",
        headline=True,
        description="Headings matched on (level, text)",
    ),
    Metric(
        "heading_text_f1", "Heading text F1", "quality", description="Headings matched on text only"
    ),
    Metric(
        "heading_sequence",
        "Heading sequence",
        "quality",
        description="Order agreement of heading lists",
    ),
    Metric(
        "markdown_valid",
        "Markdown valid",
        "quality",
        unit="flag",
        description="No unclosed fences, broken pipe tables or conversational filler",
    ),
    Metric(
        "table_detection",
        "Table detection",
        "quality",
        description="Gold tables matched by a candidate table",
    ),
    Metric(
        "table_shape_match",
        "Table shape",
        "quality",
        description="Matched tables with identical rows x columns",
    ),
    Metric("table_row_count_match", "Table rows", "quality"),
    Metric("table_col_count_match", "Table columns", "quality"),
    Metric(
        "table_cell_accuracy",
        "Table cells",
        "quality",
        headline=True,
        description="Gold cells reproduced exactly (rows aligned)",
    ),
    Metric(
        "table_numeric_recall",
        "Table numbers",
        "quality",
        description="Numbers in gold tables found in candidate tables",
    ),
    Metric(
        "numeric_recall",
        "Numeric recall",
        "quality",
        headline=True,
        description="Numbers anywhere in gold found in candidate",
    ),
    Metric(
        "numeric_precision",
        "Numeric precision",
        "quality",
        description="Candidate numbers present in gold",
    ),
    Metric(
        "required_phrase_recall",
        "Required phrases",
        "quality",
        description="Labelled key phrases (e.g. footers) present",
    ),
    Metric(
        "empty_output",
        "Empty output",
        "quality",
        better="lower",
        unit="flag",
        description="Candidate empty where gold has text",
    ),
    Metric(
        "length_ratio",
        "Length ratio",
        "quality",
        better="none",
        description="Candidate / gold text length",
    ),
    # links
    Metric("link_precision", "Link precision", "links"),
    Metric(
        "link_recall",
        "Link recall",
        "links",
        headline=True,
        description="Expected (anchor, URL) pairs found exactly",
    ),
    Metric(
        "link_target_recall",
        "Link target recall",
        "links",
        description="Expected URLs found, any anchor",
    ),
    # judge
    Metric("judge_overall", "Judge overall", "judge", unit="score5", headline=True),
    Metric("judge_text", "Judge text", "judge", unit="score5"),
    Metric("judge_reading_order", "Judge reading order", "judge", unit="score5"),
    Metric("judge_table", "Judge tables", "judge", unit="score5"),
    Metric("judge_visual", "Judge visuals", "judge", unit="score5"),
    Metric("judge_critical_errors", "Critical errors", "judge", better="lower", unit="count"),
    # routing
    Metric(
        "routing_precision",
        "Routing precision",
        "routing",
        description="VLM-routed pages that needed the VLM",
    ),
    Metric(
        "routing_recall",
        "Routing recall",
        "routing",
        headline=True,
        description="VLM-required pages routed to the VLM",
    ),
    Metric(
        "false_native_rate",
        "False-native rate",
        "routing",
        better="lower",
        headline=True,
        description="VLM-required pages parsed natively (critical)",
    ),
    Metric(
        "false_vlm_rate",
        "False-VLM rate",
        "routing",
        better="lower",
        description="Native-acceptable pages sent to the VLM (cost)",
    ),
    Metric(
        "reason_agreement",
        "Reason agreement",
        "routing",
        description="Classifier reasons equal the labelled reasons",
    ),
    # operational
    Metric("success_rate", "Success rate", "operational", headline=True),
    Metric(
        "duration_seconds",
        "Parse seconds",
        "operational",
        better="lower",
        unit="seconds",
        headline=True,
    ),
    Metric("seconds_per_page", "Seconds / page", "operational", better="lower", unit="seconds"),
    Metric("vlm_page_ratio", "VLM page ratio", "operational", better="none"),
    Metric("vlm_failures", "VLM failures", "operational", better="lower", unit="count"),
    Metric("retries", "Retries", "operational", better="lower", unit="count"),
    Metric("input_tokens", "Input tokens", "operational", better="lower", unit="count"),
    Metric("output_tokens", "Output tokens", "operational", better="lower", unit="count"),
    Metric("cost_usd", "Cost (USD)", "operational", better="lower", unit="usd"),
    Metric("output_chars", "Output chars", "operational", better="none", unit="count"),
    # performance repeats
    Metric("perf_median_seconds", "Perf median s", "performance", better="lower", unit="seconds"),
    Metric("perf_min_seconds", "Perf fastest s", "performance", better="lower", unit="seconds"),
    Metric("perf_max_seconds", "Perf slowest s", "performance", better="lower", unit="seconds"),
]

BY_KEY = {m.key: m for m in CATALOG}


def get(key: str) -> Metric:
    return BY_KEY.get(key) or Metric(key, key, "other", better="none")


def fmt(key: str, value: float | None) -> str:
    if value is None:
        return "-"
    unit = get(key).unit
    if unit == "seconds":
        return f"{value:.2f}"
    if unit == "usd":
        return f"${value:.4f}"
    if unit == "count":
        return str(int(value)) if float(value).is_integer() else f"{value:.1f}"
    if unit == "score5":
        return f"{value:.2f}"
    return f"{value:.3f}"


def verdict(key: str, base: float | None, other: float | None) -> str:
    """'better' | 'worse' | 'same' | 'n/a' for other vs base."""
    if base is None or other is None:
        return "n/a"
    metric = get(key)
    if metric.better == "none":
        return "same"
    delta = other - base
    if metric.unit in ABSOLUTE_TOLERANCE:
        significant = abs(delta) > ABSOLUTE_TOLERANCE[metric.unit]
    else:
        significant = abs(delta) > RELATIVE_TOLERANCE * max(abs(base), 1e-9)
    if not significant:
        return "same"
    return "better" if (delta > 0) == (metric.better == "higher") else "worse"
