"""`application/llm_gateway.py` 단위 테스트(06 §3.1·§3.2·§4.2, 스펙 케이스 ①~⑧).

가짜 원장·가짜 inner(라우터 모양)·`VendorHealth`(고정 시계)·가짜 sleep.
네트워크·DB·키·실제 대기 없음.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import pytest

from geoji_ai.application.llm_gateway import (
    EVALUATOR_SLOTS,
    WRITER_SLOTS,
    CallScope,
    LLMGateway,
    ScopedLLM,
    evaluator_call_index,
    rough_prompt_tokens,
    writer_call_index,
)
from geoji_ai.domain.budget import est_max_micro_usd, request_hash
from geoji_ai.domain.vendor_health import DEGRADED_THRESHOLD, VendorHealth
from geoji_ai.ports.ledger import BudgetExceeded, CallSpec
from geoji_ai.ports.llm import Cost, LLMError, LLMResult, Usage
from geoji_ai.ports.preparation import EvidenceInvalidated

MODEL = "gpt-5.6-luna"
PRICES = {"gpt-5.6-luna": (0.20, 1.20), "gpt-5.6-terra": (2.00, 12.00)}
MESSAGES = [{"role": "system", "content": "s" * 40}, {"role": "user", "content": "u" * 80}]
SCHEMA = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
OUTPUT = {"sentence": "oneDay"}
MAX_OUTPUT = 400


def ok_result(output: dict[str, Any] | None = None) -> LLMResult:
    return LLMResult(
        output=dict(output or OUTPUT),
        stop_reason="stop",
        usage=Usage(prompt_tokens=30, completion_tokens=10),
        cost=Cost(micro_usd=12, source="table"),
        provider_request_id="req-1",
        model_id=MODEL,
        vendor="openai",
        latency_ms=5,
    )


# --- 가짜 ------------------------------------------------------------------------


def _key(rhash: str, versions: dict[str, Any]) -> tuple[str, str]:
    return rhash, json.dumps(versions, sort_keys=True, ensure_ascii=False)


class FakeLedger:
    """`LedgerPort` 모양. 호출을 기록하고 `node_results` 는 메모리 dict 다."""

    def __init__(
        self, *, budget_exceeded: bool = False, reserve_error: Exception | None = None
    ) -> None:
        self.budget_exceeded = budget_exceeded
        self.reserve_error = reserve_error
        self.reserved: list[tuple[str, CallSpec]] = []
        self.settled: list[str] = []
        self.failed: list[tuple[str, str]] = []
        self.unknown: list[tuple[str, str]] = []
        self.lookups = 0
        self.stored: dict[tuple[str, str], dict[str, Any]] = {}
        self.put: list[str] = []

    async def reserve(self, post_id: str, call: CallSpec) -> str:
        if self.reserve_error is not None:
            raise self.reserve_error
        if self.budget_exceeded:
            raise BudgetExceeded(
                post_id,
                cap_micro_usd=1,
                spent_micro_usd=0,
                reserved_micro_usd=0,
                est_max_micro_usd=call.est_max_micro_usd,
            )
        self.reserved.append((post_id, call))
        return f"call-{len(self.reserved)}"

    async def settle(self, call_id: str, result: LLMResult) -> None:
        self.settled.append(call_id)

    async def fail(self, call_id: str, error: LLMError) -> None:
        self.failed.append((call_id, error.kind))

    async def mark_unknown(self, call_id: str, error: LLMError) -> None:
        self.unknown.append((call_id, error.kind))

    async def get_node_result(
        self, request_hash: str, versions: dict[str, Any]
    ) -> dict[str, Any] | None:
        self.lookups += 1
        return self.stored.get(_key(request_hash, versions))

    async def put_node_result(
        self,
        call_id: str,
        request_hash: str,
        versions: dict[str, Any],
        output: dict[str, Any],
        expires_at: Any = None,
    ) -> None:
        self.put.append(call_id)
        self.stored[_key(request_hash, versions)] = dict(output)


class FakeInner:
    """라우터 모양. `script` 를 앞에서부터 하나씩 쓴다: 예외면 올리고 결과면 돌려준다."""

    def __init__(self, *script: Any, vendor: str = "openai", hang: bool = False) -> None:
        self.script = list(script)
        self.vendor = vendor
        self.hang = hang
        self.calls: list[dict[str, Any]] = []
        self.routes: list[tuple[str, str | None]] = []

    def route(self, role: str, model_override: str | None = None) -> tuple[str, str]:
        self.routes.append((role, model_override))
        return self.vendor, model_override or MODEL

    async def structured_call(
        self,
        *,
        role: str,
        messages: list[dict],
        schema: dict,
        timeout_s: float,
        max_output_tokens: int,
        model_override: str | None = None,
    ) -> LLMResult:
        self.calls.append({"role": role, "timeout_s": timeout_s, "model_override": model_override})
        if self.hang:
            await asyncio.Event().wait()
        item = self.script.pop(0) if self.script else ok_result()
        if isinstance(item, BaseException):
            raise item
        return item


class RecordingHealth(VendorHealth):
    """실제 판정 + 호출 기록. 시계는 고정."""

    def __init__(self) -> None:
        super().__init__(clock=lambda: 100.0)
        self.successes: list[str] = []
        self.failures: list[tuple[str, str]] = []

    def record_success(self, vendor: str) -> None:
        self.successes.append(vendor)
        super().record_success(vendor)

    def record_failure(self, vendor: str, kind: str) -> None:
        self.failures.append((vendor, kind))
        super().record_failure(vendor, kind)


class Sleeps:
    def __init__(self) -> None:
        self.waits: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)


@dataclass
class Rig:
    gateway: LLMGateway
    ledger: FakeLedger
    inner: FakeInner
    health: RecordingHealth
    sleeps: Sleeps


def rig(
    *script: Any,
    ledger: FakeLedger | None = None,
    health: RecordingHealth | None = None,
    vendor: str = "openai",
    hang: bool = False,
    stale_scopes: Any = None,
) -> Rig:
    ledger = ledger or FakeLedger()
    inner = FakeInner(*script, vendor=vendor, hang=hang)
    health = health or RecordingHealth()
    sleeps = Sleeps()
    gateway = LLMGateway(inner, ledger, health, PRICES.get, sleep=sleeps, stale_scopes=stale_scopes)
    return Rig(gateway, ledger, inner, health, sleeps)


def scope(**over: Any) -> CallScope:
    base: dict[str, Any] = {
        "budget_key": "post-1",
        "node": "sentencing",
        "call_index": 0,
        "generation_id": "gen-1",
        "job_id": "job-1",
        "prompt_version": "bundle-v1",
        "policy_version": "guardrail-v2",
        "privacy_versions": [{"scope_key": "user:u1", "epoch": 1}],
        "remaining_s": lambda: 10.0,
        "reserve_s": 0.0,
    }
    base.update(over)
    return CallScope(**base)


async def call(r: Rig, s: CallScope | None = None, *, timeout_s: float = 3.0) -> Any:
    return await r.gateway.scoped_call(
        s or scope(),
        role="sentencing",
        messages=MESSAGES,
        schema=SCHEMA,
        timeout_s=timeout_s,
        max_output_tokens=MAX_OUTPUT,
    )


# --- ① 성공 ----------------------------------------------------------------------


def test_게이트웨이는_ScopedLLM_이다():
    assert isinstance(rig().gateway, ScopedLLM)


async def test_01_성공이면_reserve_settle_1회씩_health_success():
    r = rig()

    scoped = await call(r)

    assert scoped.result.output == OUTPUT
    assert scoped.reused is False and scoped.call_id == "call-1"
    assert len(r.inner.calls) == 1
    assert len(r.ledger.reserved) == 1
    assert r.ledger.settled == ["call-1"]
    assert (r.ledger.failed, r.ledger.unknown) == ([], [])
    assert r.health.successes == ["openai"]
    assert r.health.failures == []


async def test_01b_예약_CallSpec_은_scope_와_est_max_근사를_싣는다():
    r = rig()

    await call(r, scope(node="writer", call_index=2, generation_id="gen-9", job_id="job-9"))

    budget_key, spec = r.ledger.reserved[0]
    assert budget_key == "post-1"
    assert (spec.node, spec.call_index, spec.vendor, spec.model) == ("writer", 2, "openai", MODEL)
    assert (spec.generation_id, spec.job_id) == ("gen-9", "job-9")
    # 입력 token 근사 = content 길이 합 // 4 = 120 // 4
    assert rough_prompt_tokens(MESSAGES) == 30
    assert spec.est_max_micro_usd == est_max_micro_usd(30, MAX_OUTPUT, *PRICES[MODEL])
    assert spec.request_hash == request_hash(
        {
            "role": "sentencing",
            "model_id": MODEL,
            "messages": MESSAGES,
            "schema": SCHEMA,
            "max_output_tokens": MAX_OUTPUT,
        }
    )


async def test_01c_model_override_는_route_와_inner_로_가고_원장_모델이_된다():
    r = rig()

    await call(r, scope(node="evaluator", call_index=1, model_override="gpt-5.6-terra"))

    assert r.inner.routes == [("sentencing", "gpt-5.6-terra")]
    assert r.inner.calls[0]["model_override"] == "gpt-5.6-terra"
    _, spec = r.ledger.reserved[0]
    assert spec.model == "gpt-5.6-terra"
    assert spec.est_max_micro_usd == est_max_micro_usd(30, MAX_OUTPUT, *PRICES["gpt-5.6-terra"])


# --- ② ③ 429·5xx 재시도 -----------------------------------------------------------


async def test_02_429_남은시간_충분하면_같은_원장_행으로_1회_재시도():
    r = rig(LLMError("RATE_LIMIT", retry_after_s=1.0), ok_result())

    scoped = await call(r, scope(remaining_s=lambda: 10.0, reserve_s=2.0))

    assert scoped.result.output == OUTPUT
    assert len(r.inner.calls) == 2
    # 코디네이터 9/14 결정 A: 원장 1행 · reserve 1회
    assert len(r.ledger.reserved) == 1
    assert r.ledger.settled == ["call-1"]
    assert (r.ledger.failed, r.ledger.unknown) == ([], [])
    assert r.sleeps.waits == [1.0]
    assert r.health.failures == [("openai", "RATE_LIMIT")]
    assert r.health.successes == ["openai"]


async def test_02b_재시도_timeout_은_대기_뒤_남은_시간_안이다():
    r = rig(LLMError("SERVER", retry_after_s=1.0), ok_result())

    await call(r, scope(remaining_s=lambda: 5.0, reserve_s=2.0), timeout_s=3.0)

    # min(3.0, 5.0 − 2.0 − 1.0)
    assert [c["timeout_s"] for c in r.inner.calls] == [3.0, 2.0]


async def test_02c_5xx_두_번이면_같은_행을_FAILED_로_한_번_닫는다():
    r = rig(LLMError("SERVER"), LLMError("SERVER"))

    with pytest.raises(LLMError) as caught:
        await call(r)

    assert caught.value.kind == "SERVER"
    assert len(r.inner.calls) == 2
    assert len(r.ledger.reserved) == 1
    assert r.ledger.failed == [("call-1", "SERVER")]
    assert r.ledger.settled == []
    assert r.sleeps.waits == [0.0]
    assert r.health.failures == [("openai", "SERVER"), ("openai", "SERVER")]


async def test_03_429_남은시간_부족하면_재시도하지_않는다():
    r = rig(LLMError("RATE_LIMIT", retry_after_s=2.0), ok_result())

    with pytest.raises(LLMError) as caught:
        await call(r, scope(remaining_s=lambda: 1.0, reserve_s=0.5))

    assert caught.value.kind == "RATE_LIMIT"
    assert len(r.inner.calls) == 1
    assert r.sleeps.waits == []
    assert r.ledger.failed == [("call-1", "RATE_LIMIT")]
    assert r.ledger.settled == []


# --- ④ timeout --------------------------------------------------------------------


async def test_04_timeout_은_mark_unknown_예약_유지():
    r = rig(LLMError("TIMEOUT"))

    with pytest.raises(LLMError) as caught:
        await call(r)

    assert caught.value.kind == "TIMEOUT"
    assert r.ledger.unknown == [("call-1", "TIMEOUT")]
    assert (r.ledger.failed, r.ledger.settled) == ([], [])
    assert r.sleeps.waits == []


async def test_04b_시도마다_게이트웨이가_wait_for_를_건다():
    r = rig(hang=True)

    with pytest.raises(LLMError) as caught:
        await call(r, timeout_s=0.01)

    assert caught.value.kind == "TIMEOUT"
    assert r.ledger.unknown == [("call-1", "TIMEOUT")]


async def test_04c_inner_의_LLMError_아닌_예외는_TRANSPORT_UNKNOWN_벤더_장애로_세지_않는다():
    r = rig(RuntimeError("boom"))

    with pytest.raises(LLMError) as caught:
        await call(r)

    assert caught.value.kind == "TRANSPORT"
    assert r.ledger.unknown == [("call-1", "TRANSPORT")]
    assert r.health.failures == []


# --- R2 취소·예외 경로에서 원장 닫기 ---------------------------------------------------


class FailingCloseLedger(FakeLedger):
    """닫기(`mark_unknown`)가 실패하는 원장. 시도는 기록한다."""

    def __init__(self) -> None:
        super().__init__()
        self.close_attempts = 0

    async def mark_unknown(self, call_id: str, error: LLMError) -> None:
        self.close_attempts += 1
        raise RuntimeError("ledger down")


async def _start_and_cancel(r: Rig) -> asyncio.Task[Any]:
    task = asyncio.create_task(call(r))
    while not r.inner.calls:  # inner 가 대기에 들어갈 때까지
        await asyncio.sleep(0)
    task.cancel()
    return task


async def test_R2a_inner_대기_중_취소면_mark_unknown_1회_CancelledError_전파():
    r = rig(hang=True)

    task = await _start_and_cancel(r)
    with pytest.raises(asyncio.CancelledError):
        await task

    assert r.ledger.unknown == [("call-1", "TRANSPORT")]
    assert (r.ledger.settled, r.ledger.failed) == ([], [])
    assert r.health.failures == []


async def test_R2b_inner_RuntimeError_면_원장을_한_번_닫는다():
    r = rig(RuntimeError("boom"))

    with pytest.raises(LLMError):
        await call(r)

    closes = len(r.ledger.settled) + len(r.ledger.failed) + len(r.ledger.unknown)
    assert closes == 1
    assert r.ledger.unknown == [("call-1", "TRANSPORT")]


async def test_R2c_루프_밖_예외도_원장을_UNKNOWN_으로_닫고_원래_예외를_올린다():
    def broken_remaining() -> float:
        raise RuntimeError("clock broken")

    r = rig(LLMError("SERVER"))

    with pytest.raises(RuntimeError, match="clock broken"):
        await call(r, scope(remaining_s=broken_remaining))

    assert r.ledger.unknown == [("call-1", "TRANSPORT")]
    assert (r.ledger.settled, r.ledger.failed) == ([], [])


async def test_R2d_닫기가_실패해도_원래_CancelledError_를_올린다():
    ledger = FailingCloseLedger()
    r = rig(ledger=ledger, hang=True)

    task = await _start_and_cancel(r)
    with pytest.raises(asyncio.CancelledError):
        await task

    assert ledger.close_attempts == 1


async def test_R2e_정상_닫은_뒤_실패는_두_번_닫지_않는다():
    """fail 로 닫은 뒤 올리는 LLMError 는 except 에서 다시 닫지 않는다."""
    r = rig(LLMError("AUTH"))

    with pytest.raises(LLMError):
        await call(r)

    assert len(r.ledger.failed) + len(r.ledger.unknown) == 1


# --- ⑤ 예산 -------------------------------------------------------------------------


async def test_05_BudgetExceeded_면_inner_호출_0_BUDGET():
    r = rig(ledger=FakeLedger(budget_exceeded=True))

    with pytest.raises(LLMError) as caught:
        await call(r)

    assert caught.value.kind == "BUDGET"
    assert r.inner.calls == []
    assert r.ledger.reserved == []
    assert (r.ledger.failed, r.ledger.unknown, r.ledger.settled) == ([], [], [])


async def test_05b_reserve_가_다른_예외면_원장_조작_없이_TRANSPORT():
    r = rig(ledger=FakeLedger(reserve_error=RuntimeError("unique violation")))

    with pytest.raises(LLMError) as caught:
        await call(r)

    assert caught.value.kind == "TRANSPORT"
    assert r.inner.calls == []
    assert (r.ledger.failed, r.ledger.unknown, r.ledger.settled) == ([], [], [])
    assert r.health.failures == []


# --- ⑥ degraded ---------------------------------------------------------------------


async def test_06_degraded_벤더면_inner_0_예약_0_DEGRADED():
    health = RecordingHealth()
    for _ in range(DEGRADED_THRESHOLD):
        health.record_failure("openai", "SERVER")
    assert health.is_degraded("openai")
    r = rig(health=health)

    with pytest.raises(LLMError) as caught:
        await call(r)

    assert caught.value.kind == "DEGRADED"
    assert r.inner.calls == []
    assert r.ledger.reserved == []
    assert r.ledger.lookups == 0

    # 다른 벤더는 그대로 부른다
    other = rig(health=health, vendor="xai")
    await call(other)
    assert len(other.inner.calls) == 1


# --- ⑦ node_results 재사용 -----------------------------------------------------------


async def test_07_node_results_hit_면_inner_0_reserve_0():
    ledger = FakeLedger()
    first = rig(ledger=ledger)
    scoped = await call(first)
    await first.gateway.remember(scoped)
    assert ledger.put == ["call-1"]

    again = rig(ledger=ledger)
    hit = await call(again)

    assert again.inner.calls == []
    assert len(ledger.reserved) == 1
    assert hit.reused is True and hit.call_id is None
    assert hit.result.output == OUTPUT
    assert (hit.result.model_id, hit.result.vendor) == (MODEL, "openai")
    assert hit.result.usage == Usage()
    assert hit.result.latency_ms == 0
    # 재사용 결과는 다시 넣지 않는다
    await again.gateway.remember(hit)
    assert ledger.put == ["call-1"]


async def test_07b_remember_하지_않은_출력은_재사용되지_않는다():
    ledger = FakeLedger()
    await call(rig(ledger=ledger))

    again = rig(ledger=ledger)
    await call(again)

    assert len(again.inner.calls) == 1
    assert len(ledger.reserved) == 2


@pytest.mark.parametrize(
    "change",
    [
        {"prompt_version": "bundle-v2"},
        {"policy_version": "guardrail-v1"},
        {"privacy_versions": [{"scope_key": "user:u1", "epoch": 2}]},
        {"model_override": "gpt-5.6-terra"},
    ],
    ids=["prompt_version", "policy_version", "privacy_versions", "model_id"],
)
async def test_07c_키_5요소_중_하나라도_다르면_다시_부른다(change: dict[str, Any]):
    ledger = FakeLedger()
    first = rig(ledger=ledger)
    await first.gateway.remember(await call(first))

    again = rig(ledger=ledger)
    scoped = await call(again, scope(**change))

    assert scoped.reused is False
    assert len(again.inner.calls) == 1


async def test_07d_출력이_없는_결과는_remember_해도_넣지_않는다():
    empty = LLMResult(
        output=None,
        stop_reason="refusal",
        usage=Usage(),
        cost=Cost(),
        provider_request_id=None,
        model_id=MODEL,
        vendor="openai",
        latency_ms=1,
    )
    r = rig(empty)
    await r.gateway.remember(await call(r))
    assert r.ledger.put == []


class EpochFeed:
    """가짜 현재 epoch 공급(`PreparationPort.stale_scopes` 모양).

    부를 때마다 `answers` 를 하나씩 쓰고, 마지막 답은 계속 쓴다.
    """

    def __init__(self, *answers: list[str]) -> None:
        self.answers = list(answers)
        self.asked: list[list[tuple[str, int]]] = []

    async def __call__(self, privacy_versions: Any) -> list[str]:
        self.asked.append(list(privacy_versions))
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


@pytest.mark.parametrize(
    ("seed", "answers", "reused", "lookups", "put"),
    [
        # ⓐ 이미 무효화는 D-26 으로 호출 자체를 막는다(아래 test_D26_*)
        (False, ([], ["user:u1"]), False, 1, 0),  # ⓑ 호출 중 epoch +1: 저장 0
        (True, ([],), True, 1, 0),  # ⓒ 일치: 기존처럼 hit(재사용 결과는 넣지 않는다)
    ],
    ids=["stale_during_call", "current"],
)
async def test_07e_현재_epoch_과_다르면_node_results_를_조회도_저장도_하지_않는다(
    seed: bool, answers: tuple[list[str], ...], reused: bool, lookups: int, put: int
):
    """08 §4.1 "무효화 후 node_results 재사용 0", 06 §3.2."""
    ledger = FakeLedger()
    if seed:
        seeded = rig(ledger=ledger)
        await seeded.gateway.remember(await call(seeded))
        assert ledger.put == ["call-1"]
        ledger.lookups = 0
        ledger.put.clear()

    feed = EpochFeed(*answers)
    r = rig(ledger=ledger, stale_scopes=feed)
    scoped = await call(r)
    await r.gateway.remember(scoped)

    assert scoped.reused is reused
    assert len(r.inner.calls) == (0 if reused else 1)
    assert ledger.lookups == lookups
    assert len(ledger.put) == put
    assert feed.asked[0] == [("user:u1", 1)]


async def test_07f_epoch_일치면_새_호출_결과를_저장하고_확인_예외면_저장하지_않는다():
    feed = EpochFeed([])
    r = rig(stale_scopes=feed)
    await r.gateway.remember(await call(r))
    assert r.ledger.put == ["call-1"]
    assert len(feed.asked) == 2  # 조회 전·저장 직전

    async def broken(_: Any) -> list[str]:
        raise RuntimeError("db down")

    # D-26: 확인 실패는 원장 예약 실패와 같은 TRANSPORT 다. 호출·예약·조회 0.
    b = rig(stale_scopes=broken)
    with pytest.raises(LLMError) as caught:
        await call(b)
    assert caught.value.kind == "TRANSPORT"
    assert len(b.inner.calls) == 0
    assert (b.ledger.reserved, b.ledger.lookups, b.ledger.put) == ([], 0, [])

    # privacy_versions 모양이 틀려 쌍을 못 만들어도 확인 실패로 본다(예외가 새지 않는다)
    m = rig(stale_scopes=EpochFeed([]))
    with pytest.raises(LLMError) as malformed:
        await call(m, scope(privacy_versions=[{"scope": "user:u1"}]))
    assert malformed.value.kind == "TRANSPORT"
    assert len(m.inner.calls) == 0
    assert (m.ledger.reserved, m.ledger.lookups, m.ledger.put) == ([], 0, [])


# --- D-26 모델 호출 직전 epoch 확인(9/14, 10 §8·§15.5) ------------------------------------


async def test_D26_epoch_가_바뀐_뒤_호출하면_inner_0_reserve_0_EvidenceInvalidated():
    ledger = FakeLedger()
    # 저장된 재사용 결과가 있어도 무효 epoch 로는 조회하지 않는다.
    seeded = rig(ledger=ledger)
    await seeded.gateway.remember(await call(seeded))
    ledger.reserved.clear()
    ledger.settled.clear()
    ledger.lookups = 0

    feed = EpochFeed(["user:u1"])
    r = rig(ledger=ledger, stale_scopes=feed)

    with pytest.raises(EvidenceInvalidated) as caught:
        await call(r)

    assert caught.value.error_code == "EVIDENCE_INVALIDATED"
    assert caught.value.scope_keys == ["user:u1"]
    assert r.inner.calls == []
    assert ledger.reserved == []
    assert ledger.lookups == 0
    assert (ledger.settled, ledger.failed, ledger.unknown) == ([], [], [])
    assert feed.asked == [[("user:u1", 1)]]


async def test_D26_재시도_직전_epoch_가_바뀌면_재시도하지_않고_같은_원장_행을_한_번_닫는다():
    feed = EpochFeed([], ["user:u1"])
    r = rig(LLMError("SERVER"), ok_result(), stale_scopes=feed)

    with pytest.raises(EvidenceInvalidated):
        await call(r)

    assert len(r.inner.calls) == 1
    assert len(r.ledger.reserved) == 1
    assert r.ledger.settled == []
    assert len(r.ledger.failed) + len(r.ledger.unknown) == 1
    assert len(feed.asked) == 2  # 첫 시도 전·재시도 전


async def test_D26_stale_scopes_가_없으면_기존대로_호출한다():
    r = rig()
    scoped = await call(r)
    assert scoped.reused is False
    assert len(r.inner.calls) == 1


# --- ⑧ 인증 오류 ---------------------------------------------------------------------


async def test_08_401_은_AUTH_재시도_0_degraded_카운트_0():
    r = rig(*(LLMError("AUTH") for _ in range(DEGRADED_THRESHOLD + 1)))

    for _ in range(DEGRADED_THRESHOLD + 1):
        with pytest.raises(LLMError) as caught:
            await call(r)
        assert caught.value.kind == "AUTH"

    assert len(r.inner.calls) == DEGRADED_THRESHOLD + 1  # 호출마다 1회, 재시도 없음
    assert r.sleeps.waits == []
    assert r.ledger.failed == [(f"call-{n}", "AUTH") for n in range(1, DEGRADED_THRESHOLD + 2)]
    assert r.ledger.unknown == []
    assert not r.health.is_degraded("openai")


# --- call_index 오프셋(코디네이터 9/14 결정 3) ---------------------------------------------


def test_서기_call_index_는_보정_라운드마다_3칸_밀린다():
    assert WRITER_SLOTS == 3
    assert [writer_call_index(0, n) for n in range(3)] == [0, 1, 2]
    assert [writer_call_index(1, n) for n in range(3)] == [3, 4, 5]
    slots = {writer_call_index(r, n) for r in range(3) for n in range(WRITER_SLOTS)}
    assert len(slots) == 3 * WRITER_SLOTS


def test_검수_call_index_는_라운드마다_2칸_hell_별도면_1():
    assert EVALUATOR_SLOTS == 2
    assert evaluator_call_index(0, False) == 0
    assert evaluator_call_index(0, True) == 1
    assert evaluator_call_index(1, False) == 2
    assert evaluator_call_index(2, True) == 5
    slots = {evaluator_call_index(r, h) for r in range(3) for h in (False, True)}
    assert len(slots) == 6


@pytest.mark.parametrize(("repair_count", "position"), [(-1, 0), (0, -1), (0, 3)])
def test_서기_call_index_범위_밖은_ValueError(repair_count: int, position: int):
    with pytest.raises(ValueError):
        writer_call_index(repair_count, position)


def test_검수_라운드_음수는_ValueError():
    with pytest.raises(ValueError):
        evaluator_call_index(-1, False)
