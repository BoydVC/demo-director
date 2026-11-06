"""Showcase picks. A name-blocked render is never the featured GIF or still."""
from __future__ import annotations

import os
from pathlib import Path

from .guard import detail_has_name, effective_names, locate_denied

BLOCKED_STATUSES = {"pii-blocked", "name-leak", "name-blocked"}
_SHOWCASE_FILES = ("demo-", "still-")
_SHOWCASE_EXACT = {"gallery.png", "index.html", "best-still.png"}


def name_blocked_row(row: dict) -> bool:
    """True when a fleet or batch row failed the name gate, or its text hits it."""
    bucket = str(row.get("bucket") or "")
    status = str(row.get("status") or "")
    detail = str(row.get("detail") or row.get("error") or "")
    if bucket in {"name-blocked", "name-leak"} or status in {"name-leak", "name-blocked"}:
        return True
    if status == "pii-blocked" and ("name:" in detail or detail_has_name(detail)):
        return True
    text = _row_text(row)
    if text and locate_denied(effective_names(), page=text, title=str(row.get("title") or "")):
        return True
    return False


def _row_text(row: dict) -> str:
    parts = [str(row.get(key) or "") for key in ("title", "detail", "error", "caption")]
    return "\n".join(part for part in parts if part)


def showcase_eligible(row: dict) -> bool:
    """A clean, successful render. Name-blocked rows are not eligible."""
    if name_blocked_row(row):
        return False
    bucket = row.get("bucket")
    status = row.get("status")
    if bucket is not None:
        return bucket == "ok"
    return status in (None, "ok")


def pick_best(rows: list[dict]) -> dict | None:
    """Highest score among eligible rows. Ties keep the earlier row.

    A name-blocked row is never returned, including when its score is higher
    than every clean render.
    """
    best: dict | None = None
    best_key: tuple | None = None
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or not showcase_eligible(row):
            continue
        try:
            score = float(row.get("score", 0) or 0)
        except (TypeError, ValueError):
            score = 0
        key = (score, -index)
        if best_key is None or key > best_key:
            best = row
            best_key = key
    return best


def choose_still_index(total: int, title_frames: int, end_frames: int,
                       frame_text: list[str] | None = None,
                       names: tuple[str, ...] = ()) -> int | None:
    """Last content frame whose text is clean.

    Without per-frame text, this is the last content frame (the historical
    still). When text is supplied, a frame that hits the name gate is skipped.
    None means every candidate is blocked and nothing should be written.
    """
    if total <= 0:
        return None
    content = max(0, total - title_frames - end_frames)
    default = min(total - 1, title_frames + max(0, content - 1))
    if not frame_text:
        return default
    start = min(max(title_frames, 0), total)
    end = max(start, total - max(end_frames, 0))
    order = list(range(start, end)) or [default]
    denied = names or effective_names()
    for index in reversed(order):
        text = frame_text[index] if index < len(frame_text) else ""
        if not locate_denied(denied, page=text, title=text):
            return index
    return None


def discard_showcase(folder: str | Path) -> None:
    """Delete GIFs, stills, and the gallery page in ``folder``. Leave other files."""
    root = Path(folder)
    if not root.is_dir():
        return
    for path in root.iterdir():
        if not path.is_file():
            continue
        name = path.name
        if name in _SHOWCASE_EXACT or any(name.startswith(prefix) and path.suffix in {".gif", ".mp4", ".png"} for prefix in _SHOWCASE_FILES):
            path.unlink()


def feature_still(src: Path, dest: Path, text: str, names: tuple[str, ...] | None = None) -> bool:
    """Copy ``src`` to ``dest`` only when ``text`` is clean. A hit writes nothing."""
    denied = names if names is not None else effective_names()
    if locate_denied(denied, page=text, title=text):
        if dest.is_file():
            dest.unlink()
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(Path(src).read_bytes())
    return True


def publish_showcase(rows: list[dict], dest: str | Path, sources: dict[str, Path] | None = None) -> dict | None:
    """Copy the winning still. Name-blocked rows are not copied, whatever their score."""
    dest_path = Path(dest)
    dest_path.mkdir(parents=True, exist_ok=True)
    featured = dest_path / "best-still.png"
    if featured.is_file():
        featured.unlink()
    winner = pick_best(rows)
    if winner is None:
        return None
    sources = sources or {}
    src = sources.get(str(winner.get("repo") or ""))
    if src is None:
        return winner
    if not feature_still(Path(src), featured, _row_text(winner)):
        return None
    return winner


def heading_blocked(heading: str) -> bool:
    """A showcase heading that would print a denylisted name."""
    if not heading:
        return False
    return bool(locate_denied(effective_names(), title=heading))


def folder_hidden(folder: str, report: dict | None, *, own_report: bool) -> bool:
    """True when this folder's GIFs belong to a name-blocked report item."""
    if not report:
        return False
    items = [item for item in (report.get("items") or []) if isinstance(item, dict)]
    folder_abs = os.path.abspath(folder)
    for item in items:
        status = str(item.get("status") or "")
        # A failed guard must not keep a previous GIF in the gallery.
        blocked = status in BLOCKED_STATUSES
        gifs = []
        for row in (item.get("aspects") or {}).values():
            gif = row.get("gif") or ""
            if gif:
                gifs.append(os.path.abspath(os.path.dirname(gif)))
        linked = folder_abs in gifs
        if not linked and own_report and not gifs and len(items) == 1:
            linked = True
        if linked and blocked:
            return True
    return False
