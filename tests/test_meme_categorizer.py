import json

import pytest
from PIL import Image, ImageDraw

import meme_categorizer as mc
from meme_line_extractor import MemeLineExtractor


@pytest.fixture
def extractor():
    return MemeLineExtractor(use_rembg_if_available=False)


def codes(suggestions):
    return [s["code"] for s in suggestions]


def test_taxonomies_are_fully_labeled():
    assert mc.CONCEPT_FALLBACK in mc.CONCEPT_LABELS
    assert mc.EXPENSE_FALLBACK in mc.EXPENSE_LABELS
    for code in mc.CONCEPT_KEYWORDS:
        assert code in mc.CONCEPT_LABELS
    for code in mc.EXPENSE_KEYWORDS:
        assert code in mc.EXPENSE_LABELS
    assert len(mc.EXPENSE_CATEGORIES) == 11


@pytest.mark.parametrize(
    "text,expected",
    [
        ("당장 그만해 이 미친놈아", "ANGER"),
        ("부자 두 명이 거지 보고 웃는 사진", "MOCKERY"),
        ("드디어 내 돈을 다 쓴 놈을 찾아냈어", "BLAME"),
        ("어쩌라고 알빠임", "DISMISSAL"),
        ("잠깐 이럴 리가 없는데", "SHOCK"),
        ("전 돈 걱정을 하지않아용 걱정할 돈이 없으니까용", "SELF_MOCKERY"),
        ("어차피 포기했다", "RESIGNATION"),
        ("본 법정은 엄중히 선고한다", "ABSURD_SERIOUSNESS"),
        ("이건 진짜 훌륭하다 인정", "APPROVAL"),
    ],
)
def test_concept_keywords_pick_the_right_tone(text, expected):
    assert codes(mc.categorize_concepts(text))[0] == expected


def test_no_signal_falls_back_to_unclassified_for_review():
    result = mc.categorize_concepts("tvN")

    assert codes(result) == [mc.CONCEPT_FALLBACK]
    assert result[0]["confidence"] == 0.0
    assert result[0]["matched"] == []


def test_exclamation_marks_reinforce_a_tone_the_words_already_carry():
    """normalize() strips punctuation, so shouting is read off the raw string first."""
    shouted = mc.categorize_concepts("당장 그만해!!")

    assert codes(shouted) == ["ANGER"]
    assert "!!" in shouted[0]["matched"]


def test_punctuation_alone_decides_nothing():
    """'…되고 싶으세요? 네!' is a question and an exclamation, and neither tone."""
    assert codes(mc.categorize_concepts("돈 내놔!!")) == [mc.CONCEPT_FALLBACK]
    assert codes(mc.categorize_concepts("이걸 샀다고?")) == [mc.CONCEPT_FALLBACK]
    assert codes(mc.categorize_concepts("그런 사람 되고 싶으세요? 네!")) == [mc.CONCEPT_FALLBACK]


def test_a_single_weak_hit_is_not_enough_evidence():
    """A share of the evidence says nothing about how much evidence there was."""
    assert codes(mc.categorize_concepts("그냥")) == [mc.CONCEPT_FALLBACK]
    assert codes(mc.categorize_concepts("어차피 그냥")) == ["RESIGNATION"]


def test_competing_concepts_are_ranked_and_share_confidence():
    result = mc.categorize_concepts("타고난 거지 보고 웃는다 ㅋㅋ 돈이 없으니까")

    assert codes(result)[:2] == ["MOCKERY", "SELF_MOCKERY"]
    assert sum(s["confidence"] for s in result) == pytest.approx(1.0, abs=0.01)
    assert result[0]["confidence"] > result[1]["confidence"]


def test_top_n_caps_the_number_of_suggestions():
    text = "타고난 거지네 ㅋㅋ 돈이 없으니까 어차피 포기했다"

    assert len(mc.categorize_concepts(text, top_n=2)) == 2


def test_min_confidence_above_every_share_falls_back_to_review():
    text = "타고난 거지네 ㅋㅋ 돈이 없으니까 어차피 포기했다"

    result = mc.categorize_concepts(text, min_confidence=0.9)

    assert codes(result) == [mc.CONCEPT_FALLBACK]
    assert result[0]["confidence"] == 0.0


def test_nested_keyword_is_counted_once():
    """'걱정할 돈' must not also score as the shorter '돈이 없' inside the same hit."""
    result = mc.categorize_concepts("넷플릭스 구독료")

    expenses = mc.categorize_expenses("넷플릭스 구독료")
    subscription = next(s for s in expenses if s["code"] == "SUBSCRIPTION")
    assert subscription["matched"] == ["넷플릭스", "구독료"]
    assert result is not None


@pytest.mark.parametrize(
    "text,expected",
    [
        ("아메리카노 또 시켰네", "CAFE_SNACK"),
        ("배민으로 치킨 시킴", "DELIVERY"),
        ("택시비가 아깝지도 않냐", "TRANSPORT_TAXI"),
        ("관리비가 또 올랐어", "LIVING"),
    ],
)
def test_expense_categories_still_work_as_the_secondary_facet(text, expected):
    assert codes(mc.categorize_expenses(text))[0] == expected


def test_reaction_meme_has_a_concept_but_no_expense_category():
    text = "부자 두 명이 거지 보고 웃는 사진"

    assert codes(mc.categorize_concepts(text)) == ["MOCKERY"]
    assert codes(mc.categorize_expenses(text)) == [mc.EXPENSE_FALLBACK]


def test_needs_review_tracks_the_concept_not_the_expense_category():
    tagged = mc.build_metadata(mc.categorize_concepts("보고 웃는 사진"))
    untagged = mc.build_metadata(mc.categorize_concepts("tvN"))

    assert tagged["needs_review"] is False
    assert untagged["needs_review"] is True
    assert untagged["generated_by"] == "meme_categorizer/keyword-v1"


def test_process_file_writes_a_concept_sidecar(extractor, tmp_path):
    source = tmp_path / "meme.png"
    image = Image.new("RGB", (300, 120), (255, 255, 255))
    ImageDraw.Draw(image).ellipse((20, 20, 100, 100), outline=(0, 0, 0), width=4)
    image.save(source)

    out = tmp_path / "meme_lineart.png"
    extractor.process_file(str(source), str(out), remove_bg=False, text_mode="none")

    sidecar = tmp_path / "meme_lineart.json"
    assert sidecar.exists()

    data = json.loads(sidecar.read_text(encoding="utf-8"))
    assert data["asset_path"] == str(out)
    assert data["source_path"] == str(source)
    assert codes(data["concepts"]) == [mc.CONCEPT_FALLBACK]
    assert "expense_categories" in data


def test_sidecar_can_be_disabled(extractor, tmp_path):
    source = tmp_path / "meme.png"
    Image.new("RGB", (120, 120), (255, 255, 255)).save(source)

    out = tmp_path / "meme_lineart.png"
    extractor.process_file(
        str(source), str(out), remove_bg=False, text_mode="none", write_categories=False
    )

    assert not (tmp_path / "meme_lineart.json").exists()
