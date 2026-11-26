"""Tests for category presets, character sheets, story scripting, KDP extras, and core fixes."""

from __future__ import annotations

import io
import json
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from pypdf import PdfReader

from color_book_generator.config import BookConfig, ProcessingConfig
from color_book_generator.layout.pdf import compose_pdf
from color_book_generator.processing.line_art import fit_to_page
from color_book_generator.providers.codex import CodexProvider
from color_book_generator.web import store
from color_book_generator.web.app import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _isolated_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store, "WEB_ROOT", tmp_path / "web")


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    img = Image.new("RGB", (200, 200), "white")
    ImageDraw.Draw(img).rectangle((40, 40, 160, 160), outline="black", width=6)
    img.save(buf, "PNG")
    return buf.getvalue()


def test_presets_list_create_apply_and_delete() -> None:
    res = client.get("/api/presets")
    assert res.status_code == 200
    presets = res.json()
    ids = {p["id"] for p in presets}
    assert {"bold_easy_cozy", "kids_classic", "manga_lineart", "manhwa_lineart", "kawaii_chibi", "adult_intricate"}.issubset(ids)

    created = client.post(
        "/api/presets",
        json={
            "name": "Cozy Gatos",
            "category": "cozy",
            "page_size": "square_8.5x8.5",
            "context_prompt": "thick bold cute cat line art",
        },
    )
    assert created.status_code == 201, created.text
    custom_id = created.json()["id"]
    assert created.json()["builtin"] is False

    session = client.post(
        "/api/sessions",
        json={"pages": 2, "provider": "fake", "model": "fake-1", "preset_id": custom_id},
    )
    assert session.status_code == 201
    body = session.json()
    assert body["preset_id"] == custom_id
    assert body["page_size"] == "square_8.5x8.5"
    assert "thick bold cute cat" in body["context_prompt"]

    applied = client.post(
        f"/api/sessions/{body['id']}/apply-preset",
        json={"preset_id": "manhwa_lineart"},
    )
    assert applied.status_code == 200
    refreshed = client.get(f"/api/sessions/{body['id']}").json()
    assert refreshed["preset_id"] == "manhwa_lineart"
    assert refreshed["page_size"] == "6x9"

    assert client.delete(f"/api/presets/{custom_id}").status_code == 204
    assert client.delete("/api/presets/bold_easy_cozy").status_code == 400


def test_per_book_style_and_character_sheet_pipeline() -> None:
    session = client.post(
        "/api/sessions",
        json={"pages": 2, "provider": "fake", "model": "fake-1", "preset_id": "bold_easy_cozy"},
    ).json()
    sid = session["id"]

    # Generate style anchor for this book
    style = client.post(
        f"/api/sessions/{sid}/style/generate",
        json={"sample_subject": "cozy bear drinking tea", "provider": "fake", "model": "fake-1"},
    )
    assert style.status_code == 200, style.text
    assert style.json()["url"]
    assert style.json()["inherited_global"] is False

    # Create character without image, then generate Character Sheet
    char = client.post(
        f"/api/sessions/{sid}/characters",
        data={"name": "Barnaby", "description": "chubby bear wearing a knit scarf"},
    )
    assert char.status_code == 201, char.text
    cid = char.json()["id"]
    assert char.json()["sheet_status"] == "empty"

    sheet = client.post(
        f"/api/sessions/{sid}/characters/{cid}/generate-sheet",
        json={"provider": "fake", "model": "fake-1"},
    )
    assert sheet.status_code == 200, sheet.text
    assert sheet.json()["sheet_status"] == "ready"
    assert sheet.json()["approved"] is True
    assert client.get(sheet.json()["image_url"]).status_code == 200


def test_story_scripting_populates_pages_and_links_characters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from color_book_generator import story as story_mod

    fake_json = json.dumps(
        {
            "title": "As Aventuras de Barnaby",
            "synopsis": "Um ursinho acolhedor vive pequenos momentos de paz na floresta.",
            "pages": [
                {
                    "index": 1,
                    "title": "Chá da manhã",
                    "scene_prompt": "Barnaby pouring warm tea in a cozy treehouse kitchen",
                    "caption_text": "Um chá quentinho para começar o dia!",
                    "character_names": ["Barnaby"],
                },
                {
                    "index": 2,
                    "title": "Piquenique no bosque",
                    "scene_prompt": "Barnaby reading a book on a picnic blanket",
                    "caption_text": "Nada melhor que ler sob as árvores.",
                    "character_names": ["Barnaby"],
                },
            ],
        }
    )
    monkeypatch.setattr(story_mod, "_call_text_llm", lambda *_a, **_k: fake_json)

    session = client.post(
        "/api/sessions",
        json={"pages": 2, "provider": "fake", "model": "fake-1", "preset_id": "bold_easy_cozy"},
    ).json()
    sid = session["id"]
    char = client.post(
        f"/api/sessions/{sid}/characters",
        data={"name": "Barnaby", "description": "cute cozy bear"},
    ).json()

    res = client.post(
        f"/api/sessions/{sid}/story/generate",
        json={"premise": "Um ursinho acolhedor na floresta", "mode": "narrative", "pages": 2},
    )
    assert res.status_code == 200, res.text
    assert res.json()["title"] == "As Aventuras de Barnaby"
    assert res.json()["pages_updated"] == 2

    refreshed = client.get(f"/api/sessions/{sid}").json()
    assert refreshed["story_synopsis"].startswith("Um ursinho")
    assert refreshed["pages"][0]["title"] == "Chá da manhã"
    assert refreshed["pages"][0]["caption_text"] == "Um chá quentinho para começar o dia!"
    assert refreshed["pages"][0]["character_ids"] == [char["id"]]


