/**
 * Claim extractor benchmark CLI.
 *
 *   bun run claims -- --runs 3 --rpm 12 --label baseline
 *
 * Pipeline: fixtures (validated) -> jobs (episode x run) -> runner (extract with
 * retry, embed, score) -> store (checkpoint + results JSON) -> summary.
 * The code under test is whimsync's `executeStructuredExtraction` (LLM_MODEL);
 * claim matching uses `generateTextEmbeddingsBatch` (EMBEDDING_MODEL). No
 * database or Redis is touched. Compare/report: `bun run claims:compare|report`.
 */
import { join, resolve } from "node:path";
import { parseArgs } from "node:util";
import { generateTextEmbeddingsBatch } from "@whimsync/worker/services/embeddingService";
import { executeStructuredExtraction } from "@whimsync/worker/services/llmService";
import { catalogFor } from "./src/catalog";
import { loadAllEpisodes, selectEpisodes } from "./src/fixtures";
import { classifyError, pool, RateLimiter } from "./src/gemini";
import type { BenchEpisode } from "./src/metrics";
import { finalPath, gitSha, ResultStore, utcTimestamp } from "./src/results";
import { createRunner, type EpisodeRun, runKey } from "./src/runner";
import { combineRuns, formatSummary, summarizeRun } from "./src/summary";

const SUITE_DIR = import.meta.dir;
/** The whimsync checkout whose worker code is under test (see tsconfig.json paths). */
const WHIMSYNC_ROOT = resolve(SUITE_DIR, "../../whimsync");
const KIND = "claim-extractor";
/** Fixed clock for candidate claims so prompts are identical across runs. */
const CANDIDATE_RECORDED_AT = new Date("2026-01-01T00:00:00Z");

const log = (message: string) => console.error(`[bench] ${message}`);
const csv = (value: string) =>
  value
    .split(",")
    .map((v) => v.trim())
    .filter(Boolean);

function parseConfig() {
  const { values } = parseArgs({
    options: {
      runs: { type: "string", default: "1" },
      cases: { type: "string", default: "" },
      tags: { type: "string", default: "" },
      "sim-threshold": { type: "string", default: "0.80" },
      concurrency: { type: "string", default: "2" },
      rpm: { type: "string", default: "0" },
      retries: { type: "string", default: "3" },
      label: { type: "string", default: "run" },
      out: { type: "string" },
      fixtures: { type: "string", default: join(SUITE_DIR, "fixtures", "episodes") },
      resume: { type: "string" },
      "no-output": { type: "boolean", default: false },
    },
  });
  return {
    runs: Math.max(1, Number.parseInt(values.runs, 10)),
    ids: csv(values.cases),
    tags: csv(values.tags),
    simThreshold: Number.parseFloat(values["sim-threshold"]),
    concurrency: Math.max(1, Number.parseInt(values.concurrency, 10)),
    rpm: Math.max(0, Number.parseFloat(values.rpm)),
    retries: Math.max(0, Number.parseInt(values.retries, 10)),
    label: values.label,
    out: values.out,
    fixtures: resolve(values.fixtures),
    resume: values.resume ? resolve(values.resume) : undefined,
    storeOutput: !values["no-output"],
  };
}

function missingEnv(): string | null {
  for (const key of ["GEMINI_API_KEY", "LLM_MODEL", "EMBEDDING_MODEL"]) {
    if (!process.env[key]) return key;
  }
  return null;
}

async function main(): Promise<number> {
  const config = parseConfig();
  const missing = missingEnv();
  if (missing) {
    log(`${missing} must be set (copy .env.example to .env in whimsync-evals).`);
    return 1;
  }

  const episodes = selectEpisodes(loadAllEpisodes(config.fixtures), {
    ids: config.ids,
    tags: config.tags,
  });
  if (!episodes.length) {
    log("No episodes matched the filters.");
    return 1;
  }
  const byId = new Map(episodes.map((e) => [e.id, e]));

  const timestamp = utcTimestamp();
  const outPath = resolve(
    config.out ??
      (config.resume
        ? finalPath(config.resume)
        : join(SUITE_DIR, "results", `${timestamp}-${config.label}.json`)),
  );
  const store = new ResultStore<EpisodeRun>(outPath, runKey, {
    resumeFrom: config.resume,
    keep: (r) => !r.error,
  });
  if (config.resume) log(`Resuming: ${store.records.length} results carried over`);
  log(`Checkpointing each result to ${store.checkpoint}`);

  const runEpisode = createRunner({
    extract: (episode: BenchEpisode) =>
      executeStructuredExtraction(
        episode.rawText,
        episode.candidateClaims.map((c) => ({ ...c, recordedAt: CANDIDATE_RECORDED_AT })),
      ),
    embed: generateTextEmbeddingsBatch,
    simThreshold: config.simThreshold,
    maxRetries: config.retries,
    limiter: new RateLimiter(config.rpm),
    log,
  });

  const jobs = Array.from({ length: config.runs }, (_, run) =>
    episodes.map((episode) => ({ episode, run })),
  )
    .flat()
    .filter((j) => !store.isDone(runKey({ episodeId: j.episode.id, run: j.run })));
  log(
    `model=${process.env.LLM_MODEL} episodes=${episodes.length} runs=${config.runs} ` +
      `jobs=${jobs.length} threshold=${config.simThreshold} rpm=${config.rpm || "unlimited"}`,
  );

  // Preflight: fail fast on credential/model errors instead of burning through every job.
  const [first, ...rest] = jobs;
  let done = 0;
  const record = (row: EpisodeRun) => {
    store.add(row);
    done++;
    const status = row.error ? "ERROR" : `claimF1=${row.metrics?.claims.f1.toFixed(2)}`;
    log(`(${done}/${jobs.length}) ${row.episodeId} run ${row.run} ${status}`);
  };
  if (first) {
    const preflight = await runEpisode(first.episode, first.run);
    if (preflight.error && classifyError(preflight.error) === "fatal") {
      log(`Aborting: ${preflight.error}`);
      return 1;
    }
    record(preflight);
  }
  await pool(rest, config.concurrency, async ({ episode, run }) => {
    record(await runEpisode(episode, run));
  });

  const rows = [...store.records].sort(
    (a, b) => a.run - b.run || a.episodeId.localeCompare(b.episodeId),
  );
  const perRun = Array.from({ length: config.runs }, (_, run) =>
    summarizeRun(
      rows.filter((r) => r.run === run),
      byId,
    ),
  );
  const summary = combineRuns(perRun);
  console.log(`\n${formatSummary(summary, rows)}`);

  const path = store.finalize(
    {
      kind: KIND,
      label: config.label,
      timestamp,
      gitSha: gitSha(WHIMSYNC_ROOT),
      evalsSha: gitSha(SUITE_DIR),
      model: process.env.LLM_MODEL,
      embeddingModel: process.env.EMBEDDING_MODEL,
      simThreshold: config.simThreshold,
      runs: config.runs,
      rpm: config.rpm || null,
      config,
      catalog: catalogFor(Object.keys(summary)),
      // Snapshot of inputs/expectations so reports stay correct if fixtures change later.
      episodes: Object.fromEntries(
        episodes.map((e) => [e.id, { tags: e.tags, rawText: e.rawText, expected: e.expected }]),
      ),
      summary,
      perRun,
    },
    rows.map((r) => (config.storeOutput ? r : { ...r, result: undefined })),
  );
  console.log(`\n[bench] Results written to ${path}`);
  return rows.some((r) => r.error) ? 1 : 0;
}

process.exit(await main());
