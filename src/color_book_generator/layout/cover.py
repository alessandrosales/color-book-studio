"""Full wrap cover: back cover, spine and front cover on one sheet with bleed.

Layout follows KDP's cover rules: one PDF whose width is bleed + back + spine + front +
bleed and whose height is bleed + trim height + bleed; text kept 0.125" inside the trim;
spine text only from a minimum page count, with 0.0625" clear on each side of the spine;
a clear 2" x 1.2" area at the bottom right of the back cover for the barcode.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageStat
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

SAFE_IN = 0.125
SPINE_TEXT_CLEARANCE_IN = 0.0625
BARCODE_W_IN = 2.0
BARCODE_H_IN = 1.2
BARCODE_OFFSET_IN = 0.25
# Below this the spine lettering is unreadable, so the spine stays blank.
MIN_SPINE_PT = 6.0


@dataclass(frozen=True)
class CoverGeometry:
    trim_w: float
    trim_h: float
    spine_w: float
    bleed: float
    # Right-to-left books (manga) open the other way: front panel on the left.
    rtl: bool = False

    @property
    def total_w(self) -> float:
        return 2 * self.bleed + 2 * self.trim_w + self.spine_w

    @property
    def total_h(self) -> float:
        return 2 * self.bleed + self.trim_h

    @property
    def back_x(self) -> float:
        """Left edge of the back cover's trim area."""
        return self.bleed + (self.trim_w + self.spine_w if self.rtl else 0.0)

    @property
    def spine_x(self) -> float:
        return self.bleed + self.trim_w

    @property
    def front_x(self) -> float:
        """Left edge of the front cover's trim area."""
        return self.bleed + (0.0 if self.rtl else self.trim_w + self.spine_w)

    def barcode_box(self) -> tuple[float, float, float, float]:
        """(x, y, w, h) in inches from the bottom-left of the sheet (back panel, bottom right)."""
        x = self.back_x + self.trim_w - BARCODE_OFFSET_IN - BARCODE_W_IN
        y = self.bleed + BARCODE_OFFSET_IN
        return (x, y, BARCODE_W_IN, BARCODE_H_IN)

    def spine_text_room(self) -> float:
        """Width available for spine lettering (0 when the spine is too thin)."""
        return max(0.0, self.spine_w - 2 * SPINE_TEXT_CLEARANCE_IN)


def cover_geometry(
    trim_in: tuple[float, float], spine_in: float, bleed_in: float = 0.125, *, rtl: bool = False
) -> CoverGeometry:
    return CoverGeometry(
        trim_w=trim_in[0], trim_h=trim_in[1], spine_w=spine_in, bleed=bleed_in, rtl=rtl
    )


