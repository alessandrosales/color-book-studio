"""FastAPI web app for interactive page-by-page generation."""

from __future__ import annotations

import asyncio
import io
import logging
import os
import re
import shutil
import tempfile
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from color_book_generator import __version__
from color_book_generator.auth import codex as codex_auth
from color_book_generator.auth.codex import DEFAULT_CODEX_MODEL, normalize_codex_model
from color_book_generator.brief import reads_right_to_left
from color_book_generator.config import (
    BookConfig,
    CaptionConfig,
    PageSizeName,
    ProcessingConfig,
    default_book_config_path,
    load_book_config,
)
from color_book_generator.layout.pdf import (
    compose_pdf,
    describe_adjustment,
    interior_adjustments,
    plan_interior,
)
from color_book_generator.presets import default_page_layout_for_category, get_preset
from color_book_generator.print_profile import PRINT_PROFILES, get_profile
from color_book_generator.processing.line_art import prepare_page, score_line_art
from color_book_generator.prompts import (
    compose_page_prompt,
    default_context_prompt,
    default_subjects,
    effective_negative_prompt,
)
from color_book_generator.providers import available_providers, get_provider
from color_book_generator.providers.base import generate_matching
from color_book_generator.story import StoryMode
from color_book_generator.web import archive as backup_archive
from color_book_generator.web import store
from color_book_generator.web.auth_flow import flow as codex_flow
from color_book_generator.web.reading import build_report
from color_book_generator.web.routers.brief import router as brief_router
from color_book_generator.web.routers.presets import router as presets_router
from color_book_generator.web.routers.studio import (
    AutopilotStatus,
    BookCharacterView,
    ExtraPageView,
    _extra_page_view,
    _render_extra_page,
    _style_anchor_view,
    autopilot_snapshot,
    read_upload_limited,
)
from color_book_generator.web.routers.studio import (
    _character_view as _book_character_view,
)
from color_book_generator.web.routers.studio import (
    router as studio_router,
)
from color_book_generator.web.store import PageState, SessionState

STATIC_DIR = Path(__file__).parent / "static"
log = logging.getLogger(__name__)
SID_RE = re.compile(r"^[a-f0-9]{8,32}$")
_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_DEFAULT_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1", "testserver"}

@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # A fresh process has no generation in flight: anything still marked
    # "generating" was interrupted and must become retryable.
    recovered = await asyncio.to_thread(store.recover_interrupted_sessions)
    if recovered:
        log.warning("recovered interrupted generations in: %s", ", ".join(recovered))
    yield


app = FastAPI(
    title="Color Book Studio",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.include_router(presets_router)
app.include_router(studio_router)
app.include_router(brief_router)


def _allowed_hosts() -> set[str]:
    extra = {
        h.strip().lower()
        for h in os.getenv("CBG_ALLOWED_HOSTS", "").split(",")
        if h.strip()
    }
    return _DEFAULT_ALLOWED_HOSTS | extra


@app.middleware("http")
async def protect_host_and_origin(
    request: Request, call_next: RequestResponseEndpoint
) -> Response:
    host_header = (request.headers.get("host") or "").strip()
    if host_header:
        parsed_host = urlsplit(f"http://{host_header}")
        hostname = (parsed_host.hostname or "").lower()
        if hostname not in _allowed_hosts():
            return JSONResponse(status_code=403, content={"detail": "invalid host header"})

    if request.method.upper() in _MUTATING_METHODS:
        fetch_site = (request.headers.get("sec-fetch-site") or "").strip().lower()
        if fetch_site and fetch_site not in {"same-origin", "none"}:
            return JSONResponse(status_code=403, content={"detail": "cross-site request blocked"})
        origin = (request.headers.get("origin") or "").strip()
        if origin:
            if origin.lower() == "null":
                return JSONResponse(
                    status_code=403, content={"detail": "cross-origin request blocked"}
                )
            parsed_origin = urlsplit(origin)
            if not host_header or parsed_origin.netloc.lower() != host_header.lower():
                return JSONResponse(
                    status_code=403, content={"detail": "cross-origin request blocked"}
                )

    return await call_next(request)


@app.middleware("http")
async def disable_static_cache(
    request: Request, call_next: RequestResponseEndpoint
) -> Response:
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static"):
        response.headers["Cache-Control"] = "no-store"
    return response

_locks: dict[tuple[str, int], asyncio.Lock] = defaultdict(asyncio.Lock)


def _config() -> BookConfig:
    path = default_book_config_path()
    if not path.exists():
        raise HTTPException(status_code=500, detail=f"config not found: {path}")
    return load_book_config(path)


def _normalize_provider(state: SessionState) -> None:
    if state.provider == "codex":
        state.model = normalize_codex_model(state.model)


def _session(sid: str) -> SessionState:
    if not SID_RE.match(sid):
        raise HTTPException(status_code=400, detail="invalid session id")
    state = store.load_session(sid)
    if state is None:
        raise HTTPException(status_code=404, detail="session not found")
    store.migrate_referenced_characters(state)
    return state


def _resize_pages(state: SessionState, target: int) -> None:
    current = len(state.pages)
    if target == current:
        return
    cfg = _config()
    if target < current:
        removed = state.pages[target:]
        if any(page.status == "done" for page in removed):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Nao e possivel reduzir paginas que ja tem imagem. "
                    "Gere o PDF ou crie uma nova sessao."
                ),
            )
        state.pages = state.pages[:target]
    else:
        subjects = default_subjects(cfg.model_copy(update={"pages": target}))
        for index in range(current + 1, target + 1):
            prompt = subjects[index - 1] if index - 1 < len(subjects) else cfg.theme
            state.pages.append(PageState(index=index, prompt=prompt))
    state.pdf_ready = False


def _page(state: SessionState, index: int) -> PageState:
    for page in state.pages:
        if page.index == index:
            return page
    raise HTTPException(status_code=404, detail=f"page {index} not found")


class DefaultsView(BaseModel):
    title: str
    theme: str
    context_prompt: str
    provider: str
    model: str
    providers: list[str]
    pages: int
    page_size: str
    trim_in: tuple[float, float]
    dpi: int
    image_px: tuple[int, int]
    codex_authenticated: bool
    codex_model: str
    openai_configured: bool
    caption_enabled: bool
    caption_style: str
    caption_position: str
    caption_font_family: str
    caption_font_bold: bool
    caption_font_size: float


