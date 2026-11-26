"""Google Gemini / Imagen provider."""

from __future__ import annotations

import os
from typing import Any

from PIL import Image

from color_book_generator.providers.base import (
    CharacterReference,
    ImageProvider,
    ProviderError,
    pick_size,
    register,
)

# aspect ratio label -> (w, h)
_AR: dict[str, tuple[int, int]] = {
    "1:1": (1024, 1024),
    "3:4": (896, 1152),
    "4:3": (1152, 896),
    "9:16": (768, 1344),
    "16:9": (1344, 768),
}


@register("gemini")
class GeminiProvider(ImageProvider):
    name = "gemini"
    default_model = "imagen-3.0-generate-002"

    def _client(self) -> Any:
        try:
            from google import genai
        except ImportError as exc:  # pragma: no cover
            raise ProviderError(
                "gemini extra not installed: uv sync --extra gemini"
            ) from exc
        if not os.getenv("GEMINI_API_KEY"):
            raise ProviderError("GEMINI_API_KEY is not set")
        return genai.Client()

    def generate(
        self,
        prompt: str,
        *,
        negative_prompt: str = "",
        target_size: tuple[int, int] = (1024, 1024),
        seed: int | None = None,
        references: list[CharacterReference] | None = None,
        style_reference: bytes | None = None,
    ) -> Image.Image:
        if references or style_reference is not None:
            raise ProviderError(
                "este provedor nao suporta imagens de referencia; use o provedor codex"
            )
        w, h = pick_size(target_size, _AR)
        aspect = next(k for k, v in _AR.items() if v == (w, h))

        client = self._client()
        last_err: Exception | None = None
        try:
            response = client.models.generate_images(
                model=self.model,
                prompt=prompt,
                config={
                    "aspect_ratio": aspect,
                    "negative_prompt": negative_prompt or None,
                    "number_of_images": 1,
                },
            )
            if response.generated_images:
                raw = response.generated_images[0].image.image_bytes
                return self._from_bytes(raw)
            last_err = ProviderError("gemini returned no images")
        except Exception as exc:  # pragma: no cover - network errors
            last_err = exc

        raise ProviderError(f"gemini generation failed: {last_err}")
