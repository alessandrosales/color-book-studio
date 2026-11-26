"""Session state and on-disk persistence for the web UI."""

from __future__ import annotations

import json
import secrets
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

WEB_ROOT = Path("output/web")

PageStatus = Literal["pending", "generating", "done", "error"]


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


class PageState(BaseModel):
    index: int
    prompt: str
    caption_text: str = ""
    character_ids: list[str] = Field(default_factory=list)
    status: PageStatus = "pending"
    error: str | None = None
    seed: int | None = None
    width_px: int | None = None
    height_px: int | None = None
    updated_at: datetime | None = None


class SessionState(BaseModel):
    id: str
    created_at: datetime = Field(default_factory=datetime.now)
    title: str
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


class CharacterRecord(BaseModel):
    id: str
    name: str
    created_at: datetime = Field(default_factory=datetime.now)

    @property
    def filename(self) -> str:
        return f"{self.id}.png"


def style_dir() -> Path:
    return WEB_ROOT / "style"


def style_path() -> Path:
    return style_dir() / "reference.png"


def load_style_bytes() -> bytes | None:
    path = style_path()
    return path.read_bytes() if path.exists() else None


def clear_style() -> None:
    style_path().unlink(missing_ok=True)


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


def _session_file(sid: str) -> Path:
    return session_dir(sid) / "session.json"


def new_session_id() -> str:
    return secrets.token_hex(6)


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
    )
    for d in (session_dir(sid), pages_dir(sid), raw_dir(sid), preview_dir(sid)):
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
