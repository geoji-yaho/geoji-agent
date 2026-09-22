"""#58: 오류 처리 공유 전후에 핸들러별 큐 상태와 분기 우선순위를 고정한다."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from geoji_ai.adapters.fake_llm import FakeLLM
from geoji_ai.application.prepare_case import PrepareHandler
from geoji_ai.application.sentence_case import SentenceHandler, SentenceStubHandler
from geoji_ai.core.config import Settings
from tests.unit.test_graph_c import make_job
from tests.unit.test_jury_vote_handler import Rejected, Unavailable, run_handler


@pytest.mark.parametrize("handler_kind", ["prepare", "sentence", "stub", "jury"])
@pytest.mark.parametrize(
    "error,default,jury",
    [
        (Unavailable(7), ("fail", "BACKEND_UNAVAILABLE", 7), None),
        (Rejected(401, "UNAUTHORIZED"), ("fail", "BACKEND_AUTH", 60), None),
        (Rejected(403, "FORBIDDEN"), ("fail", "BACKEND_AUTH", 60), None),
        (Rejected(401, "STALE_GENERATION"), ("fail", "BACKEND_AUTH", 60), None),
        (Rejected(401, "ALREADY_VOTED"), ("fail", "BACKEND_AUTH", 60), None),
        (Rejected(409, "STALE_GENERATION"), ("complete", None, None), None),
        (Rejected(409, "OTHER_CONFLICT"), ("fail", "OTHER_CONFLICT", None), None),
        (Rejected(404, "HTTP_404"), ("fail", "HTTP_404", None), None),
        (
            Rejected(404, "NOT_FOUND"),
            ("fail", "NOT_FOUND", None),
            ("cancel", "SNAPSHOT_NOT_FOUND", None),
        ),
        (
            Rejected(403, "NOT_AI_JUROR"),
            ("fail", "BACKEND_AUTH", 60),
            ("fail", "NOT_AI_JUROR", None),
        ),
        (
            Rejected(422, "INVALID_REQUEST"),
            ("fail", "INVALID_REQUEST", None),
            ("fail", "SCHEMA_INVALID", None),
        ),
        (
            Rejected(422, "STALE_GENERATION"),
            ("complete", None, None),
            ("fail", "SCHEMA_INVALID", None),
        ),
        (
            Rejected(409, "VOTING_CLOSED"),
            ("fail", "VOTING_CLOSED", None),
            ("complete", None, None),
        ),
        (
            Rejected(409, "ALREADY_VOTED"),
            ("fail", "ALREADY_VOTED", None),
            ("complete", None, None),
        ),
    ],
)
async def test_backend_error_preserves_handler_policy(
    monkeypatch, handler_kind, error, default, jury
):
    action, code, retry = jury if handler_kind == "jury" and jury is not None else default
    if handler_kind == "jury":
        ctx = await run_handler(cast_error=error)
        assert ctx.jobs.completed == (["job-1"] if action == "complete" else [])
        assert ctx.jobs.cancelled == ([("job-1", code)] if action == "cancel" else [])
        assert ctx.jobs.failures == ([(code, retry)] if action == "fail" else [])
        return

    ctx = make_error_context(error)
    job = make_job()
    handler = make_error_handler(monkeypatch, handler_kind, error)
    await handler(job, ctx)

    args = (job.id, ctx.worker_id, ctx.generation_id)
    kwargs = {} if action == "complete" else {"error_code": code, "retry_after_s": retry}
    getattr(ctx.jobs, action).assert_awaited_once_with(*args, **kwargs)
    for other in {"complete", "cancel", "fail"} - {action}:
        getattr(ctx.jobs, other).assert_not_awaited()


def make_error_context(error):
    return SimpleNamespace(
        jobs=SimpleNamespace(complete=AsyncMock(), cancel=AsyncMock(), fail=AsyncMock()),
        backend=SimpleNamespace(
            begin_generation=AsyncMock(side_effect=error), snapshot=AsyncMock(side_effect=error)
        ),
        llm=FakeLLM(),
        memory=None,
        preparation=object(),
        settings=Settings(_env_file=None),
        semaphore=asyncio.Semaphore(1),
        generation_id="gen-1",
        worker_id="worker-1",
    )


def make_error_handler(monkeypatch, kind, error):
    if kind == "prepare":
        monkeypatch.setattr(
            "geoji_ai.application.prepare_case.run_preparation", AsyncMock(side_effect=error)
        )
        return PrepareHandler()
    if kind == "stub":
        return SentenceStubHandler()
    return SentenceHandler()


@pytest.mark.parametrize("handler_kind", ["prepare", "sentence", "stub"])
async def test_unknown_error_propagates_without_settling_job(monkeypatch, handler_kind):
    error = RuntimeError("알 수 없는 실패")
    ctx = make_error_context(error)
    handler = make_error_handler(monkeypatch, handler_kind, error)
    with pytest.raises(RuntimeError, match="알 수 없는 실패"):
        await handler(make_job(), ctx)
    ctx.jobs.complete.assert_not_awaited()
    ctx.jobs.cancel.assert_not_awaited()
    ctx.jobs.fail.assert_not_awaited()
