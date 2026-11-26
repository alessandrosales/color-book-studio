"""LLM-assisted story scripting, page breakdown, and character-sheet prompting."""

from __future__ import annotations

import json
import os
import re
from typing import Any

from pydantic import BaseModel, Field

from color_book_generator.auth import codex as codex_auth
from color_book_generator.presets import StoryMode as StoryMode
from color_book_generator.presets import StylePreset


class StoryGenerationError(RuntimeError):
    """Raised when story/script generation fails or no text backend is available."""


class CharacterBrief(BaseModel):
    id: str
    name: str
    description: str = ""


class ScriptedPage(BaseModel):
    index: int
    title: str = ""
    scene_prompt: str
    caption_text: str = ""
    character_names: list[str] = Field(default_factory=list)
    character_ids: list[str] = Field(default_factory=list)


class StoryPlan(BaseModel):
    title: str
    synopsis: str
    mode: StoryMode = "narrative"
    pages: list[ScriptedPage]


class GeneratedCharacter(BaseModel):
    name: str
    description: str


class FullBookBlueprint(BaseModel):
    title: str
    premise: str
    synopsis: str
    mode: StoryMode = "narrative"
    style_anchor_subject: str
    characters: list[GeneratedCharacter] = Field(default_factory=list)
    pages: list[ScriptedPage]


def build_character_sheet_prompt(
    name: str,
    description: str,
    style_context: str,
    sheet_template: str = "",
) -> str:
    """Compose a prompt to generate a consistent character reference sheet in the book style."""
    base_template = sheet_template.strip() or (
        "Character model sheet on a pure white background showing 3 views: "
        "front standing full body, side/three-quarter view, and 2 expressive close-up faces. "
        "Pure black-and-white coloring book line art, zero shading, zero gray."
    )
    parts = [
        base_template,
        f"Character name: {name.strip()}.",
    ]
    if description.strip():
        parts.append(f"Visual appearance & outfit: {description.strip()}.")
    if style_context.strip():
        parts.append(f"Art style rules to match strictly: {style_context.strip()}.")
    parts.append("No text labels, no watermarks, crisp closed black lines on pure white paper.")
    return "\n\n".join(parts)


def _extract_json_object(raw: str) -> dict[str, Any]:
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start : end + 1]
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise StoryGenerationError(
            "A resposta do modelo nao veio em JSON valido. Tente gerar novamente."
        ) from exc
    if not isinstance(parsed, dict):
        raise StoryGenerationError("Resposta estruturada invalida para o roteiro.")
    return parsed


def _call_codex_text(instructions: str, user_prompt: str, model: str | None) -> str:
    resolved = codex_auth.resolve_codex_model(model)
    try:
        return codex_auth.create_response(
            model=resolved,
            instructions=instructions,
            content=[{"type": "input_text", "text": user_prompt}],
        )
    except codex_auth.CodexAuthError as exc:
        raise StoryGenerationError(str(exc)) from exc


def _call_openai_text(instructions: str, user_prompt: str, model: str | None) -> str:
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover
        raise StoryGenerationError("extra openai nao instalado: uv sync --extra openai") from exc
    if not os.getenv("OPENAI_API_KEY"):
        raise StoryGenerationError("OPENAI_API_KEY nao configurada")
    text_model = (
        model
        if model and not any(k in model.lower() for k in ("image", "dall-e", "flux"))
        else "gpt-4o-mini"
    )
    client = OpenAI()
    try:
        resp = client.chat.completions.create(
            model=text_model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": instructions},
                {"role": "user", "content": user_prompt},
            ],
        )
    except Exception as exc:  # pragma: no cover
        raise StoryGenerationError(f"falha no OpenAI ao gerar roteiro: {exc}") from exc
    content = resp.choices[0].message.content if resp.choices else None
    if not isinstance(content, str) or not content:
        raise StoryGenerationError("OpenAI retornou resposta vazia ao gerar roteiro")
    return content


