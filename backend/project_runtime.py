"""
project_runtime.py — Make generated Node.js projects installable and runnable
==============================================================================
The Developer Agent returns a list of npm dependencies per file, but nothing used
to install them, so tests and the app itself failed with "Cannot find module".

This module:
  * scans the generated source files for require()/import statements to work
    out which npm packages the project really needs
  * writes a package.json (dependencies + start script)
  * runs `npm install` (streamed to a log callback)
"""

import os
import re
import json
import hashlib
import logging
import threading
import subprocess
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger("project_runtime")

GENERATED_CODE_DIR = Path(os.getenv("GENERATED_CODE_DIR", "./generated_code"))

# Never treat these as npm packages (Node built-ins).
NODE_BUILTINS = {
    "assert", "async_hooks", "buffer", "child_process", "cluster", "console", "constants",
    "crypto", "dgram", "diagnostics_channel", "dns", "domain", "events", "fs", "http", "http2",
    "https", "inspector", "module", "net", "os", "path", "perf_hooks", "process", "punycode",
    "querystring", "readline", "repl", "stream", "string_decoder", "sys", "timers", "tls",
    "trace_events", "tty", "url", "util", "v8", "vm", "wasi", "worker_threads", "zlib", "test",
}

# Pin the packages generated code leans on most, so a new major version can't break it.
PINNED = {
    "express": "^4.19.2",
    "cors": "^2.8.5",
    "supertest": "^7.0.0",
    "uuid": "^9.0.1",
    "body-parser": "^1.20.2",
}

SKIP_DIRS = {"node_modules", ".versions", ".git"}
SOURCE_EXT = {".js", ".mjs", ".cjs", ".jsx"}

_NPM_NAME = re.compile(r"^(@[a-z0-9-~][a-z0-9-._~]*/)?[a-z0-9-~][a-z0-9-._~]*$")
_REQUIRE = re.compile(r"""require\(\s*['"]([^'"]+)['"]\s*\)""")
_IMPORT = re.compile(r"""(?:import\s[^'"]*?from\s*|import\s*)['"]([^'"]+)['"]""")


# Tasks run in parallel threads: serialise package.json writes / npm installs per project.
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(directory: Path) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(str(directory), threading.Lock())


def project_dir(project_id: str) -> Path:
    return GENERATED_CODE_DIR / project_id


def _package_root(spec: str) -> Optional[str]:
    """'express' -> 'express', '@scope/pkg/sub' -> '@scope/pkg', './x' -> None."""
    spec = spec.strip()
    if not spec or spec.startswith((".", "/", "node:")):
        return None
    parts = spec.split("/")
    root = "/".join(parts[:2]) if spec.startswith("@") else parts[0]
    if root in NODE_BUILTINS or not _NPM_NAME.match(root):
        return None
    return root


def walk_files(directory: Path):
    """All files under directory, never descending into node_modules / .versions / .git."""
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            yield Path(root) / name


def iter_source_files(directory: Path):
    for path in walk_files(directory):
        if path.suffix in SOURCE_EXT:
            yield path


def scan_dependencies(directory: Path) -> set[str]:
    """Package names imported by the project's JavaScript files."""
    found: set[str] = set()
    if not directory.exists():
        return found
    for path in iter_source_files(directory):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for regex in (_REQUIRE, _IMPORT):
            for match in regex.finditer(text):
                root = _package_root(match.group(1))
                if root:
                    found.add(root)
    return found


def find_entry_point(directory: Path) -> Optional[str]:
    """The file that starts the backend: server.js, else any root-level file that calls listen()."""
    for name in ("server.js", "app.js", "index.js", "main.js"):
        if (directory / name).is_file():
            text = (directory / name).read_text(encoding="utf-8", errors="ignore")
            if ".listen(" in text or name == "server.js":
                return name
    for path in sorted(iter_source_files(directory)):
        rel = path.relative_to(directory)
        if "tests" in rel.parts or "public" in rel.parts:
            continue
        if ".listen(" in path.read_text(encoding="utf-8", errors="ignore"):
            return rel.as_posix()
    return None


