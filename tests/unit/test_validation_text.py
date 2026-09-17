"""서버 검증 ⑤ 텍스트 규칙 7항(05 §3.4). 항마다 양성·음성 + 삭제 후 재조립·`evidence_refs`."""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import Any

import pytest

from geoji_ai.contracts.case import JurySnapshot
from geoji_ai.contracts.writer import WriterDraft
from geoji_ai.domain.validation import (
    DUPLICATE_INTENSITY,
    INTENSITY_MISMATCH,
    INTENSITY_SET_MISMATCH,
    PROFANITY_OUT_OF_LIST,
    SCHEMA_INVALID,
    SELF_HARM_LEXICON,
    UNGROUNDED_CLAIM,
    EvidenceRef,
    apply_text_rules,
    is_banmal_suspect,
)

LABEL_MAP = {f"F{n}": f"uuid-{n}" for n in range(4)}

FACT = {
    "text": "일주일에 세 번, 택시가 출근 수단이 됐습니다.",
    "kind": "fact",
    "evidence_labels": ["F1"],
}
OPINION_1 = {"text": "지하철은 오늘도 정시에 왔습니다.", "kind": "opinion", "evidence_labels": []}
OPINION_2 = {"text": "알람을 하나 더 맞추세요.", "kind": "opinion", "evidence_labels": []}


def _text(intensity: str, statement: list[dict[str, Any]] | None = None, **over: Any) -> dict:
    return {
        "intensity": intensity,
        "headline": "택시가 출근 수단이 된 사건",
        "statement": copy.deepcopy(statement if statement is not None else [FACT, OPINION_1]),
        "banter_strategy": "EXCUSE_STRIPPING",
        "selected_candidate_id": None,
        "attack_angle": "CONVERSION",
        "source": "AI",
        **over,
    }


def _draft(*texts: dict[str, Any], meme_tag: str = "GUILTY_LIGHT") -> dict[str, Any]:
    return {
        "schema_version": 1,
        "texts": list(texts) or [_text("spicy")],
        "meme_tag": meme_tag,
        "meme_hints": None,
    }


def _jury(result: str = "guilty", targets: tuple[str, ...] = ("spicy",)) -> dict[str, Any]:
    return {
        "result": result,
        "policy": {
            "allowed_sentences": [
                {"code": "probation", "rank": 1},
                {"code": "oneDay", "rank": 2},
                {"code": "life", "rank": 3},
            ]
        },
        "target_intensities": list(targets),
    }


def _sentencing(sentence: str = "oneDay") -> dict[str, Any]:
    return {
        "schema_version": 1,
        "sentence": sentence,
        "sentencing_reason": None,
        "reason_source": "TEMPLATE",
        "evidence_labels": [],
        "aggravating": [],
        "mitigating": [],
    }


def _run(draft: dict[str, Any], jury: dict[str, Any] | None = None, sentencing: Any = "default"):
    return apply_text_rules(
        draft,
        LABEL_MAP,
        _sentencing() if sentencing == "default" else sentencing,
        jury if jury is not None else _jury(targets=tuple(t["intensity"] for t in draft["texts"])),
    )


def _codes(result) -> list[str]:
    return [issue.code for issue in result.issues]


def test_clean_draft_passes_unchanged() -> None:
    draft = _draft(_text("mild"), _text("spicy"), _text("hell"))
    result = _run(draft)
    assert result.issues == []
    assert result.draft == draft


def test_accepts_models_and_does_not_mutate_input() -> None:
    draft = _draft(_text("spicy", [FACT, OPINION_1, {**OPINION_2, "kind": "fact"}]))
    before = copy.deepcopy(draft)
    jury = JurySnapshot.model_validate(_full_jury())
    result = apply_text_rules(WriterDraft.model_validate(draft), LABEL_MAP, None, jury)
    assert draft == before
    assert len(result.draft["texts"][0]["statement"]) == 2
    assert _codes(result) == []