class SessionSummary(BaseModel):
    id: str
    title: str
    pages: int
    done: int
    updated_at: datetime
    provider: str
    model: str
    cover_url: str | None
    preset_id: str | None = None
    category: str = "cozy"
    page_size: str = "letter_8.5x11"
    characters_count: int = 0
    autopilot: AutopilotStatus | None = None


class CodexModels(BaseModel):
    models: list[str]
    cheapest: str | None = None


class AuthStatus(BaseModel):
    authenticated: bool
    pending: bool
    user_code: str | None
    verification_url: str
    error: str | None


CaptionStyle = Literal["balloon", "caption"]
CaptionPosition = Literal["top", "bottom"]
CaptionFontFamily = Literal["sans", "serif", "mono"]


class SessionCreate(BaseModel):
    pages: int = Field(default=12, ge=1, le=200)
    context_prompt: str | None = None
    title: str | None = None
    provider: str | None = None
    model: str | None = None
    subjects: list[str] | None = None
    language: str | None = None
    preset_id: str | None = None
    category: str | None = None
    page_size: PageSizeName | None = None
    page_layout: str | None = None
    negative_prompt: str | None = None
    processing: ProcessingConfig | None = None
    story_mode: StoryMode | None = None
    story_premise: str | None = None
    blank_verso: bool | None = None
    include_title_page: bool | None = None
    include_belongs_to_page: bool | None = None
    include_color_test_page: bool | None = None
    caption_enabled: bool | None = None
    caption_style: CaptionStyle | None = None
    caption_position: CaptionPosition | None = None
    caption_font_family: CaptionFontFamily | None = None
    caption_font_bold: bool | None = None
    caption_font_size: float | None = Field(default=None, ge=0.01, le=0.08)


class SessionPatch(BaseModel):
    context_prompt: str | None = None
    title: str | None = None
    author: str | None = None
    provider: str | None = None
    model: str | None = None
    pages: int | None = Field(default=None, ge=1, le=200)
    language: str | None = None
    preset_id: str | None = None
    category: str | None = None
    page_size: PageSizeName | None = None
    page_layout: str | None = None
    negative_prompt: str | None = None
    processing: ProcessingConfig | None = None
    story_mode: StoryMode | None = None
    story_premise: str | None = None
    story_synopsis: str | None = None
    blank_verso: bool | None = None
    include_title_page: bool | None = None
    include_belongs_to_page: bool | None = None
    include_color_test_page: bool | None = None
    include_publisher_logo: bool | None = None
    print_profile: str | None = None
    cover_include: bool | None = None
    cover_color: bool | None = None
    cover_title: str | None = None
    cover_prompt: str | None = None
    current_step: int | None = Field(default=None, ge=0, le=5)
    caption_enabled: bool | None = None
    caption_style: CaptionStyle | None = None
    caption_position: CaptionPosition | None = None
    caption_font_family: CaptionFontFamily | None = None
    caption_font_bold: bool | None = None
    caption_font_size: float | None = Field(default=None, ge=0.01, le=0.08)


class GenerateRequest(BaseModel):
    prompt: str | None = None
    caption_text: str | None = None
    character_ids: list[str] | None = None
    context_prompt: str | None = None
    provider: str | None = None
    model: str | None = None
    seed: int | None = None
    candidates: int | None = Field(default=None, ge=1, le=4)


class PagePatch(BaseModel):
    title: str | None = None
    prompt: str | None = None
    caption_text: str | None = None
    character_ids: list[str] | None = None


class CharacterReferencePayload(BaseModel):
    id: str
    name: str
    url: str


class PageView(BaseModel):
    index: int
    title: str = ""
    prompt: str
    caption_text: str
    character_ids: list[str]
    beat: str = ""
    status: str
    error: str | None
    updated_at: datetime | None
    preview_url: str | None
    view_url: str | None
    image_url: str | None
    width_px: int | None
    height_px: int | None


class SessionView(BaseModel):
    id: str
    title: str
    author: str = ""
    context_prompt: str
    provider: str
    model: str
    pages: list[PageView]
    pdf_ready: bool
    pdf_url: str | None
    caption_enabled: bool
    caption_style: str
    caption_position: str
    caption_font_family: str
    caption_font_bold: bool
    caption_font_size: float
    trim_in: tuple[float, float]
    dpi: int
    language: str = "en"
    preset_id: str | None = None
    category: str = "cozy"
    page_size: str = "letter_8.5x11"
    page_layout: str = "single_full"
    negative_prompt: str = ""
    processing: ProcessingConfig | None = None
    characters: list[BookCharacterView] = Field(default_factory=list)
    style_url: str | None = None
    style_inherited_global: bool = False
    story_mode: StoryMode = "narrative"
    story_premise: str = ""
    story_synopsis: str = ""
    blank_verso: bool = False
    include_title_page: bool = True
    include_belongs_to_page: bool = False
    include_color_test_page: bool = False
    cover_include: bool = True
    cover_color: bool = True
    cover_title: str = ""
    cover_prompt: str = ""
    cover_status: str = "pending"
    cover_error: str | None = None
    cover_preview_url: str | None = None
    cover_download_url: str | None = None
    front_extra_pages: list[ExtraPageView] = Field(default_factory=list)
    back_extra_pages: list[ExtraPageView] = Field(default_factory=list)
    include_publisher_logo: bool = True
    publisher_logo_url: str | None = None
    print_profile: str = "kdp_paperback"
    current_step: int = 1


def _stamp(state: SessionState, page: PageState) -> str:
    ts = page.updated_at or state.created_at
    return str(int(ts.timestamp()))


def _page_view(state: SessionState, page: PageState) -> PageView:
    base = f"/api/sessions/{state.id}/pages/{page.index}"
    has_image = page.status == "done"
    v = _stamp(state, page)
    return PageView(
        index=page.index,
        title=page.title,
        prompt=page.prompt,
        caption_text=page.caption_text,
        character_ids=page.character_ids,
        beat=page.beat,
        status=page.status,
        error=page.error,
        updated_at=page.updated_at,
        preview_url=f"{base}/preview?v={v}" if has_image else None,
        view_url=f"{base}/view?v={v}" if has_image else None,
        image_url=f"{base}/download?v={v}" if has_image else None,
        width_px=page.width_px,
        height_px=page.height_px,
    )


def _session_cfg(state: SessionState) -> BookConfig:
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
    if state.author.strip():
        updates["author"] = state.author.strip()
    if state.processing is not None:
        updates["processing"] = state.processing
    if state.negative_prompt:
        updates["negative_prompt"] = state.negative_prompt
    return cfg.model_copy(update=updates)


