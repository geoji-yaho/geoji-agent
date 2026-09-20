"""골든셋 회귀 평가기(06 §3.4, RM-05).

    GEOJI_EVAL=1 uv run python -m tests.evaluations.run_regression \
        [--dry-run] [--quick] [--baseline PATH] [--write-baseline PATH] \
        [--policy guardrail-v1|guardrail-v2] [--writer-model M] [--evaluator-hell-model M] \
        [--temperature T] [--only-hell] [--only-thin-evidence] [--concurrency 4] [--out PATH]

사건마다 고정 dossier·banter·형량(사건 데이터)을 넣고 그래프 C 를 돌린다. 모델은 **서기·검수관만**
부른다(조서·드립·양형 0).

- 조서·드립은 메모리 준비 포트의 `load_valid_prep` 이 사건 데이터로 돌려준다(PREP 경로)
- 형량은 메모리 백엔드의 `begin-generation` 이 `fixed_sentencing` 으로 돌려준다(양형관 생략 경로).
  유죄·지출이 아니면 형량이 없어 원래 양형관을 부르지 않는다
- 백엔드·준비 포트는 이 파일의 메모리 fake 다. 실제 백엔드를 부르지 않는다
- `--quick`: judge·검수관 생략. 서버 검증 ⑤ 직후 초안을 채점한다
- `--dry-run`: `FakeLLM`. 키·네트워크·`.env` 를 쓰지 않는다
- `--only-thin-evidence`: 조서가 `F0` 하나뿐인 빈 방 사건만 돌린다(옵트인). 기본 실행 분모
  (`cases.jsonl` 30 + `hell_boundary.jsonl` 20 = 50)는 그대로다. `--only-hell` 과 같이 줄 수 없다
- judge 모델은 `MODEL_JUDGMENT`(새 설정 키 없음)

`GEOJI_EVAL=1` 이 없으면 코드 1 로 끝난다. 리포트가 나오면 판정과 무관하게 0 이다(판정은 리포트에).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from geoji_ai.contracts.case import CaseSnapshot, JurySnapshot, VerdictResult
from geoji_ai.contracts.finalize import FinalizeRequest
from geoji_ai.contracts.jobs import Job
from geoji_ai.contracts.llm_schemas import evaluator_schema
from geoji_ai.contracts.sentencing import SentencingDecision
from geoji_ai.contracts.writer import STATEMENT_TOTAL_MAX, BanterStrategy
from geoji_ai.core.config import Settings
from geoji_ai.domain.intensity import Intensity
from geoji_ai.domain.visibility import Scope
from geoji_ai.graphs.sentencing import SentenceDeps, build_sentence_graph, initial_state
from geoji_ai.graphs.states import Candidate
from geoji_ai.ports.backend import BeginGenerationResult, FinalizeResult, FixedSentencing
from geoji_ai.ports.llm import LLMPort, LLMResult
from geoji_ai.ports.preparation import EVIDENCE_TEXT_MAX, Dossier, EvidenceFact
from geoji_ai.prompts import build_evaluator_system, prompt_bundle_version
from tests.evaluations import checks, judge
from tests.evaluations.report import (
    PolicyCheck,
    RegressionVerdict,
    ReportRow,
    RunSummary,
    judge_regression,
    render_report,
)

__all__ = [
    "CASES_PATH",
    "HELL_PATH",
    "TAGS",
    "THIN_PATH",
    "CaseRun",
    "CountingLLM",
    "Evaluation",
    "GoldenCase",
    "MemoryBackend",
    "MemoryPreparation",
    "compare_policy_report",
    "evaluate",
    "load_cases",
    "main",
    "run_case",
    "run_policy_fixture",
]

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
CASES_PATH = GOLDEN_DIR / "cases.jsonl"
HELL_PATH = GOLDEN_DIR / "hell_boundary.jsonl"
#: 빈 방 사건(조서가 `F0` 하나). 기본 실행 밖 옵트인 — `--only-thin-evidence` 로만 돈다.
THIN_PATH = GOLDEN_DIR / "thin_evidence.jsonl"
FIXTURES_DIR = Path(__file__).resolve().parents[2] / "contracts" / "fixtures"

#: 사건 태그. 06 §3.4 분포 태그 5종 + 지옥맛 경계 태그 + 빈 방 태그.
TAGS: frozenset[str] = frozenset(
    {
        "repeat",
        "mitigating",
        "injection",
        "rule_hit",
        "null_candidate",
        "hell_boundary",
        "identity_bait",
        "death_bait",
        "profanity_bait",
        "thin_evidence",
    }
)

_GUILTY = "guilty"
_SPENT = "spent"
_THIS_CASE = "THIS_CASE"


# ---------------------------------------------------------------------------
# 데이터
# ---------------------------------------------------------------------------


class FactRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(pattern=r"^F\d+$")
    kind: str
    text: str = Field(min_length=1, max_length=EVIDENCE_TEXT_MAX)


class DossierRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dossier_id: str
    facts: list[FactRow] = Field(min_length=1)
    label_map: dict[str, str]

    @model_validator(mode="after")
    def _labels(self) -> DossierRow:
        labels = [f.label for f in self.facts]
        if len(set(labels)) != len(labels) or set(labels) != set(self.label_map):
            raise ValueError("facts 라벨과 label_map 키가 다르다")
        if self.facts[0].label != "F0" or self.facts[0].kind != _THIS_CASE:
            raise ValueError("첫 근거는 F0 THIS_CASE 여야 한다")
        return self


class CandidateRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    text: str = Field(min_length=1, max_length=STATEMENT_TOTAL_MAX)
    strategy: BanterStrategy
    fits: list[VerdictResult] = Field(min_length=1)
    evidence_labels: list[str]


class ExpectRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    must_cite_any: list[str]
    must_not_contain: list[str]
    strategy_in: list[BanterStrategy] = Field(min_length=1)


class GoldenCase(BaseModel):
    """골든 사건 한 행. `snapshot.jury` 는 null 이고 `jury` 를 따로 둔다(합치면 `full_snapshot`)."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    tags: list[str]
    snapshot: CaseSnapshot
    jury: JurySnapshot
    sentencing: FixedSentencing | None
    dossier: DossierRow
    banter: dict[Intensity, list[CandidateRow]]
    expect: ExpectRow

    @model_validator(mode="after")
    def _consistent(self) -> GoldenCase:
        if self.snapshot.jury is not None:
            raise ValueError("snapshot.jury 는 null 이고 jury 를 따로 둔다")
        unknown = set(self.tags) - TAGS
        if unknown:
            raise ValueError(f"모르는 태그 {sorted(unknown)}")
        CaseSnapshot.model_validate(self.full_snapshot.model_dump(mode="json"))
        needs = str(self.jury.result) == _GUILTY and self.snapshot.post_type == _SPENT
        if needs != (self.sentencing is not None):
            raise ValueError("형량은 유죄·지출 사건에만 있다")
        if self.sentencing is not None:
            self.decision()  # 이유 길이 등 양형 계약
        labels = set(self.dossier.label_map)
        cited = set(self.expect.must_cite_any) | {
            label for rows in self.banter.values() for row in rows for label in row.evidence_labels
        }
        if not cited <= labels:
            raise ValueError(f"label_map 에 없는 라벨 {sorted(cited - labels)}")
        if not set(self.banter) <= set(self.jury.target_intensities):
            raise ValueError("target_intensities 밖 강도의 드립 후보가 있다")
        return self

    @property
    def full_snapshot(self) -> CaseSnapshot:
        return self.snapshot.model_copy(update={"jury": self.jury})

    def decision(self) -> SentencingDecision | None:
        if self.sentencing is None:
            return None
        return SentencingDecision(
            schema_version=1,
            sentence=self.sentencing.sentence,
            sentencing_reason=self.sentencing.sentencing_reason,
            reason_source=self.sentencing.reason_source,
            evidence_labels=[],
            aggravating=[],
            mitigating=[],
        )

    def case_expect(self) -> checks.CaseExpect:
        return checks.CaseExpect(
            must_cite_any=tuple(self.expect.must_cite_any),
            must_not_contain=tuple(self.expect.must_not_contain),
            strategy_in=tuple(str(s) for s in self.expect.strategy_in),
        )


