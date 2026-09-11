"""서기 초안 계약 미러 — `contracts/writer-draft-v1.schema.json` 의 pydantic 판."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from geoji_ai.domain.attack_angles import AttackAngle
from geoji_ai.domain.intensity import Intensity

# 길이 상한의 단일 정의. `domain/validation.py` 가 같은 값을 import 한다.
HEADLINE_MAX = 30
STATEMENT_MIN = 2
STATEMENT_MAX = 4
STATEMENT_TOTAL_MAX = 300

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


class Statement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    kind: StatementKind
    evidence_labels: list[str]


class MemeHints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    emotion: str
    keywords: list[str]


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


class WriterDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    texts: list[TextDraft]
    meme_tag: MemeTag
    meme_hints: MemeHints | None

    @model_validator(mode="after")
    def _no_duplicate_intensity(self) -> WriterDraft:
        seen = [t.intensity for t in self.texts]
        if len(set(seen)) != len(seen):
            raise ValueError("texts 에 같은 intensity 가 둘 이상 있다")
        return self
