"""A book keeps one identity: its own style anchor, a fixed cast and detailed model sheets."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from color_book_generator import story as story_mod
from color_book_generator.brief import BibleCharacter, StoryBible
from color_book_generator.config import default_book_config_path, load_book_config
from color_book_generator.identity import (
    CAST_RULES,
    CharacterDesign,
    build_character_sheet_prompt,
    match_cast_ids,
    render_design,
    style_anchor_prompt,
)
from color_book_generator.page_layout import PageLayout
from color_book_generator.providers.base import ImageProvider, register
from color_book_generator.web import book_pipeline, store
from color_book_generator.web.app import app
from color_book_generator.web.routers import studio as studio_mod
from color_book_generator.web.store import BookCharacter, PageState

client = TestClient(app)
CALLS: list[dict[str, Any]] = []


@register("identity_fake")
class _RecordingProvider(ImageProvider):
    name = "identity_fake"
    default_model = "fake-1"

    def generate(  # type: ignore[no-untyped-def,override]
        self, prompt, *, references=None, style_reference=None, target_size=(600, 780), **_kw
    ) -> Image.Image:
        CALLS.append({"prompt": prompt, "references": references, "style": style_reference})
        width = 600
        img = Image.new("RGB", (width, int(width * target_size[1] / target_size[0])), "white")
        ImageDraw.Draw(img).ellipse((140, 100, 460, 380), outline="black", width=10)
        return img


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store, "WEB_ROOT", tmp_path / "web")
    CALLS.clear()
    monkeypatch.setattr(
        studio_mod,
        "design_character",
        lambda **kw: CharacterDesign(
            species_age=f"young {kw['name'].lower()} fox",
            outfit="collared shirt with three round buttons",
            signature_prop="question-mark backpack",
            always=["three buttons"],
            never=["never draw shoes"],
        ),
    )
    monkeypatch.setattr(
        studio_mod,
        "generate_book_finishing",
        lambda **_kw: story_mod.BookFinishing(
            title="Final",
            synopsis="Synopsis.",
            cover_prompt="Jack on the path",
            front=story_mod.ExtraPageCopy(placement="front", text="Welcome", illustration="Jack"),
            back=story_mod.ExtraPageCopy(placement="back", text="Thanks"),
        ),
    )
    monkeypatch.setattr(
        studio_mod,
        "plan_page_layout",
        lambda **_kw: PageLayout(
            text_align="center", text_vertical="center", show_border=False,
            image_position="none", image_size="small", image_prompt="",
        ),
    )


def _new_book(pages: int = 2, preset: str = "kids_storybook") -> str:
    res = client.post(
        "/api/sessions",
        json={"pages": pages, "provider": "identity_fake", "model": "fake-1", "preset_id": preset},
    )
    assert res.status_code == 201, res.text
    return str(res.json()["id"])


def _run_autopilot(sid: str, **payload: object) -> dict[str, Any]:
    res = client.post(f"/api/sessions/{sid}/autopilot", json={"stop_after": "pdf", **payload})
    assert res.status_code == 202, res.text
    status: dict[str, Any] = {}
    for _ in range(300):
        status = client.get(f"/api/sessions/{sid}/autopilot").json()
        if status["status"] != "running":
            break
        time.sleep(0.03)
    return status


# --------------------------------------------------------------------------- units


def test_match_cast_ids_tolerates_decorated_names_and_scene_mentions() -> None:
    cast = [("a1", "Jack"), ("b2", "Mia"), ("c3", "Toby")]
    assert match_cast_ids(["Jack the fox"], "", cast) == ["a1"]
    assert match_cast_ids([], "Mia hands Jack a map", cast) == ["a1", "b2"]
    assert match_cast_ids(["mia"], "", cast) == ["b2"]
    # Substrings inside other words do not count.
    assert match_cast_ids([], "a jackal and a tobyish hat", cast) == []


def test_design_renders_into_a_detailed_prompt_line() -> None:
    design = CharacterDesign(
        species_age="young fox", eyes="large round eyes", always=["three buttons"], never=["no shoes"]
    )
    text = render_design("Jack", design)
    assert text.startswith("Jack — Species/age: young fox")
    assert "ALWAYS: three buttons" in text and "NEVER: no shoes" in text
    assert render_design("Jack", None, "a fox") == "Jack: a fox"


def test_sheet_prompt_is_a_full_turnaround_from_the_design() -> None:
    prompt = build_character_sheet_prompt(
        name="Jack", design_text="Jack — Outfit: shirt.", style_context="thick outlines"
    )
    for view in ("front view", "three-quarter view", "side profile", "back view"):
        assert view in prompt
    assert "happy, sad, surprised, determined" in prompt
    assert "Jack — Outfit: shirt." in prompt
    assert "No text" in prompt


def test_style_anchor_prompt_has_no_characters() -> None:
    prompt = style_anchor_prompt(style_context="bold lines", world="a sunny meadow")
    assert "a sunny meadow" in prompt
    assert "Draw NO characters" in prompt


def test_page_job_uses_only_this_books_cast_designs_and_files() -> None:
    sid = _new_book(pages=1)
    state = store.load_session(sid)
    assert state is not None
    jack = BookCharacter(
        id="aaaa0001", name="Jack", description="fox",
        design=CharacterDesign(outfit="shirt with three buttons"),
    )
    jack.design_source = book_pipeline.design_source("Jack", "fox")
    mia = BookCharacter(id="bbbb0002", name="Mia", description="mouse")
    state.characters = [jack, mia]
    store.session_characters_dir(sid).mkdir(parents=True, exist_ok=True)
    (store.session_characters_dir(sid) / "aaaa0001.png").write_bytes(b"jack-sheet")
    (store.session_characters_dir(sid) / "bbbb0002.png").write_bytes(b"mia-sheet")
    state.story_bible = StoryBible(locked=True, world_rules=["The path always glows"])
    page = PageState(index=1, prompt="Jack waves", character_ids=["aaaa0001", "ffff9999"])
    cfg = load_book_config(default_book_config_path())

    job = book_pipeline.page_job(state, page, cfg)

    assert job.references == [("Jack", b"jack-sheet")]  # unknown id dropped, Mia not in scene
    assert "shirt with three buttons" in job.prompt
    assert "Mia" not in job.prompt
    assert "The path always glows" in job.prompt
    assert job.style_reference is None  # no anchor yet and never another book's art


def test_scripts_forbid_new_named_characters_and_re_descriptions() -> None:
    captured: list[str] = []

    def _llm(_instructions: str, user_prompt: str, **_kw: object) -> str:
        captured.append(user_prompt)
        return json.dumps(
            {
                "title": "T", "premise": "P", "synopsis": "S", "style_anchor_subject": "x",
                "characters": [{"name": "Invented", "description": "new"}],
                "pages": [{"index": 1, "scene_prompt": "Jack runs", "character_names": []}],
            }
        )

    original = story_mod._call_text_llm
    story_mod._call_text_llm = _llm  # type: ignore[assignment]
    try:
        blueprint = story_mod.generate_full_book_blueprint(
            pages=1,
            fixed_cast=[story_mod.CharacterBrief(id="aaaa0001", name="Jack", description="fox")],
        )
    finally:
        story_mod._call_text_llm = original  # type: ignore[assignment]
    assert [c.name for c in blueprint.characters] == ["Jack"]  # the fixed cast wins
    assert CAST_RULES in captured[0]
    assert "The cast is FIXED" in captured[0]


# --------------------------------------------------------------------- pipeline


def _blueprint(**kwargs: object) -> story_mod.FullBookBlueprint:
    fixed = kwargs.get("fixed_cast") or []
    names = [c.name for c in fixed] or ["Jack"]  # type: ignore[attr-defined]
    return story_mod.FullBookBlueprint(
        title="Jack and the Bright Path",
        premise="p",
        synopsis="s",
        style_anchor_subject="a fox on a path",
        characters=[story_mod.GeneratedCharacter(name=n, description="d") for n in names],
        pages=[
            story_mod.ScriptedPage(index=1, scene_prompt="Jack opens his backpack", character_names=["Jack"]),
            story_mod.ScriptedPage(index=2, scene_prompt="Mia counts acorns while Jack watches"),
        ],
    )


def test_autopilot_keeps_the_locked_bible_cast_and_its_own_anchor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def _spy_blueprint(**kwargs: object) -> story_mod.FullBookBlueprint:
        seen.update(kwargs)
        return _blueprint(**kwargs)

    monkeypatch.setattr(studio_mod, "generate_full_book_blueprint", _spy_blueprint)
    sid = _new_book(pages=2)
    state = store.load_session(sid)
    assert state is not None
    state.story_bible = StoryBible(
        locked=True,
        summary="A fox learns",
        world_rules=["Signs on the path glow"],
        characters=[
            BibleCharacter(name="Jack", look="young fox, question-mark backpack"),
            BibleCharacter(name="Mia", look="tiny mouse with a red bow"),
        ],
    )
    store.save_session(state)

    status = _run_autopilot(sid, pages=2)
    assert status["status"] == "done", status

    fixed = seen["fixed_cast"]
    assert [c.name for c in fixed] == ["Jack", "Mia"]
    book = store.load_session(sid)
    assert book is not None
    ids = {c.name: c.id for c in book.characters}
    assert set(ids) == {"Jack", "Mia"}
    assert [c.id for c in fixed] == [ids["Jack"], ids["Mia"]]  # same ids, no new cast
    assert book.pages[0].character_ids == [ids["Jack"]]
    assert book.pages[1].character_ids == [ids["Jack"], ids["Mia"]]  # found in the scene text
    assert all(c.design is not None and c.sheet_source for c in book.characters)

    # Order of image jobs: own anchor (no refs, no characters), then two sheets, then pages.
    anchor_call, sheet_jack, sheet_mia, page1, page2 = CALLS[:5]
    # Only the anchor sees the category sample page (format guide), never the pages/sheets.
    sample = store.category_reference_bytes("kids")
    assert sample is not None and anchor_call["style"] == sample
    assert anchor_call["references"] is None
    assert "Draw NO characters" in anchor_call["prompt"]
    assert "copy ONLY its page format" in anchor_call["prompt"]
    anchor = store.session_anchor_path(sid).read_bytes()
    assert all(call["style"] != sample for call in CALLS[1:])
    assert "OFFICIAL CHARACTER MODEL SHEET for Jack" in sheet_jack["prompt"]
    assert "question-mark backpack" in sheet_jack["prompt"]
    assert sheet_jack["style"] == anchor and sheet_mia["style"] == anchor
    assert [name for name, _ in page1["references"]] == ["Jack"]
    assert [name for name, _ in page2["references"]] == ["Jack", "Mia"]
    assert page1["style"] == anchor
    assert "Signs on the path glow" in page2["prompt"]
    assert "CAST IN THIS SCENE" in page2["prompt"]

    # A fresh run on the same book keeps the cast and does not redraw current sheets.
    CALLS.clear()
    again = _run_autopilot(sid, pages=2)
    assert again["status"] == "done", again
    assert not any("OFFICIAL CHARACTER MODEL SHEET" in c["prompt"] for c in CALLS)
    rerun = store.load_session(sid)
    assert rerun is not None
    assert {c.name: c.id for c in rerun.characters} == ids


def test_editing_a_description_redraws_only_that_sheet(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(studio_mod, "generate_full_book_blueprint", _blueprint)
    sid = _new_book(pages=2)
    assert _run_autopilot(sid, pages=2, stop_after="sheets")["status"] == "done"
    book = store.load_session(sid)
    assert book is not None
    cid = book.characters[0].id
    client.patch(f"/api/sessions/{sid}/characters/{cid}", json={"description": "now with a hat"})

    CALLS.clear()
    assert _run_autopilot(sid, pages=2, stop_after="sheets", resume=True)["status"] == "done"
    sheets = [c for c in CALLS if "OFFICIAL CHARACTER MODEL SHEET" in c["prompt"]]
    assert len(sheets) == 1


def test_kids_category_uses_the_storybook_sample_pages() -> None:
    names = [path.name for path in store.category_reference_paths("kids")]
    assert names == [
        "01-lost-in-forest.png", "02-new-friends.png", "03-secret-dream.png", "04-born-to-fly.png"
    ]
    assert store.category_reference_bytes("kids") == store.category_reference_paths("kids")[0].read_bytes()


def test_category_references_are_isolated() -> None:
    for category in ("cozy", "kids", "manga", "manhwa", "kawaii", "adult"):
        paths = store.category_reference_paths(category)
        assert paths, category
        assert all(path.parent.name == category for path in paths)
    # No borrowing from another category, and no path tricks.
    assert store.category_reference_paths("custom_style") == []
    assert store.category_reference_paths("../kids") == []
