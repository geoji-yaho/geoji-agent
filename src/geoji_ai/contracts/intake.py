"""심문관 계약 미러 — `contracts/intake-v1.schema.json` 의 pydantic 판.

정본은 JSON Schema 다. 이 모듈은 같은 `required`·enum·길이 제약을 갖는다.
`Category`·`PostType` 은 여기서 한 번만 정의하고 `contracts.case` 가 다시 내보낸다
(정의 지점이 둘이 되지 않게. `case` 가 `IntakeResult` 를 쓰므로 반대 방향은 순환이다).
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PostType = Literal["spent", "considering"]

Category = Literal[
    "식비",
    "배달",
    "카페/간식",
    "교통/택시",
    "쇼핑/패션",
    "뷰티",
    "취미/여가",
    "술/유흥",
    "구독",
    "생활",
    "기타",
]

IntakeMode = Literal["INITIAL", "FINAL_CHECK"]
IntakeStatus = Literal["PASS", "NEEDS_CLARIFICATION", "BLOCKED"]
IntakeSource = Literal["AI", "FALLBACK"]


class IntakeRequest(BaseModel):
    """제출 직전 심문관 호출 요청(10 §4)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    submission_id: str
    payload_hash: str
    mode: IntakeMode
    post_type: PostType
    amount_krw: Annotated[int, Field(gt=0)]
    category: Category
    item: Annotated[str, Field(max_length=30)]
    reason: Annotated[str, Field(max_length=200)] | None


class ItemReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["OK", "VAGUE", "EXAGGERATED"]
    suggested_item: Annotated[str, Field(max_length=30)] | None


class CategoryReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["OK", "MISMATCH"]
    suggested_category: Category | None
    confidence: Annotated[float, Field(ge=0, le=1)]


class IntakeResult(BaseModel):
    """심문관 결과. `FINAL_CHECK` 는 `NEEDS_CLARIFICATION` 을 낼 수 없다(01 §3.2)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    mode: IntakeMode
    status: IntakeStatus
    item_review: ItemReview
    message: Annotated[str, Field(max_length=60)] | None
    category_review: CategoryReview
    injection_detected: bool
    intake_source: IntakeSource

    @model_validator(mode="after")
    def _final_check_has_no_clarification(self) -> IntakeResult:
        if self.mode == "FINAL_CHECK" and self.status == "NEEDS_CLARIFICATION":
            raise ValueError("FINAL_CHECK 에서는 NEEDS_CLARIFICATION 을 낼 수 없다")
        return self
