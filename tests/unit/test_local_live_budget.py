"""승인 전 실측 가드 검증. 네트워크·벤더·DB 없이 파일 예약과 dry-run만 실행한다."""

import asyncio
import importlib
import json
import multiprocessing
import os
import subprocess
import sys
import traceback
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from geoji_ai.ports.llm import LLMError
from tests.evaluations import local_live_runtime as runtime

ROOT = Path(__file__).resolve().parents[2]
SECRET = "do-not-print-test-provider-secret"
MODEL = "gpt-5.6-luna"


def budget_file(path, *, cap=50000, max_calls=10):
    path.write_text(
        json.dumps(
            {
                "cap_micro_usd": cap,
                "max_calls": max_calls,
                "reserved_micro_usd": 0,
                "calls": [],
            }
        )
    )
    path.chmod(0o600)
    return path


def reserve(path, *, output_tokens=10, model=MODEL, messages=None):
    runtime.reserve_call(
        Path(path),
        vendor="openai",
        model=model,
        role="writer",
        messages=messages or [],
        schema={"type": "object"},
        output_tokens=output_tokens,
    )


def reserve_in_process(path):
    """spawn 프로세스도 모델을 만들지 않고 price lookup·flock·파일 기록만 수행한다."""
    try:
        reserve(path)
        return True
    except LLMError as exc:
        assert exc.kind == "BUDGET"
        return False


