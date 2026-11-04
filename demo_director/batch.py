"""Batch mode. Sorted inputs, one report, GIF size budget. No PII bypass.

``batch`` calls ``direct``, and ``direct`` calls ``record``. ``record`` is the
only renderer and it always runs the session chart, which blocks on PII.
There is no skip flag on this function.
"""
from __future__ import annotations

import glob
import json
import os

import pathlib
from dataclasses import replace

from .director import direct
from .gallery import write_gallery
from .guard import derive_names, load_deny_terms
from .propose import readme_near
from .session import RecordingFailure
from .shotlist import ShotListError, load
from .types import PiiPolicy

GIF_BUDGET = 5 * 1024 * 1024
_ASPECT_ORDER = ("16:9", "1:1", "9:16")


def expand_inputs(items: list[str]) -> list[str]:
    """Directories and globs become a sorted, unique list of shot-list paths."""
    found: list[str] = []
    for item in items:
        if any(ch in item for ch in "*?["):
            found.extend(glob.glob(item))
        elif os.path.isdir(item):
            found.extend(glob.glob(os.path.join(item, "*.json")))
        else:
            found.append(item)
    return sorted(set(found))


def aspect_record(frames: int, byte_count: int, fps: int, budget: int, gif: str, mp4: str) -> dict:
    duration = round(frames / fps, 3) if fps else None
    return {
        "frames": frames,
        "bytes": byte_count,
        "duration": duration,
        "duration_s": duration,
        "gif": gif,
        "mp4": mp4,
        "within_budget": byte_count <= budget,
    }


def status_for(rows: list[dict], failure: str | None = None) -> str:
    if failure:
        return failure
    if any(not row["within_budget"] for row in rows):
        return "over-budget"
    return "ok"


def failure_summary(exc: RecordingFailure) -> str:
    """Kind, step index, and selector. The status alone is not a report."""
    parts = [f"{exc.kind} at step {exc.step_index}"]
    if exc.selector:
        parts.append(exc.selector)
    if exc.detail and exc.detail not in {exc.kind, exc.selector, "no beat"}:
        parts.append(exc.detail)
    return " ".join(parts)


def _slug(title: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in title).strip("-")
    squashed = "-".join(part for part in cleaned.split("-") if part)
    return squashed or "demo"


def _render_one(path: str, outdir: str, scale: float, budget: int,
                extra_names: tuple[str, ...] = (), allow_names: tuple[str, ...] = (),
                derive: bool = False) -> dict:
    item: dict = {"shotlist": path, "status": "ok", "error": None, "aspects": {}}
    try:
        sl = load(path)
    except (OSError, ShotListError, json.JSONDecodeError, ValueError) as exc:
        item["status"] = "error"
        item["error"] = str(exc)
        return item
    allow = {part.casefold() for part in allow_names}
    deny = [term for term in tuple(sl.pii.names) + tuple(extra_names) + load_deny_terms()
            if term.casefold() not in allow]
    if derive:
        derived = derive_names(readme_near(pathlib.Path(path).parent) + "\n" + sl.title, sl.title)
        deny.extend(term for term in derived if term.casefold() not in allow and term not in deny)
    sl = replace(sl, pii=PiiPolicy(names=tuple(deny), allow=tuple(allow_names)))
    item["title"] = sl.title
    try:
        rendered = direct(sl, outdir, scale, budget)
    except RecordingFailure as exc:
        item["status"] = exc.kind
        item["kind"] = exc.kind
        item["step"] = exc.step_index
        item["selector"] = exc.selector or None
        if exc.warning:
            item["warning"] = exc.warning
        item["error"] = failure_summary(exc)
        return item
    rows = []
    for aspect in _ASPECT_ORDER:
        if aspect not in rendered:
            continue
        info = rendered[aspect]
        row = aspect_record(
            info["frames"],
            os.path.getsize(info["gif"]),
            int(info.get("fps", sl.fps)),
            budget,
            info["gif"],
            info["mp4"],
        )
        row["attempts"] = info.get("attempts", 1)
        row["scale"] = info.get("scale", scale)
        row["fps"] = info.get("fps", sl.fps)
        item["aspects"][aspect] = row
        rows.append(row)
    item["status"] = status_for(rows)
    return item


def batch(paths: list[str], outdir: str, scale: float = 0.6, budget: int = GIF_BUDGET,
          extra_names: tuple[str, ...] = (), screenshot: bool = False,
          allow_names: tuple[str, ...] = (), derive_names: bool = False) -> dict:
    """Render every shot list. Report is deterministic for a given set of files.

    Frame counts come from the pure ``frames_for`` path. Byte sizes are measured
    from the files just written. Item order follows the sorted path list.
    ``extra_names`` is appended to each list's denylist. It cannot turn the guard off.
    """
    files = expand_inputs(list(paths))
    os.makedirs(outdir, exist_ok=True)
    multi = len(files) > 1
    names = tuple(extra_names)
    allowed = tuple(allow_names)
    items = []
    for path in files:
        if multi:
            try:
                title = load(path).title
            except (OSError, ShotListError, json.JSONDecodeError, ValueError):
                title = os.path.splitext(os.path.basename(path))[0]
            dest = os.path.join(outdir, _slug(title))
        else:
            dest = outdir
        items.append(_render_one(path, dest, scale, budget, names, allowed, derive_names))
    report = {
        "ok": bool(items) and all(item.get("status") == "ok" for item in items),
        "budget_bytes": budget,
        "items": items,
    }
    with open(os.path.join(outdir, "report.json"), "w") as fh:
        json.dump(report, fh, indent=2, sort_keys=True)
        fh.write("\n")
    title = items[0].get("title", "Demo Director") if len(items) == 1 else "Demo Director"
    gallery = write_gallery(outdir, title, report)
    report["gallery"] = gallery
    if screenshot:
        from .gallery import screenshot_gallery
        png = os.path.join(outdir, "gallery.png")
        screenshot_gallery(gallery, png)
        report["gallery_png"] = png
    return report


def failed(report: dict) -> bool:
    return any(item.get("status") != "ok" for item in report.get("items", [])) or not report.get("items")
