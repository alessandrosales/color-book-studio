from pathlib import Path

from PIL import Image
from pypdf import PdfReader

from color_book_generator.config import BookConfig
from color_book_generator.layout.pdf import compose_pdf


def _page(path: Path) -> Path:
    Image.new("L", (200, 260), 255).save(path)
    return path


def test_compose_pdf_page_count(tmp_path: Path) -> None:
    pages = [_page(tmp_path / f"page-{i:03d}.png") for i in (1, 2, 3)]
    cfg = BookConfig(theme="x", include_title_page=True)
    out = compose_pdf(pages, tmp_path / "book.pdf", cfg)
    reader = PdfReader(str(out))
    assert len(reader.pages) == 4
    assert reader.metadata.title == cfg.title


def test_compose_pdf_without_title_and_blank_verso(tmp_path: Path) -> None:
    pages = [_page(tmp_path / f"page-{i:03d}.png") for i in (1, 2)]
    cfg = BookConfig(theme="x")
    out = compose_pdf(
        pages, tmp_path / "book.pdf", cfg, include_title_page=False, blank_verso=True
    )
    assert len(PdfReader(str(out)).pages) == 3
