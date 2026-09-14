"""D-26 그래프 C: 모델 호출 직전 epoch 불일치(`EvidenceInvalidated`) 전파(9/14, 10 §8·§15.5).

게이트웨이가 `EvidenceInvalidated` 를 올리면 어느 노드든 `generation_failed(EVIDENCE_INVALIDATED)`,
finalize 0, 그 뒤 모델 호출 0 이다. 서기 fan-out 은 한 강도가 무효를 받으면 전체 무효(TEMPLATE 치환
아님). 가짜 `ScopedLLM` + `tests/unit/test_graph_c.py` 의 가짜 포트. 네트워크·DB 없음.
"""

from __future__ import annotations

from typing import Any

import pytest

from geoji_ai.adapters.fake_llm import FakeLLM
from geoji_ai.application.llm_gateway import CallScope, ScopedLLM, ScopedResult
from geoji_ai.core.config import Settings
from geoji_ai.ports.preparation import EvidenceInvalidated
from tests.unit.test_graph_c import run


class InvalidatingGateway:
    """`ScopedLLM` 모양. `invalid_role` 호출에서 `EvidenceInvalidated`, 나머지는 FakeLLM."""

    def __init__(self, invalid_role: str) -> None:
        self.inner = FakeLLM()
        self.invalid_role = invalid_role
        self.roles: list[str] = []
        self.remembered = 0

    async def scoped_call(
        self,
        scope: CallScope,
        *,
        role: Any,
        messages: list[dict],
        schema: dict,
        timeout_s: float,
        max_output_tokens: int,
    ) -> ScopedResult:
        self.roles.append(role)
        if role == self.invalid_role:
            raise EvidenceInvalidated(["room:room-ddegeoji-01"])
        result = await self.inner.structured_call(
            role=role,
            messages=messages,
            schema=schema,
            timeout_s=timeout_s,
            max_output_tokens=max_output_tokens,
        )
        return ScopedResult(result, False, None, "0" * 64, {})

    async def remember(self, scoped: ScopedResult) -> None:
        self.remembered += 1


def test_가짜_게이트웨이는_ScopedLLM_이다():
    assert isinstance(InvalidatingGateway("writer"), ScopedLLM)


@pytest.mark.parametrize(
    ("invalid_role", "run_kwargs", "not_called"),
    [
        ("sentencing", {}, {"writer", "evaluator"}),
        ("writer", {}, {"evaluator"}),
        ("evaluator", {}, set()),
        (
            "context",
            {
                "prep": None,
                "settings": Settings(
                    _env_file=None, WRITER_NODE_TIMEOUT_SECONDS=2, EVALUATOR_NODE_TIMEOUT_SECONDS=2
                ),
                "backend_kwargs": {"remaining_s": 10.0},
            },
            {"sentencing", "writer", "evaluator"},
        ),
    ],
    ids=["양형", "서기", "검수", "즉석조서"],
)
def test_어느_노드든_무효를_받으면_generation_failed_EVIDENCE_INVALIDATED_finalize_0(
    invalid_role: str, run_kwargs: dict[str, Any], not_called: set[str]
):
    gateway = InvalidatingGateway(invalid_role)

    result = run(gateway, **run_kwargs)  # type: ignore[arg-type]

    assert result.backend.failed == ["EVIDENCE_INVALIDATED"]
    assert result.backend.finalized == []
    assert result.jobs.completed == ["job-1"]
    assert result.jobs.failures == []
    assert invalid_role in gateway.roles
    assert not_called.isdisjoint(gateway.roles)
    assert result.state["failure"] == "EVIDENCE_INVALIDATED"


def test_서기_한_강도만_무효여도_TEMPLATE_치환_없이_전체_무효():
    class OneIntensity(InvalidatingGateway):
        async def scoped_call(self, scope: CallScope, **kw: Any) -> ScopedResult:
            if kw["role"] == "writer" and "hell" in str(kw["schema"]):
                self.roles.append("writer")
                raise EvidenceInvalidated(["room:room-ddegeoji-01"])
            return await super().scoped_call(scope, **kw)

    gateway = OneIntensity("none")

    result = run(gateway)  # type: ignore[arg-type]

    assert result.backend.failed == ["EVIDENCE_INVALIDATED"]
    assert result.backend.finalized == []
    assert "evaluator" not in gateway.roles
