"""사유·강도 개선의 실제 요청/검증 경계. 자연어 품질 자체는 사람 평가한다."""

import json

import pytest

from geoji_ai.contracts.case import VerdictResult
from geoji_ai.domain.intensity import Intensity
from geoji_ai.domain.validation import PROFANITY_OUT_OF_LIST, SCHEMA_INVALID, apply_text_rules
from geoji_ai.graphs.sentencing import build_writer_request, minimal_dossier
from tests.unit.test_graph_c import ScriptedLLM, body, card_output, make_snapshot, run


def request(snapshot):
    return build_writer_request(snapshot, minimal_dossier(snapshot), None, Intensity.spicy)


def test_case_id_does_not_force_a_comedy_technique():
    snapshot = make_snapshot()
    messages, schema = request(snapshot)
    _, other = request(snapshot.model_copy(update={"post_id": "different-id"}))
    allowed = schema["properties"]["attack_angle"]["enum"]
    assert len(allowed) > 1
    assert allowed == other["properties"]["attack_angle"]["enum"]
    assert "REPETITION" not in allowed
    assert "RULE_PERSONIFICATION" not in allowed
    payload = json.loads(messages[1]["content"])
    assert [angle["code"] for angle in payload["attack_angles"]] == allowed


@pytest.mark.parametrize("result", ["agree", "notGuilty"])
def test_approval_does_not_request_mockery_or_bankruptcy(result):
    snapshot = make_snapshot()
    snapshot = snapshot.model_copy(
        update={"jury": snapshot.jury.model_copy(update={"result": VerdictResult(result)})}
    )
    _, schema = request(snapshot)
    assert schema["properties"]["attack_angle"]["enum"] == ["EXCUSE_DISSECTION"]


def test_original_reason_and_evidence_type_reach_writer_without_stock_conversion():
    reason = "막차가 끊겨 택시를 탔어요. " * 20
    snapshot = make_snapshot().model_copy(update={"reason": reason})
    messages, _ = request(snapshot)
    payload = json.loads(messages[1]["content"])
    assert payload["case"]["reason"] == reason
    assert payload["dossier"][0]["epistemic_type"] == "USER_CLAIM"
    assert "회분" not in payload["dossier"][0]["text"]


@pytest.mark.parametrize("slang", ["도랏나", "도랐나", "ㅇㅈㄹ", "지랄", "미쳤네"])
@pytest.mark.parametrize("intensity", ["mild", "spicy", "hell"])
def test_light_slang_follows_user_intensity_boundary(slang, intensity):
    draft = {
        "texts": [{**card_output(), "intensity": intensity, **body(f"{slang}. 내일부터래요.")}]
    }
    result = apply_text_rules(draft, {}, None, {"target_intensities": [intensity]})
    blocked = any(issue.code == PROFANITY_OUT_OF_LIST for issue in result.issues)
    assert blocked == (intensity == "mild")


@pytest.mark.parametrize("word", ["씨발", "병신", "개소리", "새끼"])
def test_spicy_still_rejects_strong_insults(word):
    draft = {"texts": [{**card_output(), **body(word)}]}
    result = apply_text_rules(draft, {}, None, {"target_intensities": ["spicy"]})
    assert any(issue.code == PROFANITY_OUT_OF_LIST for issue in result.issues)


def test_long_text_is_rewritten_instead_of_losing_its_reason():
    original = "택시는 사치예요. " + "막차가 끊긴 상황은 이해하지만 다른 선택도 검토해 보세요. " * 4
    revised = "막차도 끊겼는데 어쩌겠어요. 귀가에 필요한 지출로 인정해요."
    llm = ScriptedLLM(
        sequences={"writer": [card_output(**body(original)), card_output(**body(revised))]}
    )
    result = run(llm, snapshot=make_snapshot(target_intensities=["spicy"]))
    assert result.roles()["writer"] == 2
    assert result.finalize().draft.texts[0].statement[0].text == revised


def test_writer_accepts_a_grounded_technique_other_than_id_choice():
    llm = ScriptedLLM(outputs={"writer": card_output(attack_angle="EXCUSE_DISSECTION")})
    result = run(llm, snapshot=make_snapshot(target_intensities=["spicy"]))
    assert result.roles()["writer"] == 1
    assert result.finalize().draft.texts[0].attack_angle == "EXCUSE_DISSECTION"


def test_writer_rejects_history_technique_without_evidence():
    snapshot = make_snapshot(target_intensities=["spicy"])
    llm = ScriptedLLM(outputs={"writer": card_output(attack_angle="REPETITION")})
    result = run(llm, snapshot=snapshot, preparation=None, backend_kwargs={"remaining_s": 8.4})
    assert result.state["drafts"][Intensity.spicy].source == "TEMPLATE"


def test_writer_schema_requests_case_reading_before_copy():
    _, schema = request(make_snapshot())
    assert next(iter(schema["properties"])) == "case_reading"
    assert set(schema["properties"]["case_reading"]["properties"]) == {
        "reason_quote",
        "acknowledged_context",
        "roast_target",
    }


@pytest.mark.parametrize("token", ["disagree", "notGuilty", "EXCUSE_DISSECTION", "FUTURE_PROPHECY"])
def test_internal_metadata_cannot_leak_into_display_copy(token):
    result = apply_text_rules(
        {"texts": [{**card_output(), **body(f"그게 {token} 사유냐 마.")}]},
        {},
        None,
        {"target_intensities": ["spicy"]},
    )
    assert any(issue.code == SCHEMA_INVALID for issue in result.issues)


@pytest.mark.parametrize("intensity", ["mild", "spicy", "hell"])
def test_investment_verb_is_not_self_harm(intensity):
    result = apply_text_rules(
        {
            "texts": [
                {**card_output(), "intensity": intensity, **body("연습에 먼저 투자해 보세요.")}
            ]
        },
        {},
        None,
        {"target_intensities": [intensity]},
    )
    assert not any(issue.code == "SELF_HARM_LEXICON" for issue in result.issues)


def test_investment_exception_does_not_hide_separate_self_harm_word():
    result = apply_text_rules(
        {"texts": [{**card_output(), **body("투자해도 자해 얘기는 안 된다.")}]},
        {},
        None,
        {"target_intensities": ["spicy"]},
    )
    assert any(issue.code == "SELF_HARM_LEXICON" for issue in result.issues)


def test_internal_code_leak_uses_existing_repair_path():
    llm = ScriptedLLM(
        sequences={
            "writer": [
                card_output(**body("그게 disagree 사유냐 마.")),
                card_output(),
            ]
        }
    )
    result = run(llm, snapshot=make_snapshot(target_intensities=["spicy"]))
    assert result.roles()["writer"] == 2
    assert (
        result.finalize().draft.texts[0].statement[0].text == card_output()["statement"][0]["text"]
    )
