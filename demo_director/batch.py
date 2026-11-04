"""Render many shot lists and report frames, bytes, and duration against the GIF budget."""
from __future__ import annotations

import json
import os
import pathlib

from .director import direct
from .gallery import screenshot, write
from .shotlist import load
from .types import Aspect, BatchItem, BatchReport, PiiBlocked, RenderReport, ShotList

GIF_BUDGET_BYTES = 5 * 1024 * 1024


def within_budget(gif_bytes: int, budget: int = GIF_BUDGET_BYTES) -> bool:
    """A missing or empty GIF is over budget. Fail closed."""
    return type(gif_bytes) is int and 0 < gif_bytes <= budget


def report_for_outputs(title: str, aspect: Aspect, frames: int, fps: int,
                       gif: str, mp4: str, budget: int) -> RenderReport:
    if frames < 1 or fps < 1:
        raise ValueError("frames and fps must be positive")
    if not os.path.isfile(gif):
        raise FileNotFoundError(gif)
    if not os.path.isfile(mp4):
        raise FileNotFoundError(mp4)
    gif_bytes = os.path.getsize(gif)
    mp4_bytes = os.path.getsize(mp4)
    return RenderReport(
        title=title,
        aspect=aspect,
        frames=frames,
        gif_bytes=gif_bytes,
        mp4_bytes=mp4_bytes,
        duration_s=frames / fps,
        gif=gif,
        mp4=mp4,
        within_budget=within_budget(gif_bytes, budget),
    )


def _reports(shotlist: ShotList, results: dict, budget: int) -> tuple[RenderReport, ...]:
    reports = []
    for aspect in shotlist.aspects:
        info = results.get(aspect.value)
        if not info:
            raise FileNotFoundError(aspect.value)
        reports.append(report_for_outputs(
            shotlist.title, aspect, int(info["frames"]), shotlist.fps, info["gif"], info["mp4"], budget,
        ))
    return tuple(reports)


def expand_paths(paths: list[str]) -> tuple[list[str], list[str]]:
    found: list[str] = []
    missing: list[str] = []
    for raw in paths:
        path = pathlib.Path(raw)
        if path.is_dir():
            files = sorted(str(item) for item in path.glob("*.json"))
            if files:
                found.extend(files)
            else:
                missing.append(raw)
        elif path.is_file():
            found.append(str(path))
        else:
            missing.append(raw)
    return found, missing


def render_paths(paths: list[str], outdir: str, scale: float = 0.6,
                 budget: int = GIF_BUDGET_BYTES, shoot: bool = False) -> BatchReport:
    found, missing = expand_paths(paths)
    items: list[BatchItem] = [BatchItem(path, (), f"shot list not found: {path}") for path in missing]
    multiple = len(found) > 1
    for path in found:
        dest = os.path.join(outdir, pathlib.Path(path).stem) if multiple else outdir
        try:
            shotlist = load(path)
            results = direct(shotlist, dest, scale)
            items.append(BatchItem(path, _reports(shotlist, results, budget), None))
        except PiiBlocked as exc:
            items.append(BatchItem(path, (), str(exc)))
        except Exception as exc:
            items.append(BatchItem(path, (), f"{type(exc).__name__}: {exc}"))
    report = BatchReport(tuple(items), budget)
    os.makedirs(outdir, exist_ok=True)
    index = write(report, outdir)
    with open(os.path.join(outdir, "report.json"), "w") as handle:
        json.dump(report.to_dict(), handle, indent=2)
        handle.write("\n")
    if shoot:
        screenshot(index, os.path.join(outdir, "gallery.png"))
    return report
