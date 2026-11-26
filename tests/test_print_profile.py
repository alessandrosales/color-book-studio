"""Print profiles: gutter by page count, mirrored margins, padding and new trim sizes."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from pypdf import PdfReader

from color_book_generator.config import PAGE_SIZES_IN, BookConfig
from color_book_generator.layout.pdf import (
    compose_pdf,
    content_bbox_in,
    interior_adjustments,
    plan_interior,
)
from color_book_generator.print_profile import (
    PRINT_PROFILES,
    Margins,
    get_profile,
    is_recto,
    page_margins,
    place_content,
)
from color_book_generator.web import store
from color_book_generator.web.app import app

client = TestClient(app)
KDP = PRINT_PROFILES["kdp_paperback"]


@pytest.fixture(autouse=True)
def _isolated_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store, "WEB_ROOT", tmp_path / "web")


def test_kdp_gutter_grows_with_page_count() -> None:
    assert KDP.gutter_in(24) == 0.375
    assert KDP.gutter_in(150) == 0.375
    assert KDP.gutter_in(151) == 0.5
    assert KDP.gutter_in(828) == 0.875
    assert KDP.padded_pages(25) == 26
    assert PRINT_PROFILES["grafica_canoa"].padded_pages(9) == 12
    assert get_profile("nao-existe").id == "kdp_paperback"


def test_recto_flips_for_right_to_left_books() -> None:
    assert is_recto(1) and not is_recto(2)
    assert not is_recto(1, rtl=True) and is_recto(2, rtl=True)


MARGINS = Margins(inside=0.5, outside=0.25, top=0.25, bottom=0.25)


def test_content_inside_live_area_is_untouched() -> None:
    placement = place_content((1.0, 1.0, 7.0, 10.0), 8.5, 11.0, MARGINS, recto=True)
    assert not placement.changed


def test_content_on_the_spine_moves_away_from_it() -> None:
    # Recto: spine on the left; content starting at 0.1" must move right to 0.5".
    recto = place_content((0.1, 1.0, 6.0, 10.0), 8.5, 11.0, MARGINS, recto=True)
    assert recto.scale == 1.0 and recto.x == pytest.approx(0.4)
    # Verso: spine on the right; content ending at 8.4" must move left to 8.0".
    verso = place_content((2.5, 1.0, 8.4, 10.0), 8.5, 11.0, MARGINS, recto=False)
    assert verso.scale == 1.0 and verso.x == pytest.approx(-0.4)


def test_content_wider_than_live_area_shrinks() -> None:
    placement = place_content((0.0, 0.0, 8.5, 11.0), 8.5, 11.0, MARGINS, recto=True)
    assert placement.scale == pytest.approx((8.5 - 0.75) / 8.5)
    # After the transform the content starts exactly at the inside margin.
    assert placement.x + 0.0 * placement.scale == pytest.approx(0.5)


def test_blank_page_needs_no_placement() -> None:
    assert not place_content(None, 8.5, 11.0, MARGINS, recto=True).changed


def _page(path: Path, size: tuple[int, int], box: tuple[int, int, int, int] | None) -> Path:
    img = Image.new("L", size, 255)
    if box:
        ImageDraw.Draw(img).rectangle(box, outline=0, width=8)
    img.save(path)
    return path


def test_plan_pads_before_closing_pages_and_starts_illustrations_on_recto(tmp_path: Path) -> None:
    pages = [_page(tmp_path / f"p{i}.png", (60, 90), None) for i in range(3)]
    closing = _page(tmp_path / "end.png", (60, 90), None)
    ops = plan_interior(
        pages,
        include_title_page=True,
        include_belongs_to_page=True,
        blank_verso=True,
        back_extra_images=[closing],
        profile=PRINT_PROFILES["grafica_canoa"],
    )
    kinds = [op.kind for op in ops]
    assert len(ops) % 4 == 0
    assert kinds[-1] == "back"
    first_art = kinds.index("illustration") + 1
    assert is_recto(first_art)


def test_compose_with_profile_moves_art_off_the_spine(tmp_path: Path) -> None:
    cfg = BookConfig(theme="x", page_size="6x9", bleed_in=0.0, dpi=150)
    # Ink touching the left edge: on a recto that is the spine side.
    art = _page(tmp_path / "art.png", (900, 1350), (0, 150, 600, 1200))
    bbox = content_bbox_in(art, cfg)
    assert bbox is not None and bbox[0] == pytest.approx(0.0, abs=0.02)

    ops = plan_interior([art] * 3, include_title_page=False, profile=KDP)
    assert len(ops) == 4  # padded to an even count; the 24-page minimum is a preflight error
    adjustments = interior_adjustments(ops, cfg, KDP)
    rectos = [a for a in adjustments if is_recto(a.page_number)]
    assert rectos and all(a.placement.x > 0 for a in rectos)

    out = compose_pdf([art] * 3, tmp_path / "book.pdf", cfg, include_title_page=False, profile=KDP)
    assert len(PdfReader(str(out)).pages) == len(ops)


def test_compose_without_profile_keeps_legacy_page_count(tmp_path: Path) -> None:
    cfg = BookConfig(theme="x", page_size="6x9", bleed_in=0.0, dpi=150)
    art = _page(tmp_path / "art.png", (900, 1350), (150, 150, 750, 1200))
    out = compose_pdf([art] * 3, tmp_path / "book.pdf", cfg, include_title_page=False)
    assert len(PdfReader(str(out)).pages) == 3


def test_page_margins_use_gutter_for_final_count() -> None:
    margins = page_margins(KDP, 200, has_bleed=False)
    assert margins.inside == 0.5 and margins.outside == 0.25


def test_new_trim_sizes() -> None:
    assert PAGE_SIZES_IN["a5"] == pytest.approx((5.827, 8.268), abs=0.001)
    assert PAGE_SIZES_IN["formatinho_13.5x19"][0] == pytest.approx(5.315, abs=0.001)
    assert "americano_17x26" in PAGE_SIZES_IN


def test_session_print_profile_patch_and_preflight() -> None:
    sid = client.post(
        "/api/sessions",
        json={"pages": 2, "provider": "fake", "model": "fake-1", "preset_id": "manga_lineart"},
    ).json()["id"]
    assert client.patch(f"/api/sessions/{sid}", json={"print_profile": "x"}).status_code == 400
    res = client.patch(
        f"/api/sessions/{sid}",
        json={
            "print_profile": "grafica_canoa",
            "page_size": "formatinho_13.5x19",
            "include_title_page": False,
            "include_belongs_to_page": False,
            "include_color_test_page": False,
        },
    )
    assert res.status_code == 200, res.text
    assert res.json()["print_profile"] == "grafica_canoa"

    report = client.get(f"/api/sessions/{sid}/kdp-preflight").json()
    assert report["profile"] == "grafica_canoa"
    assert report["physical_pages"] == 2
    assert report["kdp_pages"] == 4
    assert report["gutter_mm"] == pytest.approx(6.0, abs=0.1)
    assert any("Gráfica: grampo" in e and "pelo menos 8" in e for e in report["errors"])
    profiles = client.get("/api/print-profiles").json()
    assert {p["id"] for p in profiles} == set(PRINT_PROFILES)
