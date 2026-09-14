"""심문관 코드 규칙(07 §3.2).

모델을 부르기 전에 도는 규칙이다. 필수값 위반은 `IntakeRuleError`(라우트가 422 로 바꾼다),
강한 인젝션·무관 텍스트는 모델 없이 `BLOCKED`, 약한 패턴은 모델에 넘기는 힌트다.
검사 대상은 `item` 과 `reason`(None 이면 건너뜀) 각각이다.

강한 패턴은 계획서 `(제안)` 값 그대로다. 정상 30건 오탐 0 확인 뒤 `(제안)` 을 푼다(07 §4.2).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "IRRELEVANT_REPEAT_PATTERN",
    "ITEM_MAX_CODE_POINTS",
    "MEANINGFUL_RATIO_MIN",
    "REASON_MAX_CODE_POINTS",
    "STRONG_INJECTION_PATTERNS",
    "WEAK_INJECTION_PATTERNS",
    "IntakeRuleError",
    "RuleVerdict",
    "check_required",
    "evaluate_rules",
]

ITEM_MAX_CODE_POINTS = 30
REASON_MAX_CODE_POINTS = 200

#: 강한 인젝션(07 §3.2 `(제안)`). 걸리면 모델 미호출 → `BLOCKED`, `injection_detected=true`.
STRONG_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(이전|위|앞의?)\s*(지시|명령|규칙|프롬프트).{0,8}(무시|잊|취소)"),
    re.compile(r"(무죄|유죄|집행유예|징역).{0,4}(로|라고|으로)\s*(써|해|판결|선고)"),
    re.compile(r"시스템\s*프롬프트"),
    re.compile(r"ignore (all|the|previous|above)", re.IGNORECASE),
    re.compile(r"you are now", re.IGNORECASE),
    re.compile(r"disregard", re.IGNORECASE),
)

#: 약한 인젝션. `injection_hint=true` 로 모델에 넘기고 판정은 모델이 한다.
WEAK_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"판사(님)?"),
    re.compile(r"\bai\b", re.IGNORECASE),
    re.compile(r"봐주"),
    re.compile(r"무죄"),
)

#: 무관 텍스트 (a) 공백 제외 문자 중 한글·영문·숫자 비율 하한.
MEANINGFUL_RATIO_MIN = 0.3
_MEANINGFUL_CHAR = re.compile(r"[가-힣ㄱ-ㅎㅏ-ㅣA-Za-z0-9]")
#: 무관 텍스트 (b) 같은 문자 5회 연속.
IRRELEVANT_REPEAT_PATTERN = re.compile(r"(.)\1{4,}")
#: 무관 텍스트 (c) 공백 제외 숫자만.
_DIGITS_ONLY = re.compile(r"\d+")
_WHITESPACE = re.compile(r"\s+")


class IntakeRuleError(ValueError):
    """필수값 위반. `code` ∈ `ITEM_LENGTH`·`REASON_LENGTH`·`AMOUNT`. 메시지에 원문을 넣지 않는다."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RuleVerdict:
    blocked: bool
    injection_detected: bool
    injection_hint: bool
    reason_code: str | None


def check_required(item: str, reason: str | None, amount_krw: int) -> None:
    """`item` 공백 제거 후 1~30 code points, `reason` None 또는 ≤ 200, `amount_krw > 0`."""
    if not 1 <= len(item.strip()) <= ITEM_MAX_CODE_POINTS:
        raise IntakeRuleError("ITEM_LENGTH")
    if reason is not None and len(reason) > REASON_MAX_CODE_POINTS:
        raise IntakeRuleError("REASON_LENGTH")
    if amount_krw <= 0:
        raise IntakeRuleError("AMOUNT")


def _targets(item: str, reason: str | None) -> list[str]:
    return [item] if reason is None else [item, reason]


def _is_strong_injection(text: str) -> bool:
    return any(pattern.search(text) for pattern in STRONG_INJECTION_PATTERNS)


def _is_irrelevant(text: str) -> bool:
    compact = _WHITESPACE.sub("", text)
    if not compact:
        return False
    meaningful = sum(1 for char in compact if _MEANINGFUL_CHAR.match(char))
    if meaningful / len(compact) < MEANINGFUL_RATIO_MIN:
        return True
    if IRRELEVANT_REPEAT_PATTERN.search(text):
        return True
    return _DIGITS_ONLY.fullmatch(compact) is not None


def evaluate_rules(item: str, reason: str | None) -> RuleVerdict:
    """강한 인젝션 → 무관 텍스트 순서로 본다. 약한 패턴 힌트는 늘 계산한다."""
    targets = _targets(item, reason)
    hint = any(pattern.search(text) for text in targets for pattern in WEAK_INJECTION_PATTERNS)
    if any(_is_strong_injection(text) for text in targets):
        return RuleVerdict(True, True, hint, "STRONG_INJECTION")
    if any(_is_irrelevant(text) for text in targets):
        return RuleVerdict(True, False, hint, "IRRELEVANT_TEXT")
    return RuleVerdict(False, False, hint, None)
