"""5-stage book creation studio: style, characters, story, pages, cover/finalization."""

from __future__ import annotations

import asyncio
import io
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from PIL import Image
from pydantic import BaseModel, Field

from color_book_generator.auth.codex import normalize_codex_model
from color_book_generator.config import (
    BookConfig,
    CaptionConfig,
    ProcessingConfig,
    default_book_config_path,
    load_book_config,
)
from color_book_generator.layout.pdf import compose_pdf
from color_book_generator.page_layout import plan_page_layout
from color_book_generator.presets import default_page_layout_for_category, get_preset
from color_book_generator.processing.caption import (
    draw_cover_title,
    render_text_page,
)
from color_book_generator.processing.line_art import (
    fit_color_to_page,
    prepare_page,
    to_line_art,
)
from color_book_generator.prompts import compose_page_prompt, effective_negative_prompt
from color_book_generator.providers import get_provider
from color_book_generator.providers.base import generate_matching
from color_book_generator.story import (
    CharacterBrief,
    ScriptedPage,
    StoryGenerationError,
    StoryMode,
    build_character_sheet_prompt,
    generate_bridge_page,
    generate_full_book_blueprint,
    generate_story_plan,
)
from color_book_generator.typography import detect_typography
from color_book_generator.web import store
from color_book_generator.web.store import (
    BookCharacter,
    ExtraPage,
    PageState,
    SessionState,
)

router = APIRouter(prefix="/api/sessions/{sid}", tags=["studio"])
SID_RE = re.compile(r"^[a-f0-9]{8,32}$")
MAX_IMAGE_UPLOAD_BYTES = 12 * 1024 * 1024
_UPLOAD_CHUNK_BYTES = 64 * 1024


