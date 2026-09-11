"""커밋 요청 계약 미러 — `contracts/finalize-v1.schema.json` 의 pydantic 판(10 §5)."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, model_validator

from geoji_ai.contracts.case import PrivacyVersion
from geoji_ai.contracts.evaluation import EvaluationReport, PolicyVersion
from geoji_ai.contracts.sentencing import SentencingDecision
from geoji_ai.contracts.writer import WriterDraft

#: canonical draft 의 sha256 hex(01 §3.2 `draft_hash`).
SHA256_HEX = r"^[0-9a-f]{64}$"


class ModelIds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sentencing: str
    writer: str
    evaluator: str


class FinalizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    job_id: str
    generation_id: str
    verdict_version: Annotated[int, Field(ge=1)]
    expected_text_version: Annotated[int, Field(ge=0)]
    dossier_id: str
    privacy_versions: Annotated[list[PrivacyVersion], Field(max_length=100)]
    draft_hash: Annotated[str, Field(pattern=SHA256_HEX)]
    sentencing: SentencingDecision | None
    draft: WriterDraft
    evaluation: EvaluationReport
    evaluation_draft_hash: Annotated[str, Field(pattern=SHA256_HEX)]
    prompt_bundle_version: str
    guardrail_policy_version: PolicyVersion
    model_ids: ModelIds

    @model_validator(mode="after")
    def _policy_version_matches_settings(self, info: ValidationInfo) -> FinalizeRequest:
        context: Any = info.context
        if isinstance(context, dict) and "guardrail_policy_version" in context:
            check_policy_version(self, context["guardrail_policy_version"])
        return self


def check_policy_version(req: FinalizeRequest, configured: str) -> None:
    """설정된 정책 버전과 다르면 거부한다(거부 케이스 ⑧)."""
    if req.guardrail_policy_version != configured:
        raise ValueError(
            f"guardrail_policy_version {req.guardrail_policy_version} != 설정 {configured}"
        )
