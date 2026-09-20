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
- INLINE 조서(9/14 D-25): 서기 상한 + 검수 상한 + finalize 예약(`inline_context_reserve`)을 먼저
  남긴다. `남은 − 예약 > 0` ∧ 남은 ≥ `INLINE_CONTEXT_MIN_REMAINING_MS`(하한 보조)일 때만 시작하고,
  timeout = `min(WRITER 상한, 남은 − 예약)`. 기본 상한(서기 6·검수 4)이면 10초 마감에서
  사실상 MINIMAL
- 모델 호출 직전 epoch 불일치(게이트웨이 `EvidenceInvalidated`, 9/14 D-26)는 어느 노드든
  `failure=EVIDENCE_INVALIDATED` → `generation_failed`, finalize 0. 서기·검수 fan-out 은 한
  호출이라도 무효면 전체 무효(TEMPLATE 치환 아님)
- repair 조건 "남은 ≥ 5s" 는 05 §3.3 값(`REPAIR_MIN_REMAINING_S`), 횟수는 `IMMEDIATE_REPAIR_MAX`.
  `REGENERATE`(TEXT_RETRY) 는 repair 하지 않는다(05 §1 "round 안 보정 없음")
- repair 대상은 검수관이 `pass=false` 로 판정한 AI 강도다. 보고서에서 빠졌거나 형식이 틀린 강도는
  피할 위반이 없어 바로 TEMPLATE
- 새 카드 생성 형식(20자 제목·1항목 100자 본문, 9/19 30→100) 위반은 join에서 같은 repair 예산을
  사용한다.
  검수 시간을 예약할 수 없거나 TEXT_RETRY이면 재작성하지 않는다.
- repair 뒤 검수는 바뀐 강도만 보낸다. 바뀌지 않은 강도는 직전 통과 항목을 이어 붙인다. hash 는
  언제나 전체 draft 기준이다
- 검수 뒤 TEMPLATE·D-19 치환으로 draft 가 바뀌면 전체를 한 번 더 검수한다(코디네이터 9/14,
  hash 규칙 우선). 그것도 통과하지 못하면 `EVAL_FAILED`
- finalize 422 repair 는 AI 강도 전부를 다시 쓴다(백엔드 응답에 강도가 없다). 뒤 검수는 전 강도
- join: 강도 일부 누락 ∧ AI 강도 있음 → `SCHEMA_INVALID`(서버 검증 5항). AI 강도 없음 →
  `domain.retries.writer_failure_code`. 서기 오류에 `BUDGET` 이 있으면 `BUDGET_EXCEEDED`,
  시간 예산으로 시작 못 한 강도나 `TIMEOUT` 난 서기가 있으면 `DEADLINE_EXCEEDED`(08 §3.5),
  나머지(xAI `DEGRADED`·`AUTH` 포함) `VENDOR_UNAVAILABLE`
- `hell` 별도 검수는 `role="evaluator"` 호출을 둘로 나눈다. 게이트웨이 경로에서 hell 호출은
  `model_override=MODEL_EVALUATOR_HELL` 로 라우터가 모델을 고른다.
  두 보고서의 검사 필드는 AND 로 합친다
- 검수 호출 오류: `BUDGET` → `BUDGET_EXCEEDED`, `DEGRADED`·`AUTH` → `VENDOR_UNAVAILABLE`, 그 밖 →
  `EVAL_FAILED`. 셋 다 전 강도 TEMPLATE
- 원장 `call_index`(코디네이터 9/14 결정 3): 서기 `repair_count×3 + target 안 강도 순서`, 검수
  `검수 라운드×2 + (hell 별도면 1)`, 양형·조서 0. 검수 라운드는 state `eval_round` 로 센다
- 게이트웨이 경로의 timeout·재시도는 게이트웨이가 시도마다 건다(그래프 `wait_for` 가 재시도를 자르지
  않게). 세마포어는 재시도 대기 동안에도 쥐고 있다
- `node_results` 는 검증을 통과한 출력만: 양형은 `parse_sentencing` 뒤,
  서기는 `CardTextDraft`·강도·각도 검사 뒤, 검수는 보고서에 전역 형식 실패가 없을 때(`remember`)
- `db_now` 는 핸들러가 준다(`application.sentence_case`: `job.updated_at` + 경과 monotonic)

`REGENERATE`(TEXT_RETRY, 08 §3.2):

- 형량·양형 이유는 begin 응답의 `fixed_sentencing` 그대로다. 양형 0호출, 검수 `reason_failed` 에도
  이유를 템플릿으로 바꾸지 않는다(바꾸면 형량 필드가 달라진다) → `EVAL_FAILED`
- `load_valid_prep` 만 읽는다. prep 이 없으면 남은 시간과 무관하게 MINIMAL(조서 호출 없음)
- 대상 강도 = payload `intensities`(None 이면 `target_intensities`). 서기·join·⑤·⑥·finalize `texts`
  모두 이 집합이다. `target_intensities` 밖 강도가 있으면 호출 없이 `SCHEMA_INVALID`(계획서에 없음)
- 예산 = `min(begin deadline, begin 노드 시작 + TEXT_RETRY_TIMEOUT_SECONDS)`. round 안 보정 없음
- 어느 강도든 서기 실패·예산 없음·⑤ 실패·검수 실패면 TEMPLATE 을 새로 finalize 하지 않고 저장 없이
  `generation_failed` 다. 서기는 join 코드 규칙(`writer_failure_code`: 예산 > 시간·TIMEOUT > 벤더),
  ⑤·검수 거부·불완전은 `EVAL_FAILED`, 검수 호출 오류는 INITIAL 과 같은 표.
  다음 round 는 백엔드가 예약한다
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal, Protocol

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from geoji_ai.application import instrument
from geoji_ai.application.build_evidence import build_evidence
from geoji_ai.application.llm_gateway import (
    ScopedLLM,
    case_scope,
    evaluator_call_index,
    writer_call_index,
)
from geoji_ai.contracts.case import CaseSnapshot, JurySnapshot
from geoji_ai.contracts.evaluation import EvaluationReport, ViolationCode
from geoji_ai.contracts.finalize import FinalizeRequest, ModelIds
from geoji_ai.contracts.jobs import Job, SentencePayload, TextRetryPayload, parse_payload
from geoji_ai.contracts.llm_schemas import evaluator_schema, sentencing_schema, writer_schema
from geoji_ai.contracts.sentencing import SentencingDecision
from geoji_ai.contracts.writer import (
    CARD_HEADLINE_MAX,
    CARD_STATEMENT_MAX,
    CardTextDraft,
    MemeHints,
    TextDraft,
    WriterDraft,
)
from geoji_ai.domain.attack_angles import (
    ANGLE_GUIDES,
    ANGLE_ORDER,
    HISTORY_ANGLES,
    RULE_ANGLES,
    AttackAngle,
)
from geoji_ai.domain.budget import (
    EVALUATOR,
    SENTENCING,
    WRITER,
    Deadline,
    inline_context_reserve,
    reserve_after,
)
from geoji_ai.domain.draft_hash import draft_hash
from geoji_ai.domain.intensity import Intensity
from geoji_ai.domain.retries import writer_failure_code
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
from geoji_ai.prompts import (
    SENTENCING_PROMPT,
    build_evaluator_system,
    build_writer_system,
    load_prompt,
    prompt_bundle_version,
)
from geoji_ai.telemetry.alerts import AlertNotifier, finalize_db_error

