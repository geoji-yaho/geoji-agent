"""그래프 C(선고) — 05 §3.3·§3.5·§4.2, 스펙 케이스 ①~⑮.

FakeLLM + 이 파일 안의 가짜 백엔드·큐·준비 포트. 네트워크·DB·키 없음.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from geoji_ai.adapters.fake_llm import FakeCall, FakeLLM, FakeScenario, load_role_fixture
from geoji_ai.application.sentence_case import SentenceHandler
from geoji_ai.contracts.case import CaseSnapshot
from geoji_ai.contracts.finalize import FinalizeRequest
from geoji_ai.contracts.jobs import Job
from geoji_ai.contracts.sentencing import SentencingDecision
from geoji_ai.contracts.writer import BanterStrategy
from geoji_ai.core.config import Settings
from geoji_ai.domain.attack_angles import pick
from geoji_ai.domain.draft_hash import draft_hash
from geoji_ai.domain.intensity import Intensity
from geoji_ai.domain.visibility import Scope
from geoji_ai.graphs.sentencing import build_sentence_graph
from geoji_ai.graphs.states import Candidate
from geoji_ai.ports.backend import BeginGenerationResult, FinalizeResult
from geoji_ai.ports.llm import LLMError
from geoji_ai.ports.preparation import Dossier, EvidenceFact

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "contracts" / "fixtures"
DB_NOW = datetime(2026, 9, 7, 9, 20, tzinfo=UTC)
GENERATION_ID = "gen-1"
WORKER_ID = "worker-1"
CANDIDATE_MARK = "드립후보마커문장"
SENTENCING_FIXTURE = load_role_fixture("sentencing")


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{name}.json").read_text("utf-8"))


# ---------------------------------------------------------------------------
# 가짜 포트
# ---------------------------------------------------------------------------


class Rejected(Exception):
    """어댑터 `BackendRejected` 모양(속성으로 판별)."""

    def __init__(self, status: int, code: str) -> None:
        super().__init__(f"{status} {code}")
        self.status = status
        self.code = code


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeBackend:
    def __init__(
        self,
        snapshot: CaseSnapshot,
        *,
        remaining_s: float = 60.0,
        fixed: dict[str, Any] | None = None,
        begin_error: Exception | None = None,
        finalize_error: Exception | None = None,
        finalize_errors: list[Exception] | None = None,
    ) -> None:
        self._snapshot = snapshot
        self.remaining_s = remaining_s
        self.fixed = fixed
        self.begin_error = begin_error
        self.finalize_error = finalize_error
        #: 앞에서부터 한 번씩만 올리는 finalize 오류.
        self.finalize_errors = list(finalize_errors or [])
        self.began: list[dict[str, Any]] = []
        self.finalized: list[FinalizeRequest] = []
        self.failed: list[str] = []

    async def snapshot(self, job_id: str, generation_id: str) -> CaseSnapshot:
        return self._snapshot

    async def resolve_evidence(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("그래프 C 는 resolve-evidence 를 부르지 않는다")

    async def begin_generation(
        self, verdict_id: str, *, job_id: str, generation_id: str, verdict_version: int
    ) -> BeginGenerationResult:
        self.began.append({"verdict_id": verdict_id, "job_id": job_id})
        if self.begin_error is not None:
            raise self.begin_error
        return BeginGenerationResult.model_validate(
            {
                "fixed_sentencing": self.fixed,
                "text_version": 0,
                "deadline_at": DB_NOW + timedelta(seconds=self.remaining_s),
            }
        )

    async def finalize(self, verdict_id: str, req: FinalizeRequest) -> FinalizeResult:
        # 계약 모델로 다시 파싱한다(가짜 백엔드가 JSON 을 받는 것과 같다).
        self.finalized.append(FinalizeRequest.model_validate(req.model_dump(mode="json")))
        # 백엔드처럼 hash 를 다시 계산한다. 검수 뒤 문구가 바뀌면 422(05 §4.2 hash).
        recomputed = draft_hash(req.draft, req.sentencing)
        if not (req.draft_hash == req.evaluation_draft_hash == recomputed):
            raise Rejected(422, "INVALID_DRAFT")
        if self.finalize_errors:
            raise self.finalize_errors.pop(0)
        if self.finalize_error is not None:
            raise self.finalize_error
        return FinalizeResult(verdict_id=verdict_id, text_version=1, committed_at=DB_NOW)

    async def generation_failed(
        self, verdict_id: str, *, job_id: str, generation_id: str, error_code: str
    ) -> None:
        self.failed.append(error_code)


class FakeJobs:
    def __init__(self) -> None:
        self.completed: list[str] = []
        self.failures: list[tuple[str, float | None]] = []

    async def complete(self, job_id: str, worker_id: str, generation_id: str) -> bool:
        self.completed.append(job_id)
        return True

    async def fail(
        self,
        job_id: str,
        worker_id: str,
        generation_id: str,
        error_code: str,
        retry_after_s: float | None,
    ) -> bool:
        self.failures.append((error_code, retry_after_s))
        return True


class FakePreparation:
    def __init__(self, prep: Any, *, stale: list[str] | None = None) -> None:
        self.prep = prep
        self.stale = list(stale or [])
        self.asked: list[str] = []
        self.saved: list[Dossier] = []
        self.epoch_checks = 0

    async def load_valid_prep(self, snapshot: CaseSnapshot, prompt_version: str) -> Any:
        self.asked.append(prompt_version)
        return self.prep

    async def save_dossier(self, dossier: Dossier) -> str:
        self.saved.append(dossier)
        return dossier.dossier_id

    async def stale_scopes(self, privacy_versions: Any) -> list[str]:
        self.epoch_checks += 1
        return list(self.stale)


class ScriptedLLM(FakeLLM):
    """역할별 오류·순차 출력·호출 전 훅을 더한 FakeLLM."""

    def __init__(
        self,
        scenario: FakeScenario = FakeScenario.OK,
        *,
        errors: dict[str, LLMError] | None = None,
        sequences: dict[str, list[dict | None]] | None = None,
        before: Callable[[str], None] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(scenario, **kwargs)
        self.errors = dict(errors or {})
        self.sequences = {role: list(items) for role, items in (sequences or {}).items()}
        self.before = before

    async def structured_call(self, *, role: Any, messages: list[dict], schema: dict, **kw: Any):
        if self.before is not None:
            self.before(role)
        if role in self.errors:
            self.calls.append(
                FakeCall(
                    role, schema, messages, kw["timeout_s"], kw["max_output_tokens"], self.scenario
                )
            )
            raise self.errors[role]
        if self.sequences.get(role):
            output = self.sequences[role].pop(0)
            if output is None:
                self.outputs.pop(role, None)
            else:
                self.outputs[role] = output
        return await super().structured_call(role=role, messages=messages, schema=schema, **kw)


# ---------------------------------------------------------------------------
# 입력 만들기
# ---------------------------------------------------------------------------


def make_snapshot(**jury_update: Any) -> CaseSnapshot:
    data = fixture("case-snapshot-taxi")
    data["jury"].update({"target_intensities": ["spicy", "hell"], **jury_update})
    return CaseSnapshot.model_validate(data)


def make_dossier() -> Dossier:
    data = fixture("dossier-taxi")
    scope = Scope("ROOMS", frozenset({"room-ddegeoji-01"}))
    return Dossier(
        dossier_id=data["dossier_id"],
        post_id="post-taxi-20260907-0852",
        snapshot_hash="0" * 64,
        facts=tuple(
            EvidenceFact(
                label=f["label"],
                epistemic_type="DB_RECORD",
                fact_type=f["kind"],
                text=f["text"],
                scope=scope,
                aggregation=None,
                occurred_at=None,
                sources=(),
            )
            for f in data["facts"]
        ),
        label_map=data["label_map"],
        privacy_versions=(("user:user-01H8Z9QK", 1), ("room:room-ddegeoji-01", 1)),
    )


def make_prep() -> SimpleNamespace:
    candidate = Candidate(
        candidate_id="8d3f1a62-6c95-4b07-8e41-9a2d5f3b7c18",
        text=CANDIDATE_MARK,
        strategy=BanterStrategy.REPEAT_OFFENSE,
        fits=("guilty", "disagree"),
        evidence_labels=("F1",),
    )
    return SimpleNamespace(dossier=make_dossier(), banter={Intensity.spicy: [candidate]})


def make_job(kind: str = "SENTENCE", payload_extra: dict[str, Any] | None = None) -> Job:
    if kind == "SENTENCE":
        payload = {
            "verdict_id": "7a1d9c40-3b52-4e18-9f0a-2c6d8b4e1f31",
            "verdict_version": 1,
            "post_id": "post-taxi-20260907-0852",
        }
    else:
        payload = {
            "verdict_id": "7a1d9c40-3b52-4e18-9f0a-2c6d8b4e1f31",
            "verdict_version": 1,
            "round": 1,
        }
    payload.update(payload_extra or {})
    return Job(
        id="job-1",
        event_id="event-1",
        event_type="verdict.confirmed",
        kind=kind,  # type: ignore[arg-type]
        dedupe_key=f"{kind}:1",
        aggregate_id=payload["verdict_id"],
        aggregate_version=1,
        schema_version=1,
        payload=payload,
        status="RUNNING",
        priority=0,
        attempts=1,
        max_attempts=3,
        available_at=DB_NOW,
        deadline_at=None,
        lease_until=DB_NOW + timedelta(seconds=15),
        owner_id=WORKER_ID,
        generation_id=GENERATION_ID,
        last_error_code=None,
        trace_id="trace-1",
        created_at=DB_NOW,
        updated_at=DB_NOW,
    )


class Run(SimpleNamespace):
    state: dict[str, Any]
    backend: FakeBackend
    jobs: FakeJobs
    llm: FakeLLM

    def roles(self) -> Counter[str]:
        return Counter(call.role for call in self.llm.calls)

    def finalize(self) -> FinalizeRequest:
        assert len(self.backend.finalized) == 1
        return self.backend.finalized[0]

    def calls_of(self, role: str) -> list[FakeCall]:
        return [call for call in self.llm.calls if call.role == role]


def run(
    llm: FakeLLM | None = None,
    *,
    snapshot: CaseSnapshot | None = None,
    backend_kwargs: dict[str, Any] | None = None,
    prep: Any = "default",
    kind: str = "SENTENCE",
    clock: FakeClock | None = None,
    settings: Settings | None = None,
    preparation: FakePreparation | None = None,
    templates: dict[str, Any] | None = None,
    payload_extra: dict[str, Any] | None = None,
    notifier: Any = None,
) -> Run:
    llm = llm or FakeLLM()
    snapshot = snapshot or make_snapshot()
    backend = FakeBackend(snapshot, **(backend_kwargs or {}))
    jobs = FakeJobs()
    captured: dict[str, Any] = {}

    def factory(deps: Any) -> Any:
        graph = build_sentence_graph(
            deps if templates is None else replace(deps, templates=templates)
        )

        class Capturing:
            async def ainvoke(self, state: Any) -> Any:
                captured["state"] = await graph.ainvoke(state)
                return captured["state"]

        return Capturing()

    async def db_now() -> datetime:
        return DB_NOW

    ctx = SimpleNamespace(
        jobs=jobs,
        backend=backend,
        llm=llm,
        preparation=preparation or FakePreparation(make_prep() if prep == "default" else prep),
        semaphore=asyncio.Semaphore(8),
        settings=settings or Settings(_env_file=None),
        generation_id=GENERATION_ID,
        worker_id=WORKER_ID,
        notifier=notifier,
    )
    handler = SentenceHandler(factory, db_now=db_now, clock=clock or FakeClock())
    asyncio.run(handler(make_job(kind, payload_extra), ctx))
    return Run(state=captured.get("state", {}), backend=backend, jobs=jobs, llm=llm)


def fixture_sentencing() -> SentencingDecision:
    return SentencingDecision.model_validate(SENTENCING_FIXTURE)


def user_payload(call: FakeCall) -> dict[str, Any]:
    return json.loads(call.messages[-1]["content"])


def all_content(calls: list[FakeCall]) -> str:
    return "\n".join(str(m["content"]) for call in calls for m in call.messages)


def evaluation_without(intensity: str) -> dict[str, Any]:
    report = fixture("evaluation-taxi-pass")
    report.pop("schema_version")
    report.pop("policy_version")
    report["texts"] = [t for t in report["texts"] if t["intensity"] in {"spicy", "hell"}]
    report["texts"] = [t for t in report["texts"] if t["intensity"] != intensity]
    return report


def evaluation_reason_failed() -> dict[str, Any]:
    """전 강도 통과, `sentencing_reason_check` 만 실패한 보고서."""
    report = evaluation_without("mild")
    report["sentencing_reason_check"] = {
        "pass": False,
        "violations": [
            {
                "code": "SENTENCE_REASON_MISMATCH",
                "path": "sentencing.sentencing_reason",
                "evidence_labels": [],
                "explanation": "이유가 형량과 맞지 않는다",
            }
        ],
    }
    return report


def assert_finalize_hash_is_last_evaluated(result: Run) -> None:
    """finalize `draft_hash == evaluation_draft_hash` = 마지막 검수 입력 hash."""
    req = result.finalize()
    evaluated = user_payload(result.calls_of("evaluator")[-1])
    expected = draft_hash(evaluated["draft"], evaluated["sentencing"])
    assert req.draft_hash == req.evaluation_draft_hash == expected
    assert draft_hash(req.draft, req.sentencing) == expected


# ---------------------------------------------------------------------------
# ①~⑮
# ---------------------------------------------------------------------------


def test_01_guilty_two_intensities_call_counts() -> None:
    """① 유죄·2강도: 양형 1 + 서기 2 + 검수 1, 전 강도 AI 로 finalize."""
    result = run()
    assert result.roles() == Counter({"sentencing": 1, "writer": 2, "evaluator": 1})
    assert Counter(c.role for c in result.state["calls"]) == result.roles()
    assert all(c.error is None for c in result.state["calls"])
    req = result.finalize()
    assert [(t.intensity, t.source) for t in req.draft.texts] == [("spicy", "AI"), ("hell", "AI")]
    assert req.model_ids.model_dump() == {
        "sentencing": Settings(_env_file=None).MODEL_JUDGMENT,
        "writer": Settings(_env_file=None).MODEL_WRITER,
        "evaluator": Settings(_env_file=None).MODEL_JUDGMENT,
    }
    assert result.state["dossier_source"] == "PREP"
    assert result.backend.failed == []
    assert result.jobs.completed == ["job-1"]


def test_02_disagree_skips_sentencing() -> None:
    """② `disagree` → 양형 0호출, finalize sentencing null."""
    snapshot = make_snapshot(result="disagree", vote_counts={"agree": 1, "disagree": 3})
    result = run(snapshot=snapshot)
    assert result.roles()["sentencing"] == 0
    assert result.roles()["writer"] == 2
    req = result.finalize()
    assert req.sentencing is None
    assert req.draft.meme_tag == "REJECTED"


def test_03_out_of_list_sentence_clipped_to_top_rank(caplog: pytest.LogCaptureFixture) -> None:
    """③ 허용 목록 밖 형량 → rank 최대로 절삭 + 감사 로그.

    fallback 을 최대 rank 와 다르게(`probation`) 둬 fallback 으로 채우는 구현을 거른다.
    """
    policy = fixture("case-snapshot-taxi")["jury"]["policy"]
    snapshot = make_snapshot(policy={**policy, "fallback_sentence": "probation"})
    llm = ScriptedLLM(sequences={"sentencing": [{**SENTENCING_FIXTURE, "sentence": "life"}]})
    with caplog.at_level(logging.WARNING, logger="geoji_ai.graphs.sentencing"):
        result = run(llm, snapshot=snapshot)
    assert result.finalize().sentencing.sentence == "oneDay"
    assert result.state["sentencing_source"] == "AI"
    assert any(getattr(r, "audit", None) == "SENTENCE_CLIPPED" for r in caplog.records)


def test_04_sentencing_timeout_falls_back_to_rule() -> None:
    """④ 양형 timeout → `policy.fallback_sentence`, reason null, `sentencing_source=RULE`."""
    llm = ScriptedLLM(errors={"sentencing": LLMError("TIMEOUT")})
    result = run(llm)
    assert result.state["sentencing_source"] == "RULE"
    sentencing = result.finalize().sentencing
    assert sentencing.sentence == "oneDay"  # fallback_sentence
    assert sentencing.sentencing_reason is None
    assert [c.error for c in result.state["calls"] if c.role == "sentencing"] == ["TIMEOUT"]


def test_04b_default_deadline_10s_calls_sentencing_ai() -> None:
    """R1: 남은 10s(10 §3 SENTENCE 기본 마감)·유죄·spent → 양형 AI 1회.

    sentencing 예약 0 → timeout min(3, 10) = 3. writer min(6, 10 − 4.5) = 5.5, evaluator 4.
    """
    result = run(backend_kwargs={"remaining_s": 10.0})
    assert result.roles() == Counter({"sentencing": 1, "writer": 2, "evaluator": 1})
    assert result.state["sentencing_source"] == "AI"
    [sentencing_call] = result.calls_of("sentencing")
    assert sentencing_call.timeout_s == 3.0
    assert [c.timeout_s for c in result.calls_of("writer")] == [5.5, 5.5]
    assert result.jobs.completed == ["job-1"]


def test_04c_remaining_3s_calls_sentencing_then_writer_fallback_rule() -> None:
    """R1: 남은 3s → 양형은 부르고(min(3, 3)), 서기는 검수 예약(4.5s)이 없어 시작하지 않는다.

    이후는 기존 규칙(⑬ 보강)대로 `DEADLINE_EXCEEDED`.
    """
    result = run(backend_kwargs={"remaining_s": 3.0})
    assert result.roles()["sentencing"] == 1
    assert result.state["sentencing_source"] == "AI"
    assert result.roles()["writer"] == 0
    assert result.roles()["evaluator"] == 0
    assert result.backend.finalized == []
    assert result.backend.failed == ["DEADLINE_EXCEEDED"]


def test_05_long_reason_replaced_by_template_without_reevaluation() -> None:
    """⑤ `sentencing_reason` 101자 → 템플릿 치환 `reason_source=TEMPLATE`, 재검수 없음."""
    llm = ScriptedLLM(
        sequences={"sentencing": [{**SENTENCING_FIXTURE, "sentencing_reason": "가" * 101}]}
    )
    result = run(llm)
    sentencing = result.finalize().sentencing
    assert sentencing.sentencing_reason == "형량: 징역 1일 (내일 하루 무지출)"
    assert sentencing.reason_source == "TEMPLATE"
    assert result.roles()["evaluator"] == 1
    assert result.roles()["sentencing"] == 1


def test_05b_reason_check_failure_substitutes_and_reevaluates() -> None:
    """검수관 `sentencing_reason_check` 실패 → D-19 치환 → 재검수 1회(코디네이터 9/14)."""
    llm = ScriptedLLM(sequences={"evaluator": [evaluation_reason_failed(), None]})
    result = run(llm)
    sentencing = result.finalize().sentencing
    assert sentencing.sentence == fixture_sentencing().sentence
    assert sentencing.sentencing_reason == "형량: 징역 1일 (내일 하루 무지출)"
    assert sentencing.reason_source == "TEMPLATE"
    assert result.roles()["evaluator"] == 2
    second = user_payload(result.calls_of("evaluator")[-1])["sentencing"]
    assert second["sentencing_reason"] == "형량: 징역 1일 (내일 하루 무지출)"
    assert_finalize_hash_is_last_evaluated(result)
    assert result.backend.failed == []


def test_06_hell_writer_failure_gives_partial_template() -> None:
    """⑥ 서기 `hell` 만 실패 → `spicy` AI + `hell` TEMPLATE, finalize·검수 draft 에 둘 다."""
    result = run(ScriptedLLM(FakeScenario.INTENSITY_FAIL))
    req = result.finalize()
    assert [(t.intensity, t.source) for t in req.draft.texts] == [
        ("spicy", "AI"),
        ("hell", "TEMPLATE"),
    ]
    evaluated = user_payload(result.calls_of("evaluator")[0])["draft"]
    assert [(t["intensity"], t["source"]) for t in evaluated["texts"]] == [
        ("spicy", "AI"),
        ("hell", "TEMPLATE"),
    ]
    assert result.roles()["evaluator"] == 1
    assert result.backend.failed == []


def test_07_missing_intensity_in_report_becomes_template() -> None:
    """⑦ 검수 보고서 강도 누락 → 그 강도 TEMPLATE(바뀐 draft 는 한 번 더 검수)."""
    llm = ScriptedLLM(sequences={"evaluator": [evaluation_without("hell"), None]})
    result = run(llm)
    req = result.finalize()
    assert [(t.intensity, t.source) for t in req.draft.texts] == [
        ("spicy", "AI"),
        ("hell", "TEMPLATE"),
    ]
    assert result.roles()["evaluator"] == 2
    evaluated = user_payload(result.calls_of("evaluator")[-1])["draft"]
    assert [(t["intensity"], t["source"]) for t in evaluated["texts"]] == [
        ("spicy", "AI"),
        ("hell", "TEMPLATE"),
    ]
    assert_finalize_hash_is_last_evaluated(result)
    assert result.backend.failed == []


def test_08_evaluator_error_reports_eval_failed() -> None:
    """⑧ 검수관 오류 → 전 강도 TEMPLATE + `generation_failed(EVAL_FAILED)`, finalize 0."""
    llm = ScriptedLLM(errors={"evaluator": LLMError("SERVER")})
    result = run(llm)
    assert result.backend.finalized == []
    assert result.backend.failed == ["EVAL_FAILED"]
    assert set(result.state["draft_sources"].values()) == {"TEMPLATE"}
    assert result.jobs.completed == ["job-1"]


def test_09_finalize_stale_discards_and_completes() -> None:
    """⑨ finalize 409 STALE → complete, generation_failed 0."""
    result = run(backend_kwargs={"finalize_error": Rejected(409, "STALE_GENERATION")})
    assert len(result.backend.finalized) == 1
    assert result.backend.failed == []
    assert result.jobs.completed == ["job-1"]
    assert result.jobs.failures == []


def test_10_finalize_422_reports_schema_invalid() -> None:
    """⑩ finalize 422 → `generation_failed(SCHEMA_INVALID)`."""
    result = run(backend_kwargs={"finalize_error": Rejected(422, "INVALID_DRAFT")})
    assert result.backend.failed == ["SCHEMA_INVALID"]
    assert result.jobs.completed == ["job-1"]


@pytest.mark.parametrize(
    ("code", "failed"),
    [("DEADLINE_EXCEEDED", []), ("EVIDENCE_INVALIDATED", ["EVIDENCE_INVALIDATED"])],
)
def test_10b_finalize_other_409(code: str, failed: list[str]) -> None:
    """미리 정한 해석: 409 DEADLINE → 폐기, EVIDENCE_INVALIDATED → generation_failed."""
    result = run(backend_kwargs={"finalize_error": Rejected(409, code)})
    assert result.backend.failed == failed
    assert result.jobs.completed == ["job-1"]


def test_11_regenerate_uses_fixed_sentencing() -> None:
    """⑪ `REGENERATE` → 양형 0호출·고정 형량 그대로."""
    fixed = {"sentence": "probation", "sentencing_reason": "고정된 이유", "reason_source": "AI"}
    result = run(backend_kwargs={"fixed": fixed}, kind="TEXT_RETRY")
    assert result.state["mode"] == "REGENERATE"
    assert result.state["sentencing_source"] == "FIXED"
    assert result.roles()["sentencing"] == 0
    assert result.roles()["writer"] == 2  # target_intensities 전부
    req = result.finalize()
    assert [t.intensity for t in req.draft.texts] == ["spicy", "hell"]
    sentencing = req.sentencing
    assert (sentencing.sentence, sentencing.sentencing_reason, sentencing.reason_source) == (
        "probation",
        "고정된 이유",
        "AI",
    )


def test_12_finalize_hash_equals_evaluated_draft_hash() -> None:
    """⑫ finalize `draft_hash == evaluation_draft_hash` = 검수에 넘긴 초안 hash."""
    result = run()
    assert result.roles()["evaluator"] == 1
    assert_finalize_hash_is_last_evaluated(result)


def test_13_writer_delay_leaves_no_evaluator_budget() -> None:
    """⑬ 서기 지연으로 남은 시간 < 검수 예약 → 검수 미시작·`DEADLINE_EXCEEDED`."""
    clock = FakeClock()

    def delay(role: str) -> None:
        if role == "writer":
            clock.now += 4.5  # 두 강도 합 9초

    snapshot = make_snapshot(result="disagree", vote_counts={"agree": 1, "disagree": 3})
    result = run(
        ScriptedLLM(before=delay),
        snapshot=snapshot,
        backend_kwargs={"remaining_s": 9.3},
        clock=clock,
    )
    assert result.roles()["writer"] == 2
    assert result.roles()["evaluator"] == 0
    assert result.backend.finalized == []
    assert result.backend.failed == ["DEADLINE_EXCEEDED"]


def test_13b_writer_not_started_without_evaluator_time() -> None:
    """⑬ 보강: 검수 시간을 확보할 수 없으면 새 서기 호출을 시작하지 않는다."""
    snapshot = make_snapshot(result="disagree", vote_counts={"agree": 1, "disagree": 3})
    result = run(snapshot=snapshot, backend_kwargs={"remaining_s": 4.4})
    assert result.roles()["writer"] == 0
    assert result.backend.failed == ["DEADLINE_EXCEEDED"]


def test_14_input_minimization() -> None:
    """⑭ 양형관·검수관에 말투 예시·드립 후보 없음, `spicy` 서기 시스템에 `hell` 섹션 없음."""
    result = run()
    sentencing = all_content(result.calls_of("sentencing"))
    evaluator = all_content(result.calls_of("evaluator"))
    for text in (sentencing, evaluator):
        assert "style_examples" not in text
        assert "banter_candidates" not in text
        assert CANDIDATE_MARK not in text
    writers = {
        call.schema["properties"]["intensity"]["enum"][0]: call
        for call in result.calls_of("writer")
    }
    spicy_system = writers["spicy"].messages[0]["content"]
    assert "### SPICY" in spicy_system
    assert "### HELL" not in spicy_system
    assert CANDIDATE_MARK in all_content([writers["spicy"]])
    assert CANDIDATE_MARK not in all_content([writers["hell"]])
    assert "style_examples" not in writers["spicy"].messages[-1]["content"]


@pytest.mark.parametrize(
    "llm_factory",
    [
        lambda: FakeLLM(),
        lambda: ScriptedLLM(FakeScenario.INTENSITY_FAIL),
        lambda: ScriptedLLM(sequences={"evaluator": [evaluation_without("spicy"), None]}),
    ],
    ids=["ok", "writer-fail", "evaluator-swap"],
)
def test_15_sentencing_unchanged_on_writer_and_evaluator_paths(
    llm_factory: Callable[[], FakeLLM],
) -> None:
    """⑮ 형량 불변: 서기·검수 경로 어디서도 finalize `sentencing` 이 양형 결과와 같다."""
    result = run(llm_factory())
    assert result.finalize().sentencing == fixture_sentencing()
    assert result.state["sentencing"] == fixture_sentencing()


# ---------------------------------------------------------------------------
# 그 밖(노드 동작 표)
# ---------------------------------------------------------------------------


def test_begin_409_discards_without_model_calls() -> None:
    result = run(backend_kwargs={"begin_error": Rejected(409, "STALE_GENERATION")})
    assert result.llm.calls == []
    assert result.backend.finalized == [] and result.backend.failed == []
    assert result.jobs.completed == ["job-1"]


def test_missing_prep_uses_minimal_dossier() -> None:
    """prep 없음 ∧ 남은 < 8.5s → MINIMAL(코드 Evidence F0 만), 조서 호출 없음, dossier 저장."""
    result = run(prep=None, backend_kwargs={"remaining_s": 8.4})
    assert result.state["dossier_source"] == "MINIMAL"
    assert set(result.state["dossier"].label_map) == {"F0"}
    assert "context" not in result.roles()


def test_all_writers_failed_reports_vendor_unavailable() -> None:
    llm = ScriptedLLM(errors={"writer": LLMError("SERVER")})
    result = run(llm)
    assert result.roles()["evaluator"] == 0
    assert result.backend.failed == ["VENDOR_UNAVAILABLE"]


def test_backend_unavailable_fails_job() -> None:
    class Unavailable(Exception):
        error_code = "BACKEND_UNAVAILABLE"
        retry_after_s = 5

    result = run(backend_kwargs={"finalize_error": Unavailable()})
    assert result.jobs.failures == [("BACKEND_UNAVAILABLE", 5)]
    assert result.jobs.completed == []


# ---------------------------------------------------------------------------
# GR-06 검수·보정 / GR-03 남은 것
# ---------------------------------------------------------------------------

POST_ID = "post-taxi-20260907-0852"


def report(intensities: Iterable[str], fail: Iterable[str] = ()) -> dict[str, Any]:
    """요청 강도만의 검수 출력. `fail` 강도는 `pass=false` + 위반·문제 문장."""
    base = fixture("evaluation-taxi-pass")
    base.pop("schema_version")
    base.pop("policy_version")
    by_intensity = {t["intensity"]: t for t in base["texts"]}
    failing = set(fail)
    texts = []
    for index, intensity in enumerate(intensities):
        entry = dict(by_intensity[intensity])
        if intensity in failing:
            entry["pass"] = False
            entry["violations"] = [
                {
                    "code": "PERSONAL_ATTACK",
                    "path": f"texts[{index}].statement[0].text",
                    "evidence_labels": [],
                    "explanation": "인신공격",
                }
            ]
            entry["problem_sentences"] = ["문제 문장 마커"]
        texts.append(entry)
    base["texts"] = texts
    return base


def writer_angle(call: FakeCall) -> str:
    return call.schema["properties"]["attack_angle"]["enum"][0]


def writer_intensity(call: FakeCall) -> str:
    return call.schema["properties"]["intensity"]["enum"][0]


def evaluator_intensities(call: FakeCall) -> list[str]:
    return call.schema["properties"]["texts"]["items"]["properties"]["intensity"]["enum"]


def test_16_evaluator_failure_repairs_failed_intensity_only() -> None:
    """검수 `hell` 실패 → repair(hell 만, 각도 +1, 피할 것) → hell 만 재검수 통과 → AI finalize."""
    llm = ScriptedLLM(
        sequences={"evaluator": [report(["spicy", "hell"], fail=["hell"]), report(["hell"])]}
    )
    result = run(llm)
    writers = result.calls_of("writer")
    assert [writer_intensity(c) for c in writers] == ["spicy", "hell", "hell"]
    assert writer_angle(writers[0]) == pick(POST_ID, 0).value
    assert writer_angle(writers[2]) == pick(POST_ID, 1).value
    avoid = user_payload(writers[2])["avoid"]
    assert avoid == {"violations": ["PERSONAL_ATTACK"], "problem_sentences": ["문제 문장 마커"]}
    assert "avoid" not in user_payload(writers[1])
    evaluators = result.calls_of("evaluator")
    assert [evaluator_intensities(c) for c in evaluators] == [["spicy", "hell"], ["hell"]]
    assert [t["intensity"] for t in user_payload(evaluators[1])["draft"]["texts"]] == ["hell"]
    assert result.state["repair_count"] == 1
    req = result.finalize()
    assert [(t.intensity, t.source) for t in req.draft.texts] == [("spicy", "AI"), ("hell", "AI")]
    assert [t.intensity for t in req.evaluation.texts] == ["spicy", "hell"]
    assert req.sentencing == fixture_sentencing()  # 양형 고정
    assert result.roles()["sentencing"] == 1
    assert result.backend.failed == []


def test_17_repair_evaluation_fails_again_then_template() -> None:
    """검수 실패 2회 → repair_count=1 뒤 그 강도 TEMPLATE, 전체 재검수 뒤 부분 저장."""
    llm = ScriptedLLM(
        sequences={
            "evaluator": [
                report(["spicy", "hell"], fail=["hell"]),
                report(["hell"], fail=["hell"]),
                None,
            ]
        }
    )
    result = run(llm)
    assert result.roles()["writer"] == 3
    assert result.roles()["evaluator"] == 3
    assert result.state["repair_count"] == 1
    req = result.finalize()
    assert [(t.intensity, t.source) for t in req.draft.texts] == [
        ("spicy", "AI"),
        ("hell", "TEMPLATE"),
    ]
    assert_finalize_hash_is_last_evaluated(result)
    assert result.backend.failed == []


def test_18_no_repair_when_less_than_5s_remain() -> None:
    """남은 < 5s → repair 없이 실패 강도 TEMPLATE."""
    llm = ScriptedLLM(sequences={"evaluator": [report(["spicy", "hell"], fail=["hell"]), None]})
    result = run(llm, backend_kwargs={"remaining_s": 4.9})
    assert result.roles()["writer"] == 2
    assert result.roles()["evaluator"] == 2
    assert result.state["repair_count"] == 0
    req = result.finalize()
    assert [(t.intensity, t.source) for t in req.draft.texts] == [
        ("spicy", "AI"),
        ("hell", "TEMPLATE"),
    ]


FIXED = {"sentence": "probation", "sentencing_reason": "고정된 이유", "reason_source": "AI"}


def test_19_regenerate_does_not_repair() -> None:
    """TEXT_RETRY(REGENERATE) 는 round 안 보정 없음.

    검수 실패 → TEMPLATE 저장 없이 `EVAL_FAILED`(08 §3.2).
    """
    llm = ScriptedLLM(sequences={"evaluator": [report(["spicy", "hell"], fail=["hell"]), None]})
    result = run(llm, backend_kwargs={"fixed": FIXED}, kind="TEXT_RETRY")
    assert result.roles() == Counter({"writer": 2, "evaluator": 1})
    assert result.state["repair_count"] == 0
    assert result.backend.finalized == []
    assert result.backend.failed == ["EVAL_FAILED"]
    assert result.jobs.completed == ["job-1"]


def test_25_regenerate_only_payload_intensities() -> None:
    """payload `intensities=["hell"]` → 서기 1·검수 1(hell 만)·양형 0, finalize texts=[hell]."""
    result = run(
        backend_kwargs={"fixed": FIXED},
        kind="TEXT_RETRY",
        payload_extra={"intensities": ["hell"]},
    )
    assert result.roles() == Counter({"writer": 1, "evaluator": 1})
    assert [writer_intensity(c) for c in result.calls_of("writer")] == ["hell"]
    assert [evaluator_intensities(c) for c in result.calls_of("evaluator")] == [["hell"]]
    req = result.finalize()
    assert [(t.intensity, t.source) for t in req.draft.texts] == [("hell", "AI")]
    assert [t.intensity for t in req.evaluation.texts] == ["hell"]
    assert (req.sentencing.sentence, req.sentencing.sentencing_reason) == (
        "probation",
        "고정된 이유",
    )
    assert_finalize_hash_is_last_evaluated(result)


def test_26_regenerate_intensity_outside_target_fails_without_calls() -> None:
    """target 밖 강도 → 모델 호출 없이 `SCHEMA_INVALID`(계획서에 없는 판단)."""
    result = run(
        backend_kwargs={"fixed": FIXED},
        kind="TEXT_RETRY",
        payload_extra={"intensities": ["mild"]},
    )
    assert result.llm.calls == []
    assert result.backend.finalized == []
    assert result.backend.failed == ["SCHEMA_INVALID"]


def test_27_regenerate_without_prep_is_minimal_even_with_time() -> None:
    """REGENERATE ∧ prep 없음 → 남은 시간이 넉넉해도 MINIMAL(조서 0).

    ⑤ 실패도 TEMPLATE 저장 없음.
    """
    result = run(prep=None, backend_kwargs={"fixed": FIXED, "remaining_s": 60.0}, kind="TEXT_RETRY")
    assert result.state["dossier_source"] == "MINIMAL"
    assert "context" not in result.roles()
    assert all(
        [f["id"] for f in user_payload(c)["dossier"]] == ["F0"] for c in result.calls_of("writer")
    )
    # 서기 fixture 는 F0 밖 라벨을 인용해 ⑤ 에서 걸린다(test_graph_c_flow ② 와 같은 fixture 한계).
    assert result.backend.finalized == []
    assert result.backend.failed == ["EVAL_FAILED"]


def test_28_regenerate_writer_failure_does_not_finalize_template() -> None:
    """REGENERATE 에서 한 강도라도 서기 실패 → 검수 0·finalize 0·기존 코드 규칙."""
    result = run(
        ScriptedLLM(FakeScenario.INTENSITY_FAIL), backend_kwargs={"fixed": FIXED}, kind="TEXT_RETRY"
    )
    assert result.roles()["evaluator"] == 0
    assert result.backend.finalized == []
    assert result.backend.failed == ["VENDOR_UNAVAILABLE"]


@pytest.mark.parametrize(("retry_timeout_s", "failed"), [(10, ["DEADLINE_EXCEEDED"]), (20, [])])
def test_29_regenerate_budget_is_text_retry_timeout(
    retry_timeout_s: int, failed: list[str]
) -> None:
    """begin 마감이 60s 여도 REGENERATE 예산은 `TEXT_RETRY_TIMEOUT_SECONDS` 다."""
    clock = FakeClock()

    def delay(role: str) -> None:
        if role == "writer":
            clock.now += 4.8  # 두 강도 합 9.6초

    result = run(
        ScriptedLLM(before=delay),
        backend_kwargs={"fixed": FIXED, "remaining_s": 60.0},
        kind="TEXT_RETRY",
        clock=clock,
        settings=Settings(_env_file=None, TEXT_RETRY_TIMEOUT_SECONDS=retry_timeout_s),
    )
    assert result.backend.failed == failed
    if failed:
        assert result.roles()["evaluator"] == 0
        assert result.backend.finalized == []
    else:
        assert len(result.backend.finalized) == 1


def test_30_regenerate_reason_check_failure_keeps_reason() -> None:
    """REGENERATE 검수 `sentencing_reason_check` 실패 → 이유 치환·재검수 없이 EVAL_FAILED."""
    llm = ScriptedLLM(sequences={"evaluator": [evaluation_reason_failed()]})
    result = run(llm, backend_kwargs={"fixed": FIXED}, kind="TEXT_RETRY")
    assert result.roles()["evaluator"] == 1
    assert result.backend.finalized == []
    assert result.backend.failed == ["EVAL_FAILED"]
    assert result.state["sentencing"].sentencing_reason == "고정된 이유"


def test_20_finalize_422_repairs_once_then_succeeds() -> None:
    """finalize 422 ∧ repair 남음 → AI 강도 repair 1회 → 재검수 → finalize 성공."""
    result = run(backend_kwargs={"finalize_errors": [Rejected(422, "INVALID_DRAFT")]})
    assert len(result.backend.finalized) == 2
    assert result.roles() == Counter({"sentencing": 1, "writer": 4, "evaluator": 2})
    assert [writer_angle(c) for c in result.calls_of("writer")[2:]] == [pick(POST_ID, 1).value] * 2
    assert result.state["repair_count"] == 1
    assert result.backend.failed == []
    first, second = result.backend.finalized
    assert first.sentencing == second.sentencing == fixture_sentencing()
    assert_finalize_hash_is_last_evaluated(
        Run(
            state=result.state, backend=_last_only(result.backend), jobs=result.jobs, llm=result.llm
        )
    )


def _last_only(backend: FakeBackend) -> FakeBackend:
    backend.finalized = backend.finalized[-1:]
    return backend


def test_20b_finalize_422_after_repair_used_reports_schema_invalid() -> None:
    """검수 repair 를 이미 썼으면 422 는 `SCHEMA_INVALID`."""
    llm = ScriptedLLM(
        sequences={"evaluator": [report(["spicy", "hell"], fail=["hell"]), report(["hell"])]}
    )
    result = run(llm, backend_kwargs={"finalize_errors": [Rejected(422, "INVALID_DRAFT")]})
    assert result.roles()["writer"] == 3
    assert len(result.backend.finalized) == 1
    assert result.backend.failed == ["SCHEMA_INVALID"]


def test_21_hell_evaluated_separately_when_model_differs() -> None:
    """`hell` 포함 ∧ `MODEL_EVALUATOR_HELL != MODEL_JUDGMENT` → hell 만 별도 호출."""
    settings = Settings(_env_file=None, MODEL_EVALUATOR_HELL="other-hell-model")
    result = run(settings=settings)
    evaluators = result.calls_of("evaluator")
    assert sorted(evaluator_intensities(c) for c in evaluators) == [["hell"], ["spicy"]]
    records = [c for c in result.state["calls"] if c.role == "evaluator"]
    assert sorted(str(c.intensity) for c in records) == ["None", "hell"]
    req = result.finalize()
    assert [t.intensity for t in req.evaluation.texts] == ["spicy", "hell"]
    assert result.backend.failed == []


def test_21b_same_model_single_evaluator_call() -> None:
    result = run()
    assert [evaluator_intensities(c) for c in result.calls_of("evaluator")] == [["spicy", "hell"]]


def test_22_epoch_mismatch_before_finalize() -> None:
    """finalize 직전 epoch 불일치 → finalize 0 · `generation_failed(EVIDENCE_INVALIDATED)`."""
    preparation = FakePreparation(make_prep(), stale=["room:room-ddegeoji-01"])
    result = run(preparation=preparation)
    assert preparation.epoch_checks == 1
    assert result.backend.finalized == []
    assert result.backend.failed == ["EVIDENCE_INVALIDATED"]
    assert result.jobs.completed == ["job-1"]


def test_22b_epoch_checked_once_on_success() -> None:
    preparation = FakePreparation(make_prep())
    result = run(preparation=preparation)
    assert preparation.epoch_checks == 1
    assert len(result.backend.finalized) == 1


def test_23_missing_intensity_fails_at_join() -> None:
    """서기 hell 실패 ∧ 템플릿 없음 → 강도 누락 → join `SCHEMA_INVALID`, 검수 0."""
    llm = ScriptedLLM(FakeScenario.INTENSITY_FAIL)
    result = run(llm, templates={"results": {}, "sentence_labels": {}})
    assert result.roles()["evaluator"] == 0
    assert result.backend.finalized == []
    assert result.backend.failed == ["SCHEMA_INVALID"]


def test_24_minimal_dossier_is_saved() -> None:
    """MINIMAL dossier 도 저장한다(finalize dossier_id 무결성)."""
    preparation = FakePreparation(None)
    result = run(preparation=preparation, backend_kwargs={"remaining_s": 8.4})
    assert result.state["dossier_source"] == "MINIMAL"
    assert [d.dossier_id for d in preparation.saved] == [result.state["dossier"].dossier_id]
