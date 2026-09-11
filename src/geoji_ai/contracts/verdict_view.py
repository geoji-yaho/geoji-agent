"""프론트 폴링 응답 계약 미러 — `contracts/verdict-view-v1.schema.json` 의 pydantic 판(10 §9)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from geoji_ai.contracts.case import JuryStatus, SentenceCode
from geoji_ai.contracts.writer import MemeTag
from geoji_ai.domain.intensity import Intensity

SentenceStatus = Literal["PENDING", "FINAL"]
TextStatus = Literal["PENDING", "GENERATING", "TEMPLATE_READY", "AI_READY"]
ViewSource = Literal["AI", "TEMPLATE"]


class MemeView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tag: MemeTag
    image_id: str
    image_url: str


class VerdictTextView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intensity: Intensity
    headline: str
    statement: list[str]
    sentence: SentenceCode
    sentence_label: str
    sentencing_reason: str | None
    source: ViewSource
    meme: MemeView


class VerdictView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    post_id: str
    jury_status: JuryStatus
    sentence_status: SentenceStatus
    text_status: TextStatus
    text_version: Annotated[int, Field(ge=0)]
    view: VerdictTextView | None
    poll_after_ms: int
