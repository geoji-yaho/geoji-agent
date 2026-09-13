"""워커의 역할 → 벤더 라우터(`workers.main.RoleRoutedLLM`). 네트워크 없음."""

from __future__ import annotations

import pytest

from geoji_ai.adapters.fake_llm import FakeLLM
from geoji_ai.adapters.openai_compat_llm import OpenAICompatLLM
from geoji_ai.core.config import Settings
from geoji_ai.ports.llm import LLMError, LLMPort
from geoji_ai.workers.main import RoleRoutedLLM, build_llm

SCHEMA = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}


def _settings(**over: str) -> Settings:
    base = {"OPENAI_API_KEY": "sk-test", "XAI_API_KEY": "xai-test"}
    base.update(over)
    return Settings(_env_file=None, **base)


def test_역할별_벤더와_모델을_고른다():
    settings = _settings()
    router = RoleRoutedLLM.from_settings(settings)
    assert isinstance(router, LLMPort)
    for role in ("intake", "context", "sentencing", "evaluator"):
        adapter = router.adapter_for(role)
        assert isinstance(adapter, OpenAICompatLLM)
        assert (adapter.vendor, adapter.model_id) == ("openai", settings.MODEL_JUDGMENT)
    for role in ("banter", "writer"):
        adapter = router.adapter_for(role)
        assert isinstance(adapter, OpenAICompatLLM)
        assert (adapter.vendor, adapter.model_id) == ("xai", settings.MODEL_WRITER)
        assert str(adapter.client.base_url).rstrip("/") == settings.XAI_BASE_URL.rstrip("/")


async def test_키가_빈_벤더_역할만_TRANSPORT():
    router = RoleRoutedLLM.from_settings(_settings(XAI_API_KEY=""))
    assert router.adapter_for("banter") is None
    with pytest.raises(LLMError) as caught:
        await router.structured_call(
            role="banter", messages=[], schema=SCHEMA, timeout_s=1.0, max_output_tokens=10
        )
    assert caught.value.kind == "TRANSPORT"
    assert isinstance(router.adapter_for("context"), OpenAICompatLLM)


async def test_호출은_역할의_어댑터로_넘긴다():
    judgment, writer = FakeLLM(), FakeLLM()
    router = RoleRoutedLLM({"openai": judgment, "xai": writer})
    await router.structured_call(
        role="context", messages=[], schema=SCHEMA, timeout_s=1.0, max_output_tokens=10
    )
    assert [c.role for c in judgment.calls] == ["context"]
    assert writer.calls == []


def test_두_키가_다_비면_build_llm_은_None():
    assert build_llm(_settings(OPENAI_API_KEY="", XAI_API_KEY="")) is None
    assert isinstance(build_llm(_settings(OPENAI_API_KEY="")), RoleRoutedLLM)