def _full_jury() -> dict[str, Any]:
    return {
        "verdict_id": "v1",
        "verdict_version": 1,
        "result": "guilty",
        "vote_counts": {"guilty": 3, "notGuilty": 1},
        "guilty_ratio": 0.75,
        "confirmed_at": datetime(2026, 9, 14, tzinfo=UTC),
        "deadline_at": datetime(2026, 9, 14, 0, 0, 10, tzinfo=UTC),
        "policy": {
            "version": "p1",
            "allowed_sentences": _jury()["policy"]["allowed_sentences"],
            "fallback_sentence": "probation",
            "reason_required": True,
        },
        "target_intensities": ["spicy"],
        "default_intensity": "spicy",
    }


# --- 1 근거 없는 fact/claim ---------------------------------------------------------------


def test_rule1_ungrounded_statements_deleted_and_reassembled() -> None:
    no_label = {"text": "지난달에도 택시를 탔습니다.", "kind": "fact", "evidence_labels": []}
    outside_map = {"text": "방 규칙은 택시 금지입니다.", "kind": "claim", "evidence_labels": ["F9"]}
    grounded = {
        "text": "이번 달 택시비는 4만 원입니다.",
        "kind": "claim",
        "evidence_labels": ["F0"],
    }
    draft = _draft(_text("spicy", [no_label, FACT, outside_map, OPINION_1, grounded]))
    result = _run(draft)
    assert [s["text"] for s in result.draft["texts"][0]["statement"]] == [
        FACT["text"],
        OPINION_1["text"],
        grounded["text"],
    ]
    assert UNGROUNDED_CLAIM not in _codes(result)
    assert SCHEMA_INVALID not in _codes(result)


def test_rule1_opinion_without_labels_kept() -> None:
    result = _run(_draft(_text("spicy", [OPINION_1, OPINION_2])))
    assert len(result.draft["texts"][0]["statement"]) == 2
    assert _codes(result) == []


def test_rule1_nothing_left_is_ungrounded_claim() -> None:
    ungrounded = [
        {"text": "지난달에도 택시를 탔습니다.", "kind": "fact", "evidence_labels": []},
        {"text": "방 규칙은 택시 금지입니다.", "kind": "claim", "evidence_labels": ["F9"]},
    ]
    result = _run(_draft(_text("spicy", ungrounded)))
    assert result.draft["texts"][0]["statement"] == []
    assert UNGROUNDED_CLAIM in _codes(result)


def test_evidence_refs_follow_reassembled_positions() -> None:
    no_label = {"text": "지난달에도 택시를 탔습니다.", "kind": "fact", "evidence_labels": []}
    two_labels = {
        "text": "이번 달 네 번째 택시입니다.",
        "kind": "fact",
        "evidence_labels": ["F0", "F2"],
    }
    result = _run(_draft(_text("spicy", [no_label, FACT, two_labels])))
    assert result.evidence_refs == [
        EvidenceRef("spicy", "statement[0]", "F1", "uuid-1"),
        EvidenceRef("spicy", "statement[1]", "F0", "uuid-0"),
        EvidenceRef("spicy", "statement[1]", "F2", "uuid-2"),
    ]


# --- 2 본문 ID 문자열 ---------------------------------------------------------------------


def test_rule2_tainted_sentence_deleted_when_two_remain() -> None:
    tainted = {"text": "F1 기준 세 번째입니다.", "kind": "opinion", "evidence_labels": []}
    result = _run(_draft(_text("spicy", [FACT, tainted, OPINION_1])))
    assert [s["text"] for s in result.draft["texts"][0]["statement"]] == [
        FACT["text"],
        OPINION_1["text"],
    ]
    assert _codes(result) == []


def test_rule2_ids_removed_when_too_few_remain() -> None:
    tainted = {
        "text": "변명은 F1, F2 기준으로 끝났습니다.",
        "kind": "opinion",
        "evidence_labels": [],
    }
    result = _run(_draft(_text("spicy", [tainted], headline="F0 택시가 출근 수단")))
    text = result.draft["texts"][0]
    assert [s["text"] for s in text["statement"]] == ["변명은 기준으로 끝났습니다."]
    assert text["headline"] == "택시가 출근 수단"
    assert _codes(result) == []


