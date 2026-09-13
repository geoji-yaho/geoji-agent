"""벤더 장애 판정(06 §3.1 마지막 행, 06 스펙 ⑫~⑮)."""

from __future__ import annotations

from geoji_ai.domain.vendor_health import VendorHealth


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def make() -> tuple[VendorHealth, Clock]:
    clock = Clock()
    return VendorHealth(clock=clock), clock


def test_three_server_failures_within_60s_degraded() -> None:
    """06 ⑫ 60초 안 SERVER 3회 → degraded."""
    health, clock = make()
    health.record_failure("openai", "SERVER")
    clock.now += 20
    health.record_failure("openai", "SERVER")
    assert not health.is_degraded("openai")
    clock.now += 20
    health.record_failure("openai", "SERVER")
    assert health.is_degraded("openai")


def test_transport_counts_like_server() -> None:
    health, clock = make()
    health.record_failure("xai", "TRANSPORT")
    clock.now += 1
    health.record_failure("xai", "SERVER")
    clock.now += 1
    health.record_failure("xai", "TRANSPORT")
    assert health.is_degraded("xai")


def test_two_failures_success_one_failure_not_degraded() -> None:
    """06 ⑬ 2회 + 성공 + 1회 → 아님."""
    health, clock = make()
    health.record_failure("openai", "SERVER")
    clock.now += 1
    health.record_failure("openai", "SERVER")
    clock.now += 1
    health.record_success("openai")
    clock.now += 1
    health.record_failure("openai", "SERVER")
    assert not health.is_degraded("openai")


def test_three_failures_spanning_61s_not_degraded() -> None:
    """06 ⑭ 61초 걸친 3회 → 아님."""
    health, clock = make()
    health.record_failure("openai", "SERVER")
    clock.now += 30
    health.record_failure("openai", "SERVER")
    clock.now += 31
    health.record_failure("openai", "SERVER")
    assert not health.is_degraded("openai")


def test_vendors_are_independent() -> None:
    """06 ⑮ 벤더별 독립."""
    health, clock = make()
    for _ in range(3):
        health.record_failure("xai", "SERVER")
        clock.now += 1
    health.record_failure("openai", "SERVER")
    assert health.is_degraded("xai")
    assert not health.is_degraded("openai")
    health.record_success("openai")
    assert health.is_degraded("xai")


def test_other_kinds_neither_count_nor_break() -> None:
    """RATE_LIMIT·SCHEMA 등은 카운트에 넣지도 끊지도 않는다(코디네이터 해석 9/14)."""
    health, clock = make()
    health.record_failure("openai", "SERVER")
    health.record_failure("openai", "RATE_LIMIT")
    health.record_failure("openai", "SCHEMA")
    clock.now += 1
    health.record_failure("openai", "SERVER")
    health.record_failure("openai", "TIMEOUT")
    assert not health.is_degraded("openai")
    health.record_failure("openai", "SERVER")
    assert health.is_degraded("openai")


def test_degraded_clears_60s_after_last_failure_or_on_success() -> None:
    """해제: 마지막 실패 뒤 60초 경과 또는 성공 1회(코디네이터 해석 9/14)."""
    health, clock = make()
    for _ in range(3):
        health.record_failure("openai", "SERVER")
    clock.now += 60
    assert health.is_degraded("openai")
    clock.now += 1
    assert not health.is_degraded("openai")

    for _ in range(3):
        health.record_failure("xai", "SERVER")
    assert health.is_degraded("xai")
    health.record_success("xai")
    assert not health.is_degraded("xai")
