"""
preview_manager.py — Run the generated app and expose it as a live preview
===========================================================================
After the pipeline has produced a project, this installs its npm dependencies,
starts it on a free port (4100-4110, published by docker-compose) and reports
its status and logs so the dashboard can embed it in an iframe.

Safety: generated code is written by an LLM, so it runs with a minimal
environment (no API keys, no database passwords) and as a separate process
group that can be killed cleanly.
"""

import os
import sys
import json
import time
import socket
import asyncio
import logging
import threading
import subprocess
from collections import deque
from datetime import datetime
from typing import Callable, Optional

from fastapi import APIRouter, HTTPException

from project_runtime import (
    project_dir, find_entry_point, has_static_frontend, npm_install,
)

logger = logging.getLogger("preview")

router = APIRouter(prefix="/api/preview", tags=["preview"])

PORT_RANGE = range(4100, 4111)
MAX_RUNNING = 3
START_TIMEOUT_S = 45
LOG_LINES = 400


class Preview:
    def __init__(self, project_id: str, port: int):
        self.project_id = project_id
        self.port = port
        self.status = "installing"   # installing | starting | running | crashed | stopped | failed
        self.error: Optional[str] = None
        self.entry: Optional[str] = None
        self.kind = "node"           # node | static
        self.started_at = datetime.utcnow().isoformat()
        self.process: Optional[subprocess.Popen] = None
        self.logs: deque[str] = deque(maxlen=LOG_LINES)
        self.stopping = False

    def log(self, line: str):
        self.logs.append(f"{datetime.utcnow().strftime('%H:%M:%S')}  {line}")

    def snapshot(self, tail: int = 150) -> dict:
        return {
            "project_id": self.project_id,
            "status": self.status,
            "port": self.port,
            "url": f"http://localhost:{self.port}" if self.status == "running" else None,
            "kind": self.kind,
            "entry": self.entry,
            "error": self.error,
            "started_at": self.started_at,
            "logs": list(self.logs)[-tail:],
        }


_previews: dict[str, Preview] = {}
_lock = threading.RLock()
_broadcast: Optional[Callable] = None
_loop: Optional[asyncio.AbstractEventLoop] = None


def configure(broadcast: Callable, loop: asyncio.AbstractEventLoop) -> None:
    """Called once at startup so worker threads can push WebSocket events."""
    global _broadcast, _loop
    _broadcast, _loop = broadcast, loop


def _emit(preview: Preview, event: str) -> None:
    if not _broadcast or not _loop:
        return
    payload = json.dumps({
        "event": event, "agent": "Preview", "project_id": preview.project_id,
        "port": preview.port, "message": f"Preview {preview.status}",
        "timestamp": datetime.utcnow().isoformat(),
    })
    try:
        asyncio.run_coroutine_threadsafe(_broadcast(payload), _loop)
    except Exception as exc:
        logger.debug(f"preview emit failed: {exc}")


def _set_status(preview: Preview, status: str, error: Optional[str] = None) -> None:
    preview.status = status
    preview.error = error
    _emit(preview, f"preview_{status}")


# ─────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────
def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def _allocate_port() -> int:
    used = {p.port for p in _previews.values() if p.status in ("installing", "starting", "running")}
    for port in PORT_RANGE:
        if port not in used and _port_free(port):
            return port
    raise HTTPException(status_code=503, detail="No free preview port (4100-4110). Stop another preview first.")


def _minimal_env(port: int) -> dict:
    """Environment for generated code: nothing from the backend's own secrets."""
    keep = ("PATH", "HOME", "USERPROFILE", "SYSTEMROOT", "TEMP", "TMP", "LANG")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env.update({"PORT": str(port), "NODE_ENV": "development"})
    return env


def _kill(proc: Optional[subprocess.Popen]) -> None:
    if not proc or proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, timeout=10)
        else:
            import signal
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception as exc:
        logger.debug(f"kill failed: {exc}")
        try:
            proc.kill()
        except Exception:
            pass


def _pump_logs(preview: Preview, proc: subprocess.Popen) -> None:
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip()
        if line:
            preview.log(line)


