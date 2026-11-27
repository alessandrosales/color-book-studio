"""OpenAI Images provider (gpt-image-1 / dall-e-3)."""

from __future__ import annotations

import base64
import os
from typing import Any

from PIL import Image

from color_book_generator.providers.base import (
    CharacterReference,
    ImageProvider,
    ProviderError,
    character_reference_note,
    pick_size,
    register,
    style_reference_note,
)

_SIZES: dict[str, tuple[int, int]] = {
    "square": (1024, 1024),
    "portrait": (1024, 1536),
    "landscape": (1536, 1024),
}


@register("openai")
class OpenAIProvider(ImageProvider):
    name = "openai"
    default_model = "gpt-image-1"

    def _client(self) -> Any:
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover
            raise ProviderError(
                "openai extra not installed: uv sync --extra openai"
            ) from exc
        if not os.getenv("OPENAI_API_KEY"):
            raise ProviderError("OPENAI_API_KEY is not set")
        return OpenAI()

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
        w, h = pick_size(target_size, _SIZES)
        size = f"{w}x{h}"
        full_prompt = prompt if not negative_prompt else f"{prompt}\n\nAvoid: {negative_prompt}"
        client = self._client()

        if references or style_reference is not None:
            if self.model != "gpt-image-1":
                raise ProviderError(
                    "reference images require model gpt-image-1 for the openai provider"
                )
            files: list[tuple[str, bytes, str]] = []
            if style_reference is not None:
                full_prompt = f"{full_prompt}\n\n{style_reference_note()}"
                files.append(("style.png", style_reference, "image/png"))
            if references:
                note = character_reference_note(
                    [name for name, _data in references], after_style=style_reference is not None
                )
                full_prompt = f"{full_prompt}\n\n{note}"
                files.extend(
                    (f"{name}.png", data, "image/png") for name, data in references
                )
            try:
                edit = client.images.edit(
                    model=self.model,
                    image=files,
                    prompt=full_prompt,
                    size=size,
                    n=1,
                )
            except Exception as exc:  # pragma: no cover - network errors
                raise ProviderError(f"openai edit failed: {exc}") from exc
            return self._from_bytes(base64.b64decode(edit.data[0].b64_json))

        kwargs: dict[str, object] = {
            "model": self.model,
            "prompt": full_prompt,
            "size": size,
            "n": 1,
        }
        if self.model == "gpt-image-1":
            kwargs["background"] = "opaque"
            kwargs["quality"] = "high"

        try:
            result = client.images.generate(**kwargs)
        except Exception as exc:  # pragma: no cover - network errors
            raise ProviderError(f"openai generation failed: {exc}") from exc

        item = result.data[0]
        b64 = getattr(item, "b64_json", None)
        if b64:
            return self._from_bytes(base64.b64decode(b64))
        raise ProviderError("openai returned no image data")
