"""Plain-text table rendering and progress logging for the CLIs."""

import sys
from collections.abc import Sequence


def log(message: str) -> None:
    """Progress output goes to stderr so stdout stays clean for tables."""
    print(f"[bench] {message}", file=sys.stderr)


def table(header: Sequence[str], rows: Sequence[Sequence[str]], indent: str = "") -> str:
    widths = [len(h) for h in header]
    for row in rows:
        widths = [max(w, len(c)) for w, c in zip(widths, row, strict=True)]
    line = lambda cells: indent + "  ".join(c.ljust(w) for c, w in zip(cells, widths, strict=True))  # noqa: E731
    return "\n".join([line(header), indent + "  ".join("-" * w for w in widths), *map(line, rows)])
