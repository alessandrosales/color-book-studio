import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from pypdf import PdfReader

from color_book_generator.providers.base import ImageProvider, register
from color_book_generator.web import app as app_module
from color_book_generator.web import store
from color_book_generator.web.app import app


@pytest.fixture(autouse=True)
def _isolated_web_root(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "WEB_ROOT", tmp_path / "web")


@register("fake")
class FakeProvider(ImageProvider):
    name = "fake"
    default_model = "fake-1"
    last_references: list[tuple[str, bytes]] | None = None
    last_style_reference: bytes | None = None

    def generate(
        self,
        prompt: str,
        *,
        negative_prompt: str = "",
        target_size: tuple[int, int] = (1024, 1024),
        seed: int | None = None,
        references: list[tuple[str, bytes]] | None = None,
        style_reference: bytes | None = None,
    ) -> Image.Image:
        FakeProvider.last_references = references
        FakeProvider.last_style_reference = style_reference
        img = Image.new("RGB", (600, 780), "white")
        draw = ImageDraw.Draw(img)
        draw.ellipse((140, 160, 460, 480), outline="black", width=10)
        draw.ellipse((250, 280, 280, 300), fill="black")
        draw.ellipse((330, 280, 360, 300), fill="black")
        return img


client = TestClient(app)


def test_meta_lists_features() -> None:
    body = client.get("/api/meta").json()
    assert "characters" in body["features"]
    assert "lightbox" in body["features"]
    assert "sessions-list" in body["features"]
    assert "multi-book" in body["features"]
    assert body["version"]


def test_defaults_endpoint() -> None:
    res = client.get("/api/defaults")
    assert res.status_code == 200
    body = res.json()
    assert body["page_size"] == "letter_8.5x11"
    assert "fake" in body["providers"]
    assert body["context_prompt"]
    assert body["codex_model"]
    assert isinstance(body["openai_configured"], bool)


def test_session_flow_generate_and_compose() -> None:
    created = client.post(
        "/api/sessions",
        json={"pages": 2, "provider": "fake", "model": "fake-1", "title": "Test Book"},
    )
    assert created.status_code == 201
    session = created.json()
    assert len(session["pages"]) == 2
    assert all(p["status"] == "pending" for p in session["pages"])
    sid = session["id"]

    patched = client.patch(f"/api/sessions/{sid}", json={"context_prompt": "clean lines"})
    assert patched.json()["context_prompt"] == "clean lines"

    gen = client.post(
        f"/api/sessions/{sid}/pages/1/generate",
        json={"prompt": "a smiling fox"},
    )
    assert gen.status_code == 200, gen.text
    page = gen.json()
    assert page["status"] == "done", page
    assert page["width_px"] and page["height_px"]
    assert page["preview_url"]

    preview = client.get(page["preview_url"])
    assert preview.status_code == 200
    assert preview.headers["content-type"] == "image/png"

    download = client.get(page["image_url"])
    assert download.status_code == 200

    view = client.get(page["view_url"])
    assert view.status_code == 200
    assert view.headers["content-type"] == "image/png"
    assert "attachment" not in view.headers.get("content-disposition", "")

    composed = client.post(f"/api/sessions/{sid}/compose")
    assert composed.status_code == 200, composed.text
    assert composed.json()["pdf_ready"] is True

    pdf = client.get(f"/api/sessions/{sid}/pdf")
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF")


def test_compose_without_pages_fails() -> None:
    session = client.post(
        "/api/sessions", json={"pages": 1, "provider": "fake"}
    ).json()
    res = client.post(f"/api/sessions/{session['id']}/compose")
    assert res.status_code == 400


def test_kdp_preflight_counts_planned_pages_without_creating_files() -> None:
    session = client.post(
        "/api/sessions", json={"pages": 12, "provider": "fake", "blank_verso": True}
    ).json()
    sid = session["id"]
    res = client.get(f"/api/sessions/{sid}/kdp-preflight")
    assert res.status_code == 200
    report = res.json()
    assert report["physical_pages"] == 26
    assert report["kdp_pages"] == 26
    assert any("pendentes" in error for error in report["errors"])
    assert any("capa completa" in error for error in report["errors"])
    assert not (store.session_dir(sid) / "book.pdf").exists()


