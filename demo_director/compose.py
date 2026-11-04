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


def _union(a: Box, b: Box, pad: float, size: tuple[int, int]) -> Box:
    """Padded union of two boxes, clipped to the frame. Both boxes stay inside it."""
    width, height = size
    x0 = min(a[0], b[0]) - pad
    y0 = min(a[1], b[1]) - pad
    x1 = max(a[0] + a[2], b[0] + b[2]) + pad
    y1 = max(a[1] + a[3], b[1] + b[3]) + pad
    x0, y0 = max(0.0, x0), max(0.0, y0)
    x1, y1 = min(float(width), x1), min(float(height), y1)
    return (x0, y0, max(0.0, x1 - x0), max(0.0, y1 - y0))


def _place(region: Box, w: float, h: float, size: tuple[int, int]) -> Box:
    """Put a w×h window inside the frame so it covers ``region`` when it fits."""
    width, height = size
    rx, ry, rw, rh = region
    cx, cy = center(region)
    x = min(max(cx - w / 2, 0.0), width - w)
    y = min(max(cy - h / 2, 0.0), height - h)
    if rx < x:
        x = rx
    if ry < y:
        y = ry
    if rx + rw > x + w:
        x = rx + rw - w
    if ry + rh > y + h:
        y = ry + rh - h
    x = min(max(x, 0.0), width - w)
    y = min(max(y, 0.0), height - h)
    return (x, y, w, h)


def zoom_rect(target: Box | None, size: tuple[int, int], amount: float, label: Box | None = None) -> Box:
    """Crop rect zooming toward target. amount 0 = full frame, 1 = max zoom (1.6x).

    The rect always lies inside the frame. A small target with no label is the
    1.6x window. A target or label that does not fit in that window widens the
    crop until both are fully visible, instead of slicing the text.
    """
    width, height = size
    if target is None or amount <= 0:
        return (0, 0, width, height)
    scale = 1 + 0.6 * ease(amount)
    crop_w, crop_h = width / scale, height / scale
    region = target if label is None else _union(target, label, 16, size)
    need_w, need_h = region[2], region[3]
    if need_w > crop_w or need_h > crop_h:
        frac = min(1.0, max(need_w / float(width), need_h / float(height)))
        crop_w, crop_h = width * frac, height * frac
        if frac < 1:
            crop_w = min(float(width), crop_w + 0.05)
            crop_h = min(float(height), crop_h + 0.05)
    if crop_w >= width - 1e-6 or crop_h >= height - 1e-6:
        return (0, 0, width, height)
    return _place(region, crop_w, crop_h, size)


def _clamp_box(box: Box, size: tuple[int, int]) -> Box:
    width, height = size
    x, y, w, h = box
    x1, y1 = min(width, max(0.0, x + w)), min(height, max(0.0, y + h))
    x, y = min(width, max(0.0, x)), min(height, max(0.0, y))
    return (x, y, max(1.0, x1 - x), max(1.0, y1 - y))


def frame_rect(target: Box | None, size: tuple[int, int], amount: float, label: Box | None = None) -> Box:
    """Zoom toward target, but never crop the target or its label.

    Without a label this is zoom_rect. With a label the crop widens until both
    boxes sit fully inside the frame, then eases back toward the 1.6x limit.
    """
    if label is None:
        return zoom_rect(target, size, amount)
    width, height = size
    if target is None or amount <= 0:
        return (0, 0, float(width), float(height))
    boxes = [_clamp_box(target, size), _clamp_box(label, size)]
    x0 = min(box[0] for box in boxes)
    y0 = min(box[1] for box in boxes)
    x1 = max(box[0] + box[2] for box in boxes)
    y1 = max(box[1] + box[3] for box in boxes)
    if x1 - x0 >= width or y1 - y0 >= height:
        return (0, 0, float(width), float(height))

    pad = max(18.0, 0.035 * min(width, height))
    content_w = (x1 - x0) + pad * 2
    content_h = (y1 - y0) + pad * 2
    aspect = width / height
    if content_w / max(content_h, 1) > aspect:
        content_h = content_w / aspect
    else:
        content_w = content_h * aspect
    content_w = min(content_w, float(width))

    scale = 1 + 0.6 * ease(amount)
    crop_w = min(float(width), max(width / scale, content_w))
    crop_h = crop_w / aspect
    if crop_h > height:
        crop_h, crop_w = float(height), float(width)

    px0, py0 = max(0.0, x0 - pad), max(0.0, y0 - pad)
    px1, py1 = min(float(width), x1 + pad), min(float(height), y1 + pad)
    x, y = (px0 + px1) / 2 - crop_w / 2, (py0 + py1) / 2 - crop_h / 2
    if x > x0:
        x = x0
    if y > y0:
        y = y0
    if x + crop_w < x1:
        x = x1 - crop_w
    if y + crop_h < y1:
        y = y1 - crop_h
    x = min(max(x, 0.0), width - crop_w)
    y = min(max(y, 0.0), height - crop_h)
    return (x, y, crop_w, crop_h)


