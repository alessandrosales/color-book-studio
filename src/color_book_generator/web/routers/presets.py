"""Presets catalog API routes (built-in styles by category + custom styles)."""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from color_book_generator.config import CaptionConfig, PageSizeName, ProcessingConfig
from color_book_generator.presets import (
    CATEGORY_META,
    PresetCategory,
    StoryMode,
    StylePreset,
    builtin_presets,
)
from color_book_generator.web import store

router = APIRouter(prefix="/api/presets", tags=["presets"])


class CategoryView(BaseModel):
    id: str
    name: str
    tagline: str
    count: int


class PresetCreate(BaseModel):
    id: str | None = None
    name: str
    category: PresetCategory = "custom"
    tagline: str = ""
    description: str = ""
    badge: str = "Personalizado"
    page_size: PageSizeName = "square_8.5x8.5"
    context_prompt: str
    negative_prompt: str = (
        "color, gradient, shading, gray tones, halftone, screentone, watermark, text"
    )
    character_sheet_prompt: str = (
        "Character model sheet on pure white background: front view, side view, and 2 expressions. "
        "Clean black-and-white coloring book line art, zero shading."
    )
    concept_prompt: str = ""
    default_mode: StoryMode = "collection"
    cast: int = Field(default=2, ge=0, le=4)
    processing: ProcessingConfig = Field(default_factory=ProcessingConfig)
    caption: CaptionConfig = Field(default_factory=CaptionConfig)
    blank_verso: bool = False
    include_title_page: bool = True
    include_belongs_to_page: bool = True
    include_color_test_page: bool = True


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug or "custom_style"


@router.get("", response_model=list[StylePreset])
def list_presets() -> list[StylePreset]:
    return [*builtin_presets(), *store.load_custom_presets()]


@router.get("/categories", response_model=list[CategoryView])
def list_categories() -> list[CategoryView]:
    presets = [*builtin_presets(), *store.load_custom_presets()]
    counts: dict[str, int] = {}
    for preset in presets:
        counts[preset.category] = counts.get(preset.category, 0) + 1
    views = [
        CategoryView(
            id=meta["id"],
            name=meta["name"],
            tagline=meta["tagline"],
            count=counts.get(meta["id"], 0),
        )
        for meta in CATEGORY_META
    ]
    if counts.get("custom"):
        views.append(
            CategoryView(
                id="custom",
                name="Personalizados",
                tagline="Estilos que você salvou.",
                count=counts["custom"],
            )
        )
    return views


@router.post("", response_model=StylePreset, status_code=201)
def save_preset(body: PresetCreate) -> StylePreset:
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="informe um nome para o preset")
    if not body.context_prompt.strip():
        raise HTTPException(status_code=400, detail="informe o prompt de estilo do preset")

    builtin_ids = {p.id for p in builtin_presets()}
    pid = _slugify(body.id or name)
    if pid in builtin_ids:
        pid = f"{pid}_custom"

    preset = StylePreset(
        id=pid,
        name=name,
        category=body.category,
        tagline=body.tagline.strip() or "Estilo personalizado salvo no estúdio",
        description=body.description.strip(),
        badge=body.badge.strip() or "Personalizado",
        page_size=body.page_size,
        context_prompt=body.context_prompt.strip(),
        negative_prompt=body.negative_prompt.strip(),
        character_sheet_prompt=body.character_sheet_prompt.strip(),
        concept_prompt=body.concept_prompt.strip(),
        default_mode=body.default_mode,
        cast=body.cast,
        processing=body.processing,
        caption=body.caption,
        blank_verso=body.blank_verso,
        include_title_page=body.include_title_page,
        include_belongs_to_page=body.include_belongs_to_page,
        include_color_test_page=body.include_color_test_page,
        builtin=False,
    )
    custom = [p for p in store.load_custom_presets() if p.id != pid]
    custom.append(preset)
    store.save_custom_presets(custom)
    return preset


@router.delete("/{preset_id}", status_code=204)
def delete_preset(preset_id: str) -> None:
    builtin_ids = {p.id for p in builtin_presets()}
    if preset_id in builtin_ids:
        raise HTTPException(status_code=400, detail="presets padrao nao podem ser removidos")
    custom = store.load_custom_presets()
    remaining = [p for p in custom if p.id != preset_id]
    if len(remaining) == len(custom):
        raise HTTPException(status_code=404, detail="preset nao encontrado")
    store.save_custom_presets(remaining)
