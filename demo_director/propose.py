"""Propose a typed shot list from a page's visible controls and a README's headings.

The draft is runnable: a caption, a synthetic value in the first fields, the
visible buttons, a zoom onto the result, and a hold. Page text and README
text go through the same denylist as a render. A hit refuses the draft.
"""
from __future__ import annotations

import os
import pathlib
import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

from .guard import hits
from .session import RecordingFailure
from .shotlist import FIXTURE_URL, dump
from .types import (
    Aspect,
    Caption,
    Click,
    Draft,
    Hold,
    ShotList,
    ShotListError,
    SourceNote,
    Type,
    Zoom,
)

_HEADING = re.compile(r"^(#{1,3})[ \t]+(.+?)\s*#*\s*$", re.MULTILINE)
_HREF = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
_BARE_HTML = re.compile(r"(?<![\w/])([\w./-]+\.html?)(?![\w.])")
_ROOT = pathlib.Path(__file__).resolve().parents[1]

_DISCOVER_JS = """
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return r.width > 2 && r.height > 2 && style.visibility !== "hidden"
      && style.display !== "none" && r.bottom > 0 && r.right > 0;
  };
  const cssOf = (el) => {
    if (el.id) return "#" + CSS.escape(el.id);
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && node !== document.body) {
      let part = node.tagName.toLowerCase();
      if (node.id) { parts.unshift("#" + CSS.escape(node.id)); break; }
      const parent = node.parentElement;
      if (parent) {
        const same = [...parent.children].filter((child) => child.tagName === node.tagName);
        if (same.length > 1) part += ":nth-of-type(" + (same.indexOf(node) + 1) + ")";
      }
      parts.unshift(part);
      node = parent;
    }
    return parts.join(" > ");
  };
  const textOf = (el) => (el.innerText || el.value || el.getAttribute("placeholder") || "").trim().replace(/\\s+/g, " ").slice(0, 80);
  const inputs = [...document.querySelectorAll("input, textarea")].filter((el) => {
    const kind = (el.getAttribute("type") || "text").toLowerCase();
    return !["hidden", "submit", "button", "checkbox", "radio", "file"].includes(kind) && visible(el);
  }).slice(0, 4).map((el) => ({
    selector: cssOf(el),
    placeholder: el.getAttribute("placeholder") || "",
    type: (el.getAttribute("type") || "").toLowerCase()
  }));
  const buttons = [...document.querySelectorAll("button, [role=button], input[type=submit], input[type=button]")]
    .filter(visible).slice(0, 6)
    .map((el) => ({selector: cssOf(el), text: textOf(el)}));
  const title = ((document.querySelector("h1") && document.querySelector("h1").innerText) || document.title || "Demo").trim().slice(0, 80);
  const results = [...document.querySelectorAll("#conf, #status, #result, #output, [data-result], .result")]
    .filter(visible).slice(0, 2).map(cssOf);
  const text = document.body ? (document.body.innerText || "") : "";
  return {title, inputs, buttons, results, text};
}
"""


@dataclass(frozen=True)
class Control:
    selector: str
    kind: Literal["input", "button"]
    label: str


@dataclass(frozen=True)
class PageControls:
    title: str
    controls: tuple[Control, ...]


def headings_in(text: str) -> tuple[str, ...]:
    found = []
    for match in _HEADING.finditer(text):
        title = re.sub(r"[#*`]+", "", match.group(2))
        title = " ".join(title.split())
        if title:
            found.append(title)
    return tuple(found[:6])


def public_text(text: str, fallback: str) -> str:
    cleaned = " ".join((text or "").split())
    if not cleaned or hits(cleaned):
        return fallback
    return cleaned[:80]


def synthetic_value(placeholder: str) -> str:
    """A value we invent. Never the field's current contents, never contact data."""
    cleaned = placeholder.replace("…", "").replace("...", "").strip(" .")
    cleaned = " ".join(cleaned.split())
    if not cleaned or hits(cleaned):
        return "Sample"
    return f"{cleaned} (demo)"[:80]


def _find_html(root: pathlib.Path) -> pathlib.Path | None:
    for candidate in (
        root / "fixtures" / "app.html",
        root / "index.html",
        root / "demo.html",
        root / "fixtures" / "index.html",
    ):
        if candidate.is_file():
            return candidate
    if (root / "fixtures").is_dir():
        found = sorted((root / "fixtures").glob("*.html"))
        if found:
            return found[0]
    return None


def _page_url(readme: pathlib.Path, text: str) -> str | None:
    candidates = _HREF.findall(text) + _BARE_HTML.findall(text)
    for raw in candidates:
        target = raw.strip().strip("<>")
        if not target or target.startswith("#") or target.startswith("mailto:"):
            continue
        if target.startswith(("http://", "https://", "file://")):
            return target
        relative = (readme.parent / target).resolve()
        if relative.is_file():
            return relative.as_uri()
    return None


