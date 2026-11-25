import type { CognitiveExtractionResult } from "@whimsync/core";

export type MutationAction = "update" | "delete" | "noop";

export interface ExpectedClaim {
  content: string;
  kind: string;
}

export interface ExpectedEntity {
  subject: string;
  predicate: string;
  object: string;
}

export interface ExpectedMutation {
  targetClaimId: string;
  action: MutationAction;
}

export interface BenchEpisode {
  id: string;
  tags: string[];
  rawText: string;
  candidateClaims: { id: string; content: string; kind: string }[];
  expected: {
    claims: ExpectedClaim[];
    /** Omitted = entity extraction is not scored for this case. */
    entities?: ExpectedEntity[];
    mutations: ExpectedMutation[];
  };
}

export interface PRF {
  precision: number;
  recall: number;
  f1: number;
  tp: number;
  nPred: number;
  nRef: number;
}

export interface ClaimMatch {
  predIndex: number;
  refIndex: number;
  similarity: number;
}

export interface EpisodeMetrics {
  claims: PRF;
  claimMatches: ClaimMatch[];
  kindAccuracy: number | null;
  entities: PRF | null;
  mutationAccuracy: number | null;
  mutationConfusion: Record<string, number>;
  evidenceValidity: number | null;
  claimsWithValidEvidence: number | null;
  referentialIntegrity: number | null;
  hallucinatedClaims: number | null;
}

export function prf(tp: number, nPred: number, nRef: number): PRF {
  const precision = nPred ? tp / nPred : nRef === 0 ? 1 : 0;
  const recall = nRef ? tp / nRef : 1;
  const f1 = precision + recall ? (2 * precision * recall) / (precision + recall) : 0;
  return { precision, recall, f1, tp, nPred, nRef };
}

export function cosine(a: number[], b: number[]): number {
  let dot = 0;
  let na = 0;
  let nb = 0;
  for (let i = 0; i < a.length; i++) {
    const x = a[i] ?? 0;
    const y = b[i] ?? 0;
    dot += x * y;
    na += x * x;
    nb += y * y;
  }
  return na && nb ? dot / Math.sqrt(na * nb) : 0;
}

/**
 * Greedy one-to-one matching on cosine similarity: repeatedly takes the
 * highest-similarity unused (pred, ref) pair at or above `threshold`.
 */
export function matchClaims(
  predEmbeddings: number[][],
  refEmbeddings: number[][],
  threshold: number,
): ClaimMatch[] {
  const pairs: ClaimMatch[] = [];
  predEmbeddings.forEach((p, predIndex) => {
    refEmbeddings.forEach((r, refIndex) => {
      const similarity = cosine(p, r);
      if (similarity >= threshold) {
        pairs.push({ predIndex, refIndex, similarity });
      }
    });
  });
  pairs.sort((a, b) => b.similarity - a.similarity);

  const usedPred = new Set<number>();
  const usedRef = new Set<number>();
  const matches: ClaimMatch[] = [];
  for (const pair of pairs) {
    if (usedPred.has(pair.predIndex) || usedRef.has(pair.refIndex)) continue;
    usedPred.add(pair.predIndex);
    usedRef.add(pair.refIndex);
    matches.push(pair);
  }
  return matches;
}

export function normalizeEntity(value: string): string {
  return value
    .toLowerCase()
    .replace(/[_-]+/g, " ")
    .replace(/[^\p{L}\p{N}\s]/gu, "")
    .replace(/\s+/g, " ")
    .trim();
}

function entityTermMatches(pred: string, ref: string): boolean {
  const p = normalizeEntity(pred);
  const r = normalizeEntity(ref);
  if (!p || !r) return false;
  // Accept "pune" vs "pune india" and "the user" vs "user".
  return p === r || p.includes(r) || r.includes(p);
}

function tokenOverlap(a: string, b: string): number {
  const ta = new Set(normalizeEntity(a).split(" ").filter(Boolean));
  const tb = new Set(normalizeEntity(b).split(" ").filter(Boolean));
  if (!ta.size || !tb.size) return 0;
  let shared = 0;
  for (const t of ta) if (tb.has(t)) shared++;
  return shared / Math.min(ta.size, tb.size);
}

/**
 * Subject and object must match (normalized, containment allowed). Predicates
 * are free-form, so they match when they share at least half their tokens.
 * Predicate mismatches still count when subject+object match exactly, since
 * the expected predicate is only one reasonable phrasing.
 */
export function entityTripleMatches(pred: ExpectedEntity, ref: ExpectedEntity): boolean {
  if (!entityTermMatches(pred.subject, ref.subject)) return false;
  if (!entityTermMatches(pred.object, ref.object)) return false;
  return (
    tokenOverlap(pred.predicate, ref.predicate) >= 0.5 ||
    normalizeEntity(pred.object) === normalizeEntity(ref.object)
  );
}

