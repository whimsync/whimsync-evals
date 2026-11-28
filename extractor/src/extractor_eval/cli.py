"""
extractor-eval command line.

    extractor-eval image build --extractor-root ../whimsync
    extractor-eval run --case scanned-footer --tiers balanced,max
    extractor-eval runs list | rename | group | comment | delete ...
    extractor-eval compare RUN_A RUN_B
    extractor-eval ui
"""

import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Annotated

import pymupdf
import typer

from . import DEFAULT_EXTRACTOR_ROOT, PROJECT_DIR, TIERS, docker_runner, runner, store
from . import compare as compare_mod
from . import dataset as ds
from .config import gemini_api_key, read_json
from .judge import JUDGE_MODEL

app = typer.Typer(
    no_args_is_help=True, add_completion=False, pretty_exceptions_show_locals=False, help=__doc__
)
image_app = typer.Typer(no_args_is_help=True, help="Build and list extractor evaluation images.")
dataset_app = typer.Typer(no_args_is_help=True, help="Inspect and grow dataset.json.")
runs_app = typer.Typer(
    no_args_is_help=True, help="Manage results: list, rename, group, comment, delete."
)
app.add_typer(image_app, name="image")
app.add_typer(dataset_app, name="dataset")
app.add_typer(runs_app, name="runs")


