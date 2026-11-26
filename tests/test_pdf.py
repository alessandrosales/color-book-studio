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
    assert len(PdfReader(str(out)).pages) == 4


def test_last_illustration_has_blank_verso_before_back_matter(tmp_path: Path) -> None:
    page = _page(tmp_path / "page.png")
    back = _page(tmp_path / "back.png")
    out = compose_pdf(
        [page], tmp_path / "book.pdf", BookConfig(theme="x"),
        include_title_page=False, blank_verso=True, back_extra_images=[back],
    )
    reader = PdfReader(str(out))
    assert len(reader.pages) == 3
    assert not reader.pages[1].get_contents()


def test_compose_pdf_cover_and_extra_pages_order(tmp_path: Path) -> None:
    pages = [_page(tmp_path / f"page-{i:03d}.png") for i in (1, 2)]
    cover = _page(tmp_path / "cover.png")
    front = _page(tmp_path / "front.png")
    back = _page(tmp_path / "back.png")
    cfg = BookConfig(theme="x", include_title_page=False)
    out = compose_pdf(
        pages,
        tmp_path / "book.pdf",
        cfg,
        include_title_page=False,
        cover_image=cover,
        front_extra_images=[front],
        back_extra_images=[back],
    )
    assert len(PdfReader(str(out)).pages) == 5


def test_title_page_prints_author_and_synopsis(tmp_path: Path) -> None:
    pages = [_page(tmp_path / "page-001.png")]
    cfg = BookConfig(theme="x", title="Lumi's Journey", author="Jane Doe")
    out = compose_pdf(
        pages,
        tmp_path / "book.pdf",
        cfg,
        include_title_page=True,
        synopsis="A short story about courage and belonging.",
    )
    text = PdfReader(str(out)).pages[0].extract_text()
    assert "Lumi's Journey" in text
    assert "Jane Doe" in text
    assert "courage and belonging" in text
