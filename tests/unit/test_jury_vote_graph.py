"""그래프 D — 데모 AI 배심원 한 표(18 §3.4·§4.2).

FakeLLM + 이 파일 안의 가짜 백엔드. 네트워크·DB·키 없음.

먼저 실패시킬 케이스(18 문서 헤더) 중 셋이 여기다 — 모델 정상 표 · 모델 실패 템플릿 표 ·
공유 안 된 방 skip.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from geoji_ai.adapters.fake_llm import FakeLLM, FakeScenario, load_role_fixture
from geoji_ai.contracts.case import CaseSnapshot
from geoji_ai.contracts.jobs import Job
from geoji_ai.core.config import Settings
from geoji_ai.domain.intensity import ALL_INTENSITIES
from geoji_ai.graphs.jury_vote import (
    JuryVoteDeps,
    load_juror_templates,
    run_jury_vote,
    template_vote,
)
from geoji_ai.ports.backend import JuryVoteRequest, JuryVoteResult
from geoji_ai.ports.llm import LLMError

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "contracts" / "fixtures"
DB_NOW = datetime(2026, 9, 7, 9, 20, tzinfo=UTC)
GENERATION_ID = "gen-1"
ROOM_ID = "room-ddegeoji-01"
VOTER_ID = "bot-ddegeoji"
POST_ID = "post-taxi-20260907-0852"

JUROR_FIXTURE = load_role_fixture("juror")

#: `run(llm=...)` 의 기본값. `None` 은 "키 없음" 을 뜻하므로 따로 둔다.
_DEFAULT_LLM = object()


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{name}.json").read_text("utf-8"))


# ---------------------------------------------------------------------------
# 가짜 포트
# ---------------------------------------------------------------------------


class Rejected(Exception):
    """어댑터 `BackendRejected` 모양(속성으로 판별)."""

    def __init__(self, status: int, code: str) -> None:
        super().__init__(f"{status} {code}")
        self.status = status
        self.code = code


class FakeBackend:
    def __init__(self, snapshot: CaseSnapshot, *, cast_error: Exception | None = None) -> None:
        self._snapshot = snapshot
        self.cast_error = cast_error
        self.snapshots = 0
        self.casts: list[tuple[str, JuryVoteRequest]] = []

    async def snapshot(self, job_id: str, generation_id: str) -> CaseSnapshot:
        self.snapshots += 1
        return self._snapshot

    async def cast_jury_vote(self, post_id: str, req: JuryVoteRequest) -> JuryVoteResult:
        # 계약 모델로 다시 파싱한다(가짜 백엔드가 JSON 을 받는 것과 같다).
        self.casts.append((post_id, JuryVoteRequest.model_validate(req.model_dump(mode="json"))))
        if self.cast_error is not None:
            raise self.cast_error
        return JuryVoteResult(vote_id="vote-1")

    async def resolve_evidence(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("그래프 D 는 resolve-evidence 를 부르지 않는다")

    async def begin_generation(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("그래프 D 는 begin-generation 을 부르지 않는다")

    async def finalize(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("그래프 D 는 finalize 를 부르지 않는다")

    async def generation_failed(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("그래프 D 는 generation-failed 를 부르지 않는다")


class ErrorLLM(FakeLLM):
    """역할 호출마다 정해진 `LLMError` 를 올리는 가짜."""

    def __init__(self, error: LLMError) -> None:
        super().__init__()
        self.error = error

    async def structured_call(self, *, role: Any, messages: list[dict], schema: dict, **kw: Any):
        await super().structured_call(role=role, messages=messages, schema=schema, **kw)
        raise self.error


# ---------------------------------------------------------------------------
# 입력 만들기
# ---------------------------------------------------------------------------


def make_snapshot(
    *, post_type: str = "spent", intensity: str = "spicy", room_id: str = ROOM_ID
) -> CaseSnapshot:
    data = fixture("case-snapshot-taxi")
    data["post_type"] = post_type
    data["room_snapshots"] = [{"room_id": room_id, "intensity": intensity, "rule_version": 1}]
    data["audience"]["room_ids"] = [room_id]
    return CaseSnapshot.model_validate(data)


def make_job(*, room_id: str = ROOM_ID, voter_id: str = VOTER_ID) -> Job:
    payload = {
        "post_id": POST_ID,
        "post_version": 1,
        "room_id": room_id,
        "voter_id": voter_id,
    }
    return Job(
        id="job-1",
        event_id="event-1",
        event_type="jury.vote_requested",
        kind="JURY_VOTE",
        dedupe_key=f"jury-vote:{POST_ID}:{room_id}:{voter_id}",
        aggregate_id=POST_ID,
        aggregate_version=1,
        schema_version=1,
        payload=payload,
        status="RUNNING",
        priority=60,
        attempts=1,
        max_attempts=2,
        available_at=DB_NOW,
        deadline_at=None,
        lease_until=DB_NOW + timedelta(seconds=15),
        owner_id="worker-1",
        generation_id=GENERATION_ID,
        last_error_code=None,
        trace_id="trace-1",
        created_at=DB_NOW,
        updated_at=DB_NOW,
    )


def make_deps(backend: FakeBackend, llm: Any) -> JuryVoteDeps:
    return JuryVoteDeps(
        backend=backend,  # type: ignore[arg-type]
        settings=Settings(_env_file=None),
        semaphore=asyncio.Semaphore(4),
        generation_id=GENERATION_ID,
        llm=llm,
        prompt_version="bundle-test",
    )


async def run(
    *,
    post_type: str = "spent",
    intensity: str = "spicy",
    room_id: str = ROOM_ID,
    payload_room_id: str | None = None,
    llm: Any = _DEFAULT_LLM,
    cast_error: Exception | None = None,
) -> tuple[dict[str, Any], FakeBackend, Any]:
    backend = FakeBackend(
        make_snapshot(post_type=post_type, intensity=intensity, room_id=room_id),
        cast_error=cast_error,
    )
    model = FakeLLM() if llm is _DEFAULT_LLM else llm
    job = make_job(room_id=payload_room_id or room_id)
    state = await run_jury_vote(job, make_deps(backend, model))
    return dict(state), backend, model


# ---------------------------------------------------------------------------
# ① 모델 정상 → AI 표
# ---------------------------------------------------------------------------


async def test_spent_글_spicy_방_모델_정상이면_fixture_표를_그대로_던진다():
    state, backend, model = await run(post_type="spent", intensity="spicy")

    assert state["outcome"] == "AI"
    assert state["status"] == "CAST"
    assert state["source"] == "AI"
    assert state["verdict"] == "guilty"
    assert state["reason"] == JUROR_FIXTURE["reason"]
    assert len(model.calls) == 1
    assert model.calls[0].role == "juror"

    assert len(backend.casts) == 1
    post_id, req = backend.casts[0]
    assert post_id == POST_ID
    assert req.room_id == ROOM_ID
    assert req.voter_id == VOTER_ID
    assert req.job_id == "job-1"
    assert req.generation_id == GENERATION_ID
    assert (req.verdict, req.reason, req.source) == ("guilty", JUROR_FIXTURE["reason"], "AI")


async def test_모델에_보내는_스키마가_유형별_평결_2개다():
    _, _, model = await run(post_type="spent")
    assert model.calls[0].schema["properties"]["verdict"]["enum"] == ["guilty", "notGuilty"]

    _, _, model = await run(post_type="considering")
    assert model.calls[0].schema["properties"]["verdict"]["enum"] == ["agree", "disagree"]


# ---------------------------------------------------------------------------
# ② considering 글 — 허용 밖 평결이면 검증 실패 → 템플릿
# ---------------------------------------------------------------------------


async def test_considering_글에_guilty_가_오면_검증_실패로_템플릿_disagree_다():
    # xAI strict 는 enum 을 강제하지 않는다(06 §3.3 9/18). 모델이 허용 밖 값을 낸 상황이다.
    model = FakeLLM(outputs={"juror": dict(JUROR_FIXTURE)})
    state, backend, model = await run(post_type="considering", intensity="spicy", llm=model)

    assert model.calls[0].schema["properties"]["verdict"]["enum"] == ["agree", "disagree"]
    assert state["outcome"] == "TEMPLATE"
    assert state["verdict"] == "disagree"
    assert state["source"] == "TEMPLATE"
    assert state["reason"] == load_juror_templates()["reasons"]["spicy"]["considering"]
    # 재작성 호출은 없다. 모델 호출은 총 1회다.
    assert len(model.calls) == 1
    assert backend.casts[0][1].source == "TEMPLATE"


# ---------------------------------------------------------------------------
# ③ 모델 실패 4종 → 템플릿, 모델 호출 1회
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["TIMEOUT", "RATE_LIMIT", "SCHEMA", "PARSE"])
async def test_모델_오류는_템플릿으로_떨어지고_다시_부르지_않는다(kind: str):
    state, backend, model = await run(llm=ErrorLLM(LLMError(kind)))  # type: ignore[arg-type]

    assert state["outcome"] == "TEMPLATE"
    assert state["source"] == "TEMPLATE"
    assert state["fallback_reason"] == f"llm_error:{kind}"
    assert state["verdict"] == "guilty"
    assert len(model.calls) == 1
    assert len(backend.casts) == 1


async def test_거절과_잘림은_출력이_없어_템플릿이다():
    for scenario in (FakeScenario.REFUSAL, FakeScenario.TRUNCATED):
        state, backend, model = await run(llm=FakeLLM(scenario))
        assert state["outcome"] == "TEMPLATE"
        assert state["fallback_reason"] == "no_output"
        assert len(model.calls) == 1
        assert len(backend.casts) == 1


# ---------------------------------------------------------------------------
# ④ 사유 검증 실패 → 템플릿
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reason", "label"),
    [
        ("가" * 61, "61자"),
        ("", "빈 문자열"),
        ("   ", "공백뿐"),
        ("택시비가 과했어요\n다음엔 걸어요", "줄바꿈"),
    ],
)
async def test_사유가_계약을_어기면_템플릿이다(reason: str, label: str):
    model = FakeLLM(outputs={"juror": {"verdict": "guilty", "reason": reason}})
    state, backend, _ = await run(llm=model, intensity="hell")

    assert state["outcome"] == "TEMPLATE", label
    assert state["fallback_reason"] == "invalid_output"
    assert state["reason"] == load_juror_templates()["reasons"]["hell"]["spent"]
    assert len(backend.casts) == 1


async def test_사유_60자_경계는_통과한다():
    reason = "가" * 60
    model = FakeLLM(outputs={"juror": {"verdict": "guilty", "reason": reason}})
    state, _, _ = await run(llm=model)

    assert state["outcome"] == "AI"
    assert state["reason"] == reason


# ---------------------------------------------------------------------------
# ⑤ 템플릿 6칸이 18 §3.4 표 그대로
# ---------------------------------------------------------------------------


EXPECTED_TEMPLATES = {
    ("mild", "spent"): "이번 건은 조금 아쉬워요. 다음엔 한 번만 더 생각해 봐요.",
    ("mild", "considering"): "지금 꼭 필요한지 하루만 더 고민해 봐요.",
    ("spicy", "spent"): "이 돈 쓰기 전에 잔고는 한 번 봤어?",
    ("spicy", "considering"): "사고 싶은 거지 필요한 게 아니잖아. 일주일 재워.",
    ("hell", "spent"): "변명 낭독 끝. 너 잔고가 비명 지르는 거 안 들려? 유죄.",
    ("hell", "considering"): "그거 사면 다음 달 너는 라면이다. 반대.",
}


@pytest.mark.parametrize(("intensity", "post_type"), sorted(EXPECTED_TEMPLATES))
def test_템플릿_표_6칸이_계획서와_같다(intensity: str, post_type: str):
    verdict, reason = template_vote(intensity, post_type)

    assert reason == EXPECTED_TEMPLATES[(intensity, post_type)]
    assert verdict == ("guilty" if post_type == "spent" else "disagree")


@pytest.mark.parametrize("intensity", [i.value for i in ALL_INTENSITIES])
@pytest.mark.parametrize("post_type", ["spent", "considering"])
async def test_모델이_없으면_강도와_유형에_맞는_템플릿을_던진다(intensity: str, post_type: str):
    state, backend, _ = await run(post_type=post_type, intensity=intensity, llm=None)

    assert state["outcome"] == "TEMPLATE"
    assert state["fallback_reason"] == "no_llm"
    assert state["reason"] == EXPECTED_TEMPLATES[(intensity, post_type)]
    assert backend.casts[0][1].verdict == ("guilty" if post_type == "spent" else "disagree")


# ---------------------------------------------------------------------------
# ⑥ 공유 안 된 방 → skip
# ---------------------------------------------------------------------------


async def test_payload_방이_room_snapshots_에_없으면_모델도_cast_도_부르지_않는다():
    state, backend, model = await run(room_id=ROOM_ID, payload_room_id="room-gone")

    assert state["status"] == "SKIPPED"
    assert state["outcome"] == "SKIPPED"
    assert state["skip_reason"] == "room_not_shared"
    assert state["verdict"] is None
    assert model.calls == []
    assert backend.casts == []
    assert backend.snapshots == 1


# ---------------------------------------------------------------------------
# ⑦ llm=None(키 없음) → 템플릿
# ---------------------------------------------------------------------------


async def test_llm_이_없으면_모델_호출_없이_템플릿이다():
    backend = FakeBackend(make_snapshot(intensity="mild"))
    deps = JuryVoteDeps(
        backend=backend,  # type: ignore[arg-type]
        settings=Settings(_env_file=None),
        semaphore=asyncio.Semaphore(1),
        generation_id=GENERATION_ID,
        llm=None,
        prompt_version="bundle-test",
    )

    state = await run_jury_vote(make_job(), deps)

    assert state["outcome"] == "TEMPLATE"
    assert state["fallback_reason"] == "no_llm"
    assert state["source"] == "TEMPLATE"
    assert state["reason"] == load_juror_templates()["reasons"]["mild"]["spent"]
    assert len(backend.casts) == 1


# ---------------------------------------------------------------------------
# ⑧ 시간·토큰 예산이 설정 그대로
# ---------------------------------------------------------------------------


async def test_모델_호출_상한이_설정에서_온다():
    settings = Settings(_env_file=None)
    _, _, model = await run()

    assert model.calls[0].timeout_s == pytest.approx(settings.JUROR_TIMEOUT_SECONDS - 0.2)
    assert model.calls[0].max_output_tokens == settings.JUROR_MAX_OUTPUT_TOKENS


# ---------------------------------------------------------------------------
# ⑨ 게이트웨이(ScopedLLM) 경로 — 운영이 타는 길. 예산 키·노드·call_index 가 18 §3.4 그대로
# ---------------------------------------------------------------------------


class RecordingScopedLLM:
    """`ScopedLLM` 프로토콜의 가짜. scope 와 인자를 기록하고 fixture 출력을 돌려준다."""

    def __init__(self) -> None:
        self.inner = FakeLLM()
        self.scopes: list[Any] = []
        self.kwargs: list[dict[str, Any]] = []
        self.remembered: list[Any] = []

    async def scoped_call(
        self,
        scope: Any,
        *,
        role: Any,
        messages: list[dict],
        schema: dict,
        timeout_s: float,
        max_output_tokens: int,
    ) -> Any:
        from geoji_ai.application.llm_gateway import ScopedResult

        self.scopes.append(scope)
        self.kwargs.append(
            {"role": role, "timeout_s": timeout_s, "max_output_tokens": max_output_tokens}
        )
        result = await self.inner.structured_call(
            role=role,
            messages=messages,
            schema=schema,
            timeout_s=timeout_s,
            max_output_tokens=max_output_tokens,
        )
        return ScopedResult(
            result=result, reused=False, call_id="call-1", request_hash="h", versions={}
        )

    async def remember(self, scoped: Any) -> None:
        self.remembered.append(scoped)


async def test_게이트웨이_경로는_사건_예산_키와_juror_노드로_1회_부른다():
    from geoji_ai.domain.budget import budget_key_for_post

    gateway = RecordingScopedLLM()
    state, backend, _ = await run(llm=gateway)

    assert state["outcome"] == "AI"
    assert len(gateway.scopes) == 1
    scope = gateway.scopes[0]
    assert scope.budget_key == budget_key_for_post(POST_ID)
    assert scope.node == "juror"
    assert scope.call_index == 0
    assert scope.job_id == "job-1"
    assert scope.generation_id == GENERATION_ID
    assert scope.prompt_version == "bundle-test"
    assert gateway.kwargs[0]["role"] == "juror"
    assert (
        gateway.kwargs[0]["max_output_tokens"] == Settings(_env_file=None).JUROR_MAX_OUTPUT_TOKENS
    )
    assert len(backend.casts) == 1
    assert backend.casts[0][1].source == "AI"


# ---------------------------------------------------------------------------
# ⑩ 실제 timeout(asyncio.wait_for) — LLMError 가 아닌 TimeoutError 가지
# ---------------------------------------------------------------------------


class SlowLLM(FakeLLM):
    """상한보다 오래 걸리는 가짜. `wait_for` 가 TimeoutError 를 낸다."""

    async def structured_call(self, *, role: Any, messages: list[dict], schema: dict, **kw: Any):
        await asyncio.sleep(5)
        return await super().structured_call(role=role, messages=messages, schema=schema, **kw)


async def test_모델이_상한을_넘기면_TimeoutError_로_템플릿이다():
    backend = FakeBackend(make_snapshot(post_type="spent", intensity="mild"))
    deps = make_deps(backend, SlowLLM())
    deps.settings = Settings(_env_file=None, JUROR_TIMEOUT_SECONDS=0.4)

    state = await run_jury_vote(make_job(), deps)

    assert state["outcome"] == "TEMPLATE"
    assert state["fallback_reason"] == "llm_error:TIMEOUT"
    verdict, reason = template_vote("mild", "spent")
    assert (state["verdict"], state["reason"]) == (verdict, reason)
    assert len(backend.casts) == 1
    assert backend.casts[0][1].source == "TEMPLATE"


# ---------------------------------------------------------------------------
# ⑪ 리뷰 9/20 — 게이트웨이 remaining_s·remember, EvidenceInvalidated, 모르는 키
# ---------------------------------------------------------------------------


async def test_게이트웨이_scope_는_노드_상한으로_묶이고_검증_뒤_remember_한다():
    gateway = RecordingScopedLLM()
    state, _, _ = await run(llm=gateway)

    scope = gateway.scopes[0]
    assert scope.remaining_s is not None
    assert 0 < scope.remaining_s() <= Settings(_env_file=None).JUROR_TIMEOUT_SECONDS
    assert scope.reserve_s == pytest.approx(0.2)
    assert state["outcome"] == "AI"
    assert len(gateway.remembered) == 1


async def test_검증에_떨어진_출력은_remember_하지_않는다():
    class BadScopedLLM(RecordingScopedLLM):
        def __init__(self) -> None:
            super().__init__()
            self.inner = FakeLLM(outputs={"juror": {"verdict": "guilty", "reason": ""}})

    gateway = BadScopedLLM()
    state, _, _ = await run(llm=gateway)

    assert state["outcome"] == "TEMPLATE"
    assert gateway.remembered == []


async def test_epoch_불일치는_모델_없이_템플릿으로_간다():
    from geoji_ai.ports.preparation import EvidenceInvalidated

    class InvalidatedScopedLLM(RecordingScopedLLM):
        async def scoped_call(self, scope: Any, **kw: Any) -> Any:
            self.scopes.append(scope)
            raise EvidenceInvalidated(["post:p1"])

    gateway = InvalidatedScopedLLM()
    state, backend, _ = await run(llm=gateway)

    assert state["outcome"] == "TEMPLATE"
    assert state["fallback_reason"] == "evidence_invalidated"
    assert len(backend.casts) == 1
    assert backend.casts[0][1].source == "TEMPLATE"


async def test_모르는_키가_섞인_출력은_템플릿이다():
    model = FakeLLM(
        outputs={"juror": {"verdict": "guilty", "reason": "택시비 치고 비싸요", "foo": 1}}
    )
    state, _, _ = await run(llm=model)

    assert state["outcome"] == "TEMPLATE"
    assert state["fallback_reason"] == "invalid_output"
