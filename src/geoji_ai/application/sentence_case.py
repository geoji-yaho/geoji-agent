"""SENTENCE·TEXT_RETRY 핸들러. 본체 `SentenceHandler`(05 GR-03, 그래프 C) + M2 스텁(03 §3.3).

스텁: `begin-generation` → `generation-failed(AI_NOT_READY)` → `complete`. 모델을 부르지 않는다.
백엔드는 `fallback_sentence` 로 FINAL/RULE + 템플릿 + RETAIN 을 만든다(10 §4.6).
dispatch 는 본체를 등록하고, 본체는 `ctx.llm` 이 없을 때 스텁으로 떨어진다.

오류 처리(03 §3.2, 스펙 "미리 답한 결정"):

| 백엔드 결과 | job |
| --- | --- |
| 409 `STALE_GENERATION` | 우리 세대가 아니다. 결과 폐기, `complete` |
| 그 밖의 409·422 | `fail(code, None)` |
| 401·403 | `fail("BACKEND_AUTH", 60)` |
| 재전송 뒤에도 불가 | `fail("BACKEND_UNAVAILABLE", 5)` |

application 은 어댑터를 import 하지 않는다. 어댑터 예외(`BackendRejected`·
`BackendUnavailable`)는 속성으로 알아본다 — 거부는 `status: int`·`code: str`,
불가는 `error_code == "BACKEND_UNAVAILABLE"`·`retry_after_s`.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any, Protocol

from geoji_ai.application.llm_gateway import ScopedLLM
from geoji_ai.contracts.jobs import Job, SentencePayload, TextRetryPayload, parse_payload
from geoji_ai.graphs.sentencing import SentenceDeps, build_sentence_graph, initial_state
from geoji_ai.ports.backend import BackendPort
from geoji_ai.ports.jobs import JobsPort
from geoji_ai.ports.llm import LLMPort

__all__ = [
    "BACKEND_AUTH",
    "BACKEND_AUTH_RETRY_AFTER_S",
    "BACKEND_UNAVAILABLE",
    "STUB_ERROR_CODE",
    "SentenceHandler",
    "SentenceStubHandler",
]

#: 스텁이 백엔드에 보내는 코드. 즉시 폴백·재시도 없음 무리다(10 §4.6).
STUB_ERROR_CODE = "AI_NOT_READY"

BACKEND_UNAVAILABLE = "BACKEND_UNAVAILABLE"
BACKEND_AUTH = "BACKEND_AUTH"
BACKEND_AUTH_RETRY_AFTER_S = 60

_STALE_GENERATION = "STALE_GENERATION"
_AUTH_STATUSES = frozenset({401, 403})


class StubContext(Protocol):
    """핸들러가 쓰는 문맥. `workers.dispatch.HandlerContext` 가 구조적으로 맞는다."""

    jobs: JobsPort
    backend: BackendPort
    generation_id: str
    worker_id: str


class SentenceStubHandler:
    """SENTENCE·TEXT_RETRY 공용. 둘 다 `verdict_id`·`verdict_version` 을 payload 에 둔다."""

    async def __call__(self, job: Job, ctx: StubContext) -> None:
        payload = parse_payload(job)
        assert isinstance(payload, SentencePayload | TextRetryPayload)
        try:
            await ctx.backend.begin_generation(
                payload.verdict_id,
                job_id=job.id,
                generation_id=ctx.generation_id,
                verdict_version=payload.verdict_version,
            )
            await ctx.backend.generation_failed(
                payload.verdict_id,
                job_id=job.id,
                generation_id=ctx.generation_id,
                error_code=STUB_ERROR_CODE,
            )
        except Exception as exc:
            if not await _settle_backend_error(job, ctx, exc):
                raise
            return
        await ctx.jobs.complete(job.id, ctx.worker_id, ctx.generation_id)


class SentenceContext(Protocol):
    """그래프 C 핸들러 문맥. `preparation` 은 없어도 된다(`getattr`, 없으면 MINIMAL)."""

    jobs: JobsPort
    backend: BackendPort
    llm: LLMPort | ScopedLLM
    semaphore: asyncio.Semaphore
    settings: Any
    generation_id: str
    worker_id: str


class SentenceHandler:
    """SENTENCE·TEXT_RETRY 본체(05 GR-03). payload → 스냅샷 → 그래프 C → `complete`/`fail`.

    그래프가 끝나면(finalize 200·폐기·generation-failed 보고 모두) `complete`.
    백엔드 예외는 `_settle_backend_error` 가 스텁과 같은 표로 정리한다.

    `db_now`(`Deadline.from_db`)는 주입하지 않으면 `job.updated_at` 에 핸들러 시작부터의 경과
    monotonic 을 더한 값이다. claim 이 `updated_at = now()` 를 DB 시각으로 찍고, begin 응답에는
    DB 시각이 없어서다(코디네이터 해석). 주입 인자는 테스트용이다.

    `ctx.llm` 이 없으면(벤더 키 없음) 모델을 부를 수 없어 M2 스텁(`AI_NOT_READY`, 즉시 폴백)으로
    처리한다.
    """

    def __init__(
        self,
        graph_factory: Callable[[SentenceDeps], Any] = build_sentence_graph,
        *,
        db_now: Callable[[], Awaitable[datetime]] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._graph_factory = graph_factory
        self._db_now = db_now
        self._clock = clock
        self._stub = SentenceStubHandler()

    def _job_db_now(self, job: Job, started: float) -> Callable[[], Awaitable[datetime]]:
        async def db_now() -> datetime:
            return job.updated_at + timedelta(seconds=self._clock() - started)

        return db_now

    async def __call__(self, job: Job, ctx: SentenceContext) -> None:
        started = self._clock()
        payload = parse_payload(job)
        assert isinstance(payload, SentencePayload | TextRetryPayload)
        if getattr(ctx, "llm", None) is None:
            await self._stub(job, ctx)
            return
        mode = "REGENERATE" if job.kind == "TEXT_RETRY" else "INITIAL"
        try:
            snapshot = await ctx.backend.snapshot(job.id, ctx.generation_id)
            deps = SentenceDeps(
                backend=ctx.backend,
                llm=ctx.llm,
                semaphore=ctx.semaphore,
                settings=ctx.settings,
                generation_id=ctx.generation_id,
                db_now=self._db_now or self._job_db_now(job, started),
                preparation=getattr(ctx, "preparation", None),
                clock=self._clock,
                # 알림(08 §3.3). 없는 문맥(테스트·스텁)이면 알리지 않는다.
                notifier=getattr(ctx, "notifier", None),
            )
            graph = self._graph_factory(deps)
            await graph.ainvoke(initial_state(job, mode, snapshot))
        except Exception as exc:
            if not await _settle_backend_error(job, ctx, exc):
                raise
            return
        await ctx.jobs.complete(job.id, ctx.worker_id, ctx.generation_id)


async def _settle_backend_error(job: Job, ctx: StubContext, exc: Exception) -> bool:
    """어댑터 예외면 job 을 정리하고 `True`. 모르는 예외면 `False`(상위로 올린다)."""
    if getattr(exc, "error_code", None) == BACKEND_UNAVAILABLE:
        retry_after_s = getattr(exc, "retry_after_s", None)
        await ctx.jobs.fail(
            job.id,
            ctx.worker_id,
            ctx.generation_id,
            error_code=BACKEND_UNAVAILABLE,
            retry_after_s=retry_after_s,
        )
        return True
    status = getattr(exc, "status", None)
    code = getattr(exc, "code", None)
    if not isinstance(status, int) or not isinstance(code, str):
        return False
    if status in _AUTH_STATUSES:
        await ctx.jobs.fail(
            job.id,
            ctx.worker_id,
            ctx.generation_id,
            error_code=BACKEND_AUTH,
            retry_after_s=BACKEND_AUTH_RETRY_AFTER_S,
        )
    elif code == _STALE_GENERATION:
        # 다른 세대가 활성이다. 우리 결과는 버리고 job 은 끝낸다(03 §3.2).
        await ctx.jobs.complete(job.id, ctx.worker_id, ctx.generation_id)
    else:
        await ctx.jobs.fail(
            job.id, ctx.worker_id, ctx.generation_id, error_code=code, retry_after_s=None
        )
    return True
