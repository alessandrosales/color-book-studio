"""Domain models shared across the pipeline."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class PagePrompt(BaseModel):
    index: int = Field(ge=1)
    subject: str
    prompt: str
    negative_prompt: str = ""


class GeneratedImage(BaseModel):
    page_index: int
    provider: str
    model: str
    prompt: str
    seed: int | None = None
    raw_path: str
    final_path: str | None = None
    width_px: int
    height_px: int


class Manifest(BaseModel):
    title: str
    author: str
    created_at: datetime = Field(default_factory=datetime.now)
    provider: str
    model: str
    pages: list[GeneratedImage] = Field(default_factory=list)
    pdf_path: str | None = None
    status: Literal["generating", "composing", "done", "failed"] = "generating"
    error: str | None = None
