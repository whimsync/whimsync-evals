/**
 * Executes one episode: the single structured extraction call (code under test),
 * embedding of predicted/expected claims for matching, then scoring.
 *
 * The extractor and embedder are injected (`Extractor`, `Embedder`), so tests and
 * alternative models can swap them without touching the runner.
 */
import type { CognitiveExtractionResult } from "@whimsync/core";
import { errorMessage, type RateLimiter, withRetry } from "./gemini";
import { type BenchEpisode, type EpisodeMetrics, scoreEpisode } from "./metrics";

export interface EpisodeRun {
  episodeId: string;
  run: number;
  latencyMs: number;
  /** Extraction attempts including retries of transient (429/503) errors. */
  attempts?: number;
  error?: string;
  result?: CognitiveExtractionResult;
  metrics?: EpisodeMetrics;
}

export type Extractor = (episode: BenchEpisode) => Promise<CognitiveExtractionResult>;
export type Embedder = (texts: string[]) => Promise<number[][]>;

export interface RunnerOptions {
  extract: Extractor;
  embed: Embedder;
  simThreshold: number;
  maxRetries: number;
  /** Paces extraction calls (the LLM model's quota). */
  limiter?: RateLimiter;
  log?: (message: string) => void;
}

const MAX_ERROR_CHARS = 300;

/** Memoizes embeddings so repeated claims (and repeated runs) cost one call each. */
export function cachedEmbedder(embed: Embedder): Embedder {
  const cache = new Map<string, number[]>();
  return async (texts) => {
    const missing = [...new Set(texts.filter((t) => !cache.has(t)))];
    if (missing.length) {
      const vectors = await embed(missing);
      missing.forEach((t, i) => {
        cache.set(t, vectors[i] as number[]);
      });
    }
    return texts.map((t) => cache.get(t) as number[]);
  };
}

export function createRunner(options: RunnerOptions) {
  const embed = cachedEmbedder((texts) =>
    withRetry(() => options.embed(texts), { maxRetries: options.maxRetries }).then((r) => r.value),
  );

  return async function runEpisode(episode: BenchEpisode, run: number): Promise<EpisodeRun> {
    const start = performance.now();
    try {
      const {
        value: result,
        attempts,
        latencyMs,
      } = await withRetry(() => options.extract(episode), {
        limiter: options.limiter,
        maxRetries: options.maxRetries,
        onRetry: ({ attempt, waitMs }) =>
          options.log?.(
            `${episode.id}: transient error, retrying in ${(waitMs / 1000).toFixed(1)}s ` +
              `(${attempt}/${options.maxRetries})`,
          ),
      });
      const [predClaimEmbeddings, refClaimEmbeddings] = await Promise.all([
        embed(result.claims.map((c) => c.content)),
        embed(episode.expected.claims.map((c) => c.content)),
      ]);
      return {
        episodeId: episode.id,
        run,
        latencyMs,
        attempts,
        result,
        metrics: scoreEpisode({
          episode,
          result,
          predClaimEmbeddings,
          refClaimEmbeddings,
          simThreshold: options.simThreshold,
        }),
      };
    } catch (err) {
      return {
        episodeId: episode.id,
        run,
        latencyMs: performance.now() - start,
        error: errorMessage(err).slice(0, MAX_ERROR_CHARS),
      };
    }
  };
}

/** Key identifying one unit of work, used for checkpoint/resume. */
export const runKey = (r: { episodeId: string; run: number }) => `${r.episodeId}#${r.run}`;