def test_codex_provider_sends_both_style_and_character_references(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def _fake_create_image(prompt: str, **kwargs: object) -> bytes:
        captured["prompt"] = prompt
        captured["image_urls"] = kwargs.get("image_urls")
        return _png_bytes()

    monkeypatch.setattr("color_book_generator.providers.codex.create_image", _fake_create_image)
    provider = CodexProvider(model="gpt-6.2-sol")
    provider.generate(
        "Barnaby baking bread",
        references=[("Barnaby", _png_bytes())],
        style_reference=_png_bytes(),
    )
    urls = captured["image_urls"]
    assert isinstance(urls, list)
    assert len(urls) == 2


def test_kdp_pdf_extras_and_session_title(tmp_path: Path) -> None:
    page = tmp_path / "page-001.png"
    Image.new("L", (400, 400), 255).save(page)
    cfg = BookConfig(
        title="Cozy Bear Adventures",
        theme="bears",
        page_size="square_8.5x8.5",
        include_title_page=True,
        include_belongs_to_page=True,
        include_color_test_page=True,
        blank_verso=True,
    )
    out = compose_pdf(
        [page],
        tmp_path / "kdp.pdf",
        cfg,
        include_title_page=True,
        blank_verso=True,
        include_belongs_to_page=True,
        include_color_test_page=True,
    )
    reader = PdfReader(str(out))
    # Title + BelongsTo + ColorTest + Page1: blank versos only sit between illustrations
    assert len(reader.pages) == 4
    assert reader.metadata is not None
    assert reader.metadata.title == "Cozy Bear Adventures"


def test_crop_to_content_false_preserves_full_canvas() -> None:
    img = Image.new("L", (400, 400), 255)
    ImageDraw.Draw(img).rectangle((180, 180, 220, 220), fill=0)
    cropped = fit_to_page(img, (400, 400), ProcessingConfig(crop_to_content=True))
    uncropped = fit_to_page(img, (400, 400), ProcessingConfig(crop_to_content=False))
    # Cropped scales up the small square significantly; uncropped keeps it small
    assert (np.asarray(cropped) < 128).sum() > (np.asarray(uncropped) < 128).sum()


def test_autopilot_generates_entire_book_from_style_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time

    from color_book_generator import story as story_mod

    fake_blueprint = json.dumps(
        {
            "title": "Cozy Bakery Days",
            "premise": "Cute bear baking treats in a cozy woodland bakery",
            "synopsis": "Milo the bear bakes warm pastries every morning.",
            "style_anchor_subject": "Milo the bear holding a croissant in a cozy kitchen",
            "characters": [
                {
                    "name": "Milo",
                    "description": "Chubby cozy bear wearing a baker apron and tiny chef hat",
                }
            ],
            "pages": [
                {
                    "index": 1,
                    "title": "Mixing Dough",
                    "scene_prompt": "Milo stirring flour in a big bowl on a wooden counter",
                    "caption_text": "Time to bake something sweet!",
                    "character_names": ["Milo"],
                },
                {
                    "index": 2,
                    "title": "Tea Time",
                    "scene_prompt": "Milo pouring tea next to a plate of cookies",
                    "caption_text": "Warm tea makes everything cozy.",
                    "character_names": ["Milo"],
                },
            ],
        }
    )
    monkeypatch.setattr(story_mod, "_call_text_llm", lambda *_a, **_k: fake_blueprint)

    session = client.post(
        "/api/sessions",
        json={"pages": 2, "provider": "fake", "model": "fake-1", "preset_id": "bold_easy_cozy"},
    ).json()
    sid = session["id"]

    start = client.post(
        f"/api/sessions/{sid}/autopilot",
        json={"preset_id": "bold_easy_cozy", "pages": 2, "stop_after": "pdf"},
    )
    assert start.status_code == 202, start.text

    # Poll until background task completes
    final_status = None
    for _ in range(50):
        st = client.get(f"/api/sessions/{sid}/autopilot").json()
        if st["status"] in {"done", "error", "cancelled"}:
            final_status = st
            break
        time.sleep(0.05)

    assert final_status is not None
    assert final_status["status"] == "done", final_status

    book = client.get(f"/api/sessions/{sid}").json()
    assert book["title"] == "Cozy Bakery Days"
    assert book["style_url"]
    assert len(book["characters"]) == 1
    assert book["characters"][0]["sheet_status"] == "ready"
    assert book["characters"][0]["approved"] is True
    assert all(p["status"] == "done" for p in book["pages"])
    assert book["pdf_ready"] is True
    assert client.get(book["pdf_url"]).content.startswith(b"%PDF")


def test_selective_zip_export_includes_only_chosen_books() -> None:
    import zipfile

    b1 = client.post(
        "/api/sessions", json={"pages": 1, "provider": "fake", "model": "fake-1", "title": "Book Alpha"}
    ).json()
    b2 = client.post(
        "/api/sessions", json={"pages": 1, "provider": "fake", "model": "fake-1", "title": "Book Beta"}
    ).json()

    single = client.get(f"/api/sessions/{b1['id']}/export")
    assert single.status_code == 200
    assert "book-alpha.zip" in single.headers.get("content-disposition", "")
    with zipfile.ZipFile(io.BytesIO(single.content)) as zf:
        names = zf.namelist()
        assert f"data/{b1['id']}/session.json" in names
        assert f"data/{b2['id']}/session.json" not in names

    multi = client.post("/api/export/selected", json={"session_ids": [b2["id"]]})
    assert multi.status_code == 200
    with zipfile.ZipFile(io.BytesIO(multi.content)) as zf:
        names = zf.namelist()
        assert f"data/{b2['id']}/session.json" in names
        assert f"data/{b1['id']}/session.json" not in names

    empty = client.post("/api/export/selected", json={"session_ids": []})
    assert empty.status_code == 400