def test_kdp_interior_export_omits_cover_and_requires_complete_pages() -> None:
    session = client.post(
        "/api/sessions", json={"pages": 12, "provider": "fake", "blank_verso": True}
    ).json()
    sid = session["id"]
    assert client.post(f"/api/sessions/{sid}/kdp-interior").status_code == 400

    state = store.load_session(sid)
    assert state is not None
    for page in state.pages:
        page.status = "done"
        Image.new("L", (100, 130), 255).save(store.page_file(sid, page.index))
    store.cover_dir(sid).mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (100, 130), "red").save(store.cover_path(sid))
    store.save_session(state)

    response = client.post(f"/api/sessions/{sid}/kdp-interior")
    assert response.status_code == 200
    pdf = PdfReader(io.BytesIO(response.content))
    assert len(pdf.pages) == 26
    assert float(pdf.pages[0].mediabox.width) == 8.5 * 72
    assert float(pdf.pages[0].mediabox.height) == 11 * 72
    assert len(pdf.pages[2].images) == 1


def test_kdp_interior_export_supports_bleed(monkeypatch) -> None:
    from color_book_generator.config import BookConfig

    session = client.post(
        "/api/sessions", json={"pages": 12, "provider": "fake", "blank_verso": True}
    ).json()
    state = store.load_session(session["id"])
    assert state is not None
    for page in state.pages:
        page.status = "done"
        Image.new("L", (100, 130), 255).save(store.page_file(state.id, page.index))
    store.save_session(state)
    monkeypatch.setattr(
        "color_book_generator.web.app._config",
        lambda: BookConfig(theme="test", bleed_in=0.125),
    )
    response = client.post(f"/api/sessions/{state.id}/kdp-interior")
    assert response.status_code == 200, response.text
    pdf = PdfReader(io.BytesIO(response.content))
    # Page box is the trim plus 0.125" of bleed on every side.
    assert float(pdf.pages[0].mediabox.width) == (8.5 + 0.25) * 72
    assert float(pdf.pages[0].mediabox.height) == (11 + 0.25) * 72


def test_unknown_session_and_page() -> None:
    assert client.get("/api/sessions/deadbeef").status_code == 404
    session = client.post("/api/sessions", json={"pages": 1, "provider": "fake"}).json()
    res = client.post(f"/api/sessions/{session['id']}/pages/99/generate", json={})
    assert res.status_code == 404


def test_codex_auth_status_and_logout(monkeypatch) -> None:
    from color_book_generator.auth import codex as codex_auth

    monkeypatch.setattr(codex_auth, "is_authenticated", lambda *_a, **_k: False)
    status = client.get("/api/auth/codex")
    assert status.status_code == 200
    body = status.json()
    assert body["authenticated"] is False
    assert body["pending"] is False
    assert body["verification_url"].startswith("https://auth.openai.com/")

    cleared = {}
    monkeypatch.setattr(codex_auth, "clear_tokens", lambda *_a, **_k: cleared.setdefault("ok", True))
    assert client.post("/api/auth/codex/logout").status_code == 200
    assert cleared["ok"] is True


def test_codex_models_endpoint(monkeypatch) -> None:
    from color_book_generator.auth import codex as codex_auth

    monkeypatch.setattr(codex_auth, "is_authenticated", lambda *_a, **_k: True)
    monkeypatch.setattr(
        codex_auth, "available_models", lambda *_a, **_k: ["gpt-6-sol", "gpt-5.5"]
    )
    body = client.get("/api/auth/codex/models").json()
    assert body["models"] == ["gpt-6-sol", "gpt-5.5"]

    monkeypatch.setattr(codex_auth, "is_authenticated", lambda *_a, **_k: False)
    assert client.get("/api/auth/codex/models").json()["models"] == []