def _csv(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def _fail(message: str) -> None:
    typer.secho(message, fg="red", err=True)
    raise typer.Exit(1)


def _resolve(ref: str) -> str:
    try:
        return store.resolve(ref)
    except store.RunNotFound as e:
        _fail(str(e.args[0]))
        raise


# ---- image ---------------------------------------------------------------------------


@image_app.command("build")
def image_build(
    extractor_root: Annotated[
        Path, typer.Option(help="whimsync checkout")
    ] = DEFAULT_EXTRACTOR_ROOT,
    tag: Annotated[
        str | None, typer.Option(help="Override the tag (default: commit[-dirty-hash])")
    ] = None,
) -> None:
    """Build whimsync-extractor-eval:<commit> from a whimsync checkout."""
    try:
        image = docker_runner.build_image(extractor_root, tag)
    except docker_runner.DockerError as e:
        _fail(str(e))
    typer.echo(
        f"built {image.ref}  ({image.digest[:19]}){'  [uncommitted changes]' if image.dirty else ''}"
    )


@image_app.command("list")
def image_list() -> None:
    for image in sorted(docker_runner.list_images(), key=lambda i: i.built_at, reverse=True):
        typer.echo(
            f"{image.tag:<32} {image.built_at}  {image.digest[:19]}{'  dirty' if image.dirty else ''}"
        )


# ---- dataset ---------------------------------------------------------------------------


@dataset_app.command("list")
def dataset_list(suite: Annotated[str | None, typer.Option()] = None) -> None:
    data = ds.load()
    for case in data.select(suites=_csv(suite) or None):
        units = case.units()
        typer.echo(
            f"{case.id:<34} {case.suite:<10} {case.quality_mode:<7} units={len(units):<3} "
            f"gold={case.gold_status:<8} labels={case.labels_status}"
        )


@dataset_app.command("check")
def dataset_check() -> None:
    """Verify every PDF hash and gold file."""
    data = ds.load()
    issues = [p for c in data.cases for p in ds.problems(c)]
    for issue in issues:
        typer.secho(issue, fg="red")
    drafts = [c.id for c in data.cases if c.gold_status != "verified"]
    if drafts:
        typer.secho(
            f"{len(drafts)} case(s) with draft gold (review, then `dataset mark`): {', '.join(drafts)}",
            fg="yellow",
        )
    if issues:
        raise typer.Exit(1)
    typer.echo(f"{len(data.cases)} cases OK")


def _upsert(cases: list[ds.Case]) -> None:
    data = ds.load() if ds.DATASET_PATH.exists() else ds.Dataset(cases=[])
    by_id = {c.id: c for c in data.cases}
    for case in cases:
        by_id[case.id] = case
    data.cases = sorted(by_id.values(), key=lambda c: (c.suite != "core", c.suite, c.id))
    ds.save(data)


@dataset_app.command("synth")
def dataset_synth(
    templates: Annotated[
        str | None, typer.Option(help="Comma list of templates (default: all)")
    ] = None,
    links: Annotated[bool, typer.Option(help="Also build the link-injection suite")] = True,
) -> None:
    """(Re)build the synthetic and link suites into cases/ and dataset.json."""
    from .synth.build import build_links, build_synthetic

    cases = build_synthetic(_csv(templates) or None)
    if links:
        cases += build_links()
    _upsert(cases)
    typer.echo(f"wrote {len(cases)} synthetic case(s)")


def _parse_ranges(specs: list[str]) -> list[tuple[str, int, int]]:
    out = []
    for spec in specs:
        m = re.fullmatch(r"([a-z0-9][a-z0-9-]*):(\d+)-(\d+)", spec.strip())
        if not m:
            _fail(f"bad --range '{spec}', expected id:START-END (e.g. income-statement:32-36)")
        out.append((m.group(1), int(m.group(2)), int(m.group(3))))
    return out


@dataset_app.command("add")
def dataset_add(
    pdf: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="Source PDF")],
    case_id: Annotated[str, typer.Option("--id", help="Case id (lowercase, dashes)")],
    ranges: Annotated[
        list[str] | None, typer.Option("--range", help="id:START-END; makes a range case")
    ] = None,
    suite: Annotated[str, typer.Option()] = "core",
    description: Annotated[str, typer.Option()] = "",
    draft_tier: Annotated[
        str, typer.Option(help="Tier whose output seeds draft gold ('none' to skip)")
    ] = "max",
    image: Annotated[str | None, typer.Option(help="Image tag (default: latest built)")] = None,
    force: Annotated[bool, typer.Option(help="Replace an existing case")] = False,
) -> None:
    """Add a real PDF: copy it, record its hash, suggest page labels from the current
    classifier, and seed DRAFT gold from one tier's output for you to correct."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", case_id):
        _fail("--id must be lowercase letters, digits and dashes")
    if ds.DATASET_PATH.exists() and not force and any(c.id == case_id for c in ds.load().cases):
        _fail(f"case {case_id} exists (use --force to replace)")
    img = docker_runner.resolve_image(image)
    case_dir = PROJECT_DIR / "cases" / case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    source = case_dir / "source.pdf"
    shutil.copyfile(pdf, source)
    with pymupdf.open(source) as doc:
        n_pages = len(doc)
    parsed_ranges = _parse_ranges(ranges or [])
    for rid, start, end in parsed_ranges:
        if not 1 <= start <= end <= n_pages:
            _fail(f"range {rid} {start}-{end} is outside 1-{n_pages}")
    scored_pages = sorted({p for _, s, e in parsed_ranges for p in range(s, e + 1)}) or list(
        range(1, n_pages + 1)
    )

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        # 1. Classifier suggestions: parse with the VLM disabled (free, deterministic).
        typer.echo("collecting classifier suggestions (VLM disabled)...")
        probe = tmp_path / "probe"
        docker_runner.run_container(
            img, source, "balanced", probe, env_overrides={"GEMINI_API_KEY": ""}
        )
        trace = read_json(probe / "trace.json")
        if not trace:
            _fail(f"probe run failed; see {probe.parent / 'probe.log'}")
        by_page = {p["page_number"]: p for p in trace["pages"]}
        labels = {}
        for page in scored_pages:
            c = (by_page.get(page) or {}).get("complexity") or {}
            labels[str(page)] = ds.PageLabel(
                expected_route="vlm_required" if c.get("needs_ocr") else "native_acceptable",
                expected_classifier_reasons=[
                    r for r in c.get("reasons", []) if r in ds.CLASSIFIER_REASONS
                ],
            )

        # 2. Draft gold from one tier on just the scored pages.
        gold_pages: dict[int, str] = {}
        if draft_tier != "none":
            subset = tmp_path / "subset.pdf"
            with pymupdf.open(source) as doc:
                doc.select([p - 1 for p in scored_pages])
                doc.save(subset)
            use_vlm = bool(gemini_api_key())
            typer.echo(
                f"seeding draft gold with {draft_tier}{'' if use_vlm else ' (no GEMINI_API_KEY: native output only)'}..."
            )
            out = tmp_path / "draft"
            docker_runner.run_container(img, subset, draft_tier, out)
            for i, page in enumerate(scored_pages, start=1):
                path = out / "pages" / f"{i:03d}.md"
                gold_pages[page] = path.read_text(encoding="utf-8") if path.exists() else ""

    expected = case_dir / "expected"
    quality_ranges = []
    if parsed_ranges:
        (expected / "ranges").mkdir(parents=True, exist_ok=True)
        for rid, start, end in parsed_ranges:
            gold = expected / "ranges" / f"{rid}-{start:03d}-{end:03d}.md"
            gold.write_text(
                "\n\n".join(gold_pages.get(p, "").strip() for p in range(start, end + 1)).strip()
                + "\n",
                encoding="utf-8",
            )
            quality_ranges.append(
                ds.QualityRange(
                    id=rid, start_page=start, end_page=end, gold=str(gold.relative_to(PROJECT_DIR))
                )
            )
    else:
        (expected / "pages").mkdir(parents=True, exist_ok=True)
        for page in scored_pages:
            (expected / "pages" / f"{page:03d}.md").write_text(
                gold_pages.get(page, "").strip() + "\n", encoding="utf-8"
            )

    case = ds.Case(
        id=case_id,
        pdf=str(source.relative_to(PROJECT_DIR)),
        sha256=ds.sha256_file(source),
        quality_mode="ranges" if parsed_ranges else "pages",
        suite=suite,
        description=description,
        gold_status="draft",
        labels_status="draft",
        quality_ranges=quality_ranges,
        page_labels=labels,
    )
    _upsert([case])
    typer.echo(
        f"added {case_id}: {len(scored_pages)} labelled page(s). Gold and labels are DRAFTS: correct "
        f"{expected.relative_to(PROJECT_DIR)}/ and page_labels, then `extractor-eval dataset mark {case_id} --gold verified --labels verified`."
    )


@dataset_app.command("mark")
def dataset_mark(
    case_id: str,
    gold: Annotated[str | None, typer.Option(help="verified | draft")] = None,
    labels: Annotated[str | None, typer.Option(help="verified | draft")] = None,
) -> None:
    """Record that a case's gold Markdown / page labels were human-reviewed."""
    data = ds.load()
    case = data.get(case_id)
    for value in (gold, labels):
        if value not in (None, "verified", "draft"):
            _fail("use verified or draft")
    if gold:
        case.gold_status = gold
    if labels:
        case.labels_status = labels
    ds.save(data)
    typer.echo(f"{case_id}: gold={case.gold_status} labels={case.labels_status}")


