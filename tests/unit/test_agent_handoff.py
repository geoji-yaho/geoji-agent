"""에이전트 핸드오프 — 역할마다 무엇을 받고 무엇을 넘기는지(9/19).

판결문 품질이 낮을 때 어느 단계에서 정보가 끊기는지 짚기 위한 테스트다. `tests/fakes/pipeline.py`
가 심문관(A) → 조서·드립(B) → 양형관·서기·검수관·finalize(C) 를 **같은 사건**으로 한 바퀴 돌리고,
여기서는 단계 사이의 인터페이스만 단언한다. 문구 품질은 보지 않는다(FakeLLM).

각 절의 첫 테스트는 "그 역할이 받는 것", 다음은 "그 역할이 넘기는 것"이다. 알려진 끊김(심문관 결과·
조서 사유 분석이 어디에도 안 감, 드립이 방 강도로만 만들어짐)은 `test_gap_*` 로 못박아 두었다.
고쳐서 이 테스트가 깨지면 그건 개선이다 — 그때 테스트를 뒤집는다.

추적 마크다운은 `GEOJI_HANDOFF_TRACE=<경로>` 로 저장할 수 있다(`test_trace_renders`).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from geoji_ai.adapters.fake_llm import FakeLLM
from geoji_ai.contracts.case import CaseSnapshot
from geoji_ai.core.config import Settings
from geoji_ai.domain.attack_angles import NEEDS_EVIDENCE, pick
from geoji_ai.graphs.intake import run_intake
from geoji_ai.prompts import WRITER_VERSION, build_writer_system, load_prompt, prompt_bundle_version
from tests.fakes.pipeline import (
    PipelineTrace,
    intake_request_for,
    render_trace,
    resolved_evidence_for,
    run_pipeline,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "contracts" / "fixtures"
SETTINGS = Settings(_env_file=None, OPENAI_API_KEY="", XAI_API_KEY="")


def taxi_snapshot(**update: Any) -> CaseSnapshot:
    data = json.loads((FIXTURES / "case-snapshot-taxi.json").read_text("utf-8"))
    return CaseSnapshot.model_validate({**data, **update})


@pytest.fixture(scope="module")
def trace() -> PipelineTrace:
    """이력 있는 단골 사건(반복 3건·방 규칙·지난 판결·지난 지출) 한 바퀴. 모듈 안에서 공유한다."""
    return run_pipeline(FakeLLM(), taxi_snapshot(), approved_examples={"spicy": ["승인 예시 문장"]})


@pytest.fixture(scope="module")
def first_spend() -> PipelineTrace:
    """첫 지출: 백엔드 resolve-evidence 가 아무것도 못 준다(F0 만)."""
    return run_pipeline(FakeLLM(), taxi_snapshot(), resolved=None)


def contents(trace: PipelineTrace, stage_prefix: str) -> str:
    return "\n".join(
        str(m["content"])
        for call in trace.llm.calls
        if call.stage.startswith(stage_prefix)
        for m in call.messages
    )


# ---------------------------------------------------------------------------
# 전체 흐름
# ---------------------------------------------------------------------------


def test_pipeline_runs_end_to_end_with_one_call_per_role(trace: PipelineTrace) -> None:
    roles = [c.role for c in trace.llm.calls]
    # 심문 1 · 조서 1 · 드립(방 강도 spicy 하나) 1 · 양형 1 · 서기(배심 강도 3) 3 · 검수 1
    assert roles == [
        "intake",
        "context",
        "banter",
        "sentencing",
        "writer",
        "writer",
        "writer",
        "evaluator",
    ]
    assert trace.outcome == "AI_READY"
    assert trace.sentence_state["dossier_source"] == "PREP"
    assert trace.sentence_state["sentencing_source"] == "AI"
    assert trace.sentence_state["repair_count"] == 0


# ---------------------------------------------------------------------------
# A. 심문관
# ---------------------------------------------------------------------------


def test_intake_receives_only_the_submission(trace: PipelineTrace) -> None:
    call = trace.call("intake")
    assert call.system.startswith(load_prompt("intake-v1.md")[:40])
    assert set(call.user) == {
        "post_type",
        "amount_krw",
        "item",
        "category",
        "reason",
        "injection_hint",
        "mode",
    }
    assert call.user["mode"] == "INITIAL"
    # strict 스키마: 서버가 채우는 값은 모델에게 요구하지 않는다.
    assert "intake_source" not in call.schema["properties"]
    assert "schema_version" not in call.schema["properties"]


def test_intake_accepts_output_without_schema_version() -> None:
    """strict 스키마에 `schema_version` 이 없으니 실제 모델은 그 키 없이 답한다. 그래도 AI 결과다.

    9/19 발견: 후처리가 이 키를 채우지 않아 실제 호출이 전부 `INVALID_OUTPUT` → FALLBACK 이었다.
    """
    import asyncio

    output = {
        "mode": "INITIAL",
        "status": "PASS",
        "item_review": {"status": "OK", "suggested_item": None},
        "message": None,
        "category_review": {"status": "OK", "suggested_category": None, "confidence": 0.9},
        "injection_detected": False,
    }
    result = asyncio.run(
        run_intake(
            intake_request_for(taxi_snapshot()),
            llm=FakeLLM(outputs={"intake": output}),
            settings=SETTINGS,
        )
    )
    assert result.intake_source == "AI"
    assert result.status == "PASS"


def test_gap_intake_result_reaches_no_later_prompt(trace: PipelineTrace) -> None:
    """심문관 결과는 스냅샷에 실려 오지만 조서·드립·양형·서기·검수 어디에도 들어가지 않는다."""
    assert trace.snapshot.intake_result is not None
    later = contents(trace, "B.") + contents(trace, "C.")
    for marker in ("item_review", "category_review", "injection_detected", "intake_source"):
        assert marker not in later


# ---------------------------------------------------------------------------
# B. 조서
# ---------------------------------------------------------------------------


def test_context_receives_case_code_facts_and_reason_but_no_jury(trace: PipelineTrace) -> None:
    call = trace.call("context")
    assert call.system == load_prompt("context-v1.md")
    assert set(call.user) == {"case", "facts", "reason"}
    assert set(call.user["case"]) == {"item", "amount_krw", "category", "post_type"}
    assert "jury" not in call.user and "verdict" not in json.dumps(call.user, ensure_ascii=False)
    # 코드가 만든 근거 6개(F0 이번 지출 · F1·F2 집계 · F3 방 규칙 · F4 지난 판결 · F5 지난 지출)
    labels = [f["label"] for f in call.user["facts"]]
    assert labels == ["F0", "F1", "F2", "F3", "F4", "F5"]
    kinds = [f.fact_type for f in trace.dossier.facts]  # type: ignore[union-attr]
    assert kinds == ["SPEND", "AGGREGATE", "AGGREGATE", "RULE", "VERDICT", "SPEND"]
    assert call.user["reason"] == trace.snapshot.reason


def test_context_output_is_filtered_before_it_becomes_evidence() -> None:
    """RULE_HIT·MITIGATION 은 입력 라벨을 가리킬 때만 F-라벨로 붙는다.

    REASON_ANALYSIS 는 근거가 아니다.
    """
    context_output = {
        "facts": [
            {"kind": "RULE_HIT", "text": "택시 월 1회 규칙에 걸린다.", "source_refs": ["F3"]},
            {"kind": "MITIGATION", "text": "근거 없는 감경", "source_refs": []},
            {"kind": "PATTERN", "text": "허용되지 않는 종류", "source_refs": ["F0"]},
            {"kind": "REASON_ANALYSIS", "text": "편의 목적이다.", "source_refs": ["F0"]},
            {"kind": "RULE_HIT", "text": "없는 라벨", "source_refs": ["F9"]},
        ],
        "reason_analysis": {
            "has_mitigation": False,
            "mitigation_kind": "NONE",
            "injection_suspected": False,
        },
    }
    t = run_pipeline(FakeLLM(outputs={"context": context_output}), taxi_snapshot())
    facts = t.dossier.facts  # type: ignore[union-attr]
    assert [f.label for f in facts] == ["F0", "F1", "F2", "F3", "F4", "F5", "F6"]
    added = facts[6]
    assert (added.fact_type, added.epistemic_type) == ("RULE", "MODEL_INFERENCE")
    assert added.text == "택시 월 1회 규칙에 걸린다."
    assert added.sources == facts[3].sources  # 참조한 F3 의 출처를 물려받는다
    assert t.prepare_state["reason_analysis"]["facts"] == ["편의 목적이다."]  # type: ignore[index]
    # 추가된 F6 이 양형관·서기·검수관 입력까지 그대로 간다.
    assert [f["id"] for f in t.call("sentencing").user["dossier"]][-1] == "F6"
    assert [f["id"] for f in t.call("writer").user["dossier"]][-1] == "F6"
    assert "F6" in t.call("evaluator").user["evidence"]


def test_gap_reason_analysis_is_neither_saved_nor_forwarded(trace: PipelineTrace) -> None:
    """조서 LLM 의 사유 분석(감경 유무·인젝션 의심)은 PrepareState 에만 남는다.

    그래프 C 로 가지 않는다.
    """
    assert trace.prepare_state is not None
    assert trace.prepare_state["reason_analysis"] is not None
    assert all(f.fact_type != "REASON_ANALYSIS" for f in trace.preparation.dossier.facts)  # type: ignore[union-attr]
    later = contents(trace, "C.")
    for marker in ("has_mitigation", "mitigation_kind", "injection_suspected"):
        assert marker not in later


# ---------------------------------------------------------------------------
# B. 드립 후보
# ---------------------------------------------------------------------------


def test_banter_receives_usable_evidence_and_approved_examples_but_no_jury(
    trace: PipelineTrace,
) -> None:
    call = trace.call("banter")
    assert call.system == load_prompt("banter-v1.md")
    assert set(call.user) == {"post_type", "category", "intensity", "evidence", "approved_examples"}
    assert call.user["intensity"] == "spicy"
    assert call.user["approved_examples"] == ["승인 예시 문장"]
    assert [e["label"] for e in call.user["evidence"]] == [f.label for f in trace.dossier.facts]  # type: ignore[union-attr]
    # 평결·표 수는 드립 후보가 모른다(fits 로 양쪽 계열을 다 내게 한다).
    assert "jury" not in call.user
    assert "reason" not in call.user  # 사유 원문은 F0 문장 안에서만 본다


def test_gap_banter_is_generated_per_room_intensity_not_per_jury_target(
    trace: PipelineTrace,
) -> None:
    """드립은 `room_snapshots` 강도(spicy 하나)로만 만든다.

    배심 강도 3개 중 mild·hell 서기는 후보 0개를 받는다.
    """
    jury = trace.snapshot.jury
    assert jury is not None
    assert [i.value for i in jury.target_intensities] == ["mild", "spicy", "hell"]
    assert [c.user["intensity"] for c in trace.calls("banter")] == ["spicy"]
    assert {i.value for i in trace.preparation.banter} == {"spicy"}
    by_intensity = {c.user["intensity"]: c.user["banter_candidates"] for c in trace.calls("writer")}
    assert by_intensity["mild"] == [] and by_intensity["hell"] == []
    assert len(by_intensity["spicy"]) == 4


def test_banter_output_is_filtered_and_relabeled_before_writer() -> None:
    banter_output = {
        "candidates": [
            {
                "text": "정상 후보",
                "strategy": "PREMISE_REJECTION",
                "fits": ["guilty"],
                "evidence_labels": [],
            },
            {
                "text": "라벨 없는 반복",
                "strategy": "REPEAT_OFFENSE",
                "fits": ["guilty"],
                "evidence_labels": ["F9"],  # 입력에 없는 라벨 → 비면 삭제
            },
            {
                "text": "씨발 매운맛 욕",
                "strategy": "EXCUSE_STRIPPING",
                "fits": ["guilty"],
                "evidence_labels": [],
            },
            {
                "text": "자살 언급",
                "strategy": "EXCUSE_STRIPPING",
                "fits": ["guilty"],
                "evidence_labels": [],
            },
            {
                "text": "무죄용 후보",
                "strategy": "NECESSITY_APPROVAL",
                "fits": ["notGuilty"],
                "evidence_labels": ["F4", "F9"],
            },
        ]
    }
    t = run_pipeline(FakeLLM(outputs={"banter": banter_output}), taxi_snapshot())
    kept = t.preparation.banter[next(iter(t.preparation.banter))]
    assert [c.text for c in kept] == ["정상 후보", "무죄용 후보"]
    assert kept[1].evidence_labels == ("F4",)  # F9 는 지워지고 F4 만 남는다
    # 서기(유죄)에게는 fits 에 guilty 가 있는 후보만, id 는 서버가 붙인 uuid.
    handed = t.call("writer", 1).user["banter_candidates"]
    assert [c["text"] for c in handed] == ["정상 후보"]
    assert handed[0]["id"] == kept[0].candidate_id
    assert t.call("writer", 1).schema["properties"]["selected_candidate_id"]["anyOf"][0][
        "enum"
    ] == [kept[0].candidate_id]


# ---------------------------------------------------------------------------
# C. 양형관
# ---------------------------------------------------------------------------


def test_sentencing_receives_case_jury_policy_and_full_dossier(trace: PipelineTrace) -> None:
    call = trace.call("sentencing")
    assert call.system == load_prompt("sentencing-v1.md")
    assert set(call.user) == {"case", "jury", "dossier"}
    assert set(call.user["jury"]) == {"result", "vote_counts", "guilty_ratio", "policy"}
    assert call.user["jury"]["policy"]["allowed_sentences"] == [
        {"code": "probation", "rank": 1},
        {"code": "oneDay", "rank": 2},
    ]
    assert call.schema["properties"]["sentence"]["enum"] == ["probation", "oneDay"]
    assert [f["id"] for f in call.user["dossier"]] == [f.label for f in trace.dossier.facts]  # type: ignore[union-attr]
    # 드립 후보·말투·강도는 양형관이 모른다(입력 최소화 표).
    assert "banter" not in json.dumps(call.user, ensure_ascii=False)
    assert "intensity" not in call.user


def test_sentencing_decision_is_handed_unchanged_to_writer_evaluator_and_finalize(
    trace: PipelineTrace,
) -> None:
    decision = trace.sentence_state["sentencing"].model_dump(mode="json")
    assert decision["reason_source"] == "AI"
    assert set(decision["evidence_labels"]) <= {f.label for f in trace.dossier.facts}  # type: ignore[union-attr]
    for call in trace.calls("writer"):
        assert call.user["sentencing"] == decision
    assert trace.call("evaluator").user["sentencing"] == decision
    assert trace.finalize_request.sentencing.model_dump(mode="json") == decision  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# C. 서기
# ---------------------------------------------------------------------------


def test_writer_receives_one_intensity_section_and_the_shared_context(trace: PipelineTrace) -> None:
    calls = trace.calls("writer")
    assert [c.user["intensity"] for c in calls] == ["mild", "spicy", "hell"]
    for call in calls:
        intensity = call.user["intensity"]
        assert call.system == build_writer_system(intensity)
        assert (
            f"writer/{intensity}-{WRITER_VERSION}.md"
            and load_prompt(f"writer/{intensity}-{WRITER_VERSION}.md").strip() in call.system
        )
        assert set(call.user) == {
            "intensity",
            "attack_angle",
            "case",
            "jury",
            "sentencing",
            "dossier",
            "banter_candidates",
        }
        # 배심은 결과·표 수·비율만. 허용 형량 목록은 서기가 보지 않는다(형량 변경 통로 차단).
        assert set(call.user["jury"]) == {"result", "vote_counts", "guilty_ratio"}
        assert [f["id"] for f in call.user["dossier"]] == [f.label for f in trace.dossier.facts]  # type: ignore[union-attr]
        assert "avoid" not in call.user  # 첫 호출에는 피할 것이 없다
        # 서버가 고정하는 값은 enum 하나짜리다.
        assert call.schema["properties"]["intensity"]["enum"] == [intensity]
        assert call.schema["properties"]["attack_angle"]["enum"] == [
            call.user["attack_angle"]["code"]
        ]
    other = [
        ln for ln in load_prompt(f"writer/hell-{WRITER_VERSION}.md").splitlines() if ln.strip()
    ]
    assert not any(ln in calls[0].system for ln in other)  # mild 에 hell 섹션 없음


def test_writer_angle_is_shared_across_intensities_and_grounded(trace: PipelineTrace) -> None:
    angles = {c.user["attack_angle"]["code"] for c in trace.calls("writer")}
    assert len(angles) == 1
    # 조서에 지난 판결·방 규칙이 있으니 건너뛰는 각도가 없다 → 해시 그대로.
    assert angles == {pick(trace.snapshot.post_id).value}


def test_writer_output_becomes_draft_then_validation_then_evaluator_input(
    trace: PipelineTrace,
) -> None:
    drafts = trace.sentence_state["drafts"]
    sources = trace.sentence_state["draft_sources"]
    assert {i.value for i in drafts} == {"mild", "spicy", "hell"}
    assert all(source == "AI" for source in sources.values())
    assert all(len(d.statement) == 1 and len(d.headline) <= 20 for d in drafts.values())
    # 서버 검증 ⑤ 통과(위반 없음) 뒤 검수관에게 세 강도 초안이 한 번에 간다.
    assert all(v == [] for v in trace.sentence_state["validation"].values())
    handed = trace.call("evaluator").user["draft"]["texts"]
    assert [t["intensity"] for t in handed] == ["mild", "spicy", "hell"]
    for text in handed:
        draft = drafts[next(i for i in drafts if i.value == text["intensity"])]
        assert text["headline"] == draft.headline
        assert text["statement"][0]["text"] == draft.statement[0].text
        assert text["source"] == "AI"


# ---------------------------------------------------------------------------
# C. 검수관
# ---------------------------------------------------------------------------


def test_evaluator_receives_policy_jury_sentencing_draft_and_evidence(trace: PipelineTrace) -> None:
    call = trace.call("evaluator")
    policy = trace.settings.GUARDRAIL_POLICY_VERSION
    assert call.system == load_prompt(f"evaluator/{policy}.md")
    assert set(call.user) == {"policy_version", "jury", "sentencing", "draft", "evidence"}
    assert call.user["policy_version"] == policy
    assert set(call.user["jury"]) == {"result", "vote_counts", "guilty_ratio", "policy"}
    assert call.user["evidence"] == {f.label: f.text for f in trace.dossier.facts}  # type: ignore[union-attr]
    assert "policy_version" not in call.schema["properties"]  # 서버가 채운다
    assert call.schema["properties"]["texts"]["items"]["properties"]["intensity"]["enum"] == [
        "mild",
        "spicy",
        "hell",
    ]


def test_evaluator_splits_hell_when_a_separate_model_is_configured() -> None:
    settings = Settings(
        _env_file=None, OPENAI_API_KEY="", XAI_API_KEY="", MODEL_EVALUATOR_HELL="gpt-5.6-terra"
    )
    t = run_pipeline(FakeLLM(), taxi_snapshot(), settings=settings)
    groups = [[x["intensity"] for x in c.user["draft"]["texts"]] for c in t.calls("evaluator")]
    assert sorted(groups, key=len) == [["hell"], ["mild", "spicy"]]
    assert t.outcome == "AI_READY"


# ---------------------------------------------------------------------------
# C. finalize — 백엔드에 넘기는 것
# ---------------------------------------------------------------------------


def test_finalize_carries_dossier_hashes_versions_and_models(trace: PipelineTrace) -> None:
    req = trace.finalize_request
    assert req is not None
    assert req.dossier_id == trace.preparation.dossier.dossier_id  # type: ignore[union-attr]
    assert req.draft_hash == req.evaluation_draft_hash == trace.sentence_state["draft_hash"]
    assert req.prompt_bundle_version == prompt_bundle_version()
    assert req.guardrail_policy_version == trace.settings.GUARDRAIL_POLICY_VERSION
    assert req.model_ids.model_dump(mode="json") == {
        "sentencing": trace.settings.MODEL_JUDGMENT,
        "writer": trace.settings.MODEL_WRITER,
        "evaluator": trace.settings.MODEL_JUDGMENT,
    }
    assert [t.source for t in req.draft.texts] == ["AI", "AI", "AI"]
    assert [p.model_dump() for p in req.privacy_versions] == [
        p.model_dump() for p in trace.snapshot.privacy_versions
    ]
    assert trace.jobs.completed == ["job-sentence-1"]


# ---------------------------------------------------------------------------
# 첫 지출 — 이력이 아무것도 없을 때 각 역할이 받는 것
# ---------------------------------------------------------------------------


def test_first_spend_every_role_sees_only_f0(first_spend: PipelineTrace) -> None:
    assert first_spend.outcome == "AI_READY"
    assert [f.label for f in first_spend.dossier.facts] == ["F0"]  # type: ignore[union-attr]
    assert [f["label"] for f in first_spend.call("context").user["facts"]] == ["F0"]
    assert [f["label"] for f in first_spend.call("banter").user["evidence"]] == ["F0"]
    assert [f["id"] for f in first_spend.call("sentencing").user["dossier"]] == ["F0"]
    for call in first_spend.calls("writer"):
        assert [f["id"] for f in call.user["dossier"]] == ["F0"]
    assert list(first_spend.call("evaluator").user["evidence"]) == ["F0"]


def test_first_spend_skips_history_and_rule_angles(first_spend: PipelineTrace) -> None:
    """첫 호출 3건(강도별)은 이력·규칙 각도를 건너뛴 같은 각도다. 재작성은 다음 각도(offset+1)."""
    first_round = first_spend.calls("writer")[:3]
    angles = {c.user["attack_angle"]["code"] for c in first_round}
    assert angles == {pick(first_spend.snapshot.post_id, skip=NEEDS_EVIDENCE).value}
    assert not angles & {a.value for a in NEEDS_EVIDENCE}
    repairs = first_spend.calls("writer")[3:]
    for call in repairs:
        assert (
            call.user["attack_angle"]["code"]
            == pick(first_spend.snapshot.post_id, 1, skip=NEEDS_EVIDENCE).value
        )
        assert "avoid" in call.user


def test_first_spend_repeat_candidates_are_dropped_before_writer(
    first_spend: PipelineTrace,
) -> None:
    """fixture 드립의 반복·규칙 후보는 F1 이상을 가리키는데 F0 뿐이라 서기에게 가지 않는다."""
    handed = first_spend.call("writer", 1).user["banter_candidates"]
    assert all(c["strategy"] not in {"REPEAT_OFFENSE", "ROOM_RULE_CALLBACK"} for c in handed)
    assert all(c["evidence_labels"] in ([], ["F0"]) for c in handed)


def test_no_prepare_means_inline_dossier_and_no_candidates() -> None:
    """준비 자료가 없으면(그래프 B 미실행) 그래프 C 가 즉석 조서(D-25 ④)를 만든다.

    드립은 건너뛴다.
    """
    t = run_pipeline(FakeLLM(), taxi_snapshot(), with_prepare=False, remaining_s=90.0)
    assert t.sentence_state["dossier_source"] == "INLINE"
    roles = [c.role for c in t.llm.calls]
    assert roles[:2] == ["intake", "context"]
    assert "banter" not in roles
    assert roles.count("writer") >= 3 and roles[-1] == "evaluator"
    assert all(c.user["banter_candidates"] == [] for c in t.calls("writer"))
    # 조서는 그래프 C 가 저장한다(finalize 가 dossier_id 를 요구한다).
    assert t.preparation.saved_dossiers and t.finalize_request is not None
    assert t.finalize_request.dossier_id == t.preparation.saved_dossiers[0].dossier_id


def test_no_prepare_and_no_time_means_minimal_dossier() -> None:
    """남은 시간이 서기+검수+finalize 예약(6+4+0.5)보다 짧으면 최소 조서(F0)로 간다(D-25 ⑤)."""
    caps = {"WRITER_NODE_TIMEOUT_SECONDS": 6, "EVALUATOR_NODE_TIMEOUT_SECONDS": 4}
    settings = Settings(_env_file=None, OPENAI_API_KEY="", XAI_API_KEY="", **caps)
    t = run_pipeline(
        FakeLLM(), taxi_snapshot(), settings=settings, with_prepare=False, remaining_s=10.0
    )
    assert t.sentence_state["dossier_source"] == "MINIMAL"
    assert "context" not in [c.role for c in t.llm.calls]
    assert [f["id"] for f in t.call("sentencing").user["dossier"]] == ["F0"]


# ---------------------------------------------------------------------------
# 추적 출력
# ---------------------------------------------------------------------------


def test_trace_renders(trace: PipelineTrace, tmp_path: Path) -> None:
    text = render_trace(trace, full_prompts=True)
    for section in (
        "## A. 심문관",
        "## B. 조서·드립",
        "## C. 선고",
        "## 모델 호출 순서",
        "## finalize",
    ):
        assert section in text
    assert "### 8. [C.sentence] evaluator" in text
    (tmp_path / "trace.md").write_text(text, encoding="utf-8")
    target = os.environ.get("GEOJI_HANDOFF_TRACE")
    if target:
        Path(target).write_text(text, encoding="utf-8")


def test_resolved_evidence_helper_matches_backend_contract() -> None:
    resolved = resolved_evidence_for(taxi_snapshot(), repeat_30d=0, room_rule_text=None)
    assert resolved.aggregates.repeat_same_category_30d == 0
    assert resolved.room_rules == []
