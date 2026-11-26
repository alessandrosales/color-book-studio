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


def _stub_finishing(monkeypatch: pytest.MonkeyPatch, title: str = "The Final Title") -> None:
    """Replace the book-finishing LLM calls (frame texts and text-page layout)."""
    from color_book_generator import story as story_mod
    from color_book_generator.page_layout import PageLayout
    from color_book_generator.web.routers import studio as studio_mod

    def _finishing(**_kwargs: object) -> story_mod.BookFinishing:
        return story_mod.BookFinishing(
            title=title,
            synopsis="A finished synopsis.",
            cover_prompt="The hero under a rainbow",
            front=story_mod.ExtraPageCopy(
                placement="front", title="Welcome!", text="Grab your colors.",
                illustration="hero smiling",
            ),
            back=story_mod.ExtraPageCopy(placement="back", text="Thank you"),
        )

    monkeypatch.setattr(studio_mod, "generate_book_finishing", _finishing)
    monkeypatch.setattr(
        studio_mod,
        "plan_page_layout",
        lambda **_kwargs: PageLayout(
            text_align="center", text_vertical="center", show_border=False,
            image_position="above", image_size="small", image_prompt="hero",
        ),
    )


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
    # Three front pages, alignment page, illustration and its blank verso
    assert len(reader.pages) == 6
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
    _stub_finishing(monkeypatch, title="Milo's Cozy Bakery")

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
    for _ in range(200):
        st = client.get(f"/api/sessions/{sid}/autopilot").json()
        if st["status"] in {"done", "error", "cancelled"}:
            final_status = st
            break
        time.sleep(0.05)

    assert final_status is not None
    assert final_status["status"] == "done", final_status

    book = client.get(f"/api/sessions/{sid}").json()
    # The finishing phase re-reads the finished pages and settles the final title.
    assert book["title"] == "Milo's Cozy Bakery"
    assert book["story_synopsis"] == "A finished synopsis."
    assert book["cover_title"] == "Milo's Cozy Bakery"
    assert book["cover_preview_url"]
    assert [p["text"] for p in book["front_extra_pages"]] == ["Grab your colors."]
    assert [p["text"] for p in book["back_extra_pages"]] == ["Thank you"]
    for extra in book["front_extra_pages"] + book["back_extra_pages"]:
        assert extra["status"] == "done"
        assert store.extra_page_background_path(sid, extra["id"]).exists()
    assert book["style_url"]
    assert len(book["characters"]) == 1
    assert book["characters"][0]["sheet_status"] == "ready"
    assert book["characters"][0]["approved"] is True
    assert all(p["status"] == "done" for p in book["pages"])
    assert book["pdf_ready"] is True
    assert client.get(book["pdf_url"]).content.startswith(b"%PDF")


def test_autopilot_preserves_user_stage1_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time

    from color_book_generator import story as story_mod

    fake_blueprint = json.dumps(
        {
            "title": "Custom Configured Book",
            "premise": "A quiet forest walk",
            "synopsis": "Short synopsis.",
            "style_anchor_subject": "Fox under a tree",
            "characters": [],
            "pages": [
                {
                    "title": "Scene 1",
                    "prompt": "Fox by a stream",
                    "caption_text": "Quiet stream.",
                    "character_names": [],
                }
            ],
        }
    )
    monkeypatch.setattr(story_mod, "_call_text_llm", lambda *_a, **_k: fake_blueprint)

    session = client.post(
        "/api/sessions",
        json={"pages": 1, "provider": "fake", "model": "fake-1", "preset_id": "bold_easy_cozy"},
    ).json()
    sid = session["id"]

    patched = client.patch(
        f"/api/sessions/{sid}",
        json={
            "context_prompt": "My custom line art instructions",
            "negative_prompt": "no hatching",
            "page_size": "a4",
            "processing": {"threshold": "adaptive", "min_component_px": 12, "denoise": False},
            "caption_enabled": False,
            "caption_font_family": "serif",
            "caption_font_bold": True,
        },
    )
    assert patched.status_code == 200, patched.text

    start = client.post(
        f"/api/sessions/{sid}/autopilot",
        json={"pages": 1, "stop_after": "blueprint"},
    )
    assert start.status_code == 202, start.text

    final_status = None
    for _ in range(50):
        st = client.get(f"/api/sessions/{sid}/autopilot").json()
        if st["status"] in {"done", "error", "cancelled"}:
            final_status = st
            break
        time.sleep(0.05)

    assert final_status is not None and final_status["status"] == "done", final_status
    book = client.get(f"/api/sessions/{sid}").json()
    assert book["context_prompt"] == "My custom line art instructions"
    assert book["negative_prompt"] == "no hatching"
    assert book["page_size"] == "a4"
    assert book["processing"]["threshold"] == "adaptive"
    assert book["processing"]["min_component_px"] == 12
    assert book["processing"]["denoise"] is False
    assert book["caption_enabled"] is False
    assert book["caption_font_family"] == "serif"
    assert book["caption_font_bold"] is True


