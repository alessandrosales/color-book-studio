"""5-stage book creation studio: style, characters, story, pages, cover/finalization."""

from __future__ import annotations

import asyncio
import io
import logging
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
from color_book_generator.brief import reads_right_to_left, structure_for_category
from color_book_generator.brief import story_context as build_story_context
from color_book_generator.config import (
    BookConfig,
    CaptionConfig,
    default_book_config_path,
    load_book_config,
)
from color_book_generator.identity import (
    SHEET_SIZE,
    CharacterDesign,
    build_character_sheet_prompt,
    cast_block,
    design_character,
    design_source,
    match_cast_ids,
    sheet_style_for,
    style_anchor_prompt,
)
from color_book_generator.layout.pdf import compose_pdf
from color_book_generator.page_layout import plan_page_layout
from color_book_generator.presets import (
    StylePreset,
    default_page_layout_for_category,
    get_preset,
)
from color_book_generator.print_profile import get_profile
from color_book_generator.processing.caption import (
    render_text_page,
)
from color_book_generator.processing.line_art import (
    fit_color_to_page,
    prepare_page,
    to_line_art,
)
from color_book_generator.prompts import PAGE_LAYOUT_PROMPTS
from color_book_generator.providers import get_provider
from color_book_generator.providers.base import generate_matching
from color_book_generator.standards import caption_word_cap, page_beats, reading_standard
from color_book_generator.story import (
    BookFinishing,
    CharacterBrief,
    ExtraPageCopy,
    ScriptedPage,
    StoryGenerationError,
    StoryMode,
    generate_book_finishing,
    generate_bridge_page,
    generate_full_book_blueprint,
    generate_story_plan,
)
from color_book_generator.typography import detect_typography
from color_book_generator.web import book_pipeline, store
from color_book_generator.web.line_check import finish_new_page
from color_book_generator.web.reading import build_report
from color_book_generator.web.store import (
    BookCharacter,
    ExtraPage,
    PageState,
    SessionState,
)

log = logging.getLogger(__name__)
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


def _story_context(state: SessionState) -> str:
    """Brief + story bible the author defined in the briefing step (may be empty)."""
    return build_story_context(state.story_brief, state.story_bible, state.category)


def _caption_cap(state: SessionState) -> int:
    """Caption word cap for this book's audience and page layout."""
    standard = reading_standard(state.story_brief, state.category, state.preset_id)
    return caption_word_cap(standard, state.page_layout)


def _page_beats(state: SessionState, pages: int) -> list[str]:
    return page_beats(
        structure=structure_for_category(state.category),
        pages=pages,
        bible=state.story_bible,
        brief=state.story_brief,
    )


def _reading_warnings(state: SessionState) -> list[str]:
    return build_report(state, _session_book_cfg(state), _session_caption_cfg(state)).warnings


def _apply_reading_font(state: SessionState) -> None:
    """Raise the caption size to the audience's ideal when it is below the minimum."""
    report = build_report(state, _session_book_cfg(state), _session_caption_cfg(state))
    if report.caption_enabled and not report.font_ok:
        state.caption_font_size = report.recommended_ratio


def _story_premise(state: SessionState, premise: str = "") -> str:
    """Premise for the script: an explicit one, else the briefing (bible, core idea), else the old premise."""
    bible = state.story_bible
    for candidate in (
        premise,
        bible.logline if bible is not None and bible.locked else "",
        state.story_brief.logline,
        state.story_premise,
    ):
        if candidate.strip():
            return candidate.strip()
    return ""


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
    # Detailed design used in every prompt, and whether the sheet predates the description.
    design: CharacterDesign | None = None
    sheet_outdated: bool = False


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
        design=item.design if book_pipeline.design_is_fresh(item) else None,
        sheet_outdated=(
            item.sheet_status == "ready"
            and has_img
            and bool(item.sheet_source)
            and item.sheet_source != design_source(item.name, item.description)
        ),
    )


