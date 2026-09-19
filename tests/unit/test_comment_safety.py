"""댓글 안전 규칙 8가지(04 §3.5, 01 §3.5)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from geoji_ai.domain.comment_safety import Comment, Reason, check, filter_comments, normalize
from geoji_ai.domain.intensity import Intensity

DEFENDANT = "u-defendant"
SAFE_TEXT = "이번 달만 벌써 세 번째 배달이라니 지갑이 울고 있어요"


def _comment(content: str = SAFE_TEXT, **overrides) -> Comment:
    base = Comment(
        comment_id="c-1",
        version=1,
        room_id="r-1",
        post_id="p-1",
        post_status="JUDGED",
        author_id="u-friend",
        content=content,
        created_at=datetime(2026, 9, 14, 12, 0, tzinfo=UTC),
    )
    return replace(base, **overrides)


def _check(comment: Comment, intensity: Intensity | str = Intensity.mild) -> list[Reason]:
    return check(comment, defendant_id=DEFENDANT, room_intensity=intensity)


def test_기준_댓글은_통과():
    assert _check(_comment()) == []


# 1. post_status


def test_1_JUDGED_게시물_댓글은_통과():
    assert Reason.POST_NOT_JUDGED not in _check(_comment(post_status="JUDGED"))


def test_1_JUDGED가_아닌_게시물_댓글은_제외():
    assert Reason.POST_NOT_JUDGED in _check(_comment(post_status="PENDING"))


# 2. 길이 21~200


@pytest.mark.parametrize("length", [21, 200])
def test_2_길이_경계_안은_통과(length):
    assert Reason.LENGTH_OUT_OF_RANGE not in _check(_comment("가" * length))


@pytest.mark.parametrize("length", [20, 201])
def test_2_길이_경계_밖은_제외(length):
    assert Reason.LENGTH_OUT_OF_RANGE in _check(_comment("가" * length))


def test_2_길이는_앞뒤_공백을_빼고_센다():
    assert Reason.LENGTH_OUT_OF_RANGE in _check(_comment("   " + "가" * 20 + "   "))


# 3. URL·전화·이메일


def test_3_연락처_없는_댓글은_통과():
    text = "3.5배 비싼 커피를 이틀 연속 사 먹은 건 좀 심했다"
    assert Reason.CONTACT_INFO not in _check(_comment(text))


@pytest.mark.parametrize(
    "text",
    [
        "이거 봐 https://example.com 여기서 더 싸게 팔더라",
        "www.coupang 에서 반값인데 왜 거기서 샀어요 진짜로",
        "여기 가격 비교해봐 danawa.com 에서 반값이던데 참",
        "나한테 연락해 010-1234-5678 로 싸게 파는 곳 알려줄게",
        "나한테 연락해 010 1234 5678 로 싸게 파는 곳 알려줄게",
        "나한테 연락해 01012345678 로 싸게 파는 곳 알려줄게요",
        "지역번호로 걸어 02-123-4567 로 싸게 파는 곳 알려줄게",
        "메일 줘 friend.kim+deal@mail.co.kr 로 쿠폰 보내줄게요",
    ],
)
def test_3_URL_전화_이메일이_있으면_제외(text):
    assert Reason.CONTACT_INFO in _check(_comment(text))


# 4. 죽음 어휘


def test_4_죽음_어휘가_없으면_통과():
    assert Reason.DEATH_WORD not in _check(_comment(), Intensity.hell)


@pytest.mark.parametrize("intensity", list(Intensity))
def test_4_죽음_어휘가_있으면_모든_강도에서_제외(intensity):
    text = "이렇게 쓰다가는 월말에 진짜 뒤져 버릴 것 같다 조심해"
    assert Reason.DEATH_WORD in _check(_comment(text), intensity)


# 5. 강도별 욕


def test_5_hell은_허용_목록_욕을_통과():
    text = "미친 이걸 또 샀다고 진짜 실화냐 이 새끼 지갑 괜찮냐"
    assert Reason.PROFANITY not in _check(_comment(text), Intensity.hell)


def test_5_hell은_옛_허용_목록_밖_욕도_통과():
    """9/16 결정: 지옥맛 방은 비속어를 막지 않는다.

    (원문) 허용 목록 12개 밖이면 `PROFANITY` 로 걸렀다.
    """
    text = "씨발 이걸 또 샀다고 진짜 실화냐 지갑 괜찮은 거 맞냐"
    assert Reason.PROFANITY not in _check(_comment(text), Intensity.hell)


def test_5_hell은_허용_구절_일부만_맞는_욕도_통과():
    """(원문) 허용 구절 "개같은 선택" 과 달리 "개같" 단독은 목록 밖이라 걸렀다."""
    text = "이런 개같은 소비를 또 하다니 지갑이 불쌍해 보인다 진짜"
    assert Reason.PROFANITY not in _check(_comment(text), Intensity.hell)


def test_5_hell도_자해_어휘는_그대로_막는다():
    """비속어만 푼 것이다. 안전 규칙은 강도와 무관하다."""
    text = "이 돈 쓸 바에는 그냥 손목 긋는 게 낫겠다 정신 좀 차려라"
    assert Reason.DEATH_WORD in _check(_comment(text), Intensity.hell)


@pytest.mark.parametrize("intensity", [Intensity.mild, "spicy"])
def test_5_mild_spicy는_욕이_없으면_통과(intensity):
    assert Reason.PROFANITY not in _check(_comment(), intensity)


@pytest.mark.parametrize("intensity", [Intensity.mild, Intensity.spicy])
def test_5_mild_spicy는_hell_허용_욕도_제외(intensity):
    text = "씨발 이걸 또 샀다고 진짜 실화냐 지갑 괜찮은 거 맞냐고"
    assert Reason.PROFANITY in _check(_comment(text), intensity)


# 6. 작성자 = 피고인


def test_6_작성자가_피고인이_아니면_통과():
    assert Reason.AUTHOR_IS_DEFENDANT not in _check(_comment(author_id="u-friend"))


def test_6_작성자가_피고인이면_제외():
    assert Reason.AUTHOR_IS_DEFENDANT in _check(_comment(author_id=DEFENDANT))


# 7. @ 호출


def test_7_골뱅이가_없으면_통과():
    assert Reason.MENTION not in _check(_comment())


def test_7_골뱅이_호출이_있으면_제외():
    assert Reason.MENTION in _check(_comment("@민수 너도 이거 샀잖아 같이 재판 받아야 한다고"))


# 8. 정규화 중복 제거


def test_8_정규화가_다르면_둘_다_남긴다():
    first = _comment(comment_id="c-1")
    second = _comment("이번 달만 벌써 네 번째 배달이라니 지갑이 울고 있어요", comment_id="c-2")
    kept = filter_comments([first, second], defendant_id=DEFENDANT, room_intensity="mild")
    assert [c.comment_id for c in kept] == ["c-1", "c-2"]


def test_8_정규화가_같으면_먼저_온_것만_남긴다():
    first = _comment("Wow  이번 달만 벌써 세 번째 배달이라니 지갑이 운다", comment_id="c-1")
    second = _comment(" ＷＯＷ 이번 달만   벌써 세 번째 배달이라니 지갑이 운다 ", comment_id="c-2")
    third = _comment(comment_id="c-3")
    kept = filter_comments([first, second, third], defendant_id=DEFENDANT, room_intensity="mild")
    assert [c.comment_id for c in kept] == ["c-1", "c-3"]
    assert normalize(first.content) == normalize(second.content)


def test_목록_필터는_규칙_위반_댓글을_뺀다():
    bad = _comment(comment_id="c-bad", author_id=DEFENDANT)
    good = _comment(comment_id="c-good")
    kept = filter_comments([bad, good], defendant_id=DEFENDANT, room_intensity=Intensity.spicy)
    assert [c.comment_id for c in kept] == ["c-good"]


def test_모르는_강도는_ValueError():
    with pytest.raises(ValueError):
        _check(_comment(), "HELL")
