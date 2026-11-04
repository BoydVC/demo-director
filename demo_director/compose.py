"""Pure frame math + drawing: cursor glide, click ripple, zoom, captions, aspect fit."""
from __future__ import annotations

from PIL import Image, ImageDraw, ImageFilter, ImageFont

Box = tuple[float, float, float, float]  # x, y, w, h
Insets = tuple[int, int, int, int]  # left, top, right, bottom


def ease(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return 3 * t * t - 2 * t * t * t


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def center(b: Box) -> tuple[float, float]:
    return b[0] + b[2] / 2, b[1] + b[3] / 2


def _needed(target: Box, label: Box | None, pad: float, size: tuple[int, int]) -> Box:
    """Union of the target and its label, padded, then clipped to the frame."""
    boxes = (target,) if label is None else (target, label)
    x0 = min(box[0] for box in boxes) - pad
    y0 = min(box[1] for box in boxes) - pad
    x1 = max(box[0] + box[2] for box in boxes) + pad
    y1 = max(box[1] + box[3] for box in boxes) + pad
    width, height = float(size[0]), float(size[1])
    left, top = max(0.0, x0), max(0.0, y0)
    right, bottom = min(width, x1), min(height, y1)
    return (left, top, max(1.0, right - left), max(1.0, bottom - top))


def zoom_rect(target: Box | None, size: tuple[int, int], amount: float, label: Box | None = None) -> Box:
    """Crop rect zooming toward target. amount 0 = full frame, 1 = max zoom (1.6x).

    The crop stays inside the frame and, at every amount above 0, contains the
    whole target and its label. A wide label limits the zoom instead of losing
    the text to the left of the target.
    """
    width, height = size
    frame = (0, 0, width, height)
    if target is None or amount <= 0:
        return frame
    scale = 1 + 0.6 * ease(amount)
    crop_w, crop_h = width / scale, height / scale
    need_x, need_y, need_w, need_h = _needed(target, label, 16, size)
    frame_aspect = width / float(height)
    if need_w / need_h > frame_aspect:
        min_w, min_h = need_w, need_w / frame_aspect
    else:
        min_h, min_w = need_h, need_h * frame_aspect
    if min_w > crop_w:
        crop_w, crop_h = min_w, min_h
    if crop_w >= width or crop_h >= height:
        return frame
    return _place(need_x, need_y, need_w, need_h, crop_w, crop_h, width, height)


def _place(need_x: float, need_y: float, need_w: float, need_h: float,
           crop_w: float, crop_h: float, width: float, height: float) -> Box:
    def axis(origin: float, span: float, crop: float, limit: float) -> float:
        end = origin + span
        low = max(0.0, end - crop)
        high = min(limit - crop, origin)
        center_at = origin + span / 2
        if low <= high:
            return min(max(center_at - crop / 2, low), high)
        return min(max(center_at - crop / 2, 0.0), limit - crop)

    x = axis(need_x, need_w, crop_w, float(width))
    y = axis(need_y, need_h, crop_h, float(height))
    return (x, y, crop_w, crop_h)


def safe_insets(out: tuple[int, int], aspect: str | None = None) -> Insets:
    """Caption padding. 9:16 keeps the caption above the platform's bottom chrome."""
    width, height = out
    if aspect == "9:16":
        return (int(width * 0.07), int(height * 0.08), int(width * 0.12), int(height * 0.16))
    if aspect == "1:1":
        return (int(width * 0.05), int(height * 0.04), int(width * 0.05), int(height * 0.07))
    return (24, 16, 24, 28)


def fit_aspect(img: Image.Image, out: tuple[int, int]) -> Image.Image:
    """Fit img inside out size over a blurred fill of itself (no black bars)."""
    out_w, out_h = out
    background = img.copy()
    scale = max(out_w / background.width, out_h / background.height)
    background = background.resize((int(background.width * scale) + 1, int(background.height * scale) + 1)).crop((0, 0, out_w, out_h))
    background = background.filter(ImageFilter.GaussianBlur(24)).point(lambda p: int(p * 0.55))
    scale = min(out_w / img.width, out_h / img.height)
    foreground = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
    background.paste(foreground, ((out_w - foreground.width) // 2, (out_h - foreground.height) // 2))
    return background


def _font(size: int):
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default()


def draw_cursor(img: Image.Image, pos: tuple[float, float], ripple: float = 0) -> None:
    draw = ImageDraw.Draw(img, "RGBA")
    x, y = pos
    if ripple > 0:
        radius = 8 + 28 * ripple
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), outline=(255, 90, 60, int(255 * (1 - ripple))), width=4)
    points = [(x, y), (x, y + 22), (x + 6, y + 16), (x + 11, y + 26), (x + 15, y + 24), (x + 10, y + 15), (x + 18, y + 15)]
    draw.polygon(points, fill=(15, 15, 15, 255), outline=(255, 255, 255, 255))


def draw_caption(img: Image.Image, text: str | None, alpha: float = 1.0, insets: Insets | None = None) -> None:
    if not text:
        return
    left, top, right, bottom = insets or (24, 16, 24, 28)
    draw = ImageDraw.Draw(img, "RGBA")
    max_text = max(40, img.width - left - right - 36)
    size = max(16, img.width // 34)
    font = _font(size)
    while size > 12 and draw.textlength(text, font=font) > max_text:
        size -= 2
        font = _font(size)
    text_w = draw.textlength(text, font=font)
    bbox = draw.textbbox((0, 0), text, font=font)
    text_h = max(1, bbox[3] - bbox[1])
    pad = 18
    block_w = text_w + pad * 2
    block_h = text_h + pad * 2
    usable_w = img.width - left - right
    x0 = left + max(0, (usable_w - block_w) / 2)
    y0 = img.height - bottom - block_h
    if y0 < top:
        y0 = float(top)
    draw.rounded_rectangle((x0, y0, x0 + block_w, y0 + block_h), radius=14, fill=(10, 10, 20, int(210 * alpha)))
    draw.text((x0 + pad, y0 + pad - bbox[1]), text, font=font, fill=(255, 255, 255, int(255 * alpha)))


def render(shot: Image.Image, crop: Box, cursor: tuple[float, float] | None,
           ripple: float, caption: str | None, out: tuple[int, int],
           aspect: str | None = None) -> Image.Image:
    x, y, w, h = crop
    view = shot.crop((int(x), int(y), int(x + w), int(y + h))).resize(shot.size, Image.LANCZOS).convert("RGB")
    if cursor is not None:
        scale_x, scale_y = shot.width / w, shot.height / h
        draw_cursor(view, ((cursor[0] - x) * scale_x, (cursor[1] - y) * scale_y), ripple)
    framed = fit_aspect(view, out)
    draw_caption(framed, caption, insets=safe_insets(out, aspect))
    return framed
