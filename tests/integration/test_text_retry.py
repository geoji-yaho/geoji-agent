"""TEXT_RETRY 핸들러(08 §3.2·§4.2 `test_text_retry`). 실제 Postgres + 가짜 백엔드 + FakeLLM.

`test_graph_c_flow` 의 Env·SpyBackend·PlannedLLM 을 그대로 쓴다. 준비는 매번 같다: PREPARE →
SENTENCE(hell 서기 실패) → FINAL/AI · spicy AI · hell TEMPLATE · `TEMPLATE_READY`.

① begin 형량과 다른 형량이면 finalize 거부 · 워커 요청 형량 = begin 고정값
② `intensities=["hell"]` → 서기 1·검수 1·양형 0, finalize texts=[hell]
③ 예산(`TEXT_RETRY_TIMEOUT_SECONDS`) 초과 → finalize 0·generation_failed
④ prep 무효화 뒤 → 무효 prep 미사용(MINIMAL, 조서 0)
⑤ 성공 → text_version +1, 형량·양형 이유 불변, 나머지 강도 유지
⑥ 검수 실패 → repair 0·finalize 0·EVAL_FAILED
⑦ `intensities` 없는 payload → target 전체
추가: ⑧ 가짜 백엔드는 target 밖 강도 finalize 를 422 로 거부
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from geoji_ai.adapters.fake_llm import FakeLLM, FakeScenario
from geoji_ai.application.sentence_case import SentenceHandler
from geoji_ai.contracts.finalize import FinalizeRequest
from geoji_ai.domain.intensity import Intensity
from tests.integration import test_graph_c_flow as _flow
from tests.integration.test_graph_b import CONTEXT_OK
from tests.integration.test_graph_c_flow import (
    ROOM_B,
    VERDICT_ID,
    Env,
    PlannedLLM,
    SpyBackend,
    _dossier_ids,
    _enum,
    _labels,
    _rows,
    _snapshot_data,
    _sources,
    _user_payload,
)
from tests.integration.test_retain_handler import seed_epochs
from tests.integration.test_worker_runtime import make_settings

# `test_graph_c_flow` 의 pytest fixture 를 이 모듈에 등록한다(conftest 는 소유 밖).
env = _flow.env
snapshot_path = _flow.snapshot_path

SENTENCES = ("probation", "oneDay", "life")


class TamperBackend(SpyBackend):
    """워커가 만든 finalize 요청을 기록한 뒤 `tamper` 로 바꿔 보낸다."""

    def __init__(self, inner: Any, tamper: Callable[[FinalizeRequest], FinalizeRequest]) -> None:
        super().__init__(inner)
        self.tamper = tamper

    async def finalize(self, verdict_id: str, req: FinalizeRequest) -> Any:
        self.finalized.append(req)
        return await self.inner.finalize(verdict_id, self.tamper(req))


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def _after_initial(env: Env) -> tuple[str | None, str | None, int]:
    """PREPARE → SENTENCE(hell 서기 실패). 형량·이유·text_version 을 돌려주고 기록을 비운다."""
    await env.prepare(FakeLLM(outputs={"context": CONTEXT_OK}))
    await env.sentence(PlannedLLM(FakeLLM(FakeScenario.INTENSITY_FAIL)))
    verdict = env.fake.verdicts[VERDICT_ID]
    assert (verdict.sentence_status, verdict.sentence_source) == ("FINAL", "AI")
    assert verdict.text_sources == {"spicy": "AI", "hell": "TEMPLATE"}
    assert verdict.text_status == "TEMPLATE_READY"
    env.backend.finalized.clear()
    env.backend.failed.clear()
    env.fake.calls.clear()
    return verdict.sentence, verdict.sentencing_reason, verdict.text_version


# --- ① 형량이 다르면 거부 ---------------------------------------------------------


async def test_01_begin_형량과_다른_형량이면_finalize_거부_워커는_형량을_바꾸지_않는다(env: Env):
    sentence, reason, text_version = await _after_initial(env)
    other = next(s for s in SENTENCES if s != sentence)

    def change_sentence(req: FinalizeRequest) -> FinalizeRequest:
        assert req.sentencing is not None
        changed = req.sentencing.model_copy(update={"sentence": other})
        return req.model_copy(update={"sentencing": changed})

    env.backend = TamperBackend(env.backend.inner, change_sentence)
    llm = PlannedLLM()

    retry_id = await env.sentence(llm, kind="TEXT_RETRY")

    assert (await env.fetch_job(retry_id))["status"] == "SUCCEEDED"
    assert llm.roles()["sentencing"] == 0
    # 워커가 만든 요청의 형량 필드 = begin 고정값
    assert len(env.backend.finalized) == 1
    sent = env.backend.finalized[0].sentencing
    assert sent is not None
    assert (str(sent.sentence), sent.sentencing_reason) == (sentence, reason)
    # 바뀐 형량은 가짜 백엔드가 422 로 거부 → 저장 없음
    assert env.paths().count("finalize") == 1
    assert env.backend.failed == ["SCHEMA_INVALID"]
    verdict = env.fake.verdicts[VERDICT_ID]
    assert (verdict.sentence, verdict.sentencing_reason, verdict.text_version) == (
        sentence,
        reason,
        text_version,
    )


# --- ② intensities 부분집합 ---------------------------------------------------------


async def test_02_intensities_hell_만_재생성_서기1_검수1_양형0(env: Env):
    await _after_initial(env)
    llm = PlannedLLM()

    retry_id = await env.sentence(llm, kind="TEXT_RETRY", intensities=["hell"])

    assert (await env.fetch_job(retry_id))["status"] == "SUCCEEDED"
    assert llm.roles() == Counter({"writer": 1, "evaluator": 1})
    assert _enum(llm.of("writer")[0][1], "properties", "intensity") == ["hell"]
    assert _enum(
        llm.of("evaluator")[0][1], "properties", "texts", "items", "properties", "intensity"
    ) == ["hell"]
    assert len(env.backend.finalized) == 1
    req = env.backend.finalized[0]
    assert _sources(req) == [("hell", "AI")]
    assert [str(t.intensity) for t in req.evaluation.texts] == ["hell"]
    assert env.backend.failed == []


# --- ③ 예산 초과 -------------------------------------------------------------------


async def test_03_TEXT_RETRY_예산을_넘기면_finalize_없이_generation_failed(env: Env):
    _, _, text_version = await _after_initial(env)
    clock = FakeClock()

    async def slow(role: str) -> None:
        if role == "writer":
            clock.now += 4.8  # 두 강도 합 9.6초 → 10초 예산에서 검수 시간이 없다

    llm = PlannedLLM(before=slow)
    settings = make_settings(TEXT_RETRY_TIMEOUT_SECONDS=10)

    retry_id = await env.sentence(
        llm, kind="TEXT_RETRY", settings=settings, handler=SentenceHandler(clock=clock)
    )

    assert (await env.fetch_job(retry_id))["status"] == "SUCCEEDED"
    assert llm.roles()["evaluator"] == 0
    assert env.backend.finalized == []
    assert "finalize" not in env.paths()
    assert env.backend.failed == ["DEADLINE_EXCEEDED"]
    verdict = env.fake.verdicts[VERDICT_ID]
    assert verdict.text_version == text_version
    assert verdict.text_sources == {"spicy": "AI", "hell": "TEMPLATE"}


# --- ④ prep 무효화 뒤 ----------------------------------------------------------------


async def test_04_prep_무효화_뒤_TEXT_RETRY_는_무효_prep_을_읽지_않고_MINIMAL(
    env: Env, snapshot_path: Path
):
    await _after_initial(env)
    prep = await _rows(
        env.engine, "SELECT CAST(dossier_id AS text) AS dossier_id FROM ai.trial_prep"
    )
    assert len(prep) == 1
    before_ids = await _dossier_ids(env.engine)
    # 방 B 탈퇴·삭제로 epoch +1. 백엔드 스냅샷도 새 epoch 를 싣는다.
    await seed_epochs(env.engine, {f"room:{ROOM_B}": 2})
    data = _snapshot_data()
    for pv in data["privacy_versions"]:
        if pv["scope_key"] == f"room:{ROOM_B}":
            pv["epoch"] = 2
    snapshot_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    llm = PlannedLLM(FakeLLM(outputs={"context": CONTEXT_OK}))

    retry_id = await env.sentence(llm, kind="TEXT_RETRY")

    assert (await env.fetch_job(retry_id))["status"] == "SUCCEEDED"
    assert llm.roles()["context"] == 0
    assert llm.roles()["sentencing"] == 0
    assert "resolve-evidence" not in env.paths()
    # 새 MINIMAL dossier(F0 만)를 저장했고, 서기 입력에도 무효 prep 의 근거가 없다.
    new_ids = [d for d in await _dossier_ids(env.engine) if d not in before_ids]
    assert len(new_ids) == 1 and prep[0]["dossier_id"] not in new_ids
    assert [label for label, _ in await _labels(env.engine, new_ids[0])] == ["F0"]
    writers = llm.of("writer")
    assert len(writers) == 2
    assert all(
        [fact["id"] for fact in _user_payload(messages)["dossier"]] == ["F0"]
        for _, _, messages in writers
    )
    # 서기 fixture 가 F0 밖 라벨을 인용해 ⑤ 에서 걸린다(fixture 한계).
    # TEMPLATE 을 새로 저장하지 않는다.
    assert env.backend.finalized == []
    assert env.backend.failed == ["EVAL_FAILED"]


# --- ⑤ 성공 ------------------------------------------------------------------------


async def test_05_성공하면_text_version_만_오르고_형량_이유와_나머지_강도는_그대로(env: Env):
    sentence, reason, text_version = await _after_initial(env)
    retain_before = await _rows(env.engine, "SELECT id FROM ai.jobs WHERE kind = 'RETAIN'")
    llm = PlannedLLM()

    retry_id = await env.sentence(llm, kind="TEXT_RETRY", intensities=["hell"])

    assert (await env.fetch_job(retry_id))["status"] == "SUCCEEDED"
    req = env.backend.finalized[0]
    assert req.expected_text_version == text_version
    assert req.sentencing is not None
    assert (str(req.sentencing.sentence), req.sentencing.sentencing_reason) == (sentence, reason)
    verdict = env.fake.verdicts[VERDICT_ID]
    assert verdict.text_version == text_version + 1
    assert (verdict.sentence_status, verdict.sentence_source) == ("FINAL", "AI")
    assert (verdict.sentence, verdict.sentencing_reason) == (sentence, reason)
    # 받은 강도만 바뀌고 spicy 는 기존 행 유지 → 전 강도 AI
    assert verdict.text_sources == {"spicy": "AI", "hell": "AI"}
    assert verdict.text_status == "AI_READY"
    # 최초 확정이 아니라 RETAIN 을 다시 만들지 않는다
    assert await _rows(env.engine, "SELECT id FROM ai.jobs WHERE kind = 'RETAIN'") == retain_before


# --- ⑥ 검수 실패 --------------------------------------------------------------------


async def test_06_검수_실패면_repair_없이_finalize_0_EVAL_FAILED(env: Env):
    _, _, text_version = await _after_initial(env)
    rounds_before = len(env.fake.text_retry_rounds)
    llm = PlannedLLM(evaluator_fails=[{"hell"}])

    retry_id = await env.sentence(llm, kind="TEXT_RETRY")

    assert (await env.fetch_job(retry_id))["status"] == "SUCCEEDED"
    assert llm.roles() == Counter({"writer": 2, "evaluator": 1})
    assert env.backend.finalized == []
    assert env.backend.failed == ["EVAL_FAILED"]
    verdict = env.fake.verdicts[VERDICT_ID]
    assert verdict.text_version == text_version
    assert verdict.text_sources == {"spicy": "AI", "hell": "TEMPLATE"}
    # 다음 round 예약은 백엔드 몫
    assert len(env.fake.text_retry_rounds) == rounds_before + 1


# --- ⑦ intensities 없음 --------------------------------------------------------------


async def test_07_intensities_없는_payload_는_target_전체를_다시_쓴다(env: Env):
    await _after_initial(env)
    llm = PlannedLLM()

    retry_id = await env.sentence(llm, kind="TEXT_RETRY")

    assert (await env.fetch_job(retry_id))["status"] == "SUCCEEDED"
    assert sorted(
        _enum(schema, "properties", "intensity")[0] for _, schema, _ in llm.of("writer")
    ) == [
        "hell",
        "spicy",
    ]
    assert _sources(env.backend.finalized[0]) == [("spicy", "AI"), ("hell", "AI")]
    assert env.fake.verdicts[VERDICT_ID].text_status == "AI_READY"


# --- ⑧ 가짜 백엔드: target 밖 강도 거부 --------------------------------------------------


async def test_08_가짜_백엔드는_target_밖_강도_재생성을_422_로_거부한다(env: Env):
    _, _, text_version = await _after_initial(env)

    def rename_to_mild(req: FinalizeRequest) -> FinalizeRequest:
        texts = [t.model_copy(update={"intensity": Intensity.mild}) for t in req.draft.texts]
        return req.model_copy(update={"draft": req.draft.model_copy(update={"texts": texts})})

    env.backend = TamperBackend(env.backend.inner, rename_to_mild)

    await env.sentence(PlannedLLM(), kind="TEXT_RETRY", intensities=["hell"])

    assert env.backend.failed == ["SCHEMA_INVALID"]
    verdict = env.fake.verdicts[VERDICT_ID]
    assert verdict.text_version == text_version
    assert "mild" not in verdict.text_sources
