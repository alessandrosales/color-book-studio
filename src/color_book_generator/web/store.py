"""Session state and on-disk persistence for the web UI."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import secrets
import threading
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, PrivateAttr, ValidationError, model_validator

from color_book_generator.config import PageSizeName, ProcessingConfig
from color_book_generator.fsutil import atomic_write_text
from color_book_generator.presets import StylePreset
from color_book_generator.story import StoryMode

try:  # POSIX advisory locks: coordinate several server processes on one data dir
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

log = logging.getLogger(__name__)

ID_PATTERN = r"^[a-f0-9]{8,32}$"
ID_RE = re.compile(ID_PATTERN)


def _default_web_root() -> Path:
    override = os.getenv("CBG_DATA_DIR")
    if override:
        return Path(override).expanduser()
    candidate = Path(__file__).resolve().parents[3]
    if (candidate / "pyproject.toml").exists():
        return candidate / "output" / "web"
    return Path.cwd() / "output" / "web"


WEB_ROOT = _default_web_root()

PageStatus = Literal["pending", "generating", "done", "error"]
SheetStatus = Literal["empty", "generating", "ready", "error"]


def _drop_nulls(data: object) -> object:
    if isinstance(data, dict):
        return {key: value for key, value in data.items() if value is not None}
    return data


class BookSettings(BaseModel):
    title: str = ""
    pages: int = Field(default=12, ge=1, le=200)
    context_prompt: str = ""
    provider: str = ""
    model: str = ""
    caption_enabled: bool = False
    caption_style: Literal["balloon", "caption"] = "balloon"
    caption_position: Literal["top", "bottom"] = "bottom"
    caption_font_family: Literal["sans", "serif", "mono"] = "sans"
    caption_font_bold: bool = False
    caption_font_size: float = 0.032

    @model_validator(mode="before")
    @classmethod
    def _coerce_nulls(cls, data: Any) -> Any:
        return _drop_nulls(data)


class BookCharacter(BaseModel):
    id: str = Field(pattern=ID_PATTERN)
    name: str
    description: str = ""
    approved: bool = False
    sheet_status: SheetStatus = "empty"
    error: str | None = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime | None = None

    @property
    def filename(self) -> str:
        return f"{self.id}.png"

    @model_validator(mode="before")
    @classmethod
    def _coerce_nulls(cls, data: Any) -> Any:
        return _drop_nulls(data)


class PageState(BaseModel):
    index: int
    title: str = ""
    prompt: str
    caption_text: str = ""
    character_ids: list[str] = Field(default_factory=list)
    status: PageStatus = "pending"
    error: str | None = None
    seed: int | None = None
    width_px: int | None = None
    height_px: int | None = None
    updated_at: datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def _coerce_nulls(cls, data: Any) -> Any:
        return _drop_nulls(data)


class ExtraPage(BaseModel):
    id: str = Field(pattern=ID_PATTERN)
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
    status: PageStatus = "pending"
    error: str | None = None
    updated_at: datetime | None = None

    @property
    def filename(self) -> str:
        return f"extra-{self.id}.png"

    @model_validator(mode="before")
    @classmethod
    def _coerce_nulls(cls, data: Any) -> Any:
        return _drop_nulls(data)


class SessionState(BaseModel):
    id: str = Field(pattern=ID_PATTERN)
    created_at: datetime = Field(default_factory=datetime.now)
    title: str
    author: str = ""
    context_prompt: str
    provider: str
    model: str
    pages: list[PageState]
    pdf_ready: bool = False
    caption_enabled: bool = False
    caption_style: Literal["balloon", "caption"] = "balloon"
    caption_position: Literal["top", "bottom"] = "bottom"
    caption_font_family: Literal["sans", "serif", "mono"] = "sans"
    caption_font_bold: bool = False
    caption_font_size: float = 0.032

    # Multi-category & 5-stage pipeline metadata
    language: str = "en"
    preset_id: str | None = None
    category: str = "cozy"
    page_size: PageSizeName = "letter_8.5x11"
    page_layout: str = "single_full"
    negative_prompt: str = ""
    processing: ProcessingConfig | None = None
    characters: list[BookCharacter] = Field(default_factory=list)
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
    cover_status: PageStatus = "pending"
    cover_error: str | None = None
    cover_updated_at: datetime | None = None
    front_extra_pages: list[ExtraPage] = Field(default_factory=list)
    back_extra_pages: list[ExtraPage] = Field(default_factory=list)
    current_step: int = 1

    # JSON snapshot of what this object last read from / wrote to disk. Used by
    # save_session as the common ancestor of a three-way merge so concurrent
    # requests only persist the fields they actually changed.
    _snapshot: dict[str, Any] | None = PrivateAttr(default=None)

    @model_validator(mode="before")
    @classmethod
    def _coerce_nulls(cls, data: Any) -> Any:
        return _drop_nulls(data)


class CharacterRecord(BaseModel):
    id: str = Field(pattern=ID_PATTERN)
    name: str
    description: str = ""
    created_at: datetime = Field(default_factory=datetime.now)

    @property
    def filename(self) -> str:
        return f"{self.id}.png"


def presets_file() -> Path:
    return WEB_ROOT / "presets.json"


def load_custom_presets() -> list[StylePreset]:
    path = presets_file()
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    items: list[StylePreset] = []
    for entry in raw if isinstance(raw, list) else []:
        try:
            preset = StylePreset.model_validate(entry)
            preset.builtin = False
            items.append(preset)
        except ValueError:
            continue
    return items


def save_custom_presets(items: list[StylePreset]) -> None:
    atomic_write_text(
        presets_file(),
        json.dumps([item.model_dump(mode="json") for item in items], indent=2),
    )


def style_dir() -> Path:
    return WEB_ROOT / "style"


def style_path() -> Path:
    return style_dir() / "reference.png"


def load_style_bytes() -> bytes | None:
    path = style_path()
    return path.read_bytes() if path.exists() else None


def clear_style() -> None:
    style_path().unlink(missing_ok=True)


def session_style_dir(sid: str) -> Path:
    return session_dir(sid) / "style"


def session_style_path(sid: str) -> Path:
    return session_style_dir(sid) / "reference.png"


def load_session_style_bytes(sid: str) -> bytes | None:
    """Return only this book's own style anchor image (never inherits a global style)."""
    path = session_style_path(sid)
    return path.read_bytes() if path.exists() else None


