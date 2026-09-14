"""PREPARE 핸들러(05 GR-02).

그래프 B(`graphs.preparation`)를 한 번 돌리고 job 을 정리한다.

| 결과 | job |
| --- | --- |
| 그래프 끝(`DOSSIER_READY`·`COMPLETE`·재사용·구버전 이벤트) | `complete` |
| `EvidenceInvalidated`(저장 직전 epoch 불일치, 저장 0) | `fail("EVIDENCE_INVALIDATED", None)` |
| 백엔드 409 `STALE_GENERATION` | 결과 폐기, `complete` |
| 백엔드 그 밖의 거부 | `fail(code, None)`, 401·403 은 `fail("BACKEND_AUTH", 60)` |
| 백엔드 불가 | `fail("BACKEND_UNAVAILABLE", retry_after_s)` |
| 그 밖의 예외 | 상위로(워커가 `HANDLER_ERROR`) |

application 은 어댑터를 import 하지 않는다. 백엔드 예외는 속성으로 알아본다
(`status`·`code`, `error_code == "BACKEND_UNAVAILABLE"`·`retry_after_s`).
"""

from __future__ import annotations

import asyncio
from typing import Protocol

from geoji_ai.application import instrument
from geoji_ai.application.llm_gateway import ScopedLLM
from geoji_ai.contracts.jobs import Job
from geoji_ai.core.config import Settings
from geoji_ai.core.logging import get_logger
from geoji_ai.graphs.preparation import PrepareDeps, run_preparation
from geoji_ai.ports.backend import BackendPort
from geoji_ai.ports.jobs import JobsPort
from geoji_ai.ports.llm import LLMPort
from geoji_ai.ports.memory import MemoryPort
from geoji_ai.ports.preparation import EvidenceInvalidated, PreparationPort

__all__ = ["PrepareHandler"]

log = get_logger(__name__)

BACKEND_UNAVAILABLE = "BACKEND_UNAVAILABLE"
BACKEND_AUTH = "BACKEND_AUTH"
BACKEND_AUTH_RETRY_AFTER_S = 60
_STALE_GENERATION = "STALE_GENERATION"
_AUTH_STATUSES = frozenset({401, 403})


class _Context(Protocol):
    jobs: JobsPort
    backend: BackendPort
    memory: MemoryPort | None
    llm: LLMPort | ScopedLLM | None
    preparation: PreparationPort | None
    settings: Settings
    semaphore: asyncio.Semaphore
    generation_id: str
    worker_id: str


class PrepareHandler:
    async def __call__(self, job: Job, ctx: _Context) -> None:
        if ctx.preparation is None:
            raise RuntimeError("PREPARE 는 PreparationPort 가 필요하다(HandlerContext.preparation)")
        deps = PrepareDeps(
            backend=ctx.backend,
            preparation=ctx.preparation,
            settings=ctx.settings,
            semaphore=ctx.semaphore,
            generation_id=ctx.generation_id,
            memory=ctx.memory,
            llm=ctx.llm,
        )
        try:
            state = await run_preparation(job, deps)
        except EvidenceInvalidated as exc:
            instrument.count("invalidated_evidence_total")
            await ctx.jobs.fail(
                job.id,
                ctx.worker_id,
                ctx.generation_id,
                error_code=exc.error_code,
                retry_after_s=None,
            )
            return
        except Exception as exc:
            if not await _settle_backend_error(job, ctx, exc):
                raise
            return
        log.info("prepare_done", status=state.get("status"))
        await ctx.jobs.complete(job.id, ctx.worker_id, ctx.generation_id)


async def _settle_backend_error(job: Job, ctx: _Context, exc: Exception) -> bool:
    """백엔드 어댑터 예외면 job 을 정리하고 True. 모르는 예외면 False."""
    if getattr(exc, "error_code", None) == BACKEND_UNAVAILABLE:
        await ctx.jobs.fail(
            job.id,
            ctx.worker_id,
            ctx.generation_id,
            error_code=BACKEND_UNAVAILABLE,
            retry_after_s=getattr(exc, "retry_after_s", None),
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
        await ctx.jobs.complete(job.id, ctx.worker_id, ctx.generation_id)
    else:
        await ctx.jobs.fail(
            job.id, ctx.worker_id, ctx.generation_id, error_code=code, retry_after_s=None
        )
    return True
