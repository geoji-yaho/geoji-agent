"""지표(08 §3.3 지표).

§3.3 표의 9행(이름 14개)을 이름·종류·허용 라벨로 정의하고, 프로세스 안 레지스트리에 기록한다.

- 라벨에 개별 ID 를 넣지 않는다. 값이 UUID(하이픈 있음·32자 16진)나 숫자뿐이면 `ValueError`.
- 라벨 키는 정의와 **정확히** 같아야 한다. 밖의 키·빠진 키 모두 `ValueError`.
- 관측마다 `metric` 로그 이벤트를 남긴다. API·워커가 다른 프로세스라 레지스트리는 공유되지 않으므로
  프로세스를 넘는 집계는 이 로그로 한다. DB 로 계산되는 지표는 `adapters/postgres_telemetry.py`.
- 백분위는 nearest-rank(`ceil(p/100·n)` 번째). Postgres `percentile_disc` 와 같은 값이다.
- 히스토그램은 표본을 모두 메모리에 둔다(상한 없음 — 계획서에 수치가 없다. 보고서 미결).
"""

from __future__ import annotations

import math
import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from geoji_ai.core.logging import get_logger

__all__ = [
    "METRICS",
    "REGISTRY",
    "MetricKind",
    "MetricSpec",
    "MetricsRegistry",
    "histogram_summary",
    "percentile",
    "validate_labels",
]


class MetricKind(StrEnum):
    counter = "counter"
    histogram = "histogram"
    rate = "rate"


@dataclass(frozen=True, slots=True)
class MetricSpec:
    name: str
    kind: MetricKind
    labels: tuple[str, ...]


def _spec(name: str, kind: MetricKind, *labels: str) -> tuple[str, MetricSpec]:
    return name, MetricSpec(name=name, kind=kind, labels=tuple(labels))


#: §3.3 지표 표.
METRICS: Mapping[str, MetricSpec] = dict(
    [
        _spec("queue_wait_seconds", MetricKind.histogram, "kind"),
        _spec("first_result_latency_seconds", MetricKind.histogram, "path"),
        _spec("llm_duration_seconds", MetricKind.histogram, "node", "vendor"),
        _spec("template_first_rate", MetricKind.rate),
        _spec("retry_recovery_rate", MetricKind.rate),
        _spec("evaluation_repair_rate", MetricKind.rate, "intensity"),
        _spec("stale_finalize_total", MetricKind.counter, "kind"),
        _spec("lease_expired_total", MetricKind.counter, "kind"),
        _spec("evaluation_failure_total", MetricKind.counter, "code"),
        _spec("case_cost_micro_usd", MetricKind.histogram, "vendor"),
        _spec("unknown_calls", MetricKind.counter, "vendor"),
        _spec("invalidated_evidence_total", MetricKind.counter),
    ]
)

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_HEX32_RE = re.compile(r"^[0-9a-fA-F]{32}$")
_DIGITS_RE = re.compile(r"^\d+$")


def validate_labels(spec: MetricSpec, labels: Mapping[str, Any]) -> dict[str, str]:
    """라벨 키가 정의와 같고 값이 개별 ID 가 아닌지 본다."""
    if set(labels) != set(spec.labels):
        raise ValueError(
            f"{spec.name}: 라벨 키는 {sorted(spec.labels)} 여야 한다(받은 키 {sorted(labels)})"
        )
    out: dict[str, str] = {}
    for key in spec.labels:
        value = labels[key]
        if not isinstance(value, str) or not value:
            raise ValueError(f"{spec.name}: 라벨 {key} 는 빈 값이 아닌 문자열이어야 한다")
        if _UUID_RE.match(value) or _HEX32_RE.match(value) or _DIGITS_RE.match(value):
            # 값은 메시지에 넣지 않는다 — 그 자체가 개별 ID 다.
            raise ValueError(f"{spec.name}: 라벨 {key} 에 개별 ID 를 넣을 수 없다")
        out[key] = str(value)
    return out


