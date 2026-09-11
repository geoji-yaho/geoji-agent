"""기동 검사 3종과 헬스 엔드포인트(01 §4.1).

production + 정책 누락 → 기동 실패 / `grok-4.6` 서기 → 기동 실패 /
키 누락 → `/health/ready` 503.
"""

from __future__ import annotations

import pathlib
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from geoji_ai.api.app import create_app
from geoji_ai.core.config import SECRET_FIELDS, Settings, is_production, redact
from geoji_ai.core.startup import (
    REASONING_WRITER_MODELS,
    StartupError,
    missing_keys,
    validate,
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """이 기계의 환경변수·`.env` 가 테스트에 새어 들지 않게 한다."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)
    yield


def make_settings(**over: Any) -> Settings:
    base: dict[str, Any] = {
        "OPENAI_API_KEY": "sk-test-openai",
        "XAI_API_KEY": "xai-test",
    }
    base.update(over)
    return Settings(_env_file=None, **base)


# --- 기본값 ------------------------------------------------------------------


def test_기본값이_계획서_표와_같다():
    settings = Settings(_env_file=None)
    assert settings.APP_ENV == "development"
    assert settings.XAI_BASE_URL == "https://api.x.ai/v1"
    assert settings.MODEL_JUDGMENT == "gpt-5.6-luna"
    assert settings.MODEL_WRITER == "grok-4.20-0309-non-reasoning"
    assert settings.MODEL_EVALUATOR_HELL == "gpt-5.6-luna"
    assert settings.PROMPT_BUNDLE_VERSION == "bundle-v1"
    assert settings.GUARDRAIL_POLICY_VERSION == "guardrail-v2"
    assert settings.INTAKE_TIMEOUT_SECONDS == 4
    assert settings.MODEL_CONCURRENCY_LIMIT == 8
    assert settings.COST_ALERT_KRW_PER_DAY == 5000
    assert settings.WORKER_SLOTS == {"SENTENCE": 2, "PREPARE": 1, "BACKGROUND": 1}
    assert settings.ROOM_COMMENT_STYLE_ENABLED is False
    # 비밀값은 기본값이 없다.
    assert settings.DATABASE_URL.get_secret_value() == ""
    assert settings.SERVICE_AUTH_TOKEN.get_secret_value() == ""


def test_production_판별은_한_함수만_한다():
    assert is_production(make_settings()) is False
    assert is_production(make_settings(APP_ENV="production")) is True


def test_비밀값은_가려진다():
    settings = make_settings(
        DATABASE_URL="postgresql://u:pw@host/db",
        SERVICE_AUTH_TOKEN="token",
        ALERT_DISCORD_WEBHOOK_URL="https://discord.example/hook",
    )
    masked = redact(settings)
    for name in SECRET_FIELDS:
        assert masked[name] in ("***", "")
    assert "pw" not in str(masked)
    assert "sk-test-openai" not in str(masked)
    # SecretStr 이라 redact 를 거치지 않은 repr·덤프에도 원문이 없다.
    assert "sk-test-openai" not in repr(settings)
    assert "sk-test-openai" not in str(settings.model_dump())
    # 비밀이 아닌 값은 그대로 보인다.
    assert masked["MODEL_WRITER"] == settings.MODEL_WRITER


# --- ① production + 정책 누락 -----------------------------------------------


@pytest.mark.parametrize("policy", ["", "guardrail-v0", "none"])
def test_정책_값이_목록_밖이면_설정_자체가_거부된다(policy: str):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, APP_ENV="production", GUARDRAIL_POLICY_VERSION=policy)


def test_production_에_정책이_없으면_기동_실패():
    # 환경이 GUARDRAIL_POLICY_VERSION 을 명시하지 않고 기본값에 기대면 "정책 누락" 이다.
    settings = make_settings(APP_ENV="production")
    assert settings.GUARDRAIL_POLICY_VERSION == "guardrail-v2"  # 기본값은 있지만
    with pytest.raises(StartupError):
        validate(settings)


def test_production_에_빈_정책은_환경에서도_거부된다(monkeypatch: pytest.MonkeyPatch):
    # `GUARDRAIL_POLICY_VERSION=` 처럼 비워 두면 env_ignore_empty 로 미설정 취급 → 기동 실패.
    monkeypatch.setenv("GUARDRAIL_POLICY_VERSION", "")
    settings = make_settings(APP_ENV="production")
    with pytest.raises(StartupError):
        validate(settings)


def test_development_는_정책이_없어도_기동한다():
    validate(make_settings())


@pytest.mark.parametrize("policy", ["guardrail-v1", "guardrail-v2"])
def test_production_에_정책이_있으면_기동한다(policy: str):
    validate(make_settings(APP_ENV="production", GUARDRAIL_POLICY_VERSION=policy))


# --- .env.example ------------------------------------------------------------


def test_env_example_을_그대로_복사해도_설정이_뜬다(tmp_path):
    """`.env.example` 은 키 이름만 있고 값이 없다. 빈 값은 미설정으로 읽혀 기본값이 쓰인다."""
    example = pathlib.Path(__file__).resolve().parents[2] / ".env.example"
    text = example.read_text(encoding="utf-8")
    env = tmp_path / ".env"
    env.write_text(text, encoding="utf-8")
    settings = Settings(_env_file=env)
    assert settings.GUARDRAIL_POLICY_VERSION == "guardrail-v2"
    assert settings.WORKER_SLOTS == {"SENTENCE": 2, "PREPARE": 1, "BACKGROUND": 1}
    assert missing_keys(settings) == ["OPENAI_API_KEY", "XAI_API_KEY"]
    # 키 이름이 설정 필드와 1:1 이다.
    keys = {ln.split("=", 1)[0] for ln in text.splitlines() if ln and not ln.startswith("#")}
    assert keys == set(Settings.model_fields)


# --- ② 서기·드립 추론 모델 ---------------------------------------------------


@pytest.mark.parametrize("model", sorted(REASONING_WRITER_MODELS))
@pytest.mark.parametrize("env", ["development", "production"])
def test_서기에_추론_모델이면_기동_실패(model: str, env: str):
    # 환경과 무관하다.
    with pytest.raises(StartupError):
        validate(make_settings(APP_ENV=env, MODEL_WRITER=model))


def test_금지_목록은_grok_4_6_4_5_4_3():
    assert REASONING_WRITER_MODELS == frozenset({"grok-4.6", "grok-4.5", "grok-4.3"})


def test_기본_서기_모델은_통과():
    validate(make_settings())


# --- ③ 키 누락 → /health/ready 503 -------------------------------------------


def test_missing_keys_는_빈_키_이름을_준다():
    assert missing_keys(make_settings()) == []
    assert missing_keys(make_settings(OPENAI_API_KEY="")) == ["OPENAI_API_KEY"]
    assert missing_keys(make_settings(OPENAI_API_KEY="", XAI_API_KEY="   ")) == [
        "OPENAI_API_KEY",
        "XAI_API_KEY",
    ]


def test_health_live_는_200():
    with TestClient(create_app(make_settings())) as client:
        response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_키가_다_있으면_ready_200():
    with TestClient(create_app(make_settings())) as client:
        response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["missing"] == []


def test_키가_없으면_ready_503():
    settings = make_settings(OPENAI_API_KEY="", XAI_API_KEY="")
    with TestClient(create_app(settings)) as client:
        live = client.get("/health/live")
        ready = client.get("/health/ready")
    # 살아는 있다. 일을 못 받을 뿐이다.
    assert live.status_code == 200
    assert ready.status_code == 503
    body = ready.json()
    assert body["status"] == "not_ready"
    assert body["missing"] == ["OPENAI_API_KEY", "XAI_API_KEY"]


def test_앱_생성도_기동_검사를_돈다():
    with pytest.raises(StartupError):
        create_app(make_settings(MODEL_WRITER="grok-4.6"))
