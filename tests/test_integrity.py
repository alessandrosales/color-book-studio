"""Data integrity: concurrent session writes, atomic files, recovery, archive hygiene."""

from __future__ import annotations

import json
import os
import threading
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from color_book_generator import fsutil
from color_book_generator.providers.base import ImageProvider, register
from color_book_generator.web import archive, store
from color_book_generator.web.app import app


@pytest.fixture(autouse=True)
def _isolated_web_root(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "WEB_ROOT", tmp_path / "web")


def _new_session(pages: int = 3) -> store.SessionState:
    return store.create_session(
        pages=[store.PageState(index=i, prompt=f"p{i}") for i in range(1, pages + 1)],
        context_prompt="ctx",
        title="Livro",
        provider="fake",
        model="fake-1",
    )


def _line_art() -> Image.Image:
    img = Image.new("RGB", (600, 780), "white")
    draw = ImageDraw.Draw(img)
    draw.ellipse((140, 160, 460, 480), outline="black", width=10)
    return img


# --------------------------------------------------------------------------
# Concurrent updates
# --------------------------------------------------------------------------


def test_stale_save_keeps_changes_made_meanwhile() -> None:
    sid = _new_session().id
    slow = store.load_session(sid)  # e.g. a page generation that takes minutes
    assert slow is not None
    slow.pages[0].status = "generating"
    store.save_session(slow)

    quick = store.load_session(sid)  # user edits another page meanwhile
    assert quick is not None
    quick.pages[1].prompt = "edited while generating"
    quick.title = "Novo titulo"
    store.save_session(quick)

    slow.pages[0].status = "done"
    store.save_session(slow)

    final = store.load_session(sid)
    assert final is not None
    assert final.pages[0].status == "done"
    assert final.pages[1].prompt == "edited while generating"
    assert final.title == "Novo titulo"


def test_same_field_last_writer_wins_and_other_fields_merge() -> None:
    sid = _new_session().id
    a = store.load_session(sid)
    b = store.load_session(sid)
    assert a is not None and b is not None
    a.pages[0].prompt = "from a"
    a.author = "Autora"
    b.pages[0].prompt = "from b"
    store.save_session(a)
    store.save_session(b)
    final = store.load_session(sid)
    assert final is not None
    assert final.pages[0].prompt == "from b"
    assert final.author == "Autora"


def test_list_additions_and_deletions_merge_by_id() -> None:
    state = _new_session()
    keep = store.ExtraPage(id=store.new_extra_page_id(), title="keep")
    drop = store.ExtraPage(id=store.new_extra_page_id(), title="drop")
    state.front_extra_pages = [keep, drop]
    store.save_session(state)

    a = store.load_session(state.id)
    b = store.load_session(state.id)
    assert a is not None and b is not None
    a.front_extra_pages = [p for p in a.front_extra_pages if p.id != drop.id]
    added = store.ExtraPage(id=store.new_extra_page_id(), title="added")
    b.front_extra_pages.append(added)
    b.front_extra_pages[0].text = "texto novo"
    store.save_session(a)
    store.save_session(b)

    final = store.load_session(state.id)
    assert final is not None
    assert [p.id for p in final.front_extra_pages] == [keep.id, added.id]
    assert final.front_extra_pages[0].text == "texto novo"


def test_threads_updating_different_pages_lose_nothing() -> None:
    sid = _new_session(pages=12).id
    barrier = threading.Barrier(12)
    errors: list[BaseException] = []

    def worker(index: int) -> None:
        try:
            state = store.load_session(sid)
            assert state is not None
            barrier.wait()  # everyone holds a stale copy before anyone saves
            state.pages[index - 1].caption_text = f"caption {index}"
            store.save_session(state)
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(1, 13)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    final = store.load_session(sid)
    assert final is not None
    assert [p.caption_text for p in final.pages] == [f"caption {i}" for i in range(1, 13)]


def test_update_session_applies_mutation_on_latest_state() -> None:
    sid = _new_session().id
    other = store.load_session(sid)
    assert other is not None
    other.author = "Fulano"
    store.save_session(other)

    store.update_session(sid, lambda s: setattr(s, "title", "Atualizado"))
    final = store.load_session(sid)
    assert final is not None
    assert (final.title, final.author) == ("Atualizado", "Fulano")


@register("edits-during-generation")
class EditsDuringGeneration(ImageProvider):
    """Simulates the user editing page 2 while page 1 is being generated."""

    name = "edits-during-generation"
    default_model = "m"
    sid: str = ""

    def generate(self, prompt: str, **_: object) -> Image.Image:
        state = store.load_session(EditsDuringGeneration.sid)
        assert state is not None
        state.pages[1].prompt = "edited during generation"
        store.save_session(state)
        return _line_art()


def test_generate_endpoint_does_not_overwrite_concurrent_edit() -> None:
    client = TestClient(app)
    session = client.post(
        "/api/sessions", json={"pages": 2, "provider": "edits-during-generation"}
    ).json()
    EditsDuringGeneration.sid = session["id"]
    res = client.post(f"/api/sessions/{session['id']}/pages/1/generate", json={})
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "done"

    final = store.load_session(session["id"])
    assert final is not None
    assert final.pages[0].status == "done"
    assert final.pages[1].prompt == "edited during generation"


