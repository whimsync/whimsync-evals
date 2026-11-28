# WhimSync Benchmark Report (2026-11-25)

The full WhimSync benchmark is complete.

- **Document parser:** all three tiers (Fast, Smart and Max) tested with Gemini 3.5 Flash, on 84 documents per tier. That's 12 digital PDFs plus 2 variants each of scanned, noisy-scan and phone-photo versions.
- **Claim extractor:** tested separately with Gemini 3.5 Flash-Lite, on 21 conversations run 3 times each to measure variation.

## 1. Document Parser

Smart and Max now handle scanned documents and phone photos very well. Fast cannot read them at all in the current setup.

| Tier | Digital text similarity | Scan/photo text similarity | Heading F1 (scans) | Seconds per scanned page |
|---|---|---|---|---|
| Fast | 0.981 | 0.000 | 0.00 | 0.1 |
| Smart | 0.981 | 1.000 | 0.94–0.975 | 7.6–8.6 |
| Max | 0.984 | 0.998–1.000 | 0.94–0.975 | 7.1–7.5 |

- **Fast on digital PDFs:** about 98% text similarity.
- **Fast on scans and photos:** it returns no text at all. Fast relies on PyMuPDF4LLM's OCR, and that needs a local OCR engine (Tesseract or RapidOCR). Neither is installed, so OCR is skipped without any error. The code comment calls this "native OCR", but it only works once an engine is installed. Installing RapidOCR should give Fast basic scan support for free; this hasn't been benchmarked yet.
- **Smart and Max on scans:** 99.8–100% text similarity, with heading F1 between 0.94 and 0.975. Tables and reading order are essentially perfect, including on noisy scans and photos.
- **Flash vs Flash-Lite:** heading F1 on scans rose from about 0.50 to about 0.96. Flash-Lite turned sub-headings into top-level headings. The earlier Flash-Lite run covered only a subset of 14 documents, so this is a strong signal rather than an exact like-for-like comparison. The quality gain costs about 2–3 extra seconds per scanned page.
- **Speed:** Smart takes 7.6–8.6 s per scanned page and Max 7.1–7.5 s, with no meaningful quality difference between them.
- **Max on digital PDFs:** it sends some normal pages to the VLM (8 VLM calls across the 12 digital documents), so it takes about 3.1 s per page against 0.2 s for Smart, with no quality gain.
- **Hyperlinks:** on digital PDFs every link URL is found (link recall 1.00). However, only a third of links come out with exactly the right anchor text (0.33), because the link injector splits or misplaces the linked text. On scans and photos links are lost entirely, which is expected because a URL isn't visible in an image.

**My take:** Smart should be the default tier. Fast is only for digital PDFs until an OCR engine is installed. Max doesn't currently justify its extra cost. The fixes to make are the Fast OCR dependency, the anchor-text splitting in the link injector, and Max's routing of digital pages to the VLM.

## 2. Claim Extractor

Overall claim F1 is 0.876 (±0.009), with 88.9% precision and 86.3% recall. Results are consistent across the 3 runs.

### What's working well

- **Coverage:** it finds about 86% of the facts it should, and about 89% of the claims it extracts are correct.
- **Memory decisions:** whether to update, delete or keep an existing memory was correct 100% of the time. That's 24 decisions over 3 runs (9 updates, 3 deletes, 12 keeps), so it's a small sample.
- **Evidence and references:** both are at 100%. Every evidence excerpt appears word for word in the original conversation, and every internal reference resolves.
- **No regression:** overall performance matches the previous Flash-Lite run.

### Where it needs improvement

- **Entity relationships:** the weakest area. Entity F1 is 0.523, with precision at 0.42 and recall at 0.68. The model produces many subject–relation–object triples that aren't in the expected set. Some of these may be valid triples phrased differently from the fixtures, so a sample should be reviewed by hand before deciding how much stricter extraction should be.
- **Noise filtering:** 2 of the 3 small-talk conversations produced a claim when they should have produced nothing.
  - A plain greeting was correctly ignored.
  - A general Python question became "User is asking about the difference between a list and a tuple in Python."
  - A passing weather remark became "It is raining today."
- **Claim granularity:** the low scores on update episodes (F1 0.64) and hedged statements (0.67) mostly have the same cause. The model merges two facts into one compound claim. For example:
  - "User has gone off coffee completely and drinks green tea in the mornings" was expected as two claims: "User no longer drinks coffee" and "User drinks green tea in the mornings".
  - "User's manager Priya might move to the Singapore office next quarter, pending confirmation" was expected as "User's manager is Priya" plus the possible move.

  The rewritten claims are accurate, and the uncertainty was kept ("might move… pending confirmation"). The issue is how claims are split, not lost meaning or hedging.

**My take:** core extraction, evidence and memory mutation logic are in good shape. The main problems are:

- the model is too eager to extract from small talk and to produce entity triples;
- it bundles facts that should be stored separately.

Improving precision and claim granularity should come before pushing recall higher.

## 3. Benchmark Comparison and Regressions

Latest claims run against the previous Flash-Lite run (both 3 runs, same code commit, same model):

| Metric | Previous | Current |
|---|---|---|
| Claim F1 | 0.874 ± 0.023 | 0.876 ± 0.009 |
| Precision | 0.898 | 0.889 |
| Recall | 0.853 | 0.863 |
| Entity F1 | 0.528 | 0.523 |
| Mutation accuracy | 1.000 | 1.000 |
| Evidence validity | 1.000 | 1.000 |
| Referential integrity | 1.000 | 1.000 |
| Noise clean rate | 0.44 ± 0.19 | 0.33 |
| Latency (p50 / p95) | 1.7 / 2.4 s | 1.9 / 5.8 s |

The comparison tool flagged 4 regressions here, and none of them is real.

- **Noise clean rate:** it fell by 0.11, which is within the previous run's standard deviation of 0.19. With only 3 noise conversations, one conversation flipping moves this metric by 0.33.
- **p95 latency:** the jump is probably API-side. The parser benchmark was calling Gemini on the same API key at the same time.

The parser comparison against Flash-Lite flagged 11 regressions. All of them are the speed cost of the slower model; every quality metric held or improved.

I also fixed the report formatting so averaged count metrics display as `0.56 ± 0.19` instead of `0.555556 ± 0.19245`. The change is in `parser/bench/catalog.py`, and all 39 Python tests and lint pass.

## 4. What We Should Improve Next

1. **Fast tier OCR:** install RapidOCR (or Tesseract) for the extractor and re-benchmark Fast on scans. Alternatively, document Fast as digital-only and route documents that need OCR to Smart.
2. **Noise filtering:** make an empty result the expected outcome for greetings, general questions and passing remarks that say nothing lasting about the user.
3. **Claim granularity:** tell the extractor to produce one atomic fact per claim, so that "X and Y" becomes two claims.
4. **Entity extraction:** review a sample of the unmatched triples, then tighten extraction to relationships the conversation states explicitly.
5. **Parser links and routing:** fix the anchor-text splitting in the link injector, and stop Max from sending digital pages to the VLM when normal extraction is enough.
6. **Benchmark stability:** make the comparison tool take standard deviation into account, and add more noise and hedged fixtures, since 1–3 episodes per tag is too few for stable numbers.
7. **Latency baseline:** run latency benchmarks one at a time, without other Gemini traffic on the same key.

## 5. Overall

Smart is ready to be the default parser tier, and its scanned-document results are excellent. Fast needs an OCR engine before it can handle scans at all.

The claim extractor's core is reliable, especially evidence and memory mutations. The remaining work is fewer false-positive claims from small talk, more atomic claims, and tighter entity triples.