class StyleAnchorView(BaseModel):
    url: str | None  # typography sample (text image used to detect the caption font)
    anchor_url: str | None = None  # the book's own art style anchor
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
    anchor = store.session_anchor_path(sid)
    return StyleAnchorView(
        url=(
            f"/api/sessions/{sid}/style/image?v={int(local.stat().st_mtime)}"
            if local.exists()
            else None
        ),
        anchor_url=(
            f"/api/sessions/{sid}/style/anchor?v={int(anchor.stat().st_mtime)}"
            if anchor.exists()
            else None
        ),
        inherited_global=False,
        font_family=font_family,
        font_bold=font_bold,
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
    cfg = _session_book_cfg(state)
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
    cfg = _session_book_cfg(state)
    provider_name = body.provider or state.provider
    model_name = body.model or state.model
    if provider_name == "codex":
        model_name = normalize_codex_model(model_name)
    try:
        await asyncio.to_thread(
            _generate_anchor,
            state,
            cfg,
            provider_name,
            model_name,
            body.sample_subject.strip(),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _style_anchor_view(sid, state)


def _generate_anchor(
    state: SessionState,
    cfg: BookConfig,
    provider_name: str,
    model_name: str,
    world: str = "",
) -> None:
    """Draw and save the book's own style anchor (text only: no other book's art)."""
    layout = state.page_layout or default_page_layout_for_category(state.category)
    # The category sample page shows the expected page format. It is sent only here, to a
    # drawing with no characters; pages then follow this book's own anchor, so nothing
    # from the sample (its animals, scene or text) reaches the book's pages.
    layout_ref = store.category_reference_bytes(state.category)
    prompt = style_anchor_prompt(
        style_context=state.context_prompt,
        world=world or book_pipeline.book_world(state),
        page_layout_rule=PAGE_LAYOUT_PROMPTS.get(layout, ""),
        layout_reference=layout_ref is not None,
    )
    provider = get_provider(provider_name, model_name)
    img = generate_matching(
        provider,
        prompt,
        target_size=cfg.image_px,
        negative_prompt=state.negative_prompt or cfg.negative_prompt,
        style_reference=layout_ref,
    )
    line_art = to_line_art(img, state.processing or cfg.processing)
    buf = io.BytesIO()
    line_art.save(buf, "PNG")
    store.save_session_anchor(state.id, buf.getvalue())


def ensure_book_anchor(state: SessionState, provider_name: str, model_name: str) -> None:
    """Every generation of a book starts from its anchor, drawn from the category standard.

    Called before any sheet, page, cover or frame-page generation, so all books of a
    category share the same page format and each book keeps one style from its first image.
    """
    if store.session_anchor_path(state.id).exists():
        return
    _generate_anchor(state, _session_book_cfg(state), provider_name, _model_for(provider_name, model_name))


@router.get("/style/anchor")
def session_style_anchor_image(sid: str) -> FileResponse:
    _session(sid)
    path = store.session_anchor_path(sid)
    if not path.exists():
        raise HTTPException(status_code=404, detail="ancora de estilo do livro nao gerada")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})


@router.delete("/style/anchor", status_code=204)
def delete_session_style_anchor(sid: str) -> None:
    _session(sid)
    store.session_anchor_path(sid).unlink(missing_ok=True)


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
    bible = state.story_bible
    if bible is not None and bible.locked:
        if bible.tone.strip():
            lines.append(f"Tone: {bible.tone.strip()}")
        for char in bible.characters:
            lines.append(f"Character {char.name}: {char.look}")
    for page in state.pages:
        bits = [part for part in (page.title.strip(), page.caption_text.strip()) if part]
        if bits:
            lines.append(f"Page {page.index}: " + " — ".join(bits))
    return "\n".join(lines)


