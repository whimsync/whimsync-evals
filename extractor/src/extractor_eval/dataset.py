"""
`dataset.json`: the committed manifest of cases, gold Markdown and page labels.

Gold lives next to each case, by convention:

    cases/<id>/source.pdf
    cases/<id>/expected/pages/NNN.md          quality_mode "pages"  (one file per page)
    cases/<id>/expected/ranges/<file>.md      quality_mode "ranges" (path set per range)
    cases/<id>/expected/links.json            optional link expectations

A scoring *unit* is one gold page (`p001`) or one quality range (its id).
"""

import hashlib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from . import DATASET_PATH, PROJECT_DIR
from .config import read_json, write_json

# The classifier's reason names (apps/extractor/src/parsing/complexity.py).
CLASSIFIER_REASONS = {
    "scanned",
    "garbled",
    "no-text",
    "sparse-text",
    "vector-table",
    "embedded-images",
    "empty",
}

Route = Literal["native_acceptable", "vlm_required", "either_acceptable"]
Status = Literal["verified", "draft"]


class PageLabel(BaseModel):
    expected_route: Route | None = None
    expected_classifier_reasons: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    required_phrases: list[str] = Field(default_factory=list)

    @field_validator("expected_classifier_reasons")
    @classmethod
    def _known_reasons(cls, value: list[str]) -> list[str]:
        unknown = set(value) - CLASSIFIER_REASONS
        if unknown:
            raise ValueError(
                f"unknown classifier reasons {sorted(unknown)}; use tags for human labels "
                f"(valid: {sorted(CLASSIFIER_REASONS)})"
            )
        return value


class QualityRange(BaseModel):
    id: str
    start_page: int = Field(ge=1)
    end_page: int = Field(ge=1)
    gold: str
    tags: list[str] = Field(default_factory=list)
    required_phrases: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _ordered(self) -> "QualityRange":
        if self.end_page < self.start_page:
            raise ValueError(f"range {self.id}: end_page < start_page")
        return self

    @property
    def pages(self) -> list[int]:
        return list(range(self.start_page, self.end_page + 1))


class Unit(BaseModel):
    """One scored piece of a case: a gold page or a quality range."""

    id: str
    pages: list[int]
    gold_path: Path
    tags: list[str] = Field(default_factory=list)
    required_phrases: list[str] = Field(default_factory=list)


class Case(BaseModel):
    id: str
    pdf: str
    sha256: str
    quality_mode: Literal["pages", "ranges"]
    suite: str = "core"
    description: str = ""
    gold_status: Status = "draft"
    labels_status: Status = "draft"
    quality_ranges: list[QualityRange] = Field(default_factory=list)
    page_labels: dict[str, PageLabel] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _ranges_match_mode(self) -> "Case":
        if self.quality_mode == "ranges" and not self.quality_ranges:
            raise ValueError(f"case {self.id}: quality_mode 'ranges' needs quality_ranges")
        return self

    @property
    def dir(self) -> Path:
        return PROJECT_DIR / "cases" / self.id

    @property
    def pdf_path(self) -> Path:
        return PROJECT_DIR / self.pdf

    @property
    def links_path(self) -> Path:
        return self.dir / "expected" / "links.json"

    @property
    def small(self) -> bool:
        """Small cases get full quality scoring and performance repeats."""
        return self.quality_mode == "pages"

    def label(self, page: int) -> PageLabel | None:
        return self.page_labels.get(str(page))

    def units(self) -> list[Unit]:
        if self.quality_mode == "ranges":
            return [
                Unit(
                    id=r.id,
                    pages=r.pages,
                    gold_path=PROJECT_DIR / r.gold,
                    tags=sorted(
                        {
                            *r.tags,
                            *(t for p in r.pages for t in (self.label(p) or PageLabel()).tags),
                        }
                    ),
                    required_phrases=r.required_phrases,
                )
                for r in self.quality_ranges
            ]
        pages_dir = self.dir / "expected" / "pages"
        units = []
        for gold in sorted(pages_dir.glob("[0-9][0-9][0-9].md")):
            page = int(gold.stem)
            label = self.label(page) or PageLabel()
            units.append(
                Unit(
                    id=f"p{page:03d}",
                    pages=[page],
                    gold_path=gold,
                    tags=label.tags,
                    required_phrases=label.required_phrases,
                )
            )
        return units

    def expected_links(self) -> list[dict]:
        data = read_json(self.links_path, {"links": []})
        return data.get("links", [])