def load_cases(path: Path) -> list[GoldenCase]:
    cases: list[GoldenCase] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            cases.append(GoldenCase.model_validate_json(line))
        except ValueError as exc:
            raise ValueError(f"{path.name}:{lineno} {exc}") from exc
    return cases


# 골든셋 kind는 작성 당시 문서 분류다. 운영 EvidenceFact의 타입으로 명시 변환한다.
# 분석은 DB 사실로 승격하지 않는다. fixture 원문과 라벨은 회귀 대조를 위해 유지한다.
_FACT_TYPES: dict[str, tuple[str, str]] = {
    "THIS_CASE": ("USER_CLAIM", "SPEND"),
    "PATTERN": ("DB_RECORD", "SPEND"),
    "PRIOR_REASON": ("DB_RECORD", "SPEND"),
    "PRIOR_VERDICT": ("DB_RECORD", "VERDICT"),
    "RULE_HIT": ("DB_RECORD", "RULE"),
    "STATUS": ("DB_RECORD", "AGGREGATE"),
    "REASON_ANALYSIS": ("MODEL_INFERENCE", "MITIGATION"),
    "MITIGATION": ("MODEL_INFERENCE", "MITIGATION"),
}


def to_dossier(case: GoldenCase) -> Dossier:
    unknown = {f.kind for f in case.dossier.facts} - _FACT_TYPES.keys()
    if unknown:
        raise ValueError(f"운영 근거 타입 매핑이 없는 kind: {sorted(unknown)}")
    scope = Scope("ROOMS", frozenset(case.snapshot.audience.room_ids))
    return Dossier(
        dossier_id=case.dossier.dossier_id,
        post_id=case.snapshot.post_id,
        # 메모리 준비 포트라 저장 대조를 하지 않는다.
        snapshot_hash="0" * 64,
        facts=tuple(
            EvidenceFact(
                label=f.label,
                epistemic_type=_FACT_TYPES[f.kind][0],
                fact_type=_FACT_TYPES[f.kind][1],
                text=f.text,
                scope=scope,
                aggregation=None,
                occurred_at=None,
                sources=(),
            )
            for f in case.dossier.facts
        ),
        label_map=dict(case.dossier.label_map),
        privacy_versions=tuple((pv.scope_key, pv.epoch) for pv in case.snapshot.privacy_versions),
    )


