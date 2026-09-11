"""정본 JSON Schema ↔ pydantic 미러 동등성(01 §4.1).

`$defs` 이름으로 짝짓고 `$ref`·`anyOf[null]` 을 풀어 `required`·enum·길이·범위를 비교한다.
`contracts/jobs.py` 는 JSON Schema 정본이 없어 대상이 아니다(02 §3.1).
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from geoji_ai.contracts.case import CaseSnapshot
from geoji_ai.contracts.evaluation import EvaluationReport
from geoji_ai.contracts.finalize import FinalizeRequest
from geoji_ai.contracts.intake import IntakeRequest, IntakeResult
from geoji_ai.contracts.sentencing import SentencingDecision
from geoji_ai.contracts.verdict_view import VerdictView
from geoji_ai.contracts.writer import WriterDraft
from tests.conftest import SCHEMA_NAMES, load_schema

# 스키마 파일 → 그 파일이 정본인 미러 모델들
MODELS_BY_SCHEMA: dict[str, tuple[type[BaseModel], ...]] = {
    "intake": (IntakeRequest, IntakeResult),
    "case-snapshot": (CaseSnapshot,),
    "sentencing": (SentencingDecision,),
    "writer-draft": (WriterDraft,),
    "evaluation": (EvaluationReport,),
    "finalize": (FinalizeRequest,),
    "verdict-view": (VerdictView,),
}

CONSTRAINT_KEYS = (
    "type",
    "const",
    "enum",
    "format",
    "maxLength",
    "minLength",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "minItems",
    "maxItems",
    "uniqueItems",
)


def pydantic_defs(models: tuple[type[BaseModel], ...]) -> dict[str, Any]:
    defs: dict[str, Any] = {}
    for model in models:
        raw = model.model_json_schema(ref_template="#/$defs/{model}")
        for name, body in raw.pop("$defs", {}).items():
            defs.setdefault(name, body)
        defs[raw.pop("title", model.__name__)] = raw
    return defs


def resolve(node: Any, defs: dict[str, Any]) -> dict[str, Any]:
    """`$ref`·단일 `allOf`·`anyOf[..., null]` 을 푼다."""
    if not isinstance(node, dict):
        return {}
    if "$ref" in node:
        merged = dict(defs[node["$ref"].rsplit("/", 1)[-1]])
        merged.update({k: v for k, v in node.items() if k != "$ref"})
        return resolve(merged, defs)
    if "allOf" in node and len(node["allOf"]) == 1:
        merged = {k: v for k, v in node.items() if k != "allOf"}
        merged.update(node["allOf"][0])
        return resolve(merged, defs)
    if "anyOf" in node:
        branches = [b for b in node["anyOf"] if b.get("type") != "null"]
        if len(branches) == 1:
            merged = {k: v for k, v in node.items() if k != "anyOf"}
            merged.update(branches[0])
            return resolve(merged, defs)
    return node


def constraints(node: Any, defs: dict[str, Any]) -> dict[str, Any]:
    resolved = resolve(node, defs)
    out: dict[str, Any] = {k: resolved[k] for k in CONSTRAINT_KEYS if k in resolved}
    if isinstance(resolved.get("items"), dict):
        out["items"] = constraints(resolved["items"], defs)
    if isinstance(resolved.get("additionalProperties"), dict):
        out["additionalProperties"] = constraints(resolved["additionalProperties"], defs)
    return out


@pytest.mark.parametrize("schema_name", SCHEMA_NAMES)
def test_defs_names_match(schema_name: str) -> None:
    document = load_schema(schema_name)
    mirror = pydantic_defs(MODELS_BY_SCHEMA[schema_name])

    assert sorted(document["$defs"]) == sorted(mirror)


@pytest.mark.parametrize("schema_name", SCHEMA_NAMES)
def test_required_and_constraints_match(schema_name: str) -> None:
    document = load_schema(schema_name)
    json_defs = document["$defs"]
    mirror_defs = pydantic_defs(MODELS_BY_SCHEMA[schema_name])

    for name, json_def in json_defs.items():
        mirror_def = mirror_defs[name]
        assert sorted(json_def.get("required", [])) == sorted(mirror_def.get("required", [])), (
            f"{schema_name}/{name}: required 가 다르다"
        )
        assert json_def.get("enum") == mirror_def.get("enum"), (
            f"{schema_name}/{name}: enum 이 다르다"
        )

        json_props = json_def.get("properties", {})
        mirror_props = mirror_def.get("properties", {})
        assert sorted(json_props) == sorted(mirror_props), (
            f"{schema_name}/{name}: 필드 목록이 다르다"
        )
        for field, json_prop in json_props.items():
            assert constraints(json_prop, json_defs) == constraints(
                mirror_props[field], mirror_defs
            ), f"{schema_name}/{name}.{field}: 제약이 다르다"


@pytest.mark.parametrize("schema_name", SCHEMA_NAMES)
def test_every_object_forbids_extra_properties(schema_name: str) -> None:
    """`vote_counts`(map) 만 예외다(어긋남 D-6)."""
    document = load_schema(schema_name)

    for name, body in document["$defs"].items():
        if "properties" not in body:
            continue
        assert body.get("additionalProperties") is False, f"{schema_name}/{name}"


@pytest.mark.parametrize("schema_name", SCHEMA_NAMES)
def test_schema_version_is_const_one(schema_name: str) -> None:
    document = load_schema(schema_name)
    tops = [
        body
        for body in document["$defs"].values()
        if "schema_version" in body.get("properties", {})
    ]

    assert tops, f"{schema_name}: schema_version 을 가진 최상위 객체가 없다"
    for body in tops:
        assert body["properties"]["schema_version"]["const"] == 1
        assert "schema_version" in body["required"]
