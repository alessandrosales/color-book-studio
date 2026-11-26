"""Prompt construction for line-art pages."""

from __future__ import annotations

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
        "with generous 12-15% pure white outer margins, thick uniform monoline outlines, large open colorable areas, "
        "no external captions or speech balloons."
    ),
    "single_caption": (
        "Page layout: children's storybook coloring page with one open, friendly illustration filling the upper "
        "70-75% of the page, generous white breathing space, and a clean empty white band at the bottom 20-25% "
        "reserved for narrative caption text."
    ),
    "manga_action": (
        "Page layout: authentic Japanese manga multi-panel page with narrow 5-8% outer margins, 4 to 6 asymmetric "
        "crisp black-bordered panels including a diagonal action cut, one dominant central impact panel, narrow "
        "gutters, expressive variable-weight ink lines, and clean empty white vertical-oval speech balloon shapes "
        "crossing panel borders where dialogue fits."
    ),
    "manhwa_editorial": (
        "Page layout: Korean manhwa print page adapted from webtoon vertical rhythm with generous 20-30% white "
        "margins and wide gutters, 3 clean rectangular panels (one cinematic upper panel and tall vertical beat "
        "panels below), thin crisp digital black line art, and clean round speech balloon outlines floating "
        "across the white gutters."
    ),
    "kawaii_stickers": (
        "Page layout: kawaii chibi / sticker-cluster coloring page arranged as a playful 2x2 grid of rounded "
        "vignette frames (or compact central sticker cluster) with cute floating stars and hearts, clean 10% "
        "outer margins, smooth medium-fine outlines, and no external text."
    ),
    "adult_medallion": (
        "Page layout: adult intricate coloring page framed as a symmetrical circular medallion, mandala, or "
        "organic botanical arch with fine detailed linework, dense ornamental petals/vines/patterns, crisp "
        "pure black lines on white paper, and zero text or speech balloons."
    ),
}


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
) -> str:
    """Combine the shared context prompt, page layout directive, and a single page's prompt."""
    parts: list[str] = []
    head = context.strip()
    if head:
        parts.append(head)
    if page_layout and page_layout != "single_full":
        layout_rule = PAGE_LAYOUT_PROMPTS.get(page_layout)
        if layout_rule:
            parts.append(layout_rule)
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
