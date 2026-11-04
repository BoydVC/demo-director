"""Propose a shot list from a page's visible controls or a README's headings."""
from __future__ import annotations

import os
from pathlib import Path

from .guard import hits
from .session import RecordingFailure
from .shotlist import ShotListError

_EXTRACT_JS = """() => {
  const vis = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 1 && r.height > 1 && s.visibility !== "hidden" && s.display !== "none";
  };
  const selectorFor = (el) => {
    if (el.id) return "#" + CSS.escape(el.id);
    const tag = el.tagName.toLowerCase();
    const parent = el.parentElement;
    if (!parent) return tag;
    const same = [...parent.children].filter((c) => c.tagName === el.tagName);
    const nth = same.indexOf(el) + 1;
    return selectorFor(parent) + " > " + tag + ":nth-of-type(" + nth + ")";
  };
  const textOf = (el) => (el.innerText || el.value || el.getAttribute("aria-label") || "").trim();
  const inputs = [...document.querySelectorAll("input, textarea")].filter((el) => {
    const kind = (el.getAttribute("type") || "text").toLowerCase();
    return !["hidden", "submit", "button", "checkbox", "radio", "file", "password"].includes(kind) && vis(el);
  }).map((el) => ({selector: selectorFor(el)}));
  const buttons = [...document.querySelectorAll("button, [role=button], input[type=submit]")].filter(vis)
    .map((el) => ({selector: selectorFor(el), text: textOf(el)}));
  const h1 = document.querySelector("h1");
  return {
    title: document.title || "",
    h1: h1 ? (h1.innerText || "").trim() : "",
    text: document.body ? (document.body.innerText || "") : "",
    inputs, buttons
  };
}"""


def _is_readme(target: str) -> bool:
    name = os.path.basename(target).lower()
    return name.startswith("readme") or name.endswith(".md")


def from_readme(path: str) -> dict:
    raw = Path(path).read_text()
    found = hits(raw, ())
    if found:
        raise RecordingFailure("pii-blocked", -1, ",".join(found))
    headings: list[str] = []
    title = None
    for line in raw.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            headings.append(title)
        elif line.startswith("## "):
            headings.append(line[3:].strip())
    headings = [h for h in headings if h][:8]
    if not headings:
        raise ShotListError(f"no headings in {path}")
    steps = [{"action": "caption", "text": h[:80], "frames": 8} for h in headings]
    steps.append({"action": "hold", "frames": 8})
    return {
        "title": (title or headings[0])[:80],
        "url": "FIXTURE",
        "aspects": ["16:9", "1:1", "9:16"],
        "steps": steps,
    }


def from_url(target: str) -> dict:
    from playwright.sync_api import sync_playwright

    if "://" in target:
        url = target
    else:
        url = Path(target).resolve().as_uri()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            try:
                page.goto(url, timeout=15000)
                data = page.evaluate(_EXTRACT_JS)
            except RecordingFailure:
                raise
            except Exception as exc:
                raise RecordingFailure("timeout", -1, "navigation") from exc
        finally:
            browser.close()
    if not isinstance(data, dict):
        raise RecordingFailure("pii-blocked", -1, "unreadable")
    found = hits(data.get("text"), ())
    if found:
        raise RecordingFailure("pii-blocked", -1, ",".join(found))
    heading = (data.get("h1") or data.get("title") or "Demo").strip() or "Demo"
    steps: list[dict] = [{"action": "caption", "text": heading[:80], "frames": 8}]
    for inp in (data.get("inputs") or [])[:8]:
        sel = inp.get("selector")
        if sel:
            steps.append({"action": "type", "selector": sel, "text": "Sample value", "frames": 8})
    for btn in (data.get("buttons") or [])[:8]:
        sel = btn.get("selector")
        if sel:
            steps.append({"action": "click", "selector": sel, "frames": 6})
    steps.append({"action": "hold", "frames": 8})
    title = (data.get("title") or heading).strip() or heading
    return {
        "title": title[:80],
        "url": url,
        "aspects": ["16:9", "1:1", "9:16"],
        "steps": steps,
    }


def propose(target: str) -> dict:
    """``demo-director init <url|README>``. README headings, or the page's controls."""
    if _is_readme(target) and "://" not in target:
        return from_readme(target)
    return from_url(target)
