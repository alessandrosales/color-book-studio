"""Export and import the whole web workspace as a portable archive."""

from __future__ import annotations

import json
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

import yaml
from PIL import Image
from pydantic import BaseModel

from color_book_generator import __version__
from color_book_generator.config import BookConfig, default_book_config_path
from color_book_generator.web import store

ARCHIVE_VERSION = 1
MANIFEST_NAME = "manifest.json"
MAX_MANIFEST_BYTES = 1 * 1024 * 1024
MAX_FILE_BYTES = 120 * 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
COPY_CHUNK_BYTES = 64 * 1024
SKIP_FAKE_SESSIONS = True


class ArchiveError(RuntimeError):
    """Raised when an archive is invalid or unsafe."""


def _ensure_within(path: Path, root: Path, label: str) -> Path:
    resolved_root = root.resolve()
    resolved = path.resolve()
    if not resolved.is_relative_to(resolved_root):
        raise ArchiveError(f"caminho fora do destino: {label}")
    return resolved


def book_config_path() -> Path:
    return default_book_config_path()


class ImportResult(BaseModel):
    sessions: int
    pages: int
    extra_pages: int = 0
    covers: int = 0
    characters: int
    style: bool
    latest_session: str | None = None
    session_ids: list[str] = []
    skipped: int = 0


def _is_internal_file(rel: Path) -> bool:
    """Runtime-only files: locks, atomic-write temporaries and local backups."""
    return any(part.startswith(".") for part in rel.parts) or rel.name.endswith(".bak")


def export_archive(destination: Path) -> Path:
    root = store.WEB_ROOT
    files = 0
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        if root.exists():
            for path in sorted(root.rglob("*")):
                if not path.is_file():
                    continue
                rel = path.relative_to(root)
                if rel.parts and rel.parts[0] == "exports":
                    continue
                if _is_internal_file(rel):
                    continue
                zf.write(path, f"data/{rel.as_posix()}")
                files += 1
        config = book_config_path()
        if config.exists():
            zf.write(config, "data/project/book.yaml")
            files += 1
        manifest = {
            "archive_version": ARCHIVE_VERSION,
            "app_version": __version__,
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "files": files,
        }
        zf.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2))
    return destination


def export_to_temp() -> Path:
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        destination = Path(tmp.name)
    return export_archive(destination)


def export_selected_archive(destination: Path, session_ids: list[str]) -> Path:
    """Export only the specified session IDs (plus referenced global characters & book.yaml)."""
    unique_ids = list(dict.fromkeys(sid.strip() for sid in session_ids if sid.strip()))
    if not unique_ids:
        raise ArchiveError("selecione ao menos um livro para exportar")

    root = store.WEB_ROOT
    files = 0
    referenced_global_cids: set[str] = set()
    referenced_preset_ids: set[str] = set()
    needs_global_style = False
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for sid in unique_ids:
            sdir = store.session_dir(sid)
            if not sdir.exists() or not (sdir / "session.json").exists():
                raise ArchiveError(f"livro nao encontrado: {sid}")
            state = store.load_session(sid)
            if state is not None:
                local_cids = {c.id for c in state.characters}
                for page in state.pages:
                    for cid in page.character_ids:
                        if cid not in local_cids:
                            referenced_global_cids.add(cid)
                if state.preset_id:
                    referenced_preset_ids.add(state.preset_id)
            if not (sdir / "style" / "reference.png").exists():
                needs_global_style = True
            for path in sorted(sdir.rglob("*")):
                if not path.is_file():
                    continue
                rel = path.relative_to(root)
                if _is_internal_file(rel):
                    continue
                zf.write(path, f"data/{rel.as_posix()}")
                files += 1

        if referenced_global_cids:
            global_chars = [
                r for r in store.load_characters() if r.id in referenced_global_cids
            ]
            if global_chars:
                zf.writestr(
                    "data/characters/characters.json",
                    json.dumps([r.model_dump(mode="json") for r in global_chars], indent=2),
                )
                files += 1
                for rec in global_chars:
                    cpath = store.characters_dir() / rec.filename
                    if cpath.exists():
                        zf.write(cpath, f"data/characters/{rec.filename}")
                        files += 1

        if needs_global_style:
            global_style = store.style_path()
            if global_style.exists():
                zf.write(global_style, "data/style/reference.png")
                files += 1

        if referenced_preset_ids:
            custom_presets = [
                preset
                for preset in store.load_custom_presets()
                if preset.id in referenced_preset_ids
            ]
            if custom_presets:
                zf.writestr(
                    "data/presets.json",
                    json.dumps(
                        [preset.model_dump(mode="json") for preset in custom_presets],
                        indent=2,
                    ),
                )
                files += 1

        config = book_config_path()
        if config.exists():
            zf.write(config, "data/project/book.yaml")
            files += 1

        manifest = {
            "archive_version": ARCHIVE_VERSION,
            "app_version": __version__,
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "files": files,
            "sessions": unique_ids,
        }
        zf.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2))
    return destination


