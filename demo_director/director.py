"""Drive a real browser through the shot list, then compose + encode frames."""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile

from PIL import Image

from . import compose
from .pii import guard, guard_shotlist
from .shotlist import ASPECTS
from .types import Beat, Box, Caption, Click, Goto, Hold, ShotList, Step, Type, Zoom, step_selector

_LABEL_JS = """(selector) => {
  const el = document.querySelector(selector);
  if (!el) return null;
  const rect = (node) => {
    const r = node.getBoundingClientRect();
    return {x: r.x, y: r.y, width: r.width, height: r.height};
  };
  const border = rect(el);
  let box = border;
  const range = document.createRange();
  range.selectNodeContents(el);
  const text = range.getBoundingClientRect();
  const tag = el.tagName;
  const ink = (el.innerText || "").trim();
  if (ink && tag !== "BUTTON" && tag !== "INPUT" && tag !== "TEXTAREA" && text.width > 8 && text.height > 8 && border.width > text.width + 40) {
    box = {x: text.x, y: text.y, width: text.width, height: text.height};
  }
  const vw = window.innerWidth || 1;
  const vh = window.innerHeight || 1;
  const usable = (node) => {
    if (!node || node === el) return false;
    const r = node.getBoundingClientRect();
    if (r.width < 8 || r.height < 8) return false;
    if (r.width >= vw * 0.98 && r.height >= vh * 0.45) return false;
    return true;
  };
  let label = null;
  if (el.id) {
    const explicit = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
    if (usable(explicit)) label = explicit;
  }
  if (!label) {
    const wrap = el.closest("label");
    if (usable(wrap)) label = wrap;
  }
  if (!label) {
    const id = (el.getAttribute("aria-labelledby") || "").split(/\\s+/).filter(Boolean)[0];
    if (id) {
      const node = document.getElementById(id);
      if (usable(node)) label = node;
    }
  }
  if (!label && el.parentElement && usable(el.parentElement)) {
    const parent = el.parentElement;
    const pr = parent.getBoundingClientRect();
    const er = el.getBoundingClientRect();
    if (pr.height <= er.height * 2.4 && pr.width > er.width + 24) label = parent;
  }
  if (!label) {
    let sib = el.previousElementSibling;
    while (sib) {
      if (usable(sib)) {
        const sr = sib.getBoundingClientRect();
        const er = el.getBoundingClientRect();
        const sameRow = sr.bottom > er.top + 4 && sr.top < er.bottom - 4;
        if (sameRow) { label = sib; break; }
      }
      sib = sib.previousElementSibling;
    }
  }
  return {box, label: label ? rect(label) : null};
}"""

_TEXT_JS = """() => {
  const bits = [document.title || ""];
  if (document.body) bits.push(document.body.innerText || "");
  document.querySelectorAll("input, textarea").forEach((el) => {
    bits.push(el.value || "");
    bits.push(el.placeholder || "");
  });
  document.querySelectorAll("[aria-label]").forEach((el) => bits.push(el.getAttribute("aria-label") || ""));
  document.querySelectorAll("img[alt]").forEach((el) => bits.push(el.getAttribute("alt") || ""));
  return bits.join("\\n");
}"""


def _as_box(raw: dict | None) -> Box | None:
    if not raw or raw.get("width") is None or raw.get("height") is None:
        return None
    return (float(raw["x"]), float(raw["y"]), float(raw["width"]), float(raw["height"]))


def read_page_text(page) -> str | None:
    """Visible text, values, and labels. None when the page cannot be read."""
    try:
        text = page.evaluate(_TEXT_JS)
    except Exception:
        return None
    if not isinstance(text, str):
        return None
    return text


def _boxes(page, selector: str) -> tuple[Box | None, Box | None]:
    try:
        raw = page.evaluate(_LABEL_JS, selector)
    except Exception:
        return None, None
    if not isinstance(raw, dict):
        return None, None
    return _as_box(raw.get("box")), _as_box(raw.get("label"))


def _act(page, step: Step) -> None:
    if isinstance(step, Goto):
        page.goto(step.url)
    elif isinstance(step, Click):
        page.click(step.selector)
    elif isinstance(step, Type):
        page.fill(step.selector, step.text)


