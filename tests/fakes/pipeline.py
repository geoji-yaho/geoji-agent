"""에이전트 파이프라인 한 바퀴를 메모리 포트로 돌리고 역할 간 핸드오프를 기록한다(9/19).

심문관(그래프 A) → 조서·드립(그래프 B) → 양형관·서기·검수관·finalize(그래프 C) 를 **같은 사건**으로
잇는다. 각 모델 호출의 시스템 프롬프트·사용자 입력·출력을 `RecordingLLM` 이 그대로 남기고,
`render_trace()` 가 사람이 읽는 마크다운으로 푼다. 어느 역할이 무엇을 받고 무엇을 넘기는지,
어디서 정보가 끊기는지를 보기 위한 것이다.

- 단위 테스트(`tests/unit/test_agent_handoff.py`)는 `FakeLLM` 으로 돈다
- `scripts/trace_pipeline.py --execute` 는 같은 러너를 실제 모델로 돌린다(유료)

DB·네트워크·백엔드 없음. 포트는 전부 이 파일의 메모리 fake 다.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from geoji_ai.application.sentence_case import SentenceHandler
from geoji_ai.contracts.case import CaseSnapshot
from geoji_ai.contracts.finalize import FinalizeRequest
from geoji_ai.contracts.intake import IntakeRequest, IntakeResult
from geoji_ai.contracts.jobs import Job
from geoji_ai.core.config import Settings
from geoji_ai.domain.intensity import Intensity
from geoji_ai.graphs.intake import run_intake
from geoji_ai.graphs.preparation import PrepareDeps, run_preparation
from geoji_ai.graphs.sentencing import build_sentence_graph
from geoji_ai.graphs.states import Candidate, PrepareState
from geoji_ai.ports.backend import (
    Aggregates,
    AggregateWindow,
    BeginGenerationResult,
    EvidenceScope,
    EvidenceSource,
    FinalizeResult,
    RecentVerdict,
    ResolveEvidenceRequest,
    ResolveEvidenceResponse,
    RoomRule,
)
from geoji_ai.ports.llm import LLMPort, LLMResult
from geoji_ai.ports.memory import MemoryCandidate, RoomRecall
from geoji_ai.ports.preparation import Dossier, PrepSaveResult, ValidPrep
from geoji_ai.prompts import prompt_bundle_version

__all__ = [
    "DB_NOW",
    "GENERATION_ID",
    "MemoryBackend",
    "MemoryPreparation",
    "MemoryRecall",
    "PipelineTrace",
    "RecordedCall",
    "RecordingLLM",
    "make_prepare_job",
    "make_sentence_job",
    "render_trace",
    "resolved_evidence_for",
    "run_pipeline",
    "user_payload",
]

DB_NOW = datetime(2026, 9, 7, 9, 20, tzinfo=UTC)
GENERATION_ID = "gen-pipeline-1"
WORKER_ID = "worker-pipeline-1"
PREP_ID = "prep-pipeline-1"


# ---------------------------------------------------------------------------
# 호출 기록
# ---------------------------------------------------------------------------


@dataclass
class RecordedCall:
    """모델 호출 1회. 역할·프롬프트·입력·출력·오류를 원문 그대로 둔다(테스트·추적 전용)."""

    stage: str
    role: str
    messages: list[dict[str, Any]]
    schema: dict[str, Any]
    output: dict[str, Any] | None
    stop_reason: str | None
    error: str | None
    timeout_s: float
    max_output_tokens: int

    @property
    def system(self) -> str:
        return str(self.messages[0]["content"]) if self.messages else ""

    @property
    def user(self) -> dict[str, Any]:
        return user_payload(self)


def user_payload(call: RecordedCall) -> dict[str, Any]:
    """마지막 사용자 메시지(JSON)를 dict 로."""
    return json.loads(call.messages[-1]["content"])


class RecordingLLM:
    """어떤 `LLMPort` 든 감싸 호출을 기록한다. `stage` 는 러너가 단계마다 바꾼다."""

    def __init__(self, inner: LLMPort) -> None:
        self.inner = inner
        self.stage = "?"
        self.calls: list[RecordedCall] = []

    async def structured_call(
        self,
        *,
        role: Any,
        messages: list[dict],
        schema: dict,
        timeout_s: float,
        max_output_tokens: int,
    ) -> LLMResult:
        record = RecordedCall(
            stage=self.stage,
            role=str(role),
            messages=[dict(m) for m in messages],
            schema=schema,
            output=None,
            stop_reason=None,
            error=None,
            timeout_s=timeout_s,
            max_output_tokens=max_output_tokens,
        )
        self.calls.append(record)
        try:
            result = await self.inner.structured_call(
                role=role,
                messages=messages,
                schema=schema,
                timeout_s=timeout_s,
                max_output_tokens=max_output_tokens,
            )
        except Exception as exc:
            record.error = getattr(exc, "kind", None) or type(exc).__name__
            raise
        record.output = dict(result.output) if isinstance(result.output, Mapping) else None
        record.stop_reason = result.stop_reason
        return result

    def of(self, role: str) -> list[RecordedCall]:
        return [c for c in self.calls if c.role == role]


# ---------------------------------------------------------------------------
# 메모리 포트
# ---------------------------------------------------------------------------


class MemoryBackend:
    """`BackendPort` 메모리 fake. 그래프 B 의 resolve-evidence 와 그래프 C 의 begin·finalize."""

    def __init__(
        self,
        snapshot: CaseSnapshot,
        resolved: ResolveEvidenceResponse | None,
        *,
        remaining_s: float = 60.0,
        fixed: dict[str, Any] | None = None,
    ) -> None:
        self._snapshot = snapshot
        self.resolved = resolved
        self.remaining_s = remaining_s
        self.fixed = fixed
        self.resolve_requests: list[ResolveEvidenceRequest] = []
        self.began: list[dict[str, Any]] = []
        self.finalized: list[FinalizeRequest] = []
        self.failed: list[str] = []

    async def snapshot(self, job_id: str, generation_id: str) -> CaseSnapshot:
        return self._snapshot

    async def resolve_evidence(
        self, job_id: str, generation_id: str, request: ResolveEvidenceRequest
    ) -> ResolveEvidenceResponse:
        self.resolve_requests.append(request)
        if self.resolved is None:
            raise RuntimeError("resolve-evidence 없음(F0 만)")
        return self.resolved

    async def begin_generation(
        self, verdict_id: str, *, job_id: str, generation_id: str, verdict_version: int
    ) -> BeginGenerationResult:
        self.began.append({"verdict_id": verdict_id, "job_id": job_id})
        return BeginGenerationResult.model_validate(
            {
                "fixed_sentencing": self.fixed,
                "text_version": 0,
                "deadline_at": DB_NOW + timedelta(seconds=self.remaining_s),
            }
        )

    async def finalize(self, verdict_id: str, req: FinalizeRequest) -> FinalizeResult:
        self.finalized.append(FinalizeRequest.model_validate(req.model_dump(mode="json")))
        return FinalizeResult(verdict_id=verdict_id, text_version=1, committed_at=DB_NOW)

    async def generation_failed(
        self, verdict_id: str, *, job_id: str, generation_id: str, error_code: str
    ) -> None:
        self.failed.append(error_code)


class MemoryPreparation:
    """`PreparationPort` 메모리 fake. 그래프 B 가 저장한 조서·드립을 그래프 C 가 그대로 받는다."""

    def __init__(self, *, approved_examples: Mapping[str, list[str]] | None = None) -> None:
        self.dossier: Dossier | None = None
        self.banter: dict[Intensity, list[Candidate]] = {}
        self.status: str = ""
        self.prompt_version: str | None = None
        self.approved_examples = {k: list(v) for k, v in (approved_examples or {}).items()}
        self.saved_dossiers: list[Dossier] = []

    async def save_dossier(self, dossier: Dossier) -> str:
        self.saved_dossiers.append(dossier)
        return dossier.dossier_id

    async def save_prep(
        self,
        dossier: Dossier,
        snapshot: CaseSnapshot,
        prompt_version: str,
        *,
        reuse_dossier: bool = False,
    ) -> PrepSaveResult:
        self.dossier = dossier
        self.prompt_version = prompt_version
        self.status = "DOSSIER_READY"
        return PrepSaveResult(prep_id=PREP_ID, status="DOSSIER_READY", reused=False)

    async def save_banter(self, prep_id: str, banter: Mapping[Intensity, list[Candidate]]) -> bool:
        if self.status != "DOSSIER_READY":
            return False
        self.banter = {k: list(v) for k, v in banter.items()}
        self.status = "COMPLETE"
        return True

    async def load_valid_prep(
        self, snapshot: CaseSnapshot, prompt_version: str
    ) -> ValidPrep | None:
        if self.dossier is None or self.prompt_version != prompt_version:
            return None
        return ValidPrep(dossier=self.dossier, banter=dict(self.banter), prep_id=PREP_ID)

    async def approved_banter_examples(
        self, intensity: Intensity, category: str, limit: int
    ) -> list[str]:
        return list(self.approved_examples.get(intensity.value, []))[:limit]

    async def stale_scopes(self, privacy_versions: Any) -> list[str]:
        return []


class MemoryRecall:
    """`MemoryPort` 중 그래프 B 가 쓰는 둘. 기억 은행에서 찾은 후보 참조만 돌려준다."""

    def __init__(self, candidates: list[MemoryCandidate] | None = None) -> None:
        self.candidates = list(candidates or [])
        self.user_queries: list[tuple[str, str]] = []
        self.room_queries: list[tuple[str, str]] = []

    async def recall_user(
        self,
        author_id: str,
        category: str,
        before: datetime,
        limit: int,
        *,
        reason: str | None = None,
    ) -> list[MemoryCandidate]:
        self.user_queries.append((author_id, category))
        return list(self.candidates)[:limit]

    async def recall_room(self, room_id: str, category: str) -> RoomRecall:
        self.room_queries.append((room_id, category))
        return RoomRecall()


class _Jobs:
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


# ---------------------------------------------------------------------------
# 입력 만들기
# ---------------------------------------------------------------------------


def resolved_evidence_for(
    snapshot: CaseSnapshot,
    *,
    repeat_30d: int = 3,
    burn_rate: float = 0.41,
    tier: str = "상거지",
    no_spend_days: int = 3,
    room_rule_text: str | None = "한 달에 택시 1번",
    prior_verdict: bool = True,
    prior_spend: bool = True,
) -> ResolveEvidenceResponse:
    """백엔드 resolve-evidence 응답.

    기본은 "이력 있는 단골 사건"(반복 3건·방 규칙·지난 판결·지난 지출)이다.
    """
    room_ids = list(snapshot.audience.room_ids)
    room_id = room_ids[0] if room_ids else "room-none"
    scope = EvidenceScope(visibility="ROOMS", room_ids=room_ids)
    created = snapshot.created_at
    rules = (
        [RoomRule(room_id=room_id, rule_id="rule-1", version=1, text=room_rule_text)]
        if room_rule_text
        else []
    )
    verdicts = (
        [
            RecentVerdict(
                post_id="post-prior-1",
                post_version=1,
                category=snapshot.category,
                amount_krw=9500,
                reason="늦잠",
                result="guilty",
                sentence="probation",
                judged_at=created - timedelta(days=5),
                scope=scope,
            )
        ]
        if prior_verdict
        else []
    )
    sources = (
        [
            EvidenceSource(
                source_type="POST",
                source_id="post-prior-2",
                source_version=1,
                payload={"category": snapshot.category, "amount_krw": 8800, "reason": "비 와서"},
                scope=scope,
            )
        ]
        if prior_spend
        else []
    )
    return ResolveEvidenceResponse(
        sources=sources,
        aggregates=Aggregates(
            burn_rate=burn_rate,
            tier=tier,
            no_spend_days=no_spend_days,
            repeat_same_category_30d=repeat_30d,
            excludes_post_id=snapshot.post_id,
            window=AggregateWindow(start_at=created - timedelta(days=30), end_at=created),
            rule_version=1,
        ),
        room_rules=rules,
        recent_verdicts=verdicts,
        style_comments=[],
    )


def _job(kind: str, payload: dict[str, Any], event_type: str) -> Job:
    return Job(
        id=f"job-{kind.lower()}-1",
        event_id=f"event-{kind.lower()}-1",
        event_type=event_type,
        kind=kind,  # type: ignore[arg-type]
        dedupe_key=f"{kind}:1",
        aggregate_id=str(payload.get("verdict_id") or payload.get("post_id")),
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
        trace_id="trace-pipeline-1",
        created_at=DB_NOW,
        updated_at=DB_NOW,
    )


def make_prepare_job(snapshot: CaseSnapshot) -> Job:
    return _job(
        "PREPARE",
        {
            "post_id": snapshot.post_id,
            "post_version": snapshot.post_version,
            "audience_version": snapshot.audience.audience_version,
        },
        "post.created",
    )


def make_sentence_job(snapshot: CaseSnapshot) -> Job:
    jury = snapshot.jury
    assert jury is not None
    return _job(
        "SENTENCE",
        {
            "verdict_id": jury.verdict_id,
            "verdict_version": jury.verdict_version,
            "post_id": snapshot.post_id,
        },
        "verdict.confirmed",
    )


def intake_request_for(snapshot: CaseSnapshot) -> IntakeRequest:
    return IntakeRequest.model_validate(
        {
            "schema_version": 1,
            "submission_id": f"sub-{snapshot.post_id}",
            "payload_hash": "0" * 64,
            "mode": "INITIAL",
            "post_type": snapshot.post_type,
            "amount_krw": snapshot.amount_krw,
            "category": snapshot.category,
            "item": snapshot.item,
            "reason": snapshot.reason,
        }
    )


# ---------------------------------------------------------------------------
# 러너
# ---------------------------------------------------------------------------


@dataclass
class PipelineTrace:
    """한 바퀴의 결과. 단계별 상태와 호출 기록."""

    snapshot: CaseSnapshot
    settings: Settings
    llm: RecordingLLM
    backend: MemoryBackend
    preparation: MemoryPreparation
    recall: MemoryRecall
    intake: IntakeResult | None = None
    prepare_state: PrepareState | None = None
    sentence_state: dict[str, Any] = field(default_factory=dict)
    jobs: _Jobs = field(default_factory=_Jobs)

    def calls(self, role: str) -> list[RecordedCall]:
        return self.llm.of(role)

    def call(self, role: str, index: int = 0) -> RecordedCall:
        calls = self.calls(role)
        if len(calls) <= index:
            raise AssertionError(f"{role} 호출이 {len(calls)}회뿐이다(요청 {index})")
        return calls[index]

    @property
    def dossier(self) -> Dossier | None:
        return self.preparation.dossier

    @property
    def finalize_request(self) -> FinalizeRequest | None:
        return self.backend.finalized[0] if self.backend.finalized else None

    @property
    def outcome(self) -> str:
        if self.backend.finalized:
            return "AI_READY"
        if self.backend.failed:
            return f"FAILED:{self.backend.failed[0]}"
        return "NONE"


async def run_pipeline_async(
    llm: LLMPort,
    snapshot: CaseSnapshot,
    *,
    settings: Settings | None = None,
    resolved: ResolveEvidenceResponse | None | str = "default",
    recall_candidates: list[MemoryCandidate] | None = None,
    approved_examples: Mapping[str, list[str]] | None = None,
    remaining_s: float = 60.0,
    with_intake: bool = True,
    with_prepare: bool = True,
) -> PipelineTrace:
    """심문관 → 그래프 B → 그래프 C. `with_prepare=False` 면 그래프 C 가 최소 조서로 간다."""
    settings = settings or Settings(_env_file=None, OPENAI_API_KEY="", XAI_API_KEY="")
    recording = RecordingLLM(llm)
    resolved_response = (
        resolved_evidence_for(snapshot) if resolved == "default" else resolved  # type: ignore[arg-type]
    )
    backend = MemoryBackend(snapshot, resolved_response, remaining_s=remaining_s)
    preparation = MemoryPreparation(approved_examples=approved_examples)
    recall = MemoryRecall(recall_candidates)
    trace = PipelineTrace(
        snapshot=snapshot,
        settings=settings,
        llm=recording,
        backend=backend,
        preparation=preparation,
        recall=recall,
    )
    prompt_version = prompt_bundle_version()

    # A. 심문관 — 등록 직전. 결과는 백엔드가 스냅샷 `intake_result` 에 넣어 준다고 가정한다.
    if with_intake:
        recording.stage = "A.intake"
        trace.intake = await run_intake(
            intake_request_for(snapshot), llm=recording, settings=settings
        )
        snapshot = snapshot.model_copy(update={"intake_result": trace.intake})
        backend._snapshot = snapshot
        trace.snapshot = snapshot

    # B. 조서·드립 — 마지막 표 전에 미리.
    if with_prepare:
        recording.stage = "B.prepare"
        deps = PrepareDeps(
            backend=backend,
            preparation=preparation,
            settings=settings,
            semaphore=asyncio.Semaphore(8),
            generation_id=GENERATION_ID,
            memory=recall,
            llm=recording,
            prompt_version=prompt_version,
        )
        trace.prepare_state = await run_preparation(make_prepare_job(snapshot), deps)

    # C. 양형관·서기·검수관·finalize — 마지막 표 뒤.
    recording.stage = "C.sentence"
    captured: dict[str, Any] = {}

    def factory(deps: Any) -> Any:
        graph = build_sentence_graph(deps)

        class Capturing:
            async def ainvoke(self, state: Any) -> Any:
                captured["state"] = await graph.ainvoke(state)
                return captured["state"]

        return Capturing()

    async def db_now() -> datetime:
        return DB_NOW

    ctx = SimpleNamespace(
        jobs=trace.jobs,
        backend=backend,
        llm=recording,
        preparation=preparation,
        semaphore=asyncio.Semaphore(8),
        settings=settings,
        generation_id=GENERATION_ID,
        worker_id=WORKER_ID,
        notifier=None,
    )
    handler = SentenceHandler(factory, db_now=db_now, clock=lambda: 1000.0)
    await handler(make_sentence_job(snapshot), ctx)
    trace.sentence_state = captured.get("state", {})
    return trace


def run_pipeline(llm: LLMPort, snapshot: CaseSnapshot, **kwargs: Any) -> PipelineTrace:
    return asyncio.run(run_pipeline_async(llm, snapshot, **kwargs))


# ---------------------------------------------------------------------------
# 사람이 읽는 추적
# ---------------------------------------------------------------------------


def _pretty(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _facts_table(dossier: Dossier | None) -> list[str]:
    if dossier is None:
        return ["(조서 없음)"]
    lines = ["| 라벨 | 종류 | 출처 | 문장 |", "|---|---|---|---|"]
    for fact in dossier.facts:
        lines.append(f"| {fact.label} | {fact.fact_type} | {fact.epistemic_type} | {fact.text} |")
    return lines


def render_trace(trace: PipelineTrace, *, full_prompts: bool = False) -> str:
    """단계·호출 순서대로 마크다운. `full_prompts` 면 시스템 프롬프트 전문을 싣는다."""
    snapshot = trace.snapshot
    jury = snapshot.jury
    out: list[str] = []
    out.append("# 에이전트 핸드오프 추적")
    out.append("")
    out.append(
        f"- 사건: {snapshot.item} {snapshot.amount_krw:,}원 / {snapshot.category} / "
        f"사유 {snapshot.reason!r} / {snapshot.post_type}"
    )
    if jury is not None:
        out.append(
            f"- 배심: {jury.result} {dict(jury.vote_counts)} 유죄율 {jury.guilty_ratio} / "
            f"강도 {[i.value for i in jury.target_intensities]}"
        )
    out.append(f"- 결과: **{trace.outcome}**")
    out.append(f"- 프롬프트 번들: {prompt_bundle_version()}")
    out.append("")

    if trace.intake is not None:
        out.append("## A. 심문관")
        out.append("")
        out.append("```json")
        out.append(_pretty(trace.intake.model_dump(mode="json")))
        out.append("```")
        out.append(
            "- 이 결과는 조서 키(캐시 무효화)에만 쓰이고 **어느 프롬프트에도 들어가지 않는다**."
        )
        out.append("")

    if trace.prepare_state is not None:
        state = trace.prepare_state
        out.append("## B. 조서·드립")
        out.append("")
        out.append(f"- 상태: {state.get('status')} / 오류: {state.get('errors')}")
        out.append(f"- 기억 후보(recall): {len(state.get('candidates') or [])}건")
        out.append("")
        out.append("### 조서(최종, 그래프 C 가 받는 것)")
        out.extend(_facts_table(trace.dossier))
        out.append("")
        analysis = state.get("reason_analysis")
        out.append("### 조서 LLM 의 사유 분석(reason_analysis)")
        out.append("```json")
        out.append(_pretty(analysis))
        out.append("```")
        out.append("- **저장되지 않고 양형관·서기에게도 가지 않는다**(PrepareState 에만 남는다).")
        out.append("")
        out.append("### 드립 후보(필터 뒤, 그래프 C 가 받는 것)")
        for intensity, items in (trace.preparation.banter or {}).items():
            out.append(f"- {intensity.value}: {len(items)}개")
            for c in items:
                out.append(
                    f"  - [{c.strategy}] fits={list(c.fits)} labels={list(c.evidence_labels)} "
                    f"— {c.text}"
                )
        out.append("")

    out.append("## C. 선고")
    out.append("")
    st = trace.sentence_state
    out.append(
        f"- 조서 출처: {st.get('dossier_source')} / 양형 출처: {st.get('sentencing_source')} / "
        f"재작성: {st.get('repair_count')} / 실패: {st.get('failure')}"
    )
    decision = st.get("sentencing")
    if decision is not None:
        out.append(
            f"- 양형: {decision.sentence} — {decision.sentencing_reason} "
            f"(근거 {list(decision.evidence_labels)}, 출처 {decision.reason_source})"
        )
    for intensity, draft in (st.get("drafts") or {}).items():
        source = (st.get("draft_sources") or {}).get(intensity)
        body = " / ".join(s.text for s in draft.statement)
        out.append(
            f"- {intensity.value} [{source}] {draft.headline} — {body} "
            f"(각도 {draft.attack_angle}, 전략 {draft.banter_strategy}, "
            f"후보 {draft.selected_candidate_id})"
        )
    validation = st.get("validation") or {}
    if any(validation.values()):
        out.append(f"- 서버 검증 ⑤: { {k.value: v for k, v in validation.items()} }")
    evaluation = st.get("evaluation")
    if evaluation is not None:
        for entry in evaluation.texts:
            codes = [v.code for v in entry.violations]
            out.append(f"- 검수 {entry.intensity}: pass={entry.pass_} {codes}")
    out.append("")

    out.append("## 모델 호출 순서")
    out.append("")
    for n, call in enumerate(trace.llm.calls, 1):
        head = call.system.strip().splitlines()[0] if call.system else ""
        out.append(f"### {n}. [{call.stage}] {call.role}")
        out.append("")
        out.append(
            f"- 시스템 프롬프트 첫 줄: {head} ({len(call.system)}자)"
            f" / timeout {call.timeout_s}s / 출력 상한 {call.max_output_tokens}"
        )
        if full_prompts:
            out.append("")
            out.append("<details><summary>시스템 프롬프트 전문</summary>")
            out.append("")
            out.append("```")
            out.append(call.system)
            out.append("```")
            out.append("</details>")
        out.append("")
        out.append("입력(사용자 메시지):")
        out.append("```json")
        out.append(_pretty(call.user))
        out.append("```")
        out.append("출력:" if call.error is None else f"오류: {call.error}")
        if call.output is not None:
            out.append("```json")
            out.append(_pretty(call.output))
            out.append("```")
        out.append("")

    req = trace.finalize_request
    if req is not None:
        out.append("## finalize (백엔드에 넘긴 것)")
        out.append("")
        out.append(
            f"- dossier_id {req.dossier_id} / draft_hash {req.draft_hash[:12]}… / "
            f"정책 {req.guardrail_policy_version} / 번들 {req.prompt_bundle_version}"
        )
        out.append(f"- model_ids {req.model_ids.model_dump(mode='json')}")
        for text in req.draft.texts:
            out.append(
                f"- {text.intensity} [{text.source}] {text.headline} — "
                f"{' / '.join(s.text for s in text.statement)}"
            )
    return "\n".join(out) + "\n"
