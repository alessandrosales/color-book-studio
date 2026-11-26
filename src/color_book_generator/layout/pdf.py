"""Compose print-ready PDF interiors from processed page images."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from PIL import Image
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from color_book_generator.config import BookConfig
from color_book_generator.print_profile import (
    IDENTITY,
    Placement,
    PrintProfile,
    is_recto,
    page_margins,
    place_content,
)


def _page_dimensions_pt(cfg: BookConfig) -> tuple[float, float]:
    if cfg.bleed_in > 0:
        return cfg.bleed_size_pt
    return cfg.page_size_pt


def _draw_logo(c: canvas.Canvas, logo: Path, center_x: float, bottom_y: float, max_h: float) -> float:
    """Draw the publisher logo centred at ``center_x``; return the height it used."""
    reader = ImageReader(str(logo))
    img_w, img_h = reader.getSize()
    if img_w <= 0 or img_h <= 0:
        return 0.0
    height = max_h
    width = height * img_w / img_h
    max_w = 2.2 * inch
    if width > max_w:
        width, height = max_w, max_w * img_h / img_w
    c.drawImage(
        reader, center_x - width / 2, bottom_y, width=width, height=height, mask="auto"
    )
    return height


def _wrap_pdf_lines(
    c: canvas.Canvas, text: str, font_name: str, font_size: float, max_width: float
) -> list[str]:
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    current = ""
    for word in words:
        trial = f"{current} {word}".strip()
        if not current or c.stringWidth(trial, font_name, font_size) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _draw_title_page(
    c: canvas.Canvas, cfg: BookConfig, synopsis: str = "", logo: Path | None = None
) -> None:
    page_w, page_h = _page_dimensions_pt(cfg)
    c.setFillColorRGB(0, 0, 0)
    c.setStrokeColorRGB(0, 0, 0)
    inset = 0.65 * inch
    c.setLineWidth(1.5)
    c.roundRect(inset, inset, page_w - 2 * inset, page_h - 2 * inset, 18, stroke=1, fill=0)

    max_w = page_w - 2 * inset - 0.9 * inch
    font_name = "Helvetica-Bold"
    font_size = 28.0
    lines = _wrap_pdf_lines(c, cfg.title, font_name, font_size, max_w)
    while len(lines) > 3 and font_size > 18.0:
        font_size -= 2.0
        lines = _wrap_pdf_lines(c, cfg.title, font_name, font_size, max_w)

    line_h = font_size * 1.28
    start_y = page_h * 0.60 + ((len(lines) - 1) * line_h) / 2.0
    c.setFont(font_name, font_size)
    for i, line in enumerate(lines):
        c.drawCentredString(page_w / 2, start_y - i * line_h, line)

    text_bottom = start_y - len(lines) * line_h
    if cfg.subtitle:
        sub_y = text_bottom - 10.0
        c.setFont("Helvetica", 14)
        sub_lines = _wrap_pdf_lines(c, cfg.subtitle, "Helvetica", 14, max_w)
        for j, sline in enumerate(sub_lines):
            c.drawCentredString(page_w / 2, sub_y - j * 18.0, sline)
        text_bottom = sub_y - len(sub_lines) * 18.0

    author_y = page_h * (0.19 if logo is not None else 0.14)
    clean_synopsis = synopsis.strip()
    if clean_synopsis:
        syn_max_w = page_w - 2 * inset - 1.2 * inch
        syn_size = 11.0
        syn_lines = _wrap_pdf_lines(c, clean_synopsis, "Helvetica", syn_size, syn_max_w)
        syn_line_h = 14.0
        syn_top = text_bottom - 26.0
        available = syn_top - (author_y + 26.0)
        while syn_lines and len(syn_lines) * syn_line_h > available and syn_size > 9.0:
            syn_size -= 0.5
            syn_line_h = syn_size * 1.3
            syn_lines = _wrap_pdf_lines(c, clean_synopsis, "Helvetica", syn_size, syn_max_w)
        max_lines = max(1, int(available // syn_line_h)) if available > 0 else 0
        if len(syn_lines) > max_lines:
            syn_lines = syn_lines[:max_lines]
            if syn_lines:
                syn_lines[-1] = syn_lines[-1].rstrip(".") + "…"
        c.setFont("Helvetica", syn_size)
        for j, sline in enumerate(syn_lines):
            c.drawCentredString(page_w / 2, syn_top - j * syn_line_h, sline)

    if cfg.author:
        c.setFont("Helvetica", 13)
        c.drawCentredString(page_w / 2, author_y, cfg.author)
    if logo is not None:
        _draw_logo(c, logo, page_w / 2, inset + 0.2 * inch, 0.6 * inch)
    c.showPage()


def _draw_belongs_to_page(c: canvas.Canvas, cfg: BookConfig) -> None:
    page_w, page_h = _page_dimensions_pt(cfg)
    is_pt = cfg.language.lower().startswith("pt")
    heading = "Este livro pertence a" if is_pt else "This book belongs to"
    footer = "Pegue seus lápis ou marcadores e divirta-se!" if is_pt else "Grab your pencils or markers and enjoy!"

    c.setFillColorRGB(0, 0, 0)
    c.setStrokeColorRGB(0, 0, 0)
    box_w = page_w * 0.68
    box_h = page_h * 0.34
    bx = (page_w - box_w) / 2
    by = (page_h - box_h) / 2
    c.setLineWidth(2.2)
    c.roundRect(bx, by, box_w, box_h, 22, stroke=1, fill=0)
    c.setLineWidth(1.0)
    c.roundRect(bx + 10, by + 10, box_w - 20, box_h - 20, 16, stroke=1, fill=0)

    c.setFont("Helvetica-Bold", 20)
    c.drawCentredString(page_w / 2, by + box_h * 0.72, heading)

    c.setLineWidth(1.2)
    c.setDash(3, 3)
    line_margin = box_w * 0.12
    c.line(bx + line_margin, by + box_h * 0.42, bx + box_w - line_margin, by + box_h * 0.42)
    c.setDash()

    c.setFont("Helvetica", 11)
    c.drawCentredString(page_w / 2, by + box_h * 0.2, footer)
    c.showPage()


def _draw_color_test_page(c: canvas.Canvas, cfg: BookConfig) -> None:
    page_w, page_h = _page_dimensions_pt(cfg)
    is_pt = cfg.language.lower().startswith("pt")
    heading = "Página de Teste de Cores" if is_pt else "Color Test Page"
    sub = (
        "Teste seus marcadores e lápis aqui antes de começar a colorir."
        if is_pt
        else "Test your markers and pencils here before coloring."
    )

    c.setFillColorRGB(0, 0, 0)
    c.setStrokeColorRGB(0, 0, 0)
    c.setFont("Helvetica-Bold", 22)
    c.drawCentredString(page_w / 2, page_h - 1.15 * inch, heading)
    c.setFont("Helvetica", 11)
    c.drawCentredString(page_w / 2, page_h - 1.48 * inch, sub)

    cols, rows = 4, 5
    margin_x = 0.9 * inch
    top_y = page_h - 2.0 * inch
    bottom_y = 0.95 * inch
    gap = 0.22 * inch
    cell_w = (page_w - 2 * margin_x - (cols - 1) * gap) / cols
    cell_h = (top_y - bottom_y - (rows - 1) * gap) / rows

    c.setLineWidth(1.4)
    for r in range(rows):
        for col in range(cols):
            x = margin_x + col * (cell_w + gap)
            y = top_y - (r + 1) * cell_h - r * gap
            c.roundRect(x, y, cell_w, cell_h, 10, stroke=1, fill=0)
            c.line(x + 8, y + cell_h * 0.26, x + cell_w - 8, y + cell_h * 0.26)
    c.showPage()


PageKind = Literal["cover", "title", "belongs_to", "color_test", "front", "illustration", "back", "blank"]


@dataclass(frozen=True)
class PageOp:
    """One physical page of the interior, in printing order."""

    kind: PageKind
    image: Path | None = None
    logo: Path | None = None

    @property
    def fits_margins(self) -> bool:
        # The cover is full-bleed artwork; text pages lay themselves out inside the safe area.
        return self.image is not None and self.kind in ("front", "illustration", "back")


@dataclass(frozen=True)
class PageAdjustment:
    page_number: int
    kind: PageKind
    image: Path
    placement: Placement


def plan_interior(
    page_images: list[Path],
    *,
    include_title_page: bool = True,
    blank_verso: bool = False,
    include_belongs_to_page: bool = False,
    include_color_test_page: bool = False,
    cover_image: Path | None = None,
    front_extra_images: list[Path] | None = None,
    back_extra_images: list[Path] | None = None,
    publisher_logo: Path | None = None,
    profile: PrintProfile | None = None,
    rtl: bool = False,
) -> list[PageOp]:
    """Every physical page in order, padded with blanks to the print profile's page multiple."""
    ops: list[PageOp] = []
    if cover_image is not None and cover_image.exists():
        ops.append(PageOp("cover", cover_image))
    if include_title_page:
        ops.append(PageOp("title"))
    if include_belongs_to_page:
        ops.append(PageOp("belongs_to"))
    if include_color_test_page:
        ops.append(PageOp("color_test"))
    ops.extend(PageOp("front", path) for path in front_extra_images or [])

    # With single-sided printing every illustration sits on a right-hand page and
    # its back stays blank, so start the illustrations on a recto.
    if blank_verso and page_images and not is_recto(len(ops) + 1, rtl):
        ops.append(PageOp("blank"))
    for path in page_images:
        ops.append(PageOp("illustration", path))
        if blank_verso:
            ops.append(PageOp("blank"))

    back = back_extra_images or []
    back_ops = [
        PageOp("back", path, publisher_logo if position == len(back) else None)
        for position, path in enumerate(back, start=1)
    ]
    if profile is not None:
        missing = profile.padded_pages(len(ops) + len(back_ops)) - len(ops) - len(back_ops)
        # Padding goes before the closing pages so the book still ends on them.
        ops.extend(PageOp("blank") for _ in range(missing))
    ops.extend(back_ops)
    return ops


