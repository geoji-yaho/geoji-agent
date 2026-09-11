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
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine

from geoji_ai.adapters.postgres_jobs import PostgresJobs, make_engine, reap
from geoji_ai.contracts.jobs import Job
from geoji_ai.core.config import Settings, secret_value
from geoji_ai.core.logging import bind_trace_id, get_logger
from geoji_ai.ports.jobs import JobsPort
from geoji_ai.workers.dispatch import SLOT_KINDS, HandlerContext, handler_for
from geoji_ai.workers.heartbeat import start_heartbeat

__all__ = ["Worker", "make_worker_id", "run_worker"]

log = get_logger(__name__)

#: 폴링 jitter 상한(초). 02 §3.3 "빈 큐 WORKER_POLL_MS + jitter(0~100ms)".
_POLL_JITTER_S = 0.1


def make_worker_id(slot: str) -> str:
    """`<host>:<pid>:<slot>` (02 §3.3 원문).

    SENTENCE 슬롯은 loop 이 2개라 둘이 같은 `worker_id` 를 쓴다. 소유 조건에
    `generation_id` 가 있어 서로의 job 을 건드리지 못한다.
    """
    return f"{socket.gethostname()}:{os.getpid()}:{slot}"


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
    ) -> None:
        unknown = set(settings.WORKER_SLOTS) - set(SLOT_KINDS)
        if unknown:
            # `WORKER_SLOTS` 는 환경변수(JSON)로 덮을 수 있다. 모르는 슬롯이 있으면
            # `_slot_loop` 이 `KeyError` 로 죽으므로 기동에서 막는다.
            raise ValueError(
                f"WORKER_SLOTS 에 모르는 슬롯이 있다: {sorted(unknown)}. "
                f"아는 슬롯 {sorted(SLOT_KINDS)}"
            )
        self._jobs = jobs
        self._settings = settings
        self._reaper = reaper
        self._engine = engine
        self._shutdown = asyncio.Event()
        # 프로세스 전역 세마포어. 슬롯이 몇 개든 하나를 공유한다(02 §3.3 동시성).
        self._semaphore = asyncio.Semaphore(settings.MODEL_CONCURRENCY_LIMIT)
        self._inflight: dict[str, _InFlight] = {}

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
        generation_id = job.generation_id or ""
        ctx = HandlerContext(
            jobs=self._jobs,
            semaphore=self._semaphore,
            settings=self._settings,
            generation_id=generation_id,
            worker_id=worker_id,
        )
        handler = handler_for(job.kind)
        handler_task = asyncio.create_task(handler(job, ctx), name=f"handler:{job.kind}")
        beat = start_heartbeat(
            self._jobs,
            job_id=job.id,
            worker_id=worker_id,
            generation_id=generation_id,
            interval_s=self._settings.HEARTBEAT_SECONDS,
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
                # 오류 분류는 작업 6 `domain/retries.py` 다. 작업 2 에는 쓸 오류 코드가
                # 없으므로 결과를 저장하지 않고 reaper 회수에 맡긴다.
                self._inflight.pop(job.id, None)
                log.exception("handler_failed", slot=slot)

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
                continue
            if reaped:
                log.info("lease_reaped", reaped=reaped)

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
    engine = make_engine(secret_value(settings, "DATABASE_URL"))
    jobs: JobsPort = PostgresJobs(engine, lease_s=settings.JOB_LEASE_SECONDS)
    worker = Worker(jobs, settings, reaper=reaper, engine=engine)
    _install_sigterm(worker)
    log.info("worker_started", slots=settings.WORKER_SLOTS, reaper=reaper)
    try:
        await worker.run()
    finally:
        await engine.dispose()
        log.info("worker_stopped")