def fit_aspect(img: Image.Image, out: tuple[int, int]) -> Image.Image:
    """Fit img inside out size over a blurred fill of itself (no black bars)."""
    out_w, out_h = out
    background = img.copy()
    scale = max(out_w / background.width, out_h / background.height)
    background = background.resize((int(background.width * scale) + 1, int(background.height * scale) + 1)).crop((0, 0, out_w, out_h))
    background = background.filter(ImageFilter.GaussianBlur(24)).point(lambda p: int(p * 0.55))
    scale = min(out_w / img.width, out_h / img.height)
    foreground = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.LANCZOS)
    background.paste(foreground, ((out_w - foreground.width) // 2, (out_h - foreground.height) // 2))
    return background


def font(size: int, weight: str = "semibold") -> ImageFont.ImageFont:
    for path in _FONTS.get(weight, _FONTS["semibold"]):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _pointer(x: float, y: float, scale: float) -> list[tuple[float, float]]:
    return [
        (x, y),
        (x, y + 20 * scale),
        (x + 5.6 * scale, y + 15.2 * scale),
        (x + 10.2 * scale, y + 24.2 * scale),
        (x + 13.6 * scale, y + 22.4 * scale),
        (x + 9.0 * scale, y + 14.0 * scale),
        (x + 16.6 * scale, y + 13.4 * scale),
    ]


def draw_cursor(img: Image.Image, pos: tuple[float, float], ripple: float = 0,
                velocity: tuple[float, float] = (0.0, 0.0)) -> None:
    """Crisp pointer, soft shadow, and a short smear when it is actually moving."""
    x, y = pos
    scale = max(1.05, img.width / 1000)
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    pad = 14
    sprite_w = int(32 * scale) + pad * 2
    sprite_h = int(42 * scale) + pad * 2
    sprite = Image.new("RGBA", (sprite_w, sprite_h), (0, 0, 0, 0))
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
        draw.ellipse(
            (x - inner, y - inner, x + inner, y + inner),
            outline=(255, 255, 255, max(0, alpha // 2)),
            width=max(1, int(2 * scale)),
        )
    img.paste(Image.alpha_composite(img.convert("RGBA"), overlay).convert(img.mode))


def safe_insets(size: tuple[int, int], aspect: str | None = None) -> tuple[int, int, int, int]:
    """Left, top, right, bottom margins captions stay inside.

    9:16 keeps the lower band clear of story-style UI chrome. A portrait frame
    with no aspect hint uses the same bottom band.
    """
    width, height = size
    if aspect == "9:16" or (aspect is None and height > width):
        if aspect == "9:16":
            return (int(width * 0.07), int(height * 0.12), int(width * 0.07), int(height * 0.18))
        return (int(width * 0.06), int(height * 0.10), int(width * 0.06), int(height * 0.18))
    if aspect == "1:1":
        return (int(width * 0.06), int(height * 0.06), int(width * 0.06), int(height * 0.08))
    return (max(16, int(width * 0.05)), 16, max(16, int(width * 0.05)), max(28, int(height * 0.06)))


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


def caption_rect(size: tuple[int, int], text: str | None, aspect: str | None = None) -> Box | None:
    if not text:
        return None
    width, height = size
    left, top, right, bottom = safe_insets(size, aspect)
    face = font(max(15, width // 34), "semibold")
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    max_text = max(40, width - left - right - 36)
    lines = _wrap(probe, text, face, max_text)
    line_h = int(face.size * 1.28)
    text_w = max(probe.textlength(line, font=face) for line in lines)
    pad_x, pad_y = 18, 12
    box_w = text_w + pad_x * 2
    box_h = line_h * len(lines) + pad_y * 2
    x = min(max((width - box_w) / 2, float(left)), width - right - box_w)
    y = height - bottom - box_h
    if y < top:
        y = float(top)
    return (x, y, box_w, box_h)


def caption_placement(size: tuple[int, int], text_w: float, text_h: float, pad: int = 18
                      ) -> tuple[float, float, float, float]:
    """Pill rectangle for a caption. A caption that fits sits inside the safe area."""
    width, height = size
    left, top, right, bottom = safe_insets(size)
    pill_w = float(text_w + pad * 2)
    pill_h = float(text_h + pad * 2)
    avail_w = max(1.0, width - left - right)
    x0 = left + max(0.0, (avail_w - pill_w) / 2.0)
    if pill_w <= width:
        x0 = min(max(0.0, x0), width - pill_w)
    else:
        x0 = 0.0
    y0 = height - bottom - pill_h
    if y0 < top:
        y0 = float(top)
    if y0 + pill_h > height:
        y0 = max(0.0, height - pill_h)
    if y0 < 0:
        y0 = 0.0
    return (x0, y0, x0 + min(pill_w, float(width)), y0 + min(pill_h, float(height)))


def draw_caption(img: Image.Image, text: str | None, aspect: str | None = None, alpha: float = 1.0) -> None:
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
    ink = int(230 * alpha)
    draw.rounded_rectangle((x, y, x + w, y + h), radius=16, fill=(8, 10, 18, ink))
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
    width, height = size
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
    return small.resize((width, height), Image.BICUBIC)


def _fit(draw: ImageDraw.ImageDraw, text: str, size: int, style: str, width: int, floor: int = 14):
    """Largest face at or under ``size`` whose text fits in 90% of the width.

    A fixed width//16 face clips a long title on both edges. Shrink until it
    fits. The floor is a preference; a title that is still wider keeps
    shrinking so the card never paints past the frame.
    """
    face = font(size, style)
    limit = width * 0.9
    while size > floor and draw.textlength(text, font=face) > limit:
        nxt = int(size * 0.92)
        size = floor if nxt <= floor else nxt
        face = font(size, style)
    while size > 6 and draw.textlength(text, font=face) > limit:
        size -= 1
        face = font(size, style)
    return face


def _centered(draw: ImageDraw.ImageDraw, text: str, face: ImageFont.ImageFont, y: float, width: int,
              fill, alpha: float) -> None:
    tw = draw.textlength(text, font=face)
    color = fill + (int(255 * alpha),) if len(fill) == 3 else fill
    draw.text(((width - tw) / 2, y), text, font=face, fill=color)


def title_card(size: tuple[int, int], title: str, t: float, subtitle: str | None = None) -> Image.Image:
    """Opening card. t 0..1 eases the rule, the title, and the optional line."""
    t = ease(t)
    img = _card_bg(size).convert("RGBA")
    draw = ImageDraw.Draw(img, "RGBA")
    width, height = size
    rule = int(lerp(0, min(168, width * 0.16), t))
    cy = height * 0.46
    if rule > 2:
        draw.rounded_rectangle((width / 2 - rule / 2, cy - 36, width / 2 + rule / 2, cy - 31), radius=2, fill=ACCENT + (int(255 * t),))
    eyebrow = font(max(12, width // 52), "medium")
    _centered(draw, "SYNTHETIC DEMO", eyebrow, cy - 78, width, ACCENT, t)
    face = _fit(draw, title, max(26, width // 16), "bold", width)
    _centered(draw, title, face, cy - 8, width, INK, t)
    if subtitle:
        sub = font(max(14, width // 38), "regular")
        _centered(draw, subtitle, sub, cy + face.size + 10, width, MUTED, t)
    return img.convert("RGB")


def end_card(size: tuple[int, int], title: str, t: float) -> Image.Image:
    """Closing card. The last thing on screen is the synthetic-data line."""
    t = ease(t)
    img = _card_bg(size).convert("RGBA")
    img = Image.blend(Image.new("RGBA", size, NIGHT + (255,)), img, 0.72)
    draw = ImageDraw.Draw(img, "RGBA")
    width, height = size
    cy = height * 0.46
    rule = int(lerp(0, min(120, width * 0.12), t))
    if rule > 2:
        draw.rounded_rectangle((width / 2 - rule / 2, cy - 28, width / 2 + rule / 2, cy - 23), radius=2, fill=ACCENT + (int(255 * t),))
    face = _fit(draw, title, max(24, width // 18), "bold", width)
    _centered(draw, title, face, cy - 4, width, INK, t)
    sub = font(max(14, width // 40), "medium")
    _centered(draw, "Synthetic data only", sub, cy + face.size + 16, width, MUTED, t)
    return img.convert("RGB")


def _inside(box: Box, crop: Box, slop: float = 0.75) -> bool:
    x, y, w, h = crop
    return (box[0] >= x - slop and box[1] >= y - slop
            and box[0] + box[2] <= x + w + slop and box[1] + box[3] <= y + h + slop)


def _hits(box: Box, crop: Box) -> bool:
    x, y, w, h = crop
    return not (box[0] + box[2] < x or box[0] > x + w or box[1] + box[3] < y or box[1] > y + h)


def contain_left_text(crop: Box, lines: tuple[Box, ...], must: tuple[Box, ...], size: tuple[int, int]) -> Box:
    """Grow a crop so a text block cut on the left stays whole, with the target.

    A 1.6x window aimed at a right-hand control slices a left-aligned heading
    ("nd Men's Basketball", "eyboard Kickoff"). Sliding the window cannot keep
    both, so the crop widens until the heading, the label, and the target sit
    inside the frame. Text that is wider than the frame is pinned to the left
    edge, so the start of the line is what remains visible.
    """
    width, height = size
    x, y, w, h = crop
    extra = []
    for line in lines:
        if _inside(line, crop) or line[0] >= x - 0.25:
            continue
        overlaps_y = not (line[1] + line[3] < y or line[1] > y + h)
        # Sliced on the left, or sitting in the same band and fully left of
        # the window. Either way the left-aligned block has to come inside.
        if (_hits(line, crop) and not _inside(line, crop)) or overlaps_y:
            extra.append(line)
    if not extra:
        return crop
    boxes = (crop, *must, *extra)
    x0 = min(box[0] for box in boxes)
    y0 = min(box[1] for box in boxes)
    x1 = max(box[0] + box[2] for box in boxes)
    y1 = max(box[1] + box[3] for box in boxes)
    pad = 12.0
    x0, y0 = max(0.0, x0 - pad), max(0.0, y0 - pad)
    x1, y1 = min(float(width), x1 + pad), min(float(height), y1 + pad)
    for box in (*must, *extra):
        x0 = min(x0, max(0.0, box[0]))
        y0 = min(y0, max(0.0, box[1]))
        x1 = max(x1, min(float(width), box[0] + box[2]))
        y1 = max(y1, min(float(height), box[1] + box[3]))
    if x1 - x0 >= width - 1:
        x0, x1 = 0.0, float(width)
    if y1 - y0 >= height - 1:
        y0, y1 = 0.0, float(height)
    # A left-aligned block that still starts left of the crop is pinned so
    # the first letters are in frame even when the line cannot fit whole.
    left = min(line[0] for line in extra)
    if left < x0:
        x0 = max(0.0, left - pad)
    return (x0, y0, max(1.0, x1 - x0), max(1.0, y1 - y0))


def unsliced(crop: Box, lines: tuple[Box, ...], must: tuple[Box, ...], size: tuple[int, int]) -> Box:
    """Slide a crop so it does not cut through a text line. The must-boxes stay inside."""
    x, y, w, h = crop
    width, height = size
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
        nx = min(max(0.0, nx), max(0.0, width - w))
        ny = min(max(0.0, ny), max(0.0, height - h))
        options = [(nx, ny, w, h)]
        if ly + lh / 2 < y + h / 2:
            options.append((x, min(max(0.0, ly + lh + 2), max(0.0, height - h)), w, h))
        else:
            options.append((x, min(max(0.0, ly - h - 2), max(0.0, height - h)), w, h))
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
