"""D-25 즉석 조서 시간 규칙(9/14, 05 §3.1·§3.3, 10 §15.5).

즉석 조서(`inline_context`)는 서기 상한 + 검수 상한 + finalize 예약을 먼저 남기고 남는 시간만 쓴다.
남는 시간이 없으면 시작하지 않고 `minimal_dossier`. `INLINE_CONTEXT_MIN_REMAINING_MS` 는 하한 보조.

FakeLLM + `tests/unit/test_graph_c.py` 의 가짜 포트·주입 시계. 네트워크·DB·실제 대기 없음.
"""

from __future__ import annotations

from typing import Any

import pytest

from geoji_ai.adapters.fake_llm import FakeLLM
from geoji_ai.core.config import Settings
from geoji_ai.domain.budget import FINALIZE_RESERVE_SECONDS, inline_context_reserve
from tests.unit.test_graph_c import FakeClock, run

#: 서기 2초·검수 2초로 낮춘 설정(스펙 단위 케이스). 제품 값이 아니다.
LOW_CAPS: dict[str, float] = {"WRITER_NODE_TIMEOUT_SECONDS": 2, "EVALUATOR_NODE_TIMEOUT_SECONDS": 2}


def _settings(**over: Any) -> Settings:
    return Settings(_env_file=None, **over)


class SlowContextLLM(FakeLLM):
    """조서 호출이 받은 timeout 을 다 쓰고 끝난 것처럼 주입 시계를 민다."""

    def __init__(self, clock: FakeClock) -> None:
        super().__init__()
        self.clock = clock

    async def structured_call(self, **kw: Any) -> Any:
        result = await super().structured_call(**kw)
        if kw["role"] == "context":
            self.clock.now += kw["timeout_s"]
        return result


def test_즉석_조서_예약은_서기_상한_검수_상한_finalize_예약의_합():
    assert inline_context_reserve(_settings()) == 6.0 + 4.0 + FINALIZE_RESERVE_SECONDS
    assert inline_context_reserve(_settings(**LOW_CAPS)) == 2.0 + 2.0 + FINALIZE_RESERVE_SECONDS


def test_기본_상한에서_남은_10s_면_즉석_조서_호출_0_MINIMAL():
    result = run(prep=None, backend_kwargs={"remaining_s": 10.0})

    assert result.state["dossier_source"] == "MINIMAL"
    assert result.roles()["context"] == 0


def test_상한을_낮추면_남은_10s_에서_즉석_조서_1회_timeout_은_남은_빼기_예약_이하():
    settings = _settings(**LOW_CAPS)

    result = run(prep=None, settings=settings, backend_kwargs={"remaining_s": 10.0})

    assert result.state["dossier_source"] == "INLINE"
    contexts = result.calls_of("context")
    assert len(contexts) == 1
    # 스펙 문구 "≤ 10 − 4.5" 를 구현 함수와 독립적으로 고정한다(서기 2 + 검수 2 + finalize 0.5).
    assert 0 < contexts[0].timeout_s <= 5.5


@pytest.mark.parametrize(
    ("caps", "remaining_s"),
    [
        (LOW_CAPS, 10.0),
        # 남은 − 예약(3.5)이 서기 상한(4)보다 작아 조서 timeout 이 예약 규칙으로 정해지는 경우
        ({"WRITER_NODE_TIMEOUT_SECONDS": 4, "EVALUATOR_NODE_TIMEOUT_SECONDS": 1}, 9.0),
    ],
    ids=["서기2_검수2_남은10", "서기4_검수1_남은9"],
)
def test_조서가_timeout_까지_걸려도_서기_timeout_은_0보다_크다(
    caps: dict[str, float], remaining_s: float
):
    settings = _settings(**caps)
    clock = FakeClock()
    llm = SlowContextLLM(clock)

    result = run(
        llm,
        prep=None,
        settings=settings,
        backend_kwargs={"remaining_s": remaining_s},
        clock=clock,
    )

    contexts = result.calls_of("context")
    assert len(contexts) == 1
    expected = min(
        caps["WRITER_NODE_TIMEOUT_SECONDS"], remaining_s - inline_context_reserve(settings)
    )
    assert contexts[0].timeout_s == pytest.approx(expected)
    writers = result.calls_of("writer")
    assert len(writers) == 2
    assert all(call.timeout_s > 0 for call in writers)
    assert "DEADLINE_EXCEEDED" not in result.backend.failed
