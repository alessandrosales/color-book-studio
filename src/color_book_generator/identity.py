"""Book identity: the single source of truth that keeps one book visually consistent.

A book's identity is made of three things, all owned by the book itself:

1. **Style anchor**: one image generated from the book's own style prompt and world
   (never an image from another book or from the built-in layout library).
2. **Character designs**: a detailed, structured visual spec per recurring character
   (proportions, face, hair, outfit, signature prop, always/never rules).
3. **Character model sheets**: one turnaround sheet per character, drawn from its
   design in the book's style, used as the image reference on every page.

Every image prompt (sheet, page, cover, frame page) is built from these pieces, and
the script prompts tell the writer to name cast members only (never re-describe
them) so the scene text cannot contradict the design.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence

from pydantic import BaseModel, Field

from color_book_generator.presets import StylePreset

# --------------------------------------------------------------------------
# Character design (visual DNA)
# --------------------------------------------------------------------------

_FIELD_LIMIT = 300


class CharacterDesign(BaseModel):
    """Concrete, drawable traits of one character; every field is plain text."""

    species_age: str = Field(default="", max_length=_FIELD_LIMIT)
    body: str = Field(default="", max_length=_FIELD_LIMIT)
    face: str = Field(default="", max_length=_FIELD_LIMIT)
    eyes: str = Field(default="", max_length=_FIELD_LIMIT)
    hair: str = Field(default="", max_length=_FIELD_LIMIT)
    outfit: str = Field(default="", max_length=_FIELD_LIMIT)
    footwear: str = Field(default="", max_length=_FIELD_LIMIT)
    signature_prop: str = Field(default="", max_length=_FIELD_LIMIT)
    marks: str = Field(default="", max_length=_FIELD_LIMIT)
    silhouette: str = Field(default="", max_length=_FIELD_LIMIT)
    always: list[str] = Field(default_factory=list)
    never: list[str] = Field(default_factory=list)

    def is_empty(self) -> bool:
        return not any(
            str(v).strip() for k, v in self.model_dump().items() if k not in {"always", "never"}
        )


_DESIGN_LABELS: tuple[tuple[str, str], ...] = (
    ("species_age", "Species/age"),
    ("body", "Body & proportions"),
    ("face", "Head & face"),
    ("eyes", "Eyes"),
    ("hair", "Hair/fur"),
    ("outfit", "Outfit"),
    ("footwear", "Footwear"),
    ("signature_prop", "Signature prop"),
    ("marks", "Distinctive marks"),
    ("silhouette", "Silhouette"),
)


def design_source(name: str, description: str) -> str:
    """Fingerprint of what a design was built from; a change means the design is stale."""
    payload = f"{name.strip().lower()}\n{description.strip()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def render_design(name: str, design: CharacterDesign | None, fallback: str = "") -> str:
    """One compact paragraph describing the character for an image prompt."""
    if design is None or design.is_empty():
        return f"{name}: {fallback.strip()}" if fallback.strip() else name
    bits = [
        f"{label}: {getattr(design, key).strip()}"
        for key, label in _DESIGN_LABELS
        if getattr(design, key).strip()
    ]
    if design.always:
        bits.append("ALWAYS: " + "; ".join(item.strip() for item in design.always if item.strip()))
    if design.never:
        bits.append("NEVER: " + "; ".join(item.strip() for item in design.never if item.strip()))
    return f"{name} — " + ". ".join(bits) + "."


_PROPORTIONS_BY_CATEGORY = {
    "kids": "rounded storybook proportions, about 2.5 to 3 heads tall, big friendly head and eyes",
    "kawaii": "chibi proportions, about 2 heads tall, oversized round head, tiny body",
    "cozy": "soft rounded cute proportions, about 2.5 to 3 heads tall, chunky simple shapes",
    "manga": "manga proportions, about 6.5 to 7 heads tall for teens/adults, clean iconic shapes",
    "manhwa": "elegant manhwa proportions, about 7 to 8 heads tall, long limbs, refined features",
    "adult": "naturalistic editorial proportions, about 7 to 8 heads tall, ornamental detail",
}


def proportion_hint(category: str) -> str:
    return _PROPORTIONS_BY_CATEGORY.get(category, _PROPORTIONS_BY_CATEGORY["kids"])


def _str(value: object) -> str:
    return str(value or "").strip()[:_FIELD_LIMIT]


def _str_list(value: object, limit: int = 6) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_str(v) for v in value if _str(v)][:limit]


def parse_design(data: dict[str, object]) -> CharacterDesign:
    return CharacterDesign(
        species_age=_str(data.get("species_age")),
        body=_str(data.get("body")),
        face=_str(data.get("face")),
        eyes=_str(data.get("eyes")),
        hair=_str(data.get("hair")),
        outfit=_str(data.get("outfit")),
        footwear=_str(data.get("footwear")),
        signature_prop=_str(data.get("signature_prop")),
        marks=_str(data.get("marks")),
        silhouette=_str(data.get("silhouette")),
        always=_str_list(data.get("always")),
        never=_str_list(data.get("never")),
    )


_DESIGNER = (
    "You are a senior character designer for black-and-white coloring books and comics. "
    "You turn a short character description into a precise model-sheet specification that "
    "any illustrator can redraw identically on every page. Return ONLY valid JSON."
)


def design_prompt(
    *,
    name: str,
    description: str,
    category: str,
    style_context: str = "",
    story_context: str = "",
    others: Sequence[str] = (),
) -> str:
    """User prompt for the character-design LLM call (kept separate so it is testable)."""
    others_line = (
        "Other cast members (make this character clearly distinguishable from them): "
        + ", ".join(others)
        + "\n"
        if others
        else ""
    )
    story_block = f"\nBook context (binding):\n{story_context.strip()}\n" if story_context.strip() else ""
    return (
        f"Character name: {name.strip()}\n"
        f"Author description: {description.strip() or '(none: design one that fits the book)'}\n"
        f"Book category: {category or 'kids'}\n"
        f"Target proportions for this category: {proportion_hint(category)}\n"
        f"Book art style: {style_context.strip()[:600] or 'clean black-and-white coloring book line art'}\n"
        f"{others_line}"
        f"{story_block}\n"
        "Task: write the model-sheet specification. Keep every trait the author gave and fill "
        "the gaps with choices that fit the book. Rules:\n"
        "- Everything must be drawable in pure black outlines: describe shapes, lengths, counts "
        "and patterns (e.g. 'three round buttons', 'striped scarf with 4 stripes'), never colours "
        "or shading.\n"
        "- One fixed outfit for the whole book (no outfit changes) and one signature prop.\n"
        "- Keep it simple enough to colour: few large shapes, no tiny clutter.\n"
        "- 'always': 3 to 5 short rules that must hold in every drawing. 'never': 2 to 4 short "
        "rules for common mistakes (e.g. 'never draw shoes', 'never more than 3 whiskers per side').\n"
        "JSON shape:\n"
        '{"species_age": "", "body": "height in heads, build", "face": "head shape, muzzle/nose, '
        'mouth, ears", "eyes": "", "hair": "hair or fur shape", "outfit": "", "footwear": "", '
        '"signature_prop": "", "marks": "distinctive marks or empty", "silhouette": "the 2-3 shapes '
        'that make it recognisable at a glance", "always": [""], "never": [""]}'
    )


def design_character(
    *,
    name: str,
    description: str,
    category: str,
    style_context: str = "",
    story_context: str = "",
    others: Sequence[str] = (),
    provider: str = "",
    model: str | None = None,
) -> CharacterDesign:
    """Ask the text model for a detailed, structured design of one character."""
    from color_book_generator.story import (
        StoryGenerationError,
        _call_text_llm,
        _extract_json_object,
    )

    raw = _call_text_llm(
        _DESIGNER,
        design_prompt(
            name=name,
            description=description,
            category=category,
            style_context=style_context,
            story_context=story_context,
            others=others,
        ),
        preferred_provider=provider,
        preferred_model=model,
    )
    design = parse_design(_extract_json_object(raw))
    if design.is_empty():
        raise StoryGenerationError(f"O modelo nao descreveu o visual de {name}.")
    return design


# --------------------------------------------------------------------------
# Image prompts
# --------------------------------------------------------------------------

SHEET_SIZE = (1536, 1024)


def build_character_sheet_prompt(
    *,
    name: str,
    design_text: str,
    style_context: str,
    sheet_style: str = "",
) -> str:
    """Turnaround + expressions sheet that becomes the character's reference on every page."""
    parts = [
        f"OFFICIAL CHARACTER MODEL SHEET for {name.strip()}. This sheet is the single source "
        "of truth for how this character looks on every page of the book.",
        "LAYOUT on a pure white landscape canvas, no panel borders, frames or boxes:\n"
        "- Top row (about 60% of the height): four full-body turnaround views of the SAME "
        "character standing on one shared invisible ground line, at exactly the same height "
        "and scale: front view, three-quarter view, side profile, back view. Neutral relaxed "
        "pose, arms slightly away from the body so the outfit is fully visible.\n"
        "- Bottom row: four head-and-shoulders close-ups with the identical face design "
        "(happy, sad, surprised, determined), followed by one close-up of the signature prop.",
        "CHARACTER DESIGN (follow every detail, identical in every view):\n" + design_text.strip(),
        "CONSISTENCY: same proportions, same number of buttons/stripes/whiskers/spots, same "
        "hairstyle and same outfit in all nine drawings. This is one character, not a family.",
    ]
    if sheet_style.strip():
        parts.append("Category style notes: " + sheet_style.strip())
    if style_context.strip():
        parts.append("Book art style (match line weight and level of detail): " + style_context.strip())
    parts.append(
        "Pure black line art on pure white, crisp closed contours ready for coloring, zero "
        "shading, zero gray, zero color. No text, labels, names, arrows, grid or guide lines."
    )
    return "\n\n".join(parts)


