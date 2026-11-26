"""Line-weight QA: measuring strokes and small areas, thickening, and the book report."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from color_book_generator.brief import StoryBrief
from color_book_generator.processing.line_qa import (
    MAX_THICKEN_MM,
    auto_thicken,
    caption_rows,
    measure_line_art,
    thicken,
    thicken_px_needed,
)
from color_book_generator.standards import line_standard
from color_book_generator.web import store
from color_book_generator.web.app import app

client = TestClient(app)
DPI = 300
PX_PER_MM = DPI / 25.4


@pytest.fixture(autouse=True)
def _isolated_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store, "WEB_ROOT", tmp_path / "web")


def _drawing(
    width_px: int, tiny_boxes: int = 0, size: tuple[int, int] = (1275, 1650), boxes_y: int = 1300
) -> Image.Image:
    img = Image.new("L", size, 255)
    draw = ImageDraw.Draw(img)
    draw.rectangle((150, 150, size[0] - 150, size[1] - 150), outline=0, width=width_px)
    draw.ellipse((400, 400, 900, 900), outline=0, width=width_px)
    for i in range(tiny_boxes):
        x = 250 + i * 40
        draw.rectangle((x, boxes_y, x + 20, boxes_y + 20), outline=0, width=3)
    return img


@pytest.mark.parametrize("width_px", [6, 12, 18, 24, 36])
def test_stroke_width_is_measured_within_a_tenth_of_a_mm(width_px: int) -> None:
    metrics = measure_line_art(_drawing(width_px), DPI, min_region_mm=5)
    assert metrics.main_line_mm == pytest.approx(width_px / PX_PER_MM, abs=0.1)


def test_small_enclosed_areas_are_counted_and_background_is_not() -> None:
    metrics = measure_line_art(_drawing(12, tiny_boxes=6), DPI, min_region_mm=5)
    assert metrics.tiny_regions == 6


def test_caption_band_is_ignored_for_small_areas() -> None:
    img = _drawing(12, tiny_boxes=6)
    rows = caption_rows(img.height, "bottom")
    assert rows[0] < 1300  # the boxes sit inside the caption band
    assert measure_line_art(img, DPI, min_region_mm=5, exclude_rows=rows).tiny_regions == 0
    # Boxes above the band still count.
    above = _drawing(12, tiny_boxes=6, boxes_y=1000)
    assert measure_line_art(above, DPI, min_region_mm=5, exclude_rows=rows).tiny_regions == 6


def test_blank_page_has_no_stroke_estimate() -> None:
    metrics = measure_line_art(Image.new("L", (600, 800), 255), DPI, min_region_mm=5)
    assert metrics.main_line_mm is None and metrics.tiny_regions == 0


def test_thicken_reaches_target() -> None:
    thin = _drawing(12)  # ~1.0 mm
    add = thicken_px_needed(1.0, 1.5, DPI)
    assert add == 3
    thick = measure_line_art(thicken(thin, add), DPI, min_region_mm=5)
    assert thick.main_line_mm is not None and thick.main_line_mm >= 1.45


def test_thicken_refuses_large_gaps_and_auto_tier_is_smaller() -> None:
    assert thicken_px_needed(0.5, 0.5 + MAX_THICKEN_MM + 0.1, DPI) == 0
    assert thicken_px_needed(1.6, 1.5, DPI) == 0
    # 0.5 mm missing: manual fix yes, silent fix no.
    img, added = auto_thicken(_drawing(12), DPI, 1.5)
    assert added == 0 and img is not None
    img, added = auto_thicken(_drawing(12), DPI, 1.2)
    assert added > 0


def test_line_standard_by_category_and_age() -> None:
    assert line_standard(None, "adult").key == "intricate"
    assert line_standard(None, "manga").key == "comics"
    assert line_standard(StoryBrief(age_band="0-3"), "kids").key == "0-3"
    assert line_standard(StoryBrief(age_band="6-9"), "kids").key == "6-9"
    assert line_standard(None, "kids", "kids_storybook").key == "3-5"


def _book_with_page(img: Image.Image) -> str:
    sid = client.post(
        "/api/sessions",
        json={"pages": 1, "provider": "fake", "model": "fake-1", "preset_id": "kids_storybook"},
    ).json()["id"]
    state = store.load_session(sid)
    assert state is not None
    state.pages[0].status = "done"
    img.save(store.page_file(sid, 1), "PNG", dpi=(DPI, DPI))
    store.save_session(state)
    return sid


def test_lines_report_and_thicken_endpoint() -> None:
    # 1.2 mm strokes on a 3-5 book (target 1.5 mm): thin but fixable.
    sid = _book_with_page(_drawing(14, size=(2550, 3300)))
    report = client.get(f"/api/sessions/{sid}/lines").json()
    assert report["standard"] == "3-5"
    page = report["pages"][0]
    assert page["status"] == "thin" and page["fixable"] is True
    assert any("abaixo de 1.5 mm" in w for w in report["warnings"])

    res = client.post(f"/api/sessions/{sid}/lines/thicken").json()
    assert res["pages_fixed"] == [1]
    fixed = res["report"]["pages"][0]
    assert fixed["status"] == "ok" and fixed["fixed_px"] > 0
    assert store.load_session(sid).pages[0].line_main_mm >= 1.45  # type: ignore[union-attr]

    preflight = client.get(f"/api/sessions/{sid}/kdp-preflight").json()
    assert not any("abaixo de 1.5 mm" in w for w in preflight["warnings"])


def test_thicken_unknown_page_is_404() -> None:
    sid = _book_with_page(_drawing(14, size=(2550, 3300)))
    assert client.post(f"/api/sessions/{sid}/lines/thicken?page=9").status_code == 404
