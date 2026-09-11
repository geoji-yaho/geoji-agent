"""사건 스냅샷 계약 미러 — `contracts/case-snapshot-v1.schema.json` 의 pydantic 판.

01 §3.2 의 평면 구조를 정본으로 삼는다(10 §4.1 의 `post{...}` 중첩과 어긋남 D-1).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, NonNegativeInt

from geoji_ai.contracts.intake import Category, IntakeResult, PostType
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
    "JurySnapshot",
    "JuryStatus",
    "PostType",
    "PrivacyVersion",
    "RoomSnapshot",
    "SentenceCode",
    "SentencingPolicy",
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
        list[str], Field(json_schema_extra={"uniqueItems": True}), AfterValidator(_unique)
    ]
    audience_version: int
    public_share_enabled: bool


class PrivacyVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope_key: str
    epoch: int


class RoomSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    room_id: str
    intensity: Intensity
    rule_version: int


class AllowedSentence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: SentenceCode
    rank: int


class SentencingPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    allowed_sentences: list[AllowedSentence]
    fallback_sentence: SentenceCode
    reason_required: bool


class JurySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict_id: str
    verdict_version: Annotated[int, Field(ge=1)]
    result: VerdictResult
    vote_counts: dict[str, NonNegativeInt]
    guilty_ratio: float
    confirmed_at: datetime
    deadline_at: datetime
    policy: SentencingPolicy
    target_intensities: Annotated[
        list[Intensity], Field(json_schema_extra={"uniqueItems": True}), AfterValidator(_unique)
    ]
    default_intensity: Intensity


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
    privacy_versions: list[PrivacyVersion]
    room_snapshots: list[RoomSnapshot]
    intake_result: IntakeResult | None
    jury: JurySnapshot | None
