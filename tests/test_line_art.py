import numpy as np
from PIL import Image, ImageDraw

from color_book_generator.config import CaptionConfig, ProcessingConfig
from color_book_generator.processing.line_art import prepare_page, to_line_art


def _sample() -> Image.Image:
    img = Image.new("RGB", (600, 600), "white")
    draw = ImageDraw.Draw(img)
    draw.ellipse((150, 150, 450, 450), outline="black", width=8)
    draw.line((300, 450, 300, 520), fill="black", width=8)
    return img


def test_to_line_art_is_binary() -> None:
    out = to_line_art(_sample(), ProcessingConfig())
    arr = np.asarray(out)
    assert out.mode == "L"
    assert set(np.unique(arr)).issubset({0, 255})


def test_dark_image_is_inverted() -> None:
    dark = Image.new("RGB", (400, 400), "black")
    ImageDraw.Draw(dark).ellipse((100, 100, 300, 300), fill="white")
    out = to_line_art(dark, ProcessingConfig())
    assert np.asarray(out).mean() > 127


def test_prepare_page_matches_target_size_and_pads() -> None:
    out = prepare_page(_sample(), (1000, 1400), ProcessingConfig())
    assert out.size == (1000, 1400)
    arr = np.asarray(out)
    assert arr[0, 0] == 255
    assert arr[-1, -1] == 255


def test_min_component_removes_specks() -> None:
    img = Image.new("RGB", (400, 400), "white")
    draw = ImageDraw.Draw(img)
    draw.point((200, 200), fill="black")
    draw.rectangle((100, 100, 300, 300), outline="black", width=6)
    cfg = ProcessingConfig(threshold="fixed", fixed_threshold=200, min_component_px=50)
    out = np.asarray(to_line_art(img, cfg))
    assert out[200, 200] == 255


def test_caption_band_reserves_space_and_draws_text() -> None:
    caption = CaptionConfig(enabled=True, style="balloon", position="bottom", band_ratio=0.2)
    out = prepare_page(
        _sample(), (1000, 1400), ProcessingConfig(), caption, "Where is my family?"
    )
    assert out.size == (1000, 1400)
    arr = np.asarray(out)
    band = arr[1120:, :]
    assert (band < 128).any(), "caption text should draw black pixels"
    assert arr[:1120, :].min() == 0, "artwork still present above the band"


def test_caption_top_position() -> None:
    caption = CaptionConfig(enabled=True, style="caption", position="top", band_ratio=0.15)
    out = prepare_page(_sample(), (800, 1100), ProcessingConfig(), caption, "Hello there")
    arr = np.asarray(out)
    assert (arr[:165, :] < 128).any()
    assert out.size == (800, 1100)


def test_caption_disabled_or_empty_keeps_full_page() -> None:
    full = prepare_page(_sample(), (600, 800), ProcessingConfig())
    disabled = prepare_page(
        _sample(), (600, 800), ProcessingConfig(), CaptionConfig(enabled=False), "text"
    )
    empty = prepare_page(
        _sample(), (600, 800), ProcessingConfig(), CaptionConfig(enabled=True), "   "
    )
    assert np.array_equal(np.asarray(full), np.asarray(disabled))
    assert np.array_equal(np.asarray(full), np.asarray(empty))