def _image_frame_in(cfg: BookConfig, size_px: tuple[int, int]) -> tuple[float, float, float]:
    """Frame (width, height, offset) in inches, relative to the trim, that an image covers.

    Pages are rendered at ``cfg.image_px``: the trim, plus the bleed when there is one.
    """
    trim_w, trim_h = cfg.trim_size_in
    bleed = cfg.bleed_in
    if bleed > 0:
        frame_w, frame_h = trim_w + 2 * bleed, trim_h + 2 * bleed
        if abs(size_px[0] / size_px[1] - frame_w / frame_h) < 0.01:
            return frame_w, frame_h, -bleed
    return trim_w, trim_h, 0.0


def content_bbox_in(image_path: Path, cfg: BookConfig) -> tuple[float, float, float, float] | None:
    """Bounding box of the inked content in trim inches (top-left origin); None for a blank page."""
    with Image.open(image_path) as img:
        gray = img.convert("L")
        size = gray.size
        box = gray.point(lambda v: 255 if v < 200 else 0).getbbox()
    if box is None:
        return None
    frame_w, frame_h, offset = _image_frame_in(cfg, size)
    sx, sy = frame_w / size[0], frame_h / size[1]
    return (
        box[0] * sx + offset,
        box[1] * sy + offset,
        box[2] * sx + offset,
        box[3] * sy + offset,
    )


