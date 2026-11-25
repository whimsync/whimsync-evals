"""
Diff engine shared by `bench.compare` (terminal) and `bench.report` (HTML).

Given two metric dicts and a Catalog, it decides for each metric whether B is a
regression, an improvement, or within tolerance, using the metric's direction
and tolerance type from the catalog.
"""

from dataclasses import dataclass
from typing import Literal

from .catalog import RELATIVE_TOLERANCE, Catalog

Verdict = Literal["regression", "improved", "same", "n/a"]

DEFAULT_THRESHOLD = 0.02


@dataclass(frozen=True)
class Delta:
    metric: str
    a: float | None
    b: float | None
    verdict: Verdict

    @property
    def delta(self) -> float | None:
        if self.a is None or self.b is None:
            return None
        return self.b - self.a


def judge(
    metric: str, a: float | None, b: float | None, catalog: Catalog, threshold: float
) -> Verdict:
    if a is None or b is None:
        return "n/a"
    spec = catalog.get(metric)
    if spec.better == "none":
        return "same"
    d = b - a
    tol = abs(a) * RELATIVE_TOLERANCE if spec.tolerance == "relative" else threshold
    if spec.better == "lower":
        d = -d
    if d < -tol:
        return "regression"
    if d > tol:
        return "improved"
    return "same"


def diff_metrics(
    a: dict[str, float | None],
    b: dict[str, float | None],
    catalog: Catalog,
    threshold: float = DEFAULT_THRESHOLD,
) -> list[Delta]:
    """One Delta per metric present in either side (both-missing metrics are skipped)."""
    out = []
    for key in catalog.ordered(set(a) | set(b)):
        va, vb = a.get(key), b.get(key)
        if va is None and vb is None:
            continue
        out.append(Delta(key, va, vb, judge(key, va, vb, catalog, threshold)))
    return out


def count_regressions(deltas: list[Delta]) -> int:
    return sum(d.verdict == "regression" for d in deltas)
