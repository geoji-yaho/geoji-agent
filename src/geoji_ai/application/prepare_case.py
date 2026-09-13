"""PREPARE M2 스텁 핸들러(03 §3.3).

`complete` 만 한다. `trial_prep` 을 쓰지 않고 백엔드도 부르지 않는다.
작업 5 가 인라인 조서로 교체한다.
"""

from __future__ import annotations

from typing import Protocol

from geoji_ai.contracts.jobs import Job
from geoji_ai.ports.jobs import JobsPort

__all__ = ["PrepareStubHandler"]


class _Context(Protocol):
    jobs: JobsPort
    generation_id: str
    worker_id: str


class PrepareStubHandler:
    async def __call__(self, job: Job, ctx: _Context) -> None:
        await ctx.jobs.complete(job.id, ctx.worker_id, ctx.generation_id)
