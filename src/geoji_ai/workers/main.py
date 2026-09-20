"""워커 런타임(02 §3.3·§3.5).

슬롯 3종(`WORKER_SLOTS`: SENTENCE 2 · PREPARE 1 · BACKGROUND 1)마다 독립 claim loop 이
돈다. 빈 큐면 `WORKER_POLL_MS` + jitter(0~100ms), claim 에 성공하면 바로 다음 claim 이다.
모델 호출 동시성은 프로세스 전역 `asyncio.Semaphore(MODEL_CONCURRENCY_LIMIT)` 하나로
묶는다 — 슬롯이 몇 개든 이 세마포어는 하나다.

종료(SIGTERM)는 새 claim 을 멈추고, 진행 중 핸들러에 `WORKER_SHUTDOWN_DEADLINE_SECONDS`
만큼 준 뒤 취소하고 `release` 로 lease 를 반납한다. Windows 처럼 시그널 핸들러를 걸 수
없는 곳에서는 `request_shutdown()` 을 직접 부른다.

로그에는 job 행의 `trace_id` 만 묶는다. 개별 ID(`post_id` 등)는 넣지 않는다.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import random
import signal
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncEngine

from geoji_ai.adapters.backend_http import BackendHttp, bind_trace
from geoji_ai.adapters.llm_router import ROLE_VENDOR, RoleRoutedLLM, build_llm, price_for
from geoji_ai.adapters.postgres_call_ledger import PostgresCallLedger
from geoji_ai.adapters.postgres_jobs import PostgresJobs, make_engine, reap
from geoji_ai.adapters.postgres_memory import PostgresMemory
from geoji_ai.adapters.postgres_preparation import PostgresPreparation
from geoji_ai.adapters.postgres_telemetry import PostgresTelemetry
from geoji_ai.application import instrument
from geoji_ai.application.llm_gateway import LLMGateway, ScopedLLM
from geoji_ai.contracts.jobs import Job
from geoji_ai.core.config import Settings, secret_value
from geoji_ai.core.logging import bind_trace_id, get_logger
from geoji_ai.core.startup import StartupError
from geoji_ai.domain.vendor_health import VendorHealth
from geoji_ai.ports.backend import BackendPort
from geoji_ai.ports.jobs import JobsPort
from geoji_ai.ports.llm import LLMPort
from geoji_ai.ports.memory import MemoryPort
from geoji_ai.ports.preparation import PreparationPort
from geoji_ai.telemetry.alerts import (
    AlertNotifier,
    DailyCostTracker,
    QueueAgeWatch,
    retry_exhausted,
    sink_from_settings,
)
from geoji_ai.workers.dispatch import SLOT_KINDS, HandlerContext, handler_for
from geoji_ai.workers.heartbeat import start_heartbeat

#: `RoleRoutedLLM`·`build_llm`·`ROLE_VENDOR` 는 `adapters/llm_router.py` 로 옮겼다. 기존 import
#: 경로(`tests/evaluations/run_intake_eval.py` 등)를 위해 여기서 다시 내보낸다.
__all__ = [
    "ROLE_VENDOR",
    "RoleRoutedLLM",
    "Worker",
    "build_llm",
    "make_worker_id",
    "run_worker",
]

log = get_logger(__name__)

#: 폴링 jitter 상한(초). 02 §3.3 "빈 큐 WORKER_POLL_MS + jitter(0~100ms)".
_POLL_JITTER_S = 0.1

#: 핸들러가 예외로 죽었을 때 `fail` 에 남기는 오류 코드와 재시도 간격(초).
#: 간격은 `BACKEND_UNAVAILABLE` 과 같은 5초다(사용자 9/14).
HANDLER_ERROR_CODE = "HANDLER_ERROR"
HANDLER_ERROR_RETRY_AFTER_S = 5


def make_worker_id(slot: str) -> str:
    """`<host>:<pid>:<slot>` (02 §3.3 원문).

    SENTENCE 슬롯은 loop 이 2개라 둘이 같은 `worker_id` 를 쓴다. 소유 조건에
    `generation_id` 가 있어 서로의 job 을 건드리지 못한다.
    """
    return f"{socket.gethostname()}:{os.getpid()}:{slot}"


async def _alert_if_exhausted(
    notifier: AlertNotifier | None, job: Job, error_code: str, updated: bool
) -> None:
    """`fail` 이 반영됐고 마지막 시도였으면 재시도 소진 알림(08 §3.3).

    claim 이 `attempts` 를 이미 1 올렸으므로 `attempts >= max_attempts` 면 FAIL_SQL 이 행을 FAILED
    (기한 지난 SENTENCE 는 CANCELLED)로 닫는다 — 어느 쪽이든 더 시도하지 않는다.
    """
    if notifier is None or not updated or job.attempts < job.max_attempts:
        return
    try:
        alert = retry_exhausted(job_kind=job.kind, code=error_code, attempts=job.attempts)
    except Exception as exc:
        # 계측은 job 을 바꾸지 않는다. 알림을 만들지 못해도 fail 결과는 그대로다.
        log.warning("instrument_failed", target="retry_exhausted", error_type=type(exc).__name__)
        return
    await instrument.notify(notifier, alert)


class _AlertingJobs:
    """`JobsPort` 를 감싸 `fail` 결과로 재시도 소진을 알린다. 나머지 메서드는 그대로 넘긴다."""

    def __init__(self, inner: JobsPort, job: Job, notifier: AlertNotifier) -> None:
        self._inner = inner
        self._job = job
        self._notifier = notifier

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def fail(
        self,
        job_id: str,
        worker_id: str,
        generation_id: str,
        error_code: str,
        retry_after_s: float | None,
    ) -> bool:
        updated = await self._inner.fail(
            job_id, worker_id, generation_id, error_code=error_code, retry_after_s=retry_after_s
        )
        await _alert_if_exhausted(self._notifier, self._job, error_code, updated)
        return updated


@dataclass
class _InFlight:
    """진행 중인 job. 종료 때 lease 를 반납할 대상이다."""

    job_id: str
    worker_id: str
    generation_id: str
    task: asyncio.Task[None]


class Worker:
    def __init__(
        self,
        jobs: JobsPort,
        settings: Settings,
        *,
        reaper: bool = False,
        engine: AsyncEngine | None = None,
        backend: BackendPort | None = None,
        memory: MemoryPort | None = None,
        llm: LLMPort | ScopedLLM | None = None,
        preparation: PreparationPort | None = None,
        notifier: AlertNotifier | None = None,
        cost_tracker: DailyCostTracker | None = None,
        queue_age_threshold_s: float | None = None,
        queue_age: Callable[[], Awaitable[float | None]] | None = None,
    ) -> None:
        unknown = set(settings.WORKER_SLOTS) - set(SLOT_KINDS)
        if unknown:
            # `WORKER_SLOTS` 는 환경변수(JSON)로 덮을 수 있다. 모르는 슬롯이 있으면
            # `_slot_loop` 이 `KeyError` 로 죽으므로 기동에서 막는다.
            raise ValueError(
                f"WORKER_SLOTS 에 모르는 슬롯이 있다: {sorted(unknown)}. "
                f"아는 슬롯 {sorted(SLOT_KINDS)}"
            )
        missing = set(SLOT_KINDS) - set(settings.WORKER_SLOTS)
        if missing:
            # `.env` 가 옛 기본값으로 `WORKER_SLOTS` 를 통째로 덮으면 새 슬롯의 claim loop 이
            # 안 뜨고 그 kind 의 job 은 QUEUED 로 영원히 남는다(19 §8 JURY). 기동은 막지 않는다.
            log.warning(
                "worker_slots_missing",
                missing=sorted(missing),
                kinds=sorted(k for slot in missing for k in SLOT_KINDS[slot]),
            )
        self._jobs = jobs
        self._settings = settings
        self._reaper = reaper
        self._engine = engine
        # `run_worker` 가 `BackendHttp` 를 넣는다. 테스트는 가짜 백엔드 클라이언트를 넣는다.
        self._backend = backend
        # `run_worker` 가 `PostgresMemory` 를 넣는다(04 ME-03).
        self._memory = memory
        # `run_worker` 가 `LLMGateway`(라우터 + 원장 + 벤더 장애)와 `PostgresPreparation` 을 넣는다.
        self._llm = llm
        self._preparation = preparation
        self._shutdown = asyncio.Event()
        # 프로세스 전역 세마포어. 슬롯이 몇 개든 하나를 공유한다(02 §3.3 동시성).
        self._semaphore = asyncio.Semaphore(settings.MODEL_CONCURRENCY_LIMIT)
        self._inflight: dict[str, _InFlight] = {}
        # 운영 알림·비용 집계(08 §3.3). `run_worker` 가 프로세스에 하나씩 넣는다.
        self._notifier = notifier
        self._cost_tracker = cost_tracker
        # queue oldest age 목표값은 계획서에 수치가 없다(08 §4.1).
        # 임계 인자가 없으면 감시하지 않는다.
        self._queue_age_watch = (
            QueueAgeWatch(queue_age_threshold_s) if queue_age_threshold_s is not None else None
        )
        if queue_age is None and self._queue_age_watch is not None and engine is not None:
            queue_age = PostgresTelemetry(engine).oldest_queued_age_seconds
        self._queue_age = queue_age

    @property
    def semaphore(self) -> asyncio.Semaphore:
        """모델 호출 동시성 세마포어. 핸들러는 `HandlerContext.semaphore` 로 받는다."""
        return self._semaphore

    def request_shutdown(self) -> None:
        """SIGTERM 대체. 새 claim 을 멈춘다."""
        self._shutdown.set()

    async def run(self) -> None:
        """슬롯 loop 을 띄우고 종료 신호까지 돈다."""
        slot_tasks = [
            asyncio.create_task(self._slot_loop(slot), name=f"slot:{slot}:{index}")
            for slot, count in self._settings.WORKER_SLOTS.items()
            for index in range(count)
        ]
        aux_tasks: list[asyncio.Task[None]] = []
        if self._reaper:
            if self._engine is None:
                log.warning("reaper_skipped_no_engine")
            else:
                aux_tasks.append(asyncio.create_task(self._reaper_loop(), name="reaper"))
        try:
            await self._shutdown.wait()
            # 새 claim 은 여기서 멈춘다. 진행 중 핸들러에는 deadline 만큼 준다.
            if slot_tasks:
                await asyncio.wait(
                    slot_tasks, timeout=self._settings.WORKER_SHUTDOWN_DEADLINE_SECONDS
                )
        finally:
            for task in (*slot_tasks, *aux_tasks):
                if not task.done():
                    task.cancel()
            results = await asyncio.gather(*slot_tasks, *aux_tasks, return_exceptions=True)
            for task, result in zip((*slot_tasks, *aux_tasks), results, strict=True):
                # `return_exceptions=True` 가 삼킨 것을 여기서 드러낸다. 조용히 죽은
                # loop 이 있으면 이 로그가 유일한 흔적이다.
                if isinstance(result, BaseException) and not isinstance(
                    result, asyncio.CancelledError
                ):
                    log.error("task_failed", task=task.get_name(), error=type(result).__name__)
            # `run()` 자체가 밖에서 취소돼도 lease 는 반납한다.
            await asyncio.shield(asyncio.ensure_future(self._release_inflight()))

    async def run_job(self, job: Job, worker_id: str) -> None:
        """job 하나를 처리한다. heartbeat 가 lease 를 잃으면 `CancelledError` 가 상위로 간다."""
        bind_trace_id(job.trace_id)
        # 백엔드 호출의 `X-Trace-Id`. 포트 시그니처에 자리가 없어 어댑터 컨텍스트로 준다.
        bind_trace(job.trace_id)
        generation_id = job.generation_id or ""
        # job 단위 로그 문맥(08 §3.3). `job_kind` 는 `log_node` 필드 목록 밖이라
        # contextvars 로만 묶는다.
        structlog.contextvars.bind_contextvars(
            job_id=job.id, job_kind=job.kind, generation_id=generation_id
        )
        jobs: JobsPort = (
            self._jobs if self._notifier is None else _AlertingJobs(self._jobs, job, self._notifier)  # type: ignore[assignment]
        )
        ctx = HandlerContext(
            jobs=jobs,
            semaphore=self._semaphore,
            settings=self._settings,
            generation_id=generation_id,
            worker_id=worker_id,
            backend=self._backend,  # type: ignore[arg-type]  # None 은 백엔드 없는 테스트뿐
            memory=self._memory,
            llm=self._llm,
            preparation=self._preparation,
            notifier=self._notifier,
            cost_tracker=self._cost_tracker,
        )
        handler = handler_for(job.kind)
        handler_task = asyncio.create_task(handler(job, ctx), name=f"handler:{job.kind}")
        beat = start_heartbeat(
            self._jobs,
            job_id=job.id,
            worker_id=worker_id,
            generation_id=generation_id,
            interval_s=self._settings.HEARTBEAT_SECONDS,
            lease_s=self._settings.JOB_LEASE_SECONDS,
            target=handler_task,
        )
        self._inflight[job.id] = _InFlight(
            job_id=job.id,
            worker_id=worker_id,
            generation_id=generation_id,
            task=handler_task,
        )
        try:
            await handler_task
        except asyncio.CancelledError:
            # 종료로 슬롯 loop 이 취소된 경우다. 핸들러도 같이 접는다.
            handler_task.cancel()
            raise
        finally:
            beat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await beat
            done_clean = (
                handler_task.done()
                and not handler_task.cancelled()
                and handler_task.exception() is None
            )
            if done_clean:
                # 핸들러가 complete·fail 로 스스로 정리했다. 반납할 lease 가 없다.
                self._inflight.pop(job.id, None)

    async def _slot_loop(self, slot: str) -> None:
        worker_id = make_worker_id(slot)
        kinds = SLOT_KINDS[slot]
        poll_s = self._settings.WORKER_POLL_MS / 1000
        while not self._shutdown.is_set():
            try:
                job = await self._jobs.claim(kinds, worker_id)
            except Exception:
                # claim 이 죽으면 그 슬롯이 조용히 사라진다. 워커는 살아 있고
                # `/health/live` 도 200 이라 큐가 쌓여도 알 방법이 없다. 로그를 남기고
                # 폴링 간격만큼 쉰 뒤 계속한다.
                log.exception("claim_failed", slot=slot)
                await self._sleep_or_shutdown(poll_s + random.uniform(0, _POLL_JITTER_S))
                continue
            if job is None:
                await self._sleep_or_shutdown(poll_s + random.uniform(0, _POLL_JITTER_S))
                continue
            if self._shutdown.is_set():
                # claim 도중에 종료 신호가 섰다. 실행하지 않고 바로 반납한다(02 §3.3).
                with contextlib.suppress(Exception):
                    await self._jobs.release(job.id, worker_id, job.generation_id or "")
                return
            try:
                await self.run_job(job, worker_id)
            except asyncio.CancelledError:
                current = asyncio.current_task()
                if (current is not None and current.cancelling() > 0) or self._shutdown.is_set():
                    raise
                # heartbeat 가 lease 를 잃어 핸들러를 취소했다. 결과는 저장하지 않는다.
                # 행은 reaper 가 회수한다(LEASE_EXPIRED).
                self._inflight.pop(job.id, None)
                log.warning("job_cancelled", slot=slot)
            except Exception:
                # 핸들러가 complete·fail 없이 죽었다. lease 만료까지 RUNNING 으로 두지 않고
                # `HANDLER_ERROR` 로 fail 한다. 시도 횟수를 쓰고 5초 뒤 재시도, max 도달이면
                # FAILED, 기한 지난 SENTENCE 는 CANCELLED(FAIL_SQL). 세분 분류는 작업 6
                # `domain/retries.py` 다. 핸들러가 이미 fail 했으면 0행이라 무해하다.
                self._inflight.pop(job.id, None)
                log.exception("handler_failed", slot=slot)
                try:
                    updated = await self._jobs.fail(
                        job.id,
                        worker_id,
                        job.generation_id or "",
                        error_code=HANDLER_ERROR_CODE,
                        retry_after_s=HANDLER_ERROR_RETRY_AFTER_S,
                    )
                except Exception:
                    # fail 까지 죽으면 로그만 남긴다. 행은 lease 만료 뒤 reaper 가 회수한다.
                    log.exception("handler_fail_record_failed", slot=slot)
                else:
                    await _alert_if_exhausted(self._notifier, job, HANDLER_ERROR_CODE, updated)

    async def _reaper_loop(self) -> None:
        engine = self._engine
        if engine is None:
            return
        while not self._shutdown.is_set():
            await self._sleep_or_shutdown(self._settings.REAPER_INTERVAL_SECONDS)
            if self._shutdown.is_set():
                return
            try:
                reaped = await reap(engine)
            except Exception:
                log.exception("reaper_failed")
                reaped = 0
            if reaped:
                log.info("lease_reaped", reaped=reaped)
            await self._watch_queue_age()

    async def _watch_queue_age(self) -> None:
        """reaper 주기에 queue oldest age 를 한 번 본다. 임계가 없으면(기본) 하지 않는다."""
        if self._queue_age_watch is None or self._queue_age is None:
            return
        try:
            alert = self._queue_age_watch.observe(await self._queue_age())
        except Exception as exc:
            log.warning("queue_age_watch_failed", error=type(exc).__name__)
            return
        await instrument.notify(self._notifier, alert)

    async def _sleep_or_shutdown(self, delay: float) -> None:
        """`delay` 만큼 쉰다. 그 사이 종료 신호가 오면 바로 깬다."""
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._shutdown.wait(), timeout=delay)

    async def _release_inflight(self) -> None:
        """종료 시점에 남은 job 의 lease 를 반납한다(02 §3.3 종료)."""
        for entry in list(self._inflight.values()):
            if not entry.task.done():
                entry.task.cancel()
                with contextlib.suppress(BaseException):
                    await entry.task
            try:
                await self._jobs.release(entry.job_id, entry.worker_id, entry.generation_id)
            except Exception:
                log.exception("release_failed")
        self._inflight.clear()


def _install_sigterm(worker: Worker) -> None:
    """POSIX 면 SIGTERM 을 건다. 등록이 안 되는 플랫폼(Windows)은 조용히 건너뛴다."""
    try:
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGTERM, worker.request_shutdown)
    except (NotImplementedError, RuntimeError, ValueError, AttributeError):
        log.info("sigterm_handler_unavailable")


async def run_worker(settings: Settings, *, reaper: bool = False) -> None:
    """CLI 진입점(`geoji-ai worker [--reaper]`)이 부른다."""
    backend_url = settings.BACKEND_INTERNAL_URL.strip()
    if not backend_url:
        raise StartupError("BACKEND_INTERNAL_URL 이 비어 있다. 워커는 백엔드 내부 API 가 필요하다.")
    engine = make_engine(secret_value(settings, "DATABASE_URL"))
    jobs: JobsPort = PostgresJobs(engine, lease_s=settings.JOB_LEASE_SECONDS)
    backend = BackendHttp(backend_url, secret_value(settings, "SERVICE_AUTH_TOKEN"))
    memory: MemoryPort = PostgresMemory(engine, settings)
    preparation: PreparationPort = PostgresPreparation(engine)
    # 알림·비용 집계는 프로세스에 하나다(08 §3.3). 웹훅 URL 이 비면 싱크가 아무것도 보내지 않는다.
    sink = sink_from_settings(settings)
    notifier = AlertNotifier(sink)
    cost_tracker = DailyCostTracker(settings.COST_ALERT_KRW_PER_DAY)
    router = build_llm(settings)
    llm: ScopedLLM | None = None
    if router is None:
        log.warning("llm_disabled_no_vendor_keys")
    else:
        # 원장·벤더 장애 상태는 프로세스에 하나다(06 §3.1·§3.2).
        llm = LLMGateway(
            router,
            PostgresCallLedger(engine),
            VendorHealth(),
            price_for,
            cost_tracker=cost_tracker,
            notifier=notifier,
            # node_results 재사용·저장 전 현재 epoch 대조(08 §4.1)
            stale_scopes=preparation.stale_scopes,
        )
    # queue oldest age 임계는 넘기지 않는다 — 값 미정(08 §4.1)이라 감시하지 않는다.
    worker = Worker(
        jobs,
        settings,
        reaper=reaper,
        engine=engine,
        backend=backend,
        memory=memory,
        llm=llm,
        preparation=preparation,
        notifier=notifier,
        cost_tracker=cost_tracker,
    )
    _install_sigterm(worker)
    log.info("worker_started", slots=settings.WORKER_SLOTS, reaper=reaper)
    try:
        await worker.run()
    finally:
        await backend.aclose()
        await sink.aclose()
        await engine.dispose()
        log.info("worker_stopped")
