"""설정 단일 지점(01 §3.7).

환경변수 이름 = 필드 이름이다. 기본값은 01 §3.7 표 그대로이고, 표에 값이 없는
항목(`—`)은 빈 문자열로 둔다. **모델 ID·키를 코드 다른 곳에 박지 않는다.**

비밀값 5종(`DATABASE_URL` `SERVICE_AUTH_TOKEN` `OPENAI_API_KEY` `XAI_API_KEY`
`ALERT_DISCORD_WEBHOOK_URL`)은 `redact()` 를 거쳐서만 밖으로 나간다.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["SECRET_FIELDS", "Settings", "get_settings", "is_production", "redact", "secret_value"]

#: 로그·응답에 원문이 나가면 안 되는 필드.
SECRET_FIELDS: frozenset[str] = frozenset(
    {
        "DATABASE_URL",
        "SERVICE_AUTH_TOKEN",
        "OPENAI_API_KEY",
        "XAI_API_KEY",
        "ALERT_DISCORD_WEBHOOK_URL",
    }
)

REDACTED = "***"


class Settings(BaseSettings):
    # `env_ignore_empty`: `.env.example` 처럼 `KEY=` 만 있는 줄은 "미설정" 으로 읽어 기본값을 쓴다.
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", case_sensitive=False, env_ignore_empty=True
    )

    # 실행 환경. production 판별은 `is_production()` 한 곳에서만 이 필드를 읽는다.
    APP_ENV: Literal["development", "production"] = "development"

    # 비밀값·외부 주소
    DATABASE_URL: SecretStr = SecretStr("")
    BACKEND_INTERNAL_URL: str = ""
    SERVICE_AUTH_TOKEN: SecretStr = SecretStr("")
    OPENAI_API_KEY: SecretStr = SecretStr("")
    XAI_API_KEY: SecretStr = SecretStr("")
    XAI_BASE_URL: str = "https://api.x.ai/v1"

    # 모델
    MODEL_JUDGMENT: str = "gpt-5.6-luna"
    MODEL_WRITER: str = "grok-4.20-0309-non-reasoning"
    MODEL_EVALUATOR_HELL: str = "gpt-5.6-luna"

    # 프롬프트·정책 버전
    PROMPT_BUNDLE_VERSION: str = "bundle-v1"
    GUARDRAIL_POLICY_VERSION: Literal["guardrail-v1", "guardrail-v2"] = "guardrail-v2"

    # 시간 예산
    INTAKE_TIMEOUT_SECONDS: int = 4
    FIRST_RESULT_TARGET_SECONDS: int = 10
    REPAIR_PATH_BUDGET_SECONDS: int = 15
    SENTENCING_NODE_TIMEOUT_SECONDS: int = 3
    WRITER_NODE_TIMEOUT_SECONDS: int = 6
    EVALUATOR_NODE_TIMEOUT_SECONDS: int = 4

    # 큐·워커 (작업 2)
    WORKER_POLL_MS: int = 250
    JOB_LEASE_SECONDS: int = 15
    HEARTBEAT_SECONDS: int = 5
    WORKER_SLOTS: dict[str, int] = Field(
        default_factory=lambda: {"SENTENCE": 2, "PREPARE": 1, "BACKGROUND": 1}
    )

    # 마감·재시도 (작업 5)
    FINALIZE_RESERVE_MS: int = 500
    INLINE_CONTEXT_MIN_REMAINING_MS: int = 8500
    IMMEDIATE_REPAIR_MAX: int = 1
    TEXT_RETRY_ROUNDS: int = 3
    TEXT_RETRY_TIMEOUT_SECONDS: int = 20

    # 기억·증거 (작업 4)
    RECALL_CANDIDATE_LIMIT: int = 20
    EVIDENCE_PACK_LIMIT: int = 12
    STYLE_EXAMPLE_LIMIT: int = 3

    # 동시성·비용
    MODEL_CONCURRENCY_LIMIT: int = 8
    ALERT_DISCORD_WEBHOOK_URL: SecretStr = SecretStr("")
    COST_ALERT_KRW_PER_DAY: int = 5000

    # 토큰 상한
    MAX_TOTAL_PROMPT_TOKENS: int = 6000
    WRITER_MAX_PROMPT_TOKENS: int = 8000
    INTAKE_MAX_OUTPUT_TOKENS: int = 300
    CONTEXT_MAX_OUTPUT_TOKENS: int = 700
    BANTER_MAX_OUTPUT_TOKENS: int = 1200
    SENTENCING_MAX_OUTPUT_TOKENS: int = 400
    WRITER_MAX_OUTPUT_TOKENS: int = 700  # 강도 1개당
    EVALUATOR_MAX_OUTPUT_TOKENS: int = 800

    # 기능 플래그. hell 을 끄는 플래그는 없다 — 정책 버전으로 통제한다.
    ROOM_COMMENT_STYLE_ENABLED: bool = False
    PUBLIC_HISTORY_CALLBACK_ENABLED: bool = False
    REFLECT_ENABLED: bool = False
    HINDSIGHT_ENABLED: bool = False


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """프로세스 전역 설정. 테스트는 `Settings(_env_file=None, ...)` 로 직접 만든다."""
    return Settings()


def is_production(settings: Settings) -> bool:
    """production 판별. 환경 판별 필드를 읽는 **유일한** 함수다."""
    return settings.APP_ENV == "production"


def secret_value(settings: Settings, name: str) -> str:
    """비밀값 원문. 어댑터가 벤더 클라이언트를 만들 때만 쓴다. 로그에 넣지 않는다."""
    value = getattr(settings, name)
    return value.get_secret_value() if isinstance(value, SecretStr) else str(value)


def redact(settings: Settings) -> dict[str, Any]:
    """로그용 dict. 비밀값은 값이 있으면 `***`, 비어 있으면 빈 문자열.

    비밀값 필드는 `SecretStr` 이라 `model_dump()` 만으로도 원문이 나오지 않지만,
    로그가 읽기 쉽게 `***`/빈 문자열로 편다.
    """
    data = settings.model_dump()
    for name in SECRET_FIELDS:
        data[name] = REDACTED if secret_value(settings, name) else ""
    return data
