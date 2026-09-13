"""댓글 안전 규칙(04 §3.5, 01 §3.5).

입력 필드명은 CaseSnapshot `comment{comment_id, version, room_id, post_id,
post_status, author_id, content, created_at}` 와 같다. 계약 모델은 import 하지
않는다. 단어 목록은 `lexicon` 에서 가져온다(복사 금지).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from geoji_ai.domain.intensity import Intensity, parse_intensity
from geoji_ai.domain.lexicon import (
    DEATH_WORDS,
    HELL_ALLOWED_PROFANITY,
    PROFANITY,
    LexiconRule,
    applies,
)

__all__ = [
    "JUDGED",
    "MAX_LENGTH",
    "MIN_LENGTH",
    "Comment",
    "Reason",
    "check",
    "filter_comments",
    "normalize",
]

#: 댓글을 쓸 수 있는 게시물 상태.
JUDGED = "JUDGED"
#: 본문 길이 범위(양끝 포함, 앞뒤 공백 뺀 code point 수).
MIN_LENGTH = 21
MAX_LENGTH = 200


class Reason(StrEnum):
    POST_NOT_JUDGED = "POST_NOT_JUDGED"
    LENGTH_OUT_OF_RANGE = "LENGTH_OUT_OF_RANGE"
    CONTACT_INFO = "CONTACT_INFO"
    DEATH_WORD = "DEATH_WORD"
    PROFANITY = "PROFANITY"
    AUTHOR_IS_DEFENDANT = "AUTHOR_IS_DEFENDANT"
    MENTION = "MENTION"


@dataclass(frozen=True, slots=True)
class Comment:
    comment_id: str
    version: int
    room_id: str
    post_id: str
    post_status: str
    author_id: str
    content: str
    created_at: datetime


_URL = re.compile(
    r"https?://|www\.|\b[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b",
    re.IGNORECASE,
)
_PHONE = re.compile(r"(?<!\d)0\d{1,2}[-\s]?\d{3,4}[-\s]?\d{4}(?!\d)")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_SPACES = re.compile(r"\s+")

# 긴 구절부터 지워야 "개같은 선택" 안의 "개같" 이 먼저 잘리지 않는다.
_HELL_ALLOWED_LONGEST_FIRST = tuple(sorted(HELL_ALLOWED_PROFANITY, key=len, reverse=True))


def normalize(content: str) -> str:
    """중복 판정용 정규화. NFKC · casefold · 공백 연속 하나로 · strip."""
    return _SPACES.sub(" ", unicodedata.normalize("NFKC", content).casefold()).strip()


def _has_contact(content: str) -> bool:
    return any(pattern.search(content) for pattern in (_URL, _PHONE, _EMAIL))


def _has_profanity(content: str, intensity: Intensity) -> bool:
    if applies(intensity, LexiconRule.HELL_ALLOWED_PROFANITY):
        for phrase in _HELL_ALLOWED_LONGEST_FIRST:
            content = content.replace(phrase, " ")
        return any(word in content for word in PROFANITY)
    if applies(intensity, LexiconRule.PROFANITY):
        return any(word in content for word in PROFANITY)
    return False


def check(comment: Comment, *, defendant_id: str, room_intensity: Intensity | str) -> list[Reason]:
    """규칙 1~7 위반 이유. 빈 목록이면 통과. 중복(8)은 `filter_comments` 에서 본다."""
    intensity = parse_intensity(room_intensity)
    content = comment.content
    reasons: list[Reason] = []
    if comment.post_status != JUDGED:
        reasons.append(Reason.POST_NOT_JUDGED)
    if not MIN_LENGTH <= len(content.strip()) <= MAX_LENGTH:
        reasons.append(Reason.LENGTH_OUT_OF_RANGE)
    if _has_contact(content):
        reasons.append(Reason.CONTACT_INFO)
    if applies(intensity, LexiconRule.DEATH_WORDS) and any(w in content for w in DEATH_WORDS):
        reasons.append(Reason.DEATH_WORD)
    if _has_profanity(content, intensity):
        reasons.append(Reason.PROFANITY)
    if comment.author_id == defendant_id:
        reasons.append(Reason.AUTHOR_IS_DEFENDANT)
    if "@" in content:
        reasons.append(Reason.MENTION)
    return reasons


def filter_comments(
    comments: Iterable[Comment], *, defendant_id: str, room_intensity: Intensity | str
) -> list[Comment]:
    """규칙 1~7 을 통과한 댓글 중 정규화 값이 처음 나온 것만 순서대로 남긴다."""
    intensity = parse_intensity(room_intensity)
    seen: set[str] = set()
    kept: list[Comment] = []
    for comment in comments:
        if check(comment, defendant_id=defendant_id, room_intensity=intensity):
            continue
        key = normalize(comment.content)
        if key in seen:
            continue
        seen.add(key)
        kept.append(comment)
    return kept
