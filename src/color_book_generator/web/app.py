"""FastAPI web app for interactive page-by-page generation."""

from __future__ import annotations

import asyncio
import io
import os
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from color_book_generator import __version__
from color_book_generator.auth import codex as codex_auth
from color_book_generator.auth.codex import DEFAULT_CODEX_MODEL, normalize_codex_model
from color_book_generator.config import (
    BookConfig,
    CaptionConfig,
    default_book_config_path,
    load_book_config,
)
from color_book_generator.layout.pdf import compose_pdf
from color_book_generator.processing.line_art import prepare_page
from color_book_generator.prompts import (
    compose_page_prompt,
    default_context_prompt,
    default_subjects,
)
from color_book_generator.providers import available_providers, get_provider
from color_book_generator.web import archive as backup_archive
from color_book_generator.web import store
from color_book_generator.web.auth_flow import flow as codex_flow
from color_book_generator.web.store import PageState, SessionState

STATIC_DIR = Path(__file__).parent / "static"
SID_RE = re.compile(r"^[a-f0-9]{8,32}$")

app = FastAPI(title="Color Book Studio", docs_url="/api/docs", openapi_url="/api/openapi.json")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


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


class CodexModels(BaseModel):
    models: list[str]


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
    caption_enabled: bool | None = None
    caption_style: CaptionStyle | None = None
    caption_position: CaptionPosition | None = None
    caption_font_family: CaptionFontFamily | None = None
    caption_font_bold: bool | None = None
    caption_font_size: float | None = Field(default=None, ge=0.01, le=0.08)


class SessionPatch(BaseModel):
    context_prompt: str | None = None
    title: str | None = None
    provider: str | None = None
    model: str | None = None
    pages: int | None = Field(default=None, ge=1, le=200)
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
    prompt: str | None = None
    caption_text: str | None = None
    character_ids: list[str] | None = None


class CharacterReferencePayload(BaseModel):
    id: str
    name: str
    url: str


class PageView(BaseModel):
    index: int
    prompt: str
    caption_text: str
    character_ids: list[str]
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


def _stamp(state: SessionState, page: PageState) -> str:
    ts = page.updated_at or state.created_at
    return str(int(ts.timestamp()))


def _page_view(state: SessionState, page: PageState) -> PageView:
    base = f"/api/sessions/{state.id}/pages/{page.index}"
    has_image = page.status == "done"
    v = _stamp(state, page)
    return PageView(
        index=page.index,
        prompt=page.prompt,
        caption_text=page.caption_text,
        character_ids=page.character_ids,
        status=page.status,
        error=page.error,
        updated_at=page.updated_at,
        preview_url=f"{base}/preview?v={v}" if has_image else None,
        view_url=f"{base}/view?v={v}" if has_image else None,
        image_url=f"{base}/download?v={v}" if has_image else None,
        width_px=page.width_px,
        height_px=page.height_px,
    )


