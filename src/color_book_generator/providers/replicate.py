"""Replicate provider (hosted models such as Flux)."""

from __future__ import annotations

import os
import time

import httpx
from PIL import Image

from color_book_generator.providers.base import (
    CharacterReference,
    ImageProvider,
    ProviderError,
    pick_size,
    register,
)

_AR: dict[str, tuple[int, int]] = {
    "1:1": (1024, 1024),
    "3:4": (896, 1152),
    "4:3": (1152, 896),
    "9:16": (768, 1344),
    "16:9": (1344, 768),
}

_API = "https://api.replicate.com/v1"


@register("replicate")
class ReplicateProvider(ImageProvider):
    name = "replicate"
    default_model = "black-forest-labs/flux-schnell"

    def _token(self) -> str:
        token = os.getenv("REPLICATE_API_TOKEN")
        if not token:
            raise ProviderError("REPLICATE_API_TOKEN is not set")
        return token

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
        payload: dict[str, object] = {
            "input": {
                "prompt": prompt,
                "aspect_ratio": aspect,
                "output_format": "png",
                **({"seed": seed} if seed is not None else {}),
            }
        }
        headers = {
            "Authorization": f"Bearer {self._token()}",
            "Content-Type": "application/json",
            "Prefer": "wait=60",
        }
        with httpx.Client(timeout=120.0) as client:
            resp = client.post(
                f"{_API}/models/{self.model}/predictions", json=payload, headers=headers
            )
            if resp.status_code >= 400:
                raise ProviderError(f"replicate error {resp.status_code}: {resp.text[:300]}")
            data = resp.json()

            deadline = time.time() + 180
            while data.get("status") in {"starting", "processing"}:
                if time.time() > deadline:
                    raise ProviderError("replicate prediction timed out")
                time.sleep(2)
                poll = client.get(data["urls"]["get"], headers=headers)
                data = poll.json()

            if data.get("status") != "succeeded":
                raise ProviderError(f"replicate failed: {data.get('error')}")
            output = data.get("output")
            url = output[0] if isinstance(output, list) else output
            if not isinstance(url, str):
                raise ProviderError("replicate returned no image url")
            img_resp = client.get(url)
            img_resp.raise_for_status()
            return self._from_bytes(img_resp.content)