def test_generate_uses_provider_from_request() -> None:
    session = client.post(
        "/api/sessions", json={"pages": 1, "provider": "openai", "model": "gpt-image-1"}
    ).json()
    assert session["provider"] == "openai"

    res = client.post(
        f"/api/sessions/{session['id']}/pages/1/generate",
        json={"provider": "fake", "model": "fake-1"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "done"

    updated = client.get(f"/api/sessions/{session['id']}").json()
    assert updated["provider"] == "fake"
    assert updated["model"] == "fake-1"


def test_settings_roundtrip(monkeypatch, tmp_path) -> None:
    from color_book_generator.web import store

    monkeypatch.setattr(store, "WEB_ROOT", tmp_path)
    initial = client.get("/api/settings").json()
    assert initial["pages"] == 24
    assert initial["context_prompt"]

    payload = {
        "title": "Saved Book",
        "pages": 7,
        "context_prompt": "clean lines, white background",
        "provider": "codex",
        "model": "gpt-6.2-sol",
        "caption_enabled": True,
        "caption_style": "caption",
        "caption_position": "top",
        "caption_font_family": "serif",
        "caption_font_bold": True,
        "caption_font_size": 0.04,
    }
    put = client.put("/api/settings", json=payload)
    assert put.status_code == 200
    assert put.json() == payload
    assert (tmp_path / "settings.json").exists()
    assert client.get("/api/settings").json() == payload


def test_settings_empty_title_falls_back(monkeypatch, tmp_path) -> None:
    from color_book_generator.web import store

    monkeypatch.setattr(store, "WEB_ROOT", tmp_path)
    body = client.put(
        "/api/settings",
        json={
            "title": "   ",
            "pages": 3,
            "context_prompt": "x",
            "provider": "openai",
            "model": "gpt-image-1",
        },
    ).json()
    assert body["title"] == "Meu Livro de Colorir"


def test_patch_pages_resizes_session() -> None:
    session = client.post("/api/sessions", json={"pages": 2, "provider": "fake"}).json()
    sid = session["id"]

    grown = client.patch(f"/api/sessions/{sid}", json={"pages": 4}).json()
    assert [p["index"] for p in grown["pages"]] == [1, 2, 3, 4]
    assert all(p["status"] == "pending" for p in grown["pages"])

    shrunk = client.patch(f"/api/sessions/{sid}", json={"pages": 1}).json()
    assert len(shrunk["pages"]) == 1


def test_patch_pages_refuses_to_drop_generated() -> None:
    session = client.post("/api/sessions", json={"pages": 3, "provider": "fake"}).json()
    sid = session["id"]
    gen = client.post(
        f"/api/sessions/{sid}/pages/3/generate",
        json={"provider": "fake"},
    )
    assert gen.json()["status"] == "done"

    res = client.patch(f"/api/sessions/{sid}", json={"pages": 2})
    assert res.status_code == 409
    assert "imagem" in res.json()["detail"]


def test_page_caption_is_saved_and_used(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}
    original_generate = app_module._provider_generate

    def spy_generate(
        provider_name: str,
        model: str,
        prompt: str,
        target_px: tuple[int, int],
        negative: str,
        seed: int | None,
        references=None,
        style_reference=None,
    ) -> Image.Image:
        captured["prompt"] = prompt
        captured["negative"] = negative
        return original_generate(
            provider_name, model, prompt, target_px, negative, seed, references, style_reference
        )

    monkeypatch.setattr(app_module, "_provider_generate", spy_generate)

    session = client.post(
        "/api/sessions",
        json={
            "pages": 1,
            "provider": "fake",
            "model": "fake-1",
            "preset_id": "manga_shoujo",
            "caption_enabled": True,
            "caption_style": "balloon",
            "caption_position": "bottom",
        },
    ).json()
    assert session["caption_enabled"] is True
    sid = session["id"]

    patched = client.patch(
        f"/api/sessions/{sid}/pages/1",
        json={"prompt": "a lost unicorn", "caption_text": "Where is my family?"},
    )
    assert patched.status_code == 200
    assert patched.json()["caption_text"] == "Where is my family?"

    gen = client.post(
        f"/api/sessions/{sid}/pages/1/generate",
        json={"provider": "fake", "caption_text": "Where is my family?"},
    )
    assert gen.status_code == 200, gen.text
    page = gen.json()
    assert page["status"] == "done", page
    assert page["caption_text"] == "Where is my family?"
    assert client.get(page["preview_url"]).status_code == 200
    assert "Where is my family?" in captured["prompt"]
    assert "Manga speech balloons" in captured["prompt"]
    assert "no text" not in captured["prompt"].lower()
    assert "speech bubble," not in captured["negative"].lower()


def _png_bytes() -> bytes:
    import io

    buffer = io.BytesIO()
    Image.new("RGB", (80, 80), "white").save(buffer, "PNG")
    return buffer.getvalue()


def test_character_upload_applies_to_session_pages() -> None:
    session = client.post(
        "/api/sessions", json={"pages": 2, "provider": "fake", "model": "fake-1"}
    ).json()
    sid = session["id"]
    upload = client.post(
        "/api/characters",
        data={"name": "Lumi", "session_id": sid},
        files={"image": ("lumi.png", _png_bytes(), "image/png")},
    )
    assert upload.status_code == 201, upload.text
    cid = upload.json()["id"]
    refreshed = client.get(f"/api/sessions/{sid}").json()
    assert all(p["character_ids"] == [cid] for p in refreshed["pages"])


def test_character_upload_list_delete_and_reference() -> None:
    upload = client.post(
        "/api/characters",
        data={"name": "Lumi"},
        files={"image": ("lumi.png", _png_bytes(), "image/png")},
    )
    assert upload.status_code == 201, upload.text
    character = upload.json()
    assert character["name"] == "Lumi"
    assert client.get(character["url"]).status_code == 200

    listing = client.get("/api/characters").json()
    assert [c["id"] for c in listing] == [character["id"]]

    session = client.post(
        "/api/sessions", json={"pages": 1, "provider": "fake", "model": "fake-1"}
    ).json()
    sid = session["id"]
    client.patch(
        f"/api/sessions/{sid}/pages/1",
        json={"character_ids": [character["id"]]},
    )
    gen = client.post(
        f"/api/sessions/{sid}/pages/1/generate",
        json={"provider": "fake", "character_ids": [character["id"]]},
    )
    assert gen.status_code == 200, gen.text
    assert gen.json()["character_ids"] == [character["id"]]
    assert FakeProvider.last_references is not None
    assert FakeProvider.last_references[0][0] == "Lumi"
    assert FakeProvider.last_references[0][1].startswith(b"\x89PNG")

    assert client.delete(f"/api/characters/{character['id']}").status_code == 204
    assert client.get("/api/characters").json() == []
    refreshed = client.get(f"/api/sessions/{sid}").json()
    assert refreshed["pages"][0]["character_ids"] == []


def test_character_upload_requires_name_and_image() -> None:
    no_name = client.post(
        "/api/characters",
        data={"name": "   "},
        files={"image": ("x.png", _png_bytes(), "image/png")},
    )
    assert no_name.status_code == 400

    bad_image = client.post(
        "/api/characters",
        data={"name": "Lumi"},
        files={"image": ("x.png", b"not-an-image", "image/png")},
    )
    assert bad_image.status_code == 400


def test_style_reference_is_isolated_per_book_and_never_uses_other_books_art() -> None:
    # A legacy global style upload must NOT bleed into an unrelated book session
    upload = client.post(
        "/api/style",
        files={"image": ("style.png", _png_bytes(), "image/png")},
    )
    assert upload.status_code == 200, upload.text
    assert upload.json()["url"]
    assert client.get("/api/style/image").status_code == 200

    session = client.post(
        "/api/sessions",
        json={
            "pages": 2,
            "provider": "fake",
            "model": "fake-1",
            "preset_id": "manga_lineart",
        },
    ).json()
    assert session["style_url"] is None
    assert session["style_anchor_url"] is None
    assert session["style_inherited_global"] is False

    # The first generation draws the book's anchor from the manga standard; the page
    # itself only sees that anchor, never the category sample page.
    gen = client.post(
        f"/api/sessions/{session['id']}/pages/1/generate",
        json={"provider": "fake"},
    )
    assert gen.json()["status"] == "done"
    anchor = store.session_anchor_path(session["id"]).read_bytes()
    assert FakeProvider.last_style_reference == anchor
    assert anchor != store.category_reference_bytes("manga")
    client.post(f"/api/sessions/{session['id']}/pages/2/generate", json={"provider": "fake"})
    assert FakeProvider.last_style_reference == anchor
    assert store.session_anchor_path(session["id"]).read_bytes() == anchor  # stays fixed

    use = client.post(f"/api/sessions/{session['id']}/pages/2/use-as-style")
    assert use.status_code == 200
    assert use.json()["url"]
    assert store.session_anchor_path(session["id"]).read_bytes() == store.page_file(
        session["id"], 2
    ).read_bytes()

    assert client.delete("/api/style").status_code == 204
    assert client.get("/api/style").json()["url"] is None


def test_patch_caption_font_settings() -> None:
    session = client.post(
        "/api/sessions",
        json={
            "pages": 1,
            "provider": "fake",
            "caption_enabled": True,
            "caption_font_family": "mono",
            "caption_font_bold": True,
            "caption_font_size": 0.045,
        },
    ).json()
    assert session["caption_font_family"] == "mono"
    assert session["caption_font_bold"] is True
    assert session["caption_font_size"] == 0.045

    patched = client.patch(
        f"/api/sessions/{session['id']}",
        json={
            "caption_font_family": "serif",
            "caption_font_bold": False,
            "caption_font_size": 0.03,
        },
    ).json()
    assert patched["caption_font_family"] == "serif"
    assert patched["caption_font_bold"] is False


def test_export_import_roundtrip(monkeypatch, tmp_path) -> None:
    from color_book_generator.web import archive as backup_archive

    monkeypatch.setattr(backup_archive, "SKIP_FAKE_SESSIONS", False)
    from color_book_generator.web import archive as backup_archive

    config_path = tmp_path / "config" / "book.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        "title: Backup Book\ntheme: unicorns\npages: 2\n", encoding="utf-8"
    )
    monkeypatch.setattr(backup_archive, "book_config_path", lambda: config_path)

    session = client.post(
        "/api/sessions",
        json={"pages": 2, "provider": "fake", "model": "fake-1", "title": "Backup Book"},
    ).json()
    sid = session["id"]
    upload = client.post(
        "/api/characters",
        data={"name": "Lumi", "session_id": sid},
        files={"image": ("lumi.png", _png_bytes(), "image/png")},
    )
    assert upload.status_code == 201
    client.post("/api/style", files={"image": ("style.png", _png_bytes(), "image/png")})
    gen = client.post(
        f"/api/sessions/{sid}/pages/1/generate", json={"provider": "fake"}
    )
    assert gen.json()["status"] == "done"
    cover = client.post(
        f"/api/sessions/{sid}/cover/generate",
        json={"title": "Backup Book", "prompt": "Lumi no vale"},
    )
    assert cover.status_code == 200, cover.text
    extra = client.post(
        f"/api/sessions/{sid}/extra-pages",
        json={
            "placement": "front",
            "title": "Welcome",
            "text": "A short introduction.",
            "prompt": "Lumi acima do texto",
        },
    )
    assert extra.status_code == 201, extra.text
    extra_id = extra.json()["id"]
    background = store.extra_page_background_path(sid, extra_id)
    background.parent.mkdir(parents=True, exist_ok=True)
    background.write_bytes(_png_bytes())
    client.put(
        "/api/settings",
        json={
            "title": "Backup Book",
            "pages": 2,
            "context_prompt": "ctx",
            "provider": "fake",
            "model": "fake-1",
            "caption_enabled": True,
            "caption_style": "balloon",
            "caption_position": "bottom",
            "caption_font_family": "serif",
            "caption_font_bold": True,
            "caption_font_size": 0.04,
        },
    )

    exported = client.get("/api/export")
    assert exported.status_code == 200
    assert exported.headers["content-type"] == "application/zip"
    zip_bytes = exported.content
    assert zip_bytes.startswith(b"PK")

    monkeypatch.setattr(store, "WEB_ROOT", tmp_path / "restored")
    imported = client.post(
        "/api/import",
        files={"archive": ("backup.zip", zip_bytes, "application/zip")},
    )
    assert imported.status_code == 200, imported.text
    body = imported.json()
    assert body["sessions"] == 1
    assert body["pages"] == 2
    assert body["extra_pages"] == 1
    assert body["covers"] == 1
    assert body["characters"] == 1
    assert body["style"] is True
    assert body["latest_session"] == sid
    assert sid in body["session_ids"]

    settings = client.get("/api/settings").json()
    assert settings["title"] == "Backup Book"
    assert settings["caption_font_family"] == "serif"
    assert client.get("/api/characters").json()[0]["name"] == "Lumi"
    assert client.get("/api/style").json()["url"]

    restored = client.get(f"/api/sessions/{sid}").json()
    assert len(restored["pages"]) == 2
    page = restored["pages"][0]
    assert page["status"] == "done"
    assert client.get(page["view_url"]).status_code == 200
    assert restored["cover_status"] == "done"
    assert client.get(restored["cover_preview_url"]).status_code == 200
    assert store.cover_path(sid).exists()
    assert len(restored["front_extra_pages"]) == 1
    restored_extra = restored["front_extra_pages"][0]
    assert restored_extra["prompt"] == "Lumi acima do texto"
    assert client.get(restored_extra["preview_url"]).status_code == 200
    assert store.extra_page_path(sid, restored_extra["id"]).exists()
    assert store.extra_page_background_path(sid, restored_extra["id"]).exists()

    restored_config = tmp_path / "restored-config.yaml"
    monkeypatch.setattr(backup_archive, "book_config_path", lambda: restored_config)
    reimported = client.post(
        "/api/import",
        files={"archive": ("backup.zip", zip_bytes, "application/zip")},
    )
    assert reimported.status_code == 200, reimported.text
    assert "unicorns" in restored_config.read_text(encoding="utf-8")


def test_import_rejects_zip_slip(monkeypatch, tmp_path) -> None:
    import io
    import zipfile

    monkeypatch.setattr(store, "WEB_ROOT", tmp_path / "restored")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"archive_version": 1}))
        zf.writestr("data/../evil.txt", "boom")
    res = client.post(
        "/api/import",
        files={"archive": ("bad.zip", buffer.getvalue(), "application/zip")},
    )
    assert res.status_code == 400


