"""짤 점수 선택 — 10 §11 finalize 10 단계의 참조 구현(08 §3.4).

운영에서 이 규칙을 실행하는 곳은 백엔드 finalize 다(00 §8.2). 이 모듈은 같은 규칙을
AI 저장소에서 재현해 백엔드 이식 기준과 13 평가 기준선으로 쓴다.
DB·네트워크를 모른다. 후보 목록과 최근 노출은 호출자가 넘긴다.

계획서에 값이 없어 9/14 사용자가 정한 것:
- `+3` 전략 일치는 `default_intensity` 판결문의 `banter_strategy` 로 본다
- 동점은 `crc32(post_id + image_id)` 오름차순
- 키워드는 NFC 정규화·앞뒤 공백 제거 뒤 정확히 일치
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from zlib import crc32

from geoji_ai.contracts.writer import MemeTag, WriterDraft
from geoji_ai.domain.intensity import Intensity

__all__ = [
    "EMOTION_POINTS",
    "KEYWORD_POINTS",
    "RECENT_PENALTY",
    "RECENT_WINDOW",
    "STRATEGY_POINTS",
    "MemeImage",
    "MemeScore",
    "MemeSelection",
    "normalize_keyword",
    "select_meme",
]

# 10 §11 점수식.
STRATEGY_POINTS = 3
EMOTION_POINTS = 2
KEYWORD_POINTS = 1
RECENT_PENALTY = -5
#: 같은 사용자 최근 노출 몇 장까지 감점하는가.
RECENT_WINDOW = 5


@dataclass(frozen=True)
class MemeImage:
    """백엔드 `meme_images` 한 행 중 선택에 쓰는 열(10 §2)."""

    image_id: str
    tag: MemeTag
    strategies: frozenset[str]
    emotions: frozenset[str]
    keywords: frozenset[str]
    is_active: bool


@dataclass(frozen=True)
class MemeScore:
    """후보 한 장의 점수와 그 내역. 선택 근거 기록용."""

    image_id: str
    score: int
    strategy_match: bool
    emotion_match: bool
    keyword_overlap: int
    recently_shown: bool
    tiebreak: int


@dataclass(frozen=True)
class MemeSelection:
    """`image_id` 가 None 이면 후보 0 — 결과별 기본 이미지(프론트 5장, 08 §2)를 쓴다."""

    tag: MemeTag
    image_id: str | None
    ranking: tuple[MemeScore, ...]


def normalize_keyword(value: str) -> str:
    return unicodedata.normalize("NFC", value.strip())


def _keyword_set(values: Iterable[str]) -> frozenset[str]:
    return frozenset(k for k in map(normalize_keyword, values) if k)


def select_meme(
    draft: WriterDraft,
    *,
    post_id: str,
    default_intensity: Intensity,
    catalog: Iterable[MemeImage],
    recent_image_ids: Sequence[str] = (),
) -> MemeSelection:
    """태그 필터 → 점수 내림차순 → crc32 오름차순. 같은 입력이면 항상 같은 이미지.

    `recent_image_ids` 는 같은 사용자에게 최근 노출한 순서(가장 최근 먼저)다.
    """
    strategy = next(
        (t.banter_strategy for t in draft.texts if t.intensity == default_intensity), None
    )
    hints = draft.meme_hints
    wanted = _keyword_set(hints.keywords) if hints is not None else frozenset()
    recent = frozenset(recent_image_ids[:RECENT_WINDOW])

    scores: list[MemeScore] = []
    for image in catalog:
        if not image.is_active or image.tag != draft.meme_tag:
            continue
        strategy_match = strategy is not None and strategy in image.strategies
        emotion_match = hints is not None and hints.emotion in image.emotions
        overlap = len(wanted & _keyword_set(image.keywords))
        shown = image.image_id in recent
        score = (
            (STRATEGY_POINTS if strategy_match else 0)
            + (EMOTION_POINTS if emotion_match else 0)
            + KEYWORD_POINTS * overlap
            + (RECENT_PENALTY if shown else 0)
        )
        scores.append(
            MemeScore(
                image_id=image.image_id,
                score=score,
                strategy_match=strategy_match,
                emotion_match=emotion_match,
                keyword_overlap=overlap,
                recently_shown=shown,
                tiebreak=crc32(f"{post_id}{image.image_id}".encode()),
            )
        )

    # crc32 가 충돌하면 image_id 로 한 번 더 고정한다.
    ranking = tuple(sorted(scores, key=lambda s: (-s.score, s.tiebreak, s.image_id)))
    return MemeSelection(
        tag=draft.meme_tag,
        image_id=ranking[0].image_id if ranking else None,
        ranking=ranking,
    )
