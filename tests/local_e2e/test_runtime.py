"""고정 모델도 실제 Structured Outputs 계약을 통과해야 한다."""

import json

import jsonschema
import pytest

from geoji_ai.contracts.llm_schemas import (
    banter_schema,
    context_schema,
    evaluator_schema,
    intake_schema,
    sentencing_schema,
    writer_schema,
)
from tests.local_e2e.runtime import LocalFixtureLLM


@pytest.mark.parametrize(
    "role,schema",
    [
        ("intake", intake_schema("INITIAL")),
        ("context", context_schema()),
        ("banter", banter_schema()),
        ("sentencing", sentencing_schema(["oneDay"])),
        ("writer", writer_schema(["mild"], ["CONVERSION"], [])),
        ("evaluator", evaluator_schema(["mild"])),
    ],
)
async def test_fixture_model_is_schema_valid_and_zero_cost(tmp_path, monkeypatch, role, schema):
    control = tmp_path / "control.json"
    control.write_text(json.dumps({"tag": "GUILTY_LIGHT"}))
    monkeypatch.setenv("GEOJI_E2E_CONTROL", str(control))
    model = LocalFixtureLLM()
    result = await model.structured_call(
        role=role, messages=[], schema=schema, timeout_s=3, max_output_tokens=1200
    )
    jsonschema.validate(result.output, schema)
    assert result.vendor == "fake"
    assert result.cost.micro_usd == 0
    assert model.route(role) == ("fake", "fake-local-e2e")