def test_import_accepts_legacy_zip_without_manifest(monkeypatch, tmp_path) -> None:
    import io
    import zipfile

    monkeypatch.setattr(store, "WEB_ROOT", tmp_path / "restored")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr(
            "data/settings.json",
            json.dumps(
                {
                    "title": "Legacy",
                    "pages": 3,
                    "context_prompt": "ctx",
                    "provider": "codex",
                    "model": "gpt-6.2-sol",
                }
            ),
        )
    res = client.post(
        "/api/import",
        files={"archive": ("legacy.zip", buffer.getvalue(), "application/zip")},
    )
    assert res.status_code == 200, res.text
    assert client.get("/api/settings").json()["title"] == "Legacy"


def test_import_tolerates_null_caption_settings(monkeypatch, tmp_path) -> None:
    import io
    import zipfile

    monkeypatch.setattr(store, "WEB_ROOT", tmp_path / "restored")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr(
            "manifest.json",
            json.dumps({"archive_version": 1, "app_version": "0.1.0"}),
        )
        zf.writestr(
            "data/settings.json",
            json.dumps(
                {
                    "title": "Legacy Nulls",
                    "pages": 4,
                    "context_prompt": "ctx",
                    "provider": "codex",
                    "model": "gpt-6.2-sol",
                    "caption_enabled": True,
                    "caption_style": "caption",
                    "caption_position": "bottom",
                    "caption_font_family": None,
                    "caption_font_bold": None,
                    "caption_font_size": None,
                }
            ),
        )
    res = client.post(
        "/api/import",
        files={"archive": ("legacy.zip", buffer.getvalue(), "application/zip")},
    )
    assert res.status_code == 200, res.text
    settings = client.get("/api/settings").json()
    assert settings["title"] == "Legacy Nulls"
    assert settings["caption_font_family"] == "sans"
    assert settings["caption_font_bold"] is False
    assert settings["caption_font_size"] == 0.032


