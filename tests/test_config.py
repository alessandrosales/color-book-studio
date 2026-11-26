from pathlib import Path

import pytest
from pydantic import ValidationError

from color_book_generator.config import BookConfig, load_book_config


def test_page_geometry_defaults() -> None:
    cfg = BookConfig(theme="foxes")
    assert cfg.trim_size_in == (8.5, 11.0)
    w, h = cfg.image_px
    assert w == round(8.75 * 300)
    assert h == round(11.25 * 300)


def test_safe_area_subtracts_margins() -> None:
    cfg = BookConfig(theme="foxes", margin_inner_in=1.0, margin_outer_in=0.5)
    w, h = cfg.safe_area_in
    assert w == pytest.approx(7.0)
    assert h == pytest.approx(10.0)


def test_theme_is_required() -> None:
    with pytest.raises(ValidationError):
        BookConfig(theme="   ")


def test_custom_page_size() -> None:
    cfg = BookConfig(theme="cats", page_size="square_8x8")
    assert cfg.trim_size_in == (8.0, 8.0)
    assert cfg.bleed_size_pt == (8.25 * 72, 8.25 * 72)


def test_load_yaml(tmp_path: Path) -> None:
    path = tmp_path / "book.yaml"
    path.write_text("title: Test\ntheme: dogs\npages: 3\n", encoding="utf-8")
    cfg = load_book_config(path)
    assert cfg.title == "Test"
    assert cfg.pages == 3
