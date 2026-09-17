"""현재 서기 프롬프트의 세 강도 점검은 기본적으로 비용·네트워크 없이 실행한다."""

import json
from copy import deepcopy

import pytest
from scripts import probe_verdict_cards as probe

from geoji_ai.adapters.fake_llm import FakeLLM, FakeScenario
from geoji_ai.contracts.writer import BanterStrategy
from geoji_ai.core.config import Settings
from geoji_ai.domain.attack_angles import pick
from geoji_ai.domain.intensity import Intensity
from geoji_ai.graphs.sentencing import build_writer_request, minimal_dossier
from geoji_ai.graphs.states import Candidate
from geoji_ai.ports.llm import Cost, LLMError, Usage
from geoji_ai.prompts import WRITER_VERSION, build_writer_system


def config(**kwargs):
    return Settings(_env_file=None, OPENAI_API_KEY="", XAI_API_KEY="", **kwargs)


async def test_default_dry_run_has_current_requests_without_building_client(monkeypatch):
    monkeypatch.setattr(probe, "build_llm", lambda _: pytest.fail("network client created"))
    result = await probe.run_probe(config())
    assert result["mode"] == "dry-run"
    assert result["writer_version"] == WRITER_VERSION
    assert result["provider_calls"] == 0
    assert result["scope"] == "writer_only"
    for item in result["results"]:
        request = item["request"]
        assert request["messages"][0]["content"] == build_writer_system(item["intensity"])
        user = json.loads(request["messages"][1]["content"])
        assert user["case"]["amount_krw"] == 12000
        assert [fact["id"] for fact in user["dossier"]] == ["F0"]
        assert user["banter_candidates"] == []
        assert user["jury"]["result"] == "guilty"
        assert request["schema"]["properties"]["statement"]["maxItems"] == 1


async def test_fake_is_explicit_and_preserves_metadata():
    result = await probe.run_probe(config(), mode="fake")
    assert result["success"]
    assert result["provider_calls"] == 0
    assert [r["intensity"] for r in result["results"]] == ["mild", "spicy", "hell"]
    assert all(r["validation"] == "passed" for r in result["results"])
    assert all(r["output"]["meme_tag"] for r in result["results"])
    assert all("meme_hints" in r["output"] for r in result["results"])
    assert result["validation_scope"] == "card_schema_only"
    assert result["results"][1]["evidence_validation"] == "failed"
    assert result["results"][1]["unknown_evidence_labels"] == ["F1"]


async def test_execute_requires_only_xai_and_uses_production_limits(monkeypatch):
    fake = FakeLLM()
    monkeypatch.setattr(probe, "build_llm", lambda _: fake)
    settings = Settings(
        _env_file=None,
        OPENAI_API_KEY="",
        XAI_API_KEY="test-key",
        MODEL_WRITER="configured-model",
        WRITER_MAX_OUTPUT_TOKENS=711,
        WRITER_NODE_TIMEOUT_SECONDS=1.2,
    )
    result = await probe.run_probe(settings, mode="execute")
    assert result["success"]
    assert result["model"] == "configured-model"
    assert len(fake.calls) == result["provider_calls"] == 3
    assert all(c.max_output_tokens == 711 and c.timeout_s == 1.2 for c in fake.calls)


async def test_missing_key_never_calls_provider(monkeypatch):
    monkeypatch.setattr(probe, "build_llm", lambda _: pytest.fail("must fail before client"))
    result = await probe.run_probe(config(), mode="execute")
    assert not result["success"]
    assert result["error"] == "MISSING_XAI_API_KEY"
    assert result["provider_calls"] == 0


