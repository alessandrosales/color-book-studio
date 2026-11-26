"""Briefing step: the author's story context and the story bible built from it."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from color_book_generator.brief import (
    CATEGORY_QUESTIONS,
    STRUCTURE_GUIDE,
    SUGGESTABLE_FIELDS,
    PremiseOption,
    StoryBible,
    StoryBrief,
    StoryStructure,
    build_story_bible,
    propose_premises,
    structure_for_category,
    suggest_brief_field,
    suggest_preset_ids,
)
from color_book_generator.presets import StylePreset, get_preset
from color_book_generator.story import StoryGenerationError
from color_book_generator.web import store
from color_book_generator.web.line_check import (
    LineReport,
    analyze_pages,
    build_line_report,
    thicken_page,
)
from color_book_generator.web.reading import ReadingReport, build_report
from color_book_generator.web.routers.studio import (
    _session,
    _session_book_cfg,
    _session_caption_cfg,
)
from color_book_generator.web.store import BookCharacter, SessionState

router = APIRouter(prefix="/api/sessions/{sid}", tags=["brief"])

DEFAULT_PAGES = 24


class PresetSuggestion(BaseModel):
    id: str
    name: str
    category: str
    tagline: str = ""


class BriefView(BaseModel):
    brief: StoryBrief
    bible: StoryBible | None
    bible_stale: bool
    category: str
    structure: StoryStructure
    structure_guide: str
    questions: list[dict[str, Any]]
    suggestable_fields: list[str]
    suggested_presets: list[PresetSuggestion]


class SuggestFieldRequest(BaseModel):
    field: str = Field(min_length=1, max_length=40)
    brief: StoryBrief | None = None


class SuggestFieldResponse(BaseModel):
    field: str
    value: str


class PremisesRequest(BaseModel):
    brief: StoryBrief | None = None
    pages: int | None = Field(default=None, ge=1, le=200)


class PremisesResponse(BaseModel):
    premises: list[PremiseOption]


class BibleGenerateRequest(BaseModel):
    premise: PremiseOption | None = None
    brief: StoryBrief | None = None
    pages: int | None = Field(default=None, ge=1, le=200)


class BibleCharactersResponse(BaseModel):
    created: int
    view: BriefView


def _preset(state: SessionState) -> StylePreset | None:
    if not state.preset_id:
        return None
    return get_preset(state.preset_id, store.load_custom_presets())


def _page_count(state: SessionState, requested: int | None) -> int:
    return requested or len(state.pages) or DEFAULT_PAGES


def _view(state: SessionState) -> BriefView:
    structure = structure_for_category(state.category)
    custom = store.load_custom_presets()
    suggestions: list[PresetSuggestion] = []
    for pid in suggest_preset_ids(state.story_brief):
        preset = get_preset(pid, custom)
        if preset is not None:
            suggestions.append(
                PresetSuggestion(
                    id=preset.id,
                    name=preset.name,
                    category=preset.category,
                    tagline=preset.tagline,
                )
            )
    bible = state.story_bible
    stale = bool(
        bible is not None
        and bible.brief_fingerprint
        and bible.brief_fingerprint != state.story_brief.fingerprint()
    )
    return BriefView(
        brief=state.story_brief,
        bible=bible,
        bible_stale=stale,
        category=state.category,
        structure=structure,
        structure_guide=STRUCTURE_GUIDE[structure],
        questions=CATEGORY_QUESTIONS.get(state.category, []),
        suggestable_fields=list(SUGGESTABLE_FIELDS),
        suggested_presets=suggestions,
    )


def _language(state: SessionState) -> str:
    return state.language or "en"


@router.get("/brief", response_model=BriefView)
def get_brief(sid: str) -> BriefView:
    state = _session(sid)
    if not state.story_brief.logline and state.story_premise:
        # Older books only had the free-text premise: surface it as the core idea.
        state.story_brief = state.story_brief.model_copy(
            update={"logline": state.story_premise[:600]}
        )
    return _view(state)


@router.put("/brief", response_model=BriefView)
def put_brief(sid: str, body: StoryBrief) -> BriefView:
    state = _session(sid)
    state.story_brief = body
    store.save_session(state)
    return _view(state)


@router.post("/brief/suggest", response_model=SuggestFieldResponse)
async def suggest_field(sid: str, body: SuggestFieldRequest) -> SuggestFieldResponse:
    state = _session(sid)
    if body.field not in SUGGESTABLE_FIELDS:
        raise HTTPException(status_code=400, detail="campo do briefing nao suporta sugestao")
    brief = body.brief or state.story_brief
    try:
        value = await asyncio.to_thread(
            suggest_brief_field,
            brief=brief,
            field=body.field,
            category=state.category,
            language=_language(state),
            provider=state.provider,
            model=state.model,
        )
    except StoryGenerationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SuggestFieldResponse(field=body.field, value=value)


@router.post("/brief/premises", response_model=PremisesResponse)
async def premises(sid: str, body: PremisesRequest) -> PremisesResponse:
    state = _session(sid)
    if body.brief is not None:
        state.story_brief = body.brief
        store.save_session(state)
    try:
        options = await asyncio.to_thread(
            propose_premises,
            brief=state.story_brief,
            category=state.category,
            preset=_preset(state),
            pages=_page_count(state, body.pages),
            language=_language(state),
            provider=state.provider,
            model=state.model,
        )
    except StoryGenerationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return PremisesResponse(premises=options)


@router.post("/bible/generate", response_model=BriefView)
async def generate_bible(sid: str, body: BibleGenerateRequest) -> BriefView:
    state = _session(sid)
    if state.story_bible is not None and state.story_bible.locked:
        raise HTTPException(
            status_code=409, detail="destrave a biblia da historia antes de gerar outra"
        )
    if body.brief is not None:
        state.story_brief = body.brief
    try:
        bible = await asyncio.to_thread(
            build_story_bible,
            brief=state.story_brief,
            premise=body.premise,
            category=state.category,
            preset=_preset(state),
            pages=_page_count(state, body.pages),
            language=_language(state),
            provider=state.provider,
            model=state.model,
        )
    except StoryGenerationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    state.story_bible = bible
    store.save_session(state)
    return _view(state)


@router.put("/bible", response_model=BriefView)
def put_bible(sid: str, body: StoryBible) -> BriefView:
    state = _session(sid)
    was_locked = state.story_bible is not None and state.story_bible.locked
    bible = body.model_copy(update={"updated_at": datetime.now()})
    if bible.locked and not was_locked:
        # Locking pins the bible to the current brief and seeds the story step.
        bible.brief_fingerprint = state.story_brief.fingerprint()
        if not state.story_premise.strip():
            state.story_premise = bible.logline.strip() or state.story_brief.logline.strip()
        state.story_mode = "collection" if bible.structure == "collection" else "narrative"
    state.story_bible = bible
    store.save_session(state)
    return _view(state)


@router.delete("/bible", status_code=204)
def delete_bible(sid: str) -> None:
    state = _session(sid)
    state.story_bible = None
    store.save_session(state)


@router.post("/bible/characters", response_model=BibleCharactersResponse)
def import_bible_characters(sid: str) -> BibleCharactersResponse:
    """Register the bible's cast as book characters (text only, no reference sheet yet)."""
    state = _session(sid)
    if state.story_bible is None:
        raise HTTPException(status_code=400, detail="gere a biblia da historia primeiro")
    existing = {c.name.strip().lower() for c in state.characters}
    created = 0
    for char in state.story_bible.characters:
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
    store.save_session(state)
    return BibleCharactersResponse(created=created, view=_view(state))


