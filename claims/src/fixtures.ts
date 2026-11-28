/**
 * Episode fixtures: every `*.json` file in `fixtures/episodes/` holds an array of
 * episodes (files group episodes by category). Episodes are validated on load so
 * a typo fails fast with the file and episode id, not as a confusing score.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import type { BenchEpisode } from "./metrics";

export const CLAIM_KINDS = ["fact", "preference", "decision", "goal", "other"] as const;
export const MUTATION_ACTIONS = ["update", "delete", "noop"] as const;

const isString = (v: unknown): v is string => typeof v === "string" && v.trim().length > 0;
const isArray = Array.isArray;

/** Problems with one episode (empty = valid). */
export function validateEpisode(raw: unknown): string[] {
  const errors: string[] = [];
  const e = raw as Partial<BenchEpisode> & Record<string, unknown>;
  if (typeof raw !== "object" || raw === null) return ["episode must be an object"];
  if (!isString(e.id)) errors.push("id must be a non-empty string");
  if (!isArray(e.tags) || !e.tags.length || !e.tags.every(isString)) {
    errors.push("tags must be a non-empty string array");
  }
  if (!isString(e.rawText)) errors.push("rawText must be a non-empty string");

  const candidates = isArray(e.candidateClaims) ? e.candidateClaims : [];
  if (!isArray(e.candidateClaims)) errors.push("candidateClaims must be an array");
  const candidateIds = new Set<string>();
  candidates.forEach((c, i) => {
    if (!isString(c?.id) || !isString(c?.content) || !isString(c?.kind)) {
      errors.push(`candidateClaims[${i}] needs id, content and kind`);
    } else if (candidateIds.has(c.id)) {
      errors.push(`candidateClaims[${i}] duplicates id ${c.id}`);
    } else {
      candidateIds.add(c.id);
    }
  });

  const expected = e.expected;
  if (!expected || typeof expected !== "object") return [...errors, "expected is required"];
  if (!isArray(expected.claims)) errors.push("expected.claims must be an array");
  (expected.claims ?? []).forEach((c, i) => {
    if (!isString(c?.content)) errors.push(`expected.claims[${i}].content is required`);
    if (!(CLAIM_KINDS as readonly string[]).includes(c?.kind)) {
      errors.push(`expected.claims[${i}].kind must be one of ${CLAIM_KINDS.join("|")}`);
    }
  });
  if (expected.entities !== undefined) {
    if (!isArray(expected.entities)) errors.push("expected.entities must be an array");
    (expected.entities ?? []).forEach((t, i) => {
      if (![t?.subject, t?.predicate, t?.object].every(isString)) {
        errors.push(`expected.entities[${i}] needs subject, predicate and object`);
      }
    });
  }
  if (!isArray(expected.mutations)) errors.push("expected.mutations must be an array");
  (expected.mutations ?? []).forEach((m, i) => {
    if (!candidateIds.has(m?.targetClaimId)) {
      errors.push(`expected.mutations[${i}].targetClaimId is not a candidate claim id`);
    }
    if (!(MUTATION_ACTIONS as readonly string[]).includes(m?.action)) {
      errors.push(`expected.mutations[${i}].action must be one of ${MUTATION_ACTIONS.join("|")}`);
    }
  });
  return errors;
}

/** Loads and validates every episode under `path` (a directory or a single JSON file). */
export function loadAllEpisodes(path: string): BenchEpisode[] {
  const files = statSync(path).isDirectory()
    ? readdirSync(path)
        .filter((f) => f.endsWith(".json"))
        .sort()
        .map((f) => join(path, f))
    : [path];
  if (!files.length) throw new Error(`No fixture files (*.json) in ${path}`);

  const episodes: BenchEpisode[] = [];
  const seen = new Map<string, string>();
  const problems: string[] = [];
  for (const file of files) {
    const data = JSON.parse(readFileSync(file, "utf-8")) as unknown;
    if (!isArray(data)) {
      problems.push(`${file}: must contain a JSON array of episodes`);
      continue;
    }
    data.forEach((raw, i) => {
      const id = (raw as { id?: string })?.id ?? `#${i}`;
      const errors = validateEpisode(raw);
      if (seen.has(id)) errors.push(`duplicate id (also in ${seen.get(id)})`);
      seen.set(id, file);
      if (errors.length) problems.push(...errors.map((err) => `${file} [${id}]: ${err}`));
      else episodes.push(raw as BenchEpisode);
    });
  }
  if (problems.length) throw new Error(`Invalid fixtures:\n  ${problems.join("\n  ")}`);
  return episodes.sort((a, b) => a.id.localeCompare(b.id));
}

/** Filters by id list and/or tags (any match); unknown ids are an error. */
export function selectEpisodes(
  all: BenchEpisode[],
  filter: { ids?: string[]; tags?: string[] } = {},
): BenchEpisode[] {
  const ids = filter.ids ?? [];
  const tags = filter.tags ?? [];
  const unknown = ids.filter((id) => !all.some((e) => e.id === id));
  if (unknown.length) throw new Error(`Unknown episode id(s): ${unknown.join(", ")}`);
  return all.filter(
    (e) =>
      (!ids.length || ids.includes(e.id)) && (!tags.length || e.tags.some((t) => tags.includes(t))),
  );
}
