"""Drive a real browser through the shot list, then compose + encode frames."""
from __future__ import annotations
import os, shutil, subprocess, tempfile
from dataclasses import dataclass
from PIL import Image
from .shotlist import ShotList, ASPECTS
from . import compose


@dataclass
class Beat:
    """One recorded moment: screenshot before the action, target box, after-shot."""
    before: Image.Image
    after: Image.Image
    box: tuple | None
    step: object


def record(sl: ShotList) -> list[Beat]:
    from playwright.sync_api import sync_playwright
    beats: list[Beat] = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": sl.viewport[0], "height": sl.viewport[1]})
        page.goto(sl.url)
        snap = lambda: Image.open(__import__("io").BytesIO(page.screenshot())).convert("RGB")
        for st in sl.steps:
            before = snap()
            box = None
            if st.selector:
                bb = page.locator(st.selector).first.bounding_box()
                box = (bb["x"], bb["y"], bb["width"], bb["height"]) if bb else None
            if st.action == "goto":
                page.goto(st.url)
            elif st.action == "click":
                page.click(st.selector)
            elif st.action == "type":
                page.fill(st.selector, st.text)
            page.wait_for_timeout(120)
            beats.append(Beat(before, snap(), box, st))
        b.close()
    return beats


def frames_for(beats: list[Beat], out: tuple[int, int]):
    """Yield composed frames. Pure function of beats: cursor glides, ripples, zooms, captions."""
    cursor = (beats[0].before.width / 2, beats[0].before.height / 2) if beats else (0, 0)
    caption = None
    zoom_t, zoom_box = 0.0, None
    for bt in beats:
        st = bt.step
        if st.action == "caption":
            caption = st.text
            for _ in range(st.frames):
                yield compose.render(bt.after, compose.zoom_rect(zoom_box, bt.after.size, zoom_t), cursor, 0, caption, out)
            continue
        if bt.box:
            target = compose.center(bt.box)
            new_zoom = 1.0 if st.action in {"zoom", "type"} else 0.5
            n = max(4, st.frames)
            for i in range(n):
                t = compose.ease((i + 1) / n)
                pos = (compose.lerp(cursor[0], target[0], t), compose.lerp(cursor[1], target[1], t))
                z = compose.lerp(zoom_t, new_zoom, t)
                yield compose.render(bt.before, compose.zoom_rect(bt.box, bt.before.size, z), pos, 0, caption, out)
            cursor, zoom_t, zoom_box = target, new_zoom, bt.box
            if st.action == "click":
                for i in range(4):
                    yield compose.render(bt.after, compose.zoom_rect(zoom_box, bt.after.size, zoom_t), cursor, (i + 1) / 4, caption, out)
        hold = st.frames if st.action in {"hold", "type", "goto"} else 3
        for _ in range(hold):
            yield compose.render(bt.after, compose.zoom_rect(zoom_box, bt.after.size, zoom_t), cursor, 0, caption, out)


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
