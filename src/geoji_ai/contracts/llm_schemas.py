"""모델 호출용 strict 스키마 6종(01 §3.3).

계약 미러에서 파생한다. strict 규칙은 ① 모든 키 `required` ② `additionalProperties: false`
③ `$defs` 를 인라인으로 풀고 `schema_version` 을 뺀다(서버가 채운다).
enum 주입은 `with_enums` 한 곳에서만 한다.
"""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from typing import Any

from geoji_ai.contracts.evaluation import EvaluationReport
from geoji_ai.contracts.intake import IntakeResult
from geoji_ai.contracts.sentencing import SentencingDecision
from geoji_ai.contracts.writer import BanterStrategy, TextDraft, WriterDraft

__all__ = [
    "banter_schema",
    "context_schema",
    "evaluator_schema",
    "intake_schema",
    "sentencing_schema",
    "with_enums",
    "writer_schema",
]

CONTEXT_FACT_TEXT_MAX = 500

_NOISE_KEYS = ("title", "description", "default", "$defs")


def _inline(node: Any, defs: dict[str, Any]) -> Any:
    """`$ref`·단일 `allOf` 를 풀고 표시용 키를 지운다."""
    if isinstance(node, list):
        return [_inline(item, defs) for item in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        name = node["$ref"].rsplit("/", 1)[-1]
        merged = dict(defs[name])
        merged.update({k: v for k, v in node.items() if k != "$ref"})
        return _inline(merged, defs)
    if "allOf" in node and len(node["allOf"]) == 1:
        merged = {k: v for k, v in node.items() if k != "allOf"}
        merged.update(node["allOf"][0])
        return _inline(merged, defs)
    return {k: _inline(v, defs) for k, v in node.items() if k not in _NOISE_KEYS}


def _strictify(node: Any) -> Any:
    if isinstance(node, list):
        return [_strictify(item) for item in node]
    if not isinstance(node, dict):
        return node
    out = {k: _strictify(v) for k, v in node.items()}
    if isinstance(out.get("properties"), dict):
        out["required"] = list(out["properties"])
        out["additionalProperties"] = False
    return out


def _drop(schema: dict[str, Any], *names: str) -> None:
    for name in names:
        schema.get("properties", {}).pop(name, None)
        if isinstance(schema.get("required"), list):
            schema["required"] = [k for k in schema["required"] if k != name]


def _derive(model: type) -> dict[str, Any]:
    """계약 미러 → 인라인·strict 스키마. `schema_version` 은 뺀다."""
    raw = model.model_json_schema(ref_template="#/$defs/{model}")  # type: ignore[attr-defined]
    defs = raw.get("$defs", {})
    schema = _inline(raw, defs)
    _drop(schema, "schema_version")
    return _strictify(schema)


def _set_enum(node: dict[str, Any], values: list[str]) -> None:
    if "anyOf" in node:
        for branch in node["anyOf"]:
            if branch.get("type") != "null":
                _set_enum(branch, values)
        return
    node["enum"] = list(values)


def _walk_path(schema: dict[str, Any], path: str) -> dict[str, Any]:
    node = schema
    for segment in path.split("."):
        name = segment
        is_array = name.endswith("[]")
        if is_array:
            name = name[:-2]
        node = node["properties"][name]
        if is_array:
            node = node["items"]
    return node


def _inject_by_name(node: Any, name: str, values: list[str]) -> None:
    if isinstance(node, list):
        for item in node:
            _inject_by_name(item, name, values)
        return
    if not isinstance(node, dict):
        return
    properties = node.get("properties")
    if isinstance(properties, dict) and name in properties:
        _set_enum(properties[name], values)
    for value in node.values():
        _inject_by_name(value, name, values)


def with_enums(schema: dict[str, Any], **values: Sequence[str]) -> dict[str, Any]:
    """enum 주입 한 곳.

    키는 경로(`"texts[].intensity"`) 또는 필드명(`"intensity"`)이다.
    필드명이 최상위에 있으면 거기에만 넣고, 없으면 같은 이름 필드 전부에 넣는다
    (`status` 처럼 내포 객체에 같은 이름이 있는 경우를 덮어쓰지 않는다).
    """
    out = deepcopy(schema)
    for key, enum_values in values.items():
        if "." in key or key.endswith("[]"):
            _set_enum(_walk_path(out, key), list(enum_values))
        elif key in out.get("properties", {}):
            _set_enum(out["properties"][key], list(enum_values))
        else:
            _inject_by_name(out, key, list(enum_values))
    return out


def intake_schema(mode: str) -> dict[str, Any]:
    """심문관. `intake_source` 는 서버가 채운다. `mode` 는 서버 값으로 고정한다."""
    schema = _derive(IntakeResult)
    _drop(schema, "intake_source")
    values: dict[str, Sequence[str]] = {"mode": [mode]}
    if mode == "FINAL_CHECK":
        values["status"] = ["PASS", "BLOCKED"]
    return with_enums(schema, **values)


def context_schema() -> dict[str, Any]:
    """조서(내부 계약 없음). F-라벨은 코드가 붙인다(작업 4)."""
    return {
        "type": "object",
        "properties": {
            "facts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string"},
                        "text": {"type": "string", "maxLength": CONTEXT_FACT_TEXT_MAX},
                        "source_refs": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["kind", "text", "source_refs"],
                    "additionalProperties": False,
                },
            },
            "reason_analysis": {
                "type": "object",
                "properties": {
                    "has_mitigation": {"type": "boolean"},
                    "mitigation_kind": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                    "injection_suspected": {"type": "boolean"},
                },
                "required": ["has_mitigation", "mitigation_kind", "injection_suspected"],
                "additionalProperties": False,
            },
        },
        "required": ["facts", "reason_analysis"],
        "additionalProperties": False,
    }