def _portable(url: str) -> str:
    if not url.startswith("file:"):
        return url
    path = pathlib.Path(urlparse(url).path)
    fixture = (_ROOT / "fixtures" / "app.html").resolve()
    try:
        if path.resolve() == fixture:
            return FIXTURE_URL
    except OSError:
        pass
    try:
        relative = os.path.relpath(path, pathlib.Path.cwd())
    except ValueError:
        return url
    if relative.startswith(".."):
        return url
    return relative


def _refuse(text: object) -> None:
    found = hits(text)
    if found:
        raise RecordingFailure("pii-blocked", -1, ",".join(found))


def draft_from(title: str, url: str, headings: tuple[str, ...] | list[str],
               page: PageControls | None, results: tuple[str, ...] = ()) -> Draft:
    steps = []
    notes: list[SourceNote] = []

    def add(step, kind: str, detail: str) -> None:
        notes.append(SourceNote(len(steps), kind, detail))
        steps.append(step)

    captions = [public_text(heading, "") for heading in headings]
    captions = [caption for caption in captions if caption]
    if not captions:
        fallback = page.title if page is not None else title
        captions = [public_text(fallback, "Demo")]
    for index, caption in enumerate(captions):
        kind = "heading" if headings else "title"
        add(Caption(caption, frames=8 if index == 0 else 6), kind, caption)
    if page is not None:
        for control in page.controls:
            if not str(control.selector).strip():
                continue
            if control.kind == "input":
                add(Type(control.selector, synthetic_value(control.label), frames=8), "input", control.selector)
            else:
                label = public_text(control.label, "Button")
                add(Click(control.selector, frames=6), "button", f"{label} {control.selector}".strip())
    for selector in results:
        if selector and selector.strip():
            add(Zoom(selector, frames=10), "result", selector)
            break
    add(Hold(frames=8), "tail", "hold")
    shot = ShotList(
        title=public_text(captions[0], "Demo"),
        url=url,
        steps=tuple(steps),
        aspects=(Aspect.WIDE, Aspect.SQUARE, Aspect.TALL),
    )
    return Draft(shot, tuple(notes))


def dump_draft(draft: Draft) -> dict:
    data = dump(draft.shotlist)
    data["sources"] = [
        {"index": note.index, "kind": note.kind, "detail": note.detail} for note in draft.notes
    ]
    return data


def probe(url: str) -> tuple[PageControls, tuple[str, ...], str]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            try:
                page.goto(url, timeout=15000)
                raw = page.evaluate(_DISCOVER_JS)
            except Exception as exc:
                raise RecordingFailure("timeout", -1, "navigation") from exc
        finally:
            browser.close()
    if not isinstance(raw, dict) or not isinstance(raw.get("text"), str):
        raise RecordingFailure("pii-blocked", -1, "unreadable")
    controls: list[Control] = []
    for field in raw.get("inputs") or []:
        selector = str(field.get("selector") or "")
        label = str(field.get("placeholder") or "")
        controls.append(Control(selector, "input", label))
    for button in raw.get("buttons") or []:
        selector = str(button.get("selector") or "")
        controls.append(Control(selector, "button", str(button.get("text") or "")))
    results = tuple(str(item) for item in (raw.get("results") or []) if item)
    return PageControls(title=str(raw.get("title") or "Demo"), controls=tuple(controls)), results, raw["text"]


def from_page(url: str, fallback: str = "Demo") -> Draft:
    page, results, text = probe(url)
    _refuse(text)
    return draft_from(page.title or fallback, _portable(url), (), page, results)


def from_readme(path: pathlib.Path) -> Draft:
    text = path.read_text()
    _refuse(text)
    headings = headings_in(text)
    url = _page_url(path, text)
    if url is None:
        html = _find_html(path.parent)
        url = html.resolve().as_uri() if html else None
    if url is None:
        if not headings:
            raise ShotListError(f"no headings in {path}")
        return draft_from(headings[0], FIXTURE_URL, headings, None)
    page, results, page_text = probe(url)
    _refuse(page_text)
    title = headings[0] if headings else page.title
    return draft_from(title, _portable(url), headings, page, results)


def init_source(source: str) -> Draft:
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", source):
        return from_page(source, "Demo")
    path = pathlib.Path(source)
    if path.is_file() and (path.suffix.lower() in {".md", ".markdown"} or path.name.lower().startswith("readme")):
        return from_readme(path)
    if path.is_dir():
        readme = None
        for name in ("README.md", "Readme.md", "readme.md"):
            candidate = path / name
            if candidate.is_file():
                readme = candidate
                break
        if readme is not None:
            return from_readme(readme)
        html = _find_html(path)
        if html is None:
            raise ShotListError(f"no HTML fixture under {path}")
        return from_page(html.resolve().as_uri(), path.name)
    if path.is_file():
        return from_page(path.resolve().as_uri(), path.stem.replace("-", " ").replace("_", " ").title())
    raise ShotListError(f"init source not found: {source}")
