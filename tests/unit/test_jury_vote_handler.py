"""JURY_VOTE 핸들러 — 백엔드 응답별 job 처리(18 §3.5·§4.2).

가짜 큐·백엔드·LLM 만 쓴다. 네트워크·DB·키 없음.

먼저 실패시킬 케이스(18 문서 헤더) 중 둘이 여기다 — 마감된 글 skip · 남의 id 로 투표 403.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from geoji_ai.adapters.fake_llm import FakeLLM
from geoji_ai.application.jury_vote_case import JuryVoteHandler
from geoji_ai.contracts.case import CaseSnapshot
from geoji_ai.core.config import Settings
from geoji_ai.ports.backend import JuryVoteRequest, JuryVoteResult, SnapshotNotFound
from tests.unit.test_jury_vote_graph import GENERATION_ID, make_job, make_snapshot

WORKER_ID = "worker-1"


class Rejected(Exception):
    """어댑터 `BackendRejected` 모양(속성으로 판별). application 은 어댑터를 import 하지 않는다."""

    def __init__(self, status: int, code: str) -> None:
        super().__init__(f"{status} {code}")
        self.status = status
        self.code = code


class Unavailable(Exception):
    """어댑터 `BackendUnavailable` 모양."""

    def __init__(self, retry_after_s: float = 5) -> None:
        super().__init__("BACKEND_UNAVAILABLE")
        self.error_code = "BACKEND_UNAVAILABLE"
        self.retry_after_s = retry_after_s


class FakeJobs:
    def __init__(self) -> None:
        self.completed: list[str] = []
        self.cancelled: list[tuple[str, str]] = []
        self.failures: list[tuple[str, float | None]] = []

    async def complete(self, job_id: str, worker_id: str, generation_id: str) -> bool:
        self.completed.append(job_id)
        return True

    async def cancel(
        self, job_id: str, worker_id: str, generation_id: str, *, error_code: str
    ) -> bool:
        self.cancelled.append((job_id, error_code))
        return True

    async def fail(
        self,
        job_id: str,
        worker_id: str,
        generation_id: str,
        error_code: str,
        retry_after_s: float | None,
    ) -> bool:
        self.failures.append((error_code, retry_after_s))
        return True


class FakeBackend:
    def __init__(
        self,
        snapshot: CaseSnapshot,
        *,
        snapshot_error: Exception | None = None,
        cast_error: Exception | None = None,
    ) -> None:
        self._snapshot = snapshot
        self.snapshot_error = snapshot_error
        self.cast_error = cast_error
        self.casts: list[JuryVoteRequest] = []

    async def snapshot(self, job_id: str, generation_id: str) -> CaseSnapshot:
        if self.snapshot_error is not None:
            raise self.snapshot_error
        return self._snapshot

    async def cast_jury_vote(self, post_id: str, req: JuryVoteRequest) -> JuryVoteResult:
        self.casts.append(req)
        if self.cast_error is not None:
            raise self.cast_error
        return JuryVoteResult(vote_id="vote-1")


def make_ctx(backend: FakeBackend) -> Any:
    return SimpleNamespace(
        jobs=FakeJobs(),
        backend=backend,
        llm=FakeLLM(),
        settings=Settings(_env_file=None),
        semaphore=asyncio.Semaphore(4),
        generation_id=GENERATION_ID,
        worker_id=WORKER_ID,
    )


async def run_handler(
    *,
    snapshot_error: Exception | None = None,
    cast_error: Exception | None = None,
    payload_room_id: str | None = None,
) -> Any:
    backend = FakeBackend(make_snapshot(), snapshot_error=snapshot_error, cast_error=cast_error)
    ctx = make_ctx(backend)
    await JuryVoteHandler()(make_job(room_id=payload_room_id or "room-ddegeoji-01"), ctx)
    return ctx


# --- 201 · skip → complete ----------------------------------------------------------


async def test_201_이면_complete_다():
    ctx = await run_handler()

    assert ctx.jobs.completed == ["job-1"]
    assert ctx.jobs.failures == []
    assert ctx.jobs.cancelled == []
    assert len(ctx.backend.casts) == 1
    assert ctx.backend.casts[0].voter_id == "bot-ddegeoji"


async def test_방이_공유에서_빠졌으면_cast_없이_complete_다():
    ctx = await run_handler(payload_room_id="room-gone")

    assert ctx.jobs.completed == ["job-1"]
    assert ctx.backend.casts == []


# --- 409 → complete -----------------------------------------------------------------


@pytest.mark.parametrize("code", ["VOTING_CLOSED", "ALREADY_VOTED", "STALE_GENERATION"])
async def test_409_는_정상_종료라_complete_다(code: str):
    ctx = await run_handler(cast_error=Rejected(409, code))

    assert ctx.jobs.completed == ["job-1"]
    assert ctx.jobs.failures == []
    assert ctx.jobs.cancelled == []


# --- 404 → cancel -------------------------------------------------------------------


async def test_snapshot_404_는_cancel_이다():
    ctx = await run_handler(snapshot_error=SnapshotNotFound())

    assert ctx.jobs.cancelled == [("job-1", "SNAPSHOT_NOT_FOUND")]
    assert ctx.jobs.completed == []
    assert ctx.jobs.failures == []


async def test_cast_404_도_cancel_이다():
    ctx = await run_handler(cast_error=Rejected(404, "NOT_FOUND"))

    assert ctx.jobs.cancelled == [("job-1", "SNAPSHOT_NOT_FOUND")]
    assert ctx.jobs.completed == []


# --- 403 · 401 · 422 → fail ---------------------------------------------------------


async def test_403_NOT_AI_JUROR_는_재시도_간격_없이_fail_이다():
    ctx = await run_handler(cast_error=Rejected(403, "NOT_AI_JUROR"))

    assert ctx.jobs.failures == [("NOT_AI_JUROR", None)]
    assert ctx.jobs.failures[0][1] is None
    assert ctx.jobs.completed == []


async def test_401_은_BACKEND_AUTH_60_이다():
    ctx = await run_handler(cast_error=Rejected(401, "UNAUTHORIZED"))

    assert ctx.jobs.failures == [("BACKEND_AUTH", 60)]


async def test_그_밖의_403_도_BACKEND_AUTH_60_이다():
    ctx = await run_handler(cast_error=Rejected(403, "FORBIDDEN"))

    assert ctx.jobs.failures == [("BACKEND_AUTH", 60)]


async def test_422_는_SCHEMA_INVALID_다():
    ctx = await run_handler(cast_error=Rejected(422, "INVALID_REQUEST"))

    assert ctx.jobs.failures == [("SCHEMA_INVALID", None)]


# --- 백엔드 불가 · 모르는 예외 -------------------------------------------------------


async def test_BackendUnavailable_은_retry_after_를_그대로_싣는다():
    ctx = await run_handler(cast_error=Unavailable(5))

    assert ctx.jobs.failures == [("BACKEND_UNAVAILABLE", 5)]
    assert ctx.jobs.completed == []


async def test_모르는_예외는_상위로_올린다():
    with pytest.raises(RuntimeError):
        await run_handler(cast_error=RuntimeError("boom"))


async def test_본문_없는_404_는_삭제가_아니라_fail_이다():
    """리뷰 9/20: 경로 미배포(Spring 404, `HTTP_404`)를 삭제로 오인해 cancel 하지 않는다."""
    ctx = await run_handler(cast_error=Rejected(404, "HTTP_404"))

    assert ctx.jobs.cancelled == []
    assert ctx.jobs.failures == [("HTTP_404", None)]
