"""Run provenance: which whimsync / whimsync-evals commits produced a results file."""

import subprocess
from datetime import UTC, datetime
from pathlib import Path

from . import EVALS_ROOT


def whimsync_root() -> Path:
    """Root of the whimsync checkout whose extractor is installed (the code under test)."""
    import src  # the editable whimsync-extractor package

    return Path(src.__file__).resolve().parents[3]


def git_sha(cwd: Path) -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=cwd,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def utc_timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def provenance() -> dict[str, str | None]:
    return {"git_sha": git_sha(whimsync_root()), "evals_sha": git_sha(EVALS_ROOT)}