def test_import_latest_session_uses_activity(monkeypatch, tmp_path) -> None:
    from color_book_generator.web import archive as backup_archive

    monkeypatch.setattr(backup_archive, "SKIP_FAKE_SESSIONS", False)
    older = client.post(
        "/api/sessions",
        json={"pages": 1, "provider": "fake", "model": "fake-1", "title": "Older"},
    ).json()
    newer = client.post(
        "/api/sessions",
        json={"pages": 1, "provider": "fake", "model": "fake-1", "title": "Newer"},
    ).json()
    # Generate on the older session after the newer one was created.
    client.post(
        f"/api/sessions/{older['id']}/pages/1/generate", json={"provider": "fake"}
    )
    exported = client.get("/api/export").content

    monkeypatch.setattr(store, "WEB_ROOT", tmp_path / "restored")
    imported = client.post(
        "/api/import",
        files={"archive": ("backup.zip", exported, "application/zip")},
    ).json()
    assert imported["latest_session"] == older["id"]
    assert set(imported["session_ids"]) == {older["id"], newer["id"]}


def test_home_and_book_routes() -> None:
    home = client.get("/")
    assert home.status_code == 200
    assert "Seus livros" in home.text

    session = client.post(
        "/api/sessions", json={"pages": 1, "provider": "fake", "model": "fake-1"}
    ).json()
    editor = client.get(f"/books/{session['id']}")
    assert editor.status_code == 200
    assert "Color Book Studio" in editor.text

    missing = client.get("/books/does-not-exist", follow_redirects=False)
    assert missing.status_code == 302
    assert missing.headers["location"] == "/"


