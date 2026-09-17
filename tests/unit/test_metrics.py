"""관측 지표·로그(08 §3.3, §4.2 `test_metrics`).

① §3.3 지표 표 9행(이름 14개)이 종류·허용 라벨과 함께 등록돼 있다
② counter·histogram(p50/p95/p99)·rate 계산 ③ 라벨 값 UUID·숫자 id 거부 ④ 허용 키 밖 거부
⑤ 관측마다 `metric` 로그 이벤트 ⑥ `log_node` 가 사유·토큰 원문을 남기지 않고 hash·len 만 남긴다.
"""

from __future__ import annotations

import hashlib
import json
import uuid

import pytest
import structlog

from geoji_ai.core.logging import configure_logging
from geoji_ai.telemetry.logs import LOG_FIELDS, log_node, sanitize_fields
from geoji_ai.telemetry.metrics import (
    METRICS,
    MetricKind,
    MetricsRegistry,
    percentile,
)

#: §3.3 지표 표 그대로. 행 하나에 이름이 둘인 행이 셋이라 9행 14개다.
PLAN_TABLE: list[dict[str, tuple[str, ...]]] = [
    {"queue_wait_seconds": ("kind",)},
    {"first_result_latency_seconds": ("path",)},
    {"llm_duration_seconds": ("node", "vendor")},
    {"template_first_rate": (), "retry_recovery_rate": ()},
    {"evaluation_repair_rate": ("intensity",)},
    {"stale_finalize_total": ("kind",), "lease_expired_total": ("kind",)},
    {"evaluation_failure_total": ("code",)},
    {"case_cost_micro_usd": ("vendor",), "unknown_calls": ("vendor",)},
    {"invalidated_evidence_total": ()},
]


def test_지표_표_9행의_이름과_허용_라벨이_모두_등록돼_있다():
    assert len(PLAN_TABLE) == 9
    expected = {name: labels for row in PLAN_TABLE for name, labels in row.items()}
    assert set(METRICS) == set(expected)
    for name, labels in expected.items():
        assert METRICS[name].labels == labels, name


def test_지표_종류가_이름과_맞는다():
    for name, spec in METRICS.items():
        if name.endswith("_total") or name == "unknown_calls":
            assert spec.kind is MetricKind.counter, name
        elif name.endswith("_rate"):
            assert spec.kind is MetricKind.rate, name
        else:
            assert spec.kind is MetricKind.histogram, name


def test_percentile_은_nearest_rank_다():
    values = [float(v) for v in range(1, 101)]
    assert percentile(values, 50) == 50.0
    assert percentile(values, 95) == 95.0
    assert percentile(values, 99) == 99.0
    assert percentile([7.0], 99) == 7.0
    assert percentile([], 95) is None


def _series(snapshot: dict, name: str, **labels: str) -> dict:
    found = [s for s in snapshot["metrics"] if s["name"] == name and s["labels"] == labels]
    assert len(found) == 1, (name, labels, snapshot)
    return found[0]


def test_histogram_은_count_sum_p50_p95_p99_를_낸다():
    registry = MetricsRegistry()
    for v in range(1, 101):
        registry.histogram("first_result_latency_seconds", float(v), path="guilty")
    registry.histogram("first_result_latency_seconds", 3.0, path="regen")

    snap = registry.snapshot()
    guilty = _series(snap, "first_result_latency_seconds", path="guilty")
    assert guilty["source"] == "process"
    assert guilty["kind"] == "histogram"
    assert guilty["count"] == 100
    assert guilty["sum"] == pytest.approx(5050.0)
    assert (guilty["p50"], guilty["p95"], guilty["p99"]) == (50.0, 95.0, 99.0)
    regen = _series(snap, "first_result_latency_seconds", path="regen")
    assert regen["count"] == 1 and regen["p95"] == 3.0
    json.dumps(snap)  # JSON 으로 나간다


def test_counter_와_rate_를_계산한다():
    registry = MetricsRegistry()
    registry.counter("lease_expired_total", kind="SENTENCE")
    registry.counter("lease_expired_total", 2, kind="SENTENCE")
    registry.counter("invalidated_evidence_total")
    for hit in (True, False, False, True):
        registry.rate("template_first_rate", hit)
    registry.rate("evaluation_repair_rate", True, intensity="hell")

    snap = registry.snapshot()
    assert _series(snap, "lease_expired_total", kind="SENTENCE")["value"] == 3
    assert _series(snap, "invalidated_evidence_total")["value"] == 1
    rate = _series(snap, "template_first_rate")
    assert (rate["hits"], rate["total"], rate["value"]) == (2, 4, 0.5)
    assert _series(snap, "evaluation_repair_rate", intensity="hell")["value"] == 1.0


@pytest.mark.parametrize(
    "value",
    [
        str(uuid.uuid4()),
        str(uuid.uuid4()).upper(),
        uuid.uuid4().hex,
        "12345",
        "0",
    ],
)
def test_라벨_값이_UUID_나_숫자_id_면_거부한다(value: str):
    registry = MetricsRegistry()
    with pytest.raises(ValueError):
        registry.counter("stale_finalize_total", kind=value)
    with pytest.raises(ValueError):
        registry.histogram("llm_duration_seconds", 1.0, node="writer", vendor=value)
    assert registry.snapshot()["metrics"] == []


