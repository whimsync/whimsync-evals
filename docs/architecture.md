# Architecture

Two runners, one results format, one set of analysis tools.

```
 parser/cases/*.md ─► generate ─► runner ─┐                       ┌─► compare (terminal, CI)
                     (PDF + OpenCV)       ├─► results/*.json ─────┤
 claims/fixtures/ ──► fixtures ─► runner ─┘   (self-describing)   └─► report  (HTML)
   episodes/*.json   (validated)
```

The runners are written in the language of the code they test (Python for the
extractor, TypeScript for the worker). `compare` and `report` are Python and read
any suite's results through a small adapter (`bench/kinds.py`).

## Principles

- **Data, not code, for test inputs.** Cases and episodes are files. Adding one
  never needs a code change.
- **One catalog per suite.** Every metric's label, direction, unit and tolerance
  is declared once and embedded in each results file, so the console, `compare`
  and `report` never hard-code metric names.
- **Pure scoring.** Metric functions take strings or embeddings in and return
  numbers. They do no I/O, so they are tested without the network.
- **Crash-safe runs.** Every finished job is appended to `<results>.partial.jsonl`.
  `--resume` continues from it.
- **Fail fast, never hang.** Fatal API errors abort, transient ones retry with
  backoff, and an exhausted daily quota fails within seconds.
- **Code under test is imported, never copied.** `../whimsync` is an editable
  dependency (Python) or a tsconfig path alias (TypeScript).

## Results file (schema_version 2)

```jsonc
{
  "schema_version": 2,
  "kind": "document-parser",          // or "claim-extractor": picks the adapter and view
  "label": "baseline", "timestamp": "20261125T101532Z",
  "git_sha": "163766e",               // whimsync commit under test (claims: gitSha)
  "evals_sha": "…",                   // this repo's commit
  "config": { … },                    // the CLI flags used
  "catalog": [ { "key", "label", "better", "unit", "tolerance", … } ],
  "summary": { … },                   // parser: group -> metric; claims: metric -> {mean, std}
  "results": [ … ]                    // one record per job
}
```

Claim results also embed `episodes` (inputs and expectations), so a report stays
correct after the fixtures change. Files written before schema 2 still load. Any
metric missing from a catalog gets a spec inferred from its name
(`catalog.infer_spec`).

## Module map

### `parser/bench/` (Python)

| Module | Role |
|---|---|
| `cases.py` | Loads and validates `parser/cases/*.md` (frontmatter + ground-truth body) |
| `augment.py` | Scan profiles (`PROFILES`) and OpenCV effects. Render modes = `digital` + one per profile |
| `generate.py` | Markdown → digital PDF → scanned variants, cached and deterministic |
| `metrics.py` | Scorers registered with `@scorer` + the parser `CATALOG` |
| `runner.py` | `Job` (doc × tier), concurrent execution, per-job VLM accounting, aggregation |
| `store.py` | Checkpoint, resume and final results JSON |
| `run.py` | Parser CLI. Wiring only |
| `catalog.py` | `MetricSpec` / `Catalog`, shared by every suite |
| `diff.py` | Regression / improvement verdicts, shared by `compare` and `report` |
| `kinds.py` | Adapters that expose any results file as groups and cases |
| `compare.py` | Terminal diff CLI (both suites) |
| `report/` | HTML report: shared `html.py` helpers and one view per suite |
| `check_models.py` | Gemini availability and OCR smoke test |

### `claims/` (TypeScript)

| Module | Role |
|---|---|
| `src/fixtures.ts` | Loads and validates `fixtures/episodes/*.json` |
| `src/metrics.ts` | Pure scoring: claim matching, entities, mutations, evidence |
| `src/gemini.ts` | Rate limiter, error classification, retry with backoff, worker pool |
| `src/runner.ts` | One episode: extract → embed → score. Extractor and embedder are injected |
| `src/summary.ts` | Per-run summary, mean ± std across runs, console output |
| `src/results.ts` | Checkpoint, resume and final results JSON (mirrors `store.py`) |
| `src/catalog.ts` | Claim metric catalog (embedded in results) |
| `run.ts` | Claims CLI, and the only place that imports whimsync code |
