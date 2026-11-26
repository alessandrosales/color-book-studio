"""Project paths and run directories."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class RunPaths:
    root: Path
    images_raw: Path
    images_final: Path
    pdf: Path
    manifest: Path


def project_root() -> Path:
    return Path.cwd()


def make_run_dir(base: Path | None = None, name: str | None = None) -> RunPaths:
    base = base or project_root() / "output"
    run_name = name or datetime.now().strftime("%Y%m%d-%H%M%S")
    root = base / run_name
    images_raw = root / "raw"
    images_final = root / "pages"
    for d in (root, images_raw, images_final):
        d.mkdir(parents=True, exist_ok=True)
    return RunPaths(
        root=root,
        images_raw=images_raw,
        images_final=images_final,
        pdf=root / "book.pdf",
        manifest=root / "manifest.json",
    )
