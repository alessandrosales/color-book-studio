"""ChatGPT Plus / Codex device-code authentication and image generation.

Ported from the ``videoai`` project's ``openai_codex`` module. Credentials are
stored only in a private local file (mode 0600) and refreshed automatically.
"""

from __future__ import annotations

import base64
import json
import os
import time
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
DEVICE_CODE_URL = "https://auth.openai.com/api/accounts/deviceauth/usercode"
DEVICE_TOKEN_URL = "https://auth.openai.com/api/accounts/deviceauth/token"
TOKEN_URL = "https://auth.openai.com/oauth/token"
DEVICE_LOGIN_URL = "https://auth.openai.com/codex/device"
CODEX_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"
CODEX_MODELS_URL = "https://chatgpt.com/backend-api/codex/models"
CODEX_CLIENT_VERSION = os.getenv("CBG_CODEX_CLIENT_VERSION", "").strip()
CLIENT_VERSION_CANDIDATES = ["1.0.0", "0.180.0", "0.162.1", "0.130.0"]
MODELS_CACHE_SECONDS = 300
REFRESH_SKEW_SECONDS = 120
USER_AGENT = "ColorBookGenerator/0.1"
ORIGINATOR = "color-book-generator"

# Offline fallback when live catalog discovery is unavailable. Live discovery wins.
DEFAULT_CODEX_MODELS = [
    "gpt-5.4-mini",
    "gpt-5.4",
    "gpt-5.5",
    "gpt-5.6-luna",
    "gpt-5.6-terra",
    "gpt-5.6-sol",
    "gpt-6-luna",
    "gpt-6-sol",
    "gpt-6.1-terra",
    "gpt-6.1-sol",
    "gpt-6.2-astra",
    "gpt-6.2-sol",
]

DEFAULT_CODEX_MODEL = os.getenv("CBG_CODEX_MODEL", DEFAULT_CODEX_MODELS[0])


def _cost_tier(slug: str) -> tuple[int, int]:
    """Lower tuple = cheaper/lighter model."""
    low = slug.lower()
    if any(tok in low for tok in ("nano", "spark", "lite", "flash")):
        return (0, len(low))
    if "mini" in low:
        return (1, len(low))
    if any(tok in low for tok in ("luna", "fast", "small")):
        return (2, len(low))
    if "terra" in low:
        return (3, len(low))
    return (4, len(low))


def cheapest_codex_model(slugs: list[str] | None = None) -> str:
    """Return the lowest-cost model from a catalog list (or default cheapest)."""
    items = [s for s in (slugs or []) if isinstance(s, str) and s.strip()]
    if not items:
        return DEFAULT_CODEX_MODEL
    # Stable sort by cost tier, preferring later (typically lighter) items on ties
    ranked = sorted(
        enumerate(items),
        key=lambda pair: (_cost_tier(pair[1])[0], -pair[0]),
    )
    return ranked[0][1]
DEFAULT_IMAGE_MODEL = os.getenv("CBG_CODEX_IMAGE_MODEL", "gpt-image-2")
DEFAULT_IMAGE_QUALITY = os.getenv("CBG_CODEX_IMAGE_QUALITY", "medium")
FALLBACK_SIZE = "1024x1024"
FALLBACK_QUALITY = "medium"

# Model names that belong to the OpenAI Images API rather than the Codex agent.
IMAGE_TOOL_MODELS = {"", "gpt-image-1", "gpt-image-2", "dall-e-3"}

_catalog_cache: dict[str, tuple[float, list[str]]] = {}


class CodexAuthError(RuntimeError):
    """Raised when the local ChatGPT Plus session is missing or expired."""


class CodexRequestError(CodexAuthError):
    """Raised when the Codex backend rejects a generation request."""

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status = status


def normalize_codex_model(model: str | None) -> str:
    """Map image-tool model names to the Codex orchestrator model."""
    if model is None or model.strip() in IMAGE_TOOL_MODELS:
        return DEFAULT_CODEX_MODEL
    return model.strip()


