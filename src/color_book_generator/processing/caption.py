"""Draw optional captions / speech balloons onto processed pages."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from color_book_generator.config import CaptionConfig

FontType = ImageFont.FreeTypeFont | ImageFont.ImageFont

_FONT_CANDIDATES: dict[str, list[str]] = {
    "sans": [
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ],
    "serif": [
        "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
        "/System/Library/Fonts/Supplemental/Georgia.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
        "C:/Windows/Fonts/times.ttf",
    ],
    "mono": [
        "/System/Library/Fonts/Menlo.ttc",
        "/System/Library/Fonts/Supplemental/Courier New.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
        "C:/Windows/Fonts/cour.ttf",
    ],
}

_font_cache: dict[tuple[str, int], FontType] = {}


def _font(size: int, family: str = "sans") -> FontType:
    key = (family, size)
    if key in _font_cache:
        return _font_cache[key]
    for candidate in _FONT_CANDIDATES.get(family, _FONT_CANDIDATES["sans"]):
        path = Path(candidate)
        if path.exists():
            try:
                font = ImageFont.truetype(str(path), size=size)
                _font_cache[key] = font
                return font
            except OSError:
                continue
    fallback: FontType = ImageFont.load_default(size=size)
    _font_cache[key] = fallback
    return fallback


def _stroke(size: int, bold: bool) -> int:
    return max(0, round(size * 0.035)) if bold else 0


def _wrap(
    draw: ImageDraw.ImageDraw, text: str, font: FontType, max_width: float
) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        trial = f"{current} {word}".strip()
        if not current or draw.textlength(trial, font=font) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _layout(
    draw: ImageDraw.ImageDraw,
    text: str,
    box_w: float,
    box_h: float,
    initial_size: int,
    family: str,
) -> tuple[FontType, list[str], int]:
    size = max(12, initial_size)
    while size > 12:
        font = _font(size, family)
        lines = _wrap(draw, text, font, box_w)
        line_h = size * 1.25
        if line_h * len(lines) <= box_h:
            return font, lines, size
        size -= 2
    font = _font(size, family)
    return font, _wrap(draw, text, font, box_w), size


def _text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    line: str,
    font: FontType,
    size: int,
    bold: bool,
) -> None:
    draw.text(
        xy,
        line,
        font=font,
        fill=0,
        stroke_width=_stroke(size, bold),
        stroke_fill=0,
    )


def _draw_balloon(
    draw: ImageDraw.ImageDraw,
    font: FontType,
    lines: list[str],
    band: tuple[int, int, int, int],
    size: int,
    position: str,
    bold: bool,
) -> None:
    x0, y0, x1, y1 = band
    pad = size * 0.9
    line_h = round(size * 1.25)
    text_w = max((draw.textlength(line, font=font) for line in lines), default=0)
    text_h = line_h * len(lines)
    bw = min(text_w + pad * 2, x1 - x0)
    bh = min(text_h + pad * 2, y1 - y0)
    bx0 = x0 + ((x1 - x0) - bw) / 2
    by0 = y0 + ((y1 - y0) - bh) / 2
    bx1, by1 = bx0 + bw, by0 + bh
    line_width = max(3, size // 8)
    draw.rounded_rectangle(
        (bx0, by0, bx1, by1), radius=size, outline=0, width=line_width, fill=255
    )

    tail_w = size * 1.2
    tail_h = size * 0.9
    cx = (bx0 + bx1) / 2
    if position == "bottom":
        points = [
            (cx - tail_w, by0 + line_width),
            (cx, by0 - tail_h),
            (cx + tail_w, by0 + line_width),
        ]
    else:
        points = [
            (cx - tail_w, by1 - line_width),
            (cx, by1 + tail_h),
            (cx + tail_w, by1 - line_width),
        ]
    draw.polygon(points, fill=255)
    draw.line([points[0], points[1]], fill=0, width=line_width)
    draw.line([points[1], points[2]], fill=0, width=line_width)

    ty = by0 + (bh - text_h) / 2
    for line in lines:
        width = draw.textlength(line, font=font)
        _text(draw, (x0 + ((x1 - x0) - width) / 2, ty), line, font, size, bold)
        ty += line_h


def _draw_plain(
    draw: ImageDraw.ImageDraw,
    font: FontType,
    lines: list[str],
    band: tuple[int, int, int, int],
    size: int,
    bold: bool,
) -> None:
    x0, y0, x1, y1 = band
    line_h = round(size * 1.3)
    text_h = line_h * len(lines)
    ty = y0 + ((y1 - y0) - text_h) / 2
    for line in lines:
        width = draw.textlength(line, font=font)
        _text(draw, (x0 + ((x1 - x0) - width) / 2, ty), line, font, size, bold)
        ty += line_h


def draw_caption(
    canvas: Image.Image,
    text: str,
    cfg: CaptionConfig,
    band_height: int,
) -> None:
    """Draw the caption into the reserved band (band at top or bottom)."""
    if not text.strip() or band_height <= 0:
        return
    width, height = canvas.size
    band_y0 = 0 if cfg.position == "top" else height - band_height
    band_y1 = band_y0 + band_height
    side_pad = round(width * 0.05)
    inner_pad = round(band_height * 0.12)
    box = (side_pad, band_y0 + inner_pad, width - side_pad, band_y1 - inner_pad)
    draw = ImageDraw.Draw(canvas)
    font, lines, size = _layout(
        draw,
        text.strip(),
        box[2] - box[0],
        box[3] - box[1],
        round(width * cfg.font_size_ratio),
        cfg.font_family,
    )
    if not lines:
        return
    if cfg.style == "balloon":
        _draw_balloon(draw, font, lines, box, size, cfg.position, cfg.bold)
    else:
        _draw_plain(draw, font, lines, box, size, cfg.bold)


def caption_band_height(target_px: tuple[int, int], cfg: CaptionConfig, text: str) -> int:
    if not cfg.enabled or not text.strip():
        return 0
    return round(target_px[1] * cfg.band_ratio)
