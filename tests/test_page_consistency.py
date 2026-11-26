"""Consistent page size: matching orientation on generation and clean PDF pages."""

from __future__ import annotations

from pathlib import Path

from PIL import Image
from pypdf import PdfReader

from color_book_generator.config import BookConfig
from color_book_generator.layout.pdf import compose_pdf
from color_book_generator.providers.base import (
    ImageProvider,
    aspect_mismatch,
    generate_matching,
    orientation_hint,
)


class _SequenceProvider(ImageProvider):
    name = "sequence"

    def __init__(self, sizes: list[tuple[int, int]]) -> None:
        super().__init__("seq")
        self.sizes = sizes
        self.prompts: list[str] = []

    def generate(  # type: ignore[no-untyped-def,override]
        self, prompt, *, target_size=(1024, 1024), **_kwargs
    ) -> Image.Image:
        self.prompts.append(prompt)
        size = self.sizes[min(len(self.prompts) - 1, len(self.sizes) - 1)]
        return Image.new("RGB", size, "white")


def test_orientation_hint_names_the_page_shape() -> None:
    assert "PORTRAIT" in orientation_hint((1800, 2700))
    assert "LANDSCAPE" in orientation_hint((2700, 1800))
    assert "SQUARE" in orientation_hint((2550, 2550))
    assert orientation_hint((1800, 2700), strong=True).startswith("IMPORTANT")


def test_aspect_mismatch_flags_landscape_on_portrait_page() -> None:
    page = (1800, 2700)
    assert aspect_mismatch(Image.new("RGB", (1536, 1024)), page)
    assert not aspect_mismatch(Image.new("RGB", (1086, 1448)), page)
    assert not aspect_mismatch(Image.new("RGB", (1024, 1536)), page)


def test_generate_matching_retries_until_orientation_fits() -> None:
    provider = _SequenceProvider([(1536, 1024), (1024, 1536)])
    img = generate_matching(provider, "a fox", target_size=(1800, 2700))
    assert img.size == (1024, 1536)
    assert len(provider.prompts) == 2
    assert "IMPORTANT" not in provider.prompts[0]
    assert "IMPORTANT" in provider.prompts[1]


def test_generate_matching_gives_up_after_attempts() -> None:
    provider = _SequenceProvider([(1536, 1024)])
    img = generate_matching(provider, "a fox", target_size=(1800, 2700), attempts=2)
    assert img.size == (1536, 1024)
    assert len(provider.prompts) == 2


def _page(path: Path, size: tuple[int, int] = (1800, 2700)) -> Path:
    Image.new("L", size, 255).save(path)
    return path


def test_pdf_pages_have_the_chosen_format_and_no_blank_page(tmp_path: Path) -> None:
    pages = [_page(tmp_path / f"page-{i:03d}.png") for i in (1, 2, 3)]
    cfg = BookConfig(theme="x", page_size="6x9", bleed_in=0.0)
    out = compose_pdf(pages, tmp_path / "book.pdf", cfg, include_title_page=True)
    reader = PdfReader(str(out))
    assert len(reader.pages) == 4
    for pdf_page in reader.pages:
        assert round(float(pdf_page.mediabox.width)) == 6 * 72
        assert round(float(pdf_page.mediabox.height)) == 9 * 72
    assert all(len(p.images) == 1 for p in list(reader.pages)[1:])


def test_blank_verso_only_between_illustrations(tmp_path: Path) -> None:
    pages = [_page(tmp_path / f"page-{i:03d}.png") for i in (1, 2, 3)]
    cfg = BookConfig(theme="x", page_size="6x9", bleed_in=0.0)
    out = compose_pdf(
        pages, tmp_path / "book.pdf", cfg, include_title_page=True, blank_verso=True
    )
    reader = PdfReader(str(out))
    # title + page, blank, page, blank, page
    assert len(reader.pages) == 6
    assert len(reader.pages[-1].images) == 1
