"""
extractor-eval: evaluation harness for the whimsync document parser.

The parser under test runs inside a Docker image built from a whimsync checkout
(`apps/extractor/Dockerfile.eval`); this package never imports whimsync code.
"""

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parents[1]  # whimsync-evals/extractor
REPO_DIR = PROJECT_DIR.parent  # whimsync-evals

DATASET_PATH = Path(os.environ.get("EXTRACTOR_EVAL_DATASET", PROJECT_DIR / "dataset.json"))
RESULTS_DIR = Path(os.environ.get("EXTRACTOR_EVAL_RESULTS", PROJECT_DIR / "results"))
RUNS_DIR = RESULTS_DIR / "runs"
CLAIMS_RESULTS_DIR = Path(os.environ.get("CLAIMS_RESULTS_DIR", REPO_DIR / "claims" / "results"))
DEFAULT_EXTRACTOR_ROOT = REPO_DIR.parent / "whimsync"

TIERS = ("balanced", "max")
