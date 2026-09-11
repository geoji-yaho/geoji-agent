"""큐 포트 (01 §3.6). 구현은 작업 2 의 Postgres 어댑터."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from geoji_ai.contracts.jobs import Job


@runtime_checkable
class JobsPort(Protocol):
    async def claim(self, kinds: Sequence[str], worker_id: str) -> Job | None: ...

    async def heartbeat(self, job_id: str, worker_id: str, generation_id: str) -> bool: ...

    async def complete(self, job_id: str, worker_id: str, generation_id: str) -> bool: ...

    async def fail(
        self,
        job_id: str,
        worker_id: str,
        generation_id: str,
        error_code: str,
        retry_after_s: float | None,
    ) -> bool: ...

    async def release(self, job_id: str, worker_id: str, generation_id: str) -> bool: ...
