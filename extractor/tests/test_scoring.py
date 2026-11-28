import pytest

from extractor_eval import deterministic, links, routing
from extractor_eval.dataset import Case, PageLabel

GOLD = """# Report

## Revenue

| Year | Revenue | Margin |
|---|---|---|
| 2023 | $1,200 | 40% |
| 2024 | $1,500 | (5) |

Signed by the Joint Secretary.
"""


def test_identical_markdown_scores_perfectly():
    m = deterministic.score(GOLD, GOLD, ["Joint Secretary"])["metrics"]
    for key in (
        "text_similarity",
        "char_similarity",
        "word_f1",
        "reading_order",
        "heading_f1",
        "table_detection",
        "table_shape_match",
        "table_cell_accuracy",
        "numeric_recall",
        "numeric_precision",
        "required_phrase_recall",
        "markdown_valid",
    ):
        assert m[key] == pytest.approx(1.0), key
    assert m["empty_output"] == 0.0


def test_formatting_differences_do_not_change_text():
    pred = GOLD.replace("# Report", "# **Report**").replace("Signed by", "<u>Signed</u> by")
    m = deterministic.score(GOLD, pred)["metrics"]
    assert m["text_similarity"] == pytest.approx(1.0)
    assert m["heading_f1"] == pytest.approx(1.0)


def test_dropped_number_and_cell_are_detected():
    pred = GOLD.replace("$1,500", "$1,050")
    result = deterministic.score(GOLD, pred)
    m, d = result["metrics"], result["details"]
    assert m["numeric_recall"] < 1.0
    assert "1500" in d["missing_numbers"]
    assert m["table_cell_accuracy"] == pytest.approx(8 / 9)
    assert m["table_shape_match"] == 1.0


def test_parenthesised_numbers_are_negative():
    assert deterministic.numbers("(5) and -5") == {"-5": 2}


def test_heading_level_change_hits_level_metric_only():
    pred = GOLD.replace("## Revenue", "# Revenue")
    m = deterministic.score(GOLD, pred)["metrics"]
    assert m["heading_f1"] == pytest.approx(0.5)
    assert m["heading_text_f1"] == pytest.approx(1.0)


def test_empty_output_and_missing_phrase():
    m = deterministic.score(GOLD, "", ["Joint Secretary"])
    assert m["metrics"]["empty_output"] == 1.0
    assert m["metrics"]["required_phrase_recall"] == 0.0
    assert m["details"]["missing_phrases"] == ["Joint Secretary"]


def test_markdown_issues():
    broken = "Here is the extracted markdown:\n\n| a | b |\n| c |\n\n```\ncode"
    issues = deterministic.markdown_issues(broken)
    assert {
        "conversational_filler",
        "table_without_separator_row",
        "table_column_mismatch",
        "unclosed_code_fence",
    } <= set(issues)


def test_cjk_character_similarity():
    gold = "表7-1：发行人历史信用评级情况"
    m = deterministic.score(gold, "表7-1：发行人历史信用评级")["metrics"]
    assert 0.8 < m["char_similarity"] < 1.0


def test_html_tables_are_parsed():
    html = (
        "<table><tr><th>Year</th><th>Revenue</th></tr><tr><td>2024</td><td>$1,500</td></tr></table>"
    )
    assert deterministic.tables(html) == [[["year", "revenue"], ["2024", "$1,500"]]]


def test_links_exact_anchor_and_target():
    expected = [{"page": 1, "anchor": "PostgreSQL documentation", "url": "https://pg.org"}]
    split = "the [PostgreSQL](https://pg.org) [documentation](https://pg.org) closely"
    m = links.score(expected, split)["metrics"]
    assert m["link_recall"] == 0.0
    assert m["link_target_recall"] == 1.0
    assert m["link_precision"] == 0.0
    good = "the [**PostgreSQL documentation**](https://pg.org) closely ![img](x.png)"
    m = links.score(expected, good)["metrics"]
    assert (m["link_recall"], m["link_precision"]) == (1.0, 1.0)


def _case(labels: dict) -> Case:
    return Case(
        id="c", pdf="cases/c/source.pdf", sha256="0" * 64, quality_mode="pages", page_labels=labels
    )


def test_routing_outcomes_and_rates():
    case = _case(
        {
            "1": PageLabel(
                expected_route="vlm_required",
                expected_classifier_reasons=["scanned"],
                tags=["scan"],
            ),
            "2": PageLabel(expected_route="native_acceptable"),
            "3": PageLabel(expected_route="either_acceptable"),
            "4": PageLabel(expected_route="vlm_required", tags=["table"]),
        }
    )
    trace = {
        "pages": [
            {"page_number": 1, "route": "native", "complexity": {"reasons": []}},
            {"page_number": 2, "route": "vlm", "complexity": {"reasons": ["vector-table"]}},
            {"page_number": 3, "route": "vlm", "complexity": {"reasons": []}},
            {"page_number": 4, "route": "vlm", "complexity": {"reasons": ["vector-table"]}},
        ]
    }
    outcomes = routing.page_outcomes(case, trace)
    assert [o["outcome"] for o in outcomes] == [
        "false_native",
        "false_vlm",
        "not_scored",
        "correct",
    ]
    agg = routing.aggregate(outcomes)
    assert agg["false_native_rate"] == 0.5
    assert agg["false_vlm_rate"] == 1.0
    assert agg["routing_recall"] == 0.5
    assert agg["routing_precision"] == 0.5
    assert agg["by_tag"]["scan"] == {"false_native": 1}
    assert agg["reason_agreement"] == 0.0


def test_unknown_classifier_reason_is_rejected():
    with pytest.raises(ValueError, match="unknown classifier reasons"):
        PageLabel(expected_route="vlm_required", expected_classifier_reasons=["chinese"])