__all__ = [
    "MINIMAL_PACK_LIMIT",
    "REPAIR_MIN_REMAINING_S",
    "SENTENCING_REASON_MAX",
    "SentenceDeps",
    "SentenceGraphState",
    "ValidPrepLike",
    "build_sentence_graph",
    "build_writer_request",
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
_ROUTE_EVALUATOR = "evaluator"
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
    #: 캐시에 넣지 않은 카드 형식 오류. 강도 → 재작성에 줄 직전 제목·본문·글자 수(`_card_preview`).
    #: 로그·원장에는 넣지 않는다(9/18).
    writer_invalid: dict[Intensity, dict[str, Any]]
    #: `writer_repair` 가 다시 쓸 강도.
    repair_targets: list[Intensity]
    #: 강도별 "피할 것"(위반 코드·문제 문장. 카드 형식이면 직전 제목·본문·글자 수도).
    repair_avoid: dict[Intensity, dict[str, Any]]
    #: repair 뒤 재사용할 직전 통과 항목. 강도 값 → (그때의 TextDraft JSON, 보고서 항목).
    eval_kept: dict[str, tuple[dict[str, Any], dict[str, Any]]]
    #: 조건 간선이 읽는 다음 노드.
    route: str
    #: 이 실행에서 끝난 검수 라운드 수. 검수 `call_index` 에 쓴다.
    eval_round: int
    #: 검수 실패로 `writer_repair` 에 보낸 강도(중복 없음). `evaluation_repair_rate` 분모.
    eval_repaired: list[Intensity]
    #: finalize 결과. `SAVED`(200) · `DISCARDED`(409 폐기). 핸들러의 `sentence_summary` 가 읽는다.
    finalize_outcome: str


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
    #: 운영 알림(08 §3.3 finalize DB 오류). None 이면 알리지 않는다.
    notifier: AlertNotifier | None = None


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
        writer_invalid={},
        repair_targets=[],
        repair_avoid={},
        eval_kept={},
        route="",
        eval_round=0,
        eval_repaired=[],
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


def _raise_if_exception(item: Any) -> Any:
    """`gather(return_exceptions=True)` 결과 하나. 예외면 올린다."""
    if isinstance(item, BaseException):
        raise item
    return item


#: DDL 002 `fact_type`. 과거 지출·판결 기록(F0 는 이번 지출이라 뺀다)과 방 규칙.
#: `RULE_HIT` 는 기억 은행(003)·조서 LLM 출력의 규칙 적중 이름이다(`preparation.py`).
_HISTORY_FACT_TYPES = frozenset({"SPEND", "VERDICT"})
_RULE_FACT_TYPES = frozenset({"RULE", "RULE_HIT"})
#: `build_evidence._aggregates` 의 반복 집계 문장. 우리가 만든 문장이라 형식이 고정이다.
_REPEAT_COUNT = re.compile(r"같은 카테고리 확정 소비 (\d+)건")


def ungrounded_angles(dossier: Dossier | None) -> frozenset[AttackAngle]:
    """조서에 근거가 없어 서기에게 시키면 안 되는 각도(9/18).

    반복·미래 예언은 F0 밖의 지출·판결 기록이나 반복 집계 ≥ 1건이 있어야 하고,
    규칙 의인화(`RULE_ANGLES`)는 방 규칙 근거가 있어야 한다.
    첫 지출의 최소 조서(F0 만)는 둘 다 없다.
    """
    if dossier is None:
        return HISTORY_ANGLES | RULE_ANGLES
    facts = [fact for fact in dossier.facts if fact.label != "F0"]
    has_history = any(fact.fact_type in _HISTORY_FACT_TYPES for fact in facts) or any(
        fact.fact_type == "AGGREGATE"
        and (match := _REPEAT_COUNT.search(fact.text)) is not None
        and int(match.group(1)) >= 1
        for fact in facts
    )
    has_rule = any(fact.fact_type in _RULE_FACT_TYPES for fact in facts)
    skip: set[AttackAngle] = set()
    if not has_history:
        skip |= HISTORY_ANGLES
    if not has_rule:
        skip |= RULE_ANGLES
    return frozenset(skip)


def _case_view(snapshot: CaseSnapshot) -> dict[str, Any]:
    return {
        "item": snapshot.item,
        "amount_krw": snapshot.amount_krw,
        "category": snapshot.category,
        "reason": snapshot.reason,
        "post_type": snapshot.post_type,
    }


def _facts_view(dossier: Dossier | None) -> list[dict[str, str]]:
    return (
        []
        if dossier is None
        else [
            {"id": f.label, "text": f.text, "epistemic_type": f.epistemic_type}
            for f in dossier.facts
        ]
    )


def writer_angles(snapshot: CaseSnapshot, dossier: Dossier | None) -> list[AttackAngle]:
    """사유 검토를 우선하되, 근거가 있는 기법 중 모델이 선택한다.

    승인·무죄는 사유의 타당성을 인정하는 방식으로 쓴다. EXCUSE_DISSECTION 은
    기존 외부 enum 을 유지하되 이 경우 변명 공격이 아닌 사유 검토를 뜻한다.
    """
    if snapshot.jury is not None and str(snapshot.jury.result) in {"agree", "notGuilty"}:
        return [AttackAngle.EXCUSE_DISSECTION]
    skip = ungrounded_angles(dossier)
    return [AttackAngle.EXCUSE_DISSECTION] + [
        angle
        for angle in ANGLE_ORDER
        if angle not in skip and angle is not AttackAngle.EXCUSE_DISSECTION
    ]


def _sentencing_view(sentencing: SentencingDecision | None) -> dict[str, Any] | None:
    return None if sentencing is None else sentencing.model_dump(mode="json")


def build_writer_request(
    snapshot: CaseSnapshot,
    dossier: Dossier | None,
    decision: SentencingDecision | None,
    intensity: Intensity,
    *,
    offset: int = 0,
    banter: Mapping[Intensity, Sequence[Candidate]] | None = None,
    avoid: Mapping[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """운영 그래프와 서기 실측이 공유하는 요청 조립. 외부 호출·저장은 하지 않는다.

    offset은 기존 호출 인터페이스를 유지한다. 재작성 때도 기법을 순환 강제하지 않고
    같은 허용 범위와 avoid에 담긴 실제 문제를 제공한다.
    """
    jury = snapshot.jury
    if jury is None:
        raise ValueError("선고 스냅샷에 jury 가 없다")
    angles = writer_angles(snapshot, dossier)
    candidates = [c for c in (banter or {}).get(intensity, []) if str(jury.result) in c.fits]
    candidate_ids = [c.candidate_id for c in candidates]
    user: dict[str, Any] = {
        "intensity": intensity.value,
        "attack_angles": [
            {
                "code": angle.value,
                "label": ANGLE_GUIDES[angle].label_ko,
                "instruction": ANGLE_GUIDES[angle].instruction,
            }
            for angle in angles
        ],
        "case": _case_view(snapshot),
        "jury": {
            "result": str(jury.result),
            "vote_counts": jury.vote_counts,
            "guilty_ratio": jury.guilty_ratio,
        },
        "sentencing": _sentencing_view(decision),
        "dossier": _facts_view(dossier),
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
    return messages, writer_schema(
        [intensity.value], [angle.value for angle in angles], candidate_ids or None
    )


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


def _rule_avoid(codes: Sequence[str], messages: Sequence[str]) -> dict[str, list[str]]:
    """서버 검증 ⑤ 위반 → 서기에게 줄 "피할 것"(9/16).

    검수관 경로(`_avoid`)와 달리 문제 문장 원문이 없다. 규칙 메시지에 걸린 단어가 들어 있어
    그대로 준다(예: "hell 에 비속어 ['씨발']").
    """
    return {
        "violations": list(dict.fromkeys(str(code) for code in codes)),
        "rule_messages": list(dict.fromkeys(str(message) for message in messages)),
    }


_CARD_LIMIT_MESSAGE = (
    f"headline 1~{CARD_HEADLINE_MAX}자, statement 정확히 1항목, text 1~{CARD_STATEMENT_MAX}자. "
    "줄바꿈·빈 문구 금지."
)


def _card_preview(output: Mapping[str, Any]) -> dict[str, Any]:
    """카드 형식에 걸린 출력에서 재작성에 줄 것만 뽑는다. 제목·본문 원문과 글자 수.

    상태에만 두고 로그·원장·캐시에는 넣지 않는다. 문자열이 아닌 값은 None.
    """
    headline = output.get("headline")
    statement = output.get("statement")
    items = [s for s in statement if isinstance(s, Mapping)] if isinstance(statement, list) else []
    text = items[0].get("text") if items else None
    if not isinstance(headline, str):
        headline = None
    if not isinstance(text, str):
        text = None
    return {
        "previous_headline": headline,
        "previous_headline_length": None if headline is None else len(headline),
        "previous_text": text,
        "previous_text_length": None if text is None else len(text),
        "previous_statement_count": len(items),
    }


def _card_avoid(preview: Mapping[str, Any]) -> dict[str, Any]:
    """카드 형식 위반 → 서기에게 줄 "피할 것"(9/18).

    9/17 배포에서 서기·재작성이 둘 다 `SCHEMA_INVALID` 였다. 규칙 문장만 주면 자기가 몇 자를
    썼는지 몰라 같은 길이로 다시 쓴다. 직전 제목·본문과 글자 수를 주고 "압축" 을 시킨다.
    """
    messages = [_CARD_LIMIT_MESSAGE]
    headline_length = preview.get("previous_headline_length")
    text_length = preview.get("previous_text_length")
    count = preview.get("previous_statement_count")
    if isinstance(headline_length, int) and headline_length > CARD_HEADLINE_MAX:
        messages.append(
            f"직전 제목이 {headline_length}자라 상한 {CARD_HEADLINE_MAX}자를 넘었다. "
            f"뜻을 유지하고 {CARD_HEADLINE_MAX}자 이내로 압축한다."
        )
    if isinstance(text_length, int) and text_length > CARD_STATEMENT_MAX:
        messages.append(
            f"직전 본문이 {text_length}자라 상한 {CARD_STATEMENT_MAX}자를 넘었다. "
            f"같은 각도·같은 뜻을 두 문장 이내 {CARD_STATEMENT_MAX}자 이내로 압축한다. "
            "설명·도입·마무리는 뺀다."
        )
    if isinstance(count, int) and count != 1:
        messages.append(f"본문 항목이 {count}개다. 정확히 1항목이어야 한다.")
    return {
        **_rule_avoid([SCHEMA_INVALID], messages),
        **{key: value for key, value in preview.items() if value is not None},
    }


def _avoid(entry: Mapping[str, Any]) -> dict[str, list[str]]:
    """검수 항목 → 서기에게 줄 "피할 것"."""
    codes = [str(v.get("code")) for v in entry.get("violations") or [] if isinstance(v, Mapping)]
    sentences = [str(s) for s in entry.get("problem_sentences") or []]
    return {"violations": list(dict.fromkeys(codes)), "problem_sentences": sentences}


def _merge_same_intensity(entries: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """같은 강도 항목이 여러 개면 하나로 합친다(9/16).

    검수관이 강도마다 한 항목을 내야 하는데, 실측에서 luna 가 지옥맛 하나를 위반별로 쪼개
    같은 강도를 여러 번 냈다. 그러면 `validate_evaluation` 이 `DUPLICATE_INTENSITY` 를 내고
    그 코드는 강도별 코드가 아니라 전역 실패라 판결문이 통째로 사라졌다(15 §6 6회차).
    합치는 규칙은 `_merge_reports` 와 같다. `pass` 는 AND(하나라도 false 면 false, 하나라도
    불리언이 아니면 미완), 위반과 문제 문장은 순서를 지켜 이어 붙이고 중복만 뺀다.
    """
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    extras: list[dict[str, Any]] = []
    for entry in entries:
        key = _key(entry.get("intensity"))
        if key is None:
            # 모르는 강도는 합치지 않는다. `validate_evaluation` 이 그대로 잡아야 한다.
            extras.append(dict(entry))
            continue
        if key not in merged:
            merged[key] = dict(entry)
            order.append(key)
            continue
        target = merged[key]
        first, second = target.get("pass"), entry.get("pass")
        if isinstance(first, bool) and isinstance(second, bool):
            target["pass"] = first and second
        elif not isinstance(first, bool):
            target["pass"] = first
        else:
            target["pass"] = second
        target["violations"] = _dedup_dicts(
            [*(target.get("violations") or []), *(entry.get("violations") or [])]
        )
        target["problem_sentences"] = list(
            dict.fromkeys(
                str(s)
                for s in [
                    *(target.get("problem_sentences") or []),
                    *(entry.get("problem_sentences") or []),
                ]
            )
        )
    return [merged[key] for key in order] + extras


def _dedup_dicts(items: Sequence[Any]) -> list[Any]:
    """순서를 지키며 중첩 배열(evidence_labels)을 포함한 동일 위반도 제거한다."""
    out: list[Any] = []
    for item in items:
        if item not in out:
            out.append(item)
    return out


def _merge_reports(outputs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """검수 호출이 둘로 나뉘었을 때 보고서를 합친다. 검사 필드는 AND, 위반은 이어 붙인다."""
    if len(outputs) == 1:
        return dict(outputs[0])
    merged: dict[str, Any] = {
        "texts": [entry for output in outputs for entry in output.get("texts") or []]
    }
    for name in _EVALUATION_CHECKS:
        checks = [output.get(name) for output in outputs]
        if any(not isinstance(check, Mapping) for check in checks):
            merged[name] = None
            continue
        if any(not isinstance(check.get("pass"), bool) for check in checks):
            merged[name] = {"pass": None, "violations": []}
            continue
        merged[name] = {
            "pass": all(check["pass"] for check in checks),
            "violations": [v for check in checks for v in check.get("violations") or []],
        }
    return merged


# ---------------------------------------------------------------------------
# 그래프
# ---------------------------------------------------------------------------


#: `evaluation_failure_total(code)` 라벨로 쓸 수 있는 값. 계약 밖 코드는 세지 않는다(라벨 보호).
_VIOLATION_CODES = frozenset(code.value for code in ViolationCode)


def _observe_violations(output: Mapping[str, Any], fresh: Sequence[Mapping[str, Any]]) -> None:
    """검수관이 `pass=false` 로 낸 위반 코드마다 `evaluation_failure_total(code)` 를 1 올린다.

    이번 호출의 출력만 센다(repair 뒤 이어 붙인 직전 통과 항목은 빼고).

    코드만으로는 검수관이 과하게 잡는 것인지 서기가 실제로 지어낸 것인지 가릴 수 없어
    `evaluation_rejected` 로 검수관의 설명도 같이 남긴다(9/20 운영 진단). 남기는 것은 검수관이
    쓴 판정 사유이고 판결문 본문(`problem_sentences`)은 넣지 않는다.
    """
    names = _EVALUATION_CHECKS + tuple(_key(e.get("intensity")) or "?" for e in fresh)
    checks = [output.get(name) for name in _EVALUATION_CHECKS] + list(fresh)
    for name, check in zip(names, checks, strict=True):
        if not isinstance(check, Mapping) or check.get("pass") is not False:
            continue
        reasons = []
        for violation in check.get("violations") or []:
            if not isinstance(violation, Mapping):
                continue
            code = violation.get("code")
            if code in _VIOLATION_CODES:
                instrument.count("evaluation_failure_total", code=code)
            reasons.append(
                "{}@{}: {}".format(
                    code if code in _VIOLATION_CODES else "UNKNOWN",
                    str(violation.get("path") or "")[:60],
                    str(violation.get("explanation") or "")[:300],
                )
            )
        logger.info("검수 반려 %s | %s", name, " ; ".join(reasons) or "사유 없음")


def _new_call_errors(state: Mapping[str, Any], update: Mapping[str, Any]) -> list[str]:
    """이 노드가 더한 호출 기록의 오류 코드(폴백 원인). 원문 없음."""
    calls = update.get("calls")
    if not isinstance(calls, list):
        return []
    before = len(state.get("calls") or [])
    return sorted({c.error for c in calls[before:] if getattr(c, "error", None)})


def build_sentence_graph(deps: SentenceDeps) -> Any:
    """그래프 C 를 컴파일한다. 실행마다 새로 만들어도 된다(체크포인터 없음)."""
    settings = deps.settings

    gateway = deps.llm if isinstance(deps.llm, ScopedLLM) else None

    # --- 단계별·역할별 기록(9/16, 08 §3.3) --------------------------------------
    # 세 이벤트로 "어느 역할이 어디서 왜" 를 남긴다. 원문 없음, 코드·시간·개수만.
    # - `sentence_call`: 모델 호출 1회마다. 받은 timeout, 남은 마감, 지연, 오류 kind
    # - `sentence_fallback`: 실패가 결과를 바꾼 지점. RULE·TEMPLATE·writer_repair·generation_failed
    # - `sentence_summary`: 실행 1회 요약(핸들러, `application/sentence_case.py`)

    def _ids(state: Mapping[str, Any]) -> dict[str, Any]:
        job = state.get("job")
        return {
            "trace_id": getattr(job, "trace_id", None),
            "job_id": getattr(job, "id", None),
            "generation_id": deps.generation_id,
            "graph_name": "sentencing",
            "prompt_bundle_version": deps.prompt_version,
            "guardrail_policy_version": settings.GUARDRAIL_POLICY_VERSION,
        }

    def default_model(role: str) -> str:
        return settings.MODEL_WRITER if role in ("writer", "banter") else settings.MODEL_JUDGMENT

    def fallback_log(
        state: Mapping[str, Any],
        *,
        node: str,
        role: str,
        outcome: str,
        reason: str | None,
        intensity: Intensity | str | None = None,
    ) -> None:
        """역할 하나의 실패가 결과를 어떻게 바꿨는지. `outcome` 은 코드값이다."""
        instrument.node_log(
            "sentence_fallback",
            **_ids(state),
            node=node,
            role=role,
            intensity=getattr(intensity, "value", intensity),
            outcome=outcome,
            fallback_reason=reason,
        )

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
        intensity: Intensity | None = None,
    ) -> tuple[LLMResult, Commit] | None:
        """세마포어 안에서 예산을 계산한다. 예산이 없으면 호출하지 않고 None.

        (결과, 확정)을 돌려준다. 확정은 호출자가 출력 검증에 성공한 뒤 부른다.
        호출마다 `sentence_call` 로그(받은 timeout·남은 마감·지연·오류 kind)를 남긴다.
        """
        deadline: Deadline = state["deadline"]
        async with deps.semaphore:
            timeout = deadline.node_timeout(node, settings)
            base: dict[str, Any] = {
                **_ids(state),
                "node": node,
                "role": role,
                "intensity": intensity.value if intensity is not None else None,
                "call_index": call_index,
                "model_id": model_override or default_model(role),
                "remaining_s": round(deadline.remaining_s(), 3),
            }
            if timeout is None:
                # 상한이 아니라 마감(뒤 단계 예약 포함)이 모자라 시작하지 않았다.
                instrument.node_log(
                    "sentence_call",
                    **base,
                    timeout_s=None,
                    latency_ms=0,
                    ok=False,
                    fallback_reason="NO_BUDGET",
                )
                return None
            started = time.monotonic()
            try:
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

                    result: LLMResult = scoped.result
                    done: Commit = commit
                else:
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
                    done = _no_commit
            except BaseException as exc:
                instrument.node_log(
                    "sentence_call",
                    **base,
                    timeout_s=round(timeout, 3),
                    latency_ms=int((time.monotonic() - started) * 1000),
                    ok=False,
                    fallback_reason=_error_name(exc),
                )
                raise
            usage = getattr(result, "usage", None)
            no_output = result.output is None
            instrument.node_log(
                "sentence_call",
                **{**base, "model_id": result.model_id or base["model_id"]},
                timeout_s=round(timeout, 3),
                latency_ms=int((time.monotonic() - started) * 1000),
                ok=not no_output,
                fallback_reason=f"NO_OUTPUT:{result.stop_reason}" if no_output else None,
                prompt_tokens=getattr(usage, "prompt_tokens", None),
                completion_tokens=getattr(usage, "completion_tokens", None),
            )
            return result, done

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

    def can_repair(state: Mapping[str, Any], targets: Iterable[Intensity]) -> bool:
        """지금 `writer_repair` 를 한 번 더 태울 수 있는가(05 §3.3 조건, 검수 실패와 같은 표)."""
        sources = state.get("draft_sources") or {}
        return (
            state["mode"] == "INITIAL"
            and state.get("repair_count", 0) < settings.IMMEDIATE_REPAIR_MAX
            and state["deadline"].remaining_s() >= REPAIR_MIN_REMAINING_S
            and all(sources.get(i) == "AI" for i in targets)
        )

    def assemble(
        state: Mapping[str, Any],
        drafts: dict[Intensity, TextDraft],
        sources: dict[Intensity, str],
        sentencing: SentencingDecision | None,
        *,
        allow_repair: bool = False,
    ) -> dict[str, Any]:
        """서버 검증 ⑤. 걸린 AI 강도는 다시 쓰게 하고, 안 되면 TEMPLATE 로 바꾼다.

        `allow_repair`(9/16): 규칙 위반은 "허용 목록 밖 단어를 썼다" 처럼 기계적으로 고칠 수 있는
        것이라 검수 실패와 같은 조건에서 `writer_repair` 를 한 번 태운다. 옛 동작은 곧바로
        TEMPLATE 치환이었는데, 대상 강도가 하나(= 공유 방이 하나인 보통 경우)면 그 하나가
        TEMPLATE 이 되는 순간 `all(TEMPLATE)` 이라 검수관을 부르지도 못하고 `EVAL_FAILED` 로 끝났다.
        같은 위반인데 방 개수에 따라 결과가 갈렸다(15 §6).
        검수 실패 경로와 예산(`IMMEDIATE_REPAIR_MAX`)을 나눠 쓰므로 한 판결에서 재작성은 최대 1회다.
        """
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
            #: 강도별 위반 설명(재작성 때 "피할 것" 으로 준다). 원문 문장이 아니라 규칙 메시지다.
            messages: dict[Intensity, list[str]] = {}
            for issue in rules.issues:
                index = _text_index(issue.path)
                if index is None or index >= len(targets):
                    logger.warning("서버 검증 전역 실패: %s %s", issue.code, issue.message)
                    fallback_log(
                        state,
                        node="deterministic_validate",
                        role="server_rules",
                        outcome="generation_failed",
                        reason=f"{SCHEMA_INVALID}:{issue.code}",
                    )
                    return {"failure": SCHEMA_INVALID}
                bad.setdefault(targets[index], []).append(issue.code)
                messages.setdefault(targets[index], []).append(issue.message)
            # 라벨·식별자 제거로 빈 제목/본문이 될 수 있으므로 정리한 뒤에도 카드 규격을 검사한다.
            for intensity, text in zip(targets, rules.draft["texts"], strict=True):
                try:
                    CardTextDraft.model_validate(text)
                except ValidationError:
                    bad.setdefault(intensity, []).append(SCHEMA_INVALID)
                    messages.setdefault(intensity, []).append(
                        "제목·본문 정리 후 카드 규격 위반: "
                        f"제목 1~{CARD_HEADLINE_MAX}자, 본문 1항목 1~{CARD_STATEMENT_MAX}자."
                    )
            if not bad:
                try:
                    writer_draft = WriterDraft.model_validate(rules.draft)
                except ValidationError as exc:
                    logger.warning("재조립 초안이 계약에 맞지 않는다: %s", exc.error_count())
                    fallback_log(
                        state,
                        node="deterministic_validate",
                        role="server_rules",
                        outcome="generation_failed",
                        reason=f"{SCHEMA_INVALID}:CONTRACT",
                    )
                    return {"failure": SCHEMA_INVALID}
                rebuilt = {Intensity(t.intensity): t for t in writer_draft.texts}
                if all(sources[i] == "TEMPLATE" for i in targets):
                    fallback_log(
                        state,
                        node="deterministic_validate",
                        role="server_rules",
                        outcome="generation_failed",
                        reason=f"{EVAL_FAILED}:ALL_TEMPLATE",
                    )
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
                fallback_log(
                    state,
                    node="deterministic_validate",
                    role="server_rules",
                    outcome="generation_failed",
                    reason=f"{EVAL_FAILED}:REGENERATE",
                )
                return {"failure": EVAL_FAILED}
            if allow_repair and can_repair(state, bad):
                # 코드만 남기면 길이 위반인지 카드 규격 위반인지 가릴 수 없다(9/20 운영 진단).
                logger.info(
                    "서버 검증 실패 %s → writer_repair | %s",
                    sorted(i.value for i in bad),
                    "; ".join(f"{i.value}: {' / '.join(messages.get(i, []))}" for i in bad),
                )
                for intensity in bad:
                    fallback_log(
                        state,
                        node="deterministic_validate",
                        role="server_rules",
                        intensity=intensity,
                        outcome=_ROUTE_REPAIR,
                        reason=",".join(bad[intensity]),
                    )
                return {
                    "failure": None,
                    "route": _ROUTE_REPAIR,
                    "repair_targets": list(bad),
                    "repair_avoid": {i: _rule_avoid(bad[i], messages[i]) for i in bad},
                    "eval_kept": {},
                }
            for intensity, codes in bad.items():
                logger.info(
                    "강도 %s 서버 검증 실패 %s → TEMPLATE | %s",
                    intensity,
                    codes,
                    " / ".join(messages.get(intensity, [])) or "사유 없음",
                )
                if sources.get(intensity) == "TEMPLATE":
                    fallback_log(
                        state,
                        node="deterministic_validate",
                        role="server_rules",
                        intensity=intensity,
                        outcome="generation_failed",
                        reason=f"{SCHEMA_INVALID}:TEMPLATE_REJECTED",
                    )
                    return {"failure": SCHEMA_INVALID}
                template = template_for(intensity, jury, sentencing, snapshot.post_id)
                if template is None:
                    fallback_log(
                        state,
                        node="deterministic_validate",
                        role="server_rules",
                        intensity=intensity,
                        outcome="generation_failed",
                        reason=f"{EVAL_FAILED}:TEMPLATE_UNAVAILABLE",
                    )
                    return {"failure": EVAL_FAILED}
                fallback_log(
                    state,
                    node="deterministic_validate",
                    role="server_rules",
                    intensity=intensity,
                    outcome="TEMPLATE",
                    reason=",".join(codes),
                )
                drafts[intensity] = template
                sources[intensity] = "TEMPLATE"
        fallback_log(
            state,
            node="deterministic_validate",
            role="server_rules",
            outcome="generation_failed",
            reason=f"{SCHEMA_INVALID}:RETRY_EXHAUSTED",
        )
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
                fallback_log(
                    state,
                    node="begin_generation",
                    role="backend",
                    outcome="DISCARDED",
                    reason=f"409:{getattr(exc, 'code', None)}",
                )
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
        remaining_s = state["deadline"].remaining_s()
        # D-25: 서기·검수·finalize 시간을 먼저 남기고 남는 시간이 있을 때만. 하한 설정은 보조.
        inline = (
            remaining_s - inline_context_reserve(settings) > 0
            and remaining_s * 1000 >= settings.INLINE_CONTEXT_MIN_REMAINING_MS
        )
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
            fallback_log(
                state,
                node="inline_context",
                role="backend",
                outcome="F0_ONLY",
                reason=type(exc).__name__,
            )
            resolved = None
        dossier = dossier_from_resolved(snapshot, resolved, settings)
        reserve = inline_context_reserve(settings)
        timeout = min(
            float(settings.WRITER_NODE_TIMEOUT_SECONDS),
            state["deadline"].remaining_s() - reserve,
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
                    reserve_s=reserve,
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
            except EvidenceInvalidated:
                raise  # D-26: 노드 래퍼가 EVIDENCE_INVALIDATED 로 옮긴다.
            except Exception as exc:  # 코드 Evidence 만으로 계속(05 §5.2).
                calls.append(CallRecord("context", None, settings.MODEL_JUDGMENT, _error_name(exc)))
                logger.warning("inline 조서 실패 %s → 코드 Evidence 만", _error_name(exc))
                fallback_log(
                    state,
                    node="inline_context",
                    role="context",
                    outcome="CODE_EVIDENCE_ONLY",
                    reason=_error_name(exc),
                )
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
            {"role": "system", "content": load_prompt(SENTENCING_PROMPT)},
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
                fallback_log(
                    state, node=SENTENCING, role="sentencing", outcome="RULE", reason="NO_BUDGET"
                )
                return {**rule_sentencing(jury), "calls": calls}
            result, commit = called
            decision = parse_sentencing(result.output, jury)
            await commit()
        except (LLMError, TimeoutError, ValueError) as exc:
            calls.append(CallRecord("sentencing", None, settings.MODEL_JUDGMENT, _error_name(exc)))
            logger.warning("양형관 실패 %s → RULE", _error_name(exc))
            fallback_log(
                state, node=SENTENCING, role="sentencing", outcome="RULE", reason=_error_name(exc)
            )
            return {**rule_sentencing(jury), "calls": calls}
        calls.append(CallRecord("sentencing", None, result.model_id, None))
        if decision.reason_source == "TEMPLATE":
            # D-19: 형량은 AI, 이유만 길이 초과로 템플릿 치환.
            fallback_log(
                state,
                node=SENTENCING,
                role="sentencing",
                outcome="TEMPLATE_REASON",
                reason="REASON_TOO_LONG",
            )
        return {"sentencing": decision, "sentencing_source": "AI", "calls": calls}

    # --- 노드: 서기 ----------------------------------------------------------

    async def write_one(
        state: Mapping[str, Any],
        intensity: Intensity,
        offset: int,
        avoid: Mapping[str, Any] | None,
    ) -> tuple[str, Any, CallRecord | None]:
        jury = _jury(state)
        snapshot = state["snapshot"]
        decision: SentencingDecision | None = state.get("sentencing")
        messages, schema = build_writer_request(
            snapshot,
            state["dossier"],
            decision,
            intensity,
            offset=offset,
            banter=state.get("banter"),
            avoid=avoid,
        )
        try:
            called = await call_model(
                state,
                WRITER,
                role="writer",
                messages=messages,
                schema=schema,
                max_output_tokens=settings.WRITER_MAX_OUTPUT_TOKENS,
                call_index=writer_call_index(offset, _targets(jury).index(intensity)),
                intensity=intensity,
            )
            if called is None:
                return ("SKIPPED", None, None)
            result, commit = called
            if result.output is None:
                raise ValueError(f"서기 출력 없음({result.stop_reason})")
            raw = dict(result.output)
            # 모델의 사전 요약은 새 사실이 아니다. 원문·조서로 최종 문구를 검수한다.
            raw.pop("case_reading", None)
            hints_raw = raw.pop("meme_hints", None)
            raw.pop("meme_tag", None)
            # 뒤 문장에 사유·반전이 있을 수 있다. 초과하면 기존 1회 repair로 압축한다.
            text = CardTextDraft.model_validate({**raw, "source": "AI"})
            if (
                text.intensity != intensity
                or text.attack_angle.value not in schema["properties"]["attack_angle"]["enum"]
            ):
                raise ValueError("요청한 강도·근거 있는 각도 범위와 다르다")
            hints = MemeHints.model_validate(hints_raw) if hints_raw is not None else None
            await commit()
        except ValidationError:
            # 길이·항목 수·줄바꿈 등 형식 오류는 원문을 기록하거나 캐시하지 않는다.
            # 재작성에 줄 직전 제목·본문·글자 수만 상태로 넘긴다(9/18).
            return (
                "INVALID_CARD",
                _card_preview(raw),
                CallRecord("writer", intensity, settings.MODEL_WRITER, SCHEMA_INVALID),
            )
        except (LLMError, TimeoutError, ValueError) as exc:
            record = CallRecord("writer", intensity, settings.MODEL_WRITER, _error_name(exc))
            return ("FAILED", None, record)
        return ("AI", (text, hints), CallRecord("writer", intensity, result.model_id, None))

    async def fan_out(
        state: Mapping[str, Any],
        intensities: Sequence[Intensity],
        offset: int,
        avoid: Mapping[Intensity, Mapping[str, Any]],
    ) -> dict[str, Any]:
        """강도마다 서기 1호출. 실패·예산 없음 강도는 TEMPLATE(없으면 비워 둔다)."""
        jury = _jury(state)
        decision: SentencingDecision | None = state.get("sentencing")
        post_id = state["snapshot"].post_id
        gathered = await asyncio.gather(
            *(write_one(state, i, offset, avoid.get(i)) for i in intensities),
            return_exceptions=True,
        )
        # 한 강도라도 무효(D-26)면 전체 무효. 모든 호출이 끝난 뒤 올린다.
        outcomes = [_raise_if_exception(item) for item in gathered]
        calls = list(state["calls"])
        drafts: dict[Intensity, TextDraft] = dict(state.get("drafts") or {})
        sources: dict[Intensity, Literal["AI", "TEMPLATE"]] = dict(state.get("draft_sources") or {})
        hints: MemeHints | None = state.get("meme_hints")
        invalid = dict(state.get("writer_invalid") or {})
        skipped = False
        for intensity, (kind, value, record) in zip(intensities, outcomes, strict=True):
            invalid.pop(intensity, None)
            if record is not None:
                calls.append(record)
            if kind == "AI":
                text, text_hints = value
                drafts[intensity] = text
                sources[intensity] = "AI"
                if intensity == jury.default_intensity:
                    hints = text_hints
                continue
            if kind == "INVALID_CARD":
                invalid[intensity] = value
            skipped = skipped or kind == "SKIPPED"
            drafts.pop(intensity, None)
            sources.pop(intensity, None)
            if intensity == jury.default_intensity:
                hints = None
            reason = record.error if record is not None else "NO_BUDGET"
            node_name = WRITER if offset == 0 else _ROUTE_REPAIR
            if _regenerate(state):
                # TEMPLATE 을 새로 만들지 않는다. join 이 실패로 보낸다.
                fallback_log(
                    state,
                    node=node_name,
                    role="writer",
                    intensity=intensity,
                    outcome="NONE",
                    reason=reason,
                )
                continue
            template = template_for(intensity, jury, decision, post_id)
            fallback_log(
                state,
                node=node_name,
                role="writer",
                intensity=intensity,
                outcome="TEMPLATE" if template is not None else "NONE",
                reason=reason,
            )
            if template is not None:
                drafts[intensity] = template
                sources[intensity] = "TEMPLATE"
        return {
            "drafts": drafts,
            "draft_sources": sources,
            "meme_hints": hints,
            "writer_budget_skipped": skipped,
            "writer_invalid": {i: invalid[i] for i in _scope(state) if i in invalid},
            "calls": calls,
        }

    async def writer(state: SentenceGraphState) -> dict[str, Any]:
        return await fan_out(state, _scope(state), state.get("repair_count", 0), {})

    async def join(state: SentenceGraphState) -> dict[str, Any]:
        targets = _scope(state)
        invalid = state.get("writer_invalid") or {}
        if (
            invalid
            and state["mode"] == "INITIAL"
            and state.get("repair_count", 0) < settings.IMMEDIATE_REPAIR_MAX
            and state["deadline"].remaining_s() >= REPAIR_MIN_REMAINING_S
            and state["deadline"].node_timeout(WRITER, settings) is not None
        ):
            for intensity in invalid:
                fallback_log(
                    state,
                    node="join",
                    role="writer",
                    intensity=intensity,
                    outcome=_ROUTE_REPAIR,
                    reason=SCHEMA_INVALID,
                )
            return {
                "failure": None,
                "route": _ROUTE_REPAIR,
                "repair_targets": list(invalid),
                "repair_avoid": {i: _card_avoid(invalid[i]) for i in invalid},
                "eval_kept": {},
            }
        sources = state.get("draft_sources") or {}
        not_ai = [i for i in targets if sources.get(i) != "AI"]
        if len(not_ai) == len(targets) or (_regenerate(state) and not_ai):
            writer_errors = [c.error for c in state["calls"] if c.role == "writer"]
            code = writer_failure_code(
                writer_errors, budget_skipped=bool(state.get("writer_budget_skipped"))
            )
            if invalid and all(sources.get(i) == "AI" or i in invalid for i in targets):
                code = SCHEMA_INVALID
            kinds = ",".join(sorted({e for e in writer_errors if e})) or "NO_AI"
            fallback_log(
                state,
                node="join",
                role="writer",
                outcome="generation_failed",
                reason=f"{code}:{kinds}",
            )
            return {"failure": code}
        missing = set(targets) - set(state.get("drafts") or {})
        if missing:
            # 서버 검증 5항(강도 집합 == target_intensities)을 join 에서 먼저 건다.
            logger.warning("강도 누락 %s → SCHEMA_INVALID", sorted(i.value for i in missing))
            fallback_log(
                state,
                node="join",
                role="writer",
                outcome="generation_failed",
                reason=f"{SCHEMA_INVALID}:MISSING_{'_'.join(sorted(i.value for i in missing))}",
            )
            return {"failure": SCHEMA_INVALID}
        return {"failure": None, "route": "deterministic_validate"}

    async def deterministic_validate(state: SentenceGraphState) -> dict[str, Any]:
        result = assemble(
            state,
            state["drafts"],
            state["draft_sources"],
            state.get("sentencing"),
            allow_repair=True,
        )
        if "drafts" in result:
            result["validation"] = {i: [] for i in result["drafts"]}
        # `route` 는 늘 덮어쓴다. 앞 노드가 남긴 값이 그대로 남으면 간선이 잘못 돈다.
        result.setdefault("route", _ROUTE_EVALUATOR)
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
                {"role": "system", "content": build_evaluator_system(policy_version)},
                {
                    "role": "user",
                    "content": _dumps(
                        {
                            "policy_version": policy_version,
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
                    intensity=tag,
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

        gathered = await asyncio.gather(*(one(group) for group in groups), return_exceptions=True)
        outcomes = [_raise_if_exception(item) for item in gathered]
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

        def failed(code: str = EVAL_FAILED, *, why: str | None = None) -> dict[str, Any]:
            fallback_log(
                state,
                node=EVALUATOR,
                role="evaluator",
                outcome="generation_failed",
                reason=f"{code}:{why}" if why else code,
            )
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
                fallback_log(
                    state,
                    node=EVALUATOR,
                    role="evaluator",
                    outcome="generation_failed",
                    reason=f"{DEADLINE_EXCEEDED}:NO_BUDGET",
                )
                return {"calls": calls, "failure": DEADLINE_EXCEEDED, "eval_kept": {}}
            if kind == "FAILED" or output is None:
                code = _vendor_failure_code(records, EVAL_FAILED)
                kinds = ",".join(sorted({r.error for r in records if r.error})) or "NO_OUTPUT"
                logger.warning("검수관 오류 %s → 전 강도 TEMPLATE", code)
                return failed(code, why=kinds)

            fresh = _merge_same_intensity(
                [e for e in output.get("texts") or [] if isinstance(e, Mapping)]
            )
            _observe_violations(output, fresh)
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
                return failed(why="RECHECK:" + ",".join(sorted({i.code for i in issues})))
            if _regenerate(state):
                # round 안 보정 없음: repair·TEMPLATE·D-19 이유 치환 없이 저장하지 않는다.
                logger.info("REGENERATE 검수 실패 %s → EVAL_FAILED", [i.code for i in issues])
                return failed(why="REGENERATE:" + ",".join(sorted({i.code for i in issues})))

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
                    return failed(why=f"GLOBAL:{issue.code}")
            for commit in commits:
                await commit()
            if reason_failed:
                substituted = (
                    None
                    if decision is None
                    else sentencing_reason_template(jury, decision.sentence, deps.templates)
                )
                if decision is None or substituted is None:
                    return failed(why="REASON_CHECK:NO_TEMPLATE")
                decision = decision.model_copy(
                    update={"sentencing_reason": substituted, "reason_source": "TEMPLATE"}
                )
                fallback_log(
                    state,
                    node=EVALUATOR,
                    role="evaluator",
                    outcome="TEMPLATE_REASON",
                    reason="REASON_CHECK",
                )

            repair = (
                bool(rejected)
                and state["mode"] == "INITIAL"
                and state.get("repair_count", 0) < settings.IMMEDIATE_REPAIR_MAX
                and state["deadline"].remaining_s() >= REPAIR_MIN_REMAINING_S
                and all(sources.get(i) == "AI" for i in rejected)
            )
            for intensity in incomplete if repair else [*rejected, *incomplete]:
                why = "EVAL_REJECTED" if intensity in rejected else "EVAL_INCOMPLETE"
                if sources.get(intensity) == "TEMPLATE":
                    return failed(why=f"{why}:TEMPLATE_REJECTED:{intensity.value}")
                template = template_for(intensity, jury, decision, post_id)
                if template is None:
                    return failed(why=f"{why}:TEMPLATE_UNAVAILABLE:{intensity.value}")
                fallback_log(
                    state,
                    node=EVALUATOR,
                    role="evaluator",
                    intensity=intensity,
                    outcome="TEMPLATE",
                    reason=why,
                )
                drafts[intensity] = template
                sources[intensity] = "TEMPLATE"

            if repair:
                for intensity in rejected:
                    fallback_log(
                        state,
                        node=EVALUATOR,
                        role="evaluator",
                        intensity=intensity,
                        outcome=_ROUTE_REPAIR,
                        reason="EVAL_REJECTED",
                    )
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
                    "eval_repaired": list(
                        dict.fromkeys([*(state.get("eval_repaired") or []), *rejected])
                    ),
                    "eval_kept": keep,
                    "evaluation": None,
                    "draft_hash": None,
                    "failure": None,
                    "route": _ROUTE_REPAIR,
                }

            rebuilt = assemble({**state, "drafts": drafts}, drafts, sources, decision)
            if rebuilt.get("failure") is not None:
                return failed(why=f"REASSEMBLE:{rebuilt.get('failure')}")
            writer_draft = rebuilt["writer_draft"]
            drafts = rebuilt["drafts"]
            sources = rebuilt["draft_sources"]

        return failed(why="ROUNDS_EXHAUSTED")

    async def evaluator(state: SentenceGraphState) -> dict[str, Any]:
        rounds = [state.get("eval_round", 0)]
        update = await run_evaluator(state, rounds)
        return {**update, "eval_round": rounds[0]}

    async def writer_repair(state: SentenceGraphState) -> dict[str, Any]:
        """실패 강도에 같은 기법 범위와 피할 문장을 전달한다. 양형·조서는 고정한다."""
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
                if code == _STALE_GENERATION:
                    instrument.count("stale_finalize_total", kind=state["job"].kind)
                fallback_log(
                    state,
                    node="finalize",
                    role="backend",
                    outcome="DISCARDED",
                    reason=f"409:{code}",
                )
                return {"failure": None, "route": _ROUTE_END, "finalize_outcome": "DISCARDED"}
            if status == 409 and code == EVIDENCE_INVALIDATED:
                fallback_log(
                    state,
                    node="finalize",
                    role="backend",
                    outcome="generation_failed",
                    reason=f"409:{EVIDENCE_INVALIDATED}",
                )
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
                    fallback_log(
                        state,
                        node="finalize",
                        role="backend",
                        outcome=_ROUTE_REPAIR,
                        reason=f"422:{code}",
                    )
                    return {
                        "failure": None,
                        "repair_targets": ai,
                        "repair_avoid": {},
                        "eval_kept": {},
                        "route": _ROUTE_REPAIR,
                    }
                fallback_log(
                    state,
                    node="finalize",
                    role="backend",
                    outcome="generation_failed",
                    reason=f"{SCHEMA_INVALID}:422:{code}",
                )
                return {"failure": SCHEMA_INVALID}
            if not isinstance(status, int):
                # 백엔드 4xx 거부가 아니다 — 5xx 재전송 소진·연결 불가 등 저장 경로 오류(08 §3.3
                # "finalize DB 오류"). job 정리는 그대로 핸들러가 한다.
                code_name = getattr(exc, "error_code", None) or type(exc).__name__
                await instrument.notify(
                    deps.notifier,
                    finalize_db_error(job_kind=state["job"].kind, code=str(code_name)),
                )
            reason = (
                f"{status}:{code}"
                if isinstance(status, int)
                else str(getattr(exc, "error_code", None) or type(exc).__name__)
            )
            fallback_log(state, node="finalize", role="backend", outcome="error", reason=reason)
            raise
        await observe_saved(state)
        return {"failure": None, "route": _ROUTE_END, "finalize_outcome": "SAVED"}

    async def generation_failed(state: SentenceGraphState) -> dict[str, Any]:
        payload = _payload(state)
        await deps.backend.generation_failed(
            payload.verdict_id,
            job_id=state["job"].id,
            generation_id=deps.generation_id,
            error_code=state["failure"],  # type: ignore[arg-type]
        )
        if state["failure"] == EVIDENCE_INVALIDATED:
            instrument.count("invalidated_evidence_total")
        if _regenerate(state):
            instrument.rate("retry_recovery_rate", False)
        for intensity in state.get("eval_repaired") or []:
            instrument.rate("evaluation_repair_rate", False, intensity=Intensity(intensity).value)
        return {}

    async def observe_saved(state: SentenceGraphState) -> None:
        """finalize 200 뒤 지표(08 §3.3). 입력을 못 만들면 로그만 남기고 넘어간다."""
        try:
            jury = _jury(state)
            regenerate = _regenerate(state)
            sources = state.get("draft_sources") or {}
            targets = _scope(state)
            # "평결 확정 → 첫 저장". 두 시각 모두 DB 기준(`jury.confirmed_at`, `db_now`).
            elapsed = max(0.0, (await deps.db_now() - jury.confirmed_at).total_seconds())
        except Exception as exc:
            logger.warning("저장 지표 입력을 만들 수 없다: %s", type(exc).__name__)
            return
        if regenerate:
            path = "regen"
        else:
            path = "guilty" if str(jury.result) == GUILTY else "other"
        instrument.observe("first_result_latency_seconds", elapsed, path=path)
        if regenerate:
            instrument.rate("retry_recovery_rate", True)
        else:
            instrument.rate(
                "template_first_rate", any(sources.get(i) == "TEMPLATE" for i in targets)
            )
        for intensity in state.get("eval_repaired") or []:
            instrument.rate(
                "evaluation_repair_rate",
                sources.get(intensity) == "AI",
                intensity=Intensity(intensity).value,
            )

    # --- 노드 로그 ------------------------------------------------------------

    def logged(
        name: str, fn: Callable[[SentenceGraphState], Awaitable[dict[str, Any]]]
    ) -> Callable[[SentenceGraphState], Awaitable[dict[str, Any]]]:
        """노드 한 번마다 `sentence_node` 로그(08 §3.3). 원문 없이 코드·지연·개수만."""

        def emit(state: Mapping[str, Any], started: float, **fields: Any) -> None:
            job = state.get("job")
            instrument.node_log(
                "sentence_node",
                trace_id=getattr(job, "trace_id", None),
                job_id=getattr(job, "id", None),
                generation_id=deps.generation_id,
                graph_name="sentencing",
                node=name,
                latency_ms=int((time.monotonic() - started) * 1000),
                prompt_bundle_version=deps.prompt_version,
                guardrail_policy_version=settings.GUARDRAIL_POLICY_VERSION,
                **fields,
            )

        async def node(state: SentenceGraphState) -> dict[str, Any]:
            started = time.monotonic()
            try:
                update = await fn(state)
            except EvidenceInvalidated as exc:
                # D-26: 모델 호출 직전 epoch 불일치. 저장 없이 generation_failed 로 보낸다.
                logger.info(
                    "모델 호출 직전 epoch 불일치 %s → %s", exc.scope_keys, EVIDENCE_INVALIDATED
                )
                update = {"failure": EVIDENCE_INVALIDATED}
            except BaseException as exc:
                emit(state, started, ok=False, fallback_reason=type(exc).__name__)
                raise
            update = update or {}
            if name == "generation_failed":
                failure = state.get("failure")
            else:
                failure = update.get("failure")
            errors = _new_call_errors(state, update)
            emit(
                state,
                started,
                ok=failure is None,
                fallback_reason=failure or (",".join(errors) if errors else None),
                repair_count=update.get("repair_count", state.get("repair_count")),
                # 이 노드가 고른 길(prep/inline/minimal, AI/RULE, repair/finalize, SAVED/DISCARDED)
                outcome=update.get("finalize_outcome")
                or update.get("route")
                or update.get("dossier_source")
                or update.get("sentencing_source"),
            )
            return update

        return node

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

    def after_validate(state: SentenceGraphState) -> str:
        if state.get("failure"):
            return "generation_failed"
        return _ROUTE_REPAIR if state.get("route") == _ROUTE_REPAIR else "evaluator"

    def after_join(state: SentenceGraphState) -> str:
        if state.get("failure"):
            return "generation_failed"
        return _ROUTE_REPAIR if state.get("route") == _ROUTE_REPAIR else "deterministic_validate"

    def after_evaluator(state: SentenceGraphState) -> str:
        if state.get("failure"):
            return "generation_failed"
        return "writer_repair" if state.get("route") == _ROUTE_REPAIR else "finalize"

    def after_finalize(state: SentenceGraphState) -> str:
        if state.get("failure"):
            return "generation_failed"
        return "writer_repair" if state.get("route") == _ROUTE_REPAIR else END

    graph = StateGraph(SentenceGraphState)
    for node_name, node_fn in (
        ("begin_generation", begin_generation),
        ("load_valid_prep", load_valid_prep),
        ("inline_context", inline_context),
        ("minimal_dossier", minimal_dossier_node),
        ("sentencing", sentencing),
        ("writer", writer),
        ("join", join),
        ("deterministic_validate", deterministic_validate),
        ("evaluator", evaluator),
        ("writer_repair", writer_repair),
        ("finalize", finalize),
        ("generation_failed", generation_failed),
    ):
        graph.add_node(node_name, logged(node_name, node_fn))

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
    # 양형·서기·보정은 D-26 무효(failure)면 generation_failed 로 간다.
    graph.add_conditional_edges("sentencing", failed_or("writer"), ["writer", "generation_failed"])
    graph.add_conditional_edges("writer", failed_or("join"), ["join", "generation_failed"])
    graph.add_conditional_edges(
        "join", after_join, ["deterministic_validate", "writer_repair", "generation_failed"]
    )
    graph.add_conditional_edges(
        "deterministic_validate",
        after_validate,
        ["evaluator", "writer_repair", "generation_failed"],
    )
    graph.add_conditional_edges(
        "evaluator", after_evaluator, ["finalize", "writer_repair", "generation_failed"]
    )
    graph.add_conditional_edges(
        "writer_repair",
        failed_or("deterministic_validate"),
        ["deterministic_validate", "generation_failed"],
    )
    graph.add_conditional_edges(
        "finalize", after_finalize, [END, "writer_repair", "generation_failed"]
    )
    graph.add_edge("generation_failed", END)
    return graph.compile()
