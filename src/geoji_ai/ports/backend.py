"""백엔드 내부 API 포트 (01 §3.6, 10 §4·§5).

요청·응답 형태는 10 §4.2·§4.3·§5 를 옮긴 것이다. JSON Schema 정본이 없는 자리라
pydantic 모델만 둔다. httpx 어댑터는 작업 3.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from geoji_ai.contracts.case import CaseSnapshot
from geoji_ai.contracts.finalize import FinalizeRequest
from geoji_ai.contracts.sentencing import ReasonSource

Visibility = Literal["PUBLIC", "ROOMS", "PRIVATE"]
EvidenceInclude = Literal["rules", "aggregates", "recent_verdicts", "style_comments"]

# 10 §4.6 오류 코드 표(`(제안)` 미채택분은 넣지 않는다)
GenerationErrorCode = Literal[
    "AI_NOT_READY",
    "POLICY_ERROR",
    "EVIDENCE_INVALIDATED",
    "VENDOR_UNAVAILABLE",
    "BUDGET_EXCEEDED",
    "EVAL_FAILED",
    "SCHEMA_INVALID",
    "DEADLINE_EXCEEDED",
]

# 10 §5 finalize 거부 코드
FinalizeErrorCode = Literal[
    "STALE_GENERATION",
    "EVIDENCE_INVALIDATED",
    "DEADLINE_EXCEEDED",
    "IDEMPOTENCY_CONFLICT",
    "INVALID_DRAFT",
]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceCandidate(_Model):
    source_type: str
    source_id: str
    source_version: int
    score: float


class ResolveEvidenceRequest(_Model):
    """10 §4.2 요청. 후보 ≤ 20."""

    candidates: list[EvidenceCandidate] = Field(max_length=20)
    include: list[EvidenceInclude]


class EvidenceScope(_Model):
    visibility: Visibility
    room_ids: list[str]


class EvidenceSource(_Model):
    source_type: str
    source_id: str
    source_version: int
    payload: dict[str, Any]
    scope: EvidenceScope


class AggregateWindow(_Model):
    start_at: datetime
    end_at: datetime


class Aggregates(_Model):
    burn_rate: float
    tier: str
    no_spend_days: int
    repeat_same_category_30d: int
    excludes_post_id: str
    window: AggregateWindow
    rule_version: int


class RoomRule(_Model):
    room_id: str
    rule_id: str
    version: int
    text: str


class RecentVerdict(_Model):
    post_id: str
    post_version: int
    category: str
    amount_krw: int
    reason: str | None
    result: str
    sentence: str
    judged_at: datetime
    scope: EvidenceScope


class StyleComment(_Model):
    comment_id: str
    room_id: str
    content: str
    created_at: datetime


class ResolveEvidenceResponse(_Model):
    """10 §4.2 응답. `style_comments` 는 플래그가 켜졌을 때만 채워진다."""

    sources: list[EvidenceSource]
    aggregates: Aggregates
    room_rules: list[RoomRule]
    recent_verdicts: list[RecentVerdict]
    style_comments: list[StyleComment]


class FixedSentencing(_Model):
    sentence: str
    sentencing_reason: str | None
    reason_source: ReasonSource


class BeginGenerationResult(_Model):
    """10 §4.3 응답. `fixed_sentencing` 이 있으면 양형관을 부르지 않는다."""

    fixed_sentencing: FixedSentencing | None
    text_version: int
    deadline_at: datetime


class FinalizeResult(_Model):
    """10 §5 200 응답."""

    verdict_id: str
    text_version: int
    committed_at: datetime


@runtime_checkable
class BackendPort(Protocol):
    async def snapshot(self, job_id: str, generation_id: str) -> CaseSnapshot: ...

    async def resolve_evidence(
        self,
        job_id: str,
        generation_id: str,
        req: ResolveEvidenceRequest,
    ) -> ResolveEvidenceResponse: ...

    async def begin_generation(
        self,
        verdict_id: str,
        *,
        job_id: str,
        generation_id: str,
        verdict_version: int,
    ) -> BeginGenerationResult: ...

    async def finalize(self, verdict_id: str, req: FinalizeRequest) -> FinalizeResult: ...

    async def generation_failed(
        self,
        verdict_id: str,
        *,
        job_id: str,
        generation_id: str,
        error_code: GenerationErrorCode,
    ) -> None: ...