def _cover_lettering(state: SessionState, title: str) -> str:
    """Ask the image model to letter the title as part of the artwork, in the book's mood."""
    clean = (title.strip() or state.title).replace('"', "'")
    author = state.author.strip().replace('"', "'")
    lines = [
        "TITLE LETTERING (part of the artwork, not a caption): letter the book title "
        f'exactly as "{clean}", with every word and letter spelled correctly and '
        "nothing added or left out.",
        "Design the lettering like a bestselling picture-book cover: large, playful and "
        "easy to read, with hand-crafted typography that belongs to this story (shapes, "
        "textures or small motifs taken from its world). Place it wherever it best suits "
        "the composition, usually across the top, and compose the scene around it so it "
        "never covers the character's face or key action. No banner or plain band behind "
        "it unless it is part of the illustration.",
    ]
    if author:
        lines.append(f'Add the author line "{author}" in small, simple letters.')
    lines.append(
        "Render no other text: no subtitle, slogan, page numbers, publisher name, "
        "barcode or watermark."
    )
    return " ".join(lines)


_TEXT_BANS = re.compile(r"^(?:\w+ )?(?:text|lettering|letters|typography)$|speech bubbles?")


def _cover_negative_prompt(book_negative: str, color: bool) -> str:
    """The cover carries its own title, so drop the book's 'no text' bans."""
    base = (
        "watermark, signature, blurry, low quality, distorted proportions"
        if color
        else ", ".join(
            term
            for term in (t.strip() for t in book_negative.split(","))
            if term and not _TEXT_BANS.search(term.lower())
        )
    )
    return f"misspelled title, garbled letters, extra text, {base}".rstrip(", ")


def _compose_cover_prompt(
    state: SessionState, title: str, direction: str, color: bool
) -> str:
    memory = _cover_story_memory(state)
    characters = "\n".join(
        f"- {book_pipeline.design_text(c)}" for c in state.characters if c.name.strip()
    )
    parts = [
        state.context_prompt.strip(),
        "COVER ART DIRECTION:\n"
        + (
            direction.strip()
            or "Design a captivating, friendly cover that represents the whole story."
        ),
        "STORY MEMORY (use only as conceptual reference, do not render body text):\n"
        + memory
        if memory
        else "",
        (
            "MAIN CHARACTERS (identical to their model sheet reference images):\n" + characters
            if characters
            else ""
        ),
        (
            "COVER OVERRIDE: ignore any black-and-white, line-art-only or 'no text' "
            "instruction from the book context for this cover. Create one single "
            "full-bleed cover illustration in FULL COLOR, with a cohesive and inviting "
            "palette suitable for children. Keep the drawing style and characters "
            "consistent with the book."
            if color
            else "COVER OVERRIDE: ignore any 'no text' instruction from the book context "
            "for this cover. Create one single full-page coloring-book cover in clean "
            "black-and-white line art, with the title drawn as outlined letters that "
            "can be colored in."
        ),
        _cover_lettering(state, title),
    ]
    return "\n\n".join(part for part in parts if part)


@router.get("/cover", response_model=CoverView)
def get_cover(sid: str) -> CoverView:
    state = _session(sid)
    return _cover_view(state)


def _model_for(provider_name: str, model_name: str) -> str:
    return normalize_codex_model(model_name) if provider_name == "codex" else model_name


def _character_refs(state: SessionState) -> list[tuple[str, bytes]]:
    refs: list[tuple[str, bytes]] = []
    for character in state.characters:
        path = store.session_characters_dir(state.id) / character.filename
        if path.exists():
            refs.append((character.name, path.read_bytes()))
    return refs


