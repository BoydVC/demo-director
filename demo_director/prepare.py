"""Prepare a public app checkout so Demo Director can open a real page.

BoydVC apps often ship as an unfilled ``web/index.html`` plus ``build.py``,
``export_static.py``, or a small ``server.py`` that serves ``/api/*``. A plain
``python -m http.server`` of the checkout then looks blank. This module runs
the common build hooks, prefers a baked ``dist/`` snapshot when one appears,
and starts ``server.py`` / ``npm start`` / Vite preview when the page needs a
local API. Vite apps get ``npm ci`` + ``npm run build`` + ``vite preview``.
Seedable dashboards get fixture env (``FOUNDRY_SEED``) or a ``demo`` script.
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
from urllib.error import URLError
from urllib.request import urlopen

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
    "FOUNDRY_PORT",
    "SCHOOL_PULSE_PORT",
    "CUTAWAY_PORT",
    "BOOSTER_PORT",
    "ARENA_WALL_PORT",
    "HSPT_PORT",
)

# Phrases that mean the shell is up but the data/API has not landed yet.
LOADING_PATTERNS = (
    r"loading the household",
    r"loading[.…\s]*$",
    r"^loading\b",
    r"please wait",
    r"fetching\b",
    r"still loading",
)

VITE_CONFIG_CANDIDATES = (
    "vite.config.ts",
    "vite.config.js",
    "vite.config.mjs",
    "vite.config.cjs",
    "web/vite.config.ts",
    "web/vite.config.js",
    "web/vite.config.mjs",
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
    # Vite often writes to web/dist when config lives under web/.
    web_dist = root / "web" / "dist"
    if web_dist.is_dir():
        built = [
            path for path in web_dist.glob("*.html")
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


def npm_scripts(root: Path) -> dict[str, str]:
    pkg = root / "package.json"
    if not pkg.is_file():
        return {}
    try:
        scripts = json.loads(pkg.read_text()).get("scripts") or {}
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(scripts, dict):
        return {}
    return {str(k): str(v) for k, v in scripts.items() if isinstance(v, str)}


def vite_config(root: Path) -> str | None:
    for rel in VITE_CONFIG_CANDIDATES:
        if (root / rel).is_file():
            return rel
    return None


def is_vite_app(root: Path) -> bool:
    if vite_config(root) is not None:
        return True
    scripts = npm_scripts(root)
    blob = " ".join(scripts.values()).lower()
    return "vite" in blob


def ensure_npm_install(root: Path, env: dict | None = None, timeout: int = 240) -> bool:
    """Run ``npm ci`` when lockfile exists and ``node_modules`` is missing."""
    if (root / "node_modules").is_dir():
        return True
    if not (root / "package.json").is_file():
        return False
    env = dict(env or os.environ)
    env.setdefault("npm_config_audit", "false")
    env.setdefault("npm_config_fund", "false")
    cmd = ["npm", "ci", "--ignore-scripts"]
    if not (root / "package-lock.json").is_file():
        cmd = ["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund"]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(root),
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
    return proc.returncode == 0 and (root / "node_modules").is_dir()


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
    scripts = npm_scripts(root)
    needs_node = bool(scripts) and (
        is_vite_app(root)
        or any(key in scripts for key in ("build", "demo", "dashboard", "start", "preview"))
    )
    if needs_node:
        if ensure_npm_install(root, env=env, timeout=max(timeout * 3, 240)):
            ran.append("npm:ci")
    if scripts and "build" in scripts and (root / "node_modules").is_dir():
        build_timeout = timeout * 3 if is_vite_app(root) else timeout * 2
        try:
            proc = subprocess.run(
                ["npm", "run", "build", "--silent"],
                cwd=str(root),
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=build_timeout,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            proc = None
        if proc is not None and proc.returncode == 0:
            ran.append("npm:build")
    return ran


def seed_env(root: Path, env: dict) -> dict:
    """Turn on in-repo synthetic seeds when the checkout looks seedable."""
    out = dict(env)
    scripts = npm_scripts(root)
    has_fixtures = (root / "fixtures").is_dir()
    # Idea Foundry and similar: dashboard reads FOUNDRY_SEED.
    if "dashboard" in scripts or (root / "public" / "app.js").is_file():
        out.setdefault("FOUNDRY_SEED", "1")
        out.setdefault("FOUNDRY_FRESH", "1")
        out.setdefault("FOUNDRY_PLANNER", "fixture")
        if has_fixtures and (root / "fixtures" / "planner-records.json").is_file():
            out.setdefault(
                "FOUNDRY_PLANNER_RECORD",
                str(root / "fixtures" / "planner-records.json"),
            )
    # School Pulse demo seeds its own sqlite; keep data under the checkout.
    if "demo" in scripts and has_fixtures:
        data = root / ".demo-director-data"
        data.mkdir(parents=True, exist_ok=True)
        out.setdefault("SCHOOL_PULSE_DATA", str(data))
    return out


def _port_env(port: int, root: Path | None = None) -> dict:
    env = os.environ.copy()
    for key in PORT_ENV_KEYS:
        env[key] = str(port)
    env["HOST"] = "127.0.0.1"
    env["PYTHONUNBUFFERED"] = "1"
    if root is not None:
        env = seed_env(root, env)
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


_PORT_LOCK_DIR = Path(os.environ.get("DEMO_DIRECTOR_PORT_LOCKS", "/tmp/demo-director-ports"))


def pick_free_port(low: int = 4600, high: int = 4699) -> int:
    """Claim a port in the Demo Director render band with an exclusive lock file."""
    _PORT_LOCK_DIR.mkdir(parents=True, exist_ok=True)
    for port in range(low, high + 1):
        lock = _PORT_LOCK_DIR / f"{port}.lock"
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            continue
        sock = socket.socket()
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", port))
        except OSError:
            os.close(fd)
            try:
                lock.unlink()
            except OSError:
                pass
            continue
        finally:
            sock.close()
        try:
            os.write(fd, str(os.getpid()).encode())
        finally:
            os.close(fd)
        return port
    raise RuntimeError(f"no free port in {low}-{high}")


def release_port(port: int) -> None:
    lock = _PORT_LOCK_DIR / f"{port}.lock"
    try:
        lock.unlink()
    except OSError:
        pass


def page_looks_loading(text: str) -> bool:
    """True when visible copy is still a loading shell."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return True
    blob = "\n".join(lines[:8])
    for pattern in LOADING_PATTERNS:
        if re.search(pattern, blob, re.IGNORECASE | re.MULTILINE):
            return True
    return False