def _session_view(state: SessionState) -> SessionView:
    cfg = _session_cfg(state)
    has_pdf = state.pdf_ready and (store.session_dir(state.id) / "book.pdf").exists()
    style_anchor = _style_anchor_view(state.id)
    return SessionView(
        id=state.id,
        title=state.title,
        author=state.author,
        context_prompt=state.context_prompt,
        provider=state.provider,
        model=state.model,
        pages=[_page_view(state, p) for p in state.pages],
        pdf_ready=has_pdf,
        pdf_url=f"/api/sessions/{state.id}/pdf" if has_pdf else None,
        caption_enabled=state.caption_enabled,
        caption_style=state.caption_style,
        caption_position=state.caption_position,
        caption_font_family=state.caption_font_family,
        caption_font_bold=state.caption_font_bold,
        caption_font_size=state.caption_font_size,
        trim_in=cfg.trim_size_in,
        dpi=cfg.dpi,
        language=state.language,
        preset_id=state.preset_id,
        category=state.category,
        page_size=state.page_size,
        page_layout=state.page_layout,
        negative_prompt=state.negative_prompt or cfg.negative_prompt,
        processing=cfg.processing,
        characters=[_book_character_view(state.id, c) for c in state.characters],
        style_url=style_anchor.url,
        style_inherited_global=style_anchor.inherited_global,
        story_mode=state.story_mode,
        story_premise=state.story_premise,
        story_synopsis=state.story_synopsis,
        blank_verso=state.blank_verso,
        include_title_page=state.include_title_page,
        include_belongs_to_page=state.include_belongs_to_page,
        include_color_test_page=state.include_color_test_page,
        cover_include=state.cover_include,
        cover_color=state.cover_color,
        cover_title=state.cover_title,
        cover_prompt=state.cover_prompt,
        cover_status=state.cover_status,
        cover_error=state.cover_error,
        cover_preview_url=(
            f"/api/sessions/{state.id}/cover/preview?v={int((state.cover_updated_at or state.created_at).timestamp())}"
            if store.cover_path(state.id).exists()
            else None
        ),
        cover_download_url=(
            f"/api/sessions/{state.id}/cover/download?v={int((state.cover_updated_at or state.created_at).timestamp())}"
            if store.cover_path(state.id).exists()
            else None
        ),
        front_extra_pages=[
            _extra_page_view(state.id, page) for page in state.front_extra_pages
        ],
        back_extra_pages=[
            _extra_page_view(state.id, page) for page in state.back_extra_pages
        ],
        include_publisher_logo=state.include_publisher_logo,
        publisher_logo_url=_publisher_logo_url(),
        print_profile=state.print_profile,
        current_step=state.current_step,
    )


def _save_preview(img: Image.Image, path: Path) -> None:
    preview = img.copy()
    preview.thumbnail((640, 900), Image.Resampling.LANCZOS)
    preview.save(path, "PNG", optimize=True)


def _session_caption(state: SessionState) -> CaptionConfig:
    cfg = _config().caption
    return cfg.model_copy(
        update={
            "enabled": state.caption_enabled,
            "style": state.caption_style,
            "position": state.caption_position,
            "font_family": state.caption_font_family,
            "bold": state.caption_font_bold,
            "font_size_ratio": state.caption_font_size,
        }
    )


def _extra_page_images(
    state: SessionState, placement: str
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





def _write_page_assets(state: SessionState, page: PageState, img: Image.Image) -> None:
    cfg = _session_cfg(state)
    has_embedded_text = state.caption_enabled and bool(page.caption_text.strip())
    processing = (
        cfg.processing.model_copy(
            update={"min_component_px": min(cfg.processing.min_component_px, 12)}
        )
        if has_embedded_text
        else cfg.processing
    )
    processed = prepare_page(
        img,
        cfg.image_px,
        processing,
    )
    name = f"page-{page.index:03d}.png"
    img.save(store.raw_dir(state.id) / name, "PNG")
    final = store.pages_dir(state.id) / name
    processed.save(final, "PNG", dpi=(cfg.dpi, cfg.dpi))
    _save_preview(processed, store.preview_dir(state.id) / name)
    page.width_px = processed.width
    page.height_px = processed.height


def _provider_generate(
    provider_name: str,
    model: str,
    prompt: str,
    target_px: tuple[int, int],
    negative: str,
    seed: int | None,
    references: list[tuple[str, bytes]] | None = None,
    style_reference: bytes | None = None,
) -> Image.Image:
    provider = get_provider(provider_name, model)
    return generate_matching(
        provider,
        prompt,
        target_size=target_px,
        negative_prompt=negative,
        seed=seed,
        references=references,
        style_reference=style_reference,
    )


def _character_view(record: store.CharacterRecord) -> CharacterReferencePayload:
    return CharacterReferencePayload(
        id=record.id,
        name=record.name,
        url=f"/api/characters/{record.id}/image",
    )


def _load_references(ids: list[str], state: SessionState | None = None) -> list[tuple[str, bytes]]:
    if not ids:
        return []
    book_records = {c.id: c for c in (state.characters if state is not None else [])}
    global_records = {record.id: record for record in store.load_characters()}
    references: list[tuple[str, bytes]] = []
    for cid in ids:
        if state is not None and cid in book_records:
            char = book_records[cid]
            book_path = store.session_characters_dir(state.id) / char.filename
            if book_path.exists():
                references.append((char.name, book_path.read_bytes()))
                continue
            if cid not in global_records:
                # Character registered in the book without an image yet: skip binary ref gracefully
                continue
        record = global_records.get(cid)
        if record is None:
            raise HTTPException(
                status_code=400, detail=f"personagem nao encontrado: {cid}"
            )
        path = store.characters_dir() / record.filename
        if not path.exists():
            raise HTTPException(
                status_code=400, detail=f"imagem do personagem ausente: {record.name}"
            )
        references.append((record.name, path.read_bytes()))
    return references


class StyleView(BaseModel):
    url: str | None


def _style_view() -> StyleView:
    path = store.style_path()
    if not path.exists():
        return StyleView(url=None)
    return StyleView(url=f"/api/style/image?v={int(path.stat().st_mtime)}")


@app.get("/api/style", response_model=StyleView)
def get_style() -> StyleView:
    return _style_view()


@app.post("/api/style", response_model=StyleView)
async def set_style(image: UploadFile = File(...)) -> StyleView:
    data = await read_upload_limited(image)
    if not data:
        raise HTTPException(status_code=400, detail="envie uma imagem")
    try:
        with Image.open(io.BytesIO(data)) as source:
            source.load()
            normalized = source.convert("L" if source.mode in {"L", "1"} else "RGB")
    except Exception as exc:
        raise HTTPException(status_code=400, detail="arquivo nao e uma imagem valida") from exc
    store.style_dir().mkdir(parents=True, exist_ok=True)
    normalized.save(store.style_path(), "PNG")
    return _style_view()


@app.delete("/api/style", status_code=204)
def delete_style() -> None:
    store.clear_style()


@app.get("/api/style/image")
def style_image() -> FileResponse:
    path = store.style_path()
    if not path.exists():
        raise HTTPException(status_code=404, detail="estilo nao definido")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})