def test_rule2_negative_no_label_pattern() -> None:
    fine = {"text": "FAQ 에도 없는 변명입니다.", "kind": "opinion", "evidence_labels": []}
    result = _run(_draft(_text("spicy", [FACT, fine])))
    assert result.draft["texts"][0]["statement"][1]["text"] == fine["text"]


# --- 3 길이·문장 수 -----------------------------------------------------------------------


def test_rule3_headline_over_30() -> None:
    result = _run(_draft(_text("spicy", headline="가" * 31)))
    assert _codes(result) == [SCHEMA_INVALID]


def test_rule3_statement_total_over_300() -> None:
    long = {"text": "가" * 160 + "입니다.", "kind": "opinion", "evidence_labels": []}
    result = _run(_draft(_text("spicy", [long, copy.deepcopy(long)])))
    assert _codes(result) == [SCHEMA_INVALID]


def test_rule3_statement_count_out_of_range() -> None:
    five = [FACT, OPINION_1, OPINION_2, OPINION_1, OPINION_2]
    assert _codes(_run(_draft(_text("spicy", five)))) == [SCHEMA_INVALID]


def test_rule3_count_checked_after_deletion() -> None:
    no_label = {"text": "지난달에도 택시를 탔습니다.", "kind": "fact", "evidence_labels": []}
    result = _run(_draft(_text("spicy", [no_label, OPINION_1])))
    assert result.draft["texts"][0]["statement"] == [OPINION_1]
    assert _codes(result) == []


def test_rule3_negative_at_limits() -> None:
    four = [FACT, OPINION_1, OPINION_2, OPINION_1]
    result = _run(_draft(_text("spicy", four, headline="가" * 30)))
    assert SCHEMA_INVALID not in _codes(result)


# --- 4 meme_tag 교정 ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("result_value", "sentence", "given", "expected"),
    [
        ("guilty", "life", "GUILTY_LIGHT", "GUILTY_HEAVY"),
        ("guilty", "oneDay", "GUILTY_HEAVY", "GUILTY_LIGHT"),
        ("guilty", "probation", "NOT_GUILTY", "GUILTY_LIGHT"),
        ("notGuilty", None, "GUILTY_HEAVY", "NOT_GUILTY"),
        ("agree", None, "REJECTED", "APPROVED"),
        ("disagree", None, "APPROVED", "REJECTED"),
    ],
)
def test_rule4_meme_tag_corrected(
    result_value: str, sentence: str | None, given: str, expected: str
) -> None:
    result = apply_text_rules(
        _draft(_text("spicy"), meme_tag=given),
        LABEL_MAP,
        _sentencing(sentence) if sentence else None,
        _jury(result_value),
    )
    assert result.draft["meme_tag"] == expected
    assert result.issues == []


def test_rule4_negative_consistent_tag_kept() -> None:
    result = _run(_draft(_text("spicy"), meme_tag="GUILTY_LIGHT"))
    assert result.draft["meme_tag"] == "GUILTY_LIGHT"


# --- 5 강도 집합 --------------------------------------------------------------------------


def test_rule5_intensity_set_mismatch() -> None:
    result = _run(_draft(_text("spicy")), jury=_jury(targets=("spicy", "hell")))
    assert INTENSITY_SET_MISMATCH in _codes(result)


def test_rule5_duplicate_intensity() -> None:
    result = _run(_draft(_text("spicy"), _text("spicy")), jury=_jury(targets=("spicy",)))
    assert DUPLICATE_INTENSITY in _codes(result)


def test_rule5_negative_matching_set() -> None:
    result = _run(_draft(_text("hell"), _text("spicy")), jury=_jury(targets=("spicy", "hell")))
    assert INTENSITY_SET_MISMATCH not in _codes(result)
    assert DUPLICATE_INTENSITY not in _codes(result)


# --- 6 욕·죽음 단어 -----------------------------------------------------------------------


def _with_sentence(intensity: str, sentence: str) -> dict[str, Any]:
    return _draft(_text(intensity, [FACT, {**OPINION_1, "text": sentence}]))


