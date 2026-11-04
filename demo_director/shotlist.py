"""Shot list model. Parsing JSON is the only boundary that accepts untyped input."""
from __future__ import annotations

import json
import pathlib
import re

from .types import (
    ASPECTS,
    Aspect,
    BlankPolicy,
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


def _string_list(raw: object, label: str) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise ShotListError(f"{label} must be a list of strings")
    return list(raw)


def _names(data: dict) -> PiiPolicy:
    """``pii.names`` and ``deny_names`` are the strict denylist.

    ``pii.watch`` is derived from a README or title and only blocks when the
    term is rendered. ``pii.allow`` drops watch terms.
    """
    names: list[str] = []
    watch: list[str] = []
    allow: list[str] = []
    if "pii" in data and data["pii"] is not None:
        raw = data["pii"]
        if not isinstance(raw, dict):
            raise ShotListError("pii must be an object")
        names.extend(_string_list(raw.get("names") or [], "pii.names"))
        watch.extend(_string_list(raw.get("watch") or [], "pii.watch"))
        allow.extend(_string_list(raw.get("allow") or [], "pii.allow"))
    if "deny_names" in data:
        names.extend(_string_list(data["deny_names"], "deny_names"))
    if "allow_names" in data:
        allow.extend(_string_list(data["allow_names"], "allow_names"))

    def unique(items: list[str]) -> tuple[str, ...]:
        seen: list[str] = []
        for name in items:
            if name not in seen:
                seen.append(name)
        return tuple(seen)

    return PiiPolicy(names=unique(names), allow=unique(allow), watch=unique(watch))


def _blank(data: dict) -> BlankPolicy | None:
    """Missing key means the built-in fail-closed policy. An object replaces it."""
    if "blank" not in data or data["blank"] is None:
        return None
    raw = data["blank"]
    if not isinstance(raw, dict):
        raise ShotListError("blank must be an object")
    patterns = raw.get("patterns", BlankPolicy().patterns)
    if not isinstance(patterns, list) or not all(isinstance(item, str) for item in patterns):
        raise ShotListError("blank.patterns must be a list of strings")
    try:
        min_chars = int(raw.get("min_chars", BlankPolicy.min_chars))
    except (TypeError, ValueError):
        raise ShotListError("blank.min_chars must be an integer") from None
    if min_chars < 0:
        raise ShotListError("blank.min_chars must be an integer")
    for pattern in patterns:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ShotListError(f"blank.patterns: {exc}") from exc
    return BlankPolicy(min_chars, tuple(patterns))


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
        _blank(data),
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
    if shotlist.pii.names or shotlist.pii.watch or shotlist.pii.allow:
        policy: dict = {}
        if shotlist.pii.names:
            policy["names"] = list(shotlist.pii.names)
            data["deny_names"] = list(shotlist.pii.names)
        if shotlist.pii.watch:
            policy["watch"] = list(shotlist.pii.watch)
        if shotlist.pii.allow:
            policy["allow"] = list(shotlist.pii.allow)
        data["pii"] = policy
    if shotlist.blank is not None:
        data["blank"] = {
            "min_chars": shotlist.blank.min_chars,
            "patterns": list(shotlist.blank.patterns),
        }
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
