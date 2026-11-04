"""Fail-closed synthetic-data guard.

Emails and phone numbers always block a render. Names block when the shot
list lists them. If the page text cannot be read, the render is refused.
"""
from __future__ import annotations

import re

EMAIL_RE = re.compile(r"\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b", re.I)
# Grouped NANP / international numbers. Times like "10:00" do not match.
PHONE_RE = re.compile(
    r"(?<!\w)(?:"
    r"\+\d{1,3}[\s.\-]?\(?\d{2,4}\)?[\s.\-]?\d{3,4}[\s.\-]?\d{3,4}"
    r"|\(?\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}"
    r")(?!\w)"
)


class PiiBlocked(RuntimeError):
    def __init__(self, kind: str, detail: str = ""):
        self.kind = kind
        self.detail = detail
        super().__init__(f"pii-blocked: {kind}")


def _redact(kind: str, raw: str) -> str:
    if kind == "email" and "@" in raw:
        local, _, domain = raw.partition("@")
        return (local[:1] or "*") + "***@" + domain
    if kind == "phone":
        digits = re.sub(r"\D", "", raw)
        return "***-***-" + digits[-4:]
    if kind == "name":
        return " ".join((part[:1] + "***") for part in raw.split())
    return kind


def scan(text: str | None, names: tuple[str, ...] | list[str] = ()) -> list[tuple[str, str]]:
    """Return (kind, redacted) hits. Unreadable text fails closed."""
    if text is None:
        raise PiiBlocked("unreadable")
    hits: list[tuple[str, str]] = []
    for match in EMAIL_RE.finditer(text):
        hits.append(("email", _redact("email", match.group(0))))
    for match in PHONE_RE.finditer(text):
        hits.append(("phone", _redact("phone", match.group(0))))
    for name in names:
        cleaned = str(name).strip()
        if len(cleaned) < 2:
            continue
        if re.search(rf"(?<!\w){re.escape(cleaned)}(?!\w)", text, re.I):
            hits.append(("name", _redact("name", cleaned)))
    return hits


def assert_clean(text: str | None, names: tuple[str, ...] | list[str] = ()) -> None:
    hits = scan(text, names)
    if hits:
        kind, detail = hits[0]
        raise PiiBlocked(kind, detail)


def shotlist_blob(sl) -> str:
    parts = [sl.title or "", sl.url or ""]
    for step in sl.steps:
        parts.extend([step.text or "", step.selector or "", step.url or ""])
    return "\n".join(parts)
