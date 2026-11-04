"""Types that make an illegal shot list unrepresentable.

JSON is checked at one boundary, ``shotlist.parse``. After that a step is one
of Goto, Click, Type, Caption, Hold, or Zoom; an aspect is an Aspect; a
recorded moment is a Beat; a finished render is a RenderReport.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Literal, Union

from PIL import Image


class ShotListError(ValueError):
    """A shot list could not be parsed, or a variant invariant failed."""


class Aspect(str, enum.Enum):
    WIDE = "16:9"
    SQUARE = "1:1"
    TALL = "9:16"

    @property
    def size(self) -> tuple[int, int]:
        return _SIZES[self]


_SIZES = {
    Aspect.WIDE: (1280, 720),
    Aspect.SQUARE: (1080, 1080),
    Aspect.TALL: (720, 1280),
}

# String keys, so existing callers can index with "16:9".
ASPECTS = {aspect.value: aspect.size for aspect in Aspect}

Box = tuple[float, float, float, float]
_MIN_FRAMES = 1
_MAX_FRAMES = 120


def _frames(n: int) -> int:
    if type(n) is not int or not _MIN_FRAMES <= n <= _MAX_FRAMES:
        raise ShotListError(f"frames must be {_MIN_FRAMES}..{_MAX_FRAMES}")
    return n


def _required(kind: str, field_name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ShotListError(f"{kind} needs {field_name}")
    return value


@dataclass(frozen=True)
class Goto:
    url: str
    frames: int = 6
    action: Literal["goto"] = "goto"

    def __post_init__(self) -> None:
        object.__setattr__(self, "url", _required("goto", "url", self.url))
        object.__setattr__(self, "frames", _frames(self.frames))
        if self.action != "goto":
            raise ShotListError("goto action is fixed")


@dataclass(frozen=True)
class Click:
    selector: str
    frames: int = 6
    action: Literal["click"] = "click"

    def __post_init__(self) -> None:
        object.__setattr__(self, "selector", _required("click", "a selector", self.selector))
        object.__setattr__(self, "frames", _frames(self.frames))
        if self.action != "click":
            raise ShotListError("click action is fixed")


@dataclass(frozen=True)
class Type:
    selector: str
    text: str
    frames: int = 6
    action: Literal["type"] = "type"

    def __post_init__(self) -> None:
        object.__setattr__(self, "selector", _required("type", "a selector", self.selector))
        object.__setattr__(self, "text", _required("type", "text", self.text))
        object.__setattr__(self, "frames", _frames(self.frames))
        if self.action != "type":
            raise ShotListError("type action is fixed")


@dataclass(frozen=True)
class Caption:
    text: str
    frames: int = 6
    action: Literal["caption"] = "caption"

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", _required("caption", "text", self.text))
        object.__setattr__(self, "frames", _frames(self.frames))
        if self.action != "caption":
            raise ShotListError("caption action is fixed")


@dataclass(frozen=True)
class Hold:
    frames: int = 6
    action: Literal["hold"] = "hold"

    def __post_init__(self) -> None:
        object.__setattr__(self, "frames", _frames(self.frames))
        if self.action != "hold":
            raise ShotListError("hold action is fixed")


@dataclass(frozen=True)
class Zoom:
    selector: str
    frames: int = 6
    action: Literal["zoom"] = "zoom"

    def __post_init__(self) -> None:
        object.__setattr__(self, "selector", _required("zoom", "a selector", self.selector))
        object.__setattr__(self, "frames", _frames(self.frames))
        if self.action != "zoom":
            raise ShotListError("zoom action is fixed")


Step = Union[Goto, Click, Type, Caption, Hold, Zoom]


def step_selector(step: Step) -> str | None:
    if isinstance(step, (Click, Type, Zoom)):
        return step.selector
    return None


def step_text(step: Step) -> str | None:
    if isinstance(step, (Type, Caption)):
        return step.text
    return None


@dataclass(frozen=True)
class PiiPolicy:
    """Emails and phone numbers are always denied. ``names`` is the extra denylist."""

    names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        names = tuple(part.strip() for part in self.names if isinstance(part, str) and part.strip())
        object.__setattr__(self, "names", names)


@dataclass(frozen=True)
class ShotList:
    title: str
    url: str
    steps: tuple[Step, ...]
    aspects: tuple[Aspect, ...] = (Aspect.WIDE,)
    viewport: tuple[int, int] = (1280, 800)
    fps: int = 10
    pii: PiiPolicy = field(default_factory=PiiPolicy)

    def __post_init__(self) -> None:
        if not isinstance(self.title, str) or not self.title.strip():
            raise ShotListError("missing title")
        if not isinstance(self.url, str) or not self.url.strip():
            raise ShotListError("missing url")
        if not self.steps:
            raise ShotListError("steps is empty")
        if not self.aspects:
            raise ShotListError("unknown aspect")
        if type(self.fps) is not int or self.fps < 1:
            raise ShotListError("fps must be positive")
        for aspect in self.aspects:
            if not isinstance(aspect, Aspect):
                raise ShotListError(f"unknown aspect {aspect}")

    @property
    def deny_names(self) -> tuple[str, ...]:
        return self.pii.names


@dataclass(frozen=True)
class Beat:
    """One recorded moment: before/after screenshots, target box, label box."""

    before: Image.Image
    after: Image.Image
    box: Box | None
    step: Step
    label_box: Box | None = None
    lines: tuple[Box, ...] = ()

    @property
    def label(self) -> Box | None:
        return self.label_box


@dataclass(frozen=True)
class SourceNote:
    """Why a draft step was proposed. ``index`` matches the step tuple."""

    index: int
    kind: str
    detail: str


@dataclass(frozen=True)
class Draft:
    """Typed shot list proposed by ``init``, plus where each step came from."""

    shotlist: ShotList
    notes: tuple[SourceNote, ...]


@dataclass(frozen=True)
class RenderReport:
    title: str
    aspect: Aspect
    frames: int
    gif_bytes: int
    mp4_bytes: int
    duration_s: float
    gif: str
    mp4: str
    within_budget: bool

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "aspect": self.aspect.value,
            "frames": self.frames,
            "bytes": self.gif_bytes,
            "mp4_bytes": self.mp4_bytes,
            "duration": round(self.duration_s, 3),
            "gif": self.gif,
            "mp4": self.mp4,
            "within_budget": self.within_budget,
        }
