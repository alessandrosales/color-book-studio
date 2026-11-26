"""Export and import the whole web workspace as a portable archive."""

from __future__ import annotations

import json
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

from pydantic import BaseModel

from color_book_generator import __version__
from color_book_generator.web import store

ARCHIVE_VERSION = 1
MANIFEST_NAME = "manifest.json"
MAX_FILE_BYTES = 120 * 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024


class ArchiveError(RuntimeError):
    """Raised when an archive is invalid or unsafe."""


class ImportResult(BaseModel):
    sessions: int
    pages: int
    characters: int
    style: bool
    latest_session: str | None = None


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
    path = PurePosixPath(name.replace("\\", "/"))
    if path.is_absolute() or not path.parts:
        raise ArchiveError(f"caminho invalido no backup: {name}")
    if ".." in path.parts or path.parts[0] != "data":
        raise ArchiveError(f"caminho invalido no backup: {name}")
    return path


def _extract(archive: BinaryIO, target: Path) -> None:
    try:
        zf = zipfile.ZipFile(archive)
    except zipfile.BadZipFile as exc:
        raise ArchiveError("o arquivo enviado nao e um zip valido") from exc

    with zf:
        names = zf.namelist()
        if MANIFEST_NAME not in names:
            raise ArchiveError("manifest.json ausente: backup invalido")
        try:
            manifest: dict[str, Any] = json.loads(zf.read(MANIFEST_NAME))
        except (json.JSONDecodeError, ValueError) as exc:
            raise ArchiveError("manifest.json invalido") from exc
        if manifest.get("archive_version") != ARCHIVE_VERSION:
            raise ArchiveError(
                f"versao de backup nao suportada: {manifest.get('archive_version')}"
            )

        total = 0
        for info in zf.infolist():
            if info.is_dir() or info.filename == MANIFEST_NAME:
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


def _merge_characters(source: Path) -> int:
    incoming_path = source / "characters" / "characters.json"
    if not incoming_path.exists():
        return len(store.load_characters())
    try:
        incoming = json.loads(incoming_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ArchiveError("characters.json invalido no backup") from exc
    records = {record.id: record for record in store.load_characters()}
    for entry in incoming if isinstance(incoming, list) else []:
        try:
            record = store.CharacterRecord.model_validate(entry)
        except ValueError:
            continue
        records[record.id] = record
        image = source / "characters" / record.filename
        if image.exists():
            store.characters_dir().mkdir(parents=True, exist_ok=True)
            shutil.copy2(image, store.characters_dir() / record.filename)
    ordered = sorted(records.values(), key=lambda r: r.created_at)
    store.save_characters(ordered)
    return len(ordered)


def _merge_sessions(source: Path, result: ImportResult) -> None:
    latest_created = None
    for directory in sorted(source.iterdir()):
        if not directory.is_dir():
            continue
        if directory.name in {"characters", "style", "exports"}:
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
        destination = store.session_dir(state.id)
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(directory, destination)
        result.sessions += 1
        result.pages += len(state.pages)
        if latest_created is None or state.created_at > latest_created:
            latest_created = state.created_at
            result.latest_session = state.id


def import_archive(archive: BinaryIO) -> ImportResult:
    result = ImportResult(sessions=0, pages=0, characters=0, style=False)
    with tempfile.TemporaryDirectory(prefix="cbg-import-") as tmp:
        extracted = Path(tmp) / "data"
        extracted.mkdir(parents=True, exist_ok=True)
        archive.seek(0)
        _extract(archive, extracted.parent)

        result.characters = _merge_characters(extracted)

        style_source = extracted / "style" / "reference.png"
        if style_source.exists():
            store.style_dir().mkdir(parents=True, exist_ok=True)
            shutil.copy2(style_source, store.style_path())
            result.style = True

        settings_source = extracted / "settings.json"
        if settings_source.exists():
            try:
                settings = store.BookSettings.model_validate_json(
                    settings_source.read_text(encoding="utf-8")
                )
            except ValueError as exc:
                raise ArchiveError("settings.json invalido no backup") from exc
            store.save_settings(settings)

        _merge_sessions(extracted, result)

    return result
