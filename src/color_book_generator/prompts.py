"""Prompt construction for line-art pages."""

from __future__ import annotations

import re
from pathlib import Path

from color_book_generator.config import BookConfig
from color_book_generator.models import PagePrompt

COMPOSITIONS = [
    "close-up portrait, centered",
    "full body standing, centered",
    "sitting and smiling, centered",
    "surrounded by simple leaves and flowers",
    "in a simple forest scene with a few trees",
    "with a large sun and clouds behind",
    "holding a single flower",
    "peeking from behind a bush",
]

LAYOUT_RULES = (
    "single clear subject, balanced composition, generous white space around the subject, "
    "closed shapes suitable for coloring, consistent line weight"
)

PAGE_LAYOUT_PROMPTS: dict[str, str] = {
    "single_full": (
        "Page layout: single centered Bold & Easy / Cozy scene framed inside a clean rounded square border "
        "with generous 12-15% pure white outer margins, thick uniform monoline outlines, large open colorable areas."
    ),
    "single_caption": (
        "Page layout: children's storybook coloring page with one open, friendly illustration filling the upper "
        "70-75% of the page, generous white breathing space, separated by a crisp horizontal divider from a clean "
        "white narrative caption band at the bottom 20-25%."
    ),
    "manga_action": (
        "Page layout: clean Japanese manga multi-panel page closely matching the style reference. "
        "Narrow 5-8% white outer margins, 4 to 6 asymmetric crisp black-bordered panels with clean white gutters "
        "and diagonal action cuts, mixing dramatic close-up panels (eyes, expressive face, hand/object detail) "
        "with one dominant spacious central action panel using crisp radial speed lines. "
        "CRITICAL VISUAL CLEANLINESS: keep every panel UNCLUTTERED with only 1 primary focal beat (1-2 characters "
        "max per panel), generous open white breathing space, simple minimal backgrounds (NO dense clutter, NO "
        "crowded shelves/props, NO heavy cross-hatching), and smooth clean black ink contours ready for coloring."
    ),
    "manhwa_editorial": (
        "Page layout: Korean manhwa print page adapted from webtoon vertical rhythm with generous 20-30% white "
        "margins and wide gutters, 3 clean rectangular panels (one cinematic upper panel and tall vertical beat "
        "panels below), and thin crisp digital black line art."
    ),
    "kawaii_stickers": (
        "Page layout: kawaii chibi / sticker-cluster coloring page arranged as a playful 2x2 grid of rounded "
        "vignette frames (or compact central sticker cluster) with cute floating stars and hearts, clean 10% "
        "outer margins, and smooth medium-fine outlines."
    ),
    "adult_medallion": (
        "Page layout: adult intricate coloring page framed as a symmetrical circular medallion, mandala, or "
        "organic botanical arch with fine detailed linework, dense ornamental petals/vines/patterns, and crisp "
        "pure black lines on white paper."
    ),
}

_FONT_STYLE_HINTS: dict[str, str] = {
    "sans": "clean rounded sans-serif lettering",
    "serif": "refined editorial serif lettering",
    "mono": "clean monospaced typewriter lettering",
}

_NO_TEXT_CONTEXT_RE = re.compile(
    r",?\s*\b(?:no|zero)\s+(?:external\s+)?(?:text|captions?|speech\s+balloons?|speech\s+bubbles?|lettering)\b",
    flags=re.IGNORECASE,
)

_NEGATIVE_TEXT_TOKENS = {
    "text",
    "speech bubble",
    "speech bubbles",
    "speech balloon",
    "speech balloons",
    "lettering",
    "caption",
    "captions",
}