export function scoreEntities(predicted: ExpectedEntity[], expected: ExpectedEntity[]): PRF {
  const used = new Set<number>();
  let tp = 0;
  for (const ref of expected) {
    const idx = predicted.findIndex((p, i) => !used.has(i) && entityTripleMatches(p, ref));
    if (idx >= 0) {
      used.add(idx);
      tp++;
    }
  }
  return prf(tp, predicted.length, expected.length);
}

/** A candidate with no proposed mutation is treated as `noop`. */
export function scoreMutations(
  predicted: CognitiveExtractionResult["mutations"],
  expected: ExpectedMutation[],
): { accuracy: number | null; confusion: Record<string, number> } {
  const confusion: Record<string, number> = {};
  if (!expected.length) return { accuracy: null, confusion };
  const byTarget = new Map(predicted.map((m) => [m.targetClaimId, m.action]));
  let correct = 0;
  for (const exp of expected) {
    const got = byTarget.get(exp.targetClaimId) ?? "noop";
    const key = `${exp.action}->${got}`;
    confusion[key] = (confusion[key] ?? 0) + 1;
    if (got === exp.action) correct++;
  }
  return { accuracy: correct / expected.length, confusion };
}

export function scoreEvidence(
  result: CognitiveExtractionResult,
  rawText: string,
): { validity: number | null; claimsCovered: number | null } {
  const validity = result.evidence.length
    ? result.evidence.filter((e) => e.excerpt && rawText.includes(e.excerpt)).length /
      result.evidence.length
    : null;
  const covered = new Set(
    result.evidence
      .filter((e) => e.excerpt && rawText.includes(e.excerpt))
      .map((e) => e.claimTempId),
  );
  const claimsCovered = result.claims.length
    ? result.claims.filter((c) => covered.has(c.tempId)).length / result.claims.length
    : null;
  return { validity, claimsCovered };
}

/** Share of cross-references inside the result that point at something that exists. */
export function scoreReferentialIntegrity(
  result: CognitiveExtractionResult,
  candidateIds: string[],
): number | null {
  const tempIds = new Set(result.claims.map((c) => c.tempId));
  const candidates = new Set(candidateIds);
  const refs: boolean[] = [
    ...result.evidence.map((e) => tempIds.has(e.claimTempId)),
    ...result.entityRelationships.map((e) => tempIds.has(e.sourceTempId)),
    ...result.memoryRelationships.flatMap((r) => [
      tempIds.has(r.sourceTempId),
      tempIds.has(r.targetIdOrTempId) || candidates.has(r.targetIdOrTempId),
    ]),
    ...result.mutations.map((m) => candidates.has(m.targetClaimId)),
  ];
  if (!refs.length) return null;
  return refs.filter(Boolean).length / refs.length;
}

export function scoreEpisode(params: {
  episode: BenchEpisode;
  result: CognitiveExtractionResult;
  predClaimEmbeddings: number[][];
  refClaimEmbeddings: number[][];
  simThreshold: number;
}): EpisodeMetrics {
  const { episode, result, simThreshold } = params;
  const { expected } = episode;

  const claimMatches = matchClaims(
    params.predClaimEmbeddings,
    params.refClaimEmbeddings,
    simThreshold,
  );
  const claims = prf(claimMatches.length, result.claims.length, expected.claims.length);

  const kindAccuracy = claimMatches.length
    ? claimMatches.filter(
        (m) =>
          result.claims[m.predIndex]?.kind.toLowerCase() ===
          expected.claims[m.refIndex]?.kind.toLowerCase(),
      ).length / claimMatches.length
    : null;

  const entities = expected.entities
    ? scoreEntities(result.entityRelationships, expected.entities)
    : null;

  const mutations = scoreMutations(result.mutations, expected.mutations);
  const evidence = scoreEvidence(result, episode.rawText);

  return {
    claims,
    claimMatches,
    kindAccuracy,
    entities,
    mutationAccuracy: mutations.accuracy,
    mutationConfusion: mutations.confusion,
    evidenceValidity: evidence.validity,
    claimsWithValidEvidence: evidence.claimsCovered,
    referentialIntegrity: scoreReferentialIntegrity(
      result,
      episode.candidateClaims.map((c) => c.id),
    ),
    hallucinatedClaims: expected.claims.length === 0 ? result.claims.length : null,
  };
}

export function mean(values: number[]): number | null {
  return values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;
}

export function std(values: number[]): number | null {
  const m = mean(values);
  if (m === null || values.length < 2) return values.length ? 0 : null;
  return Math.sqrt(values.reduce((a, b) => a + (b - m) ** 2, 0) / (values.length - 1));
}

export function percentile(values: number[], p: number): number | null {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const idx = Math.min(sorted.length - 1, Math.max(0, Math.ceil((p / 100) * sorted.length) - 1));
  return sorted[idx] ?? null;
}