def _call_gemini_text(instructions: str, user_prompt: str) -> str:
    try:
        from google import genai
    except ImportError as exc:  # pragma: no cover
        raise StoryGenerationError("extra gemini nao instalado: uv sync --extra gemini") from exc
    if not os.getenv("GEMINI_API_KEY"):
        raise StoryGenerationError("GEMINI_API_KEY nao configurada")
    client = genai.Client()
    try:
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=f"{instructions}\n\n{user_prompt}",
        )
    except Exception as exc:  # pragma: no cover
        raise StoryGenerationError(f"falha no Gemini ao gerar roteiro: {exc}") from exc
    text = getattr(resp, "text", None)
    if not isinstance(text, str) or not text.strip():
        raise StoryGenerationError("Gemini retornou resposta vazia ao gerar roteiro")
    return text


def _call_text_llm(
    instructions: str,
    user_prompt: str,
    *,
    preferred_provider: str = "",
    preferred_model: str | None = None,
) -> str:
    provider = preferred_provider.strip().lower()
    if provider == "codex" and codex_auth.is_authenticated():
        return _call_codex_text(instructions, user_prompt, preferred_model)
    if provider == "openai" and os.getenv("OPENAI_API_KEY"):
        return _call_openai_text(instructions, user_prompt, preferred_model)
    if provider == "gemini" and os.getenv("GEMINI_API_KEY"):
        return _call_gemini_text(instructions, user_prompt)

    # Automatic fallback order: authenticated Codex -> OpenAI -> Gemini
    if codex_auth.is_authenticated():
        return _call_codex_text(instructions, user_prompt, preferred_model)
    if os.getenv("OPENAI_API_KEY"):
        return _call_openai_text(instructions, user_prompt, None)
    if os.getenv("GEMINI_API_KEY"):
        return _call_gemini_text(instructions, user_prompt)

    raise StoryGenerationError(
        "Nenhum provedor de texto autenticado. Conecte sua conta ChatGPT/Codex "
        "ou configure OPENAI_API_KEY / GEMINI_API_KEY."
    )


