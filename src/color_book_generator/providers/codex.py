"""ChatGPT Plus / Codex provider using the ChatGPT subscription plan."""

from __future__ import annotations

import base64

from PIL import Image

from color_book_generator.auth.codex import (
    DEFAULT_CODEX_MODEL,
    CodexAuthError,
    create_image,
    normalize_codex_model,
)
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



@register("codex")
class CodexProvider(ImageProvider):
    name = "codex"
    default_model = DEFAULT_CODEX_MODEL

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
        model = normalize_codex_model(self.model)
        if negative_prompt:
            prompt = f"{prompt}\n\nAvoid: {negative_prompt}"
        image_urls: list[str] = []
        if style_reference is not None:
            prompt = f"{prompt}\n\n{style_reference_note()}"
            image_urls.append(
                "data:image/png;base64,"
                + base64.b64encode(style_reference).decode("ascii")
            )
        if references:
            note = character_reference_note(
                [name for name, _data in references], after_style=style_reference is not None
            )
            prompt = f"{prompt}\n\n{note}"
            image_urls.extend(
                "data:image/png;base64," + base64.b64encode(data).decode("ascii")
                for _name, data in references
            )
        w, h = pick_size(target_size, _SIZES)
        try:
            data = create_image(
                prompt, image_urls=image_urls or None, model=model, size=f"{w}x{h}"
            )
        except CodexAuthError as exc:
            raise ProviderError(str(exc)) from exc
        return self._from_bytes(data)
