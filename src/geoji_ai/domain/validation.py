"""초안·검수 보고서의 **구조** 검증(01 §3.5)과 서버 검증 ⑤ 텍스트 규칙(05 §3.4).

`validate_writer_draft`·`validate_evaluation` 은 계약이 정한 모양을 지켰는지만 본다.
`false`·누락·파싱 실패는 모두 검수 실패다. 빈 리스트를 돌려주는 경우 외에는
모두 실패로 읽으면 된다.

`apply_text_rules` 는 05 §3.4 텍스트 규칙 7항이다. 근거 없는 문장·ID 문자열은 **고치고**
(삭제 후 재조립), `meme_tag` 는 교정하며, 나머지는 이슈로 돌려준다. 검수관(⑥) 앞에서만 돈다.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, get_args

from pydantic import BaseModel, ValidationError

from geoji_ai.contracts.evaluation import EvaluationReport
from geoji_ai.contracts.writer import (
    HEADLINE_MAX,
    STATEMENT_MAX,
    STATEMENT_MIN,
    STATEMENT_TOTAL_MAX,
    MemeTag,
    StatementKind,
    WriterDraft,
)
from geoji_ai.domain import lexicon
from geoji_ai.domain.intensity import Intensity, parse_intensity

__all__ = [
    "EvidenceRef",
    "TextRuleResult",
    "ValidationIssue",
    "apply_text_rules",
    "is_banmal_suspect",
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


# ---------------------------------------------------------------------------
# 서버 검증 ⑤ 텍스트 규칙(05 §3.4)
# ---------------------------------------------------------------------------

# 텍스트 규칙 이슈 코드. 값은 검수관 `ViolationCode` 와 같은 문자열이다.
UNGROUNDED_CLAIM = "UNGROUNDED_CLAIM"
PROFANITY_OUT_OF_LIST = "PROFANITY_OUT_OF_LIST"
SELF_HARM_LEXICON = "SELF_HARM_LEXICON"
INTENSITY_MISMATCH = "INTENSITY_MISMATCH"

#: 근거 라벨이 있어야 하는 문장 kind(규칙 1).
GROUNDED_KINDS: tuple[str, ...] = ("fact", "claim")

#: 규칙 2 에서 ID 만 지울 때 쓰는 패턴. `lexicon.ID_IN_TEXT` 에 "F1, F2" 나열과 앞뒤 공백을 붙인다.
_ID_WITH_LIST = re.compile(r"\s*" + lexicon.ID_IN_TEXT.pattern + r"(?:\s*,\s*F\d+)*\s*")
_SPACES = re.compile(r"\s{2,}")

#: 이모지 판정. 실측 스크립트 `validate` 의 규칙(코드포인트 > U+1F000) 그대로.
EMOJI_CODEPOINT_FLOOR = 0x1F000
REPLACEMENT_CHARACTER = "�"

#: mild 반말 휴리스틱(코디네이터 해석). 존댓말 어미가 먼저다.
#: `-습니다` 는 `-ㅂ니다`(합니다·입니다)와 같은 어미라 `니다` 로 본다.
POLITE_ENDINGS: tuple[str, ...] = ("니다", "세요", "요")
BANMAL_ENDINGS: tuple[str, ...] = ("다", "냐", "해", "지")
_SENTENCE_TRAILER = " \t\r\n.!?…'\"’”)~"
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+")

_MEME_BY_RESULT: dict[str, MemeTag] = {
    "notGuilty": MemeTag.NOT_GUILTY,
    "agree": MemeTag.APPROVED,
    "disagree": MemeTag.REJECTED,
}


@dataclass(frozen=True)
class EvidenceRef:
    """`text_evidence_refs` 후보 한 줄(10 §2 `ai.text_evidence_refs`).

    `field_path` 는 재조립 뒤 그 강도 텍스트 안의 위치(`statement[j]`).
    """

    intensity: str
    field_path: str
    label: str
    evidence_id: str


@dataclass(frozen=True)
class TextRuleResult:
    """`draft` 는 규칙 1·2 삭제와 규칙 4 교정을 반영한 새 dict. 입력은 바꾸지 않는다."""

    draft: dict[str, Any]
    issues: list[ValidationIssue]
    evidence_refs: list[EvidenceRef]


def _plain(obj: Any) -> Any:
    if isinstance(obj, BaseModel):
        return obj.model_dump(by_alias=True, mode="json")
    if isinstance(obj, Mapping):
        return copy.deepcopy(dict(obj))
    return obj


def is_banmal_suspect(sentence: str) -> bool:
    """문장 끝 어미가 `다`·`냐`·`해`·`지` 이고 존댓말 어미(`니다`·`세요`·`요`)가 아니면 True.

    명사·숫자로 끝나는 문장은 어느 쪽도 아니라 False.
    """
    stripped = sentence.rstrip(_SENTENCE_TRAILER)
    if not stripped or stripped.endswith(POLITE_ENDINGS):
        return False
    return stripped.endswith(BANMAL_ENDINGS)


def _sentences(text: str) -> list[str]:
    return [part for part in _SENTENCE_SPLIT.split(text) if part.strip()]


def _strip_ids(text: str) -> str:
    return _SPACES.sub(" ", _ID_WITH_LIST.sub(" ", text)).strip()


def _grounded(labels: Any, label_map: Mapping[str, str]) -> bool:
    return isinstance(labels, list) and bool(labels) and all(label in label_map for label in labels)


def _has_id(item: Any) -> bool:
    return (
        isinstance(item, dict)
        and isinstance(item.get("text"), str)
        and lexicon.ID_IN_TEXT.search(item["text"]) is not None
    )


def _rebuild_text(
    text: dict[str, Any], path: str, label_map: Mapping[str, str], issues: list[ValidationIssue]
) -> None:
    """규칙 1·2(삭제·ID 제거 후 재조립)와 규칙 3(길이·문장 수)."""
    statement = text.get("statement")
    if not isinstance(statement, list):
        issues.append(
            ValidationIssue(SCHEMA_INVALID, f"{path}.statement", "statement 가 배열이 아니다")
        )
        return

    # 1 근거 없는 fact/claim 문장 삭제
    kept: list[Any] = []
    for item in statement:
        if isinstance(item, Mapping):
            item = dict(item)
            if item.get("kind") in GROUNDED_KINDS and not _grounded(
                item.get("evidence_labels"), label_map
            ):
                continue
        kept.append(item)
    if statement and not kept:
        issues.append(
            ValidationIssue(UNGROUNDED_CLAIM, f"{path}.statement", "근거 있는 문장이 남지 않았다")
        )

    # 2 본문 ID 문자열: 2문장 이상 남으면 문장째 삭제, 모자라면 ID 만 제거
    tainted = [index for index, item in enumerate(kept) if _has_id(item)]
    if tainted:
        if len(kept) - len(tainted) >= STATEMENT_MIN:
            kept = [item for index, item in enumerate(kept) if index not in tainted]
        else:
            for index in tainted:
                kept[index]["text"] = _strip_ids(kept[index]["text"])
            kept = [
                item for item in kept if not (isinstance(item, dict) and item.get("text") == "")
            ]
    headline = text.get("headline")
    if isinstance(headline, str) and lexicon.ID_IN_TEXT.search(headline):
        text["headline"] = headline = _strip_ids(headline)
    text["statement"] = kept

    # 3 길이·문장 수
    if isinstance(headline, str) and len(headline) > HEADLINE_MAX:
        issues.append(
            ValidationIssue(
                SCHEMA_INVALID, f"{path}.headline", f"headline {len(headline)}자 > {HEADLINE_MAX}"
            )
        )
    total = sum(
        len(item["text"])
        for item in kept
        if isinstance(item, dict) and isinstance(item.get("text"), str)
    )
    if total > STATEMENT_TOTAL_MAX:
        issues.append(
            ValidationIssue(
                SCHEMA_INVALID,
                f"{path}.statement",
                f"문장 합산 {total}자 > {STATEMENT_TOTAL_MAX}",
            )
        )
    if not STATEMENT_MIN <= len(kept) <= STATEMENT_MAX:
        issues.append(
            ValidationIssue(
                SCHEMA_INVALID,
                f"{path}.statement",
                f"문장 {len(kept)}개. {STATEMENT_MIN}~{STATEMENT_MAX} 여야 한다",
            )
        )


def _evidence_refs(
    text: dict[str, Any], intensity: str, label_map: Mapping[str, str]
) -> list[EvidenceRef]:
    refs: list[EvidenceRef] = []
    statement = text.get("statement")
    if not isinstance(statement, list):
        return refs
    for index, item in enumerate(statement):
        if not isinstance(item, dict) or not isinstance(item.get("evidence_labels"), list):
            continue
        for label in item["evidence_labels"]:
            if label in label_map:
                refs.append(EvidenceRef(intensity, f"statement[{index}]", label, label_map[label]))
    return refs


def _text_parts(text: Mapping[str, Any]) -> list[str]:
    parts: list[str] = []
    if isinstance(text.get("headline"), str):
        parts.append(text["headline"])
    statement = text.get("statement")
    if isinstance(statement, list):
        for item in statement:
            if isinstance(item, Mapping) and isinstance(item.get("text"), str):
                parts.append(item["text"])
    return parts


def _hell_out_of_list(blob: str) -> list[str]:
    """PROFANITY 매치 중 HELL_ALLOWED_PROFANITY 어느 항목의 출현 범위에도 들지 않는 것.

    9/16 결정으로 hell 비속어 검사를 껐다(`lexicon._RULE_INTENSITIES`). `applies()` 가 False 라
    지금은 불리지 않는다. 검사를 되살릴 때를 위해 남긴다 — 되살린다면 어간/표층형이 섞인 목록부터
    고쳐야 한다(같은 주석).
    """
    allowed_spans = [
        (match.start(), match.end())
        for word in lexicon.HELL_ALLOWED_PROFANITY
        for match in re.finditer(re.escape(word), blob)
    ]
    hits: list[str] = []
    for word in lexicon.PROFANITY:
        for match in re.finditer(re.escape(word), blob):
            inside = any(
                start <= match.start() and match.end() <= end for start, end in allowed_spans
            )
            if not inside:
                hits.append(word)
                break
    return hits


def _lexicon_rules(
    text: Mapping[str, Any], intensity: Intensity, path: str, issues: list[ValidationIssue]
) -> None:
    """규칙 6(욕·죽음 단어)과 규칙 7(이모지·U+FFFD·닳은 문구·mild 반말)."""
    parts = _text_parts(text)
    blob = "\n".join(parts)
    rule = lexicon.LexiconRule

    def add(code: str, message: str) -> None:
        issues.append(ValidationIssue(code, path, message))

    # 6
    if lexicon.applies(intensity, rule.PROFANITY):
        hits = [word for word in lexicon.PROFANITY if word in blob]
        if hits:
            add(PROFANITY_OUT_OF_LIST, f"{intensity} 에 비속어 {hits}")
    if lexicon.applies(intensity, rule.DEATH_WORDS):
        hits = [word for word in lexicon.DEATH_WORDS if word in blob]
        if hits:
            add(SELF_HARM_LEXICON, f"자해·죽음 어휘 {hits}")
    if lexicon.applies(intensity, rule.HELL_ALLOWED_PROFANITY):
        outside = _hell_out_of_list(blob)
        if outside:
            add(PROFANITY_OUT_OF_LIST, f"허용 목록 밖 비속어 {outside}")
    limited: list[str] = []
    if lexicon.applies(intensity, rule.HELL_ALLOWED_PROFANITY):
        limited.extend(lexicon.HELL_ALLOWED_PROFANITY)
    if lexicon.applies(intensity, rule.HELL_ONCE_PER_VERDICT):
        limited.extend(word for word in lexicon.HELL_ONCE_PER_VERDICT if word not in limited)
    repeated = [word for word in limited if blob.count(word) >= 2]
    if repeated:
        add(PROFANITY_OUT_OF_LIST, f"같은 욕·한정 어휘 2회 이상 {repeated}")

    # 7
    if any(ord(char) > EMOJI_CODEPOINT_FLOOR for char in blob):
        add(INTENSITY_MISMATCH, "이모지 포함")
    if REPLACEMENT_CHARACTER in blob:
        add(INTENSITY_MISMATCH, "깨진 문자(U+FFFD) 포함")
    if lexicon.applies(intensity, rule.WORN_PHRASES):
        hits = [phrase for phrase in lexicon.WORN_PHRASES if phrase in blob]
        if hits:
            add(INTENSITY_MISMATCH, f"닳은 문구 {hits}")
    if intensity is Intensity.mild:
        suspects = [s for part in parts for s in _sentences(part) if is_banmal_suspect(s)]
        if suspects:
            add(INTENSITY_MISMATCH, f"mild 반말 의심 {len(suspects)}문장")


def _correct_meme_tag(
    data: dict[str, Any], sentencing: Mapping[str, Any] | None, jury: Mapping[str, Any]
) -> None:
    """규칙 4. guilty ∧ 최고 rank → GUILTY_HEAVY, 그 외 guilty → GUILTY_LIGHT, 나머지 동명."""
    result = jury.get("result")
    if result == "guilty":
        policy = jury.get("policy") if isinstance(jury.get("policy"), Mapping) else {}
        allowed = [
            item
            for item in (policy.get("allowed_sentences") or [])
            if isinstance(item, Mapping) and isinstance(item.get("rank"), int)
        ]
        sentence = sentencing.get("sentence") if isinstance(sentencing, Mapping) else None
        rank = next((item["rank"] for item in allowed if item.get("code") == sentence), None)
        top = max((item["rank"] for item in allowed), default=None)
        tag = MemeTag.GUILTY_HEAVY if rank is not None and rank == top else MemeTag.GUILTY_LIGHT
    elif result in _MEME_BY_RESULT:
        tag = _MEME_BY_RESULT[result]
    else:
        return
    data["meme_tag"] = tag.value


def apply_text_rules(
    draft: WriterDraft | Mapping[str, Any],
    label_map: Mapping[str, str],
    sentencing: BaseModel | Mapping[str, Any] | None,
    jury: BaseModel | Mapping[str, Any],
) -> TextRuleResult:
    """05 §3.4 텍스트 규칙 7항. `issues` 가 비면 통과.

    1 근거 없는 fact/claim 삭제(남은 0 → `UNGROUNDED_CLAIM`) · 2 본문 ID 삭제/제거, headline 제거 ·
    3 길이·문장 수(`SCHEMA_INVALID`) · 4 `meme_tag` 교정(이슈 없음) · 5 강도 집합·중복 ·
    6 욕·죽음 단어(`PROFANITY_OUT_OF_LIST`·`SELF_HARM_LEXICON`) ·
    7 이모지·U+FFFD·닳은 문구·mild 반말(`INTENSITY_MISMATCH`).
    `dict` 입력도 받는다(길이 초과처럼 파싱이 막는 초안도 검사한다).
    """
    issues: list[ValidationIssue] = []
    data = _plain(draft)
    if not isinstance(data, dict):
        issues.append(ValidationIssue(SCHEMA_INVALID, "", "초안이 객체가 아니다"))
        return TextRuleResult({}, issues, [])
    jury_data = _plain(jury)
    if not isinstance(jury_data, dict):
        issues.append(ValidationIssue(SCHEMA_INVALID, "jury", "jury 가 객체가 아니다"))
        jury_data = {}
    sentencing_data = _plain(sentencing)

    texts = data.get("texts")
    if not isinstance(texts, list):
        issues.append(ValidationIssue(SCHEMA_INVALID, "texts", "texts 가 배열이 아니다"))
        return TextRuleResult(data, issues, [])

    refs: list[EvidenceRef] = []
    seen: list[str] = []
    rebuilt: list[Any] = []
    for index, text in enumerate(texts):
        path = f"texts[{index}]"
        if not isinstance(text, Mapping):
            issues.append(ValidationIssue(SCHEMA_INVALID, path, "초안 항목이 객체가 아니다"))
            rebuilt.append(text)
            continue
        text = dict(text)
        key = _intensity_key(text.get("intensity"))
        if key in seen:
            issues.append(
                ValidationIssue(DUPLICATE_INTENSITY, f"{path}.intensity", f"강도 중복 {key}")
            )
        seen.append(key)
        _rebuild_text(text, path, label_map, issues)
        refs.extend(_evidence_refs(text, key, label_map))
        try:
            intensity = parse_intensity(key)
        except ValueError:
            intensity = None
        if intensity is not None:
            _lexicon_rules(text, intensity, path, issues)
        rebuilt.append(text)
    data["texts"] = rebuilt

    # 5 강도 집합
    expected = _expected_keys(jury_data.get("target_intensities") or [])
    if sorted(set(seen)) != sorted(set(expected)):
        issues.append(
            ValidationIssue(
                INTENSITY_SET_MISMATCH,
                "texts",
                f"강도 집합이 다르다. 기대 {sorted(set(expected))}, 실제 {sorted(set(seen))}",
            )
        )

    # 4 meme_tag 교정
    _correct_meme_tag(data, sentencing_data, jury_data)
    return TextRuleResult(data, issues, refs)
