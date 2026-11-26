"""Reading check: caption length and printed type size against the book's audience."""

from __future__ import annotations

from pydantic import BaseModel, Field

from color_book_generator.brief import structure_for_category
from color_book_generator.config import BookConfig, CaptionConfig
from color_book_generator.processing.caption import caption_font_px
from color_book_generator.standards import (
    caption_word_cap,
    count_words,
    nominal_caption_pt,
    pt_from_px,
    ratio_for_pt,
    reading_standard,
)
from color_book_generator.web.store import SessionState


class PageReadingIssue(BaseModel):
    index: int
    words: int
    font_pt: float | None = None
    problems: list[str] = Field(default_factory=list)


class ReadingReport(BaseModel):
    age_band: str
    age_label: str
    age_assumed: bool
    structure: str
    word_cap: int
    font_min_pt: float
    font_target_pt: float
    caption_enabled: bool
    nominal_pt: float
    recommended_ratio: float
    font_ok: bool
    pages: list[PageReadingIssue] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def build_report(state: SessionState, cfg: BookConfig, caption: CaptionConfig) -> ReadingReport:
    standard = reading_standard(state.story_brief, state.category, state.preset_id)
    word_cap = caption_word_cap(standard, state.page_layout)
    width_in, height_in = cfg.trim_size_in
    target_px = (round(width_in * cfg.dpi), round(height_in * cfg.dpi))
    nominal = nominal_caption_pt(caption.font_size_ratio, width_in)
    recommended = ratio_for_pt(standard.font_target_pt, width_in)
    font_ok = (not caption.enabled) or nominal >= standard.font_min_pt

    pages: list[PageReadingIssue] = []
    for page in sorted(state.pages, key=lambda p: p.index):
        text = page.caption_text.strip()
        if not text:
            continue
        words = count_words(text)
        problems: list[str] = []
        if words > word_cap:
            problems.append(f"{words} palavras (limite {word_cap})")
        font_pt: float | None = None
        if caption.enabled:
            font_pt = round(pt_from_px(caption_font_px(text, caption, target_px), cfg.dpi), 1)
            # A base size below the minimum is reported once, book-wide; here only
            # captions that actually shrink to fit their band are flagged.
            shrunk = font_pt < nominal - 0.5
            if shrunk and font_pt < standard.font_min_pt:
                problems.append(
                    f"texto encolhe para {font_pt:g} pt para caber na faixa "
                    f"(mínimo {standard.font_min_pt:g} pt)"
                )
        if problems:
            pages.append(PageReadingIssue(index=page.index, words=words, font_pt=font_pt, problems=problems))

    warnings: list[str] = []
    if caption.enabled and not font_ok:
        warnings.append(
            f"A fonte das legendas sai com {nominal:g} pt; para {standard.label} o mínimo é "
            f"{standard.font_min_pt:g} pt (ideal {standard.font_target_pt:g} pt)."
        )
    for issue in pages:
        warnings.append(f"Página {issue.index}: " + "; ".join(issue.problems) + ".")

    return ReadingReport(
        age_band=standard.age_band,
        age_label=standard.label,
        age_assumed=not state.story_brief.age_band,
        structure=structure_for_category(state.category),
        word_cap=word_cap,
        font_min_pt=standard.font_min_pt,
        font_target_pt=standard.font_target_pt,
        caption_enabled=caption.enabled,
        nominal_pt=nominal,
        recommended_ratio=recommended,
        font_ok=font_ok,
        pages=pages,
        warnings=warnings,
    )
