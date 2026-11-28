# Issue drafts (2026-11-26)

Drafts based on the 2026-11-25 benchmark runs (`parser/results/20261125T150109Z-flash-full.json`,
`claims/results/20261125T150109Z-lite-3runs.json`). Part 1 is comments on existing GitHub issues.
Parts 2 and 3 are new issues for whimsync/whimsync and whimsync/whimsync-evals.

---

# Part 1: Comments on existing issues (whimsync/whimsync)

## Comment on #32: Consider Removing the Fast Extraction Tier

The benchmark this issue asks for is done (whimsync-evals, `flash-full` run, 2026-11-25). On ordinary digital PDFs, Smart runs the same PyMuPDF4LLM path as Fast and gives identical results:

| Digital PDFs (12 docs) | Fast | Smart |
|---|---|---|
| Text similarity | 0.981 | 0.981 |
| Tables / headings / link recall | identical | identical |
| Seconds per page | 0.179 | 0.198 (+0.02 s) |
| VLM calls | 0 | 0 |

On scans and photos (72 docs), Fast returns no text at all (0.00) and Smart scores about 1.00. The cause: PyMuPDF4LLM's OCR needs Tesseract or RapidOCR installed, and neither is. With no engine found, `select_ocr_function()` returns `None` and OCR is skipped without an error. The README's "native OCR" description of Fast is therefore inaccurate.

**Recommendation: remove Fast.** Three caveats before closing this issue:

1. **Embedded images weren't tested.** The test corpus has no digital pages with charts or photos, and Smart sends those to the VLM. One such case should be benchmarked to see what it costs.
2. **Memory and CPU weren't measured,** only time per page. The code path is the same, so no real difference is expected.
3. **Smart can silently produce empty pages too.** If `GEMINI_API_KEY` is missing, `can_use_vlm` is false and scanned pages fall back to digital extraction, which returns nothing. A failed VLM call also returns `""`. Removing Fast doesn't close the data-integrity gap described above by itself. See the new issue "Parser silently stores empty text for pages that needed OCR".

Suggest doing this together with #33 in one PR, since both change the same tier enum.

## Comment on #33: Rename Extraction Tiers: Smart → Balanced

Places the rename touches:

