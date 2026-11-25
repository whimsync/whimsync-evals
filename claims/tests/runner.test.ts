import { describe, expect, test } from "bun:test";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { CognitiveExtractionResult } from "@whimsync/core";
import { catalogFor } from "../src/catalog";
import type { BenchEpisode } from "../src/metrics";
import { checkpointPath, finalPath, ResultStore, readRecords } from "../src/results";
import { cachedEmbedder, createRunner, type EpisodeRun, runKey } from "../src/runner";
import { combineRuns, formatSummary, summarizeRun } from "../src/summary";

const episode: BenchEpisode = {
  id: "tea",
  tags: ["preference"],
  rawText: "I like tea.",
  candidateClaims: [],
  expected: { claims: [{ content: "User likes tea.", kind: "preference" }], mutations: [] },
};

const extraction = (content: string): CognitiveExtractionResult =>
  ({
    claims: [{ tempId: "c1", content, kind: "preference", confidence: 1, categories: [] }],
    evidence: [{ claimTempId: "c1", excerpt: "I like tea." }],
    entityRelationships: [],
    memoryRelationships: [],
    mutations: [],
  }) as unknown as CognitiveExtractionResult;

/** Fake embedder: identical text -> identical unit vector, else orthogonal. */
const vocab = new Map<string, number>();
const fakeEmbed = async (texts: string[]) =>
  texts.map((t) => {
    const key = t.toLowerCase().replace(/[^a-z ]/g, "");
    if (!vocab.has(key)) vocab.set(key, vocab.size);
    return Array.from({ length: 16 }, (_, i) => (i === vocab.get(key) ? 1 : 0));
  });

describe("runner", () => {
  test("scores a matching extraction and records attempts", async () => {
    let calls = 0;
    const run = createRunner({
      extract: async () => {
        if (++calls === 1) throw new Error("503 UNAVAILABLE");
        return extraction("User likes tea.");
      },
      embed: fakeEmbed,
      simThreshold: 0.8,
      maxRetries: 2,
    });
    // first attempt fails transiently; withRetry backs off ~2s, so keep the test bounded
    const row = await run(episode, 0);
    expect(row.error).toBeUndefined();
    expect(row.attempts).toBe(2);
    expect(row.metrics?.claims.f1).toBe(1);
    expect(row.metrics?.evidenceValidity).toBe(1);
  }, 10_000);

  test("non-transient errors become an error row, not an exception", async () => {
    const run = createRunner({
      extract: async () => {
        throw new Error("schema mismatch");
      },
      embed: fakeEmbed,
      simThreshold: 0.8,
      maxRetries: 3,
    });
    const row = await run(episode, 1);
    expect(row.error).toBe("schema mismatch");
    expect(row.metrics).toBeUndefined();
  });

  test("embeddings are cached per text", async () => {
    let calls = 0;
    const embed = cachedEmbedder(async (texts) => {
      calls += texts.length;
      return fakeEmbed(texts);
    });
    await embed(["a", "b"]);
    await embed(["a", "b", "c"]);
    expect(calls).toBe(3);
  });
});

describe("summary", () => {
  const ok = (id: string, f1: number): EpisodeRun => ({
    episodeId: id,
    run: 0,
    latencyMs: 100,
    metrics: {
      claims: { precision: f1, recall: f1, f1, tp: f1 ? 1 : 0, nPred: 1, nRef: 1 },
      claimMatches: [],
      kindAccuracy: 1,
      entities: null,
      mutationAccuracy: null,
      mutationConfusion: {},
      evidenceValidity: 1,
      claimsWithValidEvidence: 1,
      referentialIntegrity: 1,
      hallucinatedClaims: null,
    },
  });
  const episodes = new Map([
    ["a", { ...episode, id: "a", tags: ["x"] }],
    ["b", { ...episode, id: "b", tags: ["y"] }],
  ]);

  test("micro-averages claims and slices by tag", () => {
    const s = summarizeRun(
      [ok("a", 1), ok("b", 0), { episodeId: "c", run: 0, latencyMs: 5, error: "e" }],
      episodes,
    );
    expect(s.claim_f1).toBe(0.5);
    expect(s.failure_rate).toBeCloseTo(1 / 3);
    expect(s["tag:x:claim_f1"]).toBe(1);
    expect(s["tag:y:claim_f1"]).toBe(0);
  });

  test("an all-failed run reports only the failure rate", () => {
    expect(summarizeRun([{ episodeId: "a", run: 0, latencyMs: 1, error: "e" }], episodes)).toEqual({
      failure_rate: 1,
    });
  });

  test("combines runs into mean ± std and formats every catalog metric", () => {
    const combined = combineRuns([{ claim_f1: 0.8 }, { claim_f1: 0.6 }]);
    expect(combined.claim_f1?.mean).toBeCloseTo(0.7);
    expect(combined.claim_f1?.std).toBeCloseTo(0.1414, 3);
    expect(formatSummary(combined, [])).toContain("claim_f1");
    expect(catalogFor(["tag:noise:claim_f1"]).at(-1)?.key).toBe("tag:noise:claim_f1");
  });
});

describe("results store", () => {
  test("checkpoints, resumes and finalizes", () => {
    const dir = mkdtempSync(join(tmpdir(), "results-"));
    const out = join(dir, "run.json");
    const store = new ResultStore<EpisodeRun>(out, runKey);
    store.add({ episodeId: "a", run: 0, latencyMs: 1 });
    store.add({ episodeId: "b", run: 0, latencyMs: 1, error: "boom" });
    expect(readRecords<EpisodeRun>(checkpointPath(out))).toHaveLength(2);

    const resumed = new ResultStore<EpisodeRun>(finalPath(checkpointPath(out)), runKey, {
      resumeFrom: checkpointPath(out),
      keep: (r) => !r.error,
    });
    expect(resumed.isDone("a#0")).toBe(true);
    expect(resumed.isDone("b#0")).toBe(false);
    resumed.finalize({ kind: "claim-extractor" });
    const data = JSON.parse(readFileSync(out, "utf-8"));
    expect(data.schema_version).toBe(2);
    expect(data.results).toHaveLength(1);
  });
});
