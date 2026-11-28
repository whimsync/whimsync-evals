# Running the evaluations

This is a step-by-step guide to running the evaluations, from a fresh clone to a comparison between two runs. Every step works from the command line or the web UI.

- How scoring works: [framework.md](framework.md)
- Adding metrics, templates and tiers: [extending.md](extending.md)

## 1. Prerequisites

| Need | Why |
|---|---|
| [Bun](https://bun.sh) and [uv](https://docs.astral.sh/uv/) | Bun runs the claims suite and the scripts; uv runs `extractor-eval` |
| Docker | The parser runs inside a container built from whimsync |
| A whimsync checkout next to this repo (`../whimsync`) | The image is built from it, and it must contain `apps/extractor/Dockerfile.eval`, `src/eval_cli.py` and `src/parsing/evaluation_trace.py` |
| A Gemini API key | Needed for the `balanced` and `max` tiers, the judge and the claims suite. Native-only parsing with `--no-judge` doesn't need one. |

Those three files aren't on whimsync `main` yet; they're in [whimsync#40](https://github.com/whimsync/whimsync/pull/40). Until it merges, check that branch out in your whimsync clone:

```bash
cd ../whimsync
git fetch https://github.com/Jishnu-Prasad888/whimsync feat/extractor-eval-support
git checkout FETCH_HEAD          # or: gh pr checkout 40 --repo whimsync/whimsync
```

On Windows, use Docker Desktop with the WSL 2 backend and run the commands from PowerShell. Paths with spaces or OneDrive folders are fine.

## 2. One-time setup

```bash
bun run setup            # bun install + uv sync
cp .env.example .env     # then set GEMINI_API_KEY in .env
```

Keep the key in `.env`, which is gitignored. The evaluator passes it into containers by variable name, so it never appears in a command line or a log. If you're on a free-tier key, keep `VLM_RPM=12` and `JUDGE_RPM=12`.

## 3. Build the extractor image

```bash
bun run eval -- image build                              # from ../whimsync
bun run eval -- image build --extractor-root ~/src/whimsync-branch
bun run eval -- image list
```

The tag is the extractor's git commit, or `<commit>-dirty-<hash>` if `apps/extractor` has uncommitted changes. Each run records the image it used, so rebuild after every extractor change you want to measure. Runs use the newest image unless you pass `--image <tag>`.

In the UI: **Setup → Build image**.

## 4. Build a dataset

The dataset starts empty. You can fill it with generated cases or your own PDFs, or both.

### Generated cases (exact expected output)

```bash
bun run eval -- dataset synth
```

This creates the `synthetic` suite (12 templates, each as a digital, a scanned and a photographed page) and the `links` suite (link-injection edge cases). The expected output is known exactly, so these cases need no review.

### Your own PDFs

```bash
bun run eval -- dataset add ~/Downloads/notice.pdf --id scanned-notice --suite mine --description "Scanned notice with footer"
# Large PDF: score only selected page ranges
bun run eval -- dataset add ~/Downloads/10k.pdf --id annual-report --suite mine --range income:32-36 --range notes:49-50
```

`dataset add` does three things:
1. Copies the PDF into `extractor/cases/<id>/`.
2. Writes draft page labels from the classifier.
3. Writes **draft** expected Markdown from the `max` tier (`--draft-tier none` skips this, which also avoids API calls).

In the UI: **Add PDFs** in the top bar. You can drop several files at once and set the id, suite and ranges for each.

### Review the drafts before you trust the numbers

Draft expected output is just the `max` tier's own answer, so it flatters `max`. For each case you add:

1. **Fix the expected Markdown.**
   - UI: open **Dataset → the case**. The source page, the editor and a live preview sit side by side; press Ctrl+S to save.
   - CLI: `bun run eval -- dataset gold <id> p001 --from fixed.md`. Page units are named `p001`, `p002` and so on; range units use the range id.
2. **Fix the page labels**: expected route, classifier reasons, tags and phrases that must appear.
   ```bash
   bun run eval -- dataset label <id> 1 --route vlm_required --reasons scanned --tags footer --phrase "Page 1 of 2"
   ```
3. **Mark the case verified.**
   ```bash
   bun run eval -- dataset mark <id> --gold verified --labels verified
   ```
4. **Check the dataset.**
   ```bash
   bun run eval -- dataset check
   ```

`dataset list` and `dataset show <id>` print what's there.

## 5. Run an evaluation

```bash
bun run eval -- run --tiers balanced,max --label baseline --group baseline        # every case
bun run eval -- run --suite synthetic,links --tiers balanced,max --label synth
bun run eval -- run --case scanned-notice --tiers max                             # one case
bun run eval -- run --suite mine --no-judge                                       # no judge calls
bun run eval -- run --suite mine --perf-repeats 3 --no-judge                      # timing only
bun run eval -- run --suite mine --model gemini-3.5-flash --label flash           # another VLM model
```

| Option | Meaning |
|---|---|
| `--case`, `--suite` | Which cases to run. The default is every case. |
| `--tiers` | `balanced`, `max`, or both (comma-separated) |
| `--image` | Image tag. The default is the newest built image. |
| `--label`, `--group` | Display name and groups, to find the run later |
| `--no-judge` | Skip the Gemini judge (deterministic, link and routing scores still run) |
| `--judge-model` | Judge model. The default comes from `JUDGE_MODEL`. |
| `--model` | Override `GEMINI_MODEL` for extraction |
| `--perf-repeats N` | N extra unscored parses per small case, for timing |

Each (case, tier) pair runs in its own container. The run prints a summary table when it finishes and writes everything to `extractor/results/runs/<run-id>/`.

In the UI: **New run** in the top bar. Pick cases or suites, tiers, the image, the judge settings, a label and groups. The run goes into the job queue; follow it on the **Jobs** page.

### When a run is interrupted

```bash
bun run eval -- run --resume <run-id>     # skips pairs that already have scores
bun run eval -- rescore <run-id>          # recompute scores from saved outputs (no Docker, no API calls)
```

Use `rescore` after editing expected output or adding a metric. It reuses the saved judge verdicts.

In the UI, the run page has **Resume** and **Rescore** buttons.

## 6. Read the results

```bash
bun run ui                                 # http://127.0.0.1:8765
bun run eval -- runs list [--group baseline]
```

In the UI, a run's page shows:
- headline metrics per tier;
- breakdowns by suite, case and tag;
- routing mistakes;
- cost and timing.

Open a unit to see the source page, the expected Markdown and each tier's output side by side. Each output can be shown raw, rendered, or as a diff. Below that are the judge's verdict and the per-page VLM trace.

Headline metrics:

| Metric | Read it as |
|---|---|
| Text, word and character similarity | How much of the content came through (1.0 is perfect) |
| Reading order, headings, tables, numbers | Structure kept correctly |
| Required phrases, links | Specific content that must appear, checked exactly |
| Judge scores (1–5) and critical errors | A Gemini judge's view, compared against the page image |
| False native and false VLM rates | Pages sent down the wrong route: false native costs quality, false VLM costs money |
| Seconds, VLM pages, tokens, cost | Operational cost. Cost needs rates in `extractor/pricing.json`. |

Quality is shown over **all** cases and over **verified** cases only. Trust the verified numbers.

## 7. Compare runs

```bash
bun run eval -- compare <runA> <runB>                    # every tier of each run
bun run eval -- compare <run> <run> --tier balanced,max  # two tiers of one run
bun run eval -- compare <runA> <runB> --tier max --scope verified
```

Each run and tier gets its own column. A change beyond the tolerance is colored against the first column. The output lists every setting that differs (commit, model, prompt hashes, judge, dataset hashes), so you can tell whether a change came from the code or from the setup.

In the UI: on **Runs**, tick two or more runs, then press **Compare**.

A typical before-and-after loop:
1. Run the baseline and put it in a group: `run --label before --group exp1`.
2. Change the extractor in whimsync, then `image build`.
3. Run again with the same cases: `run --label after --group exp1`. In the UI, **Run again** on the first run pre-fills the form.
4. `compare before after --scope verified`.

## 8. Manage results

```bash
bun run eval -- runs rename <run> "new name"
bun run eval -- runs group <run> --add exp1 --remove old
bun run eval -- runs group-rename exp1 experiment-1
bun run eval -- runs comment <run> "Max regresses on tables" --case <id> --unit p001 --tier max
bun run eval -- runs delete <run>
```

Runs can be addressed by full id, a unique prefix, or a unique display name. Results are kept in `extractor/results/`, which is gitignored. To start over, delete that folder.

## 9. Jobs (UI-started work)

The UI runs long work as jobs, one at a time: image builds, adding PDFs, runs, resume and rescore. Each job is the same `extractor-eval` command run in the background.

```bash
bun run eval -- jobs list
bun run eval -- jobs log <job-id>
bun run eval -- jobs cancel <job-id>
```

## 10. Claim extractor suite

```bash
bun run claims -- --runs 3 --rpm 12 --label baseline
LLM_MODEL=gemini-3.5-flash bun run claims -- --runs 3 --rpm 12 --label flash
bun run eval -- compare claims:<A> claims:<B>
```

This suite calls whimsync's extraction code directly from `../whimsync` and writes to `claims/results/`. Its runs show up in the same results list and comparisons.

## 11. Troubleshooting

| Symptom | Fix |
|---|---|
| `no image built yet` | Run `bun run eval -- image build` |
| `image build`: `...\apps\extractor\Dockerfile.eval not found` | Your whimsync checkout has no evaluation support. Those files aren't on whimsync `main` yet, so check out a branch that has them (see Prerequisites) |
| `balanced`/`max` cases fail or the judge is skipped | Set `GEMINI_API_KEY` in `.env` |
| Errors mentioning 429 or `RESOURCE_EXHAUSTED` | Lower `VLM_RPM` / `JUDGE_RPM`, then `run --resume <id>` |
| `dataset check` reports a hash mismatch | The PDF changed after it was added. Add it again with `dataset add ... --force`. |
| The UI looks outdated after an update | Hard-reload with Ctrl+Shift+R |
| The UI returns 403 | It only answers on `localhost` / `127.0.0.1` |
