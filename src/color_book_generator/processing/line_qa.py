"""Measure printed line art: stroke width and areas too small to colour, plus a safe fix.

Stroke width comes from the distance transform of the ink: on a stroke's centre line
the distance to the paper is half the stroke width. Small areas are enclosed white
regions (not the page background) below the audience's minimum size. Everything is
reported in millimetres at the page's print resolution.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import cv2
import numpy as np
from PIL import Image

MM_PER_INCH = 25.4
# Holes below this are anti-aliasing specks inside strokes, not areas anyone colours.
SPECK_MM = 0.5
# Solid black fills are not strokes; ignore "strokes" wider than this.
MAX_STROKE_MM = 8.0
# Never thicken by more than this in one pass: beyond it the drawing changes character.
MAX_THICKEN_MM = 0.6
# Applied silently while generating a page; larger fixes are the author's call.
AUTO_THICKEN_MM = 0.3


@dataclass(frozen=True)
class LineMetrics:
    main_line_mm: float | None
    detail_line_mm: float | None
    tiny_regions: int
    ink_ratio: float


def _px_per_mm(dpi: int) -> float:
    return dpi / MM_PER_INCH


def _ink_mask(img: Image.Image) -> np.ndarray:
    gray = np.asarray(img.convert("L"))
    return (gray < 128).astype(np.uint8)


def stroke_widths_px(ink: np.ndarray, max_px: float) -> np.ndarray:
    """Stroke widths sampled on the centre lines (ridges) of the ink, in pixels."""
    if not ink.any():
        return np.empty(0, dtype=np.float32)
    dist = cv2.distanceTransform(ink, cv2.DIST_L2, 5)
    ridge = (dist >= 1.0) & (dist >= cv2.dilate(dist, np.ones((3, 3), np.uint8)) - 1e-3)
    # Half-pixel correction: centre pixels sit 0.5 px off the true centre on even widths.
    widths = 2.0 * dist[ridge] - 0.5
    return widths[(widths > 0) & (widths <= max_px)]


def count_tiny_regions(
    ink: np.ndarray,
    min_region_px: float,
    speck_px: float,
    exclude: tuple[int, int] | None = None,
) -> int:
    """Enclosed white areas smaller than ``min_region_px`` squared (rows in ``exclude`` skipped)."""
    paper = (1 - ink).astype(np.uint8)
    num, _labels, stats, _ = cv2.connectedComponentsWithStats(paper, connectivity=4)
    h, w = ink.shape
    min_area = min_region_px * min_region_px
    speck_area = speck_px * speck_px
    count = 0
    for label in range(1, num):
        x, y, bw, bh, area = stats[label]
        if x == 0 or y == 0 or x + bw >= w or y + bh >= h:
            continue  # touches the page edge: it is the background, not an enclosed area
        if exclude is not None and y >= exclude[0] and y + bh <= exclude[1]:
            continue
        if speck_area <= area < min_area:
            count += 1
    return count


def caption_rows(
    height: int, position: Literal["top", "bottom"], share: float = 0.25
) -> tuple[int, int]:
    """Rows of the band where the caption is lettered; its letter holes are not areas to colour."""
    band = round(height * share)
    return (0, band) if position == "top" else (height - band, height)


def measure_line_art(
    img: Image.Image,
    dpi: int,
    *,
    min_region_mm: float,
    exclude_rows: tuple[int, int] | None = None,
) -> LineMetrics:
    ink = _ink_mask(img)
    ppm = _px_per_mm(dpi)
    widths = stroke_widths_px(ink, MAX_STROKE_MM * ppm)
    main = detail = None
    if widths.size >= 50:
        main = round(float(np.percentile(widths, 75)) / ppm, 2)
        detail = round(float(np.percentile(widths, 25)) / ppm, 2)
    tiny = count_tiny_regions(ink, min_region_mm * ppm, SPECK_MM * ppm, exclude_rows)
    return LineMetrics(
        main_line_mm=main,
        detail_line_mm=detail,
        tiny_regions=tiny,
        ink_ratio=round(float(ink.mean()), 4),
    )


def thicken(img: Image.Image, add_px: int) -> Image.Image:
    """Grow every black stroke by ``add_px`` on each side."""
    if add_px <= 0:
        return img
    gray = np.asarray(img.convert("L"))
    size = 2 * add_px + 1
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))
    # Eroding the white paper grows the black ink.
    return Image.fromarray(cv2.erode(gray, kernel), mode="L")


def thicken_px_needed(
    main_line_mm: float | None, target_mm: float, dpi: int, max_mm: float = MAX_THICKEN_MM
) -> int:
    """Pixels per side to bring the main stroke to ``target_mm``; 0 when beyond ``max_mm`` or not needed."""
    if main_line_mm is None or main_line_mm >= target_mm:
        return 0
    missing = target_mm - main_line_mm
    if missing > max_mm:
        return 0
    return max(1, math.ceil(missing * _px_per_mm(dpi) / 2))


def auto_thicken(
    img: Image.Image, dpi: int, target_mm: float, *, max_mm: float = AUTO_THICKEN_MM
) -> tuple[Image.Image, int]:
    """Thicken thin line art up to ``target_mm`` when at most ``max_mm`` is missing.

    Returns the (possibly unchanged) image and the pixels added on each side of a stroke.
    """
    metrics = measure_line_art(img, dpi, min_region_mm=0.0)
    add = thicken_px_needed(metrics.main_line_mm, target_mm, dpi, max_mm)
    return (thicken(img, add), add) if add else (img, 0)
