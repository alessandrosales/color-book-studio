"""Image provider abstraction and registry."""

from __future__ import annotations

import io
import math
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from PIL import Image

CharacterReference = tuple[str, bytes]


class ProviderError(RuntimeError):
    pass


class ImageProvider(ABC):
    """A backend capable of producing a single line-art image."""

    name: str = "base"
    default_model: str = ""

    def __init__(self, model: str | None = None) -> None:
        self.model = model or self.default_model

    @abstractmethod
    def generate(
        self,
        prompt: str,
        *,
        negative_prompt: str = "",
        target_size: tuple[int, int] = (2048, 2048),
        seed: int | None = None,
        references: list[CharacterReference] | None = None,
        style_reference: bytes | None = None,
    ) -> Image.Image:
        """Return a PIL image for the prompt, optionally guided by references."""

    @staticmethod
    def _from_bytes(data: bytes) -> Image.Image:
        img = Image.open(io.BytesIO(data))
        img.load()
        return img.convert("RGB")


_REGISTRY: dict[str, Callable[..., ImageProvider]] = {}


def register(name: str) -> Callable[[Callable[..., ImageProvider]], Callable[..., ImageProvider]]:
    def deco(factory: Callable[..., ImageProvider]) -> Callable[..., ImageProvider]:
        _REGISTRY[name] = factory
        return factory

    return deco


def available_providers() -> list[str]:
    return sorted(_REGISTRY)


def get_provider(name: str, model: str | None = None) -> ImageProvider:
    key = name.lower().strip()
    if key not in _REGISTRY:
        raise ProviderError(
            f"unknown provider {name!r}; available: {', '.join(available_providers())}"
        )
    return _REGISTRY[key](model=model)


def aspect_of(target: tuple[int, int]) -> float:
    w, h = target
    return w / h


def pick_size(target: tuple[int, int], options: dict[str, tuple[int, int]]) -> tuple[int, int]:
    """Pick the supported size whose aspect ratio best matches the target."""
    want = aspect_of(target)
    return min(options.values(), key=lambda s: abs(aspect_of(s) - want))


# Largest accepted gap between the generated and the page aspect ratio (log scale, about 25%).
ASPECT_TOLERANCE = 0.22


def orientation_hint(target: tuple[int, int], *, strong: bool = False) -> str:
    """Prompt line that pins the canvas orientation, whatever the reference images look like."""
    w, h = target
    ratio = w / h
    if ratio < 0.92:
        shape = "PORTRAIT (vertical, clearly taller than wide)"
    elif ratio > 1.08:
        shape = "LANDSCAPE (horizontal, clearly wider than tall)"
    else:
        shape = "SQUARE"
    hint = (
        f"Canvas format: {shape}, width to height ratio about {ratio:.2f}. Compose the whole "
        "scene for this exact frame so the artwork fills it edge to edge with no empty bands, "
        "whatever the aspect ratio of any reference image."
    )
    if strong:
        hint = f"IMPORTANT: the previous attempt had the wrong orientation. {hint}"
    return hint


def aspect_mismatch(
    img: Image.Image, target: tuple[int, int], tolerance: float = ASPECT_TOLERANCE
) -> bool:
    got = img.width / img.height
    want = target[0] / target[1]
    return abs(math.log(got / want)) > tolerance


def generate_matching(
    provider: ImageProvider,
    prompt: str,
    *,
    target_size: tuple[int, int],
    attempts: int = 3,
    **kwargs: Any,
) -> Image.Image:
    """Generate an image and retry when its orientation does not match the page."""
    seed = kwargs.get("seed")
    last: Image.Image | None = None
    for attempt in range(max(1, attempts)):
        if seed is not None:
            kwargs["seed"] = seed + attempt
        hint = orientation_hint(target_size, strong=attempt > 0)
        img = provider.generate(f"{prompt}\n\n{hint}", target_size=target_size, **kwargs)
        last = img
        if not aspect_mismatch(img, target_size):
            return img
    assert last is not None
    return last