def test_create_session_accepts_full_initial_configuration_over_preset() -> None:
    res = client.post(
        "/api/sessions",
        json={
            "title": "Pre-configured Book",
            "pages": 4,
            "language": "pt-BR",
            "preset_id": "bold_easy_cozy",
            "context_prompt": "Custom style right from new book drawer",
            "negative_prompt": "no gray, no text",
            "page_size": "a4",
            "provider": "fake",
            "model": "fake-1",
            "processing": {
                "threshold": "adaptive",
                "min_component_px": 15,
                "denoise": False,
                "crop_to_content": False,
            },
            "blank_verso": True,
            "include_title_page": False,
            "include_belongs_to_page": True,
            "include_color_test_page": True,
            "caption_enabled": True,
            "caption_style": "caption",
            "caption_position": "top",
            "caption_font_family": "serif",
            "caption_font_bold": True,
            "caption_font_size": 0.04,
        },
    )
    assert res.status_code == 201, res.text
    book = res.json()
    assert book["context_prompt"] == "Custom style right from new book drawer"
    assert book["negative_prompt"] == "no gray, no text"
    assert book["page_size"] == "a4"
    assert book["processing"]["threshold"] == "adaptive"
    assert book["processing"]["min_component_px"] == 15
    assert book["processing"]["denoise"] is False
    assert book["processing"]["crop_to_content"] is False
    assert book["blank_verso"] is True
    assert book["include_title_page"] is False
    assert book["include_belongs_to_page"] is True
    assert book["include_color_test_page"] is True
    assert book["caption_enabled"] is True
    assert book["caption_style"] == "caption"
    assert book["caption_position"] == "top"
    assert book["caption_font_family"] == "serif"
    assert book["caption_font_bold"] is True
    assert book["caption_font_size"] == 0.04


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
    state = store.load_session(sid)
    assert state is not None
    state.pages[0].status = "done"
    store.save_session(state)
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


def test_insert_contextual_page_bridges_neighbors_and_shifts_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from color_book_generator import story as story_mod

    captured: list[dict[str, object]] = []

    def _fake_llm(instructions: str, user_prompt: str, **_kwargs: object) -> str:
        captured.append({"instructions": instructions, "user_prompt": user_prompt})
        return (
            '{"title":"Ponte no rio","scene_prompt":"Barnaby crossing a wooden footbridge over a gentle stream",'
            '"caption_text":"Vamos atravessar a ponte!","character_names":["Barnaby"]}'
        )

    monkeypatch.setattr(story_mod, "_call_text_llm", _fake_llm)

    session = client.post(
        "/api/sessions",
        json={"pages": 2, "provider": "fake", "model": "fake-1", "title": "Passeio na Floresta"},
    ).json()
    sid = session["id"]

    # Add character Barnaby
    char_res = client.post(
        f"/api/sessions/{sid}/characters",
        data={"name": "Barnaby", "description": "Ursinho de cachecol"},
    )
    assert char_res.status_code == 201
    cid = char_res.json()["id"]

    # Set distinct scenes on page 1 and page 2, and generate page 2 artwork on disk
    client.patch(
        f"/api/sessions/{sid}/pages/1",
        json={"title": "Saída da cabana", "prompt": "Barnaby leaving the cozy cabin", "character_ids": [cid]},
    )
    client.patch(
        f"/api/sessions/{sid}/pages/2",
        json={"title": "Chegada na clareira", "prompt": "Barnaby having a picnic in the sunny meadow", "character_ids": [cid]},
    )
    gen2 = client.post(
        f"/api/sessions/{sid}/pages/2/generate",
        json={"prompt": "Barnaby having a picnic in the sunny meadow"},
    )
    assert gen2.status_code == 200, gen2.text
    assert store.page_file(sid, 2).exists()
    assert (store.raw_dir(sid) / "page-002.png").exists()
    assert (store.preview_dir(sid) / "page-002.png").exists()

    # Insert contextual page right after page 1 (between page 1 and page 2)
    inserted = client.post(
        f"/api/sessions/{sid}/pages/insert",
        json={"after_index": 1},
    )
    assert inserted.status_code == 201, inserted.text
    body = inserted.json()
    assert body["inserted_index"] == 2
    assert body["total_pages"] == 3
    assert body["title"] == "Ponte no rio"
    assert body["character_ids"] == [cid]

    # Verify LLM prompt received both previous and next page context
    assert len(captured) == 1
    prompt_sent = str(captured[0]["user_prompt"])
    assert "Barnaby leaving the cozy cabin" in prompt_sent
    assert "Barnaby having a picnic in the sunny meadow" in prompt_sent

    # Verify page 2's artwork shifted cleanly to page 3 on disk and page 2 is now pending
    assert not store.page_file(sid, 2).exists()
    assert store.page_file(sid, 3).exists()
    assert (store.raw_dir(sid) / "page-003.png").exists()
    assert (store.preview_dir(sid) / "page-003.png").exists()

    refreshed = client.get(f"/api/sessions/{sid}").json()
    assert [p["index"] for p in refreshed["pages"]] == [1, 2, 3]
    assert [p["title"] for p in refreshed["pages"]] == [
        "Saída da cabana",
        "Ponte no rio",
        "Chegada na clareira",
    ]
    assert refreshed["pages"][1]["status"] == "pending"
    assert refreshed["pages"][2]["status"] == "done"
    assert refreshed["pages"][2]["preview_url"] is not None


