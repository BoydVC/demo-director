"""Propose a shot list from a page and, when there is one, a README."""
from __future__ import annotations

import os
import pathlib
import re
from urllib.parse import urlparse

DISCOVER_JS = """
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
      if (node.id) {
        parts.unshift("#" + CSS.escape(node.id));
        break;
      }
      const parent = node.parentElement;
      if (parent) {
        const same = [...parent.children].filter((child) => child.tagName === node.tagName);
        if (same.length > 1) part += `:nth-of-type(${same.indexOf(node) + 1})`;
      }
      parts.unshift(part);
      node = parent;
    }
    return parts.join(" > ");
  };
  const textOf = (el) => (el.innerText || el.value || "").trim().replace(/\\s+/g, " ").slice(0, 80);
  return {
    title: ((document.querySelector("h1") && document.querySelector("h1").innerText) || document.title || "Demo").trim(),
    headings: [...document.querySelectorAll("h1,h2,h3")].filter(visible).map((el) => ({
      level: el.tagName, text: textOf(el)
    })),
    controls: [...document.querySelectorAll("button, input, textarea, select, [role=button]")].filter(visible).map((el) => ({
      selector: cssOf(el),
      tag: el.tagName.toLowerCase(),
      type: (el.getAttribute("type") || "").toLowerCase(),
      text: textOf(el),
      placeholder: el.getAttribute("placeholder") || "",
      role: el.getAttribute("role") || ""
    })),
    results: [...document.querySelectorAll("#conf, #result, #output, [data-result], .result")].filter(visible).map(cssOf)
  };
}
"""

SAMPLE = "Sat 10:00 · Scrimmage, Field 1"
GENERIC = "Sample entry"


def readme_headings(text: str) -> list[str]:
    found = []
    for line in text.splitlines():
        match = re.match(r"#{1,3}\s+(.+?)\s*#*\s*$", line.strip())
        if match:
            found.append(match.group(1).strip())
    return found


def _find_readme(root: pathlib.Path) -> pathlib.Path | None:
    for name in ("README.md", "Readme.md", "readme.md"):
        candidate = root / name
        if candidate.is_file():
            return candidate
    return None


def _find_html(root: pathlib.Path) -> pathlib.Path | None:
    for candidate in (
        root / "fixtures" / "app.html",
        root / "index.html",
        root / "demo.html",
        root / "fixtures" / "index.html",
    ):
        if candidate.is_file():
            return candidate
    found = sorted((root / "fixtures").glob("*.html")) if (root / "fixtures").is_dir() else []
    return found[0] if found else None


def resolve(source: str) -> tuple[str, list[str]]:
    """Return a page URL and README headings for a url, HTML file, README, or repo root."""
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", source):
        return source, []
    path = pathlib.Path(source)
    if path.is_dir():
        readme = _find_readme(path)
        headings = readme_headings(readme.read_text()) if readme else []
        html = _find_html(path)
        if html is None:
            raise FileNotFoundError(f"no HTML fixture under {path}")
        return html.resolve().as_uri(), headings
    if path.suffix.lower() in {".md", ".markdown"} or path.name.lower().startswith("readme"):
        headings = readme_headings(path.read_text())
        html = _find_html(path.parent)
        if html is None:
            raise FileNotFoundError(f"no HTML fixture next to {path}")
        return html.resolve().as_uri(), headings
    if path.suffix.lower() in {".html", ".htm"}:
        return path.resolve().as_uri(), []
    raise FileNotFoundError(source)


def _portable_url(url: str) -> str:
    if not url.startswith("file:"):
        return url
    path = pathlib.Path(urlparse(url).path)
    try:
        relative = os.path.relpath(path, pathlib.Path.cwd())
    except ValueError:
        return url
    if relative.startswith(".."):
        return url
    return relative


def _trim(text: str, limit: int = 72) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _value_for(control: dict) -> str:
    hint = " ".join([
        control.get("placeholder") or "",
        control.get("text") or "",
        control.get("type") or "",
    ]).lower()
    if control.get("type") in {"email", "tel", "password"}:
        return GENERIC
    if any(key in hint for key in ("email", "e-mail", "phone", "tel", "ssn")):
        return GENERIC
    if any(key in hint for key in ("event", "plan", "task", "item", "add an")):
        return SAMPLE
    return GENERIC


def _button_rank(control: dict) -> int:
    text = (control.get("text") or "").lower()
    if any(key in text for key in ("find", "search", "conflict", "run", "check", "go")):
        return 2
    if any(key in text for key in ("add", "create", "save", "submit", "next")):
        return 1
    return 0


def _is_button(control: dict) -> bool:
    return control.get("tag") == "button" or control.get("type") in {"submit", "button"} or control.get("role") == "button"


def _is_field(control: dict) -> bool:
    if control.get("tag") not in {"input", "textarea"}:
        return False
    return control.get("type") not in {"hidden", "submit", "button", "checkbox", "radio", "file"}


def propose_steps(info: dict, readme_headings_list: list[str]) -> tuple[str, list[dict]]:
    """Turn discovered controls into a short demo with a beginning, a middle, and a hold."""
    headings = [item["text"] for item in info.get("headings", []) if item.get("text")]
    for heading in readme_headings_list:
        if heading not in headings:
            headings.append(heading)
    title = (info.get("title") or (headings[0] if headings else "") or "Demo").strip()
    controls = info.get("controls") or []
    fields = [control for control in controls if _is_field(control)]
    buttons = [control for control in controls if _is_button(control)]
    add = next((control for control in buttons if _button_rank(control) == 1), None)
    find = next((control for control in buttons if _button_rank(control) == 2), None)
    if add is None and buttons:
        add = max(buttons, key=_button_rank)
    if find is not None and add is not None and find["selector"] == add["selector"]:
        find = None
    if find is None:
        others = [control for control in buttons if add is None or control["selector"] != add["selector"]]
        find = others[0] if others else None

    steps: list[dict] = [{"action": "caption", "text": _trim(headings[0] if headings else title), "frames": 10}]
    if fields:
        field = fields[0]
        steps.append({"action": "type", "selector": field["selector"], "text": _value_for(field), "frames": 10})
    if add:
        steps.append({"action": "click", "selector": add["selector"], "frames": 8})
    if len(headings) > 1:
        steps.append({"action": "caption", "text": _trim(headings[1]), "frames": 8})
    if find:
        steps.append({"action": "click", "selector": find["selector"], "frames": 8})
    results = info.get("results") or []
    zoom = results[0] if results else (find or add or (fields[0] if fields else None))
    zoom_selector = zoom if isinstance(zoom, str) else (zoom or {}).get("selector")
    if zoom_selector:
        steps.append({"action": "zoom", "selector": zoom_selector, "frames": 12})
    steps.append({"action": "hold", "frames": 12})
    return title, steps


def discover(url: str) -> dict:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            page.goto(url)
            page.wait_for_timeout(80)
            return page.evaluate(DISCOVER_JS)
        finally:
            browser.close()


def build(source: str) -> dict:
    url, headings = resolve(source)
    title, steps = propose_steps(discover(url), headings)
    return {
        "title": title,
        "url": _portable_url(url),
        "aspects": ["16:9", "1:1", "9:16"],
        "fps": 12,
        "steps": steps,
    }