async def _render_cover(
    state: SessionState,
    title: str,
    direction: str,
    color: bool,
    provider_name: str,
    model_name: str,
) -> None:
    """Generate and save the cover; on failure mark it as error and re-raise."""
    cfg = _session_book_cfg(state)
    sid = state.id
    state.cover_title = title.strip()
    state.cover_prompt = direction.strip()
    state.cover_color = color
    await asyncio.to_thread(ensure_book_anchor, state, provider_name, model_name)
    prompt = _compose_cover_prompt(state, title, direction, color)
    negative = _cover_negative_prompt(state.negative_prompt or cfg.negative_prompt, color)
    processing = state.processing or cfg.processing
    style_bytes = store.effective_style_reference_bytes(
        sid, state.category, 1, preset_id=state.preset_id
    )
    refs = _character_refs(state)

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
        return canvas

    state.cover_status = "generating"
    state.cover_error = None
    store.save_session(state)
    try:
        cover = await asyncio.to_thread(_run)
    except Exception as exc:
        state.cover_status = "error"
        state.cover_error = str(exc)
        store.save_session(state)
        raise

    store.cover_dir(sid).mkdir(parents=True, exist_ok=True)
    cover.save(store.cover_path(sid), "PNG", dpi=(cfg.dpi, cfg.dpi))
    state.cover_status = "done"
    state.cover_error = None
    state.cover_updated_at = datetime.now()
    state.pdf_ready = False
    store.save_session(state)


@router.post("/cover/generate", response_model=CoverView)
async def generate_cover(sid: str, body: CoverGenerateRequest) -> CoverView:
    state = _session(sid)
    provider_name = body.provider or state.provider
    model_name = _model_for(provider_name, body.model or state.model)
    title = body.title if body.title is not None else state.cover_title or state.title
    direction = body.prompt if body.prompt is not None else state.cover_prompt
    color = body.color if body.color is not None else state.cover_color
    try:
        await _render_cover(state, title, direction, color, provider_name, model_name)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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


_AUTO_EXTRA_PROMPTS = {
    "front": (
        "Página de introdução com texto centralizado horizontal e verticalmente, sem bordas, "
        "com uma pequena ilustração de {subject} acima do texto. Fundo branco, margens "
        "generosas, tipografia infantil legível, estilo line art do livro. Deixe bastante "
        "espaço livre para o texto e não inclua texto na ilustração."
    ),
    "back": (
        "Organize a página como um único bloco centralizado verticalmente na página: texto "
        "centralizado horizontal e verticalmente, sem bordas, com uma pequena ilustração de "
        "{subject} logo acima do texto e bem próxima dele, com pouco espaço entre a imagem e "
        "o texto. Margens generosas e fundo branco. Não inclua texto na ilustração."
    ),
}


def _auto_extra_target(
    state: SessionState, placement: Literal["front", "back"]
) -> ExtraPage:
    """Reuse this book's auto page or an empty placeholder; otherwise create a new page."""
    collection = (
        state.front_extra_pages if placement == "front" else state.back_extra_pages
    )
    for page in collection:
        if page.auto:
            return page
    for page in collection:
        if not page.text.strip() and not store.extra_page_background_path(
            state.id, page.id
        ).exists():
            return page
    page = ExtraPage(id=store.new_extra_page_id(), placement=placement)
    collection.append(page)
    return page


def _apply_auto_copy(state: SessionState, copy: ExtraPageCopy, fallback: str) -> ExtraPage:
    placement: Literal["front", "back"] = "front" if copy.placement == "front" else "back"
    page = _auto_extra_target(state, placement)
    subject = copy.illustration or fallback
    page.auto = True
    page.title = copy.title
    page.text = copy.text
    page.prompt = _AUTO_EXTRA_PROMPTS[placement].format(subject=subject)
    page.text_align = "center"
    page.text_vertical = "center"
    page.show_border = False
    page.illustration_position = "above"
    page.illustration_size = "small"
    page.include_in_pdf = True
    _render_extra_page(state, page)
    return page


def _finishing_inputs(state: SessionState) -> dict[str, object]:
    """Whole-book context handed to the LLM that writes the book's frame."""
    preset = (
        get_preset(state.preset_id, store.load_custom_presets())
        if state.preset_id
        else None
    )
    char_name_by_id = {c.id: c.name for c in state.characters}
    scripted = [
        ScriptedPage(
            index=page.index,
            title=page.title,
            scene_prompt=page.prompt,
            caption_text=page.caption_text,
            character_names=[
                char_name_by_id[cid] for cid in page.character_ids if cid in char_name_by_id
            ],
        )
        for page in sorted(state.pages, key=lambda item: item.index)
    ]
    return {
        "book_title": state.title,
        "author": state.author,
        "premise": state.story_premise,
        "synopsis": state.story_synopsis,
        "language": state.language or "en",
        "preset": preset,
        "style_context": state.context_prompt,
        "characters": [
            CharacterBrief(id=c.id, name=c.name, description=c.description)
            for c in state.characters
        ],
        "pages": scripted,
        "story_context": _story_context(state),
        "provider": state.provider,
        "model": state.model,
    }


