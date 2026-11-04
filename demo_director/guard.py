"""Synthetic-data guard.

Emails and phone numbers block. Names block when they are listed on the shot
list (``deny_names``) or passed in by the caller. RFC 2606 reserved domains
are synthetic and do not block: example.com, example.org, example.net (and
their subdomains), plus any host under .test or .example. The guard fails
closed: unreadable text, a non-string, or a denylist entry too short to match
safely is a hit, and the render does not proceed.
"""
from __future__ import annotations

import re
from collections.abc import Iterable

from .session import RecordingFailure

# Real-looking addresses. Two-label host so "a@b" does not match.
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# RFC 2606. example.com / .org / .net, any subdomain of those, and the
# .test and .example TLDs. Matched against the host only, so notexample.com
# and example.com.evil.org stay blocked.
_RESERVED_HOST = re.compile(
    r"(?i)^(?:"
    r"(?:[a-z0-9-]+\.)*example\.(?:com|org|net)"
    r"|(?:[a-z0-9-]+\.)*example"
    r"|(?:[a-z0-9-]+\.)+test"
    r")$"
)
# Separated 3-3-4, optional country code or parentheses. Digit runs without
# separators (years, hex colors, "10:30") do not match.
_PHONE = re.compile(
    r"(?<!\d)(?:"
    r"\+\d{1,3}[\s.\-]?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}"
    r"|\(\d{3}\)\s*\d{3}[\s.\-]\d{4}"
    r"|\d{3}[\s.\-]\d{3}[\s.\-]\d{4}"
    r")(?!\d)"
)


# Given names implied by "Alex's" or "for Alex Example". Short tokens and
# ordinary title words stay out so "Men's" and "Saturday session" do not block.
_POSSESSIVE = re.compile(r"\b([A-Z][a-z]{3,})'s\b")
_FOR_PERSON = re.compile(r"\bfor ([A-Z][a-z]{3,}) ([A-Z][a-z]{3,})\b")
_NAME_STOP = frozenset({
    "today", "tonight", "saturday", "sunday", "monday", "tuesday", "wednesday",
    "thursday", "friday", "weekend", "team", "demo", "user", "home", "game",
    "board", "room", "click", "page", "error", "loading", "synthetic", "planner",
    "check", "player", "locker", "keyboard", "practice", "session", "match",
    "night", "stats", "item", "field", "time", "year", "week", "coach", "club",
    "city", "park", "sample", "example", "test", "app", "data", "only",
})


def derived_names(text: str) -> tuple[str, ...]:
    """Names a README or title gives away: a possessive, or 'for First Last'."""
    if not isinstance(text, str) or not text:
        return ()
    found: list[str] = []

    def add(name: str) -> None:
        cleaned = " ".join(name.split())
        if not cleaned or cleaned.casefold() in _NAME_STOP:
            return
        if cleaned not in found:
            found.append(cleaned)

    for match in _POSSESSIVE.finditer(text):
        add(match.group(1))
    for match in _FOR_PERSON.finditer(text):
        first, last = match.group(1), match.group(2)
        if first.casefold() in _NAME_STOP:
            continue
        add(f"{first} {last}")
        add(first)
    return tuple(found)


def names_to_block(text: str, allow: Iterable[str] = ()) -> tuple[str, ...]:
    """Derived names that are not on the allowlist. Empty means render may proceed."""
    allowed = {part.casefold() for part in allow if isinstance(part, str)}
    return tuple(name for name in derived_names(text) if name.casefold() not in allowed)


def _reserved_email(address: str) -> bool:
    host = address.rsplit("@", 1)[-1]
    return _RESERVED_HOST.fullmatch(host) is not None


def hits(text: object, names: Iterable[str] = ()) -> tuple[str, ...]:
    """Return the reasons ``text`` is not synthetic-clean. Empty means clean."""
    if not isinstance(text, str):
        return ("unreadable",)
    found: list[str] = []
    try:
        if any(not _reserved_email(match.group(0)) for match in _EMAIL.finditer(text)):
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