@pytest.fixture(autouse=True)
def no_provider_keys(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("XAI_API_KEY", "")


def test_call_limit_rejects_eleventh_and_preserves_budget(tmp_path):
    path = budget_file(tmp_path / "budget.json")
    for _ in range(10):
        reserve(path)
    before = path.read_bytes()
    with pytest.raises(LLMError) as caught:
        reserve(path)
    assert caught.value.kind == "BUDGET"
    assert path.read_bytes() == before
    budget = json.loads(before)
    assert len(budget["calls"]) == 10
    assert budget["reserved_micro_usd"] <= 50000
    assert budget["reserved_micro_usd"] == sum(c["reserved_micro_usd"] for c in budget["calls"])


def test_cost_limit_accepts_exact_boundary_and_rejects_next(tmp_path):
    path = budget_file(tmp_path / "budget.json")
    reserve(path)
    amount = json.loads(path.read_text())["reserved_micro_usd"]
    budget_file(path, cap=amount)
    reserve(path)
    with pytest.raises(LLMError) as caught:
        reserve(path)
    assert caught.value.kind == "BUDGET"
    assert json.loads(path.read_text())["reserved_micro_usd"] == amount


@pytest.mark.parametrize("gate", ["calls", "money"])
def test_process_concurrency_respects_shared_cap(tmp_path, gate):
    path = budget_file(tmp_path / "budget.json")
    reserve(path)
    unit = json.loads(path.read_text())["reserved_micro_usd"]
    maximum = 10 if gate == "calls" else 3
    budget_file(path, cap=50000 if gate == "calls" else unit * maximum)
    with ProcessPoolExecutor(
        max_workers=4, mp_context=multiprocessing.get_context("spawn")
    ) as pool:
        results = list(pool.map(reserve_in_process, [str(path)] * 24))
    budget = json.loads(path.read_text())
    assert sum(results) == maximum
    assert len(budget["calls"]) == maximum
    assert budget["reserved_micro_usd"] == unit * maximum
    assert budget["reserved_micro_usd"] <= budget["cap_micro_usd"]


async def test_budget_rejection_occurs_before_vendor_call(tmp_path, monkeypatch):
    path = budget_file(tmp_path / "budget.json", cap=0)
    monkeypatch.setenv("GEOJI_LIVE_BUDGET", str(path))
    fake = SimpleNamespace(
        route=lambda role, override: ("openai", MODEL), structured_call=AsyncMock()
    )
    monkeypatch.setattr(runtime, "build_llm", lambda settings: fake)
    router = runtime.BoundedRouter(None)
    with pytest.raises(LLMError):
        await router.structured_call(
            role="writer",
            messages=[],
            schema={},
            max_output_tokens=100,
            timeout_s=1,
        )
    fake.structured_call.assert_not_awaited()


async def test_result_unknown_keeps_reservation_for_next_attempt(tmp_path, monkeypatch):
    path = budget_file(tmp_path / "budget.json")
    monkeypatch.setenv("GEOJI_LIVE_BUDGET", str(path))
    fake = SimpleNamespace(
        route=lambda role, override: ("openai", MODEL),
        structured_call=AsyncMock(side_effect=LLMError("TIMEOUT")),
    )
    monkeypatch.setattr(runtime, "build_llm", lambda settings: fake)
    router = runtime.BoundedRouter(None)
    with pytest.raises(LLMError):
        await router.structured_call(
            role="writer",
            messages=[],
            schema={},
            max_output_tokens=100,
            timeout_s=1,
        )
    budget = json.loads(path.read_text())
    assert len(budget["calls"]) == 1
    assert budget["reserved_micro_usd"] > 0
    fake.structured_call.assert_awaited_once()


def test_unknown_price_and_corrupt_budget_fail_without_input_or_secret_leak(tmp_path, capsys):
    path = budget_file(tmp_path / "budget.json")
    before = path.read_bytes()
    with pytest.raises(LLMError) as caught:
        reserve(path, model="unknown-model-" + SECRET, messages=[{"content": SECRET}])
    assert caught.value.kind == "BUDGET"
    assert SECRET not in str(caught.value)
    assert path.read_bytes() == before
    path.write_text('{"calls": ' + SECRET)
    with pytest.raises((ValueError, LLMError)) as corrupted:
        reserve(path)
    assert SECRET not in str(corrupted.value)
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err


def test_budget_file_never_contains_prompt_or_key(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    path = budget_file(tmp_path / "budget.json")
    reserve(path, messages=[{"role": "user", "content": SECRET}])
    assert SECRET not in path.read_text()
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err


@pytest.mark.parametrize("output_tokens", [-1, 0, True, 1.5])
def test_invalid_output_limit_cannot_change_budget(tmp_path, output_tokens):
    path = budget_file(tmp_path / "budget.json")
    before = path.read_bytes()
    with pytest.raises((ValueError, LLMError)):
        reserve(path, output_tokens=output_tokens)
    assert path.read_bytes() == before


@pytest.mark.parametrize("price", [(float("nan"), 1), (1, float("inf")), (-1, 1)])
def test_invalid_price_cannot_reserve(tmp_path, monkeypatch, price):
    path = budget_file(tmp_path / "budget.json")
    monkeypatch.setattr(runtime, "price_for", lambda model: price)
    before = path.read_bytes()
    with pytest.raises(LLMError) as caught:
        reserve(path)
    assert caught.value.kind == "BUDGET"
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "field,value",
    [("max_calls", True), ("cap_micro_usd", -1), ("reserved_micro_usd", -1), ("calls", {})],
)
def test_invalid_reservation_state_fails_closed(tmp_path, field, value):
    path = budget_file(tmp_path / "budget.json")
    budget = json.loads(path.read_text())
    budget[field] = value
    path.write_text(json.dumps(budget))
    before = path.read_bytes()
    with pytest.raises(LLMError) as caught:
        reserve(path)
    assert caught.value.kind == "BUDGET"
    assert path.read_bytes() == before


def test_live_dry_run_reports_scope_without_writing_state_or_keys(tmp_path):
    # import 시 scripts 경로를 전역 추가하지 않고 스크립트 자체를 실행한다.
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/run_local_live_e2e.py")],
        cwd=tmp_path,
        env={
            **os.environ,
            "PYTHONPATH": str(ROOT / "src"),
            "OPENAI_API_KEY": SECRET,
            "XAI_API_KEY": SECRET,
        },
        capture_output=True,
        text=True,
        check=True,
    )
    plan = json.loads(result.stdout)
    assert plan["posts"] == 1
    assert plan["max_calls_including_repair"] == 10
    assert plan["reservation_cap_usd"] == 0.05
    assert SECRET not in result.stdout + result.stderr
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("module_name", ["run_local_e2e", "run_local_live_e2e"])
def test_runner_failures_never_print_command_credentials(
    tmp_path, monkeypatch, capsys, module_name
):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    module = importlib.import_module(module_name)
    failure = subprocess.CalledProcessError(1, ["docker", "-e", "POSTGRES_PASSWORD=" + SECRET])
    stack = SimpleNamespace(
        directory=tmp_path,
        report={},
        run=AsyncMock(side_effect=failure),
        close=AsyncMock(),
    )
    constructor = "LiveStack" if module_name == "run_local_live_e2e" else "Stack"
    monkeypatch.setattr(module, constructor, lambda args: stack)
    arguments = [module_name, "--backend", str(tmp_path)]
    if module_name == "run_local_live_e2e":
        arguments.append("--execute-approved")
    monkeypatch.setattr(sys, "argv", arguments)
    with pytest.raises(BaseException) as caught:
        asyncio.run(module.main())
    formatted = "".join(traceback.format_exception(caught.type, caught.value, caught.tb))
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err + formatted
    assert SECRET not in (tmp_path / "report.json").read_text()
    stack.close.assert_awaited_once()
