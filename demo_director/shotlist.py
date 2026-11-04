"""Shot list model. A shot list is the single source of truth for a demo."""
from __future__ import annotations
import json
from dataclasses import dataclass, field

ACTIONS = {"goto", "click", "type", "caption", "hold", "zoom"}
ASPECTS = {"16:9": (1280, 720), "1:1": (1080, 1080), "9:16": (720, 1280)}


class ShotListError(ValueError):
    pass


@dataclass(frozen=True)
class Step:
    action: str
    selector: str | None = None
    text: str | None = None
    url: str | None = None
    frames: int = 6


@dataclass(frozen=True)
class ShotList:
    title: str
    url: str
    steps: tuple[Step, ...]
    aspects: tuple[str, ...] = ("16:9",)
    viewport: tuple[int, int] = (1280, 800)
    fps: int = 10
    deny_names: tuple[str, ...] = ()


def _step(i: int, raw: dict) -> Step:
    a = raw.get("action")
    if a not in ACTIONS:
        raise ShotListError(f"step {i}: unknown action {a!r}")
    if a in {"click", "type", "zoom"} and not raw.get("selector"):
        raise ShotListError(f"step {i}: {a} needs a selector")
    if a in {"type", "caption"} and not raw.get("text"):
        raise ShotListError(f"step {i}: {a} needs text")
    if a == "goto" and not raw.get("url"):
        raise ShotListError(f"step {i}: goto needs url")
    frames = int(raw.get("frames", 6))
    if not 1 <= frames <= 120:
        raise ShotListError(f"step {i}: frames must be 1..120")
    return Step(a, raw.get("selector"), raw.get("text"), raw.get("url"), frames)


def parse(data: dict) -> ShotList:
    for k in ("title", "url", "steps"):
        if k not in data:
            raise ShotListError(f"missing {k}")
    if not data["steps"]:
        raise ShotListError("steps is empty")
    aspects = tuple(data.get("aspects", ["16:9"]))
    bad = [a for a in aspects if a not in ASPECTS]
    if bad:
        raise ShotListError(f"unknown aspect {bad[0]}")
    vp = tuple(data.get("viewport", [1280, 800]))
    if "deny_names" in data:
        names = data["deny_names"]
        if not isinstance(names, list) or any(not isinstance(n, str) for n in names):
            raise ShotListError("deny_names must be a list of strings")
    else:
        names = []
    return ShotList(data["title"], data["url"],
                    tuple(_step(i, s) for i, s in enumerate(data["steps"])),
                    aspects, (int(vp[0]), int(vp[1])), int(data.get("fps", 10)),
                    tuple(names))


def load(path: str) -> ShotList:
    with open(path) as f:
        return parse(json.load(f))