def _apply_frame_pages(state: SessionState, finishing: BookFinishing) -> list[ExtraPage]:
    hero = state.characters[0].name if state.characters else "the main character"
    fallback = f"{hero} smiling"
    return [
        _apply_auto_copy(state, copy, fallback)
        for copy in (finishing.front, finishing.back)
    ]


class AutoExtraPagesResponse(BaseModel):
    pages: list[ExtraPageView]


@router.post("/extra-pages/auto", response_model=AutoExtraPagesResponse)
async def auto_extra_pages(sid: str) -> AutoExtraPagesResponse:
    """Write the welcome and closing pages from the whole book (title, story, cast, style)."""
    state = _session(sid)
    try:
        finishing = await asyncio.to_thread(
            generate_book_finishing, **_finishing_inputs(state)
        )
    except StoryGenerationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    state = _session(sid)
    pages = _apply_frame_pages(state, finishing)
    state.pdf_ready = False
    store.save_session(state)
    return AutoExtraPagesResponse(pages=[_extra_page_view(sid, page) for page in pages])


class ExtraPageVisualRequest(BaseModel):
    provider: str | None = None
    model: str | None = None


async def _render_extra_visual(
    state: SessionState, page: ExtraPage, provider_name: str, model_name: str
) -> None:
    """Lay out and illustrate one text page; on failure mark it as error and re-raise."""
    sid = state.id
    cfg = _session_book_cfg(state)
    processing = state.processing or cfg.processing
    await asyncio.to_thread(ensure_book_anchor, state, provider_name, model_name)
    style_bytes = store.load_session_style_bytes(sid)

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
        raise ValueError(page.error) from exc

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
        return

    art_subject = layout.image_prompt.strip() or page.prompt.strip()
    shown = match_cast_ids([], f"{art_subject} {page.prompt}", [(c.id, c.name) for c in state.characters])
    by_id = {c.id: c for c in state.characters}
    refs = book_pipeline.character_references(state, shown)
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
            cast_block([book_pipeline.design_text(by_id[cid]) for cid in shown]) if shown else "",
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
        raise

    background_path.parent.mkdir(parents=True, exist_ok=True)
    background.save(background_path, "PNG", dpi=(cfg.dpi, cfg.dpi))
    _render_extra_page(state, page)
    state.pdf_ready = False
    store.save_session(state)


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
    provider_name = body.provider or state.provider
    model_name = _model_for(provider_name, body.model or state.model)
    try:
        await _render_extra_visual(state, page, provider_name, model_name)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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
            # Keep the author's original: it is the identity reference for future sheets.
            normalized.save(store.session_characters_dir(sid) / f"{cid}-upload.png", "PNG")
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


def _preset_of(state: SessionState) -> StylePreset | None:
    return get_preset(state.preset_id, store.load_custom_presets()) if state.preset_id else None


async def _ensure_design(state: SessionState, char: BookCharacter) -> None:
    """Expand the character's description into a detailed design (once per description).

    A failure leaves the free-text description in use: the sheet is still generated.
    """
    if book_pipeline.design_is_fresh(char):
        return
    try:
        design = await asyncio.to_thread(
            design_character,
            name=char.name,
            description=char.description,
            category=state.category,
            style_context=state.context_prompt,
            story_context=_story_context(state),
            others=[c.name for c in state.characters if c.id != char.id],
            provider=state.provider,
            model=_model_for(state.provider, state.model),
        )
    except Exception:
        log.warning("character design failed for %s; using its description", char.name)
        return
    char.design = design
    char.design_source = design_source(char.name, char.description)


