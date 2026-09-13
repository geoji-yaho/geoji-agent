"""운영 알림(08 §3.3).

① 알림 4종 + 비용 경고 ② 같은 종류 5분 묶음 ③ 빈 웹훅 no-op ④ 전송 실패가 예외를 올리지 않음
⑤ 웹훅 URL 이 로그·예외 어디에도 없음. 디스코드는 `httpx.MockTransport` 로만 흉내 낸다.
"""

from __future__ import annotations

import json
from datetime import date

import httpx
import pytest
import structlog

from geoji_ai.core.config import Settings
from geoji_ai.domain.budget import KRW_PER_USD
from geoji_ai.telemetry.alerts import (
    ALERT_WINDOW_SECONDS,
    Alert,
    AlertKind,
    AlertNotifier,
    DailyCostTracker,
    DiscordWebhookSink,
    QueueAgeWatch,
    finalize_db_error,
    is_eval_run,
    retry_exhausted,
    sentence_rule_violation,
)

WEBHOOK = "https://discord.com/api/webhooks/1234567890/secret-webhook-token-zz"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class RecordingSink:
    def __init__(self) -> None:
        self.sent: list[Alert] = []

    async def send(self, alert: Alert) -> None:
        self.sent.append(alert)


def test_알림_종류는_4종과_비용_경고다():
    assert {k.value for k in AlertKind} == {
        "QUEUE_OLDEST_AGE",
        "RETRY_EXHAUSTED",
        "FINALIZE_DB_ERROR",
        "SENTENCE_RULE_VIOLATION",
        "COST_PER_DAY",
    }
    assert ALERT_WINDOW_SECONDS == 300


def test_4종_생성기는_코드만_담는다():
    a = retry_exhausted(job_kind="SENTENCE", code="VENDOR_UNAVAILABLE", attempts=3)
    assert a.kind is AlertKind.RETRY_EXHAUSTED
    assert a.details == {"job_kind": "SENTENCE", "code": "VENDOR_UNAVAILABLE", "attempts": 3}
    b = finalize_db_error(job_kind="SENTENCE", code="DB_ERROR")
    assert b.kind is AlertKind.FINALIZE_DB_ERROR
    c = sentence_rule_violation(codes=["SENTENCE_OUT_OF_RANGE", "AMOUNT_MISMATCH"])
    assert c.kind is AlertKind.SENTENCE_RULE_VIOLATION
    assert c.details["codes"] == ["AMOUNT_MISMATCH", "SENTENCE_OUT_OF_RANGE"]


def test_queue_oldest_age_는_임계를_5분_연속_넘어야_알린다():
    clock = Clock()
    watch = QueueAgeWatch(threshold_seconds=60, clock=clock)
    assert watch.observe(120) is None  # 막 넘음
    clock.now += 299
    assert watch.observe(130) is None
    clock.now += 1
    alert = watch.observe(140)
    assert alert is not None and alert.kind is AlertKind.QUEUE_OLDEST_AGE
    assert alert.details == {"oldest_age_seconds": 140, "threshold_seconds": 60}
    # 임계 아래로 내려오면 다시 센다
    assert watch.observe(10) is None
    clock.now += 10
    assert watch.observe(200) is None
    assert watch.observe(None) is None  # 대기 job 없음


def test_queue_age_watch_는_임계_기본값이_없다():
    with pytest.raises(TypeError):
        QueueAgeWatch()  # type: ignore[call-arg]


def test_비용은_일_5000원에_닿으면_하루_한_번_경고하고_eval_은_따로_센다():
    threshold = int(Settings.model_fields["COST_ALERT_KRW_PER_DAY"].default)
    assert threshold == 5000
    tracker = DailyCostTracker(threshold_krw=threshold)
    day = date(2026, 9, 14)
    limit_micro = threshold * 1_000_000 // KRW_PER_USD + 1

    tracker.add(limit_micro * 2, is_eval=True, day=day)
    assert tracker.check(day) is None  # eval 실행분은 경고에 넣지 않는다
    assert tracker.totals(day) == {"service_micro_usd": 0, "eval_micro_usd": limit_micro * 2}

    tracker.add(limit_micro - 10, is_eval=False, day=day)
    assert tracker.check(day) is None
    tracker.add(10, is_eval=False, day=day)
    alert = tracker.check(day)
    assert alert is not None and alert.kind is AlertKind.COST_PER_DAY
    assert alert.details["threshold_krw"] == 5000
    assert alert.details["service_krw"] >= 5000
    assert tracker.check(day) is None  # 같은 날 두 번 보내지 않는다
    tracker.add(limit_micro, is_eval=False, day=date(2026, 9, 15))
    assert tracker.check(date(2026, 9, 15)) is not None