def banter_schema(strategies: Sequence[str] | None = None) -> dict[str, Any]:
    """드립 후보(내부 계약 없음). 강도 1개 호출이라 `intensity` 가 없다."""
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "candidates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "strategy": {
                            "type": "string",
                            "enum": [s.value for s in BanterStrategy],
                        },
                        "fits": {"type": "array", "items": {"type": "string"}},
                        "evidence_labels": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["text", "strategy", "fits", "evidence_labels"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["candidates"],
        "additionalProperties": False,
    }
    if strategies is not None:
        schema = with_enums(schema, **{"candidates[].strategy": list(strategies)})
    return schema


def sentencing_schema(allowed_sentences: Sequence[str]) -> dict[str, Any]:
    """양형관. `reason_source` 는 서버가 채운다. `sentence` 는 허용 목록으로 고정한다."""
    schema = _derive(SentencingDecision)
    _drop(schema, "reason_source")
    return with_enums(schema, sentence=list(allowed_sentences))


def writer_schema(
    intensities: Sequence[str],
    attack_angles: Sequence[str],
    candidate_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """서기. `TextDraft` 1개 + `meme_tag` + `meme_hints`. `source` 는 서버가 채운다."""
    schema = _derive(TextDraft)
    _drop(schema, "source")
    draft = _derive(WriterDraft)
    for key in ("meme_tag", "meme_hints"):
        schema["properties"][key] = draft["properties"][key]
    schema["required"] = list(schema["properties"])
    if candidate_ids is not None:
        schema["properties"]["selected_candidate_id"] = {
            "anyOf": [
                {"type": "string", "enum": list(candidate_ids)},
                {"type": "null"},
            ]
        }
    return with_enums(
        schema,
        intensity=list(intensities),
        attack_angle=list(attack_angles),
    )


def evaluator_schema(intensities: Sequence[str]) -> dict[str, Any]:
    """검수관. `policy_version` 은 서버가 채운다. 강도 집합을 주입한다."""
    schema = _derive(EvaluationReport)
    _drop(schema, "policy_version")
    return with_enums(schema, **{"texts[].intensity": list(intensities)})