def has_static_frontend(directory: Path) -> Optional[Path]:
    for candidate in (directory / "public", directory):
        if (candidate / "index.html").is_file():
            return candidate
    return None


def ensure_package_json(directory: Path) -> dict:
    """
    Create/refresh package.json: scanned dependencies, a start script and a
    test script. Keeps anything already in an existing package.json.
    """
    pkg_path = directory / "package.json"
    pkg: dict = {}
    if pkg_path.is_file():
        try:
            pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
        except Exception:
            pkg = {}

    deps = dict(pkg.get("dependencies") or {})
    for name in sorted(scan_dependencies(directory)):
        deps.setdefault(name, PINNED.get(name, "latest"))
    # jest + supertest are only needed by the tests, keep them out of runtime deps
    deps.pop("jest", None)
    dev = dict(pkg.get("devDependencies") or {})
    if deps.pop("supertest", None) or "supertest" in dev:
        dev["supertest"] = PINNED["supertest"]

    entry = find_entry_point(directory)
    scripts = dict(pkg.get("scripts") or {})
    if entry:
        scripts["start"] = f"node {entry}"
    scripts.setdefault("test", "jest")

    pkg.update({
        "name": pkg.get("name") or "codonova-project",
        "version": pkg.get("version") or "1.0.0",
        "private": True,
        "main": entry or pkg.get("main") or "server.js",
        "scripts": scripts,
        "dependencies": dict(sorted(deps.items())),
    })
    if dev:
        pkg["devDependencies"] = dict(sorted(dev.items()))
    pkg_path.write_text(json.dumps(pkg, indent=2) + "\n", encoding="utf-8")
    return pkg


def _deps_fingerprint(pkg: dict) -> str:
    blob = json.dumps([pkg.get("dependencies"), pkg.get("devDependencies")], sort_keys=True)
    return hashlib.sha1(blob.encode()).hexdigest()


def _npm_install_locked(
    directory: Path,
    log: Optional[Callable[[str], None]] = None,
    timeout: int = 300,
) -> tuple[bool, str]:
    """
    Install the project's dependencies. Skips the install when nothing changed
    since the last successful one. Returns (ok, last output lines).
    """
    log = log or (lambda line: None)
    pkg = ensure_package_json(directory)
    fingerprint = _deps_fingerprint(pkg)
    stamp = directory / "node_modules" / ".codonova-deps"

    if stamp.is_file() and stamp.read_text().strip() == fingerprint:
        log("Dependencies already installed.")
        return True, "up to date"

    if not (pkg.get("dependencies") or pkg.get("devDependencies")):
        log("No dependencies to install.")
        return True, "no dependencies"

    log(f"Installing: {', '.join(list((pkg.get('dependencies') or {}).keys()) + list((pkg.get('devDependencies') or {}).keys()))}")
    cmd = ["npm", "install", "--no-audit", "--no-fund", "--loglevel=error"]
    tail: list[str] = []
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(directory), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            env={**os.environ, "NPM_CONFIG_UPDATE_NOTIFIER": "false",
                 "NPM_CONFIG_FUND": "false", "NPM_CONFIG_AUDIT": "false"},
        )
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                line = line.rstrip()
                if line:
                    tail.append(line)
                    log(line)
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            return False, "npm install timed out"
    except FileNotFoundError:
        return False, "npm is not installed"
    except Exception as exc:
        return False, str(exc)

    if proc.returncode != 0:
        return False, "\n".join(tail[-8:]) or f"npm install failed ({proc.returncode})"

    (directory / "node_modules").mkdir(exist_ok=True)
    stamp.write_text(fingerprint)
    log("Dependencies installed.")
    return True, "installed"


def npm_install(
    directory: Path,
    log: Optional[Callable[[str], None]] = None,
    timeout: int = 300,
) -> tuple[bool, str]:
    """Thread-safe wrapper: only one install per project directory at a time."""
    with _lock_for(directory):
        return _npm_install_locked(directory, log, timeout)
