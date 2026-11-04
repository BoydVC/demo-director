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


# Watch terms, not an automatic block. A README can mention a person; the
# render blocks only when that term shows up in the page or a caption.
_POSSESSIVE = re.compile(r"\b([A-Z][a-z]{2,})'s\b")
_PAIR = re.compile(r"\b([A-Z][a-z]{2,}) ([A-Z][a-z]{2,})\b")
_HANDLE = re.compile(r"(?<![\w])@([A-Za-z][A-Za-z0-9_]{2,})")
# Common words and places. "Earth's", "What's", "Reader's", "Oriole Park" are not people.
_NOT_PERSON = frozenset({
    "today", "tonight", "tomorrow", "saturday", "sunday", "monday", "tuesday",
    "wednesday", "thursday", "friday", "weekend", "week", "year", "month", "time",
    "day", "night", "morning", "evening", "team", "demo", "user", "home", "game",
    "board", "room", "click", "page", "error", "loading", "synthetic", "planner",
    "check", "kit", "player", "locker", "keyboard", "kickoff", "practice", "session",
    "match", "stats", "item", "field", "coach", "club", "sample", "example",
    "test", "app", "data", "only", "what", "when", "where", "which", "who",
    "whose", "whom", "how", "why", "this", "that", "these", "those", "there",
    "here", "your", "their", "ours", "they", "them", "then", "than", "earth",
    "world", "reader", "group", "island", "path", "life", "love", "best", "new",
    "old", "first", "last", "next", "every", "each", "all", "some", "many",
    "more", "most", "other", "such", "into", "from", "with", "have", "been",
    "were", "will", "just", "like", "make", "made", "over", "after", "before",
    "about", "again", "still", "also", "back", "down", "left", "right", "open",
    "close", "start", "stop", "play", "goal", "ball", "score", "court", "season",
    "round", "final", "super", "grand", "main", "side", "full", "half", "high",
    "long", "short", "small", "large", "great", "little", "public", "local",
    "total", "whole", "ready", "orange", "cones", "today", "archive", "times",
    "roster", "badge", "drill", "timeline", "standing", "standings", "control",
    "mission", "atlas", "bookmark", "galaxy", "constellation", "author", "card",
    "cards", "note", "notes", "list", "lists", "map", "chart", "graph", "code",
    "site", "blog", "post", "feed", "news", "story", "photo", "image", "video",
    "audio", "music", "song", "show", "film", "movie", "sport", "sports",
    "men", "women", "kids", "folks", "people",
    "maryland", "basketball", "weekend", "director", "synthetic", "saturday",
    "keyboard", "practice", "locker", "player", "session", "planner", "check",
    "chelsea", "oriole", "orioles", "brooklyn", "london", "paris", "york",
    "baltimore", "annapolis", "manhattan", "harlem", "mars", "venus", "jupiter",
    "saturn", "moon", "star", "stars", "land", "sky", "sun", "park", "city",
    "bay", "river", "hill", "lake", "mountain", "street", "road", "county",
    "stadium", "arena", "garden", "harbor", "harbour", "beach", "valley",
    "creek", "ridge", "heights", "square", "plaza", "bridge", "port", "coast",
    "shore", "point", "falls", "springs", "woods", "forest", "desert", "ocean",
    "sea", "gulf", "isle", "mount", "fort", "village", "town", "district",
    "yard", "dome", "grounds", "pitch", "range", "course", "track", "pool",
    "rink", "gym", "school", "college", "university", "library", "museum",
    "market", "center", "centre", "station", "airport", "tower", "building",
    "house", "hall", "palace", "castle", "gate", "wall", "avenue", "lane",
    "drive", "place", "trail", "north", "south", "east", "west", "central",
    "upper", "lower", "inner", "outer", "royal", "national", "state", "united",
    "american", "group", "path", "reader", "earth", "island", "what",
})
_GENERIC_HANDLES = frozenset({
    "demo", "example", "test", "synthetic", "user", "admin", "guest", "team", "bot", "sample",
})


