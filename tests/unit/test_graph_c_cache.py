"""실제 게이트웨이 + 메모리 원장으로 논리 재생성과 통신 재실행을 구분한다."""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from geoji_ai.application.llm_gateway import LLMGateway
from geoji_ai.application.sentence_case import SentenceHandler
from geoji_ai.core.config import Settings
from geoji_ai.domain.vendor_health import VendorHealth
from geoji_ai.graphs.sentencing import build_sentence_graph
from tests.unit.test_graph_c import (
    DB_NOW,
    SPEC_CAPS,
    WORKER_ID,
    FakeBackend,
    FakeClock,
    FakeJobs,
    FakePreparation,
    Rejected,
    Run,
    ScriptedLLM,
    make_job,
    make_prep,
    make_snapshot,
    report,
)
from tests.unit.test_llm_gateway import MODEL, PRICES, FakeLedger


class RoutedFake(ScriptedLLM):
    def route(self, role: str, model_override: str | None = None) -> tuple[str, str]:
        return "openai", model_override or MODEL

    async def structured_call(self, *, model_override: str | None = None, **kwargs: Any):
        result = await super().structured_call(**kwargs)
        return replace(result, model_id=model_override or MODEL, vendor="openai")


class CacheRig:
    def __init__(self, provider: RoutedFake | None = None) -> None:
        self.provider = provider or RoutedFake()
        self.ledger = FakeLedger()
        self.gateway = LLMGateway(self.provider, self.ledger, VendorHealth(), PRICES.get)

    def run(
        self,
        *,
        kind: str = "TEXT_RETRY",
        job_id: str = "retry-1",
        generation_id: str = "generation-1",
        round_number: int = 1,
        finalize_errors: list[Exception] | None = None,
        repair_max: int = 1,
    ) -> Run:
        snapshot = make_snapshot(result="notGuilty", target_intensities=["spicy"])
        backend = FakeBackend(snapshot, finalize_errors=finalize_errors)
        captured: dict[str, Any] = {}

        def factory(deps: Any) -> Any:
            graph = build_sentence_graph(deps)

            class Capturing:
                async def ainvoke(self, state: Any) -> Any:
                    captured["state"] = await graph.ainvoke(state)
                    return captured["state"]

            return Capturing()

        async def db_now():
            return DB_NOW

        ctx = SimpleNamespace(
            jobs=FakeJobs(),
            backend=backend,
            llm=self.gateway,
            preparation=FakePreparation(make_prep()),
            semaphore=asyncio.Semaphore(8),
            settings=Settings(_env_file=None, **SPEC_CAPS, IMMEDIATE_REPAIR_MAX=repair_max),
            generation_id=generation_id,
            worker_id=WORKER_ID,
        )
        job = make_job(kind, {"round": round_number} if kind == "TEXT_RETRY" else None)
        job = job.model_copy(update={"id": job_id, "generation_id": generation_id})
        asyncio.run(SentenceHandler(factory, db_now=db_now, clock=FakeClock())(job, ctx))
        return Run(state=captured["state"], backend=backend, jobs=ctx.jobs, llm=self.provider)


def test_initial_rejection_then_text_retry_calls_provider_again():
    rig = CacheRig(RoutedFake(outputs={"evaluator": report(["spicy"], fail=["spicy"])}))
    initial = rig.run(kind="SENTENCE", job_id="initial", repair_max=0)
    assert initial.backend.failed == ["EVAL_FAILED"]
    before = len(rig.provider.calls)
    rig.provider.outputs["evaluator"] = report(["spicy"])

    retry = rig.run()

    assert retry.state["failure"] is None
    assert len(retry.backend.finalized) == 1
    assert Counter(c.role for c in rig.provider.calls[before:]) == {"writer": 1, "evaluator": 1}


@pytest.mark.parametrize(
    "next_attempt",
    [{"job_id": "retry-2"}, {"round_number": 2}],
)
def test_new_job_or_round_has_new_writer_and_evaluator_calls(next_attempt):
    rig = CacheRig()
    rig.run()
    before = len(rig.provider.calls)

    result = rig.run(generation_id="generation-2", **next_attempt)

    assert result.state["failure"] is None
    assert Counter(c.role for c in rig.provider.calls[before:]) == {"writer": 1, "evaluator": 1}


def test_same_job_round_slots_reuse_across_generation_change():
    rig = CacheRig()
    first = rig.run()
    before = len(rig.provider.calls)
    reservations = len(rig.ledger.reserved)

    replay = rig.run(generation_id="lease-takeover-generation")

    assert replay.state["failure"] is None
    assert replay.finalize().draft == first.finalize().draft
    assert len(rig.provider.calls) == before
    assert len(rig.ledger.reserved) == reservations


def test_finalize_422_repair_uses_new_writer_and_evaluator_slots():
    rig = CacheRig()

    result = rig.run(kind="SENTENCE", finalize_errors=[Rejected(422, "INVALID_DRAFT")])

    assert result.state["failure"] is None
    assert len(result.backend.finalized) == 2
    assert Counter(c.role for c in rig.provider.calls) == {"writer": 2, "evaluator": 2}
    slots = [(call.node, call.call_index) for _, call in rig.ledger.reserved]
    assert slots == [("writer", 0), ("evaluator", 0), ("writer", 3), ("evaluator", 2)]


def test_rejected_evaluation_not_cached_but_parsed_writer_is_cached():
    rig = CacheRig(
        RoutedFake(sequences={"evaluator": [report(["spicy"], fail=["spicy"]), report(["spicy"])]})
    )

    result = rig.run(kind="SENTENCE")

    assert result.state["failure"] is None
    outputs = list(rig.ledger.stored.values())
    assert len([o for o in outputs if "headline" in o]) == 2
    reports = [o for o in outputs if "texts" in o]
    assert len(reports) == 1
    assert all(t["pass"] for o in reports for t in o["texts"])