# ─────────────────────────────────────────
# Lifecycle
# ─────────────────────────────────────────
def _run(preview: Preview) -> None:
    directory = project_dir(preview.project_id)
    try:
        entry = find_entry_point(directory)
        static_dir = has_static_frontend(directory)

        if entry:
            preview.entry = entry
            preview.log(f"Backend entry point: {entry}")
            ok, msg = npm_install(directory, log=preview.log)
            if not ok:
                preview.log(f"npm install failed: {msg}")
                _set_status(preview, "failed", f"npm install failed: {msg.splitlines()[-1] if msg else ''}")
                return
            cmd = ["node", entry]
        elif static_dir:
            preview.kind = "static"
            preview.log(f"No backend found - serving static files from {static_dir.name or '.'}/")
            cmd = [sys.executable, "-m", "http.server", str(preview.port),
                   "--bind", "0.0.0.0", "--directory", str(static_dir)]
        else:
            preview.log("Nothing to run: no server.js and no index.html were generated.")
            _set_status(preview, "failed", "Nothing to run: the project has no server.js or index.html")
            return

        if preview.stopping:
            return
        _set_status(preview, "starting")
        preview.log(f"$ {' '.join(cmd)}  (PORT={preview.port})")
        popen_kwargs = {"start_new_session": True} if os.name != "nt" else {}
        proc = subprocess.Popen(
            cmd, cwd=str(directory), env=_minimal_env(preview.port),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", **popen_kwargs,
        )
        preview.process = proc
        threading.Thread(target=_pump_logs, args=(preview, proc), daemon=True).start()

        deadline = time.time() + START_TIMEOUT_S
        while time.time() < deadline:
            if preview.stopping:
                return
            if proc.poll() is not None:
                time.sleep(0.3)  # let the log pump drain
                last = next((l for l in reversed(preview.logs) if "Error" in l), "process exited")
                _set_status(preview, "crashed",
                            f"The app exited with code {proc.returncode} before it started listening. {last[10:].strip()}")
                return
            if _port_open(preview.port):
                preview.log(f"App is listening on port {preview.port}")
                _set_status(preview, "running")
                break
            time.sleep(0.4)
        else:
            _kill(proc)
            _set_status(preview, "failed", f"The app did not open port {preview.port} within {START_TIMEOUT_S}s")
            return

        # Watch the process for the rest of its life.
        proc.wait()
        if not preview.stopping:
            preview.log(f"Process exited with code {proc.returncode}")
            _set_status(preview, "crashed", f"The app stopped unexpectedly (exit code {proc.returncode})")
    except Exception as exc:
        logger.error(f"Preview failed for {preview.project_id}: {exc}", exc_info=True)
        preview.log(f"Internal error: {exc}")
        _set_status(preview, "failed", str(exc))


def start(project_id: str) -> Preview:
    directory = project_dir(project_id)
    if not directory.exists() or not any(directory.iterdir()):
        raise HTTPException(status_code=404, detail="No generated code for this project yet.")

    with _lock:
        existing = _previews.get(project_id)
        if existing and existing.status in ("installing", "starting", "running"):
            return existing

        running = [p for p in _previews.values() if p.status in ("installing", "starting", "running")]
        if len(running) >= MAX_RUNNING:
            oldest = min(running, key=lambda p: p.started_at)
            _stop_preview(oldest)

        preview = Preview(project_id, _allocate_port())
        _previews[project_id] = preview
    _emit(preview, "preview_installing")
    threading.Thread(target=_run, args=(preview,), daemon=True, name=f"preview-{project_id[:8]}").start()
    return preview


def _stop_preview(preview: Preview) -> None:
    preview.stopping = True
    _kill(preview.process)
    if preview.status in ("installing", "starting", "running"):
        _set_status(preview, "stopped")
    preview.log("Preview stopped.")


def stop(project_id: str) -> Optional[Preview]:
    with _lock:
        preview = _previews.get(project_id)
        if preview:
            _stop_preview(preview)
        return preview


def stop_all() -> None:
    with _lock:
        for preview in list(_previews.values()):
            _stop_preview(preview)


# ─────────────────────────────────────────
# API
# ─────────────────────────────────────────
@router.post("/{project_id}/start")
def start_preview(project_id: str):
    return start(project_id).snapshot()


@router.post("/{project_id}/restart")
def restart_preview(project_id: str):
    stop(project_id)
    time.sleep(0.5)
    return start(project_id).snapshot()


@router.post("/{project_id}/stop")
def stop_preview(project_id: str):
    preview = stop(project_id)
    return preview.snapshot() if preview else {"status": "idle", "project_id": project_id, "logs": []}


@router.get("/{project_id}/status")
def preview_status(project_id: str):
    preview = _previews.get(project_id)
    if not preview:
        directory = project_dir(project_id)
        has_code = directory.exists() and any(directory.iterdir())
        return {"project_id": project_id, "status": "idle", "has_code": has_code,
                "port": None, "url": None, "error": None, "logs": []}
    snap = preview.snapshot()
    snap["has_code"] = True
    return snap
