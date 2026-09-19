"""`ai.jobs` 행과 kind 별 payload 미러(02 §3.1).

JSON Schema 정본이 없다(계약 7종 밖). 동등성 테스트 대상이 아니다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from geoji_ai.domain.intensity import Intensity

JobKind = Literal["PREPARE", "SENTENCE", "TEXT_RETRY", "RETAIN", "JURY_VOTE"]
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
    """10 §3 표 `{verdict_id, verdict_version, round, intensities[]}`.

    `intensities` 는 10 §4.5 10번이 `(제안)` 이라 선택 필드다. None 이면 `target_intensities` 전체를
    다시 쓴다. 빈 목록은 다시 쓸 강도가 없어 거부한다.
    """

    model_config = ConfigDict(extra="forbid")

    verdict_id: str
    verdict_version: int
    round: int
    intensities: Annotated[list[Intensity], Field(min_length=1)] | None = None


class RetainPayload(BaseModel):
    """`event` 에 맞는 id 하나만 채운다."""

    model_config = ConfigDict(extra="forbid")

    event: RetainEvent
    verdict_id: str | None
    comment_id: str | None
    version: int


class JuryVotePayload(BaseModel):
    """18 §3.1 = 10 §3 새 행. 네 키 모두 필수다.

    `voter_id` 는 백엔드가 넣는 떼거지봇 사용자 id 다. 워커는 값을 만들지 않고 그대로 돌려보낸다.
    """

    model_config = ConfigDict(extra="forbid")

    post_id: str
    post_version: int
    room_id: str
    voter_id: str


JobPayload = PreparePayload | SentencePayload | TextRetryPayload | RetainPayload | JuryVotePayload

_PAYLOAD_BY_KIND: dict[str, type[BaseModel]] = {
    "PREPARE": PreparePayload,
    "SENTENCE": SentencePayload,
    "TEXT_RETRY": TextRetryPayload,
    "RETAIN": RetainPayload,
    "JURY_VOTE": JuryVotePayload,
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
