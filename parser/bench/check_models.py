"""
Smoke-test Gemini models for the VLM path: a text ping plus a real OCR call on a
synthetic scanned page, reporting availability, latency and OCR accuracy.

    uv run python -m bench.check_models
    uv run python -m bench.check_models --models gemini-2.5-flash,gemini-3.5-flash --case invoice
"""

import argparse
import sys
import tempfile
import time
from pathlib import Path

import pymupdf
from PIL import Image
from src.config import settings
from src.services.vlm_service import FULL_PAGE_SCAN_PROMPT

from .augment import parse_modes
from .cases import get_cases
from .generate import render_case
from .metrics import score

DEFAULT_MODELS = "gemini-2.5-flash,gemini-3.5-flash,gemini-3.5-flash-lite"


def _short_error(exc: Exception) -> str:
    msg = str(exc)
    for marker in ("API key not valid", "NOT_FOUND", "PERMISSION_DENIED", "RESOURCE_EXHAUSTED"):
        if marker in msg:
            return f"{type(exc).__name__}: {marker}"
    return f"{type(exc).__name__}: {msg[:140]}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check which Gemini models work for VLM parsing.")
    parser.add_argument("--models", default=DEFAULT_MODELS)
    parser.add_argument("--case", default="simple_table", help="Bench case used for the OCR test")
    parser.add_argument("--mode", default="scanned_noisy", help="Render mode of the OCR test page")
    args = parser.parse_args(argv)

    if not settings.GEMINI_API_KEY:
        print("GEMINI_API_KEY is not set (set it in whimsync-evals/.env).")
        return 1

    from google import genai

    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    case = get_cases([args.case])[0]

    with tempfile.TemporaryDirectory() as d:
        doc = render_case(case, parse_modes([args.mode])[0], Path(d))
        pdf = pymupdf.open(str(doc.pdf_path))
        page_png = Path(d) / "page.png"
        pdf[0].get_pixmap(matrix=pymupdf.Matrix(2.5, 2.5)).save(str(page_png))
        pdf.close()
        gt = doc.md_path.read_text(encoding="utf-8")

        print(f"configured GEMINI_MODEL = {settings.GEMINI_MODEL}")
        print(f"OCR test page: {doc.doc_id}\n")
        header = f"{'model':<26}{'text ping':<14}{'vision OCR':<14}{'text_sim':>9}{'w_recall':>9}{'tbl_cell':>9}"
        print(header)
        print("-" * len(header))

        working = 0
        for model in [m.strip() for m in args.models.split(",") if m.strip()]:
            t = time.perf_counter()
            try:
                client.models.generate_content(model=model, contents="Reply with exactly: OK")
                ping = f"ok {time.perf_counter() - t:.1f}s"
            except Exception as exc:
                print(f"{model:<26}FAIL  {_short_error(exc)}")
                continue

            t = time.perf_counter()
            try:
                with Image.open(page_png) as img:
                    resp = client.models.generate_content(
                        model=model, contents=[FULL_PAGE_SCAN_PROMPT, img]
                    )
                ocr = f"ok {time.perf_counter() - t:.1f}s"
                s = score(resp.text or "", gt)
                cell = s["table_cell_accuracy"]
                print(
                    f"{model:<26}{ping:<14}{ocr:<14}{s['text_similarity']:>9.3f}"
                    f"{s['word_recall']:>9.3f}{(f'{cell:.3f}' if cell is not None else '-'):>9}"
                )
                working += 1
            except Exception as exc:
                print(f"{model:<26}{ping:<14}FAIL  {_short_error(exc)}")

    return 0 if working else 1


if __name__ == "__main__":
    sys.exit(main())
