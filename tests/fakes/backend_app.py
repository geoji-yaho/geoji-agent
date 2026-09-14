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
- `finalize`(10 §5): commit record → generation·버전 → hash 형식·정책 버전 → 저장.
  FINAL 재생성(TEXT_RETRY)은 형량·양형 이유가 기존과 다르면 422, `texts` 강도가 중복이거나
  `target_intensities` 밖이면 422. 받은 강도만 바꾸고 나머지 강도는 기존 `text_sources` 를 유지한다
- `snapshot`(10 §4.1): `case-snapshot-taxi.json` 을 job payload 로 덮어 반환
- `resolve-evidence`(10 §4.2): 빈 sources + 고정 aggregates
- 두 엔드포인트 모두 `create_fake_backend(snapshot_fixture=..., resolve_fixture=...)` 로 다른
  fixture 를 줄 수 있다(데모 C, 04 ME-07)

`jobs_engine` 을 주면 job 소유(RUNNING ∧ generation ∧ lease)를 `ai.jobs` 에서 확인하고 RETAIN 을
`ai.jobs` 에 INSERT 한다(02 §3.4 `sentence.finalized` 규약). 없으면 `retain_jobs` 에 기록한다.

거부 응답 본문은 `{"code": "<오류 코드>"}` 다(10 에 본문 모양이 없어 이 가짜가 정한 것).

9/14 결정 재현 도구(백엔드 스케줄러·무효화 트랜잭션 자리, `jobs_engine` 필요):

- SENTENCE 게이트(D-24, 10 §3·§6): `confirm_verdict` 가 평결 확정을 받는다. 같은 post 의 PREPARE 가
  `QUEUED`·`RUNNING` 이면 보류, 아니면 즉시 INSERT. `run_sentence_gate(now)` 가 스케줄러 한 주기로
  PREPARE 종료(`SUCCEEDED`·`FAILED`·`CANCELLED`) 또는 `confirmed_at + sentence_gate_wait(30s)`
  도달이면 INSERT 한다. job·verdict 마감 = INSERT 시각(DB `now()`) + 10s. 보류 중 verdict 는
  마감이 없어 watchdog 대상이 아니다. 시간은 `now`·`clock` 으로 주입한다
- 진행 중 작업 끄기(D-26, 10 §8): `invalidate_post(post_id, scope_keys=...)` 가 한 트랜잭션에서
  scope epoch +1 → 그 post 의 `QUEUED`·`RUNNING` PREPARE·SENTENCE·TEXT_RETRY 를 `CANCELLED`.
  TEXT_RETRY payload 에는 post_id 가 없어 이 가짜가 아는 verdict(같은 post)로 찾는다
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.types import Text

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
    "INVALIDATED_JOB_KINDS",
    "NO_DEADLINE",
    "ROUND_RETRY_CODES",
    "SENTENCE_GATE_WAIT",
    "FakeBackend",
    "HeldSentence",
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
#: 10 §7 round 1·2·3 은 템플릿 후 5·10·20분 뒤. 가짜는 예약 기록에 값만 남긴다(기다리지 않는다).
TEXT_RETRY_ROUND_DELAYS_S: tuple[int, ...] = (300, 600, 1200)

_DEFAULT_POLICY_VERSION: str = str(Settings.model_fields["GUARDRAIL_POLICY_VERSION"].default)
_RETAIN_ROUTE = JOB_ROUTES["sentence.finalized"]
_SENTENCE_ROUTE = JOB_ROUTES["verdict.confirmed"]

#: 10 §3 SENTENCE 게이트 최대 대기 `confirmed_at + 30s`(9/14 D-24).
SENTENCE_GATE_WAIT = timedelta(seconds=30)
#: 10 §8 무효화 트랜잭션이 끄는 job kind(9/14 D-26).
INVALIDATED_JOB_KINDS: tuple[str, ...] = ("PREPARE", "SENTENCE", "TEXT_RETRY")
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

