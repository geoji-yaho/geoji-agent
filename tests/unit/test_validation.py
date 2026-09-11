"""구조 검증 2함수(01 §3.5, 스펙 테스트 표).

`validate_writer_draft` 5규칙(강도 집합 일치·중복·길이·라벨·문장 수 2~4·`kind`),
`validate_evaluation` 4규칙(강도 완전성·검사 필드·`pass` 불리언·정책 버전),
그리고 `false`·누락·파싱 실패는 모두 검수 실패.
"""

from __future__ import annotations

from typing import Any

import pytest

from geoji_ai.contracts.writer import WriterDraft
from geoji_ai.domain.validation import (
    CHECK_FAILED,
    DUPLICATE_INTENSITY,
    INTENSITY_SET_MISMATCH,
    INVALID_KIND,
    LENGTH_EXCEEDED,
    MISSING_CHECK,
    MISSING_INTENSITY,
    PASS_NOT_BOOLEAN,
    POLICY_VERSION_MISMATCH,
    SCHEMA_INVALID,
    STATEMENT_COUNT,
    UNKNOWN_EVIDENCE_LABEL,
    validate_evaluation,
    validate_writer_draft,
)

ALL_THREE = ("mild", "spicy", "hell")
LABEL_MAP = {f"F{n}": f"evidence-{n}" for n in range(7)}


def _statement(text: str = "지하철 1,400원 기준 8회분이다.", **over: Any) -> dict[str, Any]:
    return {"text": text, "kind": "fact", "evidence_labels": ["F0"], **over}


def _text(intensity: str, **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "intensity": intensity,
        "headline": "택시비 12,000원",
        "statement": [_statement(), _statement("이번 달 4회째입니다.", evidence_labels=["F4"])],
        "banter_strategy": "REPEAT_OFFENSE",
        "selected_candidate_id": None,
        "attack_angle": "CONVERSION",
        "source": "AI",
    }
    base.update(over)
    return base


def _draft(intensities: tuple[str, ...] = ALL_THREE, **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": 1,
        "texts": [_text(value) for value in intensities],
        "meme_tag": "GUILTY_LIGHT",
        "meme_hints": {"emotion": "DISAPPROVAL", "keywords": ["택시", "늦잠"]},
    }
    base.update(over)
    return base


def _codes(issues: list[Any]) -> set[str]:
    return {issue.code for issue in issues}


# --- validate_writer_draft ---------------------------------------------------


def test_정상_초안은_이슈가_없다():
    assert validate_writer_draft(_draft(), ALL_THREE, LABEL_MAP) == []


def test_모델_객체도_받는다():
    draft = WriterDraft.model_validate(_draft())
    assert validate_writer_draft(draft, ALL_THREE, LABEL_MAP) == []


def test_강도_집합이_다르면_거부():
    # hell 누락(target 3개)
    issues = validate_writer_draft(_draft(("mild", "spicy")), ALL_THREE, LABEL_MAP)
    assert INTENSITY_SET_MISMATCH in _codes(issues)


def test_대상에_없는_강도가_있어도_거부():
    issues = validate_writer_draft(_draft(("mild", "spicy", "hell")), ("mild", "spicy"), LABEL_MAP)
    assert INTENSITY_SET_MISMATCH in _codes(issues)


def test_같은_강도가_두_번이면_거부():
    issues = validate_writer_draft(_draft(("mild", "spicy", "spicy")), ALL_THREE, LABEL_MAP)
    assert DUPLICATE_INTENSITY in _codes(issues)


def test_headline_이_30자를_넘으면_거부():
    draft = _draft()
    draft["texts"][0]["headline"] = "택" * 31
    issues = validate_writer_draft(draft, ALL_THREE, LABEL_MAP)
    assert LENGTH_EXCEEDED in _codes(issues)


def test_문장_합산이_300자를_넘으면_거부():
    draft = _draft()
    draft["texts"][2]["statement"] = [_statement("가" * 160), _statement("나" * 160)]
    issues = validate_writer_draft(draft, ALL_THREE, LABEL_MAP)
    assert LENGTH_EXCEEDED in _codes(issues)


def test_label_map_에_없는_라벨은_거부():
    draft = _draft()
    draft["texts"][1]["statement"][0]["evidence_labels"] = ["F9"]
    issues = validate_writer_draft(draft, ALL_THREE, LABEL_MAP)
    assert UNKNOWN_EVIDENCE_LABEL in _codes(issues)


