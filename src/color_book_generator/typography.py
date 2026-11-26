"""Detect the caption font style from a text-style reference image.

The studio lets authors upload a picture of the text style they want. The image
is a typography sample, so we ask the connected text model to map it to the
fonts the caption renderer supports (sans / serif / mono) plus weight.
"""

from __future__ import annotations

import base64
import json
import os
from typing import Literal

from pydantic import BaseModel, ValidationError

from color_book_generator.auth import codex as codex_auth

FontFamily = Literal["sans", "serif", "mono"]

_INSTRUCTIONS = (
    "You are a typography analyst for a children's coloring book. "
    "Return strict JSON only, with no prose."
)
_PROMPT = (
    "Analyze the text in this reference image. Pick the caption font family that "
    "best matches it: 'sans' for clean sans-serif letters, 'serif' when letters "
    "have visible serifs, or 'mono' for fixed-width/typewriter letters. Also say "
    "whether the text is bold. Return exactly: "
    '{"font_family": "sans|serif|mono", "bold": true|false}'
)


class TypographyStyle(BaseModel):
    font_family: FontFamily
    bold: bool = False


class TypographyDetectionError(RuntimeError):
    """Raised when the reference typography cannot be detected."""


def _parse(raw: str) -> TypographyStyle:
    text = raw.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        text = text[start : end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise TypographyDetectionError("resposta de tipografia invalida") from exc
    try:
        return TypographyStyle.model_validate(data)
    except ValidationError as exc:
        raise TypographyDetectionError("resposta de tipografia invalida") from exc


def _data_url(image: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(image).decode("ascii")


def _codex(image: bytes, model: str | None) -> str:
    resolved = codex_auth.resolve_codex_model(model)
    return codex_auth.create_response(
        model=resolved,
        instructions=_INSTRUCTIONS,
        content=[
            {"type": "input_text", "text": _PROMPT},
            {"type": "input_image", "image_url": _data_url(image)},
        ],
    )


def _openai(image: bytes, model: str | None) -> str:
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover
        raise TypographyDetectionError(
            "extra openai nao instalado: uv sync --extra openai"
        ) from exc
    if not os.getenv("OPENAI_API_KEY"):
        raise TypographyDetectionError("OPENAI_API_KEY nao configurada")
    text_model = (
        model
        if model and not any(key in model.lower() for key in ("image", "dall-e", "flux"))
        else "gpt-4o-mini"
    )
    client = OpenAI()
    try:
        resp = client.chat.completions.create(
            model=text_model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _INSTRUCTIONS},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _PROMPT},
                        {"type": "image_url", "image_url": {"url": _data_url(image)}},
                    ],
                },
            ],
        )
    except Exception as exc:  # pragma: no cover - network errors
        raise TypographyDetectionError(f"falha ao detectar tipografia: {exc}") from exc
    content = resp.choices[0].message.content if resp.choices else None
    if not isinstance(content, str) or not content.strip():
        raise TypographyDetectionError("o modelo nao retornou a tipografia")
    return content


def _gemini(image: bytes, model: str | None) -> str:
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:  # pragma: no cover
        raise TypographyDetectionError(
            "extra gemini nao instalado: uv sync --extra gemini"
        ) from exc
    if not os.getenv("GEMINI_API_KEY"):
        raise TypographyDetectionError("GEMINI_API_KEY nao configurada")
    client = genai.Client()
    try:
        resp = client.models.generate_content(
            model=model or "gemini-2.5-flash",
            contents=[
                _PROMPT,
                types.Part.from_bytes(data=image, mime_type="image/png"),
            ],
        )
    except Exception as exc:  # pragma: no cover - network errors
        raise TypographyDetectionError(f"falha ao detectar tipografia: {exc}") from exc
    text = getattr(resp, "text", None)
    if not isinstance(text, str) or not text.strip():
        raise TypographyDetectionError("o modelo nao retornou a tipografia")
    return text


def detect_typography(
    image: bytes,
    *,
    provider: str = "",
    model: str | None = None,
) -> TypographyStyle:
    """Return the caption font style that best matches the reference image."""
    preferred = provider.strip().lower()
    if preferred == "codex" and codex_auth.is_authenticated():
        return _parse(_codex(image, model))
    if preferred == "openai" and os.getenv("OPENAI_API_KEY"):
        return _parse(_openai(image, model))
    if preferred == "gemini" and os.getenv("GEMINI_API_KEY"):
        return _parse(_gemini(image, model))

    if codex_auth.is_authenticated():
        return _parse(_codex(image, model))
    if os.getenv("OPENAI_API_KEY"):
        return _parse(_openai(image, model))
    if os.getenv("GEMINI_API_KEY"):
        return _parse(_gemini(image, model))

    raise TypographyDetectionError(
        "Nenhum provedor de texto autenticado para detectar a fonte. "
        "Conecte o ChatGPT/Codex ou configure OPENAI_API_KEY / GEMINI_API_KEY."
    )
