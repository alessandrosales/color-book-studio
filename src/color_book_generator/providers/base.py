"""Image provider abstraction and registry."""

from __future__ import annotations

import io
from abc import ABC, abstractmethod
from collections.abc import Callable

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