def generate_story_plan(
    *,
    premise: str,
    pages: int,
    mode: StoryMode = "narrative",
    language: str = "en",
    preset: StylePreset | None = None,
    style_context: str = "",
    characters: list[CharacterBrief] | None = None,
    provider: str = "",
    model: str | None = None,
) -> StoryPlan:
    """Generate a structured book synopsis and page-by-page visual script + captions."""
    clean_premise = premise.strip()
    if not clean_premise:
        raise StoryGenerationError("Informe uma ideia ou tema para gerar o roteiro.")

    cast = characters or []
    cast_lines = (
        "\n".join(
            f"- {c.name}: {c.description or 'personagem recorrente do livro'}"
            for c in cast
        )
        if cast
        else "- Nenhum personagem fixo cadastrado ainda (crie sujeitos coerentes com o tema)."
    )
    style_desc = (
        f"{preset.name} ({preset.tagline}). Concept: {preset.concept_prompt}"
        if preset is not None
        else (style_context[:180] or "Livro de colorir P&B")
    )
    mode_instruction = (
        "Create a cohesive STORY with beginning, middle, emotional arc, and satisfying ending across the pages."
        if mode == "narrative"
        else "Create a cohesive THEMATIC COLLECTION of charming standalone scenes in the same universe (ideal for Cozy / Bold & Easy coloring books)."
    )

    instructions = (
        "You are an expert bestselling KDP coloring book author and visual director. "
        "Return ONLY valid JSON with no markdown prose outside the JSON object.\n"
        "JSON schema:\n"
        "{\n"
        '  "title": "catchy commercial book title",\n'
        '  "synopsis": "2-3 sentence overview of the book",\n'
        '  "pages": [\n'
        "    {\n"
        '      "index": 1,\n'
        '      "title": "short scene label",\n'
        '      "scene_prompt": "clear visual description in English or target language describing subject, pose, action, props, framing (single clear coloring scene, no text inside the drawing)",\n'
        '      "caption_text": "short engaging caption or speech bubble text for the page (max 14 words)",\n'
        '      "character_names": ["Exact Character Name from cast if present in this scene"]\n'
        "    }\n"
        "  ]\n"
        "}"
    )

    user_prompt = (
        f"Language for title, synopsis and caption_text: {language}\n"
        f"Book mode: {mode} — {mode_instruction}\n"
        f"Visual style category: {style_desc}\n"
        f"Premise / Theme: {clean_premise}\n"
        f"Exact number of pages required: {pages}\n"
        f"Registered characters:\n{cast_lines}\n\n"
        "Rules:\n"
        "- Generate EXACTLY the requested number of pages, numbered 1..N.\n"
        "- Every scene_prompt must describe ONE clear, drawable, high-converting coloring scene with distinct pose/setting (avoid repeating compositions).\n"
        "- When registered characters exist, include their exact names in character_names whenever they appear in the scene.\n"
        "- Never ask for colors or shading in scene_prompt.\n"
    )

    raw = _call_text_llm(
        instructions,
        user_prompt,
        preferred_provider=provider,
        preferred_model=model,
    )
    data = _extract_json_object(raw)

    by_name = {c.name.strip().lower(): c.id for c in cast}
    raw_pages = data.get("pages")
    if not isinstance(raw_pages, list) or not raw_pages:
        raise StoryGenerationError("O modelo nao retornou a lista de paginas no roteiro.")

    scripted: list[ScriptedPage] = []
    for i in range(pages):
        item = raw_pages[i] if i < len(raw_pages) else raw_pages[-1]
        if not isinstance(item, dict):
            continue
        scene = str(item.get("scene_prompt") or item.get("prompt") or "").strip()
        if not scene:
            scene = f"{clean_premise}, scene {i + 1}"
        caption = str(item.get("caption_text") or item.get("caption") or "").strip()
        title_str = str(item.get("title") or f"Página {i + 1}").strip()
        raw_names = item.get("character_names") or []
        names = [str(n).strip() for n in raw_names if isinstance(n, str) and n.strip()]
        matched_ids = [
            by_name[n.lower()] for n in names if n.lower() in by_name
        ]
        # Preserve order and deduplicate
        unique_ids = list(dict.fromkeys(matched_ids))
        scripted.append(
            ScriptedPage(
                index=i + 1,
                title=title_str,
                scene_prompt=scene,
                caption_text=caption,
                character_names=names,
                character_ids=unique_ids,
            )
        )

    return StoryPlan(
        title=str(data.get("title") or clean_premise[:60]).strip(),
        synopsis=str(data.get("synopsis") or "").strip(),
        mode=mode,
        pages=scripted,
    )


