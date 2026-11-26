"""Local diffusion provider (diffusers). Heavy: opt-in extra."""

from __future__ import annotations

from typing import Any, cast

from PIL import Image

from color_book_generator.providers.base import (
    CharacterReference,
    ImageProvider,
    ProviderError,
    register,
)

_CACHE: dict[str, object] = {}


@register("local")
class LocalProvider(ImageProvider):
    name = "local"
    default_model = "stabilityai/stable-diffusion-xl-base-1.0"

    def _pipe(self) -> Any:
        if self.model in _CACHE:
            return _CACHE[self.model]
        try:
            import torch
            from diffusers import AutoPipelineForText2Image
        except ImportError as exc:  # pragma: no cover
            raise ProviderError(
                "local extra not installed: uv sync --extra local"
            ) from exc
        if torch.cuda.is_available():
            device = "cuda"
        elif torch.backends.mps.is_available():
            device = "mps"
        else:
            device = "cpu"
        dtype = torch.float16 if device in {"cuda", "mps"} else torch.float32
        pipe = AutoPipelineForText2Image.from_pretrained(
            self.model, torch_dtype=dtype, use_safetensors=True
        ).to(device)
        _CACHE[self.model] = pipe
        return pipe

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
        import torch

        pipe = self._pipe()
        generator = None
        if seed is not None:
            generator = torch.Generator(device="cpu").manual_seed(seed)
        out = pipe(
            prompt=prompt,
            negative_prompt=negative_prompt or None,
            width=1024,
            height=1024,
            num_inference_steps=30,
            guidance_scale=6.0,
            generator=generator,
        )
        return cast(Image.Image, out.images[0].convert("RGB"))
