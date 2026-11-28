# The document-parser evaluation framework

How `extractor-eval` measures the whimsync document parser: what runs where, how each
score is computed, and how to read, manage and compare results. Commands are in the
[README](../README.md). Adding cases or metrics is covered in [extending.md](extending.md).

---

## 1. Architecture

```text
extractor-eval run
  → docker run --rm whimsync-extractor-eval:<commit>        (one container per case × tier)
      → python -m src.eval_cli → ParsingEngine.parse(...)  (exactly the production code path)
      → document.md + pages/NNN.md + trace.json + result.json
  → deterministic scoring + link checks + routing checks
  → Gemini judge (page images + gold + candidate, strict JSON)
  → results/runs/<run-id>/  (per-tier JSON + summary.json)
  → CLI / web UI: list, rename, group, comment, compare
```

- **The extractor runs in Docker.** The image is built from a whimsync checkout's `apps/extractor/Dockerfile.eval`. It contains PyMuPDF, PyMuPDF4LLM, Gemini support and the parser source. It starts no Redis, BullMQ, MinIO, Postgres or consumer. Each run mounts one PDF read-only plus an output folder, parses with one tier, and exits. Production ingestion never triggers it.
- **No import coupling.** The evaluator imports nothing from whimsync, and the container's artifacts are the only contract between them. One image serves every case and both tiers of a run.
- **Traceable builds.** The image tag is the extractor commit, or `<commit>-dirty-<hash>` when `apps/extractor` has uncommitted changes, with the hash covering the diff and untracked files. Each `result.json` records the tag and image digest.

### What the extractor adds for evaluation

These are whimsync files used only for evaluation. No production code changed.

| File | Purpose |
|---|---|
| `apps/extractor/Dockerfile.eval` (+ `.dockerignore`) | Builds the image from `pyproject.toml`, `uv.lock` and `src/` only. `.env` and `.venv` never enter the build context. |
| `apps/extractor/src/eval_cli.py` | `--input --tier --output [--model]`. Accepts `smart` and `balanced` as aliases, so either side of the tier rename works. |
| `apps/extractor/src/parsing/evaluation_trace.py` | For the length of one parse, wraps the functions the strategies already call. `analyze_page_complexity` gives routing inputs, and `inject_links_inline` is the last per-page step, so it yields each page's final Markdown. The VLM service and Gemini client give a record per call with page, attempts, tokens and `finish_reason`. Everything is restored afterwards. |

### Container artifacts

| File | Contents |
|---|---|
| `document.md` | The stitched Markdown production would store and chunk. |
| `pages/NNN.md` | Each page's final Markdown, after VLM replacement and link injection. Used for per-page gold comparison and routing analysis. |
| `trace.json` | Per page: complexity (`needs_ocr`, `reasons`, coverages), `route` (`vlm` / `native` / `empty`), `vlm_failed`, VLM time. Per VLM call: page, kind, model, prompt version, attempts, retries, tokens, `finish_reason`. |
| `result.json` | Commit, image tag and digest, tier, model, prompt hashes, render scale, VLM settings, library versions, pages, VLM pages, calls, failures, retries, tokens, duration, status. |

`route` is `vlm` only when the page's final Markdown actually came from the VLM. When a VLM call returns nothing, the strategy silently falls back to native text. The trace marks that case as `native` with `vlm_failed: true`, so routing and quality numbers reflect what really happened.

---

## 2. Dataset

`extractor/dataset.json` is the committed manifest. Each case has a PDF, its SHA-256, a `quality_mode`, page labels, and gold Markdown stored by convention:

- **`pages` mode** (small PDFs): `expected/pages/NNN.md`, one file per page and the only gold. A document-level expectation, when needed, is the page files concatenated.
- **`ranges` mode** (large PDFs): the full PDF stays, uncropped, and `quality_ranges` lists the page spans that have gold (`expected/ranges/<id>-<start>-<end>.md`). This keeps cross-page tables intact. The whole document still runs, for operational metrics such as runtime, completion, VLM page ratio, retries, output size and cost.

A scoring **unit** is one gold page (`p001`) or one range (its id).

### Labels

```json
"page_labels": {
  "1": {
    "expected_route": "vlm_required",
    "expected_classifier_reasons": ["vector-table"],
    "tags": ["chinese", "grid_table"],
    "required_phrases": ["..."]
  }
}
```

- **`expected_route`**: `vlm_required`, `native_acceptable` or `either_acceptable`.
- **`expected_classifier_reasons`**: only the classifier's own names, which are validated: `scanned`, `garbled`, `no-text`, `sparse-text`, `vector-table`, `embedded-images`, `empty`.
- **`tags`**: free-form human labels (`chinese`, `financial_table`, `footer`, …) that the classifier doesn't know. Failures are grouped by them.
- **`required_phrases`**: key text that must appear, such as a footer or signature line.