@dataset_app.command("show")
def dataset_show(case_id: str) -> None:
    """Show a case: status, units (gold files) and page labels."""
    case = _case(case_id)
    typer.echo(
        f"{case.id}  suite={case.suite}  mode={case.quality_mode}  pages={ds.page_count(case)}  "
        f"gold={case.gold_status}  labels={case.labels_status}"
    )
    if case.description:
        typer.echo(f"  {case.description}")
    typer.echo("units:")
    for u in case.units():
        typer.echo(
            f"  {u.id:<24} pages {u.pages[0]}-{u.pages[-1]}  {u.gold_path.relative_to(ds.PROJECT_DIR)}"
        )
    typer.echo("page labels:")
    for page, label in sorted(case.page_labels.items(), key=lambda kv: int(kv[0])):
        typer.echo(
            f"  p{int(page):<4} {label.expected_route or '-':<18} reasons={','.join(label.expected_classifier_reasons) or '-'}"
            f"  tags={','.join(label.tags) or '-'}  phrases={' | '.join(label.required_phrases) or '-'}"
        )
    for issue in ds.problems(case):
        typer.secho(issue, fg="red")


def _case(case_id: str) -> ds.Case:
    try:
        return ds.load().get(case_id)
    except KeyError as e:
        _fail(str(e.args[0]))
        raise


