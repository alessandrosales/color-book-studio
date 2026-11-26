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
MAX_FILE_BYTES = 120 * 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
SKIP_FAKE_SESSIONS = True


class ArchiveError(RuntimeError):
    """Raised when an archive is invalid or unsafe."""


def book_config_path() -> Path:
    return default_book_config_path()


class ImportResult(BaseModel):
    sessions: int
    pages: int
    characters: int
    style: bool
    latest_session: str | None = None
    session_ids: list[str] = []
    skipped: int = 0


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
    elif parts[0] in {"settings.json", "characters", "style"} or (len(parts) >= 2 and parts[-1] in {"session.json", "characters.json", "reference.png"}):
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
                manifest: dict[str, Any] = json.loads(zf.read(manifest_name))
            except (json.JSONDecodeError, ValueError) as exc:
                raise ArchiveError("manifest.json invalido") from exc
            version = manifest.get("archive_version")
            if version is not None and version != ARCHIVE_VERSION:
                raise ArchiveError(f"versao de backup nao suportada: {version}")

        total = 0
        for info in zf.infolist():
            if info.is_dir() or PurePosixPath(info.filename).name == MANIFEST_NAME:
                continue
            if info.file_size > MAX_FILE_BYTES:
                raise ArchiveError(f"arquivo grande demais no backup: {info.filename}")
            total += info.file_size
            if total > MAX_TOTAL_BYTES:
                raise ArchiveError("backup maior que o limite permitido")
            rel = _safe_relative(info.filename)
            if rel is None:
                continue
            destination = target.joinpath(*rel.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            resolved = destination.resolve()
            if not str(resolved).startswith(str(target.resolve())):
                raise ArchiveError(f"caminho fora do destino: {info.filename}")
            with zf.open(info) as source, destination.open("wb") as out:
                shutil.copyfileobj(source, out)


def _load_characters(source: Path) -> list[store.CharacterRecord] | None:
    path = source / "characters" / "characters.json"
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ArchiveError("characters.json invalido no backup") from exc
    records: list[store.CharacterRecord] = []
    for entry in raw if isinstance(raw, list) else []:
        try:
            record = store.CharacterRecord.model_validate(entry)
        except ValueError:
            continue
        if not (source / "characters" / record.filename).exists():
            raise ArchiveError(f"imagem do personagem ausente no backup: {record.name}")
        records.append(record)
    return records


def _merge_characters(
    records: list[store.CharacterRecord] | None, source: Path
) -> int:
    existing = {record.id: record for record in store.load_characters()}
    if records is not None:
        for record in records:
            existing[record.id] = record
            store.characters_dir().mkdir(parents=True, exist_ok=True)
            shutil.copy2(
                source / "characters" / record.filename,
                store.characters_dir() / record.filename,
            )
    ordered = sorted(existing.values(), key=lambda r: r.created_at)
    store.save_characters(ordered)
    return len(ordered)


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
        sessions.append((state, directory))
    return sessions, skipped


def session_activity(state: store.SessionState) -> datetime:
    stamps = [state.created_at]
    stamps.extend(
        page.updated_at for page in state.pages if page.updated_at is not None
    )
    return max(stamps)


def _session_rank(state: store.SessionState) -> tuple[bool, datetime]:
    has_images = any(page.status == "done" for page in state.pages)
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
        shutil.copytree(directory, destination)
        store.save_session(state)
        result.sessions += 1
        result.pages += len(state.pages)
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
            or settings is not None
            or config is not None
            or has_style
        ):
            raise ArchiveError("o backup nao contem dados do projeto")

        # Everything validated; now apply changes.
        result.characters = _merge_characters(characters, extracted)
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
