"""Briefing step: story brief, premises, story bible and its use as binding context."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from color_book_generator import brief as brief_mod
from color_book_generator import story as story_mod
from color_book_generator.brief import (
    BibleCharacter,
    StoryBeat,
    StoryBible,
    StoryBrief,
    story_context,
    suggest_preset_ids,
)
from color_book_generator.web import store
from color_book_generator.web.app import app

client = TestClient(app)

BIBLE_JSON = json.dumps(
    {
        "title": "Lia e as Estrelas",
        "logline": "Uma raposinha com medo do escuro descobre a luz das estrelas.",
        "summary": "Lia quer ver o céu, mas tem medo do escuro. Com a ajuda da coruja, ela aprende.",
        "tone": "doce",
        "moral": "coragem é agir mesmo com medo",
        "world_rules": ["animais falam", "sem humanos"],
        "characters": [
            {"name": "Lia", "role": "protagonista", "look": "raposinha com cachecol", "personality": "curiosa"},
            {"name": "Ozzy", "role": "mentor", "look": "coruja de óculos", "personality": "calma"},
        ],
        "beats": [
            {"label": "Apresentação", "summary": "Lia olha a janela", "pages": "1-2"},
            {"label": "Clímax", "summary": "Lia sai no escuro", "pages": "3-4"},
        ],
    }
)


@pytest.fixture(autouse=True)
def _isolated_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store, "WEB_ROOT", tmp_path / "web")


def _new_session(preset_id: str = "kids_storybook", pages: int = 4) -> str:
    res = client.post(
        "/api/sessions",
        json={"pages": pages, "provider": "fake", "model": "fake-1", "preset_id": preset_id},
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


def test_new_session_starts_on_briefing_step() -> None:
    sid = _new_session()
    assert client.get(f"/api/sessions/{sid}").json()["current_step"] == 0


def test_brief_roundtrip_and_category_questions() -> None:
    sid = _new_session()
    view = client.get(f"/api/sessions/{sid}/brief").json()
    assert view["category"] == "kids"
    assert view["structure"] == "picture_book"
    assert any(q["key"] == "reading_level" for q in view["questions"])

    res = client.put(
        f"/api/sessions/{sid}/brief",
        json={"age_band": "3-5", "logline": "Lia tem medo do escuro", "extras": {"reading_level": "Frases curtas"}},
    )
    assert res.status_code == 200, res.text
    saved = client.get(f"/api/sessions/{sid}/brief").json()["brief"]
    assert saved["logline"] == "Lia tem medo do escuro"
    assert saved["extras"] == {"reading_level": "Frases curtas"}
    assert res.json()["suggested_presets"][0]["id"] == "kids_storybook"


def test_brief_rejects_invalid_enum() -> None:
    sid = _new_session()
    res = client.put(f"/api/sessions/{sid}/brief", json={"age_band": "99"})
    assert res.status_code == 422


def test_premises_and_bible_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    sid = _new_session()
    prompts: list[str] = []

    def fake_llm(_instructions: str, user_prompt: str, **_kw: object) -> str:
        prompts.append(user_prompt)
        if "propose" in user_prompt:
            return json.dumps(
                {"premises": [{"title": "A", "logline": "um"}, {"title": "B", "logline": "dois"}, {"logline": ""}]}
            )
        return BIBLE_JSON

    monkeypatch.setattr(brief_mod, "_call_text_llm", fake_llm)
    brief = {"age_band": "3-5", "logline": "Lia tem medo do escuro", "avoid": "vilões assustadores"}

    premises = client.post(f"/api/sessions/{sid}/brief/premises", json={"brief": brief}).json()["premises"]
    assert [p["title"] for p in premises] == ["A", "B"]
    assert "vilões assustadores" in prompts[0]
    assert "Picture-book arc" in prompts[0]

    view = client.post(
        f"/api/sessions/{sid}/bible/generate", json={"premise": premises[0], "brief": brief}
    ).json()
    bible = view["bible"]
    assert bible["title"] == "Lia e as Estrelas"
    assert bible["structure"] == "picture_book"
    assert bible["locked"] is False
    # kids_storybook has cast=1: the bible keeps only the protagonist.
    assert [c["name"] for c in bible["characters"]] == ["Lia"]

    locked = client.put(f"/api/sessions/{sid}/bible", json={**bible, "locked": True}).json()
    assert locked["bible"]["locked"] is True
    assert locked["bible_stale"] is False
    session = client.get(f"/api/sessions/{sid}").json()
    assert session["story_premise"].startswith("Uma raposinha")
    assert session["story_mode"] == "narrative"

    # A locked bible cannot be replaced, and changing the brief marks it stale.
    assert client.post(f"/api/sessions/{sid}/bible/generate", json={}).status_code == 409
    stale = client.put(f"/api/sessions/{sid}/brief", json={**brief, "tone": "épico"}).json()
    assert stale["bible_stale"] is True


def test_bible_characters_become_book_characters() -> None:
    sid = _new_session(preset_id="manga_lineart")
    bible = StoryBible(
        summary="s",
        characters=[
            BibleCharacter(name="Ken", look="spiky hair, red scarf", personality="brave"),
            BibleCharacter(name="Ken", look="duplicate"),
        ],
    )
    client.put(f"/api/sessions/{sid}/bible", json=json.loads(bible.model_dump_json()))
    res = client.post(f"/api/sessions/{sid}/bible/characters").json()
    assert res["created"] == 1
    chars = client.get(f"/api/sessions/{sid}").json()["characters"]
    assert [c["name"] for c in chars] == ["Ken"]
    assert "red scarf" in chars[0]["description"]
    assert client.post(f"/api/sessions/{sid}/bible/characters").json()["created"] == 0


def test_story_generation_uses_bible_and_brief_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    sid = _new_session(pages=1)
    client.put(f"/api/sessions/{sid}/brief", json={"logline": "Lia tem medo do escuro", "avoid": "sustos"})
    bible = StoryBible(
        summary="Lia aprende a gostar da noite.",
        beats=[StoryBeat(label="Início", summary="Lia na janela", pages="1")],
        locked=True,
    )
    client.put(f"/api/sessions/{sid}/bible", json=json.loads(bible.model_dump_json()))

    seen: list[str] = []

    def fake_llm(_instructions: str, user_prompt: str, **_kw: object) -> str:
        seen.append(user_prompt)
        return json.dumps(
            {"title": "T", "synopsis": "S", "pages": [{"index": 1, "title": "p", "scene_prompt": "Lia at the window"}]}
        )

    monkeypatch.setattr(story_mod, "_call_text_llm", fake_llm)
    # Empty premise: the server falls back to the bible/brief idea.
    res = client.post(f"/api/sessions/{sid}/story/generate", json={"premise": "", "pages": 1})
    assert res.status_code == 200, res.text
    assert "STORY BIBLE (binding)" in seen[0]
    assert "Lia na janela" in seen[0]
    assert "MUST AVOID: sustos" in seen[0]


def test_story_context_is_empty_without_brief() -> None:
    assert story_context(StoryBrief(), None) == ""
    assert StoryBrief().is_empty()


def test_suggest_presets_by_audience() -> None:
    assert suggest_preset_ids(StoryBrief(age_band="0-3"))[0] == "kids_toddler"
    assert suggest_preset_ids(StoryBrief(extras={"demographic": "Shōjo"}))[0] == "manga_shoujo"
    assert suggest_preset_ids(StoryBrief(purpose="relax"))[0] == "bold_easy_cozy"
    assert suggest_preset_ids(StoryBrief()) == []


def test_suggest_field_rejects_unknown_field() -> None:
    sid = _new_session()
    res = client.post(f"/api/sessions/{sid}/brief/suggest", json={"field": "age_band"})
    assert res.status_code == 400
