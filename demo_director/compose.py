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


def _union(a: Box, b: Box, pad: float, size: tuple[int, int]) -> Box:
    """Padded union of two boxes, clipped to the frame. Both boxes stay inside it."""
    W, H = size
    x0 = min(a[0], b[0]) - pad
    y0 = min(a[1], b[1]) - pad
    x1 = max(a[0] + a[2], b[0] + b[2]) + pad
    y1 = max(a[1] + a[3], b[1] + b[3]) + pad
    x0, y0 = max(0.0, x0), max(0.0, y0)
    x1, y1 = min(float(W), x1), min(float(H), y1)
    return (x0, y0, max(0.0, x1 - x0), max(0.0, y1 - y0))


def _place(region: Box, w: float, h: float, size: tuple[int, int]) -> Box:
    """Put a w×h window inside the frame so it covers ``region`` when it fits."""
    W, H = size
    rx, ry, rw, rh = region
    cx, cy = center(region)
    x = min(max(cx - w / 2, 0.0), W - w)
    y = min(max(cy - h / 2, 0.0), H - h)
    if rx < x:
        x = rx
    if ry < y:
        y = ry
    if rx + rw > x + w:
        x = rx + rw - w
    if ry + rh > y + h:
        y = ry + rh - h
    x = min(max(x, 0.0), W - w)
    y = min(max(y, 0.0), H - h)
    return (x, y, w, h)


def zoom_rect(target: Box | None, size: tuple[int, int], amount: float,
              label: Box | None = None) -> Box:
    """Crop rect zooming toward target. amount 0 = full frame, 1 = max zoom (1.6x).

    The rect always lies inside the frame. When ``label`` is set, the rect also
    covers the whole target and the whole label (zooming out, never cropping
    the label off the left edge).
    """
    W, H = size
    if target is None or amount <= 0:
        return (0, 0, W, H)
    scale = 1 + 0.6 * ease(amount)
    w, h = W / scale, H / scale
    if label is None:
        cx, cy = center(target)
        x = min(max(cx - w / 2, 0), W - w)
        y = min(max(cy - h / 2, 0), H - h)
        return (x, y, w, h)
    region = _union(target, label, 16, size)
    rw, rh = region[2], region[3]
    if rw > w or rh > h:
        frac = min(1.0, max(rw / W, rh / H))
        w, h = W * frac, H * frac
        if frac < 1:
            # Keep the region inside despite binary float rounding.
            w, h = min(float(W), w + 0.05), min(float(H), h + 0.05)
    if w >= W or h >= H:
        return (0, 0, W, H)
    return _place(region, w, h, size)


def safe_insets(size: tuple[int, int]) -> tuple[int, int, int, int]:
    """left, top, right, bottom. Portrait (9:16) keeps the caption out of the chrome band."""
    W, H = size
    if H > W:
        return (int(W * 0.06), int(H * 0.10), int(W * 0.06), int(H * 0.18))
    return (16, 16, 16, 28)


def caption_placement(size: tuple[int, int], text_w: float, text_h: float, pad: int = 18
                      ) -> tuple[float, float, float, float]:
    """Pill rectangle for a caption. A caption that fits sits inside the safe area."""
    W, H = size
    left, top, right, bottom = safe_insets(size)
    pill_w = float(text_w + pad * 2)
    pill_h = float(text_h + pad * 2)
    avail_w = max(1.0, W - left - right)
    x0 = left + max(0.0, (avail_w - pill_w) / 2.0)
    if pill_w <= W:
        x0 = min(max(0.0, x0), W - pill_w)
    else:
        x0 = 0.0
    y0 = H - bottom - pill_h
    if y0 < top:
        y0 = float(top)
    if y0 + pill_h > H:
        y0 = max(0.0, H - pill_h)
    if y0 < 0:
        y0 = 0.0
    return (x0, y0, x0 + min(pill_w, float(W)), y0 + min(pill_h, float(H)))


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
    left, _top, right, _bottom = safe_insets(img.size)
    max_w = max(40, img.width - left - right - 36)
    size = max(16, img.width // 34)
    f = _font(size)
    while size > 12 and d.textlength(text, font=f) > max_w:
        size -= 2
        f = _font(size)
    tw = d.textlength(text, font=f)
    pad = 18
    x0, y0, x1, y1 = caption_placement(img.size, tw, f.size, pad)
    d.rounded_rectangle((x0, y0, x1, y1), radius=14, fill=(10, 10, 20, int(210 * alpha)))
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
