import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from color_book_generator.providers.base import ImageProvider, register
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


def test_page_caption_is_saved_and_used() -> None:
    session = client.post(
        "/api/sessions",
        json={
            "pages": 1,
            "provider": "fake",
            "model": "fake-1",
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


def test_style_reference_upload_used_and_removed() -> None:
    upload = client.post(
        "/api/style",
        files={"image": ("style.png", _png_bytes(), "image/png")},
    )
    assert upload.status_code == 200, upload.text
    assert upload.json()["url"]
    assert client.get("/api/style/image").status_code == 200

    session = client.post(
        "/api/sessions", json={"pages": 1, "provider": "fake", "model": "fake-1"}
    ).json()
    gen = client.post(
        f"/api/sessions/{session['id']}/pages/1/generate",
        json={"provider": "fake"},
    )
    assert gen.json()["status"] == "done"
    assert FakeProvider.last_style_reference is not None
    assert FakeProvider.last_style_reference.startswith(b"\x89PNG")

    use = client.post(f"/api/sessions/{session['id']}/pages/1/use-as-style")
    assert use.status_code == 200
    assert use.json()["url"]

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
    assert body["characters"] == 1
    assert body["style"] is True
    assert body["latest_session"] == sid

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


def test_import_requires_manifest(monkeypatch, tmp_path) -> None:
    import io
    import zipfile

    monkeypatch.setattr(store, "WEB_ROOT", tmp_path / "restored")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("data/settings.json", "{}")
    res = client.post(
        "/api/import",
        files={"archive": ("bad.zip", buffer.getvalue(), "application/zip")},
    )
    assert res.status_code == 400
