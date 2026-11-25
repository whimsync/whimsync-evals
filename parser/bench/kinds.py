"""
Result kinds: how analysis tools read each suite's results file.

`compare` and `report` work on any suite through a `ResultKind`, which exposes a
run as `groups` (group -> metric -> value) and `cases` (case -> metric -> value)
plus its metric catalog. Adding a suite = writing its runner (any language) and
registering a ResultKind here (and a report view in `report/`).
"""

from dataclasses import dataclass, field
from statistics import fmean

from .catalog import Catalog
from .metrics import CATALOG as PARSER_CATALOG

Metrics = dict[str, float | None]


@dataclass(frozen=True)
class ResultKind:
    name: str
    title: str
    default_catalog: Catalog = field(default_factory=lambda: Catalog([]))

    def catalog(self, run: dict) -> Catalog:
        """The run's embedded catalog, completed with this kind's defaults."""
        return Catalog.from_json(run.get("catalog")).merged(self.default_catalog)

    def model(self, run: dict) -> str | None:
        raise NotImplementedError

    def groups(self, run: dict) -> dict[str, Metrics]:
        raise NotImplementedError

    def spread(self, run: dict) -> dict[str, Metrics]:
        """Optional std-dev per group/metric (repeated runs); empty if not applicable."""
        return {}

    def cases(self, run: dict) -> dict[str, Metrics]:
        raise NotImplementedError


class ParserKind(ResultKind):
    def model(self, run: dict) -> str | None:
        return run.get("gemini_model")

    def groups(self, run: dict) -> dict[str, Metrics]:
        return {g: {k: v for k, v in m.items() if k != "n"} for g, m in run["summary"].items()}

    def cases(self, run: dict) -> dict[str, Metrics]:
        return {
            f"{r['doc_id']} [{r['tier']}]": r["metrics"]
            for r in run["results"]
            if not r.get("error")
        }


class ClaimsKind(ResultKind):
    GROUP = "all"

    def model(self, run: dict) -> str | None:
        return run.get("model")

    def groups(self, run: dict) -> dict[str, Metrics]:
        return {self.GROUP: {k: s.get("mean") for k, s in run["summary"].items()}}

    def spread(self, run: dict) -> dict[str, Metrics]:
        return {self.GROUP: {k: s.get("std") for k, s in run["summary"].items()}}

    def cases(self, run: dict) -> dict[str, Metrics]:
        """Per episode, averaged over repeated runs."""
        per: dict[str, list[dict]] = {}
        for r in run["results"]:
            if r.get("metrics"):
                per.setdefault(r["episodeId"], []).append(r["metrics"])
        out: dict[str, Metrics] = {}
        for episode, rows in per.items():
            out[episode] = {
                "claim_f1": fmean(m["claims"]["f1"] for m in rows),
                "mutation_accuracy": _mean_opt(m.get("mutationAccuracy") for m in rows),
                "evidence_validity": _mean_opt(m.get("evidenceValidity") for m in rows),
            }
        return out


def _mean_opt(values) -> float | None:
    vals = [v for v in values if v is not None]
    return fmean(vals) if vals else None


KINDS: dict[str, ResultKind] = {
    "document-parser": ParserKind("document-parser", "Document parser benchmark", PARSER_CATALOG),
    "claim-extractor": ClaimsKind("claim-extractor", "Claim extractor benchmark"),
}


def kind_of(run: dict) -> ResultKind:
    name = run.get("kind")
    if name not in KINDS:
        raise ValueError(f"Unknown results kind {name!r}. Known: {sorted(KINDS)}")
    return KINDS[name]