@dataset_app.command("label")
def dataset_label(
    case_id: str,
    page: int,
    route: Annotated[
        str | None, typer.Option(help="vlm_required | native_acceptable | either_acceptable")
    ] = None,
    reasons: Annotated[
        str | None, typer.Option(help=f"Comma list of: {', '.join(sorted(ds.CLASSIFIER_REASONS))}")
    ] = None,
    tags: Annotated[str | None, typer.Option(help="Comma list of free-form tags")] = None,
    phrase: Annotated[
        list[str] | None, typer.Option("--phrase", help="Required phrase (repeatable; replaces)")
    ] = None,
) -> None:
    """Set one page's routing label, expected classifier reasons, tags or required phrases."""
    try:
        label = ds.set_label(
            case_id,
            page,
            expected_route=route,
            expected_classifier_reasons=None if reasons is None else _csv(reasons),
            tags=None if tags is None else _csv(tags),
            required_phrases=phrase,
        )
    except (KeyError, ValueError) as e:
        _fail(str(e))
        raise
    typer.echo(f"{case_id} p{page}: {label.model_dump_json()}")


@dataset_app.command("edit")
def dataset_edit(
    case_id: str,
    suite: Annotated[str | None, typer.Option()] = None,
    description: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Change a case's suite or description."""
    try:
        case = ds.update_case(case_id, suite=suite, description=description)
    except (KeyError, ValueError) as e:
        _fail(str(e))
        raise
    typer.echo(f"{case.id}: suite={case.suite} description={case.description!r}")


@dataset_app.command("gold")
def dataset_gold(
    case_id: str,
    unit_id: Annotated[str, typer.Argument(help="Unit id, e.g. p001 or a range id")],
    from_file: Annotated[
        Path | None,
        typer.Option("--from", exists=True, dir_okay=False, help="Replace the gold with this file"),
    ] = None,
) -> None:
    """Print a unit's gold Markdown, or replace it with --from FILE."""
    case = _case(case_id)
    try:
        if from_file:
            path = ds.write_gold(case_id, unit_id, from_file.read_text(encoding="utf-8"))
            typer.echo(f"wrote {path.relative_to(ds.PROJECT_DIR)}")
            return
        typer.echo(ds.unit(case, unit_id).gold_path.read_text(encoding="utf-8"), nl=False)
    except (KeyError, ValueError, FileNotFoundError) as e:
        _fail(str(e))


@dataset_app.command("remove")
def dataset_remove(
    case_id: str, yes: Annotated[bool, typer.Option("--yes", help="Skip the prompt")] = False
) -> None:
    """Remove a case from dataset.json and delete its folder (PDF and gold)."""
    _case(case_id)
    if not yes:
        typer.confirm(f"Delete case {case_id} and its files?", abort=True)
    ds.remove_case(case_id)
    typer.echo(f"removed {case_id}")


# ---- run ---------------------------------------------------------------------------------


@app.command()
def run(
    case: Annotated[
        list[str] | None, typer.Option("--case", help="Case id (repeatable; default: all)")
    ] = None,
    suite: Annotated[str | None, typer.Option(help="Comma list of suites, e.g. core,links")] = None,
    tiers: Annotated[
        str | None, typer.Option(help="Comma list of tiers [default: balanced,max]")
    ] = None,
    image: Annotated[str | None, typer.Option(help="Image tag (default: latest built)")] = None,
    label: Annotated[
        str | None, typer.Option(help="Run label (also the initial display name)")
    ] = None,
    group: Annotated[
        list[str] | None, typer.Option("--group", help="Put the run in a group (repeatable)")
    ] = None,
    judge: Annotated[bool, typer.Option(help="Score with the Gemini judge")] = True,
    judge_model: Annotated[str, typer.Option()] = JUDGE_MODEL,
    perf_repeats: Annotated[
        int, typer.Option(help="Extra unscored runs per small case for timing")
    ] = 0,
    model: Annotated[str | None, typer.Option(help="Override GEMINI_MODEL for extraction")] = None,
    resume: Annotated[str | None, typer.Option(help="Continue an interrupted run id")] = None,
) -> None:
    """Parse cases in the extractor container, score them, and write a run.

    With --resume, the run's own cases and tiers are used unless you pass others."""
    data = ds.load()
    resume_id = _resolve(resume) if resume else None
    previous = read_json(store.run_dir(resume_id) / "run.json") if resume_id else None
    if previous and not case and not suite:
        case = previous["config"]["cases"]
    if previous and not tiers:
        tiers = ",".join(previous["config"]["tiers"])
    try:
        cases = data.select(case or None, _csv(suite) or None)
    except KeyError as e:
        _fail(str(e.args[0]))
    if not cases:
        _fail("no cases selected")
    tier_list = _csv(tiers) or list(TIERS)
    unknown = set(tier_list) - set(TIERS) - {"smart", "fast"}
    if unknown:
        _fail(f"unknown tier(s) {sorted(unknown)}")
    if judge and not gemini_api_key():
        _fail("GEMINI_API_KEY is not set (needed for VLM tiers and the judge); or pass --no-judge")
    try:
        img = docker_runner.resolve_image(image)
        runner.execute(
            cases,
            tier_list,
            img,
            label=label or (resume_id or "run"),
            groups=group,
            judge=judge,
            judge_model=judge_model,
            perf_repeats=perf_repeats,
            model=model,
            resume=resume_id,
            log=typer.echo,
        )
    except (docker_runner.DockerError, ValueError, store.RunNotFound) as e:
        _fail(str(e))


@app.command()
def rescore(run_ref: str) -> None:
    """Recompute deterministic scores and summaries from saved artifacts (no API calls)."""
    run_id = _resolve(run_ref)
    data = ds.load()
    runner.rescore(store.run_dir(run_id), {c.id: c for c in data.cases}, log=typer.echo)


@app.command()
def compare(
    runs: Annotated[list[str], typer.Argument(help="Two or more run ids, prefixes or names")],
    tier: Annotated[str | None, typer.Option(help="Comma list of tiers to include")] = None,
    scope: Annotated[
        str, typer.Option(help="all | verified (quality over verified gold only)")
    ] = "all",
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Compare runs side by side (one column per run x tier)."""
    try:
        result = compare_mod.compare(runs, _csv(tier) or None, scope)
    except (ValueError, store.RunNotFound) as e:
        _fail(str(e))
    typer.echo(json.dumps(result, indent=2) if as_json else compare_mod.console(result))


@app.command()
def ui(
    host: Annotated[str, typer.Option()] = "127.0.0.1",
    port: Annotated[int, typer.Option()] = 8765,
    open_browser: Annotated[bool, typer.Option("--open/--no-open")] = False,
) -> None:
    """Serve the results UI (local only by default)."""
    from .web.server import serve

    serve(host, port, open_browser)


# ---- runs ------------------------------------------------------------------------------


@runs_app.command("list")
def runs_list(
    group: Annotated[str | None, typer.Option()] = None,
    kind: Annotated[str | None, typer.Option(help="extractor | claims")] = None,
) -> None:
    for r in store.list_runs(group, kind):
        head = next(iter(r["headline"].values()), {}) if r["headline"] else {}
        key = "text_similarity" if r["kind"] == "extractor" else "claim_f1"
        value = head.get(key)
        typer.echo(
            f"{r['id']:<44} {r['name'][:28]:<28} {r['kind']:<9} {r.get('status') or '':<20} "
            f"{key}={'-' if value is None else f'{value:.3f}'}  groups=[{', '.join(r['groups'])}]"
            f"{'  comments=' + str(r['comments']) if r['comments'] else ''}"
        )


@runs_app.command("show")
def runs_show(run_ref: str) -> None:
    run_id = _resolve(run_ref)
    record = store.summary(run_id)
    typer.echo(json.dumps(record, indent=2))
    for c in store.comments(run_id):
        target = "/".join(c["target"].values()) if c.get("target") else "run"
        typer.echo(f"  [{c['id']}] {c['author']} on {target}: {c['text']}")


@runs_app.command("rename")
def runs_rename(run_ref: str, name: str) -> None:
    meta = store.rename(_resolve(run_ref), name)
    typer.echo(f"renamed to '{meta['name']}'")


@runs_app.command("group")
def runs_group(
    run_ref: str,
    add: Annotated[list[str] | None, typer.Option("--add")] = None,
    remove: Annotated[list[str] | None, typer.Option("--remove")] = None,
) -> None:
    """Add/remove a run's groups."""
    run_id = _resolve(run_ref)
    if add:
        store.add_groups(run_id, add)
    if remove:
        store.remove_groups(run_id, remove)
    typer.echo(f"groups: {', '.join(store.meta(run_id)['groups']) or '(none)'}")


@runs_app.command("groups")
def runs_groups() -> None:
    for name, count in store.groups().items():
        typer.echo(f"{name:<30} {count} run(s)")


@runs_app.command("group-rename")
def runs_group_rename(old: str, new: str) -> None:
    typer.echo(f"renamed group on {store.rename_group(old, new)} run(s)")


@runs_app.command("comment")
def runs_comment(
    run_ref: str,
    text: str,
    case: Annotated[str | None, typer.Option()] = None,
    tier: Annotated[str | None, typer.Option()] = None,
    unit: Annotated[str | None, typer.Option(help="Unit id, e.g. p001 or a range id")] = None,
    author: Annotated[str, typer.Option()] = "",
) -> None:
    comment = store.add_comment(
        _resolve(run_ref), text, author, {"case": case, "tier": tier, "unit": unit}
    )
    typer.echo(f"added comment {comment['id']}")


@runs_app.command("comments")
def runs_comments(run_ref: str) -> None:
    for c in store.comments(_resolve(run_ref)):
        target = "/".join(c["target"].values()) if c.get("target") else "run"
        typer.echo(f"[{c['id']}] {c['created_at']} {c['author']} on {target}: {c['text']}")


@runs_app.command("uncomment")
def runs_uncomment(run_ref: str, comment_id: str) -> None:
    store.delete_comment(_resolve(run_ref), comment_id)
    typer.echo("deleted")


@runs_app.command("delete")
def runs_delete(
    run_ref: str, yes: Annotated[bool, typer.Option("--yes", help="Skip the prompt")] = False
) -> None:
    """Permanently delete a run's results."""
    run_id = _resolve(run_ref)
    if not yes:
        typer.confirm(f"Permanently delete {run_id} ({store.meta(run_id)['name']})?", abort=True)
    store.delete_run(run_id)
    typer.echo(f"deleted {run_id}")


# ---- jobs (started from the web UI) ----------------------------------------------------

jobs_app = typer.Typer(no_args_is_help=True, help="Inspect jobs started from the web UI.")
app.add_typer(jobs_app, name="jobs")


@jobs_app.command("list")
def jobs_list() -> None:
    from .jobs import manager

    for job in manager().all():
        typer.echo(
            f"{job['id']:<24} {job['status']:<10} {job['kind']:<12} {job['title']}"
            + (f"  -> run {job['links']['run']}" if job["links"].get("run") else "")
        )


@jobs_app.command("log")
def jobs_log(job_id: str) -> None:
    from .jobs import JobNotFound, manager

    try:
        typer.echo(manager().log(job_id)["text"], nl=False)
    except JobNotFound:
        _fail(f"no job {job_id}")


@jobs_app.command("cancel")
def jobs_cancel(job_id: str) -> None:
    from .jobs import JobNotFound, manager

    try:
        job = manager().cancel(job_id)
    except (JobNotFound, ValueError) as e:
        _fail(str(e))
        raise
    typer.echo(f"{job_id}: cancel requested ({job['status']})")
