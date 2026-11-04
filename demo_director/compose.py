"""Pure frame math + drawing: cursor, zoom, captions, cards, aspect fit."""
from __future__ import annotations

import functools

from PIL import Image, ImageDraw, ImageFilter, ImageFont

Box = tuple[float, float, float, float]  # x, y, w, h

_FONTS = {
    "regular": (
        "/usr/share/fonts/truetype/macos/Inter-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ),
    "medium": (
        "/usr/share/fonts/truetype/macos/Inter-Medium.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ),
    "semibold": (
        "/usr/share/fonts/truetype/macos/Inter-SemiBold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ),
    "bold": (
        "/usr/share/fonts/truetype/macos/Inter-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ),
}

INK = (232, 236, 244)
MUTED = (148, 163, 184)
ACCENT = (249, 115, 22)
NIGHT = (7, 11, 20)


def clamp(t: float) -> float:
    return min(1.0, max(0.0, t))


def ease(t: float) -> float:
    """Smootherstep. Zero derivative at both ends, so moves settle instead of stopping hard."""
    t = clamp(t)
    return t * t * t * (t * (t * 6 - 15) + 10)


def ease_out(t: float) -> float:
    """Fast start, soft landing. Ripples and fades use this."""
    t = clamp(t)
    return 1 - (1 - t) ** 3


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def lerp_box(a: Box, b: Box, t: float) -> Box:
    return (
        lerp(a[0], b[0], t),
        lerp(a[1], b[1], t),
        lerp(a[2], b[2], t),
        lerp(a[3], b[3], t),
    )


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


def _clamp_box(box: Box, size: tuple[int, int]) -> Box:
    W, H = size
    x, y, w, h = box
    x1, y1 = min(W, max(0.0, x + w)), min(H, max(0.0, y + h))
    x, y = min(W, max(0.0, x)), min(H, max(0.0, y))
    return (x, y, max(1.0, x1 - x), max(1.0, y1 - y))


def frame_rect(target: Box | None, size: tuple[int, int], amount: float, label: Box | None = None) -> Box:
    """Zoom toward target, but never crop the target or its label.

    Without a label this is zoom_rect. With a label the crop widens until both
    boxes sit fully inside the frame, then eases back toward the 1.6x limit.
    """
    if label is None:
        return zoom_rect(target, size, amount)
    W, H = size
    if target is None or amount <= 0:
        return (0, 0, float(W), float(H))
    boxes = [_clamp_box(target, size), _clamp_box(label, size)]
    cx0 = min(b[0] for b in boxes)
    cy0 = min(b[1] for b in boxes)
    cx1 = max(b[0] + b[2] for b in boxes)
    cy1 = max(b[1] + b[3] for b in boxes)
    if cx1 - cx0 >= W or cy1 - cy0 >= H:
        return (0, 0, float(W), float(H))

    pad = max(18.0, 0.035 * min(W, H))
    content_w = (cx1 - cx0) + pad * 2
    content_h = (cy1 - cy0) + pad * 2
    aspect = W / H
    if content_w / max(content_h, 1) > aspect:
        content_h = content_w / aspect
    else:
        content_w = content_h * aspect
    content_w = min(content_w, float(W))

    scale = 1 + 0.6 * ease(amount)
    w = min(float(W), max(W / scale, content_w))
    h = w / aspect
    if h > H:
        h, w = float(H), float(W)

    # Center on the padded union, then shove the crop until the raw boxes are inside.
    px0, py0 = max(0.0, cx0 - pad), max(0.0, cy0 - pad)
    px1, py1 = min(float(W), cx1 + pad), min(float(H), cy1 + pad)
    x, y = (px0 + px1) / 2 - w / 2, (py0 + py1) / 2 - h / 2
    if x > cx0:
        x = cx0
    if y > cy0:
        y = cy0
    if x + w < cx1:
        x = cx1 - w
    if y + h < cy1:
        y = cy1 - h
    x = min(max(x, 0.0), W - w)
    y = min(max(y, 0.0), H - h)
    return (x, y, w, h)


def fit_aspect(img: Image.Image, out: tuple[int, int]) -> Image.Image:
    """Fit img inside out size over a blurred fill of itself (no black bars)."""
    OW, OH = out
    bg = img.copy()
    s = max(OW / bg.width, OH / bg.height)
    bg = bg.resize((int(bg.width * s) + 1, int(bg.height * s) + 1)).crop((0, 0, OW, OH))
    bg = bg.filter(ImageFilter.GaussianBlur(24)).point(lambda p: int(p * 0.55))
    s = min(OW / img.width, OH / img.height)
    fg = img.resize((max(1, int(img.width * s)), max(1, int(img.height * s))), Image.LANCZOS)
    bg.paste(fg, ((OW - fg.width) // 2, (OH - fg.height) // 2))
    return bg


def font(size: int, weight: str = "semibold") -> ImageFont.ImageFont:
    for path in _FONTS.get(weight, _FONTS["semibold"]):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _pointer(x: float, y: float, s: float) -> list[tuple[float, float]]:
    return [
        (x, y),
        (x, y + 20 * s),
        (x + 5.6 * s, y + 15.2 * s),
        (x + 10.2 * s, y + 24.2 * s),
        (x + 13.6 * s, y + 22.4 * s),
        (x + 9.0 * s, y + 14.0 * s),
        (x + 16.6 * s, y + 13.4 * s),
    ]


def draw_cursor(img: Image.Image, pos: tuple[float, float], ripple: float = 0,
                velocity: tuple[float, float] = (0.0, 0.0)) -> None:
    """Crisp pointer, soft shadow, and a short smear when it is actually moving."""
    x, y = pos
    scale = max(1.05, img.width / 1000)
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    pad = 14
    bw = int(32 * scale) + pad * 2
    bh = int(42 * scale) + pad * 2
    sprite = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    ImageDraw.Draw(sprite).polygon(_pointer(pad, pad, scale), fill=(0, 0, 0, 170))
    sprite = sprite.filter(ImageFilter.GaussianBlur(max(1, int(round(1.5 * scale)))))
    overlay.paste(sprite, (int(round(x)) - pad + 2, int(round(y)) - pad + 4), sprite)

    draw = ImageDraw.Draw(overlay, "RGBA")
    vx, vy = velocity
    speed = (vx * vx + vy * vy) ** 0.5
    if speed > 8:
        reach = min(26.0, speed * 0.55)
        ux, uy = vx / speed, vy / speed
        for i in range(6, 0, -1):
            k = i / 6
            alpha = int(64 * (1 - k))
            ox, oy = x - ux * reach * k, y - uy * reach * k
            radius = (1.4 + 2.2 * (1 - k)) * scale
            draw.ellipse((ox - radius, oy - radius, ox + radius, oy + radius), fill=(255, 255, 255, alpha))
    draw.polygon(
        _pointer(x, y, scale),
        fill=(255, 255, 255, 255),
        outline=(12, 12, 16, 255),
        width=max(2, int(round(1.25 * scale))),
    )
    if ripple > 0:
        t = ease_out(ripple)
        radius = (8 + 36 * t) * scale
        alpha = int(230 * (1 - ripple))
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), outline=ACCENT + (alpha,), width=max(2, int(3 * scale)))
        inner = radius * 0.55
        draw.ellipse((x - inner, y - inner, x + inner, y + inner), outline=(255, 255, 255, max(0, alpha // 2)), width=max(1, int(2 * scale)))

    img.paste(Image.alpha_composite(img.convert("RGBA"), overlay).convert(img.mode))


def safe_insets(size: tuple[int, int], aspect: str) -> tuple[int, int, int, int]:
    """Left, top, right, bottom margins captions stay inside.

    9:16 keeps the lower fifth clear of story-style UI chrome.
    """
    W, H = size
    if aspect == "9:16":
        return (int(W * 0.07), int(H * 0.12), int(W * 0.07), int(H * 0.18))
    if aspect == "1:1":
        return (int(W * 0.06), int(H * 0.06), int(W * 0.06), int(H * 0.08))
    return (int(W * 0.05), int(H * 0.05), int(W * 0.05), int(H * 0.06))


def _wrap(draw: ImageDraw.ImageDraw, text: str, face: ImageFont.ImageFont, max_w: float) -> list[str]:
    words = text.split()
    if not words:
        return []
    lines = [words[0]]
    for word in words[1:]:
        trial = lines[-1] + " " + word
        if draw.textlength(trial, font=face) <= max_w:
            lines[-1] = trial
        else:
            lines.append(word)
    return lines


def caption_rect(size: tuple[int, int], text: str | None, aspect: str = "16:9") -> Box | None:
    if not text:
        return None
    W, H = size
    left, top, right, bottom = safe_insets(size, aspect)
    face = font(max(15, W // 34), "semibold")
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    max_text = max(40, W - left - right - 36)
    lines = _wrap(probe, text, face, max_text)
    line_h = int(face.size * 1.28)
    text_w = max(probe.textlength(line, font=face) for line in lines)
    pad_x, pad_y = 18, 12
    box_w = text_w + pad_x * 2
    box_h = line_h * len(lines) + pad_y * 2
    x = min(max((W - box_w) / 2, float(left)), W - right - box_w)
    y = H - bottom - box_h
    if y < top:
        y = float(top)
    return (x, y, box_w, box_h)


def draw_caption(img: Image.Image, text: str | None, aspect: str = "16:9", alpha: float = 1.0) -> None:
    rect = caption_rect(img.size, text, aspect)
    if rect is None or alpha <= 0:
        return
    left, _, right, _ = safe_insets(img.size, aspect)
    face = font(max(15, img.width // 34), "semibold")
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    lines = _wrap(probe, text or "", face, max(40, img.width - left - right - 36))
    x, y, w, h = rect
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")
    a = int(230 * alpha)
    draw.rounded_rectangle((x, y, x + w, y + h), radius=16, fill=(8, 10, 18, a))
    draw.rounded_rectangle((x, y, x + w, y + h), radius=16, outline=(255, 255, 255, int(48 * alpha)), width=1)
    line_h = int(face.size * 1.28)
    ty = y + 12
    for line in lines:
        tw = probe.textlength(line, font=face)
        draw.text((x + (w - tw) / 2, ty), line, font=face, fill=INK + (int(255 * alpha),))
        ty += line_h
    img.paste(Image.alpha_composite(img.convert("RGBA"), overlay).convert(img.mode))


def _measure(text: str, face: ImageFont.ImageFont) -> float:
    return ImageDraw.Draw(Image.new("RGB", (1, 1))).textlength(text, font=face)


@functools.lru_cache(maxsize=8)
def _card_bg(size: tuple[int, int]) -> Image.Image:
    """A small gradient scaled up. Fewer colors, so the GIF doesn't band."""
    W, H = size
    small = Image.new("RGB", (24, 24))
    px = small.load()
    for y in range(24):
        for x in range(24):
            dx, dy = x / 23 - 0.62, y / 23 - 0.48
            glow = max(0.0, 1 - (dx * dx + dy * dy) ** 0.5 * 1.7)
            cool = max(0.0, 0.55 - x / 23) * (1 - y / 40)
            px[x, y] = (
                int(8 + 78 * glow + 6 * cool),
                int(12 + 32 * glow + 16 * cool),
                int(22 + 12 * glow + 40 * cool),
            )
    return small.resize((W, H), Image.BICUBIC)


def _centered(draw: ImageDraw.ImageDraw, text: str, face: ImageFont.ImageFont, y: float, W: int,
              fill, alpha: float) -> None:
    tw = draw.textlength(text, font=face)
    color = fill + (int(255 * alpha),) if len(fill) == 3 else fill
    draw.text(((W - tw) / 2, y), text, font=face, fill=color)


def title_card(size: tuple[int, int], title: str, t: float, subtitle: str | None = None) -> Image.Image:
    """Opening card. t 0..1 eases the rule, the title, and the optional line."""
    t = ease(t)
    img = _card_bg(size).convert("RGBA")
    draw = ImageDraw.Draw(img, "RGBA")
    W, H = size
    rule = int(lerp(0, min(168, W * 0.16), t))
    cy = H * 0.46
    if rule > 2:
        draw.rounded_rectangle((W / 2 - rule / 2, cy - 36, W / 2 + rule / 2, cy - 31), radius=2, fill=ACCENT + (int(255 * t),))
    eyebrow = font(max(12, W // 52), "medium")
    _centered(draw, "SYNTHETIC DEMO", eyebrow, cy - 78, W, ACCENT, t)
    face = font(max(26, W // 16), "bold")
    _centered(draw, title, face, cy - 8, W, INK, t)
    if subtitle:
        sub = font(max(14, W // 38), "regular")
        _centered(draw, subtitle, sub, cy + face.size + 10, W, MUTED, t)
    return img.convert("RGB")


def end_card(size: tuple[int, int], title: str, t: float) -> Image.Image:
    """Closing card. The last thing on screen is the synthetic-data line."""
    t = ease(t)
    img = _card_bg(size).convert("RGBA")
    # Quiet the glow so the end feels settled rather than like a second open.
    img = Image.blend(Image.new("RGBA", size, NIGHT + (255,)), img, 0.72)
    draw = ImageDraw.Draw(img, "RGBA")
    W, H = size
    cy = H * 0.46
    rule = int(lerp(0, min(120, W * 0.12), t))
    if rule > 2:
        draw.rounded_rectangle((W / 2 - rule / 2, cy - 28, W / 2 + rule / 2, cy - 23), radius=2, fill=ACCENT + (int(255 * t),))
    face = font(max(24, W // 18), "bold")
    _centered(draw, title, face, cy - 4, W, INK, t)
    sub = font(max(14, W // 40), "medium")
    _centered(draw, "Synthetic data only", sub, cy + face.size + 16, W, MUTED, t)
    return img.convert("RGB")


def _inside(box: Box, crop: Box, slop: float = 0.75) -> bool:
    x, y, w, h = crop
    return (box[0] >= x - slop and box[1] >= y - slop
            and box[0] + box[2] <= x + w + slop and box[1] + box[3] <= y + h + slop)


def _hits(box: Box, crop: Box) -> bool:
    x, y, w, h = crop
    return not (box[0] + box[2] < x or box[0] > x + w or box[1] + box[3] < y or box[1] > y + h)


def unsliced(crop: Box, lines: tuple[Box, ...], must: tuple[Box, ...], size: tuple[int, int]) -> Box:
    """Slide a crop so it does not cut through a text line. The must-boxes stay inside."""
    x, y, w, h = crop
    W, H = size
    for line in lines:
        current = (x, y, w, h)
        if _inside(line, current) or not _hits(line, current):
            continue
        lx, ly, lw, lh = line
        nx, ny = x, y
        if ly < y:
            ny = ly
        if ly + lh > y + h:
            ny = ly + lh - h
        if lx < x:
            nx = lx
        if lx + lw > x + w:
            nx = lx + lw - w
        nx = min(max(0.0, nx), max(0.0, W - w))
        ny = min(max(0.0, ny), max(0.0, H - h))
        options = [(nx, ny, w, h)]
        if ly + lh / 2 < y + h / 2:
            options.append((x, min(max(0.0, ly + lh + 2), max(0.0, H - h)), w, h))
        else:
            options.append((x, min(max(0.0, ly - h - 2), max(0.0, H - h)), w, h))
        for index, cand in enumerate(options):
            if not all(_inside(item, cand) for item in must):
                continue
            if index == 0 and not _inside(line, cand):
                continue
            if index > 0 and _hits(line, cand):
                continue
            x, y, w, h = cand
            break
    return (x, y, w, h)


def render(shot: Image.Image, crop: Box, cursor: tuple[float, float] | None,
           ripple: float, caption: str | None, out: tuple[int, int],
           aspect: str = "16:9", velocity: tuple[float, float] = (0.0, 0.0),
           caption_alpha: float = 1.0) -> Image.Image:
    x, y, w, h = crop
    view = shot.crop((int(x), int(y), max(int(x) + 1, int(x + w)), max(int(y) + 1, int(y + h))))
    view = view.resize(shot.size, Image.LANCZOS).convert("RGB")
    if cursor is not None:
        sx, sy = shot.width / w, shot.height / h
        draw_cursor(
            view,
            ((cursor[0] - x) * sx, (cursor[1] - y) * sy),
            ripple,
            (velocity[0] * sx, velocity[1] * sy),
        )
    framed = fit_aspect(view, out)
    draw_caption(framed, caption, aspect, caption_alpha)
    return framed