def test_sessions_list_orders_by_activity() -> None:
    first = client.post(
        "/api/sessions",
        json={"pages": 2, "provider": "fake", "model": "fake-1", "title": "Alpha"},
    ).json()
    second = client.post(
        "/api/sessions",
        json={"pages": 1, "provider": "fake", "model": "fake-1", "title": "Beta"},
    ).json()
    client.post(
        f"/api/sessions/{first['id']}/pages/1/generate", json={"provider": "fake"}
    )
    summaries = client.get("/api/sessions").json()
    assert [s["title"] for s in summaries[:2]] == ["Alpha", "Beta"]
    assert summaries[0]["done"] == 1
    assert summaries[0]["pages"] == 2
    assert "/pages/1/view" in summaries[0]["cover_url"]
    assert second["id"] in [s["id"] for s in summaries]

    # Once the book has a cover, the library card shows the cover instead.
    assert client.post(
        f"/api/sessions/{first['id']}/cover/generate", json={"provider": "fake"}
    ).status_code == 200
    summaries = client.get("/api/sessions").json()
    card = next(s for s in summaries if s["id"] == first["id"])
    assert "/cover/preview" in card["cover_url"]
    assert client.get(card["cover_url"]).status_code == 200


def test_import_latest_session_skips_newer_empty(monkeypatch, tmp_path) -> None:
    from color_book_generator.web import archive as backup_archive

    monkeypatch.setattr(backup_archive, "SKIP_FAKE_SESSIONS", False)
    book = client.post(
        "/api/sessions",
        json={"pages": 2, "provider": "fake", "model": "fake-1", "title": "Book"},
    ).json()
    client.post(
        f"/api/sessions/{book['id']}/pages/1/generate", json={"provider": "fake"}
    )
    client.post(
        "/api/sessions",
        json={"pages": 2, "provider": "fake", "model": "fake-1", "title": "Empty"},
    )
    exported = client.get("/api/export").content
    monkeypatch.setattr(store, "WEB_ROOT", tmp_path / "restored")
    imported = client.post(
        "/api/import",
        files={"archive": ("backup.zip", exported, "application/zip")},
    ).json()
    assert imported["latest_session"] == book["id"]


