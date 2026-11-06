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

from .guard import derive_names, effective_names, hits, locate_denied, name_detail
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

_DISCOVER_JS = r"""
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
  const textOf = (el) => (el.innerText || el.value || el.getAttribute("placeholder") || "").trim().replace(/\s+/g, " ").slice(0, 80);
  const accessibleName = (el) => {
    const labelledby = el.getAttribute("aria-labelledby");
    if (labelledby) {
      const text = labelledby.split(/\s+/).map((id) => {
        const node = document.getElementById(id);
        return node ? (node.innerText || "") : "";
      }).join(" ").replace(/\s+/g, " ").trim();
      if (text) return text.slice(0, 80);
    }
    const aria = (el.getAttribute("aria-label") || "").replace(/\s+/g, " ").trim();
    if (aria) return aria.slice(0, 80);
    if (el.id) {
      const lab = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (lab && (lab.innerText || "").trim()) return lab.innerText.replace(/\s+/g, " ").trim().slice(0, 80);
    }
    return textOf(el);
  };
  const implicitRole = (el) => {
    const explicit = (el.getAttribute("role") || "").trim().toLowerCase();
    if (explicit) return explicit;
    const tag = el.tagName;
    if (tag === "BUTTON") return "button";
    if (tag === "TEXTAREA") return "textbox";
    if (tag === "A" && el.hasAttribute("href")) return "link";
    if (tag === "INPUT") {
      const t = (el.getAttribute("type") || "text").toLowerCase();
      if (t === "button" || t === "submit") return "button";
      return "textbox";
    }
    return "";
  };
  const quote = (value) => String(value).replace(/\\/g, "\\\\").replace(/"/g, '\\"');
  const named = (role, name) => [...document.querySelectorAll("button, a, input, textarea, [role]")].filter((el) => {
    return implicitRole(el) === role && accessibleName(el) === name;
  });
  // data-testid, id, then role/text. nth-of-type is only the last resort.
  const selectorOf = (el) => {
    const testid = el.getAttribute("data-testid");
    if (testid) return '[data-testid="' + CSS.escape(testid) + '"]';
    if (el.id) return "#" + CSS.escape(el.id);
    const role = implicitRole(el);
    const name = accessibleName(el);
    if (role && name && named(role, name).length === 1) {
      return 'role=' + role + '[name="' + quote(name) + '"]';
    }
    if (name && (el.tagName === "BUTTON" || role === "button")) {
      const count = named(role || "button", name).length;
      if (count === 1) return 'text="' + quote(name) + '"';
    }
    return cssOf(el);
  };
  const inputs = [...document.querySelectorAll("input, textarea")].filter((el) => {
    const kind = (el.getAttribute("type") || "text").toLowerCase();
    return !["hidden", "submit", "button", "checkbox", "radio", "file"].includes(kind) && visible(el);
  }).slice(0, 4).map((el) => ({
    selector: selectorOf(el),
    placeholder: el.getAttribute("placeholder") || "",
    type: (el.getAttribute("type") || "text").toLowerCase()
  }));
  const buttons = [...document.querySelectorAll("button, [role=button], input[type=submit], input[type=button]")]
    .filter(visible).slice(0, 6)
    .map((el) => ({selector: selectorOf(el), text: textOf(el)}));
  const h1 = document.querySelector("h1");
  // textContent, not innerText: CSS text-transform must not change the title.
  const heading = h1 ? (h1.textContent || "").replace(/\s+/g, " ").trim() : "";
  const docTitle = (document.title || "").replace(/\s+/g, " ").trim();
  const title = (heading || docTitle || "Demo").slice(0, 80);
  const results = [...document.querySelectorAll("#conf, #status, #result, #output, [data-result], .result")]
    .filter(visible).slice(0, 2).map(selectorOf);
  const text = document.body ? (document.body.innerText || "") : "";
  return {title, heading, documentTitle: docTitle, inputs, buttons, results, text};
}
"""


@dataclass(frozen=True)
class Control:
    selector: str
    kind: Literal["input", "button"]
    label: str
    input_type: str = ""


@dataclass(frozen=True)
class PageControls:
    title: str
    controls: tuple[Control, ...]
    heading: str = ""
    document_title: str = ""


