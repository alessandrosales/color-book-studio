import base64
import io
import json
import time

import pytest
from PIL import Image

from color_book_generator.auth import codex
from color_book_generator.providers.codex import CodexProvider


def _jwt(exp: int) -> str:
    def enc(value: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    return f"{enc({'alg': 'none'})}.{enc({'exp': exp})}.signature"


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), "white").save(buffer, "PNG")
    return buffer.getvalue()


def test_save_and_load_tokens_uses_private_file(tmp_path) -> None:
    path = tmp_path / "auth" / "auth.json"
    codex.save_tokens({"access_token": "access", "refresh_token": "refresh"}, path)

    assert codex.load_tokens(path) == {"access_token": "access", "refresh_token": "refresh"}
    assert path.stat().st_mode & 0o777 == 0o600


def test_authenticate_completes_device_code_flow(monkeypatch, tmp_path, capsys) -> None:
    responses = iter(
        [
            (200, {"user_code": "ABCD", "device_auth_id": "device", "interval": 0}),
            (403, {}),
            (200, {"authorization_code": "code", "code_verifier": "verifier"}),
            (200, {"access_token": "access", "refresh_token": "refresh"}),
        ]
    )
    monkeypatch.setattr(codex, "_post_json", lambda *_args: next(responses))
    monkeypatch.setattr(codex, "_post_form", lambda *_args: next(responses))
    seen: dict[str, str] = {}
    path = tmp_path / "auth.json"

    codex.authenticate(
        path,
        on_code=lambda user_code, url: seen.update(code=user_code, url=url),
        open_browser=lambda _: True,
        sleep=lambda _: None,
    )

    assert codex.load_tokens(path) == {"access_token": "access", "refresh_token": "refresh"}
    assert seen["code"] == "ABCD"
    assert "Codigo: ABCD" in capsys.readouterr().out


def test_expired_token_is_refreshed_and_saved(monkeypatch, tmp_path) -> None:
    path = tmp_path / "auth.json"
    codex.save_tokens(
        {"access_token": _jwt(int(time.time()) - 60), "refresh_token": "old"},
        path,
    )
    fresh = _jwt(int(time.time()) + 3600)
    monkeypatch.setattr(
        codex,
        "_post_form",
        lambda *_args: (200, {"access_token": fresh, "refresh_token": "new"}),
    )

    assert codex.get_access_token(path) == fresh
    assert codex.load_tokens(path) == {"access_token": fresh, "refresh_token": "new"}


def test_refresh_without_token_fails() -> None:
    with pytest.raises(codex.CodexAuthError):
        codex.refresh_tokens({"access_token": "x"})


def test_create_image_parses_event_stream(monkeypatch, tmp_path) -> None:
    png = _png()
    encoded = base64.b64encode(png).decode()
    event = (
        "data: "
        + json.dumps(
            {
                "type": "response.output_item.done",
                "item": {
                    "type": "image_generation_call",
                    "status": "completed",
                    "result": encoded,
                },
            }
        )
        + "\n"
    )
    captured: dict[str, object] = {}

    def fake_stream(url, payload, headers, timeout):
        captured["url"] = url
        captured["payload"] = json.loads(payload)
        captured["headers"] = headers
        return event

    monkeypatch.setattr(codex, "_post_stream", fake_stream)
    monkeypatch.setattr(codex, "get_access_token", lambda *_a, **_k: "token")
    monkeypatch.setattr(codex, "available_models", lambda *_a, **_k: [])

    result = codex.create_image("draw a fox", auth_path=tmp_path / "auth.json")

    assert result == png
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["tools"][0]["type"] == "image_generation"
    assert payload["tools"][0]["output_format"] == "png"
    assert captured["url"] == codex.CODEX_RESPONSES_URL


def test_provider_maps_target_aspect_and_returns_image(monkeypatch) -> None:
    monkeypatch.setattr(
        "color_book_generator.providers.codex.create_image",
        lambda prompt, **kwargs: _png(),
    )
    provider = CodexProvider()
    image = provider.generate("line art page", target_size=(2625, 3375))

    assert image.mode == "RGB"
    assert image.size == (64, 64)