async def read_upload_limited(
    upload: UploadFile, max_bytes: int = MAX_IMAGE_UPLOAD_BYTES
) -> bytes:
    """Read an uploaded file in chunks, aborting if it exceeds max_bytes."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(_UPLOAD_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"imagem maior que {max_bytes // (1024 * 1024)} MB",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _config() -> BookConfig:
    path = default_book_config_path()
    if not path.exists():
        raise HTTPException(status_code=500, detail=f"config not found: {path}")
    return load_book_config(path)


def _session(sid: str) -> SessionState:
    if not SID_RE.match(sid):
        raise HTTPException(status_code=400, detail="invalid session id")
    state = store.load_session(sid)
    if state is None:
        raise HTTPException(status_code=404, detail="session not found")
    store.migrate_referenced_characters(state)
    return state


def _book_character(state: SessionState, cid: str) -> BookCharacter:
    for item in state.characters:
        if item.id == cid:
            return item
    raise HTTPException(status_code=404, detail="personagem nao encontrado neste livro")


class BookCharacterView(BaseModel):
    id: str
    name: str
    description: str
    approved: bool
    sheet_status: str
    error: str | None = None
    image_url: str | None = None


def _character_view(sid: str, item: BookCharacter) -> BookCharacterView:
    img_path = store.session_characters_dir(sid) / item.filename
    has_img = img_path.exists()
    v = int((item.updated_at or item.created_at).timestamp())
    return BookCharacterView(
        id=item.id,
        name=item.name,
        description=item.description,
        approved=item.approved,
        sheet_status=item.sheet_status,
        error=item.error,
        image_url=f"/api/sessions/{sid}/characters/{item.id}/image?v={v}" if has_img else None,
    )


class StyleAnchorView(BaseModel):
    url: str | None
    inherited_global: bool = False
    font_family: str | None = None
    font_bold: bool | None = None
    typography_error: str | None = None


def _style_anchor_view(
    sid: str, state: SessionState | None = None
) -> StyleAnchorView:
    font_family = state.caption_font_family if state is not None else None
    font_bold = state.caption_font_bold if state is not None else None
    local = store.session_style_path(sid)
    if local.exists():
        return StyleAnchorView(
            url=f"/api/sessions/{sid}/style/image?v={int(local.stat().st_mtime)}",
            inherited_global=False,
            font_family=font_family,
            font_bold=font_bold,
        )
    return StyleAnchorView(
        url=None, inherited_global=False, font_family=font_family, font_bold=font_bold
    )


class ExtraPageView(BaseModel):
    id: str
    placement: Literal["front", "back"]
    title: str
    text: str
    prompt: str
    text_align: Literal["left", "center"]
    text_vertical: Literal["top", "center", "bottom"]
    show_border: bool
    illustration_position: Literal["background", "above", "below", "none"]
    illustration_size: Literal["small", "medium", "large"]
    include_in_pdf: bool
    status: str
    error: str | None = None
    updated_at: datetime | None = None
    preview_url: str | None = None
    download_url: str | None = None


def _extra_page_view(sid: str, page: ExtraPage) -> ExtraPageView:
    path = store.extra_page_path(sid, page.id)
    has_image = path.exists()
    version = int((page.updated_at or datetime.now()).timestamp())
    return ExtraPageView(
        id=page.id,
        placement=page.placement,
        title=page.title,
        text=page.text,
        prompt=page.prompt,
        text_align=page.text_align,
        text_vertical=page.text_vertical,
        show_border=page.show_border,
        illustration_position=page.illustration_position,
        illustration_size=page.illustration_size,
        include_in_pdf=page.include_in_pdf,
        status=page.status,
        error=page.error,
        updated_at=page.updated_at,
        preview_url=(
            f"/api/sessions/{sid}/extra-pages/{page.id}/preview?v={version}"
            if has_image
            else None
        ),
        download_url=(
            f"/api/sessions/{sid}/extra-pages/{page.id}/download?v={version}"
            if has_image
            else None
        ),
    )


def _find_extra_page(
    state: SessionState, page_id: str
) -> tuple[ExtraPage, list[ExtraPage]]:
    for collection in (state.front_extra_pages, state.back_extra_pages):
        for page in collection:
            if page.id == page_id:
                return page, collection
    raise HTTPException(status_code=404, detail="pagina extra nao encontrada")


def _render_extra_page(state: SessionState, page: ExtraPage) -> None:
    cfg = _config()
    background_path = store.extra_page_background_path(state.id, page.id)
    background = (
        Image.open(background_path) if background_path.exists() else None
    )
    try:
        image = render_text_page(
            cfg.image_px,
            page.title,
            page.text,
            _session_caption_cfg(state),
            background=background,
            align=page.text_align,
            vertical=page.text_vertical,
            show_border=page.show_border,
            illustration_position=page.illustration_position,
            illustration_size=page.illustration_size,
        )
    finally:
        if background is not None:
            background.close()
    path = store.extra_page_path(state.id, page.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "PNG", dpi=(cfg.dpi, cfg.dpi))
    page.status = "done"
    page.error = None
    page.updated_at = datetime.now()


def _extra_page_images(
    state: SessionState, placement: Literal["front", "back"]
) -> list[Path]:
    pages = (
        state.front_extra_pages if placement == "front" else state.back_extra_pages
    )
    images: list[Path] = []
    for page in pages:
        if not page.include_in_pdf:
            continue
        path = store.extra_page_path(state.id, page.id)
        if not path.exists():
            _render_extra_page(state, page)
        images.append(path)
    return images


class ApplyPresetRequest(BaseModel):
    preset_id: str


@router.post("/apply-preset")
def apply_preset(sid: str, body: ApplyPresetRequest) -> dict[str, object]:
    state = _session(sid)
    preset = get_preset(body.preset_id, store.load_custom_presets())
    if preset is None:
        raise HTTPException(status_code=404, detail="preset nao encontrado")
    state.preset_id = preset.id
    state.category = preset.category
    state.page_size = preset.page_size
    state.page_layout = default_page_layout_for_category(preset.category)
    state.context_prompt = preset.context_prompt
    state.negative_prompt = preset.negative_prompt
    state.processing = preset.processing.model_copy(deep=True)
    state.caption_enabled = preset.caption.enabled
    state.caption_style = preset.caption.style
    state.caption_position = preset.caption.position
    state.caption_font_family = preset.caption.font_family
    state.caption_font_bold = preset.caption.bold
    state.caption_font_size = preset.caption.font_size_ratio
    state.blank_verso = preset.blank_verso
    state.include_title_page = preset.include_title_page
    state.include_belongs_to_page = preset.include_belongs_to_page
    state.include_color_test_page = preset.include_color_test_page
    state.pdf_ready = False
    store.save_session(state)
    return {"ok": True, "preset_id": preset.id, "page_size": state.page_size}


# --- Stage 1: Per-book Style Anchor ---


@router.get("/style", response_model=StyleAnchorView)
def get_session_style(sid: str) -> StyleAnchorView:
    state = _session(sid)
    return _style_anchor_view(sid, state)


@router.post("/style", response_model=StyleAnchorView)
async def upload_session_style(sid: str, image: UploadFile = File(...)) -> StyleAnchorView:
    state = _session(sid)
    data = await read_upload_limited(image)
    if not data:
        raise HTTPException(status_code=400, detail="envie uma imagem")
    try:
        with Image.open(io.BytesIO(data)) as source:
            source.load()
            normalized = source.convert("L" if source.mode in {"L", "1"} else "RGB")
    except Exception as exc:
        raise HTTPException(status_code=400, detail="arquivo nao e uma imagem valida") from exc
    store.session_style_dir(sid).mkdir(parents=True, exist_ok=True)
    normalized.save(store.session_style_path(sid), "PNG")
    return _style_anchor_view(sid, state)


@router.post("/style/detect", response_model=StyleAnchorView)
async def detect_session_style_typography(sid: str) -> StyleAnchorView:
    """Read the reference text sample and apply its font to page captions."""
    state = _session(sid)
    path = store.session_style_path(sid)
    if not path.exists():
        raise HTTPException(
            status_code=400, detail="envie uma amostra de texto antes de detectar a fonte"
        )
    try:
        detected = await asyncio.to_thread(
            detect_typography,
            path.read_bytes(),
            provider=state.provider,
            model=state.model,
        )
    except Exception as exc:
        view = _style_anchor_view(sid, state)
        view.typography_error = str(exc) or "nao foi possivel detectar a fonte"
        return view

    state.caption_font_family = detected.font_family
    state.caption_font_bold = detected.bold
    store.save_session(state)
    return _style_anchor_view(sid, state)


class GenerateStyleRequest(BaseModel):
    sample_subject: str = ""
    provider: str | None = None
    model: str | None = None


@router.post("/style/generate", response_model=StyleAnchorView)
async def generate_session_style(sid: str, body: GenerateStyleRequest) -> StyleAnchorView:
    state = _session(sid)
    cfg = _config()
    provider_name = body.provider or state.provider
    model_name = body.model or state.model
    if provider_name == "codex":
        model_name = normalize_codex_model(model_name)
    subject = body.sample_subject.strip() or (
        "a charming cozy showcase scene demonstrating the exact line weight, "
        "composition balance, and visual identity of this coloring book"
    )
    prompt = f"{state.context_prompt.strip()}\n\nStyle anchor showcase subject: {subject}."
    negative = state.negative_prompt or cfg.negative_prompt
    processing = state.processing or cfg.processing
    category_style_ref = store.load_page_layout_reference_bytes(
        state.category, 1, preset_id=state.preset_id
    )

    def _run() -> Image.Image:
        provider = get_provider(provider_name, model_name)
        img = generate_matching(
            provider,
            prompt,
            target_size=(1024, 1024),
            negative_prompt=negative,
            style_reference=category_style_ref,
        )
        return to_line_art(img, processing)

    try:
        line_art = await asyncio.to_thread(_run)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    store.session_style_dir(sid).mkdir(parents=True, exist_ok=True)
    line_art.save(store.session_style_path(sid), "PNG")
    return _style_anchor_view(sid, state)


@router.delete("/style", status_code=204)
def delete_session_style(sid: str) -> None:
    _session(sid)
    store.clear_session_style(sid)


@router.get("/style/image")
def session_style_image(sid: str) -> FileResponse:
    _session(sid)
    path = store.session_style_path(sid)
    if not path.exists():
        raise HTTPException(status_code=404, detail="estilo do livro nao definido")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})


# --- Stage 5: Cover & Extra Text Pages ---


class CoverView(BaseModel):
    status: str
    error: str | None = None
    preview_url: str | None = None
    download_url: str | None = None


def _cover_view(state: SessionState) -> CoverView:
    path = store.cover_path(state.id)
    has_image = path.exists()
    version = int((state.cover_updated_at or state.created_at).timestamp())
    return CoverView(
        status=state.cover_status,
        error=state.cover_error,
        preview_url=(
            f"/api/sessions/{state.id}/cover/preview?v={version}"
            if has_image
            else None
        ),
        download_url=(
            f"/api/sessions/{state.id}/cover/download?v={version}"
            if has_image
            else None
        ),
    )


class CoverGenerateRequest(BaseModel):
    title: str | None = None
    prompt: str | None = None
    color: bool | None = None
    provider: str | None = None
    model: str | None = None


def _cover_story_memory(state: SessionState) -> str:
    lines: list[str] = []
    if state.story_synopsis.strip():
        lines.append(state.story_synopsis.strip())
    for page in state.pages:
        bits = [part for part in (page.title.strip(), page.caption_text.strip()) if part]
        if bits:
            lines.append(f"Page {page.index}: " + " — ".join(bits))
    return "\n".join(lines)


def _compose_cover_prompt(
    state: SessionState, title: str, direction: str, color: bool
) -> str:
    memory = _cover_story_memory(state)
    characters = "; ".join(
        f"{c.name}: {c.description.strip()}".strip(": ")
        for c in state.characters
        if c.name.strip()
    )
    parts = [
        state.context_prompt.strip(),
        "COVER ART DIRECTION:\n"
        + (
            direction.strip()
            or "Design a captivating, friendly cover that represents the whole story."
        ),
        f"BOOK TITLE: {title.strip() or state.title}",
        "STORY MEMORY (use only as conceptual reference, do not render body text):\n"
        + memory
        if memory
        else "",
        f"MAIN CHARACTERS: {characters}" if characters else "",
        (
            "COVER OVERRIDE: ignore any black-and-white or line-art-only instruction "
            "from the book context for this cover. Create one single full-page cover "
            "illustration in FULL COLOR, with a cohesive and inviting palette suitable "
            "for children. Keep the drawing style and characters consistent with the "
            "book. Leave the bottom area visually calm so a title band can sit there. "
            "Do not render the title or any text; the title will be added separately."
            if color
            else "Create one single full-page coloring-book cover illustration in "
            "clean black-and-white line art. Leave the bottom area visually calm so a "
            "title band can sit there. Do not render the title or any text; the title "
            "will be added separately. No watermark."
        ),
    ]
    return "\n\n".join(part for part in parts if part)


@router.get("/cover", response_model=CoverView)
def get_cover(sid: str) -> CoverView:
    state = _session(sid)
    return _cover_view(state)


@router.post("/cover/generate", response_model=CoverView)
async def generate_cover(sid: str, body: CoverGenerateRequest) -> CoverView:
    state = _session(sid)
    cfg = _config()
    provider_name = body.provider or state.provider
    model_name = body.model or state.model
    if provider_name == "codex":
        model_name = normalize_codex_model(model_name)
    title = body.title if body.title is not None else state.cover_title or state.title
    direction = body.prompt if body.prompt is not None else state.cover_prompt
    color = body.color if body.color is not None else state.cover_color
    state.cover_title = title.strip()
    state.cover_prompt = direction.strip()
    state.cover_color = color
    prompt = _compose_cover_prompt(state, title, direction, color)
    negative = (
        "text, title lettering, watermark, signature, blurry, low quality, "
        "distorted proportions"
        if color
        else (state.negative_prompt or cfg.negative_prompt)
    )
    processing = state.processing or cfg.processing
    caption = _session_caption_cfg(state)
    style_bytes = store.load_session_style_bytes(sid)
    refs: list[tuple[str, bytes]] = []
    for character in state.characters:
        path = store.session_characters_dir(sid) / character.filename
        if path.exists():
            refs.append((character.name, path.read_bytes()))

    def _run() -> Image.Image:
        provider = get_provider(provider_name, model_name)
        img = generate_matching(
            provider,
            prompt,
            target_size=cfg.image_px,
            negative_prompt=negative,
            references=refs or None,
            style_reference=style_bytes,
        )
        if color:
            canvas = fit_color_to_page(img, cfg.image_px)
        else:
            canvas = prepare_page(img, cfg.image_px, processing)
        return draw_cover_title(canvas, title, caption)

    state.cover_status = "generating"
    state.cover_error = None
    store.save_session(state)
    try:
        cover = await asyncio.to_thread(_run)
    except Exception as exc:
        state.cover_status = "error"
        state.cover_error = str(exc)
        store.save_session(state)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    store.cover_dir(sid).mkdir(parents=True, exist_ok=True)
    cover.save(store.cover_path(sid), "PNG", dpi=(cfg.dpi, cfg.dpi))
    state.cover_status = "done"
    state.cover_error = None
    state.cover_updated_at = datetime.now()
    state.pdf_ready = False
    store.save_session(state)
    return _cover_view(state)


@router.delete("/cover", status_code=204)
def delete_cover(sid: str) -> None:
    state = _session(sid)
    store.cover_path(sid).unlink(missing_ok=True)
    state.cover_status = "pending"
    state.cover_error = None
    state.cover_updated_at = None
    state.pdf_ready = False
    store.save_session(state)


@router.get("/cover/preview")
def cover_preview(sid: str) -> FileResponse:
    _session(sid)
    path = store.cover_path(sid)
    if not path.exists():
        raise HTTPException(status_code=404, detail="capa ainda nao gerada")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})


@router.get("/cover/download")
def cover_download(sid: str) -> FileResponse:
    _session(sid)
    path = store.cover_path(sid)
    if not path.exists():
        raise HTTPException(status_code=404, detail="capa ainda nao gerada")
    return FileResponse(path, media_type="image/png", filename="cover.png")


class ExtraPageCreateRequest(BaseModel):
    placement: Literal["front", "back"] = "front"
    title: str = ""
    text: str = ""
    prompt: str = ""
    text_align: Literal["left", "center"] = "center"
    text_vertical: Literal["top", "center", "bottom"] = "center"
    show_border: bool = False
    illustration_position: Literal["background", "above", "below", "none"] = "background"
    illustration_size: Literal["small", "medium", "large"] = "small"
    include_in_pdf: bool = True


class ExtraPageUpdateRequest(BaseModel):
    placement: Literal["front", "back"] | None = None
    title: str | None = None
    text: str | None = None
    prompt: str | None = None
    text_align: Literal["left", "center"] | None = None
    text_vertical: Literal["top", "center", "bottom"] | None = None
    show_border: bool | None = None
    illustration_position: Literal["background", "above", "below", "none"] | None = None
    illustration_size: Literal["small", "medium", "large"] | None = None
    include_in_pdf: bool | None = None


@router.post("/extra-pages", response_model=ExtraPageView, status_code=201)
def create_extra_page(sid: str, body: ExtraPageCreateRequest) -> ExtraPageView:
    state = _session(sid)
    page = ExtraPage(
        id=store.new_extra_page_id(),
        placement=body.placement,
        title=body.title.strip(),
        text=body.text,
        prompt=body.prompt,
        text_align=body.text_align,
        text_vertical=body.text_vertical,
        show_border=body.show_border,
        illustration_position=body.illustration_position,
        illustration_size=body.illustration_size,
        include_in_pdf=body.include_in_pdf,
    )
    _render_extra_page(state, page)
    (state.front_extra_pages if page.placement == "front" else state.back_extra_pages).append(
        page
    )
    state.pdf_ready = False
    store.save_session(state)
    return _extra_page_view(sid, page)


@router.patch("/extra-pages/{page_id}", response_model=ExtraPageView)
def update_extra_page(
    sid: str, page_id: str, body: ExtraPageUpdateRequest
) -> ExtraPageView:
    state = _session(sid)
    page, collection = _find_extra_page(state, page_id)
    if body.title is not None:
        page.title = body.title.strip()
    if body.text is not None:
        page.text = body.text
    if body.prompt is not None:
        page.prompt = body.prompt
    if body.text_align is not None:
        page.text_align = body.text_align
    if body.text_vertical is not None:
        page.text_vertical = body.text_vertical
    if body.show_border is not None:
        page.show_border = body.show_border
    if body.illustration_position is not None:
        page.illustration_position = body.illustration_position
    if body.illustration_size is not None:
        page.illustration_size = body.illustration_size
    if body.include_in_pdf is not None:
        page.include_in_pdf = body.include_in_pdf
    if body.placement is not None and body.placement != page.placement:
        collection.remove(page)
        page.placement = body.placement
        target = (
            state.front_extra_pages
            if page.placement == "front"
            else state.back_extra_pages
        )
        target.append(page)
    _render_extra_page(state, page)
    state.pdf_ready = False
    store.save_session(state)
    return _extra_page_view(sid, page)


class ExtraPageVisualRequest(BaseModel):
    provider: str | None = None
    model: str | None = None


@router.post("/extra-pages/{page_id}/visual", response_model=ExtraPageView)
async def generate_extra_page_visual(
    sid: str, page_id: str, body: ExtraPageVisualRequest
) -> ExtraPageView:
    state = _session(sid)
    page, _collection = _find_extra_page(state, page_id)
    if not page.prompt.strip():
        raise HTTPException(
            status_code=400, detail="informe um prompt para gerar esta pagina"
        )
    cfg = _config()
    provider_name = body.provider or state.provider
    model_name = body.model or state.model
    if provider_name == "codex":
        model_name = normalize_codex_model(model_name)
    processing = state.processing or cfg.processing
    style_bytes = store.load_session_style_bytes(sid)
    refs: list[tuple[str, bytes]] = []
    for character in state.characters:
        path = store.session_characters_dir(sid) / character.filename
        if path.exists():
            refs.append((character.name, path.read_bytes()))

    page.status = "generating"
    page.error = None
    store.save_session(state)
    try:
        layout = await asyncio.to_thread(
            plan_page_layout,
            page_prompt=page.prompt,
            title=page.title,
            text=page.text,
            provider=state.provider,
            model=state.model,
        )
    except Exception as exc:
        page.status = "error"
        page.error = f"nao foi possivel interpretar o prompt da pagina: {exc}"
        store.save_session(state)
        raise HTTPException(status_code=400, detail=page.error) from exc

    page.text_align = layout.text_align
    page.text_vertical = layout.text_vertical
    page.show_border = layout.show_border
    page.illustration_position = layout.image_position
    page.illustration_size = layout.image_size

    background_path = store.extra_page_background_path(sid, page.id)
    if layout.image_position == "none":
        background_path.unlink(missing_ok=True)
        _render_extra_page(state, page)
        state.pdf_ready = False
        store.save_session(state)
        return _extra_page_view(sid, page)

    art_subject = layout.image_prompt.strip() or page.prompt.strip()
    art_instruction = (
        "Create a decorative black-and-white line-art frame or background for a "
        "text page of this book. Keep the central area mostly clear/white so the "
        "text block remains readable."
        if layout.image_position == "background"
        else "Create one single isolated black-and-white line-art illustration. "
        "Show the full subject centered on a pure white background, with no frame, "
        "no border and no background scenery."
    )
    prompt = "\n\n".join(
        part
        for part in [
            state.context_prompt.strip(),
            "INTERIOR TEXT PAGE ART DIRECTION:\n" + art_subject,
            f"PAGE TITLE CONTEXT: {page.title.strip()}" if page.title.strip() else "",
            art_instruction
            + " Do not render any text, letters, numbers, or watermarks.",
        ]
        if part
    )
    negative = state.negative_prompt or cfg.negative_prompt

    def _run() -> Image.Image:
        provider = get_provider(provider_name, model_name)
        img = generate_matching(
            provider,
            prompt,
            target_size=cfg.image_px,
            negative_prompt=negative,
            references=refs or None,
            style_reference=style_bytes,
        )
        return prepare_page(img, cfg.image_px, processing)

    try:
        background = await asyncio.to_thread(_run)
    except Exception as exc:
        page.status = "error"
        page.error = str(exc)
        store.save_session(state)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    background_path.parent.mkdir(parents=True, exist_ok=True)
    background.save(background_path, "PNG", dpi=(cfg.dpi, cfg.dpi))
    _render_extra_page(state, page)
    state.pdf_ready = False
    store.save_session(state)
    return _extra_page_view(sid, page)


@router.delete("/extra-pages/{page_id}", status_code=204)
def delete_extra_page(sid: str, page_id: str) -> None:
    state = _session(sid)
    page, collection = _find_extra_page(state, page_id)
    collection.remove(page)
    store.extra_page_path(sid, page.id).unlink(missing_ok=True)
    store.extra_page_background_path(sid, page.id).unlink(missing_ok=True)
    state.pdf_ready = False
    store.save_session(state)


@router.get("/extra-pages/{page_id}/preview")
def extra_page_preview(sid: str, page_id: str) -> FileResponse:
    _session(sid)
    path = store.extra_page_path(sid, page_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="pagina extra sem preview")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})


@router.get("/extra-pages/{page_id}/download")
def extra_page_download(sid: str, page_id: str) -> FileResponse:
    _session(sid)
    path = store.extra_page_path(sid, page_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="pagina extra sem preview")
    return FileResponse(path, media_type="image/png", filename=f"extra-{page_id}.png")


# --- Stage 2: Per-book Character Sheets ---


@router.get("/characters", response_model=list[BookCharacterView])
def list_book_characters(sid: str) -> list[BookCharacterView]:
    state = _session(sid)
    return [_character_view(sid, c) for c in state.characters]


@router.post("/characters", response_model=BookCharacterView, status_code=201)
async def create_book_character(
    sid: str,
    name: str = Form(...),
    description: str = Form(default=""),
    image: UploadFile | None = File(default=None),
    assign_all: bool = Form(default=True),
) -> BookCharacterView:
    state = _session(sid)
    clean = name.strip()
    if not clean:
        raise HTTPException(status_code=400, detail="informe um nome para o personagem")

    cid = store.new_character_id()
    has_image = False
    if image is not None:
        data = await read_upload_limited(image)
        if data:
            try:
                with Image.open(io.BytesIO(data)) as source:
                    source.load()
                    normalized = source.convert("RGB")
            except Exception as exc:
                raise HTTPException(
                    status_code=400, detail="arquivo nao e uma imagem valida"
                ) from exc
            store.session_characters_dir(sid).mkdir(parents=True, exist_ok=True)
            normalized.save(store.session_characters_dir(sid) / f"{cid}.png", "PNG")
            has_image = True

    record = BookCharacter(
        id=cid,
        name=clean,
        description=description.strip(),
        approved=has_image,
        sheet_status="ready" if has_image else "empty",
        updated_at=datetime.now(),
    )
    state.characters.append(record)
    if assign_all:
        for page in state.pages:
            if cid not in page.character_ids:
                page.character_ids.append(cid)
    store.save_session(state)
    return _character_view(sid, record)


@router.post("/characters/import-global/{cid}", response_model=BookCharacterView, status_code=201)
def import_global_character(sid: str, cid: str) -> BookCharacterView:
    state = _session(sid)
    global_record = next((r for r in store.load_characters() if r.id == cid), None)
    if global_record is None:
        raise HTTPException(status_code=404, detail="personagem global nao encontrado")
    source_img = store.characters_dir() / global_record.filename
    if not source_img.exists():
        raise HTTPException(status_code=404, detail="imagem do personagem global ausente")

    existing = next((c for c in state.characters if c.id == cid), None)
    if existing is not None:
        return _character_view(sid, existing)

    store.session_characters_dir(sid).mkdir(parents=True, exist_ok=True)
    (store.session_characters_dir(sid) / global_record.filename).write_bytes(
        source_img.read_bytes()
    )
    record = BookCharacter(
        id=global_record.id,
        name=global_record.name,
        description=global_record.description,
        approved=True,
        sheet_status="ready",
        updated_at=datetime.now(),
    )
    state.characters.append(record)
    store.save_session(state)
    return _character_view(sid, record)


class CharacterSheetGenerateRequest(BaseModel):
    description: str | None = None
    provider: str | None = None
    model: str | None = None


@router.post("/characters/{cid}/generate-sheet", response_model=BookCharacterView)
async def generate_character_sheet(
    sid: str, cid: str, body: CharacterSheetGenerateRequest
) -> BookCharacterView:
    state = _session(sid)
    char = _book_character(state, cid)
    cfg = _config()

    if body.description is not None and body.description.strip():
        char.description = body.description.strip()

    provider_name = body.provider or state.provider
    model_name = body.model or state.model
    if provider_name == "codex":
        model_name = normalize_codex_model(model_name)

    preset = (
        get_preset(state.preset_id, store.load_custom_presets())
        if state.preset_id
        else None
    )
    sheet_prompt = build_character_sheet_prompt(
        char.name,
        char.description,
        state.context_prompt,
        preset.character_sheet_prompt if preset else "",
    )
    negative = state.negative_prompt or cfg.negative_prompt
    processing = state.processing or cfg.processing
    style_bytes = store.effective_style_reference_bytes(
        sid, state.category, 1, preset_id=state.preset_id
    )

    existing_img_path = store.session_characters_dir(sid) / char.filename
    ref_list: list[tuple[str, bytes]] | None = None
    if existing_img_path.exists():
        ref_list = [(char.name, existing_img_path.read_bytes())]

    char.sheet_status = "generating"
    char.error = None
    store.save_session(state)

    def _run() -> Image.Image:
        provider = get_provider(provider_name, model_name)
        img = generate_matching(
            provider,
            sheet_prompt,
            target_size=(1536, 1024),
            negative_prompt=negative,
            references=ref_list,
            style_reference=style_bytes,
        )
        return to_line_art(img, processing)

    try:
        sheet = await asyncio.to_thread(_run)
        store.session_characters_dir(sid).mkdir(parents=True, exist_ok=True)
        sheet.save(store.session_characters_dir(sid) / char.filename, "PNG")
        char.sheet_status = "ready"
        char.approved = True
        char.updated_at = datetime.now()
    except Exception as exc:
        char.sheet_status = "error"
        char.error = str(exc)
        char.updated_at = datetime.now()

    store.save_session(state)
    return _character_view(sid, char)


class BookCharacterPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    approved: bool | None = None


@router.patch("/characters/{cid}", response_model=BookCharacterView)
def patch_book_character(sid: str, cid: str, body: BookCharacterPatch) -> BookCharacterView:
    state = _session(sid)
    char = _book_character(state, cid)
    if body.name is not None and body.name.strip():
        char.name = body.name.strip()
    if body.description is not None:
        char.description = body.description.strip()
    if body.approved is not None:
        char.approved = body.approved
    char.updated_at = datetime.now()
    store.save_session(state)
    return _character_view(sid, char)


@router.delete("/characters/{cid}", status_code=204)
def delete_book_character(sid: str, cid: str) -> None:
    state = _session(sid)
    remaining = [c for c in state.characters if c.id != cid]
    if len(remaining) == len(state.characters):
        raise HTTPException(status_code=404, detail="personagem nao encontrado")
    (store.session_characters_dir(sid) / f"{cid}.png").unlink(missing_ok=True)
    state.characters = remaining
    for page in state.pages:
        if cid in page.character_ids:
            page.character_ids = [x for x in page.character_ids if x != cid]
    store.save_session(state)


@router.get("/characters/{cid}/image")
def book_character_image(sid: str, cid: str) -> FileResponse:
    state = _session(sid)
    char = _book_character(state, cid)
    path = store.session_characters_dir(sid) / char.filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="character sheet ainda nao gerado")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})


# --- Stage 3: Story Scripting & Page Breakdown ---


class StoryGenerateRequest(BaseModel):
    premise: str
    mode: StoryMode = "narrative"
    pages: int | None = Field(default=None, ge=1, le=200)
    language: str = "en"
    overwrite_pending: bool = True
    apply_title: bool = True


class StoryPatchRequest(BaseModel):
    mode: StoryMode | None = None
    premise: str | None = None
    synopsis: str | None = None


class StoryStateView(BaseModel):
    title: str
    mode: Literal["narrative", "collection"]
    premise: str
    synopsis: str
    pages_updated: int


@router.patch("/story", response_model=StoryStateView)
def patch_story(sid: str, body: StoryPatchRequest) -> StoryStateView:
    state = _session(sid)
    if body.mode is not None:
        state.story_mode = body.mode
    if body.premise is not None:
        state.story_premise = body.premise.strip()
    if body.synopsis is not None:
        state.story_synopsis = body.synopsis.strip()
    store.save_session(state)
    return StoryStateView(
        title=state.title,
        mode=state.story_mode,
        premise=state.story_premise,
        synopsis=state.story_synopsis,
        pages_updated=0,
    )


@router.post("/story/generate", response_model=StoryStateView)
async def generate_story(sid: str, body: StoryGenerateRequest) -> StoryStateView:
    state = _session(sid)
    target_pages = body.pages or len(state.pages) or 10
    preset = (
        get_preset(state.preset_id, store.load_custom_presets())
        if state.preset_id
        else None
    )
    briefs = [
        CharacterBrief(id=c.id, name=c.name, description=c.description)
        for c in state.characters
    ]

    try:
        plan = await asyncio.to_thread(
            generate_story_plan,
            premise=body.premise,
            pages=target_pages,
            mode=body.mode,
            language=body.language,
            preset=preset,
            style_context=state.context_prompt,
            characters=briefs,
            provider=state.provider,
            model=state.model,
        )
    except StoryGenerationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    state.story_mode = body.mode
    state.language = body.language or "en"
    state.story_premise = body.premise.strip()
    state.story_synopsis = plan.synopsis
    if any(p.caption_text.strip() for p in plan.pages):
        state.caption_enabled = True
    if body.apply_title and plan.title:
        state.title = plan.title

    existing_by_idx = {p.index: p for p in state.pages}
    new_pages: list[PageState] = []
    updated_count = 0
    for scripted in plan.pages:
        old = existing_by_idx.get(scripted.index)
        if old is not None and old.status == "done":
            # Preserve already-generated artwork while allowing caption/title enrichment if empty
            if not old.title:
                old.title = scripted.title
            new_pages.append(old)
            continue
        new_pages.append(
            PageState(
                index=scripted.index,
                title=scripted.title,
                prompt=scripted.scene_prompt,
                caption_text=scripted.caption_text,
                character_ids=scripted.character_ids
                or [c.id for c in state.characters if c.approved],
                status="pending",
            )
        )
        updated_count += 1

    # Keep any extra already-generated pages beyond target_pages so user never loses art
    for old in state.pages:
        if old.index > len(new_pages) and old.status == "done":
            new_pages.append(old)

    state.pages = new_pages
    state.pdf_ready = False
    store.save_session(state)
    return StoryStateView(
        title=state.title,
        mode=state.story_mode,
        premise=state.story_premise,
        synopsis=state.story_synopsis,
        pages_updated=updated_count,
    )


class InsertPageRequest(BaseModel):
    after_index: int = Field(ge=0, le=200)
    hint: str = ""


class InsertPageResponse(BaseModel):
    inserted_index: int
    total_pages: int
    title: str
    prompt: str
    caption_text: str
    character_ids: list[str]


@router.post("/pages/insert", response_model=InsertPageResponse, status_code=201)
async def insert_contextual_page(
    sid: str, body: InsertPageRequest
) -> InsertPageResponse:
    """Generate a new page bridging the context between after_index and after_index + 1."""
    state = _session(sid)
    if len(state.pages) >= 200:
        raise HTTPException(status_code=400, detail="limite maximo de 200 paginas atingido")
    if body.after_index < 0 or body.after_index > len(state.pages):
        raise HTTPException(status_code=400, detail="posicao de insercao invalida")
    if any(p.index > body.after_index and p.status == "generating" for p in state.pages):
        raise HTTPException(
            status_code=409,
            detail="aguarde a geracao das paginas seguintes terminar antes de inserir",
        )

    ordered = sorted(state.pages, key=lambda item: item.index)
    by_idx = {p.index: p for p in ordered}
    char_name_by_id = {c.id: c.name for c in state.characters}

    def _to_scripted(page: PageState | None) -> ScriptedPage | None:
        if page is None:
            return None
        return ScriptedPage(
            index=page.index,
            title=page.title,
            scene_prompt=page.prompt,
            caption_text=page.caption_text,
            character_names=[
                char_name_by_id[cid] for cid in page.character_ids if cid in char_name_by_id
            ],
            character_ids=list(page.character_ids),
        )

    prev_page = _to_scripted(by_idx.get(body.after_index))
    next_page = _to_scripted(by_idx.get(body.after_index + 1))
    preset = (
        get_preset(state.preset_id, store.load_custom_presets())
        if state.preset_id
        else None
    )
    briefs = [
        CharacterBrief(id=c.id, name=c.name, description=c.description)
        for c in state.characters
    ]
    new_index = body.after_index + 1
    total_after = len(state.pages) + 1

    try:
        scripted = await asyncio.to_thread(
            generate_bridge_page,
            book_title=state.title,
            premise=state.story_premise,
            synopsis=state.story_synopsis,
            mode=state.story_mode,
            language=state.language or "en",
            preset=preset,
            style_context=state.context_prompt,
            characters=briefs,
            prev_page=prev_page,
            next_page=next_page,
            new_index=new_index,
            total_after=total_after,
            hint=body.hint,
            provider=state.provider,
            model=state.model,
        )
    except StoryGenerationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    fallback_chars = (
        list(prev_page.character_ids)
        if prev_page and prev_page.character_ids
        else [c.id for c in state.characters if c.approved]
    )
    char_ids = scripted.character_ids or fallback_chars

    new_page = PageState(
        index=new_index,
        title=scripted.title,
        prompt=scripted.scene_prompt,
        caption_text=scripted.caption_text,
        character_ids=char_ids,
        status="pending",
    )
    try:
        updated = store.insert_page_after(sid, body.after_index, new_page)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return InsertPageResponse(
        inserted_index=new_page.index,
        total_pages=len(updated.pages),
        title=new_page.title,
        prompt=new_page.prompt,
        caption_text=new_page.caption_text,
        character_ids=new_page.character_ids,
    )


# --- 1-Click Full-Book Autopilot (Style -> Blueprint -> Anchor -> Sheets -> Pages -> PDF) ---


class AutopilotRequest(BaseModel):
    preset_id: str | None = None
    pages: int | None = Field(default=None, ge=1, le=200)
    premise_hint: str = ""
    language: str = "en"
    mode: StoryMode | None = None
    stop_after: Literal["blueprint", "sheets", "pdf"] = "pdf"
    resume: bool = False


class AutopilotStatus(BaseModel):
    status: Literal["idle", "running", "done", "cancelled", "error"] = "idle"
    phase: str = ""
    label: str = ""
    percent: int = 0
    current_step: int = 1
    error: str | None = None
    cancelling: bool = False


_autopilot_status: dict[str, AutopilotStatus] = {}
_autopilot_cancel: set[str] = set()
_autopilot_threads: dict[str, threading.Thread] = {}


def _set_autopilot(
    sid: str,
    *,
    status: Literal["idle", "running", "done", "cancelled", "error"],
    phase: str,
    label: str,
    percent: int,
    current_step: int,
    error: str | None = None,
    cancelling: bool = False,
) -> None:
    _autopilot_status[sid] = AutopilotStatus(
        status=status,
        phase=phase,
        label=label,
        percent=max(0, min(100, percent)),
        current_step=current_step,
        error=error,
        cancelling=cancelling,
    )


def _has_existing_blueprint(state: SessionState) -> bool:
    """Return True if the session already has a planned story/cast/pages to resume."""
    if state.story_synopsis.strip() or state.characters:
        return True
    return any(
        p.status == "done" or p.title.strip() or p.caption_text.strip()
        for p in state.pages
    )




def _reset_stuck_generating(state: SessionState) -> bool:
    """Reset any 'generating' characters/pages so they can be cleanly resumed."""
    changed = False
    chars_dir = store.session_characters_dir(state.id)
    for char in state.characters:
        if char.sheet_status == "generating":
            char.sheet_status = "ready" if (chars_dir / char.filename).exists() else "empty"
            changed = True
    pages_dir = store.pages_dir(state.id)
    for page in state.pages:
        if page.status == "generating":
            fname = f"page-{page.index:03d}.png"
            page.status = "done" if (pages_dir / fname).exists() else "pending"
            changed = True
    for extra in (*state.front_extra_pages, *state.back_extra_pages):
        if extra.status == "generating":
            extra.status = "pending"
            changed = True
    if state.cover_status == "generating":
        state.cover_status = "done" if store.cover_path(state.id).exists() else "pending"
        changed = True
    return changed


def _session_book_cfg(state: SessionState) -> BookConfig:
    cfg = _config()
    updates: dict[str, object] = {
        "title": state.title or cfg.title,
        "language": state.language or "en",
        "page_size": state.page_size,
        "blank_verso": state.blank_verso,
        "include_title_page": state.include_title_page,
        "include_belongs_to_page": state.include_belongs_to_page,
        "include_color_test_page": state.include_color_test_page,
    }
    if state.processing is not None:
        updates["processing"] = state.processing
    if state.negative_prompt:
        updates["negative_prompt"] = state.negative_prompt
    return cfg.model_copy(update=updates)


def _session_caption_cfg(state: SessionState) -> CaptionConfig:
    return _config().caption.model_copy(
        update={
            "enabled": state.caption_enabled,
            "style": state.caption_style,
            "position": state.caption_position,
            "font_family": state.caption_font_family,
            "bold": state.caption_font_bold,
            "font_size_ratio": state.caption_font_size,
        }
    )


async def _run_autopilot(sid: str, req: AutopilotRequest) -> None:
    try:
        state = _session(sid)
        effective_preset_id = req.preset_id or state.preset_id
        preset = (
            get_preset(effective_preset_id, store.load_custom_presets())
            if effective_preset_id
            else None
        )
        # Only apply preset defaults when switching to a different preset.
        # Keep the user's configured style, format, processing and typography
        # when running autopilot on the already-configured book.
        if req.preset_id and req.preset_id != state.preset_id and preset is not None:
            state.preset_id = preset.id
            state.category = preset.category
            state.page_size = preset.page_size
            state.page_layout = default_page_layout_for_category(preset.category)
            state.context_prompt = preset.context_prompt
            state.negative_prompt = preset.negative_prompt
            state.processing = preset.processing.model_copy(deep=True)
            state.caption_enabled = preset.caption.enabled
            state.caption_style = preset.caption.style
            state.caption_position = preset.caption.position
            state.caption_font_family = preset.caption.font_family
            state.caption_font_bold = preset.caption.bold
            state.caption_font_size = preset.caption.font_size_ratio
            state.blank_verso = preset.blank_verso
            state.include_title_page = preset.include_title_page
            state.include_belongs_to_page = preset.include_belongs_to_page
            state.include_color_test_page = preset.include_color_test_page
            store.save_session(state)

        target_pages = req.pages or len(state.pages) or 12
        provider_name = state.provider
        model_name = (
            normalize_codex_model(state.model) if provider_name == "codex" else state.model
        )

        def _is_cancelled() -> bool:
            return sid in _autopilot_cancel

        def _finish_cancelled(phase: str, percent: int, step: int) -> None:
            _reset_stuck_generating(state)
            store.save_session(state)
            _set_autopilot(
                sid,
                status="cancelled",
                phase=phase,
                label="Geração cancelada. Você pode continuar de onde parou.",
                percent=percent,
                current_step=step,
            )

        if req.resume and _reset_stuck_generating(state):
            store.save_session(state)

        # Phase 1: Full Book Blueprint (skipped when resuming an existing plan)
        anchor_subject = (
            state.pages[0].prompt
            if state.pages and state.pages[0].prompt.strip()
            else (state.story_premise or state.title or "iconic showcase scene")
        )
        need_blueprint = not (req.resume and _has_existing_blueprint(state))
        if need_blueprint:
            if _is_cancelled():
                _finish_cancelled("blueprint", 5, 1)
                return
            _set_autopilot(
                sid,
                status="running",
                phase="blueprint",
                label="Criando conceito, elenco e roteiro completo…",
                percent=8,
                current_step=1,
            )
            blueprint = await asyncio.to_thread(
                generate_full_book_blueprint,
                pages=target_pages,
                preset=preset,
                style_context=state.context_prompt,
                premise_hint=req.premise_hint or state.story_premise,
                mode=req.mode,
                language=req.language,
                provider=provider_name,
                model=model_name,
            )

            if _is_cancelled():
                _finish_cancelled("blueprint", 10, 1)
                return

            state.title = blueprint.title
            state.language = req.language or "en"
            state.story_premise = blueprint.premise
            state.story_synopsis = blueprint.synopsis
            state.story_mode = blueprint.mode
            anchor_subject = blueprint.style_anchor_subject

            # Register generated characters into this book
            char_id_by_name: dict[str, str] = {}
            new_characters: list[BookCharacter] = []
            for gen_char in blueprint.characters:
                cid = store.new_character_id()
                char_id_by_name[gen_char.name.strip().lower()] = cid
                new_characters.append(
                    BookCharacter(
                        id=cid,
                        name=gen_char.name,
                        description=gen_char.description,
                        approved=False,
                        sheet_status="empty",
                        updated_at=datetime.now(),
                    )
                )
            state.characters = new_characters

            # Populate all pages with visual prompt, caption, and character links
            all_char_ids = [c.id for c in state.characters]
            state.pages = [
                PageState(
                    index=p.index,
                    title=p.title,
                    prompt=p.scene_prompt,
                    caption_text=p.caption_text,
                    character_ids=(
                        list(
                            dict.fromkeys(
                                char_id_by_name[n.lower()]
                                for n in p.character_names
                                if n.lower() in char_id_by_name
                            )
                        )
                        or all_char_ids
                    ),
                    status="pending",
                )
                for p in blueprint.pages
            ]
            state.pdf_ready = False
            state.current_step = 3 if req.stop_after == "blueprint" else 1
            store.save_session(state)

        if req.stop_after == "blueprint":
            _set_autopilot(
                sid,
                status="done",
                phase="done",
                label="Conceito, personagens e roteiro prontos para revisão!",
                percent=100,
                current_step=3,
            )
            return

        cfg = _session_book_cfg(state)
        total_img_steps = 1 + len(state.characters) + (
            0 if req.stop_after == "sheets" else len(state.pages)
        )
        done_img_steps = 0

        def _pct() -> int:
            return 15 + int((done_img_steps / max(1, total_img_steps)) * 80)

        # Phase 2: Ensure Style Anchor Image (use built-in category reference directly when available)
        if _is_cancelled():
            _finish_cancelled("style_anchor", _pct(), 1)
            return

        if not store.session_style_path(sid).exists():
            category_ref = store.load_page_layout_reference_bytes(
                state.category, 1, preset_id=state.preset_id
            )
            if category_ref is not None:
                store.session_style_dir(sid).mkdir(parents=True, exist_ok=True)
                store.session_style_path(sid).write_bytes(category_ref)
            else:
                _set_autopilot(
                    sid,
                    status="running",
                    phase="style_anchor",
                    label="Gerando âncora visual de estilo do livro…",
                    percent=_pct(),
                    current_step=1,
                )
                anchor_prompt = (
                    f"{state.context_prompt.strip()}\n\n"
                    f"Style anchor showcase subject: {anchor_subject}."
                )

                def _gen_anchor() -> Image.Image:
                    provider = get_provider(provider_name, model_name)
                    img = generate_matching(
                        provider,
                        anchor_prompt,
                        target_size=cfg.image_px,
                        negative_prompt=cfg.negative_prompt,
                    )
                    return to_line_art(img, cfg.processing)

                anchor_img = await asyncio.to_thread(_gen_anchor)
                store.session_style_dir(sid).mkdir(parents=True, exist_ok=True)
                anchor_img.save(store.session_style_path(sid), "PNG")
        done_img_steps += 1
        if _is_cancelled():
            _finish_cancelled("style_anchor", _pct(), 1)
            return
        style_bytes = store.effective_style_reference_bytes(
        sid, state.category, 1, preset_id=state.preset_id
    )

        # Phase 3: Generate Character Sheets (skip already-ready sheets on resume)
        chars_dir = store.session_characters_dir(sid)
        for idx, char in enumerate(state.characters, start=1):
            if _is_cancelled():
                _finish_cancelled("sheets", _pct(), 2)
                return
            if (
                req.resume
                and char.sheet_status == "ready"
                and (chars_dir / char.filename).exists()
            ):
                done_img_steps += 1
                continue
            _set_autopilot(
                sid,
                status="running",
                phase="sheets",
                label=f"Gerando Character Sheet ({idx}/{len(state.characters)}): {char.name}…",
                percent=_pct(),
                current_step=2,
            )
            char.sheet_status = "generating"
            store.save_session(state)
            sheet_prompt = build_character_sheet_prompt(
                char.name,
                char.description,
                state.context_prompt,
                preset.character_sheet_prompt if preset else "",
            )

            def _gen_sheet(prompt_text: str = sheet_prompt) -> Image.Image:
                provider = get_provider(provider_name, model_name)
                img = generate_matching(
                    provider,
                    prompt_text,
                    target_size=(1536, 1024),
                    negative_prompt=cfg.negative_prompt,
                    style_reference=style_bytes,
                )
                return to_line_art(img, cfg.processing)

            sheet_img = await asyncio.to_thread(_gen_sheet)
            chars_dir.mkdir(parents=True, exist_ok=True)
            sheet_img.save(chars_dir / char.filename, "PNG")
            char.sheet_status = "ready"
            char.approved = True
            char.updated_at = datetime.now()
            state.current_step = 2
            store.save_session(state)
            done_img_steps += 1
            if _is_cancelled():
                _finish_cancelled("sheets", _pct(), 2)
                return

        if req.stop_after == "sheets":
            _set_autopilot(
                sid,
                status="done",
                phase="done",
                label="Estilo, Character Sheets e roteiro prontos!",
                percent=100,
                current_step=2,
            )
            return

        # Phase 4: Generate Every Page with Style Anchor + Character Sheets (skip done pages on resume)
        char_by_id = {c.id: c for c in state.characters}
        pages_dir = store.pages_dir(sid)
        for page in state.pages:
            if _is_cancelled():
                _finish_cancelled("pages", _pct(), 3)
                return
            fname = f"page-{page.index:03d}.png"
            if req.resume and page.status == "done" and (pages_dir / fname).exists():
                done_img_steps += 1
                continue
            _set_autopilot(
                sid,
                status="running",
                phase="pages",
                label=f"Ilustrando página {page.index} de {len(state.pages)}…",
                percent=_pct(),
                current_step=3,
            )
            page.status = "generating"
            page.error = None
            state.current_step = 3
            store.save_session(state)

            effective_layout = state.page_layout or default_page_layout_for_category(state.category)
            embedded_text = page.caption_text.strip() if state.caption_enabled else ""
            full_prompt = compose_page_prompt(
                state.context_prompt,
                page.prompt,
                page.index,
                len(state.pages),
                page_layout=effective_layout,
                caption_text=embedded_text,
                caption_position=state.caption_position,
                caption_font_family=state.caption_font_family,
                caption_font_bold=state.caption_font_bold,
            )
            page_negative = effective_negative_prompt(
                cfg.negative_prompt,
                has_embedded_text=bool(embedded_text),
                page_layout=effective_layout,
            )
            page_processing = (
                cfg.processing.model_copy(
                    update={"min_component_px": min(cfg.processing.min_component_px, 12)}
                )
                if embedded_text
                else cfg.processing
            )
            char_notes = [
                f"{char_by_id[cid].name}: {char_by_id[cid].description}"
                for cid in page.character_ids
                if cid in char_by_id and char_by_id[cid].description.strip()
            ]
            if char_notes:
                full_prompt = f"{full_prompt}\n\nCharacters in this scene: {'; '.join(char_notes)}."

            refs: list[tuple[str, bytes]] = []
            for cid in page.character_ids:
                char_item = char_by_id.get(cid)
                if char_item is None:
                    continue
                cpath = chars_dir / char_item.filename
                if cpath.exists():
                    refs.append((char_item.name, cpath.read_bytes()))

            def _gen_page(
                prompt_text: str = full_prompt,
                page_refs: list[tuple[str, bytes]] = refs,
                page_neg: str = page_negative,
                proc_cfg: ProcessingConfig = page_processing,
                page_idx: int = page.index,
            ) -> tuple[Image.Image, Image.Image]:
                provider = get_provider(provider_name, model_name)
                effective_style = (
                    style_bytes
                    or store.load_page_layout_reference_bytes(
                        state.category, page_idx, preset_id=state.preset_id
                    )
                )
                raw_img = generate_matching(
                    provider,
                    prompt_text,
                    target_size=cfg.image_px,
                    negative_prompt=page_neg,
                    references=page_refs or None,
                    style_reference=effective_style,
                )
                proc = prepare_page(raw_img, cfg.image_px, proc_cfg)
                return raw_img, proc

            raw_img, processed = await asyncio.to_thread(_gen_page)
            raw_img.save(store.raw_dir(sid) / fname, "PNG")
            processed.save(pages_dir / fname, "PNG", dpi=(cfg.dpi, cfg.dpi))
            preview = processed.copy()
            preview.thumbnail((640, 900), Image.Resampling.LANCZOS)
            preview.save(store.preview_dir(sid) / fname, "PNG", optimize=True)
            page.width_px = processed.width
            page.height_px = processed.height
            page.status = "done"
            page.updated_at = datetime.now()
            store.save_session(state)
            done_img_steps += 1

        # Phase 5: Compose Final KDP PDF
        _set_autopilot(
            sid,
            status="running",
            phase="pdf",
            label="Montando prévia em PDF…",
            percent=97,
            current_step=4,
        )
        page_files = store.session_page_files(state)
        if page_files:
            cover_path = store.cover_path(sid)
            await asyncio.to_thread(
                compose_pdf,
                page_files,
                store.session_dir(sid) / "book.pdf",
                cfg,
                include_title_page=state.include_title_page,
                blank_verso=state.blank_verso,
                include_belongs_to_page=state.include_belongs_to_page,
                include_color_test_page=state.include_color_test_page,
                cover_image=(
                    cover_path if state.cover_include and cover_path.exists() else None
                ),
                front_extra_images=_extra_page_images(state, "front"),
                back_extra_images=_extra_page_images(state, "back"),
                synopsis=state.story_synopsis,
            )
            state.pdf_ready = True
            store.save_session(state)

        _set_autopilot(
            sid,
            status="done",
            phase="done",
            label="Geração concluída. Prévia em PDF disponível para revisão.",
            percent=100,
            current_step=4,
        )
    except Exception as exc:
        try:
            st_state = store.load_session(sid)
            if st_state is not None and _reset_stuck_generating(st_state):
                store.save_session(st_state)
        except Exception:
            pass
        if sid in _autopilot_cancel:
            _set_autopilot(
                sid,
                status="cancelled",
                phase="cancelled",
                label="Geração cancelada. Você pode continuar de onde parou.",
                percent=_autopilot_status.get(sid, AutopilotStatus()).percent,
                current_step=_autopilot_status.get(sid, AutopilotStatus()).current_step,
            )
        else:
            _set_autopilot(
                sid,
                status="error",
                phase="error",
                label="Autopilot interrompido com erro. Você pode continuar de onde parou.",
                percent=_autopilot_status.get(sid, AutopilotStatus()).percent,
                current_step=_autopilot_status.get(sid, AutopilotStatus()).current_step,
                error=str(exc),
            )
    finally:
        _autopilot_cancel.discard(sid)


def _run_autopilot_thread(sid: str, req: AutopilotRequest) -> None:
    try:
        asyncio.run(_run_autopilot(sid, req))
    finally:
        _autopilot_threads.pop(sid, None)


@router.post("/autopilot", response_model=AutopilotStatus, status_code=202)
def start_autopilot(sid: str, body: AutopilotRequest) -> AutopilotStatus:
    _session(sid)
    existing = _autopilot_threads.get(sid)
    if existing is not None and existing.is_alive():
        raise HTTPException(status_code=409, detail="autopilot ja esta em execucao neste livro")
    _autopilot_cancel.discard(sid)
    _set_autopilot(
        sid,
        status="running",
        phase="starting",
        label=(
            "Retomando geração de onde parou…"
            if body.resume
            else "Iniciando criação automática completa…"
        ),
        percent=3,
        current_step=1,
    )
    thread = threading.Thread(
        target=_run_autopilot_thread, args=(sid, body), daemon=True
    )
    _autopilot_threads[sid] = thread
    thread.start()
    return _autopilot_status[sid]


@router.get("/autopilot", response_model=AutopilotStatus)
def get_autopilot_status(sid: str) -> AutopilotStatus:
    _session(sid)
    return _autopilot_status.get(sid, AutopilotStatus())


@router.post("/autopilot/cancel", response_model=AutopilotStatus)
def cancel_autopilot(sid: str) -> AutopilotStatus:
    state = _session(sid)
    _autopilot_cancel.add(sid)
    existing = _autopilot_threads.get(sid)
    current = _autopilot_status.get(sid)
    if existing is not None and existing.is_alive() and current and current.status == "running":
        current.label = "Cancelando após a etapa atual…"
        current.cancelling = True
        return current
    if _reset_stuck_generating(state):
        store.save_session(state)
    _set_autopilot(
        sid,
        status="cancelled",
        phase=current.phase if current else "cancelled",
        label="Geração cancelada. Você pode continuar de onde parou.",
        percent=current.percent if current else 0,
        current_step=current.current_step if current else 1,
    )
    return _autopilot_status[sid]
