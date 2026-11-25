import json

from bench.store import ResultStore, checkpoint_path, final_path, load_run

key = lambda r: r["id"]  # noqa: E731


def test_records_are_checkpointed_then_finalized(tmp_path):
    out = tmp_path / "run.json"
    store = ResultStore(out, key)
    store.add({"id": 2})
    store.add({"id": 1})
    lines = checkpoint_path(out).read_text().splitlines()
    assert [json.loads(x)["id"] for x in lines] == [2, 1]

    store.finalize({"kind": "k"}, sort_key=key)
    data = load_run(out)
    assert data["schema_version"] == 2
    assert [r["id"] for r in data["results"]] == [1, 2]
    assert not checkpoint_path(out).exists()


def test_resume_keeps_only_accepted_records(tmp_path):
    partial = tmp_path / "killed.partial.jsonl"
    partial.write_text(json.dumps({"id": 1}) + "\n" + json.dumps({"id": 2, "error": "x"}) + "\n")
    out = final_path(partial)
    assert out.name == "killed.json"
    store = ResultStore(out, key, resume_from=partial, keep=lambda r: not r.get("error"))
    assert store.is_done(1) and not store.is_done(2)
    assert [r["id"] for r in store.records] == [1]
    # the checkpoint is rewritten to hold exactly the carried-over records
    assert len(partial.read_text().splitlines()) == 1
