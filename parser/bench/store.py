"""
Results persistence: crash-safe checkpointing, resume, and the final results JSON.

While a run is in progress every finished record is appended to
`<results>.partial.jsonl`, so a killed or quota-stopped run loses nothing. The
final `<results>.json` is written once at the end and the checkpoint removed.

Results file layout (schema_version 2, shared by every suite):

    {
      "schema_version": 2,
      "kind": "document-parser" | "claim-extractor",
      "label", "timestamp", "git_sha", "evals_sha",   # provenance
      "config": {...},                                # how the run was invoked
      "catalog": [MetricSpec, ...],                   # what the metrics mean
      "summary": {...},                               # aggregated metrics
      "results": [record, ...]                        # one record per job
    }
"""

import json
from collections.abc import Callable, Hashable
from pathlib import Path

SCHEMA_VERSION = 2
CHECKPOINT_SUFFIX = ".partial.jsonl"

Record = dict
KeyFn = Callable[[Record], Hashable]


def checkpoint_path(out_path: Path) -> Path:
    return out_path.with_name(out_path.stem + CHECKPOINT_SUFFIX)


def final_path(resume_from: Path) -> Path:
    """Results path that a checkpoint (or results file) belongs to."""
    if resume_from.name.endswith(CHECKPOINT_SUFFIX):
        return resume_from.with_name(resume_from.name.removesuffix(CHECKPOINT_SUFFIX) + ".json")
    return resume_from


def read_records(path: Path) -> list[Record]:
    """Records from a `.partial.jsonl` checkpoint or a results JSON."""
    text = path.read_text(encoding="utf-8")
    if path.name.endswith(".jsonl"):
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    return json.loads(text)["results"]


def load_run(path: Path) -> dict:
    """Loads a results file, remembering where it came from (`_path`)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    data["_path"] = str(path.resolve())
    return data


class ResultStore:
    """
    Accumulates records for one run.

    `key` identifies a unit of work (e.g. doc_id + tier). On resume, records that
    `keep` accepts are carried over and their keys reported by `is_done`; the rest
    (errors, failed VLM pages) are dropped so they run again.
    """

    def __init__(
        self,
        out_path: Path,
        key: KeyFn,
        resume_from: Path | None = None,
        keep: Callable[[Record], bool] = lambda r: True,
    ):
        self.out_path = out_path
        self.checkpoint = checkpoint_path(out_path)
        self._key = key
        self.records: list[Record] = (
            [r for r in read_records(resume_from) if keep(r)] if resume_from else []
        )
        self._done = {key(r) for r in self.records}
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # Rewrite the checkpoint so it holds exactly the records carried forward.
        with self.checkpoint.open("w", encoding="utf-8") as fh:
            fh.writelines(json.dumps(r) + "\n" for r in self.records)

    def is_done(self, record_key: Hashable) -> bool:
        return record_key in self._done

    def add(self, record: Record) -> None:
        self.records.append(record)
        self._done.add(self._key(record))
        with self.checkpoint.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")

    def finalize(self, payload: dict, sort_key: KeyFn | None = None) -> Path:
        """Writes the results JSON (payload + records) and removes the checkpoint."""
        records = sorted(self.records, key=sort_key) if sort_key else self.records
        body = {"schema_version": SCHEMA_VERSION, **payload, "results": records}
        self.out_path.write_text(json.dumps(body, indent=2), encoding="utf-8")
        self.checkpoint.unlink(missing_ok=True)
        return self.out_path