| Layer | File |
|---|---|
| API schema | `apps/api/src/schemas/memory.ts:81` |
| API service | `apps/api/src/services/memoryIngestService.ts:37, 210, 225` |
| Queue contract | `packages/core/src/queue/jobs.ts:11` |
| DB default | `packages/db/src/schema/ingestionRecords.ts:59` |
| Python enum | `apps/extractor/src/schemas/parsing.py` (`ParseTier`) |
| Python queue job | `apps/extractor/src/schemas/queue_jobs.py:25` |
| Dispatch | `apps/extractor/src/parsing/engine.py` (strategy map and fallback default) |
| Consumer default | `apps/extractor/src/consumers/parser_consumer.py:38` |
| Strategy | `strategies/smart.py` → `balanced.py` (class name, log prefixes, `"hybrid_smart"` metadata) |
| Tests | `apps/extractor/tests/test_schemas.py` (and `test_fast_strategy.py` if #32 lands) |
| Docs | `apps/api/README.md:41`, `apps/extractor/README.md:16-17`, `apps/extractor/AGENTS.md:44-45`, `docs/architecture.md:49-50, 148-149` |

**Keep old names working during rollout:**

- **API:** accept `"smart"` (and `"fast"` if #32 lands) as deprecated aliases for `"balanced"` with `z.preprocess` in front of the enum, so existing clients don't get 400 errors.
- **Extractor:** add `ParseTier._missing_` to map old names to `BALANCED`, and widen the `Literal` in `queue_jobs.py`. Jobs already queued as `"smart"` during a deploy then still parse.
- **Database:** run `bun run db:generate` for the new `'balanced'` default. Never edit migration `0007` by hand. Add a custom migration with `UPDATE ingestion_records SET parsing_tier = 'balanced' WHERE parsing_tier IN ('smart', 'fast')`, or treat old values as aliases when reading.
- **Evals:** the whimsync-evals repo uses the tier names in `bench/run.py` and its reports, so it needs the same rename, plus an alias for old results files.

---

# Part 2: New issues for whimsync/whimsync

## Worker has no Gemini rate limiting or retry

**Labels:** bug, reliability

`executeStructuredExtraction` (`apps/worker/src/services/llmService.ts`) and `generateTextEmbeddingsBatch` (`embeddingService.ts`) call Gemini with no delay, rate limit or retry. The only limit is BullMQ `concurrency: 5` (`episodeConsumer.ts:34`, `chunkerConsumer.ts:146`), with no per-minute cap. Each episode makes one extraction call plus one embedding call, so a backlog easily goes over free-tier limits (about 15 requests per minute). Queue jobs have no `attempts`, so a 429 error fails the job permanently.

**Fix:**

- Add a BullMQ `limiter` (e.g. `{ max: 12, duration: 60_000 }`, configurable through env) to both workers.
- Set `attempts` with exponential backoff on these queues.
- Treat 429 and 503 as retryable, and bad key or unknown model as fatal.

## Extractor VLM pacing doesn't bound requests per minute

**Labels:** bug, reliability. **Fix exists locally, not yet committed.**

`VLM_DELAY_SECONDS=0.5` sleeps after each successful call inside each of 3 semaphore slots, so the request rate is set by Gemini's response time. With replies taking 5–8 s that works out to about 25–35 requests per minute. Retries only covered 429 errors, and a daily-quota 429 (`retryDelay` of about 8,000 s) made the worker sleep for hours.

**Fix (local branch):**

- `utils/rate_limiter.py` adds a `VLM_RPM` limiter that spaces request starts.
- The VLM service honours Gemini's `retryDelay` and adds jitter.
- It retries 503 and overloaded errors.
- It gives up when the requested wait is over 120 s.
- It doesn't retry errors that can't succeed.
- Production needs `VLM_RPM` set to match the Gemini tier.

## Claim extraction sends Gemini an empty response schema

**Labels:** bug. **Fix exists locally, not yet committed.**

`zod-to-json-schema` doesn't support Zod v4 and silently returns an empty schema, so `responseSchema` didn't constrain Gemini's output.

**Fix (local branch):**

- Generate the schema with Zod v4's native `z.toJSONSchema` (`CognitiveExtractionResultJsonSchema` in `packages/core`).
- Send it as `responseJsonSchema` from `llmService.ts`.
- Drop the `zod-to-json-schema` dependency.

## Parser silently stores empty text for pages that needed OCR

**Labels:** bug, data-integrity

In Smart (and Max), a page that needs OCR comes out empty in two cases:

- The VLM is unavailable (no API key), so the page falls back to digital extraction, which has no text layer.
- The VLM call fails and returns `""`.

The job still succeeds, and empty or partial Markdown goes on to chunking, embeddings and memories.

**Fix:**

- Record those pages in `ParsedDocument.metadata["unextracted_pages"]`.
- In `parser_consumer.py`, mark the ingestion `failed` (or `partial` with a reason) when that list isn't empty, instead of queueing chunking.

## Link injector splits anchor text

**Labels:** bug, parser

On digital PDFs every link URL is recovered (link recall 1.00), but only 33% of links have the exact anchor text: `[text](url)` comes out split or misplaced around the original words. This affects every tier, because `inject_links_inline` runs last on all of them.

**Fix:** match link annotation rectangles to whole text spans or words and wrap the full span, instead of inserting at partial character offsets. Add a regression test with multi-word anchors, and anchors that wrap across lines.

## Max tier sends digital pages to the VLM with no quality gain

**Labels:** performance, cost

On the 12 digital PDFs, Max made 8 VLM calls and averaged 3.1 s per page, against 0.2 s and 0 VLM calls for Smart. Quality was identical (text similarity 0.984 vs 0.981).

**Fix:**

- Review Max's routing threshold (`img_cov` / `vg_cov`) so clean digital text pages use digital extraction.
- Or define what Max is meant to add over Smart. If the answer is nothing, consider merging it into Balanced.

## Claim extractor saves claims from small talk

**Labels:** extraction-quality

In 2 of 3 small-talk episodes the extractor produced claims when it should have produced none:

- A general Python question became "User is asking about the difference between a list and a tuple in Python."
- "Ugh, it's raining again today. Anyway, never mind." became "It is raining today."

A plain greeting was correctly ignored. Noise clean rate is 0.33.

**Fix:**

- State in the extraction prompt that an empty result is expected for greetings, general knowledge questions and passing remarks that say nothing lasting about the user.
- Add negative few-shot examples.

## Claim extractor merges several facts into one claim

**Labels:** extraction-quality

The low claim F1 on update episodes (0.64) and hedged episodes (0.67) comes mostly from compound claims, not wrong content:

- "I've gone off coffee; I only drink green tea in the mornings" became one claim, where two were expected ("User no longer drinks coffee", "User drinks green tea in the mornings").
- "My manager Priya might move to Singapore…" became one claim, where "User's manager is Priya" plus the possible move were expected.

The rewritten claims are accurate, and the uncertainty was kept ("might move… pending confirmation"). Compound claims make later updates and deletes less precise.

**Fix:** require one atomic fact per claim in the prompt, with an example that splits "X and Y".

## Entity triple precision is low (0.42)

**Labels:** extraction-quality, needs-investigation

Entity F1 is 0.523 (precision 0.42, recall 0.68): many proposed subject–predicate–object triples aren't in the expected set. Some may be valid triples phrased differently from the fixtures, which would make this partly a matching artefact.

**Fix:**

1. Hand-review a sample of the unmatched triples from the benchmark results.
2. If they really are unsupported, limit extraction to relationships the conversation states explicitly, and give a list of canonical predicates.
3. If they're valid but phrased differently, fix the eval's triple matching instead.

---

# Part 3: New issues for whimsync/whimsync-evals

## `compare` flags run-to-run noise as regressions

**Labels:** eval

`compare` uses a fixed threshold (0.02), so it flagged 4 claims regressions between two runs on the same commit and model. For example, the noise clean rate fell by 0.11 while the previous run's standard deviation was 0.19. Used as a CI gate, this would fail builds at random.

**Fix:**

- When both runs have std (`--runs` > 1), only flag a change larger than about 2× the pooled std.
- Show a "within noise" verdict instead of REGRESSION.
- Apply the same rule in the HTML report.

## Too few fixtures for noisy tags

**Labels:** eval, fixtures

Several tags rest on 1–3 episodes (noise: 3, hedged: 1), so one episode flipping moves the metric by 0.33 to 1.0.

**Fix:**

- Grow each tag to at least 8–10 episodes, starting with noise, hedged, update and noop.
- Add a digital parser case with an embedded chart or image (needed to close whimsync#32).

## Decouple evals from whimsync internals with an adapter contract

**Labels:** eval, architecture

The evals import whimsync internals: an editable install of `apps/extractor`, and tsconfig paths into `apps/worker/src/services/*`. They also require `../whimsync` as a sibling folder. Any internal refactor can break them, and the evals need both Python and TypeScript.

**Proposal:**

1. whimsync owns a thin adapter per capability, with a JSON stdin/stdout CLI or an HTTP endpoint:
   - `parse(pdf, tier)` returns `{markdown, pages, vlm_calls}`;
   - `extract(text, candidates)` returns `{claims, entities, mutations, evidence}`.
2. The evals call only the adapter. The target is configured with `WHIMSYNC_PATH`, an image tag or a URL.
3. Port the claims runner to Python and delete the duplicated TypeScript `results.ts` / `gemini.ts`.
4. Add a CI workflow triggered from whimsync with the commit SHA. Store results as artifacts and gate PRs against a baseline with `compare`.

## Separate latency runs from concurrent API traffic

**Labels:** eval

Claims p95 latency went from 2.4 s to 5.8 s while the parser benchmark was calling Gemini on the same key at the same time, so latency numbers from concurrent runs can't be compared.

**Fix:**

- Record in the results file whether another suite was running.
- Document that latency baselines come from isolated runs.
- Optionally add a `--latency-only` mode that runs a small fixed subset serially.
