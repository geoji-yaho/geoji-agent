"""배심원 사유의 명시적 원화 금액을 입력 근거와 대조한다(#73)."""

import pytest

from geoji_ai.domain.juror_amounts import has_grounded_amounts


@pytest.mark.parametrize(
    "text,amount_krw",
    [
        ("400만원은 비싸요.", 4_000_000),
        ("4백만 원이나 썼네.", 4_000_000),
        ("무려4,000,000원을 썼네.", 4_000_000),
        ("0.04억원이면 큰돈이지.", 4_000_000),
        ("1만 2천원짜리 택시라니.", 12_000),
        ("1만2,000원은 아깝다.", 12_000),
        ("1.2만 원이면 적당해.", 12_000),
        ("1억2천3백45만6천7백89원이네.", 123_456_789),
        ("１만２천원을 썼네.", 12_000),
        ("3개나 있는데 2개 더 샀네.", 12_000),
        ("한 번 더 생각하지.", 12_000),
        ("아이폰16 400만원은 비싸요.", 4_000_000),
    ],
)
def test_입력과_같은_금액이나_금액_없는_사유는_허용한다(text, amount_krw):
    assert has_grounded_amounts(text, amount_krw=amount_krw, item="물건", post_reason="필요해서")


@pytest.mark.parametrize(
    "text",
    [
        "4천만원짜리 노트북이라니.",
        "40,000,000원은 너무 커.",
        "0.4억원을 태웠네.",
        "400만원에 10만원 더 썼지.",
        "-400만원이면 돈 벌었네.",
        "4,00만원을 썼네.",
        "4.000.000원을 썼네.",
        "4백백만원을 썼네.",
        "4만억원이라니.",
        "4000000.5원이네.",
        "0만원은 무료잖아.",
    ],
)
def test_없는_금액이나_해석할_수_없는_금액은_거절한다(text):
    assert not has_grounded_amounts(
        text, amount_krw=4_000_000, item="노트북", post_reason="업무에 필요해서"
    )


@pytest.mark.parametrize(
    "item,post_reason",
    [("5만원 상품권", "선물하려고"), ("택시", "5만원 들 줄 알았지만 할인받았어요")],
)
def test_항목이나_사유에_명시된_다른_금액도_근거로_허용한다(item, post_reason):
    assert has_grounded_amounts(
        "5만 원 대신 1만2천원으로 해결했네요.",
        amount_krw=12_000,
        item=item,
        post_reason=post_reason,
    )


def test_입력의_일반_숫자를_돈의_근거로_쓰지_않는다():
    assert not has_grounded_amounts(
        "3만원이나 썼네.", amount_krw=12_000, item="3개 묶음", post_reason="30일 쓸 예정"
    )


def test_입력의_잘못된_금액_표기를_근거로_쓰지_않는다():
    assert not has_grounded_amounts(
        "4,00만원이네.", amount_krw=4_000_000, item="물건", post_reason="4,00만원을 예상"
    )
