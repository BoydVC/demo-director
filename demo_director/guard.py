"""Synthetic-data guard.

Emails and phone numbers always block. Names block when they are listed on
the shot list (``deny_names``) or passed in by the caller. The guard fails
closed: unreadable text, a non-string, or a denylist entry too short to match
safely is a hit, and the render does not proceed.
"""
from __future__ import annotations

import re
from collections.abc import Iterable

from .session import RecordingFailure

# Real-looking addresses. Two-label host so "a@b" does not match.
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# Separated 3-3-4, optional country code or parentheses. Digit runs without
# separators (years, hex colors, "10:30") do not match.
_PHONE = re.compile(
    r"(?<!\d)(?:"
    r"\+\d{1,3}[\s.\-]?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}"
    r"|\(\d{3}\)\s*\d{3}[\s.\-]\d{4}"
    r"|\d{3}[\s.\-]\d{3}[\s.\-]\d{4}"
    r")(?!\d)"
)


def hits(text: object, names: Iterable[str] = ()) -> tuple[str, ...]:
    """Return the reasons ``text`` is not synthetic-clean. Empty means clean."""
    if not isinstance(text, str):
        return ("unreadable",)
    found: list[str] = []
    try:
        if _EMAIL.search(text):
            found.append("email")
        if _PHONE.search(text):
            found.append("phone")
        folded = text.casefold()
        for name in names:
            if not isinstance(name, str):
                return ("unreadable",)
            token = name.strip()
            if not token:
                continue
            if len(token) < 3:
                found.append("name-policy")
                continue
            if token.casefold() in folded:
                found.append("name")
    except Exception:
        return ("unreadable",)
    return tuple(found)


def assert_clean(text: object, names: Iterable[str] = (), step_index: int = -1) -> None:
    """Raise ``RecordingFailure(pii-blocked)`` when ``hits`` is non-empty."""
    found = hits(text, names)
    if found:
        raise RecordingFailure("pii-blocked", step_index, ",".join(found))
