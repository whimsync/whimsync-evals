import { describe, expect, test } from "bun:test";
import type { CognitiveExtractionResult } from "@whimsync/core";
import {
  type BenchEpisode,
  cosine,
  entityTripleMatches,
  matchClaims,
  percentile,
  prf,
  scoreEntities,
  scoreEpisode,
  scoreEvidence,
  scoreMutations,
  scoreReferentialIntegrity,
} from "../src/metrics";

const CANDIDATE_ID = "11111111-1111-4111-8111-000000000001";

const episode: BenchEpisode = {
  id: "t",
  tags: ["fact"],
  rawText: "I moved from Pune to Berlin. I like tea.",
  candidateClaims: [{ id: CANDIDATE_ID, content: "User lives in Pune.", kind: "fact" }],
  expected: {
    claims: [
      { content: "User moved to Berlin.", kind: "fact" },
      { content: "User likes tea.", kind: "preference" },
    ],
    entities: [{ subject: "User", predicate: "moved_to", object: "Berlin" }],
    mutations: [{ targetClaimId: CANDIDATE_ID, action: "update" }],
  },
};

function result(overrides: Partial<CognitiveExtractionResult> = {}): CognitiveExtractionResult {
  return {
    claims: [
      {
        tempId: "c1",
        content: "User moved to Berlin",
        kind: "fact",
        confidence: 1,
        categories: [],
      },
      {
        tempId: "c2",
        content: "User likes tea",
        kind: "fact",
        confidence: 1,
        categories: [],
      },
      {
        tempId: "c3",
        content: "User owns a cat",
        kind: "fact",
        confidence: 1,
        categories: [],
      },
    ],
    memoryRelationships: [],
    entityRelationships: [
      {
        subject: "user",
        predicate: "moved_to",
        object: "Berlin, Germany",
        scope: "personal",
        rationale: "",
        sourceTempId: "c1",
      },
    ],
    mutations: [
      {
        targetClaimId: CANDIDATE_ID,
        action: "update",
        replacementContent: "User lives in Berlin.",
        reason: "",
      },
    ],
    evidence: [
      { claimTempId: "c1", excerpt: "I moved from Pune to Berlin." },
      { claimTempId: "c2", excerpt: "I love tea." },
    ],
    ...overrides,
  };
}

describe("bench metrics", () => {
  test("prf handles empty sets", () => {
    expect(prf(0, 0, 0).f1).toBe(1);
    expect(prf(0, 2, 0).precision).toBe(0);
    expect(prf(0, 0, 2).recall).toBe(0);
    expect(prf(2, 4, 2)).toMatchObject({ precision: 0.5, recall: 1 });
  });

  test("cosine similarity", () => {
    expect(cosine([1, 0], [1, 0])).toBeCloseTo(1);
    expect(cosine([1, 0], [0, 1])).toBeCloseTo(0);
    expect(cosine([0, 0], [1, 0])).toBe(0);
  });

  test("matchClaims is greedy one-to-one above threshold", () => {
    const pred = [
      [1, 0, 0],
      [0.9, 0.1, 0],
      [0, 0, 1],
    ];
    const ref = [
      [1, 0, 0],
      [0, 1, 0],
    ];
    const matches = matchClaims(pred, ref, 0.8);
    expect(matches).toHaveLength(1);
    expect(matches[0]).toMatchObject({ predIndex: 0, refIndex: 0 });
  });

  test("entity triples normalize case, snake_case and containment", () => {
    expect(
      entityTripleMatches(
        { subject: "the user", predicate: "lives_in", object: "Pune, India" },
        { subject: "User", predicate: "lives in", object: "Pune" },
      ),
    ).toBe(true);
    expect(
      entityTripleMatches(
        { subject: "User", predicate: "likes", object: "Bun" },
        { subject: "User", predicate: "likes", object: "Node" },
      ),
    ).toBe(false);
    const s = scoreEntities(
      [
        { subject: "User", predicate: "works_at", object: "Fernway" },
        { subject: "User", predicate: "owns", object: "car" },
      ],
      [{ subject: "User", predicate: "employed_at", object: "Fernway" }],
    );
    expect(s).toMatchObject({ tp: 1, precision: 0.5, recall: 1 });
  });

  test("missing mutation counts as noop", () => {
    const { accuracy, confusion } = scoreMutations(
      [],
      [
        { targetClaimId: "a", action: "noop" },
        { targetClaimId: "b", action: "update" },
      ],
    );
    expect(accuracy).toBe(0.5);
    expect(confusion).toEqual({ "noop->noop": 1, "update->noop": 1 });
    expect(scoreMutations([], []).accuracy).toBeNull();
  });

  test("evidence must be a verbatim substring", () => {
    const ev = scoreEvidence(result(), episode.rawText);
    expect(ev.validity).toBe(0.5);
    expect(ev.claimsCovered).toBeCloseTo(1 / 3);
  });

  test("referential integrity flags dangling ids", () => {
    const r = result({
      evidence: [{ claimTempId: "missing", excerpt: "x" }],
      mutations: [{ targetClaimId: "not-a-candidate", action: "delete", reason: "" }],
    });
    // entity sourceTempId c1 resolves; evidence and mutation do not.
    expect(scoreReferentialIntegrity(r, [CANDIDATE_ID])).toBeCloseTo(1 / 3);
  });

  test("scoreEpisode combines all metrics", () => {
    // pred c1~ref0, c2~ref1, c3 unmatched
    const m = scoreEpisode({
      episode,
      result: result(),
      predClaimEmbeddings: [
        [1, 0, 0],
        [0, 1, 0],
        [0, 0, 1],
      ],
      refClaimEmbeddings: [
        [1, 0, 0],
        [0, 1, 0],
      ],
      simThreshold: 0.8,
    });
    expect(m.claims).toMatchObject({ tp: 2, nPred: 3, nRef: 2 });
    expect(m.claims.recall).toBe(1);
    expect(m.kindAccuracy).toBe(0.5);
    expect(m.entities?.f1).toBe(1);
    expect(m.mutationAccuracy).toBe(1);
    expect(m.hallucinatedClaims).toBeNull();
  });

  test("hallucination counted only on zero-claim episodes", () => {
    const noise: BenchEpisode = {
      ...episode,
      candidateClaims: [],
      expected: { claims: [], mutations: [] },
    };
    const m = scoreEpisode({
      episode: noise,
      result: result(),
      predClaimEmbeddings: [[1], [1], [1]],
      refClaimEmbeddings: [],
      simThreshold: 0.8,
    });
    expect(m.hallucinatedClaims).toBe(3);
    expect(m.claims.precision).toBe(0);
  });

  test("percentile", () => {
    expect(percentile([5, 1, 3, 2, 4], 50)).toBe(3);
    expect(percentile([5, 1, 3, 2, 4], 95)).toBe(5);
    expect(percentile([], 50)).toBeNull();
  });
});