def export_selected_to_temp(session_ids: list[str]) -> Path:
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        destination = Path(tmp.name)
    return export_selected_archive(destination, session_ids)


def _safe_relative(name: str) -> PurePosixPath | None:
    """Map an archive member to a path relative to the workspace root."""
    raw = name.replace("\\", "/")
    path = PurePosixPath(raw)
    if raw.startswith("/") or not path.parts:
        raise ArchiveError(f"caminho invalido no backup: {name}")
    if ".." in path.parts:
        raise ArchiveError(f"caminho invalido no backup: {name}")
    parts = path.parts
    if parts[0] == "__MACOSX" or ".DS_Store" in parts:
        return None
    if parts[0] == "data":
        rel = parts[1:]
    elif len(parts) >= 2 and parts[0] == "output" and parts[1] == "web":
        rel = parts[2:]
    elif parts[0] in {"settings.json", "presets.json", "characters", "style"} or (len(parts) >= 2 and parts[-1] in {"session.json", "characters.json", "reference.png"}):
        rel = parts
    elif "data" in parts:
        rel = parts[parts.index("data") + 1 :]
    else:
        return None
    if not rel:
        return None
    return PurePosixPath(*rel)


def _extract(archive: BinaryIO, target: Path) -> None:
    try:
        zf = zipfile.ZipFile(archive)
    except zipfile.BadZipFile as exc:
        raise ArchiveError("o arquivo enviado nao e um zip valido") from exc

    with zf:
        names = zf.namelist()
        manifest_name = next(
            (n for n in names if PurePosixPath(n).name == MANIFEST_NAME), None
        )
        if manifest_name is not None:
            try:
                with zf.open(manifest_name) as mf:
                    raw_manifest = mf.read(MAX_MANIFEST_BYTES + 1)
                if len(raw_manifest) > MAX_MANIFEST_BYTES:
                    raise ArchiveError("manifest.json grande demais no backup")
                manifest: dict[str, Any] = json.loads(raw_manifest)
            except (json.JSONDecodeError, ValueError) as exc:
                raise ArchiveError("manifest.json invalido") from exc
            version = manifest.get("archive_version")
            if version is not None and version != ARCHIVE_VERSION:
                raise ArchiveError(f"versao de backup nao suportada: {version}")

        total_written = 0
        for info in zf.infolist():
            if info.is_dir() or PurePosixPath(info.filename).name == MANIFEST_NAME:
                continue
            if info.file_size > MAX_FILE_BYTES:
                raise ArchiveError(f"arquivo grande demais no backup: {info.filename}")
            if total_written + info.file_size > MAX_TOTAL_BYTES:
                raise ArchiveError("backup maior que o limite permitido")
            rel = _safe_relative(info.filename)
            if rel is None:
                continue
            destination = target.joinpath(*rel.parts)
            _ensure_within(destination, target, info.filename)
            destination.parent.mkdir(parents=True, exist_ok=True)
            file_written = 0
            with zf.open(info) as source, destination.open("wb") as out:
                while True:
                    chunk = source.read(COPY_CHUNK_BYTES)
                    if not chunk:
                        break
                    file_written += len(chunk)
                    total_written += len(chunk)
                    if file_written > MAX_FILE_BYTES:
                        raise ArchiveError(
                            f"arquivo grande demais no backup: {info.filename}"
                        )
                    if total_written > MAX_TOTAL_BYTES:
                        raise ArchiveError("backup maior que o limite permitido")
                    out.write(chunk)