#: watchdog 4단계 "이전 job CANCELLED"(10 §6). 끝나지 않은 job 만.
_CANCEL_JOB_SQL = text(
    """
    UPDATE ai.jobs SET status = 'CANCELLED', owner_id = NULL, generation_id = NULL,
           lease_until = NULL, updated_at = now()
    WHERE id = CAST(:id AS uuid) AND status IN ('QUEUED', 'RUNNING')
    """
)

#: 게이트 조건: 같은 post 의 PREPARE 가 아직 끝나지 않았다(10 §3).
_PREPARE_ACTIVE_SQL = text(
    """
    SELECT EXISTS (
        SELECT 1 FROM ai.jobs
        WHERE kind = 'PREPARE' AND payload->>'post_id' = :post_id
          AND status IN ('QUEUED', 'RUNNING')
    )
    """
)

#: 10 §3 SENTENCE INSERT. 마감은 INSERT 시각 + 10s(D-24, `JOB_ROUTES` 의 `deadline_after_s`).
_INSERT_SENTENCE_SQL = text(
    """
    INSERT INTO ai.jobs (
        id, event_id, event_type, kind, dedupe_key,
        aggregate_id, aggregate_version, schema_version, payload,
        priority, max_attempts, deadline_at, trace_id
    ) VALUES (
        :id, :event_id, :event_type, :kind, :dedupe_key,
        :aggregate_id, :aggregate_version, 1, CAST(:payload AS jsonb),
        :priority, :max_attempts, now() + make_interval(secs => :deadline_after_s), :trace_id
    )
    ON CONFLICT (dedupe_key) DO NOTHING
    RETURNING CAST(id AS text) AS id, deadline_at
    """
)

_SELECT_JOB_BY_DEDUPE_SQL = text(
    "SELECT CAST(id AS text) AS id, deadline_at FROM ai.jobs WHERE dedupe_key = :dedupe_key"
)

#: 10 §8 "해당 scope 의 privacy_epochs 를 먼저 잠그고 증가". 행이 없으면 0 → 1.
_BUMP_EPOCH_SQL = text(
    """
    INSERT INTO ai.privacy_epochs (scope_key, epoch) VALUES (:scope_key, 1)
    ON CONFLICT (scope_key) DO UPDATE SET epoch = ai.privacy_epochs.epoch + 1
    """
)

#: 10 §8 D-26 같은 무효화 트랜잭션에서 영향받는 게시물의 진행 중 job 끄기.
_CANCEL_POST_JOBS_SQL = text(
    """
    UPDATE ai.jobs SET status = 'CANCELLED', owner_id = NULL, generation_id = NULL,
           lease_until = NULL, updated_at = now()
    WHERE kind IN ('PREPARE', 'SENTENCE', 'TEXT_RETRY') AND status IN ('QUEUED', 'RUNNING')
      AND (payload->>'post_id' = :post_id
           OR (kind = 'TEXT_RETRY' AND payload->>'verdict_id' = ANY(:verdict_ids)))
    RETURNING CAST(id AS text) AS id
    """
).bindparams(bindparam("verdict_ids", type_=ARRAY(Text)))

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
    #: 강도 값 → 저장된 문구의 `source`(AI/TEMPLATE). 없는 강도는 템플릿이다.
    text_sources: dict[str, str] = field(default_factory=dict)
    #: generation-failed 를 처리한 세대 → 코드. 같은 요청 재전송을 흡수한다.
    failed_generations: dict[str, str] = field(default_factory=dict)