def _reading(state: SessionState) -> ReadingReport:
    return build_report(state, _session_book_cfg(state), _session_caption_cfg(state))


@router.get("/reading", response_model=ReadingReport)
def get_reading(sid: str) -> ReadingReport:
    """Caption length and printed type size checked against the book's audience."""
    return _reading(_session(sid))


@router.post("/reading/apply-font", response_model=ReadingReport)
def apply_recommended_font(sid: str) -> ReadingReport:
    """Set the caption size to the audience's ideal size for this page format."""
    state = _session(sid)
    state.caption_font_size = _reading(state).recommended_ratio
    state.pdf_ready = False
    store.save_session(state)
    return _reading(state)


@router.get("/lines", response_model=LineReport)
def get_lines(sid: str) -> LineReport:
    """Line weight and small colourable areas per page, against the book's audience/style."""
    state = _session(sid)
    dpi = _session_book_cfg(state).dpi
    if analyze_pages(state, dpi, only_missing=True):
        store.save_session(state)
    return build_line_report(state, dpi)


@router.post("/lines/analyze", response_model=LineReport)
def reanalyze_lines(sid: str) -> LineReport:
    state = _session(sid)
    dpi = _session_book_cfg(state).dpi
    analyze_pages(state, dpi, only_missing=False)
    store.save_session(state)
    return build_line_report(state, dpi)


class ThickenResponse(BaseModel):
    pages_fixed: list[int]
    report: LineReport


@router.post("/lines/thicken", response_model=ThickenResponse)
def thicken_lines(sid: str, page: int | None = None) -> ThickenResponse:
    """Thicken thin pages up to the standard (one page, or every fixable page)."""
    state = _session(sid)
    dpi = _session_book_cfg(state).dpi
    analyze_pages(state, dpi, only_missing=True)
    targets = [p for p in state.pages if p.status == "done" and (page is None or p.index == page)]
    if page is not None and not targets:
        raise HTTPException(status_code=404, detail="pagina nao encontrada ou ainda nao gerada")
    fixed = [p.index for p in targets if thicken_page(state, p, dpi)]
    if fixed:
        state.pdf_ready = False
    store.save_session(state)
    return ThickenResponse(pages_fixed=fixed, report=build_line_report(state, dpi))
