"""부팅 도중 실패해 정리용 state가 없으면 --keep이어도 자원을 남기지 않는다."""

import importlib
import os
import signal
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest


@pytest.mark.parametrize(
    "keep,state_saved,retained",
    [(True, False, False), (True, True, True), (False, True, False)],
    ids=["failed-boot-with-keep", "ready-with-keep", "ready-without-keep"],
)
async def test_cleanup_only_retains_a_stack_with_shutdown_state(
    tmp_path, monkeypatch, keep, state_saved, retained
):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    runner = importlib.import_module("run_local_e2e")
    monkeypatch.setattr(runner.tempfile, "mkdtemp", lambda **kwargs: str(tmp_path))
    monkeypatch.setattr(runner, "LocalIssuer", Mock())
    stack = runner.Stack(
        SimpleNamespace(
            keep=keep, backend_port=18080, issuer_port=18099, db_port=55436, ai_port=18100
        )
    )
    stack.client = SimpleNamespace(aclose=AsyncMock())
    stack.db = SimpleNamespace(close=AsyncMock())
    stack.container_started = True
    process = stack.start("worker", [sys.executable, "-c", "import time; time.sleep(60)"])
    docker_stop = Mock()
    monkeypatch.setattr(runner.subprocess, "run", docker_stop)
    if state_saved:
        # 정상 부팅에서는 save_state가 이 복구 자료를 저장한 뒤 --keep을 허용한다.
        runner.write_json(tmp_path / "state.json", {"container": stack.name}, private=True)
    try:
        await stack.close()

        assert (process.poll() is None) == retained
        stack.client.aclose.assert_awaited_once()
        stack.db.close.assert_awaited_once()
        if retained:
            docker_stop.assert_not_called()
        else:
            docker_stop.assert_called_once_with(
                ["docker", "stop", stack.name], capture_output=True, check=True
            )
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=5)
