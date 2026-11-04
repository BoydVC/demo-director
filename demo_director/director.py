"""Drive a real browser through the shot list, then compose + encode frames.

Every step goes through ``session.run_step``. The only way to append a beat is
for that call to return ``captured`` with the beat count increased by one.
PII, a missing selector, or a timeout returns a typed failure and no beat.
"""
from __future__ import annotations
import io, os, shutil, subprocess, tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from PIL import Image
from .shotlist import ShotList, ASPECTS
from . import compose
from .guard import hits
from .session import (
    Phase, RecordingFailure, Session, StepSignals, run_step,
)


@dataclass
class Beat:
    """One recorded moment: screenshot before the action, target box, after-shot."""
    before: Image.Image
    after: Image.Image
    box: tuple | None
    step: object
    label: tuple | None = None


_LABEL_JS = """(selector) => {
  const el = document.querySelector(selector);
  if (!el) return null;
  const box = (n) => {
    if (!n || n === document.body || n === document.documentElement) return null;
    const r = n.getBoundingClientRect();
    if (!(r.width > 1 && r.height > 1)) return null;
    return [r.x, r.y, r.width, r.height];
  };
  const id = el.id;
  if (id) {
    const lab = document.querySelector('label[for="' + CSS.escape(id) + '"]');
    if (lab) return box(lab);
  }
  const wrap = el.closest && el.closest('label');
  if (wrap && wrap !== el) return box(wrap);
  const by = el.getAttribute('aria-labelledby');
  if (by) {
    const n = document.getElementById(by.split(/\\s+/)[0]);
    if (n) return box(n);
  }
  // Prefer the control group (the row or card) so a zoom on a button keeps
  // the field to its left and the sibling button to its right in frame.
  const own = el.getBoundingClientRect();
  const parent = el.parentElement;
  const parentBox = box(parent);
  if (parentBox && parentBox[3] <= 240 && parentBox[2] >= own.width + 8) return parentBox;
  if (el.previousElementSibling) return box(el.previousElementSibling);
  return parentBox;
}"""

_TEXT_JS = """() => {
  const parts = [];
  if (document.body) parts.push(document.body.innerText || "");
  document.querySelectorAll("input, textarea").forEach((el) => {
    parts.push(el.value || "");
    parts.push(el.getAttribute("placeholder") || "");
  });
  return parts.join("\\n");
}"""


def resolve_fixture(sl: ShotList) -> ShotList:
    """``url: FIXTURE`` is the checked-in synthetic app, from any working directory."""
    if sl.url != "FIXTURE":
        return sl
    root = Path(__file__).resolve().parents[1]
    return replace(sl, url=(root / "fixtures" / "app.html").resolve().as_uri())


def _page_text(page) -> str | None:
    try:
        text = page.evaluate(_TEXT_JS)
    except Exception:
        return None
    return text if isinstance(text, str) else None


def _snap(page) -> Image.Image:
    return Image.open(io.BytesIO(page.screenshot())).convert("RGB")


def _probe(page, selector: str, timeout_ms: int = 3000):
    """Return (found, box, label, timed_out). Missing and timeout stay distinct."""
    loc = page.locator(selector).first
    try:
        count = loc.count()
    except Exception:
        return False, None, None, True
    if count == 0:
        return False, None, None, False
    try:
        bb = loc.bounding_box(timeout=timeout_ms)
    except Exception as exc:
        if "Timeout" in type(exc).__name__:
            return False, None, None, True
        return False, None, None, True
    if not bb:
        return False, None, None, False
    box = (bb["x"], bb["y"], bb["width"], bb["height"])
    label = None
    try:
        raw = page.evaluate(_LABEL_JS, selector)
        if isinstance(raw, list) and len(raw) == 4:
            label = tuple(float(v) for v in raw)
    except Exception:
        label = None
    return True, box, label, False


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
    found, box, label, nav_timeout = True, None, None, False
    if needs and not pii:
        found, box, label, nav_timeout = _probe(page, step.selector)
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
        # Unreadable text after a failed action is PII (fail closed), not a soft timeout.
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
    return final, Beat(before, after, box, step, label)


