"""heartbeat lease 판단(02 §3.3). DB 없이 가짜 `JobsPort` 로 본다.

기준: 호출마다 `asyncio.timeout(interval_s)`. 타임아웃·예외는 실패다. 마지막 성공 갱신
시각(처음은 시작 시각)에서 경과한 시간 + `interval_s` ≥ `lease_s` 이면 `target` 을 취소한다.
`False`(0행)는 즉시 취소한다. 가짜 시계 대신 `interval_s`·`lease_s` 를 작게 준다.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Sequence

from geoji_ai.contracts.jobs import Job
from geoji_ai.workers.heartbeat import start_heartbeat

INTERVAL_S = 0.2
LEASE_S = 0.6


class ScriptedJobs:
    """`heartbeat()` 가 대본(`ok`·`false`·`raise`·`hang`)대로 답하는 가짜 `JobsPort`.

    대본이 끝나면 마지막 동작을 반복한다.
    """

    def __init__(self, script: Sequence[str]) -> None:
        self._script = list(script)
        self.calls = 0
        self.last_ok_at: float | None = None

    async def claim(self, kinds: Sequence[str], worker_id: str) -> Job | None:
        return None

    async def heartbeat(self, job_id: str, worker_id: str, generation_id: str) -> bool:
        action = self._script[min(self.calls, len(self._script) - 1)]
        self.calls += 1
        if action == "ok":
            self.last_ok_at = time.monotonic()
            return True
        if action == "false":
            return False
        if action == "raise":
            raise ConnectionError("일시적 DB 오류")
        # hang: 끝나지 않는 await(멈춘 연결).
        await asyncio.Event().wait()
        return True

    async def complete(self, job_id: str, worker_id: str, generation_id: str) -> bool:
        return True

    async def fail(
        self,
        job_id: str,
        worker_id: str,
        generation_id: str,
        error_code: str,
        retry_after_s: float | None,
    ) -> bool:
        return True

    async def release(self, job_id: str, worker_id: str, generation_id: str) -> bool:
        return True


async def _run(jobs: ScriptedJobs, *, watch_s: float) -> tuple[bool, float, float | None]:
    """핸들러 대역 task 와 heartbeat 를 띄우고 `watch_s` 안에 취소되는지 본다.

    (정리 전 target 이 취소됐는지, 시작 시각, 취소된 시각 또는 None) 을 돌려준다.
    """
    target: asyncio.Task[None] = asyncio.create_task(asyncio.sleep(30))
    cancelled_at: list[float] = []
    target.add_done_callback(lambda _: cancelled_at.append(time.monotonic()))
    started = time.monotonic()
    beat = start_heartbeat(
        jobs,
        job_id="job-1",
        worker_id="host:1:PREPARE",
        generation_id="gen-1",
        interval_s=INTERVAL_S,
        lease_s=LEASE_S,
        target=target,
    )
    try:
        await asyncio.wait({target}, timeout=watch_s)
        done_at = cancelled_at[0] if cancelled_at else None
        # 아래 정리가 target 을 취소하므로 그 전에 상태를 잡는다.
        return target.cancelled(), started, done_at
    finally:
        for task in (beat, target):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


async def test_heartbeat_가_멈추면_호출_타임아웃을_실패로_세고_취소한다() -> None:
    jobs = ScriptedJobs(["hang"])

    cancelled, started, done_at = await _run(jobs, watch_s=2.0)

    # 멈춘 호출이 타임아웃으로 끊겨 실패로 세어졌고, 기준을 넘어 취소됐다.
    assert done_at is not None, "멈춘 heartbeat 가 lease 상실로 판정되지 않았다"
    assert cancelled
    # 시작 기준 lease 만료 전에 멈췄다.
    assert done_at - started < LEASE_S


async def test_성공_뒤_실패가_이어지면_lease_만료_전에_취소한다() -> None:
    jobs = ScriptedJobs(["ok", "raise"])

    cancelled, _, done_at = await _run(jobs, watch_s=3.0)

    assert done_at is not None
    assert cancelled
    assert jobs.last_ok_at is not None
    # 마지막 성공 갱신이 민 lease(`last_ok + LEASE_S`)가 끝나기 전에 취소됐다.
    # 경과 + interval ≥ lease 에서 멈추므로 실패 2회(경과 ≈ 0.4초)에서 취소된다.
    assert done_at - jobs.last_ok_at < LEASE_S - INTERVAL_S / 2
    assert jobs.calls == 3  # ok, raise, raise


async def test_실패_사이에_성공이_끼면_기준이_갱신돼_취소하지_않는다() -> None:
    jobs = ScriptedJobs(["raise", "ok"] * 20)

    cancelled, _, done_at = await _run(jobs, watch_s=1.5)

    assert done_at is None, "성공이 끼었는데 취소됐다"
    assert not cancelled
    assert jobs.calls >= 5


async def test_False_는_즉시_취소한다() -> None:
    jobs = ScriptedJobs(["false"])

    cancelled, started, done_at = await _run(jobs, watch_s=2.0)

    assert done_at is not None
    assert cancelled
    assert jobs.calls == 1
    # 첫 호출(interval 뒤)에서 바로 멈춘다. 실패 누적을 기다리지 않는다.
    assert done_at - started < INTERVAL_S * 2
