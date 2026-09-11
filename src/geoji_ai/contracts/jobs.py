"""`ai.jobs` 행과 kind 별 payload 미러(02 §3.1).

JSON Schema 정본이 없다(계약 7종 밖). 동등성 테스트 대상이 아니다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

JobKind = Literal["PREPARE", "SENTENCE", "TEXT_RETRY", "RETAIN"]
JobStatus = Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"]
RetainEvent = Literal["sentence.finalized", "comment.approved"]


class PreparePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    post_id: str
    post_version: int
    audience_version: int


class SentencePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict_id: str
    verdict_version: int
    post_id: str


class TextRetryPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict_id: str
    verdict_version: int
    round: int


class RetainPayload(BaseModel):
    """`event` 에 맞는 id 하나만 채운다."""

    model_config = ConfigDict(extra="forbid")

    event: RetainEvent
    verdict_id: str | None
    comment_id: str | None
    version: int


JobPayload = PreparePayload | SentencePayload | TextRetryPayload | RetainPayload

_PAYLOAD_BY_KIND: dict[str, type[BaseModel]] = {
    "PREPARE": PreparePayload,
    "SENTENCE": SentencePayload,
    "TEXT_RETRY": TextRetryPayload,
    "RETAIN": RetainPayload,
}


class Job(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    event_id: str
    event_type: str
    kind: JobKind
    dedupe_key: str
    aggregate_id: str
    aggregate_version: Annotated[int, Field(gt=0)]
    schema_version: Literal[1]
    payload: dict[str, Any]
    status: JobStatus
    priority: int
    attempts: Annotated[int, Field(ge=0)]
    max_attempts: Annotated[int, Field(gt=0)]
    available_at: datetime
    deadline_at: datetime | None
    lease_until: datetime | None
    owner_id: str | None
    generation_id: str | None
    last_error_code: str | None
    trace_id: str
    created_at: datetime
    updated_at: datetime


def parse_payload(job: Job) -> JobPayload:
    """`job.kind` 에 맞는 payload 모델로 파싱한다."""
    model = _PAYLOAD_BY_KIND[job.kind]
    return model.model_validate(job.payload)  # type: ignore[return-value]
