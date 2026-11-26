"""4-stage book creation studio endpoints: preset apply, per-book style, character sheets, story scripting."""

from __future__ import annotations

import asyncio
import io
import re
import threading
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from PIL import Image
from pydantic import BaseModel, Field

from color_book_generator.auth.codex import normalize_codex_model
from color_book_generator.config import (
    BookConfig,
    CaptionConfig,
    default_book_config_path,
    load_book_config,
)
from color_book_generator.layout.pdf import compose_pdf
from color_book_generator.presets import get_preset
from color_book_generator.processing.line_art import prepare_page, to_line_art
from color_book_generator.prompts import compose_page_prompt
from color_book_generator.providers import get_provider
from color_book_generator.providers.base import generate_matching
from color_book_generator.story import (
    CharacterBrief,
    StoryGenerationError,
    StoryMode,
    build_character_sheet_prompt,
    generate_full_book_blueprint,
    generate_story_plan,
)
from color_book_generator.web import store
from color_book_generator.web.store import BookCharacter, PageState, SessionState

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


def _style_anchor_view(sid: str) -> StyleAnchorView:
    local = store.session_style_path(sid)
    if local.exists():
        return StyleAnchorView(
            url=f"/api/sessions/{sid}/style/image?v={int(local.stat().st_mtime)}",
            inherited_global=False,
        )
    global_path = store.style_path()
    if global_path.exists():
        return StyleAnchorView(
            url=f"/api/style/image?v={int(global_path.stat().st_mtime)}",
            inherited_global=True,
        )
    return StyleAnchorView(url=None, inherited_global=False)


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
    _session(sid)
    return _style_anchor_view(sid)


@router.post("/style", response_model=StyleAnchorView)
async def upload_session_style(sid: str, image: UploadFile = File(...)) -> StyleAnchorView:
    _session(sid)
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
    return _style_anchor_view(sid)


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

    def _run() -> Image.Image:
        provider = get_provider(provider_name, model_name)
        img = generate_matching(
            provider,
            prompt,
            target_size=(1024, 1024),
            negative_prompt=negative,
        )
        return to_line_art(img, processing)

    try:
        line_art = await asyncio.to_thread(_run)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    store.session_style_dir(sid).mkdir(parents=True, exist_ok=True)
    line_art.save(store.session_style_path(sid), "PNG")
    return _style_anchor_view(sid)


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
    style_bytes = store.load_session_style_bytes(sid)

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


# --- 1-Click Full-Book Autopilot (Style -> Blueprint -> Anchor -> Sheets -> Pages -> PDF) ---


class AutopilotRequest(BaseModel):
    preset_id: str | None = None
    pages: int | None = Field(default=None, ge=1, le=200)
    premise_hint: str = ""
    language: str = "en"
    mode: StoryMode | None = None
    stop_after: Literal["blueprint", "sheets", "pdf"] = "pdf"


