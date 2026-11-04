"""Render every shot list in a directory and write a size-budget report."""
from __future__ import annotations

import json
import os
import pathlib

from .director import direct
from .gallery import write as write_gallery
from .guard import PiiBlocked
from .shotlist import load

GIF_BUDGET = 5 * 1024 * 1024


def collect(items: list[str]) -> list[str]:
    paths: list[str] = []
    for item in items:
        path = pathlib.Path(item)
        if path.is_dir():
            paths.extend(str(child) for child in sorted(path.glob("*.json")))
        else:
            paths.append(str(path))
    return paths


def run(items: list[str], outdir: str, scale: float = 0.6, budget: int = GIF_BUDGET) -> dict:
    """Render each shot list. PII blocks that list and fails the report. Nothing is skipped quietly."""
    os.makedirs(outdir, exist_ok=True)
    paths = collect(items)
    report: dict = {"budget_bytes": budget, "ok": True, "outputs": []}
    multi = len(paths) > 1
    for path in paths:
        stem = pathlib.Path(path).stem
        dest = os.path.join(outdir, stem) if multi else outdir
        try:
            shotlist = load(path)
            rendered = direct(shotlist, dest, scale)
        except PiiBlocked as exc:
            report["ok"] = False
            report["outputs"].append({
                "shotlist": path,
                "error": "pii-blocked",
                "kind": exc.kind,
                "detail": exc.detail,
            })
            continue
        except Exception as exc:
            report["ok"] = False
            report["outputs"].append({
                "shotlist": path,
                "error": "failed",
                "detail": type(exc).__name__ + ": " + str(exc),
            })
            continue
        for aspect, info in rendered.items():
            nbytes = os.path.getsize(info["gif"])
            frames = info["frames"]
            over = nbytes > budget
            if over:
                report["ok"] = False
            report["outputs"].append({
                "shotlist": path,
                "title": shotlist.title,
                "aspect": aspect,
                "gif": info["gif"],
                "mp4": info["mp4"],
                "still": info.get("still"),
                "frames": frames,
                "bytes": nbytes,
                "duration": round(frames / shotlist.fps, 3),
                "over_budget": over,
            })
    report_path = os.path.join(outdir, "report.json")
    with open(report_path, "w") as handle:
        json.dump(report, handle, indent=2)
        handle.write("\n")
    report["report"] = report_path
    report["gallery"] = write_gallery(outdir, report)
    return report
