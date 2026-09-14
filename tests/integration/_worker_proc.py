"""워커 subprocess · 가짜 백엔드 서버 도우미.

03 §4.2 `test_worker_restart` 와 08 §3.1 `test_failures` 가 같이 쓴다.
`test_worker_restart.py` 에 있던 도우미를 옮겼다. 동작은 그대로다.

- `_serve`: 가짜 백엔드를 이 테스트 프로세스 안의 uvicorn(별도 스레드·빈 포트)으로 띄운다
- `_start_worker`: `python -m geoji_ai.cli worker` subprocess. 벤더 키를 넣지 않는다
  (llm=None → 스텁)
- `_start_fake_llm_worker`: 이 모듈을 `python -m tests.integration._worker_proc` 로 띄운다.
  운영 조립(`workers.main.run_worker`)과 같은 모양이고 벤더 어댑터 자리만 FakeLLM 이다.
  지연은 `FAKE_WORKER_LLM_LATENCY_MS` 환경변수(ms)
- `_kill`: Windows 에서도 돌도록 `proc.kill()`
- `_wait_for`: 조건이 참이 될 때까지 짧은 간격으로 확인한다(고정 sleep 대기 대신)

접속 문자열은 비밀값이다. 로그·단언 메시지에 넣지 않는다.
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import threading
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
import uvicorn

from tests.fakes.backend_app import FAKE_SERVICE_TOKEN

REPO_ROOT = Path(__file__).resolve().parents[2]
LEASE_S = 2
WAIT_S = 45.0
POLL_S = 0.1

FAKE_LLM_LATENCY_ENV = "FAKE_WORKER_LLM_LATENCY_MS"
_VENDOR_KEYS = ("OPENAI_API_KEY", "XAI_API_KEY")


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


def _worker_env(
    database_url: str,
    backend_url: str,
    *,
    lease_s: float,
    extra_env: Mapping[str, str] | None,
) -> dict[str, str]:
    # 벤더 키를 넣지 않는다: 실제 벤더 호출 금지
    inherited = {k: v for k, v in os.environ.items() if k not in _VENDOR_KEYS}
    env = {
        **inherited,
        "DATABASE_URL": database_url,
        "BACKEND_INTERNAL_URL": backend_url,
        "SERVICE_AUTH_TOKEN": FAKE_SERVICE_TOKEN,
        "APP_ENV": "development",
        "WORKER_SLOTS": '{"SENTENCE": 1}',
        "JOB_LEASE_SECONDS": str(lease_s),
        "HEARTBEAT_SECONDS": "1",
        "WORKER_POLL_MS": "50",
        "PYTHONUNBUFFERED": "1",
    }
    env.update(extra_env or {})
    return env


def _popen(args: list[str], env: dict[str, str], log_path: Path) -> subprocess.Popen[bytes]:
    log_file = log_path.open("ab")
    try:
        return subprocess.Popen(
            [sys.executable, *args],
            cwd=REPO_ROOT,
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
    finally:
        log_file.close()


def _start_worker(
    database_url: str,
    backend_url: str,
    log_path: Path,
    *,
    lease_s: float = LEASE_S,
    extra_env: Mapping[str, str] | None = None,
) -> subprocess.Popen[bytes]:
    """`geoji-ai worker`. 벤더 키가 없어 SENTENCE 는 스텁 위임 경로다."""
    env = _worker_env(database_url, backend_url, lease_s=lease_s, extra_env=extra_env)
    return _popen(["-m", "geoji_ai.cli", "worker"], env, log_path)


def _start_fake_llm_worker(
    database_url: str,
    backend_url: str,
    log_path: Path,
    *,
    latency_ms: int = 0,
    lease_s: float = LEASE_S,
    extra_env: Mapping[str, str] | None = None,
) -> subprocess.Popen[bytes]:
    """운영 조립 + FakeLLM 워커. 모델 호출은 `latency_ms` 만큼 늦게 끝난다."""
    extra = {FAKE_LLM_LATENCY_ENV: str(latency_ms), **(extra_env or {})}
    env = _worker_env(database_url, backend_url, lease_s=lease_s, extra_env=extra)
    return _popen(["-m", "tests.integration._worker_proc"], env, log_path)


def _kill(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=10)


async def _wait_for(
    check: Callable[[], Awaitable[bool]], what: str, *, timeout: float = WAIT_S
) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if await check():
            return
        await asyncio.sleep(POLL_S)
    pytest.fail(f"{timeout}s 안에 {what} 가 되지 않았다")


# --- subprocess 진입점: FakeLLM 워커 ------------------------------------------------


async def _run_fake_llm_worker(latency_ms: int) -> None:
    from geoji_ai.adapters.backend_http import BackendHttp
    from geoji_ai.adapters.fake_llm import FakeLLM
    from geoji_ai.adapters.llm_router import RoleRoutedLLM, price_for
    from geoji_ai.adapters.postgres_call_ledger import PostgresCallLedger
    from geoji_ai.adapters.postgres_jobs import PostgresJobs, make_engine
    from geoji_ai.adapters.postgres_memory import PostgresMemory
    from geoji_ai.adapters.postgres_preparation import PostgresPreparation
    from geoji_ai.application.llm_gateway import LLMGateway
    from geoji_ai.core.config import get_settings, secret_value
    from geoji_ai.domain.vendor_health import VendorHealth
    from geoji_ai.workers.main import Worker
    from tests.integration.test_graph_b import CONTEXT_OK

    settings = get_settings()
    engine = make_engine(secret_value(settings, "DATABASE_URL"))
    backend = BackendHttp(
        settings.BACKEND_INTERNAL_URL, secret_value(settings, "SERVICE_AUTH_TOKEN")
    )
    judgment = FakeLLM(outputs={"context": CONTEXT_OK}, latency_ms=latency_ms)
    writer = FakeLLM(latency_ms=latency_ms)
    router = RoleRoutedLLM(
        {"openai": judgment, "xai": writer},
        models={"openai": settings.MODEL_JUDGMENT, "xai": settings.MODEL_WRITER},
    )
    gateway = LLMGateway(router, PostgresCallLedger(engine), VendorHealth(), price_for)
    worker = Worker(
        PostgresJobs(engine, lease_s=settings.JOB_LEASE_SECONDS),
        settings,
        engine=engine,
        backend=backend,
        memory=PostgresMemory(engine, settings),
        llm=gateway,
        preparation=PostgresPreparation(engine),
    )
    try:
        await worker.run()
    finally:
        await backend.aclose()
        await engine.dispose()


def main() -> None:
    from geoji_ai.core.logging import configure_logging

    configure_logging()
    asyncio.run(_run_fake_llm_worker(int(os.environ.get(FAKE_LLM_LATENCY_ENV, "0"))))


if __name__ == "__main__":  # pragma: no cover - subprocess 진입점
    main()