def generate_full_book_blueprint(
    *,
    pages: int,
    preset: StylePreset | None = None,
    style_context: str = "",
    premise_hint: str = "",
    mode: StoryMode | None = None,
    language: str = "en",
    provider: str = "",
    model: str | None = None,
) -> FullBookBlueprint:
    """Invent the entire book from ONLY the selected style: title, cast, anchor, and all pages."""
    chosen_mode: StoryMode = mode or (
        preset.default_mode if preset is not None else "collection"
    )
    cast_size = preset.cast if preset is not None else 2
    concept_line = (
        f"Style concept guidance (stay inside this concept): {preset.concept_prompt}\n"
        if preset is not None and preset.concept_prompt
        else ""
    )
    style_name = preset.name if preset is not None else "Coloring Book"
    style_desc = (
        f"{preset.name} ({preset.category}) — {preset.tagline}. {preset.context_prompt}"
        if preset is not None
        else (style_context or "Clean black-and-white coloring book line art")
    )
    hint_line = (
        f"Optional user theme hint (follow it closely): {premise_hint.strip()}"
        if premise_hint.strip()
        else "No user theme provided: invent an original, high-converting, commercially appealing concept that fits this category perfectly."
    )
    if cast_size == 0:
        char_guidance = (
            "Do not create any recurring characters: return an empty characters list and build "
            "every page from the style's subjects (patterns, objects, scenes)."
        )
    else:
        plural = "s" if cast_size != 1 else ""
        char_guidance = (
            f"Create exactly {cast_size} memorable recurring main character{plural} with vivid, "
            "consistent visual traits (species or age, hairstyle, signature outfit, accessories) "
            "so they stay recognizable on every page."
        )

    instructions = (
        "You are an expert bestselling KDP coloring book creator, character designer, and visual director. "
        "Return ONLY valid JSON with no markdown or commentary outside the JSON object.\n"
        "JSON schema:\n"
        "{\n"
        '  "title": "catchy commercial book title",\n'
        '  "premise": "1 sentence core concept",\n'
        '  "synopsis": "2-3 sentence engaging book overview",\n'
        '  "style_anchor_subject": "one iconic showcase coloring scene that demonstrates the exact line weight and aesthetic of the book",\n'
        '  "characters": [\n'
        '    {"name": "Short Name", "description": "precise visual traits: species/appearance, hair/ears, signature outfit, key accessory (black-and-white drawable details, no color names required)"}\n'
        "  ],\n"
        '  "pages": [\n'
        "    {\n"
        '      "index": 1,\n'
        '      "title": "short scene label",\n'
        '      "scene_prompt": "detailed visual coloring-book scene description (subject, pose, action, cozy props/setting, centered balanced framing, NO colors, NO shading, NO text inside the drawing)",\n'
        '      "caption_text": "short charming caption or speech bubble text (max 12 words)",\n'
        '      "character_names": ["Exact Name from characters list if present"]\n'
        "    }\n"
        "  ]\n"
        "}"
    )

    user_prompt = (
        f"Language for title, premise, synopsis, page titles and caption_text: {language}\n"
        f"Selected style & category: {style_name} — {style_desc}\n"
        f"Book mode: {chosen_mode}\n"
        f"{concept_line}"
        f"{hint_line}\n"
        f"Character rule: {char_guidance}\n"
        f"Exact number of pages required: {pages}\n\n"
        "Strict rules:\n"
        "- Produce EXACTLY the requested number of pages, numbered 1..N.\n"
        "- Every page must have a distinct, joyful, high-converting composition with varied actions and props.\n"
        "- Keep all scene_prompt and character descriptions 100% suitable for clean black-and-white line art.\n"
    )

    raw = _call_text_llm(
        instructions,
        user_prompt,
        preferred_provider=provider,
        preferred_model=model,
    )
    data = _extract_json_object(raw)

    raw_chars = data.get("characters")
    characters: list[GeneratedCharacter] = []
    if isinstance(raw_chars, list):
        for item in raw_chars[:4]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            desc = str(item.get("description") or "").strip()
            if name:
                characters.append(GeneratedCharacter(name=name, description=desc))

    raw_pages = data.get("pages")
    if not isinstance(raw_pages, list) or not raw_pages:
        raise StoryGenerationError("O modelo nao retornou a lista de paginas do livro.")

    scripted: list[ScriptedPage] = []
    for i in range(pages):
        item = raw_pages[i] if i < len(raw_pages) else raw_pages[-1]
        if not isinstance(item, dict):
            continue
        scene = str(item.get("scene_prompt") or item.get("prompt") or "").strip()
        if not scene:
            scene = f"Charming {style_name} coloring scene {i + 1}"
        caption = str(item.get("caption_text") or item.get("caption") or "").strip()
        title_str = str(item.get("title") or f"Página {i + 1}").strip()
        raw_names = item.get("character_names") or []
        names = [str(n).strip() for n in raw_names if isinstance(n, str) and n.strip()]
        scripted.append(
            ScriptedPage(
                index=i + 1,
                title=title_str,
                scene_prompt=scene,
                caption_text=caption,
                character_names=names,
            )
        )

    title = str(data.get("title") or f"Livro {style_name}").strip()
    premise = str(data.get("premise") or premise_hint or title).strip()
    synopsis = str(data.get("synopsis") or "").strip()
    anchor = str(
        data.get("style_anchor_subject")
        or (scripted[0].scene_prompt if scripted else f"iconic {style_name} coloring scene")
    ).strip()

    return FullBookBlueprint(
        title=title,
        premise=premise,
        synopsis=synopsis,
        mode=chosen_mode,
        style_anchor_subject=anchor,
        characters=characters,
        pages=scripted,
    )
