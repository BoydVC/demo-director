"""Batch mode. Sorted inputs, one report, GIF size budget. No PII bypass.

``batch`` calls ``direct``, and ``direct`` calls ``record``. ``record`` is the
only renderer and it always runs the session chart, which blocks on PII.
There is no skip flag on this function.
"""
from __future__ import annotations

import glob
import json
import os

from dataclasses import replace

from .director import direct
from .gallery import write_gallery
from .session import RecordingFailure
from .shotlist import ShotListError, load

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
    return {
        "frames": frames,
        "bytes": byte_count,
        "duration_s": round(frames / fps, 3) if fps else None,
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


def _slug(title: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in title).strip("-")
    squashed = "-".join(part for part in cleaned.split("-") if part)
    return squashed or "demo"


def _render_one(path: str, outdir: str, scale: float, budget: int,
                extra_names: tuple[str, ...] = ()) -> dict:
    item: dict = {"shotlist": path, "status": "ok", "aspects": {}}
    try:
        sl = load(path)
    except (OSError, ShotListError, json.JSONDecodeError, ValueError) as exc:
        item["status"] = "error"
        item["error"] = str(exc)
        return item
    if extra_names:
        sl = replace(sl, deny_names=tuple(sl.deny_names) + tuple(extra_names))
    item["title"] = sl.title
    try:
        rendered = direct(sl, outdir, scale)
    except RecordingFailure as exc:
        item["status"] = exc.kind
        item["error"] = exc.detail or exc.kind
        return item
    rows = []
    for aspect in _ASPECT_ORDER:
        if aspect not in rendered:
            continue
        info = rendered[aspect]
        row = aspect_record(
            info["frames"], os.path.getsize(info["gif"]), sl.fps, budget, info["gif"], info["mp4"]
        )
        item["aspects"][aspect] = row
        rows.append(row)
    item["status"] = status_for(rows)
    return item


def batch(paths: list[str], outdir: str, scale: float = 0.6, budget: int = GIF_BUDGET,
          extra_names: tuple[str, ...] = ()) -> dict:
    """Render every shot list. Report is deterministic for a given set of files.

    Frame counts come from the pure ``frames_for`` path. Byte sizes are measured
    from the files just written. Item order follows the sorted path list, not
    the order of ``paths``. ``extra_names`` is appended to each list's denylist
    before ``record`` runs. It cannot turn the guard off.
    """
    files = expand_inputs(list(paths))
    os.makedirs(outdir, exist_ok=True)
    multi = len(files) > 1
    names = tuple(extra_names)
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
        items.append(_render_one(path, dest, scale, budget, names))
    report = {"budget_bytes": budget, "items": items}
    with open(os.path.join(outdir, "report.json"), "w") as fh:
        json.dump(report, fh, indent=2, sort_keys=True)
        fh.write("\n")
    title = items[0].get("title", "Demo Director") if len(items) == 1 else "Demo Director"
    write_gallery(outdir, title)
    return report


def failed(report: dict) -> bool:
    return any(item.get("status") != "ok" for item in report.get("items", [])) or not report.get("items")
