"""사전 검수 템플릿(`contracts/fixtures/templates-v1.json`, 10 §10)으로 TEMPLATE 초안 만들기.

05 §3.3 서기 실패 강도와 D-19 양형 이유 치환에 쓴다.

치환 토큰은 fixture 그대로다(10 §10, 9/11 확정). `{n}` 배심원 수 = `jury.vote_counts` 합,
`{m}` 유죄 표 수 = `jury.vote_counts["guilty"]`, `{sentence_label}` = `sentence_labels[sentence]`.

해석(보고서 "질문"):

- fixture 의 `statement[]` 는 카드 규격에 맞는 문자열 1개다. 형량은 별도 필드로 전달한다.
- 문장은 모두 `kind="opinion"`, `evidence_labels=[]`(근거 인용 없음)
- `banter_strategy` 는 계약상 필수인데 템플릿에 값이 없다.
  `TEMPLATE_BANTER_STRATEGY` 한 곳에 둔다(미결정)
- `attack_angle` 은 고정 문구의 메타데이터로 `pick(post_id, offset)` 사용. AI 서기는 직접 선택.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

from geoji_ai.contracts.case import JurySnapshot
from geoji_ai.contracts.writer import BanterStrategy, CardStatement, CardTextDraft
from geoji_ai.domain.attack_angles import pick
from geoji_ai.domain.intensity import Intensity, parse_intensity

__all__ = [
    "TEMPLATES_PATH",
    "TEMPLATE_BANTER_STRATEGY",
    "TemplateUnavailable",
    "load_templates",
    "render_statement",
    "sentence_label",
    "sentencing_reason_template",
    "template_text_draft",
]

#: 저장소에서 `uv run` 할 때의 위치. 설치본에는 `contracts/` 가 없어 `path=` 로 넘긴다.
TEMPLATES_PATH = (
    Path(__file__).resolve().parents[3] / "contracts" / "fixtures" / "templates-v1.json"
)

#: 템플릿 초안의 `banter_strategy`. 계획서에 값이 없다 — 미결정(보고서 질문).
TEMPLATE_BANTER_STRATEGY = BanterStrategy.PREMISE_REJECTION

GUILTY = "guilty"


class TemplateUnavailable(ValueError):
    """이 결과의 템플릿으로는 계약에 맞는 `TextDraft` 를 만들 수 없다."""


@lru_cache(maxsize=4)
def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_templates(path: Path | None = None) -> dict[str, Any]:
    """`templates-v1.json` 을 읽는다(합성하지 않는다)."""
    return _load(path or TEMPLATES_PATH)


def _result_body(result: str, templates: Mapping[str, Any]) -> Mapping[str, Any]:
    results = templates["results"]
    if result not in results:
        raise TemplateUnavailable(f"templates 에 결과 {result!r} 가 없다")
    return results[result]


def sentence_label(sentence: str, templates: Mapping[str, Any] | None = None) -> str:
    """형량 코드 → 표시 라벨. 모르는 코드는 `TemplateUnavailable`."""
    labels = (templates or load_templates())["sentence_labels"]
    if sentence not in labels:
        raise TemplateUnavailable(f"sentence_labels 에 형량 {sentence!r} 가 없다")
    return labels[sentence]


def _tokens(
    jury: JurySnapshot, sentence: str | None, templates: Mapping[str, Any]
) -> dict[str, Any]:
    tokens: dict[str, Any] = {
        "n": sum(jury.vote_counts.values()),
        "m": jury.vote_counts.get(GUILTY, 0),
    }
    if sentence is not None:
        tokens["sentence_label"] = sentence_label(sentence, templates)
    return tokens


def render_statement(
    jury: JurySnapshot,
    sentence: str | None,
    templates: Mapping[str, Any] | None = None,
) -> list[str]:
    """결과의 `statement[]` 를 토큰 치환한 문자열 목록(fixture 모양 그대로)."""
    data = templates or load_templates()
    body = _result_body(str(jury.result), data)
    tokens = _tokens(jury, sentence, data)
    try:
        return [line.format(**tokens) for line in body["statement"]]
    except KeyError as exc:
        raise TemplateUnavailable(f"치환 토큰 {exc} 값이 없다(유죄인데 형량 없음?)") from exc


def sentencing_reason_template(
    jury: JurySnapshot, sentence: str, templates: Mapping[str, Any] | None = None
) -> str | None:
    """D-19 양형 이유 치환문. 결과에 템플릿이 없으면(유죄 외) None."""
    data = templates or load_templates()
    template = _result_body(str(jury.result), data)["sentencing_reason_template"]
    if template is None:
        return None
    return template.format(**_tokens(jury, sentence, data))


def template_text_draft(
    intensity: Intensity | str,
    jury: JurySnapshot,
    sentence: str | None,
    post_id: str,
    *,
    angle_offset: int = 0,
    templates: Mapping[str, Any] | None = None,
) -> CardTextDraft:
    """강도 하나의 TEMPLATE 초안. 카드 생성 규격에 못 맞추면 `TemplateUnavailable`."""
    data = templates or load_templates()
    body = _result_body(str(jury.result), data)
    if str(jury.result) == GUILTY and sentence is None:
        raise TemplateUnavailable("유죄 템플릿에 형량이 없다")
    sentences = render_statement(jury, sentence, data)
    try:
        return CardTextDraft(
            intensity=parse_intensity(intensity),
            headline=body["headline"],
            statement=[
                CardStatement(text=text, kind="opinion", evidence_labels=[]) for text in sentences
            ],
            banter_strategy=TEMPLATE_BANTER_STRATEGY,
            selected_candidate_id=None,
            attack_angle=pick(post_id, angle_offset),
            source="TEMPLATE",
        )
    except ValueError as exc:
        raise TemplateUnavailable(
            f"결과 {jury.result} 템플릿이 카드 규격에 맞지 않는다: 문장 {len(sentences)}개"
        ) from exc
