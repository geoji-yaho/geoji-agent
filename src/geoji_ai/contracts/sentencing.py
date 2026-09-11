"""양형관 계약 미러 — `contracts/sentencing-v1.schema.json` 의 pydantic 판."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

ReasonSource = Literal["AI", "TEMPLATE"]


class SentencingDecision(BaseModel):
    """`sentence` 는 문자열이다. 허용 목록 대조는 코드가 동적으로 한다(01 §3.2)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    sentence: str
    sentencing_reason: Annotated[str, Field(max_length=100)] | None
    reason_source: ReasonSource
    evidence_labels: Annotated[list[Annotated[str, Field(pattern=r"^F\d+$")]], Field(max_length=16)]
    aggravating: Annotated[list[Annotated[str, Field(max_length=100)]], Field(max_length=20)]
    mitigating: Annotated[list[Annotated[str, Field(max_length=100)]], Field(max_length=20)]
