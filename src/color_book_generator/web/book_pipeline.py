"""One way to build every image job of a book, shared by manual generation and autopilot.

Everything here reads only the book's own state and files: its style prompt, its
locked story bible, its cast designs, its model sheets and its style anchor. Nothing
comes from other books, the global character registry or the built-in layout library.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from color_book_generator.config import BookConfig, ProcessingConfig
from color_book_generator.identity import (
    cast_block,
    design_source,
    render_design,
    world_block,
)
from color_book_generator.presets import default_page_layout_for_category
from color_book_generator.prompts import (
    compose_page_prompt,
    effective_negative_prompt,
    line_standard_directive,
)
from color_book_generator.standards import line_standard
from color_book_generator.web import store
from color_book_generator.web.store import BookCharacter, PageState, SessionState


@dataclass
class ImageJob:
    prompt: str
    negative_prompt: str
    processing: ProcessingConfig
    references: list[tuple[str, bytes]]
    style_reference: bytes | None


def design_text(character: BookCharacter) -> str:
    """The character's design for prompts, falling back to its free-text description."""
    return render_design(
        character.name,
        character.design if design_is_fresh(character) else None,
        character.description,
    )


def _source(character: BookCharacter) -> str:
    return design_source(character.name, character.description)


def design_is_fresh(character: BookCharacter) -> bool:
    return (
        character.design is not None
        and not character.design.is_empty()
        and character.design_source == _source(character)
    )


def sheet_is_fresh(state: SessionState, character: BookCharacter) -> bool:
    """A ready sheet on disk drawn from the current name/description (legacy sheets count)."""
    path = store.session_characters_dir(state.id) / character.filename
    if character.sheet_status != "ready" or not path.exists():
        return False
    return character.sheet_source in {"", _source(character)}


def upload_path(state: SessionState, character: BookCharacter) -> Path:
    """Image the author uploaded for this character (identity reference for its sheet)."""
    return store.session_characters_dir(state.id) / f"{character.id}-upload.png"


def sync_bible_cast(state: SessionState) -> int:
    """Register the story bible's cast as book characters (matched by name). Returns new count."""
    bible = state.story_bible
    if bible is None:
        return 0
    existing = {c.name.strip().lower() for c in state.characters}
    created = 0
    for char in bible.characters:
        key = char.name.strip().lower()
        if not key or key in existing:
            continue
        description = "; ".join(
            part for part in (char.look.strip(), char.personality.strip()) if part
        )
        state.characters.append(
            BookCharacter(
                id=store.new_character_id(),
                name=char.name.strip(),
                description=description[:600],
            )
        )
        existing.add(key)
        created += 1
    return created


def fallback_cast_ids(state: SessionState) -> list[str]:
    """Cast for a scripted page that named nobody: the hero of a one-character book, else none."""
    return [state.characters[0].id] if len(state.characters) == 1 else []


def page_character_ids(state: SessionState, page: PageState) -> list[str]:
    """The page's cast, restricted to this book's own characters."""
    own = {c.id for c in state.characters}
    return [cid for cid in dict.fromkeys(page.character_ids) if cid in own]


def character_references(state: SessionState, ids: list[str]) -> list[tuple[str, bytes]]:
    """Model sheets of the given book characters, in cast order (book files only)."""
    by_id = {c.id: c for c in state.characters}
    refs: list[tuple[str, bytes]] = []
    for cid in ids:
        character = by_id.get(cid)
        if character is None:
            continue
        path = store.session_characters_dir(state.id) / character.filename
        if path.exists():
            refs.append((character.name, path.read_bytes()))
    return refs


def world_rules(state: SessionState) -> list[str]:
    bible = state.story_bible
    return list(bible.world_rules) if bible is not None and bible.locked else []


def book_world(state: SessionState) -> str:
    """Short description of the book's setting, for the style anchor."""
    bible = state.story_bible
    if bible is not None and bible.locked:
        if bible.world_rules:
            return "; ".join(bible.world_rules[:3])
        if bible.summary.strip():
            return bible.summary.strip()[:400]
    if state.story_brief.world.strip():
        return state.story_brief.world.strip()
    return state.story_premise.strip()[:400]


def page_job(
    state: SessionState,
    page: PageState,
    cfg: BookConfig,
    *,
    context: str | None = None,
) -> ImageJob:
    """Prompt, negative, processing and references for one interior page."""
    layout = state.page_layout or default_page_layout_for_category(state.category)
    embedded_text = page.caption_text.strip() if state.caption_enabled else ""
    base = compose_page_prompt(
        (context if context is not None else state.context_prompt),
        page.prompt,
        page.index,
        len(state.pages),
        page_layout=layout,
        caption_text=embedded_text,
        caption_position=state.caption_position,
        caption_font_family=state.caption_font_family,
        caption_font_bold=state.caption_font_bold,
    )
    ids = page_character_ids(state, page)
    by_id = {c.id: c for c in state.characters}
    std = line_standard(state.story_brief, state.category, state.preset_id)
    prompt = "\n\n".join(
        part
        for part in (
            base,
            line_standard_directive(std),
            world_block(world_rules(state)),
            cast_block([design_text(by_id[cid]) for cid in ids]) if state.characters else "",
        )
        if part
    )
    negative = effective_negative_prompt(
        cfg.negative_prompt, has_embedded_text=bool(embedded_text), page_layout=layout
    )
    processing = (
        cfg.processing.model_copy(
            update={"min_component_px": min(cfg.processing.min_component_px, 12)}
        )
        if embedded_text
        else cfg.processing
    )
    return ImageJob(
        prompt=prompt,
        negative_prompt=negative,
        processing=processing,
        references=character_references(state, ids),
        style_reference=store.load_session_style_bytes(state.id),
    )
