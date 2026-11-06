"""Propose synthetic replacements for demo, fixture, and UI strings.

Dry-run by default. Private data sources are not read. ``reads_private_sources``
stays locked, so tutor sheets and an hspt bank are never opened. KPD and law
repos are refused before any file is touched.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from .guard import reads_private_sources

SCRUB_BRANCH = "scrub/family-names"

# Longer patterns first is unnecessary for word boundaries, but multi-word
# school names must be one alternative so "St. Mary" is a single replacement.
_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"charlotte", "Avery"),
    (r"miranda", "Quinn"),
    (r"victor", "Jordan"),
    (r"spalding", "Riverside"),
    (r"saint\s+mary", "Northfield"),
    (r"st\.?\s*mary", "Northfield"),
    (r"boyd", "Rowan"),
    (r"char", "Avery"),
    (r"vic", "Jordan"),
)
_NAME_RE = re.compile(
    r"(?<![\w])(" + "|".join(body for body, _ in _PATTERNS) + r")(?![\w])",
    re.IGNORECASE,
)

_QUOTED = re.compile(r"""("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')""", re.DOTALL)

_DEMO_DIRS = {
    "fixtures", "fixture", "demos", "demo", "examples", "shots",
    "public", "site", "web", "ui", "copy", "content", "pages",
    "components", "static", "src",
}
_SKIP_DIRS = {".git", "node_modules", "vendor", "dist", "__pycache__", ".demo-director"}
_DATA_DIRS = {"data", "private", "secrets", "raw", "bank", "sheets", "tutor"}
_DEMO_ROOTS = {"fixtures", "fixture", "demos", "demo", "examples", "public", "shots"}
_TEXT = {".html", ".htm", ".md", ".markdown", ".json", ".txt", ".css", ".js", ".jsx", ".ts", ".tsx", ".vue", ".svg"}
_QUOTED_ONLY = {".js", ".jsx", ".ts", ".tsx", ".vue", ".py", ".css"}
_PRIVATE_MARKERS = (
    "tutor-sheet", "tutor_sheet", "tutor-sheets", "tutorsheets",
    "hspt", "private-source", "private_sources", "private-sources",
)
_REFUSED_SEGMENTS = {"kpd", "law", "laws"}
_MAX_BYTES = 1_000_000


class ScrubLockout(RuntimeError):
    """Private-source reads are locked."""


def _lock() -> None:
    if reads_private_sources():
        raise ScrubLockout("reads_private_sources is locked; refusing to read private sources")


def refusal_reason(repo: str) -> str | None:
    """KPD and law checkouts are not scrub targets."""
    parts = [part for part in re.split(r"[^a-z0-9]+", repo.casefold()) if part]
    for part in parts:
        if part in _REFUSED_SEGMENTS:
            return part
    return None


def is_private_path(path: Path) -> bool:
    blob = "/".join(path.parts).casefold()
    if any(marker in blob for marker in _PRIVATE_MARKERS):
        return True
    parts = list(path.parts)
    under_demo = any(part.casefold() in _DEMO_ROOTS for part in parts)
    if under_demo:
        return False
    return any(part.casefold() in _DATA_DIRS for part in parts)


def is_demo_surface(path: Path, root: Path) -> bool:
    """Demo, fixture, and UI text. Not private data, not build output."""
    if is_private_path(path):
        return False
    try:
        rel = path.relative_to(root)
    except ValueError:
        return False
    if any(part.casefold() in _SKIP_DIRS or is_private_path(Path(part)) for part in rel.parts):
        return False
    if path.suffix.casefold() not in _TEXT:
        return False
    parts = {part.casefold() for part in rel.parts[:-1]}
    name = path.name.casefold()
    if name in {"readme.md", "readme.markdown", "index.html", "demo.html", "app.html"}:
        return True
    if parts & _DEMO_DIRS:
        return True
    if path.suffix.casefold() in {".html", ".htm"}:
        return True
    return False


