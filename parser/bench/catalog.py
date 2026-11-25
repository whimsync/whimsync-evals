"""
Metric catalog: the single description of every metric a suite reports.

Console tables, `compare` and the HTML report never hard-code metric names; they
read a `Catalog`. Runners embed their catalog in each results file (`"catalog"`),
so analysis tools work for any suite, including metrics added later. Keys that a
catalog doesn't list (old results files, dynamic keys like `tag:<tag>:claim_f1`)
get a spec inferred from their name.
"""

from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass
from typing import Literal

Better = Literal["higher", "lower", "none"]
Unit = Literal["ratio", "ms", "s", "count"]
Tolerance = Literal["absolute", "relative"]

# Timing is noisy, so timing metrics regress only beyond this relative change.
RELATIVE_TOLERANCE = 0.25


@dataclass(frozen=True)
class MetricSpec:
    key: str
    label: str
    short: str = ""
    """Column header for console tables (defaults to `key`)."""
    better: Better = "higher"
    """Direction of improvement; `none` = informational, never flagged."""
    unit: Unit = "ratio"
    tolerance: Tolerance = "absolute"
    """`absolute` uses the compare threshold; `relative` uses RELATIVE_TOLERANCE."""
    summary: bool = False
    """Shown in the console summary table (everything is stored either way)."""
    description: str = ""

    @property
    def column(self) -> str:
        return self.short or self.key

    def format(self, value: float | int | None) -> str:
        if value is None:
            return "-"
        if isinstance(value, bool):
            return "yes" if value else "no"
        if self.unit == "count":
            return f"{value:g}" if isinstance(value, float) else str(value)
        if self.unit == "ms":
            return f"{value:.0f}"
        return f"{value:.3f}"

    def to_json(self) -> dict:
        return asdict(self)


_LOWER_HINTS = ("latency", "failure", "seconds", "noise_claims", "_ms")
_NEUTRAL_KEYS = {"n", "vlm_pages", "attempts"}


def infer_spec(key: str) -> MetricSpec:
    """Best-effort spec for a key no catalog describes."""
    if key in _NEUTRAL_KEYS:
        return MetricSpec(key, key, better="none", unit="count")
    unit: Unit = "ms" if key.endswith("_ms") else ("s" if "seconds" in key else "ratio")
    lower = any(h in key for h in _LOWER_HINTS)
    return MetricSpec(
        key,
        key,
        better="lower" if lower else "higher",
        unit=unit,
        tolerance="relative" if unit in ("ms", "s") else "absolute",
    )


class Catalog:
    """Ordered collection of MetricSpecs with inference for unknown keys."""

    def __init__(self, specs: Iterable[MetricSpec]):
        self._specs: dict[str, MetricSpec] = {}
        for spec in specs:
            if spec.key in self._specs:
                raise ValueError(f"Duplicate metric key in catalog: {spec.key}")
            self._specs[spec.key] = spec

    def __iter__(self) -> Iterator[MetricSpec]:
        return iter(self._specs.values())

    def __contains__(self, key: str) -> bool:
        return key in self._specs

    def get(self, key: str) -> MetricSpec:
        return self._specs.get(key) or infer_spec(key)

    def summary_specs(self) -> list[MetricSpec]:
        return [s for s in self if s.summary]

    def ordered(self, keys: Iterable[str]) -> list[str]:
        """Sorts keys in catalog order, unknown keys last (alphabetically)."""
        order = {k: i for i, k in enumerate(self._specs)}
        return sorted(set(keys), key=lambda k: (k not in order, order.get(k, 0), k))

    def merged(self, other: "Catalog") -> "Catalog":
        """This catalog plus specs from `other` it doesn't define."""
        return Catalog([*self, *(s for s in other if s.key not in self)])

    def to_json(self) -> list[dict]:
        return [s.to_json() for s in self]

    @classmethod
    def from_json(cls, data: list[dict] | None) -> "Catalog":
        fields = set(MetricSpec.__dataclass_fields__)
        return cls(MetricSpec(**{k: v for k, v in d.items() if k in fields}) for d in data or [])