def _publisher_logo_url() -> str | None:
    path = store.publisher_logo_path()
    if not path.exists():
        return None
    return f"/api/publisher-logo/image?v={int(path.stat().st_mtime)}"


class PublisherLogoView(BaseModel):
    url: str | None = None


@app.get("/api/publisher-logo", response_model=PublisherLogoView)
def get_publisher_logo() -> PublisherLogoView:
    return PublisherLogoView(url=_publisher_logo_url())


@app.post("/api/publisher-logo", response_model=PublisherLogoView)
async def set_publisher_logo(image: UploadFile = File(...)) -> PublisherLogoView:
    data = await read_upload_limited(image)
    if not data:
        raise HTTPException(status_code=400, detail="envie uma imagem")
    try:
        with Image.open(io.BytesIO(data)) as source:
            source.load()
            normalized = source.convert("RGBA")
    except Exception as exc:
        raise HTTPException(status_code=400, detail="arquivo nao e uma imagem valida") from exc
    path = store.publisher_logo_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized.save(path, "PNG")
    return PublisherLogoView(url=_publisher_logo_url())


@app.delete("/api/publisher-logo", status_code=204)
def delete_publisher_logo() -> None:
    store.publisher_logo_path().unlink(missing_ok=True)


@app.get("/api/publisher-logo/image")
def publisher_logo_image() -> FileResponse:
    path = store.publisher_logo_path()
    if not path.exists():
        raise HTTPException(status_code=404, detail="logo da editora nao definida")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})


@app.post("/api/sessions/{sid}/pages/{index}/use-as-style", response_model=StyleView)
def use_page_as_style(sid: str, index: int) -> StyleView:
    state = _session(sid)
    _page(state, index)
    source = store.pages_dir(sid) / f"page-{index:03d}.png"
    if not source.exists():
        raise HTTPException(status_code=400, detail="pagina ainda nao gerada")
    data = source.read_bytes()
    store.session_style_dir(sid).mkdir(parents=True, exist_ok=True)
    store.session_style_path(sid).write_bytes(data)
    return StyleView(url=_style_anchor_view(sid).url)


@app.get("/api/characters", response_model=list[CharacterReferencePayload])
def list_characters() -> list[CharacterReferencePayload]:
    return [_character_view(record) for record in store.load_characters()]


@app.post(
    "/api/characters",
    response_model=CharacterReferencePayload,
    status_code=201,
)
async def create_character(
    name: str = Form(...),
    image: UploadFile = File(...),
    session_id: str | None = Form(default=None),
) -> CharacterReferencePayload:
    clean = name.strip()
    if not clean:
        raise HTTPException(status_code=400, detail="informe um nome para o personagem")
    data = await read_upload_limited(image)
    if not data:
        raise HTTPException(status_code=400, detail="envie uma imagem")
    try:
        with Image.open(io.BytesIO(data)) as source:
            source.load()
            normalized = source.convert("RGB")
    except Exception as exc:
        raise HTTPException(status_code=400, detail="arquivo nao e uma imagem valida") from exc

    record = store.CharacterRecord(id=store.new_character_id(), name=clean)
    store.characters_dir().mkdir(parents=True, exist_ok=True)
    normalized.save(store.characters_dir() / record.filename, "PNG")
    records = store.load_characters()
    records.append(record)
    store.save_characters(records)

    if session_id and SID_RE.match(session_id):
        state = store.load_session(session_id)
        if state is not None:
            for page in state.pages:
                if record.id not in page.character_ids:
                    page.character_ids.append(record.id)
            store.save_session(state)

    return _character_view(record)


@app.get("/api/characters/{cid}/image")
def character_image(cid: str) -> FileResponse:
    record = next((r for r in store.load_characters() if r.id == cid), None)
    if record is None:
        raise HTTPException(status_code=404, detail="personagem nao encontrado")
    path = store.characters_dir() / record.filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="imagem nao encontrada")
    return FileResponse(path, media_type="image/png")


@app.delete("/api/characters/{cid}", status_code=204)
def delete_character(cid: str) -> None:
    records = store.load_characters()
    remaining = [r for r in records if r.id != cid]
    if len(remaining) == len(records):
        raise HTTPException(status_code=404, detail="personagem nao encontrado")
    for record in records:
        if record.id == cid:
            (store.characters_dir() / record.filename).unlink(missing_ok=True)
    store.save_characters(remaining)
    for session_dir in store.WEB_ROOT.iterdir():
        if not session_dir.is_dir() or session_dir.name == "characters":
            continue
        state = store.load_session(session_dir.name)
        if state is None:
            continue
        for page in state.pages:
            if cid in page.character_ids:
                page.character_ids = [x for x in page.character_ids if x != cid]
        store.save_session(state)


@app.get("/", include_in_schema=False)
def home() -> FileResponse:
    return FileResponse(STATIC_DIR / "home.html")


@app.get("/books/{sid}", include_in_schema=False)
def book_editor(sid: str) -> Response:
    if not SID_RE.match(sid) or store.load_session(sid) is None:
        return RedirectResponse("/", status_code=302)
    return FileResponse(STATIC_DIR / "index.html")


API_FEATURES = [
    "settings",
    "captions",
    "characters",
    "codex-models",
    "lightbox",
    "backup",
    "sessions-list",
    "multi-book",
    "presets",
    "character-sheets",
    "story-script",
    "kdp-extras",
    "autopilot",
]


class ExportSelectedRequest(BaseModel):
    session_ids: list[str]


@app.get("/api/export")
def export_backup() -> FileResponse:
    path = backup_archive.export_to_temp()
    filename = f"color-book-backup-{datetime.now():%Y%m%d-%H%M}.zip"
    return FileResponse(
        path,
        media_type="application/zip",
        filename=filename,
        background=BackgroundTask(path.unlink),
    )


