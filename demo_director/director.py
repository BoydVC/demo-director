"""Drive a real browser through the shot list, then compose + encode frames.

Every step goes through ``session.run_step``. The only way to append a beat is
for that call to return ``captured`` with the beat count increased by one.
A missing selector, a control that is not visible, a refused navigation, an
invalid value, a blank page, a timeout, or PII returns a typed failure and
no beat.
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
from dataclasses import replace
from pathlib import Path

from PIL import Image

from . import compose
from .guard import hits, names_to_block
from .session import Phase, RecordingFailure, Session, StepSignals, run_step
from .shotlist import ASPECTS, FIXTURE_URL
from .types import Beat, BlankPolicy, Box, ShotList, step_selector, step_text

TITLE_FRAMES = 12
END_FRAMES = 12

_LABEL_JS = """
(selector) => {
  const el = document.querySelector(selector);
  if (!el) return null;
  const rectOf = (r) => ({x: r.x, y: r.y, w: r.width, h: r.height});
  function ink(node) {
    const tag = node.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || tag === "BUTTON") {
      return rectOf(node.getBoundingClientRect());
    }
    const text = (node.innerText || "").trim();
    if (!text) return rectOf(node.getBoundingClientRect());
    const range = document.createRange();
    range.selectNodeContents(node);
    const rects = [...range.getClientRects()].filter((r) => r.width > 0.5 && r.height > 0.5);
    if (!rects.length) return rectOf(node.getBoundingClientRect());
    const x0 = Math.min(...rects.map((r) => r.x));
    const y0 = Math.min(...rects.map((r) => r.y));
    const x1 = Math.max(...rects.map((r) => r.right));
    const y1 = Math.max(...rects.map((r) => r.bottom));
    return {x: x0, y: y0, w: x1 - x0, h: y1 - y0};
  }
  function labelFor(node) {
    if (node.id) {
      const explicit = document.querySelector('label[for="' + CSS.escape(node.id) + '"]');
      if (explicit) return ink(explicit);
    }
    const labelledby = node.getAttribute("aria-labelledby");
    if (labelledby) {
      const explicit = document.getElementById(labelledby.split(/\\s+/)[0]);
      if (explicit) return ink(explicit);
    }
    const wrap = node.closest("label");
    if (wrap && wrap !== node) return ink(wrap);
    const parent = node.parentElement;
    const own = node.getBoundingClientRect();
    if (parent) {
      const left = [...parent.children].filter((child) => {
        if (child === node) return false;
        const r = child.getBoundingClientRect();
        return r.width > 2 && r.height > 2 && r.right <= own.left + 8
          && r.bottom > own.top + 4 && r.top < own.bottom - 4;
      });
      if (left.length) return ink(left[0]);
    }
    let best = null;
    let bestDy = 1e9;
    for (const heading of document.querySelectorAll("h1,h2,h3,label")) {
      const r = heading.getBoundingClientRect();
      const dy = own.top - r.bottom;
      if (dy >= -8 && dy < 240 && dy < bestDy && r.width > 2) {
        best = heading;
        bestDy = dy;
      }
    }
    return best ? ink(best) : null;
  }
  function union(a, b) {
    if (!a) return b || null;
    if (!b) return a;
    const x0 = Math.min(a.x, b.x), y0 = Math.min(a.y, b.y);
    const x1 = Math.max(a.x + a.w, b.x + b.w), y1 = Math.max(a.y + a.h, b.y + b.h);
    return {x: x0, y: y0, w: x1 - x0, h: y1 - y0};
  }
  function rowBox(node) {
    const parent = node.parentElement;
    if (!parent || parent === document.body || parent === document.documentElement) return null;
    const pr = parent.getBoundingClientRect();
    const own = node.getBoundingClientRect();
    if (pr.width < 2 || pr.height < 2) return null;
    if (pr.height > Math.max(own.height * 3.2, 180)) return null;
    return rectOf(pr);
  }
  function neighborBox(node) {
    const prev = node.previousElementSibling;
    if (!prev) return null;
    const r = prev.getBoundingClientRect();
    const own = node.getBoundingClientRect();
    if (own.top - r.bottom > 40 || r.height > 340 || r.height < 8) return null;
    return rectOf(r);
  }
  function collectLines() {
    const out = [];
    const push = (box) => {
      if (!box || box.w < 8 || box.h < 8 || box.h > 180) return;
      out.push(box);
    };
    document.querySelectorAll("h1,h2,h3,h4,header,button,.card,.sub,.hero,.kick,#conf,#status,.tag,label,p,[role=heading]").forEach((node) => {
      push(ink(node));
    });
    // Left-aligned blocks the zoom must not slice, even when they are not the
    // control's own label. A div title is the usual shape.
    document.querySelectorAll("div,span,li,strong,a").forEach((node) => {
      if (node.childElementCount > 2) return;
      const box = ink(node);
      if (!box || box.x > 120) return;
      const text = (node.innerText || "").trim();
      if (text.length < 4 || text.length > 90) return;
      push(box);
    });
    document.querySelectorAll("input, textarea").forEach((node) => {
      const r = node.getBoundingClientRect();
      if (r.width > 4 && r.height > 4) out.push(rectOf(r));
    });
    return out;
  }
  return {
    box: ink(el),
    label: union(union(labelFor(el), rowBox(el)), neighborBox(el)),
    lines: collectLines()
  };
}
"""

_LINES_JS = """
() => {
  const rectOf = (r) => ({x: r.x, y: r.y, w: r.width, h: r.height});
  function ink(node) {
    const tag = node.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || tag === "BUTTON") {
      return rectOf(node.getBoundingClientRect());
    }
    const text = (node.innerText || "").trim();
    if (!text) return rectOf(node.getBoundingClientRect());
    const range = document.createRange();
    range.selectNodeContents(node);
    const rects = [...range.getClientRects()].filter((r) => r.width > 0.5 && r.height > 0.5);
    if (!rects.length) return rectOf(node.getBoundingClientRect());
    const x0 = Math.min(...rects.map((r) => r.x));
    const y0 = Math.min(...rects.map((r) => r.y));
    const x1 = Math.max(...rects.map((r) => r.right));
    const y1 = Math.max(...rects.map((r) => r.bottom));
    return {x: x0, y: y0, w: x1 - x0, h: y1 - y0};
  }
  const out = [];
  const push = (box) => {
    if (!box || box.w < 8 || box.h < 8 || box.h > 180) return;
    out.push(box);
  };
  document.querySelectorAll("h1,h2,h3,h4,header,button,.card,.sub,.hero,.kick,.brand,#conf,#status,.tag,label,p,[role=heading]").forEach((node) => {
    push(ink(node));
  });
  document.querySelectorAll("div,span,li,strong,a").forEach((node) => {
    if (node.childElementCount > 2) return;
    const box = ink(node);
    if (!box || box.x > 120) return;
    const text = (node.innerText || "").trim();
    if (text.length < 4 || text.length > 90) return;
    push(box);
  });
  return out;
}
"""

_TEXT_JS = """
() => {
  if (!document.body) return "";
  const parts = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let node;
  while ((node = walker.nextNode())) {
    const text = (node.textContent || "").trim();
    if (text) parts.push(text);
  }
  document.querySelectorAll("input, textarea").forEach((el) => {
    if (el.value) parts.push(el.value);
    if (el.placeholder) parts.push(el.placeholder);
  });
  return parts.join("\\n");
}
"""


def resolve_fixture(sl: ShotList) -> ShotList:
    """``url: FIXTURE`` is the checked-in synthetic app, from any working directory."""
    if sl.url != FIXTURE_URL:
        return sl
    root = Path(__file__).resolve().parents[1]
    return replace(sl, url=(root / "fixtures" / "app.html").resolve().as_uri())


def shotlist_blob(sl: ShotList) -> str:
    parts = [sl.title, sl.url]
    for step in sl.steps:
        text = step_text(step)
        if text:
            parts.append(text)
        url = getattr(step, "url", None)
        if url:
            parts.append(url)
    return "\n".join(parts)


def _box(raw) -> Box | None:
    if not raw:
        return None
    try:
        return (float(raw["x"]), float(raw["y"]), float(raw["w"]), float(raw["h"]))
    except (KeyError, TypeError, ValueError):
        return None


def _page_text(page) -> str | None:
    try:
        text = page.evaluate(_TEXT_JS)
    except Exception:
        return None
    return text if isinstance(text, str) else None


def _snap(page) -> Image.Image:
    return Image.open(io.BytesIO(page.screenshot())).convert("RGB")


def classify_failure(exc: BaseException, *, navigating: bool = False) -> str:
    """Map a browser exception to the failure it actually is.

    ``_perform`` used to send every exception down the timeout edge, so a
    malformed date fill and a covered click both came back as timeout.
    """
    name = type(exc).__name__.casefold()
    text = f"{name}: {exc}".casefold()
    if any(token in text for token in (
        "element is not visible",
        "element is hidden",
        "intercepts pointer",
        "outside of the viewport",
        "not attached to the dom",
    )):
        return "not-visible"
    if any(token in text for token in (
        "malformed value",
        "invalid value",
        "cannot type",
        "cannot fill",
    )):
        return "invalid-value"
    if any(token in text for token in (
        "unknown engine",
        "malformed selector",
        "strict mode violation",
        "no element matches",
    )):
        return "selector-missing"
    network = any(token in text for token in (
        "net::",
        "ns_error",
        "err_connection",
        "err_name",
        "err_address",
        "err_aborted",
        "err_failed",
        "err_internet",
        "err_socket",
        "frame was detached",
        "download is starting",
        "page crashed",
        "navigation failed",
    ))
    timed = "timeout" in name or "timeout" in text
    if network:
        return "nav-error"
    if navigating:
        return "timeout" if timed else "nav-error"
    if timed:
        return "timeout"
    return "invalid-value"


_PAGE_STATE_JS = """
() => {
  const text = document.body ? (document.body.innerText || "").replace(/\\s+/g, " ").trim() : "";
  let styled = false;
  for (const sheet of document.styleSheets) {
    try {
      if (sheet.cssRules && sheet.cssRules.length > 0) { styled = true; break; }
    } catch (e) {
      styled = true;
      break;
    }
  }
  return {text, styled};
}
"""


def page_problem(page, response, policy: BlankPolicy | None = None) -> str | None:
    """Blank, unstyled, sparse, or an error page. None when the page is demoable.

    A style tag with no rules (an unfilled ``/*__CSS__*/`` template) counts
    as unstyled. A styled page with almost no copy, or with an error or
    empty-state phrase, fails closed too. ``file:`` loads report status 0 or
    200 and are not HTTP errors. ``policy`` replaces the default phrases and
    the character floor.
    """
    rules = policy or BlankPolicy()
    status = getattr(response, "status", None) if response is not None else None
    if isinstance(status, int) and status >= 400:
        return f"http-{status}"
    try:
        raw = page.evaluate(_PAGE_STATE_JS)
    except Exception:
        return "no-visible-text"
    if not isinstance(raw, dict):
        return "no-visible-text"
    text = raw.get("text") if isinstance(raw.get("text"), str) else ""
    if not raw.get("styled"):
        return "unstyled"
    if not text.strip():
        return "no-visible-text"
    return rules.reason(text)


def refuse_unusable(page, response, step_index: int = -1, policy: BlankPolicy | None = None) -> None:
    """Fail the take when the page is blank, unstyled, sparse, or an error page.

    The reason is on the exception. Callers put it in the JSON status. Nothing
    is written to stderr here.
    """
    problem = page_problem(page, response, policy)
    if not problem:
        return
    raise RecordingFailure(
        "blank-page",
        step_index,
        problem,
        warning=f"page is not demoable: {problem}",
    )


def _probe(page, selector: str, timeout_ms: int = 3000):
    """Return (found, box, label, lines, problem).

    ``problem`` is None, ``selector-missing``, ``not-visible``, or ``timeout``.
    A node that exists but has no box is hidden, not missing.
    """
    loc = page.locator(selector).first
    try:
        count = loc.count()
    except Exception as exc:
        return False, None, None, (), classify_failure(exc)
    if count == 0:
        return False, None, None, (), "selector-missing"
    try:
        bb = loc.bounding_box(timeout=timeout_ms)
    except Exception as exc:
        return False, None, None, (), classify_failure(exc)
    if not bb:
        return False, None, None, (), "not-visible"
    box = label = None
    lines: tuple[Box, ...] = ()
    try:
        raw = page.evaluate(_LABEL_JS, selector)
    except Exception:
        raw = None
    if isinstance(raw, dict):
        box = _box(raw.get("box"))
        label = _box(raw.get("label"))
        lines = tuple(item for item in (_box(row) for row in (raw.get("lines") or [])) if item)
    if box is None:
        box = (bb["x"], bb["y"], bb["width"], bb["height"])
    return True, box, label, lines, None


def _collect_lines(page) -> tuple[Box, ...]:
    """Text boxes on the page as it is now, after a click has changed it."""
    try:
        raw = page.evaluate(_LINES_JS)
    except Exception:
        return ()
    if not isinstance(raw, list):
        return ()
    return tuple(item for item in (_box(row) for row in raw) if item)


def _perform(page, step, policy: BlankPolicy | None = None) -> None:
    if step.action == "goto":
        response = page.goto(step.url, timeout=15000)
        refuse_unusable(page, response, policy=policy)
    elif step.action == "click":
        page.click(step.selector, timeout=5000)
    elif step.action == "type":
        page.fill(step.selector, step.text or "", timeout=5000)


def _drive_step(page, session: Session, step, names: tuple[str, ...],
                policy: BlankPolicy | None = None) -> tuple[Session, Beat | None]:
    """One chart step. A beat is returned only when ``run_step`` captures."""
    needs = step.action in {"click", "type", "zoom"}
    text = _page_text(page)
    pii = bool(hits(text, names))
    found, box, label, lines, problem = True, None, None, (), None
    if needs and not pii:
        found, box, label, lines, problem = _probe(page, step_selector(step) or "")
    timeout_at = "navigating" if problem == "timeout" else None
    action_error = None if problem in (None, "timeout") else problem
    pre = StepSignals(needs, problem is None, timeout_at, pii, False, action_error)
    trial = run_step(session, pre)
    if trial.failure or trial.pii_seen:
        return trial, None

    try:
        before = _snap(page)
    except Exception:
        failed = run_step(session, StepSignals(needs, found, "navigating", pii, False))
        return failed, None

    try:
        _perform(page, step, policy)
    except RecordingFailure:
        raise
    except Exception as exc:
        # A navigation that destroys the document makes the read fail. That is
        # the navigation error, not an unreadable-text PII hit. A string that
        # actually contains a real address still blocks.
        text_after = _page_text(page)
        reasons = hits(text_after, names) if isinstance(text_after, str) else ()
        if reasons:
            failed = run_step(session, StepSignals(needs, found, None, pii, True))
        else:
            kind = classify_failure(exc, navigating=step.action == "goto")
            failed = run_step(session, StepSignals(needs, found, None, pii, False, kind))
        return failed, None

    text_after = _page_text(page)
    pii_after = bool(hits(text_after, names))
    try:
        page.wait_for_timeout(120)
        settle_timeout = False
    except Exception:
        settle_timeout = True
    timeout_after = "settling" if settle_timeout else None
    final = run_step(session, StepSignals(needs, found, timeout_after, pii, pii_after))
    if final.phase is not Phase.CAPTURED or final.failure or final.pii_seen:
        return final, None
    if final.beats != session.beats + 1:
        return final, None
    try:
        after = _snap(page)
    except Exception:
        failed = run_step(session, StepSignals(needs, found, "settling", pii, pii_after))
        return failed, None
    # The click auto-zoom is framed from this beat. Lines probed before the
    # click miss a heading the click just revealed, so the crop slices it.
    if step.action in {"click", "zoom"}:
        fresh = _collect_lines(page)
        if fresh:
            lines = fresh
    return final, Beat(before, after, box, step, label, lines)


def record(sl: ShotList) -> list[Beat]:
    """Walk the shot list. Raises RecordingFailure instead of returning a partial take."""
    from playwright.sync_api import sync_playwright

    sl = resolve_fixture(sl)
    names = sl.pii.names
    session = Session.idle()
    blob = hits(shotlist_blob(sl), names)
    if blob:
        blocked = run_step(session, StepSignals(False, True, None, True, False))
        raise RecordingFailure(blocked.failure or "pii-blocked", -1, ",".join(blob))

    beats: list[Beat] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": sl.viewport[0], "height": sl.viewport[1]})
            try:
                response = page.goto(sl.url, timeout=15000)
            except Exception as exc:
                kind = classify_failure(exc, navigating=True)
                raise RecordingFailure(kind, -1, "navigation") from exc
            opening = _page_text(page)
            derived = names_to_block(sl.title + "\n" + (opening or ""), sl.pii.allow)
            if derived:
                raise RecordingFailure(
                    "pii-blocked", -1, "name",
                    warning="derived-name: " + ", ".join(derived),
                )
            opening_hits = hits(opening, names)
            if opening_hits:
                blocked = run_step(session, StepSignals(False, True, None, True, False))
                raise RecordingFailure(blocked.failure or "pii-blocked", -1, ",".join(opening_hits))
            policy = sl.blank or BlankPolicy()
            refuse_unusable(page, response, policy=policy)
            for index, step in enumerate(sl.steps):
                try:
                    session, beat = _drive_step(page, session, step, names, policy)
                except RecordingFailure as exc:
                    selector = exc.selector or step_selector(step) or ""
                    raise RecordingFailure(exc.kind, index, exc.detail, selector) from exc
                if beat is None:
                    selector = step_selector(step) or getattr(step, "url", "") or ""
                    detail = session.failure or "no beat"
                    if session.failure == "nav-error":
                        detail = "navigation"
                    elif session.failure == "invalid-value":
                        detail = "invalid-value"
                    elif session.failure == "not-visible":
                        detail = "not-visible"
                    raise RecordingFailure(session.failure or "timeout", index, detail, selector)
                beats.append(beat)
                if session.beats != len(beats):
                    raise RecordingFailure("timeout", index, "beat invariant")
        finally:
            browser.close()
    return beats


def lead_line(sl: ShotList) -> str | None:
    for step in sl.steps:
        text = step_text(step)
        if step.action == "caption" and text and text.strip() != sl.title.strip():
            return text.strip()
    return None


def _frame(box, size, amount, label, lines=()):
    """Crop that keeps the target, its label, and any left-aligned text it would cut."""
    if box is None or amount <= 0:
        return compose.frame_rect(None, size, 0, None)
    focus = label if label is not None else box
    crop = compose.frame_rect(box, size, amount, focus)
    must = tuple(item for item in (box, focus) if item)
    if lines:
        opened = crop
        crop = compose.contain_left_text(crop, tuple(lines), must, size)
        # Anything the widen step pulled in, including a heading the click
        # just showed, has to stay inside. unsliced may still slide off some
        # other row.
        kept = tuple(
            line for line in lines
            if compose._inside(line, crop) and line[0] < opened[0] - 0.25
        )
        crop = compose.unsliced(crop, tuple(lines), must + kept, size)
    return crop


def _content_frames(beats: list[Beat], out: tuple[int, int], aspect: str):
    cursor = (beats[0].before.width / 2, beats[0].before.height / 2)
    caption = None
    zoom_t, zoom_box, zoom_label = 0.0, None, None
    # Hold and caption beats do not probe a selector, so they carry no text
    # boxes. Keep the lines from the zoom that is still on screen; otherwise
    # the hold recomputes a tight crop and slices the heading the zoom just fit.
    zoom_lines: tuple = ()
    for beat in beats:
        step = beat.step
        if beat.lines:
            zoom_lines = beat.lines
        lines = beat.lines or zoom_lines
        if step.action == "caption":
            caption = step_text(step)
            crop = _frame(zoom_box, beat.after.size, zoom_t, zoom_label, lines)
            count = max(1, step.frames)
            for i in range(count):
                alpha = compose.ease((i + 1) / min(5, count))
                yield compose.render(beat.after, crop, cursor, 0, caption, out, aspect, (0.0, 0.0), alpha)
            continue
        if beat.box:
            target = compose.center(beat.box)
            new_zoom = 1.0 if step.action in {"zoom", "type"} else 0.55
            start = _frame(zoom_box, beat.before.size, zoom_t, zoom_label, lines)
            end = _frame(beat.box, beat.before.size, new_zoom, beat.label, lines)
            count = max(6, step.frames)
            prev = cursor
            for i in range(count):
                t = compose.ease((i + 1) / count)
                pos = (compose.lerp(cursor[0], target[0], t), compose.lerp(cursor[1], target[1], t))
                vel = (pos[0] - prev[0], pos[1] - prev[1])
                prev = pos
                shot = beat.after if step.action == "type" and t > 0.42 else beat.before
                yield compose.render(shot, compose.lerp_box(start, end, t), pos, 0, caption, out, aspect, vel)
            cursor, zoom_t, zoom_box, zoom_label = target, new_zoom, beat.box, beat.label
            if step.action == "click":
                for i in range(5):
                    yield compose.render(beat.after, end, cursor, (i + 1) / 5, caption, out, aspect)
        hold = step.frames if step.action in {"hold", "type", "goto"} else 4
        crop = _frame(zoom_box, beat.after.size, zoom_t, zoom_label, lines)
        for _ in range(hold):
            yield compose.render(beat.after, crop, cursor, 0, caption, out, aspect)


def frames_for(beats: list[Beat], out: tuple[int, int], title: str = "", aspect: str = "16:9",
               subtitle: str | None = None):
    """Yield composed frames. Title and end cards bookend the shot when a title is set."""
    content = list(_content_frames(beats, out, aspect)) if beats else []
    if not title:
        yield from content
        return
    for i in range(TITLE_FRAMES):
        yield compose.title_card(out, title, (i + 1) / TITLE_FRAMES, subtitle)
    yield from content
    for i in range(END_FRAMES):
        yield compose.end_card(out, title, (i + 1) / END_FRAMES)


def fallback_plan(scale: float, fps: int) -> list[tuple[float, int]]:
    """Scale and fps pairs to try when a GIF is over the byte budget.

    The first pair is the request. Later pairs halve the frame rate, then
    drop the scale by a third, and stop around a quarter of the frame or 4 fps.
    """
    s = float(scale)
    original = max(1, int(fps))
    f = original
    plan: list[tuple[float, int]] = []
    for _ in range(5):
        pair = (round(s, 4), int(f))
        if pair not in plan and s > 0 and f >= 1:
            plan.append(pair)
        if f > 5:
            f = max(4, f // 2)
            continue
        nxt = round(s * 0.67, 4)
        if nxt < 0.24 or nxt >= s:
            break
        s, f = nxt, original
    return plan or [(round(float(scale), 4), original)]


def _decimate(frames: list, source_fps: int, fps: int) -> list:
    """Drop frames so a lower playback rate is actually a smaller GIF."""
    if fps >= source_fps or source_fps < 1:
        return frames
    stride = max(1, int(source_fps / fps))
    chosen = frames[::stride]
    return chosen or frames[:1]


def _under_budget(results: dict, budget: int) -> bool:
    for info in results.values():
        if os.path.getsize(info["gif"]) > budget:
            return False
    return True


def encode(frames_dir: str, fps: int, gif: str, mp4: str | None) -> None:
    pattern = os.path.join(frames_dir, "f%05d.png")
    vf = (
        f"fps={fps},scale=trunc(iw/2)*2:trunc(ih/2)*2,"
        "split[a][b];[a]palettegen=stats_mode=diff:max_colors=160[p];[b][p]paletteuse=dither=sierra2_4a"
    )
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(fps), "-i", pattern, "-vf", vf, gif],
        check=True,
    )
    if mp4:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(fps), "-i", pattern,
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", "-movflags", "+faststart",
             "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", mp4],
            check=True,
        )


def _write(sl: ShotList, beats: list, outdir: str, scale: float, fps: int) -> dict:
    subtitle = lead_line(sl)
    results = {}
    for aspect in sl.aspects:
        width, height = ASPECTS[aspect.value]
        out = (int(width * scale) // 2 * 2, int(height * scale) // 2 * 2)
        tmp = tempfile.mkdtemp()
        try:
            frames = _decimate(
                list(frames_for(beats, out, title=sl.title, aspect=aspect.value, subtitle=subtitle)),
                sl.fps,
                fps,
            )
            if not frames:
                raise RuntimeError("no frames")
            for n, frame in enumerate(frames):
                frame.save(os.path.join(tmp, f"f{n:05d}.png"))
            slug = aspect.value.replace(":", "x")
            gif = os.path.join(outdir, f"demo-{slug}.gif")
            mp4 = os.path.join(outdir, f"demo-{slug}.mp4")
            encode(tmp, fps, gif, mp4)
            total = len(frames)
            content = max(0, total - TITLE_FRAMES - END_FRAMES)
            still_index = min(total - 1, TITLE_FRAMES + max(0, content - 1))
            still = os.path.join(outdir, f"still-{slug}.png")
            shutil.copy(os.path.join(tmp, f"f{still_index:05d}.png"), still)
            results[aspect.value] = {
                "gif": gif, "mp4": mp4, "frames": total, "still": still,
                "scale": scale, "fps": fps,
            }
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return results


def direct(sl: ShotList, outdir: str, scale: float = 0.6, budget: int | None = None) -> dict:
    """Record once. When ``budget`` is set, re-encode over-budget GIFs at a lower fps or scale."""
    os.makedirs(outdir, exist_ok=True)
    beats = record(sl)
    plan = fallback_plan(scale, sl.fps) if budget is not None else [(float(scale), int(sl.fps))]
    results: dict = {}
    for attempt, (s, fps) in enumerate(plan, start=1):
        results = _write(sl, beats, outdir, s, fps)
        for row in results.values():
            row["attempts"] = attempt
        if budget is None or _under_budget(results, budget):
            return results
    return results
