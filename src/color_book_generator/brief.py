"""Story briefing: the author's intent for the book and the story bible built from it.

The briefing is the first step of a book. The author states who the book is for,
what it should say and what to avoid; the AI proposes premises and, from the chosen
one, a story bible (summary, world rules, cast and beat outline). Once locked, the
bible is the binding context of every later text generation (script, inserted
pages, cover and frame pages).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from color_book_generator.presets import StylePreset
from color_book_generator.story import (
    StoryGenerationError,
    _call_text_llm,
    _extract_json_object,
)

AgeBand = Literal["", "0-3", "3-5", "6-9", "10-14", "teen", "adult"]
Reader = Literal["", "child_alone", "adult_reading", "adult"]
Purpose = Literal["", "entertain", "value", "educational", "relax", "gift"]
Ending = Literal["", "happy", "open", "twist", "circular"]
StoryStructure = Literal["picture_book", "kishotenketsu", "webtoon_beats", "collection"]

TEXT_LIMIT = 600
# Largest cast a story bible may define (every recurring character needs a model sheet).
MAX_BIBLE_CAST = 4
SHORT_LIMIT = 200


class StoryBrief(BaseModel):
    """What the author wants the book to be. Every field is optional except logline."""

    age_band: AgeBand = ""
    reader: Reader = ""
    purpose: Purpose = ""
    logline: str = Field(default="", max_length=TEXT_LIMIT)
    moral: str = Field(default="", max_length=SHORT_LIMIT)
    tone: str = Field(default="", max_length=SHORT_LIMIT)
    world: str = Field(default="", max_length=TEXT_LIMIT)
    protagonist_name: str = Field(default="", max_length=80)
    protagonist_want: str = Field(default="", max_length=SHORT_LIMIT)
    protagonist_fear: str = Field(default="", max_length=SHORT_LIMIT)
    protagonist_trait: str = Field(default="", max_length=SHORT_LIMIT)
    supporting_cast: str = Field(default="", max_length=TEXT_LIMIT)
    conflict: str = Field(default="", max_length=TEXT_LIMIT)
    ending: Ending = ""
    avoid: str = Field(default="", max_length=TEXT_LIMIT)
    cultural_notes: str = Field(default="", max_length=TEXT_LIMIT)
    references: str = Field(default="", max_length=TEXT_LIMIT)
    # Category-specific answers keyed by CATEGORY_QUESTIONS[category][i]["key"].
    extras: dict[str, str] = Field(default_factory=dict)

    def is_empty(self) -> bool:
        return not any(
            str(v).strip() for k, v in self.model_dump().items() if k != "extras"
        ) and not any(v.strip() for v in self.extras.values())

    def fingerprint(self) -> str:
        """Stable hash used to tell whether the bible was built from this exact brief."""
        payload = json.dumps(self.model_dump(), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


class PremiseOption(BaseModel):
    title: str = ""
    logline: str
    tone: str = ""
    moral: str = ""
    hook: str = ""


class BibleCharacter(BaseModel):
    name: str
    role: str = ""
    look: str = ""
    personality: str = ""


class StoryBeat(BaseModel):
    label: str
    summary: str
    pages: str = ""


class StoryBible(BaseModel):
    title: str = ""
    logline: str = ""
    summary: str = ""
    tone: str = ""
    moral: str = ""
    structure: StoryStructure = "picture_book"
    world_rules: list[str] = Field(default_factory=list)
    characters: list[BibleCharacter] = Field(default_factory=list)
    beats: list[StoryBeat] = Field(default_factory=list)
    locked: bool = False
    brief_fingerprint: str = ""
    updated_at: datetime = Field(default_factory=datetime.now)


# --------------------------------------------------------------------------
# Category knowledge
# --------------------------------------------------------------------------

CATEGORY_STRUCTURE: dict[str, StoryStructure] = {
    "kids": "picture_book",
    "manga": "kishotenketsu",
    "manhwa": "webtoon_beats",
    "cozy": "collection",
    "kawaii": "collection",
    "adult": "collection",
}

STRUCTURE_GUIDE: dict[StoryStructure, str] = {
    "picture_book": (
        "Picture-book arc: opening (hero + wish, ~10% of pages) -> inciting problem (~5%) -> "
        "three escalating attempts, rule of three (~50%) -> darkest moment (~10%) -> climax (~10%) -> "
        "resolution and home/closing image with the moral (~15%). Use page turns for surprise, "
        "especially near the climax. Text and picture complement each other."
    ),
    "kishotenketsu": (
        "Kishotenketsu manga arc: ki - introduction of cast and world (~25%), sho - development "
        "that deepens without a big change (~35%), ten - the twist that recontextualises everything "
        "(~25%), ketsu - conclusion that reconciles the twist and ends on a hook (~15%)."
    ),
    "webtoon_beats": (
        "Webtoon/manhwa beats: one emotional beat per page, each page ending on a mini cliffhanger; "
        "hook in the first page, escalating reveals, a strong turning point at ~70%, and a final "
        "page that closes this arc while teasing the next."
    ),
    "collection": (
        "Themed collection, no plot arc: every page stands alone but shares the same universe, "
        "mood and anchor (character, place or theme). Order the pages by a clear thread "
        "(seasons, rooms, times of day, recipes) and, for adult books, from simpler to more intricate."
    ),
}

CATEGORY_QUESTIONS: dict[str, list[dict[str, Any]]] = {
    "kids": [
        {
            "key": "reading_level",
            "label": "Nível de leitura",
            "options": ["Só ilustração", "Frases curtas", "Parágrafo curto"],
        },
        {
            "key": "refrain",
            "label": "Refrão ou frase que se repete",
            "placeholder": "Ex.: “E lá foi a Lia, passo a passo…”",
        },
        {
            "key": "lesson_style",
            "label": "Como a lição aparece",
            "options": ["Explícita no final", "Implícita na história"],
        },
    ],
    "manga": [
        {
            "key": "demographic",
            "label": "Demografia",
            "options": ["Shōnen", "Shōjo", "Seinen", "Josei", "Kodomo"],
        },
        {
            "key": "final_hook",
            "label": "Gancho da última página",
            "placeholder": "Ex.: o rival aparece na porta",
        },
        {
            "key": "reading_direction",
            "label": "Direção de leitura",
            "options": ["Esquerda → direita", "Direita → esquerda"],
        },
    ],
    "manhwa": [
        {
            "key": "subgenre",
            "label": "Subgênero",
            "options": ["Romance fantasy", "Romance moderno", "Murim", "Caçadores", "Histórico"],
        },
        {
            "key": "world_system",
            "label": "Sistema do mundo",
            "placeholder": "Ex.: níveis de caçador, seitas, títulos de nobreza",
        },
    ],
    "cozy": [
        {
            "key": "thread",
            "label": "Fio condutor",
            "placeholder": "Ex.: um dia inteiro em casa, do café à hora de dormir",
        },
        {"key": "anchor", "label": "Personagem-âncora", "options": ["Sim", "Não"]},
    ],
    "kawaii": [
        {
            "key": "thread",
            "label": "Fio condutor",
            "placeholder": "Ex.: doces de cada estação",
        },
        {"key": "anchor", "label": "Mascote fixo", "options": ["Sim", "Não"]},
    ],
    "adult": [
        {
            "key": "thread",
            "label": "Fio condutor",
            "placeholder": "Ex.: flores do cerrado, uma por página",
        },
        {
            "key": "difficulty",
            "label": "Progressão de dificuldade",
            "options": ["Do simples ao intricado", "Uniforme"],
        },
    ],
}

AGE_LABEL = {
    "0-3": "toddlers aged 0-3",
    "3-5": "pre-readers aged 3-5",
    "6-9": "early readers aged 6-9",
    "10-14": "middle-grade readers aged 10-14",
    "teen": "teenagers",
    "adult": "adults",
}
READER_LABEL = {
    "child_alone": "the child reads/colors alone",
    "adult_reading": "an adult reads it aloud with the child",
    "adult": "an adult reader",
}
PURPOSE_LABEL = {
    "entertain": "pure entertainment",
    "value": "teach a value or life lesson",
    "educational": "educational content (letters, numbers, facts)",
    "relax": "relaxation / mindful coloring",
    "gift": "a personalised gift",
}
ENDING_LABEL = {
    "happy": "a happy ending",
    "open": "an open ending",
    "twist": "a surprise ending",
    "circular": "a circular ending that echoes the opening",
}


def reads_right_to_left(brief: StoryBrief | None) -> bool:
    """Manga books may be read right to left, as answered in the briefing."""
    return bool(brief and brief.extras.get("reading_direction", "").startswith("Direita"))


def structure_for_category(category: str) -> StoryStructure:
    return CATEGORY_STRUCTURE.get(category, "picture_book")


def suggest_preset_ids(brief: StoryBrief) -> list[str]:
    """Rank built-in presets that fit the audience and purpose of the brief."""
    age = brief.age_band
    demographic = brief.extras.get("demographic", "").lower()
    subgenre = brief.extras.get("subgenre", "").lower()
    if age == "0-3":
        return ["kids_toddler", "kids_learning", "kawaii_chunky_animals"]
    if brief.purpose == "educational" and age in ("3-5", "6-9"):
        return ["kids_learning", "kids_classic", "kids_storybook"]
    if age in ("3-5", "6-9"):
        return ["kids_storybook", "kids_fairytale", "kids_animal_friends"]
    if demographic:
        by_demo = {
            "shōnen": "manga_lineart",
            "shōjo": "manga_shoujo",
            "seinen": "manga_seinen",
            "josei": "manga_josei",
            "kodomo": "manga_chibi_gag",
        }
        first = by_demo.get(demographic, "manga_lineart")
        return [first, "manga_adventure_classic"]
    if subgenre:
        by_genre = {
            "romance fantasy": "manhwa_lineart",
            "romance moderno": "manhwa_modern_romance",
            "murim": "manhwa_murim",
            "caçadores": "manhwa_action_hunter",
            "histórico": "manhwa_historical",
        }
        return [by_genre.get(subgenre, "manhwa_lineart"), "manhwa_fantasy_rpg"]
    if age in ("10-14", "teen"):
        return ["manga_lineart", "manhwa_lineart", "kawaii_chibi"]
    if age == "adult" or brief.purpose == "relax":
        return ["bold_easy_cozy", "adult_botanical", "adult_intricate"]
    return []


# --------------------------------------------------------------------------
# Rendering the brief/bible as LLM context
# --------------------------------------------------------------------------


def _line(label: str, value: str) -> str | None:
    value = value.strip()
    return f"- {label}: {value}" if value else None


def render_brief(brief: StoryBrief, category: str = "") -> str:
    lines: list[str | None] = [
        _line("Audience", AGE_LABEL.get(brief.age_band, "")),
        _line("Who reads", READER_LABEL.get(brief.reader, "")),
        _line("Purpose", PURPOSE_LABEL.get(brief.purpose, "")),
        _line("Core idea", brief.logline),
        _line("Moral / message", brief.moral),
        _line("Tone", brief.tone),
        _line("World / setting", brief.world),
        _line("Protagonist", brief.protagonist_name),
        _line("Protagonist wants", brief.protagonist_want),
        _line("Protagonist fears", brief.protagonist_fear),
        _line("Protagonist trait", brief.protagonist_trait),
        _line("Supporting cast", brief.supporting_cast),
        _line("Conflict / obstacle", brief.conflict),
        _line("Ending", ENDING_LABEL.get(brief.ending, "")),
        _line("MUST AVOID", brief.avoid),
        _line("Cultural / regional notes", brief.cultural_notes),
        _line("Taste references (inspiration only, never copy)", brief.references),
    ]
    labels = {q["key"]: q["label"] for q in CATEGORY_QUESTIONS.get(category, [])}
    for key, value in brief.extras.items():
        lines.append(_line(labels.get(key, key), value))
    return "\n".join(line for line in lines if line)


def render_bible(bible: StoryBible) -> str:
    parts: list[str] = []
    for label, value in (
        ("Title idea", bible.title),
        ("Logline", bible.logline),
        ("Summary", bible.summary),
        ("Tone", bible.tone),
        ("Moral", bible.moral),
    ):
        if value.strip():
            parts.append(f"{label}: {value.strip()}")
    if bible.world_rules:
        parts.append("World rules:\n" + "\n".join(f"- {r}" for r in bible.world_rules))
    if bible.characters:
        parts.append(
            "Cast (use these exact names and looks):\n"
            + "\n".join(
                f"- {c.name} ({c.role}): look: {c.look}; personality: {c.personality}"
                for c in bible.characters
            )
        )
    if bible.beats:
        parts.append(
            "Beat outline (follow this order):\n"
            + "\n".join(
                f"- {b.label}{f' [pages {b.pages}]' if b.pages else ''}: {b.summary}"
                for b in bible.beats
            )
        )
    return "\n".join(parts)


def story_context(
    brief: StoryBrief | None, bible: StoryBible | None, category: str = ""
) -> str:
    """Binding author context for text generation; empty when nothing was defined."""
    sections: list[str] = []
    if bible is not None and (bible.summary.strip() or bible.beats):
        sections.append("STORY BIBLE (binding):\n" + render_bible(bible))
    if brief is not None and not brief.is_empty():
        sections.append("AUTHOR BRIEF (binding):\n" + render_brief(brief, category))
    return "\n\n".join(sections)


# --------------------------------------------------------------------------
# LLM calls
# --------------------------------------------------------------------------

_EDITOR = (
    "You are a senior children's and comics book editor and story developer. "
    "You respect the author's brief strictly, especially audience, tone and the MUST AVOID list. "
    "Return ONLY valid JSON, with no prose outside the JSON object."
)


def _style_line(preset: StylePreset | None, category: str) -> str:
    if preset is None:
        return f"Category: {category or 'not chosen yet'}"
    return f"Category: {preset.category} — style {preset.name} ({preset.tagline})"


def propose_premises(
    *,
    brief: StoryBrief,
    category: str = "",
    preset: StylePreset | None = None,
    pages: int = 24,
    language: str = "en",
    count: int = 3,
    provider: str = "",
    model: str | None = None,
) -> list[PremiseOption]:
    """Propose distinct premises that satisfy the brief."""
    structure = structure_for_category(preset.category if preset else category)
    user_prompt = (
        f"Language for every field: {language}\n"
        f"{_style_line(preset, category)}\n"
        f"Story structure of this category: {STRUCTURE_GUIDE[structure]}\n"
        f"Book length: {pages} illustrated pages\n\n"
        f"Author brief:\n{render_brief(brief, category) or '- (empty: invent freely)'}\n\n"
        f"Task: propose {count} clearly different premises for this book. Keep every field the "
        "author filled; vary only what is open. Each premise must be drawable as black-and-white "
        "coloring pages.\n"
        "JSON shape:\n"
        '{"premises": [{"title": "working title", "logline": "1-2 sentences", '
        '"tone": "2-4 words", "moral": "short message or empty for collections", '
        '"hook": "what makes this one special, 1 sentence"}]}'
    )
    raw = _call_text_llm(_EDITOR, user_prompt, preferred_provider=provider, preferred_model=model)
    data = _extract_json_object(raw)
    items = data.get("premises")
    if not isinstance(items, list):
        raise StoryGenerationError("O modelo nao retornou a lista de premissas.")
    options: list[PremiseOption] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            option = PremiseOption.model_validate(
                {k: str(v).strip() for k, v in item.items() if v is not None}
            )
        except ValidationError:
            continue
        if option.logline:
            options.append(option)
    if not options:
        raise StoryGenerationError("Nenhuma premissa valida foi gerada. Tente novamente.")
    return options[:count]


def _as_str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()]


def build_story_bible(
    *,
    brief: StoryBrief,
    premise: PremiseOption | None = None,
    category: str = "",
    preset: StylePreset | None = None,
    pages: int = 24,
    language: str = "en",
    cast_size: int | None = None,
    provider: str = "",
    model: str | None = None,
) -> StoryBible:
    """Turn the brief (and the chosen premise) into an editable story bible."""
    cat = preset.category if preset else category
    structure = structure_for_category(cat)
    size = cast_size if cast_size is not None else (preset.cast if preset else 2)
    if size:
        # Supporting characters who recur must be designed too, or they change on every page.
        size = max(size, MAX_BIBLE_CAST)
    premise_block = (
        "Chosen premise:\n"
        f"- title: {premise.title}\n- logline: {premise.logline}\n"
        f"- tone: {premise.tone}\n- moral: {premise.moral}\n- hook: {premise.hook}\n"
        if premise is not None
        else "No premise chosen: derive it from the brief.\n"
    )
    cast_rule = (
        "Return an empty characters list (no recurring characters)."
        if size == 0
        else (
            f"Define 1 to {max(1, size)} recurring characters; the protagonist from the brief "
            "comes first. Include EVERY character that appears on more than one page "
            "(sidekicks, friends, family): characters left out of this list must not appear "
            "by name anywhere in the book. Prefer a small cast."
        )
    )
    user_prompt = (
        f"Language for every field: {language}\n"
        f"{_style_line(preset, cat)}\n"
        f"Book length: {pages} illustrated pages\n"
        f"Required structure: {STRUCTURE_GUIDE[structure]}\n\n"
        f"Author brief:\n{render_brief(brief, cat) or '- (empty)'}\n\n"
        f"{premise_block}\n"
        "Task: write the story bible that every page of this book will follow.\n"
        f"- {cast_rule} 'look' must be a concrete visual description usable for line art in "
        "2-3 sentences (species/age, proportions, face, hair or fur, ONE fixed outfit with its "
        "shapes/patterns, one signature prop, distinctive marks), never colours only. Make "
        "the characters easy to tell apart by silhouette.\n"
        "- world_rules: 2 to 5 short rules that keep scenes consistent.\n"
        f"- beats: 4 to 9 beats in order that follow the required structure, each with the page "
        f"range it covers (e.g. '1-3'), together covering pages 1 to {pages}.\n"
        "- Respect the MUST AVOID list and the audience in every line.\n"
        "JSON shape:\n"
        '{"title": "", "logline": "", "summary": "3-5 sentences", "tone": "", "moral": "", '
        '"world_rules": [""], '
        '"characters": [{"name": "", "role": "", "look": "", "personality": ""}], '
        '"beats": [{"label": "", "summary": "", "pages": "1-3"}]}'
    )
    raw = _call_text_llm(_EDITOR, user_prompt, preferred_provider=provider, preferred_model=model)
    data = _extract_json_object(raw)

    characters = [
        BibleCharacter(
            name=str(c.get("name") or "").strip(),
            role=str(c.get("role") or "").strip(),
            look=str(c.get("look") or "").strip(),
            personality=str(c.get("personality") or "").strip(),
        )
        for c in (data.get("characters") or [])
        if isinstance(c, dict) and str(c.get("name") or "").strip()
    ]
    beats = [
        StoryBeat(
            label=str(b.get("label") or "").strip() or f"Beat {i + 1}",
            summary=str(b.get("summary") or "").strip(),
            pages=str(b.get("pages") or "").strip(),
        )
        for i, b in enumerate(data.get("beats") or [])
        if isinstance(b, dict) and str(b.get("summary") or "").strip()
    ]
    summary = str(data.get("summary") or "").strip()
    if not summary and not beats:
        raise StoryGenerationError("A biblia da historia veio vazia. Tente novamente.")
    return StoryBible(
        title=str(data.get("title") or (premise.title if premise else "")).strip(),
        logline=str(data.get("logline") or (premise.logline if premise else brief.logline)).strip(),
        summary=summary,
        tone=str(data.get("tone") or brief.tone).strip(),
        moral=str(data.get("moral") or brief.moral).strip(),
        structure=structure,
        world_rules=_as_str_list(data.get("world_rules")),
        characters=characters[: max(0, size)] if size else [],
        beats=beats,
        brief_fingerprint=brief.fingerprint(),
    )


SUGGESTABLE_FIELDS: dict[str, str] = {
    "logline": "the core idea of the book in 1-2 sentences",
    "moral": "the message or moral, one short sentence",
    "tone": "the tone, 2-4 words",
    "world": "the world / setting in 1-2 sentences",
    "protagonist_name": "a fitting protagonist name (just the name)",
    "protagonist_want": "what the protagonist wants, one short sentence",
    "protagonist_fear": "what the protagonist fears, one short sentence",
    "protagonist_trait": "one memorable trait of the protagonist",
    "supporting_cast": "1-3 supporting characters, one short line each",
    "conflict": "the main obstacle, one sentence",
    "avoid": "themes or images to avoid for this audience, comma separated",
    "cultural_notes": "cultural or regional flavour, one sentence",
}


def suggest_brief_field(
    *,
    brief: StoryBrief,
    field: str,
    category: str = "",
    language: str = "en",
    provider: str = "",
    model: str | None = None,
) -> str:
    """Suggest a value for one brief field, consistent with what is already filled."""
    if field not in SUGGESTABLE_FIELDS:
        raise StoryGenerationError("Campo do briefing nao suporta sugestao.")
    user_prompt = (
        f"Language: {language}\n"
        f"Category: {category or 'not chosen yet'}\n"
        f"Current brief:\n{render_brief(brief, category) or '- (empty)'}\n\n"
        f"Task: suggest {SUGGESTABLE_FIELDS[field]}, consistent with the brief and its audience.\n"
        'JSON shape: {"value": "..."}'
    )
    raw = _call_text_llm(_EDITOR, user_prompt, preferred_provider=provider, preferred_model=model)
    value = str(_extract_json_object(raw).get("value") or "").strip()
    if not value:
        raise StoryGenerationError("O modelo nao sugeriu nada para este campo.")
    return value