def record(shotlist: ShotList) -> list[Beat]:
    guard_shotlist(shotlist)
    from playwright.sync_api import sync_playwright

    beats: list[Beat] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": shotlist.viewport[0], "height": shotlist.viewport[1]})
        page.goto(shotlist.url)
        guard(read_page_text(page), shotlist.pii)

        def snap() -> Image.Image:
            return Image.open(io.BytesIO(page.screenshot())).convert("RGB")

        for step in shotlist.steps:
            before = snap()
            selector = step_selector(step)
            box, label = _boxes(page, selector) if selector else (None, None)
            _act(page, step)
            page.wait_for_timeout(120)
            guard(read_page_text(page), shotlist.pii)
            beats.append(Beat(before, snap(), box, step, label))
        browser.close()
    return beats


def _paint(image: Image.Image, box: Box | None, label: Box | None, zoom: float,
           cursor: tuple[float, float], ripple: float, caption: str | None,
           out: tuple[int, int], aspect: str):
    crop = compose.zoom_rect(box, image.size, zoom, label)
    return compose.render(image, crop, cursor, ripple, caption, out, aspect)


def frames_for(beats: list[Beat], out: tuple[int, int], aspect: str = "16:9"):
    """Yield composed frames. Pure function of beats: cursor glides, ripples, zooms, captions."""
    cursor = (beats[0].before.width / 2, beats[0].before.height / 2) if beats else (0.0, 0.0)
    caption = None
    zoom_t, zoom_box, zoom_label = 0.0, None, None
    for beat in beats:
        step = beat.step
        if isinstance(step, Caption):
            caption = step.text
            for _ in range(step.frames):
                yield _paint(beat.after, zoom_box, zoom_label, zoom_t, cursor, 0, caption, out, aspect)
            continue
        if beat.box:
            target = compose.center(beat.box)
            new_zoom = 1.0 if isinstance(step, (Zoom, Type)) else 0.5
            count = max(4, step.frames)
            for i in range(count):
                t = compose.ease((i + 1) / count)
                pos = (compose.lerp(cursor[0], target[0], t), compose.lerp(cursor[1], target[1], t))
                z = compose.lerp(zoom_t, new_zoom, t)
                yield _paint(beat.before, beat.box, beat.label_box, z, pos, 0, caption, out, aspect)
            cursor, zoom_t, zoom_box, zoom_label = target, new_zoom, beat.box, beat.label_box
            if isinstance(step, Click):
                for i in range(4):
                    yield _paint(beat.after, zoom_box, zoom_label, zoom_t, cursor, (i + 1) / 4, caption, out, aspect)
        hold = step.frames if isinstance(step, (Hold, Type, Goto)) else 3
        for _ in range(hold):
            yield _paint(beat.after, zoom_box, zoom_label, zoom_t, cursor, 0, caption, out, aspect)


def encode(frames_dir: str, fps: int, gif: str, mp4: str | None) -> None:
    pattern = os.path.join(frames_dir, "f%05d.png")
    palette = f"fps={fps},split[a][b];[a]palettegen=max_colors=160[p];[b][p]paletteuse=dither=sierra2_4a"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(fps), "-i", pattern, "-vf", palette, gif],
        check=True,
    )
    if mp4:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(fps), "-i", pattern,
             "-pix_fmt", "yuv420p", "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", mp4],
            check=True,
        )


def direct(shotlist: ShotList, outdir: str, scale: float = 0.6) -> dict:
    beats = record(shotlist)
    os.makedirs(outdir, exist_ok=True)
    results = {}
    for aspect in shotlist.aspects:
        width, height = ASPECTS[aspect.value]
        out = (int(width * scale) // 2 * 2, int(height * scale) // 2 * 2)
        tmp = tempfile.mkdtemp()
        count = 0
        for count, frame in enumerate(frames_for(beats, out, aspect.value)):
            frame.save(os.path.join(tmp, f"f{count:05d}.png"))
        slug = aspect.value.replace(":", "x")
        gif = os.path.join(outdir, f"demo-{slug}.gif")
        mp4 = os.path.join(outdir, f"demo-{slug}.mp4")
        encode(tmp, shotlist.fps, gif, mp4)
        shutil.copy(os.path.join(tmp, f"f{count // 2:05d}.png"), os.path.join(outdir, f"still-{slug}.png"))
        shutil.rmtree(tmp)
        results[aspect.value] = {"gif": gif, "mp4": mp4, "frames": count + 1}
    return results
