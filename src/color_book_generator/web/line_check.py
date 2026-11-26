"""Line-weight check for a book: per-page metrics, the book report and the thicken fix."""

from __future__ import annotations

from pathlib import Path

from PIL import Image
from pydantic import BaseModel, Field

from color_book_generator.processing.line_qa import (
    AUTO_THICKEN_MM,
    MAX_THICKEN_MM,
    auto_thicken,
    caption_rows,
    measure_line_art,
    thicken,
    thicken_px_needed,
)
from color_book_generator.standards import LineStandard, line_standard
from color_book_generator.web import store
from color_book_generator.web.store import PageState, SessionState


def standard_for(state: SessionState) -> LineStandard:
    return line_standard(state.story_brief, state.category, state.preset_id)


def _exclude_rows(state: SessionState, page: PageState, height: int) -> tuple[int, int] | None:
    if state.caption_enabled and page.caption_text.strip():
        return caption_rows(height, state.caption_position)
    return None


def record_metrics(state: SessionState, page: PageState, img: Image.Image, dpi: int) -> None:
    """Measure ``img`` (the final page) and store the result on ``page``."""
    std = standard_for(state)
    metrics = measure_line_art(
        img, dpi, min_region_mm=std.min_region_mm, exclude_rows=_exclude_rows(state, page, img.height)
    )
    page.line_main_mm = metrics.main_line_mm
    page.line_detail_mm = metrics.detail_line_mm
    page.tiny_regions = metrics.tiny_regions


def finish_new_page(state: SessionState, page: PageState, img: Image.Image, dpi: int) -> Image.Image:
    """Quietly thicken a freshly processed page when it is only slightly thin, then measure it."""
    fixed, added = auto_thicken(img, dpi, standard_for(state).line_min_mm, max_mm=AUTO_THICKEN_MM)
    page.line_fixed_px = added
    record_metrics(state, page, fixed, dpi)
    return fixed


def _save_preview(img: Image.Image, path: Path) -> None:
    preview = img.copy()
    preview.thumbnail((640, 900), Image.Resampling.LANCZOS)
    preview.save(path, "PNG", optimize=True)


def thicken_page(state: SessionState, page: PageState, dpi: int) -> int:
    """Thicken the saved page up to the standard (at most MAX_THICKEN_MM); returns px added."""
    path = store.page_file(state.id, page.index)
    if not path.exists():
        return 0
    with Image.open(path) as raw:
        img = raw.convert("L")
    if page.line_main_mm is None:
        record_metrics(state, page, img, dpi)
    add = thicken_px_needed(page.line_main_mm, standard_for(state).line_min_mm, dpi, MAX_THICKEN_MM)
    if not add:
        return 0
    fixed = thicken(img, add)
    fixed.save(path, "PNG", dpi=(dpi, dpi))
    _save_preview(fixed, store.preview_dir(state.id) / path.name)
    page.line_fixed_px += add
    record_metrics(state, page, fixed, dpi)
    return add


class PageLineStatus(BaseModel):
    index: int
    main_line_mm: float | None
    detail_line_mm: float | None
    tiny_regions: int | None
    fixed_px: int = 0
    status: str  # ok | thin | thick | busy_areas | unmeasured
    fixable: bool = False
    problems: list[str] = Field(default_factory=list)


class LineReport(BaseModel):
    standard: str
    label: str
    line_min_mm: float
    line_max_mm: float
    min_region_mm: float
    tiny_region_allowance: int
    pages: list[PageLineStatus] = Field(default_factory=list)
    unmeasured: int = 0
    warnings: list[str] = Field(default_factory=list)


def _page_status(std: LineStandard, page: PageState, dpi: int) -> PageLineStatus:
    if page.line_main_mm is None and page.tiny_regions is None:
        return PageLineStatus(
            index=page.index,
            main_line_mm=None,
            detail_line_mm=None,
            tiny_regions=None,
            status="unmeasured",
        )
    problems: list[str] = []
    status = "ok"
    fixable = False
    main = page.line_main_mm
    if main is not None and main < std.line_min_mm:
        status = "thin"
        fixable = thicken_px_needed(main, std.line_min_mm, dpi, MAX_THICKEN_MM) > 0
        hint = "" if fixable else " (diferença grande: gere a página de novo)"
        problems.append(f"traço de {main:g} mm, abaixo de {std.line_min_mm:g} mm{hint}")
    elif main is not None and main > std.line_max_mm * 1.25:
        status = "thick"
        problems.append(f"traço de {main:g} mm, mais grosso que {std.line_max_mm:g} mm do estilo")
    if page.tiny_regions is not None and page.tiny_regions > std.tiny_region_allowance:
        if status == "ok":
            status = "busy_areas"
        problems.append(
            f"{page.tiny_regions} áreas menores que {std.min_region_mm:g} mm "
            f"(até {std.tiny_region_allowance} é normal)"
        )
    return PageLineStatus(
        index=page.index,
        main_line_mm=main,
        detail_line_mm=page.line_detail_mm,
        tiny_regions=page.tiny_regions,
        fixed_px=page.line_fixed_px,
        status=status,
        fixable=fixable,
        problems=problems,
    )


def build_line_report(state: SessionState, dpi: int) -> LineReport:
    std = standard_for(state)
    done = [p for p in sorted(state.pages, key=lambda p: p.index) if p.status == "done"]
    statuses = [_page_status(std, p, dpi) for p in done]
    warnings = [
        f"Página {s.index}: " + "; ".join(s.problems) + "."
        for s in statuses
        if s.problems
    ]
    unmeasured = sum(s.status == "unmeasured" for s in statuses)
    if unmeasured:
        warnings.append(f"{unmeasured} página(s) ainda sem análise de traço.")
    return LineReport(
        standard=std.key,
        label=std.label,
        line_min_mm=std.line_min_mm,
        line_max_mm=std.line_max_mm,
        min_region_mm=std.min_region_mm,
        tiny_region_allowance=std.tiny_region_allowance,
        pages=statuses,
        unmeasured=unmeasured,
        warnings=warnings,
    )


def analyze_pages(state: SessionState, dpi: int, *, only_missing: bool = True) -> int:
    """Measure saved pages (only those without metrics by default); returns how many."""
    count = 0
    for page in state.pages:
        if page.status != "done" or (only_missing and page.line_main_mm is not None):
            continue
        path = store.page_file(state.id, page.index)
        if not path.exists():
            continue
        with Image.open(path) as raw:
            record_metrics(state, page, raw.convert("L"), dpi)
        count += 1
    return count