def to_banter(case: GoldenCase) -> dict[Intensity, list[Candidate]]:
    return {
        Intensity(intensity): [
            Candidate(
                candidate_id=row.candidate_id,
                text=row.text,
                strategy=row.strategy,
                fits=tuple(str(fit) for fit in row.fits),
                evidence_labels=tuple(row.evidence_labels),
            )
            for row in rows
        ]
        for intensity, rows in case.banter.items()
    }


# ---------------------------------------------------------------------------
# 메모리 포트·모델 래퍼
# ---------------------------------------------------------------------------


class MemoryBackend:
    """`BackendPort` 메모리 fake. 형량은 사건 데이터의 고정 형량."""

    def __init__(self, case: GoldenCase) -> None:
        self.case = case
        self.calls: list[str] = []
        self.finalized: list[FinalizeRequest] = []
        self.failed: list[str] = []

    async def snapshot(self, job_id: str, generation_id: str) -> CaseSnapshot:
        self.calls.append("snapshot")
        return self.case.full_snapshot

    async def resolve_evidence(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append("resolve_evidence")
        raise RuntimeError("고정 dossier 평가에서는 resolve-evidence 를 부르지 않는다")

    async def begin_generation(
        self, verdict_id: str, *, job_id: str, generation_id: str, verdict_version: int
    ) -> BeginGenerationResult:
        self.calls.append("begin_generation")
        fixed = self.case.sentencing
        return BeginGenerationResult(
            fixed_sentencing=fixed,
            text_version=0,
            deadline_at=self.case.jury.deadline_at,
        )

    async def finalize(self, verdict_id: str, req: FinalizeRequest) -> FinalizeResult:
        self.calls.append("finalize")
        self.finalized.append(req)
        return FinalizeResult(
            verdict_id=verdict_id, text_version=1, committed_at=self.case.jury.deadline_at
        )

    async def generation_failed(
        self, verdict_id: str, *, job_id: str, generation_id: str, error_code: str
    ) -> None:
        self.calls.append("generation_failed")
        self.failed.append(error_code)


@dataclass(frozen=True)
class _Prep:
    dossier: Dossier
    banter: dict[Intensity, list[Candidate]]


class MemoryPreparation:
    """준비 포트 메모리 fake. 조서·드립은 사건 데이터."""

    def __init__(self, case: GoldenCase) -> None:
        self._prep = _Prep(to_dossier(case), to_banter(case))

    async def load_valid_prep(self, snapshot: CaseSnapshot, prompt_version: str) -> _Prep:
        return self._prep

    async def save_dossier(self, dossier: Dossier) -> str:
        return dossier.dossier_id

    async def stale_scopes(self, privacy_versions: Any) -> list[str]:
        return []


class _EvaluatorSkipped(Exception):
    """`--quick` 에서 검수관 호출이 시작되려 할 때. 모델을 부르지 않는다."""


def _schema_intensities(schema: Mapping[str, Any]) -> list[str] | None:
    try:
        return list(schema["properties"]["texts"]["items"]["properties"]["intensity"]["enum"])
    except (KeyError, TypeError):
        return None


class CountingLLM:
    """`LLMPort` 래퍼. 역할을 세고, `--quick` 이면 검수관을 막고, hell 검수 모델을 고른다.

    그래프 C 는 게이트웨이 없이 부르면 `model_override` 를 넘기지 않는다. 그래서 hell 만 담은 검수
    호출(`MODEL_EVALUATOR_HELL ≠ MODEL_JUDGMENT` 일 때 그래프가 나눈 호출)을 스키마의 강도 enum 으로
    알아보고 라우터에 `model_override` 를 준다. 그래프 `evaluate` 의 `is_hell` 과 같은 조건이다.
    """

    def __init__(
        self, base: LLMPort, *, hell_model: str | None = None, skip_evaluator: bool = False
    ) -> None:
        self.base = base
        self.hell_model = hell_model
        self.skip_evaluator = skip_evaluator
        self.roles: list[str] = []

    async def structured_call(
        self,
        *,
        role: Any,
        messages: list[dict],
        schema: dict,
        timeout_s: float,
        max_output_tokens: int,
    ) -> LLMResult:
        if role == "evaluator" and self.skip_evaluator:
            raise _EvaluatorSkipped
        self.roles.append(str(role))
        kwargs: dict[str, Any] = {
            "role": role,
            "messages": messages,
            "schema": schema,
            "timeout_s": timeout_s,
            "max_output_tokens": max_output_tokens,
        }
        if (
            role == "evaluator"
            and self.hell_model is not None
            and _schema_intensities(schema) == [Intensity.hell.value]
        ):
            kwargs["model_override"] = self.hell_model
        return await self.base.structured_call(**kwargs)


# ---------------------------------------------------------------------------
# 사건 실행
# ---------------------------------------------------------------------------


@dataclass
class CaseRun:
    case: GoldenCase
    draft: dict[str, Any] | None
    sentencing: dict[str, Any] | None
    evaluation: dict[str, Any] | None
    failure: str | None
    roles: list[str]
    backend_calls: list[str]


def _job(case: GoldenCase) -> Job:
    jury = case.jury
    return Job(
        id=f"job-golden-{case.case_id}",
        event_id=f"event-golden-{case.case_id}",
        event_type="verdict.confirmed",
        kind="SENTENCE",
        dedupe_key=f"SENTENCE:{jury.verdict_id}",
        aggregate_id=jury.verdict_id,
        aggregate_version=jury.verdict_version,
        schema_version=1,
        payload={
            "verdict_id": jury.verdict_id,
            "verdict_version": jury.verdict_version,
            "post_id": case.snapshot.post_id,
        },
        status="RUNNING",
        priority=0,
        attempts=1,
        max_attempts=1,
        available_at=jury.confirmed_at,
        deadline_at=jury.deadline_at,
        lease_until=None,
        owner_id="golden-eval",
        generation_id=f"golden-{case.case_id}",
        last_error_code=None,
        trace_id=f"trace-golden-{case.case_id}",
        created_at=jury.confirmed_at,
        updated_at=jury.confirmed_at,
    )


def _dump(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", by_alias=True)
    return dict(value)


async def run_case(
    case: GoldenCase,
    llm: LLMPort,
    settings: Settings,
    *,
    quick: bool = False,
    hell_model: str | None = None,
) -> CaseRun:
    """그래프 C 를 사건 하나로 돌린다. `quick` 이면 서버 검증 ⑤ 뒤에서 멈춘다."""
    backend = MemoryBackend(case)
    counting = CountingLLM(llm, hell_model=hell_model, skip_evaluator=quick)

    async def db_now() -> datetime:
        # 확정 시각을 DB 시각으로 쓴다. 남은 시간 = jury.deadline_at − confirmed_at.
        return case.jury.confirmed_at

    deps = SentenceDeps(
        backend=backend,
        llm=counting,
        semaphore=asyncio.Semaphore(settings.MODEL_CONCURRENCY_LIMIT),
        settings=settings,
        generation_id=f"golden-{case.case_id}",
        db_now=db_now,
        preparation=MemoryPreparation(case),
    )
    graph = build_sentence_graph(deps)
    state: dict[str, Any] = {}
    stream = graph.astream(
        initial_state(_job(case), "INITIAL", case.full_snapshot), stream_mode="updates"
    )
    try:
        async for chunk in stream:
            stop = False
            for node, update in chunk.items():
                if update:
                    state.update(update)
                stop = stop or (quick and node == "deterministic_validate")
            if stop:
                break
    except _EvaluatorSkipped:
        pass
    finally:
        await stream.aclose()

    failure = state.get("failure") or (backend.failed[-1] if backend.failed else None)
    if quick:
        draft = None if failure else _dump(state.get("writer_draft"))
        sentencing = _dump(state.get("sentencing"))
        evaluation = None
    elif backend.finalized:
        request = backend.finalized[-1]
        draft = _dump(request.draft)
        sentencing = _dump(request.sentencing)
        evaluation = _dump(request.evaluation)
        failure = None
    else:
        draft, evaluation = None, None
        sentencing = _dump(state.get("sentencing"))
        failure = failure or "NOT_FINALIZED"
    return CaseRun(
        case=case,
        draft=draft,
        sentencing=sentencing,
        evaluation=evaluation,
        failure=failure,
        roles=list(counting.roles),
        backend_calls=list(backend.calls),
    )


# ---------------------------------------------------------------------------
# 정책 fixture(01 부록 A)
# ---------------------------------------------------------------------------


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / f"{name}.json").read_text(encoding="utf-8"))


