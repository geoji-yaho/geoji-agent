"""배심원이 인용한 원화 금액을 입력 근거와 대조한다(#73).

숫자로 시작해 원으로 끝나는 표기만 다룬다. 일반 수량·날짜, 원이 생략된 표현,
한글로만 쓴 수사와 비교·비유의 의미까지 판정하는 검수기는 아니다.
"""

from __future__ import annotations

import re
import unicodedata
from fractions import Fraction

_WON_AMOUNT = re.compile(
    r"(?<![0-9.,+\-])([+\-]?[0-9][0-9,.]*(?:\s*[십백천만억조]\s*(?:[0-9][0-9,.]*)?)*)\s*원"
)
_TOKEN = re.compile(r"[0-9][0-9,.]*|[십백천만억조]")
_NUMBER = re.compile(r"(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?")
_SMALL_UNITS = {"십": 10, "백": 100, "천": 1_000}
_LARGE_UNITS = {"만": 10_000, "억": 100_000_000, "조": 1_000_000_000_000}


def _parse_amount(text: str) -> int | None:
    text = "".join(text.split())
    sign = -1 if text.startswith("-") else 1
    total = Fraction(0)
    section = Fraction(0)
    number: Fraction | None = None
    previous_small = 10_000
    previous_large = 10**16
    has_section = False
    for token in _TOKEN.findall(text.lstrip("+-")):
        if token in _SMALL_UNITS:
            unit = _SMALL_UNITS[token]
            if unit >= previous_small:
                return None
            section += (number if number is not None else 1) * unit
            number = None
            previous_small = unit
            has_section = True
        elif token in _LARGE_UNITS:
            unit = _LARGE_UNITS[token]
            if unit >= previous_large or not has_section:
                return None
            total += (section + (number if number is not None else 0)) * unit
            section, number = Fraction(0), None
            previous_small, previous_large = 10_000, unit
            has_section = False
        else:
            if number is not None or _NUMBER.fullmatch(token) is None:
                return None
            number = Fraction(token.replace(",", ""))
            has_section = True
    value = sign * (total + section + (number if number is not None else 0))
    return int(value) if value.denominator == 1 else None


def _amounts(text: str) -> list[int | None]:
    normalized = unicodedata.normalize("NFKC", text)
    return [_parse_amount(match[1]) for match in _WON_AMOUNT.finditer(normalized)]


def has_grounded_amounts(reason: str, *, amount_krw: int, item: str, post_reason: str) -> bool:
    """출력의 모든 명시적 원화 금액이 입력에 있으면 True. 잘못된 표기는 거절한다."""
    allowed = {amount_krw}
    allowed.update(
        amount for amount in _amounts(item) + _amounts(post_reason) if amount is not None
    )
    return all(amount is not None and amount in allowed for amount in _amounts(reason))