LAYOUT_REFERENCE_NOTE = (
    "The reference image is a sample page of this book CATEGORY: copy ONLY its page format "
    "(where the illustration sits, how it ends in white space, margins, text area, line weight "
    "and level of detail). Do NOT copy its characters, animals, objects, scene or text."
)


def style_anchor_prompt(
    *, style_context: str, world: str, page_layout_rule: str = "", layout_reference: bool = False
) -> str:
    """Prompt for the book's own style anchor: its world, without any character."""
    setting = world.strip() or "a charming, typical setting of this book's world"
    parts = [
        style_context.strip(),
        page_layout_rule.strip(),
        LAYOUT_REFERENCE_NOTE if layout_reference else "",
        "STYLE ANCHOR: one establishing scene of the book's world that shows the exact line "
        "weight, level of detail, white space and composition every page will follow.",
        f"Setting: {setting}.",
        "Draw NO characters, people or animals: only the place, props and nature, so the "
        "anchor sets the style without competing with the characters' model sheets. No text.",
    ]
    return "\n\n".join(part for part in parts if part)


def cast_block(entries: Sequence[str]) -> str:
    """Characters that must appear in a page, each with its full design."""
    if not entries:
        return (
            "CAST IN THIS SCENE: none of the book's recurring characters. Do not add any "
            "named character; only generic background elements."
        )
    return (
        "CAST IN THIS SCENE (draw exactly these characters, each identical to its model sheet "
        "reference image and to the design below; same proportions, outfit, hairstyle and "
        "signature prop):\n"
        + "\n".join(f"- {entry}" for entry in entries)
        + "\nDo not add other main characters. Any extra figure must be a small, generic "
        "background element that does not resemble the cast."
    )


