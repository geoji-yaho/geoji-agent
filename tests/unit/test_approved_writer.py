"""승인 요청 분리와 finalize 계약 보존. 모델 의미 판단의 품질 검증은 아니다."""

import json

import pytest

from geoji_ai.adapters.fake_llm import FakeLLM
from geoji_ai.contracts.llm_schemas import writer_schema
from geoji_ai.domain.attack_angles import ANGLE_GUIDES, AttackAngle
from geoji_ai.domain.intensity import Intensity
from geoji_ai.domain.visibility import usable_for_share_card
from geoji_ai.graphs.sentencing import build_writer_request, minimal_dossier
from geoji_ai.prompts import build_writer_system
from tests.unit.test_graph_c import card_output, make_snapshot, run


@pytest.mark.parametrize("intensity", list(Intensity))
@pytest.mark.parametrize("offset", [0, 1, 2])
def test_approved_request_does_not_instruct_roast_even_on_repair(intensity, offset):
    snapshot = make_snapshot(result="agree", vote_counts={"agree": 3, "disagree": 1}).model_copy(
        update={"post_type": "considering"}
    )
    before = snapshot.model_dump()
    avoid = {"violation_codes": ["SCHEMA_INVALID"]} if offset else None
    messages, schema = build_writer_request(
        snapshot, minimal_dossier(snapshot), None, intensity, offset=offset, avoid=avoid
    )
    user = json.loads(messages[1]["content"])
    assert set(user["attack_angle"]) == {"code"}
    assert "칭찬·인정" in messages[0]["content"]
    assert "정보 부족" in messages[0]["content"]
    assert "품목 이름만으로" in messages[0]["content"]
    assert "질문 형태로" in messages[0]["content"]
    assert "F0" in messages[0]["content"]
    assert "APPROVED" in messages[0]["content"]
    assert "CELEBRATION" in messages[0]["content"]
    assert "SMUG" in messages[0]["content"]
    assert user["jury"]["result"] == "agree"
    assert user["case"]["reason"] == snapshot.reason
    assert user["dossier"][0]["id"] == "F0"
    assert user.get("avoid") == avoid
    assert schema == writer_schema([intensity.value], [user["attack_angle"]["code"]])
    assert snapshot.model_dump() == before


@pytest.mark.parametrize("result", ["guilty", "notGuilty", "disagree"])
@pytest.mark.parametrize("intensity", list(Intensity))
def test_other_verdicts_keep_existing_system_and_angle_guide(result, intensity):
    snapshot = make_snapshot(result=result)
    messages, _ = build_writer_request(snapshot, minimal_dossier(snapshot), None, intensity)
    user = json.loads(messages[1]["content"])
    guide = ANGLE_GUIDES[AttackAngle(user["attack_angle"]["code"])]
    assert messages[0]["content"] == build_writer_system(intensity)
    assert user["attack_angle"]["label"] == guide.label_ko
    assert user["attack_angle"]["instruction"] == guide.instruction


@pytest.mark.parametrize(
    "emotion,keywords",
    [
        ("CELEBRATION", ["인정", "수긍"]),
        ("SMUG", ["진행시켜", "능청"]),
    ],
)
def test_approved_finalize_keeps_hints_strategy_and_private_evidence(emotion, keywords):
    snapshot = make_snapshot(
        result="agree", vote_counts={"agree": 3, "disagree": 1}, target_intensities=["spicy"]
    ).model_copy(update={"post_type": "considering"})
    output = card_output(
        headline="구매 승인",
        statement=[{"text": "이번 구매는 승인.", "kind": "fact", "evidence_labels": ["F0"]}],
        banter_strategy="NECESSITY_APPROVAL",
        selected_candidate_id=None,
        meme_tag="REJECTED",  # 서버가 배심원 결과로 다시 정해야 한다.
        meme_hints={"emotion": emotion, "keywords": keywords},
    )
    result = run(FakeLLM(outputs={"writer": output}), snapshot=snapshot)
    req = result.finalize()
    assert result.roles()["sentencing"] == 0
    assert req.sentencing is None
    assert req.draft.meme_tag == "APPROVED"
    assert req.draft.meme_hints.emotion == emotion
    assert req.draft.meme_hints.keywords == keywords
    text = req.draft.texts[0]
    assert text.source == "AI"
    assert text.banter_strategy == "NECESSITY_APPROVAL"
    assert text.statement[0].evidence_labels == ["F0"]
    dossier = result.state["dossier"]
    assert req.dossier_id == dossier.dossier_id
    fact = next(f for f in dossier.facts if f.label == "F0")
    assert fact.scope.visibility == "ROOMS"
    assert not usable_for_share_card(fact.scope)
