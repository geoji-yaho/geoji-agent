"""백엔드 예외를 큐 상태로 옮긴다. 예외 클래스 대신 속성으로 판별한다."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

from geoji_ai.contracts.jobs import Job
from geoji_ai.core.logging import get_logger
from geoji_ai.ports.jobs import JobsPort

BACKEND_UNAVAILABLE = "BACKEND_UNAVAILABLE"
BACKEND_AUTH = "BACKEND_AUTH"
BACKEND_AUTH_RETRY_AFTER_S = 60

log = get_logger(__name__)


class _Context(Protocol):
    jobs: JobsPort
    worker_id: str
    generation_id: str


@dataclass(frozen=True)
class BackendErrorAction:
    action: Literal["complete", "cancel", "fail"]
    error_code: str | None = None
    log_event: str | None = None


async def settle_backend_error(
    job: Job,
    ctx: _Context,
    exc: Exception,
    *,
    overrides: Mapping[tuple[int | None, str | None], BackendErrorAction] | None = None,
) -> bool:
    """처리했으면 True, 모르는 예외면 False.

    규칙은 (상태, 코드) → 인증 오류 → (상태, None) → (None, 코드) 순서다.
    정확히 지정한 예외만 인증 처리를 덮어쓰며, 나머지는 공통 기본 동작을 따른다.
    """
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

    rules = overrides or {}
    action = rules.get((status, code))
    if action is None and status in (401, 403):
        await ctx.jobs.fail(
            job.id,
            ctx.worker_id,
            ctx.generation_id,
            error_code=BACKEND_AUTH,
            retry_after_s=BACKEND_AUTH_RETRY_AFTER_S,
        )
        return True
    if action is None:
        action = rules.get((status, None)) or rules.get((None, code))
    if action is None:
        action = BackendErrorAction("complete" if code == "STALE_GENERATION" else "fail")

    if action.log_event is not None:
        log.info(action.log_event, reason=code, job_id=job.id)
    if action.action == "complete":
        await ctx.jobs.complete(job.id, ctx.worker_id, ctx.generation_id)
    elif action.action == "cancel":
        await ctx.jobs.cancel(
            job.id, ctx.worker_id, ctx.generation_id, error_code=action.error_code or code
        )
    else:
        await ctx.jobs.fail(
            job.id,
            ctx.worker_id,
            ctx.generation_id,
            error_code=action.error_code or code,
            retry_after_s=None,
        )
    return True
