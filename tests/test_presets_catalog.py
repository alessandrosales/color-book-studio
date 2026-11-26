"""Tests for the style catalog (categories, styles, concepts) and its use by the blueprint."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from color_book_generator import story as story_mod
from color_book_generator.presets import CATEGORY_META, builtin_presets, get_preset
from color_book_generator.web import store
from color_book_generator.web.app import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _isolated_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store, "WEB_ROOT", tmp_path / "web")


def test_every_category_has_at_least_five_styles() -> None:
    presets = builtin_presets()
    for meta in CATEGORY_META:
        styles = [p for p in presets if p.category == meta["id"]]
        assert len(styles) >= 5, meta["id"]


def test_style_ids_are_unique_and_contexts_complete() -> None:
    presets = builtin_presets()
    ids = [p.id for p in presets]
    assert len(ids) == len(set(ids))
    for preset in presets:
        assert preset.context_prompt.strip(), preset.id
        assert preset.concept_prompt.strip(), preset.id
        assert preset.negative_prompt.strip(), preset.id
        assert preset.character_sheet_prompt.strip(), preset.id
        assert "no text" in preset.context_prompt.lower(), preset.id


def test_legacy_ids_still_exist() -> None:
    ids = {p.id for p in builtin_presets()}
    assert {
        "bold_easy_cozy",
        "kids_classic",
        "manga_lineart",
        "manhwa_lineart",
        "kawaii_chibi",
        "adult_intricate",
    } <= ids


def test_categories_endpoint_counts_styles() -> None:
    body = client.get("/api/presets/categories").json()
    assert [c["id"] for c in body][:6] == [m["id"] for m in CATEGORY_META]
    assert all(c["count"] >= 5 for c in body)


def test_blueprint_prompt_uses_concept_and_cast(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, str] = {}

    def fake_llm(instructions: str, user_prompt: str, **_kwargs: object) -> str:
        seen["prompt"] = user_prompt
        return json.dumps(
            {
                "title": "T",
                "premise": "P",
                "synopsis": "S",
                "style_anchor_subject": "A",
                "characters": [],
                "pages": [{"index": 1, "title": "One", "scene_prompt": "x", "caption_text": "y"}],
            }
        )

    monkeypatch.setattr(story_mod, "_call_text_llm", fake_llm)

    mandala = get_preset("adult_intricate")
    assert mandala is not None
    story_mod.generate_full_book_blueprint(pages=1, preset=mandala)
    assert mandala.concept_prompt in seen["prompt"]
    assert "Do not create any recurring characters" in seen["prompt"]

    cozy = get_preset("bold_easy_cozy")
    assert cozy is not None
    story_mod.generate_full_book_blueprint(pages=1, preset=cozy)
    assert "Create exactly 1 memorable recurring main character " in seen["prompt"]

    manga = get_preset("manga_lineart")
    assert manga is not None
    story_mod.generate_full_book_blueprint(pages=1, preset=manga)
    assert "Book mode: narrative" in seen["prompt"]
