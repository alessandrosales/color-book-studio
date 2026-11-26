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


def _draw_title_page(c: canvas.Canvas, cfg: BookConfig, synopsis: str = "") -> None:
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

    author_y = page_h * 0.14
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
    cover_image: Path | None = None,
    front_extra_images: list[Path] | None = None,
    back_extra_images: list[Path] | None = None,
    synopsis: str = "",
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

    # Front matter precedes the illustrations. Every illustration gets its own
    # blank reverse when single-sided printing is requested.
    if cover_image is not None and cover_image.exists():
        _draw_page_image(c, cover_image, cfg)
    if include_title_page:
        _draw_title_page(c, cfg, synopsis)
    if belongs_to:
        _draw_belongs_to_page(c, cfg)
    if color_test:
        _draw_color_test_page(c, cfg)

    for extra in front_extra_images or []:
        _draw_page_image(c, extra, cfg)

    # In a left-to-right book, odd pages are right-hand pages. Start the
    # illustrations on a right-hand page so each following blank is its verso.
    if blank_verso and page_images and c.getPageNumber() % 2 == 0:
        c.showPage()

    for img in page_images:
        _draw_page_image(c, img, cfg)
        if blank_verso:
            c.showPage()

    for extra in back_extra_images or []:
        _draw_page_image(c, extra, cfg)

    c.save()
    return out_path
