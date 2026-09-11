"""기동 검사 3종(01 §3.7).

① production 에서 정책이 없으면 기동 실패. 정책 버전이 판결문 프롬프트·검수관·
   finalize·fixture 에 동시에 걸리므로, 값이 없으면 무엇으로 검수했는지 모르는
   판결문이 나간다. "없다" = 환경이 `GUARDRAIL_POLICY_VERSION` 을 명시하지 않아
   코드 기본값에 기대는 것. 값 자체가 목록 밖이면 `Settings` 생성 단계에서 이미 막힌다.
② 서기·드립에 추론 모델을 설정하면 기동 실패. 환경과 무관하다(01 §3.7 비고).
③ 키 누락은 기동을 막지 않는다. `missing_keys()` 를 `/health/ready` 가 읽어
   503 을 낸다.
"""

from __future__ import annotations

from geoji_ai.core.config import Settings, is_production, secret_value

__all__ = [
    "ALLOWED_POLICY_VERSIONS",
    "REASONING_WRITER_MODELS",
    "REQUIRED_API_KEYS",
    "StartupError",
    "missing_keys",
    "validate",
]

#: 서기·드립에 쓰면 안 되는 추론 모델. 드립은 `MODEL_WRITER` 를 같이 쓴다.
REASONING_WRITER_MODELS: frozenset[str] = frozenset({"grok-4.6", "grok-4.5", "grok-4.3"})

#: 가드레일 정책 버전 허용 목록.
ALLOWED_POLICY_VERSIONS: tuple[str, ...] = ("guardrail-v1", "guardrail-v2")

#: 없으면 모델을 부를 수 없는 키. readiness 가 본다.
REQUIRED_API_KEYS: tuple[str, ...] = ("OPENAI_API_KEY", "XAI_API_KEY")


class StartupError(RuntimeError):
    """기동을 막는 설정 오류."""


def validate(settings: Settings) -> None:
    """기동 검사. 실패하면 `StartupError`."""
    writer_model = settings.MODEL_WRITER
    if writer_model in REASONING_WRITER_MODELS:
        raise StartupError(
            f"서기·드립에 추론 모델을 쓸 수 없다: MODEL_WRITER={writer_model!r}. "
            f"금지 목록 {sorted(REASONING_WRITER_MODELS)}"
        )

    if is_production(settings):
        policy = settings.GUARDRAIL_POLICY_VERSION
        explicit = "GUARDRAIL_POLICY_VERSION" in settings.model_fields_set
        if not explicit or policy not in ALLOWED_POLICY_VERSIONS:
            raise StartupError(
                "production 인데 가드레일 정책이 없다: GUARDRAIL_POLICY_VERSION 을 환경에서 "
                f"명시해야 한다(현재 {policy!r}, 명시={explicit}). "
                f"허용 목록 {list(ALLOWED_POLICY_VERSIONS)}"
            )


def missing_keys(settings: Settings) -> list[str]:
    """비어 있는 벤더 키 이름. 빈 리스트면 준비됨."""
    return [name for name in REQUIRED_API_KEYS if not secret_value(settings, name).strip()]
