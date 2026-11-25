"""
Self-contained HTML report for one or more results files of the same suite.

    uv run python -m bench.report                        # latest parser run
    uv run python -m bench.report A.json B.json --open   # side by side + deltas
    uv run python -m bench.report ../claims/results/<file>.json

Each result kind has a view module (`parser_view`, `claims_view`) registered in
VIEWS; the page shell, CSS and helpers are shared.
"""

import webbrowser
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from .. import RESULTS_DIR
from ..kinds import ResultKind, kind_of
from ..store import load_run
from . import claims_view, parser_view
from ._assets import CSS, JS
from .html import esc

View = Callable[[list[dict], ResultKind], str]

VIEWS: dict[str, View] = {
    "document-parser": parser_view.render,
    "claim-extractor": claims_view.render,
}


def latest_result(results_dir: Path = RESULTS_DIR) -> Path | None:
    files = sorted(results_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def _provenance(run: dict, kind: ResultKind) -> str:
    sha = run.get("git_sha") or run.get("gitSha")
    return (
        f"<div class='meta'><strong>{esc(run['label'])}</strong> · {esc(run.get('timestamp'))} · "
        f"sha <code>{esc(sha)}</code> · model <code>{esc(kind.model(run))}</code> · "
        f"{esc(Path(run['_path']).name)}</div>"
    )


def build_report(paths: list[Path], out: Path | None = None, open_browser: bool = False) -> Path:
    """Renders the runs into one HTML file (default: next to the last results JSON)."""
    runs = [load_run(p) for p in paths]
    kinds = {kind_of(r).name for r in runs}
    if len(kinds) != 1:
        raise ValueError(f"Cannot mix result kinds in one report: {sorted(kinds)}")
    kind = kind_of(runs[0])
    body = VIEWS[kind.name](runs, kind)
    page = (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{esc(kind.title)}</title><style>{CSS}</style></head><body><main>"
        f"<h1>{esc(kind.title)}</h1>{''.join(_provenance(r, kind) for r in runs)}"
        f"<div class='meta'>generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC</div>{body}"
        f"</main><script>{JS}</script></body></html>"
    )
    out = out or Path(runs[-1]["_path"]).with_suffix(".html")
    out.write_text(page, encoding="utf-8")
    if open_browser:
        webbrowser.open(out.resolve().as_uri())
    return out