def _cased(sample: str, replacement: str) -> str:
    letters = [ch for ch in sample if ch.isalpha()]
    if letters and all(ch.isupper() for ch in letters):
        return replacement.upper()
    if letters and all(ch.islower() for ch in letters):
        return replacement.lower()
    if sample[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def _synthetic_for(match: re.Match[str]) -> str:
    folded = re.sub(r"\s+", " ", match.group(0).casefold())
    folded = folded.replace("st. mary", "st mary").replace("st.mary", "st mary")
    for body, synthetic in _PATTERNS:
        if re.fullmatch(body, folded, re.IGNORECASE):
            return synthetic
    return "Avery"


def replace_surface(text: str) -> tuple[str, list[dict]]:
    """Whole-word synthetic replacements. Returns the new text and each hit."""
    hits: list[dict] = []

    def sub(match: re.Match[str]) -> str:
        synthetic = _cased(match.group(0), _synthetic_for(match))
        hits.append({
            "term": match.group(0),
            "synthetic": synthetic,
            "line": text.count("\n", 0, match.start()) + 1,
        })
        return synthetic

    updated = _NAME_RE.sub(sub, text)
    return updated, hits


def replace_quoted(text: str) -> tuple[str, list[dict]]:
    """Same replacements, limited to quoted strings so identifiers stay put."""
    hits: list[dict] = []

    def sub(match: re.Match[str]) -> str:
        raw = match.group(0)
        inner, found = replace_surface(raw[1:-1])
        start_line = text.count("\n", 0, match.start())
        for item in found:
            item["line"] = start_line + item["line"]
            hits.append(item)
        return raw[0] + inner + raw[-1]

    return _QUOTED.sub(sub, text), hits


_HTML_BLOCK = re.compile(
    r"(<script\b[^>]*>.*?</script>|<style\b[^>]*>.*?</style>)",
    re.IGNORECASE | re.DOTALL,
)


def replace_html(text: str) -> tuple[str, list[dict]]:
    """Visible markup is rewritten. Script and style change only inside quotes."""
    hits: list[dict] = []
    parts: list[str] = []
    pos = 0

    def take(chunk: str, start: int, quoted: bool) -> str:
        updated, found = replace_quoted(chunk) if quoted else replace_surface(chunk)
        base = text.count("\n", 0, start)
        for item in found:
            item["line"] += base
            hits.append(item)
        return updated

    for match in _HTML_BLOCK.finditer(text):
        parts.append(take(text[pos:match.start()], pos, False))
        parts.append(take(match.group(0), match.start(), True))
        pos = match.end()
    parts.append(take(text[pos:], pos, False))
    return "".join(parts), hits


def _replace_file(path: Path, text: str) -> tuple[str, list[dict]]:
    suffix = path.suffix.casefold()
    if suffix in {".html", ".htm"}:
        return replace_html(text)
    if suffix in _QUOTED_ONLY:
        return replace_quoted(text)
    return replace_surface(text)


def propose_tree(root: Path) -> list[dict]:
    """Dry-run changes for one checkout. Does not write. Does not open private paths."""
    _lock()
    root = Path(root)
    if refusal_reason(root.name):
        return []
    changes: list[dict] = []
    if not root.is_dir():
        return changes
    for path in sorted(root.rglob("*")):
        if not path.is_file() or not is_demo_surface(path, root):
            continue
        try:
            if path.stat().st_size > _MAX_BYTES:
                continue
            raw = path.read_bytes()
        except OSError:
            continue
        if b"\0" in raw:
            continue
        text = raw.decode("utf-8", "replace")
        updated, hits = _replace_file(path, text)
        if not hits or updated == text:
            continue
        rel = path.relative_to(root).as_posix()
        changes.append({
            "path": rel,
            "replacements": hits,
            "updated": updated,
        })
    return changes


def public_change(change: dict) -> dict:
    """Drop file bodies. The report lists path, line, term, and synthetic text."""
    return {"path": change["path"], "replacements": change["replacements"]}


def blocked_repos(summary: dict) -> list[str]:
    """Repo names whose fleet bucket is a name block, in summary order."""
    found: list[str] = []
    for row in summary.get("repos") or []:
        if not isinstance(row, dict):
            continue
        bucket = str(row.get("bucket") or "")
        status = str(row.get("status") or "")
        detail = str(row.get("detail") or row.get("error") or "")
        if bucket in {"name-blocked", "name-leak"} or status in {"name-leak", "name-blocked"}:
            found.append(str(row.get("repo") or ""))
        elif status == "pii-blocked" and "name:" in detail:
            found.append(str(row.get("repo") or ""))
    return [name for name in found if name]


def _git(root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=str(root), text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )


def _is_checkout(root: Path) -> bool:
    return (root / ".git").exists()


def _ensure_identity(root: Path) -> None:
    email = _git(root, ["config", "user.email"])
    if email.returncode != 0 or not email.stdout.strip():
        _git(root, ["config", "user.email", "scrub@example.com"])
        _git(root, ["config", "user.name", "Demo Director"])


def prepare_branch(root: Path, branch: str = SCRUB_BRANCH, *, dry_run: bool = True) -> dict:
    """Write a local patch branch when ``dry_run`` is false and the tree is a checkout.

    Dry-run returns the proposal and does not modify files. A refused repo
    (KPD, law) is skipped before any file is opened.
    """
    _lock()
    root = Path(root)
    reason = refusal_reason(root.name)
    if reason:
        return {
            "repo": root.name, "skipped": reason, "changes": [],
            "dry_run": True, "branch": branch, "reads_private_sources": False,
        }
    changes = propose_tree(root)
    public = [public_change(change) for change in changes]
    result = {
        "repo": root.name,
        "dry_run": dry_run,
        "branch": branch,
        "changes": public,
        "skipped": None,
        "committed": False,
        "reads_private_sources": False,
    }
    if dry_run or not changes:
        return result
    if not _is_checkout(root):
        result["skipped"] = "not-a-checkout"
        result["dry_run"] = True
        return result
    for change in changes:
        (root / change["path"]).write_text(change["updated"])
    _ensure_identity(root)
    _git(root, ["checkout", "-B", branch])
    _git(root, ["add", "--", *[change["path"] for change in changes]])
    commit = _git(root, ["commit", "-m", "Replace denylisted demo names with synthetic placeholders"])
    result["committed"] = commit.returncode == 0
    if commit.returncode != 0:
        result["commit_error"] = (commit.stderr or commit.stdout)[-400:]
    return result


def open_pr_commands(branch: str, base: str = "main") -> list[list[str]]:
    body = (
        "Replaces denylisted names in demo, fixture, and UI strings with "
        "synthetic placeholders. Does not read private tutor sheets or an hspt bank."
    )
    return [
        ["git", "push", "-u", "origin", branch],
        [
            "gh", "pr", "create", "--draft", "--base", base, "--head", branch,
            "--title", "Scrub demo copy to synthetic names", "--body", body,
        ],
    ]


def open_prs(root: Path, branch: str = SCRUB_BRANCH, *, yes: bool = False,
             runner=None, base: str = "main") -> dict:
    """Prepare a branch and, only with ``yes``, push it and open a draft PR."""
    prepared = prepare_branch(root, branch, dry_run=not yes)
    prepared["commands"] = open_pr_commands(branch, base)
    prepared["opened"] = False
    if not yes or prepared.get("skipped") or not prepared.get("changes"):
        return prepared
    if runner is None:
        def runner(cmd: list[str], cwd: Path) -> None:
            subprocess.run(cmd, cwd=str(cwd), check=True)
    for cmd in prepared["commands"]:
        runner(cmd, Path(root))
    prepared["opened"] = True
    return prepared


def _read_set(path: Path) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        rows.append((parts[0], parts[1] if len(parts) > 1 else ""))
    return rows


def _clone(org: str, repo: str, dest: Path) -> str | None:
    if refusal_reason(repo):
        return refusal_reason(repo)
    dest.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["git", "clone", "--depth", "1", f"https://github.com/{org}/{repo}.git", str(dest)],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if proc.returncode != 0:
        return "clone-failed"
    return None


def run_set(set_path: Path, workdir: Path, mode: str, *, org: str = "",
            clone: bool = False, only: tuple[str, ...] = (), yes: bool = False,
            runner=None) -> dict:
    """Apply ``mode`` to each repo checkout under ``workdir/src/<repo>``."""
    _lock()
    rows = _read_set(set_path)
    if only:
        wanted = set(only)
        rows = [row for row in rows if row[0] in wanted]
    reports = []
    for repo, _group in rows:
        root = workdir / "src" / repo
        reason = refusal_reason(repo)
        if reason:
            reports.append({
                "repo": repo, "skipped": reason, "changes": [],
                "reads_private_sources": False,
            })
            continue
        if not root.exists() and clone and org:
            failed = _clone(org, repo, root)
            if failed:
                reports.append({"repo": repo, "skipped": failed, "changes": [], "reads_private_sources": False})
                continue
        if not root.is_dir():
            reports.append({"repo": repo, "skipped": "missing", "changes": [], "reads_private_sources": False})
            continue
        if mode == "propose":
            reports.append({
                "repo": repo,
                "dry_run": True,
                "changes": [public_change(change) for change in propose_tree(root)],
                "skipped": None,
                "reads_private_sources": False,
            })
        elif mode == "prepare":
            reports.append(prepare_branch(root, dry_run=False))
        elif mode == "open-prs":
            reports.append(open_prs(root, yes=yes, runner=runner))
        else:
            raise ScrubLockout(f"unknown mode {mode}")
    return {"reads_private_sources": False, "mode": mode, "repos": reports}


def list_blocked(summary_path: Path) -> list[str]:
    summary = json.loads(summary_path.read_text())
    if not isinstance(summary, dict):
        raise ScrubLockout("summary must be a JSON object")
    return blocked_repos(summary)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fleet_scrub", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    listed = sub.add_parser("list-blocked", help="print repos whose fleet bucket is name-blocked")
    listed.add_argument("--summary", required=True, type=Path)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--set", required=True, type=Path)
    common.add_argument("--workdir", type=Path, default=Path("/tmp/fleet-check"))
    common.add_argument("--org", default="")
    common.add_argument("--clone", action="store_true")
    common.add_argument("--only", action="append", default=[])

    sub.add_parser("propose", parents=[common], help="dry-run synthetic replacements")
    sub.add_parser("prepare", parents=[common], help="commit a local scrub/family-names branch")
    opening = sub.add_parser("open-prs", parents=[common], help="prepare, push, and open draft PRs")
    opening.add_argument("--yes", action="store_true", help="required before any push or PR")
    opening.add_argument("--base", default="main")

    args = parser.parse_args(argv)
    if args.cmd == "list-blocked":
        for repo in list_blocked(args.summary):
            print(repo)
        return 0
    report = run_set(
        args.set, args.workdir, args.cmd, org=args.org, clone=args.clone,
        only=tuple(args.only), yes=bool(getattr(args, "yes", False)),
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    if any(row.get("skipped") == "clone-failed" for row in report["repos"]):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