async def _render_sheet(
    state: SessionState,
    char: BookCharacter,
    provider_name: str,
    model_name: str,
) -> None:
    """Design (if needed) and draw one character's model sheet in the book's own style."""
    cfg = _session_book_cfg(state)
    await asyncio.to_thread(ensure_book_anchor, state, provider_name, model_name)
    await _ensure_design(state, char)
    prompt = build_character_sheet_prompt(
        name=char.name,
        design_text=book_pipeline.design_text(char),
        style_context=state.context_prompt,
        sheet_style=sheet_style_for(_preset_of(state)),
    )
    upload = book_pipeline.upload_path(state, char)
    identity_refs = [(char.name, upload.read_bytes())] if upload.exists() else None
    style_bytes = store.load_session_style_bytes(state.id)

    def _run() -> Image.Image:
        provider = get_provider(provider_name, model_name)
        img = generate_matching(
            provider,
            prompt,
            target_size=SHEET_SIZE,
            negative_prompt=state.negative_prompt or cfg.negative_prompt,
            references=identity_refs,
            style_reference=style_bytes,
        )
        return to_line_art(img, state.processing or cfg.processing)

    sheet = await asyncio.to_thread(_run)
    chars_dir = store.session_characters_dir(state.id)
    chars_dir.mkdir(parents=True, exist_ok=True)
    sheet.save(chars_dir / char.filename, "PNG")
    char.sheet_status = "ready"
    char.approved = True
    char.error = None
    char.sheet_source = design_source(char.name, char.description)
    char.updated_at = datetime.now()


