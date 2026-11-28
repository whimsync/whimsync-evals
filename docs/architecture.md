# Architecture

How the evaluation works (Docker boundary, artifacts, scoring, results) is in [framework.md](framework.md). This page is the module map.

## `extractor/src/extractor_eval/` (Python, `uv`)

| Module | Role |
|---|---|
| `cli.py` | Typer CLI: `image`, `dataset` (list/check/synth/add/show/label/gold/edit/mark/remove), `run`, `rescore`, `compare`, `runs`, `jobs`, `ui` |
| `dataset.py` | `dataset.json` models (Case, PageLabel, QualityRange, Unit), integrity checks |
| `docker_runner.py` | Builds/tags images per extractor commit; runs one container per (case, tier) |
| `runner.py` | Orchestrates a run: containers → judge → scores → per-tier JSON + summary; resume and rescore |
| `scoring.py` | Scores one (case, tier) output: units, links, routing, operational, fingerprint |
| `deterministic.py` / `links.py` / `routing.py` | Pure scoring functions |
| `judge.py` | Gemini judge: rubric, schema, page rendering, pacing, retries, saved requests |
| `report.py` | Aggregation (all vs verified gold, by suite, by tag) and console table |
| `metrics.py` | The metric catalog read by every view |
| `store.py` | Results management for extractor and claims runs: list, rename, groups, comments, delete |
| `compare.py` | Column-wise comparison with verdicts and settings that differ |
| `synth/` | Synthetic templates, PDF rendering, OpenCV scan augmentation, link-case builder |
| `jobs.py` | Job queue for the UI: runs `extractor-eval` commands one at a time in a child process, with persisted status and logs |
| `web/server.py` + `web/manage_api.py` + `web/static/` | FastAPI JSON API (results; dataset, images, runs and jobs) and the single-page UI (`app.js` results, `manage.js` dataset/run/jobs/setup; no build step) |

## `claims/` (TypeScript, Bun)

The claim-extractor benchmark. It imports whimsync's worker code through tsconfig paths and writes `claims/results/*.json`. `store.py` and `compare.py` read those files directly.

## whimsync side (`apps/extractor/`)

`Dockerfile.eval`, `src/eval_cli.py` and `src/parsing/evaluation_trace.py`. These are evaluation-only files; see framework.md §1.
