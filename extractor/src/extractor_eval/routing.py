"""
Routing evaluation: did each labelled page go where it should?

Labels (`page_labels[n].expected_route`) are tier-independent:

- `vlm_required` routed native  -> false_native (critical: content is likely lost)
- `native_acceptable` routed VLM -> false_vlm (unnecessary cost)
- `either_acceptable`            -> reported, never penalised

`expected_classifier_reasons` are compared with the reasons the classifier actually
gave (from trace.json); human `tags` group the failures.
"""

from collections import Counter, defaultdict

from .dataset import Case


def page_outcomes(case: Case, trace: dict | None) -> list[dict]:
    if not trace:
        return []
    by_page = {p["page_number"]: p for p in trace.get("pages", [])}
    outcomes = []
    for key, label in sorted(case.page_labels.items(), key=lambda kv: int(kv[0])):
        if label.expected_route is None:
            continue
        page = by_page.get(int(key))
        if page is None:
            continue
        actual = page["route"]
        reasons = (page.get("complexity") or {}).get("reasons", [])
        if label.expected_route == "either_acceptable" or actual == "empty":
            outcome = "not_scored"
        elif label.expected_route == "vlm_required":
            outcome = "correct" if actual == "vlm" else "false_native"
        else:
            outcome = "correct" if actual == "native" else "false_vlm"
        expected_reasons = set(label.expected_classifier_reasons)
        outcomes.append(
            {
                "case": case.id,
                "page": int(key),
                "expected_route": label.expected_route,
                "actual_route": actual,
                "vlm_failed": page.get("vlm_failed", False),
                "outcome": outcome,
                "classifier_reasons": reasons,
                "expected_classifier_reasons": sorted(expected_reasons),
                "reasons_match": set(reasons) == expected_reasons if expected_reasons else None,
                "tags": label.tags,
            }
        )
    return outcomes


def aggregate(outcomes: list[dict]) -> dict:
    """Precision/recall of the VLM decision over scored pages, plus grouped failures."""
    scored = [o for o in outcomes if o["outcome"] != "not_scored"]
    required = [o for o in scored if o["expected_route"] == "vlm_required"]
    native_ok = [o for o in scored if o["expected_route"] == "native_acceptable"]
    routed_vlm = [o for o in scored if o["actual_route"] == "vlm"]
    true_vlm = [o for o in required if o["actual_route"] == "vlm"]
    false_native = [o for o in scored if o["outcome"] == "false_native"]
    false_vlm = [o for o in scored if o["outcome"] == "false_vlm"]

    by_reason: dict[str, Counter] = defaultdict(Counter)
    by_tag: dict[str, Counter] = defaultdict(Counter)
    for o in scored:
        for reason in o["classifier_reasons"] or ["(none)"]:
            by_reason[reason][o["outcome"]] += 1
        for tag in o["tags"] or ["(untagged)"]:
            by_tag[tag][o["outcome"]] += 1

    with_reasons = [o for o in outcomes if o["reasons_match"] is not None]
    return {
        "labelled_pages": len(outcomes),
        "scored_pages": len(scored),
        "routing_precision": len(true_vlm) / len(routed_vlm) if routed_vlm else None,
        "routing_recall": len(true_vlm) / len(required) if required else None,
        "false_native_rate": len(false_native) / len(required) if required else None,
        "false_vlm_rate": len(false_vlm) / len(native_ok) if native_ok else None,
        "reason_agreement": (
            sum(o["reasons_match"] for o in with_reasons) / len(with_reasons)
            if with_reasons
            else None
        ),
        "false_native": len(false_native),
        "false_vlm": len(false_vlm),
        "failures": [o for o in scored if o["outcome"] != "correct"],
        "by_reason": {k: dict(v) for k, v in sorted(by_reason.items())},
        "by_tag": {k: dict(v) for k, v in sorted(by_tag.items())},
    }