def world_block(world_rules: Sequence[str]) -> str:
    rules = [r.strip() for r in world_rules if r.strip()]
    if not rules:
        return ""
    return "WORLD RULES (keep consistent on every page):\n" + "\n".join(f"- {r}" for r in rules)


# --------------------------------------------------------------------------
# Script rules and cast matching
# --------------------------------------------------------------------------

CAST_RULES = (
    "Cast rules (MANDATORY for consistency):\n"
    "- Only the registered cast may appear as named or recurring characters. Never invent new "
    "named characters, sidekicks or friends; extra figures may only be small, unnamed "
    "background elements.\n"
    "- In scene_prompt refer to cast members by their EXACT name only. Never re-describe their "
    "species, appearance, hair, clothes or props (their model sheet defines them): describe only "
    "pose, expression, action, framing and setting.\n"
    "- List in character_names every cast member visible in the scene, using the exact names.\n"
)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _mentions(text: str, name: str) -> bool:
    needle = _norm(name)
    if not needle:
        return False
    return re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", _norm(text)) is not None


def match_cast_ids(
    names: Sequence[str],
    scene_text: str,
    cast: Sequence[tuple[str, str]],
) -> list[str]:
    """Map the names a script returned (and names mentioned in the scene) to cast ids.

    ``cast`` is a list of ``(id, name)``. Matching is case-insensitive and tolerant of
    decorated names ("Jack the fox" matches "Jack"), and also picks up cast members the
    scene text mentions but the list forgot.
    """
    found: list[str] = []
    for raw in names:
        given = _norm(raw)
        if not given:
            continue
        for cid, cname in cast:
            target = _norm(cname)
            if target and (given == target or _mentions(given, target) or _mentions(target, given)):
                found.append(cid)
                break
    for cid, cname in cast:
        if _mentions(scene_text, cname):
            found.append(cid)
    return list(dict.fromkeys(found))


def sheet_style_for(preset: StylePreset | None) -> str:
    return preset.character_sheet_prompt if preset is not None else ""