def test_is_eval_run_은_GEOJI_EVAL_1_만_참이다():
    assert is_eval_run({"GEOJI_EVAL": "1"}) is True
    assert is_eval_run({"GEOJI_EVAL": "0"}) is False
    assert is_eval_run({}) is False


async def test_같은_종류는_5분에_1회로_묶고_묶인_건수를_다음에_싣는다():
    clock = Clock()
    sink = RecordingSink()
    notifier = AlertNotifier(sink, clock=clock)
    alert = retry_exhausted(job_kind="SENTENCE", code="VENDOR_UNAVAILABLE", attempts=3)

    assert await notifier.notify(alert) is True
    for _ in range(5):
        assert await notifier.notify(alert) is False
    # 다른 종류는 따로 센다
    assert await notifier.notify(finalize_db_error(job_kind="SENTENCE", code="DB_ERROR")) is True
    clock.now += ALERT_WINDOW_SECONDS - 1
    assert await notifier.notify(alert) is False
    clock.now += 1
    assert await notifier.notify(alert) is True

    kinds = [a.kind for a in sink.sent]
    assert kinds == [
        AlertKind.RETRY_EXHAUSTED,
        AlertKind.FINALIZE_DB_ERROR,
        AlertKind.RETRY_EXHAUSTED,
    ]
    assert sink.sent[0].suppressed == 0
    assert sink.sent[2].suppressed == 6


async def test_디스코드_싱크는_content_를_POST_하고_원문_URL_을_남기지_않는다(
    capsys: pytest.CaptureFixture[str],
):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sink = DiscordWebhookSink(WEBHOOK, client=client)
    with structlog.testing.capture_logs() as logs:
        await sink.send(sentence_rule_violation(codes=["SENTENCE_OUT_OF_RANGE"]))
    await client.aclose()

    assert len(requests) == 1
    assert str(requests[0].url) == WEBHOOK
    body = json.loads(requests[0].content)
    assert "SENTENCE_RULE_VIOLATION" in body["content"]
    assert "SENTENCE_OUT_OF_RANGE" in body["content"]
    emitted = repr(logs) + capsys.readouterr().out
    assert "secret-webhook-token" not in emitted


async def test_빈_웹훅이면_no_op_이다():
    calls: list[httpx.Request] = []
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: calls.append(r)))
    for url in ("", "   "):
        sink = DiscordWebhookSink(url, client=client)
        assert sink.enabled is False
        await sink.send(finalize_db_error(job_kind="SENTENCE", code="DB_ERROR"))
    await client.aclose()
    assert calls == []


@pytest.mark.parametrize("mode", ["connect_error", "http_500"])
async def test_전송_실패는_예외를_올리지_않고_URL_없이_로그만_남긴다(
    mode: str, capsys: pytest.CaptureFixture[str]
):
    def handler(request: httpx.Request) -> httpx.Response:
        if mode == "connect_error":
            raise httpx.ConnectError(f"cannot reach {request.url}", request=request)
        return httpx.Response(500, text=f"boom {request.url}")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sink = DiscordWebhookSink(WEBHOOK, client=client)
    with structlog.testing.capture_logs() as logs:
        await sink.send(retry_exhausted(job_kind="PREPARE", code="TIMEOUT", attempts=2))
    await client.aclose()

    failures = [e for e in logs if e["event"] == "alert_send_failed"]
    assert len(failures) == 1
    assert failures[0]["alert_kind"] == "RETRY_EXHAUSTED"
    emitted = repr(logs) + capsys.readouterr().out
    assert "secret-webhook-token" not in emitted
    assert "discord.com" not in emitted


def test_싱크_repr_에도_URL_이_없다():
    sink = DiscordWebhookSink(WEBHOOK)
    assert "secret-webhook-token" not in repr(sink)
