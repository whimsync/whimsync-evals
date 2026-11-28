import { describe, expect, test } from "bun:test";
import {
  classifyError,
  MAX_RETRY_WAIT_MS,
  RateLimiter,
  retryDelayMs,
  withRetry,
} from "../src/gemini";

const noSleep = async () => {};

describe("gemini retry policy", () => {
  test("classifies errors", () => {
    expect(classifyError("400 API key not valid")).toBe("fatal");
    expect(classifyError("429 RESOURCE_EXHAUSTED")).toBe("transient");
    expect(classifyError("503 UNAVAILABLE: overloaded")).toBe("transient");
    expect(classifyError("Zod validation failed")).toBe("other");
  });

  test("backoff honours the server hint, else grows exponentially", () => {
    expect(retryDelayMs(`429 {"retryDelay": "42s"}`, 0, 0)).toBe(42_000);
    expect(retryDelayMs("429", 0, 0)).toBe(2000);
    expect(retryDelayMs("429", 2, 0)).toBe(8000);
    expect(retryDelayMs("429", 0, 1)).toBe(2500);
  });

  test("retries transient errors and reports attempts", async () => {
    let calls = 0;
    const r = await withRetry(
      async () => {
        if (++calls < 3) throw new Error("503 UNAVAILABLE");
        return "ok";
      },
      { maxRetries: 3, sleep: noSleep },
    );
    expect(r.value).toBe("ok");
    expect(r.attempts).toBe(3);
  });

  test("does not retry other errors", async () => {
    let calls = 0;
    const failing = withRetry(
      async () => {
        calls++;
        throw new Error("schema mismatch");
      },
      { maxRetries: 3, sleep: noSleep },
    );
    await expect(failing).rejects.toThrow("schema mismatch");
    expect(calls).toBe(1);
  });

  test("fails fast when the quota is exhausted for the day", async () => {
    const hint = `429 RESOURCE_EXHAUSTED {"retryDelay": "${MAX_RETRY_WAIT_MS / 1000 + 1000}s"}`;
    const failing = withRetry(
      async () => {
        throw new Error(hint);
      },
      { maxRetries: 3, sleep: noSleep },
    );
    await expect(failing).rejects.toThrow("quota exhausted");
  });

  test("rate limiter spaces requests", async () => {
    const limiter = new RateLimiter(1200); // one slot per 50 ms
    const start = performance.now();
    await Promise.all([limiter.acquire(), limiter.acquire(), limiter.acquire()]);
    expect(performance.now() - start).toBeGreaterThanOrEqual(95);
    expect(await new RateLimiter(0).acquire()).toBe(0);
  });
});