def interior_adjustments(
    ops: list[PageOp], cfg: BookConfig, profile: PrintProfile, *, rtl: bool = False
) -> list[PageAdjustment]:
    """Pages whose content had to move away from the spine or shrink to clear the margins."""
    margins = page_margins(profile, len(ops), cfg.bleed_in > 0)
    trim_w, trim_h = cfg.trim_size_in
    out: list[PageAdjustment] = []
    for number, op in enumerate(ops, start=1):
        if not op.fits_margins or op.image is None or not op.image.exists():
            continue
        placement = place_content(
            content_bbox_in(op.image, cfg), trim_w, trim_h, margins, recto=is_recto(number, rtl)
        )
        if placement.changed:
            out.append(PageAdjustment(number, op.kind, op.image, placement))
    return out


def describe_adjustment(adj: PageAdjustment) -> str:
    parts: list[str] = []
    if adj.placement.scale < 0.999:
        parts.append(f"reduzida {round((1 - adj.placement.scale) * 100)}%")
    if abs(adj.placement.x) > 0.01 or abs(adj.placement.y) > 0.01:
        parts.append("deslocada para dentro da área segura")
    label = {"illustration": "ilustração", "front": "página inicial", "back": "página final"}.get(
        adj.kind, adj.kind
    )
    return f"Página física {adj.page_number} ({label}): " + " e ".join(parts) + "."


