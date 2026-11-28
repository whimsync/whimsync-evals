# whimsync-evals

Evaluation harnesses for [whimsync](https://github.com/whimsync/whimsync), plus one CLI and web UI to manage, compare, group, rename and comment on every result.

| Suite | Measures | Runner | Inputs |
|---|---|---|---|
| **Document parser** (`extractor/`) | PDF → Markdown quality, routing, links and cost of the `balanced` / `max` tiers, run in Docker | `extractor-eval` (Python, `uv`) | `extractor/dataset.json` + `extractor/cases/` |
| **Claim extractor** (`claims/`) | Claims, entities, mutations and evidence from the single Gemini extraction call | `claims/run.ts` (TypeScript, Bun) | `claims/fixtures/episodes/*.json` |

- [docs/framework.md](docs/framework.md): what the parser evaluation measures, how runs and scoring work, how to read and manage results
- [docs/extending.md](docs/extending.md): adding cases, labels, metrics and suites

---

## Setup

```bash
bun run setup                  # bun install (claims) + uv sync (extractor-eval)
cp .env.example .env           # fill in GEMINI_API_KEY
bun run eval -- image build    # build whimsync-extractor-eval:<commit> from ../whimsync
```

The parser runs **inside a Docker image** built from a whimsync checkout (`apps/extractor/Dockerfile.eval`). The evaluator never imports whimsync code. The image tag is the extractor commit, or `<commit>-dirty-<hash>` when `apps/extractor` has uncommitted changes, so every result names the exact code it measured. Use `--extractor-root` to build from another checkout.

| Variable | Used by | Notes |
|---|---|---|
| `GEMINI_API_KEY` | VLM tiers, judge, claims | Passed into the container by name, never on the command line. |
| `GEMINI_MODEL` | extraction | VLM model the tiers use (override per run with `--model`). |
| `VLM_RPM`, `VLM_CONCURRENCY`, `VLM_MAX_RETRIES` | extraction | Forwarded to the container. Use `VLM_RPM=12` on free-tier keys. |
| `JUDGE_MODEL`, `JUDGE_RPM`, `JUDGE_TEMPERATURE` | judge | Defaults: `gemini-3.8-flash`, 12, 0. |
| `LLM_MODEL`, `EMBEDDING_MODEL` | claims | Model under test, and the model that matches claims. |

---

## Document parser: `extractor-eval`

```bash
bun run eval -- run --suite core,links --tiers balanced,max --label baseline --group baseline
bun run eval -- run --case scanned-footer --tiers balanced,max
bun run eval -- run --suite core --perf-repeats 3 --no-judge        # timing only (3 extra runs per small case)
bun run eval -- run --resume <run-id>                               # continue an interrupted run
bun run eval -- rescore <run>                                       # re-score saved outputs (no API calls)
bun run eval -- compare <runA> <runB> [--tier max] [--scope verified]
bun run ui                                                          # http://127.0.0.1:8765
```

Each (case, tier) is parsed by `docker run --rm <image> --input /input/source.pdf --tier <tier> --output /output`. The container writes:
- `document.md`: the stitched Markdown production would store;
- `pages/NNN.md`: each page's final Markdown;
- `trace.json`: per-page classifier facts, route and every VLM call;
- `result.json`: configuration and summary.

The evaluator then scores each **unit** (a gold page, or a quality range of pages):

| Layer | What it checks |
|---|---|
| Deterministic | Text, character (CJK) and word similarity, reading order, heading level/sequence, table shape/rows/columns/cells, numbers (anywhere and in tables), required phrases (e.g. footers), Markdown validity, empty output |
| Links | Exact `[anchor](url)` precision/recall and URL recall against `expected/links.json`. Never judged by an LLM. |
| Gemini judge | Page image(s), gold and candidate go to a fixed rubric with a strict JSON schema, which scores text, reading order, tables and visuals (1–5) and lists critical errors. Each request and response is saved. |
| Routing | `vlm_required` pages parsed natively (**false native**, critical) and `native_acceptable` pages sent to the VLM (**false VLM**, cost). Results are grouped by classifier reason and tag. |
| Operational | Status, parse seconds, VLM page ratio, VLM failures, retries, tokens and cost (`pricing.json`), plus perf median/min/max |

### Dataset

`extractor/dataset.json` lists every case. The gold sits next to the PDF:

```
cases/<id>/source.pdf
cases/<id>/expected/pages/NNN.md        # one file per page (small PDFs)
cases/<id>/expected/ranges/<id>.md      # one file per quality range (large PDFs: only selected pages are scored)
cases/<id>/expected/links.json          # optional link expectations
```

| Suite | Cases | Gold |
|---|---|---|
| `core` | `scanned-footer`, `chinese-grid-table`, `complex-form` (I-9), `apple-filing` (121 pages; ranges 32–36 and 49–50) | **Draft**, seeded from the Max tier. Review it, then mark it verified. |
| `links` | duplicate anchors, internal GoTo link, multi-word/wrapped anchors, pre-formatted Markdown links | Exact, built by construction |
| `synthetic` | 12 templates × (digital, scanned_noisy, photo) | Exact (the template) |

```bash
bun run eval -- dataset add ~/doc.pdf --id my-case [--range income:10-16]   # copy, hash, suggest labels, draft gold
bun run eval -- dataset mark my-case --gold verified --labels verified      # after reviewing
bun run eval -- dataset synth                                               # rebuild synthetic + links suites
bun run eval -- dataset check
```

Quality is reported over **all** units and over **verified-gold** units separately, because draft gold copied from Max flatters Max.

### Everything from the CLI or the web UI

`bun run ui` serves the web UI at http://127.0.0.1:8765. Every action is available in both places. The UI runs long work (building the image, adding a PDF, runs, resume, rescore) as **jobs**: each job is the same `extractor-eval` command, started in the background. Jobs run one at a time in order, and their live log is on the Jobs page.

| Task | Web UI | CLI |
|---|---|---|
| Build the extractor image | Setup → Build image | `image build [--extractor-root ../whimsync]` |
| Add your own PDFs | Dataset → Add PDFs (drag and drop, several at once) | `dataset add file.pdf --id my-doc [--range id:S-E] [--suite mine]` |
| Correct the expected Markdown | Dataset → case: source page, editor and live preview side by side (Ctrl+S) | `dataset gold my-doc p001 [--from fixed.md]` |
| Label pages (route, classifier reasons, tags, required phrases) | Dataset → case → Page labels (saves on change) | `dataset label my-doc 3 --route vlm_required --reasons scanned --tags footer --phrase "..."` |
| Mark gold or labels verified | Dataset → case → draft / verified | `dataset mark my-doc --gold verified --labels verified` |
| Change suite or description, delete a case | Dataset → case | `dataset edit …`, `dataset remove my-doc` |
| Rebuild synthetic suites, check the dataset | Dataset sidebar | `dataset synth`, `dataset check` |
| Start a run | New run (pick cases or suites, tiers, image, judge, model, label, groups) | `run --case … --tiers … --label … --group …` |
| Resume or rescore a run | Run page → Resume / Rescore | `run --resume <run>`, `rescore <run>` |
| Follow or cancel work | Jobs | `jobs list`, `jobs log <id>`, `jobs cancel <id>` |
| Compare runs or tiers side by side | Runs → tick runs → Compare | `compare <runA> <runB> [--tier max] [--scope verified]` |
| Rename, group, comment, delete | Runs list and run pages | `runs rename`, `runs group --add/--remove`, `runs group-rename`, `runs comment --case --unit --tier`, `runs delete` |

**Typical loop with your own documents:**
1. Add the PDFs.
2. Fix each case's expected Markdown and labels, then mark them verified.
3. Run the cases. Pick "Verified gold only" when comparing runs.

After changing whimsync's extractor code, rebuild the image and run again. Then compare the new run with the earlier one.

Results live in `extractor/results/runs/<run-id>/`. Claims runs (`claims/results/*.json`) appear in the same list as `claims:<file>`. Names, groups and comments are stored as JSON next to each run (`meta.json`, `comments.json`). Job records and logs are in `extractor/results/jobs/`.

The UI only accepts requests addressed to `localhost` or `127.0.0.1`, because it can start Docker jobs.

## Claim extractor (`claims/`)

```bash
bun run claims -- --runs 3 --rpm 12 --label baseline
LLM_MODEL=gemini-3.5-flash bun run claims -- --runs 3 --rpm 12 --label flash
bun run eval -- compare claims:<A> claims:<B>     # or select both in the UI
```

The suite calls whimsync's `executeStructuredExtraction` directly (it needs the sibling `../whimsync` checkout). Metrics: claim P/R/F1 (embedding-matched), kind accuracy, entity triples, mutation accuracy, evidence validity, referential integrity, noise clean rate and latency, each as mean ± std over `--runs`.

## Tests

```bash
bun run test              # claims + extractor-eval (no Docker, no API calls)
bun run lint
```
