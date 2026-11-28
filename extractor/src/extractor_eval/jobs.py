"""
Background jobs for the web UI.

A job is one `extractor-eval` CLI invocation (image build, dataset add/synth, run,
rescore) started as a child process, so the UI and the command line execute exactly
the same code. Jobs run one at a time, in order: they share the Gemini rate limit,
Docker and dataset.json.

    results/jobs/<job-id>/job.json   kind, title, argv, status, pid, timestamps, links
    results/jobs/<job-id>/log.txt    combined stdout/stderr

Arguments are passed as an argv list (never through a shell) in `--option=value`
form, so a value can never be read as another option.
"""

import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from . import PROJECT_DIR, RESULTS_DIR
from .config import read_json, write_json

JOBS_DIR = RESULTS_DIR / "jobs"
ACTIVE = ("queued", "running")
_RUN_LINE = re.compile(r"^\[run\] (\S+): ", re.M)
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class JobNotFound(KeyError):
    pass


class JobManager:
    def __init__(self, jobs_dir: Path | None = None) -> None:
        self.dir = jobs_dir or JOBS_DIR
        self._lock = threading.RLock()
        self._wake = threading.Condition(self._lock)
        self._procs: dict[str, subprocess.Popen] = {}
        self._worker: threading.Thread | None = None

    # ---- persistence -------------------------------------------------------------

    def _path(self, job_id: str) -> Path:
        if not re.fullmatch(r"[0-9A-Za-z-]+", job_id or ""):
            raise JobNotFound(job_id)
        return self.dir / job_id / "job.json"

    def get(self, job_id: str) -> dict:
        job = read_json(self._path(job_id))
        if job is None:
            raise JobNotFound(job_id)
        return job

    def _save(self, job: dict) -> None:
        write_json(self._path(job["id"]), job)

    def all(self, limit: int = 100) -> list[dict]:
        if not self.dir.exists():
            return []
        jobs = [read_json(p) for p in self.dir.glob("*/job.json")]
        jobs = [j for j in jobs if j]
        jobs.sort(key=lambda j: j["created_at"], reverse=True)
        return jobs[:limit]

    def log(self, job_id: str, offset: int = 0) -> dict:
        self.get(job_id)
        path = self.dir / job_id / "log.txt"
        if not path.exists():
            return {"text": "", "offset": 0}
        with path.open("rb") as f:
            f.seek(max(offset, 0))
            data = f.read(512 * 1024)
        return {
            "text": _ANSI.sub("", data.decode("utf-8", "replace")),
            "offset": offset + len(data),
        }

    def recover(self) -> None:
        """Called once when the UI server starts: jobs left queued/running by a previous
        server process can't be driven by this one, so mark them instead of showing them
        as live forever. (The CLI only reads jobs and never calls this.)"""
        for job in self.all(limit=10_000):
            if job["status"] in ACTIVE:
                pid = job.get("pid")
                if job["status"] == "running" and pid and _alive(pid):
                    job["status"] = "orphaned"
                    job["error"] = (
                        "UI server restarted while this job was running (it may still be running)."
                    )
                else:
                    job["status"] = "lost"
                    job["error"] = "UI server stopped before this job finished."
                job["finished_at"] = job.get("finished_at") or _now()
                self._save(job)

    # ---- submit / cancel ------------------------------------------------------------

    def submit(
        self,
        kind: str,
        title: str,
        args: list[str],
        *,
        links: dict | None = None,
        cleanup: list[str] | None = None,
    ) -> dict:
        job_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
        job = {
            "id": job_id,
            "kind": kind,
            "title": title,
            "argv": ["extractor-eval", *args],
            "status": "queued",
            "created_at": _now(),
            "started_at": None,
            "finished_at": None,
            "exit_code": None,
            "pid": None,
            "error": None,
            "links": links or {},
            "cleanup": cleanup or [],
        }
        with self._lock:
            self._save(job)
            self._ensure_worker()
            self._wake.notify_all()
        return job

    def cancel(self, job_id: str) -> dict:
        with self._lock:
            job = self.get(job_id)
            if job["status"] == "queued":
                job.update(status="cancelled", finished_at=_now())
                self._save(job)
                return job
            if job["status"] not in ("running", "orphaned"):
                raise ValueError(f"job is {job['status']}")
            proc = self._procs.get(job_id)
            pid = proc.pid if proc else job.get("pid")
        if pid and _alive(pid):
            # SIGINT first: the runner marks the run 'interrupted' and keeps what it finished.
            try:
                _interrupt(pid)  # whole group, so `docker run` forwards it
            except (ProcessLookupError, PermissionError):
                pass
            threading.Thread(target=_escalate, args=(pid,), daemon=True).start()
        if not proc and job["status"] == "orphaned":
            job.update(status="cancelled", finished_at=_now())
        else:
            job["cancel_requested"] = True  # the server's worker records the final status
        self._save(job)
        return job

    def active(self) -> list[dict]:
        return [j for j in self.all() if j["status"] in ACTIVE]

    # ---- worker -------------------------------------------------------------------------

    def _ensure_worker(self) -> None:
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._loop, name="jobs", daemon=True)
            self._worker.start()

    def _next(self) -> dict | None:
        queued = [j for j in self.all(limit=10_000) if j["status"] == "queued"]
        return min(queued, key=lambda j: j["created_at"]) if queued else None

    def _loop(self) -> None:
        while True:
            with self._lock:
                job = self._next()
                while job is None:
                    self._wake.wait(timeout=30)
                    job = self._next()
            self._execute(job)

    def _execute(self, job: dict) -> None:
        log_path = self.dir / job["id"] / "log.txt"
        env = os.environ | {"PYTHONUNBUFFERED": "1", "NO_COLOR": "1", "TERM": "dumb"}
        cmd = [sys.executable, "-m", "extractor_eval", *job["argv"][1:]]
        with self._lock:
            job = self.get(job["id"])
            if job["status"] != "queued":
                return
            with log_path.open("ab") as log:
                log.write(f"$ {' '.join(job['argv'])}\n".encode())
                try:
                    proc = subprocess.Popen(
                        cmd,
                        cwd=PROJECT_DIR,
                        env=env,
                        stdin=subprocess.DEVNULL,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        **_NEW_GROUP,
                    )
                except OSError as e:
                    job.update(status="failed", error=str(e), finished_at=_now())
                    self._save(job)
                    return
            self._procs[job["id"]] = proc
            job.update(status="running", started_at=_now(), pid=proc.pid)
            self._save(job)

        code = proc.wait()
        with self._lock:
            self._procs.pop(job["id"], None)
            job = self.get(job["id"])
            text = log_path.read_text(encoding="utf-8", errors="replace")
            match = _RUN_LINE.search(text)
            if match:
                job["links"]["run"] = match.group(1)
            if job.get("cancel_requested"):
                status = "cancelled"
            else:
                status = "succeeded" if code == 0 else "failed"
            job.update(status=status, exit_code=code, finished_at=_now())
            if status == "failed" and not job.get("error"):
                tail = [ln for ln in _ANSI.sub("", text).splitlines() if ln.strip()][-1:]
                job["error"] = tail[0][:300] if tail else f"exit code {code}"
            for path in job.get("cleanup", []):
                Path(path).unlink(missing_ok=True)
            self._save(job)


_WINDOWS = sys.platform == "win32"

# Each job runs in its own process group, so cancelling reaches `docker run` too.
_NEW_GROUP: dict = (
    {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    if _WINDOWS
    else {"start_new_session": True}
)


def _interrupt(pid: int) -> None:
    """Ask the job to stop cleanly (Ctrl+C / Ctrl+Break), so the run is marked interrupted."""
    if _WINDOWS:
        os.kill(pid, signal.CTRL_BREAK_EVENT)
    else:
        os.killpg(pid, signal.SIGINT)


def _terminate(pid: int) -> None:
    if _WINDOWS:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    else:
        os.killpg(pid, signal.SIGTERM)


def _alive(pid: int) -> bool:
    if _WINDOWS:  # os.kill(pid, 0) would terminate the process on Windows
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def _escalate(pid: int, grace: float = 20.0) -> None:
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if not _alive(pid):
            return
        time.sleep(0.5)
    try:
        _terminate(pid)
    except (ProcessLookupError, PermissionError):
        pass


_manager: JobManager | None = None


def manager() -> JobManager:
    global _manager
    if _manager is None:
        _manager = JobManager()
    return _manager
