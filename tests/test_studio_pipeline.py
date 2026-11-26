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
    cover = client.post(
        f"/api/sessions/{b1['id']}/cover/generate",
        json={"title": "Alpha", "prompt": "Lumi"},
    )
    assert cover.status_code == 200, cover.text
    extra = client.post(
        f"/api/sessions/{b1['id']}/extra-pages",
        json={"placement": "front", "title": "Intro", "text": "Welcome"},
    )
    assert extra.status_code == 201, extra.text
    extra_id = extra.json()["id"]

    single = client.get(f"/api/sessions/{b1['id']}/export")
    assert single.status_code == 200
    assert "book-alpha.zip" in single.headers.get("content-disposition", "")
    with zipfile.ZipFile(io.BytesIO(single.content)) as zf:
        names = zf.namelist()
        assert f"data/{b1['id']}/session.json" in names
        assert f"data/{b1['id']}/cover/cover.png" in names
        assert f"data/{b1['id']}/extras/extra-{extra_id}.png" in names
        assert f"data/{b2['id']}/session.json" not in names

    multi = client.post("/api/export/selected", json={"session_ids": [b2["id"]]})
    assert multi.status_code == 200
    with zipfile.ZipFile(io.BytesIO(multi.content)) as zf:
        names = zf.namelist()
        assert f"data/{b2['id']}/session.json" in names
        assert f"data/{b1['id']}/session.json" not in names

    empty = client.post("/api/export/selected", json={"session_ids": []})
    assert empty.status_code == 400


def test_style_reference_detects_caption_font(monkeypatch: pytest.MonkeyPatch) -> None:
    from color_book_generator.typography import TypographyStyle

    monkeypatch.setattr(
        "color_book_generator.web.routers.studio.detect_typography",
        lambda image, *, provider, model: TypographyStyle(font_family="serif", bold=True),
    )
    session = client.post(
        "/api/sessions", json={"pages": 1, "provider": "fake", "model": "fake-1"}
    ).json()
    sid = session["id"]

    upload = client.post(
        f"/api/sessions/{sid}/style",
        files={"image": ("text-style.png", _png_bytes(), "image/png")},
    )
    assert upload.status_code == 200, upload.text

    detected = client.post(f"/api/sessions/{sid}/style/detect")
    assert detected.status_code == 200, detected.text
    body = detected.json()
    assert body["font_family"] == "serif"
    assert body["font_bold"] is True
    assert body["typography_error"] is None

    refreshed = client.get(f"/api/sessions/{sid}").json()
    assert refreshed["caption_font_family"] == "serif"
    assert refreshed["caption_font_bold"] is True


def test_page_character_references_migrate_into_book_cast() -> None:
    cid = "497f39c4"
    store.characters_dir().mkdir(parents=True, exist_ok=True)
    (store.characters_dir() / f"{cid}.png").write_bytes(_png_bytes())
    store.save_characters([store.CharacterRecord(id=cid, name="Lumi")])

    session = client.post(
        "/api/sessions", json={"pages": 1, "provider": "fake", "model": "fake-1"}
    ).json()
    sid = session["id"]
    state = store.load_session(sid)
    assert state is not None
    state.pages[0].character_ids = [cid]
    store.save_session(state)

    refreshed = client.get(f"/api/sessions/{sid}").json()
    assert [c["id"] for c in refreshed["characters"]] == [cid]
    assert refreshed["characters"][0]["name"] == "Lumi"


