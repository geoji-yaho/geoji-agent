"""존재하지 않는 snapshot은 모델을 부르지 않고 job을 취소한다."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from geoji_ai.adapters.backend_http import BackendHttp, BackendRejected
from geoji_ai.adapters.fake_llm import FakeLLM
from geoji_ai.application.prepare_case import PrepareHandler
from geoji_ai.application.sentence_case import SentenceHandler
from geoji_ai.core.config import Settings
from tests.unit.test_graph_c import make_job


@pytest.mark.parametrize("kind", ["PREPARE", "SENTENCE"])
async def test_missing_snapshot_cancels_once_without_model_or_retry(kind):
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(404, json={"code": "NOT_FOUND"})
        )
    )
    backend = BackendHttp("http://backend", "local-token", client=client)
    job = make_job()
    if kind == "PREPARE":
        job = job.model_copy(
            update={
                "kind": "PREPARE",
                "payload": {"post_id": "post-1", "post_version": 1, "audience_version": 1},
            }
        )
    jobs = SimpleNamespace(
        cancel=AsyncMock(return_value=True), complete=AsyncMock(), fail=AsyncMock()
    )
    llm = FakeLLM()
    ctx = SimpleNamespace(
        jobs=jobs,
        backend=backend,
        llm=llm,
        memory=None,
        preparation=object(),
        semaphore=asyncio.Semaphore(1),
        settings=Settings(_env_file=None),
        generation_id="gen-1",
        worker_id="worker-1",
    )
    try:
        await (PrepareHandler() if kind == "PREPARE" else SentenceHandler())(job, ctx)
    finally:
        await backend.aclose()
    jobs.cancel.assert_awaited_once_with(
        job.id, "worker-1", "gen-1", error_code="SNAPSHOT_NOT_FOUND"
    )
    jobs.complete.assert_not_awaited()
    jobs.fail.assert_not_awaited()
    assert llm.calls == []


async def test_unrecognized_snapshot_404_remains_an_error():
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(404, json={"code": "UNKNOWN_ENDPOINT"})
        )
    )
    backend = BackendHttp("http://backend", "local-token", client=client)
    try:
        with pytest.raises(BackendRejected, match="UNKNOWN_ENDPOINT"):
            await backend.snapshot("job", "generation")
    finally:
        await backend.aclose()
