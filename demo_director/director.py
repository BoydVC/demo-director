"""Drive a real browser through the shot list, then compose + encode frames.

Every step goes through ``session.run_step``. The only way to append a beat is
for that call to return ``captured`` with the beat count increased by one.
PII, a missing selector, or a timeout returns a typed failure and no beat.
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
from .guard import hits
from .session import Phase, RecordingFailure, Session, StepSignals, run_step
from .shotlist import ASPECTS, FIXTURE_URL
from .types import Beat, Box, ShotList, step_selector, step_text

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
    document.querySelectorAll("h1,h2,h3,button,.card,.sub,#conf,#status,.tag,label,p").forEach((node) => {
      const box = ink(node);
      if (box.w > 4 && box.h > 4 && box.h < 90) out.push(box);
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


def _probe(page, selector: str, timeout_ms: int = 3000):
    """Return (found, box, label, lines, timed_out). Missing and timeout stay distinct."""
    loc = page.locator(selector).first
    try:
        count = loc.count()
    except Exception:
        return False, None, None, (), True
    if count == 0:
        return False, None, None, (), False
    try:
        bb = loc.bounding_box(timeout=timeout_ms)
    except Exception:
        return False, None, None, (), True
    if not bb:
        return False, None, None, (), False
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
    return True, box, label, lines, False


def _perform(page, step) -> None:
    if step.action == "goto":
        page.goto(step.url, timeout=15000)
    elif step.action == "click":
        page.click(step.selector, timeout=5000)
    elif step.action == "type":
        page.fill(step.selector, step.text or "", timeout=5000)


def _drive_step(page, session: Session, step, names: tuple[str, ...]) -> tuple[Session, Beat | None]:
    """One chart step. A beat is returned only when ``run_step`` captures."""
    needs = step.action in {"click", "type", "zoom"}
    text = _page_text(page)
    pii = bool(hits(text, names))
    found, box, label, lines, nav_timeout = True, None, None, (), False
    if needs and not pii:
        found, box, label, lines, nav_timeout = _probe(page, step_selector(step) or "")
    timeout_at = "navigating" if nav_timeout else None
    pre = StepSignals(needs, found, timeout_at, pii, False)
    trial = run_step(session, pre)
    if trial.failure or trial.pii_seen:
        return trial, None

    try:
        before = _snap(page)
    except Exception:
        failed = run_step(session, StepSignals(needs, found, "navigating", pii, False))
        return failed, None

    acted_timeout = False
    try:
        _perform(page, step)
    except Exception:
        acted_timeout = True
    if acted_timeout:
        text_after = _page_text(page)
        pii_after = bool(hits(text_after, names))
        if pii_after:
            failed = run_step(session, StepSignals(needs, found, None, pii, True))
        else:
            failed = run_step(session, StepSignals(needs, found, "acting", pii, False))
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
                page.goto(sl.url, timeout=15000)
            except Exception as exc:
                raise RecordingFailure("timeout", -1, "navigation") from exc
            opening = _page_text(page)
            opening_hits = hits(opening, names)
            if opening_hits:
                blocked = run_step(session, StepSignals(False, True, None, True, False))
                raise RecordingFailure(blocked.failure or "pii-blocked", -1, ",".join(opening_hits))
            for index, step in enumerate(sl.steps):
                session, beat = _drive_step(page, session, step, names)
                if beat is None:
                    raise RecordingFailure(session.failure or "timeout", index, session.failure or "no beat")
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
    """Crop that keeps the target and its label, then slides off any sliced text."""
    if box is None or amount <= 0:
        return compose.frame_rect(None, size, 0, None)
    focus = label if label is not None else box
    crop = compose.frame_rect(box, size, amount, focus)
    must = tuple(item for item in (box, focus) if item)
    if lines:
        crop = compose.unsliced(crop, tuple(lines), must, size)
    return crop


def _content_frames(beats: list[Beat], out: tuple[int, int], aspect: str):
    cursor = (beats[0].before.width / 2, beats[0].before.height / 2)
    caption = None
    zoom_t, zoom_box, zoom_label = 0.0, None, None
    for beat in beats:
        step = beat.step
        lines = beat.lines
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


def direct(sl: ShotList, outdir: str, scale: float = 0.6) -> dict:
    os.makedirs(outdir, exist_ok=True)
    beats = record(sl)
    subtitle = lead_line(sl)
    results = {}
    for aspect in sl.aspects:
        width, height = ASPECTS[aspect.value]
        out = (int(width * scale) // 2 * 2, int(height * scale) // 2 * 2)
        tmp = tempfile.mkdtemp()
        try:
            n = -1
            for n, frame in enumerate(frames_for(beats, out, title=sl.title, aspect=aspect.value, subtitle=subtitle)):
                frame.save(os.path.join(tmp, f"f{n:05d}.png"))
            if n < 0:
                raise RuntimeError("no frames")
            slug = aspect.value.replace(":", "x")
            gif = os.path.join(outdir, f"demo-{slug}.gif")
            mp4 = os.path.join(outdir, f"demo-{slug}.mp4")
            encode(tmp, sl.fps, gif, mp4)
            total = n + 1
            content = total - TITLE_FRAMES - END_FRAMES
            still_index = TITLE_FRAMES + max(0, content - 1)
            still = os.path.join(outdir, f"still-{slug}.png")
            shutil.copy(os.path.join(tmp, f"f{still_index:05d}.png"), still)
            results[aspect.value] = {"gif": gif, "mp4": mp4, "frames": total, "still": still}
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return results
