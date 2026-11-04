"""Synthetic-data guard.

Emails and phone numbers block. Names block when they are listed on the shot
list (``deny_names``) or passed in by the caller. RFC 2606 reserved domains
are synthetic and do not block: example.com, example.org, example.net (and
their subdomains), plus any host under .test or .example. The guard fails
closed: unreadable text, a non-string, or a denylist entry too short to match
safely is a hit, and the render does not proceed.
"""
from __future__ import annotations

import os
import re
import unicodedata
from collections.abc import Iterable
from pathlib import Path

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


# Optional derivation (--derive-names, off by default). Not a capitalization scan.
_FOR_PERSON = re.compile(r"\bfor ([A-Z][a-z]{3,}) ([A-Z][a-z]{3,})\b")
_POSS_NOUN = re.compile(r"\b([A-Z][a-z]{3,})'s ([A-Za-z][A-Za-z]+)\b")
def normalize_text(text: str) -> str:
    """NFKC, straight apostrophes, case-fold, possessive ``'s`` removed.

    Letter-spaced runs such as ``B O Y D`` are collapsed so a deny term can
    see the word the letters spell. Ordinary words keep their boundaries, so
    a shorter term does not match inside a longer word.
    """
    if not isinstance(text, str):
        return ""
    folded = unicodedata.normalize("NFKC", text)
    folded = folded.replace("\u2019", "'").replace("\u2018", "'").replace("`", "'")
    folded = folded.casefold()
    folded = re.sub(r"(?<=\w)'s\b", "", folded)
    folded = re.sub(r"\s+", " ", folded)
    return folded.strip()


def _collapse_letter_runs(text: str) -> str:
    def join(match: re.Match[str]) -> str:
        return match.group(0).replace(" ", "")

    return re.sub(r"(?<![a-z0-9])(?:[a-z]\s+){2,}[a-z](?![a-z0-9])", join, text)


def term_present(text: str, term: str) -> bool:
    """True when ``term`` is its own word in ``text``, or spelled letter by letter."""
    if not isinstance(text, str) or not isinstance(term, str):
        return False
    needle = normalize_text(term)
    hay = normalize_text(text)
    if not needle or not hay:
        return False
    if re.search(rf"(?<![\w]){re.escape(needle)}(?![\w])", hay):
        return True
    if needle.startswith("@"):
        return False
    # A letter-spaced run spells a word. "b o y d fridge" collapses to a string
    # that still contains the denied word even when other letters follow.
    for match in re.finditer(r"(?<![a-z0-9])(?:[a-z]\s+){2,}[a-z](?![a-z0-9])", hay):
        if needle in match.group(0).replace(" ", ""):
            return True
    return False


def load_deny_terms() -> tuple[str, ...]:
    """Terms from ``DEMO_DIRECTOR_DENY`` and ``.demo-director/deny.txt``.

    One term per line. Blank lines and ``#`` comments are skipped. ``@handles``
    are kept as written. This file is operator config and is not part of the repo.
    """
    paths: list[Path] = []
    env = os.environ.get("DEMO_DIRECTOR_DENY")
    if env:
        paths.append(Path(env))
    local = Path.cwd() / ".demo-director" / "deny.txt"
    if local.is_file():
        paths.append(local)
    found: list[str] = []
    for path in paths:
        try:
            raw = path.read_text()
        except OSError:
            continue
        for line in raw.splitlines():
            term = line.split("#", 1)[0].strip()
            if term and term not in found:
                found.append(term)
    return tuple(found)


def _title_words(title: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", normalize_text(title)))


def derive_names(text: str, title: str = "") -> tuple[str, ...]:
    """Optional names from ``for First Last`` and ``Name's Noun`` only.

    Words that belong to ``title`` are not returned, so a repo cannot block
    on its own title. This is not used unless ``--derive-names`` is set.
    There is no two-word capitalization scan and no ``@type`` pickup.
    """
    if not isinstance(text, str) or not text:
        return ()
    skip = _title_words(title)
    found: list[str] = []

    def add(name: str) -> None:
        cleaned = " ".join(name.split())
        if not cleaned or cleaned.casefold() in skip or cleaned in found:
            return
        found.append(cleaned)

    for match in _FOR_PERSON.finditer(text):
        first, last = match.group(1), match.group(2)
        if first.casefold() in skip or last.casefold() in skip:
            continue
        add(f"{first} {last}")
        add(first)
        add(last)
    for match in _POSS_NOUN.finditer(text):
        add(match.group(1))
    return tuple(found)


def derived_names(text: str, title: str = "") -> tuple[str, ...]:
    return derive_names(text, title)


def locate_denied(terms: Iterable[str], *, page: str = "", page_source: str = "",
                  opened: str | None = None, title: str = "",
                  captions: Iterable[str] = ()) -> tuple[str, ...]:
    """One ``term:place`` entry per denied term that is actually present.

    ``page`` is text visible on screen. ``page_source`` is the rest of the
    document (hidden nodes, a closed dialog, the document title). ``opened``
    is the visible text from the first paint; pass it after a click so a
    term that was not on screen yet is ``page-click``. ``title`` covers the
    title card and the end card. Each term is reported once.
    """
    caption_list = [part for part in captions if part]
    found: list[str] = []
    seen: set[str] = set()
    for term in terms:
        if not isinstance(term, str) or not term.strip():
            continue
        token = " ".join(term.split())
        key = normalize_text(token)
        if not key or key in seen:
            continue
        places: list[str] = []
        if title and term_present(title, token):
            places.append("title")
        if any(term_present(part, token) for part in caption_list):
            places.append("caption")
        visible = bool(page) and term_present(page, token)
        if visible:
            if opened is not None and not term_present(opened, token):
                places.append("page-click")
            else:
                places.append("page")
        elif page_source and term_present(page_source, token):
            places.append("page-source")
        if not places:
            continue
        seen.add(key)
        found.append(f"{token}:{'+'.join(places)}")
    return tuple(found)


def name_detail(parts: Iterable[str]) -> str:
    """Stable ``name:term:place`` detail with each term once."""
    return "name:" + ",".join(parts)


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
        for name in names:
            if not isinstance(name, str):
                return ("unreadable",)
            token = name.strip()
            if not token:
                continue
            if len(token) < 3 and not token.startswith("@"):
                found.append("name-policy")
                continue
            if term_present(text, token):
                found.append("name")
    except Exception:
        return ("unreadable",)
    return tuple(found)


def assert_clean(text: object, names: Iterable[str] = (), step_index: int = -1) -> None:
    """Raise ``RecordingFailure(pii-blocked)`` when ``hits`` is non-empty."""
    found = hits(text, names)
    if found:
        raise RecordingFailure("pii-blocked", step_index, ",".join(found))