def test_delete_session_endpoint() -> None:
    session = client.post(
        "/api/sessions", json={"pages": 1, "provider": "fake", "model": "fake-1"}
    ).json()
    sid = session["id"]
    assert client.delete(f"/api/sessions/{sid}").status_code == 204
    assert client.get(f"/api/sessions/{sid}").status_code == 404
    assert sid not in [item["id"] for item in client.get("/api/sessions").json()]


def test_import_skips_fake_sessions(monkeypatch, tmp_path) -> None:

    fake = client.post(
        "/api/sessions", json={"pages": 1, "provider": "fake", "model": "fake-1"}
    ).json()
    real = client.post(
        "/api/sessions",
        json={"pages": 1, "provider": "codex", "model": "gpt-6.2-sol"},
    ).json()
    exported = client.get("/api/export").content

    monkeypatch.setattr(store, "WEB_ROOT", tmp_path / "restored")
    imported = client.post(
        "/api/import",
        files={"archive": ("backup.zip", exported, "application/zip")},
    ).json()
    assert imported["sessions"] == 1
    assert imported["skipped"] == 1
    assert imported["session_ids"] == [real["id"]]
    assert fake["id"] != imported["latest_session"]


def test_import_creates_copy_when_book_exists(monkeypatch) -> None:
    from color_book_generator.web import archive as backup_archive

    monkeypatch.setattr(backup_archive, "SKIP_FAKE_SESSIONS", False)
    session = client.post(
        "/api/sessions",
        json={"pages": 1, "provider": "fake", "model": "fake-1", "title": "Dup"},
    ).json()
    client.post(
        f"/api/sessions/{session['id']}/pages/1/generate", json={"provider": "fake"}
    )
    exported = client.get("/api/export").content

    # Import into the same workspace: id already exists.
    imported = client.post(
        "/api/import",
        files={"archive": ("backup.zip", exported, "application/zip")},
    ).json()
    assert imported["sessions"] == 1
    new_id = imported["session_ids"][0]
    assert new_id != session["id"]
    assert imported["latest_session"] == new_id

    original = client.get(f"/api/sessions/{session['id']}")
    copy = client.get(f"/api/sessions/{new_id}")
    assert original.status_code == 200
    assert copy.status_code == 200
    assert copy.json()["title"] == "Dup"
    assert copy.json()["pages"][0]["status"] == "done"
    assert client.get(copy.json()["pages"][0]["view_url"]).status_code == 200


def _zip_without(archive_bytes: bytes, drop_suffixes: tuple[str, ...]) -> bytes:
    import io
    import zipfile

    source = zipfile.ZipFile(io.BytesIO(archive_bytes))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as target:
        for info in source.infolist():
            if any(info.filename.endswith(suffix) for suffix in drop_suffixes):
                continue
            target.writestr(info, source.read(info.filename))
    return out.getvalue()


