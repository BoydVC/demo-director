"""Pure frame math + drawing: cursor glide, click ripple, zoom, captions, aspect fit."""
from __future__ import annotations
from PIL import Image, ImageDraw, ImageFilter, ImageFont

Box = tuple[float, float, float, float]  # x, y, w, h


def ease(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return 3 * t * t - 2 * t * t * t


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def center(b: Box) -> tuple[float, float]:
    return b[0] + b[2] / 2, b[1] + b[3] / 2


def zoom_rect(target: Box | None, size: tuple[int, int], amount: float) -> Box:
    """Crop rect zooming toward target. amount 0 = full frame, 1 = max zoom (1.6x). Stays inside frame."""
    W, H = size
    if target is None or amount <= 0:
        return (0, 0, W, H)
    scale = 1 + 0.6 * ease(amount)
    w, h = W / scale, H / scale
    cx, cy = center(target)
    x = min(max(cx - w / 2, 0), W - w)
    y = min(max(cy - h / 2, 0), H - h)
    return (x, y, w, h)


def fit_aspect(img: Image.Image, out: tuple[int, int]) -> Image.Image:
    """Fit img inside out size over a blurred fill of itself (no black bars)."""
    OW, OH = out
    bg = img.copy()
    s = max(OW / bg.width, OH / bg.height)
    bg = bg.resize((int(bg.width * s) + 1, int(bg.height * s) + 1)).crop((0, 0, OW, OH))
    bg = bg.filter(ImageFilter.GaussianBlur(24)).point(lambda p: int(p * 0.55))
    s = min(OW / img.width, OH / img.height)
    fg = img.resize((int(img.width * s), int(img.height * s)), Image.LANCZOS)
    bg.paste(fg, ((OW - fg.width) // 2, (OH - fg.height) // 2))
    return bg


def _font(size: int):
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",):
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            pass
    return ImageFont.load_default()


def draw_cursor(img: Image.Image, pos: tuple[float, float], ripple: float = 0) -> None:
    d = ImageDraw.Draw(img, "RGBA")
    x, y = pos
    if ripple > 0:
        r = 8 + 28 * ripple
        d.ellipse((x - r, y - r, x + r, y + r), outline=(255, 90, 60, int(255 * (1 - ripple))), width=4)
    pts = [(x, y), (x, y + 22), (x + 6, y + 16), (x + 11, y + 26), (x + 15, y + 24), (x + 10, y + 15), (x + 18, y + 15)]
    d.polygon(pts, fill=(15, 15, 15, 255), outline=(255, 255, 255, 255))


def draw_caption(img: Image.Image, text: str | None, alpha: float = 1.0) -> None:
    if not text:
        return
    d = ImageDraw.Draw(img, "RGBA")
    f = _font(max(18, img.width // 34))
    tw = d.textlength(text, font=f)
    pad = 18
    h = f.size + pad * 2
    x0 = (img.width - tw) / 2 - pad
    y0 = img.height - h - 28
    d.rounded_rectangle((x0, y0, x0 + tw + pad * 2, y0 + h), radius=14, fill=(10, 10, 20, int(210 * alpha)))
    d.text((x0 + pad, y0 + pad - 2), text, font=f, fill=(255, 255, 255, int(255 * alpha)))


def render(shot: Image.Image, crop: Box, cursor: tuple[float, float] | None,
           ripple: float, caption: str | None, out: tuple[int, int]) -> Image.Image:
    x, y, w, h = crop
    view = shot.crop((int(x), int(y), int(x + w), int(y + h))).resize(shot.size, Image.LANCZOS).convert("RGB")
    if cursor is not None:
        sx, sy = shot.width / w, shot.height / h
        draw_cursor(view, ((cursor[0] - x) * sx, (cursor[1] - y) * sy), ripple)
    framed = fit_aspect(view, out)
    draw_caption(framed, caption)
    return framed
