/**
 * Request pacing and retry policy for Gemini calls made by the benchmark.
 *
 * - `RateLimiter` spaces request starts to stay under a requests-per-minute cap.
 * - `classifyError` sorts failures into fatal (bad key/model: abort the run),
 *   transient (429/503: retry) and other (record and move on).
 * - `withRetry` retries transient errors with backoff that honours Gemini's
 *   `retryDelay` hint, and fails fast when the hint means the daily quota is gone.
 */

export type ErrorClass = "fatal" | "transient" | "other";

const FATAL_RE =
  /API key not valid|API_KEY_INVALID|PERMISSION_DENIED|UNAUTHENTICATED|is not found for API version|environment variable is missing/i;
const TRANSIENT_RE = /\b(429|503)\b|RESOURCE_EXHAUSTED|UNAVAILABLE|overloaded|rate limit/i;

/** Per-minute limits ask for <60s; a longer wait means the (daily) quota is exhausted. */
export const MAX_RETRY_WAIT_MS = 120_000;

export function classifyError(message: string): ErrorClass {
  if (FATAL_RE.test(message)) return "fatal";
  if (TRANSIENT_RE.test(message)) return "transient";
  return "other";
}

/** Backoff for attempt n: max(2^n * 2s, server retryDelay hint), plus up to 25% jitter. */
export function retryDelayMs(message: string, attempt: number, jitter = Math.random()): number {
  const hint = message.match(/retryDelay"?\s*:\s*"?(\d+(?:\.\d+)?)s/i);
  const base = Math.max(2 ** attempt * 2000, hint ? Number(hint[1]) * 1000 : 0);
  return base * (1 + jitter * 0.25);
}

export function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

/** Spaces request starts at least 60/rpm seconds apart (rpm <= 0 disables). */
export class RateLimiter {
  private nextSlot = 0;
  private readonly intervalMs: number;

  constructor(requestsPerMinute: number) {
    this.intervalMs = requestsPerMinute > 0 ? 60_000 / requestsPerMinute : 0;
  }

  /** Waits for the next slot; returns the milliseconds spent waiting. */
  async acquire(): Promise<number> {
    if (!this.intervalMs) return 0;
    const now = Date.now();
    const slot = Math.max(now, this.nextSlot);
    this.nextSlot = slot + this.intervalMs;
    if (slot > now) await Bun.sleep(slot - now);
    return slot - now;
  }
}

export interface RetryOptions {
  limiter?: RateLimiter;
  maxRetries: number;
  onRetry?: (info: { attempt: number; waitMs: number; message: string }) => void;
  /** Injected for tests. */
  sleep?: (ms: number) => Promise<void>;
}

export interface RetryResult<T> {
  value: T;
  attempts: number;
  /** Duration of the successful attempt only (excludes limiter waits and backoff). */
  latencyMs: number;
}

export async function withRetry<T>(
  fn: () => Promise<T>,
  options: RetryOptions,
): Promise<RetryResult<T>> {
  const sleep = options.sleep ?? ((ms: number) => Bun.sleep(ms));
  for (let attempt = 0; ; attempt++) {
    await options.limiter?.acquire();
    const start = performance.now();
    try {
      const value = await fn();
      return { value, attempts: attempt + 1, latencyMs: performance.now() - start };
    } catch (err) {
      const message = errorMessage(err);
      if (attempt >= options.maxRetries || classifyError(message) !== "transient") throw err;
      const waitMs = retryDelayMs(message, attempt);
      if (waitMs > MAX_RETRY_WAIT_MS) {
        throw new Error(
          `quota exhausted: server asked to wait ${(waitMs / 1000).toFixed(0)}s ` +
            `(cap ${MAX_RETRY_WAIT_MS / 1000}s). ${message}`,
        );
      }
      options.onRetry?.({ attempt: attempt + 1, waitMs, message });
      await sleep(waitMs);
    }
  }
}

/** Runs `fn` over items with at most `limit` in flight. */
export async function pool<T>(
  items: T[],
  limit: number,
  fn: (item: T) => Promise<void>,
): Promise<void> {
  let next = 0;
  const workers = Array.from({ length: Math.min(Math.max(1, limit), items.length) }, async () => {
    while (next < items.length) {
      const item = items[next++] as T;
      await fn(item);
    }
  });
  await Promise.all(workers);
}
