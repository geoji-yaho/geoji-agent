"""심문관 프롬프트 `prompts/intake-v1.md`(07 §3.3).

카테고리 11종·예시 소재가 평가 데이터와 겹치지 않음·닳은 문구 없음·핵심 enum 언급.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import get_args

from geoji_ai.contracts.intake import Category
from geoji_ai.domain.lexicon import WORN_PHRASES
from geoji_ai.prompts import load_prompt

DATA_DIR = Path(__file__).resolve().parents[1] / "evaluations" / "intake"
PROMPT = "intake-v1.md"


def _section(text: str, header: str) -> str:
    lines = text.splitlines()
    start = lines.index(header)
    body: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("## "):
            break
        body.append(line)
    return "\n".join(body)


def _eval_items() -> list[str]:
    items = []
    for path in sorted(DATA_DIR.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                items.append(json.loads(line)["item"])
    return items


def test_카테고리_11종이_전부_값_그대로_있다():
    text = load_prompt(PROMPT)
    section = _section(text, "## 카테고리")
    listed = {line[2:].strip() for line in section.splitlines() if line.startswith("- ")}
    assert listed == set(get_args(Category))
    assert len(get_args(Category)) == 11


def test_카테고리_절이_입력_절보다_앞이다():
    lines = load_prompt(PROMPT).splitlines()
    assert lines.index("## 카테고리") < lines.index("## 입력")


def test_예시_무엇을_6개가_평가_데이터_item_과_겹치지_않는다():
    examples = re.findall(
        r'무엇을: "([^"]+)"', _section(load_prompt(PROMPT), "## 다른 사건의 예시")
    )
    assert len(examples) == 6
    items = _eval_items()
    assert len(items) == 100
    overlaps = [(e, i) for e in examples for i in items if e in i or i in e]
    assert overlaps == []


def test_닳은_문구가_없다():
    text = load_prompt(PROMPT)
    assert [p for p in WORN_PHRASES if p in text] == []


def test_핵심_값을_언급한다():
    text = load_prompt(PROMPT)
    for word in (
        "FINAL_CHECK",
        "NEEDS_CLARIFICATION",
        "VAGUE",
        "EXAGGERATED",
        "MISMATCH",
        "injection_detected",
    ):
        assert word in text
