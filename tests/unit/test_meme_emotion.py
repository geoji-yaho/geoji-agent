"""짤 감정 enum(08 §3.4 9/8 확정, 01 §6.1 `writer-draft-v1`).

모델·네트워크 없음. 생성기는 `tests/contracts/test_schema_regen.py` 와 같은 방식으로 불러온다.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from geoji_ai.contracts.llm_schemas import writer_schema
from geoji_ai.contracts.writer import MemeEmotion, MemeHints
from tests.evaluations import checks

ROOT = Path(__file__).resolve().parents[2]
GEN = ROOT / "tools" / "gen_contracts.py"

#: 08 §3.4 원문 순서 그대로.
PLAN_EMOTIONS = [
    "DISAPPROVAL",
    "ABSURD_SERIOUSNESS",
    "SMUG",
    "PITY",
    "CELEBRATION",
    "RESIGNATION",
]


def _render_all() -> dict[str, str]:
    spec = importlib.util.spec_from_file_location("gen_contracts", GEN)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.render_all()


def test_enum_is_the_six_emotions_of_08_3_4() -> None:
    assert [e.value for e in MemeEmotion] == PLAN_EMOTIONS


@pytest.mark.parametrize("value", PLAN_EMOTIONS)
def test_meme_hints_accepts_enum_values(value: str) -> None:
    hints = MemeHints.model_validate({"emotion": value, "keywords": ["택시"]})
    assert hints.emotion == MemeEmotion(value)


@pytest.mark.parametrize("value", ["HAPPY", "disapproval", "한심", ""])
def test_meme_hints_rejects_values_outside_enum(value: str) -> None:
    with pytest.raises(ValidationError):
        MemeHints.model_validate({"emotion": value, "keywords": []})


def test_generated_writer_schema_contains_enum() -> None:
    rendered: dict[str, Any] = json.loads(_render_all()["writer-draft-v1"])
    defs = rendered["$defs"]
    assert defs["MemeEmotion"] == {"enum": PLAN_EMOTIONS, "type": "string"}
    assert defs["MemeHints"]["properties"]["emotion"] == {"$ref": "#/$defs/MemeEmotion"}
    committed = json.loads(
        (ROOT / "contracts" / "writer-draft-v1.schema.json").read_text(encoding="utf-8")
    )
    assert committed == rendered


def test_strict_writer_schema_forces_enum() -> None:
    """프롬프트는 그대로 두고 strict 스키마 enum 으로 강제한다(스펙 미리 정한 해석)."""
    schema = writer_schema(["spicy"], ["PRICE"])
    branches = schema["properties"]["meme_hints"]["anyOf"]
    hints = next(b for b in branches if b.get("type") == "object")
    assert hints["properties"]["emotion"]["enum"] == PLAN_EMOTIONS


def test_golden_check_vocabulary_comes_from_enum() -> None:
    assert frozenset(PLAN_EMOTIONS) == checks.MEME_EMOTIONS