class AutopilotStatus(BaseModel):
    status: Literal["idle", "running", "done", "cancelled", "error"] = "idle"
    phase: str = ""
    label: str = ""
    percent: int = 0
    current_step: int = 1
    error: str | None = None


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
) -> None:
    _autopilot_status[sid] = AutopilotStatus(
        status=status,
        phase=phase,
        label=label,
        percent=max(0, min(100, percent)),
        current_step=current_step,
        error=error,
    )


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
        if req.preset_id:
            preset = get_preset(req.preset_id, store.load_custom_presets())
            if preset is not None:
                state.preset_id = preset.id
                state.category = preset.category
                state.page_size = preset.page_size
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
        else:
            preset = (
                get_preset(state.preset_id, store.load_custom_presets())
                if state.preset_id
                else None
            )

        target_pages = req.pages or len(state.pages) or 12
        provider_name = state.provider
        model_name = (
            normalize_codex_model(state.model) if provider_name == "codex" else state.model
        )

        # Phase 1: Full Book Blueprint (Title, Synopsis, Cast, Style Anchor Subject, All Pages)
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

        if sid in _autopilot_cancel:
            _set_autopilot(
                sid, status="cancelled", phase="blueprint", label="Autopilot cancelado.", percent=10, current_step=1
            )
            return

        state.title = blueprint.title
        state.language = req.language or "en"
        state.story_premise = blueprint.premise
        state.story_synopsis = blueprint.synopsis
        state.story_mode = blueprint.mode
        if any(p.caption_text.strip() for p in blueprint.pages):
            state.caption_enabled = True

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

        # Phase 2: Generate Style Anchor Image
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
            f"Style anchor showcase subject: {blueprint.style_anchor_subject}."
        )

        def _gen_anchor() -> Image.Image:
            provider = get_provider(provider_name, model_name)
            img = generate_matching(
                provider,
                anchor_prompt,
                target_size=(1024, 1024),
                negative_prompt=cfg.negative_prompt,
            )
            return to_line_art(img, cfg.processing)

        anchor_img = await asyncio.to_thread(_gen_anchor)
        store.session_style_dir(sid).mkdir(parents=True, exist_ok=True)
        anchor_img.save(store.session_style_path(sid), "PNG")
        done_img_steps += 1
        style_bytes = store.session_style_path(sid).read_bytes()

        # Phase 3: Generate Character Sheets
        for idx, char in enumerate(state.characters, start=1):
            if sid in _autopilot_cancel:
                _set_autopilot(
                    sid, status="cancelled", phase="sheets", label="Autopilot cancelado.", percent=_pct(), current_step=2
                )
                return
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
            store.session_characters_dir(sid).mkdir(parents=True, exist_ok=True)
            sheet_img.save(store.session_characters_dir(sid) / char.filename, "PNG")
            char.sheet_status = "ready"
            char.approved = True
            char.updated_at = datetime.now()
            state.current_step = 2
            store.save_session(state)
            done_img_steps += 1

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

        # Phase 4: Generate Every Page with Style Anchor + Character Sheets
        caption_cfg = _session_caption_cfg(state)
        char_by_id = {c.id: c for c in state.characters}
        for page in state.pages:
            if sid in _autopilot_cancel:
                _set_autopilot(
                    sid, status="cancelled", phase="pages", label="Autopilot cancelado.", percent=_pct(), current_step=4
                )
                return
            _set_autopilot(
                sid,
                status="running",
                phase="pages",
                label=f"Ilustrando página {page.index} de {len(state.pages)}…",
                percent=_pct(),
                current_step=4,
            )
            page.status = "generating"
            page.error = None
            state.current_step = 4
            store.save_session(state)

            full_prompt = compose_page_prompt(
                state.context_prompt, page.prompt, page.index, len(state.pages)
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
                cpath = store.session_characters_dir(sid) / char_item.filename
                if cpath.exists():
                    refs.append((char_item.name, cpath.read_bytes()))

            def _gen_page(
                prompt_text: str = full_prompt,
                page_refs: list[tuple[str, bytes]] = refs,
                page_caption: str = page.caption_text,
            ) -> tuple[Image.Image, Image.Image]:
                provider = get_provider(provider_name, model_name)
                raw_img = generate_matching(
                    provider,
                    prompt_text,
                    target_size=cfg.image_px,
                    negative_prompt=cfg.negative_prompt,
                    references=page_refs or None,
                    style_reference=style_bytes,
                )
                proc = prepare_page(
                    raw_img, cfg.image_px, cfg.processing, caption_cfg, page_caption
                )
                return raw_img, proc

            raw_img, processed = await asyncio.to_thread(_gen_page)
            fname = f"page-{page.index:03d}.png"
            raw_img.save(store.raw_dir(sid) / fname, "PNG")
            processed.save(store.pages_dir(sid) / fname, "PNG", dpi=(cfg.dpi, cfg.dpi))
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
            label="Montando PDF KDP final…",
            percent=97,
            current_step=4,
        )
        page_files = sorted(store.pages_dir(sid).glob("page-*.png"))
        if page_files:
            await asyncio.to_thread(
                compose_pdf,
                page_files,
                store.session_dir(sid) / "book.pdf",
                cfg,
                include_title_page=state.include_title_page,
                blank_verso=state.blank_verso,
                include_belongs_to_page=state.include_belongs_to_page,
                include_color_test_page=state.include_color_test_page,
            )
            state.pdf_ready = True
            store.save_session(state)

        _set_autopilot(
            sid,
            status="done",
            phase="done",
            label="Livro completo gerado com sucesso! PDF KDP pronto.",
            percent=100,
            current_step=4,
        )
    except Exception as exc:
        _set_autopilot(
            sid,
            status="error",
            phase="error",
            label="Autopilot interrompido com erro.",
            percent=0,
            current_step=1,
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
        label="Iniciando criação automática completa…",
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
    _session(sid)
    _autopilot_cancel.add(sid)
    current = _autopilot_status.get(sid)
    if current and current.status == "running":
        current.label = "Cancelando após a etapa atual…"
        return current
    return current or AutopilotStatus()