def _load_characters(source: Path) -> list[store.CharacterRecord] | None:
    path = source / "characters" / "characters.json"
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ArchiveError("characters.json invalido no backup") from exc
    records: list[store.CharacterRecord] = []
    chars_root = source / "characters"
    for entry in raw if isinstance(raw, list) else []:
        try:
            record = store.CharacterRecord.model_validate(entry)
        except ValueError as exc:
            raise ArchiveError("registro de personagem invalido no backup") from exc
        char_path = _ensure_within(chars_root / record.filename, chars_root, record.filename)
        if not char_path.exists():
            raise ArchiveError(f"imagem do personagem ausente no backup: {record.name}")
        records.append(record)
    return records


def _merge_characters(
    records: list[store.CharacterRecord] | None, source: Path
) -> int:
    existing = {record.id: record for record in store.load_characters()}
    if records is not None:
        chars_root = source / "characters"
        dest_root = store.characters_dir()
        for record in records:
            existing[record.id] = record
            dest_root.mkdir(parents=True, exist_ok=True)
            src_file = _ensure_within(chars_root / record.filename, chars_root, record.filename)
            dst_file = _ensure_within(dest_root / record.filename, dest_root, record.filename)
            shutil.copy2(src_file, dst_file)
    ordered = sorted(existing.values(), key=lambda r: r.created_at)
    store.save_characters(ordered)
    return len(ordered)


def _load_presets(source: Path) -> list[Any] | None:
    from color_book_generator.presets import StylePreset

    path = source / "presets.json"
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ArchiveError("presets.json invalido no backup") from exc
    items: list[StylePreset] = []
    for entry in raw if isinstance(raw, list) else []:
        try:
            preset = StylePreset.model_validate(entry)
            preset.builtin = False
            items.append(preset)
        except ValueError:
            continue
    return items


def _merge_presets(presets: list[Any] | None) -> None:
    if not presets:
        return
    existing = {p.id: p for p in store.load_custom_presets()}
    for item in presets:
        existing[item.id] = item
    store.save_custom_presets(list(existing.values()))


def _load_settings(source: Path) -> store.BookSettings | None:
    path = source / "settings.json"
    if not path.exists():
        return None
    try:
        return store.BookSettings.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except ValueError as exc:
        raise ArchiveError("settings.json invalido no backup") from exc


def _load_config(source: Path) -> BookConfig | None:
    path = source / "project" / "book.yaml"
    if not path.exists():
        return None
    try:
        return BookConfig.model_validate(
            yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        )
    except Exception as exc:
        raise ArchiveError("book.yaml invalido no backup") from exc


def _is_test_session(state: store.SessionState) -> bool:
    return state.provider == "fake" or state.model == "fake-1"


def _validate_image(path: Path, label: str) -> None:
    if not path.exists() or path.stat().st_size == 0:
        raise ArchiveError(f"arquivo ausente no backup: {label}")
    try:
        with Image.open(path) as img:
            img.verify()
    except Exception as exc:
        raise ArchiveError(f"imagem invalida no backup: {label}") from exc


def _validate_session_assets(state: store.SessionState, directory: Path) -> None:
    for page in state.pages:
        if page.status == "done":
            _validate_image(
                directory / "pages" / f"page-{page.index:03d}.png",
                f"{state.id}/pages/page-{page.index:03d}.png",
            )
    for character in state.characters:
        if character.approved or character.sheet_status == "ready":
            _validate_image(
                directory / "characters" / character.filename,
                f"{state.id}/characters/{character.filename}",
            )
    style_reference = directory / "style" / "reference.png"
    if style_reference.exists():
        _validate_image(style_reference, f"{state.id}/style/reference.png")
    if state.cover_status == "done":
        _validate_image(
            directory / "cover" / "cover.png",
            f"{state.id}/cover/cover.png",
        )
    seen_extra_ids: set[str] = set()
    for extra in [*state.front_extra_pages, *state.back_extra_pages]:
        if extra.id in seen_extra_ids:
            raise ArchiveError(f"pagina extra duplicada no backup: {extra.id}")
        seen_extra_ids.add(extra.id)
        if extra.status == "done":
            _validate_image(
                directory / "extras" / f"extra-{extra.id}.png",
                f"{state.id}/extras/extra-{extra.id}.png",
            )
        background = directory / "extras" / f"extra-{extra.id}-background.png"
        if background.exists():
            _validate_image(
                background, f"{state.id}/extras/{background.name}"
            )


