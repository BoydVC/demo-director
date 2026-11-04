"""Synthetic-data guard. Fail closed: unreadable text is a hit, never a pass."""
from __future__ import annotations

import re

from .types import PiiBlocked, PiiHit, PiiPolicy, ShotList, step_text

# A dotted domain, so a real-looking address matches and `not-an-email` does not.
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# Separated groups only. Times like "Sat 10:00" and bare years do not match.
_PHONE = re.compile(
    r"(?<!\w)(?:"
    r"\+\d{1,3}[\s.\-]?\(?\d{1,4}\)?[\s.\-]?\d{2,4}[\s.\-]?\d{2,4}(?:[\s.\-]?\d{2,4})?"
    r"|\(\d{3}\)[\s.\-]?\d{3}[\s.\-]?\d{4}"
    r"|\d{3}[\s.\-]\d{3}[\s.\-]\d{4}"
    r")(?!\w)"
)


def scan(text: str | None, policy: PiiPolicy) -> tuple[PiiHit, ...]:
    """Hits in `text`. `None` means the page could not be read, which blocks."""
    if text is None:
        return (PiiHit("unreadable", "page text unavailable"),)
    hits: list[PiiHit] = []
    for match in _EMAIL.finditer(text):
        hits.append(PiiHit("email", match.group(0)))
    for match in _PHONE.finditer(text):
        hits.append(PiiHit("phone", match.group(0)))
    for name in policy.names:
        if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text, re.IGNORECASE):
            hits.append(PiiHit("name", name))
    return tuple(hits)


def guard(text: str | None, policy: PiiPolicy) -> None:
    hits = scan(text, policy)
    if hits:
        raise PiiBlocked(hits)


def guard_shotlist(shotlist: ShotList) -> None:
    """Refuse captions, typed text, and titles that already contain denylisted data."""
    parts = [shotlist.title]
    for step in shotlist.steps:
        text = step_text(step)
        if text:
            parts.append(text)
    guard("\n".join(parts), shotlist.pii)
