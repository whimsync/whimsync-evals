# Extending the benchmarks

Each change below is local: you touch one place and the runners, `compare` and
`report` pick it up. Run `bun run test` afterwards. The tests validate every case
and fixture file, and check that each metric is declared in its catalog.

## Add a document case (parser)

Create `parser/cases/<name>.md`. The body is the exact ground truth:

```markdown
---
description: Quarterly invoice with a totals table.
columns: 1
---
# Invoice 2041
...
```

Frontmatter is optional. The allowed keys are `description` and `columns`. The
case name is the file stem, so `--cases <name>` selects it.

## Add an episode (claims)

Append to the matching category file in `claims/fixtures/episodes/`, or add a new
`*.json` file. Each file holds an array of episodes:

```json
{
  "id": "unique_id",
  "tags": ["fact"],
  "rawText": "The episode text.",
  "candidateClaims": [{ "id": "<uuid>", "content": "Prior claim.", "kind": "fact" }],
  "expected": {
    "claims": [{ "content": "Expected claim.", "kind": "fact" }],
    "entities": [{ "subject": "User", "predicate": "lives_in", "object": "Pune" }],
    "mutations": [{ "targetClaimId": "<uuid>", "action": "update" }]
  }
}
```

- `kind` is one of fact, preference, decision, goal, other.
- `action` is one of update, delete, noop, and `targetClaimId` must be one of
  the episode's `candidateClaims`.
- Leave out `entities` to skip entity scoring for that episode.
- Use `"claims": []` for small talk that should produce nothing.
- A new tag automatically gets its own `tag:<tag>:claim_f1` metric.

## Add a scan style (parser)

Add a `ScanProfile` to `PROFILES` in `parser/bench/augment.py`. Its key becomes a
render mode (`--modes <key>`) and appears in reports. Existing variants stay
reproducible because seeds are derived from (case, mode, variant).

## Add a parser metric

Write one decorated function in `parser/bench/metrics.py`:

```python
@scorer(MetricSpec("list_items_f1", "List items F1", "lists", summary=True))
def _lists(pred: str, ref: str) -> Scores:
    return {"list_items_f1": ...}  # None when the document has no lists
```

`summary=True` adds it to the console table. Use `better="lower"` for costs and
`tolerance="relative"` for timing. Compare and report need no changes.

## Add a claims metric

Compute it in `summarizeRun` (`claims/src/summary.ts`) and declare it in
`CATALOG` (`claims/src/catalog.ts`). Per-episode values go into `scoreEpisode`
(`claims/src/metrics.ts`).

## Add a new report slice (parser)

`group_keys()` in `parser/bench/runner.py` decides which summary groups a record
belongs to (by default `tier/mode` and `tier/all`). Yield more keys, such as
`f"{tier}/{case}"`, to slice differently. Compare picks them up automatically.

## Add a new suite

1. Write a runner in any language. It writes a results file in the schema shown
   in [architecture.md](architecture.md) with a new `kind` and an embedded
   `catalog`.
2. Register a `ResultKind` in `parser/bench/kinds.py`. It defines how to read
   groups, cases and the model name from that kind of file.
3. Add a view in `parser/bench/report/` and register it in `report.VIEWS`.

`compare` then works for the new suite with no further changes.
