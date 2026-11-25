/**
 * Results persistence, mirroring parser/bench/store.py: each finished record is
 * appended to `<results>.partial.jsonl` (crash/quota-safe), `--resume` carries
 * successful records over, and the final JSON (schema_version 2) is written once.
 */
import {
  appendFileSync,
  existsSync,
  mkdirSync,
  readFileSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { dirname } from "node:path";

export const SCHEMA_VERSION = 2;
const CHECKPOINT_SUFFIX = ".partial.jsonl";

export const checkpointPath = (outPath: string) =>
  outPath.replace(/\.json$/, "") + CHECKPOINT_SUFFIX;

export const finalPath = (resumeFrom: string) =>
  resumeFrom.endsWith(CHECKPOINT_SUFFIX)
    ? `${resumeFrom.slice(0, -CHECKPOINT_SUFFIX.length)}.json`
    : resumeFrom;

export function readRecords<T>(path: string): T[] {
  const text = readFileSync(path, "utf-8");
  if (path.endsWith(".jsonl")) {
    return text
      .split("\n")
      .filter((l) => l.trim())
      .map((l) => JSON.parse(l) as T);
  }
  return (JSON.parse(text) as { results: T[] }).results;
}

export class ResultStore<T> {
  readonly records: T[];
  readonly checkpoint: string;
  private readonly done: Set<string>;

  constructor(
    readonly outPath: string,
    private readonly key: (record: T) => string,
    options: { resumeFrom?: string; keep?: (record: T) => boolean } = {},
  ) {
    const keep = options.keep ?? (() => true);
    this.records = options.resumeFrom ? readRecords<T>(options.resumeFrom).filter(keep) : [];
    this.done = new Set(this.records.map(key));
    this.checkpoint = checkpointPath(outPath);
    mkdirSync(dirname(outPath), { recursive: true });
    // Rewrite the checkpoint so it holds exactly the records carried forward.
    writeFileSync(this.checkpoint, this.records.map((r) => `${JSON.stringify(r)}\n`).join(""));
  }

  isDone(key: string): boolean {
    return this.done.has(key);
  }

  add(record: T): void {
    this.records.push(record);
    this.done.add(this.key(record));
    appendFileSync(this.checkpoint, `${JSON.stringify(record)}\n`);
  }

  finalize(payload: Record<string, unknown>, records: T[] = this.records): string {
    writeFileSync(
      this.outPath,
      JSON.stringify({ schema_version: SCHEMA_VERSION, ...payload, results: records }, null, 2),
    );
    if (existsSync(this.checkpoint)) unlinkSync(this.checkpoint);
    return this.outPath;
  }
}

export function gitSha(cwd: string): string | null {
  const proc = Bun.spawnSync(["git", "rev-parse", "--short", "HEAD"], { cwd });
  return proc.exitCode === 0 ? proc.stdout.toString().trim() : null;
}

export const utcTimestamp = () => `${new Date().toISOString().replace(/[-:]/g, "").slice(0, 15)}Z`;