def _session_view(state: SessionState) -> SessionView:
    cfg = _config()
    has_pdf = state.pdf_ready and (store.session_dir(state.id) / "book.pdf").exists()
    return SessionView(
        id=state.id,
        title=state.title,
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


def _write_page_assets(state: SessionState, page: PageState, img: Image.Image) -> None:
    cfg = _config()
    processed = prepare_page(
        img,
        cfg.image_px,
        cfg.processing,
        _session_caption(state),
        page.caption_text,
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
    return provider.generate(
        prompt,
        negative_prompt=negative,
        target_size=target_px,
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


def _load_references(ids: list[str]) -> list[tuple[str, bytes]]:
    if not ids:
        return []
    records = {record.id: record for record in store.load_characters()}
    references: list[tuple[str, bytes]] = []
    for cid in ids:
        record = records.get(cid)
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
    data = await image.read()
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


@app.post("/api/sessions/{sid}/pages/{index}/use-as-style", response_model=StyleView)
def use_page_as_style(sid: str, index: int) -> StyleView:
    state = _session(sid)
    _page(state, index)
    source = store.pages_dir(sid) / f"page-{index:03d}.png"
    if not source.exists():
        raise HTTPException(status_code=400, detail="pagina ainda nao gerada")
    store.style_dir().mkdir(parents=True, exist_ok=True)
    store.style_path().write_bytes(source.read_bytes())
    return _style_view()


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
    data = await image.read()
    if not data:
        raise HTTPException(status_code=400, detail="envie uma imagem")
    if len(data) > 12 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="imagem maior que 12 MB")
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
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


API_FEATURES = [
    "settings",
    "captions",
    "characters",
    "codex-models",
    "lightbox",
    "backup",
]


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
        codex_model=DEFAULT_CODEX_MODEL,
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
        return CodexModels(models=[])
    return CodexModels(models=codex_auth.available_models())


@app.post("/api/auth/codex/start", response_model=AuthStatus)
def codex_start() -> AuthStatus:
    return AuthStatus(**codex_flow.start())


@app.post("/api/auth/codex/logout", response_model=AuthStatus)
def codex_logout() -> AuthStatus:
    return AuthStatus(**codex_flow.logout())


@app.post("/api/sessions", response_model=SessionView, status_code=201)
def create_session(body: SessionCreate) -> SessionView:
    cfg = _config()
    provider = body.provider or cfg.provider
    model = body.model or cfg.model
    context = (body.context_prompt or default_context_prompt(cfg)).strip()

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
    caption_style = (
        body.caption_style if body.caption_style is not None else cfg.caption.style
    )
    caption_position = (
        body.caption_position if body.caption_position is not None else cfg.caption.position
    )
    caption_font_family = (
        body.caption_font_family
        if body.caption_font_family is not None
        else cfg.caption.font_family
    )
    state = store.create_session(
        pages=pages,
        context_prompt=context,
        title=body.title or cfg.title,
        provider=provider,
        model=model,
        caption_enabled=(
            body.caption_enabled if body.caption_enabled is not None else cfg.caption.enabled
        ),
        caption_style=caption_style,
        caption_position=caption_position,
        caption_font_family=caption_font_family,
        caption_font_bold=(
            body.caption_font_bold
            if body.caption_font_bold is not None
            else cfg.caption.bold
        ),
        caption_font_size=(
            body.caption_font_size
            if body.caption_font_size is not None
            else cfg.caption.font_size_ratio
        ),
    )
    _normalize_provider(state)
    store.save_session(state)
    return _session_view(state)


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
    if body.provider is not None:
        state.provider = body.provider
    if body.model is not None:
        state.model = body.model
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
    cfg = _config()

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
    full_prompt = compose_page_prompt(context, page.prompt, index, len(state.pages))
    references = _load_references(page.character_ids)
    style_reference = store.load_style_bytes()

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
            for _ in range(candidates):
                img = await asyncio.to_thread(
                    _provider_generate,
                    state.provider,
                    state.model,
                    full_prompt,
                    cfg.image_px,
                    cfg.negative_prompt,
                    seed,
                    references,
                    style_reference,
                )
                best = img
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
    cfg = _config()
    pages = sorted(store.pages_dir(sid).glob("page-*.png"))
    if not pages:
        raise HTTPException(status_code=400, detail="no generated pages to compose")
    compose_pdf(
        pages,
        store.session_dir(sid) / "book.pdf",
        cfg,
        include_title_page=cfg.include_title_page,
        blank_verso=cfg.blank_verso,
    )
    state.pdf_ready = True
    store.save_session(state)
    return _session_view(state)


@app.get("/api/sessions/{sid}/pdf")
def download_pdf(sid: str) -> FileResponse:
    state = _session(sid)
    path = store.session_dir(sid) / "book.pdf"
    if not path.exists():
        raise HTTPException(status_code=404, detail="pdf not composed yet")
    slug = re.sub(r"[^a-z0-9]+", "-", state.title.lower()).strip("-") or "color-book"
    return FileResponse(path, media_type="application/pdf", filename=f"{slug}.pdf")