@dataclass
class HeldSentence:
    """게이트가 보류한 평결 확정(10 §3 D-24). SENTENCE 는 아직 INSERT 되지 않았다."""

    verdict_id: str
    verdict_version: int
    post_id: str
    confirmed_at: datetime


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
        #: finalize 가 앞에서부터 하나씩 꺼내 그대로 거부할 `(status, code)`. 비면 기존 동작이다.
        self.finalize_rejections: list[tuple[int, str]] = []
        #: 형량이 PENDING → FINAL 로 바뀐 기록 `(verdict_id, sentence_source)`. 기록만 한다
        #: (08 §4.1 "형량 중복 확정 0" 검사용).
        self.sentence_fixes: list[tuple[str, str]] = []
        #: finalize 가 받은 본문(JSON 객체). 인증을 통과한 요청이면 거부 여부와 무관하게 쌓는다.
        self.finalize_bodies: list[dict[str, Any]] = []
        #: SENTENCE 게이트(D-24). 대기 상한·시계는 테스트가 바꿔 끼운다.
        self.sentence_gate_wait: timedelta = SENTENCE_GATE_WAIT
        self.clock: Callable[[], datetime] = _now
        #: 보류 중 평결 확정 verdict_id → 확정 기록.
        self.held_sentences: dict[str, HeldSentence] = {}
        #: 게이트가 넣은 SENTENCE job verdict_id → job id.
        self.sentence_jobs: dict[str, str] = {}

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

    # --- SENTENCE 게이트(D-24, 10 §3·§6) ------------------------------------------

    async def confirm_verdict(
        self,
        verdict_id: str,
        *,
        post_id: str,
        verdict_version: int = 1,
        fallback_sentence: str = "oneDay",
        confirmed_at: datetime | None = None,
    ) -> str | None:
        """평결 확정. 게이트를 한 번 돌려 넣었으면 SENTENCE job id, 보류면 None.

        verdict 가 없으면 마감 없이(`deadline_at=None`, PENDING) 심는다. 마감은 INSERT 때 정해진다.
        """
        self._require_engine("SENTENCE 게이트")
        confirmed = confirmed_at or self.clock()
        if verdict_id not in self.verdicts:
            self.seed_verdict(
                verdict_id,
                verdict_version=verdict_version,
                post_id=post_id,
                fallback_sentence=fallback_sentence,
                deadline_at=None,
            )
        self.held_sentences[verdict_id] = HeldSentence(
            verdict_id=verdict_id,
            verdict_version=verdict_version,
            post_id=post_id,
            confirmed_at=confirmed,
        )
        await self.run_sentence_gate(now=confirmed)
        return None if verdict_id in self.held_sentences else self.sentence_jobs.get(verdict_id)

    async def run_sentence_gate(self, now: datetime | None = None) -> list[str]:
        """게이트 스케줄러 한 주기. 이번에 넣은 SENTENCE job id 목록.

        PREPARE 가 없거나 끝났으면(`QUEUED`·`RUNNING` 이 아니면) 또는 `confirmed_at + 대기 상한` 에
        닿았으면 INSERT 하고, verdict 마감을 job 마감(INSERT 시각 + 10s)과 같게 둔다.
        """
        current = now or self.clock()
        inserted: list[str] = []
        for held in list(self.held_sentences.values()):
            waited_out = current >= held.confirmed_at + self.sentence_gate_wait
            if not waited_out and await self._prepare_active(held.post_id):
                continue
            job_id, deadline_at = await self._insert_sentence(held)
            self.verdicts[held.verdict_id].deadline_at = deadline_at
            self.sentence_jobs[held.verdict_id] = job_id
            del self.held_sentences[held.verdict_id]
            inserted.append(job_id)
        return inserted

    async def _prepare_active(self, post_id: str) -> bool:
        engine = self._require_engine("SENTENCE 게이트")
        async with engine.connect() as conn:
            result = await conn.execute(_PREPARE_ACTIVE_SQL, {"post_id": post_id})
            return bool(result.scalar_one())

    async def _insert_sentence(self, held: HeldSentence) -> tuple[str, datetime]:
        engine = self._require_engine("SENTENCE 게이트")
        payload = {
            "verdict_id": held.verdict_id,
            "verdict_version": held.verdict_version,
            "post_id": held.post_id,
        }
        dedupe_key = build_dedupe_key(_SENTENCE_ROUTE, payload)
        id_field, version_field = _SENTENCE_ROUTE.aggregate_fields
        params = {
            "id": str(uuid4()),
            "event_id": str(uuid4()),
            "event_type": _SENTENCE_ROUTE.event_type,
            "kind": _SENTENCE_ROUTE.kind,
            "dedupe_key": dedupe_key,
            "aggregate_id": str(payload[id_field]),
            "aggregate_version": int(payload[version_field]),
            "payload": json.dumps(payload, ensure_ascii=False),
            "priority": _SENTENCE_ROUTE.priority,
            "max_attempts": _SENTENCE_ROUTE.max_attempts,
            "deadline_after_s": _SENTENCE_ROUTE.deadline_after_s,
            "trace_id": str(uuid4()),
        }
        async with engine.begin() as conn:
            row = (await conn.execute(_INSERT_SENTENCE_SQL, params)).mappings().first()
            if row is None:  # 같은 dedupe_key 가 이미 있다.
                existing = await conn.execute(_SELECT_JOB_BY_DEDUPE_SQL, {"dedupe_key": dedupe_key})
                row = existing.mappings().one()
        return row["id"], row["deadline_at"]

    # --- 진행 중 작업 끄기(D-26, 10 §8) -------------------------------------------

    async def invalidate_post(self, post_id: str, *, scope_keys: Iterable[str]) -> list[str]:
        """무효화 트랜잭션 한 번. 끈(CANCELLED) job id 목록.

        scope epoch 를 먼저 올리고, 같은 트랜잭션에서 그 post 의 진행 중 PREPARE·SENTENCE·
        TEXT_RETRY 를 끈다. 어느 scope 가 영향받는지는 백엔드가 정한다 — 호출자가 `scope_keys`
        로 준다.
        """
        engine = self._require_engine("무효화 트랜잭션")
        verdict_ids = [v.verdict_id for v in self.verdicts.values() if v.post_id == post_id]
        async with engine.begin() as conn:
            for scope_key in sorted(set(scope_keys)):
                await conn.execute(_BUMP_EPOCH_SQL, {"scope_key": scope_key})
            rows = await conn.execute(
                _CANCEL_POST_JOBS_SQL, {"post_id": post_id, "verdict_ids": verdict_ids}
            )
            return [row.id for row in rows]

    def _require_engine(self, what: str) -> AsyncEngine:
        if self.jobs_engine is None:
            raise RuntimeError(f"{what}는 jobs_engine 이 필요하다")
        return self.jobs_engine

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
            self._fix_rule(verdict)
        elif verdict.text_status == "NONE":
            verdict.text_status = "TEMPLATE_READY"
        if code in ROUND_RETRY_CODES:
            self._schedule_round(verdict)
        verdict.failed_generations[generation_id] = code
        verdict.active_job_id = None
        verdict.active_generation_id = None
        if first_fix:
            await self.insert_retain(verdict, trace_id)

    def _fix_rule(self, verdict: VerdictState) -> None:
        """폴백 형량 FINAL/RULE + 템플릿(10 §4.6·§6 3단계)."""
        verdict.sentence_status = "FINAL"
        verdict.sentence_source = "RULE"
        verdict.sentence = verdict.fallback_sentence
        verdict.text_status = "TEMPLATE_READY"
        self.sentence_fixes.append((verdict.verdict_id, "RULE"))

    def _schedule_round(self, verdict: VerdictState) -> None:
        """다음 TEXT_RETRY round 예약 기록(10 §7). 3회를 넘기지 않는다."""
        done = sum(1 for r in self.text_retry_rounds if r["verdict_id"] == verdict.verdict_id)
        if done < MAX_TEXT_RETRY_ROUND:
            self.text_retry_rounds.append(
                {
                    "verdict_id": verdict.verdict_id,
                    "verdict_version": verdict.verdict_version,
                    "round": done + 1,
                    "delay_s": TEXT_RETRY_ROUND_DELAYS_S[done],
                }
            )

    async def run_watchdog(self, now: datetime | None = None) -> list[str]:
        """deadline watchdog 한 주기(10 §6). 확정한 verdict_id 목록.

        실제 백엔드는 250ms 주기 스케줄러다. 가짜는 테스트가 부를 때 한 번 돈다.
        마감이 지난 `PENDING` 만 대상이다(2단계 — 이미 FINAL 이면 반복하지 않는다).
        폴백 FINAL/RULE + 템플릿 → active 세대 해제 · 이전 job `CANCELLED` → RETAIN · round 1.
        """
        current = now or _now()
        fixed: list[str] = []
        for verdict in self.verdicts.values():
            if (
                verdict.sentence_status != "PENDING"
                or verdict.deadline_at is None
                or current < verdict.deadline_at
            ):
                continue
            self._fix_rule(verdict)
            job_id = verdict.active_job_id
            verdict.active_job_id = None
            verdict.active_generation_id = None
            if job_id is not None and self.jobs_engine is not None:
                async with self.jobs_engine.begin() as conn:
                    await conn.execute(_CANCEL_JOB_SQL, {"id": job_id})
            await self.insert_retain(verdict, str(uuid4()))
            self._schedule_round(verdict)
            fixed.append(verdict.verdict_id)
        return fixed


