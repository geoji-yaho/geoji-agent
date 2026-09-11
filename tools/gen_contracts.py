"""계약 정본 생성기 — pydantic 미러(`src/geoji_ai/contracts/`) → `contracts/*.schema.json`.

정본 JSON 파일은 손으로 고치지 않는다. 미러를 고치고 이 스크립트를 돌린다.

    uv run python tools/gen_contracts.py

`tests/contracts/test_schema_regen.py` 가 "재생성 = 커밋본과 동일" 을 단언한다.
미러가 표현하지 못해 손으로 얹는 조각은 `patch()` 한 곳에만 둔다:
- `JurySnapshot.vote_counts.propertyNames` (map 의 키 타입)
- `IntakeResult` 의 `if/then` (FINAL_CHECK → NEEDS_CLARIFICATION 금지)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from geoji_ai.contracts.case import CaseSnapshot
from geoji_ai.contracts.evaluation import EvaluationReport
from geoji_ai.contracts.finalize import FinalizeRequest
from geoji_ai.contracts.intake import IntakeRequest, IntakeResult
from geoji_ai.contracts.sentencing import SentencingDecision
from geoji_ai.contracts.verdict_view import VerdictView
from geoji_ai.contracts.writer import WriterDraft

CONTRACTS = Path(__file__).resolve().parents[1] / "contracts"
DRAFT = "https://json-schema.org/draft/2020-12/schema"


def strip_titles(node: Any) -> Any:
    if isinstance(node, list):
        return [strip_titles(x) for x in node]
    if isinstance(node, dict):
        return {k: strip_titles(v) for k, v in node.items() if k != "title"}
    return node


def collect(models: list[type]) -> dict[str, Any]:
    defs: dict[str, Any] = {}
    for model in models:
        raw = model.model_json_schema(ref_template="#/$defs/{model}")
        nested = raw.pop("$defs", {})
        for name, body in nested.items():
            defs.setdefault(name, strip_titles(body))
        name = raw.pop("title", model.__name__)
        defs[name] = strip_titles(raw)
    return defs


def patch(defs: dict[str, Any]) -> dict[str, Any]:
    if "JurySnapshot" in defs:
        defs["JurySnapshot"]["properties"]["vote_counts"]["propertyNames"] = {"type": "string"}
    if "IntakeResult" in defs:
        defs["IntakeResult"]["if"] = {
            "properties": {"mode": {"const": "FINAL_CHECK"}},
            "required": ["mode"],
        }
        defs["IntakeResult"]["then"] = {"properties": {"status": {"enum": ["PASS", "BLOCKED"]}}}
    return defs


def render(stem: str, title: str, defs: dict[str, Any], root: dict[str, Any]) -> str:
    doc: dict[str, Any] = {
        "$schema": DRAFT,
        "$id": f"{stem}.schema.json",
        "title": title,
        "$defs": patch(defs),
    }
    doc.update(root)
    return json.dumps(doc, ensure_ascii=False, indent=2) + "\n"


def render_all() -> dict[str, str]:
    """`{stem: JSON 텍스트}`. 테스트가 커밋본과 대조한다."""
    out = {
        "intake-v1": render(
            "intake-v1",
            "IntakeRequest / IntakeResult",
            collect([IntakeRequest, IntakeResult]),
            {"oneOf": [{"$ref": "#/$defs/IntakeRequest"}, {"$ref": "#/$defs/IntakeResult"}]},
        )
    }
    for stem, title, model in [
        ("case-snapshot-v1", "CaseSnapshot", CaseSnapshot),
        ("sentencing-v1", "SentencingDecision", SentencingDecision),
        ("writer-draft-v1", "WriterDraft", WriterDraft),
        ("evaluation-v1", "EvaluationReport", EvaluationReport),
        ("finalize-v1", "FinalizeRequest", FinalizeRequest),
        ("verdict-view-v1", "VerdictView", VerdictView),
    ]:
        out[stem] = render(stem, title, collect([model]), {"$ref": f"#/$defs/{title}"})
    return out


def main() -> None:
    for stem, text in render_all().items():
        path = CONTRACTS / f"{stem}.schema.json"
        path.write_text(text, encoding="utf-8", newline="\n")
        print("wrote", path.name)


if __name__ == "__main__":
    main()
