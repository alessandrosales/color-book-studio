"""Configuration models and loading."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class CaptionConfig(BaseModel):
    enabled: bool = False
    style: Literal["balloon", "caption"] = "balloon"
    position: Literal["top", "bottom"] = "bottom"
    font_family: Literal["sans", "serif", "mono"] = "sans"
    bold: bool = False
    font_size_ratio: float = Field(default=0.032, ge=0.01, le=0.08)
    band_ratio: float = Field(default=0.16, ge=0.08, le=0.35)


class ProcessingConfig(BaseModel):
    threshold: Literal["adaptive", "otsu", "fixed"] = "adaptive"
    fixed_threshold: int = Field(default=160, ge=0, le=255)
    denoise: bool = True
    min_component_px: int = Field(default=24, ge=0)
    crop_to_content: bool = True
    target_inner_margin_ratio: float = Field(default=0.06, ge=0.0, le=0.4)
    max_upscale: float = Field(default=2.0, ge=1.0, le=4.0)


PageSizeName = Literal[
    "letter_8.5x11", "a4", "square_8x8", "square_8.5x8.5", "6x9", "8x10"
]

PAGE_SIZES_IN: dict[str, tuple[float, float]] = {
    "letter_8.5x11": (8.5, 11.0),
    "a4": (8.27, 11.69),
    "square_8x8": (8.0, 8.0),
    "square_8.5x8.5": (8.5, 8.5),
    "6x9": (6.0, 9.0),
    "8x10": (8.0, 10.0),
}


class BookConfig(BaseModel):
    title: str = "Untitled Coloring Book"
    subtitle: str = ""
    author: str = ""
    language: str = "en"

    page_size: PageSizeName = "letter_8.5x11"
    margin_inner_in: float = Field(default=0.75, ge=0.0)
    margin_outer_in: float = Field(default=0.5, ge=0.0)
    margin_top_in: float = Field(default=0.5, ge=0.0)
    margin_bottom_in: float = Field(default=0.5, ge=0.0)
    bleed_in: float = Field(default=0.125, ge=0.0, le=0.5)
    dpi: int = Field(default=300, ge=150, le=600)

    theme: str = ""
    audience: str = ""
    style: str = "clean line art, thick outlines, large areas to color"
    prompt_suffix: str = "white background, no shading, no text, no color, black lines only"
    negative_prompt: str = "color, gradient, shading, gray, text, watermark, noise"

    pages: int = Field(default=24, ge=1, le=500)
    include_title_page: bool = True
    include_belongs_to_page: bool = False
    include_color_test_page: bool = False
    blank_verso: bool = False
    provider: str = "openai"
    model: str = "gpt-image-1"
    candidates: int = Field(default=1, ge=1, le=4)
    seed: int | None = None

    processing: ProcessingConfig = Field(default_factory=ProcessingConfig)
    caption: CaptionConfig = Field(default_factory=CaptionConfig)

    @field_validator("theme")
    @classmethod
    def _require_theme(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("theme must not be empty")
        return v.strip()

    @property
    def trim_size_in(self) -> tuple[float, float]:
        return PAGE_SIZES_IN[self.page_size]

    @property
    def page_size_pt(self) -> tuple[float, float]:
        w, h = self.trim_size_in
        return (w * 72.0, h * 72.0)

    @property
    def bleed_size_pt(self) -> tuple[float, float]:
        w, h = self.page_size_pt
        return (w + 2 * self.bleed_in * 72.0, h + 2 * self.bleed_in * 72.0)

    @property
    def image_px(self) -> tuple[int, int]:
        """Full-bleed image size in pixels at the configured DPI."""
        w_in, h_in = self.trim_size_in
        b = self.bleed_in
        return (
            round((w_in + 2 * b) * self.dpi),
            round((h_in + 2 * b) * self.dpi),
        )

    @property
    def safe_area_in(self) -> tuple[float, float]:
        """Usable area (trim minus margins) in inches."""
        w, h = self.trim_size_in
        return (w - self.margin_inner_in - self.margin_outer_in,
                h - self.margin_top_in - self.margin_bottom_in)


class EnvSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CBG_", env_file=".env", extra="ignore")

    provider: str | None = None
    model: str | None = None


def default_book_config_path() -> Path:
    override = os.getenv("CBG_CONFIG")
    if override:
        return Path(override).expanduser()
    candidate = Path(__file__).resolve().parents[3]
    if (candidate / "pyproject.toml").exists():
        return candidate / "config" / "book.yaml"
    return Path("config/book.yaml")


def load_book_config(path: str | Path) -> BookConfig:
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return BookConfig.model_validate(data)


def load_env_settings() -> EnvSettings:
    return EnvSettings()