@app.post("/api/export/selected")
def export_selected(body: ExportSelectedRequest) -> FileResponse:
    valid_ids = [sid for sid in body.session_ids if SID_RE.match(sid)]
    if not valid_ids:
        raise HTTPException(status_code=400, detail="selecione ao menos um livro valido")
    try:
        path = backup_archive.export_selected_to_temp(valid_ids)
    except backup_archive.ArchiveError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if len(valid_ids) == 1:
        state = store.load_session(valid_ids[0])
        slug = (
            re.sub(r"[^a-z0-9]+", "-", (state.title if state else "book").lower()).strip("-")
            or "color-book"
        )
        filename = f"{slug}.zip"
    else:
        filename = f"color-books-{len(valid_ids)}-{datetime.now():%Y%m%d-%H%M}.zip"
    return FileResponse(
        path,
        media_type="application/zip",
        filename=filename,
        background=BackgroundTask(path.unlink),
    )


@app.get("/api/sessions/{sid}/export")
def export_single_session(sid: str) -> FileResponse:
    state = _session(sid)
    try:
        path = backup_archive.export_selected_to_temp([state.id])
    except backup_archive.ArchiveError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    slug = re.sub(r"[^a-z0-9]+", "-", state.title.lower()).strip("-") or "color-book"
    return FileResponse(
        path,
        media_type="application/zip",
        filename=f"{slug}.zip",
        background=BackgroundTask(path.unlink),
    )


@app.post("/api/import", response_model=backup_archive.ImportResult)
async def import_backup(archive: UploadFile = File(...)) -> backup_archive.ImportResult:
    try:
        return await asyncio.to_thread(backup_archive.import_archive, archive.file)
    except backup_archive.ArchiveError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/meta")
def meta() -> dict[str, object]:
    return {"version": __version__, "features": API_FEATURES}


@app.get("/api/defaults", response_model=DefaultsView)
def defaults() -> DefaultsView:
    cfg = _config()
    return DefaultsView(
        title=cfg.title,
        theme=cfg.theme,
        context_prompt=default_context_prompt(cfg),
        provider=cfg.provider,
        model=cfg.model,
        providers=available_providers(),
        pages=cfg.pages,
        page_size=cfg.page_size,
        trim_in=cfg.trim_size_in,
        dpi=cfg.dpi,
        image_px=cfg.image_px,
        codex_authenticated=codex_auth.is_authenticated(),
        codex_model=codex_auth.cheapest_codex_model(
            codex_auth.available_models() if codex_auth.is_authenticated() else None
        ),
        openai_configured=bool(os.getenv("OPENAI_API_KEY")),
        caption_enabled=cfg.caption.enabled,
        caption_style=cfg.caption.style,
        caption_position=cfg.caption.position,
        caption_font_family=cfg.caption.font_family,
        caption_font_bold=cfg.caption.bold,
        caption_font_size=cfg.caption.font_size_ratio,
    )


def _default_settings() -> store.BookSettings:
    cfg = _config()
    return store.BookSettings(
        title=cfg.title,
        pages=cfg.pages,
        context_prompt=default_context_prompt(cfg),
        provider=cfg.provider,
        model=cfg.model,
        caption_enabled=cfg.caption.enabled,
        caption_style=cfg.caption.style,
        caption_position=cfg.caption.position,
        caption_font_family=cfg.caption.font_family,
        caption_font_bold=cfg.caption.bold,
        caption_font_size=cfg.caption.font_size_ratio,
    )


@app.get("/api/settings", response_model=store.BookSettings)
def get_settings() -> store.BookSettings:
    return store.load_settings() or _default_settings()


@app.put("/api/settings", response_model=store.BookSettings)
def put_settings(body: store.BookSettings) -> store.BookSettings:
    if not body.title.strip():
        body.title = _config().title
    body.title = body.title.strip()
    store.save_settings(body)
    return body


@app.get("/api/auth/codex", response_model=AuthStatus)
def codex_status() -> AuthStatus:
    return AuthStatus(**codex_flow.as_dict())


@app.get("/api/auth/codex/models", response_model=CodexModels)
def codex_models() -> CodexModels:
    if not codex_auth.is_authenticated():
        return CodexModels(models=[], cheapest=None)
    models = codex_auth.available_models()
    return CodexModels(
        models=models,
        cheapest=codex_auth.cheapest_codex_model(models) if models else DEFAULT_CODEX_MODEL,
    )


@app.post("/api/auth/codex/start", response_model=AuthStatus)
def codex_start() -> AuthStatus:
    return AuthStatus(**codex_flow.start())


@app.post("/api/auth/codex/logout", response_model=AuthStatus)
def codex_logout() -> AuthStatus:
    return AuthStatus(**codex_flow.logout())


