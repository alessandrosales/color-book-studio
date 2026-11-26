"""Turn a natural-language page prompt into a concrete text/illustration layout."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ValidationError

from color_book_generator.story import _call_text_llm, _extract_json_object


class PageLayout(BaseModel):
    text_align: Literal["left", "center"] = "center"
    text_vertical: Literal["top", "center", "bottom"] = "center"
    show_border: bool = False
    image_position: Literal["background", "above", "below", "none"] = "none"
    image_size: Literal["small", "medium", "large"] = "small"
    image_prompt: str = ""


_INSTRUCTIONS = (
    "You are a page layout director for a children's coloring book. "
    "Read the author's page prompt and decide how the text and illustration should "
    "be arranged. Return strict JSON only, with no prose."
)

_TEMPLATE = """Page title: {title}
Page text:
{text}

Author's page prompt:
{prompt}

Decide the layout and return exactly this JSON shape:
{{
  "text_align": "left|center",
  "text_vertical": "top|center|bottom",
  "show_border": true|false,
  "image_position": "background|above|below|none",
  "image_size": "small|medium|large",
  "image_prompt": "a short visual description of the illustration only"
}}

Rules:
- "image_prompt" must describe only the illustration/subject, never typography or layout.
- If the author asks for text centered horizontally/vertically, set text_align="center" and text_vertical="center".
- If the author says "above", "below" or "background" for the image, use that position.
- If the author says "no border" or "sem borda", set show_border=false.
- If the author asks for no image, use image_position="none" and image_prompt="".
- Keep the layout simple and printable.
"""


def plan_page_layout(
    *,
    page_prompt: str,
    title: str,
    text: str,
    provider: str = "",
    model: str | None = None,
) -> PageLayout:
    user_prompt = _TEMPLATE.format(
        title=title.strip() or "(none)",
        text=text.strip() or "(none)",
        prompt=page_prompt.strip(),
    )
    raw = _call_text_llm(
        _INSTRUCTIONS,
        user_prompt,
        preferred_provider=provider,
        preferred_model=model,
    )
    data = _extract_json_object(raw)
    try:
        return PageLayout.model_validate(data)
    except ValidationError as exc:  # pragma: no cover - defensive
        raise ValueError("resposta de layout invalida") from exc