def percentile(values: list[float], p: float) -> float | None:
    """nearest-rank 백분위. 표본이 없으면 `None`."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(p * len(ordered) / 100 - 1e-9))
    return ordered[min(rank, len(ordered)) - 1]


def histogram_summary(values: list[float]) -> dict[str, Any]:
    return {
        "count": len(values),
        "sum": float(sum(values)),
        "p50": percentile(values, 50),
        "p95": percentile(values, 95),
        "p99": percentile(values, 99),
    }


_LabelKey = tuple[tuple[str, str], ...]


class MetricsRegistry:
    """프로세스 안 지표 저장소. 스레드 안전."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, _LabelKey], float] = {}
        self._histograms: dict[tuple[str, _LabelKey], list[float]] = {}
        self._rates: dict[tuple[str, _LabelKey], list[int]] = {}

    def _prepare(
        self, name: str, kind: MetricKind, labels: Mapping[str, Any]
    ) -> tuple[str, _LabelKey, dict[str, str]]:
        spec = METRICS.get(name)
        if spec is None:
            raise ValueError(f"정의되지 않은 지표: {name}")
        if spec.kind is not kind:
            raise ValueError(f"{name} 은 {spec.kind} 다({kind} 로 기록할 수 없다)")
        clean = validate_labels(spec, labels)
        return name, tuple(sorted(clean.items())), clean

    @staticmethod
    def _log(name: str, kind: MetricKind, value: float, labels: dict[str, str]) -> None:
        get_logger("geoji_ai.telemetry").info(
            "metric", metric=name, kind=str(kind), value=value, labels=labels
        )

    def counter(self, name: str, value: float = 1, **labels: str) -> None:
        metric, key, clean = self._prepare(name, MetricKind.counter, labels)
        if value < 0:
            raise ValueError(f"{name}: counter 는 줄지 않는다")
        with self._lock:
            self._counters[(metric, key)] = self._counters.get((metric, key), 0) + value
        self._log(name, MetricKind.counter, value, clean)

    def histogram(self, name: str, value: float, **labels: str) -> None:
        metric, key, clean = self._prepare(name, MetricKind.histogram, labels)
        with self._lock:
            self._histograms.setdefault((metric, key), []).append(float(value))
        self._log(name, MetricKind.histogram, float(value), clean)

    def rate(self, name: str, hit: bool, **labels: str) -> None:
        metric, key, clean = self._prepare(name, MetricKind.rate, labels)
        with self._lock:
            pair = self._rates.setdefault((metric, key), [0, 0])
            pair[0] += 1 if hit else 0
            pair[1] += 1
        self._log(name, MetricKind.rate, 1 if hit else 0, clean)

    def snapshot(self) -> dict[str, list[dict[str, Any]]]:
        """`{"metrics": [series...]}`. 각 series 에 `source: "process"`."""
        with self._lock:
            counters = dict(self._counters)
            histograms = {k: list(v) for k, v in self._histograms.items()}
            rates = {k: tuple(v) for k, v in self._rates.items()}
        series: list[dict[str, Any]] = []
        for (name, key), value in counters.items():
            series.append(_series(name, MetricKind.counter, key) | {"value": value})
        for (name, key), values in histograms.items():
            series.append(_series(name, MetricKind.histogram, key) | histogram_summary(values))
        for (name, key), (hits, total) in rates.items():
            series.append(
                _series(name, MetricKind.rate, key)
                | {"hits": hits, "total": total, "value": hits / total if total else None}
            )
        series.sort(key=lambda s: (s["name"], sorted(s["labels"].items())))
        return {"metrics": series}


def _series(name: str, kind: MetricKind, key: _LabelKey) -> dict[str, Any]:
    return {"name": name, "kind": str(kind), "source": "process", "labels": dict(key)}


#: 프로세스 기본 레지스트리. 앱은 `app.state.metrics_registry` 가 있으면 그것을 쓴다.
REGISTRY = MetricsRegistry()
