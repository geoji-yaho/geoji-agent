"""intake 스텁·서비스 인증(03 §3.4, VF-03).

① 올바른 토큰 → 200 PASS/FALLBACK, mode 그대로 ② 토큰 없음·틀림 → 401 ③ 설정 토큰이 비면 401
④ `/health/live` 무인증 ⑤ 잘못된 본문 422 ⑥ 100회 왕복 p95(기록, 통과 기준 아님).
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from geoji_ai.api.app import create_app
from geoji_ai.contracts.intake import IntakeResult
from geoji_ai.core.config import Settings

TOKEN = "intake-test-token-7c1e"
URL = "/internal/v1/intake"


def make_client(token: str = TOKEN) -> TestClient:
    settings = Settings(
        _env_file=None,
        SERVICE_AUTH_TOKEN=token,
        OPENAI_API_KEY="sk-test",
        XAI_API_KEY="xai-test",
    )
    return TestClient(create_app(settings))


def intake_body(mode: str = "INITIAL", **over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema_version": 1,
        "submission_id": "sub-1",
        "payload_hash": "hash-1",
        "mode": mode,
        "post_type": "spent",
        "amount_krw": 12000,
        "category": "교통/택시",
        "item": "택시",
        "reason": "늦잠 자서 택시 탐",
    }
    body.update(over)
    return body


def auth(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize("mode", ["INITIAL", "FINAL_CHECK"])
def test_올바른_토큰이면_PASS_FALLBACK_이고_mode_가_같다(mode: str):
    client = make_client()
    response = client.post(URL, json=intake_body(mode), headers=auth())

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "schema_version": 1,
        "mode": mode,
        "status": "PASS",
        "item_review": {"status": "OK", "suggested_item": None},
        "message": None,
        "category_review": {"status": "OK", "suggested_category": None, "confidence": 1.0},
        "injection_detected": False,
        "intake_source": "FALLBACK",
    }
    IntakeResult.model_validate(body)


@pytest.mark.parametrize(
    "headers",
    [{}, auth("wrong-token"), {"Authorization": TOKEN}, {"Authorization": "Basic abc"}],
    ids=["없음", "틀림", "Bearer 없음", "Basic"],
)
def test_토큰이_없거나_틀리면_401(headers: dict[str, str]):
    client = make_client()
    response = client.post(URL, json=intake_body(), headers=headers)

    assert response.status_code == 401
    assert TOKEN not in response.text


@pytest.mark.parametrize("presented", ["", "anything"])
def test_설정_토큰이_비어_있으면_401(presented: str):
    client = make_client(token="")
    response = client.post(URL, json=intake_body(), headers=auth(presented))

    assert response.status_code == 401


def test_health_live_는_무인증_200():
    client = make_client()
    response = client.get("/health/live")

    assert response.status_code == 200


@pytest.mark.parametrize(
    "body",
    [
        intake_body(extra_field=1),
        {k: v for k, v in intake_body().items() if k != "item"},
        intake_body(mode="SOMETHING"),
        intake_body(amount_krw=0),
    ],
    ids=["여분 키", "필수 누락", "모르는 mode", "금액 0"],
)
def test_잘못된_본문은_422(body: dict[str, Any]):
    client = make_client()
    response = client.post(URL, json=body, headers=auth())

    assert response.status_code == 422


def test_100회_왕복_p95_를_기록한다():
    client = make_client()
    body = intake_body()
    headers = auth()
    client.post(URL, json=body, headers=headers)  # 첫 호출 준비 비용은 빼고 잰다

    samples: list[float] = []
    for _ in range(100):
        started = time.perf_counter()
        response = client.post(URL, json=body, headers=headers)
        samples.append((time.perf_counter() - started) * 1000)
        assert response.status_code == 200

    samples.sort()
    p95 = samples[94]
    # 03 §4.1 목표 < 50ms. 통과 기준이 아니라 보고서에 적는 숫자다(`-s` 로 본다).
    print(f"intake_stub_p95_ms={p95:.2f} p50_ms={samples[49]:.2f}")
