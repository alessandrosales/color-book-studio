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


def compose_page_prompt(context: str, page_prompt: str, index: int, total: int) -> str:
    """Combine the shared context prompt with a single page's prompt."""
    head = context.strip()
    body = f"Page {index} of {total}. Subject: {page_prompt.strip().rstrip('.')}."
    return f"{head}\n\n{body}" if head else body


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
