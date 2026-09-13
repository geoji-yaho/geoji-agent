"""역할·모델 → 벤더 라우터(`adapters/llm_router.RoleRoutedLLM`). 네트워크 없음."""

from __future__ import annotations

import pytest

from geoji_ai.adapters.fake_llm import FakeLLM
from geoji_ai.adapters.llm_router import RoleRoutedLLM, build_llm, price_for
from geoji_ai.adapters.openai_compat_llm import PRICE_PER_1M_USD, OpenAICompatLLM
from geoji_ai.core.config import Settings
from geoji_ai.ports.llm import LLMError, LLMPort

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
        assert router.route(role) == ("openai", settings.MODEL_JUDGMENT)
    for role in ("banter", "writer"):
        adapter = router.adapter_for(role)
        assert isinstance(adapter, OpenAICompatLLM)
        assert (adapter.vendor, adapter.model_id) == ("xai", settings.MODEL_WRITER)
        assert str(adapter.client.base_url).rstrip("/") == settings.XAI_BASE_URL.rstrip("/")
        assert router.route(role) == ("xai", settings.MODEL_WRITER)


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


async def test_model_override_는_역할_벤더_안에서_그_모델의_어댑터로_보낸다():
    judgment, hell, writer = FakeLLM(), FakeLLM(), FakeLLM()
    router = RoleRoutedLLM(
        {"openai": judgment, "xai": writer},
        models={"openai": "gpt-5.6-luna", "xai": "grok-4.20-0309-non-reasoning"},
        model_adapters={"gpt-5.6-terra": hell},
    )

    assert router.route("evaluator", "gpt-5.6-terra") == ("openai", "gpt-5.6-terra")
    await router.structured_call(
        role="evaluator",
        messages=[],
        schema=SCHEMA,
        timeout_s=1.0,
        max_output_tokens=10,
        model_override="gpt-5.6-terra",
    )
    assert [c.role for c in hell.calls] == ["evaluator"]
    assert judgment.calls == []

    # 기본 모델과 같은 지정은 기본 어댑터
    await router.structured_call(
        role="evaluator",
        messages=[],
        schema=SCHEMA,
        timeout_s=1.0,
        max_output_tokens=10,
        model_override="gpt-5.6-luna",
    )
    assert [c.role for c in judgment.calls] == ["evaluator"]

    # 어댑터가 없는 모델은 TRANSPORT
    with pytest.raises(LLMError) as caught:
        await router.structured_call(
            role="evaluator",
            messages=[],
            schema=SCHEMA,
            timeout_s=1.0,
            max_output_tokens=10,
            model_override="gpt-unknown",
        )
    assert caught.value.kind == "TRANSPORT"


def test_MODEL_EVALUATOR_HELL_이_다르면_OpenAI_어댑터를_하나_더_만든다():
    settings = _settings(MODEL_EVALUATOR_HELL="gpt-5.6-terra")
    router = RoleRoutedLLM.from_settings(settings)
    hell = router.adapter_for("evaluator", "gpt-5.6-terra")
    assert isinstance(hell, OpenAICompatLLM)
    assert (hell.vendor, hell.model_id) == ("openai", "gpt-5.6-terra")
    assert router.adapter_for("evaluator", settings.MODEL_JUDGMENT) is router.adapter_for(
        "evaluator"
    )

    same = RoleRoutedLLM.from_settings(_settings())
    assert same.adapter_for("evaluator", "gpt-5.6-terra") is None


def test_두_키가_다_비면_build_llm_은_None():
    assert build_llm(_settings(OPENAI_API_KEY="", XAI_API_KEY="")) is None
    assert isinstance(build_llm(_settings(OPENAI_API_KEY="")), RoleRoutedLLM)


def test_단가_조회():
    assert price_for("gpt-5.6-terra") == PRICE_PER_1M_USD["gpt-5.6-terra"]
    assert price_for("fake-model") is None


def test_workers_main_은_기존_이름을_다시_내보낸다():
    from geoji_ai.workers import main

    assert main.RoleRoutedLLM is RoleRoutedLLM
    assert main.build_llm is build_llm
