"""
whimsync-evals Python tooling.

- Document parser suite: cases -> generate (PDFs + OpenCV scans) -> runner -> metrics.
- Shared analysis for every suite's results files: catalog, diff, compare, report.

The code under test is the `apps/extractor` package of a sibling `../whimsync`
checkout, installed editable into this project (see parser/pyproject.toml).
See docs/architecture.md for the module map.
"""

from pathlib import Path

from dotenv import load_dotenv

EVALS_ROOT = Path(__file__).resolve().parents[2]
PARSER_DIR = EVALS_ROOT / "parser"
CASES_DIR = PARSER_DIR / "cases"
RESULTS_DIR = PARSER_DIR / "results"
CORPUS_DIR = RESULTS_DIR / "corpus"
CLAIMS_DIR = EVALS_ROOT / "claims"
CLAIMS_FIXTURES_DIR = CLAIMS_DIR / "fixtures" / "episodes"

# The extractor's settings read env vars at import time, so load whimsync-evals/.env
# before anything imports `src.config`. Real env vars take precedence.
load_dotenv(EVALS_ROOT / ".env")
