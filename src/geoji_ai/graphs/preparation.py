"""그래프 B — 사전 준비(05 §3.2, proposal2 §5.2).

`load_case → load_reusable_prep → recall_candidates → resolve_sources → build_db_evidence →
analyze_reason → persist_dossier → generate_banter → validate_banter → persist_banter`

9/14 D-27 부분 재사용: `load_reusable_prep` 이 조서 키가 맞는 유효 준비 자료(`load_valid_prep`)를
찾으면 recall·resolve·build·조서 호출을 건너뛰고 `persist_dossier` 로 간다. 이때 `save_prep` 은
기존 dossier 를 가리키는 `trial_prep` 새 행만 넣고(완료분 불변), 드립 키가 맞는 강도의 후보는
복사하고 목표 강도 중 없는 것만 새로 부른다. `input_hash` 완전 일치 COMPLETE 행이면 기존대로 종료.

9/14 D-26: 게이트웨이가 모델 호출 직전 epoch 불일치로 `EvidenceInvalidated` 를 올리면 삼키지 않고
핸들러(`PrepareHandler`)까지 올린다(`fail("EVIDENCE_INVALIDATED")`, 저장 0).

모델 호출은 조서(`context`) 1회와 드립 후보(`banter`) 강도마다 1회뿐이다. `llm` 이 게이트웨이
(`ScopedLLM`)면 원장·재사용을 거친다. 노드 이름 `context`(call_index 0)·`banter`(call_index = 대상
강도 순서), 예산 키 `budget_key_for_post(post_id)`, 기한 없음. 조서 저장에 성공하면
`DOSSIER_READY`, 드립까지 되면 `COMPLETE`. **드립 실패가 조서를 되돌리지 않는다.**

실행 문맥(포트·설정·세마포어)은 `PrepareDeps` 로 받아 노드 클로저에 묶는다. state 는
`graphs.states.PrepareState` 그대로이고, state 에 자리가 없는 실행 중 값(`prep_id`, 강도별 호출
성공 여부)은 실행마다 새로 만드는 `_Run` 에 둔다.

이 모듈은 SQLAlchemy·어댑터를 import 하지 않는다.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import timedelta
from typing import Any

from langgraph.graph import END, START, StateGraph

from geoji_ai.application import instrument
from geoji_ai.application.build_evidence import build_evidence, target_pack
from geoji_ai.application.llm_gateway import CallScope, ScopedLLM, case_scope
from geoji_ai.contracts.case import CaseSnapshot, VerdictResult
from geoji_ai.contracts.jobs import Job, PreparePayload, parse_payload
from geoji_ai.contracts.llm_schemas import banter_schema, context_schema, with_enums
from geoji_ai.contracts.writer import BanterStrategy
from geoji_ai.core.config import Settings
from geoji_ai.core.logging import get_logger
from geoji_ai.domain import dossier_rules
from geoji_ai.domain.input_hash import dossier_key
from geoji_ai.domain.intensity import ALL_INTENSITIES, Intensity
from geoji_ai.domain.lexicon import DEATH_WORDS, PROFANITY, LexiconRule, applies
from geoji_ai.domain.visibility import Scope, Visibility
from geoji_ai.graphs.states import Candidate, PrepareState
from geoji_ai.ports.backend import (
    Aggregates,
    AggregateWindow,
    BackendPort,
    EvidenceCandidate,
    EvidenceInclude,
    ResolveEvidenceRequest,
    ResolveEvidenceResponse,
)
from geoji_ai.ports.llm import LLMPort
from geoji_ai.ports.memory import MemoryCandidate, MemoryPort
from geoji_ai.ports.preparation import (
    EVIDENCE_TEXT_MAX,
    Dossier,
    EvidenceFact,
    EvidenceInvalidated,
    PreparationPort,
)
from geoji_ai.prompts import load_prompt, prompt_bundle_version

__all__ = [
    "ALLOWED_FACT_KINDS",
    "BANTER_PROMPT",
    "CONTEXT_PROMPT",
    "RECALL_TIMEOUT_S",
    "STATUS_COMPLETE",
    "STATUS_DOSSIER_READY",
    "STATUS_STALE_EVENT",
    "PrepareDeps",
    "build_preparation_graph",
    "call_context",
    "dossier_from_resolved",
    "evidence_include",
    "filter_banter",
    "merge_inferred_facts",
    "run_preparation",
]

log = get_logger(__name__)

CONTEXT_PROMPT = "context-v1.md"
BANTER_PROMPT = "banter-v1.md"

#: recall 전체 상한. 05 §3.2 는 0.5초지만 운영에서 그 예산으로는 왕복이 안 끝난다.
#: EB 는 서울(ap-northeast-2), Postgres 는 싱가포르(ap-southeast-1)라 왕복 하나가 68ms 다.
#: `recall_room` 은 규칙 위반과 말투 예시를 순차로 두 번 조회하고, 풀에 커넥션이 없으면
#: TLS 핸드셰이크(3~4 왕복 ≈ 270ms)가 앞에 붙는다. 그래서 따뜻할 때는 0.3초에 끝나지만
#: 차가우면 0.5초를 넘겨 `recall_failed` 로 빈손이 되고 조서에 과거 기록이 빠진다(9/17~18 운영 로그).
#: PREPARE 전체가 17~23초라 3초는 상한이지 대기 시간이 아니다.
RECALL_TIMEOUT_S = 3.0

#: 조서 출력 kind 중 허용하는 것(05 §3.2 `analyze_reason`).
ALLOWED_FACT_KINDS: frozenset[str] = frozenset({"RULE_HIT", "MITIGATION", "REASON_ANALYSIS"})

#: Evidence 로 저장하는 kind → `ai.evidence.fact_type`. `REASON_ANALYSIS` 는 002 DDL `fact_type`
#: CHECK 에 없어 state `reason_analysis` 에만 둔다(코디네이터 9/14 해석).
_FACT_TYPE_BY_KIND: dict[str, str] = {"RULE_HIT": "RULE", "MITIGATION": "MITIGATION"}
_REASON_ANALYSIS = "REASON_ANALYSIS"
_MODEL_INFERENCE = "MODEL_INFERENCE"

#: 근거 라벨이 반드시 있어야 하는 전략(05 §3.2 `validate_banter`).
_LABEL_REQUIRED: frozenset[BanterStrategy] = frozenset(
    {BanterStrategy.REPEAT_OFFENSE, BanterStrategy.ROOM_RULE_CALLBACK}
)

#: 드립 후보 `fits` 값 = 평결 결과 4종(`VerdictResult`).
_FITS: tuple[str, ...] = tuple(result.value for result in VerdictResult)

STATUS_LOADED = "LOADED"
STATUS_STALE_EVENT = "STALE_EVENT"
STATUS_DOSSIER_READY = "DOSSIER_READY"
STATUS_COMPLETE = "COMPLETE"

#: resolve 실패 시 쓰는 빈 응답의 집계 창. `build_evidence` 가 F0 뒤를 잘라내므로 pack 에 들어가지
#: 않는다(아래 `_f0_only`).
_EMPTY_WINDOW = timedelta(days=30)


@dataclass
class PrepareDeps:
    """그래프 B 가 쓰는 포트와 설정. `llm` 이 None 이면 조서·드립 호출을 건너뛴다."""

    backend: BackendPort
    preparation: PreparationPort
    settings: Settings
    semaphore: asyncio.Semaphore
    generation_id: str
    memory: MemoryPort | None = None
    llm: LLMPort | ScopedLLM | None = None
    prompt_version: str = field(default_factory=prompt_bundle_version)


@dataclass
class _Run:
    """state 에 자리가 없는 실행 중 값. 실행마다 새로 만든다."""

    dossier: Dossier | None = None
    prep_id: str | None = None
    #: 드립 호출이 성공한 강도. 하나도 없으면(복사분도 없으면) `DOSSIER_READY` 유지.
    banter_ok: set[Intensity] = field(default_factory=set)
    #: D-27: 저장된 dossier 를 다시 쓴다(`save_prep(reuse_dossier=True)`).
    reuse_dossier: bool = False
    #: D-27: 드립 키가 맞아 복사할 목표 강도의 후보. 이 강도는 드립을 다시 부르지 않는다.
    copied: dict[Intensity, list[Candidate]] = field(default_factory=dict)


# --- 순수 함수 --------------------------------------------------------------------


def _narrowest_scope(scopes: Sequence[Scope]) -> Scope:
    """참조한 라벨들 scope 중 가장 좁은 것. PRIVATE 하나라도 → PRIVATE, ROOMS 는 교집합."""
    if any(scope.visibility is Visibility.PRIVATE for scope in scopes):
        return Scope(Visibility.PRIVATE, frozenset())
    rooms = [scope.room_ids for scope in scopes if scope.visibility is Visibility.ROOMS]
    if not rooms:
        return Scope(Visibility.PUBLIC, frozenset())
    return Scope(Visibility.ROOMS, frozenset.intersection(*rooms))


def merge_inferred_facts(
    dossier: Dossier, output: Mapping[str, Any] | None
) -> tuple[Dossier, dict[str, Any] | None]:
    """조서 출력을 코드 Evidence 뒤에 `MODEL_INFERENCE` 로 이어 붙인다.

    - kind 가 `RULE_HIT`·`MITIGATION`·`REASON_ANALYSIS` 밖이면 삭제
    - `source_refs` 가 비었거나 입력 라벨 밖을 하나라도 가리키면 삭제
    - `REASON_ANALYSIS` 는 Evidence 가 아니라 반환하는 `reason_analysis["facts"]` 에만 둔다
    - scope 는 참조 라벨 scope 의 가장 좁은 것, sources 는 참조 라벨 sources 의 합
    """
    if not isinstance(output, Mapping):
        return dossier, None
    by_label = {fact.label: fact for fact in dossier.facts}
    facts = list(dossier.facts)
    label_map = dict(dossier.label_map)
    analysis_texts: list[str] = []
    for item in output.get("facts") or []:
        if not isinstance(item, Mapping):
            continue
        kind = item.get("kind")
        refs = item.get("source_refs")
        body = item.get("text")
        if kind not in ALLOWED_FACT_KINDS or not isinstance(body, str) or not body.strip():
            continue
        if not isinstance(refs, list) or not refs or not all(ref in by_label for ref in refs):
            continue
        if kind == _REASON_ANALYSIS:
            analysis_texts.append(body)
            continue
        referenced = [by_label[ref] for ref in dict.fromkeys(refs)]
        label = f"F{len(facts)}"
        facts.append(
            EvidenceFact(
                label=label,
                epistemic_type=_MODEL_INFERENCE,
                fact_type=_FACT_TYPE_BY_KIND[kind],
                text=body[:EVIDENCE_TEXT_MAX],
                scope=_narrowest_scope([fact.scope for fact in referenced]),
                aggregation=None,
                occurred_at=None,
                sources=tuple(dict.fromkeys(s for fact in referenced for s in fact.sources)),
            )
        )
        label_map[label] = str(uuid.uuid4())
    analysis = output.get("reason_analysis")
    reason_analysis = dict(analysis) if isinstance(analysis, Mapping) else {}
    reason_analysis["facts"] = analysis_texts
    return replace(dossier, facts=tuple(facts), label_map=label_map), reason_analysis


def filter_banter(
    candidates: Iterable[Candidate], intensity: Intensity, allowed_labels: Iterable[str]
) -> list[Candidate]:
    """드립 필터 4규칙(05 §3.2 `validate_banter`).

    1. 입력에 없는 라벨은 후보에서 지운다
    2. `REPEAT_OFFENSE`·`ROOM_RULE_CALLBACK` 인데 (1 뒤) `evidence_labels` 가 비면 후보 삭제
    3. `DEATH_WORDS` 가 들어 있으면 삭제(모든 강도)
    4. `mild`·`spicy` 에 `PROFANITY` 가 들어 있으면 삭제
    """
    allowed = frozenset(allowed_labels)
    check_profanity = applies(intensity, LexiconRule.PROFANITY)
    kept: list[Candidate] = []
    for candidate in candidates:
        labels = tuple(label for label in candidate.evidence_labels if label in allowed)
        if candidate.strategy in _LABEL_REQUIRED and not labels:
            continue
        if any(word in candidate.text for word in DEATH_WORDS):
            continue
        if check_profanity and any(word in candidate.text for word in PROFANITY):
            continue
        kept.append(replace(candidate, evidence_labels=labels))
    return kept


def _target_intensities(snapshot: CaseSnapshot) -> list[Intensity]:
    """`room_snapshots` 의 강도 집합(강도 순). 없으면 `jury.default_intensity` 하나."""
    present = {room.intensity for room in snapshot.room_snapshots}
    if not present and snapshot.jury is not None:
        present = {snapshot.jury.default_intensity}
    return [intensity for intensity in ALL_INTENSITIES if intensity in present]


def _copied_banter(
    banter: Mapping[Intensity, list[Candidate]], snapshot: CaseSnapshot
) -> dict[Intensity, list[Candidate]]:
    """재사용 준비 자료의 드립 중 이번 목표 강도에 든 것만."""
    targets = set(_target_intensities(snapshot))
    return {intensity: list(items) for intensity, items in banter.items() if intensity in targets}


def _parse_candidates(output: Mapping[str, Any] | None) -> list[Candidate]:
    candidates: list[Candidate] = []
    if not isinstance(output, Mapping):
        return candidates
    for item in output.get("candidates") or []:
        if not isinstance(item, Mapping) or not isinstance(item.get("text"), str):
            continue
        try:
            strategy = BanterStrategy(item.get("strategy"))
        except ValueError:
            continue
        candidates.append(
            Candidate(
                candidate_id=str(uuid.uuid4()),
                text=item["text"],
                strategy=strategy,
                fits=tuple(str(fit) for fit in item.get("fits") or () if fit in _FITS),
                evidence_labels=tuple(str(label) for label in item.get("evidence_labels") or ()),
            )
        )
    return candidates


def _evidence_lines(facts: Iterable[EvidenceFact]) -> list[dict[str, str]]:
    return [{"label": fact.label, "text": fact.text} for fact in facts]


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _context_messages(snapshot: CaseSnapshot, facts: Iterable[EvidenceFact]) -> list[dict]:
    """조서 입력 = 사건·코드 사실(라벨)·사유 원문. 평결·형량은 넣지 않는다."""
    case = {
        "item": snapshot.item,
        "amount_krw": snapshot.amount_krw,
        "category": snapshot.category,
        "post_type": snapshot.post_type,
    }
    user = {"case": case, "facts": _evidence_lines(facts), "reason": snapshot.reason}
    return [
        {"role": "system", "content": load_prompt(CONTEXT_PROMPT)},
        {"role": "user", "content": _dumps(user)},
    ]


def _banter_messages(
    snapshot: CaseSnapshot,
    intensity: Intensity,
    facts: Iterable[EvidenceFact],
    examples: Sequence[str],
) -> list[dict]:
    """드립 입력 = 허용 Evidence(라벨)·사건 타입·강도·승인 예시. 말투 예시는 플래그 꺼짐(D-04)."""
    user = {
        "post_type": snapshot.post_type,
        "category": snapshot.category,
        "intensity": intensity.value,
        "evidence": _evidence_lines(facts),
        "approved_examples": list(examples),
    }
    return [
        {"role": "system", "content": load_prompt(BANTER_PROMPT)},
        {"role": "user", "content": _dumps(user)},
    ]


def _f0_only(snapshot: CaseSnapshot) -> ResolveEvidenceResponse:
    """resolve 실패 시 빈 응답. `pack_limit=1` 과 같이 써서 F0 만 남긴다."""
    return ResolveEvidenceResponse(
        sources=[],
        aggregates=Aggregates(
            burn_rate=0.0,
            tier="",
            no_spend_days=0,
            repeat_same_category_30d=0,
            excludes_post_id="",
            window=AggregateWindow(
                start_at=snapshot.created_at - _EMPTY_WINDOW, end_at=snapshot.created_at
            ),
            rule_version=0,
        ),
        room_rules=[],
        recent_verdicts=[],
        style_comments=[],
    )


def _merge_candidates(
    groups: Iterable[Sequence[MemoryCandidate]], limit: int
) -> list[MemoryCandidate]:
    """참조 기준 중복 제거 뒤 score 내림차순 상위 `limit`."""
    best: dict[tuple[str, str, int], MemoryCandidate] = {}
    for group in groups:
        for candidate in group:
            key = (candidate.source_type, candidate.source_id, candidate.source_version)
            if key not in best or candidate.score > best[key].score:
                best[key] = candidate
    return sorted(best.values(), key=lambda c: c.score, reverse=True)[:limit]


# --- 그래프 C 가 같이 쓰는 단계(05 §3.3 inline_context) ----------------------------


def evidence_include(settings: Settings) -> list[EvidenceInclude]:
    """`resolve-evidence` 의 include. 말투 댓글은 플래그가 켜졌을 때만."""
    include: list[EvidenceInclude] = ["rules", "aggregates", "recent_verdicts"]
    if settings.ROOM_COMMENT_STYLE_ENABLED:
        include.append("style_comments")
    return include


def dossier_from_resolved(
    snapshot: CaseSnapshot, resolved: ResolveEvidenceResponse | None, settings: Settings
) -> Dossier:
    """04 `build_evidence`. resolve 실패(None)면 F0 만."""
    if resolved is None:
        return build_evidence(snapshot, _f0_only(snapshot), pack_limit=1)
    return build_evidence(
        snapshot,
        resolved,
        pack_limit=settings.EVIDENCE_PACK_LIMIT,
        rule_matcher=dossier_rules.rule_matcher,
    )


async def call_context(
    llm: LLMPort | ScopedLLM,
    semaphore: asyncio.Semaphore,
    settings: Settings,
    snapshot: CaseSnapshot,
    dossier: Dossier,
    *,
    timeout_s: float | None = None,
    scope: CallScope | None = None,
) -> tuple[Dossier, dict[str, Any] | None]:
    """조서 1호출 → `merge_inferred_facts`. 호출 오류는 호출자에게 올린다.

    `timeout_s` 가 없으면 `WRITER_NODE_TIMEOUT_SECONDS`(그래프 B 값). `llm` 이 게이트웨이면 `scope`
    가 필요하고, 합친 뒤 출력을 `node_results` 에 남긴다(timeout·재시도는 게이트웨이가 건다).
    """
    timeout = float(settings.WRITER_NODE_TIMEOUT_SECONDS) if timeout_s is None else timeout_s
    messages = _context_messages(snapshot, dossier.facts)
    scoped = None
    async with semaphore:
        if isinstance(llm, ScopedLLM):
            if scope is None:
                raise ValueError("게이트웨이 호출에는 CallScope 가 필요하다")
            scoped = await llm.scoped_call(
                scope,
                role="context",
                messages=messages,
                schema=context_schema(),
                timeout_s=timeout,
                max_output_tokens=settings.CONTEXT_MAX_OUTPUT_TOKENS,
            )
            result = scoped.result
        else:
            result = await asyncio.wait_for(
                llm.structured_call(
                    role="context",
                    messages=messages,
                    schema=context_schema(),
                    timeout_s=timeout,
                    max_output_tokens=settings.CONTEXT_MAX_OUTPUT_TOKENS,
                ),
                timeout=timeout,
            )
    merged = merge_inferred_facts(dossier, result.output)
    if scoped is not None and isinstance(llm, ScopedLLM) and isinstance(result.output, Mapping):
        await llm.remember(scoped)
    return merged


# --- 그래프 -----------------------------------------------------------------------


def build_preparation_graph(deps: PrepareDeps, run: _Run | None = None) -> Any:
    """노드 9개를 묶은 컴파일된 그래프. `run` 은 실행마다 새로 준다."""
    ctx = run or _Run()
    settings = deps.settings

    def scope(state: PrepareState, node: str, call_index: int) -> CallScope:
        return case_scope(
            state["snapshot"],
            node=node,
            call_index=call_index,
            job_id=state["job"].id,
            generation_id=deps.generation_id,
            prompt_version=deps.prompt_version,
            policy_version=settings.GUARDRAIL_POLICY_VERSION,
        )

    async def load_case(state: PrepareState) -> dict[str, Any]:
        job = state["job"]
        payload = parse_payload(job)
        assert isinstance(payload, PreparePayload)
        snapshot = await deps.backend.snapshot(job.id, deps.generation_id)
        if (
            snapshot.post_version != payload.post_version
            or snapshot.audience.audience_version != payload.audience_version
        ):
            # 구버전 이벤트. 새 이벤트가 따로 온다.
            return {"snapshot": snapshot, "status": STATUS_STALE_EVENT}
        return {"snapshot": snapshot, "status": STATUS_LOADED}

    def after_load(state: PrepareState) -> str:
        return END if state["status"] == STATUS_STALE_EVENT else "load_reusable_prep"

    async def load_reusable_prep(state: PrepareState) -> dict[str, Any]:
        """D-27: 조서 키가 맞는 유효 준비 자료가 있으면 조서를 다시 만들지 않는다."""
        snapshot = state["snapshot"]
        try:
            valid = await deps.preparation.load_valid_prep(snapshot, deps.prompt_version)
        except Exception as exc:  # 조회 실패는 처음부터 만든다.
            log.warning("reusable_prep_lookup_failed", error=type(exc).__name__)
            return {}
        if valid is None:
            return {}
        ctx.dossier = valid.dossier
        ctx.reuse_dossier = True
        ctx.copied = _copied_banter(valid.banter, snapshot)
        return {
            "dossier_id": valid.dossier.dossier_id,
            "evidence": list(valid.dossier.facts),
            "label_map": dict(valid.dossier.label_map),
        }

    def after_reuse(state: PrepareState) -> str:
        return "persist_dossier" if ctx.reuse_dossier else "recall_candidates"

    async def recall_candidates(state: PrepareState) -> dict[str, Any]:
        snapshot = state["snapshot"]
        memory = deps.memory
        if memory is None:
            return {"candidates": []}

        async def _recall() -> list[MemoryCandidate]:
            user = memory.recall_user(
                snapshot.author_id,
                snapshot.category,
                snapshot.created_at,
                settings.RECALL_CANDIDATE_LIMIT,
                reason=snapshot.reason,
            )
            rooms = [
                memory.recall_room(room_id, snapshot.category)
                for room_id in snapshot.audience.room_ids
            ]
            found, *room_recalls = await asyncio.gather(user, *rooms)
            return _merge_candidates(
                [found, *(recall.rules_hit for recall in room_recalls)],
                settings.RECALL_CANDIDATE_LIMIT,
            )

        try:
            candidates = await asyncio.wait_for(_recall(), timeout=RECALL_TIMEOUT_S)
        except Exception as exc:  # timeout 포함. 빈 후보로 계속한다.
            log.warning("recall_failed", error=type(exc).__name__)
            candidates = []
        return {"candidates": candidates}

    async def resolve_sources(state: PrepareState) -> dict[str, Any]:
        job = state["job"]
        include = evidence_include(settings)
        request = ResolveEvidenceRequest(
            candidates=[
                EvidenceCandidate(
                    source_type=c.source_type,
                    source_id=c.source_id,
                    source_version=c.source_version,
                    score=c.score,
                )
                for c in state["candidates"]
            ],
            include=include,
        )
        try:
            resolved = await deps.backend.resolve_evidence(job.id, deps.generation_id, request)
        except Exception as exc:  # 빈 응답으로 계속(F0 만).
            log.warning("resolve_evidence_failed", error=type(exc).__name__)
            resolved = None
        return {"resolved": resolved}

    async def build_db_evidence(state: PrepareState) -> dict[str, Any]:
        snapshot = state["snapshot"]
        # D-27: 조서 키를 `ai.dossiers.snapshot_hash` 에 둔다(기존 컬럼).
        dossier = replace(
            dossier_from_resolved(snapshot, state["resolved"], settings),
            snapshot_hash=dossier_key(snapshot),
        )
        ctx.dossier = dossier
        return {"evidence": list(dossier.facts), "label_map": dict(dossier.label_map)}

    async def analyze_reason(state: PrepareState) -> dict[str, Any]:
        dossier = ctx.dossier
        assert dossier is not None
        if deps.llm is None:
            return {}
        try:
            merged, reason_analysis = await call_context(
                deps.llm,
                deps.semaphore,
                settings,
                state["snapshot"],
                dossier,
                scope=scope(state, "context", 0),
            )
        except EvidenceInvalidated:
            raise  # D-26: 무효는 삼키지 않는다(핸들러가 fail 로 기록).
        except Exception as exc:  # 코드 Evidence 만으로 계속(05 §3.2).
            log.warning("context_failed", error=type(exc).__name__)
            return {"errors": [*state.get("errors", []), "CONTEXT_FAILED"]}
        ctx.dossier = merged
        return {
            "evidence": list(merged.facts),
            "label_map": dict(merged.label_map),
            "reason_analysis": reason_analysis,
        }

    async def persist_dossier(state: PrepareState) -> dict[str, Any]:
        dossier = ctx.dossier
        assert dossier is not None
        snapshot = state["snapshot"]
        saved = await deps.preparation.save_prep(
            dossier, snapshot, deps.prompt_version, reuse_dossier=ctx.reuse_dossier
        )
        ctx.prep_id = saved.prep_id
        if saved.reused:
            if saved.status != STATUS_DOSSIER_READY:
                return {"status": saved.status}
            # 같은 키의 DOSSIER_READY 행을 이어 쓴다. 라벨은 저장된 dossier 기준이다.
            valid = await deps.preparation.load_valid_prep(snapshot, deps.prompt_version)
            if valid is None:
                return {"status": "INVALIDATED"}
            ctx.dossier = valid.dossier
            ctx.copied = _copied_banter(valid.banter, snapshot)
            return {
                "status": STATUS_DOSSIER_READY,
                "dossier_id": valid.dossier.dossier_id,
                "evidence": list(valid.dossier.facts),
                "label_map": dict(valid.dossier.label_map),
            }
        return {"status": STATUS_DOSSIER_READY, "dossier_id": dossier.dossier_id}

    def after_persist(state: PrepareState) -> str:
        if state["status"] != STATUS_DOSSIER_READY or deps.llm is None:
            return END
        return "generate_banter"

    async def generate_banter(state: PrepareState) -> dict[str, Any]:
        llm = deps.llm
        dossier = ctx.dossier
        assert llm is not None and dossier is not None
        snapshot = state["snapshot"]
        allowed = target_pack(dossier, snapshot.audience.room_ids)
        schema = with_enums(banter_schema(), **{"candidates[].fits[]": list(_FITS)})
        timeout_s = float(settings.WRITER_NODE_TIMEOUT_SECONDS)

        async def one(
            intensity: Intensity, position: int
        ) -> tuple[Intensity, list[Candidate] | None]:
            scoped = None
            try:
                examples = await deps.preparation.approved_banter_examples(
                    intensity, snapshot.category, settings.STYLE_EXAMPLE_LIMIT
                )
                messages = _banter_messages(snapshot, intensity, allowed, examples)
                async with deps.semaphore:
                    if isinstance(llm, ScopedLLM):
                        scoped = await llm.scoped_call(
                            scope(state, "banter", position),
                            role="banter",
                            messages=messages,
                            schema=schema,
                            timeout_s=timeout_s,
                            max_output_tokens=settings.BANTER_MAX_OUTPUT_TOKENS,
                        )
                        result = scoped.result
                    else:
                        result = await asyncio.wait_for(
                            llm.structured_call(
                                role="banter",
                                messages=messages,
                                schema=schema,
                                timeout_s=timeout_s,
                                max_output_tokens=settings.BANTER_MAX_OUTPUT_TOKENS,
                            ),
                            timeout=timeout_s,
                        )
            except EvidenceInvalidated:
                raise  # D-26
            except Exception as exc:  # 그 강도 후보 없음.
                log.warning("banter_failed", intensity=intensity.value, error=type(exc).__name__)
                return intensity, None
            if result.output is None:
                return intensity, None
            candidates = _parse_candidates(result.output)
            if scoped is not None and isinstance(llm, ScopedLLM) and candidates:
                await llm.remember(scoped)
            return intensity, candidates

        # call_index 는 목표 강도 전체 안의 순서다(복사해 건너뛴 강도가 있어도 밀리지 않는다).
        positions = {intensity: n for n, intensity in enumerate(_target_intensities(snapshot))}
        missing = [intensity for intensity in positions if intensity not in ctx.copied]
        gathered = await asyncio.gather(
            *(one(intensity, positions[intensity]) for intensity in missing),
            return_exceptions=True,
        )
        banter: dict[Intensity, list[Candidate]] = dict(ctx.copied)
        results: list[tuple[Intensity, list[Candidate] | None]] = []
        for outcome in gathered:
            # 무효(D-26) 등은 모든 호출이 끝난 뒤 올린다(뒤에 남은 호출이 돌지 않게).
            if isinstance(outcome, BaseException):
                raise outcome
            results.append(outcome)
        for intensity, candidates in results:
            if candidates is not None:
                banter[intensity] = candidates
                ctx.banter_ok.add(intensity)
        return {"banter": banter}

    async def validate_banter(state: PrepareState) -> dict[str, Any]:
        labels = list(state["label_map"])
        dossier = ctx.dossier
        if dossier is not None:
            labels = [
                fact.label for fact in target_pack(dossier, state["snapshot"].audience.room_ids)
            ]
        return {
            "banter": {
                intensity: filter_banter(candidates, intensity, labels)
                for intensity, candidates in state["banter"].items()
            }
        }

    async def persist_banter(state: PrepareState) -> dict[str, Any]:
        if (not ctx.banter_ok and not ctx.copied) or ctx.prep_id is None:
            return {}
        try:
            done = await deps.preparation.save_banter(ctx.prep_id, state["banter"])
        except Exception as exc:  # DOSSIER_READY 유지.
            log.warning("save_banter_failed", error=type(exc).__name__)
            return {}
        return {"status": STATUS_COMPLETE} if done else {}

    def logged(
        name: str, fn: Callable[[PrepareState], Awaitable[dict[str, Any]]]
    ) -> Callable[[PrepareState], Awaitable[dict[str, Any]]]:
        """노드 한 번마다 `prepare_node` 로그(08 §3.3). 원문 없이 코드·지연만."""

        def emit(state: Mapping[str, Any], started: float, **fields: Any) -> None:
            job = state.get("job")
            instrument.node_log(
                "prepare_node",
                trace_id=getattr(job, "trace_id", None),
                job_id=getattr(job, "id", None),
                generation_id=deps.generation_id,
                graph_name="preparation",
                node=name,
                latency_ms=int((time.monotonic() - started) * 1000),
                prompt_bundle_version=deps.prompt_version,
                **fields,
            )

        async def node(state: PrepareState) -> dict[str, Any]:
            started = time.monotonic()
            try:
                update = await fn(state)
            except BaseException as exc:
                emit(state, started, ok=False, fallback_reason=type(exc).__name__)
                raise
            update = update or {}
            before = len(state.get("errors") or [])
            added = list(update.get("errors") or [])[before:]
            emit(state, started, ok=True, fallback_reason=",".join(added) if added else None)
            return update

        return node

    graph = StateGraph(PrepareState)
    for node_name, node_fn in (
        ("load_case", load_case),
        ("load_reusable_prep", load_reusable_prep),
        ("recall_candidates", recall_candidates),
        ("resolve_sources", resolve_sources),
        ("build_db_evidence", build_db_evidence),
        ("analyze_reason", analyze_reason),
        ("persist_dossier", persist_dossier),
        ("generate_banter", generate_banter),
        ("validate_banter", validate_banter),
        ("persist_banter", persist_banter),
    ):
        graph.add_node(node_name, logged(node_name, node_fn))
    graph.add_edge(START, "load_case")
    graph.add_conditional_edges("load_case", after_load, ["load_reusable_prep", END])
    graph.add_conditional_edges(
        "load_reusable_prep", after_reuse, ["recall_candidates", "persist_dossier"]
    )
    graph.add_edge("recall_candidates", "resolve_sources")
    graph.add_edge("resolve_sources", "build_db_evidence")
    graph.add_edge("build_db_evidence", "analyze_reason")
    graph.add_edge("analyze_reason", "persist_dossier")
    graph.add_conditional_edges("persist_dossier", after_persist, ["generate_banter", END])
    graph.add_edge("generate_banter", "validate_banter")
    graph.add_edge("validate_banter", "persist_banter")
    graph.add_edge("persist_banter", END)
    return graph.compile()


def _initial_state(job: Job) -> PrepareState:
    return {  # type: ignore[typeddict-item]  # snapshot 은 load_case 가 채운다
        "job": job,
        "candidates": [],
        "resolved": None,
        "evidence": [],
        "label_map": {},
        "dossier_id": None,
        "reason_analysis": None,
        "banter": {},
        "status": "",
        "errors": [],
    }


async def run_preparation(job: Job, deps: PrepareDeps) -> PrepareState:
    """그래프 B 를 한 번 돌린다. 백엔드·epoch 예외는 호출자(`PrepareHandler`)가 정리한다."""
    graph = build_preparation_graph(deps, _Run())
    return await graph.ainvoke(_initial_state(job))