def wait_http_ready(url: str, timeout: float = 20) -> bool:
    """Poll until the URL returns HTML that is not a loading shell."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urlopen(url, timeout=2) as resp:
                raw = resp.read(120_000).decode("utf-8", "replace")
            # Strip tags lightly for phrase checks.
            text = re.sub(r"<script[\s\S]*?</script>", " ", raw, flags=re.I)
            text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text).strip()
            if text and not page_looks_loading(text):
                return True
        except (URLError, OSError, TimeoutError):
            pass
        time.sleep(0.35)
    return False


@dataclass
class RunningServer:
    proc: subprocess.Popen
    port: int
    url: str
    mode: str  # "server.py" | "npm-start" | "vite-preview" | "http.server"
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
        release_port(self.port)


def _spawn(cmd: list[str], root: Path, env: dict) -> subprocess.Popen:
    return subprocess.Popen(
        cmd,
        cwd=str(root),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _stop_proc(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except OSError:
        try:
            proc.kill()
        except OSError:
            pass


def _vite_preview_cmd(root: Path, port: int) -> list[str] | None:
    config = vite_config(root)
    if config is None and not is_vite_app(root):
        return None
    if not (root / "node_modules").is_dir():
        return None
    cmd = [
        "npx", "--no-install", "vite", "preview",
        "--host", "127.0.0.1",
        "--port", str(port),
        "--strictPort",
    ]
    if config is not None:
        cmd.extend(["--config", config])
    return cmd


def _npm_serve_cmd(root: Path, port: int) -> list[str] | None:
    """Pick demo/dashboard/start/preview, skipping raw vite/next dev servers."""
    scripts = npm_scripts(root)
    if not scripts:
        return None
    # Preference: seeded demo, then dashboard API, then start, then preview.
    order = ("demo", "dashboard", "start", "preview", "serve")
    for name in order:
        start = scripts.get(name)
        if not isinstance(start, str) or not start.strip():
            continue
        low = start.lower()
        # Never launch a cold Vite/Next dev server from here; preview is separate.
        if name != "preview" and ("vite" in low or "next dev" in low or "next start" in low):
            if "preview" not in low:
                continue
        if name == "demo":
            # school-pulse and friends take --port on the CLI, not always via PORT.
            return ["npm", "run", "demo", "--silent", "--", "--port", str(port), "--host", "127.0.0.1"]
        if name == "dashboard":
            if start.strip().startswith("tsx ") or start.strip().startswith("node "):
                parts = start.strip().split()
                return parts  # env carries PORT / FOUNDRY_PORT
            return ["npm", "run", "dashboard", "--silent"]
        if name == "preview":
            if "vite" in low:
                return _vite_preview_cmd(root, port)
            return ["npm", "run", "preview", "--silent", "--", "--host", "127.0.0.1", "--port", str(port)]
        if name in {"start", "serve"}:
            if start.strip().startswith("node "):
                # Append --port when the CLI looks like school-pulse serve.
                parts = start.strip().split()
                if "serve" in parts or parts[-1].endswith("cli.js"):
                    return parts + ["--port", str(port), "--host", "127.0.0.1"]
                return parts
            # npm start -- --port N covers parseArgs-style CLIs.
            return ["npm", "start", "--silent", "--", "--port", str(port), "--host", "127.0.0.1"]
    return None


def start_server(root: Path, port: int, entry: str | None = None) -> RunningServer:
    """Start the best local server for ``root`` and return how to open it."""
    env = _port_env(port, root)
    if entry is None:
        entry = find_entry(root)

    if (root / "server.py").is_file():
        proc = _spawn([sys.executable, "server.py"], root, env)
        if wait_port(port, timeout=15):
            url = f"http://127.0.0.1:{port}/"
            wait_http_ready(url, timeout=8)
            return RunningServer(proc, port, url, "server.py", entry)
        _stop_proc(proc)

    npm_cmd = _npm_serve_cmd(root, port)
    if npm_cmd is not None:
        needs_modules = npm_cmd[0] in {"npm", "npx", "tsx"} or (
            npm_cmd[0] == "node" and not (root / "node_modules").is_dir() and "node_modules" in " ".join(npm_cmd)
        )
        if npm_cmd[0] in {"npm", "npx"} and not (root / "node_modules").is_dir():
            ensure_npm_install(root, env=env)
        if (root / "node_modules").is_dir() or npm_cmd[0] == "node":
            # tsx must resolve from node_modules/.bin — prefer npx when bare.
            if npm_cmd[0] == "tsx":
                npm_cmd = ["npx", "--no-install", *npm_cmd]
            proc = _spawn(npm_cmd, root, env)
            if wait_port(port, timeout=20):
                url = f"http://127.0.0.1:{port}/"
                wait_http_ready(url, timeout=12)
                mode = "vite-preview" if "vite" in " ".join(npm_cmd) else "npm-start"
                return RunningServer(proc, port, url, mode, entry)
            _stop_proc(proc)

    vite_cmd = _vite_preview_cmd(root, port)
    if vite_cmd is not None:
        # Ensure a build exists before preview.
        if not find_entry(root) or is_vite_app(root):
            if "npm:build" not in run_build_hooks(root, env=env):
                # Still try preview if web/dist or dist already has html.
                pass
        proc = _spawn(vite_cmd, root, env)
        if wait_port(port, timeout=20):
            url = f"http://127.0.0.1:{port}/"
            wait_http_ready(url, timeout=10)
            return RunningServer(proc, port, url, "vite-preview", entry)
        _stop_proc(proc)

    if entry is None:
        raise FileNotFoundError(f"no html entry under {root}")
    # Serve the parent of the entry when it lives in dist/, so /index.html works
    # for single-file snapshots and relative assets resolve.
    entry_path = root / entry
    cwd = entry_path.parent if entry_path.parent != root else root
    rel = entry_path.name if cwd != root else entry
    proc = _spawn(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        cwd,
        env,
    )
    if not wait_port(port):
        _stop_proc(proc)
        raise RuntimeError(f"http.server did not bind :{port}")
    return RunningServer(proc, port, f"http://127.0.0.1:{port}/{rel}", "http.server", entry)


def prepare_and_serve(root: Path, port: int, env: dict | None = None) -> tuple[RunningServer, list[str]]:
    """Build hooks, then start a server. Caller must ``server.stop()``."""
    merged = _port_env(port, root)
    if env:
        merged.update(env)
        merged = seed_env(root, merged)
    built = run_build_hooks(root, env=merged)
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