### Gold status

`gold_status` and `labels_status` are each `draft` or `verified`. `dataset add` collects the classifier's suggestions by parsing with the VLM disabled. That run is free and deterministic. It then seeds **draft** gold from one tier (Max by default) on just the scored pages. A human corrects the files and runs `dataset mark <id> --gold verified --labels verified`.

Draft gold copied from Max flatters Max. Every quality aggregate is therefore reported twice: over all units, and over verified-gold units only (`metrics_verified`; *Verified gold only* in the UI).

### Suites

| Suite | Contents | Gold |
|---|---|---|
| your own (`dataset add --suite <name>`, default `core`) | Real PDFs you add, scored per page or per quality range | Draft until reviewed |
| `links` | Duplicate anchors, internal GoTo link (`#page-2`), per-word and line-wrapped multi-word anchors, adjacent different targets, text that already contains Markdown link syntax | Exact (built by construction) |
| `synthetic` | 12 Markdown templates rendered digital plus seeded OpenCV `scanned_noisy` and `photo` variants | Exact (the template) |

---

## 3. Scoring

Deterministic checks run first. The judge only covers what they can't decide.

### Deterministic (`deterministic.py`)

Text is compared after Markdown is rendered to visible text, followed by NFKC normalisation, lowercasing and whitespace collapsing. Formatting that changes no text, such as bold markers, `<u>` tags or link syntax, therefore costs nothing.

| Metric | Computation |
|---|---|
| `text_similarity` | Indel edit similarity of the normalised text |
| `char_similarity` | The same, over letters and digits only. The fair measure for CJK, which has no word boundaries. |
| `word_precision` / `word_recall` / `word_f1` | Bag of tokens (each CJK character counts as a token). Low precision means invented text; low recall means dropped text. |
| `reading_order` | Longest common token subsequence ÷ gold tokens |
| `heading_f1` / `heading_text_f1` / `heading_sequence` | Match on (level, text) / on text only / order agreement of the heading lists |
| `table_*` | GFM and HTML tables become grids. Gold and candidate tables are paired by cell overlap, rows are aligned monotonically, then cells are compared exactly. Also reports detection, shape, row and column counts, and numbers inside tables. |
| `numeric_recall` / `numeric_precision` | Numbers anywhere, with currency symbols and thousands separators stripped and `(5)` read as `-5`. The missing and unexpected numbers are listed. |
| `required_phrase_recall` | Labelled phrases present in the candidate |
| `markdown_valid` | No unclosed fences, pipe tables with a missing separator or uneven column counts, or "Here is…" filler |
| `empty_output` | Candidate is empty where the gold has text |

### Links (`links.py`)

Expected `(anchor, url)` pairs come from `expected/links.json`. Scores are exact-match `link_precision` and `link_recall`, plus `link_target_recall` (URL found, any anchor). No LLM is involved. A split anchor such as `[PostgreSQL](u) [documentation](u)` keeps target recall at 1.0 but scores 0 on exact recall.

### Gemini judge (`judge.py`)

Each unit sends:
- the source page images, rendered at 1.5×;
- the gold and candidate Markdown;
- a fixed rubric, with its hash recorded as `prompt_version`.

The settings are:
- model: `JUDGE_MODEL` (default `gemini-3.8-flash`);
- temperature: `JUDGE_TEMPERATURE` (default 0);
- output: a strict JSON schema `{overall_score, text_fidelity, reading_order, table_fidelity|null, visual_fidelity|null, critical_errors[{type, evidence}], summary}`.

How the judge is run:
- The rubric covers multi-column reading order, formatting that preserves meaning, figures and charts, visual omissions, hallucinations, and semantic table fidelity.
- Each request (prompt text, image files and their hashes) and the raw response are saved in `cases/<case>/<tier>/judge/`.
- An empty candidate is scored 1 without an API call and flagged `synthetic`, so failures can't inflate the averages.
- Calls are paced (`JUDGE_RPM`). 429 and 5xx errors are retried following Gemini's `retryDelay`. A requested wait over 120 s fails fast as `error`.

### Routing (`routing.py`)

The routing check compares each labelled page with `trace.json`:

| Label | Routed native | Routed VLM |
|---|---|---|
| `vlm_required` | **false native** (critical: content likely lost) | correct |
| `native_acceptable` | correct | **false VLM** (unnecessary cost) |
| `either_acceptable` | reported only | reported only |

