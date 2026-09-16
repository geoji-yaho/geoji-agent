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

#: (9/16 결정으로 검사에서 뺐다 — 아래 `_RULE_INTENSITIES` 참고) hell 허용 목록.
#: 목록 자체는 프롬프트·문서 참조용으로 남긴다. 검사를 되살리려면 표에서 강도를 돌려주면 된다.
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

#: (9/16 결정으로 검사에서 뺐다) hell 에서 판결문당 한 번까지만 쓰던 어휘.
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
#
# **9/16 사용자 결정 — 지옥맛은 비속어를 차단하지 않는다.**
# (원문) hell 은 `HELL_ALLOWED_PROFANITY` 12개 안에서만 허용하고 `새끼`·`ㅋㅋ` 는 1회까지였다.
# 그 구조에 버그가 있었다. `PROFANITY` 는 어간 목록(`처먹`·`돌았`·`개같`)인데 허용 목록은 표층형
# (`처타다`·`돌았냐`·`개같은 선택`)이라, 판정이 "어간 매치가 허용 표층형 범위 안에 드는가" 였다.
# 그래서 허용 목록에 있는 단어도 활용하면 막혔다(`미쳤네`·`돌았어`·`개같은 판단`). `처타다` 는
# 사전형이라 실제 문장에 나올 수 없어 사실상 쓸 수 없는 항목이었다. 9/16 실측에서 서기가 쓴
# `처먹네` 가 막혀 판결문이 통째로 템플릿이 됐다(15 §6).
# 지옥맛 방은 "봐주지 마라"에 동의한 방이므로 비속어 검사(PROFANITY·허용 목록·1회 제한)를 전부 끈다.
# **자해·죽음 어휘(`DEATH_WORDS`)는 비속어가 아니라 안전 규칙이라 전 강도에서 그대로 막는다.**
# 정체성 비하·성적 표현은 검수관(guardrail)의 `IDENTITY_DEGRADATION`·`UNSAFE_CONTENT` 가 본다.
_RULE_INTENSITIES: dict[LexiconRule, frozenset[Intensity]] = {
    LexiconRule.DEATH_WORDS: frozenset({Intensity.mild, Intensity.spicy, Intensity.hell}),
    LexiconRule.PROFANITY: frozenset({Intensity.mild, Intensity.spicy}),
    LexiconRule.HELL_ALLOWED_PROFANITY: frozenset(),
    LexiconRule.HELL_ONCE_PER_VERDICT: frozenset(),
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
