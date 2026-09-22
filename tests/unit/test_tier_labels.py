"""#72: 백엔드 티어 코드가 새 근거·재사용 조서에서 모델 문구로 새지 않는다."""

import json
from dataclasses import replace

import pytest

from geoji_ai.application.build_evidence import build_evidence
from geoji_ai.domain.intensity import Intensity
from geoji_ai.graphs.preparation import _banter_messages, _context_messages
from tests.unit.test_build_evidence import load_snapshot, make_resolved
from tests.unit.test_graph_c import make_prep, run, user_payload


@pytest.mark.parametrize(
    "code,label",
    [("king", "거지왕"), ("flower", "꽃거지"), ("hardcore", "상거지"), ("penniless", "무일푼")],
)
def test_backend_tier_is_rendered_with_frontend_label(code, label):
    snapshot = load_snapshot()
    resolved = make_resolved(snapshot)
    resolved = resolved.model_copy(
        update={"aggregates": resolved.aggregates.model_copy(update={"tier": code})}
    )
    dossier = build_evidence(snapshot, resolved, pack_limit=12)

    assert dossier.facts[1].text == f"이번 달 예산 소진율 41%, 티어 {label}, 무지출 3일."
    assert resolved.aggregates.tier == code


@pytest.mark.parametrize("tier", ["", "FAKE", "unknown_tier"])
def test_unknown_tier_is_omitted_without_losing_other_aggregates(tier):
    snapshot = load_snapshot()
    resolved = make_resolved(snapshot)
    resolved = resolved.model_copy(
        update={"aggregates": resolved.aggregates.model_copy(update={"tier": tier})}
    )
    dossier = build_evidence(snapshot, resolved, pack_limit=12)

    assert dossier.facts[1].text == "이번 달 예산 소진율 41%, 무지출 3일."
    assert dossier.facts[2].text == "최근 30일 같은 카테고리 확정 소비 2건."


def test_already_localized_tier_is_preserved():
    snapshot = load_snapshot()
    dossier = build_evidence(snapshot, make_resolved(snapshot), pack_limit=12)
    assert "티어 상거지" in dossier.facts[1].text


def legacy_prep():
    prep = make_prep()
    facts = list(prep.dossier.facts)
    facts[1] = replace(
        facts[1],
        fact_type="AGGREGATE",
        epistemic_type="DB_RECORD",
        text="이번 달 예산 소진율 90%, 티어 penniless, 무지출 3일.",
    )
    prep.dossier = replace(prep.dossier, facts=tuple(facts))
    return prep


def test_reused_dossier_is_localized_for_judge_writer_and_evaluator_without_mutation():
    prep = legacy_prep()
    original = prep.dossier
    result = run(prep=prep)

    assert result.state["failure"] is None
    for role in ("sentencing", "writer", "evaluator"):
        calls = result.calls_of(role)
        assert calls
        for call in calls:
            payload = user_payload(call)
            text = json.dumps(payload, ensure_ascii=False)
            assert "티어 무일푼" in text
            assert "penniless" not in text
    assert prep.dossier == original
    assert "penniless" in original.facts[1].text


@pytest.mark.parametrize("role", ["context", "banter"])
def test_reused_facts_are_localized_for_preparation_models(role):
    snapshot = load_snapshot()
    facts = legacy_prep().dossier.facts
    messages = (
        _context_messages(snapshot, facts)
        if role == "context"
        else _banter_messages(snapshot, Intensity.spicy, facts, [])
    )
    assert "티어 무일푼" in messages[-1]["content"]
    assert "penniless" not in messages[-1]["content"]
    assert "penniless" in facts[1].text


def test_tier_words_in_user_claims_are_not_rewritten():
    snapshot = load_snapshot(item="flower", reason="king 앨범을 선물하려고")
    dossier = build_evidence(snapshot, make_resolved(snapshot), pack_limit=12)
    messages = _context_messages(snapshot, dossier.facts)
    payload = json.loads(messages[-1]["content"])
    assert "flower" in payload["facts"][0]["text"]
    assert "king 앨범" in payload["facts"][0]["text"]
    assert payload["reason"] == snapshot.reason
