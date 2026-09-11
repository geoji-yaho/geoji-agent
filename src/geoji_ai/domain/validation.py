"""초안·검수 보고서의 **구조** 검증(01 §3.5).

텍스트 규칙(비속어 개수·닳은 문구·반말 혼입 …)은 작업 5 다. 여기는 계약이 정한
모양을 지켰는지만 본다.

`false`·누락·파싱 실패는 모두 검수 실패다. 빈 리스트를 돌려주는 경우 외에는
모두 실패로 읽으면 된다.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, get_args

from pydantic import ValidationError

from geoji_ai.contracts.evaluation import EvaluationReport
from geoji_ai.contracts.writer import (
    HEADLINE_MAX,
    STATEMENT_MAX,
    STATEMENT_MIN,
    STATEMENT_TOTAL_MAX,
    StatementKind,
    WriterDraft,
)
from geoji_ai.domain.intensity import Intensity, parse_intensity

__all__ = [
    "ValidationIssue",
    "validate_evaluation",
    "validate_writer_draft",
]

# 이슈 코드. 검수관 `Violation.code` 와 다른 축이다(이쪽은 구조, 저쪽은 내용).
SCHEMA_INVALID = "SCHEMA_INVALID"
INTENSITY_SET_MISMATCH = "INTENSITY_SET_MISMATCH"
DUPLICATE_INTENSITY = "DUPLICATE_INTENSITY"
LENGTH_EXCEEDED = "LENGTH_EXCEEDED"
UNKNOWN_EVIDENCE_LABEL = "UNKNOWN_EVIDENCE_LABEL"
STATEMENT_COUNT = "STATEMENT_COUNT"
INVALID_KIND = "INVALID_KIND"
MISSING_INTENSITY = "MISSING_INTENSITY"
MISSING_CHECK = "MISSING_CHECK"
PASS_NOT_BOOLEAN = "PASS_NOT_BOOLEAN"
POLICY_VERSION_MISMATCH = "POLICY_VERSION_MISMATCH"
CHECK_FAILED = "CHECK_FAILED"

# 상한·kind 는 계약 미러(`contracts/writer.py`)의 값을 그대로 쓴다(단일 정의).
STATEMENT_TEXT_TOTAL_MAX = STATEMENT_TOTAL_MAX
STATEMENT_KINDS: tuple[str, ...] = get_args(StatementKind)
EVALUATION_CHECKS = ("sentence_check", "sentencing_reason_check")


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    path: str
    message: str


def _normalize(
    obj: Any, model_cls: type, issues: list[ValidationIssue]
) -> Mapping[str, Any] | None:
    """모델·매핑을 평범한 dict 로 편다.

    매핑이면 `model_validate` 로 한 번 파싱해 보고(실패는 그 자체로 검수 실패다)
    **원본 매핑을 그대로** 돌려준다. 파싱 결과를 쓰지 않는 이유는 pydantic 이
    느슨한 모드에서 값을 고쳐 주기 때문이다. `pass: "yes"` 가 `True` 로 바뀌면
    "`pass` 는 불리언" 규칙이 볼 것이 없어진다(거부 케이스 ⑦).
    """
    if obj is None:
        issues.append(ValidationIssue(SCHEMA_INVALID, "", "값이 없다"))
        return None
    if isinstance(obj, model_cls):
        return obj.model_dump(by_alias=True, mode="json")
    if isinstance(obj, Mapping):
        try:
            model_cls.model_validate(obj)
        except ValidationError as exc:
            issues.append(
                ValidationIssue(SCHEMA_INVALID, "", f"계약 파싱 실패 {exc.error_count()}건")
            )
        return obj
    issues.append(ValidationIssue(SCHEMA_INVALID, "", f"다룰 수 없는 타입 {type(obj).__name__}"))
    return None


def _intensity_key(value: Any) -> str:
    """비교용 강도 키. 모르는 값은 문자열 그대로 둔다(집합 비교에서 걸린다)."""
    try:
        return str(parse_intensity(value))
    except (ValueError, TypeError):
        return str(value)


def _expected_keys(intensities: Iterable[Intensity | str]) -> list[str]:
    return [_intensity_key(value) for value in intensities]


def validate_writer_draft(
    draft: WriterDraft | Mapping[str, Any] | None,
    target_intensities: Iterable[Intensity | str],
    label_map: Mapping[str, str],
) -> list[ValidationIssue]:
    """서기 초안의 구조 규칙. 빈 리스트면 통과.

    강도 집합 정확히 일치 · 중복 없음 · 길이 · 라벨 ∈ `label_map` · 문장 수 2~4 · `kind`.
    """
    issues: list[ValidationIssue] = []
    data = _normalize(draft, WriterDraft, issues)
    if data is None:
        return issues

    texts = data.get("texts")
    if not isinstance(texts, list):
        issues.append(ValidationIssue(SCHEMA_INVALID, "texts", "texts 가 배열이 아니다"))
        return issues

    expected = _expected_keys(target_intensities)
    seen: list[str] = []
    for index, text in enumerate(texts):
        path = f"texts[{index}]"
        if not isinstance(text, Mapping):
            issues.append(ValidationIssue(SCHEMA_INVALID, path, "초안 항목이 객체가 아니다"))
            continue
        key = _intensity_key(text.get("intensity"))
        if key in seen:
            issues.append(
                ValidationIssue(DUPLICATE_INTENSITY, f"{path}.intensity", f"강도 중복 {key}")
            )
        seen.append(key)
        issues.extend(_check_text(text, path, label_map))

    if sorted(set(seen)) != sorted(set(expected)):
        issues.append(
            ValidationIssue(
                INTENSITY_SET_MISMATCH,
                "texts",
                f"강도 집합이 다르다. 기대 {sorted(set(expected))}, 실제 {sorted(set(seen))}",
            )
        )
    return issues


def _check_text(
    text: Mapping[str, Any], path: str, label_map: Mapping[str, str]
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    headline = text.get("headline")
    if not isinstance(headline, str):
        issues.append(
            ValidationIssue(SCHEMA_INVALID, f"{path}.headline", "headline 이 문자열이 아니다")
        )
    elif len(headline) > HEADLINE_MAX:
        issues.append(
            ValidationIssue(
                LENGTH_EXCEEDED,
                f"{path}.headline",
                f"headline {len(headline)}자 > {HEADLINE_MAX}",
            )
        )

    statement = text.get("statement")
    if not isinstance(statement, list):
        issues.append(
            ValidationIssue(SCHEMA_INVALID, f"{path}.statement", "statement 가 배열이 아니다")
        )
        return issues

    if not STATEMENT_MIN <= len(statement) <= STATEMENT_MAX:
        issues.append(
            ValidationIssue(
                STATEMENT_COUNT,
                f"{path}.statement",
                f"문장 {len(statement)}개. {STATEMENT_MIN}~{STATEMENT_MAX} 여야 한다",
            )
        )

    total = 0
    for index, item in enumerate(statement):
        item_path = f"{path}.statement[{index}]"
        if not isinstance(item, Mapping):
            issues.append(ValidationIssue(SCHEMA_INVALID, item_path, "문장 항목이 객체가 아니다"))
            continue
        item_text = item.get("text")
        if isinstance(item_text, str):
            total += len(item_text)
        else:
            issues.append(
                ValidationIssue(SCHEMA_INVALID, f"{item_path}.text", "text 가 문자열이 아니다")
            )
        kind = item.get("kind")
        if kind not in STATEMENT_KINDS:
            issues.append(
                ValidationIssue(INVALID_KIND, f"{item_path}.kind", f"알 수 없는 kind {kind!r}")
            )
        labels = item.get("evidence_labels")
        if not isinstance(labels, list):
            issues.append(
                ValidationIssue(
                    SCHEMA_INVALID, f"{item_path}.evidence_labels", "라벨이 배열이 아니다"
                )
            )
            continue
        for label in labels:
            if label not in label_map:
                issues.append(
                    ValidationIssue(
                        UNKNOWN_EVIDENCE_LABEL,
                        f"{item_path}.evidence_labels",
                        f"label_map 에 없는 라벨 {label!r}",
                    )
                )

    if total > STATEMENT_TEXT_TOTAL_MAX:
        issues.append(
            ValidationIssue(
                LENGTH_EXCEEDED,
                f"{path}.statement",
                f"문장 합산 {total}자 > {STATEMENT_TEXT_TOTAL_MAX}",
            )
        )
    return issues


def validate_evaluation(
    report: EvaluationReport | Mapping[str, Any] | None,
    intensities: Iterable[Intensity | str],
    policy_version: str,
) -> list[ValidationIssue]:
    """검수 보고서의 구조 규칙. 빈 리스트면 통과.

    강도 완전성 · 검사 필드 완전성 · `pass` 불리언 · 정책 버전 일치.
    `pass` 가 `false` 인 검사도 검수 실패로 이슈를 남긴다.
    """
    issues: list[ValidationIssue] = []
    data = _normalize(report, EvaluationReport, issues)
    if data is None:
        return issues

    if data.get("policy_version") != policy_version:
        issues.append(
            ValidationIssue(
                POLICY_VERSION_MISMATCH,
                "policy_version",
                "정책 버전이 다르다. "
                f"설정 {policy_version!r}, 보고서 {data.get('policy_version')!r}",
            )
        )

    for name in EVALUATION_CHECKS:
        check = data.get(name)
        if not isinstance(check, Mapping):
            issues.append(ValidationIssue(MISSING_CHECK, name, f"{name} 가 없다"))
            continue
        issues.extend(_check_pass(check, name))

    texts = data.get("texts")
    if not isinstance(texts, list):
        issues.append(ValidationIssue(MISSING_CHECK, "texts", "texts 가 배열이 아니다"))
        return issues

    expected = _expected_keys(intensities)
    seen: list[str] = []
    for index, text in enumerate(texts):
        path = f"texts[{index}]"
        if not isinstance(text, Mapping):
            issues.append(ValidationIssue(SCHEMA_INVALID, path, "검수 항목이 객체가 아니다"))
            continue
        key = _intensity_key(text.get("intensity"))
        if key in seen:
            issues.append(
                ValidationIssue(DUPLICATE_INTENSITY, f"{path}.intensity", f"강도 중복 {key}")
            )
        seen.append(key)
        issues.extend(_check_pass(text, path))

    for key in expected:
        if key not in seen:
            issues.append(
                ValidationIssue(MISSING_INTENSITY, "texts", f"검수 결과가 없는 강도 {key}")
            )
    for key in seen:
        if key not in expected:
            issues.append(
                ValidationIssue(INTENSITY_SET_MISMATCH, "texts", f"초안에 없는 강도 {key}")
            )
    return issues


def _check_pass(node: Mapping[str, Any], path: str) -> list[ValidationIssue]:
    """`pass` 가 불리언인지, 그리고 통과인지."""
    if "pass" not in node:
        return [ValidationIssue(MISSING_CHECK, f"{path}.pass", "pass 가 없다")]
    value = node["pass"]
    if not isinstance(value, bool):
        return [
            ValidationIssue(PASS_NOT_BOOLEAN, f"{path}.pass", f"pass 가 불리언이 아니다 {value!r}")
        ]
    if value is False:
        return [ValidationIssue(CHECK_FAILED, f"{path}.pass", "검수 실패")]
    return []