def headings_in(text: str) -> tuple[str, ...]:
    found = []
    for match in _HEADING.finditer(text):
        title = re.sub(r"[#*`]+", "", match.group(2))
        title = " ".join(title.split())
        if title:
            found.append(title)
    return tuple(found[:6])


# Short tokens that stay uppercase when a shouted title is normalized.
_ACRONYMS = frozenset({"API", "HQ", "MLB"})


def _case_word(token: str) -> str:
    if token.upper() in _ACRONYMS:
        return token.upper()
    if not token:
        return token
    return token[:1].upper() + token[1:].lower()


def human_title(slug: str) -> str:
    """Title-case a repo slug, splitting only on hyphens and underscores.

    ``four-game-jam`` becomes ``Four Game Jam``. A compound word with no
    separator stays whole.
    """
    parts = [part for part in re.split(r"[-_]+", (slug or "").strip()) if part]
    titled = " ".join(_case_word(part) for part in parts)
    return titled or "Demo"


def normalize_title(text: str) -> str:
    """Title-case an all-caps heading or a hyphenated token.

    Mixed-case headings stay as written, so ``Starship Tower`` is unchanged.
    ``CHOPSTICKS`` becomes ``Chopsticks`` and ``Four-Game Jam`` becomes
    ``Four Game Jam``. ``API``, ``HQ``, and ``MLB`` stay uppercase.
    """
    raw = " ".join((text or "").split())
    if not raw:
        return ""
    letters = re.sub(r"[^A-Za-z]", "", raw)
    shouted = bool(letters) and letters.isupper()
    if not shouted and not re.search(r"[-_]", raw):
        return raw
    parts = [part for part in re.split(r"[-_\s]+", raw) if part]
    return " ".join(_case_word(part) for part in parts)


