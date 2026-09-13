"""lease 갱신 task(02 §3.3).

`HEARTBEAT_SECONDS` 주기로 `jobs.heartbeat()` 를 부른다. 갱신이 0행이면
(lease 만료·소유권 상실) 핸들러 task 를 취소한다. **취소는 상위로 전파**되고
세마포어·세션 정리는 호출자의 `finally` 가 한다.

호출마다 `asyncio.timeout(interval_s)` 를 건다. 타임아웃·예외는 실패다. 마지막 성공 갱신
시각(처음은 시작 시각)에서 경과한 시간 + `interval_s` 가 `lease_s` 이상이면 다음 갱신이
만료 전에 닿지 못하므로 lease 를 잃은 것으로 보고 미리 취소한다. 기본값(5·15)이면 경과
10초, 연속 2회 실패에서 취소된다.

`PostgresJobs` 는 호출마다 새 `AsyncSession` 을 연다(02 §3.2 마지막 줄). 그래서 이
heartbeat task 와 핸들러 task 는 세션을 공유하지 않는다 — 여기서 따로 할 일은 없다.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from geoji_ai.core.logging import get_logger
from geoji_ai.ports.jobs import JobsPort

__all__ = ["start_heartbeat"]

log = get_logger(__name__)


def start_heartbeat(
    jobs: JobsPort,
    *,
    job_id: str,
    worker_id: str,
    generation_id: str,
    interval_s: float,
    lease_s: float,
    target: asyncio.Task[Any],
) -> asyncio.Task[None]:
    """`interval_s` 마다 lease 를 민다. lease 를 잃으면 `target` 을 취소하고 끝난다."""

    async def _beat() -> None:
        last_ok = time.monotonic()
        while True:
            await asyncio.sleep(interval_s)
            try:
                # 멈춘 연결이면 await 가 끝나지 않는다. 한 주기를 넘기면 실패로 센다.
                async with asyncio.timeout(interval_s):
                    alive = await jobs.heartbeat(job_id, worker_id, generation_id)
            except Exception:
                # 일시적 DB 오류·타임아웃. 조용히 넘기면 lease 가 끝난 채 핸들러가 계속 돌고,
                # reaper 가 회수한 job 을 다른 워커가 다시 집는다(작업 5 면 LLM 이 두 번
                # 나간다). 다음 갱신이 만료 전에 못 닿는 것이 확실하면 잃은 것으로 본다.
                elapsed = time.monotonic() - last_ok
                log.exception("heartbeat_failed", elapsed_s=round(elapsed, 3))
                if elapsed + interval_s < lease_s:
                    continue
                alive = False
            else:
                if alive:
                    last_ok = time.monotonic()
            if not alive:
                # lease 를 잃었다. 결과를 저장하지 못하게 핸들러를 취소한다.
                log.warning("lease_lost")
                target.cancel()
                return

    return asyncio.create_task(_beat(), name="heartbeat")