def test_no_auth_raises_on_create_image(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(codex, "get_access_token", lambda *_a, **_k: (_ for _ in ()).throw(
        codex.CodexAuthError("ChatGPT Plus nao esta autenticado.")
    ))
    with pytest.raises(codex.CodexAuthError):
        codex.create_image("x", auth_path=tmp_path / "missing.json")


def _event(png: bytes) -> str:
    return (
        "data: "
        + json.dumps(
            {
                "type": "response.output_item.done",
                "item": {
                    "type": "image_generation_call",
                    "status": "completed",
                    "result": base64.b64encode(png).decode(),
                },
            }
        )
        + "\n"
    )


def test_normalize_codex_model() -> None:
    assert codex.normalize_codex_model("gpt-image-1") == codex.DEFAULT_CODEX_MODEL
    assert codex.normalize_codex_model(None) == codex.DEFAULT_CODEX_MODEL
    assert codex.normalize_codex_model("gpt-5.5") == "gpt-5.5"


def test_create_image_falls_back_to_square_medium(monkeypatch, tmp_path) -> None:
    calls: list[dict] = []
    png = _png()

    def fake_stream(url, payload, headers, timeout):
        calls.append(json.loads(payload))
        if len(calls) == 1:
            raise codex.CodexRequestError("rejected: invalid size", 400)
        return _event(png)

    monkeypatch.setattr(codex, "_post_stream", fake_stream)
    monkeypatch.setattr(codex, "get_access_token", lambda *_a, **_k: "token")
    monkeypatch.setattr(codex, "available_models", lambda *_a, **_k: [])

    result = codex.create_image(
        "draw", auth_path=tmp_path / "auth.json", size="1024x1536", quality="high"
    )

    assert result == png
    assert calls[0]["tools"][0]["size"] == "1024x1536"
    assert calls[1]["tools"][0]["size"] == "1024x1024"
    assert calls[1]["tools"][0]["quality"] == "medium"


def test_create_image_reports_backend_rejection(monkeypatch, tmp_path) -> None:
    def fake_stream(url, payload, headers, timeout):
        raise codex.CodexRequestError(
            "A geracao de imagem foi recusada pelo ChatGPT Plus (400): bad quality", 400
        )

    monkeypatch.setattr(codex, "_post_stream", fake_stream)
    monkeypatch.setattr(codex, "get_access_token", lambda *_a, **_k: "token")
    monkeypatch.setattr(codex, "available_models", lambda *_a, **_k: [])

    with pytest.raises(codex.CodexRequestError) as excinfo:
        codex.create_image("draw", auth_path=tmp_path / "auth.json")
    assert "400" in str(excinfo.value)
    assert "bad quality" in str(excinfo.value)


def test_ranked_slugs_filters_hidden_and_sorts_priority() -> None:
    entries = [
        {"slug": "beta", "priority": 2},
        {"slug": "hidden-one", "priority": 0, "visibility": "hide"},
        {"slug": "alpha", "priority": 1},
        {"slug": ""},
        "not-a-dict",
        {"slug": "beta", "priority": 9},
    ]
    assert codex._ranked_slugs(entries) == ["alpha", "beta"]


def test_available_models_uses_live_catalog(monkeypatch) -> None:
    codex._catalog_cache.clear()
    monkeypatch.setattr(codex, "get_access_token", lambda *_a, **_k: "token")
    monkeypatch.setattr(
        codex,
        "_get",
        lambda *_a, **_k: (200, {"models": [{"slug": "gpt-6-sol", "priority": 1}, {"slug": "gpt-5.5", "priority": 2}]}),
    )
    assert codex.available_models() == ["gpt-6-sol", "gpt-5.5"]
    codex._catalog_cache.clear()


def test_resolve_model_falls_back_to_catalog(monkeypatch) -> None:
    monkeypatch.setattr(codex, "available_models", lambda *_a, **_k: ["gpt-6-sol", "gpt-5.5"])
    assert codex.resolve_codex_model("gpt-5.5") == "gpt-5.5"
    assert codex.resolve_codex_model("gpt-image-1") == "gpt-6-sol"
    assert codex.resolve_codex_model("retired-model") == "gpt-6-sol"


def test_available_models_picks_richest_client_version(monkeypatch) -> None:
    codex._catalog_cache.clear()
    monkeypatch.setattr(codex, "get_access_token", lambda *_a, **_k: "token")
    monkeypatch.setattr(codex, "CODEX_CLIENT_VERSION", "")

    def fake_get(url, headers):
        if "client_version=1.0.0" in url:
            return 200, {"models": [{"slug": "only-one", "priority": 1}]}
        return 200, {
            "models": [
                {"slug": "best", "priority": 1},
                {"slug": "second", "priority": 2},
            ]
        }

    monkeypatch.setattr(codex, "_get", fake_get)
    assert codex.available_models() == ["best", "second"]
    codex._catalog_cache.clear()


def test_create_response_streams_and_extracts_text(monkeypatch, tmp_path) -> None:
    sse = "\n".join(
        [
            'data: {"type":"response.output_text.delta","delta":"{\\"title\\": "}',
            'data: {"type":"response.output_text.delta","delta":"\\"Cozy\\"}"}',
            'data: {"type":"response.output_text.done","text":"{\\"title\\": \\"Cozy\\"}"}',
        ]
    )
    captured: dict[str, object] = {}

    def fake_stream(url, payload, headers, timeout):
        captured["payload"] = json.loads(payload)
        captured["accept"] = headers.get("Accept")
        return sse

    monkeypatch.setattr(codex, "_post_stream", fake_stream)
    monkeypatch.setattr(codex, "get_access_token", lambda *_a, **_k: "token")
    monkeypatch.setattr(codex, "available_models", lambda *_a, **_k: ["gpt-5.4-mini"])

    out = codex.create_response(
        "gpt-5.4-mini",
        "Return JSON",
        [{"type": "input_text", "text": "hello"}],
        auth_path=tmp_path / "auth.json",
    )
    assert out == '{"title": "Cozy"}'
    assert isinstance(captured["payload"], dict)
    assert captured["payload"]["stream"] is True
    assert captured["accept"] == "text/event-stream"