def _draw_image_page(
    c: canvas.Canvas,
    image_path: Path,
    cfg: BookConfig,
    placement: Placement = IDENTITY,
    logo: Path | None = None,
) -> None:
    """Draw a processed page over its frame, moved/scaled by ``placement`` (trim inches)."""
    page_w, _page_h = _page_dimensions_pt(cfg)
    reader = ImageReader(str(image_path))
    frame_w, frame_h, offset = _image_frame_in(cfg, reader.getSize())
    bleed = cfg.bleed_in if cfg.bleed_in > 0 else 0.0
    trim_h = cfg.trim_size_in[1]
    scale = placement.scale
    # Frame's top-left in trim coordinates after the placement transform.
    left_in = placement.x + offset * scale
    top_in = placement.y + offset * scale
    width_in, height_in = frame_w * scale, frame_h * scale
    x_pt = (bleed + left_in) * inch
    y_pt = (bleed + trim_h - top_in - height_in) * inch
    c.drawImage(reader, x_pt, y_pt, width=width_in * inch, height=height_in * inch)
    if logo is not None:
        _draw_logo(c, logo, page_w / 2, 0.75 * inch, 0.55 * inch)
    c.showPage()


def compose_pdf(
    page_images: list[Path],
    out_path: Path,
    cfg: BookConfig,
    *,
    include_title_page: bool = True,
    blank_verso: bool = False,
    include_belongs_to_page: bool | None = None,
    include_color_test_page: bool | None = None,
    cover_image: Path | None = None,
    front_extra_images: list[Path] | None = None,
    back_extra_images: list[Path] | None = None,
    synopsis: str = "",
    publisher_logo: Path | None = None,
    profile: PrintProfile | None = None,
    rtl: bool = False,
) -> Path:
    """Write the interior PDF.

    With a print ``profile`` the page count is padded to its multiple and every drawing is
    kept clear of the mirrored margins (inside margin = gutter for the final page count).
    Without one, pages are drawn edge to edge exactly as rendered.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    page_w, page_h = _page_dimensions_pt(cfg)
    c = canvas.Canvas(
        str(out_path),
        pagesize=(page_w, page_h),
        pageCompression=1,
    )
    c.setTitle(cfg.title)
    if cfg.author:
        c.setAuthor(cfg.author)
    c.setSubject("Coloring book interior")

    ops = plan_interior(
        page_images,
        include_title_page=include_title_page,
        blank_verso=blank_verso,
        include_belongs_to_page=(
            cfg.include_belongs_to_page if include_belongs_to_page is None else include_belongs_to_page
        ),
        include_color_test_page=(
            cfg.include_color_test_page if include_color_test_page is None else include_color_test_page
        ),
        cover_image=cover_image,
        front_extra_images=front_extra_images,
        back_extra_images=back_extra_images,
        publisher_logo=publisher_logo,
        profile=profile,
        rtl=rtl,
    )
    placements: dict[int, Placement] = {}
    if profile is not None:
        placements = {
            adj.page_number: adj.placement
            for adj in interior_adjustments(ops, cfg, profile, rtl=rtl)
        }

    for number, op in enumerate(ops, start=1):
        if op.kind == "title":
            _draw_title_page(c, cfg, synopsis, publisher_logo)
        elif op.kind == "belongs_to":
            _draw_belongs_to_page(c, cfg)
        elif op.kind == "color_test":
            _draw_color_test_page(c, cfg)
        elif op.kind == "blank" or op.image is None:
            c.showPage()
        else:
            _draw_image_page(c, op.image, cfg, placements.get(number, IDENTITY), op.logo)

    c.save()
    return out_path
