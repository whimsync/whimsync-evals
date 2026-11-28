"""
Gemini judge for what deterministic checks can't decide: reading order of complex
layouts, meaning-preserving formatting differences, figure/chart interpretation,
visual omissions, hallucinations and semantic table fidelity.

Each request carries the rendered source page image(s), the gold Markdown, the
candidate Markdown and a fixed rubric, and must answer with a strict JSON schema.
The full request (prompt text, image files, hashes) and raw response are saved
under the run's `judge/` folder for review.
"""

import hashlib
import re
import threading
import time
from pathlib import Path
from typing import Literal

import pymupdf
from pydantic import BaseModel, Field, ValidationError

from .config import JUDGE_MODEL, JUDGE_RPM, JUDGE_TEMPERATURE, gemini_api_key, write_json
from .dataset import sha256_text

RENDER_SCALE = 1.5
MAX_IMAGES = 10
MAX_ATTEMPTS = 4
MAX_RETRY_WAIT_SECONDS = 120

ErrorType = Literal[
    "missing_content",
    "hallucinated_content",
    "missing_numeric_value",
    "wrong_numeric_value",
    "wrong_reading_order",
    "broken_table",
    "wrong_table_structure",
    "missing_visual_content",
    "wrong_heading_structure",
    "other",
]


class CriticalError(BaseModel):
    type: ErrorType
    evidence: str = Field(description="Quote or describe the specific problem")


class Verdict(BaseModel):
    overall_score: int = Field(ge=1, le=5)
    text_fidelity: int = Field(ge=1, le=5)
    reading_order: int = Field(ge=1, le=5)
    table_fidelity: int | None = Field(
        default=None, ge=1, le=5, description="null when the page has no table"
    )
    visual_fidelity: int | None = Field(
        default=None, ge=1, le=5, description="null when the page has no figure/chart/diagram"
    )
    critical_errors: list[CriticalError] = Field(default_factory=list)
    summary: str


RUBRIC = """\
You are evaluating a PDF-to-Markdown converter. You are given the source page image(s),
a GOLD Markdown transcription (human-verified reference) and a CANDIDATE Markdown produced
by the converter for the same page(s).

Judge how faithfully the CANDIDATE represents the SOURCE, using GOLD as the reference.
Score each dimension from 1 (unusable) to 5 (equivalent to gold):

- text_fidelity: all text present and correct; no invented text.
- reading_order: content appears in the order a person would read it (columns, sidebars, footers).
- table_fidelity: tables keep the same rows, columns, headers and cell values. Ignore harmless
  Markdown differences (alignment markers, spacing, bold, HTML vs pipe tables). null if no table.
- visual_fidelity: figures, charts and diagrams are represented (data, labels, meaning) as well as
  the gold does. null if the page has no figure, chart or diagram.
- overall_score: would a downstream reader or retrieval system lose or be misled by anything?

Formatting that preserves meaning is NOT an error. Do not reward or penalise length itself.
List every critical_error that changes meaning: a missing or wrong number, missing section,
hallucinated content, a broken table, wrong reading order. Quote the evidence briefly.
Respond with JSON only, matching the schema."""

PROMPT_VERSION = hashlib.sha256(RUBRIC.encode()).hexdigest()[:12]

_RETRY_DELAY_RE = re.compile(r"retry(?:Delay)?['\"]?\s*[:=]?\s*['\"]?(\d+(?:\.\d+)?)s", re.I)


class _RateLimiter:
    """Spaces request starts at least 60/rpm seconds apart, across threads."""

    def __init__(self, rpm: float):
        self.interval = 60.0 / rpm if rpm > 0 else 0.0
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        if not self.interval:
            return
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next)
            self._next = start + self.interval
        if start > now:
            time.sleep(start - now)


_limiter = _RateLimiter(JUDGE_RPM)
_client = None


def _get_client():
    global _client
    if _client is None:
        from google import genai

        key = gemini_api_key()
        if not key:
            raise RuntimeError("GEMINI_API_KEY is not set; the judge needs it (or use --no-judge)")
        _client = genai.Client(api_key=key)
    return _client


