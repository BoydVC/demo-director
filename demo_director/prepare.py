"""Prepare a public app checkout so Demo Director can open a real page.

BoydVC apps often ship as an unfilled ``web/index.html`` plus ``build.py``,
``export_static.py``, or a small ``server.py`` that serves ``/api/*``. A plain
``python -m http.server`` of the checkout then looks blank. This module runs
the common build hooks, prefers a baked ``dist/`` snapshot when one appears,
and starts ``server.py`` / ``npm start`` when the page needs a local API.
"""
from __future__ import annotations

import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

SKIP_DIRS = {".git", "node_modules", "vendor", ".venv", "venv"}

# Order matters: data builders first, then page builders, then offline snapshots.
BUILD_SCRIPTS = (
    "build_data.py",
    "build.py",
    "build_snapshot.py",
    "export_static.py",
    "export_snapshot.py",
)

PORT_ENV_KEYS = (
    "PORT",
    "DMS_PORT",
    "SMC_PORT",
    "MUSEUM_PORT",
    "HQ_PORT",
    "BOT_ISLAND_3D_PORT",
    "BOT_ISLAND_ARCADE_PORT",
    "CHELSEA_TM_PORT",
    "TOKEN_PACE_PORT",
    "LLM_ADVISOR_PORT",
    "FLEET_YEARBOOK_PORT",
)


def _placeholder(path: Path) -> bool:
    try:
        head = path.read_text(encoding="utf-8", errors="ignore")[:8000]
    except OSError:
        return False
    return "/*__CSS__*/" in head or "/*__APP__*/" in head


def find_entry(root: Path) -> str | None:
    """Relative HTML path to open. A built dist file beats an unfilled template."""
    dist = root / "dist"
    if dist.is_dir():
        built = [
            path for path in dist.glob("*.html")
            if path.is_file() and not _placeholder(path)
        ]
        if built:
            built.sort(key=lambda path: path.stat().st_size, reverse=True)
            return built[0].relative_to(root).as_posix()
    preferred = (
        "index.html",
        "web/index.html",
        "public/index.html",
        "dist/index.html",
        "static/index.html",
        "demo.html",
        "docs/index.html",
        "app/index.html",
        "src/index.html",
        "site/index.html",
    )
    found: list[Path] = []
    for rel in preferred:
        path = root / rel
        if path.is_file():
            found.append(path)
    if not found:
        found = [
            path for path in root.rglob("*.html")
            if not any(part in SKIP_DIRS for part in path.parts)
        ]
    if not found:
        return None
    found.sort(key=lambda path: (
        1 if _placeholder(path) else 0,
        len(path.relative_to(root).parts),
        len(str(path)),
    ))
    return found[0].relative_to(root).as_posix()


def run_build_hooks(root: Path, env: dict | None = None, timeout: int = 90) -> list[str]:
    """Run known seed/build/export scripts. Returns the names that exited 0."""
    env = dict(env or os.environ)
    env.setdefault("PYTHONUNBUFFERED", "1")
    ran: list[str] = []
    (root / "dist").mkdir(parents=True, exist_ok=True)
    for name in BUILD_SCRIPTS:
        script = root / name
        if not script.is_file():
            continue
        try:
            proc = subprocess.run(
                [sys.executable, name],
                cwd=str(root),
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            continue
        if proc.returncode == 0:
            ran.append(name)
    pkg = root / "package.json"
    if pkg.is_file():
        try:
            scripts = json.loads(pkg.read_text()).get("scripts") or {}
        except (OSError, json.JSONDecodeError):
            scripts = {}
        if isinstance(scripts, dict) and "build" in scripts:
            # Prefer an existing install; skip when node_modules is absent.
            if (root / "node_modules").is_dir():
                try:
                    proc = subprocess.run(
                        ["npm", "run", "build", "--silent"],
                        cwd=str(root),
                        env=env,
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        timeout=timeout * 2,
                    )
                except (subprocess.TimeoutExpired, FileNotFoundError):
                    proc = None
                if proc is not None and proc.returncode == 0:
                    ran.append("npm:build")
    return ran


def _port_env(port: int) -> dict:
    env = os.environ.copy()
    for key in PORT_ENV_KEYS:
        env[key] = str(port)
    env["HOST"] = "127.0.0.1"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def wait_port(port: int, timeout: float = 12) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), 0.2):
                return True
        except OSError:
            time.sleep(0.1)
    return False