class Dataset(BaseModel):
    version: int = 1
    cases: list[Case]

    def get(self, case_id: str) -> Case:
        for case in self.cases:
            if case.id == case_id:
                return case
        raise KeyError(f"Unknown case '{case_id}'. Known: {', '.join(c.id for c in self.cases)}")

    def select(self, ids: list[str] | None = None, suites: list[str] | None = None) -> list[Case]:
        cases = [self.get(i) for i in ids] if ids else list(self.cases)
        if suites:
            cases = [c for c in cases if c.suite in suites]
        return cases


def load(path: Path | None = None) -> Dataset:
    path = path or DATASET_PATH
    data = read_json(path)
    if data is None:
        raise FileNotFoundError(
            f"{path} not found; run `extractor-eval dataset synth` or `dataset add`"
        )
    return Dataset.model_validate(data)


def save(dataset: Dataset, path: Path | None = None) -> None:
    write_json(path or DATASET_PATH, dataset.model_dump(mode="json", exclude_defaults=False))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def problems(case: Case) -> list[str]:
    """Integrity issues that make a case unusable (missing files, hash mismatch)."""
    issues = []
    if not case.pdf_path.exists():
        return [f"{case.id}: missing PDF {case.pdf}"]
    actual = sha256_file(case.pdf_path)
    if actual != case.sha256:
        issues.append(
            f"{case.id}: PDF sha256 mismatch (manifest {case.sha256[:12]}, file {actual[:12]})"
        )
    units = case.units()
    if not units:
        issues.append(f"{case.id}: no gold Markdown found")
    for unit in units:
        if not unit.gold_path.exists():
            issues.append(f"{case.id}: missing gold {unit.gold_path.relative_to(PROJECT_DIR)}")
    return issues


# ---- edits shared by the CLI and the web UI ------------------------------------------

CASE_ID_RE = r"[a-z0-9][a-z0-9-]*"


def unit(case: Case, unit_id: str) -> Unit:
    for u in case.units():
        if u.id == unit_id:
            return u
    raise KeyError(f"case {case.id} has no unit '{unit_id}'")


def write_gold(case_id: str, unit_id: str, markdown: str) -> Path:
    """Replace one unit's gold Markdown (the file the scorer reads)."""
    case = load().get(case_id)
    path = unit(case, unit_id).gold_path.resolve()
    if not path.is_relative_to(case.dir.resolve()):
        raise ValueError("gold path is outside the case directory")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown.rstrip() + "\n", encoding="utf-8")
    return path


def set_label(case_id: str, page: int, **fields) -> PageLabel:
    """Update one page's label; only the given fields change. Validated before saving."""
    data = load()
    case = data.get(case_id)
    n_pages = page_count(case)
    if not 1 <= page <= n_pages:
        raise ValueError(f"page {page} is outside 1-{n_pages}")
    current = case.page_labels.get(str(page)) or PageLabel()
    updated = PageLabel.model_validate(
        current.model_dump() | {k: v for k, v in fields.items() if v is not None}
    )
    case.page_labels[str(page)] = updated
    save(data)
    return updated


def update_case(
    case_id: str,
    *,
    suite: str | None = None,
    description: str | None = None,
    gold_status: str | None = None,
    labels_status: str | None = None,
) -> Case:
    data = load()
    case = data.get(case_id)
    for value in (gold_status, labels_status):
        if value not in (None, "verified", "draft"):
            raise ValueError("status must be 'verified' or 'draft'")
    if suite is not None:
        if not suite.strip():
            raise ValueError("suite must not be empty")
        case.suite = suite.strip()
    if description is not None:
        case.description = description
    if gold_status:
        case.gold_status = gold_status
    if labels_status:
        case.labels_status = labels_status
    save(data)
    return case


def remove_case(case_id: str) -> None:
    """Drop a case from dataset.json and delete its folder (PDF + gold)."""
    import shutil

    data = load()
    case = data.get(case_id)
    data.cases = [c for c in data.cases if c.id != case_id]
    save(data)
    target = case.dir.resolve()
    if target.parent == (PROJECT_DIR / "cases").resolve() and target.exists():
        shutil.rmtree(target)


def page_count(case: Case) -> int:
    import pymupdf

    with pymupdf.open(case.pdf_path) as doc:
        return len(doc)