def _edge_color(image: Image.Image) -> tuple[float, float, float]:
    """Average colour of the cover's left edge, so the back and spine continue its tone."""
    rgb = image.convert("RGB")
    strip = rgb.crop((0, 0, max(1, rgb.width // 20), rgb.height))
    r, g, b = ImageStat.Stat(strip).mean[:3]
    return (r / 255, g / 255, b / 255)


def _text_color(bg: tuple[float, float, float]) -> tuple[float, float, float]:
    r, g, b = bg
    luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return (0.08, 0.08, 0.1) if luminance > 0.55 else (1.0, 1.0, 1.0)


def _wrap(c: canvas.Canvas, text: str, font: str, size: float, max_w: float) -> list[str]:
    lines: list[str] = []
    for paragraph in text.splitlines() or [text]:
        current = ""
        for word in paragraph.split():
            trial = f"{current} {word}".strip()
            if not current or c.stringWidth(trial, font, size) <= max_w:
                current = trial
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


def _draw_front(c: canvas.Canvas, cover: Path, geo: CoverGeometry) -> Image.Image:
    """Cover-fit the front artwork over the front panel and its outer bleed."""
    with Image.open(cover) as raw:
        img = raw.convert("RGB")
    panel_w = geo.trim_w + geo.bleed
    panel_h = geo.trim_h + 2 * geo.bleed
    scale = max(panel_w / img.width, panel_h / img.height)
    draw_w, draw_h = img.width * scale, img.height * scale
    # Centred on the front panel (trim + outer bleed); overflow is cropped evenly.
    panel_x = geo.front_x - geo.bleed if geo.rtl else geo.front_x
    x = panel_x + (panel_w - draw_w) / 2
    y = (geo.total_h - draw_h) / 2
    c.saveState()
    path = c.beginPath()
    path.rect(panel_x * inch, 0, panel_w * inch, geo.total_h * inch)
    c.clipPath(path, stroke=0, fill=0)
    c.drawImage(ImageReader(img), x * inch, y * inch, width=draw_w * inch, height=draw_h * inch)
    c.restoreState()
    return img


def _draw_back(
    c: canvas.Canvas,
    geo: CoverGeometry,
    ink: tuple[float, float, float],
    *,
    title: str,
    author: str,
    synopsis: str,
    logo: Path | None,
    barcode_area: bool,
) -> None:
    margin = SAFE_IN + 0.375
    left = (geo.back_x + margin) * inch
    width = (geo.trim_w - 2 * margin) * inch
    top = (geo.bleed + geo.trim_h - margin) * inch
    c.setFillColorRGB(*ink)
    y = top
    if title.strip():
        size: float = 20.0
        for line in _wrap(c, title.strip(), "Helvetica-Bold", size, width):
            y -= size * 1.2
            c.setFont("Helvetica-Bold", size)
            c.drawString(left, y, line)
        y -= 14
    if synopsis.strip():
        size = 12.5
        c.setFont("Helvetica", size)
        for line in _wrap(c, synopsis.strip(), "Helvetica", size, width):
            y -= size * 1.45
            c.drawString(left, y, line)
    if author.strip():
        y -= 22
        c.setFont("Helvetica-Oblique", 12)
        c.drawString(left, y, author.strip())

    if logo is not None and logo.exists():
        reader = ImageReader(str(logo))
        lw, lh = reader.getSize()
        if lw > 0 and lh > 0:
            h = 0.6 * inch
            w = min(1.6 * inch, h * lw / lh)
            h = w * lh / lw
            c.drawImage(reader, left, (geo.bleed + margin) * inch, width=w, height=h, mask="auto")

    if barcode_area:
        bx, by, bw, bh = geo.barcode_box()
        c.setFillColorRGB(1, 1, 1)
        c.rect(bx * inch, by * inch, bw * inch, bh * inch, stroke=0, fill=1)


def _draw_spine(
    c: canvas.Canvas,
    geo: CoverGeometry,
    ink: tuple[float, float, float],
    *,
    title: str,
    author: str,
) -> None:
    room = geo.spine_text_room()
    size = min(18.0, room * 72 * 0.62)
    if size < MIN_SPINE_PT or not title.strip():
        return
    text = f"{title.strip()}   {author.strip()}".strip() if author.strip() else title.strip()
    max_len = (geo.trim_h - 2 * SAFE_IN - 0.5) * inch
    c.saveState()
    # Reads top to bottom (English-language convention, also used by KDP).
    c.translate((geo.spine_x + geo.spine_w / 2) * inch, (geo.bleed + geo.trim_h / 2) * inch)
    c.rotate(-90)
    c.setFillColorRGB(*ink)
    font = "Helvetica-Bold"
    while size > 5 and c.stringWidth(text, font, size) > max_len:
        size -= 0.5
    c.setFont(font, size)
    c.drawCentredString(0, -size * 0.35, text)
    c.restoreState()


def compose_cover_pdf(
    out_path: Path,
    cover_image: Path,
    geo: CoverGeometry,
    *,
    title: str,
    author: str = "",
    synopsis: str = "",
    publisher_logo: Path | None = None,
    spine_text: bool = True,
    barcode_area: bool = True,
) -> Path:
    """Write the wrap cover PDF (one page: back, spine, front, all with bleed)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(out_path), pagesize=(geo.total_w * inch, geo.total_h * inch), pageCompression=1)
    c.setTitle(title or "Cover")
    if author:
        c.setAuthor(author)
    c.setSubject("Book cover (wrap)")

    with Image.open(cover_image) as probe:
        bg = _edge_color(probe)
    ink = _text_color(bg)
    c.setFillColorRGB(*bg)
    c.rect(0, 0, geo.total_w * inch, geo.total_h * inch, stroke=0, fill=1)
    _draw_front(c, cover_image, geo)
    _draw_back(
        c,
        geo,
        ink,
        title=title,
        author=author,
        synopsis=synopsis,
        logo=publisher_logo,
        barcode_area=barcode_area,
    )
    if spine_text:
        _draw_spine(c, geo, ink, title=title, author=author)

    b = geo.bleed * inch
    c.setTrimBox((b, b, (geo.total_w - geo.bleed) * inch, (geo.total_h - geo.bleed) * inch))
    c.setBleedBox((0, 0, geo.total_w * inch, geo.total_h * inch))
    c.showPage()
    c.save()
    return out_path