def test_autopilot_cancel_and_resume_from_where_it_stopped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time

    from color_book_generator import story as story_mod
    from color_book_generator.web.routers import studio as studio_mod

    blueprint_calls = 0

    def _fake_blueprint(**_kwargs: object) -> story_mod.FullBookBlueprint:
        nonlocal blueprint_calls
        blueprint_calls += 1
        return story_mod.FullBookBlueprint(
            title="Aventura em Kyoto",
            premise="Dois estudantes em Kyoto",
            synopsis="Uma jornada por templos e ruas chuvosas.",
            mode="narrative",
            style_anchor_subject="two manga students in Kyoto",
            characters=[
                story_mod.GeneratedCharacter(name="Mio", description="Garota de cabelo ondulado")
            ],
            pages=[
                story_mod.ScriptedPage(index=1, title="P1", scene_prompt="Scene 1", caption_text="Oi!", character_names=["Mio"]),
                story_mod.ScriptedPage(index=2, title="P2", scene_prompt="Scene 2", caption_text="Vamos!", character_names=["Mio"]),
                story_mod.ScriptedPage(index=3, title="P3", scene_prompt="Scene 3", caption_text="Fim!", character_names=["Mio"]),
            ],
        )

    monkeypatch.setattr(studio_mod, "generate_full_book_blueprint", _fake_blueprint)
    _stub_finishing(monkeypatch)

    generated_prompts: list[str] = []
    original_gen = studio_mod.generate_matching
    cancel_triggered = False

    session = client.post(
        "/api/sessions",
        json={"pages": 3, "provider": "fake", "model": "fake-1", "preset_id": "manga_shoujo"},
    ).json()
    sid = session["id"]

    def _spy_generate_matching(provider, prompt, **kwargs):
        nonlocal cancel_triggered
        generated_prompts.append(prompt)
        img = original_gen(provider, prompt, **kwargs)
        # Cancel immediately after Page 1 is generated
        if "Page 1 of 3." in prompt and not cancel_triggered:
            cancel_triggered = True
            studio_mod._autopilot_cancel.add(sid)
        return img

    monkeypatch.setattr(studio_mod, "generate_matching", _spy_generate_matching)

    res = client.post(
        f"/api/sessions/{sid}/autopilot",
        json={"pages": 3, "language": "pt", "stop_after": "pdf"},
    )
    assert res.status_code == 202, res.text

    # Wait until autopilot stops in cancelled state
    status: dict[str, object] = {}
    for _ in range(100):
        status = client.get(f"/api/sessions/{sid}/autopilot").json()
        if status["status"] != "running":
            break
        time.sleep(0.05)

    assert status["status"] == "cancelled", status
    assert blueprint_calls == 1
    mid_session = client.get(f"/api/sessions/{sid}").json()
    assert [p["status"] for p in mid_session["pages"]] == ["done", "pending", "pending"]
    assert mid_session["characters"][0]["sheet_status"] == "ready"
    prompts_before_resume = len(generated_prompts)

    # Resume from where it stopped: should NOT re-run blueprint, anchor, sheet, or page 1
    resumed = client.post(
        f"/api/sessions/{sid}/autopilot",
        json={"pages": 3, "language": "pt", "stop_after": "pdf", "resume": True},
    )
    assert resumed.status_code == 202, resumed.text

    for _ in range(100):
        status = client.get(f"/api/sessions/{sid}/autopilot").json()
        if status["status"] != "running":
            break
        time.sleep(0.05)

    assert status["status"] == "done", status
    assert blueprint_calls == 1  # Blueprint was NOT regenerated!
    resumed_prompts = generated_prompts[prompts_before_resume:]
    page_prompts = [p for p in resumed_prompts if " of 3." in p]
    assert len(page_prompts) == 2  # Only Page 2 and Page 3!
    assert "Page 2 of 3." in page_prompts[0]
    assert "Page 3 of 3." in page_prompts[1]
    assert len(resumed_prompts) == 5  # + cover, welcome art and closing art

    def _run_until_idle(payload: dict[str, object]) -> dict[str, object]:
        client.post(f"/api/sessions/{sid}/autopilot", json=payload)
        current: dict[str, object] = {}
        for _ in range(100):
            current = client.get(f"/api/sessions/{sid}/autopilot").json()
            if current["status"] != "running":
                break
            time.sleep(0.05)
        return current

    # Resuming a finished book must not redo the finishing work...
    done_count = len(generated_prompts)
    again = _run_until_idle({"pages": 3, "stop_after": "pdf", "resume": True})
    assert again["status"] == "done", again
    assert len(generated_prompts) == done_count
    # ...unless explicitly asked to redo it (cover + two frame pages).
    redo = _run_until_idle(
        {"pages": 3, "stop_after": "pdf", "resume": True, "redo_finishing": True}
    )
    assert redo["status"] == "done", redo
    assert len(generated_prompts) == done_count + 3

    final_session = client.get(f"/api/sessions/{sid}").json()
    assert [p["status"] for p in final_session["pages"]] == ["done", "done", "done"]
    assert final_session["pdf_ready"] is True