def record(sl: ShotList) -> list[Beat]:
    """Walk the shot list. Raises RecordingFailure instead of returning a partial take."""
    from playwright.sync_api import sync_playwright

    sl = resolve_fixture(sl)
    beats: list[Beat] = []
    session = Session.idle()
    names = sl.deny_names
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": sl.viewport[0], "height": sl.viewport[1]})
        try:
            try:
                page.goto(sl.url, timeout=15000)
            except Exception as exc:
                raise RecordingFailure("timeout", -1, "navigation") from exc
            opening = _page_text(page)
            if hits(opening, names):
                # Same gate as a step: PII on the landing page captures nothing.
                blocked = run_step(session, StepSignals(False, True, None, True, False))
                raise RecordingFailure(blocked.failure or "pii-blocked", -1, ",".join(hits(opening, names)))
            for index, step in enumerate(sl.steps):
                session, beat = _drive_step(page, session, step, names)
                if beat is None:
                    detail = session.failure or "no beat"
                    raise RecordingFailure(session.failure or "timeout", index, detail)
                beats.append(beat)
                if session.beats != len(beats):
                    raise RecordingFailure("timeout", index, "beat invariant")
        finally:
            browser.close()
    return beats


def frames_for(beats: list[Beat], out: tuple[int, int]):
    """Yield composed frames. Pure function of beats: cursor glides, ripples, zooms, captions."""
    cursor = (beats[0].before.width / 2, beats[0].before.height / 2) if beats else (0, 0)
    caption = None
    zoom_t, zoom_box, zoom_label = 0.0, None, None
    for bt in beats:
        st = bt.step
        label = bt.label
        if st.action == "caption":
            caption = st.text
            crop = compose.zoom_rect(zoom_box, bt.after.size, zoom_t, zoom_label)
            for _ in range(st.frames):
                yield compose.render(bt.after, crop, cursor, 0, caption, out)
            continue
        if bt.box:
            target = compose.center(bt.box)
            new_zoom = 1.0 if st.action in {"zoom", "type"} else 0.5
            n = max(4, st.frames)
            for i in range(n):
                t = compose.ease((i + 1) / n)
                pos = (compose.lerp(cursor[0], target[0], t), compose.lerp(cursor[1], target[1], t))
                z = compose.lerp(zoom_t, new_zoom, t)
                yield compose.render(
                    bt.before, compose.zoom_rect(bt.box, bt.before.size, z, label), pos, 0, caption, out
                )
            cursor, zoom_t, zoom_box, zoom_label = target, new_zoom, bt.box, label
            if st.action == "click":
                crop = compose.zoom_rect(zoom_box, bt.after.size, zoom_t, zoom_label)
                for i in range(4):
                    yield compose.render(bt.after, crop, cursor, (i + 1) / 4, caption, out)
        hold = st.frames if st.action in {"hold", "type", "goto"} else 3
        crop = compose.zoom_rect(zoom_box, bt.after.size, zoom_t, zoom_label)
        for _ in range(hold):
            yield compose.render(bt.after, crop, cursor, 0, caption, out)


def encode(frames_dir: str, fps: int, gif: str, mp4: str | None) -> None:
    pat = os.path.join(frames_dir, "f%05d.png")
    vf = f"fps={fps},split[a][b];[a]palettegen=max_colors=160[p];[b][p]paletteuse=dither=sierra2_4a"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(fps), "-i", pat, "-vf", vf, gif], check=True)
    if mp4:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(fps), "-i", pat,
                        "-pix_fmt", "yuv420p", "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", mp4], check=True)


def direct(sl: ShotList, outdir: str, scale: float = 0.6) -> dict:
    os.makedirs(outdir, exist_ok=True)
    beats = record(sl)
    results = {}
    for asp in sl.aspects:
        W, H = ASPECTS[asp]
        out = (int(W * scale) // 2 * 2, int(H * scale) // 2 * 2)
        tmp = tempfile.mkdtemp()
        n = 0
        for n, fr in enumerate(frames_for(beats, out)):
            fr.save(os.path.join(tmp, f"f{n:05d}.png"))
        slug = asp.replace(":", "x")
        gif, mp4 = os.path.join(outdir, f"demo-{slug}.gif"), os.path.join(outdir, f"demo-{slug}.mp4")
        encode(tmp, sl.fps, gif, mp4)
        shutil.copy(os.path.join(tmp, f"f{n // 2:05d}.png"), os.path.join(outdir, f"still-{slug}.png"))
        shutil.rmtree(tmp)
        results[asp] = {"gif": gif, "mp4": mp4, "frames": n + 1}
    return results