def default_auth_path() -> Path:
    override = os.getenv("CBG_CODEX_AUTH")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "color-book-generator" / "openai_codex_auth.json"


def _post_json(url: str, payload: dict[str, str]) -> tuple[int, dict[str, Any]]:
    return _post(
        url,
        json.dumps(payload).encode(),
        {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )


def _post_form(url: str, payload: dict[str, str]) -> tuple[int, dict[str, Any]]:
    return _post(
        url,
        urlencode(payload).encode(),
        {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )


def _get(url: str, headers: dict[str, str], timeout: int = 10) -> tuple[int, Any]:
    request = Request(url, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode()
            return response.status, json.loads(raw) if raw else {}
    except (HTTPError, URLError, ValueError):
        return 0, {}


def _post(url: str, payload: bytes, headers: dict[str, str]) -> tuple[int, dict[str, Any]]:
    request = Request(url, data=payload, headers=headers, method="POST")
    try:
        with urlopen(request, timeout=20) as response:
            raw = response.read().decode()
            return response.status, json.loads(raw) if raw else {}
    except HTTPError as error:
        raw = error.read().decode()
        try:
            return error.code, json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return error.code, {}
    except URLError as error:
        raise CodexAuthError(
            f"Nao foi possivel conectar ao servico de autenticacao: {error.reason}"
        ) from error


def load_tokens(auth_path: Path | None = None) -> dict[str, str] | None:
    path = auth_path or default_auth_path()
    try:
        with path.open(encoding="utf-8") as file:
            tokens = json.load(file).get("tokens")
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    if not isinstance(tokens, dict) or not tokens.get("access_token"):
        return None
    return {key: value for key, value in tokens.items() if isinstance(value, str)}


def save_tokens(tokens: dict[str, str], auth_path: Path | None = None) -> None:
    path = auth_path or default_auth_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        json.dump(
            {"version": 1, "provider": "openai-codex", "tokens": tokens},
            file,
            indent=2,
        )
        file.write("\n")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def clear_tokens(auth_path: Path | None = None) -> None:
    (auth_path or default_auth_path()).unlink(missing_ok=True)


def token_is_expiring(access_token: str, skew_seconds: int = REFRESH_SKEW_SECONDS) -> bool:
    try:
        payload = access_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        expires_at = json.loads(base64.urlsafe_b64decode(payload))["exp"]
        return int(expires_at) <= time.time() + skew_seconds
    except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return True


def refresh_tokens(tokens: dict[str, str]) -> dict[str, str]:
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        raise CodexAuthError("A sessao ChatGPT Plus expirou. Autentique novamente.")
    status, response = _post_form(
        TOKEN_URL,
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": CLIENT_ID,
        },
    )
    if status != 200 or not response.get("access_token"):
        raise CodexAuthError(
            "Nao foi possivel renovar a sessao ChatGPT Plus. Autentique novamente."
        )
    return {
        "access_token": response["access_token"],
        "refresh_token": response.get("refresh_token", refresh_token),
    }


def get_access_token(auth_path: Path | None = None) -> str:
    tokens = load_tokens(auth_path)
    if tokens is None:
        raise CodexAuthError(
            "ChatGPT Plus nao esta autenticado. Conecte na interface web."
        )
    if token_is_expiring(tokens["access_token"]):
        tokens = refresh_tokens(tokens)
        save_tokens(tokens, auth_path)
    return tokens["access_token"]


def _catalog_headers(access_token: str) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    account_id = _chatgpt_account_id(access_token)
    if account_id:
        headers["ChatGPT-Account-ID"] = account_id
    return headers


def _ranked_slugs(entries: object) -> list[str]:
    sortable: list[tuple[int, str]] = []
    if not isinstance(entries, list):
        return []
    for item in entries:
        if not isinstance(item, dict):
            continue
        slug = item.get("slug")
        if not isinstance(slug, str) or not slug.strip():
            continue
        visibility = item.get("visibility")
        if isinstance(visibility, str) and visibility.strip().lower() in {"hide", "hidden"}:
            continue
        priority = item.get("priority")
        rank = int(priority) if isinstance(priority, (int, float)) else 10_000
        sortable.append((rank, slug.strip()))
    sortable.sort()
    return list(dict.fromkeys(slug for _, slug in sortable))


def available_models(auth_path: Path | None = None, *, refresh: bool = False) -> list[str]:
    """Slugs available to this ChatGPT account, best first (cached)."""
    try:
        access_token = get_access_token(auth_path)
    except CodexAuthError:
        return []
    cache_key = _chatgpt_account_id(access_token) or access_token[-24:]
    now = time.monotonic()
    cached = _catalog_cache.get(cache_key)
    if cached and not refresh and now - cached[0] < MODELS_CACHE_SECONDS:
        return cached[1]

    versions = [CODEX_CLIENT_VERSION] if CODEX_CLIENT_VERSION else CLIENT_VERSION_CANDIDATES
    best: list[str] = []
    for version in versions:
        url = f"{CODEX_MODELS_URL}?client_version={version}"
        status, data = _get(url, _catalog_headers(access_token))
        if status != 200 or not isinstance(data, dict):
            continue
        slugs = _ranked_slugs(data.get("models"))
        if len(slugs) > len(best):
            best = slugs
    if best:
        _catalog_cache[cache_key] = (now, best)
        return best
    return cached[1] if cached else []


def resolve_codex_model(model: str | None, auth_path: Path | None = None) -> str:
    """Return a slug valid for this account, falling back to the top catalog entry."""
    raw = (model or "").strip()
    slugs = available_models(auth_path)
    if not raw or raw in IMAGE_TOOL_MODELS:
        return slugs[0] if slugs else DEFAULT_CODEX_MODEL
    if slugs and raw not in slugs:
        return slugs[0]
    return raw


def _codex_headers(access_token: str) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "User-Agent": USER_AGENT,
        "originator": ORIGINATOR,
    }
    account_id = _chatgpt_account_id(access_token)
    if account_id:
        headers["ChatGPT-Account-ID"] = account_id
    return headers


def _text_payload(
    model: str | None,
    instructions: str,
    content: list[dict[str, str]],
) -> bytes:
    payload: dict[str, Any] = {
        "model": normalize_codex_model(model),
        "store": False,
        "instructions": instructions,
        "input": [{"type": "message", "role": "user", "content": content}],
        "stream": True,
    }
    return json.dumps(payload).encode()


def _extract_response_text(event_stream: str) -> str | None:
    deltas: list[str] = []
    done_text: str | None = None
    completed_text: str | None = None

    for line in event_stream.splitlines():
        if not line.startswith("data:"):
            continue
        raw = line.removeprefix("data:").strip()
        if not raw or raw == "[DONE]":
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue

        etype = event.get("type")
        if etype == "response.output_text.delta" and isinstance(event.get("delta"), str):
            deltas.append(event["delta"])
        elif etype == "response.output_text.done" and isinstance(event.get("text"), str):
            if event["text"].strip():
                done_text = event["text"]
        elif etype == "response.completed" and isinstance(event.get("response"), dict):
            resp = event["response"]
            if isinstance(resp.get("output_text"), str) and resp["output_text"].strip():
                completed_text = resp["output_text"]
            else:
                parts: list[str] = []
                for item in resp.get("output", []):
                    if not isinstance(item, dict) or item.get("type") != "message":
                        continue
                    for part in item.get("content", []):
                        if (
                            isinstance(part, dict)
                            and part.get("type") in {"output_text", "text"}
                            and isinstance(part.get("text"), str)
                        ):
                            parts.append(part["text"])
                if parts:
                    completed_text = "".join(parts)

    if done_text:
        return done_text
    if completed_text:
        return completed_text
    joined = "".join(deltas).strip()
    return joined or None


def create_response(
    model: str,
    instructions: str,
    content: list[dict[str, str]],
    auth_path: Path | None = None,
) -> str:
    """Text-only helper for the Codex responses endpoint (SSE streaming)."""
    access_token = get_access_token(auth_path)
    resolved = resolve_codex_model(model, auth_path)
    slugs = available_models(auth_path)
    candidates = [resolved, *[slug for slug in slugs if slug != resolved][:2]]
    headers = _codex_headers(access_token)

    event_stream = ""
    last_error: CodexRequestError | None = None
    for candidate in candidates:
        try:
            event_stream = _post_stream(
                CODEX_RESPONSES_URL,
                _text_payload(candidate, instructions, content),
                headers,
                timeout=120,
            )
            break
        except CodexRequestError as error:
            if error.status != 400:
                raise
            last_error = error

    if not event_stream:
        if last_error:
            raise last_error
        raise CodexAuthError("O ChatGPT Plus nao retornou resposta para a solicitacao.")

    text = _extract_response_text(event_stream)
    if text:
        return text
    raise CodexAuthError("O ChatGPT Plus nao retornou conteudo de texto para a solicitacao.")


def _post_stream(url: str, payload: bytes, headers: dict[str, str], timeout: int) -> str:
    request = Request(url, data=payload, headers=headers, method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:
            return cast(str, response.read().decode())
    except HTTPError as error:
        body = error.read().decode(errors="replace").strip()
        detail = f": {body[:400]}" if body else ""
        raise CodexRequestError(
            f"A geracao de imagem foi recusada pelo ChatGPT Plus ({error.code}){detail}",
            error.code,
        ) from error
    except URLError as error:
        raise CodexAuthError(
            f"Nao foi possivel gerar a imagem: {error.reason}"
        ) from error


def _image_payload(
    prompt: str,
    image_urls: list[str] | None,
    model: str | None,
    image_model: str | None,
    size: str,
    quality: str,
) -> bytes:
    content: list[dict[str, str]] = [{"type": "input_text", "text": prompt}]
    content.extend(
        {"type": "input_image", "image_url": image_url}
        for image_url in (image_urls or [])
    )
    payload: dict[str, Any] = {
        "model": normalize_codex_model(model),
        "store": False,
        "instructions": "Use the image_generation tool to create the requested PNG image.",
        "input": [{"type": "message", "role": "user", "content": content}],
        "tools": [
            {
                "type": "image_generation",
                "model": image_model or DEFAULT_IMAGE_MODEL,
                "size": size,
                "quality": quality,
                "output_format": "png",
                "background": "opaque",
                "partial_images": 0,
            }
        ],
        "stream": True,
    }
    return json.dumps(payload).encode()


def create_image(
    prompt: str,
    image_urls: list[str] | None = None,
    auth_path: Path | None = None,
    model: str | None = None,
    image_model: str | None = None,
    size: str = "1024x1024",
    quality: str = DEFAULT_IMAGE_QUALITY,
) -> bytes:
    """Generate one PNG using the ChatGPT Plus image_generation tool."""
    access_token = get_access_token(auth_path)
    resolved = resolve_codex_model(model, auth_path)
    slugs = available_models(auth_path)
    candidates = [resolved, *[slug for slug in slugs if slug != resolved][:2]]
    headers = _codex_headers(access_token)

    attempts: list[tuple[str, str, str]] = []
    for candidate in candidates:
        attempts.append((candidate, size, quality))
        if size != FALLBACK_SIZE or quality != FALLBACK_QUALITY:
            attempts.append((candidate, FALLBACK_SIZE, FALLBACK_QUALITY))

    event_stream = ""
    last_error: CodexRequestError | None = None
    for candidate, request_size, request_quality in attempts:
        try:
            event_stream = _post_stream(
                CODEX_RESPONSES_URL,
                _image_payload(
                    prompt, image_urls, candidate, image_model, request_size, request_quality
                ),
                headers,
                timeout=180,
            )
            break
        except CodexRequestError as error:
            if error.status != 400:
                raise
            last_error = error
    if not event_stream:
        if last_error:
            raise last_error
        raise CodexAuthError("O ChatGPT Plus nao retornou resposta para a geracao.")
    image_b64 = _extract_generated_image(event_stream)
    if not image_b64:
        raise CodexAuthError("O ChatGPT Plus nao retornou a imagem solicitada.")
    try:
        return base64.b64decode(
            image_b64.removeprefix("data:image/png;base64,"), validate=True
        )
    except ValueError as error:
        raise CodexAuthError("A imagem retornada nao e um PNG valido.") from error


def _extract_generated_image(event_stream: str) -> str | None:
    for line in event_stream.splitlines():
        if not line.startswith("data:"):
            continue
        try:
            image_b64 = _find_image_result(json.loads(line.removeprefix("data:").strip()))
        except json.JSONDecodeError:
            continue
        if image_b64:
            return image_b64
    return None


def _find_image_result(value: Any) -> str | None:
    if isinstance(value, dict):
        if value.get("type") == "image_generation_call" and value.get("status") == "completed":
            result = value.get("result")
            if isinstance(result, str):
                return result
        for nested in value.values():
            result = _find_image_result(nested)
            if result:
                return result
    elif isinstance(value, list):
        for item in value:
            result = _find_image_result(item)
            if result:
                return result
    return None


def _chatgpt_account_id(access_token: str) -> str | None:
    try:
        payload = access_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        account_id = claims.get("https://api.openai.com/auth.chatgpt_account_id")
        return account_id if isinstance(account_id, str) else None
    except (IndexError, TypeError, ValueError, json.JSONDecodeError):
        return None


def authenticate(
    auth_path: Path | None = None,
    on_code: Callable[[str, str], None] | None = None,
    on_wait: Callable[[], None] | None = None,
    open_browser: Callable[[str], bool] = webbrowser.open,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Run the device-code flow, blocking until the user authorizes."""
    status, device_code = _post_json(DEVICE_CODE_URL, {"client_id": CLIENT_ID})
    user_code = device_code.get("user_code")
    device_auth_id = device_code.get("device_auth_id")
    if status != 200 or not user_code or not device_auth_id:
        raise CodexAuthError("Nao foi possivel iniciar a autenticacao ChatGPT Plus.")

    if on_code:
        on_code(str(user_code), DEVICE_LOGIN_URL)
    print(f"\nAbra: {DEVICE_LOGIN_URL}\nCodigo: {user_code}")
    open_browser(DEVICE_LOGIN_URL)

    interval = max(int(device_code.get("interval", 3)), 3)
    deadline = time.monotonic() + 15 * 60
    authorization: dict[str, Any] = {}
    while time.monotonic() < deadline:
        if on_wait:
            on_wait()
        status, authorization = _post_json(
            DEVICE_TOKEN_URL,
            {"device_auth_id": device_auth_id, "user_code": user_code},
        )
        if (
            status == 200
            and authorization.get("authorization_code")
            and authorization.get("code_verifier")
        ):
            break
        if status not in (403, 404):
            raise CodexAuthError("A autorizacao ChatGPT Plus foi recusada ou expirou.")
        sleep(interval)
    else:
        raise CodexAuthError("A autorizacao ChatGPT Plus expirou. Tente novamente.")

    status, tokens = _post_form(
        TOKEN_URL,
        {
            "grant_type": "authorization_code",
            "code": authorization["authorization_code"],
            "redirect_uri": "https://auth.openai.com/deviceauth/callback",
            "client_id": CLIENT_ID,
            "code_verifier": authorization["code_verifier"],
        },
    )
    if status != 200 or not tokens.get("access_token"):
        raise CodexAuthError("Nao foi possivel concluir a autenticacao ChatGPT Plus.")
    save_tokens(
        {
            "access_token": tokens["access_token"],
            "refresh_token": tokens.get("refresh_token", ""),
        },
        auth_path,
    )


def is_authenticated(auth_path: Path | None = None) -> bool:
    return load_tokens(auth_path) is not None
