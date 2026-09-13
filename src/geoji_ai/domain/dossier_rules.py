"""방 규칙 적중 판정(05 §3.6).

계획서에 카테고리별 키워드 목록이 없다. 그래서 카테고리 값(01 `Category` 11종) 자체를 `/` 로
나눈 토큰만 쓴다. 토큰 하나라도 규칙 원문에 들어 있으면 적중이다. `기타` 는 적중하지 않는다.
키워드를 늘리는 것은 계획서에 사전이 생긴 뒤에 한다.
"""

from __future__ import annotations

from typing import Protocol

__all__ = ["NO_MATCH_CATEGORIES", "category_tokens", "match_rule", "rule_matcher"]

#: 어떤 규칙에도 적중시키지 않는 카테고리.
NO_MATCH_CATEGORIES: frozenset[str] = frozenset({"기타"})


class _HasText(Protocol):
    """`ports.backend.RoomRule` 처럼 `text` 가 있는 규칙.

    domain 이 ports 를 import 하지 않게 구조적 타입으로 받는다.
    """

    @property
    def text(self) -> str: ...


def category_tokens(category: str) -> tuple[str, ...]:
    """`교통/택시` → `("교통", "택시")`. `기타` 는 빈 튜플."""
    if category in NO_MATCH_CATEGORIES:
        return ()
    return tuple(token for token in (part.strip() for part in category.split("/")) if token)


def match_rule(category: str, rule_text: str) -> bool:
    """카테고리 토큰 중 하나라도 규칙 원문에 들어 있으면 True."""
    return any(token in rule_text for token in category_tokens(category))


def rule_matcher(category: str, rule: _HasText) -> bool:
    """04 `build_evidence(rule_matcher=...)` 에 넘기는 `(category, RoomRule) -> bool` 어댑터."""
    return match_rule(category, rule.text)
