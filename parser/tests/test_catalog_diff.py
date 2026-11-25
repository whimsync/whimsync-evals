from bench.catalog import Catalog, MetricSpec, infer_spec
from bench.diff import diff_metrics, judge

CAT = Catalog(
    [
        MetricSpec("f1", "F1"),
        MetricSpec("latency_ms", "Latency", better="lower", unit="ms", tolerance="relative"),
        MetricSpec("pages", "Pages", better="none", unit="count"),
    ]
)


def test_judge_uses_direction_and_tolerance():
    assert judge("f1", 0.80, 0.70, CAT, 0.02) == "regression"
    assert judge("f1", 0.80, 0.81, CAT, 0.02) == "same"
    assert judge("f1", 0.80, 0.90, CAT, 0.02) == "improved"
    # relative tolerance (25%) for timing, and lower is better
    assert judge("latency_ms", 1000, 1200, CAT, 0.02) == "same"
    assert judge("latency_ms", 1000, 1300, CAT, 0.02) == "regression"
    assert judge("latency_ms", 1000, 700, CAT, 0.02) == "improved"
    assert judge("pages", 1, 50, CAT, 0.02) == "same"
    assert judge("f1", None, 0.5, CAT, 0.02) == "n/a"


def test_unknown_keys_are_inferred():
    assert infer_spec("failure_rate").better == "lower"
    assert infer_spec("latency_p95_ms").unit == "ms"
    assert infer_spec("tag:noise:claim_f1").better == "higher"
    assert infer_spec("vlm_pages").better == "none"
    assert judge("failure_rate", 0.0, 0.2, Catalog([]), 0.02) == "regression"


def test_diff_skips_metrics_missing_on_both_sides_and_keeps_order():
    deltas = diff_metrics({"f1": 0.5, "x": None}, {"f1": 0.6, "latency_ms": 5}, CAT)
    assert [d.metric for d in deltas] == ["f1", "latency_ms"]
    assert deltas[1].verdict == "n/a"


def test_catalog_json_round_trip_and_merge():
    restored = Catalog.from_json(CAT.to_json())
    assert [s.key for s in restored] == ["f1", "latency_ms", "pages"]
    merged = Catalog([MetricSpec("f1", "Custom")]).merged(CAT)
    assert merged.get("f1").label == "Custom"
    assert "pages" in merged


def test_format_by_unit():
    assert MetricSpec("f1", "F1").format(1) == "1.000"  # JS writes 1.0 as 1
    assert MetricSpec("n", "N", unit="count").format(3) == "3"
    assert MetricSpec("t", "T", unit="ms").format(1234.6) == "1235"
    assert MetricSpec("f1", "F1").format(None) == "-"