def strip_no_text_rules(context: str) -> str:
    """Remove 'no text' clauses from context when the page intentionally embeds text."""
    cleaned = _NO_TEXT_CONTEXT_RE.sub("", context)
    cleaned = re.sub(r"\s+([.,;:])", r"\1", cleaned)
    cleaned = re.sub(r",\s*\.", ".", cleaned)
    cleaned = re.sub(r"\.{2,}", ".", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip()


def effective_negative_prompt(
    negative_prompt: str,
    *,
    has_embedded_text: bool = False,
    page_layout: str = "single_full",
) -> str:
    """Adjust negative prompt so embedded text and layout speech balloons are not suppressed."""
    raw_terms = [t.strip() for t in negative_prompt.split(",") if t.strip()]
    if has_embedded_text:
        terms = [t for t in raw_terms if t.lower() not in _NEGATIVE_TEXT_TOKENS]
        extras = ["gibberish text", "misspelled text", "extra unintended text"]
        if page_layout in {"manga_action", "manhwa_editorial"}:
            extras.append("empty speech bubbles")
        for extra in extras:
            if extra not in {t.lower() for t in terms}:
                terms.append(extra)
        return ", ".join(terms)
    return ", ".join(raw_terms)


def _split_dialogue_lines(caption_text: str) -> list[str]:
    """Split multi-bubble dialogue separated by '|' or newlines into clean lines."""
    parts = [p.strip() for p in re.split(r"\s*\|\s*|\n+", caption_text.strip()) if p.strip()]
    return parts or [caption_text.strip()]


def build_layout_text_directive(
    page_layout: str,
    caption_text: str,
    *,
    caption_position: str = "bottom",
    caption_font_family: str = "sans",
    caption_font_bold: bool = False,
) -> str:
    """Return the exact in-image text placement instruction for a given page layout."""
    clean_text = caption_text.strip()
    layout = page_layout or "single_full"

    if not clean_text:
        if layout in {"manga_action", "manhwa_editorial"}:
            return (
                "Text & speech balloons: NO text and NO speech balloons anywhere on the page "
                "(do not draw empty or blank speech bubbles)."
            )
        if layout == "single_caption":
            return (
                "Text: NO text anywhere on the page; fill the illustration cleanly without an empty caption box."
            )
        return "Text: zero text, zero captions, zero speech balloons anywhere on the page."

    font_hint = _FONT_STYLE_HINTS.get(caption_font_family, _FONT_STYLE_HINTS["sans"])
    if caption_font_bold:
        font_hint = f"bold {font_hint}"

    pos_word = "top" if caption_position == "top" else "bottom"
    lines = _split_dialogue_lines(clean_text)

    if layout == "manga_action":
        if len(lines) > 1:
            quoted = "; ".join(f'Balloon {i}: "{line}"' for i, line in enumerate(lines, start=1))
            placement = (
                f"Draw exactly {len(lines)} TALL, NARROW vertical-oval white manga speech balloons "
                f"(NOT wide round circles) crossing panel borders with crisp black tails pointing to the speaker, "
                f"containing this exact dialogue in reading order: {quoted}."
            )
        else:
            placement = (
                f'Draw one TALL, NARROW vertical-oval white manga speech balloon (NOT a wide round circle) '
                f'crossing a panel border with a crisp black tail pointing to the speaker, containing this exact '
                f'dialogue: "{lines[0]}".'
            )
        return (
            f"Text placement (Manga speech balloons): {placement} "
            f"Use crisp, bold, uppercase pure black {font_hint} stacked neatly inside the narrow vertical oval "
            "(like authentic shonen manga lettering in the style reference). "
            "Every speech balloon drawn on the page MUST contain the requested text — NEVER draw empty or blank "
            "speech balloons, NEVER place text in a separate box outside the manga panels, and render ONLY this exact "
            "text verbatim with accurate spelling."
        )

    if layout == "manhwa_editorial":
        if len(lines) > 1:
            quoted = "; ".join(f'Balloon {i}: "{line}"' for i, line in enumerate(lines, start=1))
            placement = (
                f"Draw exactly {len(lines)} clean round/oval white manhwa speech balloons floating across "
                f"the white gutters between panels containing this exact dialogue in reading order: {quoted}."
            )
        else:
            placement = (
                f'Draw one clean round/oval white manhwa speech balloon floating across the white gutter '
                f'between panels containing this exact dialogue: "{lines[0]}".'
            )
        return (
            f"Text placement (Manhwa gutter balloons): {placement} "
            f"Use crisp pure black {font_hint} centered inside the balloon(s). "
            "Every speech balloon drawn on the page MUST contain the requested text — NEVER draw empty or blank "
            "speech balloons, NEVER add a separate caption strip outside the layout, and render ONLY this exact "
            "text verbatim with accurate spelling."
        )

    if layout == "single_caption":
        return (
            f'Text placement (Storybook caption band): Inside the dedicated clean white {pos_word} band '
            f'(separated from the illustration by a crisp horizontal frame line), render this exact narrative '
            f'text centered in large, friendly, highly legible pure black {font_hint}: "{clean_text}". '
            "Do NOT draw speech balloons inside the illustration and render ONLY this exact text verbatim "
            "with accurate spelling."
        )

    if layout == "kawaii_stickers":
        return (
            f'Text placement (Kawaii integrated label): Centered cleanly in the white {pos_word} margin '
            f'of the page (harmonized with the rounded vignette frames, no speech balloons), render this exact '
            f'short text in cute, crisp pure black {font_hint}: "{clean_text}". '
            "Render ONLY this exact text verbatim with accurate spelling and no other words inside the artwork."
        )

    if layout == "adult_medallion":
        return (
            f'Text placement (Editorial quote): Centered cleanly in the pure white {pos_word} margin '
            f'below/outside the intricate ornamental artwork (no speech balloons, no heavy box), render this exact '
            f'text in delicate, crisp pure black {font_hint}: "{clean_text}". '
            "Render ONLY this exact text verbatim with accurate spelling and no other words inside the artwork."
        )

    # Default: single_full (Bold & Easy / Cozy)
    return (
        f'Text placement (Cozy framed caption): Centered cleanly in the generous white {pos_word} margin '
        f'outside the rounded-square illustration border (no speech balloons), render this exact text in '
        f'friendly, crisp pure black {font_hint} matching the thick outlines: "{clean_text}". '
        "Render ONLY this exact text verbatim with accurate spelling and no extra unintended text."
    )


def default_context_prompt(config: BookConfig) -> str:
    """The shared page-standard prompt applied to every page."""
    parts = [
        "Coloring book line art page.",
        f"Audience: {config.audience}." if config.audience else "",
        f"Style: {config.style}." if config.style else "",
        LAYOUT_RULES + ".",
        config.prompt_suffix.strip().rstrip(".") + "." if config.prompt_suffix else "",
    ]
    return " ".join(p for p in parts if p)


def compose_page_prompt(
    context: str,
    page_prompt: str,
    index: int,
    total: int,
    *,
    page_layout: str = "single_full",
    caption_text: str = "",
    caption_position: str = "bottom",
    caption_font_family: str = "sans",
    caption_font_bold: bool = False,
) -> str:
    """Combine the shared context prompt, page layout directive, embedded text directive, and page subject."""
    parts: list[str] = []
    has_text = bool(caption_text.strip())
    head = strip_no_text_rules(context) if has_text else context.strip()
    if head:
        parts.append(head)
    layout_key = page_layout or "single_full"
    layout_rule = PAGE_LAYOUT_PROMPTS.get(layout_key)
    if layout_rule:
        parts.append(layout_rule)
    parts.append(
        build_layout_text_directive(
            layout_key,
            caption_text,
            caption_position=caption_position,
            caption_font_family=caption_font_family,
            caption_font_bold=caption_font_bold,
        )
    )
    parts.append(f"Page {index} of {total}. Subject: {page_prompt.strip().rstrip('.')}.")
    return "\n\n".join(parts)


def build_page_prompt(config: BookConfig, subject: str, index: int) -> str:
    return compose_page_prompt(
        default_context_prompt(config), subject, index, config.pages
    )


def default_subjects(config: BookConfig) -> list[str]:
    """Deterministic subject rotation derived from the theme."""
    theme = config.theme.strip().rstrip(".")
    n = config.pages
    subjects: list[str] = []
    for i in range(n):
        comp = COMPOSITIONS[i % len(COMPOSITIONS)]
        subjects.append(f"a cute {theme}, {comp}")
    return subjects


def load_subjects(path: str | Path | None) -> list[str] | None:
    if path is None:
        return None
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"subjects file not found: {p}")
    lines = [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines()]
    subjects = [ln for ln in lines if ln and not ln.startswith("#")]
    return subjects or None


def build_page_prompts(
    config: BookConfig, subjects: list[str] | None = None
) -> list[PagePrompt]:
    chosen = subjects or default_subjects(config)
    if len(chosen) < config.pages:
        chosen = [chosen[i % len(chosen)] for i in range(config.pages)]
    chosen = chosen[: config.pages]
    return [
        PagePrompt(
            index=i + 1,
            subject=subject,
            prompt=build_page_prompt(config, subject, i + 1),
            negative_prompt=config.negative_prompt,
        )
        for i, subject in enumerate(chosen)
    ]
