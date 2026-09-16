"""거부 케이스 ①~⑨(01 §4.2, 명세 대조 "테스트 케이스" 표).

케이스 ②·④·⑤ 는 JSON Schema 로 잡을 수 없는 구조 규칙이라 `domain.validation` 이 본다.
케이스 ⑧ 은 설정값과의 대조라 `core.config.Settings` 를 쓴다.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from geoji_ai.contracts.evaluation import EvaluationReport
from geoji_ai.contracts.finalize import FinalizeRequest, check_policy_version
from geoji_ai.contracts.intake import IntakeResult
from geoji_ai.contracts.writer import WriterDraft
from geoji_ai.core.config import Settings
from geoji_ai.domain.validation import validate_evaluation, validate_writer_draft
from tests.conftest import load_fixture, load_schema

ALL_INTENSITY_VALUES = ("mild", "spicy", "hell")
LABEL_MAP = {f"F{i}": f"evidence-{i}" for i in range(7)}


def writer_draft() -> dict[str, Any]:
    return copy.deepcopy(load_fixture("writer-draft-taxi"))


def text_of(draft: dict[str, Any], intensity: str) -> dict[str, Any]:
    return next(t for t in draft["texts"] if t["intensity"] == intensity)


def evaluation_report(intensities: tuple[str, ...] = ALL_INTENSITY_VALUES) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "policy_version": "guardrail-v2",
        "sentence_check": {"pass": True, "violations": []},
        "sentencing_reason_check": {"pass": True, "violations": []},
        "texts": [
            {
                "intensity": intensity,
                "pass": True,
                "violations": [],
                "problem_sentences": [],
            }
            for intensity in intensities
        ],
    }


def finalize_request() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "job_id": "5f8c1a20-9b47-4e6d-8a31-2c7f0b5d9e13",
        "generation_id": "9e2b7d41-0c86-4f35-9b18-6a4d3e7c1f52",
        "verdict_version": 1,
        "expected_text_version": 0,
        "dossier_id": "0b9e4c31-7d26-4f58-8a10-3c5b7e9d2a64",
        "privacy_versions": [{"scope_key": "user:user-01H8Z9QK", "epoch": 1}],
        "draft_hash": "a" * 64,
        "sentencing": {
            "schema_version": 1,
            "sentence": "oneDay",
            "sentencing_reason": (
                "유죄율 75%로 실형 범위이나, 예산 소진율이 41%라 무기징역까지는 가지 않는다."
            ),
            "reason_source": "AI",
            "evidence_labels": ["F1", "F5"],
            "aggravating": [],
            "mitigating": [],
        },
        "draft": writer_draft(),
        "evaluation": evaluation_report(),
        "evaluation_draft_hash": "a" * 64,
        "prompt_bundle_version": "bundle-v1",
        "guardrail_policy_version": "guardrail-v2",
        "model_ids": {
            "sentencing": "gpt-5.6-luna",
            "writer": "grok-4.20-0309-non-reasoning",
            "evaluator": "gpt-5.6-luna",
        },
    }


def validator(schema_name: str, defs_name: str) -> Draft202012Validator:
    document = load_schema(schema_name)
    sub = {
        "$schema": document["$schema"],
        "$id": document["$id"],
        "$defs": document["$defs"],
        "$ref": f"#/$defs/{defs_name}",
    }
    return Draft202012Validator(sub)


def test_valid_base_fixtures_pass() -> None:
    """거부 케이스의 출발점이 실제로 통과하는지 먼저 본다."""
    WriterDraft.model_validate(writer_draft())
    EvaluationReport.model_validate(evaluation_report())
    FinalizeRequest.model_validate(finalize_request())


# ① 알 수 없는 필드
def test_case_1_unknown_field_rejected() -> None:
    draft = writer_draft()
    draft["foo"] = "bar"

    with pytest.raises(ValidationError):
        WriterDraft.model_validate(draft)

    assert not validator("writer-draft", "WriterDraft").is_valid(draft)


# ② target 3종인데 hell 이 없다
def test_case_2_missing_hell_rejected() -> None:
    draft = writer_draft()
    draft["texts"] = [t for t in draft["texts"] if t["intensity"] != "hell"]

    issues = validate_writer_draft(draft, ALL_INTENSITY_VALUES, LABEL_MAP)

    assert issues, "hell 이 빠진 초안은 거부되어야 한다"


# ③ 같은 intensity 중복
def test_case_3_duplicate_intensity_rejected() -> None:
    draft = writer_draft()
    draft["texts"].append(copy.deepcopy(text_of(draft, "spicy")))

    with pytest.raises(ValidationError):
        WriterDraft.model_validate(draft)

    assert validate_writer_draft(draft, ALL_INTENSITY_VALUES, LABEL_MAP)


# ④ label_map 에 없는 Evidence 라벨
def test_case_4_unknown_evidence_label_rejected() -> None:
    draft = writer_draft()
    text_of(draft, "spicy")["statement"][0]["evidence_labels"] = ["F9"]

    issues = validate_writer_draft(draft, ALL_INTENSITY_VALUES, LABEL_MAP)

    assert issues, "label_map(F0~F6) 밖의 F9 는 거부되어야 한다"


# ⑤ EvaluationReport 에 강도 하나가 빠졌다
def test_case_5_evaluation_missing_intensity_rejected() -> None:
    report = evaluation_report(("mild", "spicy"))

    issues = validate_evaluation(report, ALL_INTENSITY_VALUES, "guardrail-v2")

    assert issues, "강도가 빠진 검수 결과는 거부되어야 한다"


# ⑥ sentence_check 누락
def test_case_6_missing_sentence_check_rejected() -> None:
    report = evaluation_report()
    del report["sentence_check"]

    with pytest.raises(ValidationError):
        EvaluationReport.model_validate(report)

    assert not validator("evaluation", "EvaluationReport").is_valid(report)


# ⑦ pass 가 불리언이 아니다
def test_case_7_pass_not_boolean_rejected() -> None:
    report = evaluation_report()
    report["sentence_check"]["pass"] = "yes"

    with pytest.raises(ValidationError):
        EvaluationReport.model_validate(report)

    assert not validator("evaluation", "EvaluationReport").is_valid(report)


# ⑧ 설정은 guardrail-v2 인데 요청이 guardrail-v1
def test_case_8_policy_version_mismatch_rejected() -> None:
    settings = Settings(_env_file=None)
    assert settings.GUARDRAIL_POLICY_VERSION == "guardrail-v2"

    payload = finalize_request()
    payload["guardrail_policy_version"] = "guardrail-v1"

    with pytest.raises(ValidationError):
        FinalizeRequest.model_validate(
            payload,
            context={"guardrail_policy_version": settings.GUARDRAIL_POLICY_VERSION},
        )

    request = FinalizeRequest.model_validate(payload)
    with pytest.raises(ValueError):
        check_policy_version(request, settings.GUARDRAIL_POLICY_VERSION)


# ⑨ FINAL_CHECK 는 NEEDS_CLARIFICATION 을 낼 수 없다
def test_case_9_final_check_needs_clarification_rejected() -> None:
    payload = {
        "schema_version": 1,
        "mode": "FINAL_CHECK",
        "status": "NEEDS_CLARIFICATION",
        "item_review": {"status": "VAGUE", "suggested_item": "택시"},
        "message": None,
        "category_review": {
            "status": "OK",
            "suggested_category": None,
            "confidence": 0.9,
        },
        "injection_detected": False,
        "intake_source": "AI",
    }

    with pytest.raises(ValidationError):
        IntakeResult.model_validate(payload)

    assert not validator("intake", "IntakeResult").is_valid(payload)

    payload["mode"] = "INITIAL"
    IntakeResult.model_validate(payload)
    assert validator("intake", "IntakeResult").is_valid(payload)