@pytest.mark.parametrize(
    ("intensity", "sentence", "code"),
    [
        ("spicy", "미친 선택입니다.", PROFANITY_OUT_OF_LIST),
        ("mild", "지랄 같은 출근길이었겠어요.", PROFANITY_OUT_OF_LIST),
        ("mild", "통장이 죽어가고 있어요.", SELF_HARM_LEXICON),
        ("spicy", "지갑이 뒤져버렸습니다.", SELF_HARM_LEXICON),
        ("hell", "이러다 자살각이다", SELF_HARM_LEXICON),
    ],
)
def test_rule6_positive(intensity: str, sentence: str, code: str) -> None:
    assert code in _codes(_run(_with_sentence(intensity, sentence)))


@pytest.mark.parametrize(
    ("intensity", "sentence"),
    [
        ("hell", "미친 선택이다 ㅋㅋ"),
        ("hell", "돌았냐 이 새끼야"),
        ("hell", "개같은 선택 레전드 찍었네"),
        ("hell", "통장 사망 선고 나왔다"),
        ("spicy", "통장 장례식은 다음 주입니다."),
        # 9/16 결정: 지옥맛은 비속어를 차단하지 않는다.
        # 옛 허용 목록 밖·활용형·2회 반복 모두 통과한다.
        ("hell", "씨발 택시 또 탔냐"),
        ("hell", "너 돌았어 진짜"),
        ("hell", "미친 선택이고 미친 변명이다"),
        ("hell", "이 새끼 택시 기사 새끼랑 친구냐"),
        ("hell", "ㅋㅋ 또 탔네 ㅋㅋ"),
        # 9/16 실측에서 판결문을 통째로 템플릿으로 만든 문장(15 §6).
        ("hell", "지하철 8번 탈 돈을 택시로 처먹네"),
        ("hell", "존나 어이없는 판단이다"),
    ],
)
def test_rule6_negative(intensity: str, sentence: str) -> None:
    codes = _codes(_run(_with_sentence(intensity, sentence)))
    assert PROFANITY_OUT_OF_LIST not in codes
    assert SELF_HARM_LEXICON not in codes


# --- 7 이모지·U+FFFD·닳은 문구·mild 반말 ----------------------------------------------------


@pytest.mark.parametrize(
    ("intensity", "sentence"),
    [
        ("spicy", "택시비가 월세입니다 \U0001f602"),
        ("hell", "택시 또 탔네�"),
        ("spicy", "정신 차리십시오."),
        ("mild", "택시 또 탔냐."),
        ("mild", "이번 달만 벌써 세 번째다."),
        ("mild", "지하철 타면 되잖아 좀 해."),
    ],
)
def test_rule7_positive(intensity: str, sentence: str) -> None:
    assert INTENSITY_MISMATCH in _codes(_run(_with_sentence(intensity, sentence)))


@pytest.mark.parametrize(
    ("intensity", "sentence"),
    [
        ("mild", "택시를 또 타셨습니다."),
        ("mild", "다음엔 지하철을 타 보세요."),
        ("mild", "이번 달 세 번째예요."),
        ("mild", "택시비 12,000원."),
        ("spicy", "택시 또 탔냐."),
        ("hell", "택시 또 탔냐."),
    ],
)
def test_rule7_negative(intensity: str, sentence: str) -> None:
    assert INTENSITY_MISMATCH not in _codes(_run(_with_sentence(intensity, sentence)))


def test_rule7_mild_headline_checked() -> None:
    result = _run(_draft(_text("mild", headline="택시는 사치다")))
    assert INTENSITY_MISMATCH in _codes(result)


@pytest.mark.parametrize(
    ("sentence", "expected"),
    [
        ("탔냐.", True),
        ("했다.", True),
        ("좀 해", True),
        ("그렇지", True),
        ("합니다.", False),
        ("했습니다.", False),
        ("하세요!", False),
        ("그래요", False),
        ("12,000원.", False),
    ],
)
def test_banmal_heuristic(sentence: str, expected: bool) -> None:
    assert is_banmal_suspect(sentence) is expected