def effective_style_reference_bytes(
    sid: str,
    category: str,
    page_index: int = 1,
    *,
    preset_id: str | None = None,
) -> bytes | None:
    """Return this book's style anchor, falling back to the built-in category/preset reference."""
    return load_session_style_bytes(sid) or load_page_layout_reference_bytes(
        category, page_index, preset_id=preset_id
    )


def clear_session_style(sid: str) -> None:
    session_style_path(sid).unlink(missing_ok=True)


_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
_SAFE_SLUG_RE = re.compile(r"^[a-z0-9_-]+$")


def _list_ref_images(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(
        p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in _IMAGE_EXTS
    )


def page_layout_reference_paths(
    category: str, preset_id: str | None = None
) -> list[Path]:
    """Return built-in illustrated reference images for ``category`` (or ``category/preset_id``)."""
    base = Path(__file__).resolve().parent / "static" / "page-layouts"
    if not base.exists():
        return []
    cat = category.strip().lower()
    if not _SAFE_SLUG_RE.match(cat):
        cat = "cozy"
    if preset_id:
        pid = preset_id.strip().lower()
        if _SAFE_SLUG_RE.match(pid):
            preset_imgs = _list_ref_images(base / cat / pid)
            if preset_imgs:
                return preset_imgs
    cat_imgs = _list_ref_images(base / cat)
    if cat_imgs:
        return cat_imgs
    return _list_ref_images(base / "cozy")


def load_page_layout_reference_bytes(
    category: str,
    page_index: int = 1,
    *,
    preset_id: str | None = None,
) -> bytes | None:
    """Pick a built-in page-layout reference image for ``category``/``preset_id``, rotating per page."""
    paths = page_layout_reference_paths(category, preset_id=preset_id)
    if not paths:
        return None
    chosen = paths[(max(1, page_index) - 1) % len(paths)]
    return chosen.read_bytes()


def session_characters_dir(sid: str) -> Path:
    return session_dir(sid) / "characters"


def characters_dir() -> Path:
    return WEB_ROOT / "characters"


def characters_file() -> Path:
    return characters_dir() / "characters.json"


def load_characters() -> list[CharacterRecord]:
    path = characters_file()
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    items: list[CharacterRecord] = []
    for entry in raw if isinstance(raw, list) else []:
        try:
            items.append(CharacterRecord.model_validate(entry))
        except ValueError:
            continue
    return items


def save_characters(items: list[CharacterRecord]) -> None:
    atomic_write_text(
        characters_file(),
        json.dumps([item.model_dump(mode="json") for item in items], indent=2),
    )


def new_character_id() -> str:
    return secrets.token_hex(4)


def settings_file() -> Path:
    return WEB_ROOT / "settings.json"


def load_settings() -> BookSettings | None:
    path = settings_file()
    if not path.exists():
        return None
    try:
        return BookSettings.model_validate_json(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def save_settings(settings: BookSettings) -> None:
    atomic_write_text(settings_file(), settings.model_dump_json(indent=2))


def session_dir(sid: str) -> Path:
    return WEB_ROOT / sid


def pages_dir(sid: str) -> Path:
    return session_dir(sid) / "pages"


def raw_dir(sid: str) -> Path:
    return session_dir(sid) / "raw"


def preview_dir(sid: str) -> Path:
    return session_dir(sid) / "previews"


def cover_dir(sid: str) -> Path:
    return session_dir(sid) / "cover"


def cover_path(sid: str) -> Path:
    return cover_dir(sid) / "cover.png"


def extras_dir(sid: str) -> Path:
    return session_dir(sid) / "extras"


def extra_page_path(sid: str, page_id: str) -> Path:
    return extras_dir(sid) / f"extra-{page_id}.png"


def extra_page_background_path(sid: str, page_id: str) -> Path:
    return extras_dir(sid) / f"extra-{page_id}-background.png"


def _session_file(sid: str) -> Path:
    return session_dir(sid) / "session.json"


def _session_backup_file(sid: str) -> Path:
    return session_dir(sid) / "session.json.bak"


def locks_dir() -> Path:
    return WEB_ROOT / ".locks"


def page_file(sid: str, index: int) -> Path:
    return pages_dir(sid) / f"page-{index:03d}.png"


def shift_page_files_after(sid: str, after_index: int, max_index: int) -> None:
    """Shift on-disk page assets for indices > after_index up by 1 in reverse order."""
    dirs = (pages_dir(sid), raw_dir(sid), preview_dir(sid))
    for i in range(max_index, after_index, -1):
        src_name = f"page-{i:03d}.png"
        dst_name = f"page-{i + 1:03d}.png"
        for directory in dirs:
            src = directory / src_name
            if src.exists():
                src.replace(directory / dst_name)


def insert_page_after(sid: str, after_index: int, new_page: PageState) -> SessionState:
    """Atomically insert ``new_page`` at ``after_index + 1``, shifting later pages and files."""
    with session_lock(sid):
        state = load_session(sid)
        if state is None:
            raise FileNotFoundError(f"session not found: {sid}")
        if len(state.pages) >= 200:
            raise ValueError("limite maximo de 200 paginas atingido")
        if after_index < 0 or after_index > len(state.pages):
            raise ValueError("posicao de insercao invalida")
        if any(p.index > after_index and p.status == "generating" for p in state.pages):
            raise RuntimeError("aguarde a geracao das paginas seguintes terminar antes de inserir")

        max_index = max((p.index for p in state.pages), default=0)
        shift_page_files_after(sid, after_index, max_index)

        for page in state.pages:
            if page.index > after_index:
                page.index += 1

        new_page.index = after_index + 1
        state.pages.append(new_page)
        state.pages.sort(key=lambda item: item.index)
        state.pdf_ready = False
        save_session(state)
        return state


def session_page_files(state: SessionState) -> list[Path]:
    """Processed page images that belong to the book, in reading order.

    Only pages listed in the session are returned, so stray ``page-*.png``
    files (e.g. left behind by an older, longer version of the book) never
    end up in the PDF.
    """
    files: list[Path] = []
    for page in sorted(state.pages, key=lambda item: item.index):
        if page.status != "done":
            continue
        path = page_file(state.id, page.index)
        if path.exists():
            files.append(path)
    return files


def new_session_id() -> str:
    return secrets.token_hex(6)


def new_extra_page_id() -> str:
    return secrets.token_hex(4)


def create_session(
    *,
    pages: list[PageState],
    context_prompt: str,
    title: str,
    provider: str,
    model: str,
    caption_enabled: bool = False,
    caption_style: Literal["balloon", "caption"] = "balloon",
    caption_position: Literal["top", "bottom"] = "bottom",
    caption_font_family: Literal["sans", "serif", "mono"] = "sans",
    caption_font_bold: bool = False,
    caption_font_size: float = 0.032,
    language: str = "en",
    preset_id: str | None = None,
    category: str = "cozy",
    page_size: PageSizeName = "letter_8.5x11",
    page_layout: str = "single_full",
    negative_prompt: str = "",
    processing: ProcessingConfig | None = None,
    story_mode: StoryMode = "narrative",
    story_premise: str = "",
    blank_verso: bool = False,
    include_title_page: bool = True,
    include_belongs_to_page: bool = False,
    include_color_test_page: bool = False,
) -> SessionState:
    sid = new_session_id()
    state = SessionState(
        id=sid,
        title=title,
        context_prompt=context_prompt,
        provider=provider,
        model=model,
        pages=pages,
        caption_enabled=caption_enabled,
        caption_style=caption_style,
        caption_position=caption_position,
        caption_font_family=caption_font_family,
        caption_font_bold=caption_font_bold,
        caption_font_size=caption_font_size,
        language=language,
        preset_id=preset_id,
        category=category,
        page_size=page_size,
        page_layout=page_layout,
        negative_prompt=negative_prompt,
        processing=processing,
        story_mode=story_mode,
        story_premise=story_premise,
        blank_verso=blank_verso,
        include_title_page=include_title_page,
        include_belongs_to_page=include_belongs_to_page,
        include_color_test_page=include_color_test_page,
    )
    for d in (
        session_dir(sid),
        pages_dir(sid),
        raw_dir(sid),
        preview_dir(sid),
        session_characters_dir(sid),
        session_style_dir(sid),
    ):
        d.mkdir(parents=True, exist_ok=True)
    save_session(state)
    return state


# --------------------------------------------------------------------------
# Session locking and persistence
#
# Requests (and the autopilot thread) follow a load -> mutate -> save pattern,
# sometimes with minutes of image generation in between. Writing the whole
# in-memory object back would erase anything saved meanwhile. save_session
# therefore does a three-way merge under a per-session lock:
#   base   = what this object last read/wrote (SessionState._snapshot)
#   ours   = the object now
#   theirs = what is on disk now
# Only fields changed since ``base`` are applied on top of ``theirs``. When the
# same field changed on both sides, the current writer wins.
# --------------------------------------------------------------------------

_thread_locks: dict[str, threading.RLock] = {}
_thread_locks_guard = threading.Lock()
_lock_depth = threading.local()


@contextlib.contextmanager
def session_lock(sid: str) -> Iterator[None]:
    """Exclusive, re-entrant lock for one book across threads and processes."""
    with _thread_locks_guard:
        lock = _thread_locks.setdefault(sid, threading.RLock())
    with lock:
        depths: dict[str, int] = getattr(_lock_depth, "value", None) or {}
        _lock_depth.value = depths
        depth = depths.get(sid, 0)
        depths[sid] = depth + 1
        try:
            if depth > 0 or fcntl is None:
                yield
                return
            locks_dir().mkdir(parents=True, exist_ok=True)
            with open(locks_dir() / f"{sid}.lock", "a+b") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            depths[sid] -= 1
            if not depths[sid]:
                del depths[sid]


_MISSING: Any = object()


def _item_key(item: object) -> tuple[str, object] | None:
    if isinstance(item, dict):
        if "id" in item:
            return ("id", item["id"])
        if "index" in item:
            return ("index", item["index"])
    return None


def _keyed(items: list[Any]) -> dict[tuple[str, object], Any] | None:
    mapping: dict[tuple[str, object], Any] = {}
    for item in items:
        key = _item_key(item)
        if key is None or key in mapping:
            return None
        mapping[key] = item
    return mapping


def _merge_lists(base: list[Any], ours: list[Any], theirs: list[Any]) -> Any:
    b, o, t = _keyed(base), _keyed(ours), _keyed(theirs)
    if b is None or o is None or t is None:
        return ours
    if list(o) != list(b):  # we added, removed or reordered items
        order = list(o) + [key for key in t if key not in o]
    else:
        order = list(t) + [key for key in o if key not in t]
    order += [key for key in b if key not in order]
    merged: list[Any] = []
    for key in order:
        value = _merge_values(b.get(key, _MISSING), o.get(key, _MISSING), t.get(key, _MISSING))
        if value is not _MISSING:
            merged.append(value)
    if merged and all(key[0] == "index" for key in order):
        merged.sort(key=lambda item: item["index"])
    return merged


def _merge_values(base: Any, ours: Any, theirs: Any) -> Any:
    if ours == base:
        return theirs
    if theirs in (base, ours):
        return ours
    if isinstance(ours, dict) and isinstance(theirs, dict):
        base_dict = base if isinstance(base, dict) else {}
        merged: dict[str, Any] = {}
        for key in list(theirs) + [k for k in ours if k not in theirs]:
            value = _merge_values(
                base_dict.get(key, _MISSING), ours.get(key, _MISSING), theirs.get(key, _MISSING)
            )
            if value is not _MISSING:
                merged[key] = value
        return merged
    if isinstance(ours, list) and isinstance(theirs, list):
        return _merge_lists(base if isinstance(base, list) else [], ours, theirs)
    return ours


def merge_session_data(
    base: dict[str, Any], ours: dict[str, Any], theirs: dict[str, Any]
) -> dict[str, Any]:
    """Three-way merge of session JSON documents (see module notes above)."""
    merged = _merge_values(base, ours, theirs)
    return merged if isinstance(merged, dict) else ours


def _read_session_file(path: Path) -> SessionState | None:
    try:
        return SessionState.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, ValidationError):
        return None


def load_session(sid: str) -> SessionState | None:
    path = _session_file(sid)
    if not path.exists():
        return None
    state = _read_session_file(path)
    if state is None:
        backup = _read_session_file(_session_backup_file(sid))
        if backup is None:
            raise ValueError(f"session.json corrompido e sem backup valido: {sid}")
        log.warning("session %s: session.json invalido, usando session.json.bak", sid)
        state = backup
        state.id = sid
    state._snapshot = state.model_dump(mode="json")
    return state


def save_session(state: SessionState) -> None:
    ours = state.model_dump(mode="json")
    path = _session_file(state.id)
    with session_lock(state.id):
        if state._snapshot is not None and not path.exists():
            raise FileNotFoundError(f"session deleted: {state.id}")
        merged = ours
        current = _read_session_file(path) if path.exists() else None
        if current is not None:
            theirs = current.model_dump(mode="json")
            if state._snapshot is not None:
                candidate = merge_session_data(state._snapshot, ours, theirs)
                try:
                    merged = SessionState.model_validate(candidate).model_dump(mode="json")
                except ValidationError:
                    log.warning("session %s: merge invalido, gravando versao atual", state.id)
            # Keep the last known-good version for recovery from a bad write.
            atomic_write_text(
                _session_backup_file(state.id), json.dumps(theirs, indent=2, ensure_ascii=False)
            )
        atomic_write_text(path, json.dumps(merged, indent=2, ensure_ascii=False))
    # Our own changes are now on disk; the next save only sends newer edits.
    state._snapshot = ours


def update_session(sid: str, mutate: Any) -> SessionState | None:
    """Load the latest state, apply ``mutate(state)`` and save, all under the lock."""
    with session_lock(sid):
        state = load_session(sid)
        if state is None:
            return None
        mutate(state)
        save_session(state)
        return state


INTERRUPTED_MESSAGE = (
    "Geracao interrompida antes de terminar (o servidor foi reiniciado). Gere novamente."
)


def recover_interrupted(state: SessionState) -> bool:
    """Turn ``generating`` leftovers into retryable errors. Returns True if changed."""
    changed = False
    now = datetime.now()
    for page in state.pages:
        if page.status == "generating":
            page.status, page.error, page.updated_at = "error", INTERRUPTED_MESSAGE, now
            changed = True
    for extra in [*state.front_extra_pages, *state.back_extra_pages]:
        if extra.status == "generating":
            extra.status, extra.error, extra.updated_at = "error", INTERRUPTED_MESSAGE, now
            changed = True
    for character in state.characters:
        if character.sheet_status == "generating":
            character.sheet_status, character.error = "error", INTERRUPTED_MESSAGE
            character.updated_at = now
            changed = True
    if state.cover_status == "generating":
        state.cover_status, state.cover_error = "error", INTERRUPTED_MESSAGE
        state.cover_updated_at = now
        changed = True
    return changed


def recover_interrupted_sessions() -> list[str]:
    """Run at server start: no generation can be in flight in a fresh process."""
    recovered: list[str] = []
    if not WEB_ROOT.exists():
        return recovered
    for directory in sorted(WEB_ROOT.iterdir()):
        if not directory.is_dir() or not ID_RE.match(directory.name):
            continue
        try:
            with session_lock(directory.name):
                state = load_session(directory.name)
                if state is not None and recover_interrupted(state):
                    save_session(state)
                    recovered.append(state.id)
        except ValueError as exc:
            log.warning("session %s: nao foi possivel recuperar: %s", directory.name, exc)
    return recovered


def migrate_referenced_characters(state: SessionState) -> bool:
    """Import global characters referenced by pages into the book's cast.

    Older books stored only the global character id on pages, so the per-book
    cast could look empty even though the pages used a character. This keeps the
    book self-contained and makes the cast show up in the studio.
    """
    referenced = {cid for page in state.pages for cid in page.character_ids}
    missing = referenced - {character.id for character in state.characters}
    if not missing:
        return False

    globals_by_id = {record.id: record for record in load_characters()}
    session_dir(state.id).mkdir(parents=True, exist_ok=True)
    session_characters_dir(state.id).mkdir(parents=True, exist_ok=True)
    changed = False
    for cid in missing:
        record = globals_by_id.get(cid)
        if record is None:
            continue
        source = characters_dir() / record.filename
        if not source.exists():
            continue
        (session_characters_dir(state.id) / record.filename).write_bytes(
            source.read_bytes()
        )
        state.characters.append(
            BookCharacter(
                id=record.id,
                name=record.name,
                description=record.description,
                approved=True,
                sheet_status="ready",
                updated_at=datetime.now(),
            )
        )
        changed = True

    if changed:
        save_session(state)
    return changed