def test_auto_extra_pages_use_book_context_and_replace_placeholders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from color_book_generator import story as story_mod

    seen: dict[str, str] = {}

    def _fake_llm(_instructions: str, user_prompt: str, **_kwargs: object) -> str:
        seen["prompt"] = user_prompt
        return json.dumps(
            {
                "front": {
                    "title": "Welcome, Little Artist!",
                    "text": "Meet Lumi, a gentle unicorn. Grab your colors!",
                    "illustration": "Lumi smiling under a small rainbow",
                },
                "back": {"title": "", "text": "Thank you", "illustration": "Lumi waving"},
            }
        )

    monkeypatch.setattr(story_mod, "_call_text_llm", _fake_llm)
    session = client.post(
        "/api/sessions", json={"pages": 1, "provider": "fake", "model": "fake-1"}
    ).json()
    sid = session["id"]
    state = store.load_session(sid)
    assert state is not None
    state.title = "The Unicorn Who Found His Wings"
    state.story_synopsis = "Lumi discovers he can fly."
    state.pages[0].prompt = "Lumi lost in the enchanted forest"
    state.pages[0].caption_text = "Where is my family?"
    store.save_session(state)
    placeholder = client.post(
        f"/api/sessions/{sid}/extra-pages",
        json={"placement": "front", "title": "Página inicial", "text": ""},
    ).json()

    res = client.post(f"/api/sessions/{sid}/extra-pages/auto")
    assert res.status_code == 200, res.text
    front, back = res.json()["pages"]
    assert front["id"] == placeholder["id"]
    assert front["title"] == "Welcome, Little Artist!"
    assert "Lumi smiling under a small rainbow" in front["prompt"]
    assert back["placement"] == "back" and back["text"] == "Thank you"
    for fragment in ("The Unicorn Who Found His Wings", "Lumi discovers he can fly.",
                     "Lumi lost in the enchanted forest", "Where is my family?"):
        assert fragment in seen["prompt"]

    again = client.post(f"/api/sessions/{sid}/extra-pages/auto").json()["pages"]
    assert [p["id"] for p in again] == [front["id"], back["id"]]
    refreshed = client.get(f"/api/sessions/{sid}").json()
    assert len(refreshed["front_extra_pages"]) == 1
    assert len(refreshed["back_extra_pages"]) == 1


