"""골든셋 judge(06 §3.4). 프롬프트 조립·출력 스키마·파싱·축 평균·기준.

judge 프롬프트 본문은 `judge.md` 다. 검수관 검사표는 복제하지 않고
`prompts/evaluator/guardrail-v3.md` 에서 절을 잘라 **인용**하고,
강도 정의는 서기 프롬프트의 그 강도 섹션을 인용한다.
judge 만으로 통과하지 않는다 — 판정은 `report.judge_regression` 이 자동 위반과 같이 본다.

judge 모델은 `MODEL_JUDGMENT` 다. 역할 enum 에 judge 가 없어 `role="evaluator"` 로 부른다
(라우터가 판단 역할 벤더·기본 모델로 보낸다).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from geoji_ai.domain.intensity import parse_intensity
from geoji_ai.prompts import WRITER_VERSION, load_prompt

__all__ = [
    "AXES",
    "GUARDRAIL_SOURCE",
    "JUDGE_PROMPT_PATH",
    "Axis",
    "JudgeScore",
    "axis_means",
    "below_thresholds",
    "build_judge_messages",
    "build_judge_system",
    "dry_run_output",
    "guardrail_checklist",
    "judge_schema",
    "parse_judge_output",
    "quote_section",
]

JUDGE_PROMPT_PATH = Path(__file__).resolve().with_name("judge.md")
GUARDRAIL_SOURCE = "evaluator/guardrail-v3.md"
#: 인용할 검수관 프롬프트 절 제목(접두어).
QUOTED_SECTIONS: tuple[str, ...] = ("## 검사표", "## 강도별 적용 표")
SCORE_VALUES: tuple[int, ...] = (1, 2, 3, 4, 5)

_CHECKLIST_SLOT = "{{GUARDRAIL_CHECKLIST}}"
_INTENSITY_SLOT = "{{INTENSITY_SECTION}}"


@dataclass(frozen=True)
class Axis:
    key: str
    label: str
    #: 축 평균 기준(06 §3.4 "≥ 4.0·4.0·3.5·4.0·4.0").
    threshold: float


AXES: tuple[Axis, ...] = (
    Axis("relevance", "관련성", 4.0),
    Axis("geojibang", "거지방다움", 4.0),
    Axis("fun", "재미", 3.5),
    Axis("intensity_fit", "강도 적합", 4.0),
    Axis("persuasion", "납득", 4.0),
)


@dataclass(frozen=True)
class JudgeScore:
    case_id: str
    intensity: str
    scores: dict[str, int]
    comment: str


def quote_section(markdown: str, header: str) -> str:
    """`header` 로 시작하는 줄부터 다음 `## ` 제목 전까지. 없으면 `ValueError`."""
    lines = markdown.splitlines()
    for start, line in enumerate(lines):
        if line.startswith(header):
            end = next(
                (i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")),
                len(lines),
            )
            return "\n".join(lines[start + 1 : end]).strip()
    raise ValueError(f"인용할 절이 없다: {header!r}")


def guardrail_checklist(root: Path | None = None) -> str:
    source = load_prompt(GUARDRAIL_SOURCE, root=root)
    return "\n\n".join(quote_section(source, header) for header in QUOTED_SECTIONS)


def build_judge_system(intensity: str, root: Path | None = None) -> str:
    key = parse_intensity(intensity)
    template = JUDGE_PROMPT_PATH.read_text(encoding="utf-8")
    section = load_prompt(f"writer/{key.value}-{WRITER_VERSION}.md", root=root).strip()
    return template.replace(_CHECKLIST_SLOT, guardrail_checklist(root)).replace(
        _INTENSITY_SLOT, section
    )


def judge_schema() -> dict[str, Any]:
    properties: dict[str, Any] = {
        axis.key: {"type": "integer", "enum": list(SCORE_VALUES)} for axis in AXES
    }
    properties["comment"] = {"type": "string"}
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def build_judge_messages(
    *,
    case_view: Mapping[str, Any],
    jury_result: str,
    sentencing: Mapping[str, Any] | None,
    facts: Sequence[Mapping[str, str]],
    text: Mapping[str, Any],
) -> list[dict[str, str]]:
    payload = {
        "case": dict(case_view),
        "jury": {"result": jury_result},
        "sentencing": dict(sentencing) if sentencing is not None else None,
        "dossier": [dict(f) for f in facts],
        "text": dict(text),
    }
    return [
        {"role": "system", "content": build_judge_system(str(text["intensity"]))},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def parse_judge_output(
    output: Mapping[str, Any] | None, case_id: str, intensity: str
) -> JudgeScore:
    if not isinstance(output, Mapping):
        raise ValueError("judge 출력이 없다")
    scores: dict[str, int] = {}
    for axis in AXES:
        value = output.get(axis.key)
        if isinstance(value, bool) or value not in SCORE_VALUES:
            raise ValueError(f"judge 축 {axis.key} 값이 1~5 정수가 아니다: {value!r}")
        scores[axis.key] = int(value)
    comment = output.get("comment")
    return JudgeScore(case_id, intensity, scores, comment if isinstance(comment, str) else "")


def axis_means(scores: Sequence[JudgeScore]) -> dict[str, float]:
    """축별 평균. 점수가 없으면 빈 dict."""
    if not scores:
        return {}
    return {axis.key: sum(s.scores[axis.key] for s in scores) / len(scores) for axis in AXES}


def below_thresholds(means: Mapping[str, float]) -> dict[str, float]:
    """기준 미달 축 → 평균. 평균이 없는 축은 보지 않는다."""
    return {
        axis.key: means[axis.key]
        for axis in AXES
        if axis.key in means and means[axis.key] < axis.threshold
    }


def dry_run_output() -> dict[str, Any]:
    """`--dry-run` judge 가짜 출력. 점수는 의미가 없다(가운데 값 3)."""
    return {**{axis.key: 3 for axis in AXES}, "comment": "dry-run"}
