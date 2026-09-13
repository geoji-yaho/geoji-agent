"""역할·모델 → 벤더 어댑터 라우터(03 §3.5, 05 §3.3 hell 분기).

`workers/main.py` 에서 옮겼다. api 도 같은 조립을 쓸 수 있게 하고, api 가 workers 를 import 하지
않게 하려는 것이다(code-layout 의존 방향).

- 역할 → 벤더는 `ROLE_VENDOR`. 판단 역할은 OpenAI(`MODEL_JUDGMENT`), 드립·서기는 xAI(`MODEL_WRITER`)
- 호출에 `model_override` 가 오면 **역할의 벤더 안에서** 그 모델의 어댑터로 보낸다. 벤더 기본 모델과
  같으면 기본 어댑터다. `MODEL_EVALUATOR_HELL` 이 `MODEL_JUDGMENT` 와 다르면 `from_settings` 가
  OpenAI 어댑터를 하나 더 만든다
- 키가 빈 벤더(와 어댑터가 없는 모델)는 그 호출에서만 `LLMError("TRANSPORT")`
- `model_override` 는 어댑터 선택에만 쓴다. 어댑터는 `LLMPort` 시그니처 그대로 받는다
- 단가 조회 `price_for` 를 여기서 노출한다. 게이트웨이(application)는 어댑터를 import 하지 않으므로
  이 함수를 주입받는다
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from geoji_ai.adapters.openai_compat_llm import PRICE_PER_1M_USD, OpenAICompatLLM
from geoji_ai.core.config import Settings, secret_value
from geoji_ai.ports.llm import LLMError, LLMPort, LLMResult, LLMRole

__all__ = ["ROLE_VENDOR", "RoleRoutedLLM", "build_llm", "price_for"]

Vendor = Literal["openai", "xai"]

#: 역할 → 벤더. 판단 역할은 OpenAI, 드립·서기는 xAI.
ROLE_VENDOR: dict[str, Vendor] = {
    "intake": "openai",
    "context": "openai",
    "sentencing": "openai",
    "evaluator": "openai",
    "banter": "xai",
    "writer": "xai",
}


def price_for(model_id: str) -> tuple[float, float] | None:
    """모델의 (입력, 출력) USD/1M token. 단가표(03 §3.5)에 없으면 None."""
    return PRICE_PER_1M_USD.get(model_id)


class RoleRoutedLLM:
    """`LLMPort` 구현. 역할(과 모델 지정)에 맞는 벤더 어댑터로 넘긴다.

    - `adapters`: 벤더 → 기본 어댑터(키가 없으면 None)
    - `models`: 벤더 → 기본 모델 id. 주지 않으면 어댑터의 `model_id` 속성
    - `model_adapters`: 기본 모델이 아닌 모델 id → 어댑터(`MODEL_EVALUATOR_HELL` 등)
    """

    def __init__(
        self,
        adapters: Mapping[str, LLMPort | None],
        *,
        models: Mapping[str, str] | None = None,
        model_adapters: Mapping[str, LLMPort | None] | None = None,
    ) -> None:
        self._adapters = dict(adapters)
        self._models = dict(models or {})
        self._model_adapters = dict(model_adapters or {})

    @classmethod
    def from_settings(cls, settings: Settings) -> RoleRoutedLLM:
        openai_key = secret_value(settings, "OPENAI_API_KEY")
        xai_key = secret_value(settings, "XAI_API_KEY")
        adapters: dict[str, LLMPort | None] = {
            "openai": OpenAICompatLLM(
                "openai", api_key=openai_key, model_id=settings.MODEL_JUDGMENT
            )
            if openai_key
            else None,
            "xai": OpenAICompatLLM(
                "xai",
                api_key=xai_key,
                base_url=settings.XAI_BASE_URL,
                model_id=settings.MODEL_WRITER,
            )
            if xai_key
            else None,
        }
        model_adapters: dict[str, LLMPort | None] = {}
        hell = settings.MODEL_EVALUATOR_HELL
        if hell != settings.MODEL_JUDGMENT:
            # 검수 역할의 벤더(OpenAI) 안에서 모델만 바꾼다.
            model_adapters[hell] = (
                OpenAICompatLLM("openai", api_key=openai_key, model_id=hell) if openai_key else None
            )
        return cls(
            adapters,
            models={"openai": settings.MODEL_JUDGMENT, "xai": settings.MODEL_WRITER},
            model_adapters=model_adapters,
        )

    def _default_model(self, vendor: str) -> str | None:
        model = self._models.get(vendor)
        if model is None:
            model = getattr(self._adapters.get(vendor), "model_id", None)
        return model

    def route(self, role: LLMRole, model_override: str | None = None) -> tuple[str, str]:
        """(벤더, 모델 id). 모델 id 를 알 수 없으면 `ValueError`."""
        vendor = ROLE_VENDOR[role]
        model = model_override or self._default_model(vendor)
        if not model:
            raise ValueError(f"{vendor} 의 기본 모델 id 를 모른다(models 인자 필요)")
        return vendor, model

    def adapter_for(self, role: str, model_override: str | None = None) -> LLMPort | None:
        """역할(과 모델 지정)의 어댑터. 키가 빈 벤더나 어댑터가 없는 모델이면 None."""
        vendor = ROLE_VENDOR[role]
        if model_override is None or model_override == self._default_model(vendor):
            return self._adapters.get(vendor)
        return self._model_adapters.get(model_override)

    async def structured_call(
        self,
        *,
        role: LLMRole,
        messages: list[dict],
        schema: dict,
        timeout_s: float,
        max_output_tokens: int,
        model_override: str | None = None,
    ) -> LLMResult:
        adapter = self.adapter_for(role, model_override)
        if adapter is None:
            target = ROLE_VENDOR[role] if model_override is None else model_override
            raise LLMError(
                "TRANSPORT", message=f"{target} 어댑터가 없다(API 키 없음 또는 모르는 모델)"
            )
        return await adapter.structured_call(
            role=role,
            messages=messages,
            schema=schema,
            timeout_s=timeout_s,
            max_output_tokens=max_output_tokens,
        )


def build_llm(settings: Settings) -> RoleRoutedLLM | None:
    """두 벤더 키가 다 비었을 때만 None. 하나라도 있으면 `RoleRoutedLLM`."""
    if not secret_value(settings, "OPENAI_API_KEY") and not secret_value(settings, "XAI_API_KEY"):
        return None
    return RoleRoutedLLM.from_settings(settings)