def expected_policy_report(policy: str) -> tuple[dict[str, Any], list[str]]:
    """정책 버전별 기대 보고서와 열린 질문. v2 파일은 `{"expected", "open_questions"}` 로 감쌌다."""
    data = _fixture(f"taxi-hell-expected-evaluation.{policy}")
    if "expected" in data:
        return dict(data["expected"]), [str(q) for q in data.get("open_questions") or []]
    return data, []


def _normalized(report: Mapping[str, Any]) -> dict[str, Any]:
    def check(node: Any) -> Any:
        if not isinstance(node, Mapping):
            return None
        codes = sorted(
            (str(v.get("code")), str(v.get("path")))
            for v in node.get("violations") or []
            if isinstance(v, Mapping)
        )
        return {"pass": node.get("pass"), "violations": codes}

    texts = sorted(
        (
            {"intensity": str(t.get("intensity")), **(check(t) or {})}
            for t in report.get("texts") or []
            if isinstance(t, Mapping)
        ),
        key=lambda t: t["intensity"],
    )
    return {
        "policy_version": report.get("policy_version"),
        "sentence_check": check(report.get("sentence_check")),
        "sentencing_reason_check": check(report.get("sentencing_reason_check")),
        "texts": texts,
    }


def compare_policy_report(actual: Mapping[str, Any], expected: Mapping[str, Any]) -> list[str]:
    """`pass`·위반 코드·경로를 비교한다. 설명·문제 문장 원문은 모델마다 달라 보지 않는다."""
    got, want = _normalized(actual), _normalized(expected)
    return [
        f"{key}: 기대 {want[key]} · 실제 {got[key]}" for key in want if got.get(key) != want[key]
    ]


