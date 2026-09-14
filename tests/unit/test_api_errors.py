"""AI API 거부 응답 본문(10 §4.7, 9/14 결정).

① 401 `{"code": "UNAUTHORIZED"}` + `WWW-Authenticate` 유지 ② intake 필수값 위반 422 `{"code": …}`
③ 잘못된 JSON·스키마 위반 422 `{"code": "INVALID_REQUEST"}`, `detail` 키 없음·입력값 없음
④ trace 인데 `DATABASE_URL` 없음 503 `{"code": "DB_UNAVAILABLE"}`
⑤ 코드가 아닌 detail(없는 경로 404)은 FastAPI 기본 ⑥ `/health/ready` 503 상태 보고 본문은 그대로.

trace 404 `TRACE_NOT_FOUND` 는 DB 가 필요해 `tests/integration/test_telemetry_routes.py` 에 있다.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from geoji_ai.api.app import create_app
from geoji_ai.core.config import Settings

TOKEN = "api-errors-test-token-3f9a"
INTAKE = "/internal/v1/intake"


@pytest.fixture
def client() -> TestClient:
    settings = Settings(
        _env_file=None,
        SERVICE_AUTH_TOKEN=TOKEN,
        OPENAI_API_KEY="",
        XAI_API_KEY="",
        DATABASE_URL="",
    )
    return TestClient(create_app(settings))


def auth(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def intake_body(**over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema_version": 1,
        "submission_id": "sub-1",
        "payload_hash": "hash-1",
        "mode": "INITIAL",
        "post_type": "spent",
        "amount_krw": 12000,
        "category": "교통/택시",
        "item": "택시",
        "reason": "늦잠 자서 택시 탐",
    }
    body.update(over)
    return body


@pytest.mark.parametrize(
    "path", [INTAKE, "/internal/v1/metrics/snapshot", "/internal/v1/trials/p/trace"]
)
@pytest.mark.parametrize("headers", [{}, auth("wrong")], ids=["없음", "틀림"])
def test_401_본문은_code_UNAUTHORIZED(client: TestClient, path: str, headers: dict[str, str]):
    if path == INTAKE:
        response = client.post(path, json=intake_body(), headers=headers)
    else:
        response = client.get(path, headers=headers)

    assert response.status_code == 401
    assert response.json() == {"code": "UNAUTHORIZED"}
    assert response.headers["www-authenticate"] == "Bearer"


def test_intake_필수값_위반은_422_code(client: TestClient):
    # 길이 초과는 스키마(max_length)가 먼저 잡아 INVALID_REQUEST 다.
    # 코드 규칙만 잡는 것은 공백뿐인 item.
    response = client.post(INTAKE, json=intake_body(item="    "), headers=auth())

    assert response.status_code == 422
    assert response.json() == {"code": "ITEM_LENGTH"}


def test_잘못된_JSON_은_422_INVALID_REQUEST(client: TestClient):
    response = client.post(
        INTAKE,
        content=b'{"item": "secret-raw-input',
        headers={**auth(), "Content-Type": "application/json"},
    )

    assert response.status_code == 422
    assert response.json() == {"code": "INVALID_REQUEST"}


def test_스키마_위반은_422_INVALID_REQUEST_이고_입력값을_싣지_않는다(client: TestClient):
    response = client.post(
        INTAKE, json=intake_body(mode="SECRET-MODE-VALUE", extra="leak-me"), headers=auth()
    )

    assert response.status_code == 422
    body = response.json()
    assert body == {"code": "INVALID_REQUEST"}
    assert "detail" not in body
    assert "SECRET-MODE-VALUE" not in response.text
    assert "leak-me" not in response.text


def test_trace_인데_DB_설정이_없으면_503_DB_UNAVAILABLE(client: TestClient):
    response = client.get("/internal/v1/trials/p/trace", headers=auth())

    assert response.status_code == 503
    assert response.json() == {"code": "DB_UNAVAILABLE"}


def test_코드가_아닌_detail_은_FastAPI_기본(client: TestClient):
    response = client.get("/no-such-path")

    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


def test_health_ready_503_본문은_바뀌지_않는다(client: TestClient):
    response = client.get("/health/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert "DATABASE_URL" in body["missing"]
    assert "code" not in body
