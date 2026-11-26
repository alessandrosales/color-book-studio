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


def render_text_page(
    target_px: tuple[int, int],
    title: str,
    text: str,
    cfg: CaptionConfig,
    *,
    margin_ratio: float = 0.1,
    background: Image.Image | None = None,
    align: str = "center",
    vertical: str = "center",
    show_border: bool = False,
    illustration_position: str = "background",
    illustration_size: str = "small",
) -> Image.Image:
    """Render a text page, optionally with a generated illustration/background."""
    from color_book_generator.processing.line_art import crop_content

    width, height = target_px
    if background is not None and illustration_position == "background":
        canvas = background.convert("L")
        if canvas.size != target_px:
            canvas = canvas.resize(target_px, Image.Resampling.LANCZOS)
    else:
        canvas = Image.new("L", target_px, color=255)
    draw = ImageDraw.Draw(canvas)

    margin_x = round(width * margin_ratio)
    margin_y = round(height * margin_ratio)
    area_w = max(1, width - 2 * margin_x)
    pad_x = round(width * 0.03)
    pad_y = round(height * 0.025)
    text_max_w = max(1, area_w - 2 * pad_x)

    clean_title = title.strip()
    body = text.strip()
    title_size = max(10, round(width * cfg.font_size_ratio * 2.0))
    title_font = _font(title_size, cfg.font_family)
    title_lines = _wrap(draw, clean_title, title_font, text_max_w) if clean_title else []
    title_line_h = round(title_size * 1.2)

    body_size = max(10, round(width * cfg.font_size_ratio))
    body_font = _font(body_size, cfg.font_family)
    body_lines = _wrap(draw, body, body_font, text_max_w) if body else []
    body_line_h = round(body_size * 1.35)
    gap = round(title_size * 0.5) if title_lines and body_lines else 0

    def max_line_w() -> float:
        widths = [draw.textlength(line, font=title_font) for line in title_lines]
        widths += [draw.textlength(line, font=body_font) for line in body_lines]
        return max(widths) if widths else 0.0

    def block_height() -> int:
        return (
            pad_y * 2
            + len(title_lines) * title_line_h
            + gap
            + len(body_lines) * body_line_h
        )

    art: Image.Image | None = None
    art_h = 0
    art_gap = round(height * 0.025)
    if background is not None and illustration_position in {"above", "below"}:
        art = crop_content(background)
        size_frac = {"small": 0.24, "medium": 0.32, "large": 0.42}.get(
            illustration_size, 0.24
        )
        width_frac = {"small": 0.5, "medium": 0.65, "large": 0.82}.get(
            illustration_size, 0.5
        )
        region_h = round(height * size_frac)
        region_w = round(area_w * width_frac)
        scale = min(region_w / art.width, region_h / art.height)
        art = art.resize(
            (max(1, round(art.width * scale)), max(1, round(art.height * scale))),
            Image.Resampling.LANCZOS,
        )
        art_h = art.height + art_gap

    available_text_h = height - 2 * margin_y - art_h
    while block_height() > available_text_h * 0.95 and body_size > 10:
        body_size -= 1
        body_font = _font(body_size, cfg.font_family)
        body_lines = _wrap(draw, body, body_font, text_max_w) if body else []
        body_line_h = round(body_size * 1.35)

    block_h = block_height()
    block_w = min(area_w, round(max_line_w()) + 2 * pad_x)
    block_x = margin_x + (area_w - block_w) // 2

    if art is not None and illustration_position in {"above", "below"}:
        group_h = art.height + art_gap + block_h
        if vertical == "top":
            group_top = margin_y
        elif vertical == "bottom":
            group_top = height - margin_y - group_h
        else:
            group_top = (height - group_h) // 2
        if illustration_position == "above":
            art_y = group_top
            block_y = group_top + art.height + art_gap
        else:
            block_y = group_top
            art_y = group_top + block_h + art_gap
        canvas.paste(art, ((width - art.width) // 2, art_y))
    else:
        if vertical == "top":
            block_y = margin_y
        elif vertical == "bottom":
            block_y = height - margin_y - block_h
        else:
            block_y = margin_y + (height - 2 * margin_y - block_h) // 2

    has_visual = background is not None and illustration_position != "none"
    if has_visual or show_border:
        draw.rounded_rectangle(
            (block_x, block_y, block_x + block_w, block_y + block_h),
            radius=round(width * 0.018),
            fill=255,
            outline=0 if show_border else None,
            width=max(2, round(width * 0.002)) if show_border else 1,
        )

    centered = align != "left"
    y = block_y + pad_y
    for line in title_lines:
        line_w = draw.textlength(line, font=title_font)
        x = block_x + ((block_w - line_w) / 2 if centered else pad_x)
        _text(draw, (x, y), line, title_font, title_size, True)
        y += title_line_h
    y += gap
    for line in body_lines:
        line_w = draw.textlength(line, font=body_font)
        x = block_x + ((block_w - line_w) / 2 if centered else pad_x)
        _text(draw, (x, y), line, body_font, body_size, cfg.bold)
        y += body_line_h
    return canvas


def draw_cover_title(
    canvas: Image.Image, title: str, cfg: CaptionConfig
) -> Image.Image:
    """Draw the book title on a white band at the bottom of the cover."""
    clean = title.strip()
    if not clean:
        return canvas
    width, height = canvas.size
    band_h = round(height * 0.15)
    band_y0 = height - band_h
    draw = ImageDraw.Draw(canvas)
    fill = 255 if canvas.mode == "L" else (255, 255, 255)
    draw.rectangle((0, band_y0, width, height), fill=fill)
    draw.line((0, band_y0, width, band_y0), fill=0, width=max(2, round(width * 0.003)))

    size = max(18, round(width * cfg.font_size_ratio * 1.9))
    max_w = round(width * 0.86)
    font = _font(size, cfg.font_family)
    lines = _wrap(draw, clean, font, max_w)
    line_h = round(size * 1.16)
    while lines and line_h * len(lines) > band_h * 0.78 and size > 12:
        size -= 2
        font = _font(size, cfg.font_family)
        lines = _wrap(draw, clean, font, max_w)
        line_h = round(size * 1.16)
    y = band_y0 + (band_h - line_h * len(lines)) / 2
    for line in lines:
        line_w = draw.textlength(line, font=font)
        _text(draw, ((width - line_w) / 2, y), line, font, size, True)
        y += line_h
    return canvas
