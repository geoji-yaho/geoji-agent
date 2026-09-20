"""공격 각도 6종과 마무리 방식 문장(01 §3.5).

서기는 사유와 평결에 맞는 기법을 허용 목록에서 고른다(9/20).
`pick` 은 과거 동작과 고정 템플릿의 메타데이터에만 남긴다.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from enum import StrEnum
from zlib import crc32

__all__ = [
    "ANGLE_GUIDES",
    "ANGLE_ORDER",
    "HISTORY_ANGLES",
    "NEEDS_EVIDENCE",
    "RULE_ANGLES",
    "AngleGuide",
    "AttackAngle",
    "pick",
]


class AttackAngle(StrEnum):
    CONVERSION = "CONVERSION"
    REPETITION = "REPETITION"
    EXCUSE_DISSECTION = "EXCUSE_DISSECTION"
    FUTURE_PROPHECY = "FUTURE_PROPHECY"
    RULE_PERSONIFICATION = "RULE_PERSONIFICATION"
    ALTERNATIVE_MOCKERY = "ALTERNATIVE_MOCKERY"


# 스크립트 ANGLES 의 순서 그대로. pick() 의 순환 순서가 된다.
ANGLE_ORDER: tuple[AttackAngle, ...] = (
    AttackAngle.CONVERSION,
    AttackAngle.REPETITION,
    AttackAngle.EXCUSE_DISSECTION,
    AttackAngle.FUTURE_PROPHECY,
    AttackAngle.RULE_PERSONIFICATION,
    AttackAngle.ALTERNATIVE_MOCKERY,
)


@dataclass(frozen=True)
class AngleGuide:
    """각도의 한글 라벨과 마무리 방식 문장."""

    label_ko: str
    instruction: str


ANGLE_GUIDES: dict[AttackAngle, AngleGuide] = {
    AttackAngle.CONVERSION: AngleGuide(
        label_ko="환산",
        instruction=(
            "사유와 직접 연결되고 입력에 비교 단가·단위가 있을 때만 환산한다."
            " 단가·시급을 만들거나 사건과 무관한 커피 잔 수를 붙이지 않는다."
        ),
    ),
    AttackAngle.REPETITION: AngleGuide(
        label_ko="반복",
        instruction=(
            "근거의 기간·횟수나 실제 과거 다짐을 이번 사유와 연결한다."
            " 이전 기록의 횟수에 이번 건이 포함되는지 확인하고, 카테고리를 품목으로 바꾸지 않는다."
        ),
    ),
    AttackAngle.EXCUSE_DISSECTION: AngleGuide(
        label_ko="사유 검토",
        instruction=(
            "사유의 구체적인 선택·모순을 짚는다. 타당한 사정은 인정한다."
            " 승인·무죄라면 필요성을 받아들이고, 유죄·기각이어도 숨은 동기를 지어내지 않는다."
        ),
    ),
    AttackAngle.FUTURE_PROPHECY: AngleGuide(
        label_ko="미래 예언",
        instruction=(
            "근거에 반복 패턴이 있을 때만 같은 행동이 이어지는 모습을 가볍게 상상한다."
            " 한 번 산 물건으로 파산·잔고·소득이나 미래 금액을 단정하지 않는다."
        ),
    ),
    AttackAngle.RULE_PERSONIFICATION: AngleGuide(
        label_ko="규칙 의인화",
        instruction=(
            "실제로 적용되는 방 규칙과 이번 선택의 모순을 놀린다."
            " 규칙 원문에 없는 의무를 만들지 않는다."
        ),
    ),
    AttackAngle.ALTERNATIVE_MOCKERY: AngleGuide(
        label_ko="대안 조롱",
        instruction=(
            "사유에 나온 대안으로 선택을 놀린다."
            " '있는 운동화에 색칠해' 같은 황당한 농담은 가능하다."
            " 포장 가능·대중교통 운행·회사 지원·제품 가격 등 실제 이용 조건은 추측하지 않는다."
        ),
    ),
}


#: 조서에 근거가 있어야 성립하는 각도(9/18). 반복은 과거 지출·판결 기록이나 반복 집계,
#: 규칙 의인화는 방 규칙이 조서에 있어야 한다. 없는데 시키면 서기가 지어내고("42번째 키보드",
#: "규칙이 절교 선언") 검수관이 `UNGROUNDED_CLAIM` 으로 반려한다. 첫 지출일수록 그렇다.
HISTORY_ANGLES: frozenset[AttackAngle] = frozenset(
    {AttackAngle.REPETITION, AttackAngle.FUTURE_PROPHECY}
)
RULE_ANGLES: frozenset[AttackAngle] = frozenset({AttackAngle.RULE_PERSONIFICATION})
NEEDS_EVIDENCE: frozenset[AttackAngle] = HISTORY_ANGLES | RULE_ANGLES


def pick(post_id: str, offset: int = 0, *, skip: Collection[AttackAngle] = ()) -> AttackAngle:
    """같은 `post_id` 는 같은 각도, `offset` 을 올리면 다음 각도(6종 순환).

    01 §3.5 원문은 `crc32(post_id) % 6 + offset` 이지만 `offset ≥ 1` 에서 범위를
    넘는다(D-2). §4.2 가 요구하는 6종 순환이 되도록 바깥에서 한 번 더 `% 6` 한다.

    `skip`(9/18) 은 조서에 근거가 없어 쓸 수 없는 각도다. 해시로 정한 자리부터 순서대로
    돌되 그 각도는 건너뛴다. 같은 `post_id`·같은 조서면 같은 결과다. 전부 건너뛰면 무시한다.
    """
    index = crc32(post_id.encode("utf-8")) % len(ANGLE_ORDER)
    rotated = [ANGLE_ORDER[(index + i) % len(ANGLE_ORDER)] for i in range(len(ANGLE_ORDER))]
    candidates = [angle for angle in rotated if angle not in skip] or rotated
    return candidates[offset % len(candidates)]
