/**
 * Aggregation: per-episode metrics -> one summary per run -> mean ± std across runs.
 * Claim/entity P/R/F1 are micro-averaged (pooled counts); ratios are macro means.
 */
import { catalogFor, formatValue } from "./catalog";
import { type BenchEpisode, type EpisodeMetrics, mean, percentile, prf, std } from "./metrics";
import type { EpisodeRun } from "./runner";

export type RunSummary = Record<string, number | null>;
export type CombinedSummary = Record<string, { mean: number | null; std: number | null }>;

const nonNull = (values: (number | null | undefined)[]): number[] =>
  values.filter((v): v is number => typeof v === "number");

function pooledPrf(items: { tp: number; nPred: number; nRef: number }[]) {
  return prf(
    items.reduce((a, x) => a + x.tp, 0),
    items.reduce((a, x) => a + x.nPred, 0),
    items.reduce((a, x) => a + x.nRef, 0),
  );
}

export function summarizeRun(rows: EpisodeRun[], episodes: Map<string, BenchEpisode>): RunSummary {
  const ok = rows.filter((r) => r.metrics);
  if (!ok.length) {
    // Nothing succeeded: report only the failure rate rather than vacuous 1.0 scores.
    return { failure_rate: rows.length ? 1 : null };
  }
  const m = ok.map((r) => r.metrics as EpisodeMetrics);
  const claim = pooledPrf(m.map((x) => x.claims));
  const ents = m.map((x) => x.entities).filter((e) => e !== null);
  const entity = pooledPrf(ents);
  const noise = m.map((x) => x.hallucinatedClaims).filter((h) => h !== null);
  const latencies = ok.map((r) => r.latencyMs);

  const summary: RunSummary = {
    claim_precision: claim.precision,
    claim_recall: claim.recall,
    claim_f1: claim.f1,
    kind_accuracy: mean(nonNull(m.map((x) => x.kindAccuracy))),
    entity_precision: ents.length ? entity.precision : null,
    entity_recall: ents.length ? entity.recall : null,
    entity_f1: ents.length ? entity.f1 : null,
    mutation_accuracy: mean(nonNull(m.map((x) => x.mutationAccuracy))),
    evidence_validity: mean(nonNull(m.map((x) => x.evidenceValidity))),
    claims_with_valid_evidence: mean(nonNull(m.map((x) => x.claimsWithValidEvidence))),
    referential_integrity: mean(nonNull(m.map((x) => x.referentialIntegrity))),
    noise_clean_rate: noise.length ? noise.filter((h) => h === 0).length / noise.length : null,
    noise_claims_per_episode: mean(noise),
    failure_rate: (rows.length - ok.length) / rows.length,
    latency_p50_ms: percentile(latencies, 50),
    latency_p95_ms: percentile(latencies, 95),
  };

  // Per-tag claim F1 for slicing regressions (e.g. tag:mutation:claim_f1).
  const tags = [...new Set(ok.flatMap((r) => episodes.get(r.episodeId)?.tags ?? []))].sort();
  for (const tag of tags) {
    const tagged = ok.filter((r) => episodes.get(r.episodeId)?.tags.includes(tag));
    summary[`tag:${tag}:claim_f1`] = pooledPrf(
      tagged.map((r) => (r.metrics as EpisodeMetrics).claims),
    ).f1;
  }
  return summary;
}

export function combineRuns(perRun: RunSummary[]): CombinedSummary {
  const keys = [...new Set(perRun.flatMap((s) => Object.keys(s)))];
  return Object.fromEntries(
    keys.map((k) => {
      const vals = nonNull(perRun.map((s) => s[k]));
      return [k, { mean: mean(vals), std: std(vals) }];
    }),
  );
}

export function mutationConfusion(rows: EpisodeRun[]): Record<string, number> {
  const out: Record<string, number> = {};
  for (const r of rows) {
    for (const [k, v] of Object.entries(r.metrics?.mutationConfusion ?? {})) {
      out[k] = (out[k] ?? 0) + v;
    }
  }
  return out;
}

export function formatSummary(combined: CombinedSummary, rows: EpisodeRun[]): string {
  const specs = new Map(catalogFor(Object.keys(combined)).map((s) => [s.key, s]));
  const keys = [...specs.keys()].filter((k) => k in combined);
  const width = Math.max(6, ...keys.map((k) => k.length));
  const lines = [
    `${"metric".padEnd(width)}  mean      ± std`,
    `${"-".repeat(width)}  --------  -------`,
  ];
  for (const k of keys) {
    const { mean: m, std: s } = combined[k] as { mean: number | null; std: number | null };
    const spec = specs.get(k);
    lines.push(
      `${k.padEnd(width)}  ${formatValue(spec, m).padEnd(8)}  ${s === null ? "" : `±${formatValue(spec, s)}`}`,
    );
  }
  const confusion = Object.entries(mutationConfusion(rows)).sort();
  if (confusion.length) {
    lines.push("", "Mutation confusion (expected->predicted, all runs):");
    for (const [k, v] of confusion) lines.push(`  ${k.padEnd(16)} ${v}`);
  }
  const errors = rows.filter((r) => r.error);
  if (errors.length) {
    lines.push("", `${errors.length} extraction(s) failed:`);
    for (const e of errors) lines.push(`  ${e.episodeId} (run ${e.run}): ${e.error}`);
  }
  return lines.join("\n");
}