def test_import_rejects_missing_cover_image(monkeypatch, tmp_path) -> None:
    from color_book_generator.web import archive as backup_archive

    monkeypatch.setattr(backup_archive, "SKIP_FAKE_SESSIONS", False)
    session = client.post(
        "/api/sessions", json={"pages": 1, "provider": "fake", "model": "fake-1"}
    ).json()
    sid = session["id"]
    cover = client.post(
        f"/api/sessions/{sid}/cover/generate", json={"title": "Capa", "prompt": "Lumi"}
    )
    assert cover.status_code == 200, cover.text
    archive_bytes = client.get("/api/export").content
    broken = _zip_without(archive_bytes, (f"data/{sid}/cover/cover.png",))

    monkeypatch.setattr(store, "WEB_ROOT", tmp_path / "restored")
    res = client.post(
        "/api/import",
        files={"archive": ("broken.zip", broken, "application/zip")},
    )
    assert res.status_code == 400
    assert "cover" in res.json()["detail"]
    assert not store.session_dir(sid).exists()


def test_import_rejects_missing_extra_page_image(monkeypatch, tmp_path) -> None:
    from color_book_generator.web import archive as backup_archive

    monkeypatch.setattr(backup_archive, "SKIP_FAKE_SESSIONS", False)
    session = client.post(
        "/api/sessions", json={"pages": 1, "provider": "fake", "model": "fake-1"}
    ).json()
    sid = session["id"]
    extra = client.post(
        f"/api/sessions/{sid}/extra-pages",
        json={"placement": "back", "title": "Fim", "text": "Até logo"},
    )
    assert extra.status_code == 201, extra.text
    extra_id = extra.json()["id"]
    archive_bytes = client.get("/api/export").content
    broken = _zip_without(
        archive_bytes, (f"data/{sid}/extras/extra-{extra_id}.png",)
    )

    monkeypatch.setattr(store, "WEB_ROOT", tmp_path / "restored")
    res = client.post(
        "/api/import",
        files={"archive": ("broken.zip", broken, "application/zip")},
    )
    assert res.status_code == 400
    assert "extra" in res.json()["detail"]
    assert not store.session_dir(sid).exists()


def test_library_flags_generation_cut_by_a_restart() -> None:
    session = client.post(
        "/api/sessions", json={"pages": 2, "provider": "fake", "model": "fake-1"}
    ).json()
    card = next(s for s in client.get("/api/sessions").json() if s["id"] == session["id"])
    assert card["autopilot"] is None

    state = store.load_session(session["id"])
    assert state is not None
    state.pages[0].status = "generating"
    store.recover_interrupted(state)  # what the server does when it starts again
    store.save_session(state)

    card = next(s for s in client.get("/api/sessions").json() if s["id"] == session["id"])
    assert card["autopilot"]["status"] == "error"
    assert card["autopilot"]["phase"] == "interrupted"


def test_library_shows_manual_page_generation_in_progress() -> None:
    session = client.post(
        "/api/sessions", json={"pages": 4, "provider": "fake", "model": "fake-1"}
    ).json()
    state = store.load_session(session["id"])
    assert state is not None
    state.pages[0].status = "done"
    state.pages[1].status = "generating"
    store.save_session(state)

    card = next(s for s in client.get("/api/sessions").json() if s["id"] == session["id"])
    assert card["autopilot"]["status"] == "running"
    assert card["autopilot"]["label"] == "Ilustrando página 2 de 4…"
    assert card["autopilot"]["percent"] == 25


def test_interior_preview_works_before_the_book_is_complete() -> None:
    session = client.post("/api/sessions", json={"pages": 12, "provider": "fake"}).json()
    sid = session["id"]
    # Nothing generated yet: there is nothing to preview.
    assert client.post(f"/api/sessions/{sid}/kdp-interior?preview=true").status_code == 400

    state = store.load_session(sid)
    assert state is not None
    for page in state.pages[:3]:
        page.status = "done"
        Image.new("L", (100, 130), 255).save(store.page_file(sid, page.index))
    store.save_session(state)

    # Incomplete book: the print download is refused, the preview is composed.
    assert client.post(f"/api/sessions/{sid}/kdp-interior").status_code == 400
    preview = client.post(f"/api/sessions/{sid}/kdp-interior?preview=true")
    assert preview.status_code == 200
    pdf = PdfReader(io.BytesIO(preview.content))
    assert float(pdf.pages[0].mediabox.width) == 8.5 * 72


def test_cover_is_generated_in_the_books_own_trim_size() -> None:
    session = client.post(
        "/api/sessions", json={"pages": 1, "provider": "fake", "page_size": "6x9"}
    ).json()
    res = client.post(f"/api/sessions/{session['id']}/cover/generate", json={"provider": "fake"})
    assert res.status_code == 200, res.text
    with Image.open(store.cover_path(session["id"])) as cover:
        # 6x9 at 300 dpi (it used to come out 8.5x11 and lose its sides in the wrap).
        assert cover.size == (1800, 2700)

