"""Shared fixtures. Tests never call Gemini: VLM tiers are faked or skipped."""

from pathlib import Path

import pytest


@pytest.fixture
def corpus_dir(tmp_path: Path) -> Path:
    return tmp_path / "corpus"