def render_pages(pdf_path: Path, pages: list[int], out_dir: Path, stem: str) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    with pymupdf.open(pdf_path) as doc:
        for page in pages[:MAX_IMAGES]:
            path = out_dir / f"{stem}-p{page:03d}.png"
            if not path.exists():
                doc[page - 1].get_pixmap(matrix=pymupdf.Matrix(RENDER_SCALE, RENDER_SCALE)).save(
                    path
                )
            paths.append(path)
    return paths


def _retry_wait(error: Exception, attempt: int) -> float | None:
    """Seconds to wait before retrying, or None if the error is not retryable."""
    text = str(error)
    lowered = text.lower()
    retryable = any(
        s in lowered
        for s in (
            "429",
            "resource_exhausted",
            "503",
            "500",
            "unavailable",
            "overloaded",
            "deadline",
        )
    )
    if not retryable or attempt >= MAX_ATTEMPTS:
        return None
    m = _RETRY_DELAY_RE.search(text)
    wait = float(m.group(1)) if m else 2.0 * 2 ** (attempt - 1)
    return wait if wait <= MAX_RETRY_WAIT_SECONDS else None


def _empty_candidate_verdict() -> dict:
    return Verdict(
        overall_score=1,
        text_fidelity=1,
        reading_order=1,
        critical_errors=[
            CriticalError(type="missing_content", evidence="Candidate output is empty")
        ],
        summary="Candidate produced no text for this unit (scored without calling the judge).",
    ).model_dump()


def judge_unit(
    pdf_path: Path,
    pages: list[int],
    gold_md: str,
    candidate_md: str,
    out_dir: Path,
    unit_id: str,
    model: str = JUDGE_MODEL,
) -> dict:
    """Judges one unit and writes `<out_dir>/<unit_id>.json`; returns that record."""
    record: dict = {
        "unit": unit_id,
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "temperature": JUDGE_TEMPERATURE,
        "gold_sha256": sha256_text(gold_md),
        "candidate_sha256": sha256_text(candidate_md),
        "verdict": None,
        "synthetic": False,
        "error": None,
    }
    if not candidate_md.strip():
        record.update(verdict=_empty_candidate_verdict(), synthetic=True)
        write_json(out_dir / f"{unit_id}.json", record)
        return record

    from google.genai import types

    images = render_pages(pdf_path, pages, out_dir, unit_id)
    text = (
        f"{RUBRIC}\n\nSOURCE PAGES: {', '.join(map(str, pages))} (images attached in order)"
        f"\n\n<GOLD>\n{gold_md}\n</GOLD>\n\n<CANDIDATE>\n{candidate_md}\n</CANDIDATE>"
    )
    contents = [
        *(types.Part.from_bytes(data=p.read_bytes(), mime_type="image/png") for p in images),
        text,
    ]
    config = types.GenerateContentConfig(
        temperature=JUDGE_TEMPERATURE,
        response_mime_type="application/json",
        response_json_schema=Verdict.model_json_schema(),
    )
    record["request"] = {
        "prompt": text,
        "images": [p.name for p in images],
        "image_sha256": [hashlib.sha256(p.read_bytes()).hexdigest() for p in images],
    }

    attempt = 0
    while True:
        attempt += 1
        _limiter.wait()
        start = time.perf_counter()
        try:
            response = _get_client().models.generate_content(
                model=model, contents=contents, config=config
            )
        except Exception as e:  # noqa: BLE001 - classified below
            wait = _retry_wait(e, attempt)
            if wait is None:
                record["error"] = f"{type(e).__name__}: {str(e)[:500]}"
                break
            time.sleep(wait)
            continue
        raw = response.text or ""
        usage = getattr(response, "usage_metadata", None)
        record["response"] = {
            "raw": raw,
            "latency_ms": round((time.perf_counter() - start) * 1000, 1),
            "input_tokens": getattr(usage, "prompt_token_count", None),
            "output_tokens": getattr(usage, "candidates_token_count", None),
            "attempts": attempt,
        }
        try:
            record["verdict"] = Verdict.model_validate_json(raw).model_dump()
        except ValidationError as e:
            record["error"] = f"invalid judge response: {e.errors()[:3]}"
        break

    write_json(out_dir / f"{unit_id}.json", record)
    return record