def _trace_id(request: Request) -> str:
    return request.headers.get("x-trace-id") or str(uuid4())


def _load_json(path: Path | str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def create_fake_backend(
    *,
    jobs_engine: AsyncEngine | None = None,
    token: str = FAKE_SERVICE_TOKEN,
    snapshot_fixture: Path | str | None = None,
    resolve_fixture: Path | str | None = None,
) -> FastAPI:
    """`snapshot_fixture`·`resolve_fixture` 는 JSON 경로(04 ME-07). 없으면 기존 동작이다.

    - `snapshot_fixture`: snapshot 과 resolve-evidence 의 기준 사건을 이 파일로 바꾼다
    - `resolve_fixture`: resolve-evidence 가 이 파일을 돌려준다. `aggregates.excludes_post_id`
      만 job 의 post_id 로 치환한다
    """
    fake = FakeBackend(token=token, jobs_engine=jobs_engine, policy_version=_DEFAULT_POLICY_VERSION)

    def load_snapshot_data() -> dict[str, Any]:
        if snapshot_fixture is None:
            return load_fixture(_SNAPSHOT_FIXTURE)
        return _load_json(snapshot_fixture)

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
        data = load_snapshot_data()
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
        snapshot_data = load_snapshot_data()
        created_at = datetime.fromisoformat(snapshot_data["created_at"])
        post_id = payload.get("post_id", snapshot_data["post_id"])
        if resolve_fixture is not None:
            fixed = _load_json(resolve_fixture)
            fixed["aggregates"]["excludes_post_id"] = post_id
            return ResolveEvidenceResponse.model_validate(fixed).model_dump(mode="json")
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
        if fake.finalize_rejections:
            status, code = fake.finalize_rejections.pop(0)
            return _reject(status, code)
        raw = await request.body()
        request_hash = hashlib.sha256(raw).hexdigest()
        try:
            loose = json.loads(raw)
        except ValueError:
            return _reject(422, _INVALID_DRAFT)
        if isinstance(loose, dict):
            fake.finalize_bodies.append(loose)
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
            fake.sentence_fixes.append((verdict_id, "AI"))
        elif req.sentencing is not None and (
            str(req.sentencing.sentence) != verdict.sentence
            or req.sentencing.sentencing_reason != verdict.sentencing_reason
        ):
            # TEXT_RETRY: 형량 필드가 기존과 다르면 거부(08 §3.2).
            return _reject(422, _INVALID_DRAFT)
        # 9·10. 문구 저장 · text_status · active 해제
        given = [str(text_draft.intensity) for text_draft in req.draft.texts]
        if first_fix:
            all_ai = all(text_draft.source == "AI" for text_draft in req.draft.texts)
            verdict.text_sources = {str(t.intensity): t.source for t in req.draft.texts}
        else:
            # 재생성: payload `intensities` 부분집합만 온다. 나머지 강도는 기존 행을 유지한다.
            targets = [str(i) for i in load_snapshot_data()["jury"]["target_intensities"]]
            if len(set(given)) != len(given) or not set(given) <= set(targets):
                return _reject(422, _INVALID_DRAFT)
            verdict.text_sources.update({str(t.intensity): t.source for t in req.draft.texts})
            all_ai = all(verdict.text_sources.get(i) == "AI" for i in targets)
        verdict.text_version += 1
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
