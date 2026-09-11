"""lease 갱신 task(02 §3.3).

`HEARTBEAT_SECONDS` 주기로 `jobs.heartbeat()` 를 부른다. 갱신이 0행이면
(lease 만료·소유권 상실) 핸들러 task 를 취소한다. **취소는 상위로 전파**되고
세마포어·세션 정리는 호출자의 `finally` 가 한다.

`PostgresJobs` 는 호출마다 새 `AsyncSession` 을 연다(02 §3.2 마지막 줄). 그래서 이
heartbeat task 와 핸들러 task 는 세션을 공유하지 않는다 — 여기서 따로 할 일은 없다.
"""

from __future__ import annotations

import asyncio
from typing import Any

from geoji_ai.core.logging import get_logger
from geoji_ai.ports.jobs import JobsPort

__all__ = ["start_heartbeat"]

log = get_logger(__name__)

#: heartbeat 가 이만큼 연속으로 예외를 내면 lease 를 잃은 것으로 본다.
#: 기본 설정(`HEARTBEAT_SECONDS=5`, `JOB_LEASE_SECONDS=15`)에서 3회는 lease 창과 같다.
_MAX_CONSECUTIVE_ERRORS = 3


def start_heartbeat(
    jobs: JobsPort,
    *,
    job_id: str,
    worker_id: str,
    generation_id: str,
    interval_s: float,
    target: asyncio.Task[Any],
) -> asyncio.Task[None]:
    """`interval_s` 마다 lease 를 민다. 실패하면 `target` 을 취소하고 끝난다."""

    async def _beat() -> None:
        errors = 0
        while True:
            await asyncio.sleep(interval_s)
            try:
                alive = await jobs.heartbeat(job_id, worker_id, generation_id)
            except Exception:
                # 일시적 DB 오류. 조용히 끝나면 lease 갱신이 멈춘 채 핸들러가 계속 돌고,
                # reaper 가 회수한 job 을 다른 워커가 다시 집는다(작업 5 면 LLM 이 두 번
                # 나간다). 연속 실패가 남은 lease 를 다 쓸 만큼이면 잃은 것으로 본다.
                errors += 1
                log.exception("heartbeat_failed", attempt=errors)
                if errors < _MAX_CONSECUTIVE_ERRORS:
                    continue
                alive = False
            else:
                errors = 0
            if not alive:
                # lease 를 잃었다. 결과를 저장하지 못하게 핸들러를 취소한다.
                log.warning("lease_lost")
                target.cancel()
                return

    return asyncio.create_task(_beat(), name="heartbeat")
