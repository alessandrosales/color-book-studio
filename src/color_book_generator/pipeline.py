"""End-to-end generation pipeline: prompts -> images -> line art -> PDF."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from color_book_generator.config import BookConfig
from color_book_generator.layout.pdf import compose_pdf
from color_book_generator.models import GeneratedImage, Manifest
from color_book_generator.paths import RunPaths, make_run_dir
from color_book_generator.processing.line_art import prepare_page
from color_book_generator.prompts import build_page_prompts
from color_book_generator.providers import get_provider
from color_book_generator.providers.base import ProviderError, generate_matching

PageCallback = Callable[[int, int], None]
MessageCallback = Callable[[str], None]


@dataclass
class RunResult:
    run: RunPaths
    manifest: Manifest
    errors: list[str] = field(default_factory=list)


def _ink_ratio(img: Image.Image) -> float:
    arr = np.asarray(img.convert("L"))
    return float((arr < 128).mean())


def _score(page: Image.Image) -> float:
    """Prefer readable line art: enough ink, not a black blob."""
    ratio = _ink_ratio(page)
    ideal = 0.12
    if ratio < 0.01 or ratio > 0.6:
        return -1.0
    return 1.0 - abs(ratio - ideal)


def generate(
    cfg: BookConfig,
    *,
    subjects: list[str] | None = None,
    out_dir: Path | None = None,
    run_name: str | None = None,
    resume: bool = False,
    dry_run: bool = False,
    on_page: PageCallback | None = None,
    on_message: MessageCallback | None = None,
) -> RunResult:
    say = on_message or (lambda _msg: None)
    run = make_run_dir(out_dir, run_name)

    prompts = build_page_prompts(cfg, subjects)
    manifest = Manifest(
        title=cfg.title,
        author=cfg.author,
        provider=cfg.provider,
        model=cfg.model,
    )
    _save_manifest(manifest, run)
    say(f"run directory: {run.root}")
    say(f"{len(prompts)} pages, provider={cfg.provider} model={cfg.model}")

    (run.root / "prompts.txt").write_text(
        "\n\n".join(f"[{p.index}] {p.prompt}" for p in prompts) + "\n", encoding="utf-8"
    )

    if dry_run:
        say("dry run: prompts written, no images generated")
        manifest.status = "done"
        _save_manifest(manifest, run)
        return RunResult(run=run, manifest=manifest)

    provider = get_provider(cfg.provider, cfg.model)
    target_px = cfg.image_px

    for prompt in prompts:
        final_path = run.images_final / f"page-{prompt.index:03d}.png"
        if resume and final_path.exists():
            say(f"[{prompt.index}/{len(prompts)}] skipped (exists)")
            manifest.pages.append(
                GeneratedImage(
                    page_index=prompt.index,
                    provider=cfg.provider,
                    model=cfg.model,
                    prompt=prompt.prompt,
                    raw_path=str(run.images_raw / f"page-{prompt.index:03d}.png"),
                    final_path=str(final_path),
                    width_px=target_px[0],
                    height_px=target_px[1],
                )
            )
            _save_manifest(manifest, run)
            if on_page:
                on_page(prompt.index, len(prompts))
            continue

        try:
            best_img = _generate_best(provider, cfg, prompt.prompt, prompt.negative_prompt)
        except ProviderError as exc:
            msg = f"page {prompt.index} failed: {exc}"
            say(msg)
            manifest.error = msg
            _save_manifest(manifest, run)
            raise

        raw_path = run.images_raw / f"page-{prompt.index:03d}.png"
        best_img.save(raw_path, "PNG")

        page = prepare_page(best_img, target_px, cfg.processing)
        page.save(final_path, "PNG", dpi=(cfg.dpi, cfg.dpi))

        manifest.pages.append(
            GeneratedImage(
                page_index=prompt.index,
                provider=cfg.provider,
                model=cfg.model,
                prompt=prompt.prompt,
                seed=cfg.seed,
                raw_path=str(raw_path),
                final_path=str(final_path),
                width_px=page.width,
                height_px=page.height,
            )
        )
        _save_manifest(manifest, run)
        say(f"[{prompt.index}/{len(prompts)}] done")
        if on_page:
            on_page(prompt.index, len(prompts))

    manifest.status = "composing"
    _save_manifest(manifest, run)

    pages = sorted(run.images_final.glob("page-*.png"))
    compose_pdf(
        pages,
        run.pdf,
        cfg,
        include_title_page=cfg.include_title_page,
        blank_verso=cfg.blank_verso,
    )
    manifest.pdf_path = str(run.pdf)
    manifest.status = "done"
    _save_manifest(manifest, run)
    say(f"pdf: {run.pdf}")
    return RunResult(run=run, manifest=manifest)


def _generate_best(provider, cfg: BookConfig, prompt: str, negative: str) -> Image.Image:  # type: ignore[no-untyped-def]
    best: Image.Image | None = None
    best_score = float("-inf")
    for _ in range(cfg.candidates):
        img = generate_matching(
            provider,
            prompt,
            target_size=cfg.image_px,
            negative_prompt=negative,
            seed=cfg.seed,
        )
        processed = prepare_page(img, cfg.image_px, cfg.processing)
        score = _score(processed)
        if score > best_score:
            best, best_score = img, score
        if best_score >= 0.98:
            break
    assert best is not None
    return best


def _save_manifest(manifest: Manifest, run: RunPaths) -> None:
    run.manifest.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")


def compose_from_pages(pages_dir: Path, out_pdf: Path, cfg: BookConfig) -> Path:
    pages = sorted(pages_dir.glob("page-*.png"))
    if not pages:
        raise FileNotFoundError(f"no page-*.png found in {pages_dir}")
    return compose_pdf(pages, out_pdf, cfg)