def policy_fixture_messages(policy: str) -> list[dict[str, str]]:
    source = _fixture("taxi-hell-input")
    requested = _fixture("taxi-hell-requested-output")
    payload = {
        "policy_version": policy,
        "case": source,
        "jury": None,
        "sentencing": None,
        "draft": {
            "schema_version": 1,
            "texts": [
                {
                    "intensity": requested["intensity"],
                    "statement": [
                        {"text": requested["text"], "kind": "opinion", "evidence_labels": []}
                    ],
                }
            ],
        },
        "evidence": {},
    }
    return [
        {"role": "system", "content": build_evaluator_system(policy)},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


async def run_policy_fixture(llm: LLMPort, settings: Settings, policy: str) -> PolicyCheck:
    expected, questions = expected_policy_report(policy)
    requested = _fixture("taxi-hell-requested-output")
    try:
        result = await llm.structured_call(
            role="evaluator",
            messages=policy_fixture_messages(policy),
            schema=evaluator_schema([requested["intensity"]]),
            timeout_s=float(settings.EVALUATOR_NODE_TIMEOUT_SECONDS),
            max_output_tokens=settings.EVALUATOR_MAX_OUTPUT_TOKENS,
        )
    except Exception as exc:  # 평가기 리포트에 남기고 계속한다.
        return PolicyCheck(policy, [f"검수관 호출 실패 {type(exc).__name__}"], questions)
    if result.output is None:
        return PolicyCheck(policy, [f"검수관 출력 없음({result.stop_reason})"], questions)
    actual = {**result.output, "schema_version": 1, "policy_version": policy}
    return PolicyCheck(policy, compare_policy_report(actual, expected), questions)


# ---------------------------------------------------------------------------
# 전체 실행
# ---------------------------------------------------------------------------


@dataclass
class Evaluation:
    runs: list[CaseRun]
    violations: list[checks.CheckViolation]
    scores: list[judge.JudgeScore]
    judge_errors: int
    policy_check: PolicyCheck | None
    judge_roles: list[str] = field(default_factory=list)

    def summary(self, bundle_version: str) -> RunSummary:
        headlines = [
            str(t.get("headline"))
            for run in self.runs
            if run.draft is not None
            for t in run.draft.get("texts") or []
        ]
        return RunSummary(
            bundle_version=bundle_version,
            axes=judge.axis_means(self.scores),
            violations=len(self.violations),
            headline_dup_rate=checks.headline_dup_rate(headlines),
        )


async def _judge(
    runs: Sequence[CaseRun], llm: LLMPort, settings: Settings, concurrency: int
) -> tuple[list[judge.JudgeScore], int, list[str]]:
    gate = asyncio.Semaphore(concurrency)
    counting = CountingLLM(llm)

    async def one(run: CaseRun, text: Mapping[str, Any]) -> judge.JudgeScore | None:
        case = run.case
        messages = judge.build_judge_messages(
            case_view={
                "item": case.snapshot.item,
                "amount_krw": case.snapshot.amount_krw,
                "category": case.snapshot.category,
                "reason": case.snapshot.reason,
                "post_type": case.snapshot.post_type,
            },
            jury_result=str(case.jury.result),
            sentencing=run.sentencing,
            facts=[{"id": f.label, "text": f.text} for f in case.dossier.facts],
            text=text,
        )
        async with gate:
            try:
                result = await counting.structured_call(
                    role="evaluator",
                    messages=messages,
                    schema=judge.judge_schema(),
                    timeout_s=float(settings.EVALUATOR_NODE_TIMEOUT_SECONDS),
                    max_output_tokens=settings.EVALUATOR_MAX_OUTPUT_TOKENS,
                )
                return judge.parse_judge_output(
                    result.output, case.case_id, str(text.get("intensity"))
                )
            except Exception:  # judge 실패는 세고 계속한다.
                return None

    jobs = [
        one(run, text)
        for run in runs
        if run.draft is not None
        for text in run.draft.get("texts") or []
    ]
    outcomes = await asyncio.gather(*jobs)
    scores = [s for s in outcomes if s is not None]
    return scores, len(outcomes) - len(scores), list(counting.roles)


async def evaluate(
    cases: Sequence[GoldenCase],
    llm: LLMPort,
    settings: Settings,
    *,
    judge_llm: LLMPort | None = None,
    quick: bool = False,
    concurrency: int = 4,
    hell_model: str | None = None,
) -> Evaluation:
    gate = asyncio.Semaphore(concurrency)

    async def one(case: GoldenCase) -> CaseRun:
        async with gate:
            return await run_case(case, llm, settings, quick=quick, hell_model=hell_model)

    runs = list(await asyncio.gather(*(one(case) for case in cases)))
    violations: list[checks.CheckViolation] = []
    for run in runs:
        violations += checks.check_case(
            run.case.case_id,
            run.draft,
            jury=run.case.jury,
            post_type=run.case.snapshot.post_type,
            label_map=run.case.dossier.label_map,
            sentencing=run.sentencing,
            expect=run.case.case_expect(),
        )
    violations += checks.check_run([run.draft for run in runs if run.draft is not None])

    scores: list[judge.JudgeScore] = []
    judge_errors = 0
    judge_roles: list[str] = []
    policy_check = None
    if not quick:
        scores, judge_errors, judge_roles = await _judge(
            runs, judge_llm or llm, settings, concurrency
        )
        policy_check = await run_policy_fixture(llm, settings, settings.GUARDRAIL_POLICY_VERSION)
    return Evaluation(runs, violations, scores, judge_errors, policy_check, judge_roles)


def report_rows(runs: Sequence[CaseRun]) -> list[ReportRow]:
    return [
        ReportRow(
            case_id=run.case.case_id,
            intensity=str(text.get("intensity")),
            source=str(text.get("source")),
            headline=str(text.get("headline")),
            attack_angle=str(text.get("attack_angle")),
            banter_strategy=str(text.get("banter_strategy")),
        )
        for run in runs
        if run.draft is not None
        for text in run.draft.get("texts") or []
    ]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run_regression", description="골든셋 회귀(06 §3.4)")
    parser.add_argument("--dry-run", action="store_true", help="FakeLLM(키·네트워크 없음)")
    parser.add_argument("--quick", action="store_true", help="judge·검수관 생략")
    parser.add_argument("--baseline", type=Path, default=None)
    parser.add_argument("--write-baseline", type=Path, default=None)
    parser.add_argument("--policy", choices=["guardrail-v1", "guardrail-v2"], default=None)
    parser.add_argument("--writer-model", default=None)
    parser.add_argument("--evaluator-hell-model", default=None)
    parser.add_argument("--temperature", type=float, default=None)
    parser.add_argument("--only-hell", action="store_true")
    parser.add_argument(
        "--only-thin-evidence",
        action="store_true",
        help="빈 방 사건(조서 F0 하나)만 돌린다. 기본 실행 50건 분모는 그대로",
    )
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--out", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    if os.environ.get("GEOJI_EVAL") != "1":
        print("실제 모델을 부르는 평가다. GEOJI_EVAL=1 을 설정하고 실행한다.", file=sys.stderr)
        return 1
    args = build_parser().parse_args(argv)
    if args.concurrency < 1:
        print("--concurrency 는 1 이상", file=sys.stderr)
        return 2
    if args.only_hell and args.only_thin_evidence:
        print(
            "--only-hell 과 --only-thin-evidence 는 서로 배타적이다(둘 다 실행 세트를 좁힌다). "
            "하나만 준다.",
            file=sys.stderr,
        )
        return 2
    if args.temperature is not None and not args.dry_run:
        print(
            "--temperature: LLMPort·어댑터가 temperature 를 받지 않아 적용할 수 없다"
            "(src 변경 필요).",
            file=sys.stderr,
        )
        return 2

    if args.dry_run:
        settings = Settings(_env_file=None)
    else:
        from geoji_ai.core.config import get_settings

        settings = get_settings()
    updates: dict[str, Any] = {}
    if args.policy:
        updates["GUARDRAIL_POLICY_VERSION"] = args.policy
    if args.writer_model:
        updates["MODEL_WRITER"] = args.writer_model
    if args.evaluator_hell_model:
        updates["MODEL_EVALUATOR_HELL"] = args.evaluator_hell_model
    settings = settings.model_copy(update=updates)

    hell_model: str | None = None
    if args.dry_run:
        from geoji_ai.adapters.fake_llm import FakeLLM

        llm: LLMPort = FakeLLM()
        judge_llm: LLMPort = FakeLLM(outputs={"evaluator": judge.dry_run_output()})
    else:
        from geoji_ai.adapters.llm_router import build_llm

        routed = build_llm(settings)
        if routed is None:
            print("OPENAI_API_KEY·XAI_API_KEY 가 모두 비어 있다", file=sys.stderr)
            return 1
        llm = judge_llm = routed
        if settings.MODEL_EVALUATOR_HELL != settings.MODEL_JUDGMENT:
            hell_model = settings.MODEL_EVALUATOR_HELL

    if args.only_hell:
        cases = load_cases(HELL_PATH)
    elif args.only_thin_evidence:
        cases = load_cases(THIN_PATH)
    else:
        cases = load_cases(CASES_PATH) + load_cases(HELL_PATH)
    bundle_version = prompt_bundle_version()
    started = time.monotonic()
    result = asyncio.run(
        evaluate(
            cases,
            llm,
            settings,
            judge_llm=judge_llm,
            quick=args.quick,
            concurrency=args.concurrency,
            hell_model=hell_model,
        )
    )
    elapsed = time.monotonic() - started

    summary = result.summary(bundle_version)
    baseline = None
    if args.baseline is not None:
        baseline = RunSummary.from_baseline(json.loads(args.baseline.read_text(encoding="utf-8")))
    verdict: RegressionVerdict = judge_regression(summary, baseline)
    if result.policy_check is not None and not result.policy_check.matched:
        verdict.notes.append(f"정책 fixture {result.policy_check.policy} 불일치 — 사람이 확인")
    if result.judge_errors:
        verdict.notes.append(f"judge 실패 {result.judge_errors}건(평균에서 뺐다)")

    roles = Counter(role for run in result.runs for role in run.roles)
    meta = {
        "bundle_version": bundle_version,
        "policy_version": settings.GUARDRAIL_POLICY_VERSION,
        "dry_run": args.dry_run,
        "quick": args.quick,
        "only_hell": args.only_hell,
        "only_thin_evidence": args.only_thin_evidence,
        "cases": len(cases),
        "writer_model": settings.MODEL_WRITER,
        "judgment_model(judge·검수)": settings.MODEL_JUDGMENT,
        "evaluator_hell_model": settings.MODEL_EVALUATOR_HELL,
        "temperature": "미적용"
        if args.temperature is None
        else f"{args.temperature}(dry-run, 미적용)",
        "graph_calls": dict(sorted(roles.items())),
        "judge_calls": len(result.judge_roles),
        "policy_fixture_calls": 0 if result.policy_check is None else 1,
        "elapsed_s": f"{elapsed:.1f}",
    }
    failures = {run.case.case_id: run.failure for run in result.runs if run.failure}
    text = render_report(
        meta=meta,
        summary=summary,
        verdict=verdict,
        violations=result.violations,
        rows=report_rows(result.runs),
        failures=failures,
        policy_check=result.policy_check,
        baseline=baseline,
    )
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"리포트: {args.out} ({'PASS' if verdict.passed else 'FAIL'})")
    else:
        print(text)
    if args.write_baseline is not None:
        args.write_baseline.parent.mkdir(parents=True, exist_ok=True)
        args.write_baseline.write_text(
            json.dumps(summary.to_baseline(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