def test_허용_라벨_키_밖이면_거부한다():
    registry = MetricsRegistry()
    with pytest.raises(ValueError):
        registry.counter("stale_finalize_total", kind="SENTENCE", post_id="post-a")
    with pytest.raises(ValueError):
        registry.counter("invalidated_evidence_total", job_id="x")
    with pytest.raises(ValueError):
        registry.histogram("llm_duration_seconds", 1.0, node="writer")  # vendor 누락


def test_모르는_지표나_종류가_다른_기록은_거부한다():
    registry = MetricsRegistry()
    with pytest.raises(ValueError):
        registry.counter("no_such_metric")
    with pytest.raises(ValueError):
        registry.histogram("lease_expired_total", 1.0, kind="SENTENCE")


def test_관측마다_metric_로그_이벤트를_남긴다():
    registry = MetricsRegistry()
    with structlog.testing.capture_logs() as logs:
        registry.histogram("queue_wait_seconds", 1.5, kind="PREPARE")
        registry.rate("retry_recovery_rate", False)

    events = [e for e in logs if e["event"] == "metric"]
    assert len(events) == 2
    assert events[0]["metric"] == "queue_wait_seconds"
    assert events[0]["kind"] == "histogram"
    assert events[0]["value"] == 1.5
    assert events[0]["labels"] == {"kind": "PREPARE"}
    assert events[1]["metric"] == "retry_recovery_rate"
    assert events[1]["value"] == 0


def _sha12(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


REASON = "여자친구 생일이라 한우 오마카세를 질렀다 미안하다"
TOKEN = "Bearer svc-secret-token-9f2a"
API_KEY = "xai-live-abcdef0123456789"


def test_sanitize_fields_는_원문을_hash_와_len_으로_바꾸고_허용_밖은_버린다():
    out = sanitize_fields(
        {
            "job_id": "j-1",
            "node": "writer",
            "latency_ms": 812,
            "ok": True,
            "evidence_labels": ["E1", "E2"],
            "reason": REASON,
            "authorization": TOKEN,
            "api_key": API_KEY,
            "xai_api_key": API_KEY,
            "free_text_blob": "무엇이든",
        }
    )
    assert out["job_id"] == "j-1"
    assert out["latency_ms"] == 812
    assert out["evidence_labels"] == ["E1", "E2"]
    assert "reason" not in out
    assert out["reason_hash"] == _sha12(REASON)
    assert out["reason_len"] == len(REASON)
    assert out["authorization_hash"] == _sha12(TOKEN)
    assert "api_key" not in out and "xai_api_key" not in out
    assert out["api_key_len"] == len(API_KEY)
    assert "free_text_blob" not in out
    assert out["dropped_fields"] == ["free_text_blob"]


def test_로그_필드_상수가_계획서_목록을_담는다():
    for name in (
        "trace_id",
        "job_id",
        "generation_id",
        "dossier_id",
        "graph_name",
        "graph_version",
        "prompt_bundle_version",
        "guardrail_policy_version",
        "vendor",
        "model_id",
        "latency_ms",
        "ok",
        "result_count",
        "recall_memory_ids",
        "evidence_labels",
        "banter_strategy",
        "attack_angle",
        "violation_codes",
        "repair_count",
        "fallback_reason",
        "prompt_tokens",
        "completion_tokens",
        "micro_usd",
    ):
        assert name in LOG_FIELDS, name


@pytest.mark.parametrize(
    "raw_field",
    ["reason", "content", "comment", "item", "statement", "headline"],
)
def test_log_node_는_사유와_토큰_원문을_렌더된_로그에_남기지_않는다(
    raw_field: str, capsys: pytest.CaptureFixture[str]
):
    configure_logging()
    structlog.reset_defaults()
    configure_logging()
    fields = {
        "job_id": "j-1",
        "node": "writer",
        "ok": False,
        raw_field: REASON,
        "authorization": TOKEN,
        "api_key": API_KEY,
    }
    with structlog.testing.capture_logs() as logs:
        log_node("node_done", **fields)
    captured_repr = repr(logs)

    log_node("node_done", **fields)
    rendered = capsys.readouterr().out

    for emitted in (captured_repr, rendered):
        assert REASON not in emitted
        assert "svc-secret-token" not in emitted
        assert API_KEY not in emitted
        assert _sha12(REASON) in emitted
    line = json.loads(rendered.strip().splitlines()[-1])
    assert line["event"] == "node_done"
    assert line[f"{raw_field}_len"] == len(REASON)
    assert raw_field not in line


def test_configure_logging_은_stdlib_로그도_같은_JSON_으로_낸다(
    capsys: pytest.CaptureFixture[str],
):
    """9/16: 그래프의 `logging.getLogger` 결정 로그(INFO 포함)가 JSON 으로 stderr 에 남는다.

    이 연결이 없으면 "검수관 오류 → 전 강도 TEMPLATE" 같은 줄이 운영 로그에서 사라진다.
    """
    import logging

    configure_logging()
    configure_logging()  # 두 번 불러도 핸들러는 하나
    root = logging.getLogger()
    bridged = [h for h in root.handlers if getattr(h, "_geoji_structlog_bridge", False)]
    assert len(bridged) == 1

    logging.getLogger("geoji_ai.graphs.sentencing").info(
        "검수관 오류 %s → 전 강도 TEMPLATE", "TIMEOUT", extra={"audit": "X"}
    )
    err = capsys.readouterr().err
    line = json.loads(err.strip().splitlines()[-1])
    assert line["event"] == "검수관 오류 TIMEOUT → 전 강도 TEMPLATE"
    assert line["level"] == "info"
    assert line["logger"] == "geoji_ai.graphs.sentencing"
    assert line["audit"] == "X"
    assert "timestamp" in line