def test_extra_text_pages_crud_and_pdf_compose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from color_book_generator.page_layout import PageLayout

    monkeypatch.setattr(
        "color_book_generator.web.routers.studio.plan_page_layout",
        lambda **_kwargs: PageLayout(
            text_align="center",
            text_vertical="center",
            show_border=False,
            image_position="above",
            image_size="small",
            image_prompt="Lumi",
        ),
    )
    session = client.post(
        "/api/sessions", json={"pages": 1, "provider": "fake", "model": "fake-1"}
    ).json()
    sid = session["id"]

    created = client.post(
        f"/api/sessions/{sid}/extra-pages",
        json={
            "placement": "front",
            "title": "Bem-vindo",
            "text": "Uma pequena introdução.",
            "prompt": "Moldura de flores nas bordas",
        },
    )
    assert created.status_code == 201, created.text
    page = created.json()
    assert page["status"] == "done"
    assert page["prompt"] == "Moldura de flores nas bordas"
    assert page["preview_url"]
    assert client.get(page["preview_url"]).status_code == 200

    visual = client.post(f"/api/sessions/{sid}/extra-pages/{page['id']}/visual", json={})
    assert visual.status_code == 200, visual.text
    assert visual.json()["preview_url"]
    assert store.extra_page_background_path(sid, page["id"]).exists()

    patched = client.patch(
        f"/api/sessions/{sid}/extra-pages/{page['id']}",
        json={"text": "Texto atualizado.", "include_in_pdf": False},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["text"] == "Texto atualizado."
    assert patched.json()["include_in_pdf"] is False

    back = client.post(
        f"/api/sessions/{sid}/extra-pages",
        json={"placement": "back", "title": "Fim", "text": "Até a próxima!"},
    )
    assert back.status_code == 201
    refreshed = client.get(f"/api/sessions/{sid}").json()
    assert len(refreshed["front_extra_pages"]) == 1
    assert len(refreshed["back_extra_pages"]) == 1

    store.pages_dir(sid).mkdir(parents=True, exist_ok=True)
    Image.new("L", (400, 500), 255).save(store.pages_dir(sid) / "page-001.png")
    composed = client.post(f"/api/sessions/{sid}/compose")
    assert composed.status_code == 200, composed.text
    assert composed.json()["pdf_ready"] is True

    deleted = client.delete(f"/api/sessions/{sid}/extra-pages/{page['id']}")
    assert deleted.status_code == 204
    final = client.get(f"/api/sessions/{sid}").json()
    assert final["front_extra_pages"] == []


def test_cover_generation_exposes_preview_and_settings() -> None:
    session = client.post(
        "/api/sessions", json={"pages": 1, "provider": "fake", "model": "fake-1"}
    ).json()
    sid = session["id"]

    cover = client.post(
        f"/api/sessions/{sid}/cover/generate",
        json={"title": "Lumi e o Vale", "prompt": "Lumi voando ao amanhecer"},
    )
    assert cover.status_code == 200, cover.text
    body = cover.json()
    assert body["status"] == "done"
    assert body["preview_url"]
    assert client.get(body["preview_url"]).status_code == 200
    with Image.open(store.cover_path(sid)) as cover_img:
        assert cover_img.mode == "RGB"

    patched = client.patch(
        f"/api/sessions/{sid}",
        json={"cover_include": False, "cover_title": "Novo título", "cover_color": False},
    )
    assert patched.status_code == 200
    assert patched.json()["cover_include"] is False
    assert patched.json()["cover_title"] == "Novo título"
    assert patched.json()["cover_color"] is False

    line_cover = client.post(f"/api/sessions/{sid}/cover/generate", json={})
    assert line_cover.status_code == 200, line_cover.text
    with Image.open(store.cover_path(sid)) as cover_img:
        assert cover_img.mode == "L"

    removed = client.delete(f"/api/sessions/{sid}/cover")
    assert removed.status_code == 204
    assert client.get(f"/api/sessions/{sid}").json()["cover_preview_url"] is None


def test_patch_author_and_synopsis_for_title_page() -> None:
    session = client.post(
        "/api/sessions", json={"pages": 1, "provider": "fake", "model": "fake-1"}
    ).json()
    sid = session["id"]
    patched = client.patch(
        f"/api/sessions/{sid}",
        json={"author": "Jane Doe", "story_synopsis": "A story about courage."},
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["author"] == "Jane Doe"
    assert body["story_synopsis"] == "A story about courage."


def test_plan_page_layout_reads_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    from color_book_generator import page_layout

    monkeypatch.setattr(
        page_layout,
        "_call_text_llm",
        lambda *_a, **_k: (
            '{"text_align":"center","text_vertical":"center","show_border":false,'
            '"image_position":"above","image_size":"small","image_prompt":"Lumi"}'
        ),
    )
    layout = page_layout.plan_page_layout(
        page_prompt="texto centralizado, sem bordas, Lumi pequeno acima",
        title="",
        text="Thank you",
    )
    assert layout.text_align == "center"
    assert layout.text_vertical == "center"
    assert layout.show_border is False
    assert layout.image_position == "above"
    assert layout.image_size == "small"
    assert layout.image_prompt == "Lumi"
