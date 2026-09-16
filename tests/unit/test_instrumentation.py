"""계측 배선(08 §3.3 로그·지표·알림, §4.1 관측).

그래프 C 는 `tests/unit/test_graph_c.py` 의 FakeLLM·가짜 포트 실행(`run`)을 그대로 쓴다.
지표는 테스트마다 새 `MetricsRegistry` 로 바꿔 끼운다. 알림 싱크는 메모리 가짜다.
네트워크·DB·키·디스코드 호출 없음.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import replace
from datetime import date
from typing import Any

import pytest
import structlog

from geoji_ai.application import instrument
from geoji_ai.application.llm_gateway import LLMGateway
from geoji_ai.contracts.jobs import Job
from geoji_ai.core.config import Settings
from geoji_ai.ports.llm import Cost, LLMError
from geoji_ai.telemetry import metrics
from geoji_ai.telemetry.alerts import Alert, AlertKind, AlertNotifier, DailyCostTracker
from geoji_ai.workers import dispatch
from geoji_ai.workers.main import Worker
from tests.unit import test_graph_c as gc
from tests.unit.test_llm_gateway import (
    MAX_OUTPUT,
    MESSAGES,
    PRICES,
    SCHEMA,
    FakeInner,
    FakeLedger,
    RecordingHealth,
    ok_result,
    scope,
)

UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
HEX32_RE = re.compile(r"^[0-9a-fA-F]{32}$")


# ---------------------------------------------------------------------------
# 가짜
# ---------------------------------------------------------------------------


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> metrics.MetricsRegistry:
    fresh = metrics.MetricsRegistry()
    monkeypatch.setattr(metrics, "REGISTRY", fresh)
    return fresh


class FakeSink:
    def __init__(self) -> None:
        self.sent: list[Alert] = []

    async def send(self, alert: Alert) -> None:
        self.sent.append(alert)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class Unavailable(Exception):
    """어댑터 `BackendUnavailable` 모양(재전송 소진 — status 없음)."""

    def __init__(self) -> None:
        super().__init__("backend unavailable")
        self.error_code = "BACKEND_UNAVAILABLE"
        self.retry_after_s = 5


class JobsStub:
    def __init__(self) -> None:
        self.failed: list[str] = []

    async def claim(self, kinds: Any, worker_id: str) -> Job | None:
        return None

    async def heartbeat(self, job_id: str, worker_id: str, generation_id: str) -> bool:
        return True

    async def complete(self, job_id: str, worker_id: str, generation_id: str) -> bool:
        return True

    async def fail(
        self,
        job_id: str,
        worker_id: str,
        generation_id: str,
        error_code: str,
        retry_after_s: float | None,
    ) -> bool:
        self.failed.append(error_code)
        return True

    async def release(self, job_id: str, worker_id: str, generation_id: str) -> bool:
        return True


def series(reg: metrics.MetricsRegistry, name: str, **labels: str) -> list[dict[str, Any]]:
    return [s for s in reg.snapshot()["metrics"] if s["name"] == name and s["labels"] == labels]


def one(reg: metrics.MetricsRegistry, name: str, **labels: str) -> dict[str, Any]:
    found = series(reg, name, **labels)
    assert len(found) == 1, (name, labels, reg.snapshot())
    return found[0]


def fixed_sentencing() -> dict[str, Any]:
    decision = gc.fixture_sentencing()
    return {
        "sentence": decision.sentence,
        "sentencing_reason": decision.sentencing_reason,
        "reason_source": decision.reason_source,
    }


# ---------------------------------------------------------------------------
# 노드 로그
# ---------------------------------------------------------------------------


def test_sentence_node_logs_have_required_fields_and_no_raw_text(
    registry: metrics.MetricsRegistry,
) -> None:
    with structlog.testing.capture_logs() as logs:
        result = gc.run()
    assert result.jobs.completed == ["job-1"]

    nodes = [e for e in logs if e.get("event") == "sentence_node"]
    assert {
        "begin_generation",
        "load_valid_prep",
        "sentencing",
        "writer",
        "join",
        "deterministic_validate",
        "evaluator",
        "finalize",
    } <= {e["node"] for e in nodes}
    for event in nodes:
        assert event["trace_id"] == "trace-1"
        assert event["job_id"] == "job-1"
        assert event["node"]
        assert event["graph_name"] == "sentencing"
        assert isinstance(event["latency_ms"], int)
        assert event["ok"] is True
        assert "dropped_fields" not in event

    snapshot = gc.make_snapshot()
    req = result.finalize()
    raw = [
        snapshot.item,
        snapshot.reason,
        *(t.headline for t in req.draft.texts),
        *(s.text for t in req.draft.texts for s in t.statement),
    ]
    emitted = repr(logs)
    for text in raw:
        if text:
            assert text not in emitted


def test_failed_node_logs_failure_code(registry: metrics.MetricsRegistry) -> None:
    with structlog.testing.capture_logs() as logs:
        gc.run(preparation=gc.FakePreparation(gc.make_prep(), stale=["user:user-01H8Z9QK"]))
    failed = [e for e in logs if e.get("event") == "sentence_node" and e["ok"] is False]
    assert {e["node"] for e in failed} == {"finalize", "generation_failed"}
    assert {e["fallback_reason"] for e in failed} == {"EVIDENCE_INVALIDATED"}


# ---------------------------------------------------------------------------
# 지표
# ---------------------------------------------------------------------------


def test_guilty_finalize_observes_first_result_latency_and_template_rate(
    registry: metrics.MetricsRegistry,
) -> None:
    gc.run()
    assert one(registry, "first_result_latency_seconds", path="guilty")["count"] == 1
    rate = one(registry, "template_first_rate")
    assert (rate["hits"], rate["total"]) == (0, 1)
    assert series(registry, "retry_recovery_rate") == []


def test_repair_success_counts_violation_and_repair_hit(
    registry: metrics.MetricsRegistry,
) -> None:
    llm = gc.ScriptedLLM(
        sequences={"evaluator": [gc.report(["spicy", "hell"], fail=["hell"]), gc.report(["hell"])]}
    )
    gc.run(llm)
    assert one(registry, "evaluation_failure_total", code="PERSONAL_ATTACK")["value"] == 1
    repair = one(registry, "evaluation_repair_rate", intensity="hell")
    assert (repair["hits"], repair["total"]) == (1, 1)
    rate = one(registry, "template_first_rate")
    assert (rate["hits"], rate["total"]) == (0, 1)


def test_repair_failure_saves_template(registry: metrics.MetricsRegistry) -> None:
    llm = gc.ScriptedLLM(
        sequences={
            "evaluator": [
                gc.report(["spicy", "hell"], fail=["hell"]),
                gc.report(["hell"], fail=["hell"]),
                None,
            ]
        }
    )
    gc.run(llm)
    assert one(registry, "evaluation_failure_total", code="PERSONAL_ATTACK")["value"] == 2
    repair = one(registry, "evaluation_repair_rate", intensity="hell")
    assert (repair["hits"], repair["total"]) == (0, 1)
    rate = one(registry, "template_first_rate")
    assert (rate["hits"], rate["total"]) == (1, 1)


def test_regenerate_observes_regen_path_and_recovery(registry: metrics.MetricsRegistry) -> None:
    result = gc.run(kind="TEXT_RETRY", backend_kwargs={"fixed": fixed_sentencing()})
    assert len(result.backend.finalized) == 1
    assert one(registry, "first_result_latency_seconds", path="regen")["count"] == 1
    recovery = one(registry, "retry_recovery_rate")
    assert (recovery["hits"], recovery["total"]) == (1, 1)
    assert series(registry, "template_first_rate") == []


def test_regenerate_failure_is_recovery_miss(registry: metrics.MetricsRegistry) -> None:
    llm = gc.ScriptedLLM(errors={"writer": gc.LLMError("TIMEOUT")})
    result = gc.run(llm, kind="TEXT_RETRY", backend_kwargs={"fixed": fixed_sentencing()})
    assert result.backend.finalized == []
    recovery = one(registry, "retry_recovery_rate")
    assert (recovery["hits"], recovery["total"]) == (0, 1)


def test_stale_finalize_counted_by_kind(registry: metrics.MetricsRegistry) -> None:
    gc.run(backend_kwargs={"finalize_error": gc.Rejected(409, "STALE_GENERATION")})
    assert one(registry, "stale_finalize_total", kind="SENTENCE")["value"] == 1
    assert series(registry, "first_result_latency_seconds", path="guilty") == []


def test_invalidated_evidence_counted(registry: metrics.MetricsRegistry) -> None:
    gc.run(preparation=gc.FakePreparation(gc.make_prep(), stale=["user:user-01H8Z9QK"]))
    assert one(registry, "invalidated_evidence_total")["value"] == 1


def test_metric_labels_have_no_individual_ids(registry: metrics.MetricsRegistry) -> None:
    gc.run()
    gc.run(backend_kwargs={"finalize_error": gc.Rejected(409, "STALE_GENERATION")})
    gc.run(kind="TEXT_RETRY", backend_kwargs={"fixed": fixed_sentencing()})
    job = gc.make_job()
    ids = {job.id, job.trace_id, job.generation_id, job.aggregate_id, gc.make_snapshot().post_id}
    all_series = registry.snapshot()["metrics"]
    assert all_series
    for s in all_series:
        for value in s["labels"].values():
            assert not UUID_RE.search(value)
            assert not HEX32_RE.match(value)
            assert not value.isdigit()
            assert value not in ids


# ---------------------------------------------------------------------------
# 알림
# ---------------------------------------------------------------------------


def test_finalize_db_error_alerts_once_then_groups_within_5_minutes(
    registry: metrics.MetricsRegistry,
) -> None:
    sink = FakeSink()
    clock = FakeClock()
    notifier = AlertNotifier(sink, clock=clock)

    first = gc.run(backend_kwargs={"finalize_error": Unavailable()}, notifier=notifier)
    assert first.jobs.failures == [("BACKEND_UNAVAILABLE", 5)]
    assert len(sink.sent) == 1
    assert sink.sent[0].kind is AlertKind.FINALIZE_DB_ERROR
    assert dict(sink.sent[0].details) == {"job_kind": "SENTENCE", "code": "BACKEND_UNAVAILABLE"}

    clock.now = 299.0
    gc.run(backend_kwargs={"finalize_error": Unavailable()}, notifier=notifier)
    assert len(sink.sent) == 1  # 5분 안 2회째는 묶인다

    clock.now = 300.0
    gc.run(backend_kwargs={"finalize_error": Unavailable()}, notifier=notifier)
    assert len(sink.sent) == 2
    assert sink.sent[1].suppressed == 1


@pytest.mark.parametrize(
    "error", [gc.Rejected(409, "STALE_GENERATION"), gc.Rejected(422, "INVALID_DRAFT")]
)
def test_backend_rejections_are_not_db_error_alerts(
    registry: metrics.MetricsRegistry, error: Exception
) -> None:
    sink = FakeSink()
    gc.run(backend_kwargs={"finalize_error": error}, notifier=AlertNotifier(sink))
    assert sink.sent == []


class FailingHandler:
    def __init__(self) -> None:
        self.bound: dict[str, Any] = {}

    async def __call__(self, job: Job, ctx: Any) -> None:
        self.bound = structlog.contextvars.get_contextvars()
        await ctx.jobs.fail(
            job.id,
            ctx.worker_id,
            ctx.generation_id,
            error_code="BACKEND_UNAVAILABLE",
            retry_after_s=5,
        )


@pytest.mark.parametrize(("attempts", "alerts"), [(2, 1), (1, 0)])
def test_retry_exhausted_alert_only_on_last_attempt(
    monkeypatch: pytest.MonkeyPatch, attempts: int, alerts: int
) -> None:
    handler = FailingHandler()
    monkeypatch.setitem(dispatch.HANDLERS, "SENTENCE", handler)
    sink = FakeSink()
    jobs = JobsStub()
    worker = Worker(jobs, Settings(_env_file=None), notifier=AlertNotifier(sink))
    job = gc.make_job().model_copy(update={"attempts": attempts, "max_attempts": 2})

    asyncio.run(worker.run_job(job, "worker-1"))

    assert jobs.failed == ["BACKEND_UNAVAILABLE"]
    assert len(sink.sent) == alerts
    if alerts:
        assert sink.sent[0].kind is AlertKind.RETRY_EXHAUSTED
        assert dict(sink.sent[0].details) == {
            "job_kind": "SENTENCE",
            "code": "BACKEND_UNAVAILABLE",
            "attempts": 2,
        }
    # job 단위 로그 문맥
    assert handler.bound["trace_id"] == "trace-1"
    assert handler.bound["job_id"] == "job-1"
    assert handler.bound["job_kind"] == "SENTENCE"
    assert handler.bound["generation_id"] == "gen-1"


def test_queue_age_watch_is_off_without_threshold() -> None:
    sink = FakeSink()
    asked: list[int] = []

    async def oldest_age() -> float | None:
        asked.append(1)
        return 1_000_000.0

    worker = Worker(
        JobsStub(), Settings(_env_file=None), notifier=AlertNotifier(sink), queue_age=oldest_age
    )
    assert worker._queue_age_watch is None
    asyncio.run(worker._watch_queue_age())
    assert asked == []
    assert sink.sent == []


# ---------------------------------------------------------------------------
# 계측 실패는 job 을 바꾸지 않는다
# ---------------------------------------------------------------------------


class BrokenRegistry:
    def counter(self, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError("registry down")

    histogram = counter
    rate = counter


class BrokenNotifier:
    async def notify(self, alert: Alert) -> bool:
        raise RuntimeError("notifier down")


def test_broken_metrics_and_logs_do_not_change_job(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("log down")

    monkeypatch.setattr(metrics, "REGISTRY", BrokenRegistry())
    monkeypatch.setattr(instrument, "log_node", boom)
    result = gc.run()
    assert result.jobs.completed == ["job-1"]
    assert len(result.backend.finalized) == 1


def test_broken_notifier_does_not_change_job(registry: metrics.MetricsRegistry) -> None:
    result = gc.run(backend_kwargs={"finalize_error": Unavailable()}, notifier=BrokenNotifier())
    assert result.jobs.failures == [("BACKEND_UNAVAILABLE", 5)]


def test_broken_notifier_does_not_change_worker_fail_on_last_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(dispatch.HANDLERS, "SENTENCE", FailingHandler())
    jobs = JobsStub()
    worker = Worker(jobs, Settings(_env_file=None), notifier=BrokenNotifier())
    job = gc.make_job().model_copy(update={"attempts": 2, "max_attempts": 2})

    asyncio.run(worker.run_job(job, "worker-1"))

    assert jobs.failed == ["BACKEND_UNAVAILABLE"]


# ---------------------------------------------------------------------------
# 게이트웨이 → 일별 비용
# ---------------------------------------------------------------------------

DAY = date(2026, 9, 14)


def gateway_call(gateway: LLMGateway) -> Any:
    return asyncio.run(
        gateway.scoped_call(
            scope(),
            role="sentencing",
            messages=MESSAGES,
            schema=SCHEMA,
            timeout_s=3.0,
            max_output_tokens=MAX_OUTPUT,
        )
    )


def test_gateway_adds_settled_cost_to_daily_tracker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEOJI_EVAL", raising=False)
    tracker = DailyCostTracker(5000)
    gateway = LLMGateway(
        FakeInner(),
        FakeLedger(),
        RecordingHealth(),
        PRICES.get,
        cost_tracker=tracker,
        today=lambda: DAY,
    )
    gateway_call(gateway)
    assert tracker.totals(DAY) == {"service_micro_usd": 12, "eval_micro_usd": 0}


@pytest.mark.parametrize(("eval_env", "alerts"), [("", 1), ("1", 0)])
def test_gateway_cost_alert_counts_service_calls_only(
    monkeypatch: pytest.MonkeyPatch, eval_env: str, alerts: int
) -> None:
    monkeypatch.setenv("GEOJI_EVAL", eval_env)
    sink = FakeSink()
    tracker = DailyCostTracker(1000)
    expensive = replace(ok_result(), cost=Cost(micro_usd=1_000_000, source="table"))
    gateway = LLMGateway(
        FakeInner(expensive),
        FakeLedger(),
        RecordingHealth(),
        PRICES.get,
        cost_tracker=tracker,
        notifier=AlertNotifier(sink),
        today=lambda: DAY,
    )
    gateway_call(gateway)
    assert [a.kind for a in sink.sent] == [AlertKind.COST_PER_DAY] * alerts


def test_gateway_tracks_cost_of_billed_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEOJI_EVAL", raising=False)
    tracker = DailyCostTracker(5000)
    refusal = LLMError("REFUSAL", cost=Cost(micro_usd=7, source="table"))
    gateway = LLMGateway(
        FakeInner(refusal),
        FakeLedger(),
        RecordingHealth(),
        PRICES.get,
        cost_tracker=tracker,
        today=lambda: DAY,
    )
    with pytest.raises(LLMError) as caught:
        gateway_call(gateway)
    assert caught.value is refusal
    assert tracker.totals(DAY) == {"service_micro_usd": 7, "eval_micro_usd": 0}


def test_gateway_without_tracker_is_unchanged() -> None:
    gateway = LLMGateway(FakeInner(), FakeLedger(), RecordingHealth(), PRICES.get)
    scoped = gateway_call(gateway)
    assert scoped.result.cost.micro_usd == 12


# ---------------------------------------------------------------------------
# 단계별·역할별 기록(9/16): sentence_call · sentence_fallback · sentence_summary
# ---------------------------------------------------------------------------


def _events(logs: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    return [e for e in logs if e.get("event") == name]


def test_sentence_call_logs_every_model_call_with_role_timeout_and_latency(
    registry: metrics.MetricsRegistry,
) -> None:
    """모델 호출 1회마다 역할·강도·받은 timeout·남은 마감·지연·모델을 남긴다. 원문 없음."""
    with structlog.testing.capture_logs() as logs:
        result = gc.run()
    assert result.jobs.completed == ["job-1"]

    calls = _events(logs, "sentence_call")
    assert [c["role"] for c in calls] == ["sentencing", "writer", "writer", "evaluator"]
    assert {c["intensity"] for c in calls if c["role"] == "writer"} == {"spicy", "hell"}
    for call in calls:
        assert call["trace_id"] == "trace-1"
        assert call["job_id"] == "job-1"
        assert call["graph_name"] == "sentencing"
        assert call["node"] in {"sentencing", "writer", "evaluator"}
        assert call["ok"] is True
        assert call["fallback_reason"] is None
        assert isinstance(call["timeout_s"], float) and call["timeout_s"] > 0
        assert isinstance(call["remaining_s"], float)
        assert isinstance(call["latency_ms"], int)
        assert isinstance(call["call_index"], int)
        assert call["model_id"]
        assert "dropped_fields" not in call
    # 받은 timeout 은 산식 그대로(SPEC_CAPS: 양형 3, 서기 min(6, 남은 − 4.5), 검수 4).
    assert calls[0]["timeout_s"] == 3.0
    assert calls[3]["timeout_s"] == 4.0
    # 검수 호출은 하나(hell 별도 모델이 아니면 한 묶음)라 강도가 없다.
    assert calls[3]["intensity"] is None


def test_sentence_call_and_fallback_record_where_a_role_broke(
    registry: metrics.MetricsRegistry,
) -> None:
    """양형관 TIMEOUT → `sentence_call` ok=False·kind, `sentence_fallback` RULE.

    운영 화면이 템플릿일 때 "어느 역할이 어디서 왜" 를 이 두 이벤트로 되짚는다.
    """
    llm = gc.ScriptedLLM(errors={"sentencing": LLMError("TIMEOUT")})
    with structlog.testing.capture_logs() as logs:
        result = gc.run(llm)
    assert result.state["sentencing_source"] == "RULE"

    [broken] = [c for c in _events(logs, "sentence_call") if c["ok"] is False]
    assert broken["role"] == "sentencing"
    assert broken["fallback_reason"] == "TIMEOUT"
    assert broken["timeout_s"] == 3.0

    fallbacks = _events(logs, "sentence_fallback")
    assert [(f["node"], f["role"], f["outcome"], f["fallback_reason"]) for f in fallbacks] == [
        ("sentencing", "sentencing", "RULE", "TIMEOUT")
    ]
    assert all(f["trace_id"] == "trace-1" and "dropped_fields" not in f for f in fallbacks)


def test_evaluator_failure_logs_fallback_to_generation_failed_with_kind(
    registry: metrics.MetricsRegistry,
) -> None:
    """검수관 TIMEOUT(9/16 운영 증상) → 전 강도 TEMPLATE·`EVAL_FAILED:TIMEOUT` 기록."""
    llm = gc.ScriptedLLM(errors={"evaluator": LLMError("TIMEOUT")})
    with structlog.testing.capture_logs() as logs:
        result = gc.run(llm)
    assert result.backend.failed == ["EVAL_FAILED"]

    [broken] = [c for c in _events(logs, "sentence_call") if c["ok"] is False]
    assert (broken["role"], broken["fallback_reason"]) == ("evaluator", "TIMEOUT")
    fallbacks = _events(logs, "sentence_fallback")
    assert [(f["role"], f["outcome"], f["fallback_reason"]) for f in fallbacks] == [
        ("evaluator", "generation_failed", "EVAL_FAILED:TIMEOUT")
    ]


def test_writer_failure_logs_template_per_intensity(
    registry: metrics.MetricsRegistry,
) -> None:
    """서기 오류 → 강도마다 `sentence_fallback` TEMPLATE(이유 = 오류 kind)."""
    llm = gc.ScriptedLLM(errors={"writer": LLMError("SERVER")})
    with structlog.testing.capture_logs() as logs:
        gc.run(llm)
    fallbacks = _events(logs, "sentence_fallback")
    writer = [f for f in fallbacks if f["role"] == "writer" and f["outcome"] == "TEMPLATE"]
    assert {f["intensity"] for f in writer} == {"spicy", "hell"}
    assert {f["fallback_reason"] for f in writer} == {"SERVER"}
    assert {f["node"] for f in writer} == {"writer"}


def test_no_budget_call_is_logged_before_any_vendor_request(
    registry: metrics.MetricsRegistry,
) -> None:
    """마감이 모자라 시작하지 않은 호출도 `sentence_call`(NO_BUDGET, timeout_s null) 로 남는다."""
    with structlog.testing.capture_logs() as logs:
        result = gc.run(backend_kwargs={"remaining_s": 3.0})
    assert result.roles()["writer"] == 0
    skipped = [c for c in _events(logs, "sentence_call") if c["fallback_reason"] == "NO_BUDGET"]
    assert {c["role"] for c in skipped} == {"writer"}
    assert all(c["timeout_s"] is None and c["ok"] is False for c in skipped)


def test_sentence_summary_one_line_per_run(registry: metrics.MetricsRegistry) -> None:
    """실행 1회에 `sentence_summary` 한 줄: 결과·조서/양형/문구 출처·역할별 오류."""
    with structlog.testing.capture_logs() as logs:
        gc.run()
    [summary] = _events(logs, "sentence_summary")
    assert summary["mode"] == "INITIAL"
    assert summary["outcome"] == "SAVED"
    assert summary["ok"] is True
    assert summary["fallback_reason"] is None
    assert summary["source"] == "PREP|AI|spicy=AI,hell=AI"
    assert summary["result_count"] == 4
    assert "dropped_fields" not in summary

    llm = gc.ScriptedLLM(errors={"evaluator": LLMError("TIMEOUT")})
    with structlog.testing.capture_logs() as logs:
        gc.run(llm)
    [failed] = _events(logs, "sentence_summary")
    assert failed["outcome"] == "EVAL_FAILED"
    assert failed["ok"] is False
    assert failed["fallback_reason"] == "evaluator:-:TIMEOUT"
    assert failed["source"] == "PREP|AI|spicy=TEMPLATE,hell=TEMPLATE"


def test_sentence_node_log_carries_outcome_route(registry: metrics.MetricsRegistry) -> None:
    """노드 로그에 그 노드가 고른 길이 남는다(prep/AI/SAVED)."""
    with structlog.testing.capture_logs() as logs:
        gc.run()
    by_node = {e["node"]: e for e in logs if e.get("event") == "sentence_node"}
    assert by_node["load_valid_prep"]["outcome"] == "prep"
    assert by_node["sentencing"]["outcome"] == "AI"
    assert by_node["finalize"]["outcome"] == "SAVED"
