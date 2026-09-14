"""그래프 C(선고) — 05 §1 flowchart·§3.3·§3.5.

`begin_generation → load_valid_prep → (inline_context | minimal_dossier) → sentencing(조건) →
writer(강도 fan-out) → join → deterministic_validate → evaluator ⇄ writer_repair → finalize |
generation_failed`.

원장·재시도·degraded·`node_results` 재사용은 `deps.llm` 이 게이트웨이(`ScopedLLM`)일 때
`application/llm_gateway.py` 가 한다. 아니면(테스트 가짜 `LLMPort`) 원장 없이 부른다.

해석(보고서 "계획서에 반영할 것"):

- MINIMAL 은 중립 집계 + `pack_limit=MINIMAL_PACK_LIMIT(1)` 로 `F0`(THIS_CASE) 만 남긴다.
  `ResolveEvidenceResponse` 는 `aggregates` 가 필수라 "빈 응답" 을 만들 수 없어서다
- MINIMAL·INLINE dossier 도 `preparation.save_dossier` 로 저장한다(finalize `dossier_id` 가
  `ai.dossiers` 에 있게). `trial_prep` 행은 만들지 않는다. 저장 직전 epoch 불일치면
  `generation_failed(EVIDENCE_INVALIDATED)`
- INLINE 조서 timeout = `min(WRITER 상한, 남은 − reserve_after(writer))`.
  05 §3.1 에 조서 예산이 없다
- repair 조건 "남은 ≥ 5s" 는 05 §3.3 값(`REPAIR_MIN_REMAINING_S`), 횟수는 `IMMEDIATE_REPAIR_MAX`.
  `REGENERATE`(TEXT_RETRY) 는 repair 하지 않는다(05 §1 "round 안 보정 없음")
- repair 대상은 검수관이 `pass=false` 로 판정한 AI 강도다. 보고서에서 빠졌거나 형식이 틀린 강도는
  피할 위반이 없어 바로 TEMPLATE
- repair 뒤 검수는 바뀐 강도만 보낸다. 바뀌지 않은 강도는 직전 통과 항목을 이어 붙인다. hash 는
  언제나 전체 draft 기준이다
- 검수 뒤 TEMPLATE·D-19 치환으로 draft 가 바뀌면 전체를 한 번 더 검수한다(코디네이터 9/14,
  hash 규칙 우선). 그것도 통과하지 못하면 `EVAL_FAILED`
- finalize 422 repair 는 AI 강도 전부를 다시 쓴다(백엔드 응답에 강도가 없다). 뒤 검수는 전 강도
- join: 강도 일부 누락 ∧ AI 강도 있음 → `SCHEMA_INVALID`(서버 검증 5항). AI 강도 없음 →
  서기 오류에 `BUDGET` 이 있으면 `BUDGET_EXCEEDED`, 시간 예산으로 시작 못 한 강도가 있으면
  `DEADLINE_EXCEEDED`, 나머지(xAI `DEGRADED` 포함) `VENDOR_UNAVAILABLE`
- `hell` 별도 검수는 `role="evaluator"` 호출을 둘로 나눈다. 게이트웨이 경로에서 hell 호출은
  `model_override=MODEL_EVALUATOR_HELL` 로 라우터가 모델을 고른다.
  두 보고서의 검사 필드는 AND 로 합친다
- 검수 호출 오류: `BUDGET` → `BUDGET_EXCEEDED`, `DEGRADED`·`AUTH` → `VENDOR_UNAVAILABLE`, 그 밖 →
  `EVAL_FAILED`. 셋 다 전 강도 TEMPLATE
- 원장 `call_index`(코디네이터 9/14 결정 3): 서기 `repair_count×3 + target 안 강도 순서`, 검수
  `검수 라운드×2 + (hell 별도면 1)`, 양형·조서 0. 검수 라운드는 state `eval_round` 로 센다
- 게이트웨이 경로의 timeout·재시도는 게이트웨이가 시도마다 건다(그래프 `wait_for` 가 재시도를 자르지
  않게). 세마포어는 재시도 대기 동안에도 쥐고 있다
- `node_results` 는 검증을 통과한 출력만: 양형은 `parse_sentencing` 뒤, 서기는 `TextDraft`·강도·각도
  검사 뒤, 검수는 보고서에 전역 형식 실패가 없을 때(`remember`)
- `db_now` 는 핸들러가 준다(`application.sentence_case`: `job.updated_at` + 경과 monotonic)

`REGENERATE`(TEXT_RETRY, 08 §3.2):

- 형량·양형 이유는 begin 응답의 `fixed_sentencing` 그대로다. 양형 0호출, 검수 `reason_failed` 에도
  이유를 템플릿으로 바꾸지 않는다(바꾸면 형량 필드가 달라진다) → `EVAL_FAILED`
- `load_valid_prep` 만 읽는다. prep 이 없으면 남은 시간과 무관하게 MINIMAL(조서 호출 없음)
- 대상 강도 = payload `intensities`(None 이면 `target_intensities`). 서기·join·⑤·⑥·finalize `texts`
  모두 이 집합이다. `target_intensities` 밖 강도가 있으면 호출 없이 `SCHEMA_INVALID`(계획서에 없음)
- 예산 = `min(begin deadline, begin 노드 시작 + TEXT_RETRY_TIMEOUT_SECONDS)`. round 안 보정 없음
- 어느 강도든 서기 실패·예산 없음·⑤ 실패·검수 실패면 TEMPLATE 을 새로 finalize 하지 않고 저장 없이
  `generation_failed` 다. 서기는 join 코드 규칙(`BUDGET_EXCEEDED`·`DEADLINE_EXCEEDED`·
  `VENDOR_UNAVAILABLE`), ⑤·검수 거부·불완전은 `EVAL_FAILED`, 검수 호출 오류는 INITIAL 과 같은 표.
  다음 round 는 백엔드가 예약한다
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal, Protocol

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from geoji_ai.application.build_evidence import build_evidence
from geoji_ai.application.llm_gateway import (
    ScopedLLM,
    case_scope,
    evaluator_call_index,
    writer_call_index,
)
from geoji_ai.contracts.case import CaseSnapshot, JurySnapshot
from geoji_ai.contracts.evaluation import EvaluationReport
from geoji_ai.contracts.finalize import FinalizeRequest, ModelIds
from geoji_ai.contracts.jobs import Job, SentencePayload, TextRetryPayload, parse_payload
from geoji_ai.contracts.llm_schemas import evaluator_schema, sentencing_schema, writer_schema
from geoji_ai.contracts.sentencing import SentencingDecision
from geoji_ai.contracts.writer import MemeHints, TextDraft, WriterDraft
from geoji_ai.domain.attack_angles import ANGLE_GUIDES, pick
from geoji_ai.domain.budget import EVALUATOR, SENTENCING, WRITER, Deadline, reserve_after
from geoji_ai.domain.draft_hash import draft_hash
from geoji_ai.domain.intensity import Intensity
from geoji_ai.domain.validation import apply_text_rules, validate_evaluation
from geoji_ai.graphs.preparation import call_context, dossier_from_resolved, evidence_include
from geoji_ai.graphs.states import CallRecord, Candidate, SentenceState
from geoji_ai.graphs.templates import (
    TemplateUnavailable,
    sentencing_reason_template,
    template_text_draft,
)
from geoji_ai.ports.backend import (
    Aggregates,
    BackendPort,
    ResolveEvidenceRequest,
    ResolveEvidenceResponse,
)
from geoji_ai.ports.llm import LLMError, LLMPort, LLMResult, LLMRole
from geoji_ai.ports.preparation import Dossier, EvidenceInvalidated
from geoji_ai.prompts import build_writer_system, load_prompt, prompt_bundle_version

__all__ = [
    "MINIMAL_PACK_LIMIT",
    "REPAIR_MIN_REMAINING_S",
    "SENTENCING_REASON_MAX",
    "SentenceDeps",
    "SentenceGraphState",
    "ValidPrepLike",
    "build_sentence_graph",
    "initial_state",
    "minimal_dossier",
]

logger = logging.getLogger(__name__)

#: 호출 결과 확정. 출력 검증에 성공한 뒤 부른다(게이트웨이 경로면 `node_results` 저장).
Commit = Callable[[], Awaitable[None]]


async def _no_commit() -> None:
    """원장 없는 경로의 확정. 아무것도 하지 않는다."""


#: `SentencingDecision.sentencing_reason` 상한(01 §3.2, `contracts/sentencing.py`).
SENTENCING_REASON_MAX = 100
#: MINIMAL 조서의 근거 수. 백엔드 근거 없이 코드가 만들 수 있는 것은 F0 뿐이다.
MINIMAL_PACK_LIMIT = 1
#: `writer_repair` 를 시작할 최소 남은 시간(05 §3.3 "남은 ≥ 5s").
REPAIR_MIN_REMAINING_S = 5.0

GUILTY = "guilty"
SPENT = "spent"

# 생성 실패 코드(03 §1 = 10 §4.6)
VENDOR_UNAVAILABLE = "VENDOR_UNAVAILABLE"
EVAL_FAILED = "EVAL_FAILED"
SCHEMA_INVALID = "SCHEMA_INVALID"
DEADLINE_EXCEEDED = "DEADLINE_EXCEEDED"
EVIDENCE_INVALIDATED = "EVIDENCE_INVALIDATED"
BUDGET_EXCEEDED = "BUDGET_EXCEEDED"

#: 벤더를 쓸 수 없어 생긴 `LLMError.kind`(06 §3.1). 검수에서 `VENDOR_UNAVAILABLE` 로 옮긴다.
_VENDOR_BLOCKED_KINDS = frozenset({"DEGRADED", "AUTH"})

# 백엔드 거부 코드(10 §5)
_STALE_GENERATION = "STALE_GENERATION"
_DISCARD_ON_FINALIZE = frozenset({_STALE_GENERATION, DEADLINE_EXCEEDED})

_REASON_CHECK = "sentencing_reason_check"
_EVALUATION_CHECKS = ("sentence_check", _REASON_CHECK)
#: 검수 보고서 이슈 중 강도 하나로 좁혀지는 것(`domain/validation.py` 코드). 나머지는 전역 실패.
_PER_TEXT_CODES = frozenset(
    {"MISSING_INTENSITY", "MISSING_CHECK", "PASS_NOT_BOOLEAN", "CHECK_FAILED"}
)

# 노드 간 라우팅 값(state["route"])
_ROUTE_PREP = "prep"
_ROUTE_INLINE = "inline_context"
_ROUTE_MINIMAL = "minimal_dossier"
_ROUTE_REPAIR = "writer_repair"
_ROUTE_FINALIZE = "finalize"
_ROUTE_END = "end"


# ---------------------------------------------------------------------------
# state·의존성
# ---------------------------------------------------------------------------


class SentenceGraphState(SentenceState, total=False):
    """`SentenceState` + 이 그래프가 노드 사이에 넘기는 값."""

    #: 서버 검증 ⑤ 를 거친, 검수에 넘기고 finalize 에 싣는 초안.
    writer_draft: WriterDraft | None
    #: `default_intensity` 서기 호출의 `meme_hints`.
    meme_hints: MemeHints | None
    #: 예산이 없어 서기 호출을 시작하지 않은 강도가 있다.
    writer_budget_skipped: bool
    #: `writer_repair` 가 다시 쓸 강도.
    repair_targets: list[Intensity]
    #: 강도별 "피할 것"(위반 코드·문제 문장).
    repair_avoid: dict[Intensity, dict[str, list[str]]]
    #: repair 뒤 재사용할 직전 통과 항목. 강도 값 → (그때의 TextDraft JSON, 보고서 항목).
    eval_kept: dict[str, tuple[dict[str, Any], dict[str, Any]]]
    #: 조건 간선이 읽는 다음 노드.
    route: str
    #: 이 실행에서 끝난 검수 라운드 수. 검수 `call_index` 에 쓴다.
    eval_round: int


class ValidPrepLike(Protocol):
    """`ports.preparation.ValidPrep` 의 구조."""

    dossier: Dossier
    banter: Mapping[Intensity, list[Candidate]]


class PreparationLike(Protocol):
    async def load_valid_prep(
        self, snapshot: CaseSnapshot, prompt_version: str
    ) -> ValidPrepLike | None: ...

    async def save_dossier(self, dossier: Dossier) -> str: ...

    async def stale_scopes(self, privacy_versions: Iterable[tuple[str, int]]) -> list[str]: ...


@dataclass
class SentenceDeps:
    backend: BackendPort
    llm: LLMPort | ScopedLLM
    semaphore: asyncio.Semaphore
    settings: Any
    generation_id: str
    #: DB 가 준 현재 시각. `Deadline.from_db(deadline_at, db_now)` 에 쓴다.
    db_now: Callable[[], Awaitable[datetime]]
    #: 없으면 prep 조회·dossier 저장·finalize 직전 epoch 재확인을 하지 않는다.
    preparation: PreparationLike | None = None
    clock: Callable[[], float] = field(default=time.monotonic)
    prompt_version: str = field(default_factory=prompt_bundle_version)
    templates: Mapping[str, Any] | None = None


def initial_state(
    job: Job, mode: Literal["INITIAL", "REGENERATE"], snapshot: CaseSnapshot
) -> SentenceGraphState:
    return SentenceGraphState(
        job=job,
        mode=mode,
        snapshot=snapshot,
        dossier=None,
        banter={},
        sentencing=None,
        drafts={},
        draft_sources={},
        validation={},
        evaluation=None,
        repair_count=0,
        draft_hash=None,
        calls=[],
        failure=None,
        writer_draft=None,
        meme_hints=None,
        writer_budget_skipped=False,
        repair_targets=[],
        repair_avoid={},
        eval_kept={},
        route="",
        eval_round=0,
    )


# ---------------------------------------------------------------------------
# 순수 도우미
# ---------------------------------------------------------------------------


def minimal_dossier(snapshot: CaseSnapshot) -> Dossier:
    """코드 Evidence 만의 조서(F0). 저장은 그래프 노드가 한다."""
    created = snapshot.created_at
    neutral = ResolveEvidenceResponse(
        sources=[],
        aggregates=Aggregates(
            burn_rate=0.0,
            tier="",
            no_spend_days=0,
            repeat_same_category_30d=0,
            excludes_post_id=snapshot.post_id,
            window={"start_at": created - timedelta(days=30), "end_at": created},
            rule_version=0,
        ),
        room_rules=[],
        recent_verdicts=[],
        style_comments=[],
    )
    return build_evidence(snapshot, neutral, pack_limit=MINIMAL_PACK_LIMIT)


def _jury(state: Mapping[str, Any]) -> JurySnapshot:
    jury = state["snapshot"].jury
    if jury is None:
        raise ValueError("선고 스냅샷에 jury 가 없다")
    return jury


def _payload(state: Mapping[str, Any]) -> SentencePayload | TextRetryPayload:
    payload = parse_payload(state["job"])
    if not isinstance(payload, SentencePayload | TextRetryPayload):
        raise ValueError(f"선고 job 이 아니다: {state['job'].kind}")
    return payload


def _error_name(exc: BaseException) -> str:
    if isinstance(exc, LLMError):
        return exc.kind
    if isinstance(exc, TimeoutError):
        return "TIMEOUT"
    return type(exc).__name__


def _vendor_failure_code(records: Iterable[CallRecord], default: str) -> str:
    """검수 호출 기록의 오류 kind → 생성 실패 코드. 예산 > 벤더 불가 > `default`."""
    errors = {record.error for record in records}
    if "BUDGET" in errors:
        return BUDGET_EXCEEDED
    if errors & _VENDOR_BLOCKED_KINDS:
        return VENDOR_UNAVAILABLE
    return default


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _case_view(snapshot: CaseSnapshot) -> dict[str, Any]:
    return {
        "item": snapshot.item,
        "amount_krw": snapshot.amount_krw,
        "category": snapshot.category,
        "reason": snapshot.reason,
        "post_type": snapshot.post_type,
    }


def _facts_view(dossier: Dossier | None) -> list[dict[str, str]]:
    return [] if dossier is None else [{"id": f.label, "text": f.text} for f in dossier.facts]


def _sentencing_view(sentencing: SentencingDecision | None) -> dict[str, Any] | None:
    return None if sentencing is None else sentencing.model_dump(mode="json")


def _targets(jury: JurySnapshot) -> list[Intensity]:
    return list(jury.target_intensities)


def _regenerate(state: Mapping[str, Any]) -> bool:
    return state["mode"] == "REGENERATE"


def _requested(state: Mapping[str, Any]) -> list[Intensity] | None:
    """REGENERATE payload 의 `intensities`. INITIAL 이거나 없으면 None."""
    if not _regenerate(state):
        return None
    payload = _payload(state)
    if not isinstance(payload, TextRetryPayload) or payload.intensities is None:
        return None
    return list(payload.intensities)


def _scope(state: Mapping[str, Any]) -> list[Intensity]:
    """이 실행이 쓰는 강도. `target_intensities` 순서를 따르고 payload `intensities` 로 좁힌다."""
    targets = _targets(_jury(state))
    requested = _requested(state)
    if requested is None:
        return targets
    wanted = set(requested)
    return [i for i in targets if i in wanted]


def _text_index(path: str) -> int | None:
    if not path.startswith("texts["):
        return None
    end = path.find("]")
    digits = path[len("texts[") : end]
    return int(digits) if end > 0 and digits.isdigit() else None


def _key(value: Any) -> str | None:
    try:
        return Intensity(value).value
    except ValueError:
        return None


def _avoid(entry: Mapping[str, Any]) -> dict[str, list[str]]:
    """검수 항목 → 서기에게 줄 "피할 것"."""
    codes = [str(v.get("code")) for v in entry.get("violations") or [] if isinstance(v, Mapping)]
    sentences = [str(s) for s in entry.get("problem_sentences") or []]
    return {"violations": list(dict.fromkeys(codes)), "problem_sentences": sentences}


def _merge_reports(outputs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """검수 호출이 둘로 나뉘었을 때 보고서를 합친다. 검사 필드는 AND, 위반은 이어 붙인다."""
    if len(outputs) == 1:
        return dict(outputs[0])
    merged: dict[str, Any] = {
        "texts": [entry for output in outputs for entry in output.get("texts") or []]
    }
    for name in _EVALUATION_CHECKS:
        checks = [output.get(name) for output in outputs]
        broken = next((check for check in checks if not isinstance(check, Mapping)), None)
        if broken is not None or any(not isinstance(c.get("pass"), bool) for c in checks):
            merged[name] = broken if broken is not None else {"pass": None, "violations": []}
            continue
        merged[name] = {
            "pass": all(check["pass"] for check in checks),
            "violations": [v for check in checks for v in check.get("violations") or []],
        }
    return merged


# ---------------------------------------------------------------------------
# 그래프
# ---------------------------------------------------------------------------


def build_sentence_graph(deps: SentenceDeps) -> Any:
    """그래프 C 를 컴파일한다. 실행마다 새로 만들어도 된다(체크포인터 없음)."""
    settings = deps.settings

    gateway = deps.llm if isinstance(deps.llm, ScopedLLM) else None

    async def call_model(
        state: Mapping[str, Any],
        node: str,
        *,
        role: LLMRole,
        messages: list[dict],
        schema: dict,
        max_output_tokens: int,
        call_index: int = 0,
        model_override: str | None = None,
    ) -> tuple[LLMResult, Commit] | None:
        """세마포어 안에서 예산을 계산한다. 예산이 없으면 호출하지 않고 None.

        (결과, 확정)을 돌려준다. 확정은 호출자가 출력 검증에 성공한 뒤 부른다.
        """
        deadline: Deadline = state["deadline"]
        async with deps.semaphore:
            timeout = deadline.node_timeout(node, settings)
            if timeout is None:
                return None
            if gateway is not None:
                scope = case_scope(
                    state["snapshot"],
                    node=node,
                    call_index=call_index,
                    job_id=state["job"].id,
                    generation_id=deps.generation_id,
                    prompt_version=deps.prompt_version,
                    policy_version=settings.GUARDRAIL_POLICY_VERSION,
                    model_override=model_override,
                    remaining_s=deadline.remaining_s,
                    reserve_s=reserve_after(node, settings),
                )
                scoped = await gateway.scoped_call(
                    scope,
                    role=role,
                    messages=messages,
                    schema=schema,
                    timeout_s=timeout,
                    max_output_tokens=max_output_tokens,
                )

                async def commit() -> None:
                    await gateway.remember(scoped)

                return scoped.result, commit
            result = await asyncio.wait_for(
                deps.llm.structured_call(  # type: ignore[union-attr]
                    role=role,
                    messages=messages,
                    schema=schema,
                    timeout_s=timeout,
                    max_output_tokens=max_output_tokens,
                ),
                timeout,
            )
            return result, _no_commit

    def template_for(
        intensity: Intensity, jury: JurySnapshot, sentencing: SentencingDecision | None, post: str
    ) -> TextDraft | None:
        try:
            return template_text_draft(
                intensity,
                jury,
                sentencing.sentence if sentencing is not None else None,
                post,
                templates=deps.templates,
            )
        except TemplateUnavailable as exc:
            logger.warning("강도 %s 템플릿을 만들 수 없다: %s", intensity, exc)
            return None

    def assemble(
        state: Mapping[str, Any],
        drafts: dict[Intensity, TextDraft],
        sources: dict[Intensity, str],
        sentencing: SentencingDecision | None,
    ) -> dict[str, Any]:
        """서버 검증 ⑤. 걸린 AI 강도는 TEMPLATE 로 바꾸고 다시 검사한다."""
        jury = _jury(state)
        snapshot = state["snapshot"]
        dossier: Dossier = state["dossier"]
        targets = _scope(state)
        regenerate = _regenerate(state)
        # 규칙 5(강도 집합)는 이 실행의 대상 강도와 대조한다(REGENERATE 는 부분집합).
        rule_jury = jury.model_copy(update={"target_intensities": targets}) if regenerate else jury
        drafts = dict(drafts)
        sources = dict(sources)
        hints: MemeHints | None = state.get("meme_hints")
        for _ in range(2):
            if set(drafts) != set(targets):
                return {"failure": SCHEMA_INVALID}
            body = {
                "schema_version": 1,
                "texts": [drafts[i].model_dump(mode="json") for i in targets],
                # 규칙 4 가 결과·형량으로 교정한다. 넣는 값은 자리만 채운다.
                "meme_tag": "GUILTY_LIGHT",
                "meme_hints": hints.model_dump(mode="json") if hints is not None else None,
            }
            rules = apply_text_rules(body, dossier.label_map, sentencing, rule_jury)
            bad: dict[Intensity, list[str]] = {}
            for issue in rules.issues:
                index = _text_index(issue.path)
                if index is None or index >= len(targets):
                    logger.warning("서버 검증 전역 실패: %s %s", issue.code, issue.message)
                    return {"failure": SCHEMA_INVALID}
                bad.setdefault(targets[index], []).append(issue.code)
            if not bad:
                try:
                    writer_draft = WriterDraft.model_validate(rules.draft)
                except ValidationError as exc:
                    logger.warning("재조립 초안이 계약에 맞지 않는다: %s", exc.error_count())
                    return {"failure": SCHEMA_INVALID}
                rebuilt = {Intensity(t.intensity): t for t in writer_draft.texts}
                if all(sources[i] == "TEMPLATE" for i in targets):
                    return {"drafts": rebuilt, "draft_sources": sources, "failure": EVAL_FAILED}
                return {
                    "drafts": rebuilt,
                    "draft_sources": sources,
                    "writer_draft": writer_draft,
                    "failure": None,
                }
            if regenerate:
                # round 안 보정·TEMPLATE 치환 없음. 다음 round 는 백엔드 몫이다.
                logger.info("REGENERATE 서버 검증 실패 %s → 저장 없이 실패", sorted(bad))
                return {"failure": EVAL_FAILED}
            for intensity, codes in bad.items():
                logger.info("강도 %s 서버 검증 실패 %s → TEMPLATE", intensity, codes)
                if sources.get(intensity) == "TEMPLATE":
                    return {"failure": SCHEMA_INVALID}
                template = template_for(intensity, jury, sentencing, snapshot.post_id)
                if template is None:
                    return {"failure": EVAL_FAILED}
                drafts[intensity] = template
                sources[intensity] = "TEMPLATE"
        return {"failure": SCHEMA_INVALID}

    async def save_dossier(dossier: Dossier) -> str | None:
        """MINIMAL·INLINE dossier 저장. epoch 불일치면 실패 코드."""
        if deps.preparation is None:
            return None
        try:
            await deps.preparation.save_dossier(dossier)
        except EvidenceInvalidated as exc:
            logger.info("dossier 저장 직전 epoch 불일치 %s", exc.scope_keys)
            return EVIDENCE_INVALIDATED
        return None

    # --- 노드: 시작·조서 -----------------------------------------------------

    async def begin_generation(state: SentenceGraphState) -> dict[str, Any]:
        payload = _payload(state)
        started = deps.clock()
        try:
            begin = await deps.backend.begin_generation(
                payload.verdict_id,
                job_id=state["job"].id,
                generation_id=deps.generation_id,
                verdict_version=payload.verdict_version,
            )
        except Exception as exc:
            if getattr(exc, "status", None) == 409:
                logger.info("begin-generation 409 %s — 폐기", getattr(exc, "code", None))
                return {}
            raise
        deadline = Deadline.from_db(begin.deadline_at, await deps.db_now(), clock=deps.clock)
        if _regenerate(state):
            # 08 §3.2 예산 20초(`TEXT_RETRY_TIMEOUT_SECONDS`). begin 마감이 더 이르면 그것을 쓴다.
            retry_expires = started + float(settings.TEXT_RETRY_TIMEOUT_SECONDS)
            deadline = Deadline(
                expires_at_monotonic=min(deadline.expires_at_monotonic, retry_expires),
                clock=deps.clock,
            )
        update: dict[str, Any] = {"begin": begin, "deadline": deadline}
        requested = _requested(state)
        if requested is not None:
            outside = sorted(
                {i.value for i in requested} - {i.value for i in _targets(_jury(state))}
            )
            if outside:
                logger.warning("TEXT_RETRY intensities %s 가 target 밖 → SCHEMA_INVALID", outside)
                update["failure"] = SCHEMA_INVALID
        fixed = begin.fixed_sentencing
        if fixed is not None:
            update["sentencing"] = SentencingDecision(
                schema_version=1,
                sentence=fixed.sentence,
                sentencing_reason=fixed.sentencing_reason,
                reason_source=fixed.reason_source,
                evidence_labels=[],
                aggravating=[],
                mitigating=[],
            )
            update["sentencing_source"] = "FIXED"
        return update

    async def load_valid_prep(state: SentenceGraphState) -> dict[str, Any]:
        snapshot = state["snapshot"]
        prep = None
        if deps.preparation is not None:
            prep = await deps.preparation.load_valid_prep(snapshot, deps.prompt_version)
        if prep is not None:
            return {
                "dossier": prep.dossier,
                "dossier_source": "PREP",
                "banter": dict(prep.banter),
                "route": _ROUTE_PREP,
            }
        if _regenerate(state):
            # 삭제·무효 prep 을 대신할 inline 조서도 부르지 않는다(08 §3.2).
            return {"route": _ROUTE_MINIMAL}
        remaining_ms = state["deadline"].remaining_s() * 1000
        inline = remaining_ms >= settings.INLINE_CONTEXT_MIN_REMAINING_MS
        return {"route": _ROUTE_INLINE if inline else _ROUTE_MINIMAL}

    async def minimal_dossier_node(state: SentenceGraphState) -> dict[str, Any]:
        dossier = minimal_dossier(state["snapshot"])
        failure = await save_dossier(dossier)
        return {"dossier": dossier, "dossier_source": "MINIMAL", "banter": {}, "failure": failure}

    async def inline_context(state: SentenceGraphState) -> dict[str, Any]:
        """04 build + 조서 1회(드립 생략). 조서가 실패해도 코드 Evidence 로 계속한다."""
        snapshot = state["snapshot"]
        calls = list(state["calls"])
        request = ResolveEvidenceRequest(candidates=[], include=evidence_include(settings))
        try:
            resolved = await deps.backend.resolve_evidence(
                state["job"].id, deps.generation_id, request
            )
        except Exception as exc:  # 그래프 B 와 같다: 빈 응답(F0 만)으로 계속.
            logger.warning("inline resolve-evidence 실패 %s → F0 만", type(exc).__name__)
            resolved = None
        dossier = dossier_from_resolved(snapshot, resolved, settings)
        timeout = min(
            float(settings.WRITER_NODE_TIMEOUT_SECONDS),
            state["deadline"].remaining_s() - reserve_after(WRITER, settings),
        )
        if timeout > 0:
            try:
                scope = case_scope(
                    snapshot,
                    node="context",
                    call_index=0,
                    job_id=state["job"].id,
                    generation_id=deps.generation_id,
                    prompt_version=deps.prompt_version,
                    policy_version=settings.GUARDRAIL_POLICY_VERSION,
                    remaining_s=state["deadline"].remaining_s,
                    reserve_s=reserve_after(WRITER, settings),
                )
                dossier, _ = await call_context(
                    deps.llm,
                    deps.semaphore,
                    settings,
                    snapshot,
                    dossier,
                    timeout_s=timeout,
                    scope=scope,
                )
            except Exception as exc:  # 코드 Evidence 만으로 계속(05 §5.2).
                calls.append(CallRecord("context", None, settings.MODEL_JUDGMENT, _error_name(exc)))
                logger.warning("inline 조서 실패 %s → 코드 Evidence 만", _error_name(exc))
            else:
                calls.append(CallRecord("context", None, settings.MODEL_JUDGMENT, None))
        failure = await save_dossier(dossier)
        return {
            "dossier": dossier,
            "dossier_source": "INLINE",
            "banter": {},
            "calls": calls,
            "failure": failure,
        }

    # --- 노드: 양형 ----------------------------------------------------------

    def rule_sentencing(jury: JurySnapshot) -> dict[str, Any]:
        return {
            "sentencing": SentencingDecision(
                schema_version=1,
                sentence=str(jury.policy.fallback_sentence),
                sentencing_reason=None,
                reason_source="TEMPLATE",
                evidence_labels=[],
                aggravating=[],
                mitigating=[],
            ),
            "sentencing_source": "RULE",
        }

    def parse_sentencing(
        output: Mapping[str, Any] | None, jury: JurySnapshot
    ) -> SentencingDecision:
        if output is None:
            raise ValueError("양형관 출력이 없다")
        ranked = sorted(jury.policy.allowed_sentences, key=lambda item: item.rank)
        allowed = {str(item.code) for item in ranked}
        sentence = output.get("sentence")
        if sentence not in allowed:
            top = str(ranked[-1].code)
            logger.warning(
                "감사: 양형관 형량 %r 가 허용 목록 %s 밖이라 rank 상한 %s 로 절삭",
                sentence,
                sorted(allowed),
                top,
                extra={"audit": "SENTENCE_CLIPPED", "verdict_id": jury.verdict_id},
            )
            sentence = top
        reason = output.get("sentencing_reason")
        reason_source = "AI"
        if isinstance(reason, str) and len(reason) > SENTENCING_REASON_MAX:
            # D-19: 검수 실패로 보지 않고 즉시 템플릿 치환
            reason = sentencing_reason_template(jury, sentence, deps.templates)
            reason_source = "TEMPLATE"
        return SentencingDecision(
            schema_version=1,
            sentence=sentence,
            sentencing_reason=reason,
            reason_source=reason_source,
            evidence_labels=output.get("evidence_labels") or [],
            aggravating=output.get("aggravating") or [],
            mitigating=output.get("mitigating") or [],
        )

    async def sentencing(state: SentenceGraphState) -> dict[str, Any]:
        jury = _jury(state)
        calls = list(state["calls"])
        messages = [
            {"role": "system", "content": load_prompt("sentencing-v1.md")},
            {
                "role": "user",
                "content": _dumps(
                    {
                        "case": _case_view(state["snapshot"]),
                        "jury": {
                            "result": str(jury.result),
                            "vote_counts": jury.vote_counts,
                            "guilty_ratio": jury.guilty_ratio,
                            "policy": {
                                "allowed_sentences": [
                                    {"code": str(item.code), "rank": item.rank}
                                    for item in jury.policy.allowed_sentences
                                ]
                            },
                        },
                        "dossier": _facts_view(state["dossier"]),
                    }
                ),
            },
        ]
        schema = sentencing_schema([str(item.code) for item in jury.policy.allowed_sentences])
        try:
            called = await call_model(
                state,
                SENTENCING,
                role="sentencing",
                messages=messages,
                schema=schema,
                max_output_tokens=settings.SENTENCING_MAX_OUTPUT_TOKENS,
            )
            if called is None:
                logger.info("양형 예산 없음 → RULE")
                return {**rule_sentencing(jury), "calls": calls}
            result, commit = called
            decision = parse_sentencing(result.output, jury)
            await commit()
        except (LLMError, TimeoutError, ValueError) as exc:
            calls.append(CallRecord("sentencing", None, settings.MODEL_JUDGMENT, _error_name(exc)))
            logger.warning("양형관 실패 %s → RULE", _error_name(exc))
            return {**rule_sentencing(jury), "calls": calls}
        calls.append(CallRecord("sentencing", None, result.model_id, None))
        return {"sentencing": decision, "sentencing_source": "AI", "calls": calls}

    # --- 노드: 서기 ----------------------------------------------------------

    async def write_one(
        state: Mapping[str, Any],
        intensity: Intensity,
        offset: int,
        avoid: Mapping[str, list[str]] | None,
    ) -> tuple[str, Any, CallRecord | None]:
        jury = _jury(state)
        snapshot = state["snapshot"]
        decision: SentencingDecision | None = state.get("sentencing")
        angle = pick(snapshot.post_id, offset)
        guide = ANGLE_GUIDES[angle]
        banter: Mapping[Intensity, Sequence[Candidate]] = state.get("banter") or {}
        candidates = [c for c in banter.get(intensity, []) if str(jury.result) in c.fits]
        candidate_ids = [c.candidate_id for c in candidates]
        user: dict[str, Any] = {
            "intensity": intensity.value,
            "attack_angle": {
                "code": angle.value,
                "label": guide.label_ko,
                "instruction": guide.instruction,
            },
            "case": _case_view(snapshot),
            "jury": {
                "result": str(jury.result),
                "vote_counts": jury.vote_counts,
                "guilty_ratio": jury.guilty_ratio,
            },
            "sentencing": _sentencing_view(decision),
            "dossier": _facts_view(state["dossier"]),
            "banter_candidates": [
                {
                    "id": c.candidate_id,
                    "text": c.text,
                    "strategy": str(c.strategy),
                    "evidence_labels": list(c.evidence_labels),
                }
                for c in candidates
            ],
        }
        if avoid:
            # repair: 검수에서 걸린 위반·문제 문장. 데이터로만 준다.
            user["avoid"] = dict(avoid)
        messages = [
            {"role": "system", "content": build_writer_system(intensity)},
            {"role": "user", "content": _dumps(user)},
        ]
        schema = writer_schema([intensity.value], [angle.value], candidate_ids or None)
        try:
            called = await call_model(
                state,
                WRITER,
                role="writer",
                messages=messages,
                schema=schema,
                max_output_tokens=settings.WRITER_MAX_OUTPUT_TOKENS,
                call_index=writer_call_index(offset, _targets(jury).index(intensity)),
            )
            if called is None:
                return ("SKIPPED", None, None)
            result, commit = called
            if result.output is None:
                raise ValueError(f"서기 출력 없음({result.stop_reason})")
            output = dict(result.output)
            hints_raw = output.pop("meme_hints", None)
            output.pop("meme_tag", None)
            text = TextDraft.model_validate({**output, "source": "AI"})
            if text.intensity != intensity or text.attack_angle != angle:
                raise ValueError("서버 지정 강도·각도와 다르다")
            hints = MemeHints.model_validate(hints_raw) if hints_raw is not None else None
            await commit()
        except (LLMError, TimeoutError, ValueError) as exc:
            record = CallRecord("writer", intensity, settings.MODEL_WRITER, _error_name(exc))
            return ("FAILED", None, record)
        return ("AI", (text, hints), CallRecord("writer", intensity, result.model_id, None))

    async def fan_out(
        state: Mapping[str, Any],
        intensities: Sequence[Intensity],
        offset: int,
        avoid: Mapping[Intensity, Mapping[str, list[str]]],
    ) -> dict[str, Any]:
        """강도마다 서기 1호출. 실패·예산 없음 강도는 TEMPLATE(없으면 비워 둔다)."""
        jury = _jury(state)
        decision: SentencingDecision | None = state.get("sentencing")
        post_id = state["snapshot"].post_id
        outcomes = await asyncio.gather(
            *(write_one(state, i, offset, avoid.get(i)) for i in intensities)
        )
        calls = list(state["calls"])
        drafts: dict[Intensity, TextDraft] = dict(state.get("drafts") or {})
        sources: dict[Intensity, Literal["AI", "TEMPLATE"]] = dict(state.get("draft_sources") or {})
        hints: MemeHints | None = state.get("meme_hints")
        skipped = False
        for intensity, (kind, value, record) in zip(intensities, outcomes, strict=True):
            if record is not None:
                calls.append(record)
            if kind == "AI":
                text, text_hints = value
                drafts[intensity] = text
                sources[intensity] = "AI"
                if intensity == jury.default_intensity:
                    hints = text_hints
                continue
            skipped = skipped or kind == "SKIPPED"
            drafts.pop(intensity, None)
            sources.pop(intensity, None)
            if intensity == jury.default_intensity:
                hints = None
            if _regenerate(state):
                continue  # TEMPLATE 을 새로 만들지 않는다. join 이 실패로 보낸다.
            template = template_for(intensity, jury, decision, post_id)
            if template is not None:
                drafts[intensity] = template
                sources[intensity] = "TEMPLATE"
        return {
            "drafts": drafts,
            "draft_sources": sources,
            "meme_hints": hints,
            "writer_budget_skipped": skipped,
            "calls": calls,
        }

    async def writer(state: SentenceGraphState) -> dict[str, Any]:
        return await fan_out(state, _scope(state), state.get("repair_count", 0), {})

    async def join(state: SentenceGraphState) -> dict[str, Any]:
        targets = _scope(state)
        sources = state.get("draft_sources") or {}
        not_ai = [i for i in targets if sources.get(i) != "AI"]
        if len(not_ai) == len(targets) or (_regenerate(state) and not_ai):
            writer_errors = {c.error for c in state["calls"] if c.role == "writer"}
            if "BUDGET" in writer_errors:
                code = BUDGET_EXCEEDED
            elif state.get("writer_budget_skipped"):
                code = DEADLINE_EXCEEDED
            else:
                code = VENDOR_UNAVAILABLE
            return {"failure": code}
        missing = set(targets) - set(state.get("drafts") or {})
        if missing:
            # 서버 검증 5항(강도 집합 == target_intensities)을 join 에서 먼저 건다.
            logger.warning("강도 누락 %s → SCHEMA_INVALID", sorted(i.value for i in missing))
            return {"failure": SCHEMA_INVALID}
        return {"failure": None}

    async def deterministic_validate(state: SentenceGraphState) -> dict[str, Any]:
        result = assemble(state, state["drafts"], state["draft_sources"], state.get("sentencing"))
        if "drafts" in result:
            result["validation"] = {i: [] for i in result["drafts"]}
        return result

    # --- 노드: 검수·보정 -----------------------------------------------------

    async def evaluate(
        state: Mapping[str, Any],
        writer_draft: WriterDraft,
        decision: SentencingDecision | None,
        scope: Sequence[Intensity],
        eval_round: int,
    ) -> tuple[str, dict[str, Any] | None, list[CallRecord], list[Commit]]:
        """scope 강도만 검수한다. (`OK`|`SKIPPED`|`FAILED`, 합친 출력, 호출 기록, 확정 목록)."""
        jury = _jury(state)
        policy_version = settings.GUARDRAIL_POLICY_VERSION
        evidence = (
            {} if state["dossier"] is None else {f.label: f.text for f in state["dossier"].facts}
        )
        hell_apart = (
            Intensity.hell in scope and settings.MODEL_EVALUATOR_HELL != settings.MODEL_JUDGMENT
        )
        groups: list[list[Intensity]] = [list(scope)]
        if hell_apart and len(scope) > 1:
            groups = [[i for i in scope if i != Intensity.hell], [Intensity.hell]]

        async def one(
            group: list[Intensity],
        ) -> tuple[str, dict[str, Any] | None, CallRecord, Commit | None]:
            is_hell = hell_apart and group == [Intensity.hell]
            subset = writer_draft.model_copy(
                update={"texts": [t for t in writer_draft.texts if Intensity(t.intensity) in group]}
            )
            messages = [
                {"role": "system", "content": load_prompt(f"evaluator/{policy_version}.md")},
                {
                    "role": "user",
                    "content": _dumps(
                        {
                            "policy_version": policy_version,
                            "jury": {
                                "result": str(jury.result),
                                "vote_counts": jury.vote_counts,
                                "guilty_ratio": jury.guilty_ratio,
                                "policy": {
                                    "allowed_sentences": [
                                        {"code": str(item.code), "rank": item.rank}
                                        for item in jury.policy.allowed_sentences
                                    ]
                                },
                            },
                            "sentencing": _sentencing_view(decision),
                            "draft": subset.model_dump(mode="json", by_alias=True),
                            "evidence": evidence,
                        }
                    ),
                },
            ]
            tag = Intensity.hell if is_hell else None
            model = settings.MODEL_EVALUATOR_HELL if is_hell else settings.MODEL_JUDGMENT
            try:
                called = await call_model(
                    state,
                    EVALUATOR,
                    role="evaluator",
                    messages=messages,
                    schema=evaluator_schema([i.value for i in group]),
                    max_output_tokens=settings.EVALUATOR_MAX_OUTPUT_TOKENS,
                    call_index=evaluator_call_index(eval_round, is_hell),
                    model_override=settings.MODEL_EVALUATOR_HELL if is_hell else None,
                )
                if called is None:
                    skipped = CallRecord("evaluator", tag, model, "NO_BUDGET")
                    return ("SKIPPED", None, skipped, None)
                result, commit = called
                if result.output is None:
                    raise ValueError(f"검수관 출력 없음({result.stop_reason})")
            except (LLMError, TimeoutError, ValueError) as exc:
                broken = CallRecord("evaluator", tag, model, _error_name(exc))
                return ("FAILED", None, broken, None)
            record = CallRecord("evaluator", tag, result.model_id, None)
            return ("OK", dict(result.output), record, commit)

        outcomes = await asyncio.gather(*(one(group) for group in groups))
        records = [record for _, _, record, _ in outcomes if record.error != "NO_BUDGET"]
        commits = [commit for _, _, _, commit in outcomes if commit is not None]
        kinds = {kind for kind, _, _, _ in outcomes}
        if "FAILED" in kinds:
            return ("FAILED", None, records, [])
        if "SKIPPED" in kinds:
            return ("SKIPPED", None, records, [])
        merged = _merge_reports([output for _, output, _, _ in outcomes if output])
        return ("OK", merged, records, commits)

    async def run_evaluator(state: SentenceGraphState, rounds: list[int]) -> dict[str, Any]:
        """검수 루프. `rounds[0]` 은 이 실행의 다음 검수 라운드 번호다(call_index 용)."""
        jury = _jury(state)
        targets = _scope(state)
        order = {i.value: n for n, i in enumerate(targets)}
        policy_version = settings.GUARDRAIL_POLICY_VERSION
        calls = list(state["calls"])
        writer_draft: WriterDraft = state["writer_draft"]
        drafts = dict(state["drafts"])
        sources = dict(state["draft_sources"])
        decision: SentencingDecision | None = state.get("sentencing")
        kept = dict(state.get("eval_kept") or {})
        post_id = state["snapshot"].post_id
        all_template = {i: "TEMPLATE" for i in targets}

        def failed(code: str = EVAL_FAILED) -> dict[str, Any]:
            return {"calls": calls, "draft_sources": all_template, "failure": code, "eval_kept": {}}

        for attempt in range(2):
            current_hash = draft_hash(writer_draft, decision)
            current = {
                Intensity(t.intensity).value: t.model_dump(mode="json") for t in writer_draft.texts
            }
            reused = {key: entry for key, (text, entry) in kept.items() if current.get(key) == text}
            kept = {}
            scope = [i for i in targets if i.value not in reused] or targets
            kind, output, records, commits = await evaluate(
                state, writer_draft, decision, scope, rounds[0]
            )
            rounds[0] += 1
            calls.extend(records)
            if kind == "SKIPPED":
                logger.info("검수 예산 없음 → 검수 미시작")
                return {"calls": calls, "failure": DEADLINE_EXCEEDED, "eval_kept": {}}
            if kind == "FAILED" or output is None:
                code = _vendor_failure_code(records, EVAL_FAILED)
                logger.warning("검수관 오류 %s → 전 강도 TEMPLATE", code)
                return failed(code)

            fresh = [e for e in output.get("texts") or [] if isinstance(e, Mapping)]
            fresh_keys = {_key(e.get("intensity")) for e in fresh}
            entries_list = fresh + [e for k, e in reused.items() if k not in fresh_keys]
            entries_list.sort(key=lambda e: order.get(_key(e.get("intensity")) or "", len(order)))
            report = {
                **output,
                "texts": entries_list,
                "schema_version": 1,
                "policy_version": policy_version,
            }
            issues = validate_evaluation(report, targets, policy_version)
            if not issues:
                for commit in commits:
                    await commit()
                return {
                    "calls": calls,
                    "evaluation": EvaluationReport.model_validate(report),
                    "draft_hash": current_hash,
                    # D-19 치환이 있었으면 검수한 형량(이유만 바뀜)을 finalize 로 넘긴다.
                    "sentencing": decision,
                    "writer_draft": writer_draft,
                    "drafts": drafts,
                    "draft_sources": sources,
                    "eval_kept": {},
                    "failure": None,
                    "route": _ROUTE_FINALIZE,
                }
            if attempt == 1:
                logger.warning("재검수도 통과하지 못했다: %s", [i.code for i in issues])
                return failed()
            if _regenerate(state):
                # round 안 보정 없음: repair·TEMPLATE·D-19 이유 치환 없이 저장하지 않는다.
                logger.info("REGENERATE 검수 실패 %s → EVAL_FAILED", [i.code for i in issues])
                return failed()

            entries = {_key(e.get("intensity")): e for e in entries_list}
            rejected = [i for i in targets if entries.get(i.value, {}).get("pass") is False]
            incomplete = [
                i
                for i in targets
                if i not in rejected and entries.get(i.value, {}).get("pass") is not True
            ]
            reason_failed = False
            for issue in issues:
                if issue.path.startswith(_REASON_CHECK):
                    reason_failed = True
                elif not (issue.path.startswith("texts") and issue.code in _PER_TEXT_CODES):
                    logger.warning("검수 전역 실패: %s %s", issue.code, issue.path)
                    return failed()
            for commit in commits:
                await commit()
            if reason_failed:
                substituted = (
                    None
                    if decision is None
                    else sentencing_reason_template(jury, decision.sentence, deps.templates)
                )
                if decision is None or substituted is None:
                    return failed()
                decision = decision.model_copy(
                    update={"sentencing_reason": substituted, "reason_source": "TEMPLATE"}
                )

            repair = (
                bool(rejected)
                and state["mode"] == "INITIAL"
                and state.get("repair_count", 0) < settings.IMMEDIATE_REPAIR_MAX
                and state["deadline"].remaining_s() >= REPAIR_MIN_REMAINING_S
                and all(sources.get(i) == "AI" for i in rejected)
            )
            for intensity in incomplete if repair else [*rejected, *incomplete]:
                if sources.get(intensity) == "TEMPLATE":
                    return failed()
                template = template_for(intensity, jury, decision, post_id)
                if template is None:
                    return failed()
                drafts[intensity] = template
                sources[intensity] = "TEMPLATE"

            if repair:
                changed = {*rejected, *incomplete}
                keep = (
                    {}
                    if reason_failed
                    else {
                        i.value: (current[i.value], entries[i.value])
                        for i in targets
                        if i not in changed and i.value in current and i.value in entries
                    }
                )
                logger.info("검수 실패 %s → writer_repair", [i.value for i in rejected])
                return {
                    "calls": calls,
                    "drafts": drafts,
                    "draft_sources": sources,
                    "sentencing": decision,
                    "repair_targets": rejected,
                    "repair_avoid": {i: _avoid(entries[i.value]) for i in rejected},
                    "eval_kept": keep,
                    "evaluation": None,
                    "draft_hash": None,
                    "failure": None,
                    "route": _ROUTE_REPAIR,
                }

            rebuilt = assemble({**state, "drafts": drafts}, drafts, sources, decision)
            if rebuilt.get("failure") is not None:
                return failed()
            writer_draft = rebuilt["writer_draft"]
            drafts = rebuilt["drafts"]
            sources = rebuilt["draft_sources"]

        return failed()

    async def evaluator(state: SentenceGraphState) -> dict[str, Any]:
        rounds = [state.get("eval_round", 0)]
        update = await run_evaluator(state, rounds)
        return {**update, "eval_round": rounds[0]}

    async def writer_repair(state: SentenceGraphState) -> dict[str, Any]:
        """실패 강도만 각도 +1·"피할 것" 전달로 다시 쓴다. 양형·조서는 고정. 실패 → TEMPLATE."""
        count = state.get("repair_count", 0) + 1
        targets = list(state.get("repair_targets") or [])
        update = await fan_out(state, targets, count, state.get("repair_avoid") or {})
        update.pop("writer_budget_skipped", None)
        return {
            **update,
            "repair_count": count,
            "repair_targets": [],
            "repair_avoid": {},
            "evaluation": None,
            "draft_hash": None,
            "failure": None,
        }

    # --- 노드: finalize -------------------------------------------------------

    async def finalize(state: SentenceGraphState) -> dict[str, Any]:
        payload = _payload(state)
        snapshot = state["snapshot"]
        decision = state.get("sentencing")
        writer_draft: WriterDraft = state["writer_draft"]
        evaluated_hash = state["draft_hash"]
        if evaluated_hash is None or state.get("evaluation") is None:
            raise RuntimeError("검수 없이 finalize 할 수 없다")
        # 검수 뒤 문구·형량이 바뀌었으면 여기서 멈춘다(백엔드가 422 로 거부할 요청이다).
        if draft_hash(writer_draft, decision) != evaluated_hash:
            raise RuntimeError("검수 대상 hash 와 finalize draft hash 가 다르다")
        if deps.preparation is not None:
            # 04 §3.5 (b) finalize 직전 epoch 재확인.
            stale = await deps.preparation.stale_scopes(
                [(pv.scope_key, pv.epoch) for pv in snapshot.privacy_versions]
            )
            if stale:
                logger.info("finalize 직전 epoch 불일치 %s → finalize 하지 않음", stale)
                return {"failure": EVIDENCE_INVALIDATED}
        request = FinalizeRequest(
            schema_version=1,
            job_id=state["job"].id,
            generation_id=deps.generation_id,
            verdict_version=payload.verdict_version,
            expected_text_version=state["begin"].text_version,
            dossier_id=state["dossier"].dossier_id,
            privacy_versions=snapshot.privacy_versions,
            draft_hash=evaluated_hash,
            sentencing=decision,
            draft=writer_draft,
            evaluation=state["evaluation"],
            evaluation_draft_hash=evaluated_hash,
            prompt_bundle_version=deps.prompt_version,
            guardrail_policy_version=settings.GUARDRAIL_POLICY_VERSION,
            model_ids=ModelIds(
                sentencing=settings.MODEL_JUDGMENT,
                writer=settings.MODEL_WRITER,
                evaluator=settings.MODEL_JUDGMENT,
            ),
        )
        try:
            await deps.backend.finalize(payload.verdict_id, request)
        except Exception as exc:
            status = getattr(exc, "status", None)
            code = getattr(exc, "code", None)
            if status == 409 and code in _DISCARD_ON_FINALIZE:
                logger.info("finalize 409 %s — 폐기", code)
                return {"failure": None, "route": _ROUTE_END}
            if status == 409 and code == EVIDENCE_INVALIDATED:
                return {"failure": EVIDENCE_INVALIDATED}
            if status == 422:
                sources = state.get("draft_sources") or {}
                ai = [i for i in _scope(state) if sources.get(i) == "AI"]
                can_repair = (
                    bool(ai)
                    and state["mode"] == "INITIAL"
                    and state.get("repair_count", 0) < settings.IMMEDIATE_REPAIR_MAX
                    and state["deadline"].remaining_s() >= REPAIR_MIN_REMAINING_S
                )
                if can_repair:
                    logger.info("finalize 422 %s → writer_repair %s", code, [i.value for i in ai])
                    return {
                        "failure": None,
                        "repair_targets": ai,
                        "repair_avoid": {},
                        "eval_kept": {},
                        "route": _ROUTE_REPAIR,
                    }
                return {"failure": SCHEMA_INVALID}
            raise
        return {"failure": None, "route": _ROUTE_END}

    async def generation_failed(state: SentenceGraphState) -> dict[str, Any]:
        payload = _payload(state)
        await deps.backend.generation_failed(
            payload.verdict_id,
            job_id=state["job"].id,
            generation_id=deps.generation_id,
            error_code=state["failure"],  # type: ignore[arg-type]
        )
        return {}

    # --- 간선 ---------------------------------------------------------------

    def after_begin(state: SentenceGraphState) -> str:
        if state.get("begin") is None:
            return END
        return "generation_failed" if state.get("failure") else "load_valid_prep"

    def sentencing_or_writer(state: SentenceGraphState) -> str:
        jury = _jury(state)
        needs = (
            state["mode"] == "INITIAL"
            and state.get("sentencing_source") != "FIXED"
            and str(jury.result) == GUILTY
            and state["snapshot"].post_type == SPENT
        )
        return "sentencing" if needs else "writer"

    def after_prep(state: SentenceGraphState) -> str:
        route = state.get("route")
        if route == _ROUTE_INLINE:
            return "inline_context"
        if route == _ROUTE_MINIMAL:
            return "minimal_dossier"
        return sentencing_or_writer(state)

    def after_dossier(state: SentenceGraphState) -> str:
        return "generation_failed" if state.get("failure") else sentencing_or_writer(state)

    def failed_or(next_node: str) -> Callable[[SentenceGraphState], str]:
        def route(state: SentenceGraphState) -> str:
            return "generation_failed" if state.get("failure") else next_node

        return route

    def after_evaluator(state: SentenceGraphState) -> str:
        if state.get("failure"):
            return "generation_failed"
        return "writer_repair" if state.get("route") == _ROUTE_REPAIR else "finalize"

    def after_finalize(state: SentenceGraphState) -> str:
        if state.get("failure"):
            return "generation_failed"
        return "writer_repair" if state.get("route") == _ROUTE_REPAIR else END

    graph = StateGraph(SentenceGraphState)
    graph.add_node("begin_generation", begin_generation)
    graph.add_node("load_valid_prep", load_valid_prep)
    graph.add_node("inline_context", inline_context)
    graph.add_node("minimal_dossier", minimal_dossier_node)
    graph.add_node("sentencing", sentencing)
    graph.add_node("writer", writer)
    graph.add_node("join", join)
    graph.add_node("deterministic_validate", deterministic_validate)
    graph.add_node("evaluator", evaluator)
    graph.add_node("writer_repair", writer_repair)
    graph.add_node("finalize", finalize)
    graph.add_node("generation_failed", generation_failed)

    graph.add_edge(START, "begin_generation")
    graph.add_conditional_edges(
        "begin_generation", after_begin, ["load_valid_prep", "generation_failed", END]
    )
    graph.add_conditional_edges(
        "load_valid_prep", after_prep, ["inline_context", "minimal_dossier", "sentencing", "writer"]
    )
    for node in ("inline_context", "minimal_dossier"):
        graph.add_conditional_edges(
            node, after_dossier, ["sentencing", "writer", "generation_failed"]
        )
    graph.add_edge("sentencing", "writer")
    graph.add_edge("writer", "join")
    graph.add_conditional_edges(
        "join", failed_or("deterministic_validate"), ["deterministic_validate", "generation_failed"]
    )
    graph.add_conditional_edges(
        "deterministic_validate", failed_or("evaluator"), ["evaluator", "generation_failed"]
    )
    graph.add_conditional_edges(
        "evaluator", after_evaluator, ["finalize", "writer_repair", "generation_failed"]
    )
    graph.add_edge("writer_repair", "deterministic_validate")
    graph.add_conditional_edges(
        "finalize", after_finalize, [END, "writer_repair", "generation_failed"]
    )
    graph.add_edge("generation_failed", END)
    return graph.compile()
