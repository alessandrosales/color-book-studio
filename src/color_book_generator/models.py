"""Domain models shared across prompt generation."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PagePrompt(BaseModel):
    index: int = Field(ge=1)
    subject: str
    prompt: str
    negative_prompt: str = ""