# --------------------------------------------------------------------------
# Atomic writes and corruption recovery
# --------------------------------------------------------------------------


def test_atomic_write_failure_keeps_previous_file(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "data.json"
    fsutil.atomic_write_text(target, '{"v": 1}')

    def boom(*_args: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(fsutil.os, "replace", boom)
    with pytest.raises(OSError):
        fsutil.atomic_write_text(target, '{"v": 2}')
    assert json.loads(target.read_text()) == {"v": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["data.json"]


def test_save_session_failure_keeps_previous_state(monkeypatch) -> None:
    state = _new_session()
    state.title = "nao deve aparecer"
    with monkeypatch.context() as mp:
        mp.setattr(fsutil.os, "replace", lambda *_a: (_ for _ in ()).throw(OSError("x")))
        with pytest.raises(OSError):
            store.save_session(state)
    loaded = store.load_session(state.id)
    assert loaded is not None
    assert loaded.title == "Livro"


def test_corrupted_session_falls_back_to_backup() -> None:
    state = _new_session()
    state.title = "Segunda versao"
    store.save_session(state)  # writes session.json.bak with the first version
    (store.session_dir(state.id) / "session.json").write_text("{trunc", encoding="utf-8")

    loaded = store.load_session(state.id)
    assert loaded is not None
    assert loaded.id == state.id
    assert loaded.title == "Livro"


def test_corrupted_session_without_backup_raises() -> None:
    state = _new_session()
    (store.session_dir(state.id) / "session.json").write_text("{trunc", encoding="utf-8")
    with pytest.raises(ValueError):
        store.load_session(state.id)


# --------------------------------------------------------------------------
# Restart recovery
# --------------------------------------------------------------------------


def test_recover_interrupted_sessions_marks_generating_as_error() -> None:
    state = _new_session()
    state.pages[0].status = "generating"
    state.pages[1].status = "done"
    state.cover_status = "generating"
    extra = store.ExtraPage(id=store.new_extra_page_id(), status="generating")
    state.back_extra_pages = [extra]
    state.characters = [
        store.BookCharacter(id=store.new_character_id(), name="Ana", sheet_status="generating")
    ]
    store.save_session(state)
    untouched = _new_session()

    assert store.recover_interrupted_sessions() == [state.id]

    final = store.load_session(state.id)
    assert final is not None
    assert final.pages[0].status == "error"
    assert final.pages[0].error == store.INTERRUPTED_MESSAGE
    assert final.pages[1].status == "done"
    assert final.cover_status == "error"
    assert final.back_extra_pages[0].status == "error"
    assert final.characters[0].sheet_status == "error"
    assert store.recover_interrupted_sessions() == []
    assert store.load_session(untouched.id) is not None


def test_app_startup_runs_recovery() -> None:
    state = _new_session()
    state.pages[0].status = "generating"
    store.save_session(state)
    with TestClient(app):
        pass
    final = store.load_session(state.id)
    assert final is not None
    assert final.pages[0].status == "error"


# --------------------------------------------------------------------------
# Page list and archive hygiene
# --------------------------------------------------------------------------


def test_session_page_files_ignores_stray_pages() -> None:
    state = _new_session(pages=2)
    for page in state.pages:
        page.status = "done"
    store.pages_dir(state.id).mkdir(parents=True, exist_ok=True)
    for index in (2, 1, 9):
        Image.new("L", (10, 10), 255).save(store.page_file(state.id, index))
    assert [p.name for p in store.session_page_files(state)] == [
        "page-001.png",
        "page-002.png",
    ]


def test_pending_page_does_not_reuse_old_image() -> None:
    state = _new_session(pages=1)
    Image.new("L", (10, 10), 255).save(store.page_file(state.id, 1))
    assert store.session_page_files(state) == []


def test_deleted_session_cannot_be_restored_by_stale_save() -> None:
    state = _new_session()
    from color_book_generator.web.app import delete_session

    delete_session(state.id)
    with pytest.raises(FileNotFoundError):
        store.save_session(state)
    assert store.load_session(state.id) is None


def test_library_skips_corrupt_session() -> None:
    state = _new_session()
    bad = store.WEB_ROOT / "12345678"
    bad.mkdir()
    (bad / "session.json").write_text("{", encoding="utf-8")
    from color_book_generator.web.app import list_sessions

    assert [item.id for item in list_sessions()] == [state.id]


def test_archive_export_skips_locks_temp_and_backup_files(tmp_path: Path) -> None:
    state = _new_session()
    store.save_session(state)  # creates .locks/ and session.json.bak
    (store.session_dir(state.id) / ".session.json.abc.tmp").write_text("x")
    assert (store.session_dir(state.id) / "session.json.bak").exists()

    for out in (
        archive.export_archive(tmp_path / "all.zip"),
        archive.export_selected_archive(tmp_path / "one.zip", [state.id]),
    ):
        names = zipfile.ZipFile(out).namelist()
        assert f"data/{state.id}/session.json" in names
        assert not any(".lock" in n or n.endswith((".bak", ".tmp")) for n in names), names


def test_lock_file_is_not_a_session_dir() -> None:
    _new_session()
    store.save_session(_new_session())
    assert store.locks_dir().is_dir()
    assert os.path.basename(store.locks_dir()).startswith(".")