@app.post("/api/sessions", response_model=SessionView, status_code=201)
def create_session(body: SessionCreate) -> SessionView:
    cfg = _config()
    preset = (
        get_preset(body.preset_id, store.load_custom_presets())
        if body.preset_id
        else None
    )
    provider = body.provider or cfg.provider
    model = body.model or cfg.model
    context = (
        body.context_prompt
        or (preset.context_prompt if preset is not None else default_context_prompt(cfg))
    ).strip()

    base_cfg = cfg.model_copy(update={"pages": body.pages})
    if body.subjects:
        subjects = body.subjects
    else:
        subjects = default_subjects(base_cfg)[: body.pages]
        if len(subjects) < body.pages:
            subjects = [subjects[i % len(subjects)] for i in range(body.pages)]

    pages = [
        PageState(index=i + 1, prompt=subject) for i, subject in enumerate(subjects)
    ]
    cap_defaults = preset.caption if preset is not None else cfg.caption
    caption_style = (
        body.caption_style if body.caption_style is not None else cap_defaults.style
    )
    caption_position = (
        body.caption_position if body.caption_position is not None else cap_defaults.position
    )
    caption_font_family = (
        body.caption_font_family
        if body.caption_font_family is not None
        else cap_defaults.font_family
    )
    state = store.create_session(
        pages=pages,
        context_prompt=context,
        title=body.title or cfg.title,
        provider=provider,
        model=model,
        caption_enabled=(
            body.caption_enabled if body.caption_enabled is not None else cap_defaults.enabled
        ),
        caption_style=caption_style,
        caption_position=caption_position,
        caption_font_family=caption_font_family,
        caption_font_bold=(
            body.caption_font_bold
            if body.caption_font_bold is not None
            else cap_defaults.bold
        ),
        caption_font_size=(
            body.caption_font_size
            if body.caption_font_size is not None
            else cap_defaults.font_size_ratio
        ),
        language=body.language or "en",
        preset_id=preset.id if preset is not None else body.preset_id,
        category=body.category or (preset.category if preset is not None else "cozy"),
        page_size=body.page_size or (preset.page_size if preset is not None else cfg.page_size),
        page_layout=body.page_layout
        or default_page_layout_for_category(
            body.category or (preset.category if preset is not None else "cozy")
        ),
        negative_prompt=(
            body.negative_prompt
            if body.negative_prompt is not None
            else (preset.negative_prompt if preset is not None else cfg.negative_prompt)
        ),
        processing=(
            body.processing.model_copy(deep=True)
            if body.processing is not None
            else (preset.processing.model_copy(deep=True) if preset is not None else None)
        ),
        story_mode=body.story_mode or "narrative",
        story_premise=(body.story_premise or "").strip(),
        blank_verso=(
            body.blank_verso
            if body.blank_verso is not None
            else (preset.blank_verso if preset is not None else cfg.blank_verso)
        ),
        include_title_page=(
            body.include_title_page
            if body.include_title_page is not None
            else (preset.include_title_page if preset is not None else cfg.include_title_page)
        ),
        include_belongs_to_page=(
            body.include_belongs_to_page
            if body.include_belongs_to_page is not None
            else (
                preset.include_belongs_to_page
                if preset is not None
                else cfg.include_belongs_to_page
            )
        ),
        include_color_test_page=(
            body.include_color_test_page
            if body.include_color_test_page is not None
            else (
                preset.include_color_test_page
                if preset is not None
                else cfg.include_color_test_page
            )
        ),
    )
    _normalize_provider(state)
    store.save_session(state)
    return _session_view(state)


@app.get("/api/sessions", response_model=list[SessionSummary])
def list_sessions() -> list[SessionSummary]:
    summaries: list[SessionSummary] = []
    if not store.WEB_ROOT.exists():
        return summaries
    for directory in store.WEB_ROOT.iterdir():
        if not directory.is_dir() or not SID_RE.match(directory.name):
            continue
        try:
            state = store.load_session(directory.name)
        except ValueError:
            log.warning("ignoring corrupted session %s", directory.name)
            continue
        if state is None:
            continue
        done_pages = [page for page in state.pages if page.status == "done"]
        cover = done_pages[0] if done_pages else None
        summaries.append(
            SessionSummary(
                id=state.id,
                title=state.title,
                pages=len(state.pages),
                done=len(done_pages),
                updated_at=backup_archive.session_activity(state),
                provider=state.provider,
                model=state.model,
                cover_url=(
                    f"/api/sessions/{state.id}/pages/{cover.index}/view"
                    f"?v={int((cover.updated_at or state.created_at).timestamp())}"
                    if cover is not None
                    else None
                ),
                preset_id=state.preset_id,
                category=state.category,
                page_size=state.page_size,
                characters_count=len(state.characters),
                autopilot=autopilot_snapshot(state.id),
            )
        )
    summaries.sort(
        key=lambda item: (item.done > 0, item.updated_at), reverse=True
    )
    return summaries


@app.delete("/api/sessions/{sid}", status_code=204)
def delete_session(sid: str) -> None:
    if not SID_RE.match(sid):
        raise HTTPException(status_code=400, detail="invalid session id")
    with store.session_lock(sid):
        state = _session(sid)
        shutil.rmtree(store.session_dir(state.id))


@app.get("/api/sessions/{sid}", response_model=SessionView)
def get_session(sid: str) -> SessionView:
    return _session_view(_session(sid))


@app.patch("/api/sessions/{sid}", response_model=SessionView)
def patch_session(sid: str, body: SessionPatch) -> SessionView:
    state = _session(sid)
    if body.context_prompt is not None:
        state.context_prompt = body.context_prompt.strip()
    if body.title is not None:
        state.title = body.title.strip() or state.title
    if body.author is not None:
        state.author = body.author.strip()
    if body.provider is not None:
        state.provider = body.provider
    if body.model is not None:
        state.model = body.model
    if body.language is not None:
        state.language = body.language
        state.pdf_ready = False
    if body.preset_id is not None:
        state.preset_id = body.preset_id
    if body.category is not None:
        state.category = body.category
    if body.page_size is not None:
        state.page_size = body.page_size
        state.pdf_ready = False
    if body.page_layout is not None:
        state.page_layout = body.page_layout
    if body.negative_prompt is not None:
        state.negative_prompt = body.negative_prompt.strip()
    if body.processing is not None:
        state.processing = body.processing
    if body.story_mode is not None:
        state.story_mode = body.story_mode
    if body.story_premise is not None:
        state.story_premise = body.story_premise.strip()
    if body.story_synopsis is not None:
        state.story_synopsis = body.story_synopsis.strip()
    if body.blank_verso is not None:
        state.blank_verso = body.blank_verso
        state.pdf_ready = False
    if body.include_title_page is not None:
        state.include_title_page = body.include_title_page
        state.pdf_ready = False
    if body.include_belongs_to_page is not None:
        state.include_belongs_to_page = body.include_belongs_to_page
        state.pdf_ready = False
    if body.include_color_test_page is not None:
        state.include_color_test_page = body.include_color_test_page
        state.pdf_ready = False
    if body.include_publisher_logo is not None:
        state.include_publisher_logo = body.include_publisher_logo
        state.pdf_ready = False
    if body.print_profile is not None:
        if body.print_profile not in PRINT_PROFILES:
            raise HTTPException(status_code=400, detail="perfil de impressao desconhecido")
        state.print_profile = body.print_profile
        state.pdf_ready = False
    if body.cover_include is not None:
        state.cover_include = body.cover_include
        state.pdf_ready = False
    if body.cover_color is not None:
        state.cover_color = body.cover_color
        state.pdf_ready = False
    if body.cover_title is not None:
        state.cover_title = body.cover_title.strip()
    if body.cover_prompt is not None:
        state.cover_prompt = body.cover_prompt
    if body.current_step is not None:
        state.current_step = body.current_step
    if body.caption_enabled is not None:
        state.caption_enabled = body.caption_enabled
    if body.caption_style is not None:
        state.caption_style = body.caption_style
    if body.caption_position is not None:
        state.caption_position = body.caption_position
    if body.caption_font_family is not None:
        state.caption_font_family = body.caption_font_family
    if body.caption_font_bold is not None:
        state.caption_font_bold = body.caption_font_bold
    if body.caption_font_size is not None:
        state.caption_font_size = body.caption_font_size
    if body.pages is not None:
        _resize_pages(state, body.pages)
    _normalize_provider(state)
    store.save_session(state)
    return _session_view(state)


