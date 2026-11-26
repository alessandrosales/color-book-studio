"""Filesystem helpers shared by the CLI pipeline and the web store."""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """Write ``text`` to ``path`` so readers see either the old or the new file.

    The content goes to a temporary file in the same directory, is flushed and
    fsynced, and then replaces the destination with ``os.replace`` (atomic on
    POSIX and Windows when both paths are on the same filesystem). A crash in
    the middle never leaves a truncated destination behind.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
        raise
    _fsync_dir(path.parent)


def _fsync_dir(directory: Path) -> None:
    """Persist the rename itself (best effort; not supported everywhere)."""
    flags = getattr(os, "O_DIRECTORY", None)
    if flags is None:
        return
    try:
        fd = os.open(directory, os.O_RDONLY | flags)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)
