# Extending the evaluations

Each change below is local. Make it in one place and the runner, `compare`, the CLI and the UI pick it up. Run `bun run test` afterwards.

## Add a real document

```bash
bun run eval -- dataset add ~/Downloads/report.pdf --id quarterly-report --description "..."
bun run eval -- dataset add ~/Downloads/10k.pdf --id big-filing --range statements:40-45 --range notes:80-82
```

This does the following:

1. Copies the PDF to `extractor/cases/<id>/source.pdf` and records its SHA-256.
2. Parses it with the VLM disabled, which is free, and writes the classifier's suggestions as draft `page_labels`.
3. Parses only the scored pages with `--draft-tier` (default `max`) and writes **draft** gold.

Then finish it by hand:

1. Correct `expected/pages/NNN.md` (or `expected/ranges/*.md`) against the PDF. That gold is the reference everything is scored on.
2. Edit `page_labels` in `dataset.json`:
   - set `expected_route`;
   - keep only valid `expected_classifier_reasons`;
   - add `tags` and `required_phrases`.
3. Add `expected/links.json` if links matter: `{"links": [{"page": 1, "anchor": "...", "url": "..."}]}`.
4. Run `bun run eval -- dataset mark <id> --gold verified --labels verified`.
5. Run `bun run eval -- dataset check`.

## Add a synthetic template

Add a Markdown file to `extractor/src/extractor_eval/synth/templates/`. Optional frontmatter (`description`, `columns`) controls rendering. Then run `bun run eval -- dataset synth`. Each template gets a digital, a `scanned_noisy` and a `photo` case with exact gold. Scan styles live in `synth/augment.py` (`PROFILES`).

## Add a link case

Add an entry to `_links_cases()` in `synth/build.py` with the page lines, a linker function that places annotations, the expected links and the gold pages. Then rerun `dataset synth`.

## Add a metric

1. Compute it in `deterministic.py`, `links.py` or `routing.py` (unit-level metrics go in the `metrics` dict, explanations in `details`).
2. Declare it in `metrics.py` `CATALOG`, with its label, group, `better` direction, unit and whether it's a headline metric.

Aggregation, `compare`, the console tables and the UI read the catalog, so nothing else changes. Then run `bun run eval -- rescore <run>` to add the metric to existing runs without new API calls.

## Change the judge

Edit `RUBRIC` or `Verdict` in `judge.py`. The rubric hash becomes the new `prompt_version`, so `compare` flags runs judged with different rubrics.

## Add a tier

Add it to `TIERS` in `extractor_eval/__init__.py` once the extractor's `ParseTier` has it. The container already accepts any tier the checkout defines.