@pytest.mark.parametrize("scenario", [FakeScenario.TRUNCATED, FakeScenario.REFUSAL])
async def test_failure_has_no_repair_or_template(monkeypatch, scenario):
    fake = FakeLLM(scenario=scenario)
    monkeypatch.setattr(probe, "build_llm", lambda _: fake)
    settings = Settings(_env_file=None, XAI_API_KEY="test-key", OPENAI_API_KEY="")
    result = await probe.run_probe(settings, mode="execute")
    assert not result["success"]
    assert len(fake.calls) == 3
    assert all(r["validation"] == "failed" and r["output"] is None for r in result["results"])


async def test_error_keeps_usage_but_not_provider_message(monkeypatch):
    class Broken:
        async def structured_call(self, **kwargs):
            raise LLMError(
                "SCHEMA",
                message="secret-provider-body",
                usage=Usage(10, 700),
                cost=Cost(micro_usd=20, source="usage"),
            )

    monkeypatch.setattr(probe, "build_llm", lambda _: Broken())
    settings = Settings(_env_file=None, XAI_API_KEY="secret-key", OPENAI_API_KEY="")
    result = await probe.run_probe(settings, mode="execute")
    assert not result["success"]
    assert "secret" not in json.dumps(result)
    assert result["results"][0]["usage"]["completion_tokens"] == 700
    assert result["results"][0]["cost"]["micro_usd"] == 20


def test_cli_default_and_missing_key_exit_code(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(probe, "Settings", config)
    out = tmp_path / "report.json"
    assert probe.main(["--out", str(out)]) == 0
    assert json.loads(out.read_text())["mode"] == "dry-run"
    assert "dry-run" in capsys.readouterr().out
    assert probe.main(["--execute"]) == 1


@pytest.mark.parametrize("field", ["headline", "statement", "meme_tag", "meme_hints"])
async def test_invalid_output_is_returned_unchanged_and_never_repaired(monkeypatch, field):
    demonstration = await probe.run_probe(config(), mode="fake")
    raw = deepcopy(demonstration["results"][0]["output"])
    changes = {
        "headline": "가" * 21,
        "statement": [{"text": " ", "kind": "opinion", "evidence_labels": []}],
        "meme_tag": "UNKNOWN",
        "meme_hints": {"emotion": "UNKNOWN", "keywords": []},
    }
    raw[field] = changes[field]
    fake = FakeLLM(outputs={"writer": raw})
    monkeypatch.setattr(probe, "build_llm", lambda _: fake)
    settings = Settings(_env_file=None, XAI_API_KEY="key", OPENAI_API_KEY="")
    result = await probe.run_probe(settings, mode="execute")
    assert not result["success"]
    assert len(fake.calls) == 3
    assert all(row["output"] == raw for row in result["results"])
    assert all(row["error"] == "INVALID_OUTPUT" for row in result["results"])


def test_shared_request_filters_candidates_and_keeps_repair_data():
    snapshot, decision = probe.example_case()
    accepted = Candidate("yes", "허용", BanterStrategy.PREMISE_REJECTION, ("guilty",), ("F0",))
    rejected = Candidate("no", "제외", BanterStrategy.PREMISE_REJECTION, ("notGuilty",), ())
    avoid = {"violation_codes": ["SCHEMA_INVALID"]}
    messages, schema = build_writer_request(
        snapshot,
        minimal_dossier(snapshot),
        decision,
        Intensity.hell,
        offset=1,
        banter={Intensity.hell: [accepted, rejected]},
        avoid=avoid,
    )
    user = json.loads(messages[1]["content"])
    assert user["attack_angle"]["code"] == pick(snapshot.post_id, 1).value
    assert user["avoid"] == avoid
    assert user["banter_candidates"] == [
        {
            "id": "yes",
            "text": "허용",
            "strategy": "PREMISE_REJECTION",
            "evidence_labels": ["F0"],
        }
    ]
    assert user["sentencing"] == decision.model_dump(mode="json")
    assert schema["properties"]["intensity"]["enum"] == ["hell"]
    assert schema["properties"]["selected_candidate_id"]["anyOf"][0]["enum"] == ["yes"]
