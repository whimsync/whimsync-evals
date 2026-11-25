import pytest

from bench import metrics

REF = """\
# Title

Intro paragraph with a [link](https://example.com/docs).

## Section

| A | B |
|---|---|
| 1 | 2 |
| 3 | 4 |
"""


def test_identical_scores_perfect():
    for key, value in metrics.score(REF, REF).items():
        assert value == pytest.approx(1.0), key


def test_markdown_noise_is_ignored():
    noisy = REF.replace("# Title", "# **Title**").replace("Intro", "*Intro*")
    assert metrics.text_similarity(noisy, REF) == pytest.approx(1.0)
    assert metrics.word_prf(noisy, REF)["f1"] == pytest.approx(1.0)


def test_empty_prediction_scores_zero():
    s = metrics.score("", REF)
    for key in (
        "text_similarity",
        "word_recall",
        "heading_f1",
        "table_cell_accuracy",
        "link_recall",
    ):
        assert s[key] == 0.0, key


def test_inapplicable_metrics_are_none():
    s = metrics.score("plain words", "plain words")
    assert s["heading_f1"] is None
    assert s["table_cell_accuracy"] is None
    assert s["link_recall"] is None


def test_word_prf_dropped_vs_hallucinated():
    w = metrics.word_prf("alpha beta", "alpha beta gamma delta")
    assert (w["precision"], w["recall"]) == (1.0, 0.5)
    w = metrics.word_prf("alpha beta gamma delta", "alpha beta")
    assert (w["precision"], w["recall"]) == (0.5, 1.0)


def test_reading_order_penalizes_shuffle():
    ref = "one two three four"
    assert metrics.reading_order(ref, ref) == 1.0
    assert metrics.reading_order("three four one two", ref) == 0.5


def test_heading_levels_are_relative():
    ref = "## A\n\n### B"
    assert metrics.heading_prf("# A\n\n## B", ref)["f1"] == 1.0
    flat = metrics.heading_prf("# A\n\n# B", ref)
    assert flat["f1"] == 0.5
    assert flat["text_only_f1"] == 1.0


def test_table_cell_accuracy():
    pred = "| A | B |\n|---|---|\n| 1 | 9 |\n| 3 | 4 |"
    t = metrics.table_metrics(pred, REF)
    assert t == {"cell_accuracy": pytest.approx(5 / 6), "detection_recall": 1.0, "shape_match": 1.0}
    assert metrics.table_metrics(pred, "no tables here") is None


def test_link_metrics_detect_split_anchor():
    ref = "see [PostgreSQL docs](https://pg.org)"
    split = "see [PostgreSQL](https://pg.org) [docs](https://pg.org)"
    assert metrics.link_recall(split, ref) == 1.0
    assert metrics.link_exact(split, ref) == 0.0
    assert metrics.link_exact(ref, ref) == 1.0
    assert metrics.link_recall("x", "no links") is None


def test_every_scored_key_is_in_the_catalog_and_vice_versa():
    """Guards the registry: a scorer must declare exactly the keys it returns."""
    scored = set(metrics.score(REF, REF))
    declared = {s.key for s in metrics.SCORE_SPECS}
    assert scored == declared
    assert all(k in metrics.CATALOG for k in scored)