def _load_sessions(
    source: Path,
) -> tuple[list[tuple[store.SessionState, Path]], int]:
    sessions: list[tuple[store.SessionState, Path]] = []
    skipped = 0
    for directory in sorted(source.iterdir()):
        if not directory.is_dir():
            continue
        if directory.name in {"characters", "style", "exports", "project"}:
            continue
        state_file = directory / "session.json"
        if not state_file.exists():
            continue
        try:
            state = store.SessionState.model_validate_json(
                state_file.read_text(encoding="utf-8")
            )
        except ValueError as exc:
            raise ArchiveError(
                f"session.json invalido em {directory.name}"
            ) from exc
        if SKIP_FAKE_SESSIONS and _is_test_session(state):
            skipped += 1
            continue
        _validate_session_assets(state, directory)
        sessions.append((state, directory))
    return sessions, skipped


def session_activity(state: store.SessionState) -> datetime:
    stamps = [state.created_at]
    stamps.extend(
        page.updated_at for page in state.pages if page.updated_at is not None
    )
    stamps.extend(
        extra.updated_at
        for extra in [*state.front_extra_pages, *state.back_extra_pages]
        if extra.updated_at is not None
    )
    if state.cover_updated_at is not None:
        stamps.append(state.cover_updated_at)
    return max(stamps)


def _session_rank(state: store.SessionState) -> tuple[bool, datetime]:
    has_images = (
        any(page.status == "done" for page in state.pages)
        or state.cover_status == "done"
        or any(
            extra.status == "done"
            for extra in [*state.front_extra_pages, *state.back_extra_pages]
        )
    )
    return (has_images, session_activity(state))


def _merge_sessions(
    sessions: list[tuple[store.SessionState, Path]], result: ImportResult
) -> None:
    best_rank: tuple[bool, datetime] | None = None
    for state, directory in sessions:
        if store.session_dir(state.id).exists():
            # A book with this id already exists: import it as a new copy.
            new_id = store.new_session_id()
            while store.session_dir(new_id).exists():
                new_id = store.new_session_id()
            state = state.model_copy(update={"id": new_id})
        destination = store.session_dir(state.id)
        _ensure_within(destination, store.WEB_ROOT, state.id)
        shutil.copytree(directory, destination)
        store.save_session(state)
        result.sessions += 1
        result.pages += len(state.pages)
        result.extra_pages += len(state.front_extra_pages) + len(
            state.back_extra_pages
        )
        if state.cover_status == "done":
            result.covers += 1
        result.session_ids.append(state.id)
        rank = _session_rank(state)
        if best_rank is None or rank > best_rank:
            best_rank = rank
            result.latest_session = state.id


def import_archive(archive: BinaryIO) -> ImportResult:
    result = ImportResult(sessions=0, pages=0, characters=0, style=False)
    with tempfile.TemporaryDirectory(prefix="cbg-import-") as tmp:
        extracted = Path(tmp) / "data"
        extracted.mkdir(parents=True, exist_ok=True)
        archive.seek(0)
        _extract(archive, extracted)

        sessions, skipped = _load_sessions(extracted)
        result.skipped = skipped
        characters = _load_characters(extracted)
        presets = _load_presets(extracted)
        settings = _load_settings(extracted)
        config = _load_config(extracted)
        style_source = extracted / "style" / "reference.png"
        has_style = style_source.exists()
        if has_style:
            try:
                Image.open(style_source).verify()
            except Exception as exc:
                raise ArchiveError("reference.png invalido no backup") from exc

        if not (
            sessions
            or characters is not None
            or presets is not None
            or settings is not None
            or config is not None
            or has_style
            or skipped
        ):
            raise ArchiveError("o backup nao contem dados do projeto")

        # Everything validated; now apply changes.
        result.characters = _merge_characters(characters, extracted)
        _merge_presets(presets)
        if has_style:
            store.style_dir().mkdir(parents=True, exist_ok=True)
            shutil.copy2(style_source, store.style_path())
            result.style = True
        if config is not None:
            destination = book_config_path()
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(extracted / "project" / "book.yaml", destination)
        if settings is not None:
            store.save_settings(settings)
        _merge_sessions(sessions, result)

    return result
