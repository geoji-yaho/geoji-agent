"""TEMPLATE 초안 조립·치환(05 §3.3 서기 실패·D-19, 10 §10 토큰 표)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from geoji_ai.contracts.case import JurySnapshot
from geoji_ai.domain.attack_angles import pick
from geoji_ai.domain.validation import apply_text_rules
from geoji_ai.graphs.templates import (
    TEMPLATE_BANTER_STRATEGY,
    TemplateUnavailable,
    load_templates,
    render_statement,
    sentencing_reason_template,
    template_text_draft,
)

ROOT = Path(__file__).resolve().parents[2]
POST_ID = "post-taxi-20260907-0852"


def jury(**update: Any) -> JurySnapshot:
    data = json.loads((ROOT / "contracts/fixtures/jury-guilty-75.json").read_text("utf-8"))
    data.update(update)
    return JurySnapshot.model_validate(data)


def test_guilty_statement_substitutes_n_m_sentence_label() -> None:
    """유죄: {n}=표 합, {m}=유죄 표, {sentence_label}=형량 라벨."""
    assert render_statement(jury(), "oneDay") == [
        "배심원 4인 중 3인이 유죄로 판단했습니다. 형량: 징역 1일 (내일 하루 무지출)"
    ]


def test_guilty_template_draft_shape() -> None:
    draft = template_text_draft("hell", jury(), "oneDay", POST_ID)
    assert draft.headline == "유죄"
    assert [s.text for s in draft.statement] == [
        "배심원 4인 중 3인이 유죄로 판단했습니다.",
        "형량: 징역 1일 (내일 하루 무지출)",
    ]
    assert all(s.kind == "opinion" and s.evidence_labels == [] for s in draft.statement)
    assert draft.source == "TEMPLATE"
    assert draft.selected_candidate_id is None
    assert draft.attack_angle == pick(POST_ID, 0)
    assert draft.banter_strategy == TEMPLATE_BANTER_STRATEGY


@pytest.mark.parametrize(
    ("result", "votes", "headline", "texts"),
    [
        (
            "agree",
            {"agree": 3, "disagree": 1},
            "동의",
            ["배심원단이 구매를 승인했습니다.", "후회는 본인 몫입니다."],
        ),
        (
            "disagree",
            {"agree": 1, "disagree": 3},
            "기각",
            ["배심원단이 구매를 기각했습니다.", "지갑을 닫으십시오."],
        ),
    ],
)
def test_agree_disagree_template_drafts(
    result: str, votes: dict[str, int], headline: str, texts: list[str]
) -> None:
    draft = template_text_draft("mild", jury(result=result, vote_counts=votes), None, POST_ID)
    assert draft.headline == headline
    assert [s.text for s in draft.statement] == texts


def test_not_guilty_template_cannot_meet_statement_count() -> None:
    """무죄 템플릿은 1문장이라 TextDraft(2~4문장)를 만들 수 없다 — 문구를 지어내지 않는다."""
    j = jury(result="notGuilty", vote_counts={"guilty": 1, "notGuilty": 3})
    assert render_statement(j, None) == [
        "배심원단은 이 지출에 정상 참작의 여지가 있다고 판단했습니다."
    ]
    with pytest.raises(TemplateUnavailable):
        template_text_draft("mild", j, None, POST_ID)


def test_guilty_without_sentence_is_unavailable() -> None:
    with pytest.raises(TemplateUnavailable):
        template_text_draft("mild", jury(), None, POST_ID)


def test_sentencing_reason_template_guilty_only() -> None:
    """D-19: 유죄만 `형량: {sentence_label}`, 나머지 None."""
    assert sentencing_reason_template(jury(), "probation") == "형량: 집행유예"
    assert sentencing_reason_template(jury(), "oneDay") == "형량: 징역 1일 (내일 하루 무지출)"
    for result in ("agree", "disagree", "notGuilty"):
        assert sentencing_reason_template(jury(result=result), "oneDay") is None


def test_dismissed_has_no_template() -> None:
    assert "dismissed" not in load_templates()["results"]


@pytest.mark.parametrize("intensity", ["mild", "spicy", "hell"])
@pytest.mark.parametrize("result", ["guilty", "agree", "disagree"])
def test_template_drafts_pass_text_rules(intensity: str, result: str) -> None:
    """템플릿 초안은 서버 검증 ⑤ 를 통과해야 검수 대상 draft 에 넣을 수 있다."""
    j = jury(result=result, target_intensities=[intensity])
    sentence = "oneDay" if result == "guilty" else None
    text = template_text_draft(intensity, j, sentence, POST_ID)
    draft = {
        "schema_version": 1,
        "texts": [text.model_dump(mode="json")],
        "meme_tag": "GUILTY_LIGHT",
        "meme_hints": None,
    }
    sentencing = {"sentence": sentence} if sentence else None
    result_rules = apply_text_rules(draft, {}, sentencing, j)
    assert result_rules.issues == []
