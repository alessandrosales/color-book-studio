"""Editorial standards: beat plans, caption caps by audience and the reading check."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from color_book_generator import story as story_mod
from color_book_generator.brief import StoryBeat, StoryBible, StoryBrief
from color_book_generator.config import CaptionConfig
from color_book_generator.processing.caption import caption_font_px
from color_book_generator.standards import (
    BEAT_TEMPLATES,
    beat_plan,
    bible_beat_plan,
    caption_word_cap,
    count_words,
    nominal_caption_pt,
    page_beats,
    ratio_for_pt,
    reading_standard,
    resolve_age_band,
)
from color_book_generator.web import store
from color_book_generator.web.app import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _isolated_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store, "WEB_ROOT", tmp_path / "web")


@pytest.mark.parametrize("structure", list(BEAT_TEMPLATES))
@pytest.mark.parametrize("pages", [1, 3, 4, 12, 24, 32])
def test_beat_plan_covers_every_page_in_order(structure: str, pages: int) -> None:
    plan = beat_plan(structure, pages)  # type: ignore[arg-type]
    labels = [label for label, _ in BEAT_TEMPLATES[structure]]  # type: ignore[index]
    assert len(plan) == pages
    order = [labels.index(p) for p in plan]
    assert order == sorted(order)
    if pages >= len(labels):
        assert set(plan) == set(labels)
        assert plan[0] == labels[0] and plan[-1] == labels[-1]


def test_picture_book_climax_falls_late_in_a_24_page_book() -> None:
    plan = beat_plan("picture_book", 24)
    climax = [i for i, p in enumerate(plan, start=1) if p.startswith("Clímax")]
    assert climax and 17 <= climax[0] <= 21


def test_progressive_collection_tiers() -> None:
    plan = beat_plan("collection", 9, progressive=True)
    assert plan[0].endswith("simples") and plan[-1].endswith("intricado")


def test_bible_ranges_drive_the_plan_and_fill_gaps() -> None:
    bible = StoryBible(
        locked=True,
        beats=[
            StoryBeat(label="Início", summary="Lia na janela", pages="1-2"),
            StoryBeat(label="Fim", summary="Lia dorme", pages="4"),
        ],
    )
    plan = bible_beat_plan(bible, 4)
    assert plan == ["Início: Lia na janela"] * 3 + ["Fim: Lia dorme"]
    # An unlocked bible is ignored in favour of the category template.
    draft = bible.model_copy(update={"locked": False})
    assert page_beats(structure="picture_book", pages=4, bible=draft, brief=None) == beat_plan(
        "picture_book", 4
    )


def test_bible_with_unparseable_ranges_falls_back() -> None:
    bible = StoryBible(locked=True, beats=[StoryBeat(label="x", summary="y", pages="começo")])
    assert bible_beat_plan(bible, 10) is None


def test_age_resolution_and_caps() -> None:
    assert resolve_age_band(StoryBrief(age_band="6-9"), "kids") == "6-9"
    assert resolve_age_band(None, "kids", "kids_toddler") == "0-3"
    assert resolve_age_band(StoryBrief(), "manga") == "teen"
    toddler = reading_standard(None, "kids", "kids_toddler")
    assert caption_word_cap(toddler, "single_caption") == 6
    # The manga balloon cap (8) wins over the looser teen cap.
    assert caption_word_cap(reading_standard(None, "manga"), "manga_action") == 8
    assert count_words("LET'S GO! | WAIT!") == 3


def test_font_conversion_round_trip() -> None:
    ratio = ratio_for_pt(24, 8.5)
    assert abs(nominal_caption_pt(ratio, 8.5) - 24) < 0.1
    assert ratio_for_pt(400, 6) == 0.08


def test_long_caption_shrinks_below_nominal_size() -> None:
    cfg = CaptionConfig(enabled=True, font_size_ratio=0.04)
    short = caption_font_px("Oi!", cfg, (2550, 3300))
    long = caption_font_px(" ".join(["palavra"] * 120), cfg, (2550, 3300))
    assert short == round(2550 * 0.04)
    assert long < short


def _session(preset: str = "kids_storybook", pages: int = 2) -> str:
    res = client.post(
        "/api/sessions",
        json={"pages": pages, "provider": "fake", "model": "fake-1", "preset_id": preset},
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


def test_story_generation_sends_beats_and_caption_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    sid = _session(pages=2)
    client.put(f"/api/sessions/{sid}/brief", json={"age_band": "0-3", "logline": "Bebê urso"})
    seen: list[str] = []
    long_caption = " ".join(["muito"] * 10)

    def fake_llm(_i: str, user_prompt: str, **_k: object) -> str:
        seen.append(user_prompt)
        return json.dumps(
            {
                "title": "T",
                "synopsis": "S",
                "pages": [
                    {"index": 1, "scene_prompt": "bear", "caption_text": "Oi urso"},
                    {"index": 2, "scene_prompt": "bear sleeps", "caption_text": long_caption},
                ],
            }
        )

    monkeypatch.setattr(story_mod, "_call_text_llm", fake_llm)
    res = client.post(f"/api/sessions/{sid}/story/generate", json={"premise": "", "pages": 2})
    assert res.status_code == 200, res.text
    assert "Beat plan (MANDATORY)" in seen[0]
    assert "AT MOST 6 words" in seen[0]
    assert any("Página 2" in w and "10 palavras" in w for w in res.json()["warnings"])

    pages = client.get(f"/api/sessions/{sid}").json()["pages"]
    assert pages[0]["beat"] and pages[-1]["beat"]
    assert pages[0]["beat"] != pages[-1]["beat"]


def test_reading_report_and_apply_font() -> None:
    sid = _session(preset="kids_storybook")
    client.patch(
        f"/api/sessions/{sid}", json={"page_size": "6x9", "caption_enabled": True, "caption_font_size": 0.032}
    )
    client.put(f"/api/sessions/{sid}/brief", json={"age_band": "3-5", "logline": "x"})
    report = client.get(f"/api/sessions/{sid}/reading").json()
    assert report["age_band"] == "3-5" and report["age_assumed"] is False
    assert report["nominal_pt"] < report["font_min_pt"]
    assert report["font_ok"] is False

    fixed = client.post(f"/api/sessions/{sid}/reading/apply-font").json()
    assert fixed["font_ok"] is True
    assert abs(fixed["nominal_pt"] - fixed["font_target_pt"]) < 0.2
    preflight = client.get(f"/api/sessions/{sid}/kdp-preflight").json()
    assert not any("fonte das legendas" in w for w in preflight["warnings"])


def test_story_generation_raises_caption_font_to_the_audience_minimum(monkeypatch: pytest.MonkeyPatch) -> None:
    sid = _session(pages=1)
    client.patch(f"/api/sessions/{sid}", json={"page_size": "6x9", "caption_enabled": True, "caption_font_size": 0.032})
    client.put(f"/api/sessions/{sid}/brief", json={"age_band": "3-5", "logline": "Ursinho"})
    fake = json.dumps({"title": "T", "synopsis": "S", "pages": [{"index": 1, "scene_prompt": "bear", "caption_text": "Oi"}]})
    monkeypatch.setattr(story_mod, "_call_text_llm", lambda *_a, **_k: fake)
    assert client.post(f"/api/sessions/{sid}/story/generate", json={"premise": "", "pages": 1}).status_code == 200
    report = client.get(f"/api/sessions/{sid}/reading").json()
    assert report["font_ok"] is True
    assert report["nominal_pt"] == pytest.approx(report["font_target_pt"], abs=0.2)
