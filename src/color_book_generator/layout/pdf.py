"""Compose print-ready PDF interiors from processed page images."""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from color_book_generator.config import BookConfig


def _draw_image_full_bleed(c: canvas.Canvas, image_path: Path, cfg: BookConfig) -> None:
    """Place an image covering the whole trim page (white margins are in the image)."""
    page_w, page_h = cfg.trim_size_in
    c.drawImage(
        ImageReader(str(image_path)),
        0,
        0,
        width=page_w * inch,
        height=page_h * inch,
        preserveAspectRatio=False,
        anchor="c",
    )
    c.showPage()


def _draw_title_page(c: canvas.Canvas, cfg: BookConfig) -> None:
    page_w = cfg.trim_size_in[0] * inch
    page_h = cfg.trim_size_in[1] * inch
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica-Bold", 30)
    c.drawCentredString(page_w / 2, page_h * 0.62, cfg.title)
    if cfg.subtitle:
        c.setFont("Helvetica", 16)
        c.drawCentredString(page_w / 2, page_h * 0.55, cfg.subtitle)
    if cfg.author:
        c.setFont("Helvetica", 14)
        c.drawCentredString(page_w / 2, page_h * 0.16, cfg.author)
    c.showPage()


def compose_pdf(
    page_images: list[Path],
    out_path: Path,
    cfg: BookConfig,
    *,
    include_title_page: bool = True,
    blank_verso: bool = False,
) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    page_w, page_h = cfg.trim_size_in
    c = canvas.Canvas(
        str(out_path),
        pagesize=(page_w * inch, page_h * inch),
        pageCompression=1,
    )
    c.setTitle(cfg.title)
    if cfg.author:
        c.setAuthor(cfg.author)
    c.setSubject("Coloring book interior")

    if include_title_page:
        _draw_title_page(c, cfg)

    for i, img in enumerate(page_images):
        _draw_image_full_bleed(c, img, cfg)
        if blank_verso and i < len(page_images) - 1:
            c.showPage()

    c.save()
    return out_path
