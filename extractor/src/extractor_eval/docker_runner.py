"""
Build the extractor evaluation image and run it once per (case, tier).

Images are tagged `whimsync-extractor-eval:<commit>` for a clean checkout, or
`<commit>-dirty-<hash>` when `apps/extractor` has uncommitted changes (the hash
covers the diff and untracked files, so different local edits get different tags).
Built images are recorded in `results/images.json`; `run` uses the latest by default.
"""

import hashlib
import os
import subprocess
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import RESULTS_DIR
from .config import CONTAINER_ENV, CONTAINER_TIMEOUT_SECONDS, read_json, write_json

IMAGE_REPO = "whimsync-extractor-eval"
EXTRACTOR_SUBDIR = "apps/extractor"
IMAGES_PATH = RESULTS_DIR / "images.json"


class DockerError(RuntimeError):
    pass


@dataclass
class Image:
    tag: str
    commit: str
    dirty: bool
    digest: str
    built_at: str
    extractor_root: str

    @property
    def ref(self) -> str:
        return f"{IMAGE_REPO}:{self.tag}"


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def extractor_version(root: Path) -> tuple[str, bool, str]:
    """(short commit, dirty, tag) for the extractor in a whimsync checkout."""
    commit = _git(root, "rev-parse", "--short", "HEAD")
    status = _git(root, "status", "--porcelain", "--", EXTRACTOR_SUBDIR)
    if not status:
        return commit, False, commit
    digest = hashlib.sha256(_git(root, "diff", "HEAD", "--", EXTRACTOR_SUBDIR).encode())
    untracked = _git(
        root, "ls-files", "--others", "--exclude-standard", "--", EXTRACTOR_SUBDIR
    ).splitlines()
    for rel in sorted(untracked):
        path = root / rel
        if path.is_file():
            digest.update(rel.encode())
            digest.update(path.read_bytes())
    return commit, True, f"{commit}-dirty-{digest.hexdigest()[:8]}"


def build_image(extractor_root: Path, tag: str | None = None, quiet: bool = False) -> Image:
    root = extractor_root.resolve()
    if not (root / EXTRACTOR_SUBDIR / "Dockerfile.eval").exists():
        raise DockerError(f"{root / EXTRACTOR_SUBDIR / 'Dockerfile.eval'} not found")
    commit, dirty, auto_tag = extractor_version(root)
    tag = tag or auto_tag
    ref = f"{IMAGE_REPO}:{tag}"
    cmd = [
        "docker",
        "build",
        "-f",
        str(root / EXTRACTOR_SUBDIR / "Dockerfile.eval"),
        "--build-arg",
        f"EXTRACTOR_COMMIT={tag}",
        "-t",
        ref,
        str(root / EXTRACTOR_SUBDIR),
    ]
    if quiet:
        cmd.insert(2, "-q")
    proc = subprocess.run(cmd, capture_output=quiet, text=True)
    if proc.returncode != 0:
        raise DockerError(f"docker build failed:\n{proc.stderr if quiet else ''}")
    image = Image(
        tag=tag,
        commit=commit,
        dirty=dirty,
        digest=image_digest(ref),
        built_at=datetime.now(UTC).isoformat(timespec="seconds"),
        extractor_root=str(root),
    )
    images = [i for i in read_json(IMAGES_PATH, []) if i["tag"] != tag]
    images.append(image.__dict__)
    write_json(IMAGES_PATH, images)
    return image


def image_digest(ref: str) -> str:
    proc = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", ref], capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise DockerError(f"image {ref} not found; run `extractor-eval image build` first")
    return proc.stdout.strip()


def list_images() -> list[Image]:
    return [Image(**i) for i in read_json(IMAGES_PATH, [])]


def resolve_image(tag: str | None) -> Image:
    images = list_images()
    if tag:
        for image in images:
            if image.tag == tag:
                image.digest = image_digest(image.ref)
                return image
        # An image built outside this tool: accept it, with what we can learn.
        ref = tag if ":" in tag else f"{IMAGE_REPO}:{tag}"
        return Image(
            tag=ref.split(":", 1)[1],
            commit=ref.split(":", 1)[1].split("-dirty")[0],
            dirty="-dirty" in ref,
            digest=image_digest(ref),
            built_at="",
            extractor_root="",
        )
    if not images:
        raise DockerError("no image built yet; run `extractor-eval image build` first")
    image = max(images, key=lambda i: i.built_at)
    image.digest = image_digest(image.ref)
    return image


@dataclass
class ContainerRun:
    exit_code: int
    wall_seconds: float
    log_path: Path
    timed_out: bool = False


def run_container(
    image: Image,
    pdf: Path,
    tier: str,
    output: Path,
    model: str | None = None,
    env_overrides: dict[str, str] | None = None,
    timeout: int = CONTAINER_TIMEOUT_SECONDS,
) -> ContainerRun:
    """Runs one parse in a fresh container; artifacts land in `output`."""
    output.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update(env_overrides or {})
    name = f"extractor-eval-{uuid.uuid4().hex[:12]}"
    cmd = [
        "docker",
        "run",
        "--rm",
        "--name",
        name,
        "--network",
        "bridge",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "-v",
        f"{pdf.parent.resolve()}:/input:ro",
        "-v",
        f"{output.resolve()}:/output",
        "-e",
        f"IMAGE_TAG={image.tag}",
        "-e",
        f"IMAGE_DIGEST={image.digest}",
    ]
    for name in CONTAINER_ENV:
        if name in env:
            cmd += ["-e", name]  # value is inherited from `env`, never put on argv
    cmd += [image.ref, "--input", f"/input/{pdf.name}", "--tier", tier, "--output", "/output"]
    if model:
        cmd += ["--model", model]

    log_path = output.parent / f"{output.name}.log"
    start = time.perf_counter()
    with log_path.open("w", encoding="utf-8") as log:
        try:
            proc = subprocess.run(
                cmd, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=timeout
            )
        except subprocess.TimeoutExpired:
            # Killing the docker client leaves the container running; remove it.
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)
            return ContainerRun(-1, time.perf_counter() - start, log_path, timed_out=True)
    return ContainerRun(proc.returncode, time.perf_counter() - start, log_path)
