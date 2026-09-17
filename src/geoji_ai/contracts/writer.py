"""서기 초안 계약 미러 — `contracts/writer-draft-v1.schema.json` 의 pydantic 판."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from geoji_ai.domain.attack_angles import AttackAngle
from geoji_ai.domain.intensity import Intensity

# 길이 상한의 단일 정의. `domain/validation.py` 가 같은 값을 import 한다.
HEADLINE_MAX = 30
STATEMENT_MIN = 1
STATEMENT_MAX = 4
STATEMENT_TOTAL_MAX = 300

# 새 생성은 카드 분량으로 제한한다. 저장된 과거 판결문에는 위의 읽기 계약을 유지한다.
CARD_HEADLINE_MAX = 20
CARD_STATEMENT_MAX = 30
_CARD_LINE_BREAKS = "\r\n\v\f\x1c\x1d\x1e\x85\u2028\u2029"
_CARD_LINE_PATTERN = (
    r"^[^\r\n\v\f\u001c-\u001e\u0085\u2028\u2029]*\S"
    r"[^\r\n\v\f\u001c-\u001e\u0085\u2028\u2029]*$"
)


def _validate_card_line(value: str) -> str:
    if not value.strip() or any(char in _CARD_LINE_BREAKS for char in value):
        raise ValueError("카드 문구는 공백뿐이거나 줄바꿈을 포함할 수 없다")
    return value


StatementKind = Literal["fact", "claim", "opinion"]
TextSource = Literal["AI", "TEMPLATE"]


class BanterStrategy(StrEnum):
    CHEAPER_ALTERNATIVE = "CHEAPER_ALTERNATIVE"
    FREE_ALTERNATIVE = "FREE_ALTERNATIVE"
    DIY_REPLACEMENT = "DIY_REPLACEMENT"
    PREMISE_REJECTION = "PREMISE_REJECTION"
    EXCUSE_STRIPPING = "EXCUSE_STRIPPING"
    NECESSITY_APPROVAL = "NECESSITY_APPROVAL"
    REPEAT_OFFENSE = "REPEAT_OFFENSE"
    ROOM_RULE_CALLBACK = "ROOM_RULE_CALLBACK"


class MemeTag(StrEnum):
    GUILTY_HEAVY = "GUILTY_HEAVY"
    GUILTY_LIGHT = "GUILTY_LIGHT"
    NOT_GUILTY = "NOT_GUILTY"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


# `meme_hints.emotion` 어휘(08 §3.4, 9/8 확정). 백엔드 짤 점수 규칙이 이 값으로 대조한다.
# docstring 을 두지 않는다 — 생성 스키마에 description 으로 들어간다.
class MemeEmotion(StrEnum):
    DISAPPROVAL = "DISAPPROVAL"
    ABSURD_SERIOUSNESS = "ABSURD_SERIOUSNESS"
    SMUG = "SMUG"
    PITY = "PITY"
    CELEBRATION = "CELEBRATION"
    RESIGNATION = "RESIGNATION"


class Statement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: Annotated[str, Field(min_length=1, max_length=STATEMENT_TOTAL_MAX)]
    kind: StatementKind
    evidence_labels: Annotated[list[Annotated[str, Field(pattern=r"^F\d+$")]], Field(max_length=16)]


class MemeHints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    emotion: MemeEmotion
    keywords: Annotated[list[Annotated[str, Field(max_length=30)]], Field(max_length=10)]


class TextDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intensity: Intensity
    headline: Annotated[str, Field(max_length=HEADLINE_MAX)]
    statement: Annotated[list[Statement], Field(min_length=STATEMENT_MIN, max_length=STATEMENT_MAX)]
    banter_strategy: BanterStrategy
    selected_candidate_id: str | None
    attack_angle: AttackAngle
    source: TextSource

    @model_validator(mode="after")
    def _statement_total_length(self) -> TextDraft:
        total = sum(len(s.text) for s in self.statement)
        if total > STATEMENT_TOTAL_MAX:
            raise ValueError(f"statement 합산 {total}자 > {STATEMENT_TOTAL_MAX}자")
        return self


class CardStatement(Statement):
    text: Annotated[
        str, Field(min_length=1, max_length=CARD_STATEMENT_MAX, pattern=_CARD_LINE_PATTERN)
    ]

    _single_line = field_validator("text")(_validate_card_line)


class CardTextDraft(TextDraft):
    headline: Annotated[
        str, Field(min_length=1, max_length=CARD_HEADLINE_MAX, pattern=_CARD_LINE_PATTERN)
    ]
    statement: Annotated[list[CardStatement], Field(min_length=1, max_length=1)]

    _single_line = field_validator("headline")(_validate_card_line)


class WriterDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    texts: Annotated[list[TextDraft], Field(min_length=1, max_length=3)]
    meme_tag: MemeTag
    meme_hints: MemeHints | None

    @model_validator(mode="after")
    def _no_duplicate_intensity(self) -> WriterDraft:
        seen = [t.intensity for t in self.texts]
        if len(set(seen)) != len(seen):
            raise ValueError("texts 에 같은 intensity 가 둘 이상 있다")
        return self
