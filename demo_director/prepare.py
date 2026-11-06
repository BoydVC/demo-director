"""Prepare a public app checkout so Demo Director can open a real page.

BoydVC apps often ship as an unfilled ``web/index.html`` plus ``build.py``,
``export_static.py``, or a small ``server.py`` that serves ``/api/*``. A plain
``python -m http.server`` of the checkout then looks blank.

Fleet demos default to committed fixtures (``DEMO_DIRECTOR_FIXTURES`` unset
or ``1``). Scrubbed ``data/``, ``web/``, and ``dist/`` are served as
committed. ``collect.py``, ``build.py``, ``export_snapshot.py``, ``npm run
build``, and a server that would rebuild those trees from live box caches
are not run. ``DEMO_DIRECTOR_FIXTURES=0`` (or
``prefer_committed_fixtures=False``) opts into that live collect for private
local use: the common build hooks run, a baked ``dist/`` snapshot is
preferred when one appears, and ``server.py`` / ``npm start`` / Vite preview
start when the page needs a local API. Vite apps then get ``npm ci`` +
``npm run build`` + ``vite preview``. Seedable dashboards get fixture env
(``FOUNDRY_SEED``) or a ``demo`` script.
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

PRIVATE_PATH_MARKERS = (
    "/workspace/hspt",
    "/workspace/tokenmax/hspt",
    "/cursor/stores/",
    "HQ_HSPT_ROOT",
)

# Live box caches. ``/workspace/tokenmax`` covers newspaper PDFs under that tree.
# A hit means "starting this process would re-ingest dirty data", not "skip in
# live-collect mode" — live collect keeps the previous hook behavior.
LIVE_CACHE_MARKERS = (
    "/workspace/tokenmax",
    "/workspace/hspt",
    "/home/box/agent-data",
    "agent-data",
    "/cursor/stores/",
    "HQ_HSPT_ROOT",
)

FIXTURES_ENV = "DEMO_DIRECTOR_FIXTURES"
# Explicit opt-in to live collect. Anything else, including unset, stays on.
_FIXTURES_OFF = frozenset({"0", "false", "no", "off", "live"})


def reads_private_sources(path: Path) -> bool:
    """True when a script would rebuild from private box files (e.g. HSPT sheets)."""
    try:
        head = path.read_text(encoding="utf-8", errors="ignore")[:80_000]
    except OSError:
        return False
    if any(marker in head for marker in PRIVATE_PATH_MARKERS):
        return True
    pattern = r"(?:import|from)\s+(build_bank)" + r"\b"
    for match in re.finditer(pattern, head):
        sibling = path.parent / f"{match.group(1)}.py"
        if sibling.is_file() and sibling.resolve() != path.resolve():
            if reads_private_sources(sibling):
                return True
    return False


def _read_head(path: Path, limit: int = 80_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")[:limit]
    except OSError:
        return ""


def use_committed_fixtures(env: dict | None = None, flag: bool | None = None) -> bool:
    """True when demos must serve committed ``data/``, ``web/``, and ``dist/``.

    An explicit ``flag`` wins. Otherwise ``DEMO_DIRECTOR_FIXTURES`` is read
    from ``env`` (or ``os.environ`` when ``env`` is None). Unset is on, so a
    fleet render fail-closes toward scrubbed bytes. ``0``, ``false``, ``no``,
    ``off``, and ``live`` opt into live collect.
    """
    if flag is not None:
        return bool(flag)
    source = os.environ if env is None else env
    raw = source.get(FIXTURES_ENV)
    if raw is None:
        return True
    return str(raw).strip().lower() not in _FIXTURES_OFF


_REWRITE_SCRIPT_RE = re.compile(
    r"(?:^|[\s'\"`(=/])(?:collect|build_data|build_snapshot|build|export_static|export_snapshot|render)\.py\b"
)
_COLLECT_IMPORT_RE = re.compile(
    r"(?m)^\s*(?:import|from)\s+(?:collect|export_snapshot|export_static|build_data|build_snapshot)\b"
)
_LAUNCH_RE = re.compile(
    r"\b(?:subprocess|Popen|os\.system|run_path|check_call|check_output"
    r"|child_process|execSync|execFile|spawnSync)\b|\bexec\b|\bspawn\b"
)


def text_rewrites_committed_caches(text: str) -> bool:
    """True when a source file would rebuild committed trees on import or start.

    A comment that merely names ``build.py`` does not count. A live-cache path,
    an import of a collector, or a launcher plus a rewrite script does.
    """
    if not text:
        return False
    if any(marker in text for marker in LIVE_CACHE_MARKERS):
        return True
    if _COLLECT_IMPORT_RE.search(text):
        return True
    if _REWRITE_SCRIPT_RE.search(text) and _LAUNCH_RE.search(text):
        return True
    return False


def command_text_rewrites(text: str) -> bool:
    """True when a command line or npm script itself runs a rewrite or live cache."""
    if not text:
        return False
    if any(marker in text for marker in LIVE_CACHE_MARKERS):
        return True
    return _REWRITE_SCRIPT_RE.search(text) is not None


def server_rebuilds_on_start(path: Path, *, _seen: set[Path] | None = None, _depth: int = 0) -> bool:
    """True when launching ``path`` would rewrite ``data/``, ``web/``, or ``dist/``."""
    if _depth > 2 or not path.is_file():
        return False
    seen = _seen if _seen is not None else set()
    try:
        resolved = path.resolve()
    except OSError:
        return False
    if resolved in seen:
        return False
    seen.add(resolved)
    text = _read_head(path)
    if text_rewrites_committed_caches(text):
        return True
    if _depth >= 2:
        return False
    for name in re.findall(r"(?:import|from)\s+([A-Za-z_][\w]*)", text):
        sibling = path.parent / f"{name}.py"
        if sibling.is_file() and server_rebuilds_on_start(sibling, _seen=seen, _depth=_depth + 1):
            return True
    return False


def command_rewrites_on_start(root: Path, cmd: list[str]) -> bool:
    """True when ``cmd`` (or the local program it names) rebuilds committed caches."""
    blob = " ".join(cmd)
    scripts = npm_scripts(root)
    if cmd and cmd[0] == "npm":
        if "run" in cmd:
            idx = cmd.index("run")
            if idx + 1 < len(cmd):
                blob += "\n" + scripts.get(cmd[idx + 1], "")
        if "start" in cmd[:3]:
            blob += "\n" + scripts.get("start", "")
    if command_text_rewrites(blob):
        return True
    files: list[Path] = []
    for part in list(cmd) + re.findall(r"[\w./-]+\.(?:py|js|mjs|cjs|ts)", blob):
        candidate = Path(part)
        if not candidate.is_file():
            candidate = root / part
        if not candidate.is_file():
            continue
        if "node_modules" in candidate.parts:
            continue
        try:
            if not candidate.resolve().is_relative_to(root.resolve()):
                continue
        except OSError:
            continue
        files.append(candidate)
    seen: set[Path] = set()
    for candidate in files:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        if len(seen) > 8:
            break
        if candidate.suffix == ".py":
            # Python uses the tight detector so a comment that names build.py
            # does not sideline an API server. Imports are followed.
            if server_rebuilds_on_start(candidate):
                return True
            continue
        head = _read_head(candidate)
        # JS/TS entrypoints are the command. A rewrite script name or a live
        # cache path in the file is enough; they have no Python launcher word.
        if text_rewrites_committed_caches(head) or command_text_rewrites(head):
            return True
    return False


def stage_committed_data(root: Path) -> None:
    """Copy committed ``data/*.json`` into ``web/`` only when that file is absent.

    Never overwrites a scrubbed ``web/`` file.
    """
    import shutil

    web = root / "web"
    data = root / "data"
    if not web.is_dir() or not data.is_dir():
        return
    for src in data.glob("*.json"):
        dest = web / src.name
        if not dest.exists():
            shutil.copy2(src, dest)


# Order matters: data builders first, then page builders, then offline snapshots.
# ``collect.py`` is the live-cache ingest some apps run directly (others call it
# from ``build.py``). Fixtures mode skips this whole list.
BUILD_SCRIPTS = (
    "collect.py",
    "build_data.py",
    "build.py",
    "build_snapshot.py",
    "export_static.py",
    "export_snapshot.py",
    "render.py",
)

PORT_ENV_KEYS = (
    "PORT",
    "DMS_PORT",
    "SMC_PORT",
    "MUSEUM_PORT",
    "HQ_PORT",
    "HUB_PORT",
    "SB_PORT",
    "TN_PORT",
    "WS_PORT",
    "TBA_PORT",
    "RR_PORT",
    "KCS_PORT",
    "STARLINK_GLOBE_PORT",
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



def fill_html_placeholders(root: Path) -> list[str]:
    """Inline sibling CSS/JS into common template markers when build.py did not.

    Handles ``/*__CSS__*/`` / ``/*__APP__*/`` and HTML comments ``<!--CSS-->`` /
    ``<!--JS-->``. Returns the relative paths that were rewritten.
    """
    rewritten: list[str] = []
    candidates: list[Path] = []
    for rel in ("web/index.html", "index.html", "public/index.html", "src/template.html"):
        path = root / rel
        if path.is_file():
            candidates.append(path)
    for path in candidates:
        try:
            html = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        original = html
        parent = path.parent
        css_files = sorted(
            [p for p in parent.glob("*.css") if p.is_file()],
            key=lambda p: (0 if "app" in p.name or "museum" in p.name or "style" in p.name else 1, p.name),
        )
        js_files = sorted(
            [p for p in parent.glob("*.js") if p.is_file() and "test" not in p.name],
            key=lambda p: (0 if "app" in p.name or "museum" in p.name else 1, p.name),
        )
        if "/*__CSS__*/" in html and css_files:
            html = html.replace("/*__CSS__*/", css_files[0].read_text(encoding="utf-8", errors="ignore"), 1)
        if "/*__APP__*/" in html and js_files:
            js = js_files[0].read_text(encoding="utf-8", errors="ignore").replace("</script", "<\\/script")
            html = html.replace("/*__APP__*/", js, 1)
        if "<!--CSS-->" in html and css_files:
            css = css_files[0].read_text(encoding="utf-8", errors="ignore")
            html = html.replace("<!--CSS-->", f"<style>\n{css}\n</style>", 1)
        if "<!--JS-->" in html and js_files:
            js = js_files[0].read_text(encoding="utf-8", errors="ignore").replace("</script", "<\\/script")
            html = html.replace("<!--JS-->", f"<script>\n{js}\n</script>", 1)
        # museum-style: comment CSS filled but external script still referenced — leave as-is
        # If still no <script> body and a museum.js/app.js exists next to a canvas page, append it.
        if html != original and "<script" not in html.lower() and js_files:
            js = js_files[0].read_text(encoding="utf-8", errors="ignore").replace("</script", "<\\/script")
            if "</body>" in html:
                html = html.replace("</body>", f"<script>\n{js}\n</script></body>", 1)
            else:
                html += f"<script>\n{js}\n</script>"
        if html == original:
            continue
        path.write_text(html, encoding="utf-8")
        rewritten.append(path.relative_to(root).as_posix())
        # Stage into site/ when server.py serves an empty site directory.
        site = root / "site"
        if site.is_dir() and not (site / "index.html").is_file() and path.name == "index.html":
            (site / "index.html").write_text(html, encoding="utf-8")
            rewritten.append("site/index.html")
    return rewritten


def run_build_hooks(
    root: Path,
    env: dict | None = None,
    timeout: int = 90,
    *,
    prefer_committed_fixtures: bool | None = None,
) -> list[str]:
    """Run known seed/build/export scripts. Returns the names that exited 0.

    When committed fixtures are on (the default), rewrite scripts and
    ``npm run build`` are skipped so live caches cannot replace scrubbed
    ``data/``, ``web/``, or ``dist/``. Sibling CSS/JS placeholders are still
    inlined from files already in the checkout.
    """
    env = dict(env or os.environ)
    env.setdefault("PYTHONUNBUFFERED", "1")
    fixtures = use_committed_fixtures(env, prefer_committed_fixtures)
    env[FIXTURES_ENV] = "1" if fixtures else "0"
    ran: list[str] = []
    if fixtures:
        filled = fill_html_placeholders(root)
        if filled:
            ran.append("fill:placeholders")
        return ran
    (root / "dist").mkdir(parents=True, exist_ok=True)
    for name in BUILD_SCRIPTS:
        script = root / name
        if not script.is_file():
            continue
        if reads_private_sources(script):
            # Never rebuild public demos from private tutor sheets / inbox trees.
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
    filled = fill_html_placeholders(root)
    if filled:
        ran.append("fill:placeholders")
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
        port = out.get("PORT") or out.get("SCHOOL_PULSE_PORT") or "0"
        data = root / f".demo-director-data-{port}"
        data.mkdir(parents=True, exist_ok=True)
        out.setdefault("SCHOOL_PULSE_DATA", str(data))
    return out


def discover_port_env_keys(root: Path | None) -> tuple[str, ...]:
    """Union of known PORT keys and any ``*PORT`` names referenced in server.py."""
    keys = list(PORT_ENV_KEYS)
    if root is None:
        return tuple(keys)
    server = root / "server.py"
    if not server.is_file():
        return tuple(keys)
    try:
        blob = server.read_text(encoding="utf-8", errors="ignore")[:40_000]
    except OSError:
        return tuple(keys)
    found = re.findall(r'["\']([A-Z][A-Z0-9_]*PORT)["\']', blob)
    for key in found:
        if key not in keys:
            keys.append(key)
    return tuple(keys)


def _port_env(port: int, root: Path | None = None) -> dict:
    env = os.environ.copy()
    for key in discover_port_env_keys(root):
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
            if "Directory listing for" in raw:
                return False
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


def _is_next_app(root: Path) -> bool:
    return any(
        (root / name).is_file()
        for name in ("next.config.ts", "next.config.js", "next.config.mjs", "next.config.cjs")
    ) or (root / ".next").is_dir()


def _npm_serve_cmd(root: Path, port: int) -> list[str] | None:
    """Pick demo/dashboard/start/preview, skipping raw vite/next dev servers."""
    scripts = npm_scripts(root)
    if not scripts:
        return None
    # Next.js App Router demos often ship a one-shot ``demo`` CLI that is not
    # an HTTP server. Prefer ``next start`` once ``.next`` exists.
    if _is_next_app(root) and (root / ".next").is_dir():
        return ["npx", "--no-install", "next", "start", "-H", "127.0.0.1", "-p", str(port)]
    # Preference: seeded demo, then dashboard API, then start, then preview.
    order = ("demo", "dashboard", "start", "preview", "serve")
    for name in order:
        start = scripts.get(name)
        if not isinstance(start, str) or not start.strip():
            continue
        low = start.lower()
        # Never launch a cold Vite/Next *dev* server from here; preview is separate.
        # A production ``next start`` is fine once ``.next`` exists from npm run build.
        if name != "preview" and ("vite" in low and "preview" not in low):
            continue
        if "next dev" in low:
            continue
        if "next start" in low and not (root / ".next").is_dir():
            continue
        if name == "demo":
            # Prefer the compiled CLI directly so forked fleet workers do not
            # depend on npm's script runner.
            cli = root / "dist" / "src" / "cli.js"
            if cli.is_file():
                return ["node", "--disable-warning=ExperimentalWarning", str(cli),
                        "demo", "--port", str(port), "--host", "127.0.0.1"]
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
            if "next start" in low:
                return ["npx", "--no-install", "next", "start", "-H", "127.0.0.1", "-p", str(port)]
            if start.strip().startswith("node "):
                # Append --port when the CLI looks like school-pulse serve.
                parts = start.strip().split()
                if "serve" in parts or parts[-1].endswith("cli.js"):
                    return parts + ["--port", str(port), "--host", "127.0.0.1"]
                return parts
            # npm start -- --port N covers parseArgs-style CLIs.
            return ["npm", "start", "--silent", "--", "--port", str(port), "--host", "127.0.0.1"]
    return None


def start_server(
    root: Path,
    port: int,
    entry: str | None = None,
    *,
    prefer_committed_fixtures: bool | None = None,
) -> RunningServer:
    """Start the best local server for ``root`` and return how to open it.

    Committed-fixtures mode does not launch a server that would rewrite
    ``data/``, ``web/``, or ``dist/`` on start. Those checkouts fall through
    to the committed HTML snapshot.
    """
    env = _port_env(port, root)
    fixtures = use_committed_fixtures(env, prefer_committed_fixtures)
    env[FIXTURES_ENV] = "1" if fixtures else "0"
    if entry is None:
        entry = find_entry(root)

    server_py = root / "server.py"
    rebuilds = bool(server_py.is_file() and fixtures and server_rebuilds_on_start(server_py))
    private = bool(server_py.is_file() and reads_private_sources(server_py))
    if server_py.is_file() and not private and not rebuilds:
        proc = _spawn([sys.executable, "server.py"], root, env)
        if wait_port(port, timeout=15):
            url = f"http://127.0.0.1:{port}/"
            if wait_http_ready(url, timeout=8):
                return RunningServer(proc, port, url, "server.py", entry)
            # Bound but empty/dir-listing (e.g. museum site/ before build) — fall through.
            _stop_proc(proc)
        else:
            _stop_proc(proc)
    elif private or rebuilds:
        # Stage checked-in JSON next to the static web UI so http.server can
        # satisfy /bank.json without rebuilding from private sheets or live caches.
        stage_committed_data(root)

    npm_cmd = _npm_serve_cmd(root, port)
    if npm_cmd is not None and fixtures and command_rewrites_on_start(root, npm_cmd):
        npm_cmd = None
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
        # Live collect may bake a preview bundle. Fixtures mode keeps the
        # committed dist and must not run ``npm run build`` here.
        if not fixtures and (not find_entry(root) or is_vite_app(root)):
            if "npm:build" not in run_build_hooks(root, env=env, prefer_committed_fixtures=False):
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


def prepare_and_serve(
    root: Path,
    port: int,
    env: dict | None = None,
    *,
    prefer_committed_fixtures: bool | None = None,
) -> tuple[RunningServer, list[str]]:
    """Serve ``root``. Caller must ``server.stop()``.

    Committed fixtures are the default: collect/build/export hooks that
    rewrite ``data/``, ``web/``, or ``dist/`` do not run. Pass
    ``prefer_committed_fixtures=False`` or set ``DEMO_DIRECTOR_FIXTURES=0``
    to opt into live collect.
    """
    merged = _port_env(port, root)
    if env:
        merged.update(env)
        merged = seed_env(root, merged)
    fixtures = use_committed_fixtures(merged, prefer_committed_fixtures)
    merged[FIXTURES_ENV] = "1" if fixtures else "0"
    built = run_build_hooks(root, env=merged, prefer_committed_fixtures=fixtures)
    entry = find_entry(root)
    server = start_server(root, port, entry=entry, prefer_committed_fixtures=fixtures)
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
