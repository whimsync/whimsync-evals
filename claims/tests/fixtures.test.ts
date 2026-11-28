import { describe, expect, test } from "bun:test";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { loadAllEpisodes, selectEpisodes, validateEpisode } from "../src/fixtures";

const FIXTURES = join(import.meta.dir, "..", "fixtures", "episodes");
const CANDIDATE = "11111111-1111-4111-8111-000000000001";

const valid = {
  id: "e1",
  tags: ["fact"],
  rawText: "I moved to Berlin.",
  candidateClaims: [{ id: CANDIDATE, content: "User lives in Pune.", kind: "fact" }],
  expected: {
    claims: [{ content: "User moved to Berlin.", kind: "fact" }],
    mutations: [{ targetClaimId: CANDIDATE, action: "update" }],
  },
};

describe("fixtures", () => {
  test("every committed fixture is valid and ids are unique", () => {
    const all = loadAllEpisodes(FIXTURES);
    expect(all.length).toBeGreaterThanOrEqual(21);
    expect(new Set(all.map((e) => e.id)).size).toBe(all.length);
  });

  test("a valid episode has no errors", () => {
    expect(validateEpisode(valid)).toEqual([]);
  });

  test("catches bad kinds, actions and dangling mutation targets", () => {
    const bad = {
      ...valid,
      expected: {
        claims: [{ content: "x", kind: "opinion" }],
        mutations: [{ targetClaimId: "nope", action: "merge" }],
      },
    };
    const errors = validateEpisode(bad).join("\n");
    expect(errors).toContain("kind must be one of");
    expect(errors).toContain("not a candidate claim id");
    expect(errors).toContain("action must be one of");
  });

  test("reports duplicate ids across files with the file name", () => {
    const dir = mkdtempSync(join(tmpdir(), "fixtures-"));
    writeFileSync(join(dir, "a.json"), JSON.stringify([valid]));
    writeFileSync(join(dir, "b.json"), JSON.stringify([valid]));
    expect(() => loadAllEpisodes(dir)).toThrow(/b\.json \[e1\]: duplicate id/);
  });

  test("selects by id and tag, rejecting unknown ids", () => {
    const all = loadAllEpisodes(FIXTURES);
    expect(selectEpisodes(all, { tags: ["noise"] }).every((e) => e.tags.includes("noise"))).toBe(
      true,
    );
    expect(selectEpisodes(all, { ids: ["fact_job"] }).map((e) => e.id)).toEqual(["fact_job"]);
    expect(() => selectEpisodes(all, { ids: ["missing"] })).toThrow("Unknown episode id");
  });
});
