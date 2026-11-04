"""Shot list model. Parsing JSON is the only boundary that accepts untyped input."""
from __future__ import annotations

import json
import pathlib
import re

from .types import (
    ASPECTS,
    Aspect,
    Caption,
    Click,
    Goto,
    Hold,
    PiiPolicy,
    ShotList,
    ShotListError,
    Step,
    Type,
    Zoom,
)

FIXTURE_URL = "FIXTURE"
_ROOT = pathlib.Path(__file__).resolve().parents[1]

_BUILDERS = {
    "goto": lambda raw, frames: Goto(url=str(raw.get("url") or ""), frames=frames),
    "click": lambda raw, frames: Click(selector=str(raw.get("selector") or ""), frames=frames),
    "type": lambda raw, frames: Type(
        selector=str(raw.get("selector") or ""), text=str(raw.get("text") or ""), frames=frames
    ),
    "caption": lambda raw, frames: Caption(text=str(raw.get("text") or ""), frames=frames),
    "hold": lambda raw, frames: Hold(frames=frames),
    "zoom": lambda raw, frames: Zoom(selector=str(raw.get("selector") or ""), frames=frames),
}


def _step(i: int, raw: object) -> Step:
    if not isinstance(raw, dict):
        raise ShotListError(f"step {i}: expected an object")
    action = raw.get("action")
    if action not in _BUILDERS:
        raise ShotListError(f"step {i}: unknown action {action!r}")
    try:
        frames = int(raw.get("frames", 6))
    except (TypeError, ValueError):
        raise ShotListError(f"step {i}: frames must be 1..120") from None
    try:
        return _BUILDERS[action](raw, frames)
    except ShotListError as exc:
        raise ShotListError(f"step {i}: {exc}") from exc


def _names(data: dict) -> PiiPolicy:
    """``pii.names`` and ``deny_names`` are the same denylist, accepted at the boundary."""
    names: list[str] = []
    if "pii" in data and data["pii"] is not None:
        raw = data["pii"]
        if not isinstance(raw, dict):
            raise ShotListError("pii must be an object")
        extra = raw.get("names") or []
        if not isinstance(extra, list) or not all(isinstance(name, str) for name in extra):
            raise ShotListError("pii.names must be a list of strings")
        names.extend(extra)
    if "deny_names" in data:
        extra = data["deny_names"]
        if not isinstance(extra, list) or not all(isinstance(name, str) for name in extra):
            raise ShotListError("deny_names must be a list of strings")
        names.extend(extra)
    seen: list[str] = []
    for name in names:
        if name not in seen:
            seen.append(name)
    return PiiPolicy(names=tuple(seen))


def parse(data: dict) -> ShotList:
    if not isinstance(data, dict):
        raise ShotListError("shot list must be an object")
    for key in ("title", "url", "steps"):
        if key not in data:
            raise ShotListError(f"missing {key}")
    raw_steps = data["steps"]
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ShotListError("steps is empty")
    aspects: list[Aspect] = []
    for name in data.get("aspects", ["16:9"]):
        try:
            aspects.append(Aspect(name))
        except ValueError:
            raise ShotListError(f"unknown aspect {name}") from None
    viewport = data.get("viewport", [1280, 800])
    try:
        width, height = int(viewport[0]), int(viewport[1])
    except (TypeError, ValueError, IndexError):
        raise ShotListError("viewport must be [width, height]") from None
    try:
        fps = int(data.get("fps", 10))
    except (TypeError, ValueError):
        raise ShotListError("fps must be positive") from None
    return ShotList(
        str(data["title"]),
        str(data["url"]),
        tuple(_step(i, step) for i, step in enumerate(raw_steps)),
        tuple(aspects),
        (width, height),
        fps,
        _names(data),
    )


def dump(shotlist: ShotList) -> dict:
    steps = []
    for step in shotlist.steps:
        raw: dict = {"action": step.action, "frames": step.frames}
        if isinstance(step, (Click, Type, Zoom)):
            raw["selector"] = step.selector
        if isinstance(step, (Type, Caption)):
            raw["text"] = step.text
        if isinstance(step, Goto):
            raw["url"] = step.url
        steps.append(raw)
    data = {
        "title": shotlist.title,
        "url": shotlist.url,
        "aspects": [aspect.value for aspect in shotlist.aspects],
        "viewport": [shotlist.viewport[0], shotlist.viewport[1]],
        "fps": shotlist.fps,
        "steps": steps,
    }
    if shotlist.pii.names:
        data["pii"] = {"names": list(shotlist.pii.names)}
        data["deny_names"] = list(shotlist.pii.names)
    return data


def load(path: str) -> ShotList:
    with open(path) as handle:
        data = json.load(handle)
    if isinstance(data, dict) and data.get("url"):
        data = dict(data)
        url = data["url"]
        if url == FIXTURE_URL:
            data["url"] = (_ROOT / "fixtures" / "app.html").resolve().as_uri()
        elif isinstance(url, str) and not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", url):
            data["url"] = pathlib.Path(path).resolve().parent.joinpath(url).resolve().as_uri()
    return parse(data)


__all__ = ["ASPECTS", "FIXTURE_URL", "ShotListError", "dump", "load", "parse"]