@pytest.mark.parametrize("count", [0, 1, 5])
def test_문장_수는_2에서_4여야_한다(count: int):
    draft = _draft()
    draft["texts"][0]["statement"] = [_statement() for _ in range(count)]
    issues = validate_writer_draft(draft, ALL_THREE, LABEL_MAP)
    assert STATEMENT_COUNT in _codes(issues)


@pytest.mark.parametrize("count", [2, 3, 4])
def test_문장_수_2에서_4는_통과(count: int):
    draft = _draft()
    draft["texts"][0]["statement"] = [_statement() for _ in range(count)]
    assert validate_writer_draft(draft, ALL_THREE, LABEL_MAP) == []


def test_알_수_없는_kind_는_거부():
    draft = _draft()
    draft["texts"][0]["statement"][0]["kind"] = "guess"
    issues = validate_writer_draft(draft, ALL_THREE, LABEL_MAP)
    assert INVALID_KIND in _codes(issues)


def test_초안이_없거나_파싱_실패면_검수_실패():
    assert _codes(validate_writer_draft(None, ALL_THREE, LABEL_MAP)) == {SCHEMA_INVALID}
    assert _codes(validate_writer_draft("초안", ALL_THREE, LABEL_MAP)) == {SCHEMA_INVALID}
    broken = validate_writer_draft({"schema_version": 1}, ALL_THREE, LABEL_MAP)
    assert broken != []
    assert SCHEMA_INVALID in _codes(broken)


def test_계약에_없는_필드가_있으면_검수_실패():
    issues = validate_writer_draft(_draft(foo="bar"), ALL_THREE, LABEL_MAP)
    assert SCHEMA_INVALID in _codes(issues)


# --- validate_evaluation -----------------------------------------------------


def _check(passed: bool = True) -> dict[str, Any]:
    return {"pass": passed, "violations": []}


def _report(intensities: tuple[str, ...] = ALL_THREE, **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": 1,
        "policy_version": "guardrail-v2",
        "sentence_check": _check(),
        "sentencing_reason_check": _check(),
        "texts": [
            {"intensity": value, "pass": True, "violations": [], "problem_sentences": []}
            for value in intensities
        ],
    }
    base.update(over)
    return base


def test_정상_보고서는_이슈가_없다():
    assert validate_evaluation(_report(), ALL_THREE, "guardrail-v2") == []


def test_강도가_하나_빠지면_거부():
    issues = validate_evaluation(_report(("mild", "spicy")), ALL_THREE, "guardrail-v2")
    assert MISSING_INTENSITY in _codes(issues)


def test_초안에_없는_강도가_있으면_거부():
    issues = validate_evaluation(_report(ALL_THREE), ("mild", "spicy"), "guardrail-v2")
    assert INTENSITY_SET_MISMATCH in _codes(issues)


@pytest.mark.parametrize("name", ["sentence_check", "sentencing_reason_check"])
def test_검사_필드가_없으면_거부(name: str):
    report = _report()
    del report[name]
    issues = validate_evaluation(report, ALL_THREE, "guardrail-v2")
    assert MISSING_CHECK in _codes(issues)


def test_pass_가_문자열이면_거부():
    report = _report()
    report["sentence_check"]["pass"] = "yes"
    issues = validate_evaluation(report, ALL_THREE, "guardrail-v2")
    assert PASS_NOT_BOOLEAN in _codes(issues)


def test_정책_버전이_다르면_거부():
    issues = validate_evaluation(_report(), ALL_THREE, "guardrail-v1")
    assert POLICY_VERSION_MISMATCH in _codes(issues)


def test_pass_가_false_면_검수_실패():
    report = _report()
    report["texts"][2]["pass"] = False
    issues = validate_evaluation(report, ALL_THREE, "guardrail-v2")
    assert CHECK_FAILED in _codes(issues)

    report = _report()
    report["sentencing_reason_check"]["pass"] = False
    assert CHECK_FAILED in _codes(validate_evaluation(report, ALL_THREE, "guardrail-v2"))


def test_보고서가_없거나_파싱_실패면_검수_실패():
    assert _codes(validate_evaluation(None, ALL_THREE, "guardrail-v2")) == {SCHEMA_INVALID}
    broken = validate_evaluation({"policy_version": "guardrail-v2"}, ALL_THREE, "guardrail-v2")
    assert broken != []
    assert SCHEMA_INVALID in _codes(broken)
