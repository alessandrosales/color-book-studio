"""Wrap cover geometry, cover PDF and the PDF/X export."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pypdf import PdfReader

from color_book_generator.layout import pdfx
from color_book_generator.layout.cover import compose_cover_pdf, cover_geometry
from color_book_generator.print_profile import PRINT_PROFILES
from color_book_generator.web import store
from color_book_generator.web.app import app

client = TestClient(app)
needs_gs = pytest.mark.skipif(not pdfx.available(), reason="Ghostscript not installed")


@pytest.fixture(autouse=True)
def _isolated_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store, "WEB_ROOT", tmp_path / "web")


def test_geometry_matches_kdp_formula() -> None:
    spine = PRINT_PROFILES["kdp_paperback"].spine_in(100)
    assert spine == pytest.approx(0.2252)
    geo = cover_geometry((8.5, 11.0), spine, 0.125)
    assert geo.total_w == pytest.approx(0.125 * 2 + 8.5 * 2 + 0.2252)
    assert geo.total_h == pytest.approx(11.25)
    assert geo.front_x == pytest.approx(0.125 + 8.5 + 0.2252)
    bx, by, bw, _bh = geo.barcode_box()
    # Barcode area sits on the back cover, clear of the spine and the bottom trim.
    assert bx + bw == pytest.approx(geo.spine_x - 0.25)
    assert by == pytest.approx(0.125 + 0.25)


def test_spine_rules_by_profile_and_paper() -> None:
    kdp = PRINT_PROFILES["kdp_paperback"]
    assert kdp.spine_in(100, "cream") == pytest.approx(0.25)
    assert PRINT_PROFILES["grafica_canoa"].spine_in(40) == 0.0
    assert PRINT_PROFILES["home_print"].spine_in(40) == 0.0


def _cover_png(path: Path) -> Path:
    Image.new("RGB", (850, 1100), (30, 60, 120)).save(path)
    return path


def test_cover_pdf_has_wrap_size_and_trim_box(tmp_path: Path) -> None:
    geo = cover_geometry((8.5, 11.0), 0.3, 0.125)
    out = compose_cover_pdf(
        tmp_path / "cover.pdf", _cover_png(tmp_path / "c.png"), geo, title="Lia", author="Ana", synopsis="Uma história."
    )
    page = PdfReader(str(out)).pages[0]
    assert float(page.mediabox.width) == pytest.approx(geo.total_w * 72)
    assert float(page.mediabox.height) == pytest.approx(geo.total_h * 72)
    assert float(page.trimbox.left) == pytest.approx(9)
    assert "Lia" in page.extract_text()


def test_thin_spine_gets_no_lettering(tmp_path: Path) -> None:
    geo = cover_geometry((6.0, 9.0), 0.06, 0.125)
    out = compose_cover_pdf(tmp_path / "c.pdf", _cover_png(tmp_path / "c.png"), geo, title="ZZTITLE")
    # Title only on the back cover, not on the spine.
    assert PdfReader(str(out)).pages[0].extract_text().count("ZZTITLE") == 1


def test_interior_pdf_declares_trim_box(tmp_path: Path) -> None:
    from color_book_generator.config import BookConfig
    from color_book_generator.layout.pdf import compose_pdf

    art = tmp_path / "a.png"
    Image.new("L", (900, 1350), 255).save(art)
    cfg = BookConfig(theme="x", page_size="6x9", bleed_in=0.125, dpi=150)
    out = compose_pdf([art], tmp_path / "i.pdf", cfg, include_title_page=False)
    page = PdfReader(str(out)).pages[0]
    assert float(page.trimbox.left) == pytest.approx(9)
    assert float(page.bleedbox.width) == pytest.approx((6 + 0.25) * 72)


@needs_gs
def test_pdfx_conversion_declares_output_intent(tmp_path: Path) -> None:
    geo = cover_geometry((6.0, 9.0), 0.2, 0.125)
    src = compose_cover_pdf(tmp_path / "c.pdf", _cover_png(tmp_path / "c.png"), geo, title="Lia (x)")
    result = pdfx.convert_to_pdfx(src, tmp_path / "x.pdf", mode="cmyk", title="Lia (x)")
    reader = PdfReader(str(result.path))
    assert reader.metadata.get("/GTS_PDFXVersion") == "PDF/X-3:2002"
    assert "/OutputIntents" in reader.trailer["/Root"]
    assert float(reader.pages[0].trimbox.left) == pytest.approx(9)


def test_pdfx_reports_missing_custom_icc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CBG_PDFX_ICC", str(tmp_path / "missing.icc"))
    with pytest.raises(pdfx.PdfxError):
        pdfx.convert_to_pdfx(tmp_path / "a.pdf", tmp_path / "b.pdf", mode="cmyk", title="x")


def test_ps_string_escapes_title() -> None:
    assert pdfx._ps_string("a(b)\\c") == "a\\(b\\)\\\\c"


def _book() -> str:
    sid = client.post(
        "/api/sessions", json={"pages": 24, "provider": "fake", "model": "fake-1"}
    ).json()["id"]
    return sid


def test_cover_endpoints() -> None:
    sid = _book()
    geo = client.get(f"/api/sessions/{sid}/cover-geometry").json()
    assert geo["has_spine"] is True and geo["cover_ready"] is False
    assert client.post(f"/api/sessions/{sid}/cover-pdf").status_code == 400

    store.cover_dir(sid).mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (850, 1100), (200, 40, 40)).save(store.cover_path(sid))
    client.patch(f"/api/sessions/{sid}", json={"paper": "cream"})
    geo = client.get(f"/api/sessions/{sid}/cover-geometry").json()
    assert geo["paper"] == "cream" and geo["cover_ready"] is True
    assert geo["spine_mm"] == pytest.approx(geo["pages"] * 0.0025 * 25.4, abs=0.1)

    res = client.post(f"/api/sessions/{sid}/cover-pdf")
    assert res.status_code == 200, res.text
    page = PdfReader(io.BytesIO(res.content)).pages[0]
    assert float(page.mediabox.width) == pytest.approx(geo["total_w_mm"] / 25.4 * 72, abs=0.5)

    preflight = client.get(f"/api/sessions/{sid}/kdp-preflight").json()
    assert not any("capa completa em PDF separado" in e for e in preflight["errors"])
    assert any("Lombada de" in w for w in preflight["warnings"])


@needs_gs
def test_cover_endpoint_pdfx() -> None:
    sid = _book()
    store.cover_dir(sid).mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (850, 1100), (200, 40, 40)).save(store.cover_path(sid))
    res = client.post(f"/api/sessions/{sid}/cover-pdf?pdfx=true")
    assert res.status_code == 200, res.text
    assert PdfReader(io.BytesIO(res.content)).metadata.get("/GTS_PDFXVersion") == "PDF/X-3:2002"


def test_rtl_wrap_puts_the_front_on_the_left() -> None:
    geo = cover_geometry((6.0, 9.0), 0.1, 0.125, rtl=True)
    assert geo.front_x == pytest.approx(0.125)
    assert geo.back_x == pytest.approx(0.125 + 6.0 + 0.1)
    bx, _by, bw, _bh = geo.barcode_box()
    # Barcode stays at the bottom right of the back panel, clear of the outer trim.
    assert bx + bw == pytest.approx(geo.back_x + 6.0 - 0.25)


def test_short_book_spine_uses_the_publishable_page_count() -> None:
    sid = client.post(
        "/api/sessions", json={"pages": 4, "provider": "fake", "model": "fake-1"}
    ).json()["id"]
    geo = client.get(f"/api/sessions/{sid}/cover-geometry").json()
    # KDP binds at least 24 pages, so the spine is sized for 24 even if fewer exist now.
    assert geo["spine_mm"] == pytest.approx(24 * 0.002252 * 25.4, abs=0.05)

