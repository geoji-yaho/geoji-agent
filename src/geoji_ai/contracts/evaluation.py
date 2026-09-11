"""검수관 계약 미러 — `contracts/evaluation-v1.schema.json` 의 pydantic 판."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool

from geoji_ai.domain.intensity import Intensity

PolicyVersion = Literal["guardrail-v1", "guardrail-v2"]


class ViolationCode(StrEnum):
    PERSONAL_ATTACK = "PERSONAL_ATTACK"
    IDENTITY_DEGRADATION = "IDENTITY_DEGRADATION"
    SELF_HARM_LEXICON = "SELF_HARM_LEXICON"
    UNGROUNDED_CLAIM = "UNGROUNDED_CLAIM"
    VERDICT_CONTRADICTION = "VERDICT_CONTRADICTION"
    INJECTION_FOLLOWED = "INJECTION_FOLLOWED"
    UNSAFE_CONTENT = "UNSAFE_CONTENT"
    INTENSITY_MISMATCH = "INTENSITY_MISMATCH"
    PROFANITY_OUT_OF_LIST = "PROFANITY_OUT_OF_LIST"
    SENTENCE_REASON_MISMATCH = "SENTENCE_REASON_MISMATCH"
    SCHEMA_INVALID = "SCHEMA_INVALID"


class Violation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: ViolationCode
    path: str
    evidence_labels: list[str]
    explanation: Annotated[str, Field(max_length=300)]


class CheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    pass_: StrictBool = Field(alias="pass")
    violations: list[Violation]


class TextEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    intensity: Intensity
    pass_: StrictBool = Field(alias="pass")
    violations: list[Violation]
    problem_sentences: list[str]


class EvaluationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    policy_version: PolicyVersion
    sentence_check: CheckResult
    sentencing_reason_check: CheckResult
    texts: list[TextEvaluation]
