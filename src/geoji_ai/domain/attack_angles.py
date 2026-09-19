"""공격 각도 6종과 마무리 방식 문장(01 §3.5).

서버가 판결마다 하나를 지정한다. 모델이 고르지 않는다.
라벨·문장은 `scripts/probe_writer_latency.py:235-242` 의 `ANGLES` 원문이다.
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
            "금액을 다른 물건·횟수·시간으로 바꿔 비교한다(F0 인용). 마무리도 환산으로 끝낸다."
        ),
    ),
    AttackAngle.REPETITION: AngleGuide(
        label_ko="반복",
        instruction=(
            "횟수와 패턴을 세어 준다. 이번이 몇 번째인지, 같은 사유가 몇 번째인지."
            " 마무리는 다음 횟수를 예고한다."
        ),
    ),
    AttackAngle.EXCUSE_DISSECTION: AngleGuide(
        label_ko="변명 해부",
        instruction=(
            "사유를 그대로 인용한 뒤 그 논리를 한 문장으로 무너뜨린다."
            " 마무리는 진짜 사유를 대신 써 준다."
        ),
    ),
    AttackAngle.FUTURE_PROPHECY: AngleGuide(
        label_ko="미래 예언",
        instruction=(
            "이 속도면 월말·연말에 어떻게 되는지 구체적으로 예언한다."
            " 마무리는 예언의 날짜를 박는다."
        ),
    ),
    AttackAngle.RULE_PERSONIFICATION: AngleGuide(
        label_ko="규칙 의인화",
        instruction=(
            "방 규칙을 사람처럼 다룬다. 규칙이 실망했다, 포기했다, 절교했다까지."
            " 규칙은 죽지 않는다. 마무리는 규칙의 한마디로 끝낸다."
        ),
    ),
    AttackAngle.ALTERNATIVE_MOCKERY: AngleGuide(
        label_ko="대안 조롱",
        instruction="무료·더 싼 대안을 과장되게 구체적으로 제시한다. 마무리는 그 대안을 명령한다.",
    ),
}


#: 조서에 근거가 있어야 성립하는 각도(9/18). 반복은 과거 지출·판결 기록이나 반복 집계,
#: 규칙 의인화는 방 규칙이 조서에 있어야 한다. 없는데 시키면 서기가 지어내고("42번째 키보드",
#: "규칙이 절교 선언") 검수관이 `UNGROUNDED_CLAIM` 으로 반려한다. 첫 지출일수록 그렇다.
HISTORY_ANGLES: frozenset[AttackAngle] = frozenset({AttackAngle.REPETITION})
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