From these it reports:
- `routing_precision`: share of VLM-routed pages that needed the VLM;
- `routing_recall`: share of VLM-required pages that went to the VLM;
- `false_native_rate` and `false_vlm_rate`;
- `reason_agreement`: share of pages where the classifier's reasons match `expected_classifier_reasons`;
- failures grouped by classifier reason and by tag.

### Operational and performance

Each (case, tier) records:
- status, `duration_seconds` (in-container parse time) and `wall_seconds`;
- seconds per page, VLM page ratio, VLM calls, failures and retries;
- tokens, and cost from `pricing.json` (null for unpriced models);
- output size.

`--perf-repeats N` runs each small (`pages`-mode) case N more times per tier, unscored and unjudged, and reports median, fastest and slowest duration. Large cases run once.

### Aggregation (`report.py`)

Per tier, quality is the macro mean over units, with `None` (not applicable) left out. It is also broken down by suite and by tag. Routing pools all labelled pages. Operational metrics are summed, or shown as ratios over the totals.

---

## 4. Results

```
extractor/results/runs/<run-id>/
  run.json          configuration, image tag + digest, judge model/prompt/temperature, dataset + gold hashes, status, errors
  meta.json         display name and groups (editable)
  comments.json     review comments (editable)
  summary.json      per tier: metrics, metrics_verified, by_case, by_suite, by_tag, routing, operational, perf, judge, fingerprint
  tier-<tier>.json  every case score for that tier
  cases/<case>/<tier>/output/   container artifacts     .../output.log  container log
  cases/<case>/<tier>/scores.json   per-unit metrics + details, routing outcomes, operational
  cases/<case>/<tier>/judge/        judge requests, responses, page images
  cases/<case>/<tier>/perf/<i>/     performance repeats
```

- **Resume:** `run --resume <id>` skips (case, tier) pairs that already have `scores.json`, so an interrupted Apple run doesn't start over.
- **Rescore:** `rescore <id>` recomputes deterministic scores and summaries from the saved outputs and reuses saved judge verdicts, without Docker or API calls. Use it after editing gold or a metric.

### Comparison consistency

Each column records what has to stay fixed for a fair comparison:
- extractor commit and image digest;
- extraction model, prompt hashes, render scale, and VLM concurrency, retry and RPM settings;
- judge model, prompt version and temperature;
- PDF and gold hashes, and library versions.

`compare` lists every setting that differs between columns, so each score change can be traced to a deliberate code, configuration, prompt or model change.

### Managing results

`store.py` backs both the CLI (`runs ...`) and the UI:
- **List** runs, filtering by group or kind. Claims-suite results appear as `claims:<file>`, with sidecar `<file>.meta.json` and `<file>.comments.json`.
- **Rename** a run (only its display name changes; the folder id stays) and **group** runs. A run can be in several groups, and a group can be renamed everywhere at once.
- **Comment** on a whole run or pin a comment to a case, unit and tier. Comments can be edited or deleted.
- **Delete** a run (the CLI asks for confirmation).
- Runs are addressed by full id, a unique id prefix, or a unique display name.

`compare` takes any number of runs, with one column per (run, tier):
- Two tiers of one run, one tier across runs, or everything at once all work.
- Each metric is colored against the first column when the change exceeds the tolerance: 0.02 for ratios, 0.1 for judge scores, 25% for seconds, counts and cost.
- The output includes the settings that differ and a per-unit table.

The UI's unit view puts the source page images, the gold, and every candidate column side by side:
- each candidate can be shown raw, rendered (server-side, raw HTML disabled) or as a word diff against gold or another column;
- below that are the unit's metrics, the judge verdict, the routing/VLM trace, Markdown issues, missing numbers and phrases, and link misses;
- comments for that unit sit underneath.

---

## 5. Known limitations

- **Added PDFs start with draft gold.** `dataset add` seeds it from Max output, which flatters Max. Review each case before trusting its quality numbers, and use *Verified gold only* until then.
- **Per-page timing for native pages isn't available.** Digital extraction is one pass over the whole document (`native_extraction_ms` in the trace). Only VLM pages get a per-page `duration_ms`.
- **The trace depends on the strategies' current call pattern.** If a strategy stops calling `inject_links_inline` as its last per-page step, `pages/NNN.md` will be wrong. `tests/test_eval_cli.py` in whimsync would catch that.
- **The judge varies.** Even at temperature 0 it isn't perfectly deterministic. Treat a judge delta under about 0.3 as noise unless it repeats.
- **Cost needs prices.** Cost is null until `pricing.json` has rates for the model; token counts are always recorded.