@app.post("/api/sessions/{sid}/pages/{index}/generate", response_model=PageView)
async def generate_page(sid: str, index: int, body: GenerateRequest) -> PageView:
    state = _session(sid)
    page = _page(state, index)
    cfg = _session_cfg(state)

    context = (body.context_prompt or state.context_prompt).strip()
    if body.provider:
        state.provider = body.provider
    if body.model:
        state.model = body.model
    _normalize_provider(state)
    if body.prompt is not None and body.prompt.strip():
        page.prompt = body.prompt.strip()
    if body.caption_text is not None:
        page.caption_text = body.caption_text.strip()
    if body.character_ids is not None:
        page.character_ids = body.character_ids
    seed = body.seed if body.seed is not None else cfg.seed
    candidates = body.candidates or cfg.candidates
    effective_layout = state.page_layout or default_page_layout_for_category(state.category)
    embedded_text = page.caption_text.strip() if state.caption_enabled else ""
    full_prompt = compose_page_prompt(
        context,
        page.prompt,
        index,
        len(state.pages),
        page_layout=effective_layout,
        caption_text=embedded_text,
        caption_position=state.caption_position,
        caption_font_family=state.caption_font_family,
        caption_font_bold=state.caption_font_bold,
    )
    negative_prompt = effective_negative_prompt(
        cfg.negative_prompt,
        has_embedded_text=bool(embedded_text),
        page_layout=effective_layout,
    )
    # Enrich prompt with textual character descriptions for selected book characters
    char_notes = [
        f"{c.name}: {c.description}"
        for c in state.characters
        if c.id in page.character_ids and c.description.strip()
    ]
    if char_notes:
        full_prompt = f"{full_prompt}\n\nCharacters in this scene: {'; '.join(char_notes)}."
    references = _load_references(page.character_ids, state)
    style_reference = store.effective_style_reference_bytes(
        sid, state.category, index, preset_id=state.preset_id
    )

    lock = _locks[(sid, index)]
    if lock.locked():
        raise HTTPException(status_code=409, detail="page is already generating")

    async with lock:
        page.status = "generating"
        page.error = None
        page.seed = seed
        store.save_session(state)
        try:
            best: Image.Image | None = None
            best_score = float("-inf")
            for _ in range(candidates):
                img = await asyncio.to_thread(
                    _provider_generate,
                    state.provider,
                    state.model,
                    full_prompt,
                    cfg.image_px,
                    negative_prompt,
                    seed,
                    references,
                    style_reference,
                )
                if candidates == 1:
                    best = img
                    break
                processed = await asyncio.to_thread(
                    prepare_page, img, cfg.image_px, cfg.processing
                )
                score = score_line_art(processed)
                if score > best_score:
                    best, best_score = img, score
                if best_score >= 0.98:
                    break
            assert best is not None
            await asyncio.to_thread(_write_page_assets, state, page, best)
            page.status = "done"
            page.updated_at = datetime.now()
            state.pdf_ready = False
        except Exception as exc:  # network/provider/processing failures surface in UI
            page.status = "error"
            page.error = str(exc)
            page.updated_at = datetime.now()
        store.save_session(state)

    return _page_view(state, page)


@app.patch("/api/sessions/{sid}/pages/{index}", response_model=PageView)
def patch_page(sid: str, index: int, body: PagePatch) -> PageView:
    state = _session(sid)
    page = _page(state, index)
    if body.title is not None:
        page.title = body.title.strip()
    if body.prompt is not None:
        page.prompt = body.prompt.strip()
    if body.caption_text is not None:
        page.caption_text = body.caption_text.strip()
    if body.character_ids is not None:
        page.character_ids = body.character_ids
    store.save_session(state)
    return _page_view(state, page)


@app.get("/api/sessions/{sid}/pages/{index}/preview")
def page_preview(sid: str, index: int) -> FileResponse:
    state = _session(sid)
    _page(state, index)
    path = store.preview_dir(sid) / f"page-{index:03d}.png"
    if not path.exists():
        raise HTTPException(status_code=404, detail="preview not generated yet")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})


@app.get("/api/sessions/{sid}/pages/{index}/view")
def page_view(sid: str, index: int) -> FileResponse:
    state = _session(sid)
    _page(state, index)
    path = store.pages_dir(sid) / f"page-{index:03d}.png"
    if not path.exists():
        raise HTTPException(status_code=404, detail="image not generated yet")
    return FileResponse(
        path, media_type="image/png", headers={"Cache-Control": "no-store"}
    )


@app.get("/api/sessions/{sid}/pages/{index}/download")
def page_download(sid: str, index: int) -> FileResponse:
    state = _session(sid)
    _page(state, index)
    path = store.pages_dir(sid) / f"page-{index:03d}.png"
    if not path.exists():
        raise HTTPException(status_code=404, detail="image not generated yet")
    return FileResponse(
        path, media_type="image/png", filename=f"page-{index:03d}.png"
    )


@app.post("/api/sessions/{sid}/compose", response_model=SessionView)
def compose(sid: str) -> SessionView:
    state = _session(sid)
    cfg = _session_cfg(state)
    pages = store.session_page_files(state)
    if not pages:
        raise HTTPException(status_code=400, detail="no generated pages to compose")
    cover_path = store.cover_path(sid)
    compose_pdf(
        pages,
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
        rtl=_is_rtl(state),
    )
    state.pdf_ready = True
    store.save_session(state)
    return _session_view(state)


def _is_rtl(state: SessionState) -> bool:
    """Right-to-left reading (manga answered so in the briefing)."""
    return reads_right_to_left(state.story_brief)


@app.get("/api/print-profiles")
def list_print_profiles() -> list[dict[str, object]]:
    return [
        {
            "id": p.id,
            "name": p.name,
            "description": p.description,
            "page_multiple": p.page_multiple,
            "min_pages": p.min_pages,
            "max_pages": p.max_pages,
            "supported_sizes": sorted(p.supported_sizes),
        }
        for p in PRINT_PROFILES.values()
    ]


