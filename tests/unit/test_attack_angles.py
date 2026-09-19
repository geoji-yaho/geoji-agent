"""공격 각도 6종과 `pick()`(01 §4.2)."""

from __future__ import annotations

import pytest

from geoji_ai.domain.attack_angles import (
    ANGLE_GUIDES,
    ANGLE_ORDER,
    HISTORY_ANGLES,
    NEEDS_EVIDENCE,
    RULE_ANGLES,
    AttackAngle,
    pick,
)

POST_IDS = ["post-1", "post-2", "9f0c4c21-0000-4000-8000-000000000001", "택시-9200"]


def test_각도는_6종이고_순서가_스크립트와_같다():
    assert len(ANGLE_ORDER) == 6
    assert ANGLE_ORDER == (
        AttackAngle.CONVERSION,
        AttackAngle.REPETITION,
        AttackAngle.EXCUSE_DISSECTION,
        AttackAngle.FUTURE_PROPHECY,
        AttackAngle.RULE_PERSONIFICATION,
        AttackAngle.ALTERNATIVE_MOCKERY,
    )
    assert set(ANGLE_GUIDES) == set(AttackAngle)


def test_마무리_방식_문장이_스크립트_원문이다():
    assert ANGLE_GUIDES[AttackAngle.CONVERSION].label_ko == "환산"
    assert ANGLE_GUIDES[AttackAngle.CONVERSION].instruction == (
        "금액을 다른 물건·횟수·시간으로 바꿔 비교한다(F0 인용). 마무리도 환산으로 끝낸다."
    )
    assert ANGLE_GUIDES[AttackAngle.ALTERNATIVE_MOCKERY].label_ko == "대안 조롱"
    assert ANGLE_GUIDES[AttackAngle.ALTERNATIVE_MOCKERY].instruction == (
        "무료·더 싼 대안을 과장되게 구체적으로 제시한다. 마무리는 그 대안을 명령한다."
    )
    for guide in ANGLE_GUIDES.values():
        assert guide.label_ko
        assert guide.instruction.endswith(".")


@pytest.mark.parametrize("post_id", POST_IDS)
def test_같은_post_id_는_같은_각도(post_id: str):
    assert pick(post_id) is pick(post_id)
    assert pick(post_id, 0) is pick(post_id)


@pytest.mark.parametrize("post_id", POST_IDS)
def test_offset_을_1_올리면_다음_각도(post_id: str):
    base = ANGLE_ORDER.index(pick(post_id))
    for offset in range(1, 7):
        assert pick(post_id, offset) is ANGLE_ORDER[(base + offset) % 6]


@pytest.mark.parametrize("post_id", POST_IDS)
def test_6종_순환(post_id: str):
    picked = [pick(post_id, offset) for offset in range(6)]
    assert set(picked) == set(AttackAngle)
    # 여섯 바퀴를 돌면 제자리로 온다.
    assert pick(post_id, 6) is pick(post_id, 0)
    assert pick(post_id, 13) is pick(post_id, 1)


def test_각도_값은_대문자_식별자_그대로():
    assert [member.name for member in AttackAngle] == [member.value for member in AttackAngle]


# --- skip (9/18) -----------------------------------------------------------


def test_근거_필요_각도는_반복과_규칙_의인화():
    assert HISTORY_ANGLES == {AttackAngle.REPETITION}
    assert RULE_ANGLES == {AttackAngle.RULE_PERSONIFICATION}
    assert NEEDS_EVIDENCE == HISTORY_ANGLES | RULE_ANGLES


@pytest.mark.parametrize("post_id", POST_IDS)
def test_skip_이_비면_예전과_같다(post_id: str):
    for offset in range(7):
        assert pick(post_id, offset, skip=()) is pick(post_id, offset)


@pytest.mark.parametrize("post_id", POST_IDS)
def test_skip_한_각도는_나오지_않고_나머지_5종을_돈다(post_id: str):
    picked = [pick(post_id, offset, skip=HISTORY_ANGLES) for offset in range(5)]
    assert AttackAngle.REPETITION not in picked
    assert set(picked) == set(AttackAngle) - HISTORY_ANGLES
    assert pick(post_id, 5, skip=HISTORY_ANGLES) is picked[0]


def test_skip_은_해시_자리에서_다음_근거_있는_각도로_민다():
    # "post-graph-c" 는 해시가 REPETITION(1) 이다. 이력이 없으면 바로 다음인 변명 해부.
    assert pick("post-graph-c") is AttackAngle.REPETITION
    assert pick("post-graph-c", skip=NEEDS_EVIDENCE) is AttackAngle.EXCUSE_DISSECTION
    assert pick("post-graph-c", 1, skip=NEEDS_EVIDENCE) is AttackAngle.FUTURE_PROPHECY
    # 규칙 의인화(4) 도 건너뛰어 대안 조롱(5) 으로.
    assert pick("post-graph-c", 2, skip=NEEDS_EVIDENCE) is AttackAngle.ALTERNATIVE_MOCKERY


@pytest.mark.parametrize("post_id", POST_IDS)
def test_전부_skip_이면_무시한다(post_id: str):
    assert pick(post_id, skip=set(AttackAngle)) is pick(post_id)
