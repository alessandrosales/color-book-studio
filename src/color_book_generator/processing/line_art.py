"""Turn generated images into clean, printable black-and-white line art."""

from __future__ import annotations

import cv2
import numpy as np
from PIL import Image

from color_book_generator.config import CaptionConfig, ProcessingConfig
from color_book_generator.processing.caption import (
    caption_band_height,
    draw_caption,
)


def _as_gray(img: Image.Image) -> np.ndarray:
    return np.array(img.convert("L"))


def _ensure_dark_lines(gray: np.ndarray) -> np.ndarray:
    """Invert images that are mostly dark so lines end up black on white."""
    if float(gray.mean()) < 110:
        return cv2.bitwise_not(gray)
    return gray


def _threshold(gray: np.ndarray, cfg: ProcessingConfig) -> np.ndarray:
    if cfg.threshold == "otsu":
        _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    elif cfg.threshold == "fixed":
        _, bw = cv2.threshold(gray, cfg.fixed_threshold, 255, cv2.THRESH_BINARY)
    else:
        bw = cv2.adaptiveThreshold(
            gray,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            blockSize=31,
            C=10,
        )
    return bw


def _remove_small_components(bw: np.ndarray, min_px: int) -> np.ndarray:
    if min_px <= 0:
        return bw
    inv = cv2.bitwise_not(bw)
    num, labels, stats, _ = cv2.connectedComponentsWithStats(inv, connectivity=8)
    cleaned = np.zeros_like(inv)
    for label in range(1, num):
        if stats[label, cv2.CC_STAT_AREA] >= min_px:
            cleaned[labels == label] = 255
    return cv2.bitwise_not(cleaned)


def _content_bbox(bw: np.ndarray) -> tuple[int, int, int, int]:
    inv = cv2.bitwise_not(bw)
    ys, xs = np.where(inv > 0)
    if len(xs) == 0:
        h, w = bw.shape
        return 0, 0, w, h
    return int(np.min(xs)), int(np.min(ys)), int(np.max(xs)) + 1, int(np.max(ys)) + 1


def to_line_art(img: Image.Image, cfg: ProcessingConfig) -> Image.Image:
    """Return a pure black-on-white line-art image (mode 'L')."""
    gray = _as_gray(img)
    gray = _ensure_dark_lines(gray)
    if cfg.denoise:
        gray = cv2.medianBlur(gray, 3)
        gray = cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX)
    bw = _threshold(gray, cfg)
    bw = _remove_small_components(bw, cfg.min_component_px)
    return Image.fromarray(bw, mode="L")


def fit_to_page(img: Image.Image, target_px: tuple[int, int], cfg: ProcessingConfig) -> Image.Image:
    """Crop to content, scale up to fit the page with a safe margin, pad to exact size."""
    bw = np.array(img)
    x0, y0, x1, y1 = _content_bbox(bw)
    cropped = Image.fromarray(bw[y0:y1, x0:x1], mode="L")

    tw, th = target_px
    margin = cfg.target_inner_margin_ratio
    avail_w = max(1, int(tw * (1 - 2 * margin)))
    avail_h = max(1, int(th * (1 - 2 * margin)))

    scale = min(avail_w / cropped.width, avail_h / cropped.height, cfg.max_upscale)
    new_w = max(1, round(cropped.width * scale))
    new_h = max(1, round(cropped.height * scale))
    resized = cropped.resize((new_w, new_h), Image.Resampling.LANCZOS)

    canvas = Image.new("L", (tw, th), color=255)
    canvas.paste(resized, ((tw - new_w) // 2, (th - new_h) // 2))
    return canvas


def prepare_page(
    img: Image.Image,
    target_px: tuple[int, int],
    cfg: ProcessingConfig,
    caption: CaptionConfig | None = None,
    caption_text: str = "",
) -> Image.Image:
    line_art = to_line_art(img, cfg)
    if caption is None or not caption.enabled or not caption_text.strip():
        return fit_to_page(line_art, target_px, cfg)

    band = caption_band_height(target_px, caption, caption_text)
    width, height = target_px
    art = fit_to_page(line_art, (width, height - band), cfg)
    canvas = Image.new("L", target_px, color=255)
    canvas.paste(art, (0, band if caption.position == "top" else 0))
    draw_caption(canvas, caption_text, caption, band)
    return canvas