@app.get("/api/sessions/{sid}/kdp-preflight")
def kdp_preflight(sid: str) -> dict[str, object]:
    """Report known issues for a paperback interior before publication."""
    state = _session(sid)
    cfg = _session_cfg(state)
    errors: list[str] = []
    warnings: list[str] = []
    pages = store.session_page_files(state)
    if len(pages) != len(state.pages):
        errors.append("Há ilustrações pendentes ou arquivos de página ausentes.")

    for extra in state.front_extra_pages + state.back_extra_pages:
        if extra.include_in_pdf and (extra.status != "done" or not store.extra_page_path(sid, extra.id).exists()):
            errors.append(f"A página extra '{extra.title or extra.id}' ainda não está pronta.")

    profile = get_profile(state.print_profile)
    rtl = _is_rtl(state)
    plan_args = {
        "include_title_page": state.include_title_page,
        "blank_verso": state.blank_verso,
        "include_belongs_to_page": state.include_belongs_to_page,
        "include_color_test_page": state.include_color_test_page,
        "front_extra_images": _extra_page_images(state, "front"),
        "back_extra_images": _extra_page_images(state, "back"),
        "publisher_logo": store.publisher_logo_for(state),
        "rtl": rtl,
    }
    # Plan with every scripted page, generated or not, so the count is known up front.
    planned = [store.page_file(sid, page.index) for page in sorted(state.pages, key=lambda p: p.index)]
    physical_pages = len(plan_interior(planned, **plan_args))  # type: ignore[arg-type]
    ops = plan_interior(planned, profile=profile, **plan_args)  # type: ignore[arg-type]
    rounded_pages = len(ops)
    if rounded_pages < profile.min_pages:
        errors.append(
            f"{profile.name} exige pelo menos {profile.min_pages} páginas. Contagem prevista: {rounded_pages}."
        )
    if rounded_pages > profile.max_pages:
        errors.append(
            f"{profile.name} aceita no máximo {profile.max_pages} páginas. Contagem prevista: {rounded_pages}."
        )
    if rounded_pages > physical_pages:
        warnings.append(
            f"{rounded_pages - physical_pages} página(s) em branco serão adicionadas antes das páginas finais "
            f"para fechar múltiplo de {profile.page_multiple}."
        )
    if profile.supported_sizes and cfg.page_size not in profile.supported_sizes:
        errors.append(f"O formato escolhido não é um tamanho padrão de {profile.name}.")
    gutter = profile.gutter_in(rounded_pages)
    warnings.append(
        f"Margem interna (lombada) de {gutter * 25.4:.1f} mm para {rounded_pages} páginas; "
        f"externa de {profile.outside(cfg.bleed_in > 0) * 25.4:.1f} mm."
    )
    if profile.spine_per_page_in:
        spine = profile.spine_in(rounded_pages)
        warnings.append(f"Lombada estimada: {spine * 25.4:.1f} mm.")
    try:
        adjustments = interior_adjustments(ops, cfg, profile, rtl=rtl)
    except (OSError, ValueError):
        adjustments = []
    warnings.extend(describe_adjustment(adj) for adj in adjustments)
    warnings.extend(profile.notes)
    for path in pages:
        try:
            with Image.open(path) as image:
                width, height = image.size
            trim_w, trim_h = cfg.trim_size_in
            if min(width / trim_w, height / trim_h) < 300:
                warnings.append(f"{path.name} tem menos de 300 pixels por polegada no tamanho de impressão.")
        except (OSError, ValueError):
            errors.append(f"Não foi possível ler a imagem {path.name}.")
    warnings.extend(build_report(state, cfg, _session_caption(state)).warnings)
    interior_errors = errors.copy()
    errors.append("A capa completa em PDF separado ainda não está disponível.")
    if state.cover_include:
        warnings.append("A prévia atual pode incluir a capa no miolo; a contagem acima considera a capa separada.")
    warnings.append("A resolução dos arquivos processados não comprova a qualidade da imagem original.")
    warnings.append("Revise direitos, metadados, conteúdo gerado por IA e fontes antes de publicar.")
    warnings.append("A exportação do miolo ainda usa fontes PDF padrão; confira a incorporação e os caracteres antes do envio.")
    warnings.append("Confira os arquivos no KDP Print Previewer e revise uma prova física.")
    return {"physical_pages": physical_pages, "kdp_pages": rounded_pages,
            "profile": profile.id, "profile_name": profile.name,
            "gutter_mm": round(gutter * 25.4, 1),
            "adjusted_pages": [adj.page_number for adj in adjustments],
            "interior_errors": interior_errors, "errors": errors, "warnings": warnings}


@app.post("/api/sessions/{sid}/kdp-interior")
def export_kdp_interior(sid: str) -> FileResponse:
    """Create a separate, no-bleed interior for review and download."""
    if not SID_RE.fullmatch(sid):
        raise HTTPException(status_code=400, detail="invalid session id")
    with store.session_lock(sid):
        state = _session(sid)
        report = kdp_preflight(sid)
        interior_errors = report["interior_errors"]
        if interior_errors:
            raise HTTPException(status_code=400, detail=interior_errors)

        with tempfile.NamedTemporaryFile(prefix="cbg-interior-", suffix=".pdf", delete=False) as handle:
            path = Path(handle.name)
        try:
            compose_pdf(
                store.session_page_files(state), path, _session_cfg(state),
                include_title_page=state.include_title_page,
                blank_verso=state.blank_verso,
                include_belongs_to_page=state.include_belongs_to_page,
                include_color_test_page=state.include_color_test_page,
                front_extra_images=_extra_page_images(state, "front"),
                back_extra_images=_extra_page_images(state, "back"),
                synopsis=state.story_synopsis,
                publisher_logo=store.publisher_logo_for(state),
                profile=get_profile(state.print_profile),
                rtl=_is_rtl(state),
            )
        except BaseException:
            path.unlink(missing_ok=True)
            raise
    slug = re.sub(r"[^a-z0-9]+", "-", state.title.lower()).strip("-") or "color-book"
    return FileResponse(path, media_type="application/pdf", filename=f"{slug}-interior.pdf",
                        background=BackgroundTask(path.unlink, missing_ok=True))


@app.get("/api/sessions/{sid}/pdf")
def download_pdf(sid: str) -> FileResponse:
    state = _session(sid)
    path = store.session_dir(sid) / "book.pdf"
    if not path.exists():
        raise HTTPException(status_code=404, detail="pdf not composed yet")
    slug = re.sub(r"[^a-z0-9]+", "-", state.title.lower()).strip("-") or "color-book"
    return FileResponse(path, media_type="application/pdf", filename=f"{slug}.pdf")
