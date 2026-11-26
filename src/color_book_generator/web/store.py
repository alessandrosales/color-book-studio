"""Session state and on-disk persistence for the web UI."""

from __future__ import annotations

import json
import os
import re
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from color_book_generator.config import PageSizeName, ProcessingConfig
from color_book_generator.presets import StylePreset
from color_book_generator.story import StoryMode

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
    WEB_ROOT.mkdir(parents=True, exist_ok=True)
    presets_file().write_text(
        json.dumps([item.model_dump(mode="json") for item in items], indent=2),
        encoding="utf-8",
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
    path = session_style_path(sid)
    if path.exists():
        return path.read_bytes()
    return load_style_bytes()


def clear_session_style(sid: str) -> None:
    session_style_path(sid).unlink(missing_ok=True)


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
    characters_dir().mkdir(parents=True, exist_ok=True)
    characters_file().write_text(
        json.dumps([item.model_dump(mode="json") for item in items], indent=2),
        encoding="utf-8",
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
    WEB_ROOT.mkdir(parents=True, exist_ok=True)
    settings_file().write_text(settings.model_dump_json(indent=2), encoding="utf-8")


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


def load_session(sid: str) -> SessionState | None:
    path = _session_file(sid)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return SessionState.model_validate(data)


def save_session(state: SessionState) -> None:
    session_dir(state.id).mkdir(parents=True, exist_ok=True)
    _session_file(state.id).write_text(
        state.model_dump_json(indent=2), encoding="utf-8"
    )


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
