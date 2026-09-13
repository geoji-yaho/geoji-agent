"""사건 스냅샷 계약 미러 — `contracts/case-snapshot-v1.schema.json` 의 pydantic 판.

01 §3.2 의 평면 구조를 정본으로 삼는다(10 §4.1 의 `post{...}` 중첩과 어긋남 D-1).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, NonNegativeInt

from geoji_ai.contracts.intake import Category, IntakeResult, PostType
from geoji_ai.contracts.sentencing import ReasonSource
from geoji_ai.contracts.writer import BanterStrategy
from geoji_ai.domain.intensity import Intensity


def _unique(items: list) -> list:
    """정본의 `uniqueItems: true` 를 미러에서도 강제한다."""
    if len(set(items)) != len(items):
        raise ValueError("중복 항목이 있다")
    return items


__all__ = [
    "AllowedSentence",
    "Audience",
    "CaseSnapshot",
    "Category",
    "CommentSnapshot",
    "JurySnapshot",
    "JuryStatus",
    "PostType",
    "PrivacyVersion",
    "RoomSnapshot",
    "SentenceCode",
    "SentencingPolicy",
    "VerdictFinal",
    "VerdictResult",
]


class VerdictResult(StrEnum):
    """배심원 평결. 각하(`dismissed`)는 선고 작업을 만들지 않으므로 여기 없다."""

    guilty = "guilty"
    notGuilty = "notGuilty"
    agree = "agree"
    disagree = "disagree"


class JuryStatus(StrEnum):
    """`verdict-view-v1` 의 `jury_status`. 각하까지 5종."""

    guilty = "guilty"
    notGuilty = "notGuilty"
    agree = "agree"
    disagree = "disagree"
    dismissed = "dismissed"


class SentenceCode(StrEnum):
    probation = "probation"
    oneDay = "oneDay"
    life = "life"


class Audience(BaseModel):
    model_config = ConfigDict(extra="forbid")

    room_ids: Annotated[
        list[str],
        Field(max_length=50, json_schema_extra={"uniqueItems": True}),
        AfterValidator(_unique),
    ]
    audience_version: Annotated[int, Field(ge=1)]
    public_share_enabled: bool


class PrivacyVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope_key: str
    epoch: Annotated[int, Field(ge=0)]


class RoomSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    room_id: str
    intensity: Intensity
    rule_version: Annotated[int, Field(ge=0)]


class AllowedSentence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: SentenceCode
    rank: Annotated[int, Field(ge=1)]


class SentencingPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    allowed_sentences: Annotated[list[AllowedSentence], Field(min_length=1, max_length=3)]
    fallback_sentence: SentenceCode
    reason_required: bool


class JurySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict_id: str
    verdict_version: Annotated[int, Field(ge=1)]
    result: VerdictResult
    vote_counts: dict[str, NonNegativeInt]
    guilty_ratio: Annotated[float, Field(ge=0, le=1)]
    confirmed_at: datetime
    deadline_at: datetime
    policy: SentencingPolicy
    target_intensities: Annotated[
        list[Intensity],
        Field(min_length=1, max_length=3, json_schema_extra={"uniqueItems": True}),
        AfterValidator(_unique),
    ]
    default_intensity: Intensity


class VerdictFinal(BaseModel):
    """확정 판결 요약. RETAIN 스냅샷에만 온다(10 §4.1 제안).

    `sentence` 는 문자열이다. 허용 목록 대조는 코드가 한다 — `SentencingDecision` 과 같다.
    유죄가 아니면 null.
    """

    model_config = ConfigDict(extra="forbid")

    sentence: str | None
    sentence_source: Literal["AI", "RULE"] | None
    sentencing_reason: Annotated[str, Field(max_length=100)] | None
    reason_source: ReasonSource | None
    applied_intensity: Intensity
    banter_strategy: BanterStrategy | None


class CommentSnapshot(BaseModel):
    """RETAIN 대상 댓글(10 §4.1 제안). `post_status` 값 목록은 백엔드 소유라 문자열이다."""

    model_config = ConfigDict(extra="forbid")

    comment_id: str
    version: Annotated[int, Field(ge=1)]
    room_id: str
    post_id: str
    post_status: str
    author_id: str
    content: Annotated[str, Field(max_length=1000)]
    created_at: datetime


class CaseSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    post_id: str
    author_id: str
    post_version: Annotated[int, Field(ge=1)]
    item: Annotated[str, Field(max_length=30)]
    reason: Annotated[str, Field(max_length=200)] | None
    amount_krw: Annotated[int, Field(gt=0)]
    category: Category
    post_type: PostType
    created_at: datetime
    audience: Audience
    privacy_versions: Annotated[list[PrivacyVersion], Field(max_length=100)]
    room_snapshots: Annotated[list[RoomSnapshot], Field(max_length=50)]
    intake_result: IntakeResult | None
    jury: JurySnapshot | None
    # RETAIN 확장(10 §4.1 제안). 선택 필드라 `required` 에 없고, 기존 스냅샷은 그대로 유효하다.
    verdict_final: VerdictFinal | None = None
    comment: CommentSnapshot | None = None
