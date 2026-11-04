#!/usr/bin/env python3
"""Serve a set of public repos and run init + batch at a fixed scale.

The set file is one ``repo group`` pair per line. Deny terms come from
``--deny`` (passed through as ``DEMO_DIRECTOR_DENY``) and are never stored
in this script. After a render reports ok, the page is replayed the way a
leak check would: title, captions, visible text, and click steps. A denied
term there is ``name-leak``, not ok.

Writes a JSON summary with one row per repo: ok, name-blocked, blank, other.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SKIP_DIRS = {".git", "node_modules", "vendor"}


def _read_set(path: Path) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        rows.append((parts[0], parts[1] if len(parts) > 1 else ""))
    return rows


def _placeholder(path: Path) -> bool:
    try:
        head = path.read_text(encoding="utf-8", errors="ignore")[:8000]
    except OSError:
        return False
    return "/*__CSS__*/" in head or "/*__APP__*/" in head


def _entry(root: Path) -> str | None:
    """Static page to open. A built dist file beats an unfilled template."""
    dist = root / "dist"
    if dist.is_dir():
        built = [path for path in dist.glob("*.html") if path.is_file() and not _placeholder(path)]
        if built:
            built.sort(key=lambda path: path.stat().st_size, reverse=True)
            return built[0].relative_to(root).as_posix()
    preferred = (
        "index.html",
        "web/index.html",
        "public/index.html",
        "dist/index.html",
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


def _wait_port(port: int, timeout: float = 8) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), 0.2):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def _load_json(raw: str) -> dict | None:
    raw = raw.strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(raw[start:end + 1])
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


def _bucket(status: str, detail: str) -> str:
    if status == "ok":
        return "ok"
    if status == "blank-page" or "blank-page" in detail:
        return "blank"
    if status == "pii-blocked" and "name:" in detail:
        return "name-blocked"
    if status == "name-leak":
        return "name-leak"
    return "other"


def _run(cmd: list[str], env: dict, timeout: int) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            cmd, env=env, cwd=ROOT, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout or ""
        err = exc.stderr or ""
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        if isinstance(err, bytes):
            err = err.decode("utf-8", "replace")
        return 124, out, err or "timeout"
    return proc.returncode, proc.stdout, proc.stderr


def _replay(shot: dict, terms: tuple[str, ...], port: int) -> list[str]:
    """Visible text plus the title card and captions. Word-boundary match."""
    from playwright.sync_api import sync_playwright

    from demo_director.guard import term_present

    url = re.sub(r"127\.0\.0\.1:\d+", f"127.0.0.1:{port}", str(shot.get("url", "")))
    chunks = [str(shot.get("title") or "")]
    for step in shot.get("steps") or []:
        if step.get("action") == "caption" and step.get("text"):
            chunks.append(str(step["text"]))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            page.goto(url, timeout=15000)
            page.wait_for_timeout(1200)
            chunks.append(page.inner_text("body"))
            for step in shot.get("steps") or []:
                if step.get("action") != "click" or not step.get("selector"):
                    continue
                try:
                    page.locator(step["selector"]).first.click(timeout=3000)
                    page.wait_for_timeout(500)
                    chunks.append(page.inner_text("body"))
                except Exception:
                    continue
        finally:
            browser.close()
    blob = "\n".join(chunks)
    return [term for term in terms if term_present(blob, term)]


def check_repo(repo: str, group: str, org: str, work: str, deny: str,
               scale: float, port: int) -> dict:
    row: dict = {"repo": repo, "group": group, "status": "other", "detail": "", "title": ""}
    root = Path(work) / "src" / repo
    env = os.environ.copy()
    env["DEMO_DIRECTOR_DENY"] = deny
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONUNBUFFERED"] = "1"
    if not (root / ".git").is_dir():
        root.parent.mkdir(parents=True, exist_ok=True)
        code, _out, err = _run(
            ["git", "clone", "--depth", "1", f"https://github.com/{org}/{repo}.git", str(root)],
            env, 90,
        )
        if code != 0:
            row["detail"] = "clone failed"
            row["bucket"] = "other"
            row["stderr"] = err[-400:]
            return row
    if (root / "build.py").is_file():
        _run([sys.executable, str(root / "build.py")], env, 90)
    rel = _entry(root)
    if rel is None:
        row["detail"] = "no html entry"
        row["bucket"] = "other"
        return row
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        if not _wait_port(port):
            row["detail"] = "server did not start"
            row["bucket"] = "other"
            return row
        url = f"http://127.0.0.1:{port}/{rel}"
        shot_path = Path(work) / "shots" / f"{repo}.json"
        outdir = Path(work) / "out" / repo
        shot_path.parent.mkdir(parents=True, exist_ok=True)
        outdir.mkdir(parents=True, exist_ok=True)
        code, stdout, stderr = _run(
            [sys.executable, "-m", "demo_director", "init", url, "-o", str(shot_path)],
            env, 120,
        )
        payload = _load_json(stdout)
        if payload is None or "steps" not in payload:
            status = (payload or {}).get("status") or "error"
            detail = (payload or {}).get("detail") or (stderr or stdout)[-300:]
            row["status"] = status
            row["detail"] = str(detail)
            row["title"] = str((payload or {}).get("title") or "")
            row["bucket"] = _bucket(status, str(detail))
            return row
        row["title"] = str(payload.get("title") or "")
        code, stdout, stderr = _run(
            [sys.executable, "-m", "demo_director", "batch", str(shot_path),
             "-o", str(outdir), "--scale", str(scale)],
            env, 420,
        )
        report = _load_json(stdout)
        item = ((report or {}).get("items") or [{}])[0]
        status = str(item.get("status") or ("error" if code else "error"))
        detail = str(item.get("error") or item.get("warning") or "")
        row["title"] = str(item.get("title") or row["title"])
        if status == "ok":
            try:
                terms = tuple(
                    line.split("#", 1)[0].strip()
                    for line in Path(deny).read_text().splitlines()
                    if line.split("#", 1)[0].strip()
                )
                leaked = _replay(payload, terms, port)
            except Exception as exc:
                row["status"] = "other"
                row["detail"] = f"replay failed: {type(exc).__name__}"
                row["bucket"] = "other"
                return row
            if leaked:
                row["status"] = "name-leak"
                row["detail"] = "denied term in replayed text"
                row["leaked"] = leaked
                row["bucket"] = "name-leak"
                return row
        row["status"] = status
        row["detail"] = detail
        row["bucket"] = _bucket(status, detail)
        if stderr.strip() and status == "ok":
            row["stderr"] = stderr[-200:]
        return row
    finally:
        try:
            os.killpg(server.pid, 15)
        except OSError:
            server.kill()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Render a repo set and summarize guard results")
    ap.add_argument("--set", required=True, type=Path)
    ap.add_argument("--deny", required=True, type=Path)
    ap.add_argument("--org", required=True)
    ap.add_argument("--workdir", type=Path, default=Path("/tmp/fleet-check"))
    ap.add_argument("--out", type=Path, default=Path("/tmp/fleet-check/summary.json"))
    ap.add_argument("--scale", type=float, default=0.5)
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--only", action="append", default=[])
    args = ap.parse_args(argv)
    rows = _read_set(args.set)
    if args.only:
        wanted = set(args.only)
        rows = [row for row in rows if row[0] in wanted]
    args.workdir.mkdir(parents=True, exist_ok=True)
    done: dict[str, dict] = {}
    if args.resume and args.out.is_file():
        prior = json.loads(args.out.read_text())
        for item in prior.get("repos") or []:
            done[item["repo"]] = item
    pending = [(repo, group, index) for index, (repo, group) in enumerate(rows) if repo not in done]
    results = list(done.values())

    def store() -> None:
        ordered = {item["repo"]: item for item in results}
        repos = [ordered[repo] for repo, _group in rows if repo in ordered]
        counts: dict[str, int] = {}
        for item in repos:
            counts[item["bucket"]] = counts.get(item["bucket"], 0) + 1
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps({"counts": counts, "repos": repos}, indent=2) + "\n")

    if not pending:
        store()
        print(args.out.read_text())
        return 0
    workers = max(1, args.jobs)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                check_repo, repo, group, args.org, str(args.workdir),
                str(args.deny), args.scale, 46000 + index,
            ): repo
            for repo, group, index in pending
        }
        for future in as_completed(futures):
            repo = futures[future]
            try:
                row = future.result()
            except Exception as exc:
                row = {
                    "repo": repo, "group": "", "status": "other",
                    "detail": f"{type(exc).__name__}: {exc}", "title": "", "bucket": "other",
                }
            results.append(row)
            store()
            print(f"{row['repo']}\t{row['bucket']}\t{row.get('title','')}\t{row.get('detail','')[:160]}", flush=True)
    print(args.out.read_text())
    return 0


if __name__ == "__main__":
    sys.exit(main())
