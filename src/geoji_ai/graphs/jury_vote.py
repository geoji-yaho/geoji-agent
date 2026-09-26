"""그래프 D — 데모 AI 배심원(떼거지봇) 한 표(18 §3.4).

`snapshot → room → juror → validate → cast`

- snapshot: 기존 snapshot API 재사용(18 §1 결정 9). 404 `SnapshotNotFound` 는 핸들러가 `cancel`
- room: `room_snapshots` 에서 `payload.room_id` 를 찾아 방 강도를 집는다. 없으면(공유 철회)
  `SKIPPED` 로 끝난다 — 모델 호출 0, cast 호출 0
- juror: `juror` 역할로 **1회**. 예산 키는 사건과 **따로 둔 배심원 예산**
  (`budget_key_for_jury(post_id)`), `node="juror"`, `call_index=0`.
  `LLMError`·timeout·`stop_reason != "stop"`·출력 없음 → 템플릿
- validate: 유형별 허용 평결 2개 ∧ 사유 1~60 code point ∧ 줄바꿈 없음. 위반 → 템플릿.
  명시적 원화 금액도 입력의 금액·항목·사유와 대조한다. 불일치 → 템플릿, 캐시 저장 없음.
  **재작성 호출은 없다. 모델 호출은 총 1회다**(18 §3.4)
- cast: `backend.cast_jury_vote`. 백엔드 거부 처리는 핸들러(`application/jury_vote_case.py`)

템플릿 폴백(18 §3.4): 평결은 `spent → guilty`, `considering → disagree`, `source="TEMPLATE"`,
사유는 `contracts/fixtures/juror-templates-v1.json` 의 `reasons.{intensity}.{post_type}` 이다.

정한 것(계획서가 말로만 적은 자리):

- "사유 공백 제거 뒤 1~60 code point" 는 **앞뒤 공백을 턴 문자열의 길이**로 읽는다. 백엔드로
  보내는 값도 턴 문자열이다. 줄바꿈이 하나라도 있으면 길이와 무관하게 템플릿이다
- state TypedDict 는 이 모듈에 둔다(18 §3.4 가 `graphs/states.py` 를 지정하지 않았다)

사유 원문은 로그에 남기지 않는다(다른 노드와 같은 규칙). 로그에는 길이만 싣는다.

이 모듈은 SQLAlchemy·어댑터를 import 하지 않는다.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from geoji_ai.application import instrument
from geoji_ai.application.llm_gateway import CallScope, ScopedLLM, case_scope
from geoji_ai.contracts.case import CaseSnapshot
from geoji_ai.contracts.jobs import Job, JuryVotePayload, parse_payload
from geoji_ai.contracts.juror import JurorVote
from geoji_ai.contracts.llm_schemas import VERDICTS_BY_POST_TYPE, juror_schema
from geoji_ai.core.config import Settings
from geoji_ai.core.logging import get_logger
from geoji_ai.domain.budget import budget_key_for_jury
from geoji_ai.domain.intensity import Intensity, parse_intensity
from geoji_ai.domain.juror_amounts import has_grounded_amounts
from geoji_ai.ports.backend import BackendPort, JuryVoteRequest
from geoji_ai.ports.llm import LLMError, LLMPort
from geoji_ai.ports.preparation import EvidenceInvalidated
from geoji_ai.prompts import build_juror_system, prompt_bundle_version

__all__ = [
    "JUROR_TEMPLATES_PATH",
    "TEMPLATE_VERDICT_BY_POST_TYPE",
    "JuryVoteDeps",
    "JuryVoteState",
    "build_jury_vote_graph",
    "load_juror_templates",
    "run_jury_vote",
    "template_vote",
    "validate_juror_output",
]

log = get_logger(__name__)

#: 저장소에서 `uv run` 할 때의 위치. 설치본에는 `contracts/` 가 없어 `path=` 로 넘긴다.
JUROR_TEMPLATES_PATH = (
    Path(__file__).resolve().parents[3] / "contracts" / "fixtures" / "juror-templates-v1.json"
)

#: 템플릿 평결(18 §3.4 표).
TEMPLATE_VERDICT_BY_POST_TYPE: dict[str, str] = {"spent": "guilty", "considering": "disagree"}

#: 모델 호출 상한에서 빼는 여유(cast 까지 가야 한다).
_TIMEOUT_RESERVE_S = 0.2

_SKIP_ROOM_NOT_SHARED = "room_not_shared"


class JuryVoteState(TypedDict):
    """그래프 D 의 state. 18 §3.4 가 `graphs/states.py` 를 지정하지 않아 이 모듈에 둔다."""

    job: Job
    payload: JuryVotePayload | None
    snapshot: CaseSnapshot | None
    intensity: Intensity | None
    #: juror 노드가 받은 모델 출력 그대로. 검증 전이라 계약을 지킨다는 보장이 없다.
    raw_output: dict[str, Any] | None
    #: 게이트웨이 경로의 `ScopedResult`. 검증을 지난 뒤 `remember` 에 돌려준다(원장·재사용 캐시).
    scoped: Any | None
    verdict: str | None
    reason: str | None
    source: Literal["AI", "TEMPLATE"] | None
    #: `CAST` · `SKIPPED`.
    status: str
    #: `AI` · `TEMPLATE` · `SKIPPED`.
    outcome: str
    #: 템플릿으로 떨어진 까닭(`llm_error:TIMEOUT`·`invalid_reason` 등). AI 면 None.
    fallback_reason: str | None
    #: skip 한 까닭(`room_not_shared`). skip 이 아니면 None.
    skip_reason: str | None
    vote_id: str | None


@dataclass
class JuryVoteDeps:
    """그래프 D 가 쓰는 포트와 설정. `llm` 이 None 이면(키 없음) 템플릿으로 간다."""

    backend: BackendPort
    settings: Settings
    semaphore: asyncio.Semaphore
    generation_id: str
    llm: LLMPort | ScopedLLM | None = None
    prompt_version: str = field(default_factory=prompt_bundle_version)


# --- 템플릿 -------------------------------------------------------------------------


@lru_cache(maxsize=4)
def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_juror_templates(path: Path | None = None) -> dict[str, Any]:
    """`juror-templates-v1.json` 을 읽는다(합성하지 않는다)."""
    return _load(path or JUROR_TEMPLATES_PATH)


def template_vote(
    intensity: Intensity | str, post_type: str, *, path: Path | None = None
) -> tuple[str, str]:
    """강도×유형 템플릿 표 (평결, 사유). 18 §3.4 표 6칸 그대로다."""
    key = parse_intensity(intensity)
    try:
        verdict = TEMPLATE_VERDICT_BY_POST_TYPE[post_type]
    except KeyError:
        raise ValueError(f"알 수 없는 게시물 유형: {post_type!r}") from None
    reasons = load_juror_templates(path)["reasons"]
    return verdict, reasons[key.value][post_type]


# --- 순수 함수 ----------------------------------------------------------------------


def validate_juror_output(output: Any, post_type: str) -> tuple[str, str] | None:
    """모델 출력이 계약을 지키면 (평결, 턴 사유). 아니면 None(→ 템플릿).

    ① 키는 `verdict`·`reason` 둘뿐(모르는 키가 섞인 출력은 받지 않는다) ② 유형별 허용 평결 2개 안
    ③ 사유에 줄바꿈 없음 ④ 앞뒤 공백을 턴 뒤 1~60 code point — 길이는 `JurorVote` 계약이 검사한다.
    """
    if not isinstance(output, dict) or set(output) != {"verdict", "reason"}:
        return None
    verdict = output["verdict"]
    reason = output["reason"]
    if not isinstance(verdict, str) or not isinstance(reason, str):
        return None
    if verdict not in VERDICTS_BY_POST_TYPE.get(post_type, ()):
        return None
    if "\n" in reason or "\r" in reason:
        return None
    try:
        vote = JurorVote.model_validate({"verdict": verdict, "reason": reason.strip()})
    except Exception:
        return None
    return vote.verdict, vote.reason


def _juror_messages(snapshot: CaseSnapshot, intensity: Intensity) -> list[dict]:
    """시스템(프롬프트 + 강도 섹션) + 사용자(이 글 하나)."""
    payload = {
        "post_type": str(snapshot.post_type),
        "amount_krw": snapshot.amount_krw,
        "item": snapshot.item,
        "category": str(snapshot.category),
        "reason": snapshot.reason,
        "intensity": intensity.value,
        "allowed_verdicts": list(VERDICTS_BY_POST_TYPE[str(snapshot.post_type)]),
    }
    return [
        {"role": "system", "content": build_juror_system(intensity)},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


async def call_juror(
    llm: LLMPort | ScopedLLM,
    semaphore: asyncio.Semaphore,
    settings: Settings,
    snapshot: CaseSnapshot,
    intensity: Intensity,
    *,
    scope: CallScope | None = None,
) -> tuple[dict[str, Any] | None, Any]:
    """배심원 1회 호출. (출력 dict 또는 None(거절·잘림), 게이트웨이 `ScopedResult` 또는 None).

    오류는 호출자에게 올린다. 게이트웨이 경로의 timeout·재시도 대기는 `scope.remaining_s` 가
    묶는다(호출자가 `JUROR_TIMEOUT_SECONDS` 기준으로 넣는다).
    """
    timeout = max(float(settings.JUROR_TIMEOUT_SECONDS) - _TIMEOUT_RESERVE_S, 0.1)
    messages = _juror_messages(snapshot, intensity)
    schema = juror_schema(str(snapshot.post_type))
    scoped = None
    async with semaphore:
        if isinstance(llm, ScopedLLM):
            if scope is None:
                raise ValueError("게이트웨이 호출에는 CallScope 가 필요하다")
            scoped = await llm.scoped_call(
                scope,
                role="juror",
                messages=messages,
                schema=schema,
                timeout_s=timeout,
                max_output_tokens=settings.JUROR_MAX_OUTPUT_TOKENS,
            )
            result = scoped.result
        else:
            result = await asyncio.wait_for(
                llm.structured_call(
                    role="juror",
                    messages=messages,
                    schema=schema,
                    timeout_s=timeout,
                    max_output_tokens=settings.JUROR_MAX_OUTPUT_TOKENS,
                ),
                timeout=timeout,
            )
    if result.stop_reason != "stop" or not isinstance(result.output, dict):
        return None, scoped
    return dict(result.output), scoped


# --- 그래프 -----------------------------------------------------------------------


def build_jury_vote_graph(deps: JuryVoteDeps) -> Any:
    """노드 5개를 묶은 컴파일된 그래프."""
    settings = deps.settings

    async def snapshot_node(state: JuryVoteState) -> dict[str, Any]:
        job = state["job"]
        payload = parse_payload(job)
        assert isinstance(payload, JuryVotePayload)
        snapshot = await deps.backend.snapshot(job.id, deps.generation_id)
        return {"payload": payload, "snapshot": snapshot}

    async def room_node(state: JuryVoteState) -> dict[str, Any]:
        payload = state["payload"]
        snapshot = state["snapshot"]
        assert payload is not None and snapshot is not None
        room = next(
            (r for r in snapshot.room_snapshots if r.room_id == payload.room_id),
            None,
        )
        if room is None:
            log.info("jury_vote_skipped", reason=_SKIP_ROOM_NOT_SHARED, job_id=state["job"].id)
            return {
                "status": "SKIPPED",
                "outcome": "SKIPPED",
                "skip_reason": _SKIP_ROOM_NOT_SHARED,
            }
        return {"intensity": parse_intensity(room.intensity)}

    def after_room(state: JuryVoteState) -> str:
        return END if state["status"] == "SKIPPED" else "juror"

    async def juror_node(state: JuryVoteState) -> dict[str, Any]:
        snapshot = state["snapshot"]
        intensity = state["intensity"]
        assert snapshot is not None and intensity is not None
        if deps.llm is None:
            log.info("jury_vote_call", intensity=intensity.value, error="no_llm")
            return {"fallback_reason": "no_llm"}
        # 게이트웨이의 timeout·재시도 대기(Retry-After)를 이 노드의 상한 안에 묶는다.
        # 없으면 remaining 이 무한대라 429 한 번에 수십 초를 잘 수 있다(리뷰 9/20).
        started = time.monotonic()
        budget_s = float(settings.JUROR_TIMEOUT_SECONDS)

        def remaining_s() -> float:
            return max(budget_s - (time.monotonic() - started), 0.0)

        scope = case_scope(
            snapshot,
            node="juror",
            call_index=0,
            job_id=state["job"].id,
            generation_id=deps.generation_id,
            prompt_version=deps.prompt_version,
            policy_version=settings.GUARDRAIL_POLICY_VERSION,
            remaining_s=remaining_s,
            reserve_s=_TIMEOUT_RESERVE_S,
            # 사건 예산과 분리한다. 붙여 두면 배심원 한 표가 같은 사건 검수관 몫을 먹는다.
            budget_key=budget_key_for_jury(snapshot.post_id),
        )
        timeout = max(budget_s - _TIMEOUT_RESERVE_S, 0.1)
        try:
            output, scoped = await call_juror(
                deps.llm, deps.semaphore, settings, snapshot, intensity, scope=scope
            )
        except EvidenceInvalidated:
            # D-26: 모델 호출 직전 epoch 가 달라졌다(삭제·공유 철회). 모델을 부르지 않고
            # 템플릿 표로 간다 — 백엔드가 VOTING_CLOSED·404 로 정리한다.
            log.info("jury_vote_call", intensity=intensity.value, error="EVIDENCE_INVALIDATED")
            return {"fallback_reason": "evidence_invalidated"}
        except LLMError as exc:
            log.warning(
                "jury_vote_call",
                intensity=intensity.value,
                timeout_s=timeout,
                error=exc.kind,
            )
            return {"fallback_reason": f"llm_error:{exc.kind}"}
        except TimeoutError:
            log.warning(
                "jury_vote_call", intensity=intensity.value, timeout_s=timeout, error="TIMEOUT"
            )
            return {"fallback_reason": "llm_error:TIMEOUT"}
        log.info("jury_vote_call", intensity=intensity.value, timeout_s=timeout, error=None)
        if output is None:
            return {"fallback_reason": "no_output", "scoped": scoped}
        return {"raw_output": output, "scoped": scoped}

    async def validate_node(state: JuryVoteState) -> dict[str, Any]:
        snapshot = state["snapshot"]
        intensity = state["intensity"]
        assert snapshot is not None and intensity is not None
        post_type = str(snapshot.post_type)
        fallback_reason = state.get("fallback_reason")
        checked = None
        if fallback_reason is None:
            checked = validate_juror_output(state.get("raw_output"), post_type)
            if checked is None:
                fallback_reason = "invalid_output"
            elif not has_grounded_amounts(
                checked[1],
                amount_krw=snapshot.amount_krw,
                item=snapshot.item,
                post_reason=snapshot.reason,
            ):
                checked = None
                fallback_reason = "ungrounded_amount"
        if checked is None:
            verdict, reason = template_vote(intensity, post_type)
            source: Literal["AI", "TEMPLATE"] = "TEMPLATE"
            outcome = "TEMPLATE"
        else:
            verdict, reason = checked
            source = "AI"
            outcome = "AI"
            fallback_reason = None
            # 검증을 지난 출력만 원장·재사용 캐시에 남긴다(서기·검수와 같은 규칙).
            scoped = state.get("scoped")
            if scoped is not None and isinstance(deps.llm, ScopedLLM):
                await deps.llm.remember(scoped)
        return {
            "verdict": verdict,
            "reason": reason,
            "source": source,
            "outcome": outcome,
            "fallback_reason": fallback_reason,
        }

    async def cast_node(state: JuryVoteState) -> dict[str, Any]:
        payload = state["payload"]
        assert payload is not None
        verdict = state["verdict"]
        reason = state["reason"]
        source = state["source"]
        assert verdict is not None and reason is not None and source is not None
        result = await deps.backend.cast_jury_vote(
            payload.post_id,
            JuryVoteRequest(
                job_id=state["job"].id,
                generation_id=deps.generation_id,
                room_id=payload.room_id,
                voter_id=payload.voter_id,
                verdict=verdict,
                reason=reason,
                source=source,
            ),
        )
        return {"status": "CAST", "vote_id": result.vote_id}

    graph = StateGraph(JuryVoteState)
    graph.add_node("snapshot", snapshot_node)
    graph.add_node("room", room_node)
    graph.add_node("juror", juror_node)
    graph.add_node("validate", validate_node)
    graph.add_node("cast", cast_node)
    graph.add_edge(START, "snapshot")
    graph.add_edge("snapshot", "room")
    graph.add_conditional_edges("room", after_room, ["juror", END])
    graph.add_edge("juror", "validate")
    graph.add_edge("validate", "cast")
    graph.add_edge("cast", END)
    return graph.compile()


def _initial_state(job: Job) -> JuryVoteState:
    return {
        "job": job,
        "payload": None,
        "snapshot": None,
        "intensity": None,
        "raw_output": None,
        "scoped": None,
        "verdict": None,
        "reason": None,
        "source": None,
        "status": "",
        "outcome": "",
        "fallback_reason": None,
        "skip_reason": None,
        "vote_id": None,
    }


async def run_jury_vote(job: Job, deps: JuryVoteDeps) -> JuryVoteState:
    """그래프 D 를 한 번 돌린다. 백엔드 예외는 호출자(`JuryVoteHandler`)가 정리한다."""
    graph = build_jury_vote_graph(deps)
    state: JuryVoteState = await graph.ainvoke(_initial_state(job))
    outcome = state.get("outcome") or "SKIPPED"
    instrument.count("jury_vote_total", outcome=outcome)
    log.info(
        "jury_vote_summary",
        outcome=outcome,
        verdict=state.get("verdict"),
        reason_length=len(state.get("reason") or ""),
        fallback_reason=state.get("fallback_reason"),
    )
    return state
