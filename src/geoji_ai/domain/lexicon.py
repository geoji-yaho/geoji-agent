"""판결문 어휘 목록과 강도별 적용 표(01 §3.5).

단어 목록은 01 §3.5 에 적힌 것 **그대로**다. 늘리거나 줄이지 않는다.
텍스트 규칙(몇 개까지 허용하는지, 어디에 적용하는지)은 작업 5 에서 붙인다.
여기는 목록과 "어느 강도에 어느 검사가 켜지는가" 표까지다.
"""

from __future__ import annotations

import re
from enum import StrEnum

from geoji_ai.domain.intensity import Intensity, parse_intensity

__all__ = [
    "DEATH_WORDS",
    "HELL_ALLOWED_PROFANITY",
    "HELL_ONCE_PER_VERDICT",
    "ID_IN_TEXT",
    "PROFANITY",
    "WORN_PHRASES",
    "LexiconRule",
    "applies",
]

#: 자해·죽음 어휘. 모든 강도에서 금지한다.
DEATH_WORDS: tuple[str, ...] = (
    "자살",
    "자해",
    "죽어",
    "죽고 싶",
    "죽여",
    "뒤져",
    "뒤지",
    "목을 매",
    "손목",
    "극단적 선택",
)

#: mild·spicy 에서 0개인지 세는 비속어.
PROFANITY: tuple[str, ...] = (
    "미친",
    "미쳤",
    "돌았",
    "지랄",
    "새끼",
    "처먹",
    "처타",
    "처박",
    "처발",
    "개같",
    "개무시",
    "씨발",
    "씨빨",
    "ㅅㅂ",
    "병신",
    "ㅂㅅ",
    "존나",
    "ㅈㄴ",
    "좆",
    "꺼져",
    "닥쳐",
    "또라이",
    "등신",
    "멍청",
)

#: hell 에서만 허용하는 목록. 이 밖의 비속어는 PROFANITY_OUT_OF_LIST.
HELL_ALLOWED_PROFANITY: tuple[str, ...] = (
    "미친",
    "돌았냐",
    "정신 나갔냐",
    "실화냐",
    "어이없네",
    "개같은 선택",
    "지랄",
    "꼴",
    "처타다",
    "헛소리",
    "레전드",
    "새끼",
)

#: hell 에서 판결문당 한 번까지만 쓰는 어휘.
HELL_ONCE_PER_VERDICT: tuple[str, ...] = ("새끼", "ㅋㅋ")

#: 닳은 문구. 01 §3.5 는 "정신 차리십시오 등"으로 끝나고 나머지 항목을 주지 않았다.
WORN_PHRASES: tuple[str, ...] = ("정신 차리십시오",)

#: 본문에 새어 나온 Evidence 라벨(F0·F12 …)을 찾는 패턴.
ID_IN_TEXT: re.Pattern[str] = re.compile(r"\bF\d+")


class LexiconRule(StrEnum):
    DEATH_WORDS = "DEATH_WORDS"
    PROFANITY = "PROFANITY"
    HELL_ALLOWED_PROFANITY = "HELL_ALLOWED_PROFANITY"
    HELL_ONCE_PER_VERDICT = "HELL_ONCE_PER_VERDICT"
    WORN_PHRASES = "WORN_PHRASES"
    ID_IN_TEXT = "ID_IN_TEXT"


# 강도별 적용 표. 값은 그 검사를 켜는 강도 집합이다.
_RULE_INTENSITIES: dict[LexiconRule, frozenset[Intensity]] = {
    LexiconRule.DEATH_WORDS: frozenset({Intensity.mild, Intensity.spicy, Intensity.hell}),
    LexiconRule.PROFANITY: frozenset({Intensity.mild, Intensity.spicy}),
    LexiconRule.HELL_ALLOWED_PROFANITY: frozenset({Intensity.hell}),
    LexiconRule.HELL_ONCE_PER_VERDICT: frozenset({Intensity.hell}),
    LexiconRule.WORN_PHRASES: frozenset({Intensity.mild, Intensity.spicy, Intensity.hell}),
    LexiconRule.ID_IN_TEXT: frozenset({Intensity.mild, Intensity.spicy, Intensity.hell}),
}


def _parse_rule(rule: LexiconRule | str) -> LexiconRule:
    try:
        return LexiconRule(rule)
    except ValueError as exc:
        raise ValueError(f"알 수 없는 어휘 검사: {rule!r}") from exc


def applies(intensity: Intensity | str, rule: LexiconRule | str) -> bool:
    """이 강도에서 이 검사를 켜는가."""
    return parse_intensity(intensity) in _RULE_INTENSITIES[_parse_rule(rule)]