@router.post("/characters/{cid}/generate-sheet", response_model=BookCharacterView)
async def generate_character_sheet(
    sid: str, cid: str, body: CharacterSheetGenerateRequest
) -> BookCharacterView:
    state = _session(sid)
    char = _book_character(state, cid)

    if body.description is not None and body.description.strip():
        char.description = body.description.strip()

    provider_name = body.provider or state.provider
    model_name = _model_for(provider_name, body.model or state.model)

    char.sheet_status = "generating"
    char.error = None
    store.save_session(state)
    try:
        await _render_sheet(state, char, provider_name, model_name)
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
    (store.session_characters_dir(sid) / f"{cid}-upload.png").unlink(missing_ok=True)
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
    warnings: list[str] = Field(default_factory=list)


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
            premise=_story_premise(state, body.premise),
            pages=target_pages,
            mode=body.mode,
            language=body.language,
            preset=preset,
            style_context=state.context_prompt,
            characters=briefs,
            story_context=_story_context(state),
            page_beats=_page_beats(state, target_pages),
            caption_max_words=_caption_cap(state),
            provider=state.provider,
            model=state.model,
        )
    except StoryGenerationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    state.story_mode = body.mode
    state.language = body.language or "en"
    state.story_premise = _story_premise(state, body.premise)
    state.story_synopsis = plan.synopsis
    if any(p.caption_text.strip() for p in plan.pages):
        state.caption_enabled = True
    _apply_reading_font(state)
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
            if not old.beat:
                old.beat = scripted.beat
            new_pages.append(old)
            continue
        new_pages.append(
            PageState(
                index=scripted.index,
                title=scripted.title,
                prompt=scripted.scene_prompt,
                caption_text=scripted.caption_text,
                character_ids=scripted.character_ids or book_pipeline.fallback_cast_ids(state),
                beat=scripted.beat,
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
        warnings=_reading_warnings(state),
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
            story_context=_story_context(state),
            caption_max_words=_caption_cap(state),
            provider=state.provider,
            model=state.model,
        )
    except StoryGenerationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    char_ids = scripted.character_ids or book_pipeline.fallback_cast_ids(state)

    new_page = PageState(
        index=new_index,
        title=scripted.title,
        prompt=scripted.scene_prompt,
        caption_text=scripted.caption_text,
        character_ids=char_ids,
        beat=by_idx[body.after_index].beat if body.after_index in by_idx else "",
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
    # Regenerate title, synopsis, cover and frame pages even when they already exist.
    redo_finishing: bool = False


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


_FINISHING_STEPS = 4  # frame texts, cover, welcome page art, closing page art


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

        # Phase 1: Full Book Blueprint (skipped when resuming an existing plan).
        # The cast is fixed whenever the book already has one (its characters or the
        # locked story bible): the script then uses exactly those characters, with the
        # same ids, designs and sheets, instead of inventing a new cast every run.
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
            if state.story_bible is not None and state.story_bible.locked:
                book_pipeline.sync_bible_cast(state)
            fixed_cast = [
                CharacterBrief(id=c.id, name=c.name, description=c.description)
                for c in state.characters
            ]
            blueprint = await asyncio.to_thread(
                generate_full_book_blueprint,
                pages=target_pages,
                preset=preset,
                style_context=state.context_prompt,
                premise_hint=req.premise_hint or _story_premise(state),
                mode=req.mode,
                language=req.language,
                story_context=_story_context(state),
                page_beats=_page_beats(state, target_pages),
                caption_max_words=_caption_cap(state),
                fixed_cast=fixed_cast or None,
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
            _apply_reading_font(state)

            if not fixed_cast:
                state.characters = [
                    BookCharacter(
                        id=store.new_character_id(),
                        name=gen_char.name,
                        description=gen_char.description,
                        approved=False,
                        sheet_status="empty",
                        updated_at=datetime.now(),
                    )
                    for gen_char in blueprint.characters
                ]

            cast_pairs = [(c.id, c.name) for c in state.characters]
            state.pages = [
                PageState(
                    index=p.index,
                    title=p.title,
                    prompt=p.scene_prompt,
                    caption_text=p.caption_text,
                    beat=p.beat,
                    character_ids=(
                        match_cast_ids(p.character_names, p.scene_prompt, cast_pairs)
                        or book_pipeline.fallback_cast_ids(state)
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
            0 if req.stop_after == "sheets" else len(state.pages) + _FINISHING_STEPS
        )
        done_img_steps = 0

        def _pct() -> int:
            return 15 + int((done_img_steps / max(1, total_img_steps)) * 80)

        # Phase 2: the book's own style anchor, drawn from the category standard (no characters).
        if _is_cancelled():
            _finish_cancelled("style_anchor", _pct(), 1)
            return
        if not store.session_anchor_path(sid).exists():
            _set_autopilot(
                sid,
                status="running",
                phase="style_anchor",
                label="Gerando âncora visual de estilo do livro…",
                percent=_pct(),
                current_step=1,
            )
            await asyncio.to_thread(ensure_book_anchor, state, provider_name, model_name)
        done_img_steps += 1
        if _is_cancelled():
            _finish_cancelled("style_anchor", _pct(), 1)
            return

        # Phase 3: detailed design + model sheet per character (kept when still current).
        for idx, char in enumerate(state.characters, start=1):
            if _is_cancelled():
                _finish_cancelled("sheets", _pct(), 2)
                return
            if book_pipeline.sheet_is_fresh(state, char):
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
            await _render_sheet(state, char, provider_name, model_name)
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

        # Phase 4: every page from the same recipe: style anchor + cast sheets + designs.
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

            job = book_pipeline.page_job(state, page, cfg)

            def _gen_page(
                page_job: book_pipeline.ImageJob = job,
                page_state: PageState = page,
            ) -> tuple[Image.Image, Image.Image]:
                provider = get_provider(provider_name, model_name)
                raw_img = generate_matching(
                    provider,
                    page_job.prompt,
                    target_size=cfg.image_px,
                    negative_prompt=page_job.negative_prompt,
                    references=page_job.references or None,
                    style_reference=page_job.style_reference,
                )
                proc = prepare_page(raw_img, cfg.image_px, page_job.processing)
                proc = finish_new_page(state, page_state, proc, cfg.dpi)
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
            if _is_cancelled():
                _finish_cancelled("pages", _pct(), 3)
                return

        # Phase 5: Book frame — read the finished pages, then write a coherent title,
        # synopsis and cover direction, paint the cover and illustrate the welcome and
        # closing pages. A failure here is reported but still lets the PDF be composed.
        finishing_errors: list[str] = []
        keep_finished = req.resume and not req.redo_finishing
        auto_pages = [
            p for p in (*state.front_extra_pages, *state.back_extra_pages) if p.auto
        ]
        if not (keep_finished and len(auto_pages) >= 2):
            if _is_cancelled():
                _finish_cancelled("finishing", _pct(), 4)
                return
            _set_autopilot(
                sid,
                status="running",
                phase="finishing",
                label="Lendo o livro pronto para criar título, sinopse e páginas finais…",
                percent=_pct(),
                current_step=4,
            )
            try:
                finishing = await asyncio.to_thread(
                    generate_book_finishing, **_finishing_inputs(state)
                )
            except StoryGenerationError as exc:
                finishing_errors.append(f"textos finais: {exc}")
            else:
                if finishing.title:
                    state.title = finishing.title
                    state.cover_title = finishing.title
                if finishing.synopsis:
                    state.story_synopsis = finishing.synopsis
                if finishing.cover_prompt:
                    state.cover_prompt = finishing.cover_prompt
                _apply_frame_pages(state, finishing)
                state.current_step = 4
                store.save_session(state)
        done_img_steps += 1

        cover_ready = state.cover_status == "done" and store.cover_path(sid).exists()
        if state.cover_include and not (keep_finished and cover_ready):
            if _is_cancelled():
                _finish_cancelled("cover", _pct(), 4)
                return
            _set_autopilot(
                sid,
                status="running",
                phase="cover",
                label="Criando a capa…",
                percent=_pct(),
                current_step=4,
            )
            try:
                await _render_cover(
                    state,
                    state.cover_title or state.title,
                    state.cover_prompt,
                    state.cover_color,
                    provider_name,
                    model_name,
                )
            except Exception as exc:
                finishing_errors.append(f"capa: {exc}")
        done_img_steps += 1

        frame_pages = [
            p for p in (*state.front_extra_pages, *state.back_extra_pages) if p.auto
        ]
        for frame_page in frame_pages[:2]:
            if _is_cancelled():
                _finish_cancelled("finishing", _pct(), 4)
                return
            has_art = store.extra_page_background_path(sid, frame_page.id).exists()
            if keep_finished and has_art and frame_page.status == "done":
                done_img_steps += 1
                continue
            label = "boas-vindas" if frame_page.placement == "front" else "agradecimento"
            _set_autopilot(
                sid,
                status="running",
                phase="finishing",
                label=f"Ilustrando a página de {label}…",
                percent=_pct(),
                current_step=4,
            )
            try:
                await _render_extra_visual(state, frame_page, provider_name, model_name)
            except Exception as exc:
                finishing_errors.append(f"página de {label}: {exc}")
            done_img_steps += 1

        # Phase 5: Compose Final KDP PDF
        if _is_cancelled():
            _finish_cancelled("pdf", _pct(), 4)
            return
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
                publisher_logo=store.publisher_logo_for(state),
                profile=get_profile(state.print_profile),
                rtl=reads_right_to_left(state.story_brief),
            )
            state.pdf_ready = True
            store.save_session(state)

        if finishing_errors:
            _set_autopilot(
                sid,
                status="error",
                phase="finishing",
                label="Livro montado, mas parte da finalização falhou. Continue para tentar de novo.",
                percent=99,
                current_step=4,
                error="; ".join(finishing_errors),
            )
            return
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


def autopilot_snapshot(sid: str) -> AutopilotStatus | None:
    """Current autopilot state of a book for the library, or None when idle/finished."""
    status = _autopilot_status.get(sid)
    if status is None or status.status in {"idle", "done"}:
        return None
    return status.model_copy()


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
