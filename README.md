# whimsync-evals

Benchmarks that measure how well [whimsync](https://github.com/whimsync/whimsync) extracts information, scored against **synthetic ground truth** (the expected output is known exactly because the inputs are generated from it).

| Suite | Measures | Runner | Inputs |
|---|---|---|---|
| **Document parser** | PDF → Markdown quality of the `fast` / `smart` / `max` tiers on digital, scanned and photographed pages | `parser/` (Python, `uv`) | `parser/cases/*.md` |
| **Claim extractor** | Claims, entities, mutations and evidence from the single Gemini extraction call | `claims/` (TypeScript, Bun) | `claims/fixtures/episodes/*.json` |

Every run writes a self-describing JSON results file. One set of tools compares runs and renders HTML reports for **both** suites.

- [docs/architecture.md](docs/architecture.md): how the code is organised and how data flows
- [docs/extending.md](docs/extending.md): adding cases, episodes, metrics, scan styles or a new suite

---

## Setup

whimsync must be checked out **next to** this repo. The suites import its code directly, so uncommitted changes there are benchmarked as-is.

```
projects/
  whimsync/          # code under test
  whimsync-evals/    # this repo
```

```bash
(cd ../whimsync && bun install)    # worker dependencies
bun run setup                       # bun install + uv sync (includes OpenCV)
cp .env.example .env                # then fill in GEMINI_API_KEY
bun run parser:models               # check the key and models work
```

| Variable | Used by | Notes |
|---|---|---|
| `GEMINI_API_KEY` | both | Without it only the parser's `fast` tier runs. |
| `GEMINI_MODEL` | parser (smart/max) | Default `gemini-3.5-flash`. `gemini-2.5-flash` returns 404 for new keys. |
| `VLM_RPM` | parser | Max VLM requests per minute (0 = unlimited). |
| `LLM_MODEL` | claims | Model under test, e.g. `gemini-3.5-flash-lite`. |
| `EMBEDDING_MODEL` | claims | Used to match predicted claims to expected ones. |

> **Free-tier keys** allow about 15 requests per minute **and a limited number per day** per model. Use `--rpm 12` (or `VLM_RPM=12`). When the daily quota runs out, calls fail within seconds instead of waiting for hours; finish the run later with `--resume`.

All commands run from the repo root, and file arguments are relative to where you run them.

---

## Running

```bash
# Document parser
bun run parser -- --tiers fast --open                                    # offline, opens the report
bun run parser -- --tiers fast,smart,max --variants 3 --rpm 12 --label baseline
bun run parser -- --tiers smart,max --model gemini-3.5-flash-lite --rpm 12 --label lite

# Claim extractor
bun run claims -- --runs 3 --rpm 12 --label baseline
LLM_MODEL=gemini-3.5-flash bun run claims -- --runs 3 --rpm 12 --label flash

# Continue an interrupted or quota-stopped run (either suite)
bun run parser -- --tiers fast,smart,max --resume parser/results/<ts>-baseline.partial.jsonl
```

Each finished job is appended to `<results>.partial.jsonl` straight away, so a killed run loses nothing. `--resume` skips finished jobs and re-runs failed ones.

### Comparing and viewing runs

```bash
bun run parser:compare -- parser/results/A.json parser/results/B.json --per-case
bun run parser:compare -- X.json X.json --group-a fast/scanned --group-b smart/scanned   # tier vs tier
bun run claims:compare -- claims/results/A.json claims/results/B.json --per-case
bun run parser:report  -- parser/results/A.json parser/results/B.json --open
bun run claims:report  -- claims/results/A.json claims/results/B.json --open
```

`compare` prints B − A per metric and flags `REGRESSION` / `improved` beyond `--threshold` (default 0.02; timing uses ±25%). It exits with `1` on any regression, so it can gate CI. The report is one self-contained HTML file:
- **Parser:** a summary per tier/mode, deltas, accuracy plotted against rotation and JPEG quality, and a card per document (thumbnail, scores, side-by-side output, word diff).
- **Claims:** metrics as mean ± std per run, and every episode's expected vs predicted claims.

---

## Document parser suite

Each case in `parser/cases/` is rendered to a real PDF. Links, tables and multi-column layout are preserved. Randomised scans of that PDF are then made with OpenCV, one set per render mode:

| Mode | Simulates | Effects (random per variant) |
|---|---|---|
| `digital` | Native text PDF | none (1 variant) |
| `scanned` | Clean flatbed scan | rotation ±2°, light noise and blur, JPEG 80–95 |
| `scanned_noisy` | Old office copier | rotation ±6°, blur, speckle, JPEG 35–70, uneven light, dark border, folds |
| `photo` | Phone photo | rotation ±10°, perspective warp, lighting gradient, colour cast |

The corpus is cached in `parser/results/corpus/` and is deterministic for a given `--seed`.

| Metric | Meaning |
|---|---|
| `text_similarity` | Character similarity of normalised text |
| `word_recall` / `word_precision` | Ground-truth words kept / output words that are real (hallucination) |
| `reading_order` | Longest common word subsequence ÷ reference length |
| `heading_f1` / `heading_text_f1` | Headings matched on relative level + text / on text only |
| `table_cell_accuracy`, `table_detection`, `table_shape_match` | GFM table cells, tables found, row/column shape |
| `link_recall` / `link_exact` | Link URLs present / exact `[anchor](url)` pairs |
| `seconds_per_page`, `vlm_pages`, `vlm_failures` | Cost, and VLM calls that returned nothing (above 0 means scores are understated) |

Metrics that don't apply to a document (e.g. tables in a letter) are `null` and are left out of averages.

| Flag | Default | |
|---|---|---|
| `--tiers` / `--modes` / `--cases` | all | Comma lists |
| `--variants` / `--seed` | `2` / `0` | Random scan variants per mode |
| `--concurrency` | `1` | Documents in flight. Higher is faster for VLM-bound runs but inflates `seconds_per_page`. |
| `--rpm` / `--model` | env | VLM pacing / model override |
| `--label` / `--out` / `--resume` | | Naming, output path, continue a run |
| `--no-output` / `--keep-workdir` | off | Skip storing Markdown / keep page scans |
| `--report` / `--open` | off | Build (and open) the HTML report afterwards |

## Claim extractor suite

The suite calls whimsync's `executeStructuredExtraction` directly, so no Postgres or Redis is needed. Fixtures cover facts, preferences, decisions, goals, multi-fact episodes, small talk that should yield **no** claims, and prior claims to update, delete or leave alone. Fixtures are validated on load.

| Metric | Meaning |
|---|---|
| `claim_precision` / `claim_recall` / `claim_f1` | Claims matched one-to-one by embedding similarity (≥ `--sim-threshold`), micro-averaged |
| `kind_accuracy` | Matched claims with the right kind |
| `entity_*` | Subject–predicate–object triples |
| `mutation_accuracy` | Correct update / delete / noop per candidate (also printed as a confusion matrix) |
| `evidence_validity`, `claims_with_valid_evidence` | Evidence excerpts found verbatim in the episode |
| `referential_integrity` | Internal IDs that resolve |
| `noise_clean_rate`, `noise_claims_per_episode` | Hallucination on small talk |
| `failure_rate`, `latency_p50_ms`, `latency_p95_ms` | Operational health |
| `tag:<tag>:claim_f1` | Claim F1 per fixture tag |

With `--runs N` every metric is reported as mean ± std.

Flags: `--runs` (1), `--cases` / `--tags`, `--sim-threshold` (0.80), `--concurrency` (2), `--rpm` (0), `--retries` (3), `--label`, `--out`, `--fixtures` (`claims/fixtures/episodes`), `--resume`, `--no-output`.

---

## Tests

```bash
bun run test        # both suites (no API calls)
bun run typecheck && bun run lint
```

## Known findings

- **Link anchors are split:** `link_injector.py` splits multi-word links (`[PostgreSQL documentation](url)` becomes two links), so `link_exact` is 0.33 on digital PDFs.
- **Two-column order:** the `fast` tier interleaves two-column layouts, so `reading_order` is about 0.82.
- **Scans need the VLM:** `fast` scores 0 on every scanned/photo document because it has no OCR. Those documents need `smart` or `max`.
- **VLM pacing** (`vlm_service.py`):
  - `VLM_CONCURRENCY` (default 3) caps how many calls run at once.
  - `VLM_DELAY_SECONDS` (default 0.5) pauses after each call.
  - `VLM_RPM` (default 0 = off) caps requests per minute.
  - 429 and 503 errors are retried with backoff that follows Gemini's `retryDelay` hint, plus jitter. A hint over 120 s (`MAX_RETRY_WAIT_SECONDS`) means the daily quota is exhausted, so the page fails immediately and counts toward `vlm_failures`.
  - Errors that can't succeed on retry (bad key, unknown model) fail immediately.
  - A page that finally fails still becomes empty Markdown. The benchmark counts every VLM call that returns no text as a `vlm_failure`, per document.
- **Heading levels flattened on scans (flash-lite):** `smart` and `max` transcribe scanned/photo pages perfectly (text, word recall, reading order all 1.00) but turn every `###` into `##`, so `heading_f1` is 0.50. The rendered sizes (20 / 15 / 12.5 pt) are easy to tell apart, so the model or the OCR prompt is at fault; the prompt doesn't ask for distinct levels.
- **Claim extractor (flash-lite, 3 runs):** claim F1 0.87 ± 0.02. Mutations, evidence and referential integrity are all 1.00. Small talk still yields a claim in 2 of 3 noise episodes. Update cases score 0.60 because the model merges two expected claims into one. Entity precision (0.42) is understated: the model extracts valid triples the fixtures don't list.
- **Worker pacing:** the worker's own Gemini calls (outside the benchmark) still have no pacing.
- **Fixed: the claim extractor sent Gemini an empty schema.** `zod-to-json-schema` doesn't support Zod v4, so `responseSchema` was `{"$schema": ...}` with no structure, and most responses failed Zod validation. `@whimsync/core` now exports `CognitiveExtractionResultJsonSchema` (built with `z.toJSONSchema`), and `llmService.ts` sends it as `responseJsonSchema`.
