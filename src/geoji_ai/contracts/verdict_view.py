"""프론트 폴링 응답 계약 미러 — `contracts/verdict-view-v1.schema.json` 의 pydantic 판(10 §9)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from geoji_ai.contracts.case import JuryStatus, SentenceCode
from geoji_ai.contracts.writer import HEADLINE_MAX, STATEMENT_MAX, STATEMENT_TOTAL_MAX, MemeTag
from geoji_ai.domain.intensity import Intensity

SentenceStatus = Literal["PENDING", "FINAL"]
TextStatus = Literal["PENDING", "GENERATING", "TEMPLATE_READY", "AI_READY"]
ViewSource = Literal["AI", "TEMPLATE"]


class _PublicView(BaseModel):
    model_config = ConfigDict(extra="forbid", alias_generator=to_camel, serialize_by_alias=True)


class MemeView(_PublicView):
    tag: MemeTag
    image_id: str
    image_url: str


class VerdictTextView(_PublicView):
    intensity: Intensity
    headline: Annotated[str, Field(max_length=HEADLINE_MAX)]
    statement: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=STATEMENT_TOTAL_MAX)]],
        Field(min_length=1, max_length=STATEMENT_MAX),
    ]
    sentence: SentenceCode | None
    sentence_label: Annotated[str, Field(max_length=30)] | None
    sentencing_reason: Annotated[str, Field(max_length=100)] | None
    source: ViewSource
    meme: MemeView | None


class VerdictView(_PublicView):
    schema_version: Literal[1]
    post_id: str
    jury_status: JuryStatus | None
    sentence_status: SentenceStatus
    text_status: TextStatus
    text_version: Annotated[int, Field(ge=0)]
    view: VerdictTextView | None
    poll_after_ms: Annotated[int, Field(ge=0, le=60_000)]
