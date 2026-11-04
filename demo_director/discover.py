"""Propose a typed shot list from a page's controls and a README's headings."""
from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass
from typing import Literal

from .pii import scan
from .shotlist import dump
from .types import (
    Aspect,
    Caption,
    Click,
    Draft,
    Hold,
    PiiPolicy,
    ShotList,
    ShotListError,
    SourceNote,
    Step,
    Type,
)

_HEADING = re.compile(r"^(#{1,3})[ \t]+(.+?)\s*$", re.MULTILINE)
_HREF = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
_BARE_HTML = re.compile(r"(?<![\w/])([\w./-]+\.html?)(?![\w.])")
_CLEAN = PiiPolicy()

_PROBE_JS = """() => {
  const vis = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 2 && r.height > 2 && s.visibility !== "hidden" && s.display !== "none" && s.opacity !== "0";
  };
  const sel = (el) => {
    if (el.id) return "#" + CSS.escape(el.id);
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && node.tagName !== "HTML") {
      let part = node.tagName.toLowerCase();
      if (node.id) { parts.unshift("#" + CSS.escape(node.id)); break; }
      const parent = node.parentElement;
      if (parent) {
        const same = [...parent.children].filter((child) => child.tagName === node.tagName);
        if (same.length > 1) part += ":nth-of-type(" + (same.indexOf(node) + 1) + ")";
      }
      parts.unshift(part);
      node = node.parentElement;
    }
    return parts.join(" > ");
  };
  const buttons = [...document.querySelectorAll("button, [role='button'], input[type='submit'], input[type='button']")]
    .filter(vis).slice(0, 6)
    .map((el) => ({selector: sel(el), text: (el.innerText || el.value || "").trim().slice(0, 48)}));
  const inputs = [...document.querySelectorAll("input, textarea")]
    .filter((el) => vis(el) && !["hidden", "submit", "button", "checkbox", "radio", "file"].includes((el.getAttribute("type") || "text").toLowerCase()))
    .slice(0, 4)
    .map((el) => ({selector: sel(el), placeholder: (el.getAttribute("placeholder") || "").trim().slice(0, 60)}));
  const title = (document.querySelector("h1") && document.querySelector("h1").innerText || document.title || "Demo").trim().slice(0, 80);
  return {title, buttons, inputs};
}"""


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
    return tuple(found[:4])


def public_text(text: str, fallback: str) -> str:
    cleaned = " ".join((text or "").split())
    if not cleaned or scan(cleaned, _CLEAN):
        return fallback
    return cleaned[:80]


def synthetic_value(placeholder: str) -> str:
    """A value we invent. Never the field's current contents, never contact data."""
    cleaned = placeholder.replace("…", "").replace("...", "").strip(" .")
    cleaned = " ".join(cleaned.split())
    if not cleaned or scan(cleaned, _CLEAN):
        return "Sample"
    return f"{cleaned} (demo)"[:80]


def draft_from(title: str, url: str, headings: tuple[str, ...], page: PageControls | None) -> Draft:
    steps: list[Step] = []
    notes: list[SourceNote] = []

    def add(step: Step, kind: str, detail: str) -> None:
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
            if not control.selector.strip():
                continue
            if control.kind == "input":
                value = synthetic_value(control.label)
                add(Type(control.selector, value, frames=8), "input", control.selector)
            else:
                label = public_text(control.label, "Button")
                add(Click(control.selector, frames=6), "button", f"{label} {control.selector}".strip())
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


def probe(url: str) -> PageControls:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            page.goto(url)
            raw = page.evaluate(_PROBE_JS)
        except Exception as exc:
            raise ShotListError(f"could not read {url}") from exc
        finally:
            browser.close()
    if not isinstance(raw, dict):
        raise ShotListError(f"could not read {url}")
    controls: list[Control] = []
    for field in raw.get("inputs") or []:
        selector = str(field.get("selector") or "")
        if selector:
            controls.append(Control(selector, "input", str(field.get("placeholder") or "")))
    for button in raw.get("buttons") or []:
        selector = str(button.get("selector") or "")
        if selector:
            controls.append(Control(selector, "button", str(button.get("text") or "")))
    return PageControls(title=str(raw.get("title") or "Demo"), controls=tuple(controls))


def from_url(url: str, fallback_title: str = "Demo") -> Draft:
    page = probe(url)
    return draft_from(fallback_title, url, (), page)


def from_readme(path: pathlib.Path) -> Draft:
    text = path.read_text()
    found = headings_in(text)
    url = _page_url(path, text)
    page = probe(url) if url else None
    if url is None:
        url = path.resolve().as_uri()
    title = found[0] if found else path.stem
    return draft_from(title, url, found, page)


def init_source(source: str) -> Draft:
    path = pathlib.Path(source)
    if path.is_file() and (path.suffix.lower() in {".md", ".markdown"} or path.name.lower().startswith("readme")):
        return from_readme(path)
    if path.is_file():
        return from_url(path.resolve().as_uri(), path.stem.replace("-", " ").replace("_", " ").title())
    if "://" not in source:
        raise ShotListError(f"init source not found: {source}")
    return from_url(source, "Demo")
