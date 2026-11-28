"""Environment configuration (read from whimsync-evals/.env) and small shared helpers."""

import json
import os
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from . import PROJECT_DIR, REPO_DIR

load_dotenv(REPO_DIR / ".env")

# Environment variables forwarded into the extractor container (by name only, so
# secret values never appear on the docker command line).
CONTAINER_ENV = (
    "GEMINI_API_KEY",
    "GEMINI_MODEL",
    "VLM_CONCURRENCY",
    "VLM_MAX_RETRIES",
    "VLM_DELAY_SECONDS",
    "VLM_RPM",
)

JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "gemini-3.8-flash")
JUDGE_TEMPERATURE = float(os.environ.get("JUDGE_TEMPERATURE", "0"))
JUDGE_RPM = float(os.environ.get("JUDGE_RPM", "12"))
JUDGE_CONCURRENCY = int(os.environ.get("JUDGE_CONCURRENCY", "2"))
CONTAINER_TIMEOUT_SECONDS = int(os.environ.get("EXTRACTOR_EVAL_TIMEOUT", "3600"))


def gemini_api_key() -> str | None:
    return os.environ.get("GEMINI_API_KEY") or None


@lru_cache
def pricing() -> dict[str, dict[str, float]]:
    """USD per million tokens by model, from `pricing.json` (empty -> cost not reported)."""
    path = PROJECT_DIR / "pricing.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


def cost_usd(
    model: str | None, input_tokens: int | None, output_tokens: int | None
) -> float | None:
    rates = pricing().get(model or "")
    if not rates or input_tokens is None:
        return None
    return round(
        input_tokens / 1e6 * rates.get("input_per_million", 0)
        + (output_tokens or 0) / 1e6 * rates.get("output_per_million", 0),
        6,
    )


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def write_json(path: Path, data: Any) -> None:
    """Atomic write: readers (the UI) never see a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)
