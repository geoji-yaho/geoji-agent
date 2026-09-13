"""운영 알림(08 §3.3 알림, 9/8 확정: 디스코드 웹훅).

- 알림 4종: queue oldest age 목표 5분 연속 초과 / 재시도 소진 /
  finalize DB 오류 / 구조적 형량 규칙 위반 저장 시도.
  보정 가능한 오류(검수 재생성 등)는 알림 생성기를 두지 않는다 — 1건마다 보내지 않는다.
- 비용 경고: 일 `COST_ALERT_KRW_PER_DAY`(5,000원). 환율은 `domain.budget.KRW_PER_USD`.
  `GEOJI_EVAL=1` 로 돈 평가 실행분은 따로 세고 경고 판정에 넣지 않는다. 같은 날 한 번만 낸다.
- 같은 알림 종류는 `ALERT_WINDOW_SECONDS`(5분)에 1회로 묶고, 묶인 건수를 다음 알림에 싣는다.
- 웹훅 URL 은 비밀값이다. 로그·예외·repr 어디에도 넣지 않는다. 전송 실패는 로그만 남긴다
  (httpx 예외 문자열에 URL 이 들어가므로 예외 **타입 이름**만 남긴다).
- queue oldest age 목표값은 계획서에 수치가 없다. 임계는 인자로 받고 기본값을 두지 않는다.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date
from enum import StrEnum
from typing import Any, Protocol

import httpx

from geoji_ai.core.config import Settings, secret_value
from geoji_ai.core.logging import get_logger
from geoji_ai.domain.budget import KRW_PER_USD

__all__ = [
    "ALERT_WINDOW_SECONDS",
    "EVAL_ENV",
    "Alert",
    "AlertKind",
    "AlertNotifier",
    "AlertSink",
    "DailyCostTracker",
    "DiscordWebhookSink",
    "QueueAgeWatch",
    "finalize_db_error",
    "format_alert",
    "is_eval_run",
    "retry_exhausted",
    "sentence_rule_violation",
    "sink_from_settings",
]

#: 08 §3.3 "5분". 같은 종류 묶음 창이자 queue oldest age 지속 시간.
ALERT_WINDOW_SECONDS = 300

#: 평가 실행 표시 환경변수(08 §3.3).
EVAL_ENV = "GEOJI_EVAL"

_MICRO = 1_000_000


class AlertKind(StrEnum):
    QUEUE_OLDEST_AGE = "QUEUE_OLDEST_AGE"
    RETRY_EXHAUSTED = "RETRY_EXHAUSTED"
    FINALIZE_DB_ERROR = "FINALIZE_DB_ERROR"
    SENTENCE_RULE_VIOLATION = "SENTENCE_RULE_VIOLATION"
    COST_PER_DAY = "COST_PER_DAY"


@dataclass(frozen=True, slots=True)
class Alert:
    """알림 한 건. `details` 에는 코드·개수·임계만 둔다(원문·개별 ID 없음)."""

    kind: AlertKind
    details: Mapping[str, Any] = field(default_factory=dict)
    suppressed: int = 0


def retry_exhausted(*, job_kind: str, code: str, attempts: int) -> Alert:
    return Alert(
        AlertKind.RETRY_EXHAUSTED, {"job_kind": job_kind, "code": code, "attempts": attempts}
    )


def finalize_db_error(*, job_kind: str, code: str) -> Alert:
    return Alert(AlertKind.FINALIZE_DB_ERROR, {"job_kind": job_kind, "code": code})


def sentence_rule_violation(*, codes: Iterable[str]) -> Alert:
    return Alert(AlertKind.SENTENCE_RULE_VIOLATION, {"codes": sorted(set(codes))})


class QueueAgeWatch:
    """가장 오래 기다린 QUEUED job 나이가 임계를 `sustain_seconds` 연속 넘으면 알림을 낸다.

    넘은 동안 관측할 때마다 알림을 돌려준다. 묶음은 `AlertNotifier` 가 한다.
    """

    def __init__(
        self,
        threshold_seconds: float,
        *,
        sustain_seconds: float = ALERT_WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._threshold = threshold_seconds
        self._sustain = sustain_seconds
        self._clock = clock
        self._since: float | None = None

    def observe(self, oldest_age_seconds: float | None) -> Alert | None:
        if oldest_age_seconds is None or oldest_age_seconds <= self._threshold:
            self._since = None
            return None
        now = self._clock()
        if self._since is None:
            self._since = now
        if now - self._since < self._sustain:
            return None
        return Alert(
            AlertKind.QUEUE_OLDEST_AGE,
            {
                "oldest_age_seconds": round(oldest_age_seconds),
                "threshold_seconds": round(self._threshold),
            },
        )


def is_eval_run(environ: Mapping[str, str] | None = None) -> bool:
    """`GEOJI_EVAL=1` 이면 평가 실행이다. 판별은 이 함수 한 곳에서만 한다."""
    env = os.environ if environ is None else environ
    return env.get(EVAL_ENV, "").strip() == "1"


class DailyCostTracker:
    """프로세스 안 일별 비용. 서비스분이 임계에 **닿으면**(≥) 그날 한 번 경고한다."""

    def __init__(self, threshold_krw: int, *, krw_per_usd: int = KRW_PER_USD) -> None:
        self._threshold_krw = threshold_krw
        self._krw_per_usd = krw_per_usd
        self._service: dict[date, int] = {}
        self._eval: dict[date, int] = {}
        self._alerted: set[date] = set()

    def add(self, micro_usd: int, *, is_eval: bool, day: date) -> None:
        bucket = self._eval if is_eval else self._service
        bucket[day] = bucket.get(day, 0) + int(micro_usd)

    def totals(self, day: date) -> dict[str, int]:
        return {
            "service_micro_usd": self._service.get(day, 0),
            "eval_micro_usd": self._eval.get(day, 0),
        }

    def check(self, day: date) -> Alert | None:
        service = self._service.get(day, 0)
        if day in self._alerted or service * self._krw_per_usd < self._threshold_krw * _MICRO:
            return None
        self._alerted.add(day)
        return Alert(
            AlertKind.COST_PER_DAY,
            {
                "day": day.isoformat(),
                "service_krw": service * self._krw_per_usd // _MICRO,
                "eval_krw": self._eval.get(day, 0) * self._krw_per_usd // _MICRO,
                "threshold_krw": self._threshold_krw,
            },
        )


class AlertSink(Protocol):
    async def send(self, alert: Alert) -> None: ...


def format_alert(alert: Alert) -> str:
    parts = [f"[geoji-ai] {alert.kind}"]
    for key, value in alert.details.items():
        shown = ",".join(str(v) for v in value) if isinstance(value, (list, tuple)) else value
        parts.append(f"{key}={shown}")
    if alert.suppressed:
        parts.append(f"(지난 5분 묶인 {alert.suppressed}건)")
    return " ".join(parts)


class DiscordWebhookSink:
    """디스코드 웹훅 POST. URL 이 비면 아무것도 하지 않는다. 실패는 로그만."""

    __slots__ = ("_client", "_owns_client", "_url")

    def __init__(self, url: str, client: httpx.AsyncClient | None = None) -> None:
        self._url = url.strip()
        self._client = client
        self._owns_client = client is None

    def __repr__(self) -> str:
        return f"DiscordWebhookSink(enabled={self.enabled})"

    @property
    def enabled(self) -> bool:
        return bool(self._url)

    async def send(self, alert: Alert) -> None:
        if not self.enabled:
            return
        log = get_logger("geoji_ai.telemetry")
        if self._client is None:
            self._client = httpx.AsyncClient()
        try:
            response = await self._client.post(self._url, json={"content": format_alert(alert)})
        except Exception as exc:
            log.warning(
                "alert_send_failed", alert_kind=str(alert.kind), error_type=type(exc).__name__
            )
            return
        if response.status_code >= 400:
            log.warning(
                "alert_send_failed", alert_kind=str(alert.kind), status_code=response.status_code
            )
            return
        log.info("alert_sent", alert_kind=str(alert.kind), suppressed=alert.suppressed)

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None


def sink_from_settings(settings: Settings) -> DiscordWebhookSink:
    return DiscordWebhookSink(secret_value(settings, "ALERT_DISCORD_WEBHOOK_URL"))


class AlertNotifier:
    """같은 종류는 `window_seconds` 에 1회만 싱크로 보낸다. 싱크 예외도 삼킨다."""

    def __init__(
        self,
        sink: AlertSink,
        *,
        window_seconds: float = ALERT_WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._sink = sink
        self._window = window_seconds
        self._clock = clock
        self._last_sent: dict[AlertKind, float] = {}
        self._suppressed: dict[AlertKind, int] = {}

    async def notify(self, alert: Alert) -> bool:
        """보냈으면 `True`, 창 안이라 묶였으면 `False`."""
        now = self._clock()
        last = self._last_sent.get(alert.kind)
        if last is not None and now - last < self._window:
            self._suppressed[alert.kind] = self._suppressed.get(alert.kind, 0) + 1
            return False
        self._last_sent[alert.kind] = now
        outgoing = replace(alert, suppressed=self._suppressed.pop(alert.kind, 0))
        try:
            await self._sink.send(outgoing)
        except Exception as exc:
            get_logger("geoji_ai.telemetry").warning(
                "alert_send_failed", alert_kind=str(alert.kind), error_type=type(exc).__name__
            )
        return True
