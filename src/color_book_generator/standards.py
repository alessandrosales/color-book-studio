"""Editorial standards per audience and category: reading load, type size and beat plans.

Numbers come from the discovery in ``docs/discovery/padroes-editoriais-e-contexto.md``:
picture books carry 15-30 words per page (fewer for ages 3-5), pre-readers need type of
24 pt or more, early readers 14-18 pt. Captions here are printed in a short band under the
drawing, so the word caps are a little tighter than a full picture-book text page.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from color_book_generator.brief import AgeBand, StoryBible, StoryBrief, StoryStructure

PT_PER_INCH = 72.0


@dataclass(frozen=True)
class ReadingStandard:
    age_band: str
    label: str
    caption_max_words: int
    font_min_pt: float
    font_target_pt: float


READING_STANDARDS: dict[str, ReadingStandard] = {
    "0-3": ReadingStandard("0-3", "0 a 3 anos", 6, 24.0, 28.0),
    "3-5": ReadingStandard("3-5", "3 a 5 anos", 15, 20.0, 24.0),
    "6-9": ReadingStandard("6-9", "6 a 9 anos", 25, 14.0, 16.0),
    "10-14": ReadingStandard("10-14", "10 a 14 anos", 30, 11.0, 12.0),
    "teen": ReadingStandard("teen", "adolescente", 30, 9.0, 11.0),
    "adult": ReadingStandard("adult", "adulto", 30, 9.0, 11.0),
}

# Audience assumed when the briefing leaves the age band empty.
CATEGORY_DEFAULT_AGE: dict[str, str] = {
    "kids": "3-5",
    "kawaii": "6-9",
    "manga": "teen",
    "manhwa": "teen",
    "cozy": "adult",
    "adult": "adult",
}
PRESET_DEFAULT_AGE: dict[str, str] = {
    "kids_toddler": "0-3",
    "kids_learning": "3-5",
    "kids_dino_adventure": "6-9",
}

# Caps the page layout itself imposes (balloons, labels, quotes). The audience cap
# can only make these stricter, never looser.
LAYOUT_CAPTION_CAP: dict[str, int] = {
    "manga_action": 8,
    "manhwa_editorial": 12,
    "kawaii_stickers": 5,
    "adult_medallion": 8,
    "single_full": 8,
}


def resolve_age_band(brief: StoryBrief | None, category: str, preset_id: str | None = None) -> str:
    age: AgeBand | str = brief.age_band if brief is not None else ""
    if age:
        return age
    if preset_id and preset_id in PRESET_DEFAULT_AGE:
        return PRESET_DEFAULT_AGE[preset_id]
    return CATEGORY_DEFAULT_AGE.get(category, "adult")


def reading_standard(
    brief: StoryBrief | None, category: str, preset_id: str | None = None
) -> ReadingStandard:
    return READING_STANDARDS[resolve_age_band(brief, category, preset_id)]


def caption_word_cap(standard: ReadingStandard, page_layout: str) -> int:
    layout_cap = LAYOUT_CAPTION_CAP.get(page_layout)
    if layout_cap is None:
        return standard.caption_max_words
    return min(layout_cap, standard.caption_max_words)


def count_words(text: str) -> int:
    # Balloon captions separate lines with " | "; the bar is not a word.
    return len([w for w in re.split(r"\s+", text.replace("|", " ")) if w.strip()])


def pt_from_px(px: float, dpi: int) -> float:
    return px / dpi * PT_PER_INCH


def ratio_for_pt(pt: float, page_width_in: float) -> float:
    """caption.font_size_ratio that prints at ``pt`` (the ratio is relative to page width)."""
    ratio = pt / (page_width_in * PT_PER_INCH)
    return round(min(0.08, max(0.01, ratio)), 4)


def nominal_caption_pt(font_size_ratio: float, page_width_in: float) -> float:
    return round(font_size_ratio * page_width_in * PT_PER_INCH, 1)


# --------------------------------------------------------------------------
# Beat plans
# --------------------------------------------------------------------------

# (label, share of the book). Labels are shown in the UI and sent to the LLM.
BEAT_TEMPLATES: dict[StoryStructure, list[tuple[str, float]]] = {
    "picture_book": [
        ("Apresentação: o herói e o seu desejo", 0.10),
        ("O mundo e a rotina do herói", 0.06),
        ("Incidente: o problema aparece", 0.06),
        ("Primeira tentativa (não dá certo)", 0.16),
        ("Segunda tentativa (mais difícil)", 0.16),
        ("Terceira tentativa (quase dá certo)", 0.14),
        ("Pior momento", 0.08),
        ("Clímax: a escolha corajosa (virada de página)", 0.10),
        ("Resolução", 0.08),
        ("Fechamento e moral", 0.06),
    ],
    "kishotenketsu": [
        ("Ki: apresentação do elenco e do mundo", 0.25),
        ("Shō: desenvolvimento, aprofundar sem grande mudança", 0.35),
        ("Ten: a virada que muda o sentido de tudo", 0.25),
        ("Ketsu: conclusão e gancho final", 0.15),
    ],
    "webtoon_beats": [
        ("Gancho de abertura", 0.08),
        ("Apresentação e desejo", 0.17),
        ("Escalada", 0.25),
        ("Revelação", 0.20),
        ("Ponto de virada", 0.15),
        ("Clímax", 0.10),
        ("Cliffhanger final", 0.05),
    ],
    "collection": [
        ("Abertura: página-vitrine do tema", 0.10),
        ("Variação do tema", 0.80),
        ("Fechamento: página de despedida", 0.10),
    ],
}

PROGRESSIVE_TIERS = ("simples", "médio", "intricado")


def _allocate(shares: list[float], pages: int) -> list[int]:
    """Largest-remainder allocation of pages to beats, each beat at least one page."""
    total = sum(shares)
    raw = [s / total * pages for s in shares]
    counts = [max(1, int(r)) for r in raw]
    while sum(counts) > pages:
        # Take from the beat that is most over its share and can spare a page.
        idx = max(
            (i for i, c in enumerate(counts) if c > 1),
            key=lambda i: counts[i] - raw[i],
        )
        counts[idx] -= 1
    while sum(counts) < pages:
        idx = max(range(len(counts)), key=lambda i: raw[i] - counts[i])
        counts[idx] += 1
    return counts


def beat_plan(structure: StoryStructure, pages: int, *, progressive: bool = False) -> list[str]:
    """One beat label per page, in order, for a book of ``pages`` illustrations."""
    if pages <= 0:
        return []
    template = BEAT_TEMPLATES[structure]
    if pages < len(template):
        # Too few pages for every beat: keep an even spread that includes both ends.
        if pages == 1:
            return [template[-1][0]]
        picks = sorted({round(i * (len(template) - 1) / (pages - 1)) for i in range(pages)})
        labels = [template[i][0] for i in picks]
        while len(labels) < pages:
            labels.insert(-1, template[len(template) // 2][0])
        return labels
    counts = _allocate([share for _, share in template], pages)
    plan = [label for (label, _), count in zip(template, counts, strict=True) for _ in range(count)]
    if progressive:
        plan = [
            f"{label} · traço {PROGRESSIVE_TIERS[min(2, i * 3 // pages)]}"
            for i, label in enumerate(plan)
        ]
    return plan


_RANGE_SEPARATORS = ("-", "\u2013", "\u2014", " a ")


def _parse_range(value: str) -> tuple[int, int] | None:
    """Parse '3', '3-5', '3\u20135' or '3 a 5' into an inclusive page range."""
    text = value.strip()
    for sep in _RANGE_SEPARATORS:
        text = text.replace(sep, "-")
    parts = [part.strip() for part in text.split("-")]
    if not 1 <= len(parts) <= 2 or not all(part.isdigit() for part in parts):
        return None
    start, end = int(parts[0]), int(parts[-1])
    return (min(start, end), max(start, end))


def bible_beat_plan(bible: StoryBible, pages: int) -> list[str] | None:
    """Per-page beats taken from the bible's page ranges; None when they don't cover the book."""
    if not bible.beats:
        return None
    plan: list[str | None] = [None] * pages
    for beat in bible.beats:
        span = _parse_range(beat.pages)
        if span is None:
            continue
        text = f"{beat.label}: {beat.summary}".strip(": ")
        for page in range(span[0], min(span[1], pages) + 1):
            if 1 <= page <= pages and plan[page - 1] is None:
                plan[page - 1] = text
    covered = sum(p is not None for p in plan)
    if covered < max(1, pages // 2):
        return None
    # Fill gaps with the nearest previous beat (or the next one at the start).
    last: str | None = next((p for p in plan if p is not None), None)
    filled: list[str] = []
    for item in plan:
        if item is not None:
            last = item
        filled.append(last or "")
    return filled


def page_beats(
    *,
    structure: StoryStructure,
    pages: int,
    bible: StoryBible | None,
    brief: StoryBrief | None,
) -> list[str]:
    """Beat for each page: the locked bible wins, then the category template."""
    if bible is not None and bible.locked:
        from_bible = bible_beat_plan(bible, pages)
        if from_bible is not None:
            return from_bible
    progressive = bool(
        brief is not None and brief.extras.get("difficulty", "").lower().startswith("do simples")
    )
    return beat_plan(structure, pages, progressive=progressive)


# --------------------------------------------------------------------------
# Line weight and colourable area
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class LineStandard:
    key: str
    label: str
    line_min_mm: float
    line_max_mm: float
    min_region_mm: float
    # How many areas smaller than min_region_mm a page may have before it is flagged.
    # Calibrated on well-drawn books: eyes, buttons and small props are normal.
    tiny_region_allowance: int


LINE_STANDARDS: dict[str, LineStandard] = {
    "0-3": LineStandard("0-3", "0 a 3 anos", 2.5, 4.0, 15.0, 8),
    "3-5": LineStandard("3-5", "3 a 5 anos", 1.5, 2.5, 8.0, 30),
    "6-9": LineStandard("6-9", "6 a 9 anos", 1.0, 1.5, 5.0, 60),
    "cozy": LineStandard("cozy", "Bold & Easy", 1.5, 2.5, 6.0, 30),
    "kawaii": LineStandard("kawaii", "Kawaii", 1.0, 1.5, 5.0, 60),
    "comics": LineStandard("comics", "Mangá e manhwa", 0.6, 1.0, 3.0, 400),
    "intricate": LineStandard("intricate", "Adulto intricado", 0.5, 0.8, 2.0, 2000),
}

CATEGORY_LINE_KEY: dict[str, str] = {
    "cozy": "cozy",
    "kawaii": "kawaii",
    "manga": "comics",
    "manhwa": "comics",
    "adult": "intricate",
}


def line_standard(brief: StoryBrief | None, category: str, preset_id: str | None = None) -> LineStandard:
    """Line weight target: by style for most categories, by age for children's books."""
    if category in CATEGORY_LINE_KEY:
        return LINE_STANDARDS[CATEGORY_LINE_KEY[category]]
    age = resolve_age_band(brief, category, preset_id)
    if age in ("0-3", "3-5"):
        return LINE_STANDARDS[age]
    return LINE_STANDARDS["6-9"]
