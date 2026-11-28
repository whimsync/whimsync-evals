"""
Synthetic case templates: one Markdown file per case in `synth/templates/`.

The file body is the exact ground truth that gets rendered into PDFs. Optional
frontmatter configures rendering:

    ---
    description: Two-column newsletter layout (tests reading order).
    columns: 2
    ---
    # Engineering Newsletter
    ...

Adding a case = adding a file. The case name is the file stem.
"""

from dataclasses import dataclass
from pathlib import Path

CASES_DIR = Path(__file__).resolve().parent / "templates"

_FRONTMATTER_FIELDS = {"description": str, "columns": int}


@dataclass(frozen=True)
class BenchCase:
    name: str
    markdown: str
    columns: int = 1
    description: str = ""


def parse_case(name: str, text: str) -> BenchCase:
    """Splits optional `---` frontmatter from the ground-truth body and validates it."""
    fields: dict = {}
    body = text
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end < 0:
            raise ValueError(f"Case {name!r}: frontmatter is not closed with '---'")
        for line in text[4:end].splitlines():
            if not line.strip():
                continue
            key, sep, value = line.partition(":")
            key = key.strip()
            if not sep or key not in _FRONTMATTER_FIELDS:
                raise ValueError(
                    f"Case {name!r}: unknown frontmatter line {line!r} "
                    f"(allowed keys: {sorted(_FRONTMATTER_FIELDS)})"
                )
            fields[key] = _FRONTMATTER_FIELDS[key](value.strip())
        body = text[end + len("\n---\n") :]
    if not body.strip():
        raise ValueError(f"Case {name!r} has no ground-truth content")
    if fields.get("columns", 1) < 1:
        raise ValueError(f"Case {name!r}: columns must be >= 1")
    return BenchCase(name=name, markdown=body, **fields)


def load_cases(cases_dir: Path = CASES_DIR) -> list[BenchCase]:
    paths = sorted(cases_dir.glob("*.md"))
    if not paths:
        raise FileNotFoundError(f"No case files (*.md) in {cases_dir}")
    return [parse_case(p.stem, p.read_text(encoding="utf-8")) for p in paths]


def get_cases(names: list[str] | None = None, cases_dir: Path = CASES_DIR) -> list[BenchCase]:
    """All cases, or the named ones (in the given order)."""
    cases = load_cases(cases_dir)
    if not names:
        return cases
    by_name = {c.name: c for c in cases}
    unknown = [n for n in names if n not in by_name]
    if unknown:
        raise ValueError(f"Unknown case(s): {unknown}. Available: {sorted(by_name)}")
    return [by_name[n] for n in names]
