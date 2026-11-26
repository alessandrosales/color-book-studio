"""Regression tests for security fixes (archive traversal, CSRF/Host, upload caps, Replicate model, SRI)."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from color_book_generator.providers.base import ProviderError
from color_book_generator.providers.replicate import ReplicateProvider
from color_book_generator.web import archive as backup_archive
from color_book_generator.web import store
from color_book_generator.web.app import STATIC_DIR, app
from color_book_generator.web.routers import studio as studio_router


@pytest.fixture(autouse=True)
def _isolated_web_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store, "WEB_ROOT", tmp_path / "web")


client = TestClient(app)


def _zip_bytes(entries: dict[str, bytes | str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_import_rejects_session_id_traversal(tmp_path: Path) -> None:
    escaped_target = tmp_path / "escaped_session"
    payload = _zip_bytes(
        {
            "manifest.json": json.dumps({"archive_version": 1}),
            "data/abc12345/session.json": json.dumps(
                {
                    "id": str(escaped_target),
                    "title": "Evil",
                    "context_prompt": "ctx",
                    "provider": "openai",
                    "model": "gpt-image-1",
                    "pages": [{"index": 1, "prompt": "cat"}],
                }
            ),
            "data/abc12345/planted.txt": "pwned",
        }
    )
    res = client.post(
        "/api/import",
        files={"archive": ("evil.zip", payload, "application/zip")},
    )
    assert res.status_code == 400
    assert not escaped_target.exists()


def test_import_rejects_relative_session_id_traversal(tmp_path: Path) -> None:
    payload = _zip_bytes(
        {
            "manifest.json": json.dumps({"archive_version": 1}),
            "data/abc12345/session.json": json.dumps(
                {
                    "id": "../escaped_rel",
                    "title": "Evil",
                    "context_prompt": "ctx",
                    "provider": "openai",
                    "model": "gpt-image-1",
                    "pages": [{"index": 1, "prompt": "cat"}],
                }
            ),
        }
    )
    res = client.post(
        "/api/import",
        files={"archive": ("evil.zip", payload, "application/zip")},
    )
    assert res.status_code == 400
    assert not (tmp_path / "escaped_rel").exists()


def test_import_rejects_character_id_traversal(tmp_path: Path) -> None:
    payload = _zip_bytes(
        {
            "manifest.json": json.dumps({"archive_version": 1}),
            "data/characters/characters.json": json.dumps(
                [{"id": "../evil", "name": "Evil"}]
            ),
            "data/evil.png": b"\x89PNG\r\n\x1a\n",
        }
    )
    res = client.post(
        "/api/import",
        files={"archive": ("evil-char.zip", payload, "application/zip")},
    )
    assert res.status_code == 400
    assert not (store.WEB_ROOT / "evil.png").exists()


def test_import_enforces_streaming_file_size_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backup_archive, "MAX_FILE_BYTES", 1024)
    payload = _zip_bytes(
        {
            "manifest.json": json.dumps({"archive_version": 1}),
            "data/settings.json": json.dumps(
                {
                    "title": "Big",
                    "pages": 1,
                    "context_prompt": "x" * 2048,
                    "provider": "openai",
                    "model": "gpt-image-1",
                }
            ),
        }
    )
    res = client.post(
        "/api/import",
        files={"archive": ("big.zip", payload, "application/zip")},
    )
    assert res.status_code == 400


def test_import_enforces_manifest_size_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backup_archive, "MAX_MANIFEST_BYTES", 128)
    payload = _zip_bytes(
        {
            "manifest.json": json.dumps({"archive_version": 1, "pad": "a" * 512}),
            "data/settings.json": json.dumps(
                {
                    "title": "Ok",
                    "pages": 1,
                    "context_prompt": "x",
                    "provider": "openai",
                    "model": "gpt-image-1",
                }
            ),
        }
    )
    res = client.post(
        "/api/import",
        files={"archive": ("big-manifest.zip", payload, "application/zip")},
    )
    assert res.status_code == 400


def test_style_upload_enforces_size_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(studio_router, "MAX_IMAGE_UPLOAD_BYTES", 1024)
    # Use a small override directly on read_upload_limited default via monkeypatch
    orig = studio_router.read_upload_limited

    async def _small_limit(upload, max_bytes: int = 1024) -> bytes:
        return await orig(upload, max_bytes=1024)

    monkeypatch.setattr("color_book_generator.web.app.read_upload_limited", _small_limit)
    res = client.post(
        "/api/style",
        files={"image": ("huge.png", b"A" * 2048, "image/png")},
    )
    assert res.status_code == 413


def test_csrf_and_host_protection() -> None:
    # Invalid Host header (DNS rebinding)
    bad_host = client.get("/api/meta", headers={"Host": "evil.example:8000"})
    assert bad_host.status_code == 403

    # Cross-origin Origin header on mutating request
    bad_origin = client.post(
        "/api/auth/codex/logout",
        headers={"Origin": "https://evil.example"},
    )
    assert bad_origin.status_code == 403

    # Null Origin header on mutating request
    null_origin = client.post(
        "/api/auth/codex/logout",
        headers={"Origin": "null"},
    )
    assert null_origin.status_code == 403

    # Sec-Fetch-Site: cross-site on mutating request
    cross_site = client.post(
        "/api/auth/codex/logout",
        headers={"Sec-Fetch-Site": "cross-site"},
    )
    assert cross_site.status_code == 403

    # Same-origin mutating request succeeds
    ok = client.post(
        "/api/sessions",
        json={"pages": 1, "provider": "openai", "model": "gpt-image-1"},
        headers={"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"},
    )
    assert ok.status_code == 201


def test_replicate_rejects_invalid_model_path() -> None:
    provider = ReplicateProvider(model="../../files")
    with pytest.raises(ProviderError, match="invalid replicate model identifier"):
        provider.generate("a cute cat")


def test_static_html_uses_sri_for_gsap() -> None:
    for filename in ("home.html", "index.html"):
        html = (STATIC_DIR / filename).read_text(encoding="utf-8")
        assert 'integrity="sha384-' in html
        assert 'crossorigin="anonymous"' in html