def _letters(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").casefold())


def _run_together(text: str) -> bool:
    """All caps, or one hyphenated token such as ``FOUR-GAMEJAM``."""
    raw = " ".join((text or "").split())
    if not raw:
        return False
    letters = re.sub(r"[^A-Za-z]", "", raw)
    if letters and letters.isupper():
        return True
    return " " not in raw and bool(re.search(r"[-_]", raw))


def resolve_title(heading: str, document_title: str = "", slug: str = "") -> str:
    """Prefer a document title that spells the same letters with real word breaks.

    ``FOUR-GAMEJAM`` plus ``Four-Game Jam`` becomes ``Four Game Jam``. A shouted
    name whose document title is a different sentence, such as ``CHOPSTICKS``,
    stays the normalized heading.
    """
    heading = " ".join((heading or "").split())
    document_title = " ".join((document_title or "").split())
    if heading and document_title and _run_together(heading) and _letters(heading) == _letters(document_title):
        return normalize_title(document_title) or document_title
    return display_title(heading or document_title, slug)


def display_title(page_title: str, slug: str = "") -> str:
    """Normalize a page heading, then the document title, then the slug.

    The caller passes the h1 text, or the document title when the h1 is empty.
    A shouted logo that drops separators yields to the slug when the slug
    spells the same letters with more words (``FOUR-GAMEJAM`` / ``four-game-jam``).
    """
    raw = " ".join((page_title or "").split())
    pretty = human_title(slug) if slug else ""
    if not raw or raw.casefold() in {"demo", "index.html", "index.htm"}:
        return pretty or "Demo"
    titled = normalize_title(raw)
    if pretty and _letters(raw) == _letters(slug) and len(pretty.split()) > len(titled.split()):
        return pretty
    return titled or pretty or raw


def repo_slug(path: pathlib.Path) -> str:
    """Directory name the page belongs to, skipping generic folders like ``web``."""
    generic = {"web", "public", "dist", "src", "fixtures", "demo", "app", "www", "static"}
    start = path if path.is_dir() else path.parent
    current = start
    for _ in range(6):
        name = current.name
        if name and name.lower() not in generic and ("-" in name or "_" in name):
            return name
        if current.parent == current:
            break
        current = current.parent
    if path.is_file() and path.stem.lower() not in generic:
        return path.stem
    return start.name


def readme_near(start: pathlib.Path) -> str:
    """README text walking up from ``start``, or empty when there is none."""
    current = start if start.is_dir() else start.parent
    for _ in range(8):
        for name in ("README.md", "Readme.md", "readme.md"):
            candidate = current / name
            if candidate.is_file():
                try:
                    return candidate.read_text()
                except OSError:
                    return ""
        if current.parent == current:
            break
        current = current.parent
    return ""


def _deny_terms(extra: tuple[str, ...] = (), readme: str = "", title: str = "",
               derive: bool = False, allow: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Explicit deny names, the deny file, the built-in denylist, and optional derivations.

    Derived words that are already in ``title`` are dropped so a repo title
    cannot block itself. Derivation is off unless the caller asks. Allow
    entries cannot remove the built-in household and school denylist.
    """
    terms = list(extra)
    if derive:
        terms.extend(derive_names(readme + "\n" + title, title))
    return effective_names(terms, allow)


def _refuse_denied(terms: tuple[str, ...], *, page: str = "", title: str = "",
                   captions: tuple[str, ...] | list[str] = ()) -> None:
    found = locate_denied(terms, page=page, title=title, captions=captions)
    if not found:
        return
    detail = name_detail(found)
    raise RecordingFailure("pii-blocked", -1, detail, warning=detail)


def public_text(text: str, fallback: str) -> str:
    cleaned = " ".join((text or "").split())
    if not cleaned or hits(cleaned):
        return fallback
    return cleaned[:80]


_TYPED = {
    "date": "2026-11-07",
    "time": "10:00",
    "datetime-local": "2026-11-07T10:00",
    "month": "2026-11",
    "week": "2026-W45",
    "number": "3",
    "range": "5",
    "color": "#3366ff",
    "url": "https://example.test",
    "search": "demo",
}
# Any address shape, reserved or not. The guard may allow example.com on a
# page; we still do not type contact data into a field.
_EMAIL_SHAPE = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def synthetic_value(placeholder: str, input_type: str = "") -> str:
    """A value we invent. Never the field's current contents, never contact data.

    Date, time, number, and the other typed inputs get a value the browser
    will accept. ``"Sample"`` is what ``fill`` rejects on ``input type=date``.
    """
    if input_type in _TYPED:
        return _TYPED[input_type]
    cleaned = placeholder.replace("…", "").replace("...", "").strip(" .")
    cleaned = " ".join(cleaned.split())
    if not cleaned or hits(cleaned) or _EMAIL_SHAPE.search(cleaned):
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
        captions = [public_text(title or (page.title if page is not None else ""), "Demo")]
    for index, caption in enumerate(captions):
        kind = "heading" if headings else "title"
        add(Caption(caption, frames=8 if index == 0 else 6), kind, caption)
    if page is not None:
        for control in page.controls:
            if not str(control.selector).strip():
                continue
            if control.kind == "input":
                add(Type(control.selector, synthetic_value(control.label, control.input_type), frames=8), "input", control.selector)
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


def _selector_visible(page, selector: str) -> bool:
    if not str(selector).strip():
        return False
    loc = page.locator(selector)
    try:
        if loc.count() < 1:
            return False
        return bool(loc.first.is_visible())
    except Exception:
        return False


def _act(page, control: Control) -> None:
    if control.kind == "input":
        page.fill(control.selector, synthetic_value(control.label, control.input_type), timeout=2500)
    else:
        page.click(control.selector, timeout=2500)


def _controls_from(raw: dict) -> list[Control]:
    controls: list[Control] = []
    for field in raw.get("inputs") or []:
        controls.append(Control(
            str(field.get("selector") or ""),
            "input",
            str(field.get("placeholder") or ""),
            str(field.get("type") or ""),
        ))
    for button in raw.get("buttons") or []:
        controls.append(Control(str(button.get("selector") or ""), "button", str(button.get("text") or "")))
    return controls


def _runnable(page, controls: list[Control]) -> list[Control]:
    """Drop controls hidden by an earlier drafted action.

    The page is probed again after each action that succeeds. A tab that
    hides the other panel's button is not followed by a click on that button.
    An action the browser rejects is left out, so the draft can be replayed.
    """
    kept: list[Control] = []
    for control in controls:
        if not _selector_visible(page, control.selector):
            continue
        try:
            _act(page, control)
        except Exception:
            continue
        kept.append(control)
    return kept


def probe(url: str) -> tuple[PageControls, tuple[str, ...], str]:
    from playwright.sync_api import sync_playwright

    from .director import classify_failure, refuse_unusable

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            try:
                response = page.goto(url, timeout=15000)
            except Exception as exc:
                kind = classify_failure(exc, navigating=True)
                raise RecordingFailure(kind, -1, "navigation") from exc
            try:
                raw = page.evaluate(_DISCOVER_JS)
            except Exception as exc:
                raise RecordingFailure(classify_failure(exc), -1, "navigation") from exc
            if not isinstance(raw, dict) or not isinstance(raw.get("text"), str):
                raise RecordingFailure("pii-blocked", -1, "unreadable")
            text = raw["text"]
            found = hits(text)
            if found:
                raise RecordingFailure("pii-blocked", -1, ",".join(found))
            refuse_unusable(page, response)
            controls = _runnable(page, _controls_from(raw))
            results = tuple(
                item for item in (str(entry) for entry in (raw.get("results") or []) if entry)
                if _selector_visible(page, item)
            )
            return PageControls(
                title=str(raw.get("title") or "Demo"),
                controls=tuple(controls),
                heading=str(raw.get("heading") or ""),
                document_title=str(raw.get("documentTitle") or ""),
            ), results, text
        finally:
            browser.close()


def from_page(url: str, fallback: str = "Demo", slug: str = "",
              allow_names: tuple[str, ...] = (), deny_names: tuple[str, ...] = (),
              derive: bool = False) -> Draft:
    page, results, text = probe(url)
    _refuse(text)
    local = pathlib.Path(urlparse(url).path) if url.startswith("file:") else None
    readme = readme_near(local) if local is not None else ""
    chosen = resolve_title(page.heading, page.document_title, slug or fallback)
    if not page.heading and not page.document_title:
        chosen = display_title(page.title or fallback, slug or fallback)
    terms = _deny_terms(deny_names, readme, chosen, derive, allow_names)
    _refuse_denied(terms, page=text, title=chosen, captions=(chosen,))
    return draft_from(chosen, _portable(url), (), page, results)


def from_readme(path: pathlib.Path, slug: str = "", allow_names: tuple[str, ...] = (),
                deny_names: tuple[str, ...] = (), derive: bool = False) -> Draft:
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
        title = display_title(headings[0], slug)
        terms = _deny_terms(deny_names, text, title, derive, allow_names)
        _refuse_denied(terms, title=title, captions=(title, *headings[1:]))
        return draft_from(title, FIXTURE_URL, (title, *headings[1:]), None)
    page, results, page_text = probe(url)
    _refuse(page_text)
    raw = headings[0] if headings else page.title
    title = display_title(raw, slug or path.parent.name)
    terms = _deny_terms(deny_names, text, title, derive, allow_names)
    shown = (title, *headings[1:]) if headings else ()
    _refuse_denied(terms, page=page_text, title=title, captions=shown or (title,))
    return draft_from(title, _portable(url), shown, page, results)


def init_source(source: str, allow_names: tuple[str, ...] = (),
                deny_names: tuple[str, ...] = (), derive: bool = False,
                slug: str = "") -> Draft:
    allow = tuple(allow_names)
    deny = tuple(deny_names)
    given = slug
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", source):
        return from_page(source, "Demo", slug=given, allow_names=allow, deny_names=deny, derive=derive)
    path = pathlib.Path(source)
    if path.is_file() and (path.suffix.lower() in {".md", ".markdown"} or path.name.lower().startswith("readme")):
        return from_readme(path, slug=given or repo_slug(path.parent), allow_names=allow, deny_names=deny, derive=derive)
    if path.is_dir():
        readme = None
        for name in ("README.md", "Readme.md", "readme.md"):
            candidate = path / name
            if candidate.is_file():
                readme = candidate
                break
        slug = given or repo_slug(path)
        if readme is not None:
            return from_readme(readme, slug=slug, allow_names=allow, deny_names=deny, derive=derive)
        html = _find_html(path)
        if html is None:
            raise ShotListError(f"no HTML fixture under {path}")
        return from_page(html.resolve().as_uri(), slug, slug=slug, allow_names=allow, deny_names=deny, derive=derive)
    if path.is_file():
        slug = given or repo_slug(path)
        return from_page(path.resolve().as_uri(), slug, slug=slug, allow_names=allow, deny_names=deny, derive=derive)
    raise ShotListError(f"init source not found: {source}")