def _person_token(word: str) -> bool:
    token = word.casefold()
    return len(token) >= 3 and token not in _NOT_PERSON


def _place_word(word: str) -> bool:
    return word.casefold() in _NOT_PERSON and word.casefold() in {
        "park", "island", "city", "bay", "river", "hill", "lake", "mountain",
        "street", "road", "county", "stadium", "arena", "garden", "harbor",
        "harbour", "beach", "valley", "creek", "ridge", "heights", "square",
        "plaza", "bridge", "port", "coast", "shore", "point", "falls", "springs",
        "woods", "forest", "desert", "ocean", "sea", "gulf", "isle", "mount",
        "fort", "village", "town", "district", "yard", "dome", "grounds", "pitch",
        "court", "field", "range", "course", "track", "center", "centre",
        "station", "tower", "hall", "house", "place", "chelsea", "york",
        "brooklyn", "london", "paris", "baltimore", "annapolis", "maryland",
        "oriole", "orioles",
    }


def watch_terms(text: str) -> tuple[str, ...]:
    """Person-like terms and @handles in a README or title. Not a block by itself.

    Places and ordinary words are ignored. A ``First Last`` match also watches
    the surname. ``--deny-name`` does not go through this filter.
    """
    if not isinstance(text, str) or not text:
        return ()
    found: list[str] = []

    def add(name: str) -> None:
        cleaned = " ".join(name.split())
        if not cleaned or cleaned in found:
            return
        found.append(cleaned)

    for match in _HANDLE.finditer(text):
        handle = match.group(1)
        if handle.casefold() in _GENERIC_HANDLES:
            continue
        add("@" + handle)
    for match in _POSSESSIVE.finditer(text):
        word = match.group(1)
        if _person_token(word):
            add(word)
    for match in _PAIR.finditer(text):
        first, last = match.group(1), match.group(2)
        if not _person_token(first) or not _person_token(last):
            continue
        if _place_word(last) or _place_word(first):
            continue
        add(f"{first} {last}")
        add(first)
        add(last)
    return tuple(found)


def derived_names(text: str) -> tuple[str, ...]:
    """Watch terms a README or title gives away. Seeing one is not yet a block."""
    return watch_terms(text)


def names_to_block(text: str, allow: Iterable[str] = ()) -> tuple[str, ...]:
    """Watch terms left after the allowlist. The caller still has to see them rendered."""
    allowed = {part.casefold() for part in allow if isinstance(part, str)}
    return tuple(name for name in watch_terms(text) if name.casefold() not in allowed)


def _contains_term(term: str, text: str) -> bool:
    if not term or not text:
        return False
    if term.startswith("@"):
        return term.casefold() in text.casefold()
    return re.search(rf"(?<![\w]){re.escape(term)}(?![\w])", text, re.IGNORECASE) is not None


def matched_terms(terms: Iterable[str], *, page: str = "", title: str = "",
                  captions: Iterable[str] = ()) -> tuple[str, ...]:
    """``term:where`` for watch or deny terms that actually appear.

    ``where`` is page, caption, or title. A term that is only in a README is
    not listed. Explicit deny names use the same locator and stay strict:
    they are not dropped for being common words.
    """
    caption_text = "\n".join(part for part in captions if part)
    found: list[str] = []
    for term in terms:
        if not isinstance(term, str) or not term.strip():
            continue
        token = " ".join(term.split())
        if _contains_term(token, page):
            found.append(f"{token}:page")
        elif _contains_term(token, caption_text):
            found.append(f"{token}:caption")
        elif _contains_term(token, title):
            found.append(f"{token}:title")
    return tuple(found)


def page_handles(text: str) -> tuple[str, ...]:
    """Non-generic @handles written on a page or caption. They are already seen."""
    if not isinstance(text, str) or not text:
        return ()
    found: list[str] = []
    for match in _HANDLE.finditer(text):
        handle = match.group(1)
        if handle.casefold() in _GENERIC_HANDLES:
            continue
        token = "@" + handle
        if token not in found:
            found.append(token)
    return tuple(found)


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
