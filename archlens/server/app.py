"""FastAPI application: the local web UI and its JSON API.

Scans run in a background thread and report progress into a job record that the
browser polls. Polling (rather than websockets or SSE) is a deliberate choice -
a scan emits a progress update every few hundred milliseconds at most, and
polling survives the reloads you get while iterating on the UI.

Security posture: this binds to localhost and reads arbitrary local paths by
design - it is a developer tool for inspecting your own code. It is not
hardened for exposure to a network, and `create_app` refuses to serve on a
non-loopback host unless explicitly forced.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..inference import llm
from ..pipeline import analyse
from ..render.architecture import graphviz_available
from ..render.icons import available as icons_available

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

# Where rendered diagrams land. Overridable so the CLI can point it elsewhere.
DEFAULT_OUTPUT_ROOT = Path(os.environ.get("ARCHLENS_OUTPUT", Path.cwd() / "archlens-output"))


# --------------------------------------------------------------------------- #
# Job tracking
# --------------------------------------------------------------------------- #


@dataclass
class Job:
    id: str
    root: str
    status: Literal["queued", "running", "done", "error"] = "queued"
    message: str = "Queued"
    progress: float = 0.0
    result: Any = None
    error: str | None = None
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    def public(self) -> dict:
        return {
            "id": self.id,
            "root": self.root,
            "status": self.status,
            "message": self.message,
            "progress": round(self.progress, 3),
            "error": self.error,
            "elapsed": round((self.finished_at or time.time()) - self.started_at, 1),
        }


class JobStore:
    """In-memory job registry. Bounded so a long-lived server cannot grow forever."""

    def __init__(self, limit: int = 40) -> None:
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()
        self._limit = limit

    def create(self, root: str) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], root=root)
        with self._lock:
            self._jobs[job.id] = job
            self._order.append(job.id)
            while len(self._order) > self._limit:
                self._jobs.pop(self._order.pop(0), None)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def recent(self, limit: int = 12) -> list[Job]:
        with self._lock:
            return [self._jobs[jid] for jid in reversed(self._order[-limit:]) if jid in self._jobs]


# --------------------------------------------------------------------------- #
# Request models
# --------------------------------------------------------------------------- #


class ScanRequest(BaseModel):
    path: str = Field(description="Absolute path to the repository to scan")
    use_llm: bool = True
    dark: bool = False
    direction: Literal["LR", "TB"] = "LR"


# --------------------------------------------------------------------------- #
# App factory
# --------------------------------------------------------------------------- #


def create_app(output_root: Path | None = None) -> FastAPI:
    app = FastAPI(
        title="ArchLens",
        description="Scan a codebase, understand it, and draw the architecture.",
        version="1.0.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    store = JobStore()
    out_root = Path(output_root or DEFAULT_OUTPUT_ROOT)
    out_root.mkdir(parents=True, exist_ok=True)
    app.state.job_store = store
    app.state.output_root = out_root

    # ---- capability probe ------------------------------------------------
    @app.get("/api/health")
    def health() -> dict:
        """What this installation can actually do, for the UI to reflect."""
        return {
            "ok": True,
            "graphviz": graphviz_available(),
            "diagrams": icons_available(),
            "anthropic_sdk": llm.sdk_available(),
            "anthropic_key": llm.api_key_available(),
            "output_root": str(out_root),
        }

    # ---- filesystem browsing (for the path picker) ------------------------
    @app.get("/api/browse")
    def browse(path: str = "") -> dict:
        """List directories under `path`, so the UI can offer a picker."""
        if not path:
            candidates = [Path.home(), Path.cwd()]
            roots = []
            for candidate in candidates:
                if candidate.is_dir():
                    roots.append({"name": str(candidate), "path": str(candidate)})
            # On Windows, offer the drive letters too.
            if os.name == "nt":
                for letter in "CDEFGH":
                    drive = Path(f"{letter}:\\")
                    if drive.exists():
                        roots.append({"name": f"{letter}:\\", "path": str(drive)})
            return {"path": "", "parent": None, "entries": roots}

        target = Path(path).expanduser()
        if not target.is_dir():
            raise HTTPException(status_code=404, detail=f"Not a directory: {path}")

        entries = []
        try:
            for child in sorted(target.iterdir(), key=lambda p: p.name.lower()):
                if not child.is_dir() or child.name.startswith("."):
                    continue
                entries.append({
                    "name": child.name,
                    "path": str(child),
                    "is_repo": (child / ".git").exists(),
                })
        except PermissionError:
            raise HTTPException(status_code=403, detail="Permission denied") from None

        return {
            "path": str(target),
            "parent": str(target.parent) if target.parent != target else None,
            "entries": entries[:400],
        }

    # ---- start a scan ------------------------------------------------------
    @app.post("/api/scan")
    def start_scan(request: ScanRequest) -> dict:
        root = Path(request.path).expanduser()
        if not root.exists():
            raise HTTPException(status_code=400, detail=f"Path does not exist: {root}")
        if not root.is_dir():
            raise HTTPException(status_code=400, detail=f"Not a directory: {root}")

        job = store.create(str(root.resolve()))
        job_dir = out_root / job.id

        def run() -> None:
            job.status = "running"

            def progress(message: str, fraction: float) -> None:
                job.message = message
                job.progress = fraction

            try:
                result = analyse(
                    root, job_dir,
                    use_llm=request.use_llm,
                    dark=request.dark,
                    direction=request.direction,
                    job_id=job.id,
                    progress=progress,
                )
                job.result = result
                job.status = "done"
                job.message = "Complete"
                job.progress = 1.0
            except Exception as exc:
                log.exception("Scan failed for %s", root)
                job.status = "error"
                job.error = f"{type(exc).__name__}: {exc}"
                job.message = "Failed"
            finally:
                job.finished_at = time.time()

        threading.Thread(target=run, name=f"archlens-{job.id}", daemon=True).start()
        return job.public()

    # ---- poll job status ---------------------------------------------------
    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str) -> dict:
        job = store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Unknown job")
        return job.public()

    @app.get("/api/jobs")
    def job_list() -> dict:
        return {"jobs": [job.public() for job in store.recent()]}

    # ---- fetch the finished result ----------------------------------------
    @app.get("/api/jobs/{job_id}/result")
    def job_result(job_id: str) -> JSONResponse:
        job = store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Unknown job")
        if job.status == "error":
            raise HTTPException(status_code=500, detail=job.error or "Scan failed")
        if job.status != "done" or job.result is None:
            raise HTTPException(status_code=409, detail="Scan is still running")

        result = job.result
        # `evidence.files` is the bulk of the payload and the UI does not need
        # every entry - send a summary instead of megabytes of paths.
        payload = result.model_dump(mode="json", exclude={"evidence": {"files"}})
        payload["evidence"]["file_sample"] = [
            f.model_dump(mode="json") for f in result.evidence.files[:200]
        ]
        payload["diagram_base"] = f"/api/jobs/{job_id}/diagram"
        return JSONResponse(payload)

    # ---- serve a rendered diagram ------------------------------------------
    @app.get("/api/jobs/{job_id}/diagram/{filename}")
    def diagram(job_id: str, filename: str) -> FileResponse:
        job = store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Unknown job")

        # Resolve inside the job directory and verify containment, so a
        # traversal attempt in `filename` cannot escape.
        job_dir = (out_root / job_id).resolve()
        target = (job_dir / filename).resolve()
        if not str(target).startswith(str(job_dir)) or not target.is_file():
            raise HTTPException(status_code=404, detail="No such diagram")

        media = {"svg": "image/svg+xml", "png": "image/png"}.get(target.suffix.lstrip("."))
        return FileResponse(target, media_type=media)

    # ---- static UI -----------------------------------------------------------
    if STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

    return app


app = create_app()
