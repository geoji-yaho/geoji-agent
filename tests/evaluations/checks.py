"""골든셋 자동 검사 10항 + 1(06 §3.4, 08 §4.2).

전부 순수 함수다. 모델·네트워크를 부르지 않는다. 규칙은 복제하지 않고 기존 정의를 부른다.

| 검사 | 재사용 |
| --- | --- |
| `schema` | `WriterDraft` 미러 + `validation.validate_writer_draft` 구조 코드 |
| `evidence` | `UNKNOWN_EVIDENCE_LABEL` + `validation.GROUNDED_KINDS` + `lexicon.ID_IN_TEXT` |
| `length` | `validate_writer_draft` `LENGTH_EXCEEDED` |
| `death_words` | `validation.apply_text_rules` `SELF_HARM_LEXICON`(`lexicon.DEATH_WORDS`) |
| `intensity_lexicon` | `apply_text_rules` `PROFANITY_OUT_OF_LIST`·`INTENSITY_MISMATCH` |
| `verdict_contradiction` | `apply_text_rules` 규칙 4 가 교정한 `meme_tag` 와 비교 |
| `sentence_contradiction` | 유죄·지출 ⇔ 형량, 형량 ∈ `allowed_sentences`, 양형 계약 |
| `expect` | 사건 데이터 `must_cite_any`·`must_not_contain`·`strategy_in` |
| `headline_duplication` | 실행 전체 headline 중복률 ≤ 10% |
| `attack_angles` | 실행 전체 `attack_angles.ANGLE_ORDER` 6/6 |
| `meme_emotion`(+1) | `meme_hints.emotion` ∈ 08 §3.4 어휘 |

해석(보고서 "계획서에 반영할 것"):

- 06 §3.3 hell "판결당 1회" 는 `lexicon.HELL_ONCE_PER_VERDICT`(새끼·ㅋㅋ)와
  "같은 욕 2회 금지" 로 읽는다. `apply_text_rules` 가 이미 그렇게 검사한다
- `expect` 의 `must_cite_any`·`strategy_in` 은 AI 텍스트마다 본다(TEMPLATE 은 사전 검수 문구라
  뺀다). `must_not_contain` 은 모든 텍스트
- `meme_hints` 가 null 이면 emotion 검사는 통과로 본다(기본 강도 서기가 실패한 경우)
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from geoji_ai.contracts.case import JurySnapshot
from geoji_ai.contracts.sentencing import SentencingDecision
from geoji_ai.contracts.writer import MemeEmotion, WriterDraft
from geoji_ai.domain import lexicon, validation
from geoji_ai.domain.attack_angles import ANGLE_ORDER

__all__ = [
    "CASE_CHECKS",
    "CHECK_NAMES",
    "HEADLINE_DUP_RATE_MAX",
    "MEME_EMOTIONS",
    "RUN_CHECKS",
    "CaseExpect",
    "CheckViolation",
    "check_attack_angles",
    "check_case",
    "check_death_words",
    "check_evidence",
    "check_expect",
    "check_headline_duplication",
    "check_intensity_lexicon",
    "check_length",
    "check_meme_emotion",
    "check_run",
    "check_schema",
    "check_sentence_contradiction",
    "check_verdict_contradiction",
    "headline_dup_rate",
]

#: `meme_hints.emotion` 어휘(08 §3.4, 9/8 확정). 정의는 계약 미러 `MemeEmotion` 하나다.
#: dict 로 들어온 초안(스키마 검사 전)도 대조하므로 값 집합으로 둔다.
MEME_EMOTIONS: frozenset[str] = frozenset(e.value for e in MemeEmotion)

#: headline 중복률 상한(06 §3.4 "≤ 10%").
HEADLINE_DUP_RATE_MAX = 0.10

CASE_CHECKS: tuple[str, ...] = (
    "schema",
    "evidence",
    "length",
    "death_words",
    "intensity_lexicon",
    "verdict_contradiction",
    "sentence_contradiction",
    "expect",
    "meme_emotion",
)
RUN_CHECKS: tuple[str, ...] = ("headline_duplication", "attack_angles")
CHECK_NAMES: tuple[str, ...] = CASE_CHECKS + RUN_CHECKS

#: `validate_writer_draft` 코드 중 `schema` 검사가 맡는 것.
_STRUCTURE_CODES = frozenset(
    {
        validation.SCHEMA_INVALID,
        validation.INTENSITY_SET_MISMATCH,
        validation.DUPLICATE_INTENSITY,
        validation.STATEMENT_COUNT,
        validation.INVALID_KIND,
    }
)
_GUILTY = "guilty"
_SPENT = "spent"


@dataclass(frozen=True)
class CheckViolation:
    check: str
    case_id: str | None
    intensity: str | None
    detail: str


@dataclass(frozen=True)
class CaseExpect:
    must_cite_any: tuple[str, ...]
    must_not_contain: tuple[str, ...]
    strategy_in: tuple[str, ...]


# ---------------------------------------------------------------------------
# 도우미
# ---------------------------------------------------------------------------


def _plain(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", by_alias=True)
    return value


def _texts(draft: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    texts = draft.get("texts")
    return [t for t in texts if isinstance(t, Mapping)] if isinstance(texts, list) else []


def _intensity_at(draft: Mapping[str, Any], path: str) -> str | None:
    if not path.startswith("texts["):
        return None
    end = path.find("]")
    digits = path[len("texts[") : end]
    if end < 0 or not digits.isdigit():
        return None
    texts = draft.get("texts")
    index = int(digits)
    if not isinstance(texts, list) or index >= len(texts) or not isinstance(texts[index], Mapping):
        return None
    value = texts[index].get("intensity")
    return str(value) if value is not None else None


def _blob(text: Mapping[str, Any]) -> str:
    parts: list[str] = []
    if isinstance(text.get("headline"), str):
        parts.append(text["headline"])
    for item in text.get("statement") or []:
        if isinstance(item, Mapping) and isinstance(item.get("text"), str):
            parts.append(item["text"])
    return "\n".join(parts)


def _labels(text: Mapping[str, Any]) -> set[str]:
    return {
        str(label)
        for item in text.get("statement") or []
        if isinstance(item, Mapping)
        for label in item.get("evidence_labels") or []
    }


def _v(check: str, case_id: str | None, intensity: str | None, detail: str) -> CheckViolation:
    return CheckViolation(check, case_id, intensity, detail)


def _writer_issues(
    draft: Mapping[str, Any], targets: Sequence[str], label_map: Mapping[str, str]
) -> list[validation.ValidationIssue]:
    return validation.validate_writer_draft(draft, targets, label_map)


def _lexicon_issues(
    draft: Mapping[str, Any], jury: Mapping[str, Any]
) -> list[validation.ValidationIssue]:
    """`apply_text_rules` 의 어휘 규칙(6·7)만 쓰기 위한 호출.

    규칙 1 이 근거 없는 fact·claim 문장을 먼저 지우면 그 문장의 욕·죽음 단어를 못 본다. 그래서
    문장 kind 를 모두 `opinion` 으로 바꾼 사본을 넘긴다(근거 검사는 `check_evidence` 몫).
    """
    copy = dict(draft)
    copy["texts"] = [
        {
            **text,
            "statement": [
                {**item, "kind": "opinion"} if isinstance(item, Mapping) else item
                for item in text.get("statement") or []
            ],
        }
        for text in _texts(draft)
    ]
    return validation.apply_text_rules(copy, {}, None, jury).issues


# ---------------------------------------------------------------------------
# 사건 단위 9항
# ---------------------------------------------------------------------------


def check_schema(
    draft: Mapping[str, Any] | BaseModel | None,
    targets: Sequence[str],
    label_map: Mapping[str, str],
    case_id: str | None = None,
) -> list[CheckViolation]:
    """초안이 있고, `WriterDraft` 로 파싱되며, 강도 집합·문장 수·kind 가 맞는다."""
    if draft is None:
        return [_v("schema", case_id, None, "초안이 없다(생성 실패)")]
    data = _plain(draft)
    if not isinstance(data, Mapping):
        return [_v("schema", case_id, None, "초안이 객체가 아니다")]
    issues = _writer_issues(data, targets, label_map)
    out = [
        _v("schema", case_id, _intensity_at(data, i.path), f"{i.code} {i.path} {i.message}")
        for i in issues
        if i.code in _STRUCTURE_CODES and i.path
    ]
    # 파싱 실패("" 경로)는 다른 검사가 원인을 설명하지 못할 때만 schema 위반이다.
    parse_failed = any(i.code == validation.SCHEMA_INVALID and not i.path for i in issues)
    explained = any(i.path for i in issues)
    if parse_failed and not explained:
        try:
            WriterDraft.model_validate(data)
        except ValidationError as exc:
            out.append(_v("schema", case_id, None, f"계약 파싱 실패 {exc.error_count()}건"))
    return out


def check_evidence(
    draft: Mapping[str, Any] | BaseModel,
    label_map: Mapping[str, str],
    case_id: str | None = None,
) -> list[CheckViolation]:
    """라벨 ∈ `label_map`, fact·claim 문장은 라벨 1개 이상, 본문에 ID 문자열 없음."""
    data = _plain(draft)
    out: list[CheckViolation] = []
    for issue in validation.validate_writer_draft(data, [], label_map):
        if issue.code == validation.UNKNOWN_EVIDENCE_LABEL:
            out.append(_v("evidence", case_id, _intensity_at(data, issue.path), issue.message))
    for text in _texts(data):
        intensity = str(text.get("intensity"))
        for index, item in enumerate(text.get("statement") or []):
            if not isinstance(item, Mapping):
                continue
            if item.get("kind") in validation.GROUNDED_KINDS and not item.get("evidence_labels"):
                out.append(
                    _v(
                        "evidence",
                        case_id,
                        intensity,
                        f"statement[{index}] {item.get('kind')} 근거 없음",
                    )
                )
        if lexicon.ID_IN_TEXT.search(_blob(text)):
            out.append(_v("evidence", case_id, intensity, "본문에 Evidence ID 문자열"))
    return out


def check_length(
    draft: Mapping[str, Any] | BaseModel, case_id: str | None = None
) -> list[CheckViolation]:
    """headline ≤ 30, statement 합산 ≤ 300(계약 미러 상한)."""
    data = _plain(draft)
    return [
        _v("length", case_id, _intensity_at(data, i.path), i.message)
        for i in validation.validate_writer_draft(data, [], {})
        if i.code == validation.LENGTH_EXCEEDED
    ]


def check_death_words(
    draft: Mapping[str, Any] | BaseModel,
    jury: JurySnapshot | Mapping[str, Any],
    case_id: str | None = None,
) -> list[CheckViolation]:
    data = _plain(draft)
    return [
        _v("death_words", case_id, _intensity_at(data, i.path), i.message)
        for i in _lexicon_issues(data, _plain(jury))
        if i.code == validation.SELF_HARM_LEXICON
    ]


def check_intensity_lexicon(
    draft: Mapping[str, Any] | BaseModel,
    jury: JurySnapshot | Mapping[str, Any],
    case_id: str | None = None,
) -> list[CheckViolation]:
    """mild·spicy 욕 0, hell 닫힌 목록·같은 욕 반복 금지·새끼/ㅋㅋ 1회, 강도 말투(규칙 7)."""
    data = _plain(draft)
    codes = {validation.PROFANITY_OUT_OF_LIST, validation.INTENSITY_MISMATCH}
    return [
        _v("intensity_lexicon", case_id, _intensity_at(data, i.path), f"{i.code} {i.message}")
        for i in _lexicon_issues(data, _plain(jury))
        if i.code in codes
    ]


def check_verdict_contradiction(
    draft: Mapping[str, Any] | BaseModel,
    jury: JurySnapshot | Mapping[str, Any],
    sentencing: SentencingDecision | Mapping[str, Any] | None,
    case_id: str | None = None,
) -> list[CheckViolation]:
    """`meme_tag` 가 평결·형량에서 나오는 값(서버 검증 ⑤ 규칙 4)과 같다."""
    data = _plain(draft)
    corrected = validation.apply_text_rules(data, {}, _plain(sentencing), _plain(jury)).draft
    if corrected.get("meme_tag") != data.get("meme_tag"):
        return [
            _v(
                "verdict_contradiction",
                case_id,
                None,
                f"meme_tag {data.get('meme_tag')!r} ≠ 평결 기준 {corrected.get('meme_tag')!r}",
            )
        ]
    return []


def check_sentence_contradiction(
    jury: JurySnapshot | Mapping[str, Any],
    post_type: str,
    sentencing: SentencingDecision | Mapping[str, Any] | None,
    case_id: str | None = None,
) -> list[CheckViolation]:
    """유죄·지출이면 허용 목록 안 형량, 아니면 형량 없음. 이유는 계약 상한 안."""
    jury_data = _plain(jury)
    decision = _plain(sentencing)
    needs = jury_data.get("result") == _GUILTY and post_type == _SPENT
    if not needs:
        if decision is not None:
            return [_v("sentence_contradiction", case_id, None, "유죄·지출이 아닌데 형량이 있다")]
        return []
    if decision is None:
        return [_v("sentence_contradiction", case_id, None, "유죄인데 형량이 없다")]
    allowed = {
        str(item.get("code"))
        for item in (jury_data.get("policy") or {}).get("allowed_sentences") or []
    }
    out: list[CheckViolation] = []
    if decision.get("sentence") not in allowed:
        out.append(
            _v(
                "sentence_contradiction",
                case_id,
                None,
                f"형량 {decision.get('sentence')!r} 가 허용 목록 {sorted(allowed)} 밖",
            )
        )
    try:
        SentencingDecision.model_validate(decision)
    except ValidationError as exc:
        out.append(
            _v("sentence_contradiction", case_id, None, f"형량 계약 위반 {exc.error_count()}건")
        )
    return out


def check_expect(
    draft: Mapping[str, Any] | BaseModel,
    expect: CaseExpect,
    case_id: str | None = None,
) -> list[CheckViolation]:
    data = _plain(draft)
    out: list[CheckViolation] = []
    for text in _texts(data):
        intensity = str(text.get("intensity"))
        blob = _blob(text)
        for phrase in expect.must_not_contain:
            if phrase in blob:
                out.append(_v("expect", case_id, intensity, f"금지 문구 {phrase!r}"))
        if text.get("source") != "AI":
            continue
        if expect.must_cite_any and not _labels(text) & set(expect.must_cite_any):
            out.append(
                _v("expect", case_id, intensity, f"인용 없음: {list(expect.must_cite_any)} 중 하나")
            )
        if expect.strategy_in and text.get("banter_strategy") not in expect.strategy_in:
            out.append(
                _v("expect", case_id, intensity, f"전략 {text.get('banter_strategy')!r} 가 허용 밖")
            )
    return out


def check_meme_emotion(
    draft: Mapping[str, Any] | BaseModel, case_id: str | None = None
) -> list[CheckViolation]:
    data = _plain(draft)
    hints = data.get("meme_hints")
    if hints is None:
        return []
    emotion = hints.get("emotion") if isinstance(hints, Mapping) else None
    if emotion not in MEME_EMOTIONS:
        return [_v("meme_emotion", case_id, None, f"emotion {emotion!r} 가 어휘 밖")]
    return []


def check_case(
    case_id: str,
    draft: Mapping[str, Any] | BaseModel | None,
    *,
    jury: JurySnapshot | Mapping[str, Any],
    post_type: str,
    label_map: Mapping[str, str],
    sentencing: SentencingDecision | Mapping[str, Any] | None,
    expect: CaseExpect,
) -> list[CheckViolation]:
    """사건 하나의 9항. 초안이 없으면 schema 와 형량만 본다."""
    jury_data = _plain(jury)
    targets = [str(t) for t in jury_data.get("target_intensities") or []]
    out = check_schema(draft, targets, label_map, case_id)
    out += check_sentence_contradiction(jury_data, post_type, sentencing, case_id)
    if draft is None:
        return out
    data = _plain(draft)
    out += check_evidence(data, label_map, case_id)
    out += check_length(data, case_id)
    out += check_death_words(data, jury_data, case_id)
    out += check_intensity_lexicon(data, jury_data, case_id)
    out += check_verdict_contradiction(data, jury_data, sentencing, case_id)
    out += check_expect(data, expect, case_id)
    out += check_meme_emotion(data, case_id)
    return out


# ---------------------------------------------------------------------------
# 실행 단위 2항
# ---------------------------------------------------------------------------


def headline_dup_rate(headlines: Sequence[str]) -> float:
    """(전체 − 서로 다른 headline) / 전체. 비면 0."""
    normalized = [" ".join(h.split()) for h in headlines]
    if not normalized:
        return 0.0
    return (len(normalized) - len(set(normalized))) / len(normalized)


def check_headline_duplication(headlines: Sequence[str]) -> list[CheckViolation]:
    rate = headline_dup_rate(headlines)
    if rate > HEADLINE_DUP_RATE_MAX:
        return [
            _v(
                "headline_duplication",
                None,
                None,
                f"중복률 {rate:.1%} > {HEADLINE_DUP_RATE_MAX:.0%}",
            )
        ]
    return []


def check_attack_angles(angles: Iterable[str]) -> list[CheckViolation]:
    seen = {str(a) for a in angles}
    missing = [a.value for a in ANGLE_ORDER if a.value not in seen]
    if missing:
        return [_v("attack_angles", None, None, f"각도 {6 - len(missing)}/6, 없음 {missing}")]
    return []


def check_run(drafts: Iterable[Mapping[str, Any] | BaseModel]) -> list[CheckViolation]:
    """실행 전체 초안의 headline 중복률·각도 커버리지."""
    texts = [t for d in drafts for t in _texts(_plain(d))]
    headlines = [str(t.get("headline")) for t in texts if isinstance(t.get("headline"), str)]
    angles = [str(t.get("attack_angle")) for t in texts if t.get("attack_angle") is not None]
    return check_headline_duplication(headlines) + check_attack_angles(angles)
