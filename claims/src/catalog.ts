/**
 * Metric catalog for the claim extractor suite.
 *
 * Describes every summary metric (direction, unit, tolerance). It is embedded in
 * each results file, so the shared Python `compare` and `report` tools interpret
 * the metrics correctly without hard-coding them. Field names match the Python
 * `MetricSpec` (parser/bench/catalog.py).
 */

export interface MetricSpec {
  key: string;
  label: string;
  /** Short console column name. */
  short?: string;
  /** Direction of improvement; "none" = informational. */
  better: "higher" | "lower" | "none";
  unit: "ratio" | "ms" | "s" | "count";
  /** "absolute" uses the compare threshold; "relative" a ±25% band (timing). */
  tolerance: "absolute" | "relative";
  /** Shown in the console summary. */
  summary?: boolean;
  description?: string;
}

const ratio = (key: string, label: string, description: string): MetricSpec => ({
  key,
  label,
  better: "higher",
  unit: "ratio",
  tolerance: "absolute",
  summary: true,
  description,
});

export const CATALOG: MetricSpec[] = [
  ratio("claim_precision", "Claim precision", "Predicted claims matching an expected one"),
  ratio("claim_recall", "Claim recall", "Expected claims that were extracted"),
  ratio("claim_f1", "Claim F1", "Micro-averaged over all episodes"),
  ratio("kind_accuracy", "Kind accuracy", "Matched claims with the right kind"),
  ratio("entity_precision", "Entity precision", "Predicted triples matching an expected one"),
  ratio("entity_recall", "Entity recall", "Expected triples that were extracted"),
  ratio("entity_f1", "Entity F1", "Micro-averaged entity triple F1"),
  ratio("mutation_accuracy", "Mutation accuracy", "Correct update/delete/noop per candidate"),
  ratio("evidence_validity", "Evidence validity", "Evidence excerpts found verbatim in the text"),
  ratio("claims_with_valid_evidence", "Claims with evidence", "Claims backed by a valid excerpt"),
  ratio("referential_integrity", "Referential integrity", "Cross-references that resolve"),
  ratio("noise_clean_rate", "Noise clean rate", "Small-talk episodes with zero claims"),
  {
    key: "noise_claims_per_episode",
    label: "Noise claims / episode",
    better: "lower",
    unit: "count",
    tolerance: "absolute",
    summary: true,
    description: "Hallucinated claims on small-talk episodes",
  },
  {
    key: "failure_rate",
    label: "Failure rate",
    better: "lower",
    unit: "ratio",
    tolerance: "absolute",
    summary: true,
    description: "Extractions that errored",
  },
  ...(["p50", "p95"] as const).map(
    (p): MetricSpec => ({
      key: `latency_${p}_ms`,
      label: `Latency ${p}`,
      better: "lower",
      unit: "ms",
      tolerance: "relative",
      summary: true,
      description: "Successful extraction call (excludes limiter waits and retries)",
    }),
  ),
];

/** Spec for the dynamic per-tag slice `tag:<tag>:claim_f1`. */
export function tagSpec(tag: string): MetricSpec {
  return {
    ...ratio(`tag:${tag}:claim_f1`, `Claim F1 [${tag}]`, `Claim F1 on '${tag}' episodes`),
    summary: false,
  };
}

export const TAG_KEY = /^tag:(.+):claim_f1$/;

/** Catalog covering the given summary keys: static specs plus tag slices, in display order. */
export function catalogFor(keys: Iterable<string>): MetricSpec[] {
  const tags = [...keys].map((k) => TAG_KEY.exec(k)?.[1]).filter((t): t is string => !!t);
  return [...CATALOG, ...[...new Set(tags)].sort().map(tagSpec)];
}

export function formatValue(spec: MetricSpec | undefined, value: number | null): string {
  if (value === null) return "-";
  return spec?.unit === "ms" ? value.toFixed(0) : value.toFixed(3);
}
