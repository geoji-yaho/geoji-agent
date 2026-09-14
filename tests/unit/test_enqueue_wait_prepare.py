"""`scripts/enqueue_job.py` SENTENCE 게이트 대기(9/14 D-24, 10 §3). DB·실제 대기 없음.

대기 로직은 순수 함수 `wait_for_prepare` 다. 조회 함수·시계·sleep 을 주입한다.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts" / "enqueue_job.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("enqueue_job_under_test", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


script = _load()


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _answers(*values: bool):
    remaining = list(values)
    asked: list[float] = []

    async def active() -> bool:
        asked.append(0)
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    return active, asked


async def test_PREPARE_가_없거나_끝났으면_기다리지_않는다():
    clock = Clock()
    active, asked = _answers(False)

    done = await script.wait_for_prepare(
        active, max_wait_s=30, clock=clock, sleep=clock.sleep, poll_s=0.25
    )

    assert done is True
    assert clock.sleeps == []
    assert len(asked) == 1


async def test_PREPARE_가_끝나면_그때_넣는다():
    clock = Clock()
    active, asked = _answers(True, True, False)

    done = await script.wait_for_prepare(
        active, max_wait_s=30, clock=clock, sleep=clock.sleep, poll_s=0.25
    )

    assert done is True
    assert clock.sleeps == [0.25, 0.25]
    assert len(asked) == 3


async def test_PREPARE_가_안_끝나면_대기_상한에서_멈춘다():
    clock = Clock()
    active, _ = _answers(True)

    done = await script.wait_for_prepare(
        active, max_wait_s=1.0, clock=clock, sleep=clock.sleep, poll_s=0.3
    )

    assert done is False
    assert clock.now == pytest.approx(1.0)
    assert sum(clock.sleeps) == pytest.approx(1.0)
    assert max(clock.sleeps) <= 0.3


def test_기본_폴링_주기는_watchdog_250ms():
    assert script.PREPARE_POLL_SECONDS == 0.25


def test_wait_prepare_는_기본으로_켜지고_대기_상한은_인자다():
    parser = script.build_parser()
    base = ["--kind", "SENTENCE", "--verdict", "v1", "--version", "1", "--post", "p1"]

    default = parser.parse_args(base)
    assert default.wait_prepare is True
    assert default.prepare_wait_seconds == 30.0

    off = parser.parse_args([*base, "--no-wait-prepare", "--prepare-wait-seconds", "5"])
    assert off.wait_prepare is False
    assert off.prepare_wait_seconds == 5.0
