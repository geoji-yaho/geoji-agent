"""그래프 B — 사전 준비(05 §3.2, proposal2 §5.2).

`load_case → recall_candidates → resolve_sources → build_db_evidence → analyze_reason →
persist_dossier → generate_banter → validate_banter → persist_banter`

모델 호출은 조서(`context`) 1회와 드립 후보(`banter`) 강도마다 1회뿐이다. 조서 저장에 성공하면
`DOSSIER_READY`, 드립까지 되면 `COMPLETE`. **드립 실패가 조서를 되돌리지 않는다.**

실행 문맥(포트·설정·세마포어)은 `PrepareDeps` 로 받아 노드 클로저에 묶는다. state 는
`graphs.states.PrepareState` 그대로이고, state 에 자리가 없는 실행 중 값(`prep_id`, 강도별 호출
성공 여부)은 실행마다 새로 만드는 `_Run` 에 둔다.

이 모듈은 SQLAlchemy·어댑터를 import 하지 않는다.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import timedelta
from typing import Any

from langgraph.graph import END, START, StateGraph

from geoji_ai.application.build_evidence import build_evidence, target_pack
from geoji_ai.contracts.case import CaseSnapshot, VerdictResult
from geoji_ai.contracts.jobs import Job, PreparePayload, parse_payload
from geoji_ai.contracts.llm_schemas import banter_schema, context_schema, with_enums
from geoji_ai.contracts.writer import BanterStrategy
from geoji_ai.core.config import Settings
from geoji_ai.core.logging import get_logger
from geoji_ai.domain import dossier_rules
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
    "filter_banter",
    "merge_inferred_facts",
    "run_preparation",
]

log = get_logger(__name__)

CONTEXT_PROMPT = "context-v1.md"
BANTER_PROMPT = "banter-v1.md"

#: recall 전체 상한(05 §3.2 `recall_candidates` "0.5초 wait_for").
RECALL_TIMEOUT_S = 0.5

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
    llm: LLMPort | None = None
    prompt_version: str = field(default_factory=prompt_bundle_version)


@dataclass
class _Run:
    """state 에 자리가 없는 실행 중 값. 실행마다 새로 만든다."""

    dossier: Dossier | None = None
    prep_id: str | None = None
    #: 드립 호출이 성공한 강도. 하나도 없으면 `DOSSIER_READY` 유지.
    banter_ok: set[Intensity] = field(default_factory=set)


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


# --- 그래프 -----------------------------------------------------------------------


def build_preparation_graph(deps: PrepareDeps, run: _Run | None = None) -> Any:
    """노드 9개를 묶은 컴파일된 그래프. `run` 은 실행마다 새로 준다."""
    ctx = run or _Run()
    settings = deps.settings

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
        return END if state["status"] == STATUS_STALE_EVENT else "recall_candidates"

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
        include: list[EvidenceInclude] = ["rules", "aggregates", "recent_verdicts"]
        if settings.ROOM_COMMENT_STYLE_ENABLED:
            include.append("style_comments")
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
        resolved = state["resolved"]
        if resolved is None:
            dossier = build_evidence(snapshot, _f0_only(snapshot), pack_limit=1)
        else:
            dossier = build_evidence(
                snapshot,
                resolved,
                pack_limit=settings.EVIDENCE_PACK_LIMIT,
                rule_matcher=dossier_rules.rule_matcher,
            )
        ctx.dossier = dossier
        return {"evidence": list(dossier.facts), "label_map": dict(dossier.label_map)}

    async def analyze_reason(state: PrepareState) -> dict[str, Any]:
        dossier = ctx.dossier
        assert dossier is not None
        if deps.llm is None:
            return {}
        timeout_s = float(settings.WRITER_NODE_TIMEOUT_SECONDS)
        try:
            async with deps.semaphore:
                result = await asyncio.wait_for(
                    deps.llm.structured_call(
                        role="context",
                        messages=_context_messages(state["snapshot"], dossier.facts),
                        schema=context_schema(),
                        timeout_s=timeout_s,
                        max_output_tokens=settings.CONTEXT_MAX_OUTPUT_TOKENS,
                    ),
                    timeout=timeout_s,
                )
        except Exception as exc:  # 코드 Evidence 만으로 계속(05 §3.2).
            log.warning("context_failed", error=type(exc).__name__)
            return {"errors": [*state.get("errors", []), "CONTEXT_FAILED"]}
        merged, reason_analysis = merge_inferred_facts(dossier, result.output)
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
        saved = await deps.preparation.save_prep(dossier, snapshot, deps.prompt_version)
        ctx.prep_id = saved.prep_id
        if saved.reused:
            if saved.status != STATUS_DOSSIER_READY:
                return {"status": saved.status}
            # 같은 키의 DOSSIER_READY 행을 이어 쓴다. 라벨은 저장된 dossier 기준이다.
            valid = await deps.preparation.load_valid_prep(snapshot, deps.prompt_version)
            if valid is None:
                return {"status": "INVALIDATED"}
            ctx.dossier = valid.dossier
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

        async def one(intensity: Intensity) -> tuple[Intensity, list[Candidate] | None]:
            try:
                examples = await deps.preparation.approved_banter_examples(
                    intensity, snapshot.category, settings.STYLE_EXAMPLE_LIMIT
                )
                async with deps.semaphore:
                    result = await asyncio.wait_for(
                        llm.structured_call(
                            role="banter",
                            messages=_banter_messages(snapshot, intensity, allowed, examples),
                            schema=schema,
                            timeout_s=timeout_s,
                            max_output_tokens=settings.BANTER_MAX_OUTPUT_TOKENS,
                        ),
                        timeout=timeout_s,
                    )
            except Exception as exc:  # 그 강도 후보 없음.
                log.warning("banter_failed", intensity=intensity.value, error=type(exc).__name__)
                return intensity, None
            if result.output is None:
                return intensity, None
            return intensity, _parse_candidates(result.output)

        results = await asyncio.gather(*(one(i) for i in _target_intensities(snapshot)))
        banter: dict[Intensity, list[Candidate]] = {}
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
        if not ctx.banter_ok or ctx.prep_id is None:
            return {}
        try:
            done = await deps.preparation.save_banter(ctx.prep_id, state["banter"])
        except Exception as exc:  # DOSSIER_READY 유지.
            log.warning("save_banter_failed", error=type(exc).__name__)
            return {}
        return {"status": STATUS_COMPLETE} if done else {}

    graph = StateGraph(PrepareState)
    graph.add_node("load_case", load_case)
    graph.add_node("recall_candidates", recall_candidates)
    graph.add_node("resolve_sources", resolve_sources)
    graph.add_node("build_db_evidence", build_db_evidence)
    graph.add_node("analyze_reason", analyze_reason)
    graph.add_node("persist_dossier", persist_dossier)
    graph.add_node("generate_banter", generate_banter)
    graph.add_node("validate_banter", validate_banter)
    graph.add_node("persist_banter", persist_banter)
    graph.add_edge(START, "load_case")
    graph.add_conditional_edges("load_case", after_load, ["recall_candidates", END])
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
