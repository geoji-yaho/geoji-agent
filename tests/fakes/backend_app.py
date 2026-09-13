"""가짜 백엔드(03 §3.7, VF-01).

10 §4 의 내부 API 5종을 **최소 상태 기계**로 구현한 FastAPI 앱이다. 작업 3·4·5·8 의 통합
테스트가 실제 백엔드 대신 쓴다. 실제 백엔드와의 대조는 M2 시연(VF-06)에서 한다.

    uv run uvicorn tests.fakes.backend_app:app --port 8200

상태는 앱 인스턴스(`app.state.fake`)에 둔다. 테스트는 `create_fake_backend()` 로 매번 새로 만든다.
모듈 수준 `app` 은 수동 가이드(03 §4.3)용으로 `v1`/`p1` 판결 하나를 미리 심어 둔다.

verdict 별 상태: `sentence_status(PENDING/FINAL)`·`sentence_source(AI/RULE)`·
`text_status(NONE/TEMPLATE_READY/AI_READY)`·`text_version`·`active_job_id`·
`active_generation_id`·`deadline_at`. commit record 는 `generation_id → (request_hash, 응답)`.

- `begin-generation`(10 §4.3): PENDING 은 마감 전만, FINAL 은 `fixed_sentencing`(TEXT_RETRY).
  같은 현재 generation 재호출 허용, 다른 활성 generation 이 유효하면 409 `STALE_GENERATION`
- `generation-failed`(10 §4.6): 현재 세대만. 다른 세대는 409. 코드 표 그대로 폴백·round 예약
- `finalize`(10 §5): commit record → generation·버전 → hash 형식·정책 버전 → 저장
- `snapshot`(10 §4.1): `case-snapshot-taxi.json` 을 job payload 로 덮어 반환
- `resolve-evidence`(10 §4.2): 빈 sources + 고정 aggregates

`jobs_engine` 을 주면 job 소유(RUNNING ∧ generation ∧ lease)를 `ai.jobs` 에서 확인하고 RETAIN 을
`ai.jobs` 에 INSERT 한다(02 §3.4 `sentence.finalized` 규약). 없으면 `retain_jobs` 에 기록한다.

거부 응답 본문은 `{"code": "<오류 코드>"}` 다(10 에 본문 모양이 없어 이 가짜가 정한 것).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from geoji_ai.contracts.case import CaseSnapshot
from geoji_ai.contracts.finalize import SHA256_HEX, FinalizeRequest, check_policy_version
from geoji_ai.contracts.jobs import RetainPayload
from geoji_ai.core.config import Settings
from geoji_ai.ports.backend import (
    GenerationErrorCode,
    ResolveEvidenceRequest,
    ResolveEvidenceResponse,
)
from geoji_ai.workers.dispatch import JOB_ROUTES, build_dedupe_key
from tests.conftest import load_fixture

__all__ = [
    "FAKE_SERVICE_TOKEN",
    "IMMEDIATE_FALLBACK_CODES",
    "NO_DEADLINE",
    "ROUND_RETRY_CODES",
    "FakeBackend",
    "VerdictState",
    "app",
    "create_fake_backend",
]

#: 테스트 기본 서비스 토큰. 실제 값이 아니다.
FAKE_SERVICE_TOKEN = "fake-service-token"

#: `seed_verdict(deadline_at=None)` 인 판결의 응답용 마감. 마감 검사를 하지 않는다는 표시다.
NO_DEADLINE = datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC)

#: 10 §4.6 즉시 폴백 · TEXT_RETRY 예약 안 함.
IMMEDIATE_FALLBACK_CODES: frozenset[str] = frozenset(
    {"AI_NOT_READY", "POLICY_ERROR", "EVIDENCE_INVALIDATED"}
)
#: 10 §4.6 폴백 + TEXT_RETRY round 예약.
ROUND_RETRY_CODES: frozenset[str] = frozenset(
    {"VENDOR_UNAVAILABLE", "BUDGET_EXCEEDED", "EVAL_FAILED", "SCHEMA_INVALID", "DEADLINE_EXCEEDED"}
)
#: 10 §7 `retry_round <= 3`.
MAX_TEXT_RETRY_ROUND = 3

_DEFAULT_POLICY_VERSION: str = str(Settings.model_fields["GUARDRAIL_POLICY_VERSION"].default)
_RETAIN_ROUTE = JOB_ROUTES["sentence.finalized"]
_SNAPSHOT_FIXTURE = "case-snapshot-taxi"

#: resolve-evidence 의 고정 aggregates. fixture 에 집계 값이 없어 테스트용으로 둔 값이다
#: (`burn_rate` 는 `sentencing-taxi.json` 사유의 41%). 제품 값이 아니다.
_FAKE_AGGREGATES: dict[str, Any] = {
    "burn_rate": 0.41,
    "tier": "FAKE",
    "no_spend_days": 0,
    "repeat_same_category_30d": 0,
    "rule_version": 1,
}
_AGGREGATE_WINDOW_DAYS = 30  # 10 §4.2 "사건 생성 시각 이전 30일"

_JOB_ROW_SQL = text(
    """
    SELECT status, CAST(generation_id AS text) AS generation_id,
           (lease_until IS NOT NULL AND lease_until > now()) AS lease_valid, payload
    FROM ai.jobs WHERE id = CAST(:id AS uuid)
    """
)

_INSERT_RETAIN_SQL = text(
    """
    INSERT INTO ai.jobs (
        id, event_id, event_type, kind, dedupe_key,
        aggregate_id, aggregate_version, schema_version, payload,
        priority, max_attempts, deadline_at, trace_id
    ) VALUES (
        :id, :event_id, :event_type, :kind, :dedupe_key,
        :aggregate_id, :aggregate_version, 1, CAST(:payload AS jsonb),
        :priority, :max_attempts, NULL, :trace_id
    )
    ON CONFLICT (dedupe_key) DO NOTHING
    """
)

_STALE = "STALE_GENERATION"
_INVALID_DRAFT = "INVALID_DRAFT"


class _BeginBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    generation_id: str
    verdict_version: int


class _FailedBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    generation_id: str
    error_code: GenerationErrorCode


@dataclass
class VerdictState:
    verdict_id: str
    verdict_version: int
    post_id: str
    fallback_sentence: str
    #: `None` 이면 마감 검사를 하지 않는다.
    deadline_at: datetime | None
    sentence_status: str = "PENDING"
    sentence_source: str | None = None
    sentence: str | None = None
    sentencing_reason: str | None = None
    text_status: str = "NONE"
    text_version: int = 0
    active_job_id: str | None = None
    active_generation_id: str | None = None
    #: generation-failed 를 처리한 세대 → 코드. 같은 요청 재전송을 흡수한다.
    failed_generations: dict[str, str] = field(default_factory=dict)


def _reject(status: int, code: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"code": code})


def _now() -> datetime:
    return datetime.now(UTC)


class FakeBackend:
    """가짜 백엔드의 상태와 규칙. 엔드포인트는 `create_fake_backend` 가 붙인다."""

    def __init__(self, *, token: str, jobs_engine: AsyncEngine | None, policy_version: str) -> None:
        self.token = token
        self.jobs_engine = jobs_engine
        self.policy_version = policy_version
        self.verdicts: dict[str, VerdictState] = {}
        self.commit_records: dict[str, tuple[str, dict[str, Any]]] = {}
        self.retain_jobs: list[dict[str, Any]] = []
        self.text_retry_rounds: list[dict[str, Any]] = []
        #: `/internal/` 호출 기록 `(method, path)`.
        self.calls: list[tuple[str, str]] = []
        #: `jobs_engine` 이 없을 때 begin-generation 이 알려 준 job → generation·verdict.
        self.jobs: dict[str, dict[str, str]] = {}
        #: begin-generation 이 상태를 바꾼 뒤 켜진다(재기동 테스트가 기다린다).
        self.began = threading.Event()
        #: 주면 begin-generation 이 상태를 바꾼 뒤 이 이벤트가 설 때까지 응답을 미룬다.
        self.hold_after_begin: threading.Event | None = None

    # --- 테스트 헬퍼 -------------------------------------------------------------

    def seed_verdict(
        self,
        verdict_id: str,
        *,
        verdict_version: int = 1,
        post_id: str,
        fallback_sentence: str = "oneDay",
        deadline_at: datetime | None,
    ) -> VerdictState:
        state = VerdictState(
            verdict_id=verdict_id,
            verdict_version=verdict_version,
            post_id=post_id,
            fallback_sentence=fallback_sentence,
            deadline_at=deadline_at,
        )
        self.verdicts[verdict_id] = state
        return state

    def verdict_for_post(self, post_id: str) -> VerdictState | None:
        return next((v for v in self.verdicts.values() if v.post_id == post_id), None)

    # --- 규칙 ---------------------------------------------------------------------

    def authorized(self, request: Request) -> bool:
        header = request.headers.get("authorization", "")
        if not self.token or not header.startswith("Bearer "):
            return False
        return hmac.compare_digest(header[len("Bearer ") :].encode(), self.token.encode())

    async def _job_row(self, job_id: str) -> dict[str, Any] | None:
        assert self.jobs_engine is not None
        async with self.jobs_engine.connect() as conn:
            row = (await conn.execute(_JOB_ROW_SQL, {"id": job_id})).mappings().first()
        if row is None:
            return None
        data = dict(row)
        if isinstance(data["payload"], str | bytes):
            data["payload"] = json.loads(data["payload"])
        return data

    async def job_payload(self, job_id: str, generation_id: str) -> dict[str, Any] | None:
        """job 이 있고 그 세대가 현재면 payload. 아니면 `None`(10 §4.1 검증)."""
        if self.jobs_engine is not None:
            row = await self._job_row(job_id)
            if row is None or row["status"] != "RUNNING" or row["generation_id"] != generation_id:
                return None
            return dict(row["payload"])
        known = self.jobs.get(job_id)
        if known is None or known["generation_id"] != generation_id:
            return None
        verdict = self.verdicts[known["verdict_id"]]
        return {
            "verdict_id": verdict.verdict_id,
            "verdict_version": verdict.verdict_version,
            "post_id": verdict.post_id,
        }

    async def requester_owns(self, job_id: str, generation_id: str) -> bool:
        if self.jobs_engine is None:
            return True
        row = await self._job_row(job_id)
        return (
            row is not None and row["status"] == "RUNNING" and row["generation_id"] == generation_id
        )

    async def active_is_valid(self, verdict: VerdictState, requester_job_id: str) -> bool:
        """다른 활성 generation 이 아직 유효한가."""
        if verdict.active_generation_id is None or verdict.active_job_id is None:
            return False
        if self.jobs_engine is None:
            # 같은 job 의 새 claim 은 이전 세대를 대체한다(generation 은 claim 이 바꾼다).
            return verdict.active_job_id != requester_job_id
        row = await self._job_row(verdict.active_job_id)
        return (
            row is not None
            and row["status"] == "RUNNING"
            and row["generation_id"] == verdict.active_generation_id
            and bool(row["lease_valid"])
        )

    async def wait_hold(self) -> None:
        hold = self.hold_after_begin
        if hold is None:
            return
        while not hold.is_set():
            await asyncio.sleep(0.01)

    async def insert_retain(self, verdict: VerdictState, trace_id: str) -> None:
        payload = RetainPayload(
            event="sentence.finalized",
            verdict_id=verdict.verdict_id,
            comment_id=None,
            version=verdict.verdict_version,
        ).model_dump()
        dedupe_key = build_dedupe_key(_RETAIN_ROUTE, payload)
        if self.jobs_engine is None:
            if not any(job["dedupe_key"] == dedupe_key for job in self.retain_jobs):
                self.retain_jobs.append({"dedupe_key": dedupe_key, "payload": payload})
            return
        id_field, version_field = _RETAIN_ROUTE.aggregate_fields
        params = {
            "id": str(uuid4()),
            "event_id": str(uuid4()),
            "event_type": _RETAIN_ROUTE.event_type,
            "kind": _RETAIN_ROUTE.kind,
            "dedupe_key": dedupe_key,
            "aggregate_id": str(payload[id_field]),
            "aggregate_version": int(payload[version_field]),
            "payload": json.dumps(payload, ensure_ascii=False),
            "priority": _RETAIN_ROUTE.priority,
            "max_attempts": _RETAIN_ROUTE.max_attempts,
            "trace_id": trace_id,
        }
        async with self.jobs_engine.begin() as conn:
            await conn.execute(_INSERT_RETAIN_SQL, params)

    async def apply_failure(
        self, verdict: VerdictState, generation_id: str, code: str, trace_id: str
    ) -> None:
        """10 §4.6 코드 표. 처음 확정이면 폴백 FINAL/RULE + 템플릿 + RETAIN."""
        first_fix = verdict.sentence_status == "PENDING"
        if first_fix:
            verdict.sentence_status = "FINAL"
            verdict.sentence_source = "RULE"
            verdict.sentence = verdict.fallback_sentence
            verdict.text_status = "TEMPLATE_READY"
        elif verdict.text_status == "NONE":
            verdict.text_status = "TEMPLATE_READY"
        if code in ROUND_RETRY_CODES:
            done = sum(1 for r in self.text_retry_rounds if r["verdict_id"] == verdict.verdict_id)
            if done < MAX_TEXT_RETRY_ROUND:
                self.text_retry_rounds.append(
                    {
                        "verdict_id": verdict.verdict_id,
                        "verdict_version": verdict.verdict_version,
                        "round": done + 1,
                    }
                )
        verdict.failed_generations[generation_id] = code
        verdict.active_job_id = None
        verdict.active_generation_id = None
        if first_fix:
            await self.insert_retain(verdict, trace_id)


def _trace_id(request: Request) -> str:
    return request.headers.get("x-trace-id") or str(uuid4())


def create_fake_backend(
    *,
    jobs_engine: AsyncEngine | None = None,
    token: str = FAKE_SERVICE_TOKEN,
) -> FastAPI:
    fake = FakeBackend(token=token, jobs_engine=jobs_engine, policy_version=_DEFAULT_POLICY_VERSION)
    app = FastAPI(title="fake-backend")
    app.state.fake = fake

    @app.middleware("http")
    async def record_calls(request: Request, call_next: Any) -> Any:
        if request.url.path.startswith("/internal/"):
            fake.calls.append((request.method, request.url.path))
        return await call_next(request)

    @app.get("/internal/v1/ai-jobs/{job_id}/snapshot")
    async def snapshot(job_id: str, request: Request) -> Any:
        if not fake.authorized(request):
            return _reject(401, "UNAUTHORIZED")
        payload = await fake.job_payload(job_id, request.headers.get("x-generation-id", ""))
        if payload is None:
            return _reject(409, _STALE)
        data = load_fixture(_SNAPSHOT_FIXTURE)
        if "post_id" in payload:
            data["post_id"] = payload["post_id"]
        if "verdict_id" in payload and data.get("jury") is not None:
            data["jury"]["verdict_id"] = payload["verdict_id"]
            verdict = fake.verdicts.get(payload["verdict_id"])
            if verdict is not None:
                data["jury"]["verdict_version"] = verdict.verdict_version
                if verdict.deadline_at is not None:
                    data["jury"]["deadline_at"] = verdict.deadline_at.isoformat()
        return CaseSnapshot.model_validate(data).model_dump(mode="json")

    @app.post("/internal/v1/ai-jobs/{job_id}/resolve-evidence")
    async def resolve_evidence(job_id: str, request: Request) -> Any:
        if not fake.authorized(request):
            return _reject(401, "UNAUTHORIZED")
        try:
            ResolveEvidenceRequest.model_validate_json(await request.body())
        except ValidationError:
            return _reject(422, "INVALID_REQUEST")
        payload = await fake.job_payload(job_id, request.headers.get("x-generation-id", ""))
        if payload is None:
            return _reject(409, _STALE)
        snapshot_data = load_fixture(_SNAPSHOT_FIXTURE)
        created_at = datetime.fromisoformat(snapshot_data["created_at"])
        post_id = payload.get("post_id", snapshot_data["post_id"])
        response = {
            "sources": [],
            "aggregates": {
                **_FAKE_AGGREGATES,
                "excludes_post_id": post_id,
                "window": {
                    "start_at": (created_at - timedelta(days=_AGGREGATE_WINDOW_DAYS)).isoformat(),
                    "end_at": created_at.isoformat(),
                },
            },
            "room_rules": [],
            "recent_verdicts": [],
            "style_comments": [],
        }
        return ResolveEvidenceResponse.model_validate(response).model_dump(mode="json")

    @app.post("/internal/v1/verdicts/{verdict_id}/begin-generation")
    async def begin_generation(verdict_id: str, request: Request) -> Any:
        if not fake.authorized(request):
            return _reject(401, "UNAUTHORIZED")
        try:
            body = _BeginBody.model_validate_json(await request.body())
        except ValidationError:
            return _reject(422, "INVALID_REQUEST")
        verdict = fake.verdicts.get(verdict_id)
        if verdict is None:
            return _reject(404, "NOT_FOUND")
        if (
            body.verdict_version != verdict.verdict_version
            or body.generation_id in verdict.failed_generations
        ):
            return _reject(409, _STALE)
        same_current = (
            verdict.active_job_id == body.job_id
            and verdict.active_generation_id == body.generation_id
        )
        if not same_current:
            if not await fake.requester_owns(body.job_id, body.generation_id):
                return _reject(409, _STALE)
            if await fake.active_is_valid(verdict, body.job_id):
                return _reject(409, _STALE)
        if verdict.sentence_status == "PENDING":
            if verdict.deadline_at is not None and _now() >= verdict.deadline_at:
                return _reject(409, "DEADLINE_EXCEEDED")
            fixed = None
        else:
            if verdict.text_status == "AI_READY":
                return _reject(409, _STALE)
            fixed = {
                "sentence": verdict.sentence,
                "sentencing_reason": verdict.sentencing_reason,
                "reason_source": "TEMPLATE" if verdict.sentence_source == "RULE" else "AI",
            }
        verdict.active_job_id = body.job_id
        verdict.active_generation_id = body.generation_id
        fake.jobs[body.job_id] = {"generation_id": body.generation_id, "verdict_id": verdict_id}
        fake.began.set()
        await fake.wait_hold()
        return {
            "fixed_sentencing": fixed,
            "text_version": verdict.text_version,
            "deadline_at": (verdict.deadline_at or NO_DEADLINE).isoformat(),
        }

    @app.post("/internal/v1/verdicts/{verdict_id}/generation-failed")
    async def generation_failed(verdict_id: str, request: Request) -> Any:
        if not fake.authorized(request):
            return _reject(401, "UNAUTHORIZED")
        try:
            body = _FailedBody.model_validate_json(await request.body())
        except ValidationError:
            return _reject(422, "INVALID_REQUEST")
        verdict = fake.verdicts.get(verdict_id)
        if verdict is None:
            return _reject(404, "NOT_FOUND")
        if verdict.failed_generations.get(body.generation_id) == body.error_code:
            return {"verdict_id": verdict_id}
        if body.generation_id != verdict.active_generation_id:
            return _reject(409, _STALE)
        await fake.apply_failure(verdict, body.generation_id, body.error_code, _trace_id(request))
        return {"verdict_id": verdict_id}

    @app.post("/internal/v1/verdicts/{verdict_id}/finalize")
    async def finalize(verdict_id: str, request: Request) -> Any:
        if not fake.authorized(request):
            return _reject(401, "UNAUTHORIZED")
        raw = await request.body()
        request_hash = hashlib.sha256(raw).hexdigest()
        try:
            loose = json.loads(raw)
        except ValueError:
            return _reject(422, _INVALID_DRAFT)
        generation_id = loose.get("generation_id") if isinstance(loose, dict) else None
        if not isinstance(generation_id, str):
            return _reject(422, _INVALID_DRAFT)
        # 1. commit record
        record = fake.commit_records.get(generation_id)
        if record is not None:
            if record[0] == request_hash:
                return record[1]
            return _reject(409, "IDEMPOTENCY_CONFLICT")
        try:
            req = FinalizeRequest.model_validate(loose)
        except ValidationError:
            return _reject(422, _INVALID_DRAFT)
        verdict = fake.verdicts.get(verdict_id)
        if verdict is None:
            return _reject(404, "NOT_FOUND")
        # 4·5. generation · verdict_version · expected_text_version
        if (
            req.generation_id != verdict.active_generation_id
            or req.job_id != verdict.active_job_id
            or req.verdict_version != verdict.verdict_version
            or req.expected_text_version != verdict.text_version
        ):
            return _reject(409, _STALE)
        # 6. 최초 SENTENCE 마감
        first_fix = verdict.sentence_status == "PENDING"
        if first_fix and verdict.deadline_at is not None and _now() >= verdict.deadline_at:
            return _reject(409, "DEADLINE_EXCEEDED")
        # 7. hash 형식 · 정책 버전
        if not re.fullmatch(SHA256_HEX, req.draft_hash) or not re.fullmatch(
            SHA256_HEX, req.evaluation_draft_hash
        ):
            return _reject(422, _INVALID_DRAFT)
        try:
            check_policy_version(req, fake.policy_version)
        except ValueError:
            return _reject(422, _INVALID_DRAFT)
        # 8. 형량
        if first_fix:
            if req.sentencing is None:
                return _reject(422, _INVALID_DRAFT)
            verdict.sentence_status = "FINAL"
            verdict.sentence_source = "AI"
            verdict.sentence = str(req.sentencing.sentence)
            verdict.sentencing_reason = req.sentencing.sentencing_reason
        elif req.sentencing is not None and str(req.sentencing.sentence) != verdict.sentence:
            return _reject(422, _INVALID_DRAFT)
        # 9·10. 문구 저장 · text_status · active 해제
        verdict.text_version += 1
        all_ai = all(text_draft.source == "AI" for text_draft in req.draft.texts)
        verdict.text_status = "AI_READY" if all_ai else "TEMPLATE_READY"
        verdict.active_job_id = None
        verdict.active_generation_id = None
        # 11. 최초 확정 때만 RETAIN
        if first_fix:
            await fake.insert_retain(verdict, _trace_id(request))
        # 12. commit record
        response = {
            "verdict_id": verdict_id,
            "text_version": verdict.text_version,
            "committed_at": _now().isoformat(),
        }
        fake.commit_records[generation_id] = (request_hash, response)
        return response

    @app.get("/posts/{post_id}/verdict")
    async def post_verdict(post_id: str) -> Any:
        verdict = fake.verdict_for_post(post_id)
        if verdict is None:
            return _reject(404, "NOT_FOUND")
        source = {"AI_READY": "AI", "TEMPLATE_READY": "TEMPLATE"}.get(verdict.text_status)
        return {
            "sentence_status": verdict.sentence_status,
            "text_status": verdict.text_status,
            "text_version": verdict.text_version,
            "sentence_source": verdict.sentence_source,
            "view": {"source": source},
        }

    return app


def _module_app() -> FastAPI:
    """수동 가이드(03 §4.3)용. 토큰은 `SERVICE_AUTH_TOKEN` 환경변수, 판결 `v1`/`p1` 을 심는다."""
    token = os.environ.get("SERVICE_AUTH_TOKEN", "").strip() or FAKE_SERVICE_TOKEN
    module_app = create_fake_backend(token=token)
    module_app.state.fake.seed_verdict("v1", verdict_version=1, post_id="p1", deadline_at=None)
    return module_app


app = _module_app()
