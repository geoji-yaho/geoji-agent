"""그래프 C(선고) — 05 §1 flowchart·§3.3·§3.5.

`begin_generation → load_valid_prep → sentencing(조건) → writer(강도 fan-out) → join →
deterministic_validate → evaluator → finalize | generation_failed`.

이 모듈이 하지 않는 것(웨이브 3): `writer_repair`, `inline_context`, evaluator `hell` 별도 호출,
원장 기록(작업 6, no-op).

해석(보고서 "계획서에 반영할 것"):

- prep 이 없으면 항상 MINIMAL. `ResolveEvidenceResponse` 는 `aggregates` 가 필수라 "빈 응답" 을
  만들 수 없어, 중립 집계 + `pack_limit=MINIMAL_PACK_LIMIT(1)` 로 `F0`(THIS_CASE) 만 남긴다
- 검수 뒤 강도를 TEMPLATE 로 바꾸거나(보고서 누락·`pass=false`) 양형 이유를 템플릿으로 바꾸면
  hash 가 바뀐다. 검수 없는 저장을 막으려고 바뀐 draft 를 **한 번 더** 검수한다(최대 2호출).
  두 번째도 통과하지 못하면 `EVAL_FAILED`
- `Deadline.from_db` 의 `db_now` 는 begin 응답에 없다. 의존성 `db_now()` 로 받는다(질문)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal, Protocol

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from geoji_ai.application.build_evidence import build_evidence
from geoji_ai.contracts.case import CaseSnapshot, JurySnapshot
from geoji_ai.contracts.evaluation import EvaluationReport
from geoji_ai.contracts.finalize import FinalizeRequest, ModelIds
from geoji_ai.contracts.jobs import Job, SentencePayload, TextRetryPayload, parse_payload
from geoji_ai.contracts.llm_schemas import evaluator_schema, sentencing_schema, writer_schema
from geoji_ai.contracts.sentencing import SentencingDecision
from geoji_ai.contracts.writer import MemeHints, TextDraft, WriterDraft
from geoji_ai.domain.attack_angles import ANGLE_GUIDES, AttackAngle, pick
from geoji_ai.domain.budget import EVALUATOR, SENTENCING, WRITER, Deadline
from geoji_ai.domain.draft_hash import draft_hash
from geoji_ai.domain.intensity import Intensity
from geoji_ai.domain.validation import apply_text_rules, validate_evaluation
from geoji_ai.graphs.states import CallRecord, Candidate, SentenceState
from geoji_ai.graphs.templates import (
    TemplateUnavailable,
    sentencing_reason_template,
    template_text_draft,
)
from geoji_ai.ports.backend import Aggregates, BackendPort, ResolveEvidenceResponse
from geoji_ai.ports.llm import LLMError, LLMPort, LLMResult, LLMRole
from geoji_ai.ports.preparation import Dossier
from geoji_ai.prompts import build_writer_system, load_prompt, prompt_bundle_version

__all__ = [
    "MINIMAL_PACK_LIMIT",
    "SENTENCING_REASON_MAX",
    "SentenceDeps",
    "SentenceGraphState",
    "ValidPrepLike",
    "build_sentence_graph",
    "initial_state",
    "minimal_dossier",
]

logger = logging.getLogger(__name__)

#: `SentencingDecision.sentencing_reason` 상한(01 §3.2, `contracts/sentencing.py`).
SENTENCING_REASON_MAX = 100
#: MINIMAL 조서의 근거 수. 백엔드 근거 없이 코드가 만들 수 있는 것은 F0 뿐이다.
MINIMAL_PACK_LIMIT = 1

GUILTY = "guilty"
SPENT = "spent"

# 생성 실패 코드(03 §1 = 10 §4.6)
VENDOR_UNAVAILABLE = "VENDOR_UNAVAILABLE"
EVAL_FAILED = "EVAL_FAILED"
SCHEMA_INVALID = "SCHEMA_INVALID"
DEADLINE_EXCEEDED = "DEADLINE_EXCEEDED"
EVIDENCE_INVALIDATED = "EVIDENCE_INVALIDATED"

# 백엔드 거부 코드(10 §5)
_STALE_GENERATION = "STALE_GENERATION"
_DISCARD_ON_FINALIZE = frozenset({_STALE_GENERATION, DEADLINE_EXCEEDED})

_TEXT_PATH = re.compile(r"^texts\[(\d+)\]")
_REASON_CHECK = "sentencing_reason_check"
#: 검수 보고서 이슈 중 강도 하나로 좁혀지는 것(`domain/validation.py` 코드). 나머지는 전역 실패.
_PER_TEXT_CODES = frozenset(
    {"MISSING_INTENSITY", "MISSING_CHECK", "PASS_NOT_BOOLEAN", "CHECK_FAILED"}
)


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


class ValidPrepLike(Protocol):
    """`ports.preparation.ValidPrep`(feat-05-graph-b) 의 구조."""

    dossier: Dossier
    banter: Mapping[Intensity, list[Candidate]]


class PreparationLike(Protocol):
    async def load_valid_prep(
        self, snapshot: CaseSnapshot, prompt_version: str
    ) -> ValidPrepLike | None: ...


@dataclass
class SentenceDeps:
    backend: BackendPort
    llm: LLMPort
    semaphore: asyncio.Semaphore
    settings: Any
    generation_id: str
    #: DB 가 준 현재 시각. `Deadline.from_db(deadline_at, db_now)` 에 쓴다.
    db_now: Callable[[], Awaitable[datetime]]
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
    )


# ---------------------------------------------------------------------------
# 순수 도우미
# ---------------------------------------------------------------------------


def minimal_dossier(snapshot: CaseSnapshot) -> Dossier:
    """코드 Evidence 만의 조서(F0). 저장하지 않는다."""
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


def _text_index(path: str) -> int | None:
    match = _TEXT_PATH.match(path)
    return int(match.group(1)) if match else None


# ---------------------------------------------------------------------------
# 그래프
# ---------------------------------------------------------------------------


def build_sentence_graph(deps: SentenceDeps) -> Any:
    """그래프 C 를 컴파일한다. 실행마다 새로 만들어도 된다(체크포인터 없음)."""
    settings = deps.settings

    async def call_model(
        node: str,
        deadline: Deadline,
        *,
        role: LLMRole,
        messages: list[dict],
        schema: dict,
        max_output_tokens: int,
    ) -> LLMResult | None:
        """세마포어 안에서 예산을 계산한다. 예산이 없으면 호출하지 않고 None."""
        async with deps.semaphore:
            timeout = deadline.node_timeout(node, settings)
            if timeout is None:
                return None
            return await asyncio.wait_for(
                deps.llm.structured_call(
                    role=role,
                    messages=messages,
                    schema=schema,
                    timeout_s=timeout,
                    max_output_tokens=max_output_tokens,
                ),
                timeout,
            )

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
        targets = _targets(jury)
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
            rules = apply_text_rules(body, dossier.label_map, sentencing, jury)
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

    # --- 노드 ---------------------------------------------------------------

    async def begin_generation(state: SentenceGraphState) -> dict[str, Any]:
        payload = _payload(state)
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
        update: dict[str, Any] = {"begin": begin, "deadline": deadline}
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
        if prep is None:
            return {"dossier": minimal_dossier(snapshot), "dossier_source": "MINIMAL", "banter": {}}
        return {"dossier": prep.dossier, "dossier_source": "PREP", "banter": dict(prep.banter)}

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
            result = await call_model(
                SENTENCING,
                state["deadline"],
                role="sentencing",
                messages=messages,
                schema=schema,
                max_output_tokens=settings.SENTENCING_MAX_OUTPUT_TOKENS,
            )
            if result is None:
                logger.info("양형 예산 없음 → RULE")
                return {**rule_sentencing(jury), "calls": calls}
            decision = parse_sentencing(result.output, jury)
        except (LLMError, TimeoutError, ValueError) as exc:
            calls.append(CallRecord("sentencing", None, settings.MODEL_JUDGMENT, _error_name(exc)))
            logger.warning("양형관 실패 %s → RULE", _error_name(exc))
            return {**rule_sentencing(jury), "calls": calls}
        calls.append(CallRecord("sentencing", None, result.model_id, None))
        return {"sentencing": decision, "sentencing_source": "AI", "calls": calls}

    async def writer(state: SentenceGraphState) -> dict[str, Any]:
        jury = _jury(state)
        snapshot = state["snapshot"]
        decision: SentencingDecision | None = state.get("sentencing")
        angle: AttackAngle = pick(snapshot.post_id, state.get("repair_count", 0))
        guide = ANGLE_GUIDES[angle]
        banter: Mapping[Intensity, Sequence[Candidate]] = state.get("banter") or {}

        async def write_one(intensity: Intensity) -> tuple[str, Any, CallRecord | None]:
            candidates = [c for c in banter.get(intensity, []) if str(jury.result) in c.fits]
            candidate_ids = [c.candidate_id for c in candidates]
            messages = [
                {"role": "system", "content": build_writer_system(intensity)},
                {
                    "role": "user",
                    "content": _dumps(
                        {
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
                    ),
                },
            ]
            schema = writer_schema([intensity.value], [angle.value], candidate_ids or None)
            try:
                result = await call_model(
                    WRITER,
                    state["deadline"],
                    role="writer",
                    messages=messages,
                    schema=schema,
                    max_output_tokens=settings.WRITER_MAX_OUTPUT_TOKENS,
                )
                if result is None:
                    return ("SKIPPED", None, None)
                if result.output is None:
                    raise ValueError(f"서기 출력 없음({result.stop_reason})")
                output = dict(result.output)
                hints_raw = output.pop("meme_hints", None)
                output.pop("meme_tag", None)
                text = TextDraft.model_validate({**output, "source": "AI"})
                if text.intensity != intensity or text.attack_angle != angle:
                    raise ValueError("서버 지정 강도·각도와 다르다")
                hints = MemeHints.model_validate(hints_raw) if hints_raw is not None else None
            except (LLMError, TimeoutError, ValueError) as exc:
                record = CallRecord("writer", intensity, settings.MODEL_WRITER, _error_name(exc))
                return ("FAILED", None, record)
            return ("AI", (text, hints), CallRecord("writer", intensity, result.model_id, None))

        targets = _targets(jury)
        outcomes = await asyncio.gather(*(write_one(i) for i in targets))

        calls = list(state["calls"])
        drafts: dict[Intensity, TextDraft] = {}
        sources: dict[Intensity, Literal["AI", "TEMPLATE"]] = {}
        hints: MemeHints | None = None
        skipped = False
        for intensity, (kind, value, record) in zip(targets, outcomes, strict=True):
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
            template = template_for(intensity, jury, decision, snapshot.post_id)
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

    async def join(state: SentenceGraphState) -> dict[str, Any]:
        targets = _targets(_jury(state))
        sources = state.get("draft_sources") or {}
        if set(state.get("drafts") or {}) == set(targets) and any(
            sources.get(i) == "AI" for i in targets
        ):
            return {"failure": None}
        code = DEADLINE_EXCEEDED if state.get("writer_budget_skipped") else VENDOR_UNAVAILABLE
        return {"failure": code}

    async def deterministic_validate(state: SentenceGraphState) -> dict[str, Any]:
        result = assemble(state, state["drafts"], state["draft_sources"], state.get("sentencing"))
        if "drafts" in result:
            result["validation"] = {i: [] for i in result["drafts"]}
        return result

    async def evaluator(state: SentenceGraphState) -> dict[str, Any]:
        jury = _jury(state)
        targets = _targets(jury)
        policy_version = settings.GUARDRAIL_POLICY_VERSION
        calls = list(state["calls"])
        writer_draft: WriterDraft = state["writer_draft"]
        drafts = dict(state["drafts"])
        sources = dict(state["draft_sources"])
        decision: SentencingDecision | None = state.get("sentencing")
        evidence = (
            {} if state["dossier"] is None else {f.label: f.text for f in state["dossier"].facts}
        )
        all_template = {i: "TEMPLATE" for i in targets}

        for attempt in range(2):
            current_hash = draft_hash(writer_draft, decision)
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
                            "draft": writer_draft.model_dump(mode="json", by_alias=True),
                            "evidence": evidence,
                        }
                    ),
                },
            ]
            try:
                result = await call_model(
                    EVALUATOR,
                    state["deadline"],
                    role="evaluator",
                    messages=messages,
                    schema=evaluator_schema([i.value for i in targets]),
                    max_output_tokens=settings.EVALUATOR_MAX_OUTPUT_TOKENS,
                )
                if result is None:
                    logger.info("검수 예산 없음 → 검수 미시작")
                    return {"calls": calls, "failure": DEADLINE_EXCEEDED}
                if result.output is None:
                    raise ValueError(f"검수관 출력 없음({result.stop_reason})")
            except (LLMError, TimeoutError, ValueError) as exc:
                calls.append(
                    CallRecord("evaluator", None, settings.MODEL_JUDGMENT, _error_name(exc))
                )
                logger.warning("검수관 오류 %s → 전 강도 TEMPLATE", _error_name(exc))
                return {"calls": calls, "draft_sources": all_template, "failure": EVAL_FAILED}
            calls.append(CallRecord("evaluator", None, result.model_id, None))

            report = {**result.output, "schema_version": 1, "policy_version": policy_version}
            issues = validate_evaluation(report, targets, policy_version)
            if not issues:
                return {
                    "calls": calls,
                    "evaluation": EvaluationReport.model_validate(report),
                    "draft_hash": current_hash,
                    # D-19 치환이 있었으면 검수한 형량(이유만 바뀜)을 finalize 로 넘긴다.
                    "sentencing": decision,
                    "writer_draft": writer_draft,
                    "drafts": drafts,
                    "draft_sources": sources,
                    "failure": None,
                }
            if attempt == 1:
                logger.warning("재검수도 통과하지 못했다: %s", [i.code for i in issues])
                return {"calls": calls, "draft_sources": all_template, "failure": EVAL_FAILED}

            # 보고서 완전성·pass 로 강도를 가른다. 그 밖의 실패는 전역 실패다.
            entries = {
                entry.get("intensity"): entry
                for entry in report.get("texts") or []
                if isinstance(entry, Mapping)
            }
            bad = [i for i in targets if entries.get(i.value, {}).get("pass") is not True]
            reason_failed = False
            for issue in issues:
                if issue.path.startswith(_REASON_CHECK):
                    reason_failed = True
                elif not (issue.path.startswith("texts") and issue.code in _PER_TEXT_CODES):
                    logger.warning("검수 전역 실패: %s %s", issue.code, issue.path)
                    return {"calls": calls, "draft_sources": all_template, "failure": EVAL_FAILED}
            if reason_failed:
                substituted = (
                    None
                    if decision is None
                    else sentencing_reason_template(jury, decision.sentence, deps.templates)
                )
                if decision is None or substituted is None:
                    return {"calls": calls, "draft_sources": all_template, "failure": EVAL_FAILED}
                decision = decision.model_copy(
                    update={"sentencing_reason": substituted, "reason_source": "TEMPLATE"}
                )
            for intensity in bad:
                if sources.get(intensity) == "TEMPLATE":
                    return {"calls": calls, "draft_sources": all_template, "failure": EVAL_FAILED}
                template = template_for(intensity, jury, decision, state["snapshot"].post_id)
                if template is None:
                    return {"calls": calls, "draft_sources": all_template, "failure": EVAL_FAILED}
                drafts[intensity] = template
                sources[intensity] = "TEMPLATE"
            rebuilt = assemble({**state, "drafts": drafts}, drafts, sources, decision)
            if rebuilt.get("failure") is not None:
                return {"calls": calls, "draft_sources": all_template, "failure": EVAL_FAILED}
            writer_draft = rebuilt["writer_draft"]
            drafts = rebuilt["drafts"]
            sources = rebuilt["draft_sources"]

        return {"calls": calls, "draft_sources": all_template, "failure": EVAL_FAILED}

    async def finalize(state: SentenceGraphState) -> dict[str, Any]:
        payload = _payload(state)
        decision = state.get("sentencing")
        writer_draft: WriterDraft = state["writer_draft"]
        evaluated_hash = state["draft_hash"]
        if evaluated_hash is None or state.get("evaluation") is None:
            raise RuntimeError("검수 없이 finalize 할 수 없다")
        # 검수 뒤 문구·형량이 바뀌었으면 여기서 멈춘다(백엔드가 422 로 거부할 요청이다).
        if draft_hash(writer_draft, decision) != evaluated_hash:
            raise RuntimeError("검수 대상 hash 와 finalize draft hash 가 다르다")
        request = FinalizeRequest(
            schema_version=1,
            job_id=state["job"].id,
            generation_id=deps.generation_id,
            verdict_version=payload.verdict_version,
            expected_text_version=state["begin"].text_version,
            dossier_id=state["dossier"].dossier_id,
            privacy_versions=state["snapshot"].privacy_versions,
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
                return {"failure": None}
            if status == 409 and code == EVIDENCE_INVALIDATED:
                return {"failure": EVIDENCE_INVALIDATED}
            if status == 422:
                return {"failure": SCHEMA_INVALID}
            raise
        return {"failure": None}

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
        return "load_valid_prep" if state.get("begin") is not None else END

    def after_prep(state: SentenceGraphState) -> str:
        jury = _jury(state)
        needs = (
            state["mode"] == "INITIAL"
            and state.get("sentencing_source") != "FIXED"
            and str(jury.result) == GUILTY
            and state["snapshot"].post_type == SPENT
        )
        return "sentencing" if needs else "writer"

    def failed_or(next_node: str) -> Callable[[SentenceGraphState], str]:
        def route(state: SentenceGraphState) -> str:
            return "generation_failed" if state.get("failure") else next_node

        return route

    graph = StateGraph(SentenceGraphState)
    graph.add_node("begin_generation", begin_generation)
    graph.add_node("load_valid_prep", load_valid_prep)
    graph.add_node("sentencing", sentencing)
    graph.add_node("writer", writer)
    graph.add_node("join", join)
    graph.add_node("deterministic_validate", deterministic_validate)
    graph.add_node("evaluator", evaluator)
    graph.add_node("finalize", finalize)
    graph.add_node("generation_failed", generation_failed)

    graph.add_edge(START, "begin_generation")
    graph.add_conditional_edges("begin_generation", after_begin, ["load_valid_prep", END])
    graph.add_conditional_edges("load_valid_prep", after_prep, ["sentencing", "writer"])
    graph.add_edge("sentencing", "writer")
    graph.add_edge("writer", "join")
    graph.add_conditional_edges(
        "join", failed_or("deterministic_validate"), ["deterministic_validate", "generation_failed"]
    )
    graph.add_conditional_edges(
        "deterministic_validate", failed_or("evaluator"), ["evaluator", "generation_failed"]
    )
    graph.add_conditional_edges(
        "evaluator", failed_or("finalize"), ["finalize", "generation_failed"]
    )
    graph.add_conditional_edges("finalize", failed_or(END), [END, "generation_failed"])
    graph.add_edge("generation_failed", END)
    return graph.compile()
