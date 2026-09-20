"""21 §3.2·§3.3: 검수 적용 조건과 근거 유형. 외부 모델·DB 없이 그래프를 실행한다."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from geoji_ai.core.config import Settings
from geoji_ai.domain.intensity import Intensity
from tests.unit.test_graph_c import (
    FIXED,
    SPEC_CAPS,
    ScriptedLLM,
    make_prep,
    make_snapshot,
    report,
    run,
    user_payload,
)


def no_sentence_snapshot(result):
    considering = result in {"agree", "disagree"}
    counts = {"agree": 1, "disagree": 3} if considering else {"guilty": 1, "notGuilty": 3}
    return make_snapshot(
        result=result, vote_counts=counts, target_intensities=["spicy"], default_intensity="spicy"
    ).model_copy(update={"post_type": "considering" if considering else "spent"})


@pytest.mark.parametrize("result", ["agree", "disagree", "notGuilty"])
@pytest.mark.parametrize("kind", ["SENTENCE", "TEXT_RETRY"])
def test_no_sentence_case_does_not_ask_model_to_check_a_sentence(result, kind):
    output = report(["spicy"])
    for name in ("sentence_check", "sentencing_reason_check"):
        output[name] = {
            "pass": False,
            "violations": [
                {
                    "code": "SENTENCE_REASON_MISMATCH",
                    "path": "sentencing",
                    "evidence_labels": [],
                    "explanation": "형량이 없어 확인할 수 없다",
                }
            ],
        }
    result = run(
        ScriptedLLM(outputs={"evaluator": output}),
        snapshot=no_sentence_snapshot(result),
        kind=kind,
    )

    assert result.backend.failed == []
    request = result.finalize()
    assert request.sentencing is None
    for name in ("sentence_check", "sentencing_reason_check"):
        check = getattr(request.evaluation, name)
        assert check.pass_ is True
        assert check.violations == []
        schema = result.calls_of("evaluator")[0].schema
        assert name not in schema["properties"]
        assert name not in schema["required"]


@pytest.mark.parametrize("output", [{"texts": []}, {}, {"texts": [{"intensity": "spicy"}]}])
def test_no_sentence_case_still_rejects_incomplete_text_checks(output):
    result = run(
        ScriptedLLM(outputs={"evaluator": output}),
        snapshot=no_sentence_snapshot("disagree"),
        kind="TEXT_RETRY",
    )
    assert result.backend.failed == ["EVAL_FAILED"]
    assert result.backend.finalized == []


def test_no_sentence_case_still_rejects_unsafe_text():
    result = run(
        ScriptedLLM(outputs={"evaluator": report(["spicy"], fail=["spicy"])}),
        snapshot=no_sentence_snapshot("agree"),
        kind="TEXT_RETRY",
    )
    assert result.backend.failed == ["EVAL_FAILED"]
    assert result.backend.finalized == []


@pytest.mark.parametrize(
    "malformed_check",
    [{"pass": "true"}, {"pass": 1}, {"violations": [{}]}],
    ids=["string-pass", "integer-pass", "incomplete-violation"],
)
def test_no_sentence_case_still_rejects_malformed_text_checks(malformed_check):
    output = report(["spicy"])
    output["texts"][0].update(malformed_check)
    result = run(
        ScriptedLLM(outputs={"evaluator": output}),
        snapshot=no_sentence_snapshot("disagree"),
        kind="TEXT_RETRY",
    )
    assert result.backend.failed == ["EVAL_FAILED"]
    assert result.backend.finalized == []


def test_guilty_retry_requires_fixed_sentence_before_model_calls():
    result = run(kind="TEXT_RETRY", backend_kwargs={"fixed": None})
    assert result.backend.failed == ["SCHEMA_INVALID"]
    assert result.backend.finalized == []
    assert result.llm.calls == []


@pytest.mark.parametrize("kind", ["SENTENCE", "TEXT_RETRY"])
def test_no_sentence_case_rejects_unexpected_fixed_sentence(kind):
    result = run(
        snapshot=no_sentence_snapshot("disagree"),
        kind=kind,
        backend_kwargs={"fixed": FIXED},
    )
    assert result.backend.failed == ["SCHEMA_INVALID"]
    assert result.backend.finalized == []
    assert result.llm.calls == []


@pytest.mark.parametrize("name", ["sentence_check", "sentencing_reason_check"])
def test_guilty_retry_keeps_sentence_checks_required(name):
    output = report(["spicy", "hell"])
    output[name] = {"pass": False, "violations": []}
    result = run(
        ScriptedLLM(outputs={"evaluator": output}),
        kind="TEXT_RETRY",
        backend_kwargs={"fixed": FIXED},
    )
    assert result.backend.failed == ["EVAL_FAILED"]
    assert result.backend.finalized == []
    assert name in result.calls_of("evaluator")[0].schema["required"]


def test_no_sentence_case_supports_split_evaluation():
    snapshot = no_sentence_snapshot("disagree")
    snapshot = snapshot.model_copy(
        update={
            "jury": snapshot.jury.model_copy(
                update={"target_intensities": [Intensity.spicy, Intensity.hell]}
            )
        }
    )
    result = run(
        snapshot=snapshot,
        settings=Settings(_env_file=None, MODEL_EVALUATOR_HELL="separate-model", **SPEC_CAPS),
    )
    assert result.backend.failed == []
    assert len(result.calls_of("evaluator")) == 2
    assert len(result.finalize().evaluation.texts) == 2


def test_evaluator_preserves_evidence_type_without_exposing_source_ids():
    prep = make_prep()
    types = ["USER_CLAIM", "DB_RECORD", "MODEL_INFERENCE"]
    facts = tuple(
        replace(f, epistemic_type=types[index % 3], sources=(("POST", "private-source-id", 1),))
        for index, f in enumerate(prep.dossier.facts)
    )
    prep = SimpleNamespace(dossier=replace(prep.dossier, facts=facts), banter=prep.banter)
    result = run(prep=prep)
    writer = user_payload(result.calls_of("writer")[0])
    evaluator = user_payload(result.calls_of("evaluator")[0])
    for fact in facts:
        metadata = evaluator["evidence_metadata"][fact.label]
        assert metadata == {"epistemic_type": fact.epistemic_type, "fact_type": fact.fact_type}
        assert evaluator["evidence"][fact.label] == fact.text
        matching = next(f for f in writer["dossier"] if f["id"] == fact.label)
        assert matching["epistemic_type"] == metadata["epistemic_type"]
    assert "private-source-id" not in str(evaluator)
