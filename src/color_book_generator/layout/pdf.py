"""Compose print-ready PDF interiors from processed page images."""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from color_book_generator.config import BookConfig


def _page_dimensions_pt(cfg: BookConfig) -> tuple[float, float]:
    if cfg.bleed_in > 0:
        return cfg.bleed_size_pt
    return cfg.page_size_pt


def _draw_page_image(c: canvas.Canvas, image_path: Path, cfg: BookConfig) -> None:
    """Draw one processed page so it covers exactly the PDF page (same size on every page)."""
    page_w, page_h = _page_dimensions_pt(cfg)
    c.drawImage(
        ImageReader(str(image_path)),
        0,
        0,
        width=page_w,
        height=page_h,
        preserveAspectRatio=True,
        anchor="c",
    )
    c.showPage()


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


def _draw_title_page(c: canvas.Canvas, cfg: BookConfig) -> None:
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

    if cfg.subtitle:
        sub_y = start_y - len(lines) * line_h - 10.0
        c.setFont("Helvetica", 14)
        for j, sline in enumerate(_wrap_pdf_lines(c, cfg.subtitle, "Helvetica", 14, max_w)):
            c.drawCentredString(page_w / 2, sub_y - j * 18.0, sline)
    if cfg.author:
        c.setFont("Helvetica", 13)
        c.drawCentredString(page_w / 2, page_h * 0.16, cfg.author)
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


def compose_pdf(
    page_images: list[Path],
    out_path: Path,
    cfg: BookConfig,
    *,
    include_title_page: bool = True,
    blank_verso: bool = False,
    include_belongs_to_page: bool | None = None,
    include_color_test_page: bool | None = None,
) -> Path:
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

    belongs_to = (
        cfg.include_belongs_to_page
        if include_belongs_to_page is None
        else include_belongs_to_page
    )
    color_test = (
        cfg.include_color_test_page
        if include_color_test_page is None
        else include_color_test_page
    )

    # Front matter pages follow each other; a blank verso only separates illustration pages,
    # so the PDF never starts with, or ends on, an empty page.
    if include_title_page:
        _draw_title_page(c, cfg)
    if belongs_to:
        _draw_belongs_to_page(c, cfg)
    if color_test:
        _draw_color_test_page(c, cfg)

    for i, img in enumerate(page_images):
        _draw_page_image(c, img, cfg)
        if blank_verso and i < len(page_images) - 1:
            c.showPage()

    c.save()
    return out_path