def test_publisher_logo_upload_and_pdf(tmp_path: Path) -> None:
    session = client.post(
        "/api/sessions", json={"pages": 1, "provider": "fake", "model": "fake-1"}
    ).json()
    sid = session["id"]
    assert client.get(f"/api/sessions/{sid}").json()["publisher_logo_url"] is None

    up = client.post(
        "/api/publisher-logo", files={"image": ("logo.png", _png_bytes(), "image/png")}
    )
    assert up.status_code == 200, up.text
    view = client.get(f"/api/sessions/{sid}").json()
    assert view["publisher_logo_url"] and view["include_publisher_logo"] is True
    assert client.get(view["publisher_logo_url"]).status_code == 200

    state = store.load_session(sid)
    assert state is not None
    assert store.publisher_logo_for(state) == store.publisher_logo_path()
    off = client.patch(f"/api/sessions/{sid}", json={"include_publisher_logo": False})
    assert off.json()["include_publisher_logo"] is False
    state = store.load_session(sid)
    assert state is not None and store.publisher_logo_for(state) is None

    page = tmp_path / "p.png"
    Image.new("L", (400, 500), 255).save(page)
    out = compose_pdf(
        [page],
        tmp_path / "book.pdf",
        BookConfig(title="Logo"),
        back_extra_images=[page],
        publisher_logo=store.publisher_logo_path(),
    )
    reader = PdfReader(str(out))
    assert len(reader.pages) == 3
    title_images = reader.pages[0].images
    assert len(title_images) == 1

    assert client.delete("/api/publisher-logo").status_code == 204
    assert client.get(f"/api/sessions/{sid}").json()["publisher_logo_url"] is None


def test_autopilot_finishing_failure_still_composes_pdf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time

    from color_book_generator import story as story_mod
    from color_book_generator.web.routers import studio as studio_mod

    _stub_finishing(monkeypatch)
    monkeypatch.setattr(
        studio_mod,
        "generate_full_book_blueprint",
        lambda **_k: story_mod.FullBookBlueprint(
            title="Lumi",
            premise="A unicorn",
            synopsis="Lumi flies.",
            style_anchor_subject="Lumi",
            characters=[story_mod.GeneratedCharacter(name="Lumi", description="unicorn")],
            pages=[story_mod.ScriptedPage(index=1, scene_prompt="Lumi in a forest")],
        ),
    )

    def _boom(**_kwargs: object) -> story_mod.BookFinishing:
        raise story_mod.StoryGenerationError("sem provedor de texto")

    monkeypatch.setattr(studio_mod, "generate_book_finishing", _boom)
    sid = client.post(
        "/api/sessions", json={"pages": 1, "provider": "fake", "model": "fake-1"}
    ).json()["id"]
    client.post(f"/api/sessions/{sid}/autopilot", json={"pages": 1, "stop_after": "pdf"})
    status: dict[str, object] = {}
    for _ in range(200):
        status = client.get(f"/api/sessions/{sid}/autopilot").json()
        if status["status"] != "running":
            break
        time.sleep(0.05)

    assert status["status"] == "error", status
    assert "textos finais" in str(status["error"])
    book = client.get(f"/api/sessions/{sid}").json()
    assert book["pdf_ready"] is True
    assert book["cover_preview_url"]  # the cover still got painted


def test_cover_prompt_letters_the_exact_title_in_the_art() -> None:
    from color_book_generator.web.routers import studio as studio_mod

    sid = client.post(
        "/api/sessions", json={"pages": 1, "provider": "fake", "model": "fake-1"}
    ).json()["id"]
    state = store.load_session(sid)
    assert state is not None
    state.author = "Alessandro e Lucas"
    prompt = studio_mod._compose_cover_prompt(state, "Lumi Learns to Fly", "", True)
    assert '"Lumi Learns to Fly"' in prompt
    assert "spelled correctly" in prompt
    assert '"Alessandro e Lucas"' in prompt
    assert "Do not render the title" not in prompt

    negative = studio_mod._cover_negative_prompt(
        "color, speech bubble, japanese text, text, watermark, messy sketch", False
    )
    assert "japanese text" not in negative and "speech bubble" not in negative
    assert ", text," not in f", {negative},"
    assert "watermark" in negative and "misspelled title" in negative
