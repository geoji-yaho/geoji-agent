"""JURY_VOTE 핸들러(18 §3.5).

그래프 D(`graphs.jury_vote`)를 한 번 돌리고 job 을 정리한다.

| 백엔드 응답 | job |
| --- | --- |
| 201 `{vote_id}` · skip(방 없음) | `complete` |
| 409 `VOTING_CLOSED` · `ALREADY_VOTED` | `complete` + 로그 `jury_vote_skipped`(정상 종료) |
| 409 `STALE_GENERATION` | `complete`(결과 폐기) |
| 404 `NOT_FOUND`(snapshot 또는 cast) | `cancel(SNAPSHOT_NOT_FOUND)`. 본문 없는 404 는 "그 밖" |
| 403 `NOT_AI_JUROR` | `fail("NOT_AI_JUROR", retry_after_s=None)` |
| 401 · 그 밖의 403 | `fail("BACKEND_AUTH", 60)` |
| 422 `INVALID_REQUEST` | `fail("SCHEMA_INVALID", None)` |
| `BackendUnavailable` | `fail("BACKEND_UNAVAILABLE", retry_after_s)` |
| 그 밖의 예외 | 상위로(워커 `HANDLER_ERROR`) |

`prepare_case._settle_backend_error` 는 401·403 을 모두 `BACKEND_AUTH` 로 묶는다. 18 §3.5 는
**403 `NOT_AI_JUROR` 만 따로** 떼라고 하므로 그 분기를 이 모듈에 새로 둔다(prepare_case 를 고치지
않는다).

application 은 어댑터를 import 하지 않는다. 백엔드 예외는 속성으로 알아본다
(`status`·`code`, `error_code == "BACKEND_UNAVAILABLE"`·`retry_after_s`).
"""

from __future__ import annotations

import asyncio
from typing import Protocol

from geoji_ai.application.llm_gateway import ScopedLLM
from geoji_ai.contracts.jobs import Job
from geoji_ai.core.config import Settings
from geoji_ai.core.logging import get_logger
from geoji_ai.graphs.jury_vote import JuryVoteDeps, run_jury_vote
from geoji_ai.ports.backend import BackendPort, SnapshotNotFound
from geoji_ai.ports.jobs import JobsPort
from geoji_ai.ports.llm import LLMPort

__all__ = ["JuryVoteHandler"]

log = get_logger(__name__)

BACKEND_UNAVAILABLE = "BACKEND_UNAVAILABLE"
BACKEND_AUTH = "BACKEND_AUTH"
BACKEND_AUTH_RETRY_AFTER_S = 60
NOT_AI_JUROR = "NOT_AI_JUROR"
NOT_FOUND = "NOT_FOUND"
SCHEMA_INVALID = "SCHEMA_INVALID"
SNAPSHOT_NOT_FOUND = "SNAPSHOT_NOT_FOUND"

_STALE_GENERATION = "STALE_GENERATION"
#: 마감·확정·중복은 정상 종료다(18 §3.5).
_SKIP_CODES = frozenset({"VOTING_CLOSED", "ALREADY_VOTED"})


class _Context(Protocol):
    jobs: JobsPort
    backend: BackendPort
    llm: LLMPort | ScopedLLM | None
    settings: Settings
    semaphore: asyncio.Semaphore
    generation_id: str
    worker_id: str


class JuryVoteHandler:
    async def __call__(self, job: Job, ctx: _Context) -> None:
        deps = JuryVoteDeps(
            backend=ctx.backend,
            settings=ctx.settings,
            semaphore=ctx.semaphore,
            generation_id=ctx.generation_id,
            llm=ctx.llm,
        )
        try:
            state = await run_jury_vote(job, deps)
        except SnapshotNotFound:
            await ctx.jobs.cancel(
                job.id, ctx.worker_id, ctx.generation_id, error_code=SNAPSHOT_NOT_FOUND
            )
            return
        except Exception as exc:
            if not await _settle_backend_error(job, ctx, exc):
                raise
            return
        log.info("jury_vote_done", status=state.get("status"), outcome=state.get("outcome"))
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
    if status == 404 and code == NOT_FOUND:
        # 계약의 404 `NOT_FOUND` 만 삭제로 본다. 본문 없는 404(`HTTP_404`, 경로 미배포 등)는
        # 아래 else 로 가서 재시도·알림 대상이다(리뷰 9/20)
        await ctx.jobs.cancel(
            job.id, ctx.worker_id, ctx.generation_id, error_code=SNAPSHOT_NOT_FOUND
        )
    elif status == 403 and code == NOT_AI_JUROR:
        # 설정 불일치다. 재시도해도 같으므로 간격을 두지 않는다(attempts 소진 뒤 알림).
        await ctx.jobs.fail(
            job.id, ctx.worker_id, ctx.generation_id, error_code=NOT_AI_JUROR, retry_after_s=None
        )
    elif status in (401, 403):
        await ctx.jobs.fail(
            job.id,
            ctx.worker_id,
            ctx.generation_id,
            error_code=BACKEND_AUTH,
            retry_after_s=BACKEND_AUTH_RETRY_AFTER_S,
        )
    elif status == 422:
        await ctx.jobs.fail(
            job.id, ctx.worker_id, ctx.generation_id, error_code=SCHEMA_INVALID, retry_after_s=None
        )
    elif code in _SKIP_CODES:
        log.info("jury_vote_skipped", reason=code, job_id=job.id)
        await ctx.jobs.complete(job.id, ctx.worker_id, ctx.generation_id)
    elif code == _STALE_GENERATION:
        await ctx.jobs.complete(job.id, ctx.worker_id, ctx.generation_id)
    else:
        await ctx.jobs.fail(
            job.id, ctx.worker_id, ctx.generation_id, error_code=code, retry_after_s=None
        )
    return True