@dataclass
class RunningServer:
    proc: subprocess.Popen
    port: int
    url: str
    mode: str  # "server.py" | "npm-start" | "http.server"
    entry: str | None

    def stop(self) -> None:
        try:
            os.killpg(self.proc.pid, signal.SIGTERM)
        except OSError:
            try:
                self.proc.kill()
            except OSError:
                pass
        try:
            self.proc.wait(timeout=2)
        except Exception:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except OSError:
                pass


def _npm_start_cmd(root: Path) -> list[str] | None:
    pkg = root / "package.json"
    if not pkg.is_file():
        return None
    try:
        scripts = json.loads(pkg.read_text()).get("scripts") or {}
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(scripts, dict):
        return None
    start = scripts.get("start")
    if not isinstance(start, str) or not start.strip():
        return None
    # Avoid vite/dev servers that need a fresh install of many packages.
    if "vite" in start or "next" in start:
        return None
    if start.strip().startswith("node "):
        return ["node", *start.strip().split()[1:]]
    return ["npm", "start", "--silent"]


def start_server(root: Path, port: int, entry: str | None = None) -> RunningServer:
    """Start the best local server for ``root`` and return how to open it."""
    env = _port_env(port)
    if entry is None:
        entry = find_entry(root)

    if (root / "server.py").is_file():
        proc = subprocess.Popen(
            [sys.executable, "server.py"],
            cwd=str(root),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        if wait_port(port):
            # App servers usually mount / at the UI; keep a path only when the
            # entry is a dist snapshot that the server does not rewrite.
            url = f"http://127.0.0.1:{port}/"
            return RunningServer(proc, port, url, "server.py", entry)
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except OSError:
            pass

    npm_cmd = _npm_start_cmd(root)
    if npm_cmd is not None and (
        (root / "node_modules").is_dir() or npm_cmd[0] == "node"
    ):
        proc = subprocess.Popen(
            npm_cmd,
            cwd=str(root),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        if wait_port(port):
            return RunningServer(proc, port, f"http://127.0.0.1:{port}/", "npm-start", entry)
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except OSError:
            pass

    if entry is None:
        raise FileNotFoundError(f"no html entry under {root}")
    # Serve the parent of the entry when it lives in dist/, so /index.html works
    # for single-file snapshots and relative assets resolve.
    entry_path = root / entry
    cwd = entry_path.parent if entry_path.parent != root else root
    rel = entry_path.name if cwd != root else entry
    proc = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        cwd=str(cwd),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    if not wait_port(port):
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except OSError:
            pass
        raise RuntimeError(f"http.server did not bind :{port}")
    return RunningServer(proc, port, f"http://127.0.0.1:{port}/{rel}", "http.server", entry)


def prepare_and_serve(root: Path, port: int, env: dict | None = None) -> tuple[RunningServer, list[str]]:
    """Build hooks, then start a server. Caller must ``server.stop()``."""
    built = run_build_hooks(root, env=env)
    entry = find_entry(root)
    server = start_server(root, port, entry=entry)
    return server, built


def detect_default_port(root: Path) -> int | None:
    """Best-effort default port from ``server.py`` when present."""
    path = root / "server.py"
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    match = re.search(
        r'os\.environ\.get\(\s*["\'][A-Z0-9_]*PORT["\']\s*,\s*["\']?(\d{2,5})["\']?\s*\)',
        text,
    )
    if match:
        return int(match.group(1))
    match = re.search(r"\bPORT\s*=\s*(\d{2,5})\b", text)
    if match:
        return int(match.group(1))
    return None
