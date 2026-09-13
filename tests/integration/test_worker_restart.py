"""워커 재기동(03 §1·§4.1 "먼저 실패시킬 케이스", §4.2 `test_worker_restart`).

job RUNNING 중(가짜 백엔드가 `begin-generation` 뒤 응답을 붙잡는다) 워커 **프로세스**를 죽인다 →
짧은 lease 가 만료되면 `reap()` → 새 워커 프로세스의 재claim 은 **새** generation →
새 generation 으로 SUCCEEDED → 이전 generation 의 `generation-failed` 는 409.

가짜 백엔드는 이 테스트 프로세스 안의 uvicorn(별도 스레드·빈 포트)이다. 워커는
`python -m geoji_ai.cli worker` subprocess 다. Windows 에서도 돌도록 `proc.kill()` 을 쓴다.
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import threading
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.pool import NullPool

from geoji_ai.adapters.backend_http import BackendHttp, BackendRejected
from geoji_ai.adapters.postgres_jobs import make_engine, reap
from tests.fakes.backend_app import FAKE_SERVICE_TOKEN, create_fake_backend

Enqueue = Callable[..., Awaitable[str]]
FetchJob = Callable[[str], Awaitable[dict[str, Any]]]

REPO_ROOT = Path(__file__).resolve().parents[2]
VERDICT_ID = "v-restart"
POST_ID = "p-restart"
LEASE_S = 2
WAIT_S = 45.0


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def _serve(app: Any, port: int) -> Iterator[None]:
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="fake-backend", daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        threading.Event().wait(0.05)
    assert server.started, "가짜 백엔드가 뜨지 않았다"
    try:
        yield
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def _start_worker(database_url: str, backend_url: str, log_path: Path) -> subprocess.Popen[bytes]:
    # 벤더 키를 넣지 않는다: llm=None → SENTENCE 는 스텁 위임 경로(실제 벤더 호출 금지)
    inherited = {k: v for k, v in os.environ.items() if k not in ("OPENAI_API_KEY", "XAI_API_KEY")}
    env = {
        **inherited,
        "DATABASE_URL": database_url,
        "BACKEND_INTERNAL_URL": backend_url,
        "SERVICE_AUTH_TOKEN": FAKE_SERVICE_TOKEN,
        "APP_ENV": "development",
        "WORKER_SLOTS": '{"SENTENCE": 1}',
        "JOB_LEASE_SECONDS": str(LEASE_S),
        "HEARTBEAT_SECONDS": "1",
        "WORKER_POLL_MS": "50",
        "PYTHONUNBUFFERED": "1",
    }
    log_file = log_path.open("ab")
    try:
        return subprocess.Popen(
            [sys.executable, "-m", "geoji_ai.cli", "worker"],
            cwd=REPO_ROOT,
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
    finally:
        log_file.close()


def _kill(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=10)


async def _wait_for(check: Callable[[], Awaitable[bool]], what: str) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + WAIT_S
    while loop.time() < deadline:
        if await check():
            return
        await asyncio.sleep(0.1)
    pytest.fail(f"{WAIT_S}s 안에 {what} 가 되지 않았다")


async def test_RUNNING_중_프로세스가_죽으면_새_generation_으로_끝나고_이전_generation_은_409(
    test_database_url: str,
    engine: AsyncEngine,
    enqueue: Enqueue,
    fetch_job: FetchJob,
    tmp_path: Path,
):
    fake_engine = make_engine(test_database_url, poolclass=NullPool)
    app = create_fake_backend(jobs_engine=fake_engine)
    fake = app.state.fake
    deadline_at = datetime.now(UTC) + timedelta(seconds=120)
    fake.seed_verdict(VERDICT_ID, verdict_version=1, post_id=POST_ID, deadline_at=deadline_at)
    port = _free_port()
    backend_url = f"http://127.0.0.1:{port}"
    log_path = tmp_path / "worker.log"

    hold = threading.Event()
    fake.hold_after_begin = hold
    job_id = await enqueue(
        "SENTENCE",
        verdict_id=VERDICT_ID,
        verdict_version=1,
        post_id=POST_ID,
        deadline_at=deadline_at,
    )

    try:
        with _serve(app, port):
            # 1) 첫 워커가 begin-generation 을 마치고 응답을 기다리는 중에 죽인다.
            first = _start_worker(test_database_url, backend_url, log_path)
            try:
                began = await asyncio.to_thread(fake.began.wait, WAIT_S)
                assert began, log_path.read_text(encoding="utf-8", errors="replace")
                row = await fetch_job(job_id)
                assert row["status"] == "RUNNING"
                old_generation = str(row["generation_id"])
                assert fake.verdicts[VERDICT_ID].active_generation_id == old_generation
            finally:
                _kill(first)
            hold.set()
            fake.hold_after_begin = None

            # 2) lease 만료 → reaper 회수. 마감 전 SENTENCE 는 QUEUED 로 되살아난다.
            async def reaped() -> bool:
                return await reap(engine) > 0

            await _wait_for(reaped, "lease 회수")
            row = await fetch_job(job_id)
            assert row["status"] == "QUEUED"
            assert row["generation_id"] is None
            assert row["last_error_code"] == "LEASE_EXPIRED"
            assert fake.verdicts[VERDICT_ID].sentence_status == "PENDING"

            # 3) 새 워커 프로세스가 새 generation 으로 끝낸다.
            second = _start_worker(test_database_url, backend_url, log_path)
            try:

                async def succeeded() -> bool:
                    return (await fetch_job(job_id))["status"] == "SUCCEEDED"

                await _wait_for(succeeded, "SUCCEEDED")
            finally:
                _kill(second)

            row = await fetch_job(job_id)
            new_generation = str(row["generation_id"])
            assert new_generation != old_generation
            assert row["attempts"] == 2
            state = fake.verdicts[VERDICT_ID]
            assert (state.sentence_status, state.sentence_source, state.text_status) == (
                "FINAL",
                "RULE",
                "TEMPLATE_READY",
            )
            assert state.failed_generations == {new_generation: "AI_NOT_READY"}

            # 4) 이전 generation 의 generation-failed 는 409.
            backend = BackendHttp(backend_url, FAKE_SERVICE_TOKEN)
            try:
                with pytest.raises(BackendRejected) as caught:
                    await backend.generation_failed(
                        VERDICT_ID,
                        job_id=job_id,
                        generation_id=old_generation,
                        error_code="AI_NOT_READY",
                    )
            finally:
                await backend.aclose()
            assert (caught.value.status, caught.value.code) == (409, "STALE_GENERATION")
    finally:
        hold.set()
        await fake_engine.dispose()
